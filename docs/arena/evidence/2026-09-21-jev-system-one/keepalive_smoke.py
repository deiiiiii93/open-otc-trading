"""Live smoke: the merged keep-alive scorer (score_pending) against real Jev.

Every fact is seeded at the SAME extractor confidence (0.90), so any spread in
keep_alive_score is Jev's, not the extractor's. `expect` is the level we would
assign by hand (0 = drop ... 3 = keep); normalized = level / 3.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from sqlalchemy import update

from app import database
from app.config import get_settings
from app.models import MemoryEntry
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.keep_alive import score_pending
from app.services.deep_agent.memory.normalize import normalize_content
from app.services.deep_agent.memory.store import MemoryStore

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

# (key, content, age_days, expected_level, kind)
FACTS = [
    ("F1", "Desk reports P&L in CNY by default; USD only when a client asks for it.", 40, 3, "standing rule"),
    ("F2", "Use ACT/365 for CNY snowball coupon accrual unless the term sheet says otherwise.", 60, 3, "standing rule"),
    ("F3", "Any booking above the desk's notional threshold needs head-of-desk approval before release.", 90, 3, "standing rule"),
    ("F4", "Current project: migrating snowball pricing from Monte Carlo to the PDE engine.", 10, 2, "for now"),
    ("F5", "Book 3's vega limit is temporarily raised until month-end while its hedge is rebuilt.", 5, 2, "for now"),
    ("F6", "Run #134 is the latest flash board.", 18, 1, "one-off"),
    ("F7", "Position 27 is being reopened today pending the knock-out dispute.", 3, 1, "one-off"),
    ("F8", "Today's CSI 500 close was 6,812.4.", 1, 1, "one-off"),
    ("F9", "Desk reports P&L in USD by default.", 200, 0, "contradicted by F1"),
    ("F10", "Settlement notices go out to counterparties by fax.", 300, 0, "contradicted by F11"),
    ("F11", "Settlement notices are emailed as PDF; fax was retired in August.", 20, 3, "standing rule"),
]


def main():
    settings = get_settings()
    database.configure_database(settings)
    database.init_db()
    config = MemoryConfig(keep_alive_batch=len(FACTS))
    store = MemoryStore(config)
    now = datetime(2026, 9, 21, 12, 0, 0)
    ids = {}
    with database.SessionLocal() as s:
        for key, content, age, _lvl, _kind in FACTS:
            created = now - timedelta(days=age)
            row = MemoryEntry(scope_type="user", scope_id="desk", content=content,
                              normalized_content=normalize_content(content), confidence=0.90,
                              status="active", category="desk", created_by="extractor",
                              pinned=False, meta={}, created_at=created, updated_at=created)
            s.add(row)
            s.flush()
            ids[key] = row.id
        s.commit()

    results = {key: [] for key, *_ in FACTS}
    for rep in range(REPEATS):
        with database.SessionLocal() as s:
            s.execute(update(MemoryEntry).values(
                keep_alive_score=None, keep_alive_confidence=None, keep_alive_scored_at=None,
                keep_alive_attempted_at=None, keep_alive_unscored_reason=None,
                updated_at=MemoryEntry.updated_at))
            s.commit()
        scored = score_pending(database.SessionLocal, store, config, now=now)
        print(f"rep {rep}: scored {scored}/{len(FACTS)}", flush=True)
        with database.SessionLocal() as s:
            for key, *_ in FACTS:
                row = s.get(MemoryEntry, ids[key])
                results[key].append({"score": row.keep_alive_score, "conf": row.keep_alive_confidence,
                                     "reason": row.keep_alive_unscored_reason})

    out = []
    for key, content, age, lvl, kind in FACTS:
        scores = [r["score"] for r in results[key]]
        out.append({"key": key, "fact": content, "age_days": age, "kind": kind,
                    "expected": round(lvl / 3, 2), "scores": scores,
                    "confidence": [r["conf"] for r in results[key]],
                    "reasons": [r["reason"] for r in results[key]]})
        shown = " ".join("—" if v is None else f"{v:.2f}" for v in scores)
        print(f"{key:4} want={lvl / 3:.2f} got={shown:18} {kind:20} {content[:60]}")
    with open("keepalive_results.json", "w") as fh:
        json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render per-lab Arena outreach scorecards from the live DB.

Usage:
    .venv/bin/python scripts/render_scorecards.py --run 94

Read-only against data/open_otc.sqlite3. Writes docs/arena/scorecards/<lab>.md.

Cards come from store.leaderboard (per-trial derive + average), not from the
folded top-level score_breakdown, which carries no diagnosis block. Transcripts
come from the banked per-model runs, since a folded board row stores none.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import database  # noqa: E402
from app.services.arena import store  # noqa: E402
from app.services.arena.scorecard import (  # noqa: E402
    banked_run_ids,
    failed_checks,
    latest_transcript_paths,
    load_targets,
    render_scorecard,
    resolve_transcript,
)

TARGETS = REPO / "docs/arena/scorecards/targets.yaml"
OUT_DIR = REPO / "docs/arena/scorecards"


def _slug(lab: str) -> str:
    return lab.lower().replace(" ", "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=94)
    ap.add_argument("--db", default=str(REPO / "data/open_otc.sqlite3"))
    args = ap.parse_args()

    with database.SessionLocal() as session:
        board = {r["model_id"]: r for r in store.leaderboard(session, run_id=args.run)}
    if not board:
        print(f"ERROR: run #{args.run} has no leaderboard rows", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    breakdowns = {
        m: json.loads(b)
        for m, b in conn.execute(
            "select model_id, score_breakdown from arena_match where run_id=?",
            (args.run,),
        )
    }
    # Transcript-bearing runs come from the board's own merged_from provenance,
    # not a hardcoded range — see scorecard.banked_run_ids.
    configs = [
        json.loads(c) if c else {}
        for (c,) in conn.execute(
            "select config from arena_match where run_id=?", (args.run,)
        )
    ]
    runs = banked_run_ids(configs, args.run)
    placeholders = ",".join("?" for _ in runs)
    banked = list(
        conn.execute(
            "select run_id, model_id, transcript_path from arena_match "
            f"where run_id in ({placeholders})",
            runs,
        )
    )
    paths = latest_transcript_paths(banked)
    print(f"transcript source runs: {runs}", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    targets = load_targets(TARGETS)
    for target in targets:
        rows = [board[m] for m in target.model_ids if m in board]
        if not rows:
            print(f"SKIP {target.lab}: no board rows", file=sys.stderr)
            continue
        checks = {m: failed_checks(breakdowns.get(m) or {}) for m in target.model_ids}
        transcripts = {
            m: resolve_transcript(paths, m, REPO) for m in target.model_ids
        }
        trials = {
            m: int((breakdowns.get(m) or {}).get("n_trials") or 0)
            for m in target.model_ids
        }
        md = render_scorecard(
            target=target, rows=rows, checks=checks,
            transcripts=transcripts, run_id=args.run, trials=trials,
        )
        (OUT_DIR / f"{_slug(target.lab)}.md").write_text(md, encoding="utf-8")
        written += 1
        traced = sum(1 for v in transcripts.values() if v)
        print(
            f"wrote {_slug(target.lab)}.md  tier={target.tier}  "
            f"models={','.join(target.model_ids)}  "
            f"traces={traced}/{len(transcripts)}  "
            f"checks={sum(len(c) for c in checks.values())}"
        )

    print(f"\n{written}/{len(targets)} cards written to {OUT_DIR.relative_to(REPO)}")
    return 0 if written == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())

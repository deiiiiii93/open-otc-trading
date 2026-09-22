"""Probe: the thread-state question on the only desk text the arena has produced.

The arena corpus for risk-limit-breach-day holds model-written incident COMMENTS
(step 3: "Acknowledge the breach incident and log a comment on its timeline
summarizing your root-cause analysis") and no waivers. The step-3 prompt asks for a
root-cause summary, so the expected reading is `root_cause_only` — or `remediating`
where a model also reports the hedge the book already holds.

NOT the feature path: the feature never scores an arena thread (spec D14). This
builds the state in `build_thread_state`'s shape from the trace spans (read-only)
and asks the SAME `review.THREAD_QUESTIONS`, one call per thread.

Usage: OPEN_OTC_DATABASE_URL=<live DB, read-only> OPEN_OTC_TRACE_DB_PATH=<trace DB>
       python arena_threads.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "scripts"))

from limit_review_probe import _corpus  # noqa: E402  — the committed probe's reader

from app.services.limits import review  # noqa: E402
from app.services.system_one import SystemOneUnavailable, ask  # noqa: E402

FIRST_SEEN = datetime(2026, 6, 23, 15, 31, 30)   # the fixture incident's first_seen_at


def main() -> int:
    rows = [r for r in _corpus(1000) if r["tool"] == "comment_limit_incident"]
    threads: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        threads[row["thread_id"]].append(row)
    out = []
    for thread_id, comments in sorted(threads.items()):
        comments.sort(key=lambda r: r["at"] or "")
        last_at = review.parse_iso(comments[-1]["at"]) or FIRST_SEEN
        state = {
            "limit": {"name": "Desk Net Delta Cap", "scope_label": "Arena Limit Control Book"},
            "incident": {"severity": "breach", "status": "acknowledged",
                         "days_open": review.floor_days(last_at, FIRST_SEEN)},
            "comments": [{"at": c["at"], "actor": "agent",
                          "text": review.cap(c["text"], review.review_comment_chars)}
                         for c in comments[-review.review_thread_comments:]],
        }
        rec = {"thread_id": thread_id, "run_id": comments[0]["run_id"], "model": comments[0]["model"],
               "step5": comments[0]["step5"], "n_comments": len(comments), "state": state}
        try:
            result = ask(state, review.THREAD_QUESTIONS)
            answer = result.answers["thread_state"]
            rec.update(choice=answer.choice, p=round(answer.probabilities.get(answer.choice, 0.0), 3),
                       probabilities=dict(answer.probabilities), latency_ms=result.latency_ms)
        except SystemOneUnavailable as exc:
            rec.update(unscored=exc.reason)
        print(f"  t{thread_id:<6} {rec['model'][:28]:28s} n={len(comments)} "
              f"{rec.get('choice') or rec.get('unscored')} {rec.get('p')}", flush=True)
        out.append(rec)
    dist = Counter(r.get("choice") or f"unscored:{r.get('unscored')}" for r in out)
    summary = {"threads": len(out), "comments": len(rows), "distribution": dict(dist),
               "low_p": sum(1 for r in out if r.get("p") is not None and r["p"] < review.review_choice_min_p)}
    print(summary)
    (HERE / "arena_threads.json").write_text(json.dumps(
        {"summary": summary, "threads": out}, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

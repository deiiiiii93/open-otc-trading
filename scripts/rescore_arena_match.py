#!/usr/bin/env python3
"""Re-harvest and re-score ONE arena match from its recorded trace — no model calls.

For a match whose PLAY was sound but whose HARVEST was wrong: the fix belongs in
the harvester, and the match that actually happened is then scored again from the
same trace spans. Re-running it instead would draw a new stochastic sample and
throw away the real one. First use: run #141 match 633 (gpt-6-luna,
risk-manager-control), whose step-6 envelope-escalation retry was filed as step 7
and shifted every later step by one (see ``trace_harvest._group_roots_into_turns``).

Scoring goes through the same ``_run_and_score_once`` / ``_record_pair`` path as
a live match, with ``run_match_fn`` replaced by the re-harvested transcript. The
old transcript is kept under ``superseded-<date>/``; the new row keeps the
original ``config.provenance`` (what the match RAN under) and adds
``config.rescored`` (when, why, and the app that re-harvested it).

The thread id is not stored on the match, so pass it (find it from the trace DB by
the match's time window). The script refuses a thread whose LLM calls are not the
match's contestant.

Usage:
    OPEN_OTC_DATABASE_URL=sqlite:////abs/open_otc.sqlite3 \\
        python scripts/rescore_arena_match.py --match 633 --thread 1092 --reason "..."
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", type=int, required=True)
    ap.add_argument("--thread", type=int, required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app import database
    from app.config import get_settings
    from app.golden_workflows.registry import get_workflow_bundle
    from app.models import ArenaMatch
    from app.services.arena import task
    from app.services.arena.models import get_model
    from app.services.arena.provenance import app_provenance
    from app.services.arena.trace_harvest import transcript_from_trace
    from app.services.tracing.store import get_trace_store

    settings = get_settings()
    with database.SessionLocal() as session:
        row = session.get(ArenaMatch, args.match)
        if row is None:
            sys.exit(f"match {args.match} not found")
        if (row.config or {}).get("trials", 1) != 1:
            sys.exit("multi-trial rows need every trial's thread; not supported")
        loaded = get_workflow_bundle(row.workflow_id)
        model = get_model(row.model_id)
        transcript = transcript_from_trace(
            args.thread, loaded.workflow, model, store=get_trace_store(settings))
        seen = {c.get("model") for s in transcript.steps for c in (s.usage or [])}
        wire = model.zenmux_name.split(":", 1)[0]
        if seen and seen != {wire}:
            sys.exit(f"thread {args.thread} ran {sorted(map(str, seen))}, not {wire}")
        old_score, old_path = row.objective_score, row.transcript_path
        print(f"match {row.id} {row.workflow_id} {row.model_id}@{row.reasoning_effort}: "
              f"stored {old_score}")

        # Relative, like the live runner's, so the stored transcript_path matches
        # every other row (the script must run from the repo root).
        artifact_root = Path("artifacts") / "arena" / str(row.run_id)
        if old_path and not args.dry_run:
            old = REPO_ROOT / old_path
            keep = old.parent / f"superseded-{date.today().isoformat()}"
            keep.mkdir(exist_ok=True)
            for f in old.parent.glob("transcript*.json"):
                shutil.copy2(f, keep / f.name)

        effort = row.reasoning_effort or None
        budget = row.max_output_tokens or None
        status, breakdown, path, _ = task._run_and_score_once(
            session, run_id=row.run_id, loaded=loaded, model=model,
            workflow_id=row.workflow_id, model_id=row.model_id,
            weights=(row.config or {}).get("weights"),
            artifact_root=(artifact_root if not args.dry_run
                           else Path("/tmp") / "rescore-dry-run"),
            cfg=settings, run_match_fn=lambda *_a, **_k: transcript,
            judge_fn=None, post=None, trial=0,
            reasoning_effort=effort, max_output_tokens=budget,
        )
        if status != "scored" or breakdown is None:
            sys.exit(f"re-harvest gated as {status}; stored row left untouched")
        print(f"re-harvested: {breakdown['objective_score']} "
              f"({breakdown['diagnosis']['counts']})")
        if args.dry_run:
            return 0

        provenance = dict((row.config or {}).get("provenance") or {})
        run_id, wf, mid = row.run_id, row.workflow_id, row.model_id
        session.delete(row)
        session.flush()
        task._record_pair(
            session, run_id, wf, mid, (row.config or {}).get("weights"), 1,
            [breakdown], path, None, None,
            reasoning_effort=effort, max_output_tokens=budget,
            provenance={**provenance, "rescored": {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "app": app_provenance()["label"], "thread_id": args.thread,
                "previous_score": old_score, "reason": args.reason}},
        )
        session.commit()
    print("recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

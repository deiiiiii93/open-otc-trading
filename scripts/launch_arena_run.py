#!/usr/bin/env python
"""Launch — or RESUME — an arena run synchronously, without a backend server.

Queues an ArenaRun + TaskRun, commits, then drives ``execute_arena_run_task`` in
this process so the run survives independently of any dev server. Intended to be
started detached (``start_new_session``) for long boards.

Contestants route through the zenmux channel — ``arena_model_to_selection``
hardcodes it. Requires ``OPEN_OTC_DATABASE_URL`` (absolute), ``ZENMUX_API_KEY``
in the environment or repo-root ``.env``, and cwd = repo root so trace,
checkpoint and artifact paths resolve as usual.

``--resume RUN_ID`` continues an interrupted board **into the same run**, which
matters because a board is one run by definition — resuming into a second run
and merging would fold across two different network/time conditions, the thing
``merge_runs`` must never be used for.

Why resume exists at all: ``_execute`` catches per-trial exceptions and carries
on to the next pair. That isolates one transient blip, but under a SUSTAINED
outage every call fails in seconds, so the loop sweeps every remaining pair into
``invalid`` and marks the run ``completed`` — a quiet wrong board rather than a
crash. The safe response to a known outage is to stop the process, then resume.
Resume re-runs any pair that is not ``scored`` and deletes its stale non-scored
rows first, so a swept board can be repaired rather than re-run from zero.

Example:
    python scripts/launch_arena_run.py \
        --workflows risk-limit-breach-day \
        --models grok-4-6 --trials 1
    python scripts/launch_arena_run.py --resume 104
"""
from __future__ import annotations

import argparse
import sys


def resume(run_id: int) -> int:
    """Re-run every pair of *run_id* that is not already ``scored``.

    Mirrors ``task._execute``'s per-pair loop, but skips completed pairs and
    records into the SAME run. Stale non-scored rows for a retried pair are
    deleted first, or the pair would end up with two match rows.
    """
    from pathlib import Path

    from app.config import get_settings
    from app.database import SessionLocal
    from app.golden_workflows.registry import get_workflow_bundle
    from app.models import ArenaMatch
    from app.services.arena import store
    from app.services.arena.models import get_model
    from app.services.arena.runner import run_match
    from app.services.arena.task import _record_pair, _run_and_score_once

    session = SessionLocal()
    try:
        run = store.get_run(session, run_id)
        if run is None:
            print(f"run {run_id} not found", flush=True)
            return 1

        trials_n = int(run.get("trials") or 1)
        weights = run.get("weights")
        # Resume MUST reuse the run's own efforts, never re-read a flag: resuming
        # a pinned board at a different effort would splice two regimes into one run.
        reasoning_efforts = run.get("reasoning_efforts") or {}
        done = {(m["workflow_id"], m["model_id"])
                for m in run["matches"] if m["status"] == "scored"}
        pairs = [(w, m) for w in run["workflow_ids"] for m in run["model_ids"]]
        todo = [p for p in pairs if p not in done]

        print(f"resume run {run_id}: {len(done)} scored, {len(todo)} to run "
              f"(trials={trials_n}, efforts={reasoning_efforts or 'unpinned'})", flush=True)
        for w, m in todo:
            print(f"  todo: {m} x {w}", flush=True)
        if not todo:
            store.set_run_status(session, run_id, "completed")
            session.commit()
            print("nothing to resume; run marked completed", flush=True)
            return 0

        cfg = get_settings()
        artifact_root = Path(cfg.artifact_dir) / "arena" / str(run_id)
        artifact_root.mkdir(parents=True, exist_ok=True)
        store.set_run_status(session, run_id, "running")
        session.commit()

        for workflow_id, model_id in todo:
            # A previous sweep may have left an invalid/failed row for this pair.
            stale = (session.query(ArenaMatch)
                     .filter(ArenaMatch.run_id == run_id,
                             ArenaMatch.workflow_id == workflow_id,
                             ArenaMatch.model_id == model_id,
                             ArenaMatch.status != "scored")
                     .all())
            for row in stale:
                session.delete(row)
            if stale:
                session.commit()
                print(f"  cleared {len(stale)} stale row(s) for {model_id} x {workflow_id}",
                      flush=True)

            loaded = get_workflow_bundle(workflow_id)
            model = get_model(model_id)
            clean: list[dict] = []
            last_infra = last_path = last_infra_path = failed_exc = None
            for trial_index in range(trials_n):
                try:
                    status, breakdown, info, invalid_path = _run_and_score_once(
                        session, run_id=run_id, loaded=loaded, model=model,
                        workflow_id=workflow_id, model_id=model_id, weights=weights,
                        artifact_root=artifact_root, cfg=cfg, run_match_fn=run_match,
                        judge_fn=None, post=None, trial=trial_index,
                        reasoning_effort=reasoning_efforts.get(model_id))
                    if status == "scored" and breakdown is not None:
                        clean.append(breakdown)
                        last_path = info
                    else:
                        last_infra, last_infra_path = info, invalid_path
                except Exception:
                    import traceback as _tb
                    failed_exc = _tb.format_exc()
                session.commit()

            _record_pair(session, run_id, workflow_id, model_id, weights, trials_n,
                         clean, last_path, last_infra, failed_exc,
                         last_infra_path=last_infra_path,
                         reasoning_effort=reasoning_efforts.get(model_id))
            session.commit()
            print(f"  recorded {model_id} x {workflow_id} "
                  f"({len(clean)}/{trials_n} clean trials)", flush=True)

        remaining = {(m["workflow_id"], m["model_id"])
                     for m in (store.get_run(session, run_id) or {}).get("matches", [])
                     if m["status"] == "scored"}
        store.set_run_status(
            session, run_id,
            "completed" if len(remaining) == len(pairs) else "failed")
        session.commit()
        print(f"DONE resume run={run_id} scored={len(remaining)}/{len(pairs)}", flush=True)
        return 0
    finally:
        session.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflows", nargs="+")
    ap.add_argument("--models", nargs="+")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"],
        default=None,
        help="Pin EVERY selected model to this effort (expanded to a per-model "
             "map, then validated against each model's own ladder — launch fails "
             "naming any model that cannot take it). Omitted = vendor default, "
             "which is what boards #8-#104 measured; pinning makes a board "
             "non-comparable to them on EFF and CON. Ignored with --resume (the "
             "run keeps its own).",
    )
    ap.add_argument("--resume", type=int, metavar="RUN_ID",
                    help="Continue an interrupted run: re-run every non-scored pair.")
    args = ap.parse_args()

    if args.resume:
        from app.database import init_db
        init_db()
        return resume(args.resume)
    if not args.workflows or not args.models:
        ap.error("--workflows and --models are required unless --resume is given")

    from app.database import SessionLocal, init_db
    from app.services.arena.task import queue_arena_run, execute_arena_run_task

    init_db()
    session = SessionLocal()
    try:
        # queue_arena_run returns (run_id_int, task_run) — the run is an id, not an ORM object.
        run_id, task = queue_arena_run(
            session,
            workflow_ids=list(args.workflows),
            model_ids=list(args.models),
            trials=args.trials,
            # A scalar flag is sugar for "same effort for every model"; the stored
            # shape is per-model, since ladders differ and a mixed board may need
            # different levels. queue_arena_run rejects any model that cannot take it.
            reasoning_efforts=(
                {m: args.reasoning_effort for m in args.models}
                if args.reasoning_effort else None
            ),
        )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    pairs = len(args.workflows) * len(args.models)
    print(f"RUN_ID={run_id} TASK_ID={task_id} "
          f"pairs={pairs} trials={args.trials} "
          f"effort={args.reasoning_effort or 'unpinned'} "
          f"model_trials={pairs * args.trials}", flush=True)

    execute_arena_run_task(task_id, run_id)
    print(f"DONE run={run_id}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

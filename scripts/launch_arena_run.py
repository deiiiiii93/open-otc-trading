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
    # Per-model efforts: the ladder is a property of the ROUTE, so "every model
    # at its own ceiling" needs one token per model — a uniform pin at the
    # highest of them fails launch naming the models that cannot take it.
    python scripts/launch_arena_run.py \
        --workflows risk-limit-breach-day \
        --models mimo-2-5 minimax-m3 \
        --reasoning-effort mimo-2-5=high minimax-m3=max
    python scripts/launch_arena_run.py --resume 104
"""
from __future__ import annotations

import argparse
import sys


def _resume_todo(run: dict) -> list[tuple[str, str, "str | None"]]:
    """Arms of this run that are not yet scored, as (workflow, model, effort).

    Keyed by ARM, not by (workflow, model): a resume that treated a pair as done
    because its unpinned arm scored would never run the pinned arm and would then
    mark the run completed — this script's own reason for existing, one dimension
    further in.
    """
    from app.services.arena.task import arms_for

    efforts = run.get("reasoning_efforts") or {}
    budgets = run.get("max_output_tokens") or {}
    done = {
        (m["workflow_id"], m["model_id"], m.get("reasoning_effort"),
         m.get("max_output_tokens"))
        for m in run["matches"] if m["status"] == "scored"
    }
    arms = [
        (w, m, effort, budget)
        for w in run["workflow_ids"]
        for m in run["model_ids"]
        for effort, budget in arms_for(efforts, budgets, m)
    ]
    return [a for a in arms if a not in done]


def resume(run_id: int) -> int:
    """Re-run every ARM of *run_id* that is not already ``scored``.

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
        # Same rule for the output budget, and it is the reason this matters at
        # all: the budget used to be env-only with no column, so a resume that
        # omitted OPEN_OTC_AGENT_MAX_OUTPUT_TOKENS silently finished the run at a
        # DIFFERENT budget than it started with, and nothing in the stored data
        # would reveal it. Reading it off the run makes that impossible.
        max_output_tokens = run.get("max_output_tokens") or {}
        todo = _resume_todo(run)
        # A board is one test. Resuming it on an EDITED manifest would splice two
        # tests into one run, so refuse; start a new run instead. An unstamped
        # (pre-2026-09-25) run cannot be checked and is resumed as before.
        from app.services.arena.provenance import manifest_drift, match_provenance
        drift = manifest_drift(run.get("provenance"),
                               sorted({w for w, _m, _e, _b in todo}))
        if drift:
            for w, d in drift.items():
                print(f"  REFUSED: {w} manifest changed since run {run_id} was stamped "
                      f"(v{d['stamped'].get('manifest_version')} "
                      f"{str(d['stamped'].get('sha256'))[:12]} -> "
                      f"v{d['current']['manifest_version']} "
                      f"{d['current']['sha256'][:12]}); start a new run", flush=True)
            return 2
        from app.services.arena.task import arms_for
        arms_total = len(run["workflow_ids"]) * sum(
            len(arms_for(reasoning_efforts, max_output_tokens, m))
            for m in run["model_ids"]
        )

        print(f"resume run {run_id}: {arms_total - len(todo)} scored, "
              f"{len(todo)} to run (trials={trials_n}, "
              f"efforts={reasoning_efforts or 'unpinned'}, "
              f"budgets={max_output_tokens or 'unpinned'})", flush=True)
        for w, m, effort, budget in todo:
            print(f"  todo: {m} @ {effort or 'default'}"
                  f"/{budget or 'default'} x {w}", flush=True)
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

        for workflow_id, model_id, model_effort, model_budget in todo:
            # A previous sweep may have left an invalid/failed row for this ARM.
            # The effort filter matters: without it this would also delete the
            # OTHER arm's stale row, which its own iteration then never repairs.
            stale = (session.query(ArenaMatch)
                     .filter(ArenaMatch.run_id == run_id,
                             ArenaMatch.workflow_id == workflow_id,
                             ArenaMatch.model_id == model_id,
                             ArenaMatch.reasoning_effort == (model_effort or ""),
                             ArenaMatch.max_output_tokens == (model_budget or 0),
                             ArenaMatch.status != "scored")
                     .all())
            for row in stale:
                session.delete(row)
            if stale:
                session.commit()
                print(f"  cleared {len(stale)} stale row(s) for {model_id} "
                      f"@ {model_effort or 'default'}/{model_budget or 'default'}"
                      f" x {workflow_id}", flush=True)

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
                        reasoning_effort=model_effort,
                        max_output_tokens=model_budget)
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
                         reasoning_effort=model_effort,
                         max_output_tokens=model_budget,
                         provenance=match_provenance(loaded))
            session.commit()
            print(f"  recorded {model_id} @ {model_effort or 'default'}"
                  f"/{model_budget or 'default'} x {workflow_id} "
                  f"({len(clean)}/{trials_n} clean trials)", flush=True)

        remaining = _resume_todo(store.get_run(session, run_id) or run)
        store.set_run_status(
            session, run_id, "completed" if not remaining else "failed")
        session.commit()
        print(f"DONE resume run={run_id} "
              f"scored={arms_total - len(remaining)}/{arms_total}", flush=True)
        return 0
    finally:
        session.close()


def _effort_map(
    tokens: "list[str] | None",
    model_ids: list[str],
) -> "dict[str, list[str]] | None":
    """Expand ``--reasoning-effort`` tokens into the per-model arm map.

    Two forms, mixable in one invocation:

        uniform    ``high``            — applies to every selected model
        per-model  ``mimo-2-5=high``   — applies to that model only

    Per-model exists because the ladder is a property of the ROUTE, not of the
    board: on this field the ceilings are ``max`` (deepseek-v4-flash, gpt-5-6-luna,
    minimax-m3), ``xhigh`` (hunyuan-3, gemini-3-7-flash) and ``high`` (mimo-2-5).
    A uniform pin at the highest of those fails launch naming the three models
    that cannot take it — ``queue_arena_run`` validates each entry against its own
    model — so "every model at its own ceiling" is unexpressible without this.

    Returns ``None`` when *tokens* is empty, which is the unpinned vendor-default
    run every board through #104 measured.

    Raises:
        ValueError: on an unknown level, or a model key not in *model_ids* (which
            ``queue_arena_run`` also rejects — a key naming a model not in the run
            would otherwise leave that arm silently unpinned).
    """
    if not tokens:
        return None

    # Sourced, never restated: a second copy of the level vocabulary is exactly
    # the drift class this repo keeps paying for.
    from app.services.deep_agent.model_factory import VALID_REASONING_EFFORTS

    known = set(model_ids)
    resolved: "dict[str, list[str]]" = {}

    for token in tokens:
        slug, sep, level = token.rpartition("=")
        if sep:
            if slug not in known:
                raise ValueError(
                    f"--reasoning-effort {token!r}: {slug!r} is not one of the "
                    f"selected models ({', '.join(model_ids)}). A key naming a "
                    "model not in the run would leave that arm silently unpinned."
                )
            targets = [slug]
        else:
            targets = list(model_ids)

        if level not in VALID_REASONING_EFFORTS:
            raise ValueError(
                f"--reasoning-effort {token!r}: unknown level {level!r}; "
                f"expected one of {list(VALID_REASONING_EFFORTS)}"
            )

        for model_id in targets:
            arms = resolved.setdefault(model_id, [])
            if level in arms:
                raise ValueError(
                    f"--reasoning-effort {token!r}: {model_id} is already pinned "
                    f"to {level!r}. A duplicate level mints two contestants "
                    "sharing one key."
                )
            arms.append(level)

    # Models nobody named stay ABSENT — an absent model runs once at the vendor
    # default, which is not the same as mapping it to None (the explicit
    # unpinned ARM). Never materialise a key for a model the caller did not pin.
    return resolved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workflows", nargs="+")
    ap.add_argument("--models", nargs="+")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument(
        "--reasoning-effort",
        nargs="+",
        metavar="LEVEL|SLUG=LEVEL",
        default=None,
        help="Pin EVERY selected model to these efforts (expanded to a per-model "
             "map, then validated against each model's own ladder — launch fails "
             "naming any model that cannot take one). Give SEVERAL to run each "
             "model once per level, as separate contestants that rank against "
             "each other on the same board. Omitted = vendor default, which is "
             "what boards #8-#104 measured; pinning makes a board non-comparable "
             "to them on EFF and CON. Ignored with --resume (the run keeps its "
             "own).",
    )
    ap.add_argument(
        "--max-output-tokens",
        nargs="+",
        type=int,
        default=None,
        help="Pin EVERY selected model to these output-token budgets (expanded "
             "to a per-model map). Give SEVERAL to run each model once per "
             "budget, as separate contestants that rank against each other on "
             "the same board — that is the A/B runs #118/#119 had to do as two "
             "separate runs. Omitted = the process default "
             "(OPEN_OTC_AGENT_MAX_OUTPUT_TOKENS), which is what every board "
             "through #117 measured. Budget is a REGIME: #118 vs #119 differ by "
             "16.4 mean objective on this alone, so a pinned board is not "
             "comparable to an unpinned one. Ignored with --resume (the run "
             "keeps its own, which is the point — the budget used to be env-only "
             "and a resume could silently change it).",
    )
    ap.add_argument("--resume", type=int, metavar="RUN_ID",
                    help="Continue an interrupted run: re-run every non-scored arm.")
    args = ap.parse_args()

    if args.resume:
        from app.database import init_db
        init_db()
        return resume(args.resume)
    if not args.workflows or not args.models:
        ap.error("--workflows and --models are required unless --resume is given")

    from app.database import SessionLocal, init_db
    from app.services.arena.task import (
        arms_for, queue_arena_run, execute_arena_run_task,
    )

    try:
        efforts = _effort_map(args.reasoning_effort, list(args.models))
    except ValueError as exc:
        ap.error(str(exc))
    budgets = ({m: list(args.max_output_tokens) for m in args.models}
               if args.max_output_tokens else None)

    init_db()
    session = SessionLocal()
    try:
        # queue_arena_run returns (run_id_int, task_run) — the run is an id, not an ORM object.
        run_id, task = queue_arena_run(
            session,
            workflow_ids=list(args.workflows),
            model_ids=list(args.models),
            trials=args.trials,
            # Already the stored per-model shape (see _effort_map): a bare
            # level is sugar for "every model", a slug=level token pins one.
            # Several levels for one model make it several ARMS.
            # queue_arena_run rejects any model that cannot take one of them —
            # that ladder check is effort_rejection's job, not the parser's.
            reasoning_efforts=efforts,
            # Same sugar, same per-model stored shape. Several budgets make each
            # model several ARMS, so a single run can carry the whole A/B.
            max_output_tokens=budgets,
        )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    # Arms, not pairs: pinned levels and pinned budgets MULTIPLY — a model at two
    # efforts and two budgets is four contestants. Counted through arms_for, the
    # single definition the execution loop and progress total also read: with a
    # PER-MODEL effort map the old uniform arithmetic silently miscounts, and a
    # short total reads as a stuck progress bar rather than as a bug.
    arms = len(args.workflows) * sum(
        len(arms_for(efforts, budgets, m)) for m in args.models)
    print(f"RUN_ID={run_id} TASK_ID={task_id} "
          f"arms={arms} trials={args.trials} "
          f"effort={efforts or 'unpinned'} "
          f"budget={','.join(map(str, args.max_output_tokens)) if args.max_output_tokens else 'unpinned'} "
          f"model_trials={arms * args.trials}", flush=True)

    execute_arena_run_task(task_id, run_id)
    print(f"DONE run={run_id}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

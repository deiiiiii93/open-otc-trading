"""Arena run queueing and execution.

queue_arena_run  — validate inputs, create ArenaRun + TaskRun, return both.
execute_arena_run_task — sequential fan-out over (workflow, model) pairs;
    each pair is run_match + judge_match + score; failures are per-match
    (status="failed") and never abort the run.
"""
from __future__ import annotations

import json
import re
import traceback
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.models import TaskKind, TaskRun, TaskStatus
from app.services.arena import store
from app.services.arena.models import validate_model_ids


def queue_arena_run(
    session: Session,
    *,
    workflow_ids: list[str],
    model_ids: list[str],
    weights: dict | None = None,
    trials: int = 1,
) -> tuple[Any, TaskRun]:
    """Validate inputs, create ArenaRun + TaskRun, flush (no commit).

    Args:
        session:      Open SQLAlchemy session (caller commits).
        workflow_ids: Non-empty list of workflow IDs.
        model_ids:    Non-empty list of model slugs/zenmux_names.
        weights:      Optional dict with keys "obj" and "judge".
        trials:       Number of trials to run per (workflow, model) pair,
                      folded into one aggregate match at execution time.

    Returns:
        (run_id_int, task_run) — run_id_int is the ArenaRun.id.

    Raises:
        ValueError: if any workflow_id is unknown, any model_id is unknown,
                    or either list is empty.
    """
    from app.golden_workflows.registry import get_workflow_bundle

    if not workflow_ids:
        raise ValueError("workflow_ids must not be empty")
    if not model_ids:
        raise ValueError("model_ids must not be empty")

    # Validate all workflow IDs (raises FileNotFoundError or WorkflowError if unknown)
    for wid in workflow_ids:
        try:
            get_workflow_bundle(wid)
        except Exception as exc:
            raise ValueError(f"Unknown workflow_id '{wid}': {exc}") from exc

    # Validate + canonicalize model IDs (raises ValueError if unknown)
    canonical_model_ids = validate_model_ids(model_ids)

    run_id = store.create_run(
        session,
        workflow_ids=workflow_ids,
        model_ids=canonical_model_ids,
        weights=weights,
        trials=trials,
    )

    task = TaskRun(
        kind=TaskKind.ARENA_RUN.value,
        status=TaskStatus.QUEUED.value,
        description=f"Arena run: {len(workflow_ids)} workflow(s) × {len(canonical_model_ids)} model(s)",
        progress_current=0,
        progress_total=len(workflow_ids) * len(canonical_model_ids) * trials,
        message="Queued arena run",
    )
    session.add(task)
    session.flush()

    # Return a simple namespace so callers can access .id and the task
    from types import SimpleNamespace
    run_obj = SimpleNamespace(id=run_id)
    return run_obj, task


def _is_infra_blank(transcript) -> bool:
    """All steps produced nothing AND at least one step carries error evidence.

    Blankness alone is not enough — a model that silently did nothing is a real
    scored 0; invalidity must be corroborated by transport/provider errors.
    """
    steps = transcript.steps
    if not steps:
        return False
    all_blank = all(
        not s.tool_calls and not s.response_text.strip() for s in steps)
    has_error = any(s.errors for s in steps)
    return all_blank and has_error


# Provider/transport failure signatures — quota, rate-limit, upstream 5xx, and
# connection/proxy errors. Deliberately narrow: a domain tool raising (e.g.
# "portfolio not found") is a real outcome the agent should handle and must NOT
# invalidate the trial. These patterns only ever come from the LLM provider or
# the network path to it.
_PROVIDER_ERROR_RE = re.compile(
    r"quote_exceeded"
    r"|insufficient[_ ]?quota"
    r"|rate[_ ]?limit"
    r"|Error code:\s*(?:402|429|5\d\d)"
    r"|\b(?:429|502|503|504)\b"
    r"|overloaded_error|Overloaded"
    r"|ServiceUnavailable"
    r"|(?:Connection|Proxy|Read)\s*(?:Error|Timeout|refused|reset|aborted)",
    re.IGNORECASE,
)


def _error_text(entry) -> str:
    """Flatten a step-error record (dict {span,name,error} or str) to text."""
    if isinstance(entry, dict):
        return " ".join(str(entry.get(k, "")) for k in ("error", "name", "span"))
    return str(entry)


def _is_infra_contaminated(transcript) -> bool:
    """A provider transport error (quota/rate-limit/5xx/connection) struck the
    run — even mid-flight after real early steps.

    This is the partial-death case ``_is_infra_blank`` misses: when the first
    steps produce real content but a later model call dies on a 402 quota, the
    truncated transcript cannot be fairly scored or compared. It is invalid, not
    a real low score. Domain tool errors do not match ``_PROVIDER_ERROR_RE`` and
    stay scored.

    A provider blip that was *retried and recovered* leaves an error span but the
    step still produces a completed assistant response (`response_text`). Only a
    step left WITHOUT a completed response by a provider error is terminal — an
    issued tool call alone is NOT recovery (a tool call followed by a 402 on the
    final response is a partial death); a transient error that recovered into a
    real response must not invalidate the trial.
    """
    for s in transcript.steps:
        if s.response_text.strip():
            continue  # recovered — a COMPLETED assistant response, not a mere
                      # issued tool call (a tool call then a 402 on the final
                      # response is a partial death, not recovery)
        for entry in (s.errors or []):
            if _PROVIDER_ERROR_RE.search(_error_text(entry)):
                return True
    return False


def _save_transcript(transcript, artifact_root: Path,
                     workflow_id: str, model_id: str) -> str | None:
    """Persist the transcript JSON to disk; best-effort, None on failure."""
    try:
        t_dir = artifact_root / workflow_id / model_id
        t_dir.mkdir(parents=True, exist_ok=True)
        t_file = t_dir / "transcript.json"
        t_file.write_text(
            json.dumps(transcript.model_dump(), indent=2),
            encoding="utf-8",
        )
        return str(t_file)
    except Exception:
        return None


def execute_arena_run_task(
    task_id: int,
    run_id: int,
    session_factory: sessionmaker | None = None,
    *,
    settings=None,
    run_match_fn: Callable | None = None,
    judge_fn: Callable | None = None,
    post: Callable | None = None,
    get_bundle_fn: Callable | None = None,
) -> None:
    """Execute a queued arena run: fan out over (workflow, model) pairs sequentially.

    Each pair:
      1. get_bundle_fn(workflow_id) → LoadedWorkflow (or real get_workflow_bundle)
      2. run_match_fn(loaded, model, artifact_root=...) → MatchTranscript
      3. judge_fn(transcript, loaded, post=post) → JudgeResult
      4. scoring.objective_score + scoring.total_score
      5. store.record_match(..., status="scored")

    On any per-match exception: record match as status="failed" and continue.
    Run status is set to "completed" when all pairs are processed (even if some
    failed). Only an infra-level exception (e.g. can't load the run row) sets
    run status to "failed".

    Args:
        task_id:       TaskRun.id
        run_id:        ArenaRun.id
        session_factory: Optional sessionmaker; defaults to database.SessionLocal
        settings:      Optional Settings instance (for artifact_dir).
        run_match_fn:  Injectable fake for testing (replaces runner.run_match).
        judge_fn:      Injectable fake for testing (replaces judge.judge_match).
        post:          Passed through to judge_fn as post= kwarg.
        get_bundle_fn: Injectable fake for testing (replaces get_workflow_bundle).
    """
    sf = session_factory or database.SessionLocal
    session = sf()
    try:
        _execute(session, task_id, run_id,
                 settings=settings,
                 run_match_fn=run_match_fn,
                 judge_fn=judge_fn,
                 post=post,
                 get_bundle_fn=get_bundle_fn)
    finally:
        session.close()


def _run_and_score_once(
    session: Session,
    *,
    run_id: int,
    loaded,
    model,
    workflow_id: str,
    model_id: str,
    weights: dict | None,
    artifact_root: Path,
    cfg,
    run_match_fn: Callable,
    judge_fn: Callable | None,
    post: Callable | None,
) -> tuple[str, dict | None, str | None, str | None]:
    """Run and score ONE trial for a (workflow, model) pair.

    Returns ("scored", breakdown, transcript_path) or ("invalid", None,
    infra_reason, transcript_path). Raises on transport/other exceptions —
    the caller is responsible for catching those per-trial and recording a
    "failed" pair. The transcript is saved to disk (audit evidence) for both
    outcomes; ``transcript_path`` is the 4th tuple element for the invalid
    case (kept out of the 3rd slot so it stays the human-readable reason).
    """
    from app.services.arena import scoring
    from app.services.arena.judge import judge_panel as _judge_panel

    transcript = run_match_fn(loaded, model, artifact_root=artifact_root, run_id=run_id)

    # Infra gate: a route/transport failure is not model ability — record it
    # as 'invalid' (excluded from leaderboard means), skip judge+scoring. Two
    # shapes: an all-blank transcript with error evidence (infra_blank), and a
    # partial run truncated by a provider transport error after real early
    # steps (infra_error). Transcript evidence is still saved for audit.
    if _is_infra_blank(transcript):
        invalid_path = _save_transcript(transcript, artifact_root, workflow_id, model_id)
        return "invalid", None, "infra_blank", invalid_path
    if _is_infra_contaminated(transcript):
        invalid_path = _save_transcript(transcript, artifact_root, workflow_id, model_id)
        return "invalid", None, "infra_error", invalid_path

    # Subjective judgment: the injected test seam, else a contestant-excluded
    # jury. Judge-missing (jury unavailable) is NOT infra-invalid — the
    # objective axis is the spine and still scores the match.
    # Jury is opt-in (spec 2026-07-06): an explicitly injected judge_fn
    # (test/caller intent) always runs; otherwise the default
    # contestant-excluded jury runs ONLY when arena_jury_enabled. When off, no
    # judge is attempted — the row is scored objective-only.
    if judge_fn is not None:
        judge_result = judge_fn(transcript, loaded, post=post)
    elif cfg.arena_jury_enabled:
        judge_result = _judge_panel(
            transcript, loaded,
            judge_models=cfg.arena_judge_models,
            exclude_model=model_id,
            substitutes=cfg.arena_judge_substitutes,
            min_judges=cfg.arena_min_judges,
            self_consistency_k=cfg.arena_self_consistency_k,
        )
    else:
        judge_result = None

    obj_score, _passed, _total = scoring.objective_score(transcript, loaded)
    # No blend (spec D5): the stored total mirrors the objective axis, the
    # sole ranking dimension; subjective is advisory, reported apart.
    t_score = round(obj_score, 1)

    # Per-check breakdown behind the aggregate scores, persisted so the
    # /arena match drilldown can show where points were won/lost. The judge
    # block is present only when a judge actually ran; a deliberately
    # jury-off row stamps subjective_mode="disabled" so it is distinguishable
    # from a jury-on row whose judges all failed ("missing"). See spec D3/D8.
    heuristic = scoring.diagnose_heuristic(transcript, loaded)
    breakdown = {
        "objective": scoring.objective_breakdown(transcript, loaded),
        # Why/where the model won or lost: deterministic engagement counts
        # (always present) + the judge's LLM failure analysis.
        "diagnosis": {
            "counts": heuristic["summary"],
            "counts_detail": heuristic,
            "analysis": (judge_result.diagnosis if judge_result else None),
        },
        "objective_score": round(obj_score, 1),
        "total_score": t_score,
    }
    if judge_result is not None:
        breakdown["judge"] = {
            "rubric_scores": judge_result.rubric_scores,
            "judged_score": judge_result.judged_score,
            "judge_missing": judge_result.judge_missing,
            "per_judge": judge_result.per_judge,
            "judged_stdev": judge_result.judged_stdev,
        }
        breakdown["subjective_mode"] = judge_result.subjective_mode
    else:
        breakdown["subjective_mode"] = "disabled"

    judged_score = judge_result.judged_score if judge_result else None

    # Ability card (spec B7): derived from the same objective axes +
    # tool-call count, JDG advisory (the jury score, None when off).
    breakdown["card"] = scoring.ability_card(
        transcript, loaded, judged=judged_score)

    # Save transcript to disk
    transcript_path = _save_transcript(transcript, artifact_root, workflow_id, model_id)

    return "scored", breakdown, transcript_path, None


def _record_pair(
    session: Session,
    run_id: int,
    workflow_id: str,
    model_id: str,
    weights: dict | None,
    trials_n: int,
    clean: list[dict],
    last_path: str | None,
    last_infra: str | None,
    failed_exc: str | None,
    last_infra_path: str | None = None,
) -> None:
    """Persist exactly one match row for a (workflow, model) pair after its trials.

    >=1 clean trial folds into one scored aggregate match; else a failed row
    if every trial raised, else an invalid row (all trials were infra-gated).
    ``last_infra_path`` preserves the last infra-gated trial's saved
    transcript (audit evidence) on the invalid row, matching the
    single-trial behavior of always saving transcript evidence for an
    infra-invalid match.
    """
    from app.services.arena import scoring

    cfg = {"weights": weights, "trials": trials_n}
    if clean:
        agg = scoring.fold_trial_breakdowns(clean)

        # Roll up per-trial judge scores (each clean trial's own
        # breakdown["judge"]["judged_score"]) onto the aggregate so the
        # top-level ArenaMatch.judged_score column and score_breakdown["judge"]
        # aren't silently dropped when the jury ran. Only stamp a judge block
        # when at least one clean trial actually carries one — a jury-off
        # aggregate must keep judged_score=None / no "judge" key, matching
        # subjective_mode="disabled".
        trial_judges = [t["judge"] for t in clean if isinstance(t.get("judge"), dict)]
        judged_values = [
            j["judged_score"] for j in trial_judges if j.get("judged_score") is not None
        ]
        judged_score = (
            round(sum(judged_values) / len(judged_values), 1) if judged_values else None
        )
        judge_missing = any(j.get("judge_missing") for j in trial_judges)
        if trial_judges:
            agg["judge"] = {"judged_score": judged_score, "judge_missing": judge_missing}

        store.record_match(
            session,
            run_id=run_id,
            workflow_id=workflow_id,
            model_id=model_id,
            objective_score=agg["objective_score"],
            judged_score=judged_score,
            total_score=agg["objective_score"],
            judge_missing=judge_missing,
            config=cfg,
            transcript_path=last_path,
            status="scored",
            score_breakdown=agg,
        )
    elif last_infra is None and failed_exc is not None:
        store.record_match(
            session,
            run_id=run_id,
            workflow_id=workflow_id,
            model_id=model_id,
            objective_score=None,
            judged_score=None,
            total_score=None,
            judge_missing=True,
            config=cfg,
            transcript_path=None,
            status="failed",
            error=failed_exc,
        )
    else:
        store.record_match(
            session,
            run_id=run_id,
            workflow_id=workflow_id,
            model_id=model_id,
            objective_score=None,
            judged_score=None,
            total_score=None,
            judge_missing=True,
            config=cfg,
            transcript_path=last_infra_path,
            status="invalid",
            error=last_infra or "infra_blank",
        )


def _execute(
    session: Session,
    task_id: int,
    run_id: int,
    *,
    settings=None,
    run_match_fn: Callable | None = None,
    judge_fn: Callable | None = None,
    post: Callable | None = None,
    get_bundle_fn: Callable | None = None,
) -> None:
    from app.services.arena.runner import run_match as _run_match
    from app.golden_workflows.registry import get_workflow_bundle as _get_workflow_bundle
    from app.services.arena.models import get_model
    from app.config import get_settings

    _get_bundle = get_bundle_fn or _get_workflow_bundle
    from app.services.task_runner import mark_task_running, mark_task_finished, update_task_progress

    _cfg = settings if settings is not None else get_settings()
    _run_match_fn = run_match_fn or _run_match

    # Resolve artifact root
    if settings is not None:
        artifact_root = Path(settings.artifact_dir) / "arena" / str(run_id)
    else:
        try:
            from app.config import get_settings
            artifact_root = Path(get_settings().artifact_dir) / "arena" / str(run_id)
        except Exception:
            artifact_root = Path("/tmp/arena") / str(run_id)
    artifact_root.mkdir(parents=True, exist_ok=True)

    # Load the run to get workflow_ids and model_ids
    run_dict = store.get_run(session, run_id)
    if run_dict is None:
        mark_task_finished(
            session, task_id,
            status=TaskStatus.FAILED.value,
            error=f"ArenaRun not found: {run_id}",
        )
        session.commit()
        return

    workflow_ids: list[str] = run_dict["workflow_ids"]
    model_ids: list[str] = run_dict["model_ids"]
    weights: dict | None = run_dict.get("weights")
    trials_n = int(run_dict.get("trials") or 1)

    total_units = len(workflow_ids) * len(model_ids) * trials_n
    mark_task_running(session, task_id)
    store.set_run_status(session, run_id, "running")
    session.commit()

    completed = 0

    # Sequential fan-out: one pair at a time, N trials per pair folded into a
    # single aggregate match (trials_n == 1 is the historical single-match path).
    for workflow_id in workflow_ids:
        for model_id in model_ids:
            loaded = _get_bundle(workflow_id)
            model = get_model(model_id)

            clean: list[dict] = []
            last_infra: str | None = None
            last_path: str | None = None
            last_infra_path: str | None = None
            failed_exc: str | None = None
            for _trial in range(trials_n):
                try:
                    status, breakdown, info, invalid_path = _run_and_score_once(
                        session,
                        run_id=run_id,
                        loaded=loaded,
                        model=model,
                        workflow_id=workflow_id,
                        model_id=model_id,
                        weights=weights,
                        artifact_root=artifact_root,
                        cfg=_cfg,
                        run_match_fn=_run_match_fn,
                        judge_fn=judge_fn,
                        post=post,
                    )
                    if status == "scored":
                        clean.append(breakdown)
                        last_path = info          # info is the saved transcript path
                    else:
                        last_infra = info         # info is the infra reason
                        last_infra_path = invalid_path
                except Exception:
                    failed_exc = traceback.format_exc()

                completed += 1
                update_task_progress(session, task_id, current=completed, total=total_units)
                session.commit()

            _record_pair(session, run_id, workflow_id, model_id, weights, trials_n,
                        clean, last_path, last_infra, failed_exc,
                        last_infra_path=last_infra_path)
            session.commit()

    # All pairs processed — always mark completed (individual match failures are ok)
    store.set_run_status(session, run_id, "completed")
    mark_task_finished(session, task_id, status=TaskStatus.COMPLETED.value)
    session.commit()

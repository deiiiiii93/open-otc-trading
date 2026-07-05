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
) -> tuple[Any, TaskRun]:
    """Validate inputs, create ArenaRun + TaskRun, flush (no commit).

    Args:
        session:      Open SQLAlchemy session (caller commits).
        workflow_ids: Non-empty list of workflow IDs.
        model_ids:    Non-empty list of model slugs/zenmux_names.
        weights:      Optional dict with keys "obj" and "judge".

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
    )

    task = TaskRun(
        kind=TaskKind.ARENA_RUN.value,
        status=TaskStatus.QUEUED.value,
        description=f"Arena run: {len(workflow_ids)} workflow(s) × {len(canonical_model_ids)} model(s)",
        progress_current=0,
        progress_total=len(workflow_ids) * len(canonical_model_ids),
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
    step still produces real output (response text or tool calls). Only a step
    left with NO usable output by a provider error is terminal — a transient one
    that recovered must not invalidate the trial.
    """
    for s in transcript.steps:
        if s.response_text.strip() or s.tool_calls:
            continue  # step recovered — a retried provider blip is not terminal
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
    from app.services.arena.judge import judge_match as _judge_match
    from app.services.arena import scoring
    from app.golden_workflows.registry import get_workflow_bundle as _get_workflow_bundle
    from app.services.arena.models import get_model

    _get_bundle = get_bundle_fn or _get_workflow_bundle
    from app.services.task_runner import mark_task_running, mark_task_finished, update_task_progress

    _run_match_fn = run_match_fn or _run_match
    _judge_fn = judge_fn or _judge_match

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

    total_pairs = len(workflow_ids) * len(model_ids)
    mark_task_running(session, task_id)
    store.set_run_status(session, run_id, "running")
    session.commit()

    completed_count = 0

    # Sequential fan-out: one pair at a time
    for workflow_id in workflow_ids:
        for model_id in model_ids:
            try:
                loaded = _get_bundle(workflow_id)
                model = get_model(model_id)

                transcript = _run_match_fn(loaded, model, artifact_root=artifact_root, run_id=run_id)

                # Infra gate: a route/transport failure is not model ability —
                # record it as 'invalid' (excluded from leaderboard means), skip
                # judge+scoring. Two shapes: an all-blank transcript with error
                # evidence (infra_blank), and a partial run truncated by a
                # provider transport error after real early steps (infra_error).
                infra_error = None
                if _is_infra_blank(transcript):
                    infra_error = "infra_blank"
                elif _is_infra_contaminated(transcript):
                    infra_error = "infra_error"
                if infra_error is not None:
                    store.record_match(
                        session,
                        run_id=run_id,
                        workflow_id=workflow_id,
                        model_id=model_id,
                        objective_score=None,
                        judged_score=None,
                        total_score=None,
                        judge_missing=True,
                        config={"weights": weights},
                        transcript_path=_save_transcript(
                            transcript, artifact_root, workflow_id, model_id),
                        status="invalid",
                        error=infra_error,
                    )
                    completed_count += 1
                    update_task_progress(session, task_id,
                                         current=completed_count, total=total_pairs)
                    session.commit()
                    continue

                judge_result = _judge_fn(transcript, loaded, post=post)

                obj_score, _passed, _total = scoring.objective_score(transcript, loaded)
                t_score = scoring.total_score(
                    obj_score,
                    judge_result.judged_score,
                    weights=weights,
                    judge_missing=judge_result.judge_missing,
                )

                # Per-check breakdown behind the aggregate scores, persisted so
                # the /arena match drilldown can show where points were won/lost.
                heuristic = scoring.diagnose_heuristic(transcript, loaded)
                breakdown = {
                    "objective": scoring.objective_breakdown(transcript, loaded),
                    "judge": {
                        "rubric_scores": judge_result.rubric_scores,
                        "judged_score": judge_result.judged_score,
                        "judge_missing": judge_result.judge_missing,
                    },
                    # Why/where the model won or lost: deterministic engagement
                    # counts (always present) + the judge's LLM failure analysis.
                    "diagnosis": {
                        "counts": heuristic["summary"],
                        "counts_detail": heuristic,
                        "analysis": judge_result.diagnosis,
                    },
                    "weights": weights or {"obj": 0.5, "judge": 0.5},
                    "objective_score": round(obj_score, 1),
                    "total_score": t_score,
                }

                # Save transcript to disk
                transcript_path = _save_transcript(
                    transcript, artifact_root, workflow_id, model_id)

                store.record_match(
                    session,
                    run_id=run_id,
                    workflow_id=workflow_id,
                    model_id=model_id,
                    objective_score=round(obj_score, 1),
                    judged_score=judge_result.judged_score,
                    total_score=t_score,
                    judge_missing=judge_result.judge_missing,
                    config={"weights": weights},
                    transcript_path=transcript_path,
                    status="scored",
                    score_breakdown=breakdown,
                )

            except Exception as exc:
                error_msg = traceback.format_exc()
                store.record_match(
                    session,
                    run_id=run_id,
                    workflow_id=workflow_id,
                    model_id=model_id,
                    objective_score=None,
                    judged_score=None,
                    total_score=None,
                    judge_missing=True,
                    config={"weights": weights},
                    transcript_path=None,
                    status="failed",
                    error=error_msg,
                )

            completed_count += 1
            update_task_progress(session, task_id, current=completed_count, total=total_pairs)
            session.commit()

    # All pairs processed — always mark completed (individual match failures are ok)
    store.set_run_status(session, run_id, "completed")
    mark_task_finished(session, task_id, status=TaskStatus.COMPLETED.value)
    session.commit()

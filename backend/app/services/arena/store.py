"""Arena persistence store.

Functions operate on a SQLAlchemy session and raise no HTTP exceptions —
callers handle HTTP mapping.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ArenaRun, ArenaMatch


def create_run(
    session: Session,
    workflow_ids: list[str],
    model_ids: list[str],
    weights: dict | None = None,
) -> int:
    """Insert a new ArenaRun in 'queued' status; return its id."""
    run = ArenaRun(
        status="queued",
        workflow_ids=workflow_ids,
        model_ids=model_ids,
        weights=weights,
    )
    session.add(run)
    session.flush()
    return run.id


def record_match(
    session: Session,
    run_id: int,
    workflow_id: str,
    model_id: str,
    *,
    objective_score: float | None,
    judged_score: float | None,
    total_score: float | None,
    judge_missing: bool,
    config: dict,
    transcript_path: str | None,
    status: str,
    error: str | None = None,
    score_breakdown: dict | None = None,
) -> int:
    """Upsert an ArenaMatch row; return its id."""
    existing = (
        session.query(ArenaMatch)
        .filter_by(run_id=run_id, workflow_id=workflow_id, model_id=model_id)
        .one_or_none()
    )
    if existing is not None:
        existing.status = status
        existing.objective_score = objective_score
        existing.judged_score = judged_score
        existing.total_score = total_score
        existing.judge_missing = judge_missing
        existing.config = config
        existing.transcript_path = transcript_path
        existing.error = error
        existing.score_breakdown = score_breakdown
        session.flush()
        return existing.id

    match = ArenaMatch(
        run_id=run_id,
        workflow_id=workflow_id,
        model_id=model_id,
        status=status,
        objective_score=objective_score,
        judged_score=judged_score,
        total_score=total_score,
        judge_missing=judge_missing,
        config=config,
        transcript_path=transcript_path,
        error=error,
        score_breakdown=score_breakdown,
    )
    session.add(match)
    session.flush()
    return match.id


def set_run_status(
    session: Session,
    run_id: int,
    status: str,
    error: str | None = None,
) -> None:
    """Update an ArenaRun's status (and optionally error)."""
    run = session.get(ArenaRun, run_id)
    if run is None:
        return
    run.status = status
    if error is not None:
        run.error = error
    session.flush()


def get_run(session: Session, run_id: int) -> dict | None:
    """Return a run dict with its matches, or None if not found."""
    run = session.get(ArenaRun, run_id)
    if run is None:
        return None
    return _run_to_dict(run)


def list_runs(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (rows, total) ordered by created_at desc."""
    total = session.query(ArenaRun).count()
    rows = (
        session.query(ArenaRun)
        .order_by(ArenaRun.created_at.desc(), ArenaRun.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_run_to_dict(r) for r in rows], total


def get_match_transcript_path(session: Session, match_id: int) -> str | None:
    """Return the transcript_path for a match, or None."""
    match = session.get(ArenaMatch, match_id)
    if match is None:
        return None
    return match.transcript_path


def leaderboard(
    session: Session,
    *,
    run_id: int | None = None,
    tag: str | None = None,
) -> list[dict]:
    """Return leaderboard rows for the latest completed run (or specified run_id).

    Each row: {model_id, mean_total, mean_objective, match_count}.
    Only scored matches (status=='scored') are counted.
    Ordered by mean_total desc, mean_objective desc, model_id asc.
    If tag is given, only matches for workflows with that tag are included.
    Returns [] on no completed run or no scored matches.
    """
    if run_id is None:
        # Find the latest completed run
        run = (
            session.query(ArenaRun)
            .filter(ArenaRun.status == "completed")
            .order_by(ArenaRun.created_at.desc(), ArenaRun.id.desc())
            .first()
        )
        if run is None:
            return []
        run_id = run.id

    # Fetch scored matches for this run
    matches = (
        session.query(ArenaMatch)
        .filter(
            ArenaMatch.run_id == run_id,
            ArenaMatch.status == "scored",
        )
        .all()
    )

    if not matches:
        return []

    # Apply tag filter
    if tag is not None:
        from app.golden_workflows.registry import get_workflow
        filtered = []
        for m in matches:
            try:
                wf = get_workflow(m.workflow_id)
                if tag in wf.tags:
                    filtered.append(m)
            except Exception:
                pass
        matches = filtered
        if not matches:
            return []

    # Aggregate per model
    from collections import defaultdict
    model_totals: dict[str, list[float]] = defaultdict(list)
    model_objectives: dict[str, list[float]] = defaultdict(list)

    for m in matches:
        if m.total_score is not None:
            model_totals[m.model_id].append(m.total_score)
        if m.objective_score is not None:
            model_objectives[m.model_id].append(m.objective_score)

    # Build rows for models that have at least one scored match
    rows = []
    for model_id, totals in model_totals.items():
        objectives = model_objectives.get(model_id, [])
        mean_total = sum(totals) / len(totals)
        mean_objective = sum(objectives) / len(objectives) if objectives else 0.0
        rows.append({
            "model_id": model_id,
            "mean_total": round(mean_total, 1),
            "mean_objective": round(mean_objective, 1),
            "match_count": len(totals),
        })

    # Tie-break: mean_total desc, mean_objective desc, model_id asc
    rows.sort(key=lambda r: (-r["mean_total"], -r["mean_objective"], r["model_id"]))
    return rows


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _match_to_dict(m: ArenaMatch) -> dict:
    return {
        "id": m.id,
        "run_id": m.run_id,
        "workflow_id": m.workflow_id,
        "model_id": m.model_id,
        "status": m.status,
        "objective_score": m.objective_score,
        "judged_score": m.judged_score,
        "total_score": m.total_score,
        "judge_missing": m.judge_missing,
        "config": m.config,
        "score_breakdown": m.score_breakdown,
        "transcript_path": m.transcript_path,
        "error": m.error,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _run_to_dict(run: ArenaRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "workflow_ids": run.workflow_ids,
        "model_ids": run.model_ids,
        "weights": run.weights,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "matches": [_match_to_dict(m) for m in run.matches],
    }

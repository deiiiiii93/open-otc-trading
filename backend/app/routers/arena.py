"""Arena API router.

Endpoints:
    POST  /api/arena/runs           → 202 {run_id, status}
    GET   /api/arena/runs           → {runs:[RunSummary], total}
    GET   /api/arena/runs/{id}      → {run, matches:[MatchSummary]}  (404 unknown)
    GET   /api/arena/matches/{id}/transcript → transcript JSON (404 if missing)
    GET   /api/arena/leaderboard    → {rows:[{model_id, avg_total, avg_objective, matches}]}
    GET   /api/arena/models         → {models:[{slug, zenmux_name, display_name}]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.arena.models import CANDIDATE_MODELS
from app.services.arena import store as arena_store


# ---------------------------------------------------------------------------
# Pydantic shapes
# ---------------------------------------------------------------------------

class RunSummary(BaseModel):
    id: int
    status: str
    created_at: str | None
    workflow_ids: list[str]
    model_ids: list[str]


class MatchSummary(BaseModel):
    id: int
    workflow_id: str
    model_id: str
    status: str
    objective_score: float | None
    judged_score: float | None
    total_score: float | None
    judge_missing: bool
    transcript_path: str | None
    score_breakdown: dict | None = None
    # Corroborating failure reason (e.g. "infra_blank" for invalid matches) —
    # exclusions must be auditable, not just visible as a count.
    error: str | None = None


class CreateRunRequest(BaseModel):
    workflow_ids: list[str]
    model_ids: list[str]
    weights: dict | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def build_arena_router(
    get_db: Callable | None = None,
    submit_async_task_fn: Callable | None = None,
    queue_arena_run_fn: Callable | None = None,
    execute_arena_run_task_fn: Callable | None = None,
    session_factory=None,
    settings=None,
) -> APIRouter:
    """Build and return the arena APIRouter.

    Injectable parameters allow test hermetics without monkeypatching globals.
    """
    router = APIRouter(prefix="/api/arena", tags=["arena"])

    def _get_db():
        if get_db is not None:
            yield from get_db()
        else:
            from app import database
            db = database.SessionLocal()
            try:
                yield db
            finally:
                db.close()

    # ------------------------------------------------------------------
    # POST /api/arena/runs
    # ------------------------------------------------------------------

    @router.post("/runs", status_code=202)
    def create_arena_run(
        payload: CreateRunRequest,
        session=Depends(_get_db),
    ) -> dict[str, Any]:
        from app.services.arena.task import (
            queue_arena_run as _queue,
            execute_arena_run_task as _execute,
        )

        _queue_fn = queue_arena_run_fn or _queue
        _exec_fn = execute_arena_run_task_fn or _execute

        if not payload.workflow_ids:
            raise HTTPException(status_code=422, detail="workflow_ids must not be empty")
        if not payload.model_ids:
            raise HTTPException(status_code=422, detail="model_ids must not be empty")

        try:
            run, task = _queue_fn(
                session,
                workflow_ids=payload.workflow_ids,
                model_ids=payload.model_ids,
                weights=payload.weights,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        session.commit()

        # Submit the async task
        _sf = session_factory
        if _sf is None:
            from app import database
            _sf = database.SessionLocal

        _settings = settings

        if submit_async_task_fn is not None:
            submit_async_task_fn(_exec_fn, task.id, run.id, _sf, settings=_settings)
        else:
            from app.services.task_runner import submit_async_task
            submit_async_task(_exec_fn, task.id, run.id, _sf, settings=_settings)

        return {"run_id": run.id, "status": "queued"}

    # ------------------------------------------------------------------
    # GET /api/arena/runs
    # ------------------------------------------------------------------

    @router.get("/runs")
    def list_runs(
        limit: int = 50,
        offset: int = 0,
        session=Depends(_get_db),
    ) -> dict[str, Any]:
        limit = min(limit, 200)
        rows, total = arena_store.list_runs(session, limit=limit, offset=offset)
        summaries = [
            RunSummary(
                id=r["id"],
                status=r["status"],
                created_at=r.get("created_at"),
                workflow_ids=r.get("workflow_ids") or [],
                model_ids=r.get("model_ids") or [],
            )
            for r in rows
        ]
        return {"runs": [s.model_dump() for s in summaries], "total": total}

    # ------------------------------------------------------------------
    # GET /api/arena/runs/{id}
    # ------------------------------------------------------------------

    @router.get("/runs/{run_id}")
    def get_run(run_id: int, session=Depends(_get_db)) -> dict[str, Any]:
        run_dict = arena_store.get_run(session, run_id)
        if run_dict is None:
            raise HTTPException(status_code=404, detail=f"Arena run {run_id} not found")

        run_summary = RunSummary(
            id=run_dict["id"],
            status=run_dict["status"],
            created_at=run_dict.get("created_at"),
            workflow_ids=run_dict.get("workflow_ids") or [],
            model_ids=run_dict.get("model_ids") or [],
        )

        match_summaries = [
            MatchSummary(
                id=m["id"],
                workflow_id=m["workflow_id"],
                model_id=m["model_id"],
                status=m["status"],
                objective_score=m.get("objective_score"),
                judged_score=m.get("judged_score"),
                total_score=m.get("total_score"),
                judge_missing=m.get("judge_missing", False),
                transcript_path=m.get("transcript_path"),
                score_breakdown=m.get("score_breakdown"),
                error=m.get("error"),
            )
            for m in (run_dict.get("matches") or [])
        ]

        return {
            "run": run_summary.model_dump(),
            "matches": [m.model_dump() for m in match_summaries],
        }

    # ------------------------------------------------------------------
    # GET /api/arena/matches/{id}/transcript
    # ------------------------------------------------------------------

    @router.get("/matches/{match_id}/transcript")
    def get_transcript(match_id: int, session=Depends(_get_db)) -> Any:
        path_str = arena_store.get_match_transcript_path(session, match_id)
        if path_str is None:
            raise HTTPException(
                status_code=404,
                detail=f"Match {match_id} has no transcript path",
            )
        path = Path(path_str)
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Transcript file not found: {path_str}",
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load transcript: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # GET /api/arena/leaderboard
    # ------------------------------------------------------------------

    @router.get("/leaderboard")
    def get_leaderboard(
        run_id: int | None = None,
        tag: str | None = None,
        session=Depends(_get_db),
    ) -> dict[str, Any]:
        rows = arena_store.leaderboard(session, run_id=run_id, tag=tag)
        # Ranking is by the deterministic objective axis (spec D5 — no blend);
        # subjective is advisory (mean ± stdev + mode). `rank` is shared on ties.
        renamed = [
            {
                "model_id": r["model_id"],
                "rank": r["rank"],
                # Ability card (spec B5): OVR is the headline ranking axis; the
                # full card_mean stat block feeds the radar. Null for uncarded rows.
                "ovr": (r.get("card_mean") or {}).get("ovr"),
                "card_mean": r.get("card_mean"),
                "avg_objective": r["mean_objective"],
                "subjective_mean": r["subjective_mean"],
                "subjective_stdev": r["subjective_stdev"],
                "subjective_mode": r["subjective_mode"],
                "matches": r["match_count"],
                "invalid": r["invalid_count"],
            }
            for r in rows
        ]
        return {"rows": renamed}

    # ------------------------------------------------------------------
    # GET /api/arena/models
    # ------------------------------------------------------------------

    @router.get("/models")
    def list_models() -> dict[str, Any]:
        return {
            "models": [
                {
                    "slug": m.slug,
                    "zenmux_name": m.zenmux_name,
                    "display_name": m.display_name,
                }
                for m in CANDIDATE_MODELS
            ]
        }

    return router

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
import shutil
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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
    # {model_slug: [effort | null, ...]} — one entry per ARM this model ran at;
    # null is the explicit unpinned (vendor-default) arm, and a model absent from
    # the map ran once at its vendor default. Surfaced on the list so a board's
    # regime is visible without opening a match. Legacy rows stored a bare scalar;
    # store._run_to_dict normalises it to a list on read.
    reasoning_efforts: dict[str, list[str | None]] = Field(default_factory=dict)


class MatchSummary(BaseModel):
    id: int
    workflow_id: str
    model_id: str
    # Which regime produced this row; null = unpinned. Part of the contestant key,
    # so (model_id, reasoning_effort) identifies the arm this match scored.
    reasoning_effort: str | None = None
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
    trials: int = Field(default=2, ge=1, le=10)
    # Per-model effort ARMS {model_slug: [level | null, ...]}. Two entries for one
    # model make it two contestants that rank against each other; null is the
    # explicit unpinned arm. Omitted/absent = do not pin, so that contestant runs
    # at its own vendor default — what boards #8-#104 measured. Per-model because
    # the ladders differ (GLM-5.2 accepts only high/max, five contestants have no
    # levels at all), so no single value could express a pinned mixed field. A
    # bare string is accepted for backward compatibility with the single-arm form.
    # Validated per arm in queue_arena_run so a bad level fails at launch rather
    # than per-match.
    reasoning_efforts: dict[str, list[str | None] | str] | None = None


class DeleteRunsRequest(BaseModel):
    run_ids: list[int]


class MergeRunsRequest(BaseModel):
    source_run_ids: list[int]


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
            run_id, task = _queue_fn(
                session,
                workflow_ids=payload.workflow_ids,
                model_ids=payload.model_ids,
                weights=payload.weights,
                trials=payload.trials,
                reasoning_efforts=payload.reasoning_efforts,
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
            submit_async_task_fn(_exec_fn, task.id, run_id, _sf, settings=_settings)
        else:
            from app.services.task_runner import submit_async_task
            submit_async_task(_exec_fn, task.id, run_id, _sf, settings=_settings)

        return {"run_id": run_id, "status": "queued"}

    # ------------------------------------------------------------------
    # POST /api/arena/runs/delete
    # ------------------------------------------------------------------

    @router.post("/runs/delete")
    def delete_arena_runs(
        payload: DeleteRunsRequest,
        session=Depends(_get_db),
    ) -> dict[str, Any]:
        if not payload.run_ids:
            raise HTTPException(status_code=400, detail="run_ids must not be empty")

        out = arena_store.delete_runs(session, payload.run_ids)

        # Persist the deletion FIRST — the filesystem cleanup below is best-effort
        # (OSError-tolerant); if it partially fails, the DB rows must already be
        # gone rather than leaving a committed-looking response with an uncommitted
        # transaction.
        session.commit()

        files_removed = 0
        for p in out["transcript_paths"]:
            try:
                Path(p).unlink()
                files_removed += 1
            except OSError:
                pass

        if settings is not None:
            arena_root = Path(settings.artifact_dir) / "arena"
            for rid in out["deleted_run_ids"]:
                d = arena_root / str(rid)
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
                    files_removed += 1

        return {
            "deleted_run_ids": out["deleted_run_ids"],
            "match_count": out["match_count"],
            "files_removed": files_removed,
        }

    # ------------------------------------------------------------------
    # POST /api/arena/runs/merge
    # ------------------------------------------------------------------

    @router.post("/runs/merge")
    def merge_arena_runs(
        payload: MergeRunsRequest,
        session=Depends(_get_db),
    ) -> dict[str, Any]:
        try:
            new_run_id = arena_store.merge_runs(session, payload.source_run_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session.commit()

        return {"run_id": new_run_id}

    # ------------------------------------------------------------------
    # GET /api/arena/workflows
    # ------------------------------------------------------------------

    @router.get("/workflows")
    def list_arena_workflows() -> dict[str, Any]:
        from app.golden_workflows.registry import list_workflows

        return {
            "workflows": [
                {
                    "id": w.id,
                    "title": w.title,
                    "tags": w.tags,
                    "step_count": len(w.steps),
                }
                for w in list_workflows()
            ]
        }

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
                reasoning_efforts=r.get("reasoning_efforts") or {},
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
            reasoning_efforts=run_dict.get("reasoning_efforts") or {},
        )

        match_summaries = [
            MatchSummary(
                id=m["id"],
                workflow_id=m["workflow_id"],
                model_id=m["model_id"],
                reasoning_effort=m.get("reasoning_effort"),
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
                # Named explicitly because this projection is an ALLOWLIST: a key
                # the store gains is served only if it appears here (no
                # response_model on this route to carry it automatically).
                "reasoning_effort": r["reasoning_effort"],
                "rank": r["rank"],
                # Ability card (spec B5): OVR is the headline ranking axis; the
                # full card_mean stat block feeds the radar. Null for uncarded rows.
                "ovr": (r.get("card_mean") or {}).get("ovr"),
                "card_mean": r.get("card_mean"),
                # Coverage: how many of this model's scored matches are carded.
                # ovr/card_mean are null unless carded_count == matches (full sample).
                "carded_count": r.get("carded_count", 0),
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
        from app.services.arena.models import arena_model_to_selection
        from app.services.deep_agent.channel_registry import get_registry
        from app.services.deep_agent.model_factory import (
            VALID_REASONING_EFFORTS, effort_rejection,
        )

        registry = get_registry()

        def _efforts(model) -> list[str]:
            """This contestant's effort ladder, mirroring what a LAUNCH will accept.

            Derived through the same `effort_rejection` seam `queue_arena_run` uses,
            rather than reading the ladder directly, so the panel can never offer a
            level the launch refuses. That matters beyond the ladder: glm-5.2,
            minimax-m3, qwen3.7-max and longcat-2.0 are routed with
            `protocol: anthropic`, whose client cannot carry ANY effort — reading
            only the measured ladder showed them a full menu that would 422.

            Empty means no level is usable for this contestant. An unmeasured or
            unknown model keeps the permissive outer bound, because the server does
            not gate it either.
            """
            selection = arena_model_to_selection(model)
            return [
                level for level in VALID_REASONING_EFFORTS
                if effort_rejection(
                    registry, selection["channel"], selection["provider"],
                    selection["model"], level,
                ) is None
            ]

        return {
            "models": [
                {
                    "slug": m.slug,
                    "zenmux_name": m.zenmux_name,
                    "display_name": m.display_name,
                    "reasoning_efforts": _efforts(m),
                }
                for m in CANDIDATE_MODELS
            ]
        }

    return router

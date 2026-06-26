"""Goal-mode HTTP surface (spec §G/§B).

Thin lifecycle endpoints over ``GoalRunService``: the ``/goal`` command frames a goal
(returning a contract to ratify or clarification questions), then ratify / resume /
cancel drive the run. The grader is attached on the desk turn itself (AgentService),
not here — these endpoints only manage the run record.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import StartGoalRequest
from ..services.deep_agent.goal_mode import (
    ContractValidationError,
    GoalRunService,
    GoalStateError,
)

_BASE = "/api/chat/threads/{thread_id}/goal"


def build_goal_router(service: GoalRunService) -> APIRouter:
    router = APIRouter()

    @router.post(_BASE)
    def start_goal(thread_id: str, payload: StartGoalRequest):
        try:
            result = service.start(thread_id, payload.goal_text, payload.mode)
        except ContractValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except GoalStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post(_BASE + "/ratify")
    def ratify_goal(thread_id: str):
        return _transition(service.ratify, thread_id)

    @router.post(_BASE + "/resume")
    def resume_goal(thread_id: str):
        return _transition(service.resume, thread_id)

    @router.post(_BASE + "/cancel")
    def cancel_goal(thread_id: str):
        return _transition(service.cancel, thread_id)

    @router.get(_BASE)
    def get_goal(thread_id: str):
        state = service.active(thread_id)
        return state.model_dump(mode="json") if state is not None else None

    return router


def _transition(fn, thread_id: str):
    try:
        return fn(thread_id).model_dump(mode="json")
    except GoalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

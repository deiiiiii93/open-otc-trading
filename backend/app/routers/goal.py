"""Goal-mode HTTP surface (spec §G/§B).

Thin lifecycle endpoints over ``GoalRunService``: the ``/goal`` command frames a goal
(returning a contract to ratify or clarification questions), then ratify / resume /
cancel drive the run. The grader is attached on the desk turn itself (AgentService),
not here — these endpoints only manage the run record.
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from ..schemas import StartGoalRequest
from ..services.deep_agent.goal_mode import (
    ContractValidationError,
    GoalRunService,
    GoalStateError,
)

_BASE = "/api/chat/threads/{thread_id}/goal"


def build_goal_router(
    service: GoalRunService,
    thread_guard: Callable[[str], None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def _guard(thread_id: str) -> None:
        if thread_guard is not None:
            thread_guard(thread_id)

    @router.post(_BASE)
    def start_goal(thread_id: str, payload: StartGoalRequest):
        _guard(thread_id)
        try:
            result = service.start(thread_id, payload.goal_text, payload.mode)
        except ContractValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except GoalStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @router.post(_BASE + "/ratify")
    def ratify_goal(thread_id: str):
        _guard(thread_id)
        return _transition(service.ratify, thread_id)

    @router.post(_BASE + "/resume")
    def resume_goal(thread_id: str):
        _guard(thread_id)
        return _transition(service.resume, thread_id)

    @router.post(_BASE + "/cancel")
    def cancel_goal(thread_id: str):
        _guard(thread_id)
        return _transition(service.cancel, thread_id)

    @router.get(_BASE)
    def get_goal(thread_id: str):
        _guard(thread_id)
        state = service.active(thread_id)
        return state.model_dump(mode="json") if state is not None else None

    @router.get(_BASE + "/contract")
    def get_goal_contract(thread_id: str):
        _guard(thread_id)
        return service.contract_view(thread_id)

    return router


def _transition(fn, thread_id: str):
    try:
        return fn(thread_id).model_dump(mode="json")
    except GoalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

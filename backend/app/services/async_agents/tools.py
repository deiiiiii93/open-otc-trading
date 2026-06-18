"""Three LangChain BaseTool subclasses for orchestrator dispatch.

Each tool resolves parent_thread_id from RunnableConfig.configurable.thread_id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ... import database as _database
from ...models import AgentMessage, TaskRun, TaskStatus
from ..audit import record_audit
from ..deep_agent.run_control import request_async_task_drain
from .runner import TooManyRunningError, start_async_agent_task


def _latest_parent_user_meta(
    session: Any, parent_thread_id: int
) -> dict[str, Any]:
    """Read meta from the most recent user message on the parent thread.

    The orchestrator stamps each user turn with accounting_date and
    model_selection (agents.py:499-505); the async worker needs both to
    mirror the parent run instead of defaulting to wall-clock UTC + the
    registry default model.
    """
    row = (
        session.query(AgentMessage)
        .filter(
            AgentMessage.thread_id == parent_thread_id,
            AgentMessage.role == "user",
        )
        .order_by(AgentMessage.id.desc())
        .first()
    )
    if row is None or not isinstance(row.meta, dict):
        return {}
    return row.meta


_ACTIVE = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)
_TERMINAL = (
    TaskStatus.COMPLETED.value,
    TaskStatus.COMPLETED_WITH_ERRORS.value,
    TaskStatus.FAILED.value,
)


def _resolve_parent_thread_id(config: RunnableConfig | None) -> int | None:
    if not config:
        return None
    configurable = config.get("configurable") if isinstance(config, dict) else None
    if not configurable:
        return None
    raw = configurable.get("thread_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_task_summaries(
    session,
    *,
    parent_thread_id: int,
    include_terminal: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Shared query used by both the tool and the API endpoint."""
    q = session.query(TaskRun).filter(
        TaskRun.kind == "async_agent",
        TaskRun.parent_thread_id == parent_thread_id,
    )
    if not include_terminal:
        q = q.filter(TaskRun.status.in_(_ACTIVE))
    rows = q.order_by(TaskRun.started_at.desc().nulls_last()).limit(limit).all()
    return [
        {
            "task_id": row.id,
            "description": row.description or "",
            "status": row.status,
            "awaiting_approval": (row.message or "") == "awaiting approval",
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "last_message_preview": _preview(row),
        }
        for row in rows
    ]


def _preview(row: TaskRun) -> str | None:
    payload = row.result_payload or {}
    text = payload.get("final_text")
    if isinstance(text, str):
        return text[:120]
    return None


def _infer_proxy(prompt: str, inputs: dict[str, Any] | None) -> str:
    """Heuristic tagging for audit observability."""
    lower = prompt.lower()
    written = any(
        kw in lower
        for kw in ("narrative", "summary", "audit", "comparison", "draft", "write")
    )
    parallel = any(
        kw in lower
        for kw in (
            "while",
            "and also",
            "in parallel",
            "in the background",
            "let me know when",
            "come back",
        )
    )
    if written and parallel:
        return "multiple"
    if written:
        return "written_artifact"
    if parallel:
        return "user_signal"
    return "5plus_calls"


class StartAsyncAgentInput(BaseModel):
    description: str = Field(..., max_length=80)
    prompt: str = Field(..., max_length=8000)
    inputs: dict[str, Any] | None = None


class StartAsyncAgentTool(BaseTool):
    name: str = "start_async_agent"
    description: str = (
        "Spawn a background general-purpose agent with a self-contained brief. "
        "Returns a task_id. The agent runs in parallel with the chat; its "
        "result auto-posts as a new assistant message in this thread."
    )
    args_schema: type[BaseModel] = StartAsyncAgentInput

    def _run(
        self,
        description: str,
        prompt: str,
        inputs: dict[str, Any] | None = None,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        parent_thread_id = _resolve_parent_thread_id(config)
        if parent_thread_id is None:
            return {
                "ok": False,
                "error": "no_parent_thread",
                "message": "thread_id missing from config",
            }

        with _database.SessionLocal() as session:
            parent_meta = _latest_parent_user_meta(session, parent_thread_id)
            accounting_date = parent_meta.get("accounting_date")
            if not isinstance(accounting_date, str) or not accounting_date:
                accounting_date = None
            raw_selection = parent_meta.get("model_selection")
            model_selection = (
                raw_selection if isinstance(raw_selection, dict) else None
            )
            try:
                task_id = start_async_agent_task(
                    session,
                    parent_thread_id=parent_thread_id,
                    description=description,
                    prompt=prompt,
                    inputs=inputs,
                    accounting_date=accounting_date,
                    model_selection=model_selection,
                )
            except TooManyRunningError as exc:
                session.rollback()
                return {
                    "ok": False,
                    "error": "too_many_running",
                    "message": str(exc),
                }
            record_audit(
                session,
                event_type="async_agent.started",
                actor="desk_user",
                subject_type="thread",
                subject_id=parent_thread_id,
                payload={
                    "task_id": task_id,
                    "description": description,
                    "proxy_fired": _infer_proxy(prompt, inputs),
                },
            )
            session.commit()
        return {"ok": True, "task_id": task_id, "status": "queued"}

    async def _arun(
        self,
        description: str,
        prompt: str,
        inputs: dict[str, Any] | None = None,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        # Mirror _run's signature so LangChain's config auto-injection on the
        # async path (agent.ainvoke/streaming) finds the RunnableConfig param.
        return self._run(description, prompt, inputs, config=config)


class ListAsyncAgentsInput(BaseModel):
    include_terminal: bool = False
    limit: int = Field(20, ge=1, le=100)


class ListAsyncAgentsTool(BaseTool):
    name: str = "list_async_agents"
    description: str = (
        "List background agents on this thread. By default only active "
        "(queued/running) tasks; set include_terminal=true to see completed/failed."
    )
    args_schema: type[BaseModel] = ListAsyncAgentsInput

    def _run(
        self,
        include_terminal: bool = False,
        limit: int = 20,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        parent_thread_id = _resolve_parent_thread_id(config)
        if parent_thread_id is None:
            return {"tasks": []}
        with _database.SessionLocal() as session:
            tasks = build_task_summaries(
                session,
                parent_thread_id=parent_thread_id,
                include_terminal=include_terminal,
                limit=limit,
            )
        return {"tasks": tasks}

    async def _arun(
        self,
        include_terminal: bool = False,
        limit: int = 20,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(include_terminal, limit, config=config)


class CancelAsyncAgentInput(BaseModel):
    task_id: int
    reason: str | None = Field(None, max_length=200)


class CancelAsyncAgentTool(BaseTool):
    name: str = "cancel_async_agent"
    description: str = (
        "Stop a background agent. Best-effort; running tasks finish their "
        "current step."
    )
    args_schema: type[BaseModel] = CancelAsyncAgentInput

    def _run(
        self,
        task_id: int,
        reason: str | None = None,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        parent_thread_id = _resolve_parent_thread_id(config)
        if parent_thread_id is None:
            return {"ok": False, "task_id": task_id, "error": "no_parent_thread"}

        with _database.SessionLocal() as session:
            row = session.get(TaskRun, task_id)
            if row is None or row.kind != "async_agent":
                return {"ok": False, "task_id": task_id, "error": "not_found"}
            if row.parent_thread_id != parent_thread_id:
                return {"ok": False, "task_id": task_id, "error": "not_owned"}
            prev = row.status
            if prev in _TERMINAL:
                return {
                    "ok": False,
                    "task_id": task_id,
                    "previous_status": prev,
                    "new_status": prev,
                    "note": "already terminal",
                }
            # Always set cancel_requested: the worker may already be queued in
            # the thread pool and would otherwise wake up, find status mutated
            # but cancel_requested False, and resurrect the row to run.
            row.cancel_requested = True
            drain_requested = False
            if prev == TaskStatus.QUEUED.value:
                row.status = TaskStatus.FAILED.value
                row.message = "cancelled before start"
                row.finished_at = datetime.utcnow()
                if row.started_at is None:
                    row.started_at = row.finished_at
                new_status = row.status
            elif row.message == "awaiting approval":
                # HITL-paused: no worker is active to observe cancel_requested.
                # Terminalize directly so the row stops counting against the
                # concurrency cap and stops appearing as 'active'.
                row.status = TaskStatus.FAILED.value
                row.message = "cancelled while awaiting approval"
                row.finished_at = datetime.utcnow()
                if row.started_at is None:
                    row.started_at = row.finished_at
                new_status = row.status
            else:
                new_status = prev
                request_reason = reason or "cancelled"
                drain_requested = request_async_task_drain(task_id, request_reason)
            record_audit(
                session,
                event_type="async_agent.cancelled",
                actor="desk_user",
                subject_type="thread",
                subject_id=parent_thread_id,
                payload={"task_id": task_id, "reason": reason},
            )
            session.commit()
        return {
            "ok": True,
            "task_id": task_id,
            "previous_status": prev,
            "new_status": new_status,
            "drain_requested": drain_requested,
        }

    async def _arun(
        self,
        task_id: int,
        reason: str | None = None,
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(task_id, reason, config=config)

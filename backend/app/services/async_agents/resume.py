"""Resume an async-agent run after a HITL approve/reject decision."""
from __future__ import annotations

from typing import Any

from ...models import TaskRun, TaskStatus
from ..task_runner import submit_async_task


class TaskNotResumableError(Exception):
    """The async task is cancelled or terminal and cannot be resumed."""

    def __init__(self, task_id: int, status: str, cancel_requested: bool):
        super().__init__(
            f"async_agent task {task_id} is not resumable "
            f"(status={status}, cancel_requested={cancel_requested})"
        )
        self.task_id = task_id
        self.status = status
        self.cancel_requested = cancel_requested

# Indirection point so tests can monkeypatch
_submit = submit_async_task


def resume_async_agent_interrupt(
    *,
    task_id: int,
    decision: str,
    message: str | None,
) -> None:
    """Submit `_resume_run` to the thread pool with the resume decision."""
    from ... import database

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            raise ValueError(f"async_agent task {task_id} not found")
        if task.kind != "async_agent":
            raise ValueError(f"task {task_id} is not an async_agent")
        # Prevent resurrection: the HITL card may persist in the UI after
        # cancellation. Without this guard a later approve click would
        # re-run the agent on a cancelled/terminal task.
        if task.cancel_requested or task.status in (
            TaskStatus.FAILED.value,
            TaskStatus.COMPLETED.value,
        ):
            raise TaskNotResumableError(
                task_id, task.status, bool(task.cancel_requested)
            )
    _submit(_resume_run, task_id, decision, message)


def _build_agent_for_resume(
    *, model: Any, tools: Any, checkpointer: Any, task_id: int
) -> Any:
    """Factory hook isolated for test monkeypatch."""
    from .agent import build_async_agent

    return build_async_agent(
        model=model, tools=tools, checkpointer=checkpointer, task_id=task_id
    )


def _resume_run(task_id: int, decision: str, message: str | None) -> None:
    """Worker entry: re-build the async agent and ainvoke with the resume command."""
    import asyncio

    asyncio.run(_resume_run_async(task_id, decision, message))


async def _resume_run_async(
    task_id: int, decision: str, message: str | None
) -> None:
    from langgraph.errors import GraphDrained

    from ... import database
    from ...config import get_settings
    from ..audit import record_audit
    from ..deep_agent.channel_registry import get_registry
    from ..deep_agent.checkpointer import build_async_checkpointer
    from ..deep_agent.hitl import build_resume_command
    from ..deep_agent.model_factory import build_agent_model
    from ..deep_agent.run_control import (
        new_run_control,
        register_async_task_control,
    )
    from ..deep_agent.runtime_config import graph_run_config
    from ..task_runner import mark_task_finished
    from ...models import TaskStatus
    from ..agents import select_async_agent_tools
    from .autopost import handle as autopost_handle
    from .bubble_up import handle as bubble_up_handle

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            return
        # Re-check cancellation: a CancelAsyncAgentTool call can land between
        # the approval click that enqueued this worker and the worker starting.
        # Without this the resume would execute the approved tool call anyway.
        if task.cancel_requested or task.status in (
            TaskStatus.FAILED.value,
            TaskStatus.COMPLETED.value,
        ):
            return
        parent_thread_id = task.parent_thread_id
        # task.message has been overwritten by mark_task_running/bubble_up by
        # now; the durable dispatch-time copy lives in result_payload (see
        # start_async_agent_task).
        durable = task.result_payload if isinstance(task.result_payload, dict) else {}
        raw_selection = durable.get("model_selection")
        model_selection = (
            raw_selection if isinstance(raw_selection, dict) else None
        )

    settings = get_settings()
    registry = get_registry()
    model = build_agent_model(registry, model_selection)
    if model is None:
        with database.SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="no LLM channel configured (resume)",
            )
            session.commit()
        return

    command = build_resume_command(decision, message=message)
    control = new_run_control()
    try:
        async with build_async_checkpointer(settings) as checkpointer:
            agent = _build_agent_for_resume(
                model=model,
                tools=select_async_agent_tools(),
                checkpointer=checkpointer,
                task_id=task_id,
            )
            config = graph_run_config(
                settings,
                thread_id=f"async:{parent_thread_id}:{task_id}",
                trace_meta={"thread_id": parent_thread_id, "task_id": task_id},
            )
            with register_async_task_control(task_id, control):
                try:
                    await agent.ainvoke(command, config=config, control=control)
                except TypeError as exc:
                    if "control" not in str(exc):
                        raise
                    await agent.ainvoke(command, config=config)
            state = await agent.aget_state(config)
    except GraphDrained as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        with database.SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message=f"resume drained: {reason}"[:500],
            )
            session.commit()
        return
    except Exception as exc:
        # Resume runs in a background executor, so without this catch the
        # parent action is marked confirmed while the task stays running
        # with no error recorded. Mirror _run_async's failure path.
        with database.SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message=f"resume failed: {str(exc)[:480]}",
            )
            session.commit()
        return

    interrupts: list[Any] = []
    if state and getattr(state, "tasks", None):
        for t in state.tasks:
            interrupts.extend(getattr(t, "interrupts", []) or [])

    with database.SessionLocal() as session:
        # Mirror _run_async: a cancel landing during resume should discard
        # the result instead of bubbling up or autoposting.
        cancelled_now = session.get(TaskRun, task_id)
        if cancelled_now is not None and cancelled_now.cancel_requested:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="cancelled during resume",
            )
            session.commit()
            return
        if interrupts:
            bubble_up_handle(session, task_id=task_id, interrupts=interrupts)
        else:
            autopost_handle(
                session,
                task_id=task_id,
                state_values=getattr(state, "values", {}) or {},
            )
        record_audit(
            session,
            event_type="async_agent.resumed",
            actor="desk_user",
            subject_type="thread",
            subject_id=parent_thread_id,
            payload={"task_id": task_id, "decision": decision},
        )
        session.commit()

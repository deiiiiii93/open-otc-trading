"""Async-agent runner: lifecycle, dispatch, brief composition."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models import TaskRun, TaskStatus
from .policy import MAX_CONCURRENT_PER_THREAD, scratch_dir_for_task


_ACTIVE_STATUSES = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)


class TooManyRunningError(Exception):
    """Raised when a thread's per-thread async-agent cap is exceeded."""

    def __init__(self, cap: int):
        super().__init__(
            f"This thread already has {cap} background agents in flight. "
            f"Cancel one or wait."
        )
        self.cap = cap


def compose_task_brief(
    *,
    task_id: int,
    parent_thread_id: int,
    prompt: str,
    inputs: dict[str, Any] | None,
    accounting_date: str,
) -> HumanMessage:
    """Compose the first HumanMessage for the async agent.

    Combines orchestrator's prompt (free-text briefing), structured inputs,
    and the framework metadata block (task_id, scratch dir, accounting
    anchor, parent_thread_id for traceability). The name ``framework_meta``
    avoids overloading "envelope", which is a capability-scope concept on
    the orchestrator side.
    """
    scratch = scratch_dir_for_task(task_id)

    if inputs:
        inputs_block = "Inputs (structured context):\n" + json.dumps(
            inputs, indent=2, default=str
        )
    else:
        inputs_block = "Inputs: none. (no structured inputs)"

    framework_meta = (
        "=== Framework metadata (injected) ===\n"
        f"task_id: {task_id}\n"
        f"parent_thread_id: {parent_thread_id}\n"
        f"scratch_dir: {scratch}\n"
        f"Accounting anchor: {accounting_date}"
    )

    body = (
        "=== Orchestrator brief ===\n"
        f"{prompt}\n\n"
        f"=== {inputs_block} ===\n\n"
        f"{framework_meta}"
    )
    return HumanMessage(content=body)


def _count_active_for_thread(session: Session, parent_thread_id: int) -> int:
    return (
        session.query(func.count(TaskRun.id))
        .filter(
            TaskRun.kind == "async_agent",
            TaskRun.parent_thread_id == parent_thread_id,
            TaskRun.status.in_(_ACTIVE_STATUSES),
        )
        .scalar()
        or 0
    )


def start_async_agent_task(
    session: Session,
    *,
    parent_thread_id: int,
    description: str,
    prompt: str,
    inputs: dict[str, Any] | None,
    accounting_date: str | None = None,
    model_selection: dict[str, Any] | None = None,
    _submit: Callable[..., Any] | None = None,
) -> int:
    """Insert a QUEUED TaskRun row and submit the runner to the thread pool.

    Returns the new task_id. Raises TooManyRunningError if the per-thread cap
    is exceeded. The `_submit` parameter is for test injection — defaults to
    task_runner.submit_async_task at runtime.
    """
    active = _count_active_for_thread(session, parent_thread_id)
    if active >= MAX_CONCURRENT_PER_THREAD:
        raise TooManyRunningError(MAX_CONCURRENT_PER_THREAD)

    payload = {
        "prompt": prompt,
        "inputs": inputs or {},
        "accounting_date": accounting_date,
        "model_selection": model_selection,
    }
    # Durable copy of dispatch-time fields the resume worker needs.
    # task.message is overwritten by mark_task_running/bubble_up, so the
    # resume path reads model_selection from here instead.
    durable_payload = {
        "model_selection": model_selection,
        "accounting_date": accounting_date,
    }
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.QUEUED.value,
        parent_thread_id=parent_thread_id,
        description=description[:120],
        message=json.dumps(payload, default=str),
        result_payload=durable_payload,
    )
    session.add(row)
    session.flush()
    # Commit before submit: the worker opens its own SessionLocal() and
    # cannot observe an uncommitted row, so a fast-starting worker would
    # find task is None and leave the row stuck in QUEUED.
    session.commit()
    task_id = row.id

    submit = _submit
    if submit is None:
        from ..task_runner import submit_async_task

        submit = submit_async_task
    submit(_run, task_id)
    return task_id


def _run(task_id: int) -> None:
    """Worker entry: build agent, invoke, dispatch to bubble_up/autopost."""
    import asyncio

    asyncio.run(_run_async(task_id))


async def _run_async(task_id: int) -> None:
    from datetime import datetime

    from langgraph.errors import GraphDrained

    from ... import database
    from ...config import get_settings
    from ..deep_agent.channel_registry import get_registry
    from ..agents import select_async_agent_tools
    from ..deep_agent.checkpointer import build_async_checkpointer
    from ..deep_agent.model_factory import build_agent_model
    from ..deep_agent.run_control import (
        new_run_control,
        register_async_task_control,
    )
    from ..deep_agent.runtime_config import graph_run_config
    from ..task_runner import mark_task_finished, mark_task_running
    from .agent import build_async_agent
    from .autopost import handle as autopost_handle
    from .bubble_up import handle as bubble_up_handle

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            return
        if task.cancel_requested or task.status != TaskStatus.QUEUED.value:
            # Either cancellation flipped the flag, or some other path moved
            # the row out of QUEUED (cancel-before-start, stale recovery,
            # double-submit). Don't resurrect a non-QUEUED row.
            if (
                task.cancel_requested
                and task.status not in (TaskStatus.FAILED.value, TaskStatus.COMPLETED.value)
            ):
                mark_task_finished(
                    session,
                    task_id,
                    status=TaskStatus.FAILED.value,
                    message="cancelled before start",
                )
                session.commit()
            return
        payload = json.loads(task.message or "{}")
        parent_thread_id = task.parent_thread_id
        description = task.description or "async task"
        accounting_date = payload.get("accounting_date")
        raw_selection = payload.get("model_selection")
        model_selection = (
            raw_selection if isinstance(raw_selection, dict) else None
        )
        mark_task_running(session, task_id, message=f"running: {description}")
        session.commit()

    settings = get_settings()
    registry = get_registry()
    # Use parent's model_selection so a non-default user choice is preserved.
    # Without this the worker silently uses the registry default (or fails
    # if the default channel is unhealthy).
    model = build_agent_model(registry, model_selection)
    if model is None:
        with database.SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="no LLM channel configured",
            )
            session.commit()
        return

    # Wrap the entire setup + invoke + state-fetch block: any exception
    # (checkpointer init, build_async_agent, ainvoke, aget_state) must mark
    # the task FAILED, else the row stays RUNNING indefinitely.
    control = new_run_control()
    try:
        async with build_async_checkpointer(settings) as checkpointer:
            agent = build_async_agent(
                model=model,
                tools=select_async_agent_tools(),
                checkpointer=checkpointer,
                task_id=task_id,
            )
            brief = compose_task_brief(
                task_id=task_id,
                parent_thread_id=parent_thread_id,
                prompt=payload.get("prompt", ""),
                inputs=payload.get("inputs"),
                # Use the parent chat's anchor when the start tool captured it.
                # Fall back to today's UTC only when the parent didn't set one.
                accounting_date=accounting_date
                or datetime.utcnow().date().isoformat(),
            )
            config = graph_run_config(
                settings, thread_id=f"async:{parent_thread_id}:{task_id}"
            )
            with register_async_task_control(task_id, control):
                try:
                    await agent.ainvoke(
                        {"messages": [brief]},
                        config=config,
                        control=control,
                    )
                except TypeError as exc:
                    if "control" not in str(exc):
                        raise
                    await agent.ainvoke({"messages": [brief]}, config=config)
            state = await agent.aget_state(config)
    except GraphDrained as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        with database.SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message=f"drained: {reason}"[:500],
            )
            session.commit()
        return
    except Exception as exc:
        with database.SessionLocal() as session:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message=str(exc)[:500],
            )
            session.commit()
        return

    interrupts: list[Any] = []
    if state and getattr(state, "tasks", None):
        for t in state.tasks:
            interrupts.extend(getattr(t, "interrupts", []) or [])

    with database.SessionLocal() as session:
        # Re-check cancellation: a CancelAsyncAgentTool call during the run
        # only flips cancel_requested. We can't interrupt the graph mid-step,
        # but we can suppress bubble_up/autopost and mark the task FAILED.
        cancelled_now = session.get(TaskRun, task_id)
        if cancelled_now is not None and cancelled_now.cancel_requested:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="cancelled during run",
            )
            session.commit()
            return
        if interrupts:
            bubble_up_handle(session, task_id=task_id, interrupts=interrupts)
            session.commit()
            return
        autopost_handle(
            session,
            task_id=task_id,
            state_values=getattr(state, "values", {}) or {},
        )
        session.commit()

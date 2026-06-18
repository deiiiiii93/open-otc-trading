"""Project subagent Interrupts into parent-thread AgentMessages."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from langgraph.types import Interrupt
from sqlalchemy.orm import Session

from ...models import AgentMessage, AgentThread, TaskRun
from ..audit import record_audit
from ..deep_agent.hitl import pending_actions_from_interrupts


def handle(
    session: Session,
    *,
    task_id: int,
    interrupts: list[Interrupt],
) -> AgentMessage:
    """Write an awaiting_confirmation AgentMessage on the parent thread."""
    task = session.get(TaskRun, task_id)
    if task is None or task.parent_thread_id is None:
        raise ValueError(
            f"async_agent task {task_id} not found or has no parent thread"
        )

    # Async pending actions don't have a persona (the Literal allows only the
    # three persona names); the async_task_id field below conveys provenance.
    proposals = pending_actions_from_interrupts(interrupts, persona=None)
    pending_dicts: list[dict[str, Any]] = []
    for proposal in proposals:
        d = proposal.model_dump(mode="json")
        d["async_task_id"] = task_id
        pending_dicts.append(d)

    description = task.description or f"task #{task_id}"
    first_tool = pending_dicts[0]["tool_name"] if pending_dicts else "unknown"
    msg = AgentMessage(
        thread_id=task.parent_thread_id,
        role="assistant",
        character="async_agent",
        content=(
            f"Background task '{description}' wants approval for "
            f"{first_tool}."
        ),
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "awaiting_confirmation",
            "async_task_id": task_id,
            "pending_actions": pending_dicts,
            "interrupt_ids": [intr.id for intr in interrupts],
        },
    )
    session.add(msg)
    task.message = "awaiting approval"
    thread = session.get(AgentThread, task.parent_thread_id)
    if thread is not None:
        thread.updated_at = datetime.utcnow()
    session.flush()

    record_audit(
        session,
        event_type="async_agent.awaiting_approval",
        actor="system",
        subject_type="thread",
        subject_id=task.parent_thread_id,
        payload={
            "task_id": task_id,
            "tool_name": first_tool,
            "interrupt_id": interrupts[0].id if interrupts else None,
        },
    )
    return msg

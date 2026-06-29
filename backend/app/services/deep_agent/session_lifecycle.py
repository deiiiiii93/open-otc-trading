"""Persona session acquisition and task-lease helpers."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models import AgentSession, AgentTask, DomainEvent, Workflow
from .payload_registry import validate_event_payload
from .task_registry import task_registration, validate_task_spec, TaskSpec


class SessionLeaseUnavailable(RuntimeError):
    """Raised when a persona's active session is already leased by another task."""


def _utcnow() -> datetime:
    return datetime.utcnow()


def _emit_event(
    session: Session,
    *,
    workflow_id: int,
    agent_session: AgentSession,
    task_id: int,
    kind: str,
    payload: dict,
) -> None:
    validated = validate_event_payload(
        kind=kind,
        schema_version=1,
        payload=payload,
    )
    session.add(
        DomainEvent(
            workflow_id=workflow_id,
            session_id=agent_session.id,
            task_id=task_id,
            kind=kind,
            payload=validated,
            actor="system",
        )
    )


def _task_spec(task: AgentTask) -> TaskSpec:
    return validate_task_spec(
        TaskSpec(
            task_type=task.task_type,
            inputs=task.inputs or {},
            depends_on=task.depends_on or [],
            assigned_persona=task.assigned_persona,
        )
    )


def _claim(
    session: Session,
    *,
    workflow_id: int,
    agent_session: AgentSession,
    task: AgentTask,
    event_kind: str,
    source: str,
) -> AgentSession:
    agent_session.current_task_id = task.id
    agent_session.lease_acquired_at = _utcnow()
    task.assigned_session_id = agent_session.id
    _emit_event(
        session,
        workflow_id=workflow_id,
        agent_session=agent_session,
        task_id=task.id,
        kind=event_kind,
        payload={
            "session_id": agent_session.id,
            "task_id": task.id,
            "persona": agent_session.persona,
            "source": source,
        },
    )
    return agent_session


def _fresh_enough(
    agent_session: AgentSession,
    *,
    freshness_window_seconds: int,
) -> bool:
    if agent_session.closed_at is None:
        return False
    age = (_utcnow() - agent_session.closed_at).total_seconds()
    return age <= freshness_window_seconds


def acquire_session_lease(
    session: Session,
    *,
    task: AgentTask,
    freshness_window_seconds: int | None = None,
) -> AgentSession:
    """Acquire the single in-flight task lease for a persona session."""
    if task.id is None:
        session.flush()
    workflow = session.get(Workflow, task.workflow_id)
    if workflow is None:
        raise ValueError(f"Workflow {task.workflow_id} not found")
    spec = _task_spec(task)
    registration = task_registration(spec.task_type)
    window_seconds = (
        freshness_window_seconds
        if freshness_window_seconds is not None
        else registration.freshness_window_seconds
    )

    active = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == workflow.id,
            AgentSession.persona == spec.assigned_persona,
            AgentSession.status == "active",
        )
        .order_by(AgentSession.episode_id.desc(), AgentSession.id.desc())
        .first()
    )
    if active is not None:
        if active.current_task_id not in (None, task.id):
            raise SessionLeaseUnavailable(
                f"Session {active.id} is leased by task {active.current_task_id}"
            )
        return _claim(
            session,
            workflow_id=workflow.id,
            agent_session=active,
            task=task,
            event_kind="session_resumed",
            source="active_idle",
        )

    closed = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == workflow.id,
            AgentSession.persona == spec.assigned_persona,
            AgentSession.status == "closed",
        )
        .order_by(AgentSession.episode_id.desc(), AgentSession.id.desc())
        .first()
    )
    if closed is not None and _fresh_enough(
        closed,
        freshness_window_seconds=window_seconds,
    ):
        closed.status = "active"
        closed.closed_at = None
        closed.closed_reason = None
        return _claim(
            session,
            workflow_id=workflow.id,
            agent_session=closed,
            task=task,
            event_kind="session_resumed",
            source="closed_reused",
        )
    if closed is not None:
        closed.status = "archived"
        closed.closed_reason = "freshness_expired"

    max_episode = (
        session.query(func.max(AgentSession.episode_id))
        .filter(
            AgentSession.workflow_id == workflow.id,
            AgentSession.persona == spec.assigned_persona,
        )
        .scalar()
        or 0
    )
    episode_id = int(max_episode) + 1
    agent_session = AgentSession(
        workflow_id=workflow.id,
        persona=spec.assigned_persona,
        episode_id=episode_id,
        status="active",
        checkpointer_key=(
            f"workflow:{workflow.id}:persona:{spec.assigned_persona}:"
            f"episode:{episode_id}"
        ),
    )
    session.add(agent_session)
    session.flush()
    return _claim(
        session,
        workflow_id=workflow.id,
        agent_session=agent_session,
        task=task,
        event_kind="session_opened",
        source="new_episode",
    )


def release_session_lease(
    session: Session,
    *,
    session_id: int,
    task_id: int,
    close_reason: str | None = None,
    last_summary: str | None = None,
) -> bool:
    agent_session = session.get(AgentSession, session_id)
    if agent_session is None or agent_session.current_task_id != task_id:
        return False
    agent_session.current_task_id = None
    agent_session.lease_acquired_at = None
    if close_reason is not None:
        agent_session.status = "closed"
        agent_session.closed_at = _utcnow()
        agent_session.closed_reason = close_reason
        agent_session.last_summary = last_summary
        _emit_event(
            session,
            workflow_id=agent_session.workflow_id,
            agent_session=agent_session,
            task_id=task_id,
            kind="session_closed",
            payload={
                "session_id": agent_session.id,
                "task_id": task_id,
                "persona": agent_session.persona,
                "closed_reason": close_reason,
            },
        )
        try:
            from .memory.runtime import enqueue_session_close
            from .memory.scope import book_scope_for_session

            # thread_id must be the AgentThread id (workflow.thread_id), not the
            # workflow id. `Workflow` is already imported at the top of this module.
            workflow = session.get(Workflow, agent_session.workflow_id)
            enqueue_session_close(
                session_id=agent_session.id,
                thread_id=workflow.thread_id if workflow is not None else None,
                persona=agent_session.persona,
                book_scope_id=book_scope_for_session(session, agent_session.id),
            )
        except Exception:  # noqa: BLE001 — memory must never break a turn
            pass
    return True

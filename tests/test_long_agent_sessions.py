from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import AgentSession, AgentTask, AgentThread, DomainEvent, Workflow
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state


def _workflow(session) -> Workflow:
    thread = AgentThread(title="lease", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    workflow = session.get(Workflow, state.domain_workflow_id)
    assert workflow is not None
    return workflow


def _task(session, workflow: Workflow, *, persona: str = "trader") -> AgentTask:
    task = AgentTask(
        workflow_id=workflow.id,
        task_type="fetch_position_summaries",
        inputs={"portfolio_id": 1, "fields": "summary"},
        depends_on=[],
        assigned_persona=persona,
        status="ready",
    )
    session.add(task)
    session.flush()
    return task


def test_acquire_session_lease_reuses_active_idle_session(session):
    from app.services.deep_agent.session_lifecycle import acquire_session_lease

    workflow = _workflow(session)
    task = _task(session, workflow)
    existing = AgentSession(
        workflow_id=workflow.id,
        persona="trader",
        episode_id=1,
        status="active",
        checkpointer_key=f"workflow:{workflow.id}:persona:trader:episode:1",
    )
    session.add(existing)
    session.flush()

    acquired = acquire_session_lease(session, task=task)
    session.flush()

    assert acquired.id == existing.id
    assert acquired.current_task_id == task.id
    assert acquired.lease_acquired_at is not None
    assert task.assigned_session_id == existing.id
    event = (
        session.query(DomainEvent)
        .filter_by(workflow_id=workflow.id, session_id=existing.id, kind="session_resumed")
        .one()
    )
    assert event.payload["task_id"] == task.id
    assert event.payload["source"] == "active_idle"


def test_acquire_session_lease_blocks_when_active_session_busy(session):
    from app.services.deep_agent.session_lifecycle import (
        SessionLeaseUnavailable,
        acquire_session_lease,
    )

    workflow = _workflow(session)
    first = _task(session, workflow)
    second = _task(session, workflow)
    busy = AgentSession(
        workflow_id=workflow.id,
        persona="trader",
        episode_id=1,
        status="active",
        checkpointer_key=f"workflow:{workflow.id}:persona:trader:episode:1",
        current_task_id=first.id,
        lease_acquired_at=datetime.utcnow(),
    )
    session.add(busy)
    session.flush()

    with pytest.raises(SessionLeaseUnavailable):
        acquire_session_lease(session, task=second)

    assert second.assigned_session_id is None


def test_acquire_session_lease_reopens_fresh_closed_session(session):
    from app.services.deep_agent.session_lifecycle import acquire_session_lease

    workflow = _workflow(session)
    task = _task(session, workflow)
    closed = AgentSession(
        workflow_id=workflow.id,
        persona="trader",
        episode_id=3,
        status="closed",
        checkpointer_key=f"workflow:{workflow.id}:persona:trader:episode:3",
        closed_at=datetime.utcnow() - timedelta(minutes=10),
        closed_reason="return_to_orchestrator",
    )
    session.add(closed)
    session.flush()

    acquired = acquire_session_lease(session, task=task)
    session.flush()

    assert acquired.id == closed.id
    assert acquired.status == "active"
    assert acquired.closed_at is None
    assert acquired.closed_reason is None
    assert acquired.current_task_id == task.id


def test_acquire_session_lease_opens_new_episode_when_closed_stale(session):
    from app.services.deep_agent.session_lifecycle import acquire_session_lease

    workflow = _workflow(session)
    task = _task(session, workflow)
    stale = AgentSession(
        workflow_id=workflow.id,
        persona="trader",
        episode_id=2,
        status="closed",
        checkpointer_key=f"workflow:{workflow.id}:persona:trader:episode:2",
        closed_at=datetime.utcnow() - timedelta(days=2),
        closed_reason="return_to_orchestrator",
    )
    session.add(stale)
    session.flush()

    acquired = acquire_session_lease(session, task=task, freshness_window_seconds=60)
    session.flush()

    assert acquired.id != stale.id
    assert acquired.episode_id == 3
    assert acquired.status == "active"
    assert acquired.current_task_id == task.id
    assert stale.status == "archived"
    assert stale.closed_reason == "freshness_expired"


def test_release_session_lease_clears_matching_task_only(session):
    from app.services.deep_agent.session_lifecycle import release_session_lease

    workflow = _workflow(session)
    task = _task(session, workflow)
    other = _task(session, workflow)
    leased = AgentSession(
        workflow_id=workflow.id,
        persona="trader",
        episode_id=1,
        status="active",
        checkpointer_key=f"workflow:{workflow.id}:persona:trader:episode:1",
        current_task_id=task.id,
        lease_acquired_at=datetime.utcnow(),
    )
    session.add(leased)
    session.flush()

    assert release_session_lease(session, session_id=leased.id, task_id=other.id) is False
    assert leased.current_task_id == task.id

    assert (
        release_session_lease(
            session,
            session_id=leased.id,
            task_id=task.id,
            close_reason="return_to_orchestrator",
            last_summary="done",
        )
        is True
    )
    session.flush()

    assert leased.current_task_id is None
    assert leased.lease_acquired_at is None
    assert leased.status == "closed"
    assert leased.closed_reason == "return_to_orchestrator"
    assert leased.last_summary == "done"

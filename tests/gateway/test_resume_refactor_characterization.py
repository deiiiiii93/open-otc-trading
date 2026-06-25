"""Task 6 characterization tests: resume_pending_action + actor threading.

Step 1 (characterization): web confirm path still produces AuditEvent with
actor="desk_user" — pins existing behavior BEFORE the refactor.

Step 2b (failing → green after refactor): calling
active_agent_service.resume_pending_action(..., actor="im_actor") produces
an AuditEvent with actor="im_actor".

Architecture note: the conftest's `client` fixture creates a new app with its
own AgentService instance. We must patch AgentService.resume_async_agent at
the class level (or use the same instance that the client's app uses). Here we
use the `agent_service_override` pattern from test_api.py to share one service
instance between the seeding step and the HTTP client.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import database
from app.config import Settings
from app.models import AgentMessage, AgentThread, AuditEvent
from app.services.agents import AgentService


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_test_service(settings: Settings) -> AgentService:
    """Create a minimal AgentService backed by the test settings."""
    svc = AgentService(settings=settings)
    svc.model = None
    svc.deep_agent = None
    return svc


def _seed_async_pending_action(session) -> tuple[int, int, str]:
    """Seed a thread + assistant message with one pending action carrying an
    async_task_id (so _resume_action takes the async-bubble-up branch, which
    does NOT require a live agent).  Returns (thread_id, message_id, action_id).
    """
    thread = AgentThread(title="test-thread", character="trader")
    session.add(thread)
    session.flush()

    # async_task_id is an int → triggers the async-agent bubble-up path
    msg = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        character="trader",
        content="please confirm",
        meta={
            "agent_phase": "awaiting_confirmation",
            "pending_actions": [
                {
                    "id": "act-async-1",
                    "tool_name": "run_batch_pricing",
                    "label": "Price",
                    "summary": "run pricing",
                    "payload": {},
                    "status": "pending",
                    "async_task_id": 999,  # triggers async bubble-up, not real agent
                }
            ],
        },
    )
    session.add(msg)
    session.commit()
    return thread.id, msg.id, "act-async-1"


def _make_client_with_service(settings: Settings, svc: AgentService):
    """Create a FastAPI TestClient sharing `svc` with seeded session data."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    return TestClient(create_app(settings=settings, agent_service_override=svc))


# ---------------------------------------------------------------------------
# Step 1: Characterization test — web confirm path writes actor="desk_user"
# ---------------------------------------------------------------------------

def test_web_confirm_still_uses_desk_user_actor(session, settings, monkeypatch):
    """Characterization: the existing HTTP /confirm endpoint records an
    AuditEvent with actor='desk_user'.  This must PASS before and after the
    refactor (it guards that web behavior is unchanged).
    """
    svc = _make_test_service(settings)

    # Stub resume_async_agent so we don't need a real TaskRun row or worker
    resume_calls = []

    def _fake_resume(task_id, decision, message):
        resume_calls.append({"task_id": task_id, "decision": decision})

    monkeypatch.setattr(svc, "resume_async_agent", _fake_resume)

    thread_id, msg_id, action_id = _seed_async_pending_action(session)

    client = _make_client_with_service(settings, svc)
    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/{action_id}/confirm"
    )
    assert r.status_code == 200, r.text

    # resume_async_agent must have been called
    assert len(resume_calls) == 1
    assert resume_calls[0]["decision"] == "approve"

    # An AuditEvent with actor="desk_user" and event_type="agent.action.confirmed"
    # must exist for this thread.
    # Refresh session to see the committed audit row
    session.expire_all()
    evt = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "agent.action.confirmed",
            AuditEvent.subject_id == str(thread_id),
        )
        .one_or_none()
    )
    assert evt is not None, "Expected an agent.action.confirmed AuditEvent"
    assert evt.actor == "desk_user"


def test_web_dismiss_still_uses_desk_user_actor(session, settings, monkeypatch):
    """Characterization: /dismiss also records actor='desk_user'."""
    svc = _make_test_service(settings)

    resume_calls = []

    def _fake_resume(task_id, decision, message):
        resume_calls.append({"task_id": task_id, "decision": decision})

    monkeypatch.setattr(svc, "resume_async_agent", _fake_resume)

    thread_id, msg_id, action_id = _seed_async_pending_action(session)

    client = _make_client_with_service(settings, svc)
    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/{action_id}/dismiss"
    )
    assert r.status_code == 200, r.text
    assert resume_calls[0]["decision"] == "reject"

    session.expire_all()
    evt = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "agent.action.dismissed",
            AuditEvent.subject_id == str(thread_id),
        )
        .one_or_none()
    )
    assert evt is not None, "Expected an agent.action.dismissed AuditEvent"
    assert evt.actor == "desk_user"


# ---------------------------------------------------------------------------
# Step 2b: NEW behavior — resume_pending_action respects a custom actor
# ---------------------------------------------------------------------------

def test_resume_pending_action_uses_provided_actor(session, settings, monkeypatch):
    """After the refactor: calling active_agent_service.resume_pending_action
    with actor='im_actor' must produce an AuditEvent with actor='im_actor'
    (not 'desk_user').

    This test FAILS before the refactor (resume_pending_action doesn't exist
    yet) and PASSES after.
    """
    svc = _make_test_service(settings)

    # Stub resume_async_agent on the service instance
    resume_calls = []

    def _fake_resume(task_id, decision, message):
        resume_calls.append({"task_id": task_id, "decision": decision})

    monkeypatch.setattr(svc, "resume_async_agent", _fake_resume)

    thread_id, msg_id, action_id = _seed_async_pending_action(session)

    # Call the NEW service method directly (not via HTTP)
    msg = svc.resume_pending_action(
        thread_id=thread_id,
        message_id=msg_id,
        action_id=action_id,
        decision="confirm",
        actor="im_actor",
        session=session,
    )
    session.commit()

    assert msg is not None

    session.expire_all()
    evt = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "agent.action.confirmed",
            AuditEvent.subject_id == str(thread_id),
        )
        .one_or_none()
    )
    assert evt is not None, "Expected an agent.action.confirmed AuditEvent"
    assert evt.actor == "im_actor", (
        f"Expected actor='im_actor', got '{evt.actor}' "
        "(resume_pending_action must thread actor into audit call)"
    )

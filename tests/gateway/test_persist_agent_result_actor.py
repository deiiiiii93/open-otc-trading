"""Fix 1 regression test: _persist_agent_result must thread `actor` into
the chat.message AuditEvent — not hardcode "desk_user".

This test FAILS against the pre-fix code (actor hardcoded to "desk_user")
and PASSES after the fix (actor parameter is forwarded to record_audit).
"""
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import AgentThread, AuditEvent
from app.services.agents import AgentService


# ---------------------------------------------------------------------------
# Fixture: minimal DB-backed service
# ---------------------------------------------------------------------------


@pytest.fixture
def svc(db_settings: Settings) -> AgentService:
    """Minimal AgentService with no LLM configured."""
    database.configure_database(db_settings)
    database.init_db()
    s = AgentService(settings=db_settings)
    s.model = None
    s.deep_agent = None
    return s


@pytest.fixture
def db_session_for_test(db_settings: Settings):
    with database.SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Test: actor threading
# ---------------------------------------------------------------------------


def test_persist_agent_result_uses_provided_actor(svc, db_session_for_test):
    """_persist_agent_result(actor='im_actor') must write a chat.message
    AuditEvent with actor='im_actor', not 'desk_user'.

    Pre-fix: actor is hardcoded to 'desk_user' → assertion fails.
    Post-fix: actor param is forwarded → assertion passes.
    """
    session = db_session_for_test

    thread = AgentThread(title="test-thread-actor", character="trader")
    session.add(thread)
    session.flush()

    # Minimal result dict: all dict.get() calls return None, which is safe.
    result: dict = {}

    svc._persist_agent_result(
        session,
        thread,
        result,
        assets=[],
        page_context=None,
        actor="im_actor",
    )
    session.commit()

    # Verify a chat.message AuditEvent exists with the correct actor
    session.expire_all()
    evt = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "chat.message",
            AuditEvent.subject_id == str(thread.id),
        )
        .one_or_none()
    )
    assert evt is not None, "Expected a chat.message AuditEvent to be recorded"
    assert evt.actor == "im_actor", (
        f"Expected actor='im_actor', got '{evt.actor}'. "
        "_persist_agent_result must forward the actor param to record_audit."
    )


def test_persist_agent_result_default_actor_is_desk_user(svc, db_session_for_test):
    """Default actor must stay 'desk_user' for backward compatibility with web callers."""
    session = db_session_for_test

    thread = AgentThread(title="test-thread-default-actor", character="trader")
    session.add(thread)
    session.flush()

    svc._persist_agent_result(
        session,
        thread,
        {},
        assets=[],
        page_context=None,
        # actor not passed — defaults to "desk_user"
    )
    session.commit()

    session.expire_all()
    evt = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "chat.message",
            AuditEvent.subject_id == str(thread.id),
        )
        .one_or_none()
    )
    assert evt is not None
    assert evt.actor == "desk_user", (
        f"Default actor must be 'desk_user' for web-path callers; got '{evt.actor}'"
    )

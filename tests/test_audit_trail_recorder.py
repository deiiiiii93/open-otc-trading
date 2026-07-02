"""Recorder service: fail-closed phase-1, best-effort phase-2 (audit spec §5.2)."""
import pytest
from sqlalchemy.exc import OperationalError

from app import database
from app.models import AgentActionAudit
from app.services import audit_trail
from app.services.audit_trail import (
    AuditUnavailableError,
    record_attempt,
    record_hitl_decision,
    record_hitl_proposal,
    record_outcome,
    record_refusal,
)

CTX = {
    "actor": "desk_user", "mode": "yolo", "model": "deepseek/deepseek-v4-flash",
    "thread_id": None, "workflow_id": None, "session_id": None, "task_id": None,
    "message_id": None, "desk_workflow_slug": None, "envelope": "DESK_WORKFLOW",
}


def test_attempt_then_outcome(session):
    row_id = record_attempt(
        tool_name="book_position", tool_class="domain_write",
        tool_call_id="call_1", args={"qty": 1, "api_key": "sk"}, context=CTX,
    )
    row = session.get(AgentActionAudit, row_id)
    assert row.status == "attempted"
    assert row.args_json["api_key"] == "[REDACTED]"
    assert row.redacted is True
    assert row.mode == "yolo"
    assert row.actor == "desk_user"
    record_outcome(row_id, status="ok", result_preview="booked #7")
    session.expire_all()
    row = session.get(AgentActionAudit, row_id)
    assert row.status == "ok"
    assert row.result_preview == "booked #7"
    assert row.completed_at is not None


def test_attempt_fail_closed_after_bounded_retry(session, monkeypatch):
    calls = []

    class _BoomSession:
        def __enter__(self):
            raise OperationalError("db locked", None, Exception("locked"))

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(audit_trail, "_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(
        database, "SessionLocal", lambda: calls.append(1) or _BoomSession()
    )
    with pytest.raises(AuditUnavailableError):
        record_attempt(tool_name="book_position", tool_class="domain_write",
                       tool_call_id="c", args={}, context=CTX)
    assert len(calls) == 4  # 1 try + 3 retries


def test_outcome_failure_is_swallowed(session, monkeypatch):
    row_id = record_attempt(tool_name="book_position", tool_class="domain_write",
                            tool_call_id="c2", args={}, context=CTX)

    def _boom():
        raise OperationalError("locked", None, Exception("locked"))

    monkeypatch.setattr(database, "SessionLocal", lambda: _boom())
    record_outcome(row_id, status="ok")  # must not raise
    monkeypatch.undo()
    session.expire_all()
    assert session.get(AgentActionAudit, row_id).status == "attempted"


def test_refusal_row_and_unpersisted_counter(session, monkeypatch):
    record_refusal(tool_name="book_position", tool_class="domain_write",
                   tool_call_id="c9", context=CTX)
    row = session.query(AgentActionAudit).filter_by(status="refused").one()
    assert row.deny_reason == "audit_unavailable"
    before = audit_trail.unpersisted_refusals()
    monkeypatch.setattr(audit_trail, "_RETRY_DELAYS", (0.0,))

    def _boom():
        raise OperationalError("locked", None, Exception("locked"))

    monkeypatch.setattr(database, "SessionLocal", lambda: _boom())
    record_refusal(tool_name="t", tool_class="domain_write",
                   tool_call_id=None, context=CTX)
    assert audit_trail.unpersisted_refusals() == before + 1


def test_hitl_proposal_joins_caller_session(session):
    proposal = {
        "id": "int1:0", "tool_name": "book_position",
        "payload": {"qty": 2},
        "source_meta": {"audit": {"audit_ref": "ref-1", "tool_call_id": "call_2"}},
    }
    record_hitl_proposal(session, proposal=proposal, tool_class="domain_write",
                         context=CTX)
    # NOT committed yet — atomicity belongs to the caller's transaction.
    session.rollback()
    assert session.query(AgentActionAudit).filter_by(kind="hitl_proposal").count() == 0
    record_hitl_proposal(session, proposal=proposal, tool_class="domain_write",
                         context=CTX)
    session.commit()
    row = session.query(AgentActionAudit).filter_by(kind="hitl_proposal").one()
    assert row.status == "proposed"
    assert row.audit_ref == "ref-1"
    assert row.tool_call_id == "call_2"
    assert row.args_json == {"qty": 2}


def test_hitl_decision_row_with_context_and_source_meta_fallback(session, agent_thread_factory):
    from app.models import Workflow

    thread = agent_thread_factory()
    workflow = Workflow(thread_id=thread.id, title="t", intent="test", status="open")
    session.add(workflow)
    session.flush()
    action = {
        "id": "int1:0", "tool_name": "book_position",
        "source_meta": {
            "workflow_id": workflow.id,
            "audit": {"audit_ref": "ref-2", "tool_call_id": "c3"},
        },
    }
    record_hitl_decision(session, action=action, decision="approved",
                         actor="desk_user", context={"thread_id": None})
    session.commit()
    row = session.query(AgentActionAudit).filter_by(kind="hitl_decision").one()
    assert row.status == "approved"
    assert row.audit_ref == "ref-2"
    assert row.actor == "desk_user"
    assert row.workflow_id == workflow.id  # fell back to source_meta


def test_contention_fail_closed_with_real_lock(tmp_path, monkeypatch):
    """Hold a real SQLite write lock; record_attempt must retry then refuse."""
    import sqlite3

    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    db_file = tmp_path / "locked.sqlite3"
    engine = sa.create_engine(
        f"sqlite:///{db_file}", connect_args={"timeout": 0.05}
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(audit_trail, "_RETRY_DELAYS", (0.01, 0.01))

    locker = sqlite3.connect(db_file)
    locker.execute("BEGIN IMMEDIATE")  # hold RESERVED write lock
    try:
        with pytest.raises(AuditUnavailableError):
            record_attempt(tool_name="book_position", tool_class="domain_write",
                           tool_call_id="c", args={}, context=None)
    finally:
        locker.rollback()
        locker.close()

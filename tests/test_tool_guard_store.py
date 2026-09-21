"""Committed verdicts are the guard's source of truth on re-entry (D10)."""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.exc import OperationalError

from app import database
from app.models import AgentToolGuardVerdict
from app.services.deep_agent import tool_guard_store as store


def _fields(**over):
    fp = store.args_fingerprint("void_settlement_cashflow", {"cashflow_id": 9300})
    base = dict(
        thread_id=7, tool_call_id="c1", persona="trader", exec_mode="auto",
        guard_mode="shadow", tool_name="void_settlement_cashflow",
        args_json=fp.payload, redacted=fp.redacted, args_hash=fp.sha256,
        user_request_source="latest", verdict="flagged", unscored_reason=None,
        predicates_json=[{"key": "unnamed_target", "probability": 0.85, "threshold": 0.5,
                          "flagged": True, "evidence": "tested-posthoc"}],
        max_probability=0.85, model="typesafe/jev-1.13", latency_ms=1300, error=None,
    )
    base.update(over)
    return base


def test_fingerprint_is_order_independent_redacted_and_total():
    a = store.args_fingerprint("close_position", {"position_id": 1, "reason": "x"})
    b = store.args_fingerprint("close_position", {"reason": "x", "position_id": 1})
    c = store.args_fingerprint("close_position", {"position_id": 2, "reason": "x"})
    assert a.sha256 == b.sha256 != c.sha256
    secret = store.args_fingerprint("close_position", {"api_key": "sk-x", "at": dt.date(2026, 9, 21)})
    assert secret.payload == {"api_key": "[REDACTED]", "at": "2026-09-21"}
    assert secret.redacted is True


def test_commit_then_find(session):
    stored = store.commit_verdict(_fields())
    found = store.find_verdict(7, "c1")
    assert found == stored
    assert (found.verdict, found.max_probability) == ("flagged", 0.85)
    assert found.predicates[0]["key"] == "unnamed_target"
    assert store.find_verdict(7, "nope") is None
    assert store.find_verdict(8, "c1") is None


def test_the_stored_row_wins_a_lost_insert_race(session):
    first = store.commit_verdict(_fields(verdict="clear", max_probability=0.1))
    second = store.commit_verdict(_fields(verdict="flagged"))
    assert second == first
    assert second.verdict == "clear"
    with database.SessionLocal() as s:
        assert s.query(AgentToolGuardVerdict).count() == 1


def test_structural_rows_never_collide(session):
    store.record_structural(_fields(tool_call_id="", verdict="unscored",
                                    unscored_reason="no_tool_call_id"))
    store.record_structural(_fields(tool_call_id="", verdict="unscored",
                                    unscored_reason="no_tool_call_id"))
    with database.SessionLocal() as s:
        assert s.query(AgentToolGuardVerdict).filter_by(tool_call_id="").count() == 2


def test_mark_interrupted(session):
    row = store.commit_verdict(_fields())
    store.mark_interrupted([row.id, None])
    with database.SessionLocal() as s:
        assert s.get(AgentToolGuardVerdict, row.id).action == "interrupted"


def _broken_session_factory(exc):
    def factory():
        raise exc
    return factory


def test_lookup_failure_is_store_unavailable(session, monkeypatch):
    monkeypatch.setattr(store.database, "SessionLocal",
                        _broken_session_factory(OperationalError("s", {}, Exception("locked"))))
    with pytest.raises(store.GuardStoreUnavailable):
        store.find_verdict(7, "c1")


def test_commit_failure_after_retries_is_store_unavailable(session, monkeypatch):
    monkeypatch.setattr(store, "_RETRY_DELAYS", ())
    monkeypatch.setattr(store.database, "SessionLocal",
                        _broken_session_factory(OperationalError("s", {}, Exception("locked"))))
    with pytest.raises(store.GuardStoreUnavailable):
        store.commit_verdict(_fields())


def test_best_effort_writers_never_raise(session, monkeypatch):
    monkeypatch.setattr(store.database, "SessionLocal",
                        _broken_session_factory(OperationalError("s", {}, Exception("locked"))))
    store.record_structural(_fields(tool_call_id=""))
    store.mark_interrupted([1])

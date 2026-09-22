"""The hourly desk sweep (spec 2026-09-22 D8, D11; parent D17)."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from _system_one_fakes import JevPost
from app.models import AgentActionAudit, AgentMessage, AgentToolGuardVerdict
from app.services.deep_agent import tool_guard_sweep as sweep


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def live(settings):
    return dataclasses.replace(settings, system_one_enabled=True, guard_sweep_enabled=True)


def _recent(session, factory, n, *, prefix, source="desk", age=timedelta(hours=1)):
    thread = factory()
    thread.source = source
    at = datetime.utcnow() - age
    session.add(AgentMessage(thread_id=thread.id, role="user", content="release 9301", meta={},
                             created_at=at - timedelta(minutes=1)))
    for i in range(n):
        session.add(AgentActionAudit(
            kind="execution", status="ok", tool_name="release_settlement_cashflow",
            tool_class="domain_write", tool_call_id=f"{prefix}-{i}", thread_id=thread.id,
            mode="auto", args_json={"cashflow_id": 9300 + i}, occurred_at=at + timedelta(seconds=i)))
    session.commit()


def _daemon(settings, jev, tmp_path):
    return sweep.SweepDaemon(settings, post=jev, trace_path=tmp_path / "none.sqlite3")


@pytest.mark.parametrize("master, feature", [(False, True), (True, False), (False, False)])
def test_inert_unless_both_switches_are_on(settings, master, feature):
    daemon = sweep.SweepDaemon(dataclasses.replace(
        settings, system_one_enabled=master, guard_sweep_enabled=feature))
    assert daemon.start() is False
    assert daemon.running is False


def test_it_runs_on_a_named_daemon_thread_and_stops(session, live, tmp_path):
    daemon = _daemon(live, JevPost(), tmp_path)
    try:
        assert daemon.start() is True
        assert daemon._thread.name == "guard-sweep" and daemon._thread.daemon
        assert daemon.start() is False
    finally:
        daemon.stop()
    assert daemon.running is False


def test_a_pass_scores_recent_desk_rows_only(session, agent_thread_factory, live, tmp_path):
    _recent(session, agent_thread_factory, 2, prefix="desk")
    _recent(session, agent_thread_factory, 2, prefix="arena", source="arena")
    _recent(session, agent_thread_factory, 1, prefix="stale", age=timedelta(days=8))
    jev = JevPost()
    counts = _daemon(live, jev, tmp_path).run_pass()
    assert counts["scored"] == 2 and len(jev.calls) == 2
    rows = session.query(AgentToolGuardVerdict).all()
    assert {(r.tool_call_id, r.source) for r in rows} == {("desk-0", "sweep"), ("desk-1", "sweep")}


def test_an_outage_ends_the_pass_after_one_call(session, agent_thread_factory, live, tmp_path):
    _recent(session, agent_thread_factory, 3, prefix="desk")
    jev = JevPost()
    jev.exc = TimeoutError("slow")
    counts = _daemon(live, jev, tmp_path).run_pass()
    assert counts["outage"] == 1 and len(jev.calls) == 1
    assert session.query(AgentToolGuardVerdict).count() == 0


def test_a_row_fact_does_not_end_the_pass(session, agent_thread_factory, live, tmp_path):
    _recent(session, agent_thread_factory, 3, prefix="desk")
    jev = JevPost()
    jev.response = {"answers": {}}
    counts = _daemon(live, jev, tmp_path).run_pass()
    assert counts["unscored"] == 3 and len(jev.calls) == 3


def test_a_pass_is_bounded_by_the_batch(session, agent_thread_factory, live, tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "sweep_batch", 2)
    _recent(session, agent_thread_factory, 5, prefix="desk")
    jev = JevPost()
    _daemon(live, jev, tmp_path).run_pass()
    assert len(jev.calls) == 2


def test_an_exception_ends_the_pass_and_the_next_one_works(session, agent_thread_factory, live,
                                                           tmp_path, monkeypatch):
    _recent(session, agent_thread_factory, 2, prefix="desk")
    real = sweep.score_audit_row

    def boom(*args, **kwargs):
        raise RuntimeError("a bug")

    monkeypatch.setattr(sweep, "score_audit_row", boom)
    daemon = _daemon(live, JevPost(), tmp_path)
    assert daemon.run_pass()["scored"] == 0          # logged, never raised
    monkeypatch.setattr(sweep, "score_audit_row", real)
    assert daemon.run_pass()["scored"] == 2


def test_the_app_wires_the_daemon_and_leaves_it_inert_by_default(client):
    daemon = client.app.state.guard_sweep
    assert isinstance(daemon, sweep.SweepDaemon)
    assert daemon.running is False                   # conftest pins the master switch off

"""score_audit_row and due_rows (spec 2026-09-22 D3, D13; finding F4)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from _system_one_fakes import JevPost
from app.models import AgentActionAudit, AgentMessage, AgentToolGuardVerdict
from app.services.deep_agent import tool_guard_sweep as sweep
from app.services.deep_agent.tool_guard_policy import GUARD_POLICY, SWEEP_POLICY
from app.services.deep_agent.tool_guard_store import args_fingerprint, commit_verdict

T = datetime(2026, 9, 20, 9, 0, 0)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _thread(session, factory, source="desk", *, with_user=True):
    thread = factory()
    thread.source = source
    if with_user:
        session.add(AgentMessage(thread_id=thread.id, role="user", content="release cashflow 9301",
                                 meta={}, created_at=T))
    session.flush()
    return thread


def _row(session, thread, *, tool="release_settlement_cashflow", call_id="c1", status="ok",
         kind="execution", at=None, args=None):
    row = AgentActionAudit(kind=kind, status=status, tool_name=tool, tool_class="domain_write",
                           tool_call_id=call_id, thread_id=thread.id if thread else None,
                           mode="auto", args_json={"cashflow_id": 9301} if args is None else args,
                           occurred_at=at or T + timedelta(minutes=1))
    session.add(row)
    session.flush()
    return row


def _verdict_fields(row, *, source, verdict="clear"):
    fp = args_fingerprint(row.tool_name, row.args_json)
    return dict(thread_id=row.thread_id, tool_call_id=row.tool_call_id, persona=None,
                exec_mode="auto", guard_mode="shadow", tool_name=row.tool_name,
                args_json=fp.payload, redacted=fp.redacted, args_hash=fp.sha256,
                user_request_source="latest", verdict=verdict, unscored_reason=None,
                predicates_json=[], max_probability=0.1, source=source)


def _score(session, row, tmp_path, post):
    return sweep.score_audit_row(session, row, post=post, trace_path=tmp_path / "none.sqlite3")


# --- due rules (D13) ---------------------------------------------------------

def test_due_rows_applies_every_exclusion(session, agent_thread_factory):
    desk = _thread(session, agent_thread_factory)
    arena = _thread(session, agent_thread_factory, "arena")
    smoke = _thread(session, agent_thread_factory, "smoke")
    _row(session, desk, call_id="due")
    _row(session, None, call_id="no-thread")
    _row(session, desk, call_id="")
    _row(session, desk, call_id=None)
    _row(session, desk, call_id="attempted", status="attempted")
    _row(session, desk, call_id="interrupted", status="interrupted")
    _row(session, desk, call_id="proposal", kind="hitl_proposal", status="proposed")
    _row(session, desk, call_id="not-swept", tool="create_report")
    _row(session, arena, call_id="arena")
    _row(session, smoke, call_id="smoke")
    _row(session, desk, call_id="old", at=T - timedelta(days=30))
    live = _row(session, desk, call_id="has-live")
    swept = _row(session, desk, call_id="has-sweep")
    session.commit()
    commit_verdict(_verdict_fields(live, source="live"))
    commit_verdict(_verdict_fields(swept, source="sweep"))
    got = sweep.due_rows(session, kinds={sweep.DESK}, since=T - timedelta(days=7))
    assert [r.tool_call_id for r in got] == ["due"]


def test_denied_and_error_rows_are_terminal_and_due(session, agent_thread_factory):
    desk = _thread(session, agent_thread_factory)
    _row(session, desk, call_id="e", status="error")
    _row(session, desk, call_id="d", status="denied")
    session.commit()
    assert {r.tool_call_id for r in sweep.due_rows(session, kinds={sweep.DESK})} == {"e", "d"}


def test_due_rows_are_oldest_first_and_bounded(session, agent_thread_factory):
    desk = _thread(session, agent_thread_factory)
    for call_id, minutes in (("c0", 5), ("c1", 1), ("c2", 3)):
        _row(session, desk, call_id=call_id, at=T + timedelta(minutes=minutes))
    session.commit()
    assert [r.tool_call_id for r in sweep.due_rows(session, kinds={sweep.DESK}, limit=2)] == ["c1", "c2"]


def test_the_arena_kind_selects_arena_threads_only(session, agent_thread_factory):
    _row(session, _thread(session, agent_thread_factory), call_id="desk")
    _row(session, _thread(session, agent_thread_factory, "arena"), call_id="arena")
    session.commit()
    assert [r.tool_call_id for r in sweep.due_rows(session, kinds={sweep.ARENA})] == ["arena"]


@pytest.mark.parametrize("kinds", [set(), {"desk", "nope"}])
def test_kinds_must_be_known(session, kinds):
    with pytest.raises(ValueError, match="kinds"):
        sweep.eligible_rows(session, kinds=kinds)


def test_an_unswept_tool_is_refused(session):
    with pytest.raises(ValueError, match="neither policy"):
        sweep.eligible_rows(session, kinds={sweep.DESK}, tools={"create_report"})


# --- scoring -------------------------------------------------------------------

def test_a_candidate_call_gets_an_advisory_sweep_row(session, agent_thread_factory, tmp_path):
    row = _row(session, _thread(session, agent_thread_factory))
    session.commit()
    jev = JevPost({"beyond_named_scope": 0.83})
    result = _score(session, row, tmp_path, jev)
    assert (result.fidelity, result.outage, result.already_scored) == ("audit_only", None, False)
    assert (result.stored.verdict, result.stored.source) == ("flagged", "sweep")
    v = session.query(AgentToolGuardVerdict).one()
    assert (v.action, v.audit_id, v.state_fidelity, v.user_request_source, v.exec_mode) == (
        "recorded", row.id, "audit_only", "occurred_at", "auto")
    assert v.args_hash == args_fingerprint(row.tool_name, row.args_json).sha256
    [payload] = jev.calls
    assert payload["state"]["user_request"] == "release cashflow 9301"
    assert payload["state"]["pending_tool_call"] == {
        "name": "release_settlement_cashflow", "args": {"cashflow_id": 9301}}
    assert set(payload["questions"]) == {p.key for p in SWEEP_POLICY["release_settlement_cashflow"]}


def test_a_guarded_tool_is_asked_the_live_guards_own_predicates(session, agent_thread_factory,
                                                                tmp_path):
    row = _row(session, _thread(session, agent_thread_factory), tool="void_settlement_cashflow",
               args={"cashflow_id": 9304})
    session.commit()
    jev = JevPost()
    assert _score(session, row, tmp_path, jev).stored.verdict == "clear"
    assert set(jev.calls[0]["questions"]) == {p.key for p in GUARD_POLICY["void_settlement_cashflow"]}


@pytest.mark.parametrize("exc, reason", [(TimeoutError("slow"), "timeout"),
                                         (ConnectionError("down"), "http_error")])
def test_an_outage_writes_nothing_and_the_call_stays_due(session, agent_thread_factory, tmp_path,
                                                         exc, reason):
    row = _row(session, _thread(session, agent_thread_factory))
    session.commit()
    jev = JevPost()
    jev.exc = exc
    result = _score(session, row, tmp_path, jev)
    assert (result.stored, result.outage) == (None, reason)
    assert session.query(AgentToolGuardVerdict).count() == 0
    assert [r.id for r in sweep.due_rows(session, kinds={sweep.DESK})] == [row.id]


def test_a_missing_key_is_an_outage(session, agent_thread_factory, tmp_path, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY", raising=False)
    row = _row(session, _thread(session, agent_thread_factory))
    session.commit()
    assert _score(session, row, tmp_path, JevPost()).outage == "no_key"
    assert session.query(AgentToolGuardVerdict).count() == 0


def test_a_bad_response_is_a_row_fact(session, agent_thread_factory, tmp_path):
    row = _row(session, _thread(session, agent_thread_factory))
    session.commit()
    jev = JevPost()
    jev.response = {"answers": {}}
    result = _score(session, row, tmp_path, jev)
    assert (result.stored.verdict, result.stored.unscored_reason, result.stored.source) == (
        "unscored", "bad_response", "sweep")
    assert sweep.due_rows(session, kinds={sweep.DESK}) == []


def test_no_user_message_at_or_before_the_call_is_unscored(session, agent_thread_factory, tmp_path):
    row = _row(session, _thread(session, agent_thread_factory, with_user=False))
    session.commit()
    jev = JevPost()
    result = _score(session, row, tmp_path, jev)
    assert (result.stored.unscored_reason, result.fidelity) == ("no_user_request", None)
    assert jev.calls == []


def test_an_existing_row_of_any_source_wins_without_a_call(session, agent_thread_factory, tmp_path):
    row = _row(session, _thread(session, agent_thread_factory))
    session.commit()
    commit_verdict(_verdict_fields(row, source="live"))
    jev = JevPost()
    result = _score(session, row, tmp_path, jev)
    assert result.already_scored and result.stored.source == "live" and jev.calls == []


@pytest.mark.parametrize("status", ["attempted", "interrupted"])
def test_a_non_terminal_row_is_refused_and_never_stamped(session, agent_thread_factory, tmp_path,
                                                         status):
    """D3: the live guard scores BEFORE a call runs, so a sweep row for a call
    without a terminal execution row could collide with it. Never written."""
    row = _row(session, _thread(session, agent_thread_factory), status=status)
    session.commit()
    with pytest.raises(ValueError, match="terminal"):
        _score(session, row, tmp_path, JevPost())
    assert session.query(AgentToolGuardVerdict).count() == 0


def test_a_row_without_a_call_key_is_refused(session, agent_thread_factory, tmp_path):
    row = _row(session, _thread(session, agent_thread_factory), call_id="")
    session.commit()
    with pytest.raises(ValueError, match="call key"):
        _score(session, row, tmp_path, JevPost())

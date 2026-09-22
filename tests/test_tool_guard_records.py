"""Rebuilding the guard's state from records (spec 2026-09-22 D5-D7; findings F1-F3)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from app.models import AgentActionAudit, AgentMessage
from app.services.deep_agent import tool_guard_records as records
from app.services.deep_agent.tool_guard_state import assemble_guard_state, build_guard_state
from app.services.tracing.store import _SCHEMA
from app.services.tracing.tracer import _json as trace_json

T0 = datetime(2026, 9, 4, 1, 0, 0)     # naive UTC, as the ORM stores it
ORCH = records.ORCHESTRATOR_AGENT


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _iso(seconds: float) -> str:
    """The trace DB's own format: ISO with `T` and `+00:00`."""
    return _at(seconds).replace(tzinfo=timezone.utc).isoformat()


def _tool(thread_id, dotted, name, seconds, *, call_id, agent, args, result=None, error=None):
    outputs = (trace_json({"output": ToolMessage(content=result, tool_call_id=call_id)})
               if result is not None else "{}")
    return (dotted.rsplit(".", 1)[-1], "tr", dotted, thread_id, name, "tool", _iso(seconds),
            "error" if error else "success", trace_json(args), outputs, error,
            json.dumps({"tool_call_id": call_id, "metadata": {"lc_agent_name": agent}}))


def _llm(thread_id, dotted, seconds):
    return (dotted.rsplit(".", 1)[-1], "tr", dotted, thread_id, "ChatModel", "llm",
            _iso(seconds), "success", None, None, None, None)


def _trace_db(tmp_path, rows):
    path = tmp_path / "traces.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO trace_runs (id, trace_id, dotted_order, thread_id, name, run_type, "
        "start_time, status, inputs, outputs, error, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    return path


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _user(session, thread, text, seconds):
    session.add(AgentMessage(thread_id=thread.id, role="user", content=text, meta={},
                             created_at=_at(seconds)))


def _audit(session, thread, name, call_id, seconds, *, args, status="ok", preview=None,
           error=None):
    row = AgentActionAudit(kind="execution", status=status, tool_name=name,
                           tool_class="domain_write", tool_call_id=call_id, thread_id=thread.id,
                           mode="yolo", args_json=args, result_preview=preview, error=error,
                           occurred_at=_at(seconds))
    session.add(row)
    session.flush()
    return row


def _rebuild(session, row, db):
    turn = records.user_turn(session, row.thread_id, row.occurred_at)
    return records.window_from_records(session, row, turn, trace_path=db)


def test_orchestrator_state_equals_the_live_guards(session, agent_thread_factory, tmp_path):
    """D5 + F2/F3: same dict both ways; a call in the PENDING AIMessage is not earlier."""
    thread = agent_thread_factory()
    ask = "Close position 7 and check its cashflows"
    _user(session, thread, ask, 0)
    row = _audit(session, thread, "close_position", "c3", 4, args={"position_id": 7})
    session.commit()
    db = _trace_db(tmp_path, [
        _llm(thread.id, "R.A1.L1", 1),
        _tool(thread.id, "R.B1.c1", "get_positions", 2, call_id="c1", agent=ORCH,
              args={"portfolio_id": 9}, result="[position 7]"),
        _tool(thread.id, "R.B2.c2", "get_settlement_cashflows", 2.1, call_id="c2", agent=ORCH,
              args={"position_id": 7}, result="[]"),
        _llm(thread.id, "R.A2.L2", 3),
        # c4 shares the pending AIMessage with c3 and starts first: never "earlier"
        _tool(thread.id, "R.B4.c4", "get_positions", 4.0005, call_id="c4", agent=ORCH,
              args={}, result="[]"),
        _tool(thread.id, "R.B3.c3", "close_position", 4.001, call_id="c3", agent=ORCH,
              args={"position_id": 7}, result="closed"),
    ])
    pending = _call("close_position", {"position_id": 7}, "c3")
    live = build_guard_state([
        HumanMessage(ask),
        AIMessage("", tool_calls=[_call("get_positions", {"portfolio_id": 9}, "c1"),
                                  _call("get_settlement_cashflows", {"position_id": 7}, "c2")]),
        ToolMessage("[position 7]", tool_call_id="c1"),
        ToolMessage("[]", tool_call_id="c2"),
        AIMessage("", tool_calls=[_call("get_positions", {}, "c4"), pending]),
    ], pending, user_request=ask, is_subagent=False)
    rec = _rebuild(session, row, db)
    assert (rec.fidelity, rec.agent) == (records.TRACE, "orchestrator")
    assert assemble_guard_state(rec.window, {"name": row.tool_name, "args": row.args_json}) == live


def test_persona_state_equals_the_live_guards_and_carries_its_task(session, agent_thread_factory,
                                                                   tmp_path):
    """D5 + F1/F2: the persona sees its own calls only; delegated_task is structural."""
    thread = agent_thread_factory()
    ask = "The desk disputes the KO on 9311: void cashflow 9304 and reopen the trade"
    task_text = "Void cashflow 9304 (the disputed KO on position 9311)."
    _user(session, thread, ask, 0)
    row = _audit(session, thread, "void_settlement_cashflow", "c9", 6, args={"cashflow_id": 9304})
    session.commit()
    db = _trace_db(tmp_path, [
        _tool(thread.id, "R.O.o1", "write_todos", 1, call_id="o1", agent=ORCH,
              args={"todos": []}, result="ok"),
        _tool(thread.id, "R.K.T", "task", 2, call_id="t1", agent=ORCH,
              args={"subagent_type": "trader", "description": task_text}),
        _tool(thread.id, "R.K2.T2", "task", 2.5, call_id="t2", agent=ORCH,
              args={"subagent_type": "risk_manager", "description": "check limits"}),
        _llm(thread.id, "R.K.T.P.L1", 3),
        _tool(thread.id, "R.K.T.P.X.p1", "get_settlement_cashflows", 4, call_id="p1",
              agent="trader", args={"position_id": 9311}, result="[9304 released]"),
        _tool(thread.id, "R.K2.T2.P.Z.q1", "get_limits", 4.5, call_id="q1",
              agent="risk_manager", args={}, result="[]"),
        _llm(thread.id, "R.K.T.P.L2", 5),
        _tool(thread.id, "R.K.T.P.Y.c9", "void_settlement_cashflow", 6.001, call_id="c9",
              agent="trader", args={"cashflow_id": 9304}, result="voided"),
    ])
    pending = _call("void_settlement_cashflow", {"cashflow_id": 9304}, "c9")
    live = build_guard_state([
        HumanMessage(task_text),
        AIMessage("", tool_calls=[_call("get_settlement_cashflows", {"position_id": 9311}, "p1")]),
        ToolMessage("[9304 released]", tool_call_id="p1"),
        AIMessage("", tool_calls=[pending]),
    ], pending, user_request=ask, is_subagent=True)
    rec = _rebuild(session, row, db)
    state = assemble_guard_state(rec.window, {"name": row.tool_name, "args": row.args_json})
    assert (rec.fidelity, rec.agent) == (records.TRACE, "trader")
    assert state == live
    assert state["delegated_task"] == task_text


def test_a_resumed_persona_call_keeps_the_calls_before_the_pause(session, agent_thread_factory,
                                                                tmp_path):
    """A HITL resume starts a NEW trace root, and LangGraph re-enters the same
    `task` call (same tool_call_id) under it. Scoping by dotted_order alone gave
    the resumed call an empty window at `trace` fidelity; the live guard, which
    judged it before the pause, saw every earlier call."""
    thread = agent_thread_factory()
    ask = "Parse and book the attached confirmation into portfolio 1. Proceed directly."
    task_text = "Parse the confirmation and book it via book_position."
    _user(session, thread, ask, 0)
    row = _audit(session, thread, "book_position", "b1", 40, args={"portfolio_id": 1})
    session.commit()
    db = _trace_db(tmp_path, [
        _tool(thread.id, "R1.K.T", "task", 1, call_id="t1", agent=ORCH,
              args={"subagent_type": "trader", "description": task_text}),
        _llm(thread.id, "R1.K.T.P.L1", 2),
        _tool(thread.id, "R1.K.T.P.X.p1", "parse_trade_confirmation", 3, call_id="p1",
              agent="trader", args={"path": "conf.pdf"}, result="AAPL call, strike 232.5"),
        _llm(thread.id, "R1.K.T.P.L2", 4),                      # emits book_position, then pauses
        _tool(thread.id, "R2.K.Tr", "task", 40, call_id="t1", agent=ORCH,   # the resume
              args={"subagent_type": "trader", "description": task_text}),
        _tool(thread.id, "R2.K.Tr.P.Y.b1", "book_position", 40.001, call_id="b1",
              agent="trader", args={"portfolio_id": 1}, error="ValueError('bad term')"),
        _llm(thread.id, "R2.K.Tr.P.L3", 41),
    ])
    pending = _call("book_position", {"portfolio_id": 1}, "b1")
    live = build_guard_state([
        HumanMessage(task_text),
        AIMessage("", tool_calls=[_call("parse_trade_confirmation", {"path": "conf.pdf"}, "p1")]),
        ToolMessage("AAPL call, strike 232.5", tool_call_id="p1"),
        AIMessage("", tool_calls=[pending]),
    ], pending, user_request=ask, is_subagent=True)
    rec = _rebuild(session, row, db)
    state = assemble_guard_state(rec.window, {"name": row.tool_name, "args": row.args_json})
    assert (rec.fidelity, rec.agent) == (records.TRACE, "trader")
    assert state == live


def test_turn_scoping_parses_time_across_both_formats(session, agent_thread_factory, tmp_path):
    """D6: the call in turn 2 gets turn 2's words and none of turn 1's or turn 3's calls."""
    thread = agent_thread_factory()
    for n, seconds in ((1, 0), (2, 600), (3, 1200)):
        _user(session, thread, f"turn {n}", seconds)
    row = _audit(session, thread, "release_settlement_cashflow", "b2", 720,
                 args={"cashflow_id": 1})
    session.commit()
    db = _trace_db(tmp_path, [
        _tool(thread.id, "R1.a1", "get_positions", 60, call_id="a1", agent=ORCH, args={},
              result="turn-1 read"),
        _tool(thread.id, "R2.b1", "get_settlement_cashflows", 660, call_id="b1", agent=ORCH,
              args={}, result="turn-2 read"),
        _llm(thread.id, "R2.L", 690),
        _tool(thread.id, "R2.b2", "release_settlement_cashflow", 720.001, call_id="b2",
              agent=ORCH, args={"cashflow_id": 1}, result="released"),
        _tool(thread.id, "R3.c1", "get_positions", 1260, call_id="c1", agent=ORCH, args={},
              result="turn-3 read"),
    ])
    turn = records.user_turn(session, thread.id, row.occurred_at)
    assert turn.text == "turn 2"
    rec = records.window_from_records(session, row, turn, trace_path=db)
    assert [c.result for c in rec.window.earlier_calls] == ["turn-2 read"]
    # The trap D6 exists for: the two storage formats do not compare as strings.
    assert not (_iso(660) < str(_at(720)))
    assert records.parse_utc(_iso(720)) == records.parse_utc(str(_at(720))) == records.parse_utc(_at(720))


def test_no_trace_db_is_audit_only_from_the_turns_audited_writes(session, agent_thread_factory,
                                                                 tmp_path):
    """D7: classified writes only, result_preview as the head, no delegated_task."""
    thread = agent_thread_factory()
    _user(session, thread, "earlier turn", 0)
    _audit(session, thread, "quote_rfq", "x0", 10, args={"rfq_id": 1}, preview="old")
    _user(session, thread, "release 9301", 100)
    _audit(session, thread, "update_settlement_cashflow", "x1", 110,
           args={"cashflow_id": 9301}, preview="updated")
    _audit(session, thread, "generate_settlement_notice", "x2", 115,
           args={"cashflow_id": 9301}, status="error", error="notice failed")
    row = _audit(session, thread, "release_settlement_cashflow", "x3", 120,
                 args={"cashflow_id": 9301})
    session.commit()
    absent = tmp_path / "absent.sqlite3"
    rec = _rebuild(session, row, absent)
    assert (rec.fidelity, rec.agent) == (records.AUDIT_ONLY, None)
    assert rec.window.delegated_task is None
    assert [(c.name, c.result) for c in rec.window.earlier_calls] == [
        ("update_settlement_cashflow", "updated"), ("generate_settlement_notice", "notice failed")]
    assert not absent.exists()        # read-only: the sweep never creates a trace DB


def test_trace_rows_without_the_calls_own_span_are_audit_only(session, agent_thread_factory,
                                                              tmp_path):
    thread = agent_thread_factory()
    _user(session, thread, "go", 0)
    row = _audit(session, thread, "close_position", "mine", 5, args={"position_id": 1})
    session.commit()
    db = _trace_db(tmp_path, [_tool(thread.id, "R.x", "get_positions", 2, call_id="other",
                                    agent=ORCH, args={}, result="[]")])
    assert _rebuild(session, row, db).fidelity == records.AUDIT_ONLY


def test_span_results_read_back_what_the_agent_saw():
    content = "The knock-out for **9311** is recorded.\nIt's settled."
    command = repr(Command(update={"messages": [ToolMessage(content=content, tool_call_id="t")]}))
    as_task = {"output": {"lc": 1, "type": "not_implemented",
                          "id": ["langgraph", "types", "Command"], "repr": command}}
    assert records.span_result(as_task, None) == content
    as_tool = json.loads(trace_json({"output": ToolMessage(content="ok", tool_call_id="t")}))
    assert records.span_result(as_tool, None) == "ok"
    assert records.span_result({}, "1 validation error for X\nTraceback ...") == \
        "Error: 1 validation error for X"
    assert records.span_result(None, None) is None
    garbled = "Command(update={'messages': [ToolMessage(content='unterminated"
    assert records.span_result({"output": {"repr": garbled}}, None) == garbled


def test_every_trace_query_is_thread_scoped_and_indexed(tmp_path):
    """Open risk: the live trace DB is 38 GB; an unscoped query must not exist."""
    conn = sqlite3.connect(tmp_path / "plan.sqlite3")
    conn.executescript(_SCHEMA)
    for sql in (records.TOOL_SPANS_SQL, records.LLM_SPANS_SQL):
        assert "thread_id = ?" in sql
        plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql,
                                                          (1, "2026-09-01")))
        assert "ix_trace_runs_thread_start" in plan, plan
    conn.close()

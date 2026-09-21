"""What the guard sends to Jev: a bounded, documented projection (spec §1)."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.models import AgentMessage
from app.services.deep_agent.tool_guard_state import (
    ARGS_HEAD_CHARS, build_guard_state, load_user_request,
)


def _user(session, thread, text, role="user"):
    msg = AgentMessage(thread_id=thread.id, role=role, content=text, meta={})
    session.add(msg)
    session.flush()
    return msg


def test_latest_user_message_of_the_thread(session, agent_thread_factory):
    t = agent_thread_factory()
    _user(session, t, "first")
    _user(session, t, "reply", role="assistant")
    _user(session, t, "second")
    session.commit()
    assert load_user_request({"thread_id": t.id}) == ("second", "latest")


def test_user_message_id_selects_exactly_that_message(session, agent_thread_factory):
    t = agent_thread_factory()
    first = _user(session, t, "void cashflow 9300")
    _user(session, t, "later message")
    session.commit()
    assert load_user_request({"thread_id": t.id, "user_message_id": first.id}) == (
        "void cashflow 9300", "message_id")


def test_non_user_or_other_thread_message_id_is_unresolvable(session, agent_thread_factory):
    t, other = agent_thread_factory(), agent_thread_factory()
    _user(session, t, "hi")
    reply = _user(session, t, "assistant text", role="assistant")
    foreign = _user(session, other, "someone else")
    session.commit()
    assert load_user_request({"thread_id": t.id, "user_message_id": reply.id}) == (None, None)
    assert load_user_request({"thread_id": t.id, "user_message_id": foreign.id}) == (None, None)


def test_no_thread_or_no_user_message(session, agent_thread_factory):
    t = agent_thread_factory()
    session.commit()
    assert load_user_request({}) == (None, None)
    assert load_user_request({"thread_id": t.id}) == (None, None)


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_state_shape_and_caps():
    pending = _call("void_settlement_cashflow", {"cashflow_id": 9300, "api_key": "sk-x"}, "c9")
    messages = [
        HumanMessage("delegated: " + "d" * 3000),
        AIMessage("", tool_calls=[_call("reopen_position", {"position_id": 27}, "c1")]),
        ToolMessage("Settle, void, or edit that cashflow first. " + "r" * 400, tool_call_id="c1"),
        AIMessage("", tool_calls=[pending]),
    ]
    state = build_guard_state(messages, pending, user_request="u" * 5000, is_subagent=True)
    assert state["mode"] == "auto (no human will review this call)"
    assert len(state["user_request"]) == 4000 and state["user_request"].endswith("…")
    assert len(state["delegated_task"]) == 2000
    assert state["pending_tool_call"] == {
        "name": "void_settlement_cashflow", "args": {"cashflow_id": 9300, "api_key": "[REDACTED]"}}
    [earlier] = state["earlier_in_this_turn"]
    assert earlier.startswith('reopen_position({"position_id": 27}) -> Settle, void')
    head = earlier.split(" -> ", 1)[1]
    assert len(head) == 300 and head.endswith("…")


def test_orchestrator_stack_sends_no_delegated_task():
    pending = _call("close_position", {"position_id": 1}, "c1")
    state = build_guard_state([HumanMessage("hi"), AIMessage("", tool_calls=[pending])],
                              pending, user_request="close 1", is_subagent=False)
    assert "delegated_task" not in state


def test_earlier_calls_are_since_the_last_human_message_last_eight_args_capped():
    old = _call("get_positions", {}, "old")
    calls = [_call("get_cashflow", {"id": i, "blob": "b" * 1000}, f"k{i}") for i in range(10)]
    pending = _call("void_settlement_cashflow", {"cashflow_id": 1}, "p")
    messages = [HumanMessage("earlier turn"), AIMessage("", tool_calls=[old]),
                ToolMessage("old result", tool_call_id="old"), HumanMessage("this turn")]
    for call in calls:
        messages += [AIMessage("", tool_calls=[call]), ToolMessage(f"r{call['id']}", tool_call_id=call["id"])]
    messages.append(AIMessage("", tool_calls=[pending]))
    earlier = build_guard_state(messages, pending, user_request="x", is_subagent=False)["earlier_in_this_turn"]
    assert len(earlier) == 8
    assert earlier[0].startswith("get_cashflow(") and "rk2" in earlier[0]
    assert not any("old result" in e for e in earlier)
    args_text = earlier[0][len("get_cashflow("): earlier[0].index(") -> ")]
    assert len(args_text) == ARGS_HEAD_CHARS


def test_a_call_without_a_result_is_marked():
    pending = _call("close_position", {"position_id": 1}, "p")
    messages = [HumanMessage("go"), AIMessage("", tool_calls=[_call("x", {}, "k")]),
                AIMessage("", tool_calls=[pending])]
    [earlier] = build_guard_state(messages, pending, user_request="go", is_subagent=False)["earlier_in_this_turn"]
    assert earlier == "x({}) -> (no result)"

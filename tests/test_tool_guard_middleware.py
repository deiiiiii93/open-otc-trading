"""ToolGuardMiddleware resolution + shadow behaviour (spec §1, D10/D11/D17)."""
from __future__ import annotations

import asyncio
import datetime as dt
import decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from _system_one_fakes import JevPost
from app import database
from app.models import AgentMessage, AgentToolGuardVerdict
from app.services.deep_agent import tool_guard
from app.services.deep_agent.tool_guard import ToolGuardMiddleware
from app.services.deep_agent.tool_guard_store import (
    GuardStoreUnavailable, args_fingerprint, commit_verdict,
)

USER_TEXT = "Settle today's cashflows."


@pytest.fixture
def guard_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "shadow")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def thread(session, agent_thread_factory):
    t = agent_thread_factory()
    session.add(AgentMessage(thread_id=t.id, role="user", content=USER_TEXT, meta={}))
    session.commit()
    return t


def ctx(monkeypatch, **context):
    monkeypatch.setattr(tool_guard, "_read_audit_context", lambda: dict(context))


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def state(*calls, first_human="delegated task text"):
    return {"messages": [HumanMessage(first_human), AIMessage("", tool_calls=list(calls))]}


def rows():
    with database.SessionLocal() as s:
        return s.query(AgentToolGuardVerdict).order_by(AgentToolGuardVerdict.id).all()


VOID = ("void_settlement_cashflow", {"cashflow_id": 9300})


# --- inert paths (D6, D17, runtime belt) ------------------------------------

@pytest.mark.parametrize("setup", [
    lambda mp: mp.setenv("OPEN_OTC_SYSTEM_ONE", "false"),
    lambda mp: mp.setenv("OPEN_OTC_TOOL_GUARD", "off"),
])
def test_inert_when_switched_off(guard_env, thread, monkeypatch, setup):
    setup(monkeypatch)
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    assert ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call(*VOID, "c1")), None) is None
    assert post.calls == [] and rows() == []


@pytest.mark.parametrize("mode", ["interactive", "yolo", None])
def test_runtime_belt_requires_auto(guard_env, thread, monkeypatch, mode):
    ctx(monkeypatch, mode=mode, thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(state(call(*VOID, "c1")), None)
    assert post.calls == [] and rows() == []


def test_unguarded_calls_are_ignored(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call("get_position_summaries", {}, "c1")), None)
    assert post.calls == [] and rows() == []


# --- shadow records and never blocks -----------------------------------------

def test_shadow_records_a_flagged_verdict_and_returns_none(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost({"unnamed_target": 0.85})
    result = ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call(*VOID, "c1")), None)
    assert result is None
    [row] = rows()
    assert (row.verdict, row.max_probability, row.action) == ("flagged", 0.85, "recorded")
    assert [p["key"] for p in row.predicates_json] == [
        "unnamed_target", "from_document", "clears_blocker"]
    assert row.predicates_json[0] == {"key": "unnamed_target", "probability": 0.85,
                                      "threshold": 0.5, "flagged": True,
                                      "evidence": "tested-posthoc"}
    assert (row.thread_id, row.tool_call_id, row.persona) == (thread.id, "c1", "trader")
    assert (row.guard_mode, row.exec_mode, row.user_request_source) == ("shadow", "auto", "latest")
    assert row.model == "typesafe/jev-1.13" and isinstance(row.latency_ms, int)
    assert len(post.calls) == 1   # D12: one request, every predicate inside it
    assert set(post.calls[0]["questions"]) == {"unnamed_target", "from_document", "clears_blocker"}


def test_shadow_clear(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(state(call(*VOID, "c1")), None)
    [row] = rows()
    assert (row.verdict, row.max_probability) == ("clear", 0.05)


def test_no_key_is_a_visible_unscored_row_and_the_tool_runs(guard_env, thread, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    assert ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(
        state(call(*VOID, "c1")), None) is None
    [row] = rows()
    assert (row.verdict, row.unscored_reason, row.latency_ms) == ("unscored", "no_key", None)
    assert row.model == "typesafe/jev-1.13" and row.predicates_json == []


def test_empty_tool_call_id_never_reaches_jev(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(state(call(*VOID, "")), None)
    assert post.calls == []
    [row] = rows()
    assert (row.tool_call_id, row.unscored_reason) == ("", "no_tool_call_id")


def test_a_committed_verdict_is_reused_and_jev_is_not_asked_again(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost({"unnamed_target": 0.9})
    mw = ToolGuardMiddleware(persona="trader", post=post)
    s = state(call(*VOID, "c1"))
    mw.after_model(s, None)
    post.probs = {}                       # a fresh ask would now say "clear"
    [decision] = mw._resolve_all(mw._prepare(s))
    assert decision.verdict == "flagged"
    assert len(post.calls) == 1 and len(rows()) == 1


def test_a_reused_id_for_a_different_call_is_a_collision_never_the_stored_clear(
        guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    mw = ToolGuardMiddleware(persona="trader", post=post)
    mw.after_model(state(call("void_settlement_cashflow", {"cashflow_id": 1}, "c1")), None)
    [decision] = mw._resolve_all(mw._prepare(
        state(call("void_settlement_cashflow", {"cashflow_id": 2}, "c1"))))
    assert (decision.verdict, decision.unscored_reason, decision.row_id) == (
        "unscored", "tool_call_id_collision", None)
    assert len(post.calls) == 1 and len(rows()) == 1


def test_no_user_request(guard_env, session, agent_thread_factory, monkeypatch):
    empty = agent_thread_factory()
    session.commit()
    ctx(monkeypatch, mode="auto", thread_id=empty.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(state(call(*VOID, "c1")), None)
    assert post.calls == []
    assert rows()[0].unscored_reason == "no_user_request"


def test_user_request_comes_from_the_db_not_the_delegated_task(guard_env, thread, monkeypatch):
    """D11: inside a persona the first human message is the orchestrator's paraphrase."""
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call(*VOID, "c1"), first_human="void cashflow 9300 please"), None)
    sent = post.calls[0]["state"]
    assert sent["user_request"] == USER_TEXT
    assert sent["delegated_task"] == "void cashflow 9300 please"


def test_user_message_id_is_recorded_as_the_source(guard_env, thread, session, monkeypatch):
    first = session.query(AgentMessage).filter_by(thread_id=thread.id).one()
    ctx(monkeypatch, mode="auto", thread_id=thread.id, user_message_id=first.id)
    ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(state(call(*VOID, "c1")), None)
    assert rows()[0].user_request_source == "message_id"


def test_args_are_redacted_before_they_leave_the_process(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    post = JevPost()
    ToolGuardMiddleware(persona="trader", post=post).after_model(
        state(call("void_settlement_cashflow", {"cashflow_id": 1, "api_key": "sk-" + "x" * 20}, "c1")),
        None)
    assert post.calls[0]["state"]["pending_tool_call"]["args"]["api_key"] == "[REDACTED]"
    assert rows()[0].args_json["api_key"] == "[REDACTED]"


def test_non_json_args_do_not_crash(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(
        state(call("settle_position", {"position_id": 1, "as_of": dt.datetime(2026, 9, 21),
                                       "amount": decimal.Decimal("1.5")}, "c1")), None)
    assert rows()[0].args_json == {"position_id": 1, "as_of": "2026-09-21 00:00:00", "amount": "1.5"}


def test_an_unexpected_exception_is_unscored_internal_error(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(tool_guard, "build_guard_state", boom)
    assert ToolGuardMiddleware(persona="trader", post=JevPost()).after_model(
        state(call(*VOID, "c1")), None) is None
    assert rows()[0].unscored_reason == "internal_error"


def test_shadow_runs_the_tool_when_the_store_is_down(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)

    def down(*a, **k):
        raise GuardStoreUnavailable("locked")

    monkeypatch.setattr(tool_guard, "find_verdict", down)
    mw = ToolGuardMiddleware(persona="trader", post=JevPost())
    assert mw.after_model(state(call(*VOID, "c1")), None) is None
    [decision] = mw._resolve_all(mw._prepare(state(call(*VOID, "c1"))))
    assert decision.verdict == "persist_failed"


def test_a_lost_insert_race_returns_the_stored_row(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    fp = args_fingerprint(*VOID)
    commit_verdict(dict(thread_id=thread.id, tool_call_id="c1", persona="trader",
                        exec_mode="auto", guard_mode="shadow", tool_name=VOID[0],
                        args_json=fp.payload, redacted=False, args_hash=fp.sha256,
                        user_request_source="latest", verdict="clear", unscored_reason=None,
                        predicates_json=[], max_probability=0.1, model="m",
                        latency_ms=1, error=None))
    monkeypatch.setattr(tool_guard, "find_verdict", lambda *a: None)   # lookup "missed" it
    mw = ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.99}))
    [decision] = mw._resolve_all(mw._prepare(state(call(*VOID, "c1"))))
    assert decision.verdict == "clear"


def test_async_path_records_the_same_way(guard_env, thread, monkeypatch):
    ctx(monkeypatch, mode="auto", thread_id=thread.id)
    mw = ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.9}))
    assert asyncio.run(mw.aafter_model(state(call(*VOID, "c1")), None)) is None
    assert rows()[0].verdict == "flagged"


def test_construction_validates_the_policy():
    from app.services.deep_agent.tool_guard_policy import GuardPredicate

    with pytest.raises(ValueError):
        ToolGuardMiddleware(persona="trader",
                            policy={"book_extracted_trade": (GuardPredicate("k", "p"),)})


def test_scored_fields_is_the_one_flag_rule():
    """Spec 2026-09-22 D5: the sweep flags with the live guard's own rule."""
    from app.services.deep_agent.tool_guard import scored_fields
    from app.services.deep_agent.tool_guard_policy import GuardPredicate
    from app.services.system_one import NoulAnswer, SystemOneResult

    predicates = (GuardPredicate("a", "p", threshold=0.5), GuardPredicate("b", "q", threshold=0.7))
    result = SystemOneResult(answers={"a": NoulAnswer(0.5), "b": NoulAnswer(0.69)},
                             model="m", latency_ms=12)
    fields = scored_fields(predicates, result, user_request_source="occurred_at")
    assert fields["verdict"] == "flagged"                     # >= threshold flags
    assert [p["flagged"] for p in fields["predicates_json"]] == [True, False]
    assert (fields["max_probability"], fields["model"], fields["latency_ms"],
            fields["user_request_source"], fields["unscored_reason"]) == (0.69, "m", 12, "occurred_at", None)

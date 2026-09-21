"""Enforce: flagged or unscoreable calls take the normal approval card; the
interrupt set is a pure function of committed state (D8, D10)."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from _system_one_fakes import JevPost
from app import database
from app.models import AgentMessage, AgentToolGuardVerdict
from app.services.deep_agent import tool_guard
from app.services.deep_agent.hitl import GUARD_NOTE_PREFIX
from app.services.deep_agent.tool_guard import ToolGuardMiddleware
from app.services.deep_agent.tool_guard_store import args_fingerprint, commit_verdict


@pytest.fixture
def enforce_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "enforce")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def thread(session, agent_thread_factory, monkeypatch):
    t = agent_thread_factory()
    session.add(AgentMessage(thread_id=t.id, role="user", content="Settle today's cashflows.", meta={}))
    session.commit()
    monkeypatch.setattr(tool_guard, "_read_audit_context",
                        lambda: {"mode": "auto", "thread_id": t.id})
    return t


class Interrupts:
    """Fake langgraph.types.interrupt: records requests, replays scripted answers.
    A scripted exception class is raised (a first pass that pauses)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer()
        return answer


class Paused(Exception):
    pass


def call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def run(mw, *calls):
    ai = AIMessage("", tool_calls=list(calls))
    return ai, mw.after_model({"messages": [HumanMessage("task"), ai]}, None)


def approve():
    return {"decisions": [{"type": "approve"}]}


def reject():
    return {"decisions": [{"type": "reject"}]}


VOID = call("void_settlement_cashflow", {"cashflow_id": 9300}, "c1")


def test_flagged_cards_and_approve_keeps_the_call(enforce_env, thread, monkeypatch):
    fake = Interrupts(approve())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.85})), VOID)
    [action] = fake.requests[0]["action_requests"]
    assert action["name"] == "void_settlement_cashflow"
    assert action["description"].startswith(GUARD_NOTE_PREFIX)
    assert "unnamed_target p=0.85" in action["description"]
    assert fake.requests[0]["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]
    assert result["messages"] == [ai] and ai.tool_calls == [VOID]
    with database.SessionLocal() as s:
        assert s.query(AgentToolGuardVerdict).one().action == "interrupted"


def test_reject_keeps_the_call_and_answers_it_with_an_error(enforce_env, thread, monkeypatch):
    monkeypatch.setattr(tool_guard, "interrupt", Interrupts(reject()))
    ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost({"from_document": 0.9})), VOID)
    [tool_message] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert (tool_message.status, tool_message.tool_call_id) == ("error", "c1")
    assert ai.tool_calls == [VOID]


def test_clear_runs_unattended(enforce_env, thread, monkeypatch):
    fake = Interrupts()
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    _ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost()), VOID)
    assert result is None and fake.requests == []


def _no_key(mp, post, session, thread):
    mp.delenv("ZENMUX_API_KEY")


def _timeout(mp, post, session, thread):
    post.exc = TimeoutError("slow")


def _http(mp, post, session, thread):
    post.exc = RuntimeError("HTTP 502")


def _bad(mp, post, session, thread):
    post.response = {"answers": {}}


def _too_large(mp, post, session, thread):
    mp.setenv("OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS", "10")


def _internal(mp, post, session, thread):
    def boom(*a, **k):
        raise RuntimeError("bug")
    mp.setattr(tool_guard, "build_guard_state", boom)


def _no_user(mp, post, session, thread):
    session.query(AgentMessage).filter_by(thread_id=thread.id).delete()
    session.commit()


def _collision(mp, post, session, thread):
    fp = args_fingerprint("void_settlement_cashflow", {"cashflow_id": 1})   # different args, same id
    commit_verdict(dict(thread_id=thread.id, tool_call_id="c1", persona="trader",
                        exec_mode="auto", guard_mode="enforce",
                        tool_name="void_settlement_cashflow", args_json=fp.payload,
                        redacted=False, args_hash=fp.sha256, user_request_source="latest",
                        verdict="clear", unscored_reason=None, predicates_json=[],
                        max_probability=0.05, model="m", latency_ms=1, error=None))


@pytest.mark.parametrize("setup, reason", [
    (_no_key, "no_key"), (_timeout, "timeout"), (_http, "http_error"),
    (_bad, "bad_response"), (_too_large, "state_too_large"), (_internal, "internal_error"),
    (_no_user, "no_user_request"), (_collision, "tool_call_id_collision"),
])
def test_every_unscored_reason_takes_the_approval_card(
        enforce_env, thread, session, monkeypatch, setup, reason):
    post = JevPost()
    setup(monkeypatch, post, session, thread)
    fake = Interrupts(approve())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    run(ToolGuardMiddleware(persona="trader", post=post), VOID)
    [action] = fake.requests[0]["action_requests"]
    assert f"({reason})" in action["description"]


def test_empty_id_cards_even_when_its_best_effort_row_is_lost(enforce_env, thread, monkeypatch):
    monkeypatch.setattr(tool_guard, "record_structural", lambda fields: None)
    fake = Interrupts(approve())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    post = JevPost()
    run(ToolGuardMiddleware(persona="trader", post=post),
        call("void_settlement_cashflow", {"cashflow_id": 1}, ""))
    assert post.calls == []
    assert "(no_tool_call_id)" in fake.requests[0]["action_requests"][0]["description"]


def test_resume_reuses_the_committed_verdict_and_never_re_asks(enforce_env, thread, monkeypatch):
    """LangGraph re-runs the node on resume. A fresh (non-deterministic) answer
    would shrink the card set and mis-attach the positional decision."""
    fake = Interrupts(Paused, reject())
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    post = JevPost({"unnamed_target": 0.9})
    mw = ToolGuardMiddleware(persona="trader", post=post)
    ai = AIMessage("", tool_calls=[VOID])
    state = {"messages": [HumanMessage("task"), ai]}
    with pytest.raises(Paused):
        mw.after_model(state, None)
    post.probs = {}                                     # a fresh ask would now be "clear"
    result = mw.after_model(state, None)
    assert len(post.calls) == 1
    assert fake.requests[1]["action_requests"] == fake.requests[0]["action_requests"]
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in result["messages"])


def test_mixed_message_one_request_original_order(enforce_env, thread, monkeypatch):
    fake = Interrupts({"decisions": [{"type": "approve"}, {"type": "reject"}]})
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    clear = call("void_settlement_cashflow", {"cashflow_id": 1}, "c-clear")
    flagged = call("close_position", {"position_id": 27}, "c-flag")
    unscored = call("mark_knockout", {"position_id": 28}, "")

    class PerCall(JevPost):
        def __call__(self, url, payload, timeout):
            name = payload["state"]["pending_tool_call"]["name"]
            self.probs = {"unnamed_target": 0.9} if name == "close_position" else {}
            return super().__call__(url, payload, timeout)

    ai, result = run(ToolGuardMiddleware(persona="trader", post=PerCall()), clear, flagged, unscored)
    names = [a["name"] for a in fake.requests[0]["action_requests"]]
    assert names == ["close_position", "mark_knockout"]
    assert ai.tool_calls == [clear, flagged, unscored]
    [rejected] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert rejected.tool_call_id == ""   # the second carded call was rejected


def test_a_decision_count_mismatch_raises(enforce_env, thread, monkeypatch):
    monkeypatch.setattr(tool_guard, "interrupt", Interrupts({"decisions": []}))
    with pytest.raises(ValueError, match="decisions"):
        run(ToolGuardMiddleware(persona="trader", post=JevPost({"unnamed_target": 0.9})), VOID)


from app.services.deep_agent.tool_guard_store import GuardStoreUnavailable  # noqa: E402


def _fail_commit_for(monkeypatch, failing_id):
    real = tool_guard.commit_verdict

    def commit(fields):
        if fields["tool_call_id"] == failing_id:
            raise GuardStoreUnavailable("locked")
        return real(fields)

    monkeypatch.setattr(tool_guard, "commit_verdict", commit)


def test_mixed_with_a_persist_failure_is_a_refusal_pass(enforce_env, thread, monkeypatch):
    """[clear, flagged, persist_failed] => no interrupt at all; every guarded call
    that is not a committed clear is refused; order preserved (D10 rule 3)."""
    fake = Interrupts()
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    _fail_commit_for(monkeypatch, "c-fail")
    clear = call("void_settlement_cashflow", {"cashflow_id": 1}, "c-clear")
    flagged = call("close_position", {"position_id": 27}, "c-flag")
    failed = call("settle_position", {"position_id": 28}, "c-fail")
    unguarded = call("get_position_summaries", {}, "c-read")

    class PerCall(JevPost):
        def __call__(self, url, payload, timeout):
            name = payload["state"]["pending_tool_call"]["name"]
            self.probs = {"unnamed_target": 0.9} if name == "close_position" else {}
            return super().__call__(url, payload, timeout)

    ai, result = run(ToolGuardMiddleware(persona="trader", post=PerCall()),
                     clear, flagged, failed, unguarded)
    assert fake.requests == []
    assert ai.tool_calls == [clear, flagged, failed, unguarded]   # calls stay, answered
    refused = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in refused] == ["c-flag", "c-fail"]
    assert all(m.status == "error" and "fail-closed" in m.content for m in refused)


def test_shadow_runs_on_a_persist_failure(thread, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "shadow")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    _fail_commit_for(monkeypatch, "c1")
    _ai, result = run(ToolGuardMiddleware(persona="trader", post=JevPost()), VOID)
    assert result is None


def test_an_unreadable_store_on_resume_refuses_rather_than_re_asks(enforce_env, thread, monkeypatch):
    def down(*a, **k):
        raise GuardStoreUnavailable("locked")

    monkeypatch.setattr(tool_guard, "find_verdict", down)
    fake = Interrupts()
    monkeypatch.setattr(tool_guard, "interrupt", fake)
    post = JevPost()
    _ai, result = run(ToolGuardMiddleware(persona="trader", post=post), VOID)
    assert fake.requests == [] and post.calls == []
    assert isinstance(result["messages"][-1], ToolMessage)

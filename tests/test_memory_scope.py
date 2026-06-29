"""Tests for memory scope keys and book resolution (Task 5)."""
from app.services.deep_agent.memory.scope import (
    scope_key, resolve_book_scope, active_read_scopes, active_write_scopes,
)


def test_scope_key():
    assert scope_key("user") == ("user", "desk")
    assert scope_key("correction") == ("correction", "desk")
    assert scope_key("domain") == ("domain", "global")
    assert scope_key("book", "42") == ("book", "42")


def test_resolve_book_scope_counts():
    live = {1, 2}
    assert resolve_book_scope([], live.__contains__) is None
    assert resolve_book_scope([1], live.__contains__) == ("book", "1")
    assert resolve_book_scope([1, 2], live.__contains__) is None
    assert resolve_book_scope([1, 99], live.__contains__) == ("book", "1")
    assert resolve_book_scope([98, 99], live.__contains__) is None


def test_read_and_write_scope_sets():
    assert active_read_scopes(None) == [
        ("user", "desk"), ("correction", "desk"), ("domain", "global")]
    assert ("book", "7") in active_read_scopes(("book", "7"))
    assert "book" not in active_write_scopes(None)
    assert "book" in active_write_scopes(("book", "7"))


# --- DB-backed tests ---

from app.models import Workflow, ContextPack, ContextPackPayload, Portfolio, AgentSession
from app.services.deep_agent.memory.scope import book_scope_for_session


def _pack(session, workflow_id, portfolio_ids):
    payload = ContextPackPayload(
        content_hash=f"h{portfolio_ids}-{workflow_id}",
        stable_payload={"task_brief": {"portfolio_ids": portfolio_ids}})
    session.add(payload); session.flush()
    pack = ContextPack(workflow_id=workflow_id, payload_id=payload.id, metadata_={})
    session.add(pack); session.flush()
    return pack


def test_book_scope_for_session_last_single(session, agent_thread_factory):
    thread = agent_thread_factory()
    wf = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf); session.flush()
    s = AgentSession(workflow_id=wf.id, persona="trader", episode_id=1,
                     status="closed", checkpointer_key="k1")
    session.add(s); session.flush()
    p = Portfolio(name="bookA"); session.add(p); session.flush()
    _pack(session, wf.id, [p.id, 9999])
    _pack(session, wf.id, [p.id])
    session.flush()
    assert book_scope_for_session(session, s.id) == str(p.id)


def test_book_scope_for_session_ambiguous_none(session, agent_thread_factory):
    thread = agent_thread_factory()
    wf = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf); session.flush()
    s = AgentSession(workflow_id=wf.id, persona="trader", episode_id=1,
                     status="closed", checkpointer_key="k2")
    session.add(s); session.flush()
    p1 = Portfolio(name="b1"); p2 = Portfolio(name="b2")
    session.add_all([p1, p2]); session.flush()
    _pack(session, wf.id, [p1.id, p2.id])
    session.flush()
    assert book_scope_for_session(session, s.id) is None


def test_book_scope_for_session_filters_non_live(session, agent_thread_factory):
    thread = agent_thread_factory()
    wf = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf); session.flush()
    s = AgentSession(workflow_id=wf.id, persona="trader", episode_id=1,
                     status="closed", checkpointer_key="k3")
    session.add(s); session.flush()
    live = Portfolio(name="live-book"); gone = Portfolio(name="gone-book")
    session.add_all([live, gone]); session.flush()
    _pack(session, wf.id, [live.id, gone.id])   # two referenced ids
    session.delete(gone); session.flush()        # one is no longer live
    # after filtering non-live, exactly one live remains -> resolves to it.
    assert book_scope_for_session(session, s.id) == str(live.id)

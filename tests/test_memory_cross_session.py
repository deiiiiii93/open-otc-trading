"""Cross-session memory integration tests (Task 18).

Verifies that:
1. search_memories() returns injectable Facts (active user-scope) and
   excludes non-approved domain facts (scope-aware loader, no namespace).
2. Facts written via MemoryWriteQueue in session A are visible to
   load_injectable in a later session B (genuine cross-session test).
"""
from app import database
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import MemoryStore
from app.services.deep_agent.memory.runs import ExtractionRunStore, RunSpec, session_run_key
from app.services.deep_agent.memory.queue import MemoryWriteQueue
from app.services.deep_agent.memory.inject import format_for_injection
from app.services.deep_agent.memory.scope import active_read_scopes


def test_search_memories_returns_injectable(session):
    from app.services.agents import search_memories
    store = MemoryStore(MemoryConfig())
    store.create(session, scope_type="user", scope_id="desk", content="books in USD")
    store.create(session, scope_type="domain", scope_id="global", content="cnh act/365")
    session.commit()
    contents = {f.content for f in search_memories(session)}
    assert "books in USD" in contents
    assert "cnh act/365" not in contents  # proposed, not approved


def test_cross_session_fact_injected_later(session):
    cfg = MemoryConfig()
    store = MemoryStore(cfg)
    q = MemoryWriteQueue(
        cfg, store, ExtractionRunStore(cfg),
        session_factory=lambda: database.SessionLocal(),
        window_loader=lambda sid, after, c: [{"id": 1, "role": "user", "content": "I book in USD"}],
        extractor_llm=lambda p: '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}',
        portfolio_resolver=lambda s, sid: None)
    with database.SessionLocal() as s:
        q.run_job(s, RunSpec(run_key=session_run_key(1), kind="session", session_id=1,
                             thread_id=1, persona="trader", book_scope_id=None,
                             trigger_message_id=None))
        s.commit()
    with database.SessionLocal() as s:
        block = format_for_injection(store.load_injectable(s, active_read_scopes(None)), cfg)
    assert "books in USD" in block

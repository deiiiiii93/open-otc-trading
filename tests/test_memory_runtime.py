# tests/test_memory_runtime.py
import time
from app import database
from app.models import AgentMessage, MemoryExtractionRun


def test_singletons_cached_no_deadlock(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory.runtime import (
        get_memory_store, get_memory_queue, get_memory_middleware, reset_memory_runtime,
    )
    reset_memory_runtime()
    # These nested locked getters (queue->store, middleware->queue+store) must
    # return without hanging — proves _LOCK is reentrant (RLock).
    assert get_memory_store() is get_memory_store()
    assert get_memory_queue() is get_memory_queue()      # acquires _LOCK, then calls get_memory_store()
    assert get_memory_middleware() is get_memory_middleware()
    reset_memory_runtime()


def test_window_loader_filters_and_caps(session, agent_thread_factory):
    from app.models import Workflow, AgentSession
    from app.services.deep_agent.memory.config import MemoryConfig
    from app.services.deep_agent.memory.window import load_extraction_window
    thread = agent_thread_factory()
    wf = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf); session.flush()
    s = AgentSession(workflow_id=wf.id, persona="trader", episode_id=1,
                     status="closed", checkpointer_key="kwin")
    session.add(s); session.flush()
    session.add_all([
        AgentMessage(thread_id=thread.id, session_id=s.id, role="system", content="sys"),
        AgentMessage(thread_id=thread.id, session_id=s.id, role="user", content="book in USD"),
        AgentMessage(thread_id=thread.id, session_id=s.id, role="assistant", content="ok"),
    ])
    session.commit()
    window = load_extraction_window(s.id, None, MemoryConfig())
    roles = [m["role"] for m in window]
    assert "system" not in roles and roles == ["user", "assistant"]


def test_memory_configurable_and_latest_user_message(session, agent_thread_factory):
    from app.models import AgentMessage
    from app.services.deep_agent.memory.runtime import (
        memory_configurable, latest_user_message_id,
    )
    thread = agent_thread_factory()
    session.add_all([
        AgentMessage(thread_id=thread.id, role="user", content="first"),
        AgentMessage(thread_id=thread.id, role="assistant", content="reply"),
        AgentMessage(thread_id=thread.id, role="user", content="second"),
    ])
    session.commit()
    last_user = (session.query(AgentMessage)
                 .filter_by(thread_id=thread.id, role="user")
                 .order_by(AgentMessage.id.desc()).first())
    mid = latest_user_message_id(session, thread.id)
    assert mid == last_user.id
    cfg = memory_configurable(session_id=7, thread_id=thread.id, persona="trader", message_id=mid)
    assert cfg["memory_session_id"] == 7 and cfg["memory_message_id"] == mid
    assert "memory_message_id" not in memory_configurable(
        session_id=7, thread_id=thread.id, persona="trader", message_id=None)


def test_enqueue_session_close_lazy_starts_writer(session, agent_thread_factory, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory import runtime as rt
    rt.reset_memory_runtime()
    # stub the extractor llm so no network + a window loader that yields one message
    monkeypatch.setattr(rt, "_extractor_llm",
                        lambda prompt: '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}')
    monkeypatch.setattr(rt, "_window_loader",
                        lambda sid, after, cfg: [{"id": 1, "role": "human", "content": "book in USD"}])
    q = rt.get_memory_queue()
    try:
        rt.enqueue_session_close(session_id=31, thread_id=1, persona="trader", book_scope_id=None)
        assert q._writer is not None and q._writer.is_alive()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with database.SessionLocal() as s:
                run = s.get(MemoryExtractionRun, "session:31")
                if run is not None and run.status == "succeeded":
                    break
            time.sleep(0.05)
        with database.SessionLocal() as s:
            assert s.get(MemoryExtractionRun, "session:31").status == "succeeded"
    finally:
        rt.reset_memory_runtime()

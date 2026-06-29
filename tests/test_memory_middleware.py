# tests/test_memory_middleware.py
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app import database
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import MemoryStore
from app.services.deep_agent.memory.middleware import MemoryMiddleware, matches_correction


def test_matches_correction_word_boundary():
    cfg = MemoryConfig()
    assert matches_correction("No, actually we book in USD", cfg.correction_phrases)
    assert matches_correction("that's wrong, use ACT/365", cfg.correction_phrases)
    assert not matches_correction("everything is fine here", cfg.correction_phrases)


def test_before_agent_disabled_is_noop():
    cfg = MemoryConfig(enabled=False)
    mw = MemoryMiddleware(config=cfg, store=MemoryStore(cfg), queue=None, session_factory=None)
    assert mw.before_agent({}, None, cfg) is None


def test_before_agent_injects_block(session):
    cfg = MemoryConfig()
    store = MemoryStore(cfg)
    store.create(session, scope_type="user", scope_id="desk", content="books all trades in USD")
    session.commit()
    mw = MemoryMiddleware(config=cfg, store=store, queue=None,
                          session_factory=lambda: database.SessionLocal())
    update = mw.before_agent({}, None, cfg)
    assert update is not None and "books all trades in USD" in update["memory_block"]


def test_before_agent_injects_single_book(session, agent_thread_factory):
    cfg = MemoryConfig()
    store = MemoryStore(cfg)
    store.create(session, scope_type="book", scope_id="55", content="this book hedges weekly")
    session.commit()
    # resolver returns the single live book id "55"
    mw = MemoryMiddleware(config=cfg, store=store, queue=None,
                          session_factory=lambda: database.SessionLocal(),
                          book_resolver=lambda s, sid: "55")
    cfg_call = {"configurable": {"memory_session_id": 3}}
    update = mw.before_agent({}, None, cfg_call)
    assert update is not None and "this book hedges weekly" in update["memory_block"]


def test_wrap_model_call_appends_block_to_system_prompt():
    cfg = MemoryConfig()
    mw = MemoryMiddleware(config=cfg, store=MemoryStore(cfg), queue=None, session_factory=None)

    class _Req:
        def __init__(self):
            self.system_message = SystemMessage(content="BASE PROMPT")
            self.state = {"memory_block": "<memory>remembered</memory>"}
            self.captured = None
        def override(self, **kw):
            self.captured = kw
            return self

    req = _Req()
    seen = {}
    def handler(r):
        seen["sys"] = r.captured["system_message"].content
        return "RESULT"
    out = mw.wrap_model_call(req, handler)
    assert out == "RESULT"
    assert seen["sys"].index("BASE PROMPT") < seen["sys"].index("<memory>remembered</memory>")


def test_wrap_model_call_fail_open_on_override_error():
    cfg = MemoryConfig()
    mw = MemoryMiddleware(config=cfg, store=MemoryStore(cfg), queue=None, session_factory=None)

    class _BadReq:
        def __init__(self):
            self.system_message = SystemMessage(content="BASE")
            self.state = {"memory_block": "<memory>x</memory>"}
        def override(self, **kw):
            raise RuntimeError("override boom")

    bad = _BadReq()
    seen = {}
    def handler(r):
        seen["req"] = r
        return "OK"
    out = mw.wrap_model_call(bad, handler)
    assert out == "OK"             # turn still completes
    assert seen["req"] is bad      # original request used; no memory injected


def test_after_model_enqueues_high_on_correction(session):
    class _FakeQueue:
        def __init__(self): self.jobs = []
        def enqueue(self, job): self.jobs.append(job); return True
    cfg = MemoryConfig()
    q = _FakeQueue()
    mw = MemoryMiddleware(config=cfg, store=MemoryStore(cfg), queue=q,
                          session_factory=lambda: database.SessionLocal())
    state = {"messages": [AIMessage(content="I'll use ACT/365"),
                          HumanMessage(content="No, actually that's wrong", id="m42")]}
    # durable integer AgentMessage id supplied via configurable; the LangChain
    # string id "m42" is ignored.
    mw.after_model(state, None, {"configurable": {"memory_session_id": 3, "memory_message_id": 42}})
    assert len(q.jobs) == 1 and q.jobs[0].priority == "high"
    assert q.jobs[0].spec.run_key == "corr:3:42"
    assert q.jobs[0].spec.trigger_message_id == 42


def test_after_model_no_durable_message_id_is_noop(session):
    class _FakeQueue:
        def __init__(self): self.jobs = []
        def enqueue(self, job): self.jobs.append(job); return True
    cfg = MemoryConfig()
    q = _FakeQueue()
    mw = MemoryMiddleware(config=cfg, store=MemoryStore(cfg), queue=q,
                          session_factory=lambda: database.SessionLocal())
    state = {"messages": [HumanMessage(content="No, actually that's wrong", id="m42")]}
    # no memory_message_id -> cannot build a durable run_key -> no enqueue
    mw.after_model(state, None, {"configurable": {"memory_session_id": 3}})
    assert q.jobs == []


def test_read_path_uses_busy_timeout(session):
    """memory_read_session sets PRAGMA busy_timeout = read_timeout_ms (250)."""
    from app.services.deep_agent.memory.middleware import memory_read_session
    from sqlalchemy import text

    cfg = MemoryConfig()  # read_timeout_ms = 250

    captured = {}

    def _factory():
        s = database.SessionLocal()
        orig_execute = s.execute

        def recording_execute(stmt, *args, **kw):
            sql = str(stmt)
            if "busy_timeout" in sql.lower():
                captured["pragma"] = sql
            return orig_execute(stmt, *args, **kw)

        s.execute = recording_execute
        return s

    with memory_read_session(_factory, cfg.read_timeout_ms) as s:
        pass  # session opened, PRAGMA set, then closed

    assert "250" in captured.get("pragma", ""), (
        f"Expected PRAGMA busy_timeout = 250 to be set, got: {captured}"
    )


def test_enabled_false_no_enqueue(session):
    """enabled=False → after_model never enqueues, even on a correction phrase."""
    class _FakeQueue:
        def __init__(self): self.jobs = []
        def enqueue(self, job): self.jobs.append(job); return True

    cfg = MemoryConfig(enabled=False)
    q = _FakeQueue()
    mw = MemoryMiddleware(config=cfg, store=MemoryStore(cfg), queue=q,
                          session_factory=lambda: database.SessionLocal())
    state = {"messages": [HumanMessage(content="No, actually that's wrong")]}
    mw.after_model(state, None, {"configurable": {"memory_session_id": 1, "memory_message_id": 7}})
    assert q.jobs == []

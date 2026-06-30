# tests/test_memory_runtime.py
import time
import pytest
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
    # stub extractor llm, window loader, and model-id resolver (no YAML in worktree)
    monkeypatch.setattr(rt, "_extractor_llm",
                        lambda prompt: '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}')
    monkeypatch.setattr(rt, "_window_loader",
                        lambda sid, after, cfg: [{"id": 1, "role": "human", "content": "book in USD"}])
    monkeypatch.setattr(rt, "_extractor_model_id", lambda: "stub-model")
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


# ---------------------------------------------------------------------------
# Regression tests for _extractor_llm real body (import + model construction)
# These tests stub at the model-factory / registry boundary so they exercise
# the actual function body — they would have caught the broken import.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, content):
        self._content = content

    def invoke(self, prompt):
        return _FakeResponse(self._content)


class _FakeRegistry:
    """Minimal registry stub exposing the two methods the extractor resolver
    uses. ``tag_selection`` is what select_by_tag returns (None => no tagged
    model => caller falls back to default_selection)."""

    def __init__(self, *, tag_selection=None, default=None):
        self._tag_selection = tag_selection
        self._default = default or {
            "channel": "zenmux", "provider": "anthropic", "model": "default-model"}

    def select_by_tag(self, tag):
        return self._tag_selection

    def default_selection(self):
        return self._default


def test_extractor_llm_real_body_returns_str(monkeypatch):
    """_extractor_llm exercises get_registry + resolve_extractor_selection +
    build_agent_model and returns the model's string content unchanged. Stubs at
    the factory boundary so the import path and function body are fully exercised."""
    from app.services.deep_agent.memory import runtime as rt
    import app.services.deep_agent.channel_registry as cr
    import app.services.deep_agent.model_factory as mf

    expected = '{"add":[],"remove":[]}'
    monkeypatch.setattr(cr, "get_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(mf, "build_agent_model",
                        lambda reg, sel=None: _FakeModel(expected))

    result = rt._extractor_llm("some prompt")
    assert result == expected


def test_extractor_llm_builds_from_flash_tier_selection(monkeypatch):
    """_extractor_llm builds the model from the FLASH-tier selection resolved by
    select_by_tag, not the agent default — the routing this follow-up adds."""
    from app.services.deep_agent.memory import runtime as rt
    import app.services.deep_agent.channel_registry as cr
    import app.services.deep_agent.model_factory as mf

    flash = {"channel": "zenmux-flash", "provider": "openai", "model": "step-3.7-flash"}
    monkeypatch.setattr(cr, "get_registry",
                        lambda: _FakeRegistry(tag_selection=flash))
    seen = {}

    def _capture(reg, sel=None):
        seen["selection"] = sel
        return _FakeModel('{"add":[],"remove":[]}')

    monkeypatch.setattr(mf, "build_agent_model", _capture)
    rt._extractor_llm("some prompt")
    assert seen["selection"] == flash


def test_extractor_llm_non_str_content_raises(monkeypatch):
    """When the model returns non-str content (multimodal list), _extractor_llm
    must raise RuntimeError with a diagnostic message."""
    from app.services.deep_agent.memory import runtime as rt
    import app.services.deep_agent.channel_registry as cr
    import app.services.deep_agent.model_factory as mf

    monkeypatch.setattr(cr, "get_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(mf, "build_agent_model",
                        lambda reg, sel=None: _FakeModel([{"type": "text", "text": "hi"}]))

    with pytest.raises(RuntimeError, match="non-str content"):
        rt._extractor_llm("some prompt")


def test_extractor_llm_unavailable_model_raises(monkeypatch):
    """When build_agent_model returns None (channel unhealthy) a RuntimeError is raised."""
    from app.services.deep_agent.memory import runtime as rt
    import app.services.deep_agent.channel_registry as cr
    import app.services.deep_agent.model_factory as mf

    monkeypatch.setattr(cr, "get_registry", lambda: _FakeRegistry())
    monkeypatch.setattr(mf, "build_agent_model", lambda reg, sel=None: None)

    with pytest.raises(RuntimeError, match="unavailable"):
        rt._extractor_llm("some prompt")


# ---------------------------------------------------------------------------
# Flash routing: resolve_extractor_selection + provenance.
#
# MemoryConfig.extractor_model is a registry TAG ("fast"). resolve_extractor_
# selection prefers a healthy model carrying that tag and falls back to the
# registry default. _extractor_model_id() records WHAT was actually used.
# ---------------------------------------------------------------------------

def test_resolve_extractor_selection_prefers_tagged_model():
    from app.services.deep_agent.memory import runtime as rt
    from app.services.deep_agent.memory.config import MemoryConfig

    flash = {"channel": "zenmux-flash", "provider": "openai", "model": "doubao-turbo"}
    reg = _FakeRegistry(tag_selection=flash)
    assert rt.resolve_extractor_selection(reg, MemoryConfig()) == flash


def test_resolve_extractor_selection_falls_back_to_default():
    from app.services.deep_agent.memory import runtime as rt
    from app.services.deep_agent.memory.config import MemoryConfig

    default = {"channel": "zenmux", "provider": "anthropic", "model": "claude-opus"}
    reg = _FakeRegistry(tag_selection=None, default=default)
    assert rt.resolve_extractor_selection(reg, MemoryConfig()) == default


def test_extractor_model_id_returns_flash_tier_model(monkeypatch):
    """_extractor_model_id() returns the FLASH-tier model id when available,
    NOT the tier-concept string from MemoryConfig.extractor_model."""
    import app.services.deep_agent.channel_registry as cr
    from app.services.deep_agent.memory import runtime as rt

    flash = {"channel": "zenmux-flash", "provider": "openai", "model": "step-3.7-flash"}
    monkeypatch.setattr(cr, "get_registry", lambda: _FakeRegistry(tag_selection=flash))
    result = rt._extractor_model_id()
    assert result == "step-3.7-flash"
    assert result != "fast"  # not the config tier/tag name


def test_extractor_model_id_falls_back_to_default(monkeypatch):
    """When no model carries the tag, _extractor_model_id() records the registry
    default model id."""
    import app.services.deep_agent.channel_registry as cr
    from app.services.deep_agent.memory import runtime as rt

    default = {"channel": "zenmux", "provider": "anthropic", "model": "claude-haiku-test"}
    monkeypatch.setattr(cr, "get_registry",
                        lambda: _FakeRegistry(tag_selection=None, default=default))
    assert rt._extractor_model_id() == "claude-haiku-test"


def test_meta_extractor_model_reflects_real_model(session):
    """meta.extractor_model in a stored MemoryEntry is the actual resolved model id,
    not the 'flash' tier string from MemoryConfig.extractor_model.

    This is the API-limitation provenance fix: since ChannelRegistry.find_by_tag
    does not exist, we fall back to default selection but record WHAT we actually
    used via extractor_model_fn rather than blindly copying config.extractor_model.
    """
    from app import database
    from app.models import MemoryEntry
    from app.services.deep_agent.memory.config import MemoryConfig
    from app.services.deep_agent.memory.store import MemoryStore
    from app.services.deep_agent.memory.runs import ExtractionRunStore, RunSpec, session_run_key
    from app.services.deep_agent.memory.queue import MemoryWriteQueue

    actual_model_id = "anthropic/claude-haiku-resolved"
    cfg = MemoryConfig()
    q = MemoryWriteQueue(
        cfg, MemoryStore(cfg), ExtractionRunStore(cfg),
        session_factory=lambda: database.SessionLocal(),
        window_loader=lambda sid, after, c: [{"id": 1, "role": "user", "content": "USD books"}],
        extractor_llm=lambda p: '{"add":[{"content":"books USD","scope_type":"user","confidence":0.9}]}',
        portfolio_resolver=lambda s, sid: None,
        extractor_model_fn=lambda: actual_model_id,
    )
    spec = RunSpec(run_key=session_run_key(991), kind="session", session_id=991,
                   thread_id=1, persona="trader", book_scope_id=None, trigger_message_id=None)
    with database.SessionLocal() as s:
        q.run_job(s, spec); s.commit()
    with database.SessionLocal() as s:
        entry = s.query(MemoryEntry).filter_by(scope_type="user").first()
        assert entry is not None
        assert entry.meta["extractor_model"] == actual_model_id
        assert entry.meta["extractor_model"] != cfg.extractor_model  # not "flash"

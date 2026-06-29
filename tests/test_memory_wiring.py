# tests/test_memory_wiring.py
from langchain_core.messages import SystemMessage

from app import database


def test_agent_middleware_includes_memory_when_enabled(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory.runtime import reset_memory_runtime
    from app.services.deep_agent.memory.middleware import MemoryMiddleware
    from app.services.deep_agent import orchestrator
    reset_memory_runtime()
    mws = orchestrator._agent_middleware(False, model=None, backend=None, tools=[])
    assert any(isinstance(m, MemoryMiddleware) for m in mws)
    reset_memory_runtime()


def test_agent_middleware_omits_memory_when_disabled(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "off")
    from app.services.deep_agent.memory.runtime import reset_memory_runtime
    from app.services.deep_agent.memory.middleware import MemoryMiddleware
    from app.services.deep_agent import orchestrator
    reset_memory_runtime()
    mws = orchestrator._agent_middleware(False, model=None, backend=None, tools=[])
    assert not any(isinstance(m, MemoryMiddleware) for m in mws)


def test_real_prompt_path_via_assembled_chain(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory.runtime import reset_memory_runtime
    from app.services.deep_agent.memory.middleware import MemoryMiddleware
    from app.services.deep_agent.compaction import LedgerScopedCompactionMiddleware
    from app.services.deep_agent import orchestrator
    reset_memory_runtime()

    branches = [False]
    try:
        import langchain_quickjs  # noqa: F401
        branches.append(True)
    except Exception:
        pass

    for code_interp in branches:
        mws = orchestrator._agent_middleware(code_interp, model=None, backend=None, tools=[])
        mem = [m for m in mws if isinstance(m, MemoryMiddleware)]
        assert len(mem) == 1
        idx_mem = mws.index(mem[0])
        idx_comp = next(i for i, m in enumerate(mws)
                        if isinstance(m, LedgerScopedCompactionMiddleware))
        assert idx_comp < idx_mem            # memory runs AFTER compaction
        if code_interp:
            from langchain_quickjs import CodeInterpreterMiddleware
            idx_ci = next(i for i, m in enumerate(mws)
                          if isinstance(m, CodeInterpreterMiddleware))
            assert idx_mem < idx_ci          # memory before the code-interpreter mw

        # call wrap_model_call on the REAL assembled instance
        class _Req:
            def __init__(self):
                self.system_message = SystemMessage(content="ORCH BASE PROMPT")
                self.state = {"memory_block": "<memory>remembered ctx</memory>"}
                self.captured = None
            def override(self, **kw):
                self.captured = kw
                return self

        seen = {}
        out = mem[0].wrap_model_call(
            _Req(), lambda r: seen.update(sys=r.captured["system_message"].content) or "R")
        assert out == "R"
        assert seen["sys"].index("ORCH BASE PROMPT") < seen["sys"].index("<memory>remembered ctx</memory>")
    reset_memory_runtime()


def test_release_session_lease_enqueues_close(session, agent_thread_factory, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory.runtime import get_memory_queue, reset_memory_runtime
    from app.services.deep_agent.session_lifecycle import release_session_lease
    from app.models import Workflow, AgentSession, AgentTask
    reset_memory_runtime()
    thread = agent_thread_factory()
    wf = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf); session.flush()
    task = AgentTask(workflow_id=wf.id, task_type="run_risk", assigned_persona="trader",
                     inputs={}, depends_on=[])
    session.add(task); session.flush()
    s = AgentSession(workflow_id=wf.id, persona="trader", episode_id=1, status="active",
                     checkpointer_key="kwire", current_task_id=task.id)
    session.add(s); session.flush()
    # Ensure thread.id != wf.id (both start at 1 in fresh SQLite; bump wf id with a dummy)
    _dummy = Workflow(thread_id=thread.id, title="dummy", intent="chat")
    session.add(_dummy); session.flush()
    wf2 = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf2); session.flush()
    task2 = AgentTask(workflow_id=wf2.id, task_type="run_risk", assigned_persona="trader",
                      inputs={}, depends_on=[])
    session.add(task2); session.flush()
    s2 = AgentSession(workflow_id=wf2.id, persona="trader", episode_id=1, status="active",
                      checkpointer_key="kwire2", current_task_id=task2.id)
    session.add(s2); session.flush()
    q = get_memory_queue()
    q._ensure_writer = lambda: None   # deterministic: inspect the queued job, no drain race
    release_session_lease(session, session_id=s2.id, task_id=task2.id, close_reason="done")
    session.commit()
    job = q._next_job()
    assert job is not None and job.spec.session_id == s2.id
    # thread_id is the AgentThread id (workflow.thread_id), NOT the workflow id
    assert job.spec.thread_id == thread.id and job.spec.thread_id != wf2.id
    reset_memory_runtime()


def test_correction_enqueued_via_real_middleware_and_config(session, agent_thread_factory, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from langchain_core.messages import HumanMessage, AIMessage
    from app.models import AgentMessage
    from app.services.deep_agent import orchestrator
    from app.services.deep_agent.memory.middleware import MemoryMiddleware
    from app.services.deep_agent.memory.runtime import (
        memory_configurable, latest_user_message_id, get_memory_queue, reset_memory_runtime,
    )
    reset_memory_runtime()
    thread = agent_thread_factory()
    msg = AgentMessage(thread_id=thread.id, role="user", content="No, actually that's wrong")
    session.add(msg); session.commit()

    # REAL assembled middleware chain (not get_memory_middleware() directly)
    mws = orchestrator._agent_middleware(False, model=None, backend=None, tools=[])
    mem = next(m for m in mws if isinstance(m, MemoryMiddleware))
    q = get_memory_queue()
    q._ensure_writer = lambda: None   # deterministic: inspect the queued job, no drain

    # REAL configurable built by the production helper (not a hand-built dict)
    mid = latest_user_message_id(session, thread.id)
    config = {"configurable": memory_configurable(
        session_id=99, thread_id=thread.id, persona="trader", message_id=mid)}
    state = {"messages": [AIMessage(content="I'll use ACT/365"),
                          HumanMessage(content="No, actually that's wrong", id="lc-id")]}
    mem.after_model(state, None, config)

    job = q._next_job()
    assert job is not None and job.priority == "high" and job.spec.kind == "correction"
    assert isinstance(mid, int) and job.spec.trigger_message_id == mid
    assert job.spec.run_key == f"corr:99:{mid}"
    reset_memory_runtime()

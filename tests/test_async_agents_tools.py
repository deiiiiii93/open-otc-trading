"""Tool-surface tests for the three orchestrator tools."""
from __future__ import annotations

import json

import pytest

from app.models import AuditEvent, TaskRun, TaskStatus


@pytest.fixture
def tool_config_for_thread(agent_thread_factory, session):
    def make(**overrides):
        thread = agent_thread_factory()
        session.commit()  # release SQLite transaction so tool's session can write
        return {
            "configurable": {"thread_id": str(thread.id)},
            "_thread": thread,
            **overrides,
        }

    return make


@pytest.mark.asyncio
async def test_start_async_agent_arun_preserves_config_injection(
    session, tool_config_for_thread, monkeypatch
):
    """LangChain's async path (agent.ainvoke) introspects _arun for a
    RunnableConfig parameter to inject thread_id. A generic *args/**kwargs
    override breaks that injection; the typed override must keep it working."""
    from app.services.async_agents import runner, tools

    cfg = tool_config_for_thread()
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task",
        lambda *a, **k: None,
        raising=False,
    )
    monkeypatch.setattr(runner, "_run", lambda *a, **k: None, raising=False)
    tool = tools.StartAsyncAgentTool()
    result = await tool.ainvoke(
        {"description": "test", "prompt": "do a thing", "inputs": {"a": 1}},
        config=cfg,
    )
    # If config wasn't injected, parent_thread_id would be None and we'd get
    # an "ok": False with error="no_parent_thread".
    assert result["ok"] is True
    assert isinstance(result["task_id"], int)


def test_start_async_agent_returns_task_id(session, tool_config_for_thread, monkeypatch):
    from app.services.async_agents import runner, tools

    cfg = tool_config_for_thread()
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task",
        lambda *a, **k: None,
        raising=False,
    )
    monkeypatch.setattr(runner, "_run", lambda *a, **k: None, raising=False)
    tool = tools.StartAsyncAgentTool()
    result = tool.invoke(
        {"description": "test", "prompt": "do a thing", "inputs": {"a": 1}},
        config=cfg,
    )
    assert result["ok"] is True
    assert isinstance(result["task_id"], int)


def test_start_async_agent_persists_task_row_and_audit_event(
    session, tool_config_for_thread, monkeypatch
):
    from app.services.async_agents import runner, tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task",
        lambda *a, **k: None,
        raising=False,
    )
    monkeypatch.setattr(runner, "_run", lambda *a, **k: None, raising=False)

    tool = tools.StartAsyncAgentTool()
    result = tool.invoke(
        {
            "description": "risk narrative",
            "prompt": "Write a risk summary in the background.",
            "inputs": {"portfolio_id": 5},
        },
        config=cfg,
    )

    assert result["ok"] is True
    task_id = result["task_id"]
    session.expire_all()
    row = session.get(TaskRun, task_id)
    assert row is not None
    assert row.kind == "async_agent"
    assert row.status == TaskStatus.QUEUED.value
    assert row.parent_thread_id == thread.id
    assert row.description == "risk narrative"
    message = json.loads(row.message)
    assert message["inputs"] == {"portfolio_id": 5}
    assert message["prompt"] == "Write a risk summary in the background."

    audit = (
        session.query(AuditEvent)
        .filter(AuditEvent.event_type == "async_agent.started")
        .one()
    )
    assert audit.subject_type == "thread"
    assert str(audit.subject_id) == str(thread.id)
    assert audit.payload["task_id"] == task_id
    assert audit.payload["description"] == "risk narrative"
    assert "proxy_fired" in audit.payload


def test_start_async_agent_rejects_at_concurrency_cap(
    session, tool_config_for_thread, monkeypatch
):
    from app.services.async_agents import policy, tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    for _ in range(policy.MAX_CONCURRENT_PER_THREAD):
        session.add(
            TaskRun(
                kind="async_agent",
                status=TaskStatus.RUNNING.value,
                parent_thread_id=thread.id,
                description="prior",
            )
        )
    session.commit()
    tool = tools.StartAsyncAgentTool()
    result = tool.invoke({"description": "overflow", "prompt": "..."}, config=cfg)
    assert result["ok"] is False
    assert result["error"] == "too_many_running"


def test_list_async_agents_excludes_terminal_by_default(
    session, tool_config_for_thread
):
    from app.services.async_agents import tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    session.add(
        TaskRun(
            kind="async_agent",
            status=TaskStatus.RUNNING.value,
            parent_thread_id=thread.id,
            description="active",
        )
    )
    session.add(
        TaskRun(
            kind="async_agent",
            status=TaskStatus.COMPLETED.value,
            parent_thread_id=thread.id,
            description="done",
        )
    )
    session.commit()
    tool = tools.ListAsyncAgentsTool()
    result = tool.invoke({}, config=cfg)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["description"] == "active"


def test_cancel_async_agent_flags_running(session, tool_config_for_thread, monkeypatch):
    from app.services.async_agents import tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="cancel me",
    )
    session.add(row)
    session.commit()
    calls = []
    monkeypatch.setattr(
        tools,
        "request_async_task_drain",
        lambda task_id, reason="cancelled": calls.append((task_id, reason)) or True,
    )
    tool = tools.CancelAsyncAgentTool()
    result = tool.invoke({"task_id": row.id}, config=cfg)
    assert result["ok"] is True
    assert result["previous_status"] == TaskStatus.RUNNING.value
    assert result["new_status"] == TaskStatus.RUNNING.value
    assert result["drain_requested"] is True
    assert calls == [(row.id, "cancelled")]
    session.refresh(row)
    assert row.cancel_requested is True


def test_cancel_async_agent_terminalizes_hitl_paused_task(
    session, tool_config_for_thread
):
    """When a RUNNING task is paused at HITL (message='awaiting approval')
    there is no worker to observe cancel_requested. Cancel must terminalize
    the row directly so it stops counting against the concurrency cap."""
    from app.services.async_agents import tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="paused at hitl",
        message="awaiting approval",
    )
    session.add(row)
    session.commit()
    tool = tools.CancelAsyncAgentTool()
    result = tool.invoke({"task_id": row.id}, config=cfg)
    assert result["ok"] is True
    assert result["new_status"] == TaskStatus.FAILED.value
    session.refresh(row)
    assert row.status == TaskStatus.FAILED.value
    assert row.cancel_requested is True
    assert row.finished_at is not None
    assert "awaiting" in (row.message or "")


def test_cancel_async_agent_queued_also_sets_cancel_requested(
    session, tool_config_for_thread
):
    """Cancelling a QUEUED task must also set cancel_requested=True so a
    worker already scheduled in the thread pool doesn't resurrect the row."""
    from app.services.async_agents import tools

    cfg = tool_config_for_thread()
    thread = cfg["_thread"]
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.QUEUED.value,
        parent_thread_id=thread.id,
        description="cancel before start",
    )
    session.add(row)
    session.commit()
    tool = tools.CancelAsyncAgentTool()
    result = tool.invoke({"task_id": row.id}, config=cfg)
    assert result["ok"] is True
    assert result["previous_status"] == TaskStatus.QUEUED.value
    assert result["new_status"] == TaskStatus.FAILED.value
    session.refresh(row)
    assert row.status == TaskStatus.FAILED.value
    assert row.cancel_requested is True


def test_cancel_async_agent_refuses_other_threads_task(
    session, tool_config_for_thread, agent_thread_factory
):
    from app.services.async_agents import tools

    other_thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=other_thread.id,
        description="not yours",
    )
    session.add(row)
    session.commit()

    cfg = tool_config_for_thread()
    tool = tools.CancelAsyncAgentTool()
    result = tool.invoke({"task_id": row.id}, config=cfg)
    assert result["ok"] is False
    assert result["error"] == "not_owned"


def test_quant_agent_tools_includes_three_async_tools():
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "start_async_agent" in names
    assert "list_async_agents" in names
    assert "cancel_async_agent" in names


def test_deep_agent_tool_names_includes_three_async_tools():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert "start_async_agent" in DEEP_AGENT_TOOL_NAMES
    assert "list_async_agents" in DEEP_AGENT_TOOL_NAMES
    assert "cancel_async_agent" in DEEP_AGENT_TOOL_NAMES


def test_async_tools_not_in_interrupt_names():
    """Dispatching is free; only subagent's tool calls bubble."""
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES

    assert "start_async_agent" not in INTERRUPT_TOOL_NAMES
    assert "list_async_agents" not in INTERRUPT_TOOL_NAMES
    assert "cancel_async_agent" not in INTERRUPT_TOOL_NAMES

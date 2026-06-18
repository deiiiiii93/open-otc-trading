"""End-to-end integration: orchestrator dispatches → runner runs → autopost lands."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from app.models import AgentMessage, TaskRun, TaskStatus


def test_runner_runs_stubbed_agent_then_autoposts(
    session, agent_thread_factory, monkeypatch
):
    """Full _run path with a stubbed agent + scripted final AI message."""
    from langchain_core.messages import AIMessage

    from app.services.async_agents import runner as runner_mod

    thread = agent_thread_factory()
    task = TaskRun(
        kind="async_agent",
        status=TaskStatus.QUEUED.value,
        parent_thread_id=thread.id,
        description="integration test",
        message=json.dumps({"prompt": "do it", "inputs": {"x": 1}}),
    )
    session.add(task)
    session.commit()
    task_id = task.id
    captured: dict = {}

    class StubAgent:
        async def ainvoke(self, payload, config=None):
            captured["config"] = config
            return None

        async def aget_state(self, config):
            class S:
                tasks: list = []
                values: dict = {
                    "messages": [AIMessage(content="Headline.\n\n- finding")],
                    "files": {},
                }

            return S()

    @asynccontextmanager
    async def fake_checkpointer(*a, **k):
        yield object()

    # The runner imports these lazily inside _run_async, so patch at source.
    monkeypatch.setattr(
        "app.services.deep_agent.checkpointer.build_async_checkpointer",
        fake_checkpointer,
    )
    monkeypatch.setattr(
        "app.services.async_agents.agent.build_async_agent",
        lambda **kw: StubAgent(),
    )
    monkeypatch.setattr(
        "app.services.deep_agent.model_factory.build_agent_model",
        lambda *a, **k: object(),
    )

    asyncio.run(runner_mod._run_async(task_id))
    from app.config import get_settings

    assert (
        captured["config"]["recursion_limit"]
        == get_settings().agent_recursion_limit
    )

    session.expire_all()
    refreshed = session.get(TaskRun, task_id)
    assert refreshed.status == TaskStatus.COMPLETED.value

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert any(
        m.character == "async_agent" and (m.meta or {}).get("agent_phase") == "completed"
        for m in msgs
    )

"""HITL bubble-up endpoint routing + integration tests."""
from __future__ import annotations

from datetime import datetime

from app.models import AgentMessage, TaskRun, TaskStatus


def test_resume_endpoint_routes_to_async_when_action_has_task_id(
    client, session, agent_thread_factory, monkeypatch
):
    """Confirm endpoint detects async_task_id on pending action and routes."""
    from app.services import agents as agents_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="route test",
    )
    session.add(row)
    session.flush()  # populate row.id before composing meta
    msg = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        character="async_agent",
        content="approve please",
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "awaiting_confirmation",
            "async_task_id": row.id,
            "pending_actions": [
                {
                    "id": "intr-x:0",
                    "tool_name": "create_report",
                    "label": "Create report",
                    "summary": "",
                    "payload": {},
                    "requires_confirmation": True,
                    "status": "pending",
                    "async_task_id": row.id,
                }
            ],
        },
    )
    session.add(msg)
    session.commit()

    called: dict = {}

    def fake_resume(self, task_id, decision, message, audit_ref=None):
        called["task_id"] = task_id
        called["decision"] = decision
        called["message"] = message
        return None

    monkeypatch.setattr(
        agents_mod.AgentService,
        "resume_async_agent",
        fake_resume,
        raising=False,
    )

    resp = client.post(
        f"/api/chat/threads/{thread.id}/messages/{msg.id}/actions/intr-x:0/confirm"
    )
    assert resp.status_code == 200, resp.text
    assert called.get("task_id") == row.id
    assert called.get("decision") == "approve"


def test_list_async_agents_endpoint_returns_active_tasks(
    client, session, agent_thread_factory
):
    thread = agent_thread_factory()
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

    resp = client.get(f"/api/chat/threads/{thread.id}/async_agents")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["description"] == "active"

    resp_all = client.get(
        f"/api/chat/threads/{thread.id}/async_agents?include_terminal=true"
    )
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 2


def test_multiple_bubble_ups_in_one_task_each_route_correctly(
    session, agent_thread_factory
):
    """Two consecutive bubble-ups produce two awaiting messages, each tagged."""
    from app.services.async_agents import bubble_up
    from langgraph.types import Interrupt

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="multi-bubble",
    )
    session.add(row)
    session.flush()

    def mk_interrupt(name: str, idx: int) -> Interrupt:
        return Interrupt(
            value={
                "action_requests": [
                    {"name": name, "args": {}, "description": f"{name} call"}
                ]
            },
            id=f"intr-{idx}",
        )

    bubble_up.handle(
        session, task_id=row.id, interrupts=[mk_interrupt("create_report", 1)]
    )
    bubble_up.handle(
        session, task_id=row.id, interrupts=[mk_interrupt("run_batch_pricing", 2)]
    )
    session.commit()

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert len(msgs) == 2
    for msg in msgs:
        assert msg.meta["async_task_id"] == row.id
        assert msg.meta["agent_phase"] == "awaiting_confirmation"
    assert msgs[0].meta["pending_actions"][0]["tool_name"] == "create_report"
    assert msgs[1].meta["pending_actions"][0]["tool_name"] == "run_batch_pricing"


def test_bubble_up_bumps_parent_thread_updated_at(session, agent_thread_factory):
    from app.services.async_agents import bubble_up
    from langgraph.types import Interrupt

    thread = agent_thread_factory()
    thread.updated_at = datetime(2020, 1, 1)
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="approval",
    )
    session.add(row)
    session.commit()
    original_updated_at = thread.updated_at

    bubble_up.handle(
        session,
        task_id=row.id,
        interrupts=[
            Interrupt(
                value={
                    "action_requests": [
                        {
                            "name": "create_report",
                            "args": {},
                            "description": "create report",
                        }
                    ]
                },
                id="intr-refresh",
            )
        ],
    )
    session.commit()
    session.refresh(thread)

    assert thread.updated_at > original_updated_at


def test_startup_recovery_preserves_async_task_awaiting_approval(
    session, agent_thread_factory
):
    from app.services.task_runner import mark_stale_tasks_failed

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="paused",
        message="awaiting approval",
    )
    session.add(row)
    session.commit()

    stale_count = mark_stale_tasks_failed(session)
    session.commit()
    session.refresh(row)

    assert stale_count == 0
    assert row.status == TaskStatus.RUNNING.value
    assert row.message == "awaiting approval"
    assert row.error is None


def test_resume_async_agent_routes_command_to_subagent_thread_id(
    session, agent_thread_factory, monkeypatch
):
    """The LangGraph config uses 'async:<parent>:<task>' as thread_id on resume."""
    import asyncio

    from app.services.async_agents import resume as resume_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="route command",
    )
    session.add(row)
    session.commit()

    captured: dict = {}

    class StubAgent:
        async def ainvoke(self, payload, config=None):
            captured["thread_id"] = (config or {}).get("configurable", {}).get(
                "thread_id"
            )
            captured["recursion_limit"] = (config or {}).get("recursion_limit")
            return None

        async def aget_state(self, config):
            class S:
                tasks: list = []
                values: dict = {}

            return S()

    monkeypatch.setattr(
        resume_mod,
        "_build_agent_for_resume",
        lambda **kw: StubAgent(),
        raising=False,
    )
    # Stub the model + checkpointer so nothing real is loaded
    monkeypatch.setattr(
        "app.services.deep_agent.model_factory.build_agent_model",
        lambda *a, **k: object(),
    )
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_cp(*a, **k):
        yield object()

    monkeypatch.setattr(
        "app.services.deep_agent.checkpointer.build_async_checkpointer",
        fake_cp,
    )

    asyncio.run(resume_mod._resume_run_async(row.id, "approve", None))
    from app.config import get_settings

    assert captured["thread_id"] == f"async:{thread.id}:{row.id}"
    assert captured["recursion_limit"] == get_settings().agent_recursion_limit

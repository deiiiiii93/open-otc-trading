from __future__ import annotations

import pytest

from app.models import AgentTask, AgentThread, DomainEvent, SessionArtifact, Workflow
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state


class _FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def invoke(self, payload, *, config):
        self.calls.append({"payload": payload, "config": config})
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _workflow(session) -> Workflow:
    thread = AgentThread(title="executor", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    workflow = session.get(Workflow, state.domain_workflow_id)
    assert workflow is not None
    return workflow


def _ready_task(session, workflow: Workflow) -> AgentTask:
    task = AgentTask(
        workflow_id=workflow.id,
        task_type="fetch_position_summaries",
        inputs={"portfolio_id": 1, "fields": "summary"},
        depends_on=[],
        assigned_persona="trader",
        status="ready",
    )
    session.add(task)
    session.flush()
    return task


def test_task_executor_runs_task_scoped_deepagent_and_writes_ledger(
    session, settings
):
    from app.services.deep_agent.executor import TaskExecutor

    workflow = _workflow(session)
    task = _ready_task(session, workflow)
    agent = _FakeAgent({"rows": [{"id": 1}], "message": "ok"})

    artifact = TaskExecutor(settings=settings).invoke_deep_agent(
        session,
        task_id=task.id,
        agent=agent,
    )
    session.flush()

    refreshed = session.get(AgentTask, task.id)
    assert refreshed.status == "completed"
    assert refreshed.context_pack_id is not None
    assert refreshed.assigned_session_id is not None
    assert refreshed.output_artifact_id == artifact.id
    assert artifact.kind == "deterministic_query"
    assert artifact.context_pack_id == refreshed.context_pack_id

    call = agent.calls[0]
    assert (
        call["config"]["configurable"]["thread_id"]
        == f"workflow:{workflow.id}:persona:trader:episode:1:task:{task.id}"
    )
    assert call["config"]["configurable"]["workflow_id"] == workflow.id
    assert call["config"]["configurable"]["session_id"] == refreshed.assigned_session_id
    assert call["config"]["configurable"]["task_id"] == task.id
    assert call["config"]["configurable"]["context_pack_id"] == refreshed.context_pack_id
    assert call["config"]["configurable"]["tools_scope"] == ["get_positions"]
    prompt = call["payload"]["messages"][0].content
    assert '"portfolio_id": 1' in prompt
    assert "Context pack payload" in prompt
    assert "Never expose task ids" in prompt

    events = [
        event.kind
        for event in session.query(DomainEvent)
        .filter(DomainEvent.task_id == task.id)
        .order_by(DomainEvent.id)
        .all()
    ]
    assert "task_started" in events
    assert "artifact_created" in events
    assert "task_completed" in events


def test_task_executor_pauses_on_hitl_interrupt_and_releases_lease(session, settings):
    from app.models import AgentSession
    from app.services.deep_agent.executor import TaskExecutor

    workflow = _workflow(session)
    task = _ready_task(session, workflow)
    agent = _FakeAgent({"__interrupt__": [{"id": "intr-1"}]})

    result = TaskExecutor(settings=settings).invoke_deep_agent(
        session,
        task_id=task.id,
        agent=agent,
    )
    session.flush()

    refreshed = session.get(AgentTask, task.id)
    agent_session = session.get(AgentSession, refreshed.assigned_session_id)
    assert result is None
    assert refreshed.status == "awaiting_hitl"
    assert refreshed.output_artifact_id is None
    assert agent_session.status == "active"
    assert agent_session.current_task_id is None
    assert (
        session.query(DomainEvent)
        .filter_by(task_id=task.id, kind="hitl_requested")
        .count()
        == 1
    )


def test_task_executor_schedules_children_from_plan_artifact(session, settings):
    from app.services.deep_agent.executor import TaskExecutor

    workflow = _workflow(session)
    planner = AgentTask(
        workflow_id=workflow.id,
        task_type="plan_workflow_step",
        inputs={"user_message": "summarize positions"},
        depends_on=[],
        assigned_persona="orchestrator",
        status="ready",
    )
    session.add(planner)
    session.flush()
    agent = _FakeAgent(
        {
            "artifact": {
                "tasks": [
                    {
                        "task_type": "fetch_position_summaries",
                        "inputs": {"portfolio_id": 7, "fields": "summary"},
                        "assigned_persona": "trader",
                    }
                ]
            }
        }
    )

    artifact = TaskExecutor(settings=settings).invoke_deep_agent(
        session,
        task_id=planner.id,
        agent=agent,
    )
    session.flush()

    children = (
        session.query(AgentTask)
        .filter(AgentTask.workflow_id == workflow.id, AgentTask.id != planner.id)
        .all()
    )
    assert artifact.kind == "plan"
    assert len(children) == 1
    assert children[0].task_type == "fetch_position_summaries"
    assert children[0].status == "ready"
    assert children[0].inputs == {"portfolio_id": 7, "fields": "summary"}


def test_task_executor_allows_plan_step_to_clarify_without_scheduling(
    session, settings
):
    from app.services.deep_agent.executor import TaskExecutor

    workflow = _workflow(session)
    planner = AgentTask(
        workflow_id=workflow.id,
        task_type="plan_workflow_step",
        inputs={"user_message": "list positions near KO"},
        depends_on=[],
        assigned_persona="orchestrator",
        status="ready",
    )
    session.add(planner)
    session.flush()
    agent = _FakeAgent(
        {
            "messages": [
                type(
                    "Message",
                    (),
                    {
                        "content": "Which portfolio should I scan?",
                    },
                )()
            ]
        }
    )

    artifact = TaskExecutor(settings=settings).invoke_deep_agent(
        session,
        task_id=planner.id,
        agent=agent,
    )
    session.flush()

    children = (
        session.query(AgentTask)
        .filter(AgentTask.workflow_id == workflow.id, AgentTask.id != planner.id)
        .all()
    )
    assert artifact.kind == "claim"
    assert artifact.payload["content"] == "Which portfolio should I scan?"
    assert children == []


def test_task_executor_marks_task_failed_and_releases_lease(session, settings):
    from app.models import AgentSession
    from app.services.deep_agent.executor import TaskExecutor

    workflow = _workflow(session)
    task = _ready_task(session, workflow)

    with pytest.raises(RuntimeError, match="LLM failed"):
        TaskExecutor(settings=settings).invoke_deep_agent(
            session,
            task_id=task.id,
            agent=_FakeAgent(RuntimeError("LLM failed")),
        )
    session.flush()

    refreshed = session.get(AgentTask, task.id)
    agent_session = session.get(AgentSession, refreshed.assigned_session_id)
    assert refreshed.status == "failed"
    assert "LLM failed" in refreshed.error
    assert refreshed.closed_at is not None
    assert agent_session.current_task_id is None
    assert (
        session.query(DomainEvent)
        .filter_by(task_id=task.id, kind="task_failed")
        .count()
        == 1
    )
    assert session.query(SessionArtifact).filter_by(task_id=task.id).count() == 0

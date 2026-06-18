from __future__ import annotations

import pytest

from app.models import AgentTask, AgentThread, DomainEvent, Workflow
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state


def _workflow(session) -> Workflow:
    thread = AgentThread(title="scheduler", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    workflow = session.get(Workflow, state.domain_workflow_id)
    assert workflow is not None
    return workflow


def test_plan_workflow_step_task_registration_emits_plan_artifact():
    from app.services.deep_agent.task_registry import TASK_REGISTRY

    registration = TASK_REGISTRY["plan_workflow_step"]

    assert registration.assigned_personas == ("orchestrator",)
    assert registration.output_artifact_kind == "plan"
    assert registration.tools_scope == ("propose_reply_options",)


def test_propose_run_batch_pricing_accepts_both_personas_and_tool_shaped_inputs():
    """The unified batch-pricing task mirrors the run_batch_pricing tool:
    trader (price-portfolio) and risk_manager (run-risk) both queue it, and
    inputs use pricing_parameter_profile_id (optional), not legacy
    profile_id/valuation_date."""
    from app.services.deep_agent.task_registry import TaskSpec, validate_task_spec

    for persona in ("trader", "risk_manager"):
        spec = validate_task_spec(
            TaskSpec(
                task_type="propose_run_batch_pricing",
                assigned_persona=persona,
                inputs={
                    "portfolio_id": 6,
                    "position_ids": [1, 2],
                    "pricing_parameter_profile_id": 3,
                },
            )
        )
        assert spec.inputs["pricing_parameter_profile_id"] == 3

    # Profile is optional (explicit user-confirmed run without one).
    validate_task_spec(
        TaskSpec(
            task_type="propose_run_batch_pricing",
            assigned_persona="risk_manager",
            inputs={"portfolio_id": 6},
        )
    )

    # Market overrides stay forbidden at the task layer too.
    with pytest.raises(Exception):
        validate_task_spec(
            TaskSpec(
                task_type="propose_run_batch_pricing",
                assigned_persona="trader",
                inputs={"portfolio_id": 6, "spot": 101.0},
            )
        )


def test_renamed_task_types_resolve_via_alias():
    """Persisted task rows created before the run_risk -> run_batch_pricing
    rename must still resolve on HITL resume/executor paths."""
    from app.services.deep_agent.task_registry import task_registration

    registration = task_registration("propose_run_risk")
    assert registration.task_type == "propose_run_batch_pricing"
    assert registration.tools_scope == ("run_batch_pricing", "convert_currency")

    with pytest.raises(ValueError, match="Unknown task type"):
        task_registration("never_existed_task")


def test_legacy_propose_run_risk_spec_is_canonicalized_end_to_end():
    """A persisted pre-rename task row (old task_type + legacy profile_id /
    valuation_date inputs) validates into the canonical spec, so the
    TOOL_SCOPES_BY_TASK_TYPE lookup in context assembly cannot KeyError."""
    from app.services.deep_agent.task_registry import (
        TOOL_SCOPES_BY_TASK_TYPE,
        TaskSpec,
        validate_task_spec,
    )

    spec = validate_task_spec(
        TaskSpec(
            task_type="propose_run_risk",
            assigned_persona="risk_manager",
            inputs={
                "portfolio_id": 6,
                "position_ids": [1, 2],
                "profile_id": 3,
                "valuation_date": "2026-04-30",
            },
        )
    )

    assert spec.task_type == "propose_run_batch_pricing"
    assert spec.inputs["pricing_parameter_profile_id"] == 3
    assert "profile_id" not in spec.inputs
    assert "valuation_date" not in spec.inputs
    # Context assembly indexes the scope map with the validated task_type.
    assert TOOL_SCOPES_BY_TASK_TYPE[spec.task_type] == (
        "run_batch_pricing",
        "convert_currency",
    )


def test_scheduler_validates_plan_artifact_and_inserts_ready_tasks(session):
    from app.services.deep_agent.ledger import LedgerWriter
    from app.services.deep_agent.scheduler import schedule_tasks_from_plan

    workflow = _workflow(session)
    planner = AgentTask(
        workflow_id=workflow.id,
        task_type="plan_workflow_step",
        inputs={"user_message": "summarize positions"},
        depends_on=[],
        assigned_persona="orchestrator",
        status="completed",
    )
    session.add(planner)
    session.flush()
    artifact = LedgerWriter(session).write_artifact(
        workflow_id=workflow.id,
        task_id=planner.id,
        kind="plan",
        title="Planner output",
        payload={
            "tasks": [
                {
                    "task_type": "fetch_position_summaries",
                    "inputs": {"portfolio_id": 7, "fields": "summary"},
                    "assigned_persona": "trader",
                },
                {
                    "task_type": "run_analytic_script",
                    "inputs": {
                        "code": "result = {'ok': True}",
                        "payload": {"portfolio_id": 7},
                        "writes_artifacts": False,
                    },
                    "depends_on": [],
                    "assigned_persona": "risk_manager",
                },
            ]
        },
    )
    planner.output_artifact_id = artifact.id
    session.flush()

    scheduled = schedule_tasks_from_plan(
        session,
        planner_task_id=planner.id,
        plan_artifact_id=artifact.id,
    )
    session.flush()

    assert [task.task_type for task in scheduled] == [
        "fetch_position_summaries",
        "run_analytic_script",
    ]
    assert all(task.workflow_id == workflow.id for task in scheduled)
    assert all(task.status == "ready" for task in scheduled)
    assert scheduled[0].inputs == {"portfolio_id": 7, "fields": "summary"}
    assert scheduled[0].assigned_persona == "trader"

    events = (
        session.query(DomainEvent)
        .filter(DomainEvent.kind == "task_planned")
        .order_by(DomainEvent.id)
        .all()
    )
    assert [event.task_id for event in events] == [task.id for task in scheduled]
    assert events[0].payload["planner_task_id"] == planner.id
    assert events[0].payload["plan_artifact_id"] == artifact.id


def test_scheduler_rejects_invalid_plan_without_partial_inserts(session):
    from app.services.deep_agent.ledger import LedgerWriter
    from app.services.deep_agent.scheduler import schedule_tasks_from_plan

    workflow = _workflow(session)
    planner = AgentTask(
        workflow_id=workflow.id,
        task_type="plan_workflow_step",
        inputs={"user_message": "bad plan"},
        depends_on=[],
        assigned_persona="orchestrator",
        status="completed",
    )
    session.add(planner)
    session.flush()
    artifact = LedgerWriter(session).write_artifact(
        workflow_id=workflow.id,
        task_id=planner.id,
        kind="plan",
        title="Bad planner output",
        payload={
            "tasks": [
                {
                    "task_type": "run_analytic_script",
                    "inputs": {"code": "result = 1", "payload": {}},
                    "assigned_persona": "trader",
                }
            ]
        },
    )
    planner.output_artifact_id = artifact.id
    session.flush()

    with pytest.raises(ValueError, match="Invalid plan task"):
        schedule_tasks_from_plan(
            session,
            planner_task_id=planner.id,
            plan_artifact_id=artifact.id,
        )

    assert (
        session.query(AgentTask)
        .filter(
            AgentTask.workflow_id == workflow.id,
            AgentTask.id != planner.id,
        )
        .count()
        == 0
    )

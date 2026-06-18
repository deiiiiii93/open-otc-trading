"""Task scheduler helpers for plan artifacts."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from ...models import AgentTask, SessionArtifact
from .ledger import LedgerWriter
from .task_registry import TaskSpec, validate_task_spec


def schedule_tasks_from_plan(
    session: Session,
    *,
    planner_task_id: int,
    plan_artifact_id: int,
) -> list[AgentTask]:
    planner = session.get(AgentTask, planner_task_id)
    if planner is None:
        raise ValueError(f"Planner task {planner_task_id} not found")
    artifact = session.get(SessionArtifact, plan_artifact_id)
    if artifact is None:
        raise ValueError(f"Plan artifact {plan_artifact_id} not found")
    if artifact.workflow_id != planner.workflow_id:
        raise ValueError("Plan artifact belongs to a different workflow")
    if artifact.kind != "plan":
        raise ValueError(f"Artifact {plan_artifact_id} is {artifact.kind}, not plan")

    specs = _validated_plan_specs(artifact.payload or {})
    tasks = [
        AgentTask(
            workflow_id=planner.workflow_id,
            task_type=spec.task_type,
            inputs=spec.inputs,
            depends_on=spec.depends_on,
            assigned_persona=spec.assigned_persona,
            status=_initial_status_for(session, workflow_id=planner.workflow_id, depends_on=spec.depends_on),
        )
        for spec in specs
    ]
    session.add_all(tasks)
    session.flush()

    writer = LedgerWriter(session)
    for task in tasks:
        writer.emit_event(
            workflow_id=task.workflow_id,
            task_id=task.id,
            kind="task_planned",
            payload={
                "task_id": task.id,
                "task_type": task.task_type,
                "planner_task_id": planner.id,
                "plan_artifact_id": artifact.id,
                "status": task.status,
            },
        )
    return tasks


def _validated_plan_specs(payload: dict[str, Any]) -> list[TaskSpec]:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("Plan artifact payload must contain tasks: list")
    specs: list[TaskSpec] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid plan task at index {index}: expected object")
        try:
            spec = validate_task_spec(
                TaskSpec(
                    task_type=str(raw.get("task_type") or ""),
                    inputs=dict(raw.get("inputs") or {}),
                    depends_on=[int(dep) for dep in (raw.get("depends_on") or [])],
                    assigned_persona=str(raw.get("assigned_persona") or ""),
                )
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid plan task at index {index}: {exc}") from exc
        specs.append(spec)
    return specs


def _initial_status_for(
    session: Session,
    *,
    workflow_id: int,
    depends_on: list[int],
) -> str:
    if not depends_on:
        return "ready"
    completed = (
        session.query(AgentTask.id)
        .filter(
            AgentTask.workflow_id == workflow_id,
            AgentTask.id.in_(depends_on),
            AgentTask.status == "completed",
        )
        .count()
    )
    return "ready" if completed == len(depends_on) else "planned"

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import AgentTask, AgentThread, SessionArtifact, Workflow
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state


def _thread_with_workflow(session) -> tuple[AgentThread, Workflow]:
    thread = AgentThread(title="wf", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    workflow = session.get(Workflow, state.domain_workflow_id)
    assert workflow is not None
    return thread, workflow


def test_task_registry_validates_initial_task_specs():
    from app.services.deep_agent.task_registry import (
        TASK_REGISTRY,
        TaskSpec,
        validate_task_spec,
    )

    assert TASK_REGISTRY["run_analytic_script"].output_artifact_kind == "sandbox_output"
    assert TASK_REGISTRY["fetch_position_summaries"].freshness_window_seconds == 86400
    assert TASK_REGISTRY["query_positions_near_barrier"].tools_scope == (
        "query_positions_near_barrier",
    )
    assert TASK_REGISTRY["query_snowball_ko_from_spot"].tools_scope == (
        "query_snowball_ko_from_spot",
    )
    assert (
        TASK_REGISTRY["query_snowball_ko_from_spot"].output_artifact_kind
        == "deterministic_query"
    )
    assert TASK_REGISTRY["query_positions"].output_artifact_kind == "deterministic_query"
    assert TASK_REGISTRY["get_snowball_ko_schedule"].tools_scope == (
        "get_snowball_ko_schedule",
    )

    spec = TaskSpec(
        task_type="run_analytic_script",
        inputs={
            "code": "result = {'ok': True}",
            "payload": {"rows": []},
            "writes_artifacts": False,
        },
        assigned_persona="trader",
    )
    validated = validate_task_spec(spec)

    assert validated.assigned_persona == "trader"
    assert validated.task_type == "run_analytic_script"

    with pytest.raises(ValidationError):
        validate_task_spec(
            TaskSpec(
                task_type="run_analytic_script",
                inputs={"code": "result = 1", "payload": {}},
                assigned_persona="trader",
            )
        )

    with pytest.raises(ValueError, match="Unknown task type"):
        validate_task_spec(
            TaskSpec(task_type="unknown", inputs={}, assigned_persona="trader")
        )


def test_payload_registry_rejects_unknown_artifact_and_evidence_kinds():
    from app.services.deep_agent.payload_registry import (
        is_load_bearing_evidence,
        validate_artifact_payload,
        validate_evidence_payload,
    )

    payload = validate_artifact_payload(
        kind="deterministic_query",
        schema_version=1,
        payload={"rows": [{"id": 1}]},
    )
    evidence = validate_evidence_payload(
        evidence_kind="snapshot",
        evidence_payload={"position_state_at": "v1"},
    )

    assert payload["rows"] == [{"id": 1}]
    assert evidence["position_state_at"] == "v1"
    assert is_load_bearing_evidence("snapshot") is True
    assert is_load_bearing_evidence("agent_attestation") is False

    with pytest.raises(ValueError, match="Unknown artifact kind"):
        validate_artifact_payload(kind="made_up", schema_version=1, payload={})

    with pytest.raises(ValueError, match="Unknown evidence kind"):
        validate_evidence_payload(evidence_kind="rumor", evidence_payload={})


def test_context_assembler_dedups_payload_but_creates_pack_per_task(session):
    from app.models import ContextPack, ContextPackPayload
    from app.services.deep_agent.context_assembler import assemble_context_pack

    _, workflow = _thread_with_workflow(session)
    first = AgentTask(
        workflow_id=workflow.id,
        task_type="fetch_position_summaries",
        inputs={"portfolio_id": 1, "fields": "summary"},
        depends_on=[],
        assigned_persona="trader",
        status="ready",
    )
    second = AgentTask(
        workflow_id=workflow.id,
        task_type="fetch_position_summaries",
        inputs={"fields": "summary", "portfolio_id": 1},
        depends_on=[],
        assigned_persona="trader",
        status="ready",
    )
    session.add_all([first, second])
    session.flush()

    first_pack = assemble_context_pack(session, task=first, recent_summary="same")
    second_pack = assemble_context_pack(session, task=second, recent_summary="same")
    session.flush()

    assert first_pack.id != second_pack.id
    assert first_pack.payload_id == second_pack.payload_id
    assert first.context_pack_id == first_pack.id
    assert second.context_pack_id == second_pack.id
    assert session.query(ContextPackPayload).count() == 2
    assert session.query(ContextPack).filter(ContextPack.task_id.isnot(None)).count() == 2


def test_context_assembler_cites_dependency_outputs_and_pinned_artifacts(session):
    from app.models import ContextPackPayload
    from app.services.deep_agent.context_assembler import assemble_context_pack

    _, workflow = _thread_with_workflow(session)
    dependency = AgentTask(
        workflow_id=workflow.id,
        task_type="fetch_position_summaries",
        inputs={"portfolio_id": 1, "fields": "summary"},
        depends_on=[],
        assigned_persona="trader",
        status="completed",
    )
    session.add(dependency)
    session.flush()
    dep_artifact = SessionArtifact(
        workflow_id=workflow.id,
        task_id=dependency.id,
        kind="deterministic_query",
        title="dependency",
        payload={"rows": []},
    )
    pinned_artifact = SessionArtifact(
        workflow_id=workflow.id,
        kind="finding",
        title="pinned",
        payload={"markdown": "keep"},
        pinned=True,
    )
    session.add_all([dep_artifact, pinned_artifact])
    session.flush()
    dependency.output_artifact_id = dep_artifact.id
    task = AgentTask(
        workflow_id=workflow.id,
        task_type="compute_barrier_proximity",
        inputs={"portfolio_id": 1, "spot": {"ABC": 100.0}, "within_pct": 0.05},
        depends_on=[dependency.id],
        assigned_persona="risk_manager",
        status="ready",
    )
    session.add(task)
    session.flush()

    pack = assemble_context_pack(session, task=task, recent_summary=None)
    payload = session.get(ContextPackPayload, pack.payload_id).stable_payload

    assert payload["cited_artifact_ids"] == sorted(
        [dep_artifact.id, pinned_artifact.id]
    )
    assert payload["tools_scope"] == ["get_positions"]
    assert payload["canonical_snapshot_ids"]["scope_kind"] == "ad_hoc"
    assert payload["canonical_snapshot_ids"]["captured_at"]


def test_ledger_writer_validates_and_writes_artifact_evidence_and_event(session):
    from app.models import ArtifactEvidenceRef, DomainEvent
    from app.services.deep_agent.context_assembler import assemble_context_pack
    from app.services.deep_agent.ledger import EvidenceRef, LedgerWriter

    _, workflow = _thread_with_workflow(session)
    task = AgentTask(
        workflow_id=workflow.id,
        task_type="run_analytic_script",
        inputs={"code": "result = {'total': 1}", "payload": {}, "writes_artifacts": False},
        depends_on=[],
        assigned_persona="trader",
        status="ready",
    )
    session.add(task)
    session.flush()
    pack = assemble_context_pack(session, task=task, recent_summary=None)

    writer = LedgerWriter(session)
    artifact = writer.write_artifact(
        workflow_id=workflow.id,
        session_id=None,
        task_id=task.id,
        context_pack_id=pack.id,
        kind="sandbox_output",
        title="script result",
        payload={"result": {"total": 1}, "artifacts": []},
        evidence_refs=[
            EvidenceRef(
                evidence_kind="agent_attestation",
                evidence_payload={"persona": "trader", "context_pack_id": pack.id},
            )
        ],
    )
    session.flush()

    refs = (
        session.query(ArtifactEvidenceRef)
        .filter(ArtifactEvidenceRef.artifact_id == artifact.id)
        .order_by(ArtifactEvidenceRef.evidence_kind)
        .all()
    )
    events = (
        session.query(DomainEvent)
        .filter(DomainEvent.artifact_id == artifact.id)
        .all()
    )
    task = session.get(AgentTask, task.id)

    assert artifact.schema_version == 1
    assert artifact.context_pack_id == pack.id
    assert task.output_artifact_id == artifact.id
    assert [ref.evidence_kind for ref in refs] == [
        "agent_attestation",
        "context_pack",
    ]
    assert len(events) == 1
    assert events[0].kind == "artifact_created"
    assert events[0].payload["artifact_id"] == artifact.id

    with pytest.raises(ValueError, match="Unknown artifact kind"):
        writer.write_artifact(
            workflow_id=workflow.id,
            kind="made_up",
            title="bad",
            payload={},
        )

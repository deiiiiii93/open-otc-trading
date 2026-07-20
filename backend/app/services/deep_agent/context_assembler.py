"""Context pack assembly for task-scoped DeepAgent runs."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...models import (
    AgentTask,
    AgentThread,
    ContextPack,
    ContextPackPayload,
    DomainEvent,
    SessionArtifact,
    Workflow,
)
from .payload_registry import validate_event_payload
from .task_registry import TOOL_SCOPES_BY_TASK_TYPE, TaskSpec, validate_task_spec
from .artifact_access import artifact_descriptor, effective_tools_scope

_ASSEMBLER_VERSION = "1.0"
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def canonical_jsonify(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): canonical_jsonify(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_jsonify(item) for item in value]
    if isinstance(value, set):
        return [canonical_jsonify(item) for item in sorted(value, key=str)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite float is not valid context-pack JSON")
        return int(value) if value.is_integer() else value
    return value


def _canonical_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def persona_prompt_revision_hash(persona: str) -> str:
    prompt_path = _PROMPTS_DIR / f"{persona}.md"
    body = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else persona
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def tool_signature_hash_for(tools: list[str]) -> str:
    # Initial registry contract: the whitelisted tool names are the stable
    # authority boundary. Later executor work can replace the value with a
    # schema hash without changing the context-pack row shape.
    signatures = {tool: {"name": tool, "schema": "registry-v1"} for tool in tools}
    return _canonical_hash(signatures)


def current_model_id() -> str:
    return "configured-default"


def curate_relevant_artifacts(
    session: Session,
    *,
    task: AgentTask,
    workflow: Workflow,
) -> list[int]:
    cited: set[int] = set()
    dependency_ids = [int(dep_id) for dep_id in (task.depends_on or [])]
    if dependency_ids:
        dependency_outputs = (
            session.query(AgentTask.output_artifact_id)
            .filter(
                AgentTask.workflow_id == workflow.id,
                AgentTask.id.in_(dependency_ids),
                AgentTask.output_artifact_id.isnot(None),
            )
            .all()
        )
        cited.update(row[0] for row in dependency_outputs if row[0] is not None)

    pinned_artifacts = (
        session.query(SessionArtifact.id)
        .filter(
            SessionArtifact.workflow_id == workflow.id,
            SessionArtifact.pinned.is_(True),
        )
        .all()
    )
    cited.update(row[0] for row in pinned_artifacts)
    return sorted(cited)


def _context_pack_payload(
    *,
    task_spec: TaskSpec,
    workflow: Workflow,
    artifact_ids: list[int],
    artifact_refs: list[dict[str, Any]] | None = None,
    recent_summary: str,
    report_currency: str = "by_position",
) -> tuple[str, dict[str, Any]]:
    tools = list(effective_tools_scope(TOOL_SCOPES_BY_TASK_TYPE[task_spec.task_type]))
    summary_hash = "sha256:" + hashlib.sha256(
        recent_summary.encode("utf-8")
    ).hexdigest()
    stable_payload = {
        "task_type": task_spec.task_type,
        "assigned_persona": task_spec.assigned_persona,
        "task_brief": canonical_jsonify(task_spec.inputs),
        "canonical_snapshot_ids": canonical_jsonify(
            workflow.canonical_snapshot_ids or {}
        ),
        "cited_artifact_ids": artifact_ids,
        "artifact_refs": canonical_jsonify(artifact_refs or []),
        "tools_scope": tools,
        "tool_signature_hash": tool_signature_hash_for(tools),
        "recent_session_summary_hash": summary_hash,
        "prompt_revision_hash": persona_prompt_revision_hash(
            task_spec.assigned_persona
        ),
        "model_id": current_model_id(),
        "report_currency": report_currency,
    }
    return _canonical_hash(stable_payload), stable_payload


def assemble_context_pack(
    session: Session,
    *,
    task: AgentTask,
    recent_summary: str | None,
) -> ContextPack:
    if task.id is None:
        session.flush()
    workflow = session.get(Workflow, task.workflow_id)
    if workflow is None:
        raise ValueError(f"Workflow {task.workflow_id} not found")

    task_spec = validate_task_spec(
        TaskSpec(
            task_type=task.task_type,
            inputs=task.inputs or {},
            depends_on=task.depends_on or [],
            assigned_persona=task.assigned_persona,
        )
    )
    artifact_ids = curate_relevant_artifacts(session, task=task, workflow=workflow)
    artifacts = (
        session.query(SessionArtifact)
        .filter(SessionArtifact.id.in_(artifact_ids))
        .order_by(SessionArtifact.id)
        .all()
        if artifact_ids
        else []
    )
    artifact_refs = [artifact_descriptor(artifact) for artifact in artifacts]
    summary_text = recent_summary or ""
    thread = session.get(AgentThread, workflow.thread_id)
    report_currency = (
        getattr(thread, "report_currency", None) or "by_position"
        if thread is not None
        else "by_position"
    )
    content_hash, stable_payload = _context_pack_payload(
        task_spec=task_spec,
        workflow=workflow,
        artifact_ids=artifact_ids,
        artifact_refs=artifact_refs,
        recent_summary=summary_text,
        report_currency=report_currency,
    )
    payload_row = (
        session.query(ContextPackPayload)
        .filter(ContextPackPayload.content_hash == content_hash)
        .one_or_none()
    )
    if payload_row is None:
        payload_row = ContextPackPayload(
            content_hash=content_hash,
            stable_payload=stable_payload,
        )
        session.add(payload_row)
        session.flush()

    pack = ContextPack(
        workflow_id=workflow.id,
        task_id=task.id,
        payload_id=payload_row.id,
        metadata_={
            "recent_session_summary": summary_text,
            "assembled_at": datetime.utcnow().isoformat(),
            "assembler_version": _ASSEMBLER_VERSION,
        },
    )
    session.add(pack)
    session.flush()
    task.context_pack_id = pack.id
    payload = validate_event_payload(
        kind="context_pack_assembled",
        schema_version=1,
        payload={
            "context_pack_id": pack.id,
            "payload_id": payload_row.id,
            "content_hash": content_hash,
            "task_id": task.id,
        },
    )
    session.add(
        DomainEvent(
            workflow_id=workflow.id,
            task_id=task.id,
            kind="context_pack_assembled",
            payload=payload,
            actor="system",
        )
    )
    return pack

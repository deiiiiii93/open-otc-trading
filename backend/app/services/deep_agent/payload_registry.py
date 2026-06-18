"""Payload kind validators for the long-agent artifact and event ledger."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

PAYLOAD_LIBRARY_VERSION = "1.0"

ARTIFACT_KINDS: frozenset[str] = frozenset(
    {
        "claim",
        "deterministic_query",
        "finding",
        "persisted_run",
        "plan",
        "report",
        "sandbox_output",
        "tool_result",
    }
)

EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "agent_attestation",
        "context_pack",
        "deterministic_run",
        "human_approval",
        "snapshot",
    }
)

LOAD_BEARING_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "deterministic_run",
        "human_approval",
        "snapshot",
    }
)

EVENT_KINDS: frozenset[str] = frozenset(
    {
        "artifact_created",
        "artifact_gc'd",
        "context_pack_assembled",
        "hitl_approved",
        "hitl_requested",
        "hitl_rejected",
        "session_closed",
        "session_opened",
        "session_resumed",
        "snapshot_captured",
        "task_completed",
        "task_failed",
        "task_planned",
        "task_resumed",
        "task_started",
        "workflow_opened",
    }
)


class _JsonObjectPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def _validate_json_object(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} payload must be a JSON object")
    return _JsonObjectPayload.model_validate(payload).model_dump(mode="json")


def validate_artifact_payload(
    *,
    kind: str,
    schema_version: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"Unknown artifact kind: {kind}")
    if schema_version != 1:
        raise ValueError(f"Unsupported artifact schema version: {schema_version}")
    return _validate_json_object(payload, label="Artifact")


def validate_evidence_payload(
    *,
    evidence_kind: str,
    evidence_payload: dict[str, Any],
) -> dict[str, Any]:
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(f"Unknown evidence kind: {evidence_kind}")
    return _validate_json_object(evidence_payload, label="Evidence")


def validate_event_payload(
    *,
    kind: str,
    schema_version: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if kind not in EVENT_KINDS:
        raise ValueError(f"Unknown domain event kind: {kind}")
    if schema_version != 1:
        raise ValueError(f"Unsupported domain event schema version: {schema_version}")
    return _validate_json_object(payload, label="Domain event")


def is_load_bearing_evidence(evidence_kind: str) -> bool:
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(f"Unknown evidence kind: {evidence_kind}")
    return evidence_kind in LOAD_BEARING_EVIDENCE_KINDS

"""Artifact, evidence, and event ledger writes for workflow-routing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...models import (
    AgentTask,
    ArtifactEvidenceRef,
    DomainEvent,
    SessionArtifact,
)
from .payload_registry import (
    validate_artifact_payload,
    validate_event_payload,
    validate_evidence_payload,
)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_kind: str
    evidence_payload: dict[str, Any]


class LedgerWriter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def emit_event(
        self,
        *,
        workflow_id: int,
        kind: str,
        payload: dict[str, Any],
        actor: str = "system",
        session_id: int | None = None,
        task_id: int | None = None,
        artifact_id: int | None = None,
    ) -> DomainEvent:
        event_payload = validate_event_payload(
            kind=kind,
            schema_version=1,
            payload=payload,
        )
        event = DomainEvent(
            workflow_id=workflow_id,
            session_id=session_id,
            task_id=task_id,
            artifact_id=artifact_id,
            kind=kind,
            schema_version=1,
            payload=event_payload,
            actor=actor,
        )
        self.session.add(event)
        return event

    def write_artifact(
        self,
        *,
        workflow_id: int,
        kind: str,
        title: str,
        payload: dict[str, Any],
        session_id: int | None = None,
        task_id: int | None = None,
        context_pack_id: int | None = None,
        rendered_path: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
        pinned: bool = False,
    ) -> SessionArtifact:
        artifact_payload = validate_artifact_payload(
            kind=kind,
            schema_version=1,
            payload=payload,
        )
        artifact = SessionArtifact(
            workflow_id=workflow_id,
            session_id=session_id,
            task_id=task_id,
            kind=kind,
            schema_version=1,
            title=title,
            payload=artifact_payload,
            rendered_path=rendered_path,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            context_pack_id=context_pack_id,
            pinned=pinned,
        )
        self.session.add(artifact)
        self.session.flush()

        refs = list(evidence_refs or [])
        if context_pack_id is not None and not any(
            ref.evidence_kind == "context_pack" for ref in refs
        ):
            refs.append(
                EvidenceRef(
                    evidence_kind="context_pack",
                    evidence_payload={"context_pack_id": context_pack_id},
                )
            )

        for ref in refs:
            evidence_payload = validate_evidence_payload(
                evidence_kind=ref.evidence_kind,
                evidence_payload=ref.evidence_payload,
            )
            self.session.add(
                ArtifactEvidenceRef(
                    artifact_id=artifact.id,
                    evidence_kind=ref.evidence_kind,
                    evidence_payload=evidence_payload,
                )
            )

        if task_id is not None:
            task = self.session.get(AgentTask, task_id)
            if task is not None:
                task.output_artifact_id = artifact.id

        self.emit_event(
            workflow_id=workflow_id,
            session_id=session_id,
            task_id=task_id,
            artifact_id=artifact.id,
            kind="artifact_created",
            payload={
                "artifact_id": artifact.id,
                "kind": kind,
                "title": title,
                "context_pack_id": context_pack_id,
            },
            actor="system",
        )
        return artifact

"""Workflow/session bootstrap helpers for the long-agent runtime."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import (
    AgentMessage,
    AgentSession,
    AgentThread,
    ContextPack,
    ContextPackPayload,
    DomainEvent,
    Workflow,
)
from .snapshot import capture_workflow_snapshot, snapshot_for_workflow

_ASSEMBLER_VERSION = "legacy-backfill-v1"


@dataclass(frozen=True)
class ThreadWorkflowState:
    meta_workflow_id: int
    router_session_id: int
    domain_workflow_id: int
    orchestrator_session_id: int
    context_pack_id: int


def _utcnow() -> datetime:
    return datetime.utcnow()


def _canonical_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _get_or_create_workflow(
    session: Session,
    *,
    thread: AgentThread,
    intent: str,
    title: str,
    opened_by: str,
    canonical_snapshot_ids: dict[str, Any],
) -> Workflow:
    workflow = (
        session.query(Workflow)
        .filter(
            Workflow.thread_id == thread.id,
            Workflow.intent == intent,
            Workflow.opened_at >= thread.created_at,
        )
        .order_by(Workflow.id)
        .first()
    )
    if workflow is not None:
        return workflow
    snapshot = snapshot_for_workflow(intent=intent, base=canonical_snapshot_ids)
    workflow = Workflow(
        thread_id=thread.id,
        title=title,
        intent=intent,
        status="active",
        opened_by=opened_by,
        canonical_snapshot_ids=snapshot,
    )
    session.add(workflow)
    session.flush()
    session.add(
        DomainEvent(
            workflow_id=workflow.id,
            kind="workflow_opened",
            payload={
                "thread_id": thread.id,
                "intent": intent,
                "title": title,
                "source": "legacy_backfill",
            },
            actor="system",
        )
    )
    capture_workflow_snapshot(
        session,
        workflow=workflow,
        snapshot=snapshot,
        source="legacy_backfill",
    )
    return workflow


def _checkpointer_key_in_use_elsewhere(
    session: Session,
    *,
    checkpointer_key: str,
    workflow_id: int,
    persona: str,
    episode_id: int,
) -> bool:
    existing = (
        session.query(AgentSession)
        .filter(AgentSession.checkpointer_key == checkpointer_key)
        .one_or_none()
    )
    if existing is None:
        return False
    return not (
        existing.workflow_id == workflow_id
        and existing.persona == persona
        and existing.episode_id == episode_id
    )


def unique_checkpointer_key(
    session: Session,
    *,
    desired_key: str,
    workflow_id: int,
    persona: str,
    episode_id: int = 1,
) -> str:
    """Return a checkpoint key that will not attach to another session."""
    if not _checkpointer_key_in_use_elsewhere(
        session,
        checkpointer_key=desired_key,
        workflow_id=workflow_id,
        persona=persona,
        episode_id=episode_id,
    ):
        return desired_key

    base_key = f"workflow:{workflow_id}:persona:{persona}:episode:{episode_id}"
    candidate = base_key
    suffix = 2
    while _checkpointer_key_in_use_elsewhere(
        session,
        checkpointer_key=candidate,
        workflow_id=workflow_id,
        persona=persona,
        episode_id=episode_id,
    ):
        candidate = f"{base_key}:dedupe:{suffix}"
        suffix += 1
    return candidate


def _get_or_create_session(
    session: Session,
    *,
    workflow_id: int,
    persona: str,
    checkpointer_key: str,
) -> AgentSession:
    agent_session = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == workflow_id,
            AgentSession.persona == persona,
            AgentSession.episode_id == 1,
        )
        .one_or_none()
    )
    if agent_session is not None:
        return agent_session
    checkpointer_key = unique_checkpointer_key(
        session,
        desired_key=checkpointer_key,
        workflow_id=workflow_id,
        persona=persona,
        episode_id=1,
    )
    agent_session = AgentSession(
        workflow_id=workflow_id,
        persona=persona,
        episode_id=1,
        status="active",
        checkpointer_key=checkpointer_key,
    )
    session.add(agent_session)
    session.flush()
    session.add(
        DomainEvent(
            workflow_id=workflow_id,
            session_id=agent_session.id,
            kind="session_opened",
            payload={
                "persona": persona,
                "episode_id": 1,
                "checkpointer_key": checkpointer_key,
                "source": "legacy_backfill",
            },
            actor="system",
        )
    )
    return agent_session


def _get_or_create_default_context_pack(
    session: Session,
    *,
    thread_id: int,
    workflow_id: int,
) -> ContextPack:
    existing = (
        session.query(ContextPack)
        .filter(ContextPack.workflow_id == workflow_id, ContextPack.task_id.is_(None))
        .order_by(ContextPack.id)
        .first()
    )
    if existing is not None:
        return existing

    stable_payload = {
        "task_type": "legacy_thread_backfill",
        "assigned_persona": "orchestrator",
        "task_brief": {"thread_id": thread_id},
        "canonical_snapshot_ids": {"scope_kind": "ad_hoc"},
        "cited_artifact_ids": [],
        "tools_scope": [],
        "tool_signature_hash": "sha256:legacy",
        "recent_session_summary_hash": "sha256:empty",
        "prompt_revision_hash": "sha256:legacy",
        "model_id": "legacy",
    }
    content_hash = _canonical_hash(stable_payload)
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
        workflow_id=workflow_id,
        task_id=None,
        payload_id=payload_row.id,
        metadata_={
            "recent_session_summary": "",
            "assembled_at": _utcnow().isoformat(),
            "assembler_version": _ASSEMBLER_VERSION,
        },
    )
    session.add(pack)
    session.flush()
    return pack


def ensure_thread_workflow_state(
    session: Session,
    thread_id: int,
) -> ThreadWorkflowState:
    """Backfill the C-mig-2 workflow/session skeleton for one legacy thread."""
    thread = session.get(AgentThread, thread_id)
    if thread is None:
        raise ValueError(f"AgentThread {thread_id} not found")

    meta_workflow = _get_or_create_workflow(
        session,
        thread=thread,
        intent="workspace_meta",
        title=f"{thread.title} / workspace",
        opened_by="system",
        canonical_snapshot_ids={"scope_kind": "workspace_meta"},
    )
    router_session = _get_or_create_session(
        session,
        workflow_id=meta_workflow.id,
        persona="router",
        checkpointer_key=f"thread:{thread.id}:router",
    )
    domain_workflow = _get_or_create_workflow(
        session,
        thread=thread,
        intent="ad_hoc",
        title=thread.title,
        opened_by="system",
        canonical_snapshot_ids={"scope_kind": "ad_hoc"},
    )
    orchestrator_session = _get_or_create_session(
        session,
        workflow_id=domain_workflow.id,
        persona="orchestrator",
        checkpointer_key=str(thread.id),
    )
    context_pack = _get_or_create_default_context_pack(
        session,
        thread_id=thread.id,
        workflow_id=domain_workflow.id,
    )

    thread.active_workflow_id = domain_workflow.id
    messages = (
        session.query(AgentMessage)
        .filter(
            AgentMessage.thread_id == thread.id,
            AgentMessage.workflow_id.is_(None),
        )
        .all()
    )
    for message in messages:
        message.workflow_id = domain_workflow.id
        message.session_id = orchestrator_session.id
    session.flush()
    return ThreadWorkflowState(
        meta_workflow_id=meta_workflow.id,
        router_session_id=router_session.id,
        domain_workflow_id=domain_workflow.id,
        orchestrator_session_id=orchestrator_session.id,
        context_pack_id=context_pack.id,
    )


def active_message_scope(
    session: Session,
    thread_id: int,
) -> tuple[int | None, int | None]:
    """Return workflow/session ids for legacy AgentMessage inserts."""
    thread = session.get(AgentThread, thread_id)
    if thread is None or thread.active_workflow_id is None:
        return None, None
    agent_session = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == thread.active_workflow_id,
            AgentSession.persona == "orchestrator",
            AgentSession.status == "active",
        )
        .order_by(AgentSession.episode_id.desc(), AgentSession.id.desc())
        .first()
    )
    return thread.active_workflow_id, agent_session.id if agent_session else None

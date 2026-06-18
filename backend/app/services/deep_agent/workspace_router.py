"""Deterministic workspace-router phase for workflow routing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from ...models import AgentSession, AgentThread, DomainEvent, Workflow
from .snapshot import capture_workflow_snapshot, snapshot_for_workflow
from .workflow_state import ensure_thread_workflow_state, unique_checkpointer_key

RouterDecisionKind = Literal[
    "continue_workflow",
    "new_workflow",
    "status_query",
    "cross_workflow_query",
    "clarify",
]

_WORKFLOW_REF_RE = re.compile(r"\bworkflow\s*#?\s*(\d+)\b", re.IGNORECASE)
_STATUS_PATTERNS = (
    "what's in flight",
    "what is in flight",
    "whats in flight",
    "pending approvals",
    "any pending approval",
    "any pending approvals",
)
_CROSS_WORKFLOW_RE = re.compile(
    r"\b(compare|across)\b.*\bworkflow\s*#?\s*\d+.*\bworkflow\s*#?\s*\d+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkspaceRouteDecision:
    kind: RouterDecisionKind
    workflow_id: int
    session_id: int
    response_content: str | None = None
    target_workflow_id: int | None = None
    opened_workflow_id: int | None = None


def route_workspace_turn(
    session: Session,
    *,
    thread: AgentThread,
    user_message: str,
    yolo_mode: bool = False,
) -> WorkspaceRouteDecision:
    meta_workflow, router_session, default_domain_workflow_id = _router_home(
        session, thread=thread
    )

    text = user_message.strip()
    lowered = text.lower()
    active_domain = _active_domain_workflows(session, thread=thread)

    if _is_status_query(lowered):
        return WorkspaceRouteDecision(
            kind="status_query",
            workflow_id=meta_workflow.id,
            session_id=router_session.id,
            response_content=_status_reply(active_domain),
        )

    if _CROSS_WORKFLOW_RE.search(text):
        return WorkspaceRouteDecision(
            kind="cross_workflow_query",
            workflow_id=meta_workflow.id,
            session_id=router_session.id,
            response_content=(
                "Cross-workflow synthesis is not enabled yet. "
                "Pick one workflow or ask for the in-flight status."
            ),
        )

    explicit = _resolve_explicit_workflow(session, thread=thread, message=text)
    if explicit is not None:
        session_id = ensure_workflow_persona_session(
            session,
            workflow_id=explicit.id,
            persona="orchestrator",
            legacy_checkpointer_key=None,
        ).id
        thread.active_workflow_id = explicit.id
        return WorkspaceRouteDecision(
            kind="continue_workflow",
            workflow_id=explicit.id,
            session_id=session_id,
            target_workflow_id=explicit.id,
        )

    if len(active_domain) == 1 and not _asks_for_new_workflow(lowered):
        workflow = active_domain[0]
        session_id = ensure_workflow_persona_session(
            session,
            workflow_id=workflow.id,
            persona="orchestrator",
            legacy_checkpointer_key=(
                str(thread.id) if workflow.id == default_domain_workflow_id else None
            ),
        ).id
        thread.active_workflow_id = workflow.id
        return WorkspaceRouteDecision(
            kind="continue_workflow",
            workflow_id=workflow.id,
            session_id=session_id,
            target_workflow_id=workflow.id,
        )

    if yolo_mode:
        workflow = _open_ad_hoc_workflow(session, thread=thread, title=_title_for(text))
        session_id = ensure_workflow_persona_session(
            session,
            workflow_id=workflow.id,
            persona="orchestrator",
            legacy_checkpointer_key=None,
        ).id
        thread.active_workflow_id = workflow.id
        return WorkspaceRouteDecision(
            kind="new_workflow",
            workflow_id=workflow.id,
            session_id=session_id,
            target_workflow_id=workflow.id,
            opened_workflow_id=workflow.id,
        )

    return WorkspaceRouteDecision(
        kind="clarify",
        workflow_id=meta_workflow.id,
        session_id=router_session.id,
        response_content=_clarifying_reply(active_domain),
    )


def _router_home(
    session: Session,
    *,
    thread: AgentThread,
) -> tuple[Workflow, AgentSession, int | None]:
    meta_workflow = (
        session.query(Workflow)
        .filter(
            Workflow.thread_id == thread.id,
            Workflow.intent == "workspace_meta",
            Workflow.opened_at >= thread.created_at,
        )
        .order_by(Workflow.id)
        .first()
    )
    if meta_workflow is not None:
        router_session = ensure_workflow_persona_session(
            session,
            workflow_id=meta_workflow.id,
            persona="router",
            legacy_checkpointer_key=f"thread:{thread.id}:router",
        )
        return meta_workflow, router_session, thread.active_workflow_id

    state = ensure_thread_workflow_state(session, thread.id)
    meta_workflow = session.get(Workflow, state.meta_workflow_id)
    router_session = session.get(AgentSession, state.router_session_id)
    if meta_workflow is None or router_session is None:
        raise ValueError(f"Workspace meta state missing for thread {thread.id}")
    return meta_workflow, router_session, state.domain_workflow_id


def ensure_workflow_persona_session(
    session: Session,
    *,
    workflow_id: int,
    persona: str,
    legacy_checkpointer_key: str | None,
) -> AgentSession:
    existing = (
        session.query(AgentSession)
        .filter(
            AgentSession.workflow_id == workflow_id,
            AgentSession.persona == persona,
            AgentSession.episode_id == 1,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    checkpointer_key = (
        legacy_checkpointer_key
        or f"workflow:{workflow_id}:persona:{persona}:episode:1"
    )
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
                "source": "workspace_router",
            },
            actor="system",
        )
    )
    return agent_session


def _active_domain_workflows(session: Session, *, thread: AgentThread) -> list[Workflow]:
    return (
        session.query(Workflow)
        .filter(
            Workflow.thread_id == thread.id,
            Workflow.status == "active",
            Workflow.intent != "workspace_meta",
            Workflow.opened_at >= thread.created_at,
        )
        .order_by(Workflow.id)
        .all()
    )


def _is_status_query(lowered: str) -> bool:
    return any(pattern in lowered for pattern in _STATUS_PATTERNS)


def _resolve_explicit_workflow(
    session: Session,
    *,
    thread: AgentThread,
    message: str,
) -> Workflow | None:
    match = _WORKFLOW_REF_RE.search(message)
    if match is None:
        return None
    workflow_id = int(match.group(1))
    return (
        session.query(Workflow)
        .filter(
            Workflow.id == workflow_id,
            Workflow.thread_id == thread.id,
            Workflow.intent != "workspace_meta",
            Workflow.status == "active",
            Workflow.opened_at >= thread.created_at,
        )
        .one_or_none()
    )


def _asks_for_new_workflow(lowered: str) -> bool:
    return "new workflow" in lowered or "start workflow" in lowered


def _open_ad_hoc_workflow(
    session: Session,
    *,
    thread: AgentThread,
    title: str,
) -> Workflow:
    snapshot = snapshot_for_workflow(
        intent="ad_hoc",
        base={"scope_kind": "ad_hoc"},
    )
    workflow = Workflow(
        thread_id=thread.id,
        title=title,
        intent="ad_hoc",
        status="active",
        opened_by="router",
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
                "intent": workflow.intent,
                "title": workflow.title,
                "source": "workspace_router",
            },
            actor="system",
        )
    )
    capture_workflow_snapshot(
        session,
        workflow=workflow,
        snapshot=snapshot,
        source="workspace_router",
    )
    return workflow


def _title_for(message: str) -> str:
    title = " ".join(message.split())[:80].strip()
    return title or "Ad-hoc workflow"


def _status_reply(workflows: list[Workflow]) -> str:
    if not workflows:
        return "Active workflows: none."
    lines = ["Active workflows:"]
    for workflow in workflows:
        lines.append(f"- #{workflow.id} {workflow.title} ({workflow.status})")
    return "\n".join(lines)


def _clarifying_reply(workflows: list[Workflow]) -> str:
    if not workflows:
        return "Which workflow should I start for this?"
    options = ", ".join(f"#{workflow.id} {workflow.title}" for workflow in workflows)
    return f"Which workflow should I continue? Active workflows: {options}."

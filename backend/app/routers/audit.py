"""Read-only API over agent_action_audits (audit spec §6).

READ-ONLY BY DOCTRINE (same rule as tracing.py): the audit trail is
append-only evidence; no mutating endpoint may ever be added here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from app import database
from app.models import AgentActionAudit
from app.services.audit_trail import unpersisted_refusals


class AuditActionOut(BaseModel):
    id: int
    kind: str
    status: str
    deny_reason: str | None
    tool_name: str
    tool_class: str
    tool_call_id: str | None
    audit_ref: str | None
    mode: str | None
    envelope: str | None
    actor: str
    model: str | None
    persona: str | None
    thread_id: int | None
    workflow_id: int | None
    session_id: int | None
    task_id: int | None
    message_id: int | None
    desk_workflow_slug: str | None
    args_json: Any
    redacted: bool
    result_preview: str | None
    error: str | None
    occurred_at: Any
    completed_at: Any


def _out(row: AgentActionAudit) -> dict:
    return AuditActionOut(
        **{field: getattr(row, field) for field in AuditActionOut.model_fields}
    ).model_dump()


def build_audit_router() -> APIRouter:
    router = APIRouter(prefix="/api/audit", tags=["audit"])

    @router.get("/actions")
    def list_actions(
        status: str | None = None,
        kind: str | None = None,
        tool_name: str | None = None,
        tool_class: str | None = None,
        audit_ref: str | None = None,
        mode: str | None = None,
        thread_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = Query(50, le=200, ge=1),
        offset: int = Query(0, ge=0),
    ):
        with database.SessionLocal() as session:
            q = session.query(AgentActionAudit)
            for column, value in (
                (AgentActionAudit.status, status),
                (AgentActionAudit.kind, kind),
                (AgentActionAudit.tool_class, tool_class),
                (AgentActionAudit.audit_ref, audit_ref),
                (AgentActionAudit.mode, mode),
                (AgentActionAudit.thread_id, thread_id),
            ):
                if value is not None:
                    q = q.filter(column == value)
            if tool_name is not None:
                # Substring match: the UI exposes this as a search box, and the
                # list is server-paginated so the filter must be server-side.
                q = q.filter(AgentActionAudit.tool_name.ilike(f"%{tool_name}%"))
            if since is not None:
                q = q.filter(AgentActionAudit.occurred_at >= since)
            if until is not None:
                q = q.filter(AgentActionAudit.occurred_at <= until)
            total = q.count()
            rows = (
                q.order_by(
                    AgentActionAudit.occurred_at.desc(), AgentActionAudit.id.desc()
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {"items": [_out(r) for r in rows], "total": total}

    @router.get("/actions/{action_id}")
    def get_action(action_id: int):
        with database.SessionLocal() as session:
            row = session.get(AgentActionAudit, action_id)
            if row is None:
                raise HTTPException(404, "audit action not found")
            if row.audit_ref:
                related_q = session.query(AgentActionAudit).filter(
                    AgentActionAudit.audit_ref == row.audit_ref,
                    AgentActionAudit.id != row.id,
                )
            elif row.tool_call_id and row.thread_id is not None:
                # Display-only fallback for legacy rows without audit_ref:
                # scoped (thread_id, tool_call_id) grouping.
                related_q = session.query(AgentActionAudit).filter(
                    AgentActionAudit.thread_id == row.thread_id,
                    AgentActionAudit.tool_call_id == row.tool_call_id,
                    AgentActionAudit.id != row.id,
                )
            else:
                related_q = None
            related = (
                related_q.order_by(AgentActionAudit.id).all()
                if related_q is not None
                else []
            )
            return {**_out(row), "related": [_out(r) for r in related]}

    @router.get("/summary")
    def summary(since: datetime | None = None):
        with database.SessionLocal() as session:
            q = session.query(AgentActionAudit)
            if since is not None:
                q = q.filter(AgentActionAudit.occurred_at >= since)

            def _counts(column):
                rows = (
                    q.with_entities(column, func.count()).group_by(column).all()
                )
                return {str(key): count for key, count in rows if key is not None}

            refused = q.filter(AgentActionAudit.status == "refused").count()
            return {
                "by_status": _counts(AgentActionAudit.status),
                "by_class": _counts(AgentActionAudit.tool_class),
                "by_mode": _counts(AgentActionAudit.mode),
                "fail_closed_refusals": {
                    "persisted": refused,
                    "unpersisted": unpersisted_refusals(),
                },
            }

    return router

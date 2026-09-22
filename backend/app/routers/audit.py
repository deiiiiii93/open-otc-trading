"""Read-only API over agent_action_audits (audit spec §6).

READ-ONLY BY DOCTRINE (same rule as tracing.py): the audit trail is
append-only evidence; no mutating endpoint may ever be added here.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import exists, func

from app import database
from app.models import AgentActionAudit, AgentToolGuardVerdict
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
    guard: dict[str, Any] | None = None


_GUARD_VERDICTS = frozenset({"clear", "flagged", "unscored"})
_GUARD_SOURCES = frozenset({"live", "sweep"})
_FIDELITIES = frozenset({"trace", "audit_only"})
_GUARD_FILTERS = _GUARD_VERDICTS | {"none"}


def _one_of(name: str, value: str | None, allowed: frozenset[str]) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(400, f"{name} must be one of {sorted(allowed)}")


def _guard_filter(guard: str | None, guard_source: str | None):
    """EXISTS on the verdict key, so the action list stays server-paginated. A
    NULL audit thread matches verdict thread 0 (as `_call_key`); an empty id
    never joins."""
    v = AgentToolGuardVerdict
    conditions = [
        v.thread_id == func.coalesce(AgentActionAudit.thread_id, 0),
        v.tool_call_id == AgentActionAudit.tool_call_id,
        v.tool_call_id != "",
    ]
    if guard_source is not None:
        conditions.append(v.source == guard_source)
    if guard in _GUARD_VERDICTS:
        conditions.append(v.verdict == guard)
    matched = exists().where(*conditions)
    return ~matched if guard == "none" else matched


def _call_key(thread_id: int | None, tool_call_id: str) -> tuple[int, str]:
    """Both halves of the verdict key; a NULL audit thread matches verdict thread 0."""
    return (thread_id or 0, tool_call_id)


def _guard_by_call(session, rows) -> dict[tuple[int, str], dict]:
    wanted = {_call_key(r.thread_id, r.tool_call_id) for r in rows if r.tool_call_id}
    if not wanted:
        return {}
    verdicts = (
        session.query(AgentToolGuardVerdict)
        .filter(AgentToolGuardVerdict.tool_call_id.in_({key[1] for key in wanted}))
        .all()
    )
    # The UNIQUE key makes each (thread_id, tool_call_id) at most one row.
    return {
        (v.thread_id, v.tool_call_id): {
            "verdict": v.verdict, "max_probability": v.max_probability,
            "source": v.source, "state_fidelity": v.state_fidelity,
        }
        for v in verdicts
        if (v.thread_id, v.tool_call_id) in wanted
    }


def _out(row: AgentActionAudit, guard: dict | None = None) -> dict:
    data = {field: getattr(row, field) for field in AuditActionOut.model_fields if field != "guard"}
    return AuditActionOut(**data, guard=guard).model_dump()


def _outs(session, rows) -> list[dict]:
    guards = _guard_by_call(session, rows)
    return [
        _out(r, guards.get(_call_key(r.thread_id, r.tool_call_id)) if r.tool_call_id else None)
        for r in rows
    ]


class GuardVerdictOut(BaseModel):
    id: int
    thread_id: int
    tool_call_id: str
    persona: str | None
    exec_mode: str | None
    guard_mode: str
    tool_name: str
    args_json: Any
    redacted: bool
    verdict: str
    unscored_reason: str | None
    predicates: list[Any]
    max_probability: float | None
    action: str
    model: str | None
    latency_ms: int | None
    error: str | None
    created_at: Any
    execution_status: str | None
    source: str
    state_fidelity: str | None
    audit_id: int | None


def _execution_status_by_call(session, verdicts) -> dict[tuple[int, str], str]:
    call_ids = {v.tool_call_id for v in verdicts if v.tool_call_id}
    if not call_ids:
        return {}
    rows = (
        session.query(AgentActionAudit.thread_id, AgentActionAudit.tool_call_id,
                      AgentActionAudit.status)
        .filter(AgentActionAudit.kind == "execution",
                AgentActionAudit.tool_call_id.in_(call_ids))
        .order_by(AgentActionAudit.id.asc())
        .all()
    )
    out: dict[tuple[int, str], str] = {}
    for thread_id, call_id, status in rows:
        out[_call_key(thread_id, call_id)] = status  # ascending id: newest wins
    return out


def _verdict_out(v: AgentToolGuardVerdict, execution_status: str | None) -> dict:
    return GuardVerdictOut(
        id=v.id, thread_id=v.thread_id, tool_call_id=v.tool_call_id, persona=v.persona,
        exec_mode=v.exec_mode, guard_mode=v.guard_mode, tool_name=v.tool_name,
        args_json=v.args_json, redacted=v.redacted, verdict=v.verdict,
        unscored_reason=v.unscored_reason, predicates=list(v.predicates_json or []),
        max_probability=v.max_probability, action=v.action, model=v.model,
        latency_ms=v.latency_ms, error=v.error, created_at=v.created_at,
        execution_status=execution_status,
        source=v.source, state_fidelity=v.state_fidelity, audit_id=v.audit_id,
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
        guard: str | None = None,
        guard_source: str | None = None,
        limit: int = Query(50, le=200, ge=1),
        offset: int = Query(0, ge=0),
    ):
        _one_of("guard", guard, _GUARD_FILTERS)
        _one_of("guard_source", guard_source, _GUARD_SOURCES)
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
            if guard is not None or guard_source is not None:
                q = q.filter(_guard_filter(guard, guard_source))
            total = q.count()
            rows = (
                q.order_by(
                    AgentActionAudit.occurred_at.desc(), AgentActionAudit.id.desc()
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {"items": _outs(session, rows), "total": total}

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
            outs = _outs(session, [row, *related])
            return {**outs[0], "related": outs[1:]}

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

    @router.get("/guard-verdicts")
    def list_guard_verdicts(
        verdict: str | None = None,
        tool_name: str | None = None,
        thread_id: int | None = None,
        since: datetime | None = None,
        source: str = "all",
        state_fidelity: str | None = None,
        limit: int = Query(50, le=200, ge=1),
        offset: int = Query(0, ge=0),
    ):
        """System One guard verdicts, newest first (spec §1 "Reading shadow data").
        Both sources by default: a reader asking for verdicts wants to see them."""
        _one_of("verdict", verdict, _GUARD_VERDICTS)
        _one_of("source", source, _GUARD_SOURCES | {"all"})
        _one_of("state_fidelity", state_fidelity, _FIDELITIES)
        with database.SessionLocal() as session:
            q = session.query(AgentToolGuardVerdict)
            if verdict is not None:
                q = q.filter(AgentToolGuardVerdict.verdict == verdict)
            if tool_name is not None:
                q = q.filter(AgentToolGuardVerdict.tool_name == tool_name)
            if thread_id is not None:
                q = q.filter(AgentToolGuardVerdict.thread_id == thread_id)
            if since is not None:
                q = q.filter(AgentToolGuardVerdict.created_at >= since)
            if source != "all":
                q = q.filter(AgentToolGuardVerdict.source == source)
            if state_fidelity is not None:
                q = q.filter(AgentToolGuardVerdict.state_fidelity == state_fidelity)
            total = q.count()
            rows = (
                q.order_by(AgentToolGuardVerdict.created_at.desc(),
                           AgentToolGuardVerdict.id.desc())
                .offset(offset).limit(limit).all()
            )
            status = _execution_status_by_call(session, rows)
            return {
                "items": [
                    _verdict_out(v, status.get(_call_key(v.thread_id, v.tool_call_id))
                                 if v.tool_call_id else None)
                    for v in rows
                ],
                "total": total,
            }

    @router.get("/guard-verdicts/summary")
    def guard_verdict_summary(since: datetime | None = None, source: str = "live"):
        """`flagged_then_ok` = flagged verdicts whose call then ran `ok` — the
        candidate false positives shadow mode exists to count. Defaults to
        source=live (spec 2026-09-22 D12): a sweep row describes a call that ran
        by definition, so pooling it would inflate the count."""
        _one_of("source", source, _GUARD_SOURCES | {"all"})
        with database.SessionLocal() as session:
            q = session.query(AgentToolGuardVerdict)
            if since is not None:
                q = q.filter(AgentToolGuardVerdict.created_at >= since)
            if source != "all":
                q = q.filter(AgentToolGuardVerdict.source == source)
            verdicts = q.all()
            status = _execution_status_by_call(session, verdicts)
        by_tool: dict[str, dict[str, Any]] = {}
        latencies: dict[str, list[int]] = {}
        reasons: dict[str, int] = {}
        for v in verdicts:
            row = by_tool.setdefault(v.tool_name, {
                "tool_name": v.tool_name, "total": 0, "clear": 0, "flagged": 0,
                "unscored": 0, "flagged_then_ok": 0, "median_latency_ms": None,
            })
            row["total"] += 1
            if v.verdict in _GUARD_VERDICTS:
                row[v.verdict] += 1
            if (v.verdict == "flagged" and v.tool_call_id
                    and status.get(_call_key(v.thread_id, v.tool_call_id)) == "ok"):
                row["flagged_then_ok"] += 1
            if v.latency_ms is not None:
                latencies.setdefault(v.tool_name, []).append(v.latency_ms)
            if v.unscored_reason:
                reasons[v.unscored_reason] = reasons.get(v.unscored_reason, 0) + 1
        for name, values in latencies.items():
            by_tool[name]["median_latency_ms"] = median(values)
        return {
            "source": source,
            "by_tool": [by_tool[name] for name in sorted(by_tool)],
            "unscored_reasons": reasons,
        }

    return router

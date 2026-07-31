"""Agent tools over the governed Limits module (monitoring runs, incidents).

Thin wrappers over ``services/limits/`` — no logic is reimplemented here.
Reads return plain dicts; incident payloads always include ``row_version``
because every mutation requires it (optimistic concurrency is preserved,
never bypassed server-side).
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .. import database
from ..models import (
    LimitEvaluation,
    LimitIncident,
    LimitMonitoringRun,
    LimitSourceReference,
    RiskLimit,
    RiskLimitVersion,
)
from ..services.deep_agent.capability_gate import capability_gated
from ..services.deep_agent.envelopes import ToolGroup


def _version_out(version: RiskLimitVersion) -> dict[str, Any]:
    return {
        "version": version.version,
        "metric_kind": version.metric_kind,
        "source_kind": version.source_kind,
        "scope_type": version.scope_type,
        "scope_config": dict(version.scope_config or {}),
        "aggregation": version.aggregation,
        "transform": version.transform,
        "comparator": version.comparator,
        "warning_lower": version.warning_lower,
        "warning_upper": version.warning_upper,
        "hard_lower": version.hard_lower,
        "hard_upper": version.hard_upper,
        "unit": version.unit,
        "currency": version.currency,
    }


def _limit_key_by_version(session, version_ids: list[int]) -> dict[int, str]:
    if not version_ids:
        return {}
    rows = session.execute(
        select(RiskLimitVersion.id, RiskLimit.key)
        .join(RiskLimit, RiskLimit.id == RiskLimitVersion.risk_limit_id)
        .where(RiskLimitVersion.id.in_(version_ids))
    ).all()
    return {int(vid): key for vid, key in rows}


def _incident_out(session, incident: LimitIncident) -> dict[str, Any]:
    limit = session.get(RiskLimit, incident.risk_limit_id)
    return {
        "id": incident.id,
        "portfolio_id": incident.portfolio_id,
        "limit_key": limit.key if limit is not None else None,
        "scope_type": incident.scope_type,
        "scope_key": incident.scope_key,
        "scope_label": incident.scope_label,
        "severity": incident.severity,
        "status": incident.status,
        "assignee": incident.assignee,
        "waiver_rationale": incident.waiver_rationale,
        "first_seen_at": _iso(incident.first_seen_at),
        "last_seen_at": _iso(incident.last_seen_at),
        "resolved_at": _iso(incident.resolved_at),
        "row_version": incident.row_version,
    }


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


class ListRiskLimitsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_risk_limits", args_schema=ListRiskLimitsInput)
def list_risk_limits_tool(portfolio_id: int) -> dict[str, Any]:
    """List risk limit definitions governing a portfolio, with each limit's
    active version boundaries (warning/hard), metric, scope, and unit."""
    database.init_db()
    with database.SessionLocal() as session:
        versions = list(
            session.execute(
                select(RiskLimitVersion)
                .join(RiskLimit, RiskLimit.id == RiskLimitVersion.risk_limit_id)
                .where(RiskLimitVersion.activated_at.is_not(None))
                .order_by(RiskLimitVersion.id)
            ).scalars()
        )
        limits: list[dict[str, Any]] = []
        seen: set[int] = set()
        for version in versions:
            if version.scope_type == "portfolio":
                ids = (version.scope_config or {}).get("portfolio_ids") or []
                if portfolio_id not in {int(v) for v in ids}:
                    continue
            limit = session.get(RiskLimit, version.risk_limit_id)
            if limit is None or limit.id in seen:
                continue
            seen.add(limit.id)
            active = version
            if limit.active_version_id is not None:
                active = session.get(
                    RiskLimitVersion, limit.active_version_id
                ) or version
            limits.append(
                {
                    "id": limit.id,
                    "key": limit.key,
                    "name": limit.name,
                    "category": limit.category,
                    "active_version": _version_out(active),
                }
            )
        return {"portfolio_id": portfolio_id, "limits": limits}


class GetLimitMonitoringRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int | None = None
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_limit_monitoring_run", args_schema=GetLimitMonitoringRunInput)
def get_limit_monitoring_run_tool(
    run_id: int | None = None, portfolio_id: int | None = None
) -> dict[str, Any]:
    """Fetch a limit monitoring run with its per-limit evaluations (status,
    observed value, utilization, headroom) and evidence sources. Pass run_id
    for a specific run, or portfolio_id for the latest run on that book."""
    if (run_id is None) == (portfolio_id is None):
        return {"error": "pass exactly one of run_id or portfolio_id"}
    database.init_db()
    with database.SessionLocal() as session:
        if run_id is not None:
            run = session.get(LimitMonitoringRun, run_id)
        else:
            run = session.execute(
                select(LimitMonitoringRun)
                .where(LimitMonitoringRun.portfolio_id == portfolio_id)
                .order_by(LimitMonitoringRun.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        if run is None:
            return {"error": "no limit monitoring run found"}
        evaluations = list(
            session.execute(
                select(LimitEvaluation)
                .where(LimitEvaluation.monitoring_run_id == run.id)
                .order_by(LimitEvaluation.id)
            ).scalars()
        )
        keys = _limit_key_by_version(
            session, [e.limit_version_id for e in evaluations]
        )
        sources = list(
            session.execute(
                select(LimitSourceReference)
                .where(LimitSourceReference.monitoring_run_id == run.id)
                .order_by(LimitSourceReference.id)
            ).scalars()
        )
        return {
            "id": run.id,
            "portfolio_id": run.portfolio_id,
            "status": run.status,
            "valuation_as_of": _iso(run.valuation_as_of),
            "source_policy": run.source_policy,
            "summary": dict(run.summary or {}),
            "evaluations": [
                {
                    "limit_key": keys.get(e.limit_version_id),
                    "scope_type": e.scope_type,
                    "scope_key": e.scope_key,
                    "scope_label": e.scope_label,
                    "status": e.status,
                    "observed_value": e.observed_value,
                    "utilization": e.utilization,
                    "headroom": e.headroom,
                    "warning_upper": e.warning_upper,
                    "hard_upper": e.hard_upper,
                    "reason_code": e.reason_code,
                }
                for e in evaluations
            ],
            "sources": [
                {
                    "source_kind": s.source_kind,
                    "risk_run_id": s.risk_run_id,
                    "source_status": s.source_status,
                    "is_fresh": s.is_fresh,
                }
                for s in sources
            ],
        }


class ListLimitIncidentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    status: str | None = None


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_limit_incidents", args_schema=ListLimitIncidentsInput)
def list_limit_incidents_tool(
    portfolio_id: int, status: str | None = None
) -> dict[str, Any]:
    """List limit breach incidents for a portfolio (optionally filtered by
    status: open, acknowledged, assigned, waived, resolved, recovered). Each
    row carries row_version — mutations require it."""
    database.init_db()
    with database.SessionLocal() as session:
        stmt = (
            select(LimitIncident)
            .where(LimitIncident.portfolio_id == portfolio_id)
            .order_by(LimitIncident.id)
        )
        if status is not None:
            stmt = stmt.where(LimitIncident.status == status)
        incidents = list(session.execute(stmt).scalars())
        return {
            "portfolio_id": portfolio_id,
            "incidents": [_incident_out(session, i) for i in incidents],
        }


class GetLimitIncidentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_limit_incident", args_schema=GetLimitIncidentInput)
def get_limit_incident_tool(incident_id: int) -> dict[str, Any]:
    """Fetch one limit incident with its full event timeline and current
    row_version (required by acknowledge/comment/waive/resolve)."""
    database.init_db()
    with database.SessionLocal() as session:
        incident = session.get(LimitIncident, incident_id)
        if incident is None:
            return {"error": f"limit incident not found: {incident_id}"}
        out = _incident_out(session, incident)
        out["events"] = [
            {
                "event_type": e.event_type,
                "actor": e.actor,
                "persona": e.persona,
                "created_at": _iso(e.created_at),
                "payload": dict(e.payload or {}),
            }
            for e in incident.events
        ]
        return out

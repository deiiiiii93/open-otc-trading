"""Limits, incident and stress block producers.

The tri-state matters most here. A limit evaluation that could not be computed
(`unknown`, `incomplete_scope`) is neither a pass nor a breach: it is counted
as indeterminate and surfaced, because "we could not evaluate this limit" must
never render as "this limit is fine".
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import (
    LimitEvaluation,
    LimitIncident,
    LimitMonitoringRun,
    ScenarioTestRun,
)

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_BREACH_STATUSES = frozenset({"breach", "hard_breach", "warning"})
_INDETERMINATE_STATUSES = frozenset({"unknown", "incomplete_scope"})

_NO_MONITORING = (
    "no limit monitoring run exists for this portfolio, so limit status could "
    "not be evaluated"
)
_NO_SCENARIO = (
    "no scenario test run exists for this portfolio, so no stress grid could "
    "be reported"
)
_UNREADABLE_SCENARIO = (
    "the latest scenario run publishes no recognisable result grid, so its "
    "stress results could not be read"
)

# The scenario payload nests greeks/underlying_results/position_results, which a
# rows renderer stringifies to "[object Object]". Project to the scalars a stress
# grid is actually read for.
_SCENARIO_ROW_FIELDS = ("name", "portfolio_value", "pnl", "pnl_pct")


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _load_latest_evaluations(
    ctx: BlockContext,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Latest monitoring run's evaluations, as plain dicts."""
    with _session_scope() as session:
        run = (
            session.query(LimitMonitoringRun)
            .filter(LimitMonitoringRun.portfolio_id == ctx.portfolio_id)
            .order_by(LimitMonitoringRun.id.desc())
            .first()
        )
        if run is None:
            return None, {}
        rows = (
            session.query(LimitEvaluation)
            .filter(LimitEvaluation.monitoring_run_id == run.id)
            .all()
        )
        evaluations = [
            {
                "scope_label": row.scope_label,
                "status": row.status,
                "observed_value": row.observed_value,
                "utilization": row.utilization,
                "headroom": row.headroom,
                "governing_boundary": row.governing_boundary,
                "reason": row.reason,
                "reason_code": row.reason_code,
                "coverage_ratio": row.coverage_ratio,
            }
            for row in rows
        ]
        return evaluations, {
            "monitoring_run_id": run.id,
            "evaluated_at": rows[0].evaluated_at.isoformat() if rows else None,
        }


def _load_incidents(
    ctx: BlockContext,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(LimitIncident)
            .filter(LimitIncident.portfolio_id == ctx.portfolio_id)
            .order_by(LimitIncident.first_seen_at.desc(), LimitIncident.id.desc())
            .all()
        )
        incidents = [
            {
                "incident_id": row.id,
                "scope_label": row.scope_label,
                "severity": row.severity,
                "status": row.status,
                "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
                "owner": row.owner,
                "row_version": row.row_version,
            }
            for row in rows
        ]
        return incidents, {"portfolio_id": ctx.portfolio_id}


def _load_latest_scenario_run(
    ctx: BlockContext,
) -> tuple[Any | None, dict[str, Any]]:
    with _session_scope() as session:
        run = (
            session.query(ScenarioTestRun)
            .filter(ScenarioTestRun.portfolio_id == ctx.portfolio_id)
            .order_by(ScenarioTestRun.id.desc())
            .first()
        )
        if run is None:
            return None, {}
        return run, {"scenario_test_run_id": run.id}


@report_block(
    key="limits.utilization", title="Limit utilisation",
    shape=BlockShape.SCALARS_WITH_PRIOR, domain="limits",
    description="Per-limit observed value, utilisation and headroom from the latest monitoring run.",
)
def limits_utilization(ctx: BlockContext) -> BlockResult:
    evaluations, provenance = _load_latest_evaluations(ctx)
    if evaluations is None:
        return BlockResult.unavailable(_NO_MONITORING)
    if not evaluations:
        return BlockResult.empty(
            "the monitoring run evaluated no limits", provenance=provenance
        )
    utilizations = [
        row["utilization"] for row in evaluations
        if isinstance(row.get("utilization"), (int, float))
    ]
    return BlockResult.ok(
        data={
            "rows": evaluations,
            "worst_utilization": max(utilizations) if utilizations else None,
        },
        provenance=provenance,
    )


@report_block(
    key="limits.breaches", title="Limit breaches", shape=BlockShape.ITEMS,
    domain="limits",
    description="Limits currently in breach or warning, plus any that could not be evaluated.",
)
def limits_breaches(ctx: BlockContext) -> BlockResult:
    evaluations, provenance = _load_latest_evaluations(ctx)
    if evaluations is None:
        return BlockResult.unavailable(_NO_MONITORING)

    breached = [
        row for row in evaluations if (row.get("status") or "") in _BREACH_STATUSES
    ]
    indeterminate = [
        row for row in evaluations
        if (row.get("status") or "") in _INDETERMINATE_STATUSES
    ]

    if not breached and not indeterminate:
        return BlockResult.empty(
            "no limit is in breach or warning on the latest monitoring run",
            provenance=provenance,
        )

    return BlockResult.ok(
        data={
            "items": breached,
            "breach_count": len(breached),
            "indeterminate": indeterminate,
            "indeterminate_count": len(indeterminate),
        },
        provenance=provenance,
    )


@report_block(
    key="limits.incidents", title="Limit incidents", shape=BlockShape.ROWS,
    domain="limits",
    description="Limit incidents for this portfolio with their lifecycle status.",
)
def limits_incidents(ctx: BlockContext) -> BlockResult:
    incidents, provenance = _load_incidents(ctx)
    if not incidents:
        return BlockResult.empty(
            "no limit incident has been raised for this portfolio",
            provenance=provenance,
        )
    open_count = sum(
        1 for row in incidents
        if row.get("status") in ("open", "acknowledged")
    )
    return BlockResult.ok(
        data={"rows": incidents, "open_count": open_count,
              "total_count": len(incidents)},
        provenance=provenance,
    )


@report_block(
    key="scenario.latest_grid", title="Latest stress grid", shape=BlockShape.ROWS,
    domain="limits",
    description="Results of the most recent scenario test run for this portfolio.",
)
def scenario_latest_grid(ctx: BlockContext) -> BlockResult:
    run, provenance = _load_latest_scenario_run(ctx)
    if run is None:
        return BlockResult.unavailable(_NO_SCENARIO)
    results = getattr(run, "results", None) or {}
    # The runner persists `shape_results(...)`, whose grid key is "scenarios".
    scenarios = results.get("scenarios")
    if not isinstance(scenarios, list):
        # We could not READ this run — not the same as the run finding nothing.
        # `empty` here would report a clean stress test for a payload we simply
        # failed to parse, which is the reassuring answer by default.
        return BlockResult.unavailable(_UNREADABLE_SCENARIO, provenance=provenance)
    if not scenarios:
        return BlockResult.empty(
            "the scenario run stressed no positions", provenance=provenance
        )
    rows = [
        {field: entry.get(field) for field in _SCENARIO_ROW_FIELDS}
        for entry in scenarios
        if isinstance(entry, dict)
    ]
    return BlockResult.ok(
        data={
            "rows": rows,
            "baseline_value": results.get("baseline_value"),
            "worst_scenario": results.get("worst_scenario"),
            "best_scenario": results.get("best_scenario"),
        },
        provenance=provenance,
    )


__all__ = [
    "limits_utilization",
    "limits_breaches",
    "limits_incidents",
    "scenario_latest_grid",
]

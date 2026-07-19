"""Compatibility reader for server-authoritative position-limit breaches.

The dynamic-subagents pilot still consumes a list of position ids.  Numeric
truth now lives in persisted Limits evaluations, so this module deliberately
keeps its historical public function while reading the latest completed Limits
run.  The legacy ``RiskRun.metrics`` branch remains only for pre-Limits data.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LimitEvaluation, LimitMonitoringRun, RiskRun

# Only trust terminal-success runs. A freshly-queued run (metrics={}) created by the
# workflow's own scope step must NOT mask an older completed run's breach data.
_TERMINAL_STATUSES = ("completed", "completed_with_errors")


def enumerate_limit_breaches(session: Session, portfolio_id) -> list[str]:
    """Return breached position ids (as strings) from authoritative evaluations.

    The newest terminal Limits run by valuation time is authoritative.  When
    no Limits run exists, the pre-module ``RiskRun.metrics`` payload remains a
    compatibility fallback.  Returns ``[]`` when neither source records a
    breached position scope.
    """
    try:
        pid = int(portfolio_id)
    except (TypeError, ValueError):
        return []
    monitoring_run = session.execute(
        select(LimitMonitoringRun)
        .where(LimitMonitoringRun.portfolio_id == pid)
        .where(
            LimitMonitoringRun.status.in_(
                ("completed", "completed_with_unknowns")
            )
        )
        .order_by(
            LimitMonitoringRun.valuation_as_of.desc(),
            LimitMonitoringRun.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if isinstance(monitoring_run, LimitMonitoringRun):
        rows = session.execute(
            select(LimitEvaluation.scope_key)
            .where(LimitEvaluation.monitoring_run_id == monitoring_run.id)
            .where(LimitEvaluation.status == "breach")
            .where(LimitEvaluation.scope_type == "position")
            .order_by(LimitEvaluation.id.asc())
        ).scalars()
        out: list[str] = []
        for scope_key in rows:
            if not isinstance(scope_key, str):
                continue
            value = scope_key.removeprefix("position:")
            if value:
                out.append(value)
        return list(dict.fromkeys(out))

    run = session.execute(
        select(RiskRun)
        .where(RiskRun.portfolio_id == pid)
        .where(RiskRun.status.in_(_TERMINAL_STATUSES))
        .order_by(RiskRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None or not isinstance(run.metrics, dict):
        return []
    raw = run.metrics.get("limit_breaches") or run.metrics.get("breaches") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        value = item.get("position_id") if isinstance(item, dict) else item
        if value is not None:
            out.append(str(value))
    return list(dict.fromkeys(out))  # de-dupe, preserve order

"""Deterministic, server-side enumeration of limit breaches for a portfolio.

Scope for the dynamic-subagents pilot is server-authoritative: it is derived from the
persisted latest ``RiskRun`` artifact, never from model-supplied text. This keeps the
fan-out coverage guarantee (``reconcile_fanout_coverage``) honest — the model cannot
shrink coverage by omitting ids, because it never supplies the breach list.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RiskRun


def enumerate_limit_breaches(session: Session, portfolio_id) -> list[str]:
    """Return breached position ids (as strings) from a portfolio's latest RiskRun.

    Reads ``RiskRun.metrics['limit_breaches']`` (or ``['breaches']``), tolerating both
    a list of ids and a list of ``{"position_id": ...}`` dicts. Returns ``[]`` when
    there is no run or no recorded breaches.

    NOTE (pilot follow-up): the batch-pricing producer does not yet persist a
    ``limit_breaches`` artifact (a portfolio risk-limits system is out of scope for the
    pilot). Until it does, this returns ``[]`` for real runs — an *honest empty* report,
    never false coverage. Populating ``metrics['limit_breaches']`` in the risk producer
    is the required follow-up to make the pilot surface breaches end-to-end.
    """
    try:
        pid = int(portfolio_id)
    except (TypeError, ValueError):
        return []
    run = session.execute(
        select(RiskRun).where(RiskRun.portfolio_id == pid).order_by(RiskRun.id.desc()).limit(1)
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

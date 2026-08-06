"""Diff two risk runs into a day-over-day change payload.

The pure entry point ``diff_metrics`` takes two ``risk_runs.metrics``
dictionaries so it can be tested without a database. ``load_run_pair`` is the
thin ORM boundary that resolves which two runs to compare.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import RiskRun

# Totals we diff. Restricted to an explicit list so a new metric appearing in
# QuantArk output does not silently widen every report.
_TOTALS_KEYS: tuple[str, ...] = (
    "market_value",
    "pnl",
    "gross_notional",
    "delta",
    "gamma",
    "delta_cash",
    "gamma_cash",
    "vega",
    "theta",
    "rho",
    "rho_q",
    "one_day_var_proxy",
)

_MARKET_KEYS: tuple[str, ...] = ("spot", "volatility", "rate", "dividend_yield")

_COMPLETED_STATUSES = ("completed", "completed_with_errors")


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _change(before: Any, after: Any) -> dict[str, Any]:
    b = _float(before)
    a = _float(after)
    change = None if (b is None or a is None) else a - b
    pct = None
    if b not in (None, 0.0) and change is not None:
        pct = change / abs(b) * 100.0
    return {"before": b, "after": a, "change": change, "pct_change": pct}


def _parse_instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _positions_by_id(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows = metrics.get("positions") or []
    return {
        int(row["position_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("position_id") is not None
    }


def _market_by_id(metrics: dict[str, Any]) -> dict[int, dict[str, Any]]:
    manifest = (
        (metrics.get("source_metadata") or {}).get("market_evidence_manifest") or {}
    )
    out: dict[int, dict[str, Any]] = {}
    for entry in manifest.get("positions") or []:
        if not isinstance(entry, dict) or entry.get("position_id") is None:
            continue
        resolved = entry.get("resolved_market") or {}
        if isinstance(resolved, dict):
            out[int(entry["position_id"])] = resolved
    return out


def _coverage(metrics: dict[str, Any]) -> dict[str, int | None]:
    coverage = metrics.get("coverage") or {}
    return {
        "priced": coverage.get("coverage_count"),
        "total": coverage.get("total_count"),
    }


def diff_metrics(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two ``risk_runs.metrics`` payloads.

    Positions are matched by ``position_id``; a position present in only one
    run appears in ``membership`` and is deliberately absent from
    ``positions``, so an opened or closed trade never reads as a price move.
    """
    before_positions = _positions_by_id(before)
    after_positions = _positions_by_id(after)
    before_ids = set(before_positions)
    after_ids = set(after_positions)
    held = sorted(before_ids & after_ids)

    before_totals = before.get("totals") or {}
    after_totals = after.get("totals") or {}
    totals = {
        key: _change(before_totals.get(key), after_totals.get(key))
        for key in _TOTALS_KEYS
        if key in before_totals or key in after_totals
    }

    positions = {
        pid: _change(
            before_positions[pid].get("market_value"),
            after_positions[pid].get("market_value"),
        )
        for pid in held
    }

    before_market = _market_by_id(before)
    after_market = _market_by_id(after)
    market: dict[int, dict[str, Any]] = {}
    for pid in held:
        b_mkt = before_market.get(pid) or {}
        a_mkt = after_market.get(pid) or {}
        if not b_mkt and not a_mkt:
            continue
        market[pid] = {
            key: _change(b_mkt.get(key), a_mkt.get(key)) for key in _MARKET_KEYS
        }

    before_coverage = _coverage(before)
    after_coverage = _coverage(after)
    regressed = (
        before_coverage["priced"] is not None
        and after_coverage["priced"] is not None
        and after_coverage["priced"] < before_coverage["priced"]
    )

    before_as_of = _parse_instant(before.get("valuation_as_of"))
    after_as_of = _parse_instant(after.get("valuation_as_of"))
    elapsed_days = 0.0
    if before_as_of is not None and after_as_of is not None:
        elapsed_days = (after_as_of - before_as_of).total_seconds() / 86400.0

    return {
        "totals": totals,
        "positions": positions,
        "membership": {
            "added": sorted(after_ids - before_ids),
            "removed": sorted(before_ids - after_ids),
            "held": held,
        },
        "coverage": {
            "before": before_coverage,
            "after": after_coverage,
            "regressed": regressed,
        },
        "market": market,
        "as_of": {
            "before": before.get("valuation_as_of"),
            "after": after.get("valuation_as_of"),
            "elapsed_days": elapsed_days,
        },
        "composition_changed": (
            before.get("position_set_hash") != after.get("position_set_hash")
        ),
    }


def load_run_pair(
    *,
    portfolio_id: int,
    risk_run_id: int | None = None,
    compare_to_run_id: int | None = None,
    session: Session | None = None,
) -> tuple[RiskRun | None, RiskRun | None]:
    """Resolve the (before, after) risk runs to diff.

    ``after`` defaults to the latest completed run for the portfolio.
    ``before`` defaults to the run immediately preceding ``after``. Either may
    be ``None`` — a portfolio with a single run has no comparison, which the
    caller must surface as ``unavailable`` rather than as a zero change.
    """
    with _session_scope(session) as sess:
        base = sess.query(RiskRun).filter(
            RiskRun.portfolio_id == portfolio_id,
            RiskRun.status.in_(_COMPLETED_STATUSES),
        )
        if risk_run_id is not None:
            after = sess.get(RiskRun, risk_run_id)
        else:
            after = base.order_by(
                RiskRun.created_at.desc(), RiskRun.id.desc()
            ).first()
        if after is None:
            return None, None

        if compare_to_run_id is not None:
            return sess.get(RiskRun, compare_to_run_id), after

        before = (
            base.filter(RiskRun.id != after.id)
            .filter(RiskRun.created_at <= after.created_at)
            .order_by(RiskRun.created_at.desc(), RiskRun.id.desc())
            .first()
        )
        return before, after


__all__ = ["diff_metrics", "load_run_pair"]

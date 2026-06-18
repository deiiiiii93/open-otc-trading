# backend/app/services/hedging_greeks.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import Instrument, Position, PricingParameterRow, RiskRun
from .domains import risk as risk_svc
from .pricing_profiles import resolve_underlying_market_params


def aggregate_by_underlying(session: Session, *, portfolio_id: int) -> dict[str, Any]:
    """Per-underlying {delta_cash, gamma_cash, vega} from the latest usable RiskRun
    (completed or completed_with_errors; only greeks_ok rows aggregate).

    Returns {"status": "ok"|"no_risk_run", "risk_run_id", "created_at", "stale",
             "underlyings": [{"underlying", "targets": {...}, "spot"}]}.

    ``stale`` is True when the run was created before today (UTC, matching the
    clock ``created_at`` is stored on) — a cue to re-run risk before hedging.
    """
    run: RiskRun | None = risk_svc.get_latest_run(portfolio_id=portfolio_id, session=session)
    if run is None:
        return {"status": "no_risk_run", "portfolio_id": portfolio_id,
                "message": "No completed risk run for this portfolio. Run risk first."}
    rows = (run.metrics or {}).get("positions", [])
    rollup_targets = _hedge_rollup_targets(session, rows)
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("greeks_ok"):
            continue
        raw_underlying = row.get("underlying") or "UNKNOWN"
        u = rollup_targets.get(_row_position_id(row)) or raw_underlying
        remapped = u != raw_underlying
        bucket = acc.setdefault(u, {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "spot": None})
        bucket["delta"] += float(row.get("delta_cash", 0.0) or 0.0)
        bucket["gamma"] += float(row.get("gamma_cash", 0.0) or 0.0)
        bucket["vega"] += float(row.get("vega", 0.0) or 0.0)
        if bucket["spot"] is None and row.get("spot") and not remapped:
            bucket["spot"] = float(row["spot"])

    profile_id = run.pricing_parameter_profile_id
    profile_rows = (
        session.query(PricingParameterRow)
        .filter(PricingParameterRow.profile_id == profile_id)
        .all()
        if profile_id is not None
        else []
    )

    underlyings = []
    for u, v in sorted(acc.items()):
        market, params_ok, missing_params = _resolve_market(
            u, v["spot"], profile_id, profile_rows)
        underlyings.append({
            "underlying": u,
            "targets": {"delta": v["delta"], "gamma": v["gamma"], "vega": v["vega"]},
            "spot": v["spot"],
            "market": market,
            "params_ok": params_ok,
            "missing_params": missing_params,
        })
    stale = bool(run.created_at and run.created_at.date() < datetime.utcnow().date())
    return {"status": "ok", "portfolio_id": portfolio_id, "risk_run_id": run.id,
            "pricing_parameter_profile_id": profile_id,
            "created_at": run.created_at.isoformat(), "stale": stale,
            "underlyings": underlyings}


def _row_position_id(row: dict[str, Any]) -> int | None:
    try:
        value = row.get("position_id")
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_hedge_position(position: Position) -> bool:
    payload = position.source_payload if isinstance(position.source_payload, dict) else {}
    hedge = payload.get("hedge") if isinstance(payload, dict) else None
    if isinstance(hedge, dict) and hedge.get("is_hedge"):
        return True
    return str(position.source_trade_id or "").startswith("HEDGE:")


def _hedged_underlying_from_payload(position: Position) -> str | None:
    payload = position.source_payload if isinstance(position.source_payload, dict) else {}
    hedge = payload.get("hedge") if isinstance(payload, dict) else None
    if not isinstance(hedge, dict):
        return None
    value = str(hedge.get("hedged_underlying") or "").strip()
    return value or None


def _hedge_rollup_targets(
    session: Session,
    risk_rows: list[dict[str, Any]],
) -> dict[int, str]:
    """Map hedge position ids to the original underlying they hedge."""
    position_ids = {
        position_id
        for row in risk_rows
        if (position_id := _row_position_id(row)) is not None
    }
    if not position_ids:
        return {}

    positions = (
        session.query(Position)
        .filter(Position.id.in_(position_ids))
        .all()
    )
    hedge_positions = [position for position in positions if _is_hedge_position(position)]
    if not hedge_positions:
        return {}

    instrument_ids = {
        position.underlying_id
        for position in hedge_positions
        if position.underlying_id is not None
    }
    instruments = {
        row.id: row
        for row in session.query(Instrument)
        .filter(Instrument.id.in_(instrument_ids))
        .all()
    } if instrument_ids else {}
    parent_ids = {
        instrument.parent_id
        for instrument in instruments.values()
        if instrument.parent_id is not None
    }
    parent_symbols = {
        row.id: row.symbol
        for row in session.query(Instrument.id, Instrument.symbol)
        .filter(Instrument.id.in_(parent_ids))
        .all()
    } if parent_ids else {}

    targets: dict[int, str] = {}
    for position in hedge_positions:
        target = _hedged_underlying_from_payload(position)
        if target is None and position.underlying_id is not None:
            instrument = instruments.get(position.underlying_id)
            if instrument is not None and instrument.parent_id is not None:
                target = parent_symbols.get(instrument.parent_id)
        if target:
            targets[position.id] = target
    return targets


def _resolve_market(
    underlying: str, spot: Any, profile_id: int | None,
    profile_rows: list[PricingParameterRow],
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    """Per-underlying option-pricing market from the run's profile, or a refusal.

    Returns (market | None, params_ok, missing_params). ``market`` carries the full
    spot+r+q+vol only when the profile supplies an unambiguous (rate, div, vol);
    otherwise the option legs for this underlying will refuse to price.
    """
    if profile_id is None:
        return None, False, ["risk run not priced under a pricing parameter profile"]
    params = resolve_underlying_market_params(profile_rows, underlying)
    if not params.ok:
        reasons = [f"{f} (missing)" for f in params.missing_fields]
        reasons += [f"{f} (ambiguous)" for f in params.ambiguous_fields]
        return None, False, reasons
    market = {
        "spot": float(spot) if spot is not None else None,
        "rate": params.rate,
        "dividend_yield": params.dividend_yield,
        "volatility": params.volatility,
    }
    return market, True, []

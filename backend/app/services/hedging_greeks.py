# backend/app/services/hedging_greeks.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import PricingParameterRow, RiskRun
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
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("greeks_ok"):
            continue
        u = row.get("underlying") or "UNKNOWN"
        bucket = acc.setdefault(u, {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "spot": None})
        bucket["delta"] += float(row.get("delta_cash", 0.0) or 0.0)
        bucket["gamma"] += float(row.get("gamma_cash", 0.0) or 0.0)
        bucket["vega"] += float(row.get("vega", 0.0) or 0.0)
        if bucket["spot"] is None and row.get("spot"):
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

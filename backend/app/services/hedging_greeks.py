# backend/app/services/hedging_greeks.py
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Instrument,
    Portfolio,
    Position,
    PricingParameterRow,
    RiskRun,
    TaskRun,
    TaskStatus,
)
from .portfolio_membership import resolve_positions
from .pricing_profiles import resolve_underlying_market_params
from .quantark import closed_position_exclusion


def _utc_timestamp(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def resolved_position_set_hash(positions: list[Position]) -> str:
    """Hash the exact economically-open position objects used by a risk run."""
    payload = [
        {
            "id": position.id,
            "version": int(position.version or 1),
            "status": position.status,
            "quantity": float(position.quantity or 0.0),
            "updated_at": (
                position.updated_at.isoformat() if position.updated_at else None
            ),
        }
        for position in sorted(positions, key=lambda item: int(item.id or 0))
    ]
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _resolved_open_positions(session: Session, *, portfolio_id: int) -> list[Position]:
    session.flush()
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")

    # ``resolve_positions`` intentionally uses the container relationship, which
    # may already be loaded in a long-lived service session. Refresh those
    # collections so a just-booked hedge cannot be hidden by SQLAlchemy's
    # identity-map cache on a subsequent solve in the same unit of work.
    visited: set[int] = set()

    def _expire_container_membership(candidate: Portfolio) -> None:
        if candidate.id in visited:
            return
        visited.add(candidate.id)
        if candidate.kind == "container":
            session.expire(candidate, ["positions"])
            return
        for source_id in candidate.source_portfolio_ids or []:
            source = session.get(Portfolio, source_id)
            if source is not None:
                _expire_container_membership(source)

    _expire_container_membership(portfolio)
    return [
        position
        for position in resolve_positions(portfolio, session)
        if closed_position_exclusion(position) is None
    ]


def position_set_hash(session: Session, *, portfolio_id: int) -> str:
    """Hash current resolved membership, excluding economically closed positions."""
    return resolved_position_set_hash(
        _resolved_open_positions(session, portfolio_id=portfolio_id)
    )


def _legacy_position_set_matches(
    run: RiskRun,
    current_positions: list[Position],
) -> bool:
    """Best-effort compatibility for pre-fingerprint RiskRun rows.

    Empty synthetic/manual runs remain usable for empty portfolios. For non-empty
    books, the run must identify the same positions and any persisted row
    quantities must still match. Position updates after run creation fail closed
    because older rows cannot prove which economics were calculated.
    """
    rows = list((run.metrics or {}).get("positions") or [])
    current_by_id = {
        int(position.id): position
        for position in current_positions
        if position.id is not None
    }

    expected_ids: set[int] | None = None
    if run.resolved_position_ids is not None:
        try:
            expected_ids = {int(position_id) for position_id in run.resolved_position_ids}
        except (TypeError, ValueError):
            return False
    else:
        row_ids: list[int] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("position_id") is None:
                continue
            try:
                row_ids.append(int(row["position_id"]))
            except (TypeError, ValueError):
                return False
        if row_ids:
            expected_ids = set(row_ids)

    if expected_ids is None:
        return not current_by_id
    if expected_ids != set(current_by_id):
        return False

    for row in rows:
        if not isinstance(row, dict) or row.get("position_id") is None:
            continue
        try:
            position_id = int(row["position_id"])
        except (TypeError, ValueError):
            return False
        if "quantity" not in row:
            continue
        try:
            if float(row["quantity"]) != float(current_by_id[position_id].quantity):
                return False
        except (KeyError, TypeError, ValueError):
            return False

    if run.created_at is not None:
        for position in current_positions:
            if position.updated_at is not None and position.updated_at > run.created_at:
                return False
    return True


def risk_run_time_metadata(
    session: Session,
    run: RiskRun,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    anchor = now or datetime.utcnow()
    finished_row = (
        session.query(TaskRun.finished_at)
        .filter(
            TaskRun.risk_run_id == run.id,
            TaskRun.finished_at.isnot(None),
            TaskRun.status.in_((
                TaskStatus.COMPLETED.value,
                TaskStatus.COMPLETED_WITH_ERRORS.value,
            )),
        )
        .order_by(TaskRun.finished_at.desc(), TaskRun.id.desc())
        .first()
    )
    finished_at = finished_row[0] if finished_row is not None else None
    generated = finished_at or run.created_at or anchor
    valuation_raw = (run.metrics or {}).get("valuation_as_of")
    parsed_valuation = _parse_datetime(valuation_raw)
    valuation = parsed_valuation or generated
    max_age = int(get_settings().hedge_risk_max_age_seconds)
    age_seconds = max(0.0, (anchor - generated).total_seconds())
    expires_at = generated + timedelta(seconds=max_age)
    stale_reasons: list[str] = []
    if age_seconds > max_age:
        stale_reasons.append("risk_age_exceeded")
    if valuation_raw is not None and parsed_valuation is None:
        stale_reasons.append("valuation_time_invalid")
    elif valuation.date() < anchor.date():
        stale_reasons.append("historical_valuation")
    elif valuation.date() > anchor.date():
        stale_reasons.append("future_valuation")
    return {
        "valuation_as_of": _utc_timestamp(valuation),
        "risk_generated_at": _utc_timestamp(generated),
        "age_seconds": age_seconds,
        "expires_at": _utc_timestamp(expires_at),
        "stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "freshness_policy_seconds": max_age,
    }


def aggregate_by_underlying(session: Session, *, portfolio_id: int) -> dict[str, Any]:
    """Per-underlying {delta_cash, gamma_cash, vega} from the latest usable RiskRun
    (completed or completed_with_errors; only greeks_ok rows aggregate).

    Returns the risk run id, explicit generation/valuation/expiry timestamps,
    a position-set fingerprint, and per-underlying targets. ``stale`` is based
    on both the configured intraday action TTL and historical valuation dates.
    """
    from .domains import risk as risk_svc

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
    now = datetime.utcnow()
    timing = risk_run_time_metadata(session, run, now=now)
    current_positions = _resolved_open_positions(session, portfolio_id=portfolio_id)
    current_position_set_hash = resolved_position_set_hash(current_positions)
    frozen_position_set_hash = (run.metrics or {}).get("position_set_hash")
    if isinstance(frozen_position_set_hash, str) and frozen_position_set_hash.startswith(
        "sha256:"
    ):
        snapshot_matches = frozen_position_set_hash == current_position_set_hash
    else:
        frozen_position_set_hash = current_position_set_hash
        snapshot_matches = _legacy_position_set_matches(run, current_positions)
    if not snapshot_matches:
        timing["stale"] = True
        timing["stale_reasons"] = list(
            dict.fromkeys([*timing["stale_reasons"], "portfolio_snapshot_changed"])
        )
    return {"status": "ok", "portfolio_id": portfolio_id, "risk_run_id": run.id,
            "pricing_parameter_profile_id": profile_id,
            "created_at": run.created_at.isoformat(),
            "generated_at": _utc_timestamp(now),
            "position_set_hash": frozen_position_set_hash,
            **timing,
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

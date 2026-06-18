from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Portfolio, Position, RiskRun, TaskStatus
from ..schemas import PricingEnvironmentSnapshot
from .assumptions import latest_assumption_row
from .pricing_profiles import (
    position_requires_pricing_params,
    pricing_parameter_resolution_diagnostics,
    pricing_parameter_resolution_message,
    pricing_rows_for_profile,
    resolve_pricing_parameter_row_for_position,
)
from .portfolio_membership import resolve_positions
from .quantark import (
    build_engine_for_position,
    build_pricing_env,
    build_product_for_position,
    calculate_portfolio_risk,
    gross_notional_for_position,
    ensure_quantark_path,
    closed_position_exclusion,
    market_snapshot_for_position,
    risk_pricing_exclusion,
    usable_model_value,
)


def _risk_worker_count(job_count: int) -> int:
    if job_count <= 1:
        return 1
    workers = get_settings().risk_parallel_workers
    return max(1, min(int(workers), job_count))


def _position_snapshot(position: Position) -> SimpleNamespace:
    source_payload = getattr(position, "source_payload", None)
    return SimpleNamespace(
        product_type=position.product_type,
        product_kwargs=dict(position.product_kwargs or {}),
        engine_name=position.engine_name,
        engine_kwargs=dict(position.engine_kwargs or {}),
        quantity=float(position.quantity or 0.0),
        status=getattr(position, "status", "open"),
        mapping_status=getattr(position, "mapping_status", "manual"),
        mapping_error=getattr(position, "mapping_error", None),
        source_payload=(
            dict(source_payload) if isinstance(source_payload, dict) else source_payload
        ),
    )


def _pricing_position_context(
    session: Session,
    positions: list[Position],
    *,
    pricing_parameter_profile_id: int | None = None,
    valuation_date: datetime | None = None,
) -> tuple[
    dict[int, PricingEnvironmentSnapshot],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    # Profile-row resolution only happens when a profile is selected. The
    # per-position market_snapshot_for_position(... session=session,
    # diagnostics=...) loop below runs in BOTH cases so the quote store reaches
    # the default (no-profile) risk path too (T6 review): otherwise that path
    # would build markets without a session and price off env defaults.
    pricing_rows = (
        pricing_rows_for_profile(session, profile_id=pricing_parameter_profile_id)
        if pricing_parameter_profile_id is not None
        else []
    )
    # Seed the fallback snapshot with the pinned valuation date so it reaches
    # the engines (time-to-maturity) and the quote store (as_of <= date) —
    # mirroring the sync pricer, which constructs its snapshots with an
    # explicit valuation_date. Without this, each snapshot defaults to its own
    # construction-time utcnow regardless of the caller's pin.
    fallback_market = (
        PricingEnvironmentSnapshot(valuation_date=valuation_date)
        if valuation_date is not None
        else PricingEnvironmentSnapshot()
    )
    markets: dict[int, PricingEnvironmentSnapshot] = {}
    pricing_failures: dict[int, dict[str, Any]] = {}
    pricing_diagnostics: dict[int, dict[str, Any]] = {}
    for position in positions:
        if position.id is None:
            continue
        if not position_requires_pricing_params(position):
            # Delta-one (futures/spot) engines are linear in spot and ignore
            # rate/dividend/volatility, so a profile lacking a complete param row
            # is NOT a pricing failure for them. Build the spot-driven snapshot
            # like the no-profile path (quote store + assumption fallback) and
            # record no pricing failure, regardless of whether a profile is set.
            diagnostics: dict[str, Any] = {}
            base_market = market_snapshot_for_position(
                position, fallback_market, session=session, diagnostics=diagnostics
            )
            markets[position.id] = _apply_assumption_rqv(
                session, position, base_market, valuation_date, diagnostics
            )
            diagnostics["pricing_params_required"] = False
            pricing_diagnostics[position.id] = diagnostics
            continue
        if pricing_parameter_profile_id is None:
            # No profile => no pricing-parameter row expectations, so NO pricing
            # failure for a missing row. Thread the session + diagnostics so a
            # recorded quote feeds spot and stamps market_input_source; then fold
            # the instrument-level assumption set on top for r/q/vol.
            diagnostics = {}
            base_market = market_snapshot_for_position(
                position,
                fallback_market,
                session=session,
                diagnostics=diagnostics,
            )
            markets[position.id] = _apply_assumption_rqv(
                session, position, base_market, valuation_date, diagnostics
            )
            pricing_diagnostics[position.id] = diagnostics
            continue

        resolution = resolve_pricing_parameter_row_for_position(pricing_rows, position)
        diagnostics = pricing_parameter_resolution_diagnostics(
            profile_id=pricing_parameter_profile_id,
            resolution=resolution,
        )
        pricing_diagnostics[position.id] = diagnostics
        # Spot chain (T8): thread the session so the quote store feeds the spot
        # slot; pass the diagnostics dict so a quote-sourced spot stamps
        # market_input_source/quote_age_days onto the risk row.
        base_market = market_snapshot_for_position(
            position, fallback_market, session=session, diagnostics=diagnostics
        )
        if not resolution.ok or resolution.row is None:
            # No usable trade row: fall through to the instrument-level assumption
            # set for r/q/vol before reporting the row-resolution failure.
            markets[position.id] = _apply_assumption_rqv(
                session, position, base_market, valuation_date, diagnostics
            )
            pricing_failures[position.id] = {
                "pricing_error": pricing_parameter_resolution_message(position, resolution),
                **diagnostics,
            }
            continue

        # Trade row wins for r/q/vol; spot is observation-only (quote store).
        row = resolution.row
        markets[position.id] = base_market.model_copy(
            update={
                "rate": row.rate if row.rate is not None else base_market.rate,
                "dividend_yield": (
                    row.dividend_yield
                    if row.dividend_yield is not None
                    else base_market.dividend_yield
                ),
                "volatility": (
                    row.volatility if row.volatility is not None else base_market.volatility
                ),
            }
        )
    return markets, pricing_failures, pricing_diagnostics


def _apply_assumption_rqv(
    session: Session,
    position: Position,
    base_market: PricingEnvironmentSnapshot,
    valuation_date: datetime | None,
    diagnostics: dict[str, Any],
) -> PricingEnvironmentSnapshot:
    """Fold the latest instrument-level AssumptionRow r/q/vol onto ``base_market``.

    Only fields the row actually supplies override the fallback. When the
    assumption set supplies at least one field, stamps ``market_input_source``
    and the set/row ids into ``diagnostics`` (unless a market quote already
    claimed market_input_source for spot).
    """
    underlying_id = getattr(position, "underlying_id", None)
    if underlying_id is None:
        return base_market
    as_of = valuation_date or base_market.valuation_date or datetime.utcnow()
    row = latest_assumption_row(session, underlying_id, as_of=as_of)
    if row is None:
        return base_market
    update: dict[str, Any] = {}
    if row.rate is not None:
        update["rate"] = float(row.rate)
    if row.dividend_yield is not None:
        update["dividend_yield"] = float(row.dividend_yield)
    if row.volatility is not None:
        update["volatility"] = float(row.volatility)
    if not update:
        return base_market
    # The assumption set supplies r/q/vol; it claims the source label even when a
    # market quote also supplied spot (quote_age_days, if any, is preserved).
    diagnostics["market_input_source"] = "assumption_set"
    diagnostics["assumption_set_id"] = row.set_id
    diagnostics["assumption_row_id"] = row.id
    return base_market.model_copy(update=update)


def _pricing_position_markets(
    session: Session,
    positions: list[Position],
    *,
    pricing_parameter_profile_id: int | None = None,
    valuation_date: datetime | None = None,
) -> dict[int, PricingEnvironmentSnapshot]:
    markets, _pricing_failures, _pricing_diagnostics = _pricing_position_context(
        session,
        positions,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        valuation_date=valuation_date,
    )
    return markets


def pricing_position_markets(
    session: Session,
    positions: list[Position],
    *,
    pricing_parameter_profile_id: int | None = None,
    valuation_date: datetime | None = None,
) -> dict[int, PricingEnvironmentSnapshot]:
    return _pricing_position_markets(
        session,
        positions,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        valuation_date=valuation_date,
    )


def compute_position_greeks(
    position: Position,
    market: PricingEnvironmentSnapshot,
    *,
    engine_kwargs: dict | None = None,
) -> dict[str, Any]:
    """Return per-position Greeks dict. {ok, delta, gamma, vega, theta, rho, rho_q, error?}.

    When ``engine_kwargs`` is provided it overrides ``position.engine_kwargs`` for the
    duration of this call (the position object is not mutated).
    """
    try:
        ensure_quantark_path()
        from quantark.asset.equity.riskmeasures.greeks_calculator import GreeksCalculator
    except Exception as exc:  # pragma: no cover - import guard
        return {
            "ok": False,
            "error": f"GreeksCalculator unavailable: {exc}",
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }
    try:
        target = position
        if engine_kwargs is not None:
            target = _position_snapshot(position)
            target.engine_kwargs = dict(engine_kwargs)
        product = build_product_for_position(target, market)
        engine = build_engine_for_position(target, market)
        env = build_pricing_env(market)
        calc = GreeksCalculator()
        result = calc.calculate(product, env, engine, method="auto")
        return {
            "ok": True,
            "delta": float(result.get("delta", 0.0)),
            "gamma": float(result.get("gamma", 0.0)),
            "vega": float(result.get("vega", 0.0)),
            "theta": float(result.get("theta", 0.0)),
            "rho": float(result.get("rho", 0.0)),
            "rho_q": float(result.get("dividend_rho", 0.0)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }


def compute_portfolio_greeks(
    portfolio: Portfolio,
    market: PricingEnvironmentSnapshot,
) -> dict[str, float]:
    """Aggregate Greeks across positions weighted by signed quantity."""
    totals: dict[str, float] = {
        "delta": 0.0,
        "gamma": 0.0,
        "vega": 0.0,
        "theta": 0.0,
        "rho": 0.0,
        "rho_q": 0.0,
    }
    for position in portfolio.positions:
        per = compute_position_greeks(position, market)
        if not per.get("ok"):
            continue
        for greek in totals:
            totals[greek] += per[greek] * position.quantity
    return totals


def run_portfolio_scenarios(
    portfolio: Portfolio,
    market: PricingEnvironmentSnapshot,
    spot_shifts_pct: list[float],
    vol_shifts_abs: list[float],
) -> dict[str, Any]:
    """Reprices the portfolio under a grid of (spot_shift, vol_shift)."""
    from .quantark import price_product

    positions = [
        (_position_snapshot(pos), market_snapshot_for_position(pos, market))
        for pos in list(getattr(portfolio, "positions", []) or [])
    ]

    def _position_value(pos: Position, env: PricingEnvironmentSnapshot) -> float:
        if risk_pricing_exclusion(pos):
            return 0.0
        priced = price_product(
            pos.product_type,
            pos.product_kwargs,
            env,
            pos.engine_name,
            pos.engine_kwargs,
        )
        price = float(priced.data.get("price", 0.0))
        value = price * float(pos.quantity)
        if not priced.ok or not usable_model_value(
            value, gross_notional_for_position(pos, env)
        ):
            return 0.0
        return value

    def _portfolio_value(spot_shift_pct: float, vol_shift: float) -> float:
        total = 0.0
        for pos, base_market in positions:
            shifted = base_market.model_copy(
                update={
                    "spot": base_market.spot * (1.0 + spot_shift_pct / 100.0),
                    "volatility": max(base_market.volatility + vol_shift, 1e-6),
                }
            )
            total += _position_value(pos, shifted)
        return total

    value_jobs: list[tuple[int | None, int | None, float, float]] = [
        (None, None, 0.0, 0.0)
    ]
    for row_index, vol_shift in enumerate(vol_shifts_abs):
        for column_index, spot_shift_pct in enumerate(spot_shifts_pct):
            value_jobs.append((row_index, column_index, spot_shift_pct, vol_shift))

    def _portfolio_value_job(
        job: tuple[int | None, int | None, float, float],
    ) -> tuple[int | None, int | None, float, float, float]:
        row_index, column_index, spot_shift_pct, vol_shift = job
        return (
            row_index,
            column_index,
            spot_shift_pct,
            vol_shift,
            _portfolio_value(spot_shift_pct, vol_shift),
        )

    workers = _risk_worker_count(len(value_jobs))
    if workers > 1:
        ensure_quantark_path()
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="scenario-pricer"
        ) as executor:
            values = list(executor.map(_portfolio_value_job, value_jobs))
    else:
        values = [_portfolio_value_job(job) for job in value_jobs]

    base_value = next(
        value
        for row_index, _column_index, _spot, _vol, value in values
        if row_index is None
    )
    rows: list[list[dict[str, Any]]] = [
        [{} for _spot_shift in spot_shifts_pct] for _vol_shift in vol_shifts_abs
    ]
    for row_index, column_index, spot_shift_pct, vol_shift, value in values:
        if row_index is None or column_index is None:
            continue
        rows[row_index][column_index] = {
            "spot_shift_pct": spot_shift_pct,
            "vol_shift_abs": vol_shift,
            "pnl": value - base_value,
        }
    return {"portfolio_id": portfolio.id, "base_pnl": 0.0, "cells": rows}


def run_portfolio_risk(
    session: Session,
    *,
    portfolio_id: int,
    method: str = "summary",
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
    market_snapshot_id: int | None = None,
) -> RiskRun:
    """Resolve portfolio membership (supporting views), compute risk, and persist a RiskRun."""
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    resolved = _resolve_risk_positions(
        portfolio,
        session,
        position_ids=position_ids,
    )
    # RiskRun has no explicit valuation date; pin assumption/quote resolution to a
    # single instant (utcnow) so every position in this run resolves as-of the same
    # time instead of letting each _apply_assumption_rqv default to its own utcnow.
    valuation_date = datetime.utcnow()
    position_markets, pricing_failures, pricing_diagnostics = _pricing_position_context(
        session,
        resolved,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        valuation_date=valuation_date,
    )
    portfolio_like = SimpleNamespace(
        id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        positions=resolved,
    )
    metrics = calculate_portfolio_risk(
        portfolio_like,  # type: ignore[arg-type]
        position_markets=position_markets,
        pricing_failures=pricing_failures,
        pricing_diagnostics=pricing_diagnostics,
    )
    status = _risk_status_from_metrics(metrics)
    run = RiskRun(
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        market_snapshot_id=market_snapshot_id,
        method=method,
        status=status,
        metrics=metrics,
        scenario_cells=None,
        resolved_position_ids=[p.id for p in resolved],
    )
    session.add(run)
    session.flush()
    return run


def _resolve_risk_positions(
    portfolio: Portfolio,
    session: Session,
    *,
    position_ids: list[int] | None,
) -> list[Position]:
    resolved = resolve_positions(portfolio, session)
    # Economically-closed positions never enter a risk run: no metrics row,
    # not in resolved_position_ids, cannot poison run status. Membership
    # display (resolve_positions itself) still includes them.
    open_positions = [p for p in resolved if closed_position_exclusion(p) is None]
    if position_ids is None:
        return open_positions
    requested_ids = _normalize_position_ids(position_ids)
    by_id = {position.id: position for position in resolved}
    open_ids = {position.id for position in open_positions}
    missing_ids = [position_id for position_id in requested_ids if position_id not in by_id]
    if missing_ids:
        raise ValueError(
            "Position ids are not in portfolio "
            f"{portfolio.id}: {', '.join(str(position_id) for position_id in missing_ids)}"
        )
    # Closed positions are silently filtered (user decision 2026-06-05);
    # foreign ids still error above. All-requested-closed yields an empty,
    # plainly-completed run with empty metrics — honest, if unusual.
    return [by_id[position_id] for position_id in requested_ids if position_id in open_ids]


def _normalize_position_ids(position_ids: list[int]) -> list[int]:
    normalized: list[int] = []
    for raw_id in position_ids:
        position_id = int(raw_id)
        if position_id <= 0:
            raise ValueError("position_ids must contain positive ids")
        if position_id not in normalized:
            normalized.append(position_id)
    if not normalized:
        raise ValueError("position_ids must not be empty")
    return normalized


def _risk_status_from_metrics(metrics: dict[str, Any]) -> str:
    for row in metrics.get("positions", []) or []:
        if not row.get("pricing_ok") or not row.get("greeks_ok"):
            return TaskStatus.COMPLETED_WITH_ERRORS.value
    return TaskStatus.COMPLETED.value


def _risk_completion_message(metrics: dict[str, Any], status: str) -> str:
    rows = list(metrics.get("positions", []) or [])
    if status == TaskStatus.COMPLETED_WITH_ERRORS.value:
        failed = sum(
            1 for row in rows if not row.get("pricing_ok") or not row.get("greeks_ok")
        )
        return f"Completed with {failed} position issue{'s' if failed != 1 else ''}"
    return f"Completed {len(rows)} positions"


def _risk_error_payload(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """Structured per-position failure summary for completed_with_errors tasks.

    Returns ``None`` when no position failed, so successful runs leave
    ``task.result_payload`` empty.
    """
    failing = [
        {
            "position_id": row.get("position_id"),
            "underlying": row.get("underlying"),
            "product_type": row.get("product_type"),
            "pricing_ok": bool(row.get("pricing_ok")),
            "pricing_error": row.get("pricing_error"),
            "greeks_ok": bool(row.get("greeks_ok")),
            "greeks_error": row.get("greeks_error"),
        }
        for row in (metrics.get("positions") or [])
        if not row.get("pricing_ok") or not row.get("greeks_ok")
    ]
    if not failing:
        return None
    return {
        "errors": {
            "kind": "risk_run",
            "failed_count": len(failing),
            "positions": failing,
        }
    }

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..models import (
    GreekLandscapeRun,
    Portfolio,
    PricingParameterProfile,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from .engine_configs import get_engine_config, position_with_engine, resolve_pricing_engine
from .quantark import build_engine_for_position, build_pricing_env, build_product_for_position
from .risk_engine import _pricing_position_context, _resolve_risk_positions
from .task_runner import mark_task_finished, mark_task_running, update_task_progress


def queue_greeks_landscape(
    session: Session,
    *,
    portfolio_id: int,
    spot_min_pct: float = -30.0,
    spot_max_pct: float = 30.0,
    spot_nodes: int = 61,
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
) -> tuple[GreekLandscapeRun, TaskRun]:
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    minimum, maximum, nodes = _validate_grid(spot_min_pct, spot_max_pct, spot_nodes)
    get_engine_config(session, engine_config_id)
    resolved_ids = None
    if position_ids is not None:
        resolved_ids = [
            position.id
            for position in _resolve_risk_positions(portfolio, session, position_ids=position_ids)
        ]
    run = GreekLandscapeRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        status=TaskStatus.QUEUED.value,
        config={"spot_min_pct": minimum, "spot_max_pct": maximum, "spot_nodes": nodes},
        results={},
        excluded_positions=[],
        resolved_position_ids=resolved_ids,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.GREEKS_LANDSCAPE.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        greeks_landscape_run_id=run.id,
        message="Queued Greeks landscape run",
    )
    session.add(task)
    session.flush()
    return run, task


def execute_greeks_landscape_task(
    task_id: int,
    run_id: int,
    session_factory: sessionmaker | None = None,
) -> None:
    session = (session_factory or database.SessionLocal)()
    try:
        _execute_greeks_landscape_task(session, task_id, run_id)
    finally:
        session.close()


def _execute_greeks_landscape_task(session: Session, task_id: int, run_id: int) -> None:
    try:
        run = session.get(GreekLandscapeRun, run_id)
        if run is None:
            raise ValueError(f"Greeks landscape run not found: {run_id}")
        portfolio = session.get(Portfolio, run.portfolio_id)
        if portfolio is None:
            raise ValueError(f"Portfolio not found: {run.portfolio_id}")
        positions = _resolve_risk_positions(
            portfolio, session, position_ids=run.resolved_position_ids
        )
        run.resolved_position_ids = [position.id for position in positions]
        mark_task_running(session, task_id, message=f"Calculating {len(positions)} position landscapes", total=len(positions))
        run.status = TaskStatus.RUNNING.value
        session.commit()

        valuation_date = run.created_at
        if run.pricing_parameter_profile_id is not None:
            profile = session.get(PricingParameterProfile, run.pricing_parameter_profile_id)
            if profile is not None and profile.valuation_date is not None:
                valuation_date = profile.valuation_date
        markets, pricing_failures, diagnostics = _pricing_position_context(
            session,
            positions,
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            valuation_date=valuation_date,
        )
        engine_config = get_engine_config(session, run.engine_config_id)
        shifts = np.linspace(
            run.config["spot_min_pct"], run.config["spot_max_pct"], run.config["spot_nodes"]
        ).tolist()
        position_rows: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for index, position in enumerate(positions, start=1):
            try:
                if position.id in pricing_failures:
                    raise ValueError(pricing_failures[position.id]["pricing_error"])
                resolved_engine = resolve_pricing_engine(position, engine_config)
                target = position_with_engine(position, resolved_engine)
                market = markets[position.id]
                base_spot = float(market.spot)
                spot_levels = [base_spot * (1.0 + shift / 100.0) for shift in shifts]
                product = build_product_for_position(target, market)
                engine = build_engine_for_position(target, market)
                curve = _calculate_spot_greeks_curve(engine, product, market, spot_levels)
                raw, cash = [], []
                quantity = float(position.quantity)
                for shift, point in zip(shifts, curve, strict=True):
                    spot = float(point["spot"])
                    delta = float(point["delta"]) * quantity
                    gamma = float(point["gamma"]) * quantity
                    raw.append({"spot_shift_pct": shift, "spot": spot, "delta": delta, "gamma": gamma})
                    cash.append({
                        "spot_shift_pct": shift,
                        "spot": spot,
                        "delta_cash": delta * spot,
                        "gamma_cash": gamma * spot * spot / 100.0,
                    })
                position_rows.append({
                    "position_id": position.id,
                    "source_trade_id": position.source_trade_id,
                    "underlying": position.underlying,
                    "currency": position.currency,
                    "engine_name": target.engine_name,
                    "calculation_mode": curve[0]["calculation_mode"] if curve else "none",
                    "diagnostics": diagnostics.get(position.id, {}),
                    "curves": {"raw": raw, "cash": cash},
                })
            except Exception as exc:
                excluded.append({"position_id": position.id, "underlying": position.underlying, "reason": str(exc)})
            update_task_progress(session, task_id, current=index, total=len(positions))
            session.commit()

        results = _aggregate(position_rows, shifts)
        results["valuation_as_of"] = valuation_date.isoformat()
        run.results = results
        run.excluded_positions = excluded
        status = TaskStatus.COMPLETED_WITH_ERRORS.value if excluded else TaskStatus.COMPLETED.value
        run.status = status
        mark_task_finished(
            session,
            task_id,
            status=status,
            message=f"Completed {len(position_rows)} position landscapes",
            result_payload={"greeks_landscape_run_id": run.id},
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(GreekLandscapeRun, run_id)
        if run is not None:
            run.status = TaskStatus.FAILED.value
            run.results = {"error": str(exc)}
        mark_task_finished(session, task_id, status=TaskStatus.FAILED.value, error=str(exc))
        session.commit()


def _validate_grid(minimum: float, maximum: float, nodes: int) -> tuple[float, float, int]:
    minimum, maximum, nodes = float(minimum), float(maximum), int(nodes)
    if minimum >= maximum:
        raise ValueError("spot_min_pct must be less than spot_max_pct")
    if minimum > 0 or maximum < 0:
        raise ValueError("spot range must include 0")
    if nodes < 3 or nodes > 501:
        raise ValueError("spot_nodes must be between 3 and 501")
    if 1.0 + minimum / 100.0 <= 0:
        raise ValueError("spot_min_pct must keep scenario spots positive")
    return minimum, maximum, nodes


def _calculate_spot_greeks_curve(
    engine: Any,
    product: Any,
    market: Any,
    spot_levels: list[float],
) -> list[dict[str, Any]]:
    native = getattr(engine, "calculate_spot_greeks_curve", None)
    if callable(native):
        return native(product, build_pricing_env(market), spot_levels)

    curve = []
    for spot in spot_levels:
        shifted_market = market.model_copy(update={"spot": spot})
        greeks = engine.calculate_greeks(product, build_pricing_env(shifted_market))
        curve.append(
            {
                "spot": spot,
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "calculation_mode": "point_greeks",
            }
        )
    return curve


def _aggregate(positions: list[dict[str, Any]], shifts: list[float]) -> dict[str, Any]:
    def empty_raw() -> list[dict[str, float]]:
        return [{"spot_shift_pct": shift, "delta": 0.0, "gamma": 0.0} for shift in shifts]

    def empty_cash() -> list[dict[str, float]]:
        return [{"spot_shift_pct": shift, "delta_cash": 0.0, "gamma_cash": 0.0} for shift in shifts]

    portfolio_raw = empty_raw()
    by_underlying: dict[str, dict[str, Any]] = {}
    cash_by_currency: dict[str, list[dict[str, float]]] = {}
    for position in positions:
        underlying = by_underlying.setdefault(
            position["underlying"], {"raw": empty_raw(), "cash_by_currency": {}}
        )
        currency = position["currency"]
        portfolio_cash = cash_by_currency.setdefault(currency, empty_cash())
        underlying_cash = underlying["cash_by_currency"].setdefault(currency, empty_cash())
        for index, raw in enumerate(position["curves"]["raw"]):
            for target in (portfolio_raw[index], underlying["raw"][index]):
                target["delta"] += raw["delta"]
                target["gamma"] += raw["gamma"]
        for index, cash in enumerate(position["curves"]["cash"]):
            for target in (portfolio_cash[index], underlying_cash[index]):
                target["delta_cash"] += cash["delta_cash"]
                target["gamma_cash"] += cash["gamma_cash"]
    return {
        "spot_shifts_pct": shifts,
        "positions": positions,
        "portfolio": {"raw": portfolio_raw, "cash_by_currency": cash_by_currency},
        "by_underlying": by_underlying,
    }

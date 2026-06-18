"""Unified batch pricing.

One pricing pass (``calculate_portfolio_risk``, Greeks included) persists BOTH
outputs: the ``RiskRun`` metrics (Risk page) and a ``PositionValuationRun``
with per-position results (Positions page). Replaces the separate
``position_pricing`` and ``risk_run`` task paths.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import get_settings
from ..models import (
    Portfolio,
    Position,
    PositionValuationResult,
    PositionValuationRun,
    PricingParameterProfile,
    RiskRun,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from ..schemas import PricingEnvironmentSnapshot
from .quantark import RISK_GREEK_KEYS, calculate_portfolio_risk
from .risk_engine import (
    _pricing_position_context,
    _resolve_risk_positions,
    _risk_completion_message,
    _risk_error_payload,
    _risk_status_from_metrics,
)
from .task_runner import (
    mark_task_finished,
    mark_task_running,
    update_task_progress,
)


def queue_batch_pricing(
    session: Session,
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    market_snapshot_id: int | None = None,
    method: str = "summary",
) -> tuple[RiskRun, TaskRun]:
    """Create a queued RiskRun + TaskRun for a batch pricing pass; the executor fills metrics and writes the valuation run."""
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    if engine_config_id is not None:
        from .engine_configs import get_engine_config

        get_engine_config(session, engine_config_id)
    scoped_position_ids: list[int] | None = None
    if position_ids is not None:
        scoped_position_ids = [
            position.id
            for position in _resolve_risk_positions(
                portfolio,
                session,
                position_ids=position_ids,
            )
        ]
    run = RiskRun(
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        market_snapshot_id=market_snapshot_id,
        method=method,
        status=TaskStatus.QUEUED.value,
        metrics={},
        scenario_cells=None,
        resolved_position_ids=scoped_position_ids,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.BATCH_PRICING.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio.id,
        risk_run_id=run.id,
        progress_current=0,
        progress_total=0,
        message="Queued batch pricing run",
    )
    session.add(task)
    session.flush()
    return run, task


def execute_batch_pricing_task(
    task_id: int,
    risk_run_id: int,
    session_factory: sessionmaker | None = None,
) -> None:
    session = (session_factory or database.SessionLocal)()
    try:
        _execute_batch_pricing_task(session, task_id, risk_run_id)
    finally:
        session.close()


def _execute_batch_pricing_task(
    session: Session, task_id: int, risk_run_id: int
) -> None:
    try:
        run = session.get(RiskRun, risk_run_id)
        if run is None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                error=f"Risk run not found: {risk_run_id}",
            )
            session.commit()
            return
        portfolio = session.get(Portfolio, run.portfolio_id)
        if portfolio is None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                error=f"Portfolio not found: {run.portfolio_id}",
            )
            session.commit()
            return

        # Queue-time value: None = full portfolio, list = user-scoped subset.
        # Captured before the rewrite below so the valuation run can record
        # the scoping in overrides (the Positions page keys full-portfolio
        # header summaries off overrides.position_ids).
        scoped_position_ids = run.resolved_position_ids
        resolved = _resolve_risk_positions(
            portfolio,
            session,
            position_ids=run.resolved_position_ids,
        )
        position_ids = [p.id for p in resolved]
        run.resolved_position_ids = position_ids
        total = len(position_ids)
        mark_task_running(
            session,
            task_id,
            message=f"Pricing {total} positions",
            total=total,
        )
        session.commit()

        def _progress(current: int, total_positions: int) -> None:
            update_task_progress(
                session,
                task_id,
                current=current,
                total=total_positions,
                message=f"Priced {current} of {total_positions} positions",
            )
            session.commit()

        # Profile-bound runs price as-of the profile's valuation date: the
        # profile supplies r/q/vol AND the date those parameters were observed,
        # so maturities, quote as-of, and the persisted valuation-run stamp all
        # follow it (historical repricing). Unbound runs keep the old contract:
        # as-of queue time (RiskRun has no valuation_date column; created_at).
        valuation_as_of = run.created_at
        if run.pricing_parameter_profile_id is not None:
            profile = session.get(
                PricingParameterProfile, run.pricing_parameter_profile_id
            )
            if profile is not None and profile.valuation_date is not None:
                valuation_as_of = profile.valuation_date

        position_markets, pricing_failures, pricing_diagnostics = (
            _pricing_position_context(
                session,
                resolved,
                pricing_parameter_profile_id=run.pricing_parameter_profile_id,
                valuation_date=valuation_as_of,
            )
        )
        from .engine_configs import get_engine_config, position_with_engine, resolve_pricing_engine

        engine_config = get_engine_config(session, run.engine_config_id)
        resolved_for_risk = []
        for position in resolved:
            try:
                engine = resolve_pricing_engine(position, engine_config)
                wrapped = position_with_engine(position, engine)
                pricing_diagnostics.setdefault(position.id, {})["resolved_engine"] = engine.diagnostics()
                resolved_for_risk.append(wrapped)
            except ValueError as exc:
                pricing_failures[position.id] = {"pricing_error": str(exc)}
                resolved_for_risk.append(position)
        portfolio_like = SimpleNamespace(
            id=portfolio.id,
            name=portfolio.name,
            base_currency=portfolio.base_currency,
            positions=resolved_for_risk,
        )
        metrics = calculate_portfolio_risk(
            portfolio_like,  # type: ignore[arg-type]
            position_markets=position_markets,
            pricing_failures=pricing_failures,
            pricing_diagnostics=pricing_diagnostics,
            max_workers=get_settings().risk_parallel_workers,
            progress_callback=_progress,
        )
        # Stamp the pricing as-of into the persisted metrics so downstream
        # latest-risk consumers (agent get_latest_risk_run, hedging freshness)
        # can distinguish a historical profile-dated run from current risk.
        metrics["valuation_as_of"] = valuation_as_of.isoformat()
        status = _risk_status_from_metrics(metrics)
        run.metrics = metrics
        run.status = status

        valuation_run = _persist_valuation_run(
            session,
            run=run,
            resolved=resolved,
            metrics=metrics,
            position_markets=position_markets,
            pricing_diagnostics=pricing_diagnostics,
            scoped_position_ids=scoped_position_ids,
            valuation_date=valuation_as_of,
        )

        update_task_progress(
            session,
            task_id,
            current=total,
            total=total,
            message=_risk_completion_message(metrics, status),
        )
        result_payload: dict[str, Any] = {
            "risk_run_id": run.id,
            "valuation_run_id": valuation_run.id,
            **(_risk_error_payload(metrics) or {}),
        }
        mark_task_finished(
            session,
            task_id,
            status=status,
            message=_risk_completion_message(metrics, status),
            result_payload=result_payload,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(RiskRun, risk_run_id)
        if run is not None:
            run.status = TaskStatus.FAILED.value
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.FAILED.value,
            message="Batch pricing run failed",
            error=str(exc),
        )
        session.commit()


_MARKET_DIAGNOSTIC_KEYS = (
    "market_input_source",
    "quote_age_days",
    "pricing_parameter_profile_id",
    "pricing_parameter_row_id",
    "pricing_parameter_match_type",
    "missing_pricing_fields",
)


def _market_inputs_for_position(
    market: PricingEnvironmentSnapshot | None,
    diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Valuation-result market_inputs from the risk pass's resolved snapshot.

    Mirrors the shape the Positions page reads (spot/rate/dividend_yield/
    volatility/valuation_date + source diagnostics)."""
    inputs: dict[str, Any] = {}
    if market is not None:
        inputs.update(
            {
                "valuation_date": market.valuation_date.isoformat(),
                "spot": market.spot,
                "rate": market.rate,
                "dividend_yield": market.dividend_yield,
                "volatility": market.volatility,
                "asset_name": market.asset_name,
            }
        )
    if diagnostics:
        for key in _MARKET_DIAGNOSTIC_KEYS:
            if key in diagnostics:
                inputs[key] = diagnostics[key]
    return inputs


def _persist_valuation_run(
    session: Session,
    *,
    run: RiskRun,
    resolved: list[Position],
    metrics: dict[str, Any],
    position_markets: dict[int, PricingEnvironmentSnapshot],
    pricing_diagnostics: dict[int, dict[str, Any]],
    scoped_position_ids: list[int] | None = None,
    valuation_date: datetime | None = None,
) -> PositionValuationRun:
    """Fan the per-position risk rows out into a PositionValuationRun."""
    rows_by_id = {
        row["position_id"]: row
        for row in (metrics.get("positions") or [])
        if row.get("position_id") is not None
    }
    overrides: dict[str, Any] = {}
    if run.pricing_parameter_profile_id is not None:
        overrides["pricing_parameter_profile_id"] = run.pricing_parameter_profile_id
    if scoped_position_ids is not None:
        # Mirrors the legacy sync pricer's overrides.position_ids marker: its
        # presence tells the Positions page this was NOT a full-portfolio run.
        overrides["position_ids"] = sorted(scoped_position_ids)
    valuation_run = PositionValuationRun(
        portfolio_id=run.portfolio_id,
        pricing_parameter_profile_id=run.pricing_parameter_profile_id,
        market_source_path=None,
        valuation_date=valuation_date if valuation_date is not None else run.created_at,
        overrides=overrides,
        summary={},
        status="running",
        resolved_position_ids=[p.id for p in resolved],
    )
    session.add(valuation_run)
    session.flush()

    totals: dict[str, Any] = {
        "positions": 0,
        "priced": 0,
        "failed": 0,
        "market_value": 0.0,
        "pnl": 0.0,
        "delta": 0.0,
        "vega": 0.0,
    }
    for position in resolved:
        row = rows_by_id.get(position.id)
        if row is None:
            continue
        totals["positions"] += 1
        ok = bool(row.get("pricing_ok"))
        result_payload: dict[str, Any] = {
            greek: float(row.get(greek) or 0.0) for greek in RISK_GREEK_KEYS
        }
        if row.get("gross_notional") is not None:
            result_payload["gross_notional"] = row["gross_notional"]
        if row.get("greeks_error"):
            result_payload["greeks_error"] = row["greeks_error"]
        session.add(
            PositionValuationResult(
                valuation_run_id=valuation_run.id,
                position_id=position.id,
                source_trade_id=row.get("source_trade_id"),
                ok=ok,
                price=row.get("price") if ok else None,
                market_value=row.get("market_value") if ok else None,
                pnl=row.get("pnl") if ok else None,
                market_inputs=_market_inputs_for_position(
                    position_markets.get(position.id),
                    pricing_diagnostics.get(position.id),
                ),
                result_payload=result_payload,
                error=row.get("pricing_error"),
            )
        )
        if ok:
            totals["priced"] += 1
            totals["market_value"] += float(row.get("market_value") or 0.0)
            totals["pnl"] += float(row.get("pnl") or 0.0)
            totals["delta"] += float(row.get("delta") or 0.0)
            totals["vega"] += float(row.get("vega") or 0.0)
        else:
            totals["failed"] += 1
    valuation_run.summary = totals
    valuation_run.status = (
        "completed" if totals["failed"] == 0 else "completed_with_errors"
    )
    session.flush()
    return valuation_run

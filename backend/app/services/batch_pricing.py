"""Unified batch pricing.

One pricing pass (``calculate_portfolio_risk``, Greeks included) persists BOTH
outputs: the ``RiskRun`` metrics (Risk page) and a ``PositionValuationRun``
with per-position results (Positions page). Replaces the separate
``position_pricing`` and ``risk_run`` task paths.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable

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
    risk_coverage_diagnostics,
    _risk_error_payload,
    _risk_status_from_metrics,
    RiskPositionSnapshot,
    snapshot_risk_position,
)
from .task_runner import (
    mark_task_finished,
    mark_task_running,
    update_task_progress,
)


@dataclass(frozen=True, slots=True)
class ResolvedRiskSource:
    risk_run_id: int
    portfolio_id: int
    portfolio_name: str
    base_currency: str
    requested_position_ids: tuple[int, ...] | None
    positions: tuple[RiskPositionSnapshot, ...]
    position_markets: dict[int, PricingEnvironmentSnapshot]
    pricing_failures: dict[int, dict[str, Any]]
    pricing_diagnostics: dict[int, dict[str, Any]]
    valuation_as_of: datetime


@dataclass(frozen=True, slots=True)
class ComputedRiskSource:
    resolved: ResolvedRiskSource
    metrics: dict[str, Any]
    status: str


@dataclass(frozen=True, slots=True)
class PersistedRiskSource:
    risk_run_id: int
    valuation_run_id: int
    status: str
    coverage: dict[str, Any]


def _create_risk_run(
    session: Session,
    *,
    portfolio_id: int,
    position_ids: list[int] | None,
    pricing_parameter_profile_id: int | None,
    engine_config_id: int | None,
    market_snapshot_id: int | None,
    method: str,
    status: str,
) -> RiskRun:
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
        status=status,
        metrics={},
        scenario_cells=None,
        resolved_position_ids=scoped_position_ids,
    )
    session.add(run)
    session.flush()
    return run


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
    run = _create_risk_run(
        session,
        portfolio_id=portfolio_id,
        position_ids=position_ids,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        market_snapshot_id=market_snapshot_id,
        method=method,
        status=TaskStatus.QUEUED.value,
    )
    task = TaskRun(
        kind=TaskKind.BATCH_PRICING.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=run.portfolio_id,
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
    factory = session_factory or database.SessionLocal
    try:
        _run_persisted_risk_source(
            factory,
            risk_run_id=risk_run_id,
            task_id=task_id,
        )
    except Exception as exc:
        _mark_risk_source_failed(
            factory,
            risk_run_id=risk_run_id,
            task_id=task_id,
            error=str(exc),
        )


def _execute_batch_pricing_task(
    session: Session, task_id: int, risk_run_id: int
) -> None:
    """Compatibility wrapper for deterministic in-session producer drivers."""
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    execute_batch_pricing_task(
        task_id,
        risk_run_id,
        session_factory=factory,
    )
    session.expire_all()


def run_persisted_risk_source(
    *,
    session_factory: sessionmaker | None = None,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    market_snapshot_id: int | None = None,
    valuation_as_of: datetime | None = None,
    method: str = "summary",
) -> PersistedRiskSource:
    """Synchronously persist batch-equivalent risk evidence without a TaskRun."""
    factory = session_factory or database.SessionLocal
    with factory() as session:
        run = _create_risk_run(
            session,
            portfolio_id=portfolio_id,
            position_ids=position_ids,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            engine_config_id=engine_config_id,
            market_snapshot_id=market_snapshot_id,
            method=method,
            status=TaskStatus.RUNNING.value,
        )
        if valuation_as_of is not None:
            run.created_at = valuation_as_of
        session.commit()
        risk_run_id = run.id

    try:
        return _run_persisted_risk_source(
            factory,
            risk_run_id=risk_run_id,
            task_id=None,
        )
    except Exception as exc:
        _mark_risk_source_failed(
            factory,
            risk_run_id=risk_run_id,
            task_id=None,
            error=str(exc),
        )
        raise


def _run_persisted_risk_source(
    session_factory: Callable[[], Session],
    *,
    risk_run_id: int,
    task_id: int | None,
) -> PersistedRiskSource:
    resolved = _resolve_risk_source(session_factory, risk_run_id=risk_run_id)
    _mark_risk_source_running(
        session_factory,
        resolved=resolved,
        task_id=task_id,
    )
    progress_callback = (
        _task_progress_callback(session_factory, task_id)
        if task_id is not None
        else None
    )
    computed = _compute_risk_source(
        resolved,
        progress_callback=progress_callback,
    )
    return _persist_risk_source(
        session_factory,
        computed=computed,
        task_id=task_id,
    )


def _resolve_risk_source(
    session_factory: Callable[[], Session],
    *,
    risk_run_id: int,
) -> ResolvedRiskSource:
    with session_factory() as session:
        run = session.get(RiskRun, risk_run_id)
        if run is None:
            raise ValueError(f"Risk run not found: {risk_run_id}")
        portfolio = session.get(Portfolio, run.portfolio_id)
        if portfolio is None:
            raise ValueError(f"Portfolio not found: {run.portfolio_id}")

        scoped_position_ids = (
            tuple(run.resolved_position_ids)
            if run.resolved_position_ids is not None
            else None
        )
        resolved = _resolve_risk_positions(
            portfolio,
            session,
            position_ids=run.resolved_position_ids,
        )
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
        from .engine_configs import get_engine_config, resolve_pricing_engine

        engine_config = get_engine_config(session, run.engine_config_id)
        position_snapshots: list[RiskPositionSnapshot] = []
        for position in resolved:
            try:
                engine = resolve_pricing_engine(position, engine_config)
                pricing_diagnostics.setdefault(position.id, {})[
                    "resolved_engine"
                ] = engine.diagnostics()
            except ValueError as exc:
                pricing_failures[position.id] = {"pricing_error": str(exc)}
                engine = SimpleNamespace(
                    engine_name=position.engine_name or "BlackScholesEngine",
                    engine_kwargs=dict(position.engine_kwargs or {}),
                )
            position_snapshots.append(snapshot_risk_position(position, engine))

        return ResolvedRiskSource(
            risk_run_id=run.id,
            portfolio_id=portfolio.id,
            portfolio_name=portfolio.name,
            base_currency=portfolio.base_currency,
            requested_position_ids=scoped_position_ids,
            positions=tuple(position_snapshots),
            position_markets={
                position_id: market.model_copy(deep=True)
                for position_id, market in position_markets.items()
            },
            pricing_failures=deepcopy(pricing_failures),
            pricing_diagnostics=deepcopy(pricing_diagnostics),
            valuation_as_of=valuation_as_of,
        )


def _mark_risk_source_running(
    session_factory: Callable[[], Session],
    *,
    resolved: ResolvedRiskSource,
    task_id: int | None,
) -> None:
    with session_factory() as session:
        run = session.get(RiskRun, resolved.risk_run_id)
        if run is None:
            raise ValueError(f"Risk run not found: {resolved.risk_run_id}")
        run.status = TaskStatus.RUNNING.value
        run.resolved_position_ids = [position.id for position in resolved.positions]
        if task_id is not None:
            mark_task_running(
                session,
                task_id,
                message=f"Pricing {len(resolved.positions)} positions",
                total=len(resolved.positions),
            )
        session.commit()


def _task_progress_callback(
    session_factory: Callable[[], Session],
    task_id: int,
) -> Callable[[int, int], None]:
    def _progress(current: int, total_positions: int) -> None:
        with session_factory() as session:
            update_task_progress(
                session,
                task_id,
                current=current,
                total=total_positions,
                message=f"Priced {current} of {total_positions} positions",
            )
            session.commit()

    return _progress


def _compute_risk_source(
    resolved: ResolvedRiskSource,
    *,
    progress_callback: Callable[[int, int], None] | None,
) -> ComputedRiskSource:
    portfolio_like = SimpleNamespace(
        id=resolved.portfolio_id,
        name=resolved.portfolio_name,
        base_currency=resolved.base_currency,
        positions=list(resolved.positions),
    )
    metrics = calculate_portfolio_risk(
        portfolio_like,  # type: ignore[arg-type]
        position_markets=resolved.position_markets,
        pricing_failures=resolved.pricing_failures,
        pricing_diagnostics=resolved.pricing_diagnostics,
        max_workers=get_settings().risk_parallel_workers,
        progress_callback=progress_callback,
    )
    metrics["valuation_as_of"] = resolved.valuation_as_of.isoformat()
    metrics["coverage"] = risk_coverage_diagnostics(
        metrics,
        requested_position_ids=(
            list(resolved.requested_position_ids)
            if resolved.requested_position_ids is not None
            else None
        ),
        resolved_position_ids=[position.id for position in resolved.positions],
    )
    return ComputedRiskSource(
        resolved=resolved,
        metrics=metrics,
        status=_risk_status_from_metrics(metrics),
    )


def _persist_risk_source(
    session_factory: Callable[[], Session],
    *,
    computed: ComputedRiskSource,
    task_id: int | None,
) -> PersistedRiskSource:
    resolved = computed.resolved
    with session_factory() as session:
        run = session.get(RiskRun, resolved.risk_run_id)
        if run is None:
            raise ValueError(f"Risk run not found: {resolved.risk_run_id}")
        run.metrics = deepcopy(computed.metrics)
        run.status = computed.status
        run.resolved_position_ids = [position.id for position in resolved.positions]

        valuation_run = _persist_valuation_run(
            session,
            run=run,
            resolved=list(resolved.positions),
            metrics=computed.metrics,
            position_markets=resolved.position_markets,
            pricing_diagnostics=resolved.pricing_diagnostics,
            scoped_position_ids=(
                list(resolved.requested_position_ids)
                if resolved.requested_position_ids is not None
                else None
            ),
            valuation_date=resolved.valuation_as_of,
        )

        message = _risk_completion_message(computed.metrics, computed.status)
        if task_id is not None:
            update_task_progress(
                session,
                task_id,
                current=len(resolved.positions),
                total=len(resolved.positions),
                message=message,
            )
            result_payload: dict[str, Any] = {
                "risk_run_id": run.id,
                "valuation_run_id": valuation_run.id,
                **(_risk_error_payload(computed.metrics) or {}),
            }
            mark_task_finished(
                session,
                task_id,
                status=computed.status,
                message=message,
                result_payload=result_payload,
            )
        session.commit()
        return PersistedRiskSource(
            risk_run_id=run.id,
            valuation_run_id=valuation_run.id,
            status=computed.status,
            coverage=deepcopy(computed.metrics["coverage"]),
        )


def _mark_risk_source_failed(
    session_factory: Callable[[], Session],
    *,
    risk_run_id: int,
    task_id: int | None,
    error: str,
) -> None:
    with session_factory() as session:
        run = session.get(RiskRun, risk_run_id)
        if run is not None:
            run.status = TaskStatus.FAILED.value
        if task_id is not None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="Batch pricing run failed",
                error=error,
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
    resolved: list[Position | RiskPositionSnapshot],
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
        engine_config_id=run.engine_config_id,
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

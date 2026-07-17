"""Queue and synchronous execution for persisted backtest sources."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import get_settings
from ..models import (
    BacktestRun,
    Portfolio,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from .audit import record_audit
from .domains import backtest as backtest_svc
from .domains import positions as positions_svc
from .risk_engine import RiskPositionSnapshot, snapshot_risk_position
from .source_evidence import (
    finalize_market_metadata,
    utc_naive,
    valuation_metadata,
)
from .task_runner import (
    mark_task_finished,
    mark_task_running,
    submit_async_task,
    update_task_progress,
)

_VALID_ENGINES = {"quad", "pde", "mc", "analytical"}
_VALID_FALLBACK_ENGINES = {"quad", "pde", "mc"}
_VALID_ENGINE_FAMILIES = {"autocallable", "other"}
_VALID_VOL = {"realized", "flat"}


@dataclass(frozen=True, slots=True)
class ResolvedBacktestSource:
    backtest_run_id: int
    portfolio_id: int
    portfolio_name: str
    positions: tuple[RiskPositionSnapshot, ...]
    spec: dict[str, Any]
    config: dict[str, Any]
    pricing_parameter_profile_id: int | None
    engine_config_id: int | None
    valuation_as_of: datetime
    prepared_pipeline: Any
    source_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ComputedBacktestSource:
    resolved: ResolvedBacktestSource
    status: str
    results: dict[str, Any]
    excluded_positions: list[dict[str, Any]]
    artifacts: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PersistedBacktestSource:
    backtest_run_id: int
    status: str


def _validate_spec(spec: dict) -> None:
    import pandas as pd

    start, end = spec.get("start"), spec.get("end")
    if not start or not end or pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError("spec.start must be a date strictly before spec.end")
    for key in ("engine", "autocallable_engine", "other_engine"):
        value = spec.get(key)
        if value is not None and value not in _VALID_ENGINES:
            raise ValueError(f"{key} must be one of {sorted(_VALID_ENGINES)}")
    fallback = spec.get("fallback_engine")
    if fallback is not None and fallback not in _VALID_FALLBACK_ENGINES:
        raise ValueError(
            f"fallback_engine must be one of {sorted(_VALID_FALLBACK_ENGINES)}"
        )
    family = spec.get("engine_family")
    if family is not None and family not in _VALID_ENGINE_FAMILIES:
        raise ValueError(
            f"engine_family must be one of {sorted(_VALID_ENGINE_FAMILIES)}"
        )
    if spec.get("vol_source", "realized") not in _VALID_VOL:
        raise ValueError(f"vol_source must be one of {sorted(_VALID_VOL)}")


def _create_backtest_run(
    session: Session,
    *,
    portfolio_id: int,
    spec: dict[str, Any],
    config: dict[str, Any],
    pricing_parameter_profile_id: int | None,
    engine_config_id: int | None,
    position_ids: list[int] | None,
    status: str,
    valuation_as_of: datetime | None = None,
    market_snapshot_id: int | None = None,
    effective_market_evidence_id: str | None = None,
) -> BacktestRun:
    _validate_spec(spec)
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    if engine_config_id is not None:
        from .engine_configs import get_engine_config

        get_engine_config(session, engine_config_id)
    if market_snapshot_id is not None:
        raise ValueError(
            "market_snapshot_id is not supported for historical backtest; "
            "use canonical effective_market_evidence_id"
        )
    if position_ids is not None:
        from .risk_engine import _resolve_risk_positions

        position_ids = [
            position.id
            for position in _resolve_risk_positions(
                portfolio,
                session,
                position_ids=position_ids,
            )
        ]
    run = BacktestRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        status=status,
        spec=deepcopy(spec),
        config=deepcopy(config),
        results={},
        excluded_positions=[],
        artifacts={},
        resolved_position_ids=position_ids,
    )
    session.add(run)
    session.flush()
    source_metadata = valuation_metadata(
            session,
            created_at=run.created_at,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            explicit_valuation_as_of=valuation_as_of,
            market_snapshot_id=None,
            requested_effective_market_evidence_id=effective_market_evidence_id,
        )
    source_metadata.update(
        {
            "methodology": {
                "method": "historical",
                "confidence": 0.95,
                "horizon": "1_trading_day",
                "scaling": "none",
            },
            "source_config": {
                "spec": deepcopy(spec),
                "config": deepcopy(config),
            },
        }
    )
    run.results = {
        "source_metadata": source_metadata
    }
    return run


def queue_backtest(
    session: Session,
    *,
    portfolio_id: int,
    spec: dict,
    config: dict,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
    valuation_as_of: datetime | None = None,
    market_snapshot_id: int | None = None,
    effective_market_evidence_id: str | None = None,
) -> tuple[BacktestRun, TaskRun]:
    """Create a queued BacktestRun + TaskRun and dispatch the worker."""
    run = _create_backtest_run(
        session,
        portfolio_id=portfolio_id,
        spec=spec,
        config=config,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        position_ids=position_ids,
        status=TaskStatus.QUEUED.value,
        valuation_as_of=valuation_as_of,
        market_snapshot_id=market_snapshot_id,
        effective_market_evidence_id=effective_market_evidence_id,
    )
    task = TaskRun(
        kind=TaskKind.BACKTEST.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        backtest_run_id=run.id,
        message="Queued backtest run",
    )
    session.add(task)
    session.flush()
    record_audit(
        session,
        event_type="backtest.queued",
        actor="desk_user",
        subject_type="portfolio",
        subject_id=portfolio_id,
        payload={"run_id": run.id, "spec": spec},
    )
    session.commit()
    submit_async_task(execute_backtest_task, task.id, run.id)
    return run, task


def run_persisted_backtest_source(
    *,
    session_factory: sessionmaker | None = None,
    portfolio_id: int,
    spec: dict[str, Any],
    config: dict[str, Any],
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
    valuation_as_of: datetime | None = None,
    market_snapshot_id: int | None = None,
    effective_market_evidence_id: str | None = None,
    write_artifacts: bool = True,
) -> PersistedBacktestSource:
    """Persist queue-equivalent backtest evidence without creating a TaskRun."""
    factory = session_factory or database.SessionLocal
    with factory() as session:
        run = _create_backtest_run(
            session,
            portfolio_id=portfolio_id,
            spec=spec,
            config=config,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            engine_config_id=engine_config_id,
            position_ids=position_ids,
            status=TaskStatus.RUNNING.value,
            valuation_as_of=valuation_as_of,
            market_snapshot_id=market_snapshot_id,
            effective_market_evidence_id=effective_market_evidence_id,
        )
        session.commit()
        run_id = run.id

    try:
        return _run_persisted_backtest_source(
            factory,
            run_id=run_id,
            task_id=None,
            write_artifacts=write_artifacts,
        )
    except Exception as exc:
        _mark_backtest_source_failed(
            factory,
            run_id=run_id,
            task_id=None,
            error=str(exc),
        )
        raise


def execute_backtest_task(
    task_id: int,
    run_id: int,
    session_factory: sessionmaker | None = None,
) -> None:
    """Worker entry point; queued and inline paths share the same phases."""
    database.init_db()
    factory = session_factory or database.SessionLocal
    try:
        _run_persisted_backtest_source(
            factory,
            run_id=run_id,
            task_id=task_id,
            write_artifacts=True,
        )
    except Exception as exc:  # noqa: BLE001 - persist failure, never crash worker
        _mark_backtest_source_failed(
            factory,
            run_id=run_id,
            task_id=task_id,
            error=str(exc),
        )


def _execute(session: Session, task_id: int, run_id: int) -> None:
    """Compatibility wrapper for deterministic in-session producer drivers."""
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    execute_backtest_task(task_id, run_id, session_factory=factory)
    session.expire_all()


def _run_persisted_backtest_source(
    session_factory: Callable[[], Session],
    *,
    run_id: int,
    task_id: int | None,
    write_artifacts: bool,
) -> PersistedBacktestSource:
    resolved = _resolve_backtest_source(session_factory, run_id=run_id)
    _mark_backtest_source_running(
        session_factory,
        resolved=resolved,
        task_id=task_id,
    )
    progress = (
        _backtest_progress_callback(session_factory, task_id)
        if task_id is not None
        else lambda _current, _total: None
    )
    computed = _compute_backtest_source(
        session_factory,
        resolved=resolved,
        progress=progress,
        write_artifacts=write_artifacts,
    )
    return _persist_backtest_source(
        session_factory,
        computed=computed,
        task_id=task_id,
    )


def _resolve_backtest_source(
    session_factory: Callable[[], Session],
    *,
    run_id: int,
) -> ResolvedBacktestSource:
    with session_factory() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            raise ValueError(f"Backtest run not found: {run_id}")
        portfolio = session.get(Portfolio, run.portfolio_id)
        if portfolio is None:
            raise ValueError(f"Portfolio not found: {run.portfolio_id}")

        all_positions = positions_svc.list_filtered(
            portfolio_id=run.portfolio_id,
            session=session,
        )
        if run.resolved_position_ids is not None:
            wanted = set(run.resolved_position_ids)
            positions = [
                position for position in all_positions if position.id in wanted
            ]
        else:
            positions = list(all_positions)

        source_metadata = deepcopy(
            (run.results or {}).get("source_metadata") or {}
        )
        if not source_metadata:
            source_metadata = valuation_metadata(
                session,
                created_at=run.created_at,
                pricing_parameter_profile_id=run.pricing_parameter_profile_id,
                explicit_valuation_as_of=None,
                market_snapshot_id=None,
                requested_effective_market_evidence_id=None,
            )
        raw_valuation = (
            source_metadata.get("effective_valuation_as_of")
            or source_metadata.get("valuation_as_of")
        )
        valuation_as_of = (
            utc_naive(datetime.fromisoformat(raw_valuation))
            if isinstance(raw_valuation, str)
            else utc_naive(run.created_at)
        )

        from .engine_configs import get_engine_config, resolve_pricing_engine

        engine_config = get_engine_config(session, run.engine_config_id)
        snapshots: list[RiskPositionSnapshot] = []
        for position in positions:
            engine = resolve_pricing_engine(position, engine_config)
            snapshots.append(snapshot_risk_position(position, engine))
        prepared = backtest_svc.prepare_pipeline_inputs(
            session,
            positions=snapshots,
            spec=deepcopy(run.spec or {}),
            engine_config_id=run.engine_config_id,
        )
        source_metadata = finalize_market_metadata(
            source_metadata,
            prepared.evidence_manifest,
            namespace="backtest-market-evidence/v1",
        )
        source_metadata["source_currencies"] = sorted(
            {
                str(position.currency).strip().upper()
                for position in snapshots
                if position.currency
            }
        )

        return ResolvedBacktestSource(
            backtest_run_id=run.id,
            portfolio_id=portfolio.id,
            portfolio_name=portfolio.name,
            positions=tuple(snapshots),
            spec=deepcopy(run.spec or {}),
            config=deepcopy(run.config or {}),
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            engine_config_id=run.engine_config_id,
            valuation_as_of=valuation_as_of,
            prepared_pipeline=prepared,
            source_metadata=deepcopy(source_metadata),
        )


def _mark_backtest_source_running(
    session_factory: Callable[[], Session],
    *,
    resolved: ResolvedBacktestSource,
    task_id: int | None,
) -> None:
    with session_factory() as session:
        run = session.get(BacktestRun, resolved.backtest_run_id)
        if run is None:
            raise ValueError(f"Backtest run not found: {resolved.backtest_run_id}")
        run.status = TaskStatus.RUNNING.value
        run.resolved_position_ids = [position.id for position in resolved.positions]
        if task_id is not None:
            mark_task_running(session, task_id, message="Running backtest")
        session.commit()


def _backtest_progress_callback(
    session_factory: Callable[[], Session],
    task_id: int,
) -> Callable[[int, int], None]:
    def _progress(current: int, total: int) -> None:
        with session_factory() as session:
            update_task_progress(
                session,
                task_id,
                current=current,
                total=total,
            )
            session.commit()

    return _progress


def _compute_backtest_source(
    session_factory: Callable[[], Session],
    *,
    resolved: ResolvedBacktestSource,
    progress: Callable[[int, int], None],
    write_artifacts: bool,
) -> ComputedBacktestSource:
    status, results, excluded, raw = backtest_svc.run_prepared_pipeline(
        resolved.prepared_pipeline,
        positions=list(resolved.positions),
        pricing_parameter_profile_id=resolved.pricing_parameter_profile_id,
        engine_config_id=resolved.engine_config_id,
        spec=deepcopy(resolved.spec),
        config=deepcopy(resolved.config),
        portfolio_name=resolved.portfolio_name,
        valuation_date=resolved.valuation_as_of,
        progress=progress,
    )

    persisted_results = deepcopy(results)
    persisted_results["source_metadata"] = {
        **deepcopy(resolved.source_metadata),
        "engine_config_id": resolved.engine_config_id,
        "resolved_position_ids": [position.id for position in resolved.positions],
    }
    artifacts: dict[str, Any] = {}
    if write_artifacts and status == "completed" and raw:
        settings = get_settings()
        artifacts = backtest_svc.write_artifacts(
            raw=raw,
            run_id=resolved.backtest_run_id,
            formats=resolved.config.get(
                "export_formats",
                ["json", "xlsx", "html"],
            ),
            base_dir=settings.backtest_output_dir,
        )
    return ComputedBacktestSource(
        resolved=resolved,
        status=status,
        results=persisted_results,
        excluded_positions=deepcopy(excluded),
        artifacts=deepcopy(artifacts),
    )


def _persist_backtest_source(
    session_factory: Callable[[], Session],
    *,
    computed: ComputedBacktestSource,
    task_id: int | None,
) -> PersistedBacktestSource:
    resolved = computed.resolved
    with session_factory() as session:
        run = session.get(BacktestRun, resolved.backtest_run_id)
        if run is None:
            raise ValueError(f"Backtest run not found: {resolved.backtest_run_id}")
        run.results = deepcopy(computed.results)
        run.excluded_positions = deepcopy(computed.excluded_positions)
        run.artifacts = deepcopy(computed.artifacts)
        run.status = computed.status
        run.resolved_position_ids = [position.id for position in resolved.positions]
        if task_id is not None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.COMPLETED.value,
                message=f"Backtest {computed.status}",
                result_payload={"backtest_run_id": run.id},
            )
            # TaskRun uses generic terminal states; the producer row retains the
            # authoritative domain state, including the valid ``empty`` outcome.
            run.status = computed.status
        session.commit()
        return PersistedBacktestSource(
            backtest_run_id=run.id,
            status=computed.status,
        )


def _mark_backtest_source_failed(
    session_factory: Callable[[], Session],
    *,
    run_id: int,
    task_id: int | None,
    error: str,
) -> None:
    with session_factory() as session:
        try:
            run = session.get(BacktestRun, run_id)
            if run is not None:
                run.status = TaskStatus.FAILED.value
                run.results = {"error": error}
            if task_id is not None:
                mark_task_finished(
                    session,
                    task_id,
                    status=TaskStatus.FAILED.value,
                    error=error,
                )
            session.commit()
        except Exception:
            session.rollback()

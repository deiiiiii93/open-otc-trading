"""Queue and synchronous execution for persisted scenario-test sources."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import get_settings
from ..models import (
    Portfolio,
    ScenarioTestRun,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from .audit import record_audit
from .domains import positions as positions_svc
from .domains import scenario_catalog
from .domains import scenario_test as scenario_test_svc
from .risk_engine import RiskPositionSnapshot, snapshot_risk_position
from .risk_engine import _pricing_position_context
from .source_evidence import (
    build_market_evidence_manifest,
    finalize_market_metadata,
    source_metric_contract,
    utc_naive,
    valuation_metadata,
)
from .task_runner import (
    mark_task_finished,
    mark_task_running,
    submit_async_task,
)


@dataclass(frozen=True, slots=True)
class ResolvedScenarioSource:
    scenario_test_run_id: int
    portfolio_id: int
    portfolio_name: str
    positions: tuple[RiskPositionSnapshot, ...]
    scenario_request: dict[str, Any]
    config: dict[str, Any]
    pricing_parameter_profile_id: int | None
    engine_config_id: int | None
    valuation_as_of: datetime
    frozen_scenario_specs: tuple[dict[str, Any], ...]
    scenario_set_hash: str
    position_markets: dict[int, Any]
    pricing_failures: dict[int, dict[str, Any]]
    source_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ComputedScenarioSource:
    resolved: ResolvedScenarioSource
    status: str
    results: dict[str, Any]
    excluded_positions: list[dict[str, Any]]
    artifacts: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PersistedScenarioSource:
    scenario_test_run_id: int
    status: str


def _create_scenario_run(
    session: Session,
    *,
    portfolio_id: int,
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    pricing_parameter_profile_id: int | None,
    engine_config_id: int | None,
    position_ids: list[int] | None,
    status: str,
    valuation_as_of: datetime | None = None,
    market_snapshot_id: int | None = None,
    effective_market_evidence_id: str | None = None,
) -> ScenarioTestRun:
    if (
        not scenario_request.get("predefined")
        and not scenario_request.get("custom")
        and not scenario_request.get("scenario_set")
    ):
        raise ValueError(
            "At least one scenario is required (predefined, custom, or scenario_set)"
        )
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    if engine_config_id is not None:
        from .engine_configs import get_engine_config

        get_engine_config(session, engine_config_id)
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

    # Fail synchronously before any run row is persisted.
    frozen_request = scenario_catalog.freeze_scenario_request(scenario_request)
    run = ScenarioTestRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        status=status,
        scenario_spec=frozen_request,
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
        market_snapshot_id=market_snapshot_id,
        requested_effective_market_evidence_id=effective_market_evidence_id,
    )
    frozen_specs, scenario_hash = scenario_catalog.frozen_scenario_specs(
        frozen_request
    )
    source_metadata.update(
        {
            "methodology": {
                "method": "scenario_distribution",
                "confidence": 0.95,
                "horizon": "scenario_set",
                "scaling": "none",
            },
            "source_config": {
                "scenario_request": scenario_catalog.strip_source_snapshot(
                    frozen_request
                ),
                "config": deepcopy(config),
                "scenario_set_hash": scenario_hash,
            },
            "scenario_set_name": scenario_request.get("scenario_set"),
            "scenario_set_hash": scenario_hash,
            "scenario_names": [str(spec["name"]) for spec in frozen_specs],
            "frozen_scenarios": frozen_specs,
            "metric_contract": source_metric_contract("scenario_test"),
        }
    )
    run.results = {"source_metadata": source_metadata}
    return run


def queue_scenario_test(
    session: Session,
    *,
    portfolio_id: int,
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
    valuation_as_of: datetime | None = None,
    market_snapshot_id: int | None = None,
    effective_market_evidence_id: str | None = None,
) -> tuple[ScenarioTestRun, TaskRun]:
    """Create a queued ScenarioTestRun + TaskRun and dispatch the worker."""
    run = _create_scenario_run(
        session,
        portfolio_id=portfolio_id,
        scenario_request=scenario_request,
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
        kind=TaskKind.SCENARIO_TEST.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        scenario_test_run_id=run.id,
        message="Queued scenario test run",
    )
    session.add(task)
    session.flush()
    record_audit(
        session,
        event_type="scenario_test.queued",
        actor="desk_user",
        subject_type="portfolio",
        subject_id=portfolio_id,
        payload={"run_id": run.id, "scenarios": scenario_request},
    )
    session.commit()
    submit_async_task(execute_scenario_test_task, task.id, run.id)
    return run, task


def run_persisted_scenario_source(
    *,
    session_factory: sessionmaker | None = None,
    portfolio_id: int,
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
    valuation_as_of: datetime | None = None,
    market_snapshot_id: int | None = None,
    effective_market_evidence_id: str | None = None,
    write_artifacts: bool = True,
) -> PersistedScenarioSource:
    """Persist queue-equivalent scenario evidence without creating a TaskRun."""
    factory = session_factory or database.SessionLocal
    with factory() as session:
        run = _create_scenario_run(
            session,
            portfolio_id=portfolio_id,
            scenario_request=scenario_request,
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
        return _run_persisted_scenario_source(
            factory,
            run_id=run_id,
            task_id=None,
            write_artifacts=write_artifacts,
        )
    except Exception as exc:
        _mark_scenario_source_failed(
            factory,
            run_id=run_id,
            task_id=None,
            error=str(exc),
        )
        raise


def execute_scenario_test_task(
    task_id: int,
    run_id: int,
    session_factory: sessionmaker | None = None,
) -> None:
    """Worker entry point; queued and inline paths share the same phases."""
    database.init_db()
    factory = session_factory or database.SessionLocal
    try:
        _run_persisted_scenario_source(
            factory,
            run_id=run_id,
            task_id=task_id,
            write_artifacts=True,
        )
    except Exception as exc:  # noqa: BLE001 - persist failure, never crash worker
        _mark_scenario_source_failed(
            factory,
            run_id=run_id,
            task_id=task_id,
            error=str(exc),
        )


def _execute(session: Session, task_id: int, run_id: int) -> None:
    """Compatibility wrapper for deterministic in-session producer drivers."""
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    execute_scenario_test_task(task_id, run_id, session_factory=factory)
    session.expire_all()


def _run_persisted_scenario_source(
    session_factory: Callable[[], Session],
    *,
    run_id: int,
    task_id: int | None,
    write_artifacts: bool,
) -> PersistedScenarioSource:
    resolved = _resolve_scenario_source(session_factory, run_id=run_id)
    _mark_scenario_source_running(
        session_factory,
        resolved=resolved,
        task_id=task_id,
    )
    computed = _compute_scenario_source(
        session_factory,
        resolved=resolved,
        write_artifacts=write_artifacts,
    )
    return _persist_scenario_source(
        session_factory,
        computed=computed,
        task_id=task_id,
    )


def _resolve_scenario_source(
    session_factory: Callable[[], Session],
    *,
    run_id: int,
) -> ResolvedScenarioSource:
    with session_factory() as session:
        run = session.get(ScenarioTestRun, run_id)
        if run is None:
            raise ValueError(f"Scenario test run not found: {run_id}")
        portfolio = session.get(Portfolio, run.portfolio_id)
        if portfolio is None:
            raise ValueError(f"Portfolio not found: {run.portfolio_id}")
        # A persisted scenario source is valid only if it retains the exact
        # snapshot captured at creation.  Never rebuild a queued source from a
        # mutable named set: a missing, malformed, or hash-mismatched envelope
        # must fail the producer rather than silently changing its economics.
        frozen_specs, scenario_hash = scenario_catalog.frozen_scenario_specs(
            run.scenario_spec or {}
        )

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
        position_markets, pricing_failures, pricing_diagnostics = (
            _pricing_position_context(
                session,
                positions,
                pricing_parameter_profile_id=run.pricing_parameter_profile_id,
                valuation_date=valuation_as_of,
                market_snapshot_id=source_metadata.get("market_snapshot_id"),
            )
        )
        for position in positions:
            engine = resolve_pricing_engine(position, engine_config)
            pricing_diagnostics.setdefault(position.id, {})[
                "resolved_engine"
            ] = engine.diagnostics()
        manifest = build_market_evidence_manifest(
            session,
            positions=positions,
            position_markets=position_markets,
            pricing_diagnostics=pricing_diagnostics,
            valuation_as_of=valuation_as_of,
            market_snapshot_id=source_metadata.get("market_snapshot_id"),
        )
        source_metadata = finalize_market_metadata(source_metadata, manifest)
        source_metadata.update(
            {
                "source_config": {
                    "scenario_request": scenario_catalog.strip_source_snapshot(
                        run.scenario_spec or {}
                    ),
                    "config": deepcopy(run.config or {}),
                    "scenario_set_hash": scenario_hash,
                },
                "scenario_set_hash": scenario_hash,
                "scenario_names": [str(spec["name"]) for spec in frozen_specs],
                "frozen_scenarios": frozen_specs,
                "source_currencies": sorted(
                    {
                        str(position.currency).strip().upper()
                        for position in snapshots
                        if position.currency
                    }
                ),
            }
        )

        return ResolvedScenarioSource(
            scenario_test_run_id=run.id,
            portfolio_id=portfolio.id,
            portfolio_name=portfolio.name,
            positions=tuple(snapshots),
            scenario_request=scenario_catalog.strip_source_snapshot(
                run.scenario_spec or {}
            ),
            config=deepcopy(run.config or {}),
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            engine_config_id=run.engine_config_id,
            valuation_as_of=valuation_as_of,
            frozen_scenario_specs=tuple(deepcopy(frozen_specs)),
            scenario_set_hash=scenario_hash,
            position_markets={
                key: value.model_copy(deep=True)
                for key, value in position_markets.items()
            },
            pricing_failures=deepcopy(pricing_failures),
            source_metadata=deepcopy(source_metadata),
        )


def _mark_scenario_source_running(
    session_factory: Callable[[], Session],
    *,
    resolved: ResolvedScenarioSource,
    task_id: int | None,
) -> None:
    with session_factory() as session:
        run = session.get(ScenarioTestRun, resolved.scenario_test_run_id)
        if run is None:
            raise ValueError(
                f"Scenario test run not found: {resolved.scenario_test_run_id}"
            )
        run.status = TaskStatus.RUNNING.value
        run.resolved_position_ids = [position.id for position in resolved.positions]
        if task_id is not None:
            mark_task_running(
                session,
                task_id,
                message="Running scenario test",
            )
        session.commit()


def _compute_scenario_source(
    session_factory: Callable[[], Session],
    *,
    resolved: ResolvedScenarioSource,
    write_artifacts: bool,
) -> ComputedScenarioSource:
    status, results, excluded, raw = scenario_test_svc.run_pipeline(
        None,
        positions=list(resolved.positions),
        pricing_parameter_profile_id=resolved.pricing_parameter_profile_id,
        engine_config_id=resolved.engine_config_id,
        scenario_request=deepcopy(resolved.scenario_request),
        resolved_scenario_specs=deepcopy(list(resolved.frozen_scenario_specs)),
        position_markets={
            key: value.model_copy(deep=True)
            for key, value in resolved.position_markets.items()
        },
        pricing_failures=deepcopy(resolved.pricing_failures),
        config=deepcopy(resolved.config),
        portfolio_name=f"{resolved.portfolio_name}-scenario",
        valuation_date=resolved.valuation_as_of,
    )

    persisted_results = deepcopy(results)
    persisted_results["source_metadata"] = {
        **deepcopy(resolved.source_metadata),
        "engine_config_id": resolved.engine_config_id,
        "resolved_position_ids": [position.id for position in resolved.positions],
    }
    artifacts: dict[str, Any] = {}
    if write_artifacts and status == "completed" and raw is not None:
        settings = get_settings()
        artifacts = scenario_test_svc.write_artifacts(
            results=persisted_results,
            excluded_positions=deepcopy(excluded),
            run_id=resolved.scenario_test_run_id,
            formats=resolved.config.get("export_formats", ["json"]),
            base_dir=settings.scenario_test_output_dir,
        )
    return ComputedScenarioSource(
        resolved=resolved,
        status=status,
        results=persisted_results,
        excluded_positions=deepcopy(excluded),
        artifacts=deepcopy(artifacts),
    )


def _persist_scenario_source(
    session_factory: Callable[[], Session],
    *,
    computed: ComputedScenarioSource,
    task_id: int | None,
) -> PersistedScenarioSource:
    resolved = computed.resolved
    with session_factory() as session:
        run = session.get(ScenarioTestRun, resolved.scenario_test_run_id)
        if run is None:
            raise ValueError(
                f"Scenario test run not found: {resolved.scenario_test_run_id}"
            )
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
                message=f"Scenario test {computed.status}",
                result_payload={"scenario_test_run_id": run.id},
            )
            # TaskRun uses generic terminal states; the producer row retains the
            # authoritative domain state, including the valid ``empty`` outcome.
            run.status = computed.status
        session.commit()
        return PersistedScenarioSource(
            scenario_test_run_id=run.id,
            status=computed.status,
        )


def _mark_scenario_source_failed(
    session_factory: Callable[[], Session],
    *,
    run_id: int,
    task_id: int | None,
    error: str,
) -> None:
    with session_factory() as session:
        try:
            run = session.get(ScenarioTestRun, run_id)
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

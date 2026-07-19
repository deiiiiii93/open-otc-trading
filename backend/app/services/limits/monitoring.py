"""Durable composite monitoring orchestration for versioned limits.

The worker deliberately owns one task only.  It synchronously reuses or refreshes
persisted producer evidence, commits every source reference before evaluation,
and records business ``unknown`` outcomes without failing its TaskRun.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ... import database
from ...models import (
    BacktestRun,
    EngineConfigVariant,
    LimitEvaluation,
    LimitMonitoringRun,
    LimitMonitoringRunVersion,
    Portfolio,
    PricingParameterProfile,
    Position,
    RiskLimit,
    RiskLimitVersion,
    RiskRun,
    ScenarioTestRun,
    MarketSnapshot,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from ..task_runner import mark_task_finished, submit_async_task
from .contracts import LimitActionContext
from .definitions import canonical_version_snapshot
from .errors import LimitConflictError
from .evaluator import EvaluationResult, LimitRule, NormalizedObservation, evaluate
from .source_planner import (
    SourcePlanKey,
    SourcePlanRequest,
    SourceSelection,
    group_source_plans,
    persist_source_reference,
    select_source,
)
from .sources import (
    ObservationScope,
    adapt_backtest_run,
    adapt_risk_run,
    adapt_scenario_test_run,
)


_TRIGGERS = frozenset({"manual", "agent", "scheduled"})
_POLICIES = frozenset({"reuse_only", "refresh_if_stale", "force_refresh"})
_LOGGER = logging.getLogger(__name__)


IncidentReconciler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class QueuedLimitMonitoring:
    monitoring_run_id: int
    task_id: int


@dataclass(frozen=True, slots=True)
class _ResolvedScope:
    scope_type: str
    scope_key: str
    scope_label: str
    position_ids: tuple[int, ...]
    value: str | int | None


@dataclass(frozen=True, slots=True)
class _ResolvedGroup:
    key: SourcePlanKey
    limit_version_ids: tuple[int, ...]
    source_id: int | None
    source_status: str
    is_fresh: bool
    reused: bool
    reason_code: str | None
    reference_id: int


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()


def _context_snapshot(context: LimitActionContext) -> dict[str, Any]:
    return {
        "actor": context.actor,
        "persona": context.persona,
        "mode": context.mode,
        "thread_id": context.thread_id,
        "audit_ref": context.audit_ref,
    }


def _context_from_snapshot(snapshot: Mapping[str, Any]) -> LimitActionContext:
    raw = dict(snapshot.get("context") or {})
    return LimitActionContext(
        actor=str(raw.get("actor") or "system"),
        persona=raw.get("persona") if isinstance(raw.get("persona"), str) else None,
        mode=str(raw.get("mode") or "auto"),  # type: ignore[arg-type]
        thread_id=(
            int(raw["thread_id"])
            if isinstance(raw.get("thread_id"), int)
            and not isinstance(raw.get("thread_id"), bool)
            else None
        ),
        audit_ref=(
            raw.get("audit_ref") if isinstance(raw.get("audit_ref"), str) else None
        ),
    )


def _active_versions(
    session: Session,
    *,
    portfolio_id: int,
    valuation_as_of: datetime,
) -> list[RiskLimitVersion]:
    rows = list(
        session.execute(
            select(RiskLimitVersion)
            .join(RiskLimit)
            .where(
                RiskLimitVersion.activated_at.is_not(None),
                RiskLimitVersion.effective_from <= valuation_as_of,
                or_(
                    RiskLimitVersion.effective_until.is_(None),
                    RiskLimitVersion.effective_until > valuation_as_of,
                ),
            )
            .order_by(RiskLimitVersion.id)
        ).scalars()
    )
    return [
        row
        for row in rows
        if row.scope_type != "portfolio"
        or portfolio_id in set((row.scope_config or {}).get("portfolio_ids") or [])
    ]


def _positions(session: Session, portfolio_id: int) -> list[Position]:
    return list(
        session.execute(
            select(Position)
            .where(Position.portfolio_id == portfolio_id)
            .order_by(Position.id)
        ).scalars()
    )


def _resolve_scopes(
    version: RiskLimitVersion,
    *,
    portfolio: Portfolio,
    positions: list[Position],
) -> tuple[_ResolvedScope, ...]:
    config = dict(version.scope_config or {})
    by_id = {position.id: position for position in positions}
    if version.scope_type == "portfolio":
        return (
            _ResolvedScope(
                "portfolio",
                f"portfolio:{portfolio.id}",
                portfolio.name,
                tuple(sorted(by_id)),
                portfolio.id,
            ),
        )
    if version.scope_type == "position":
        ids = tuple(sorted(int(value) for value in config.get("position_ids") or []))
        ids = tuple(position_id for position_id in ids if position_id in by_id)
        return tuple(
            _ResolvedScope(
                "position",
                f"position:{position_id}",
                f"Position {position_id}",
                (position_id,),
                position_id,
            )
            for position_id in ids
        )
    if version.scope_type == "underlying":
        symbols = config.get("symbols")
        values = (
            sorted({str(position.underlying) for position in positions})
            if config.get("all_in_portfolio") is True
            else sorted(str(value) for value in (symbols or []))
        )
        return tuple(
            _ResolvedScope(
                "underlying",
                f"underlying:{value}",
                value,
                tuple(
                    position.id
                    for position in positions
                    if position.underlying == value
                ),
                value,
            )
            for value in values
            if any(position.underlying == value for position in positions)
        )
    if version.scope_type == "product_family":
        families = config.get("families")
        values = (
            sorted({str(position.product_type) for position in positions})
            if config.get("all_in_portfolio") is True
            else sorted(str(value) for value in (families or []))
        )
        return tuple(
            _ResolvedScope(
                "product_family",
                f"product_family:{value}",
                value,
                tuple(
                    position.id
                    for position in positions
                    if str(position.product_type) == value
                ),
                value,
            )
            for value in values
            if any(str(position.product_type) == value for position in positions)
        )
    raise ValueError(f"unsupported scope type {version.scope_type!r}")


def _snapshot_version(
    version: RiskLimitVersion,
    scopes: tuple[_ResolvedScope, ...],
) -> dict[str, Any]:
    payload = canonical_version_snapshot(version)
    payload["id"] = version.id
    payload["scopes"] = [
        {
            "scope_type": scope.scope_type,
            "scope_key": scope.scope_key,
            "scope_label": scope.scope_label,
            "position_ids": list(scope.position_ids),
            "value": scope.value,
        }
        for scope in scopes
    ]
    return payload


def _validate_queue_inputs(
    *,
    trigger: str,
    context: LimitActionContext,
    market_snapshot_id: int | None,
    effective_market_evidence_id: str | None,
    source_policy: str,
    max_source_age_seconds: int | None,
    schedule_id: int | None,
    occurrence_id: int | None,
) -> None:
    if trigger not in _TRIGGERS:
        raise ValueError(f"unsupported monitoring trigger {trigger!r}")
    if context.mode not in {"interactive", "auto", "yolo"}:
        raise ValueError("context.mode is invalid")
    if not isinstance(context.actor, str) or not context.actor.strip():
        raise ValueError("context.actor must be non-empty")
    if source_policy not in _POLICIES:
        raise ValueError(f"unsupported source policy {source_policy!r}")
    if market_snapshot_id is None and not effective_market_evidence_id:
        raise ValueError(
            "market_snapshot_id or effective_market_evidence_id is required"
        )
    if max_source_age_seconds is not None and (
        isinstance(max_source_age_seconds, bool) or max_source_age_seconds < 0
    ):
        raise ValueError("max_source_age_seconds must be a non-negative integer")
    if trigger == "scheduled" and (schedule_id is None or occurrence_id is None):
        raise ValueError("scheduled monitoring requires schedule_id and occurrence_id")
    if trigger != "scheduled" and (
        schedule_id is not None or occurrence_id is not None
    ):
        raise ValueError(
            "schedule_id and occurrence_id are valid only for scheduled monitoring"
        )
    for field, value in (
        ("schedule_id", schedule_id),
        ("occurrence_id", occurrence_id),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError(f"{field} must be a positive integer")


def queue_limit_monitoring(
    session: Session,
    *,
    portfolio_id: int,
    trigger: str,
    context: LimitActionContext,
    pricing_parameter_profile_id: int | None,
    engine_config_id: int | None,
    market_snapshot_id: int | None,
    effective_market_evidence_id: str | None,
    valuation_as_of: datetime,
    source_policy: str,
    max_source_age_seconds: int | None,
    source_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    schedule_id: int | None = None,
    occurrence_id: int | None = None,
) -> tuple[LimitMonitoringRun, TaskRun]:
    """Create the immutable monitoring envelope and its one composite task.

    This function intentionally does not commit or dispatch.  The caller owns the
    queued-state commit, which keeps HTTP/scheduler scheduling deterministic.
    """
    _validate_queue_inputs(
        trigger=trigger,
        context=context,
        market_snapshot_id=market_snapshot_id,
        effective_market_evidence_id=effective_market_evidence_id,
        source_policy=source_policy,
        max_source_age_seconds=max_source_age_seconds,
        schedule_id=schedule_id,
        occurrence_id=occurrence_id,
    )
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"portfolio not found: {portfolio_id}")
    active_run_id = session.scalar(
        select(LimitMonitoringRun.id)
        .where(
            LimitMonitoringRun.portfolio_id == portfolio_id,
            LimitMonitoringRun.status.in_(
                (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)
            ),
        )
        .order_by(LimitMonitoringRun.id.desc())
        .limit(1)
    )
    if active_run_id is not None:
        raise LimitConflictError(
            f"portfolio {portfolio_id} already has active "
            f"limit monitoring run {active_run_id}"
        )
    for model, value, field in (
        (
            PricingParameterProfile,
            pricing_parameter_profile_id,
            "pricing_parameter_profile_id",
        ),
        (EngineConfigVariant, engine_config_id, "engine_config_id"),
        (MarketSnapshot, market_snapshot_id, "market_snapshot_id"),
    ):
        if value is not None and session.get(model, value) is None:
            raise ValueError(f"{field} not found: {value}")
    normalized_valuation = _utc_naive(valuation_as_of)
    versions = _active_versions(
        session,
        portfolio_id=portfolio_id,
        valuation_as_of=normalized_valuation,
    )
    positions = _positions(session, portfolio_id)
    snapshot_versions = []
    for version in versions:
        scopes = _resolve_scopes(version, portfolio=portfolio, positions=positions)
        if scopes:
            snapshot_versions.append(_snapshot_version(version, scopes))
    snapshot = {
        "context": _context_snapshot(context),
        "inputs": {
            "portfolio_id": portfolio_id,
            "pricing_parameter_profile_id": pricing_parameter_profile_id,
            "engine_config_id": engine_config_id,
            "market_snapshot_id": market_snapshot_id,
            "effective_market_evidence_id": effective_market_evidence_id,
            "valuation_as_of": normalized_valuation.isoformat(),
            "source_policy": source_policy,
            "max_source_age_seconds": max_source_age_seconds,
            "schedule_id": schedule_id,
            "occurrence_id": occurrence_id,
        },
        "source_inputs": json.loads(_canonical(dict(source_inputs or {}))),
        "versions": snapshot_versions,
    }
    run = LimitMonitoringRun(
        trigger=trigger,
        mode=context.mode,
        schedule_id=schedule_id,
        occurrence_id=occurrence_id,
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        market_snapshot_id=market_snapshot_id,
        valuation_as_of=normalized_valuation,
        source_policy=source_policy,
        max_source_age_seconds=max_source_age_seconds,
        status=TaskStatus.QUEUED.value,
        summary={},
        definition_snapshot=snapshot,
        definition_snapshot_hash=_snapshot_hash(snapshot),
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError as exc:
        raise LimitConflictError(
            f"portfolio {portfolio_id} already has active limit monitoring"
        ) from exc
    for snapshot_version in snapshot_versions:
        session.add(
            LimitMonitoringRunVersion(
                monitoring_run_id=run.id,
                limit_version_id=int(snapshot_version["id"]),
            )
        )
    task = TaskRun(
        kind=TaskKind.LIMIT_MONITORING.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        limit_monitoring_run_id=run.id,
        message="Queued limit monitoring",
    )
    session.add(task)
    session.flush()
    return run, task


def dispatch_limit_monitoring(task_id: int, monitoring_run_id: int) -> None:
    """Submit the one composite worker after the caller commits its queue rows."""
    future = submit_async_task(
        execute_limit_monitoring_task,
        task_id,
        monitoring_run_id,
    )
    future.add_done_callback(
        lambda completed: _terminalize_failed_future(
            completed,
            task_id=task_id,
            monitoring_run_id=monitoring_run_id,
        )
    )


def _version_rule(snapshot: Mapping[str, Any]) -> LimitRule:
    return LimitRule(
        metric_kind=str(snapshot["metric_kind"]),
        source_kind=str(snapshot["source_kind"]),
        aggregation=str(snapshot["aggregation"]),
        transform=str(snapshot["transform"]),
        comparator=str(snapshot["comparator"]),
        warning_lower=snapshot.get("warning_lower"),
        warning_upper=snapshot.get("warning_upper"),
        hard_lower=snapshot.get("hard_lower"),
        hard_upper=snapshot.get("hard_upper"),
        unit=str(snapshot["unit"]),
        currency=snapshot.get("currency"),
        bump_convention=snapshot.get("bump_convention"),
    )


def _scope_from_snapshot(raw: Mapping[str, Any]) -> _ResolvedScope:
    return _ResolvedScope(
        scope_type=str(raw["scope_type"]),
        scope_key=str(raw["scope_key"]),
        scope_label=str(raw["scope_label"]),
        position_ids=tuple(int(value) for value in raw.get("position_ids") or []),
        value=raw.get("value"),
    )


def _scenario_source_config(source_input: Mapping[str, Any]) -> dict[str, Any]:
    from ..domains import scenario_catalog

    request = dict(source_input.get("scenario_request") or {})
    if not request:
        raise ValueError("scenario_test monitoring requires scenario_request")
    frozen = scenario_catalog.freeze_scenario_request(request)
    _specs, scenario_hash = scenario_catalog.frozen_scenario_specs(frozen)
    return {
        "scenario_request": scenario_catalog.strip_source_snapshot(frozen),
        "config": deepcopy(dict(source_input.get("config") or {})),
        "scenario_set_hash": scenario_hash,
    }


def _source_key(
    snapshot_version: Mapping[str, Any],
    scope: _ResolvedScope,
    inputs: Mapping[str, Any],
    source_inputs: Mapping[str, Any],
) -> SourcePlanKey:
    source_kind = str(snapshot_version["source_kind"])
    source_input = dict(source_inputs.get(source_kind) or {})
    if source_kind == "risk_run":
        methodology = {"method": str(source_input.get("method") or "summary")}
        config: dict[str, Any] = {}
    elif source_kind == "scenario_test":
        methodology = dict(snapshot_version["methodology"])
        config = _scenario_source_config(source_input)
    elif source_kind == "backtest":
        methodology = dict(snapshot_version["methodology"])
        if not isinstance(source_input.get("spec"), Mapping):
            raise ValueError("backtest monitoring requires source_inputs.backtest.spec")
        if not inputs.get("effective_market_evidence_id"):
            raise ValueError(
                "backtest monitoring requires effective_market_evidence_id"
            )
        config = {
            "spec": deepcopy(dict(source_input["spec"])),
            "config": deepcopy(dict(source_input.get("config") or {})),
        }
    else:
        raise ValueError(f"unsupported source kind {source_kind!r}")
    max_age = inputs.get("max_source_age_seconds")
    if max_age is None:
        max_age = (snapshot_version.get("freshness_policy") or {}).get(
            "max_age_seconds"
        )
    return SourcePlanKey.create(
        source_kind=source_kind,
        portfolio_id=int(inputs["portfolio_id"]),
        position_ids=scope.position_ids,
        pricing_parameter_profile_id=inputs.get("pricing_parameter_profile_id"),
        engine_config_id=inputs.get("engine_config_id"),
        # Historical backtests do not consume a point market snapshot; their
        # immutable historical evidence identity is mandatory above.
        market_snapshot_id=(
            None if source_kind == "backtest" else inputs.get("market_snapshot_id")
        ),
        effective_market_evidence_id=inputs.get("effective_market_evidence_id"),
        methodology=methodology,
        config=config,
        valuation_policy={"valuation_as_of": inputs["valuation_as_of"]},
        freshness_policy={"max_age_seconds": max_age},
    )


def _source_refresh(
    key: SourcePlanKey,
    *,
    session_factory: sessionmaker,
    source_inputs: Mapping[str, Any],
) -> Any:
    if key.source_kind == "risk_run":
        from ..batch_pricing import run_persisted_risk_source

        return run_persisted_risk_source(
            session_factory=session_factory,
            portfolio_id=key.portfolio_id,
            position_ids=list(key.position_ids),
            pricing_parameter_profile_id=key.pricing_parameter_profile_id,
            engine_config_id=key.engine_config_id,
            market_snapshot_id=key.market_snapshot_id,
            effective_market_evidence_id=key.effective_market_evidence_id,
            valuation_as_of=datetime.fromisoformat(
                key.valuation_policy["valuation_as_of"]
            ),
            method=str(key.methodology["method"]),
        )
    source_input = dict(source_inputs[key.source_kind])
    if key.source_kind == "scenario_test":
        from ..scenario_test_runner import run_persisted_scenario_source

        return run_persisted_scenario_source(
            session_factory=session_factory,
            portfolio_id=key.portfolio_id,
            scenario_request=deepcopy(dict(source_input["scenario_request"])),
            config=deepcopy(dict(source_input.get("config") or {})),
            pricing_parameter_profile_id=key.pricing_parameter_profile_id,
            engine_config_id=key.engine_config_id,
            position_ids=list(key.position_ids),
            valuation_as_of=datetime.fromisoformat(
                key.valuation_policy["valuation_as_of"]
            ),
            market_snapshot_id=key.market_snapshot_id,
            effective_market_evidence_id=key.effective_market_evidence_id,
            write_artifacts=False,
        )
    if key.source_kind == "backtest":
        from ..backtest_runner import run_persisted_backtest_source

        return run_persisted_backtest_source(
            session_factory=session_factory,
            portfolio_id=key.portfolio_id,
            spec=deepcopy(dict(source_input["spec"])),
            config=deepcopy(dict(source_input.get("config") or {})),
            pricing_parameter_profile_id=key.pricing_parameter_profile_id,
            engine_config_id=key.engine_config_id,
            position_ids=list(key.position_ids),
            valuation_as_of=datetime.fromisoformat(
                key.valuation_policy["valuation_as_of"]
            ),
            market_snapshot_id=key.market_snapshot_id,
            effective_market_evidence_id=key.effective_market_evidence_id,
            write_artifacts=False,
        )
    raise ValueError(f"unsupported source kind {key.source_kind!r}")


def _source_id(source: Any) -> int | None:
    value = getattr(source, "id", None)
    return (
        int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    )


def _source_diagnostics(source: Any, selection: SourceSelection) -> dict[str, Any]:
    """Freeze producer-wide completeness evidence on the durable reference."""
    diagnostics: dict[str, Any] = {
        "reason_code": selection.reason_code,
        "reused": selection.reused,
        "is_fresh": selection.is_fresh,
    }
    if source is None:
        return diagnostics
    payload = source.metrics if isinstance(source, RiskRun) else source.results
    metadata = dict((payload or {}).get("source_metadata") or {})
    diagnostics.update(
        {
            "source_status": source.status,
            "resolved_position_ids": list(source.resolved_position_ids or []),
            "market_evidence_complete": metadata.get("market_evidence_complete"),
            "missing_market_evidence": deepcopy(
                metadata.get("missing_market_evidence")
            ),
        }
    )
    if isinstance(source, (ScenarioTestRun, BacktestRun)):
        diagnostics["excluded_positions"] = deepcopy(source.excluded_positions or [])
    return diagnostics


def _resolve_source_groups(
    session_factory: sessionmaker,
    *,
    monitoring_run_id: int,
    snapshot: Mapping[str, Any],
    selection_now: datetime,
) -> tuple[dict[tuple[int, str], _ResolvedGroup], dict[SourcePlanKey, _ResolvedGroup]]:
    inputs = dict(snapshot["inputs"])
    source_inputs = dict(snapshot.get("source_inputs") or {})
    requests: list[SourcePlanRequest] = []
    request_lookup: dict[tuple[int, str], SourcePlanKey] = {}
    for version in snapshot.get("versions") or []:
        for raw_scope in version.get("scopes") or []:
            scope = _scope_from_snapshot(raw_scope)
            key = _source_key(version, scope, inputs, source_inputs)
            request = SourcePlanRequest(limit_version_id=int(version["id"]), key=key)
            requests.append(request)
            request_lookup[(int(version["id"]), scope.scope_key)] = key
    by_key: dict[SourcePlanKey, _ResolvedGroup] = {}
    for group in group_source_plans(requests):
        selection = select_source(
            session_factory,
            group.key,
            policy=str(inputs["source_policy"]),
            now=selection_now,
            refresh=lambda key: _source_refresh(
                key,
                session_factory=session_factory,
                source_inputs=source_inputs,
            ),
        )
        source = selection.run
        diagnostics = _source_diagnostics(source, selection)
        with session_factory() as session:
            reference = persist_source_reference(
                session,
                monitoring_run_id=monitoring_run_id,
                key=group.key,
                source=source,
                source_status="missing" if source is None else selection.reason_code,
                is_fresh=selection.is_fresh,
                diagnostics=diagnostics,
            )
            session.commit()
            resolved = _ResolvedGroup(
                key=group.key,
                limit_version_ids=group.limit_version_ids,
                source_id=_source_id(source),
                source_status=(source.status if source is not None else "missing"),
                is_fresh=selection.is_fresh,
                reused=selection.reused,
                reason_code=selection.reason_code,
                reference_id=reference.id,
            )
        by_key[group.key] = resolved
    mapped = {item: by_key[key] for item, key in request_lookup.items()}
    return mapped, by_key


def _load_source(
    session: Session, group: _ResolvedGroup
) -> RiskRun | ScenarioTestRun | BacktestRun | None:
    if group.source_id is None:
        return None
    model = {
        "risk_run": RiskRun,
        "scenario_test": ScenarioTestRun,
        "backtest": BacktestRun,
    }[group.key.source_kind]
    return session.get(model, group.source_id)


def _observation(
    session: Session,
    *,
    rule: LimitRule,
    scope: _ResolvedScope,
    group: _ResolvedGroup,
    valuation_as_of: datetime,
) -> NormalizedObservation:
    source = _load_source(session, group)
    if source is None:
        return NormalizedObservation(
            values=None,
            source_kind=rule.source_kind,
            unit=rule.unit,
            currency=rule.currency,
            bump_convention=rule.bump_convention,
            source_status="missing",
            reason_code="missing_source",
            reason="No eligible source evidence was available.",
            evidence={"source_reference_id": group.reference_id},
        )
    if isinstance(source, RiskRun):
        observation = adapt_risk_run(
            session,
            source,
            metric_kind=rule.metric_kind,
            aggregation=rule.aggregation,
            unit=rule.unit,
            currency=rule.currency,
            bump_convention=rule.bump_convention,
            scope=ObservationScope(
                scope.scope_type,
                value=scope.value,
                position_ids=scope.position_ids,
            ),
            valuation_as_of=valuation_as_of,
        )
    elif isinstance(source, ScenarioTestRun):
        observation = adapt_scenario_test_run(
            source,
            metric_kind=rule.metric_kind,
            methodology=group.key.methodology,
            unit=rule.unit,
            currency=rule.currency,
            session=session,
            valuation_as_of=valuation_as_of,
        )
    else:
        observation = adapt_backtest_run(
            source,
            metric_kind=rule.metric_kind,
            methodology=group.key.methodology,
            unit=rule.unit,
            currency=rule.currency,
            session=session,
            valuation_as_of=valuation_as_of,
        )
    evidence = deepcopy(observation.evidence)
    evidence["source_reference_id"] = group.reference_id
    return NormalizedObservation(
        values=observation.values,
        source_kind=observation.source_kind,
        unit=observation.unit,
        currency=observation.currency,
        bump_convention=observation.bump_convention,
        source_status=observation.source_status,
        is_stale=not group.is_fresh and observation.reason_code is None,
        is_complete=observation.is_complete,
        reason_code=observation.reason_code,
        reason=observation.reason,
        coverage_count=observation.coverage_count,
        coverage_ratio=observation.coverage_ratio,
        evidence=evidence,
    )


def _persist_evaluation(
    session: Session,
    *,
    monitoring_run_id: int,
    version_id: int,
    scope: _ResolvedScope,
    result: EvaluationResult,
) -> LimitEvaluation:
    row = LimitEvaluation(
        monitoring_run_id=monitoring_run_id,
        limit_version_id=version_id,
        scope_type=scope.scope_type,
        scope_key=scope.scope_key,
        scope_label=scope.scope_label,
        observed_value=result.observed_value,
        adverse_value=result.adverse_value,
        warning_lower=result.warning_lower,
        warning_upper=result.warning_upper,
        hard_lower=result.hard_lower,
        hard_upper=result.hard_upper,
        utilization=result.utilization,
        headroom=result.headroom,
        governing_boundary=result.governing_boundary,
        status=result.status,
        reason_code=result.reason_code,
        reason=result.reason,
        coverage_count=result.coverage_count,
        coverage_ratio=result.coverage_ratio,
        evidence=deepcopy(result.evidence),
    )
    session.add(row)
    return row


def _default_incident_reconciler(
    session: Session,
    *,
    monitoring_run: LimitMonitoringRun,
    evaluations: list[LimitEvaluation],
    context: LimitActionContext,
) -> None:
    """Lazy Task 8 seam; production picks it up as soon as it is available."""
    try:
        from .incidents import reconcile_monitoring_incidents
    except ImportError:
        return
    reconcile_monitoring_incidents(
        session,
        monitoring_run=monitoring_run,
        evaluations=evaluations,
        context=context,
    )


def _claim_monitoring_task(
    session_factory: sessionmaker,
    *,
    task_id: int,
    monitoring_run_id: int,
) -> bool:
    """Atomically claim one queued task without touching mismatched pairs."""
    with session_factory() as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            raise ValueError(f"limit monitoring task not found: {task_id}")
        if (
            task.kind != TaskKind.LIMIT_MONITORING.value
            or task.limit_monitoring_run_id != monitoring_run_id
        ):
            raise ValueError("limit monitoring task/run linkage is invalid")
        run = session.get(LimitMonitoringRun, monitoring_run_id)
        if run is None:
            raise ValueError(f"limit monitoring run not found: {monitoring_run_id}")
        if task.status != TaskStatus.QUEUED.value:
            return False
        if run.status != TaskStatus.QUEUED.value:
            return False

        now = datetime.utcnow()
        claimed_task = session.execute(
            update(TaskRun)
            .where(
                TaskRun.id == task_id,
                TaskRun.kind == TaskKind.LIMIT_MONITORING.value,
                TaskRun.limit_monitoring_run_id == monitoring_run_id,
                TaskRun.status == TaskStatus.QUEUED.value,
            )
            .values(
                status=TaskStatus.RUNNING.value,
                started_at=now,
                finished_at=None,
                error=None,
                message="Running limit monitoring",
            )
            .execution_options(synchronize_session=False)
        )
        if claimed_task.rowcount != 1:
            session.rollback()
            return False
        claimed_run = session.execute(
            update(LimitMonitoringRun)
            .where(
                LimitMonitoringRun.id == monitoring_run_id,
                LimitMonitoringRun.status == TaskStatus.QUEUED.value,
            )
            .values(
                status=TaskStatus.RUNNING.value,
                started_at=now,
                finished_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed_run.rowcount != 1:
            session.rollback()
            return False
        session.commit()
        return True


def _finalize(
    session_factory: sessionmaker,
    *,
    task_id: int,
    monitoring_run_id: int,
    snapshot: Mapping[str, Any],
    groups: Mapping[tuple[int, str], _ResolvedGroup],
    incident_reconciler: IncidentReconciler,
) -> None:
    with session_factory() as session:
        run = session.get(LimitMonitoringRun, monitoring_run_id)
        if run is None:
            raise ValueError(f"limit monitoring run not found: {monitoring_run_id}")
        evaluations: list[LimitEvaluation] = []
        valuation = datetime.fromisoformat(str(snapshot["inputs"]["valuation_as_of"]))
        for version_snapshot in snapshot.get("versions") or []:
            version_id = int(version_snapshot["id"])
            rule = _version_rule(version_snapshot)
            for raw_scope in version_snapshot.get("scopes") or []:
                scope = _scope_from_snapshot(raw_scope)
                group = groups[(version_id, scope.scope_key)]
                result = evaluate(
                    rule,
                    _observation(
                        session,
                        rule=rule,
                        scope=scope,
                        group=group,
                        valuation_as_of=valuation,
                    ),
                )
                evaluations.append(
                    _persist_evaluation(
                        session,
                        monitoring_run_id=run.id,
                        version_id=version_id,
                        scope=scope,
                        result=result,
                    )
                )
        session.flush()
        counts = {status: 0 for status in ("ok", "warning", "breach", "unknown")}
        for row in evaluations:
            counts[row.status] = counts.get(row.status, 0) + 1
        run.summary = {**counts, "total": len(evaluations)}
        run.status = "completed_with_unknowns" if counts["unknown"] else "completed"
        run.finished_at = datetime.utcnow()
        incident_reconciler(
            session,
            monitoring_run=run,
            evaluations=evaluations,
            context=_context_from_snapshot(snapshot),
        )
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.COMPLETED.value,
            message="Limit monitoring completed",
            result_payload={"limit_monitoring_run_id": run.id},
        )
        session.commit()


def _mark_failed(
    session_factory: sessionmaker,
    *,
    task_id: int,
    monitoring_run_id: int,
    error: str,
) -> None:
    with session_factory() as session:
        task = session.get(TaskRun, task_id)
        if (
            task is None
            or task.kind != TaskKind.LIMIT_MONITORING.value
            or task.limit_monitoring_run_id != monitoring_run_id
            or task.status not in (
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
            )
        ):
            return
        run = session.get(LimitMonitoringRun, monitoring_run_id)
        if (
            run is None
            or run.status not in (
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
            )
        ):
            return
        run.status = TaskStatus.FAILED.value
        run.finished_at = datetime.utcnow()
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.FAILED.value,
            message="Limit monitoring failed",
            error=error,
            result_payload={"limit_monitoring_run_id": monitoring_run_id},
        )
        session.commit()


def _terminalize_failed_future(
    future: Future[Any],
    *,
    task_id: int,
    monitoring_run_id: int,
) -> None:
    """Release a queued/running portfolio slot if the worker Future aborts.

    The worker persists ordinary post-claim failures itself. This callback
    covers bootstrap and pre-claim exceptions that otherwise only live on the
    discarded Future.
    """
    if future.cancelled():
        error = "Limit monitoring worker was cancelled before claiming its task"
    else:
        failure = future.exception()
        if failure is None:
            return
        error = str(failure) or type(failure).__name__
    try:
        _mark_failed(
            database.SessionLocal,
            task_id=task_id,
            monitoring_run_id=monitoring_run_id,
            error=error,
        )
    except Exception:  # noqa: BLE001 - callbacks must not hide the worker failure
        _LOGGER.exception(
            "Could not persist failed limit-monitoring Future for task %s / run %s",
            task_id,
            monitoring_run_id,
        )


def execute_limit_monitoring_task(
    task_id: int,
    monitoring_run_id: int,
    session_factory: sessionmaker | None = None,
    *,
    incident_reconciler: IncidentReconciler | None = None,
    selection_now: datetime | None = None,
) -> None:
    """Worker entrypoint.  It never submits or waits for a nested task."""
    database.init_db()
    factory = session_factory or database.SessionLocal
    try:
        claimed = _claim_monitoring_task(
            factory,
            task_id=task_id,
            monitoring_run_id=monitoring_run_id,
        )
    except (TypeError, ValueError):
        # A malformed or mismatched dispatch is not entitled to mutate either
        # durable row. Legitimate retries see a non-queued task and no-op.
        return
    if not claimed:
        return
    try:
        with factory() as session:
            run = session.get(LimitMonitoringRun, monitoring_run_id)
            task = session.get(TaskRun, task_id)
            if run is None or task is None or task.limit_monitoring_run_id != run.id:
                raise ValueError("limit monitoring task/run linkage is invalid")
            snapshot = deepcopy(run.definition_snapshot)
            if _snapshot_hash(snapshot) != run.definition_snapshot_hash:
                raise ValueError("limit monitoring definition snapshot hash mismatch")
        groups, _ = _resolve_source_groups(
            factory,
            monitoring_run_id=monitoring_run_id,
            snapshot=snapshot,
            selection_now=selection_now or datetime.utcnow(),
        )
        _finalize(
            factory,
            task_id=task_id,
            monitoring_run_id=monitoring_run_id,
            snapshot=snapshot,
            groups=groups,
            incident_reconciler=incident_reconciler or _default_incident_reconciler,
        )
    except Exception as exc:  # noqa: BLE001 - task boundary persists failures
        _mark_failed(
            factory,
            task_id=task_id,
            monitoring_run_id=monitoring_run_id,
            error=str(exc),
        )

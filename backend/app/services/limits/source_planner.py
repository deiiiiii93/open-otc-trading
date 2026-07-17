"""Canonical source grouping, exact reuse selection, and evidence references."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    BacktestRun,
    LimitSourceReference,
    RiskRun,
    ScenarioTestRun,
)
from .sources import (
    BACKTEST_TAIL_METHODOLOGY,
    SCENARIO_TAIL_METHODOLOGY,
)


_POLICIES = frozenset({"reuse_only", "refresh_if_stale", "force_refresh"})
_MODELS = {
    "risk_run": RiskRun,
    "scenario_test": ScenarioTestRun,
    "backtest": BacktestRun,
}
_TERMINAL_STATUSES = {
    "risk_run": ("completed", "completed_with_errors"),
    "scenario_test": ("completed", "empty"),
    "backtest": ("completed", "empty"),
}


def _canonical_json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_mapping(value: str) -> dict[str, Any]:
    return json.loads(value)


@dataclass(frozen=True, slots=True)
class SourcePlanKey:
    """Hashable exact identity for one reusable persisted producer run."""

    source_kind: str
    portfolio_id: int
    position_ids: tuple[int, ...]
    pricing_parameter_profile_id: int | None
    engine_config_id: int | None
    market_snapshot_id: int | None
    effective_market_evidence_id: str | None
    methodology_json: str
    config_json: str
    valuation_policy_json: str
    freshness_policy_json: str

    @classmethod
    def create(
        cls,
        *,
        source_kind: str,
        portfolio_id: int,
        position_ids: Iterable[int],
        pricing_parameter_profile_id: int | None,
        engine_config_id: int | None,
        market_snapshot_id: int | None,
        effective_market_evidence_id: str | None,
        methodology: Mapping[str, Any],
        config: Mapping[str, Any],
        valuation_policy: Mapping[str, Any],
        freshness_policy: Mapping[str, Any],
    ) -> SourcePlanKey:
        if source_kind not in _MODELS:
            raise ValueError(f"unsupported source kind {source_kind!r}")
        normalized_positions = tuple(sorted({int(value) for value in position_ids}))
        normalized_evidence_id = effective_market_evidence_id
        if market_snapshot_id is None and normalized_evidence_id is None:
            if pricing_parameter_profile_id is None:
                raise ValueError(
                    "market_snapshot_id or effective_market_evidence_id is required"
                )
            normalized_evidence_id = (
                f"pricing_parameter_profile:{pricing_parameter_profile_id}"
            )
        return cls(
            source_kind=source_kind,
            portfolio_id=int(portfolio_id),
            position_ids=normalized_positions,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            engine_config_id=engine_config_id,
            market_snapshot_id=market_snapshot_id,
            effective_market_evidence_id=normalized_evidence_id,
            methodology_json=_canonical_json(methodology),
            config_json=_canonical_json(config),
            valuation_policy_json=_canonical_json(valuation_policy),
            freshness_policy_json=_canonical_json(freshness_policy),
        )

    @property
    def methodology(self) -> dict[str, Any]:
        return _json_mapping(self.methodology_json)

    @property
    def config(self) -> dict[str, Any]:
        return _json_mapping(self.config_json)

    @property
    def valuation_policy(self) -> dict[str, Any]:
        return _json_mapping(self.valuation_policy_json)

    @property
    def freshness_policy(self) -> dict[str, Any]:
        return _json_mapping(self.freshness_policy_json)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "portfolio_id": self.portfolio_id,
            "position_ids": list(self.position_ids),
            "pricing_parameter_profile_id": self.pricing_parameter_profile_id,
            "engine_config_id": self.engine_config_id,
            "market_snapshot_id": self.market_snapshot_id,
            "effective_market_evidence_id": self.effective_market_evidence_id,
            "methodology": self.methodology,
            "config": self.config,
            "valuation_policy": self.valuation_policy,
            "freshness_policy": self.freshness_policy,
        }


@dataclass(frozen=True, slots=True)
class SourcePlanRequest:
    limit_version_id: int
    key: SourcePlanKey


@dataclass(frozen=True, slots=True)
class SourcePlanGroup:
    key: SourcePlanKey
    limit_version_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SourceSelection:
    run: RiskRun | ScenarioTestRun | BacktestRun | Any | None
    reused: bool
    is_fresh: bool
    reason_code: str | None


def group_source_plans(
    requests: Iterable[SourcePlanRequest],
) -> tuple[SourcePlanGroup, ...]:
    grouped: dict[SourcePlanKey, list[int]] = {}
    for request in requests:
        grouped.setdefault(request.key, []).append(int(request.limit_version_id))
    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            item[0].source_kind,
            item[0].portfolio_id,
            item[0].position_ids,
            item[0].methodology_json,
            item[0].config_json,
        ),
    )
    return tuple(
        SourcePlanGroup(
            key=key,
            limit_version_ids=tuple(sorted(set(version_ids))),
        )
        for key, version_ids in ordered
    )


def _source_payload(
    source: RiskRun | ScenarioTestRun | BacktestRun,
) -> dict[str, Any]:
    return source.metrics if isinstance(source, RiskRun) else source.results


def _source_metadata(
    source: RiskRun | ScenarioTestRun | BacktestRun,
) -> dict[str, Any]:
    payload = _source_payload(source) or {}
    return dict(payload.get("source_metadata") or {})


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif not isinstance(value, str):
        return None
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def source_valuation_at(
    source: RiskRun | ScenarioTestRun | BacktestRun,
) -> datetime:
    payload = _source_payload(source) or {}
    metadata = _source_metadata(source)
    value = (
        payload.get("valuation_as_of")
        if isinstance(source, RiskRun)
        else metadata.get("valuation_as_of")
    )
    return _parse_datetime(value) or source.created_at


def _matches_methodology(
    source: RiskRun | ScenarioTestRun | BacktestRun,
    key: SourcePlanKey,
) -> bool:
    if isinstance(source, RiskRun):
        return key.methodology == {"method": source.method}
    if isinstance(source, ScenarioTestRun):
        return key.methodology == SCENARIO_TAIL_METHODOLOGY
    return key.methodology == BACKTEST_TAIL_METHODOLOGY


def _matches_config(
    source: RiskRun | ScenarioTestRun | BacktestRun,
    key: SourcePlanKey,
) -> bool:
    expected = key.config
    if isinstance(source, RiskRun):
        return expected == {}
    if isinstance(source, ScenarioTestRun):
        return expected == {
            "scenario_request": source.scenario_spec or {},
            "config": source.config or {},
        }
    return expected == {
        "spec": source.spec or {},
        "config": source.config or {},
    }


def _matches_identity(
    source: RiskRun | ScenarioTestRun | BacktestRun,
    key: SourcePlanKey,
) -> bool:
    if tuple(sorted(source.resolved_position_ids or [])) != key.position_ids:
        return False
    metadata = _source_metadata(source)
    actual_market_snapshot_id = (
        source.market_snapshot_id
        if isinstance(source, RiskRun)
        else metadata.get("market_snapshot_id")
    )
    if actual_market_snapshot_id != key.market_snapshot_id:
        return False
    if (
        (
            metadata.get("effective_market_evidence_id")
            or (
                f"pricing_parameter_profile:"
                f"{source.pricing_parameter_profile_id}"
                if source.pricing_parameter_profile_id is not None
                and actual_market_snapshot_id is None
                else None
            )
        )
        != key.effective_market_evidence_id
    ):
        return False
    if not _matches_methodology(source, key) or not _matches_config(source, key):
        return False
    requested_valuation = key.valuation_policy.get("valuation_as_of")
    if requested_valuation is not None:
        normalized_requested = _parse_datetime(requested_valuation)
        if normalized_requested is None:
            return False
        if source_valuation_at(source) != normalized_requested:
            return False
    return True


def _candidate_sources(
    session: Session,
    key: SourcePlanKey,
) -> list[RiskRun | ScenarioTestRun | BacktestRun]:
    model = _MODELS[key.source_kind]
    stmt = (
        select(model)
        .where(
            model.portfolio_id == key.portfolio_id,
            model.pricing_parameter_profile_id
            == key.pricing_parameter_profile_id,
            model.engine_config_id == key.engine_config_id,
            model.status.in_(_TERMINAL_STATUSES[key.source_kind]),
        )
        .order_by(model.created_at.desc(), model.id.desc())
    )
    return list(session.execute(stmt).scalars().all())


def _is_fresh(
    source: RiskRun | ScenarioTestRun | BacktestRun,
    key: SourcePlanKey,
    now: datetime,
) -> bool:
    max_age = key.freshness_policy.get("max_age_seconds")
    if max_age is None:
        return True
    if (
        isinstance(max_age, bool)
        or not isinstance(max_age, (int, float))
        or max_age < 0
    ):
        return False
    age = (now - source.created_at).total_seconds()
    return 0 <= age <= float(max_age)


def find_reusable_source(
    session: Session,
    key: SourcePlanKey,
    *,
    now: datetime,
) -> SourceSelection:
    exact = [
        source
        for source in _candidate_sources(session, key)
        if _matches_identity(source, key)
    ]
    if not exact:
        return SourceSelection(
            run=None,
            reused=False,
            is_fresh=False,
            reason_code="missing_source",
        )
    source = exact[0]
    fresh = _is_fresh(source, key, now)
    return SourceSelection(
        run=source if fresh else None,
        reused=fresh,
        is_fresh=fresh,
        reason_code=None if fresh else "stale_source",
    )


def select_source(
    session: Session,
    key: SourcePlanKey,
    *,
    policy: str,
    now: datetime,
    refresh: Callable[[SourcePlanKey], Any] | None = None,
) -> SourceSelection:
    """Apply exact reuse/refresh policy without allowing active runs to mask history."""
    if policy not in _POLICIES:
        raise ValueError(f"unsupported source policy {policy!r}")
    if policy == "force_refresh":
        if refresh is None:
            raise ValueError("force_refresh requires a refresh callback")
        return SourceSelection(
            run=refresh(key),
            reused=False,
            is_fresh=True,
            reason_code=None,
        )

    reusable = find_reusable_source(session, key, now=now)
    if reusable.run is not None or policy == "reuse_only":
        return reusable
    if refresh is None:
        return reusable
    return SourceSelection(
        run=refresh(key),
        reused=False,
        is_fresh=True,
        reason_code=None,
    )


def persist_source_reference(
    session: Session,
    *,
    monitoring_run_id: int,
    key: SourcePlanKey,
    source: RiskRun | ScenarioTestRun | BacktestRun,
    is_fresh: bool,
    diagnostics: Mapping[str, Any],
) -> LimitSourceReference:
    """Persist the exact source link and completeness decision for audit."""
    source_ids = {
        "risk_run_id": source.id if isinstance(source, RiskRun) else None,
        "scenario_test_run_id": (
            source.id if isinstance(source, ScenarioTestRun) else None
        ),
        "backtest_run_id": (
            source.id if isinstance(source, BacktestRun) else None
        ),
    }
    reference = LimitSourceReference(
        monitoring_run_id=monitoring_run_id,
        source_kind=key.source_kind,
        requested_parameters=key.as_dict(),
        source_status=source.status,
        is_fresh=bool(is_fresh),
        completeness_diagnostics=json.loads(
            json.dumps(
                dict(diagnostics),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        ),
        source_valuation_at=source_valuation_at(source),
        source_created_at=source.created_at,
        **source_ids,
    )
    session.add(reference)
    session.flush()
    return reference

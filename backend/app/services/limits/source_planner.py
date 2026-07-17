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
    "scenario_test": ("completed",),
    "backtest": ("completed",),
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
        normalized_evidence_id = (
            str(effective_market_evidence_id).strip()
            if effective_market_evidence_id is not None
            else None
        )
        if market_snapshot_id is None and normalized_evidence_id is None:
            raise ValueError(
                "market_snapshot_id or effective_market_evidence_id is required"
            )
        if normalized_evidence_id == "":
            raise ValueError("effective_market_evidence_id must be nonblank")
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
    metadata = _source_metadata(source)
    if metadata.get("market_evidence_complete") is False:
        return False
    if isinstance(source, RiskRun):
        return (
            metadata.get("methodology") == key.methodology
            and key.methodology == {"method": source.method}
        )
    if isinstance(source, ScenarioTestRun):
        if metadata.get("methodology") != SCENARIO_TAIL_METHODOLOGY:
            return False
        scenario_hash = metadata.get("scenario_set_hash")
        if not isinstance(scenario_hash, str) or not scenario_hash:
            return False
        if key.methodology == SCENARIO_TAIL_METHODOLOGY:
            return True
        if key.methodology.get("scenario_set_hash") != scenario_hash:
            return False
        scenario_names = metadata.get("scenario_names")
        if not isinstance(scenario_names, list) or not all(
            isinstance(name, str) and name for name in scenario_names
        ):
            return False
        selection = key.methodology.get("selection")
        if selection == "named":
            return key.methodology.get("scenario_name") in scenario_names
        if selection == "worst_of_set":
            requested_names = key.methodology.get("scenario_names")
            return (
                isinstance(requested_names, list)
                and bool(requested_names)
                and all(name in scenario_names for name in requested_names)
            )
        return False
    return (
        metadata.get("methodology") == BACKTEST_TAIL_METHODOLOGY
        and key.methodology == BACKTEST_TAIL_METHODOLOGY
    )


def _matches_config(
    source: RiskRun | ScenarioTestRun | BacktestRun,
    key: SourcePlanKey,
) -> bool:
    return _source_metadata(source).get("source_config") == key.config


def _matches_identity(
    session: Session,
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
        metadata.get("effective_market_evidence_id")
        != key.effective_market_evidence_id
    ):
        return False
    if not _market_evidence_is_current(session, source, metadata):
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


def _market_evidence_is_current(
    session: Session,
    source: RiskRun | ScenarioTestRun | BacktestRun,
    metadata: Mapping[str, Any],
) -> bool:
    evidence_id = metadata.get("effective_market_evidence_id")
    if not isinstance(evidence_id, str):
        return False
    if evidence_id.startswith("risk-market-evidence/v1:"):
        try:
            from ...models import Position
            from ..risk_engine import _pricing_position_context
            from ..source_evidence import (
                build_market_evidence_manifest,
                canonical_hash,
            )

            positions = [
                session.get(Position, position_id)
                for position_id in source.resolved_position_ids or []
            ]
            if any(position is None for position in positions):
                return False
            valuation = source_valuation_at(source)
            market_snapshot_id = (
                source.market_snapshot_id
                if isinstance(source, RiskRun)
                else metadata.get("market_snapshot_id")
            )
            markets, _failures, diagnostics = _pricing_position_context(
                session,
                positions,
                pricing_parameter_profile_id=source.pricing_parameter_profile_id,
                valuation_date=valuation,
                market_snapshot_id=market_snapshot_id,
            )
            from ..engine_configs import (
                get_engine_config,
                resolve_pricing_engine,
            )

            engine_config = get_engine_config(session, source.engine_config_id)
            for position in positions:
                engine = resolve_pricing_engine(position, engine_config)
                diagnostics.setdefault(position.id, {})[
                    "resolved_engine"
                ] = engine.diagnostics()
            manifest = build_market_evidence_manifest(
                session,
                positions=positions,
                position_markets=markets,
                pricing_diagnostics=diagnostics,
                valuation_as_of=valuation,
                market_snapshot_id=market_snapshot_id,
            )
            return canonical_hash(manifest) == metadata.get(
                "market_evidence_hash"
            )
        except Exception:
            return False
    if evidence_id.startswith("backtest-market-evidence/v1:"):
        try:
            from ...models import MarketDataProfile, Position
            from ..engine_configs import get_engine_config, resolve_pricing_engine
            from ..source_evidence import (
                backtest_position_evidence,
                canonical_hash,
                datetime_iso,
            )

            manifest = metadata.get("market_evidence_manifest") or {}
            manifest_hash = canonical_hash(manifest)
            if manifest_hash != metadata.get("market_evidence_hash"):
                return False
            if evidence_id != (
                "backtest-market-evidence/v1:"
                f"{manifest_hash.removeprefix('sha256:')}"
            ):
                return False
            stored_positions = manifest.get("positions")
            if not isinstance(stored_positions, list):
                return False
            positions = [
                session.get(Position, position_id)
                for position_id in source.resolved_position_ids or []
            ]
            if any(position is None for position in positions):
                return False
            engine_config = get_engine_config(session, source.engine_config_id)
            current_positions = [
                backtest_position_evidence(
                    position,
                    resolve_pricing_engine(position, engine_config),
                )
                for position in sorted(positions, key=lambda row: int(row.id))
            ]
            if current_positions != stored_positions:
                return False
            for underlying in manifest.get("underlyings") or []:
                for field in ("spot_profile", "futures_profile"):
                    stored = underlying.get(field)
                    if stored is None:
                        continue
                    row = session.get(MarketDataProfile, stored.get("id"))
                    if row is None:
                        return False
                    current = {
                        "id": row.id,
                        "symbol": row.symbol,
                        "updated_at": datetime_iso(row.updated_at),
                        "data_hash": canonical_hash(row.data or {}),
                    }
                    if field == "spot_profile":
                        current.update(
                            {
                                "asset_class": row.asset_class,
                                "adjust": row.adjust,
                            }
                        )
                    if current != stored:
                        return False
                    latest = (
                        session.query(MarketDataProfile)
                        .filter(
                            MarketDataProfile.symbol == row.symbol,
                            MarketDataProfile.asset_class == row.asset_class,
                        )
                        .order_by(MarketDataProfile.id.desc())
                        .first()
                    )
                    if latest is None or latest.id != row.id:
                        return False
            return True
        except Exception:
            return False
    # Externally supplied canonical evidence namespaces remain supported; the
    # planner cannot resolve their backing rows and therefore relies on the
    # producer's persisted exact identifier.
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
    metadata = _source_metadata(source)
    if (
        metadata.get("valuation_origin") == "profile"
        and key.freshness_policy.get("allow_profile_dated", False) is not True
    ):
        return False
    normalized_now = _parse_datetime(now)
    normalized_created = _parse_datetime(source.created_at)
    if normalized_now is None or normalized_created is None:
        return False
    age = (normalized_now - normalized_created).total_seconds()
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
        if _matches_identity(session, source, key)
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
        run=source,
        reused=fresh,
        is_fresh=fresh,
        reason_code=None if fresh else "stale_source",
    )


def _selection_session(
    session_or_factory: Session | Callable[[], Session],
) -> tuple[Session, bool]:
    if isinstance(session_or_factory, Session):
        return session_or_factory, False
    return session_or_factory(), True


def _detach_selection(session: Session, selection: SourceSelection) -> SourceSelection:
    if selection.run is not None:
        session.expunge(selection.run)
    return selection


def _refreshed_source_id(key: SourcePlanKey, refreshed: Any) -> int | None:
    attr = {
        "risk_run": "risk_run_id",
        "scenario_test": "scenario_test_run_id",
        "backtest": "backtest_run_id",
    }[key.source_kind]
    value = getattr(refreshed, attr, None)
    if value is None and isinstance(refreshed, _MODELS[key.source_kind]):
        value = refreshed.id
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _validate_refreshed_source(
    session_or_factory: Callable[[], Session],
    key: SourcePlanKey,
    *,
    refreshed: Any,
    now: datetime,
) -> SourceSelection:
    source_id = _refreshed_source_id(key, refreshed)
    if source_id is None:
        return SourceSelection(
            run=None,
            reused=False,
            is_fresh=False,
            reason_code="refresh_failed",
        )
    with session_or_factory() as session:
        source = session.get(_MODELS[key.source_kind], source_id)
        if source is None:
            return SourceSelection(
                run=None,
                reused=False,
                is_fresh=False,
                reason_code="refresh_failed",
            )
        if source.status not in _TERMINAL_STATUSES[key.source_kind]:
            reason = (
                "empty_source"
                if source.status == "empty"
                else "source_failed"
                if source.status == "failed"
                else "refresh_failed"
            )
            session.expunge(source)
            return SourceSelection(
                run=source,
                reused=False,
                is_fresh=False,
                reason_code=reason,
            )
        if not _matches_identity(session, source, key):
            session.expunge(source)
            return SourceSelection(
                run=source,
                reused=False,
                is_fresh=False,
                reason_code="refresh_mismatch",
            )
        fresh = _is_fresh(source, key, now)
        session.expunge(source)
        return SourceSelection(
            run=source,
            reused=False,
            is_fresh=fresh,
            reason_code=None if fresh else "stale_source",
        )


def select_source(
    session_or_factory: Session | Callable[[], Session],
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
        if isinstance(session_or_factory, Session):
            raise ValueError(
                "refresh policies require a session factory so selection closes "
                "before the refresh callback"
            )
        return _validate_refreshed_source(
            session_or_factory,
            key,
            refreshed=refresh(key),
            now=now,
        )

    session, should_close = _selection_session(session_or_factory)
    try:
        reusable = find_reusable_source(session, key, now=now)
        if should_close:
            reusable = _detach_selection(session, reusable)
    finally:
        if should_close:
            session.close()
    if reusable.is_fresh or policy == "reuse_only":
        return reusable
    if refresh is None:
        return reusable
    if isinstance(session_or_factory, Session):
        raise ValueError(
            "refresh policies require a session factory so selection closes "
            "before the refresh callback"
        )
    return _validate_refreshed_source(
        session_or_factory,
        key,
        refreshed=refresh(key),
        now=now,
    )


def persist_source_reference(
    session: Session,
    *,
    monitoring_run_id: int,
    key: SourcePlanKey,
    source: RiskRun | ScenarioTestRun | BacktestRun | None,
    source_status: str | None = None,
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
        source_status=(
            source.status
            if source is not None
            else source_status or "missing"
        ),
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
        source_valuation_at=(
            source_valuation_at(source) if source is not None else None
        ),
        source_created_at=source.created_at if source is not None else None,
        **source_ids,
    )
    session.add(reference)
    session.flush()
    return reference

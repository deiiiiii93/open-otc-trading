"""Versioned risk-limit definition governance.

V1 effective dating is deliberately immediate-only. Public draft input must
omit ``effective_from``; activation owns and writes the authoritative start.
All persisted governance timestamps are UTC-naive: aware inputs are converted
to UTC and naive inputs are interpreted as UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models import RiskLimit, RiskLimitVersion, utcnow
from ..audit import record_audit
from ..currency_codes import is_valid_currency, normalize_currency
from .contracts import LimitActionContext, LimitVersionSpec
from .errors import (
    LimitConflictError,
    LimitImmutableError,
    LimitNotFoundError,
    LimitValidationError,
)


_KEY = re.compile(r"^[a-z][a-z0-9_-]{2,119}$")
_CATEGORIES = frozenset({"greek", "var", "cvar", "stress"})
_GREEKS = frozenset({"delta", "gamma", "vega", "theta", "rho", "rho_q"})
_METRICS = _GREEKS | {"var", "cvar", "stress_pnl"}
_SOURCES = frozenset({"risk_run", "scenario_test", "backtest"})
_SCOPES = frozenset({"portfolio", "underlying", "product_family", "position"})
_AGGREGATIONS = frozenset(
    {"net", "gross_abs", "max_abs", "minimum", "maximum"}
)
_TRANSFORMS = frozenset({"signed", "absolute", "loss_magnitude"})
_COMPARATORS = frozenset({"upper", "lower", "range"})
_IMMUTABLE_STATES = frozenset({"active", "superseded", "retired"})
_MONETARY_GREEKS = frozenset({"vega", "theta", "rho", "rho_q"})


def _fail(message: str) -> None:
    raise LimitValidationError(message)


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} must be a non-empty string")
    return value.strip()


def _utc_naive(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        _fail(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=None)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _normalized_currency(value: str) -> str:
    clean = _non_empty(value, "currency")
    normalized = normalize_currency(clean)
    if not is_valid_currency(normalized):
        _fail("currency must be an active ISO 4217 alphabetic code")
    return normalized


def _validate_context(context: LimitActionContext) -> None:
    _non_empty(context.actor, "context.actor")
    if context.persona is not None:
        _non_empty(context.persona, "context.persona")
    if context.mode not in {"interactive", "auto", "yolo"}:
        _fail("context.mode is invalid")
    if context.thread_id is not None and (
        isinstance(context.thread_id, bool) or context.thread_id <= 0
    ):
        _fail("context.thread_id must be a positive integer")


def _validate_identity(
    *,
    key: Any,
    name: Any,
    description: Any,
    category: Any,
    owner: Any,
    tags: Any,
) -> dict[str, Any]:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        _fail("key must be a lowercase stable machine identifier")
    clean_name = _non_empty(name, "name")
    if not isinstance(description, str):
        _fail("description must be a string")
    if category not in _CATEGORIES:
        _fail("category is not supported")
    clean_owner = _non_empty(owner, "owner")
    if not isinstance(tags, list):
        _fail("tags must be a list")
    clean_tags: list[str] = []
    for tag in tags:
        clean_tag = _non_empty(tag, "tag")
        if clean_tag in clean_tags:
            _fail("tags must be unique")
        clean_tags.append(clean_tag)
    return {
        "key": key,
        "name": clean_name,
        "description": description.strip(),
        "category": category,
        "owner": clean_owner,
        "tags": clean_tags,
    }


def _validate_positive_ids(values: Any, field: str) -> None:
    if not isinstance(values, list) or not values:
        _fail(f"{field} must be a non-empty list")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        for value in values
    ):
        _fail(f"{field} must contain positive integer ids")
    if len(values) != len(set(values)):
        _fail(f"{field} must not contain duplicates")


def _validate_string_values(values: Any, field: str) -> None:
    if not isinstance(values, list) or not values:
        _fail(f"{field} must be a non-empty list")
    cleaned = [_non_empty(value, field) for value in values]
    if len(cleaned) != len(set(cleaned)):
        _fail(f"{field} must not contain duplicates")


def _validate_scope(spec: LimitVersionSpec) -> None:
    if spec.scope_type not in _SCOPES:
        _fail("scope_type is not supported")
    config = spec.scope_config
    if not isinstance(config, dict):
        _fail("scope_config must be an object")

    if spec.scope_type == "portfolio":
        if set(config) != {"portfolio_ids"}:
            _fail("portfolio scope requires only portfolio_ids")
        _validate_positive_ids(config["portfolio_ids"], "portfolio_ids")
        return

    if spec.scope_type == "underlying":
        if set(config) == {"symbols"}:
            _validate_string_values(config["symbols"], "symbols")
            return
        if config == {"all_in_portfolio": True}:
            return
        _fail("underlying scope requires symbols or all_in_portfolio")

    if spec.scope_type == "product_family":
        if set(config) == {"families"}:
            _validate_string_values(config["families"], "families")
            return
        if config == {"all_in_portfolio": True}:
            return
        _fail("product_family scope requires families or all_in_portfolio")

    if set(config) != {"position_ids"}:
        _fail("position scope requires only position_ids")
    _validate_positive_ids(config["position_ids"], "position_ids")


def _validate_thresholds(spec: LimitVersionSpec) -> None:
    thresholds = (
        spec.warning_lower,
        spec.warning_upper,
        spec.hard_lower,
        spec.hard_upper,
    )
    for value in thresholds:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            _fail("thresholds must be finite numbers or null")
        if value is not None and not math.isfinite(value):
            _fail("thresholds must be finite numbers or null")

    if spec.transform in {"absolute", "loss_magnitude"}:
        if spec.comparator != "upper":
            _fail("absolute and loss limits require a positive upper comparator")
        if (
            spec.warning_lower is not None
            or spec.hard_lower is not None
            or spec.warning_upper is None
            or spec.hard_upper is None
            or spec.warning_upper < 0
            or spec.hard_upper <= 0
            or spec.warning_upper >= spec.hard_upper
        ):
            _fail(
                "absolute and loss limits require "
                "0 <= warning_upper < hard_upper"
            )
        return

    if spec.comparator == "upper":
        if (
            spec.warning_lower is not None
            or spec.hard_lower is not None
            or spec.warning_upper is None
            or spec.hard_upper is None
            or spec.warning_upper < 0
            or spec.hard_upper <= 0
            or spec.warning_upper >= spec.hard_upper
        ):
            _fail(
                "signed upper comparator requires "
                "0 <= warning_upper < hard_upper"
            )
        return
    if spec.comparator == "lower":
        if (
            spec.warning_upper is not None
            or spec.hard_upper is not None
            or spec.warning_lower is None
            or spec.hard_lower is None
            or spec.warning_lower > 0
            or spec.hard_lower >= 0
            or spec.hard_lower >= spec.warning_lower
        ):
            _fail(
                "signed lower comparator requires "
                "hard_lower < warning_lower <= 0"
            )
        return
    if spec.comparator == "range":
        if any(value is None for value in thresholds):
            _fail("range comparator requires all four thresholds")
        if not (
            spec.hard_lower
            < spec.warning_lower
            < 0
            < spec.warning_upper
            < spec.hard_upper
        ):
            _fail(
                "range thresholds must satisfy "
                "hard_lower < warning_lower < 0 < "
                "warning_upper < hard_upper"
            )
        return
    _fail("comparator is not supported")


def _validate_methodology(spec: LimitVersionSpec) -> None:
    methodology = spec.methodology
    if not isinstance(methodology, dict):
        _fail("methodology must be an object")

    if spec.metric_kind in _GREEKS:
        if spec.source_kind != "risk_run":
            _fail("Greek limits require risk_run evidence")
        if methodology:
            _fail("Greek methodology must be empty in v1")
        return

    if spec.metric_kind in {"var", "cvar"}:
        expected_by_source = {
            "scenario_test": {
                "method": "scenario_distribution",
                "confidence": 0.95,
                "horizon": "scenario_set",
                "scaling": "none",
            },
            "backtest": {
                "method": "historical",
                "confidence": 0.95,
                "horizon": "1_trading_day",
                "scaling": "none",
            },
        }
        expected = expected_by_source.get(spec.source_kind)
        if expected is None or methodology != expected:
            _fail("VaR/CVaR source methodology must match the v1 contract")
        return

    if spec.source_kind != "scenario_test":
        _fail("stress_pnl limits require scenario_test evidence")
    selection = methodology.get("selection")
    if selection == "named":
        if set(methodology) != {
            "selection",
            "scenario_set_id",
            "scenario_name",
        }:
            _fail("named stress selection must be exact")
        if (
            isinstance(methodology["scenario_set_id"], bool)
            or not isinstance(methodology["scenario_set_id"], int)
            or methodology["scenario_set_id"] <= 0
        ):
            _fail("scenario_set_id must be a positive integer")
        _non_empty(methodology["scenario_name"], "scenario_name")
        return
    if selection == "worst_of_set":
        if set(methodology) != {
            "selection",
            "scenario_set_id",
            "scenario_names",
        }:
            _fail("worst-of stress selection must be exact")
        if (
            isinstance(methodology["scenario_set_id"], bool)
            or not isinstance(methodology["scenario_set_id"], int)
            or methodology["scenario_set_id"] <= 0
        ):
            _fail("scenario_set_id must be a positive integer")
        _validate_string_values(methodology["scenario_names"], "scenario_names")
        return
    _fail("stress_pnl requires named or worst_of_set selection")


def validate_version_spec(
    spec: LimitVersionSpec,
    *,
    category: str,
) -> None:
    if not isinstance(spec, LimitVersionSpec):
        _fail("version spec has the wrong type")
    if spec.metric_kind not in _METRICS:
        _fail("metric_kind is not supported")
    if spec.source_kind not in _SOURCES:
        _fail("source_kind is not supported")
    expected_category = (
        "greek"
        if spec.metric_kind in _GREEKS
        else "stress"
        if spec.metric_kind == "stress_pnl"
        else spec.metric_kind
    )
    if category != expected_category:
        _fail("identity category does not match metric_kind")
    if spec.aggregation not in _AGGREGATIONS:
        _fail("aggregation is not supported")
    if spec.transform not in _TRANSFORMS:
        _fail("transform is not supported")
    if spec.metric_kind in {"var", "cvar", "stress_pnl"}:
        if spec.transform != "loss_magnitude":
            _fail("tail and stress limits require loss_magnitude")
    elif spec.transform == "loss_magnitude":
        _fail("Greek limits do not support loss_magnitude")

    _validate_scope(spec)
    _validate_thresholds(spec)
    _validate_methodology(spec)

    _non_empty(spec.unit, "unit")
    if spec.currency is not None:
        _normalized_currency(spec.currency)
    if spec.metric_kind in _MONETARY_GREEKS | {"var", "cvar", "stress_pnl"}:
        _normalized_currency(spec.currency)
    if spec.metric_kind in {"rho", "rho_q"}:
        _non_empty(spec.bump_convention, "bump_convention")
    elif spec.bump_convention is not None:
        _non_empty(spec.bump_convention, "bump_convention")

    policy = spec.freshness_policy
    if not isinstance(policy, dict) or "max_age_seconds" not in policy:
        _fail("freshness_policy requires max_age_seconds")
    if not set(policy) <= {"max_age_seconds", "allow_profile_dated"}:
        _fail("freshness_policy contains unsupported fields")
    max_age = policy["max_age_seconds"]
    if (
        isinstance(max_age, bool)
        or not isinstance(max_age, int)
        or max_age < 0
    ):
        _fail("max_age_seconds must be a non-negative integer")
    if "allow_profile_dated" in policy and not isinstance(
        policy["allow_profile_dated"], bool
    ):
        _fail("allow_profile_dated must be boolean")
    if spec.effective_from is not None:
        _fail("effective_from must be absent for immediate-only v1 drafts")
    if spec.rationale is not None and not isinstance(spec.rationale, str):
        _fail("rationale must be a string or null")


def _context_payload(context: LimitActionContext, **extra: Any) -> dict:
    return {
        "persona": context.persona,
        "mode": context.mode,
        "thread_id": context.thread_id,
        "audit_ref": context.audit_ref,
        **extra,
    }


def _audit(
    session: Session,
    *,
    event_type: str,
    limit_id: int,
    context: LimitActionContext,
    **payload: Any,
) -> None:
    record_audit(
        session,
        event_type=event_type,
        actor=context.actor,
        subject_type="risk_limit",
        subject_id=limit_id,
        payload=_context_payload(context, **payload),
    )


def _identity(session: Session, limit_id: int) -> RiskLimit:
    identity = session.get(RiskLimit, limit_id)
    if identity is None:
        raise LimitNotFoundError(f"risk limit {limit_id} was not found")
    return identity


def _version(session: Session, version_id: int) -> RiskLimitVersion:
    version = session.get(RiskLimitVersion, version_id)
    if version is None:
        raise LimitNotFoundError(f"risk limit version {version_id} was not found")
    return version


def _key_exists(session: Session, key: str) -> bool:
    return session.scalar(
        select(RiskLimit.id).where(RiskLimit.key == key)
    ) is not None


def _conditional_identity_update(
    session: Session,
    *,
    identity: RiskLimit,
    expected_row_version: int,
    values: dict[str, Any] | None = None,
) -> RiskLimit:
    if (
        isinstance(expected_row_version, bool)
        or not isinstance(expected_row_version, int)
        or expected_row_version <= 0
    ):
        _fail("expected_row_version must be a positive integer")
    result = session.execute(
        update(RiskLimit)
        .where(
            RiskLimit.id == identity.id,
            RiskLimit.row_version == expected_row_version,
        )
        .values(
            **(values or {}),
            row_version=RiskLimit.row_version + 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise LimitConflictError(
            f"risk limit {identity.id} row version is stale"
        )
    session.flush()
    session.refresh(identity)
    return identity


def _new_version(
    *,
    identity: RiskLimit,
    number: int,
    spec: LimitVersionSpec,
    context: LimitActionContext,
) -> RiskLimitVersion:
    return RiskLimitVersion(
        risk_limit_id=identity.id,
        version=number,
        state="draft",
        metric_kind=spec.metric_kind,
        source_kind=spec.source_kind,
        methodology=dict(spec.methodology),
        scope_type=spec.scope_type,
        scope_config=dict(spec.scope_config),
        aggregation=spec.aggregation,
        transform=spec.transform,
        comparator=spec.comparator,
        warning_lower=(
            float(spec.warning_lower)
            if spec.warning_lower is not None
            else None
        ),
        warning_upper=(
            float(spec.warning_upper)
            if spec.warning_upper is not None
            else None
        ),
        hard_lower=(
            float(spec.hard_lower)
            if spec.hard_lower is not None
            else None
        ),
        hard_upper=(
            float(spec.hard_upper)
            if spec.hard_upper is not None
            else None
        ),
        unit=spec.unit.strip(),
        currency=(
            _normalized_currency(spec.currency)
            if spec.currency is not None
            else None
        ),
        bump_convention=(
            spec.bump_convention.strip() if spec.bump_convention else None
        ),
        freshness_policy=dict(spec.freshness_policy),
        effective_from=(
            _utc_naive(spec.effective_from, "effective_from")
            if spec.effective_from is not None
            else None
        ),
        effective_until=(
            _utc_naive(spec.effective_until, "effective_until")
            if spec.effective_until is not None
            else None
        ),
        rationale=spec.rationale,
        created_by_actor=context.actor,
        created_by_persona=context.persona,
        created_in_mode=context.mode,
        created_in_thread_id=context.thread_id,
    )


def create_limit(
    session: Session,
    *,
    key: str,
    name: str,
    description: str,
    category: str,
    owner: str,
    tags: list[str],
    initial_version: LimitVersionSpec,
    context: LimitActionContext,
) -> tuple[RiskLimit, RiskLimitVersion]:
    _validate_context(context)
    metadata = _validate_identity(
        key=key,
        name=name,
        description=description,
        category=category,
        owner=owner,
        tags=tags,
    )
    validate_version_spec(initial_version, category=metadata["category"])
    if _key_exists(session, metadata["key"]):
        raise LimitConflictError(f"risk limit key {metadata['key']!r} exists")

    identity = RiskLimit(
        **metadata,
        created_by_actor=context.actor,
        created_by_persona=context.persona,
    )
    try:
        with session.begin_nested():
            session.add(identity)
            session.flush()
            version = _new_version(
                identity=identity,
                number=1,
                spec=initial_version,
                context=context,
            )
            session.add(version)
            session.flush()
    except IntegrityError as exc:
        raise LimitConflictError(
            f"risk limit key {metadata['key']!r} conflicts"
        ) from exc
    _audit(
        session,
        event_type="limit.created",
        limit_id=identity.id,
        context=context,
        version_id=version.id,
    )
    return identity, version


def add_version(
    session: Session,
    *,
    limit_id: int,
    expected_row_version: int,
    spec: LimitVersionSpec,
    context: LimitActionContext,
) -> RiskLimitVersion:
    _validate_context(context)
    identity = _identity(session, limit_id)
    validate_version_spec(spec, category=identity.category)
    next_number = (
        session.scalar(
            select(func.max(RiskLimitVersion.version)).where(
                RiskLimitVersion.risk_limit_id == limit_id
            )
        )
        or 0
    ) + 1
    _conditional_identity_update(
        session,
        identity=identity,
        expected_row_version=expected_row_version,
    )
    version = _new_version(
        identity=identity,
        number=next_number,
        spec=spec,
        context=context,
    )
    session.add(version)
    session.flush()
    _audit(
        session,
        event_type="limit.version_created",
        limit_id=limit_id,
        context=context,
        version_id=version.id,
        version=version.version,
    )
    return version


def update_draft(
    session: Session,
    *,
    version_id: int,
    expected_row_version: int,
    spec: LimitVersionSpec,
    context: LimitActionContext,
) -> RiskLimitVersion:
    _validate_context(context)
    version = _version(session, version_id)
    if version.state in _IMMUTABLE_STATES:
        raise LimitImmutableError(
            f"risk limit version {version.id} is immutable in {version.state}"
        )
    identity = _identity(session, version.risk_limit_id)
    validate_version_spec(spec, category=identity.category)
    _conditional_identity_update(
        session,
        identity=identity,
        expected_row_version=expected_row_version,
    )
    replacement = _new_version(
        identity=identity,
        number=version.version,
        spec=spec,
        context=context,
    )
    for field in (
        "metric_kind",
        "source_kind",
        "methodology",
        "scope_type",
        "scope_config",
        "aggregation",
        "transform",
        "comparator",
        "warning_lower",
        "warning_upper",
        "hard_lower",
        "hard_upper",
        "unit",
        "currency",
        "bump_convention",
        "freshness_policy",
        "effective_from",
        "effective_until",
        "rationale",
    ):
        setattr(version, field, getattr(replacement, field))
    session.flush()
    _audit(
        session,
        event_type="limit.draft_updated",
        limit_id=identity.id,
        context=context,
        version_id=version.id,
    )
    return version


def activate_version(
    session: Session,
    *,
    limit_id: int,
    version_id: int,
    expected_row_version: int,
    activated_at: datetime | None,
    context: LimitActionContext,
) -> RiskLimitVersion:
    _validate_context(context)
    identity = _identity(session, limit_id)
    version = _version(session, version_id)
    if version.risk_limit_id != limit_id:
        raise LimitNotFoundError(
            f"version {version_id} does not belong to risk limit {limit_id}"
        )
    if version.state != "draft":
        raise LimitImmutableError(
            f"only a draft can activate; version is {version.state}"
        )
    if version.effective_from is not None:
        _fail("draft effective_from must be absent before activation")
    when = _utc_naive(activated_at or utcnow(), "activated_at")
    if version.effective_until is not None:
        version.effective_until = _utc_naive(
            version.effective_until,
            "effective_until",
        )
    if version.effective_until is not None and when >= version.effective_until:
        _fail("activation must precede effective_until")

    previous = (
        session.get(RiskLimitVersion, identity.active_version_id)
        if identity.active_version_id is not None
        else None
    )
    if (
        previous is not None
        and previous.effective_from is not None
        and _utc_naive(previous.effective_from, "effective_from") > when
    ):
        _fail("activation cannot precede the current active version")
    _conditional_identity_update(
        session,
        identity=identity,
        expected_row_version=expected_row_version,
        values={"active_version_id": version.id},
    )
    if previous is not None:
        previous.state = "superseded"
        existing_end = (
            _utc_naive(previous.effective_until, "effective_until")
            if previous.effective_until is not None
            else None
        )
        previous.effective_until = (
            min(existing_end, when) if existing_end else when
        )
    version.state = "active"
    version.effective_from = when
    version.activated_at = when
    version.activated_by_actor = context.actor
    version.activated_by_persona = context.persona
    version.activated_in_mode = context.mode
    version.activated_in_thread_id = context.thread_id
    session.flush()
    _audit(
        session,
        event_type="limit.version_activated",
        limit_id=limit_id,
        context=context,
        version_id=version.id,
        superseded_version_id=previous.id if previous else None,
    )
    return version


def _clamped_effective_until(
    version: RiskLimitVersion,
    action_at: datetime,
) -> datetime:
    effective_from = (
        _utc_naive(version.effective_from, "effective_from")
        if version.effective_from is not None
        else None
    )
    existing_end = (
        _utc_naive(version.effective_until, "effective_until")
        if version.effective_until is not None
        else None
    )
    if effective_from is not None and action_at < effective_from:
        _fail("governance action cannot precede activation")
    if (
        effective_from is not None
        and existing_end is not None
        and existing_end < effective_from
    ):
        _fail("stored effective interval is invalid")
    return min(existing_end, action_at) if existing_end else action_at


def deactivate(
    session: Session,
    *,
    limit_id: int,
    expected_row_version: int,
    context: LimitActionContext,
    action_at: datetime | None = None,
) -> RiskLimit:
    _validate_context(context)
    identity = _identity(session, limit_id)
    active = (
        session.get(RiskLimitVersion, identity.active_version_id)
        if identity.active_version_id is not None
        else None
    )
    when = _utc_naive(action_at or utcnow(), "action_at")
    if active is not None:
        clamped_end = _clamped_effective_until(active, when)
    _conditional_identity_update(
        session,
        identity=identity,
        expected_row_version=expected_row_version,
        values={"active_version_id": None},
    )
    if active is not None:
        active.state = "superseded"
        active.effective_until = clamped_end
    session.flush()
    _audit(
        session,
        event_type="limit.deactivated",
        limit_id=limit_id,
        context=context,
        version_id=active.id if active else None,
    )
    return identity


def retire(
    session: Session,
    *,
    limit_id: int,
    expected_row_version: int,
    context: LimitActionContext,
    action_at: datetime | None = None,
) -> RiskLimit:
    _validate_context(context)
    identity = _identity(session, limit_id)
    when = _utc_naive(action_at or utcnow(), "action_at")
    clamped_ends: dict[int, datetime] = {}
    for version in identity.versions:
        if version.state == "active":
            clamped_ends[version.id] = _clamped_effective_until(version, when)
    _conditional_identity_update(
        session,
        identity=identity,
        expected_row_version=expected_row_version,
        values={"active_version_id": None},
    )
    for version in identity.versions:
        if version.state in {"draft", "active"}:
            version.state = "retired"
            if version.activated_at is not None:
                version.effective_until = clamped_ends.get(
                    version.id,
                    _clamped_effective_until(version, when),
                )
    session.flush()
    _audit(
        session,
        event_type="limit.retired",
        limit_id=limit_id,
        context=context,
    )
    return identity


def update_metadata(
    session: Session,
    *,
    limit_id: int,
    expected_row_version: int,
    patch: dict[str, Any],
    context: LimitActionContext,
) -> RiskLimit:
    _validate_context(context)
    if not isinstance(patch, dict) or not patch:
        _fail("metadata patch must be a non-empty object")
    allowed = {"name", "description", "owner", "tags"}
    if not set(patch) <= allowed:
        _fail("metadata patch contains unsupported fields")
    identity = _identity(session, limit_id)
    candidate = {
        field: patch.get(field, getattr(identity, field))
        for field in {"key", "name", "description", "category", "owner", "tags"}
    }
    metadata = _validate_identity(**candidate)
    updated = _conditional_identity_update(
        session,
        identity=identity,
        expected_row_version=expected_row_version,
        values={field: metadata[field] for field in patch},
    )
    _audit(
        session,
        event_type="limit.metadata_updated",
        limit_id=limit_id,
        context=context,
        changed_fields=sorted(patch),
    )
    return updated


def effective_version(
    session: Session,
    *,
    limit_id: int,
    valuation_at: datetime,
) -> RiskLimitVersion:
    normalized_valuation_at = _utc_naive(valuation_at, "valuation_at")
    version = session.scalar(
        select(RiskLimitVersion)
        .where(
            RiskLimitVersion.risk_limit_id == limit_id,
            RiskLimitVersion.activated_at.is_not(None),
            RiskLimitVersion.effective_from <= normalized_valuation_at,
            or_(
                RiskLimitVersion.effective_until.is_(None),
                RiskLimitVersion.effective_until > normalized_valuation_at,
            ),
        )
        .order_by(
            RiskLimitVersion.effective_from.desc(),
            RiskLimitVersion.version.desc(),
        )
        .limit(1)
    )
    if version is None:
        raise LimitNotFoundError(
            f"risk limit {limit_id} has no effective version at {valuation_at}"
        )
    return version


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return (
            _utc_naive(value, "snapshot datetime")
            .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
    if isinstance(value, dict):
        return {
            str(key): _json_value(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(nested) for nested in value]
    return value


def canonical_version_snapshot(version: RiskLimitVersion) -> dict[str, Any]:
    fields = (
        "risk_limit_id",
        "version",
        "state",
        "metric_kind",
        "source_kind",
        "methodology",
        "scope_type",
        "scope_config",
        "aggregation",
        "transform",
        "comparator",
        "warning_lower",
        "warning_upper",
        "hard_lower",
        "hard_upper",
        "unit",
        "currency",
        "bump_convention",
        "freshness_policy",
        "effective_from",
        "effective_until",
        "rationale",
        "activated_at",
    )
    threshold_fields = {
        "warning_lower",
        "warning_upper",
        "hard_lower",
        "hard_upper",
    }
    raw = {}
    for field in fields:
        value = getattr(version, field)
        if field in threshold_fields and value is not None:
            value = float(value)
        raw[field] = _json_value(value)
    return json.loads(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def canonical_version_hash(version: RiskLimitVersion) -> str:
    payload = json.dumps(
        canonical_version_snapshot(version),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

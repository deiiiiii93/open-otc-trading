from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.limits import definitions
from app.services.limits.contracts import LimitActionContext, LimitVersionSpec
from app.services.limits.errors import (
    LimitConflictError,
    LimitImmutableError,
    LimitValidationError,
)


HUMAN_CONTEXT = LimitActionContext(
    actor="alice",
    persona="limit_manager",
    mode="interactive",
    thread_id=41,
    audit_ref="audit-human-1",
)


def _spec(**overrides) -> LimitVersionSpec:
    values = {
        "metric_kind": "delta",
        "source_kind": "risk_run",
        "methodology": {},
        "scope_type": "portfolio",
        "scope_config": {"portfolio_ids": [7]},
        "aggregation": "net",
        "transform": "absolute",
        "comparator": "upper",
        "warning_lower": None,
        "warning_upper": 80.0,
        "hard_lower": None,
        "hard_upper": 100.0,
        "unit": "delta_units",
        "currency": None,
        "bump_convention": None,
        "freshness_policy": {"max_age_seconds": 900},
        "effective_from": None,
        "effective_until": None,
        "rationale": "Desk appetite",
    }
    values.update(overrides)
    return LimitVersionSpec(**values)


def _create(session, *, key: str = "desk-delta", spec=None, category=None):
    version_spec = spec or _spec()
    resolved_category = category or (
        "greek"
        if version_spec.metric_kind
        in {"delta", "gamma", "vega", "theta", "rho", "rho_q"}
        else "stress"
        if version_spec.metric_kind == "stress_pnl"
        else version_spec.metric_kind
    )
    return definitions.create_limit(
        session,
        key=key,
        name="Desk delta",
        description="Absolute desk delta",
        category=resolved_category,
        owner="market-risk",
        tags=["desk", "intraday"],
        initial_version=version_spec,
        context=HUMAN_CONTEXT,
    )


def test_create_identity_and_initial_draft_records_audit(session) -> None:
    from app.models import AuditEvent

    limit_row, draft = _create(session)

    assert limit_row.key == "desk-delta"
    assert limit_row.row_version == 1
    assert draft.version == 1
    assert draft.state == "draft"
    assert draft.created_by_actor == "alice"
    assert draft.created_by_persona == "limit_manager"
    audit = session.query(AuditEvent).filter_by(
        event_type="limit.created",
        subject_id=str(limit_row.id),
    ).one()
    assert audit.payload["mode"] == "interactive"
    assert audit.payload["audit_ref"] == "audit-human-1"


def test_adds_sequential_draft_versions(session) -> None:
    limit_row, _ = _create(session, key="sequential")

    second = definitions.add_version(
        session,
        limit_id=limit_row.id,
        expected_row_version=1,
        spec=_spec(warning_upper=90.0, hard_upper=120.0),
        context=HUMAN_CONTEXT,
    )
    third = definitions.add_version(
        session,
        limit_id=limit_row.id,
        expected_row_version=2,
        spec=_spec(warning_upper=100.0, hard_upper=140.0),
        context=HUMAN_CONTEXT,
    )

    assert (second.version, third.version) == (2, 3)
    assert limit_row.row_version == 3


def test_activation_supersedes_previous_and_effective_lookup_is_historical(
    session,
) -> None:
    limit_row, first = _create(session, key="activation")
    first_at = datetime(2026, 7, 17, 9, 0)
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=first.id,
        expected_row_version=1,
        activated_at=first_at,
        context=HUMAN_CONTEXT,
    )
    second = definitions.add_version(
        session,
        limit_id=limit_row.id,
        expected_row_version=2,
        spec=_spec(warning_upper=90.0, hard_upper=120.0),
        context=HUMAN_CONTEXT,
    )
    second_at = first_at + timedelta(hours=2)
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=second.id,
        expected_row_version=3,
        activated_at=second_at,
        context=HUMAN_CONTEXT,
    )

    assert first.state == "superseded"
    assert first.effective_until == second_at
    assert second.state == "active"
    assert limit_row.active_version_id == second.id
    assert definitions.effective_version(
        session,
        limit_id=limit_row.id,
        valuation_at=first_at + timedelta(minutes=30),
    ).id == first.id
    assert definitions.effective_version(
        session,
        limit_id=limit_row.id,
        valuation_at=second_at,
    ).id == second.id


def test_deactivate_and_retire_preserve_history(session) -> None:
    limit_row, draft = _create(session, key="lifecycle")
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=draft.id,
        expected_row_version=1,
        activated_at=datetime(2026, 7, 17, 9),
        context=HUMAN_CONTEXT,
    )

    definitions.deactivate(
        session,
        limit_id=limit_row.id,
        expected_row_version=2,
        context=HUMAN_CONTEXT,
    )
    assert limit_row.active_version_id is None
    assert draft.state == "superseded"

    next_draft = definitions.add_version(
        session,
        limit_id=limit_row.id,
        expected_row_version=3,
        spec=_spec(),
        context=HUMAN_CONTEXT,
    )
    definitions.retire(
        session,
        limit_id=limit_row.id,
        expected_row_version=4,
        context=HUMAN_CONTEXT,
    )
    assert next_draft.state == "retired"
    assert session.get(type(limit_row), limit_row.id) is limit_row
    assert [version.version for version in limit_row.versions] == [1, 2]


def test_active_and_superseded_versions_are_immutable(session) -> None:
    limit_row, first = _create(session, key="immutable")
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=first.id,
        expected_row_version=1,
        activated_at=datetime(2026, 7, 17, 9),
        context=HUMAN_CONTEXT,
    )
    with pytest.raises(LimitImmutableError):
        definitions.update_draft(
            session,
            version_id=first.id,
            expected_row_version=2,
            spec=_spec(hard_upper=110.0),
            context=HUMAN_CONTEXT,
        )

    second = definitions.add_version(
        session,
        limit_id=limit_row.id,
        expected_row_version=2,
        spec=_spec(hard_upper=120.0),
        context=HUMAN_CONTEXT,
    )
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=second.id,
        expected_row_version=3,
        activated_at=datetime(2026, 7, 17, 10),
        context=HUMAN_CONTEXT,
    )
    with pytest.raises(LimitImmutableError):
        definitions.update_draft(
            session,
            version_id=first.id,
            expected_row_version=4,
            spec=_spec(hard_upper=130.0),
            context=HUMAN_CONTEXT,
        )


def test_canonical_snapshot_and_hash_are_deterministic(session) -> None:
    _, draft = _create(session, key="snapshot")
    first = definitions.canonical_version_snapshot(draft)
    first_hash = definitions.canonical_version_hash(draft)
    session.expire(draft)
    second = definitions.canonical_version_snapshot(draft)

    assert first == second
    assert first_hash == definitions.canonical_version_hash(draft)
    assert len(first_hash) == 64
    assert first["scope_config"] == {"portfolio_ids": [7]}


def test_metadata_update_uses_atomic_expected_row_version(session) -> None:
    limit_row, _ = _create(session, key="metadata")
    updated = definitions.update_metadata(
        session,
        limit_id=limit_row.id,
        expected_row_version=1,
        patch={"owner": "market-risk-2"},
        context=HUMAN_CONTEXT,
    )
    assert updated.row_version == 2
    assert updated.owner == "market-risk-2"

    with pytest.raises(LimitConflictError):
        definitions.update_metadata(
            session,
            limit_id=limit_row.id,
            expected_row_version=1,
            patch={"owner": "stale-writer"},
            context=HUMAN_CONTEXT,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", "Not Valid"),
        ("name", " "),
        ("description", None),
        ("category", "liquidity"),
        ("owner", ""),
        ("tags", ["desk", "desk"]),
    ],
)
def test_identity_metadata_validation(session, field, value) -> None:
    kwargs = {
        "key": "valid-key",
        "name": "Valid",
        "description": "",
        "category": "greek",
        "owner": "market-risk",
        "tags": ["desk"],
    }
    kwargs[field] = value
    with pytest.raises(LimitValidationError):
        definitions.create_limit(
            session,
            **kwargs,
            initial_version=_spec(),
            context=HUMAN_CONTEXT,
        )


def test_key_remains_unique_when_metadata_changes(session) -> None:
    first, _ = _create(session, key="first-key")
    _create(session, key="second-key")

    with pytest.raises(LimitConflictError):
        definitions.update_metadata(
            session,
            limit_id=first.id,
            expected_row_version=1,
            patch={"key": "second-key"},
            context=HUMAN_CONTEXT,
        )


@pytest.mark.parametrize(
    "spec",
    [
        _spec(source_kind="scenario_test"),
        _spec(scope_config={"portfolio_ids": []}),
        _spec(aggregation="average"),
        _spec(transform="loss_magnitude"),
        _spec(warning_upper=100.0, hard_upper=100.0),
        _spec(warning_upper=float("nan")),
        _spec(unit=" "),
        _spec(freshness_policy={"max_age_seconds": -1}),
        _spec(
            comparator="range",
            warning_lower=-100.0,
            hard_lower=-80.0,
            warning_upper=80.0,
            hard_upper=100.0,
        ),
        _spec(
            metric_kind="var",
            source_kind="scenario_test",
            methodology={
                "method": "scenario_distribution",
                "confidence": 0.99,
                "horizon": "scenario_set",
                "scaling": "none",
            },
            transform="loss_magnitude",
            unit="USD",
            currency="USD",
        ),
        _spec(
            metric_kind="stress_pnl",
            source_kind="scenario_test",
            methodology={"selection": "named"},
            transform="loss_magnitude",
            unit="USD",
            currency="USD",
        ),
        _spec(
            metric_kind="rho",
            unit="USD",
            currency="USD",
            bump_convention=None,
        ),
    ],
)
def test_version_contract_validation(session, spec) -> None:
    with pytest.raises(LimitValidationError):
        _create(session, key=f"invalid-{abs(hash(repr(spec)))}", spec=spec)


def test_scope_contracts_are_explicit(session) -> None:
    valid_scopes = [
        _spec(
            scope_type="underlying",
            scope_config={"symbols": ["AAPL", "MSFT"]},
        ),
        _spec(
            scope_type="underlying",
            scope_config={"all_in_portfolio": True},
        ),
        _spec(
            scope_type="product_family",
            scope_config={"families": ["autocallables"]},
        ),
        _spec(
            scope_type="position",
            scope_config={"position_ids": [1, 2]},
        ),
    ]

    for index, spec in enumerate(valid_scopes):
        _, draft = _create(session, key=f"scope-{index}", spec=spec)
        assert draft.scope_config == spec.scope_config

    with pytest.raises(LimitValidationError):
        _create(
            session,
            key="ambiguous-scope",
            spec=_spec(
                scope_type="underlying",
                scope_config={
                    "symbols": ["AAPL"],
                    "all_in_portfolio": True,
                },
            ),
        )


@pytest.mark.parametrize(
    "source_kind,methodology",
    [
        (
            "scenario_test",
            {
                "method": "scenario_distribution",
                "confidence": 0.95,
                "horizon": "scenario_set",
                "scaling": "none",
            },
        ),
        (
            "backtest",
            {
                "method": "historical",
                "confidence": 0.95,
                "horizon": "1_trading_day",
                "scaling": "none",
            },
        ),
    ],
)
def test_var_methodologies_are_exact(session, source_kind, methodology) -> None:
    _, draft = _create(
        session,
        key=f"var-{source_kind}",
        spec=_spec(
            metric_kind="var",
            source_kind=source_kind,
            methodology=methodology,
            transform="loss_magnitude",
            unit="USD",
            currency="USD",
        ),
    )
    assert draft.methodology == methodology


def test_exact_named_stress_and_rho_q_are_valid(session) -> None:
    _, stress = _create(
        session,
        key="stress-exact",
        spec=_spec(
            metric_kind="stress_pnl",
            source_kind="scenario_test",
            methodology={
                "selection": "named",
                "scenario_set_id": 12,
                "scenario_name": "equity-down-10",
            },
            transform="loss_magnitude",
            unit="USD",
            currency="USD",
        ),
    )
    _, rho_q = _create(
        session,
        key="rho-q",
        spec=_spec(
            metric_kind="rho_q",
            unit="USD_per_1bp",
            currency="USD",
            bump_convention="parallel_dividend_yield_1bp",
        ),
    )

    assert stress.methodology["scenario_name"] == "equity-down-10"
    assert rho_q.metric_kind == "rho_q"
    assert rho_q.bump_convention == "parallel_dividend_yield_1bp"

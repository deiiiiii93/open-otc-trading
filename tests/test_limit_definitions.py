from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def test_public_draft_specs_reject_populated_effective_from(session) -> None:
    populated = _spec(effective_from=datetime(2026, 7, 17, 9))
    with pytest.raises(LimitValidationError):
        _create(session, key="scheduled-create", spec=populated)

    limit_row, draft = _create(session, key="immediate-only")
    with pytest.raises(LimitValidationError):
        definitions.add_version(
            session,
            limit_id=limit_row.id,
            expected_row_version=1,
            spec=populated,
            context=HUMAN_CONTEXT,
        )
    with pytest.raises(LimitValidationError):
        definitions.update_draft(
            session,
            version_id=draft.id,
            expected_row_version=1,
            spec=populated,
            context=HUMAN_CONTEXT,
        )
    assert draft.state == "draft"
    assert limit_row.active_version_id is None
    assert limit_row.row_version == 1


def test_activation_owns_start_and_normalizes_aware_instant_to_utc_naive(
    session,
) -> None:
    activation_at = datetime(2026, 7, 17, 9)
    same_instant = datetime(
        2026,
        7,
        17,
        17,
        tzinfo=timezone(timedelta(hours=8)),
    )
    limit_row, draft = _create(
        session,
        key="handover-equal",
    )

    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=draft.id,
        expected_row_version=1,
        activated_at=same_instant,
        context=HUMAN_CONTEXT,
    )

    assert draft.effective_from == activation_at
    assert draft.activated_at == activation_at
    assert draft.effective_from.tzinfo is None


@pytest.mark.parametrize("operation", ["deactivate", "retire"])
def test_governance_action_clamps_later_expiry_and_historical_lookup(
    session,
    operation,
) -> None:
    activation_at = datetime(2026, 7, 17, 9)
    action_at = activation_at + timedelta(hours=1)
    limit_row, draft = _create(
        session,
        key=f"clamp-{operation}",
        spec=_spec(
            effective_until=action_at + timedelta(days=1),
        ),
    )
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=draft.id,
        expected_row_version=1,
        activated_at=activation_at,
        context=HUMAN_CONTEXT,
    )

    getattr(definitions, operation)(
        session,
        limit_id=limit_row.id,
        expected_row_version=2,
        action_at=action_at,
        context=HUMAN_CONTEXT,
    )

    assert draft.effective_until == action_at
    assert definitions.effective_version(
        session,
        limit_id=limit_row.id,
        valuation_at=action_at - timedelta(microseconds=1),
    ).id == draft.id
    with pytest.raises(definitions.LimitNotFoundError):
        definitions.effective_version(
            session,
            limit_id=limit_row.id,
            valuation_at=action_at,
        )
    with pytest.raises(definitions.LimitNotFoundError):
        definitions.effective_version(
            session,
            limit_id=limit_row.id,
            valuation_at=action_at + timedelta(microseconds=1),
        )


def test_governance_action_cannot_precede_activation(session) -> None:
    activation_at = datetime(2026, 7, 17, 9)
    limit_row, draft = _create(
        session,
        key="non-retroactive-action",
    )
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=draft.id,
        expected_row_version=1,
        activated_at=activation_at,
        context=HUMAN_CONTEXT,
    )

    with pytest.raises(LimitValidationError):
        definitions.deactivate(
            session,
            limit_id=limit_row.id,
            expected_row_version=2,
            action_at=activation_at - timedelta(microseconds=1),
            context=HUMAN_CONTEXT,
        )

    assert draft.effective_until is None
    assert draft.state == "active"


def test_replacement_activation_preserves_a_genuine_expired_gap(
    session,
) -> None:
    first_at = datetime(2026, 7, 17, 9)
    first_end = first_at + timedelta(hours=1)
    second_at = first_at + timedelta(hours=2)
    limit_row, first = _create(
        session,
        key="expired-gap",
        spec=_spec(effective_until=first_end),
    )
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
    definitions.activate_version(
        session,
        limit_id=limit_row.id,
        version_id=second.id,
        expected_row_version=3,
        activated_at=second_at,
        context=HUMAN_CONTEXT,
    )

    assert first.effective_until == first_end
    assert definitions.effective_version(
        session,
        limit_id=limit_row.id,
        valuation_at=first_at + timedelta(minutes=30),
    ).id == first.id
    with pytest.raises(definitions.LimitNotFoundError):
        definitions.effective_version(
            session,
            limit_id=limit_row.id,
            valuation_at=first_end + timedelta(minutes=30),
        )
    assert definitions.effective_version(
        session,
        limit_id=limit_row.id,
        valuation_at=datetime(
            2026,
            7,
            17,
            19,
            tzinfo=timezone(timedelta(hours=8)),
        ),
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


def test_canonical_snapshot_is_stable_preflush_reload_and_fresh_session(
    session,
) -> None:
    from app import database

    limit_row, _ = _create(session, key="canonical-lifecycle")
    effective_from = datetime(
        2026,
        7,
        17,
        17,
        tzinfo=timezone(timedelta(hours=8)),
    )
    pending = definitions._new_version(
        identity=limit_row,
        number=2,
        spec=_spec(
            warning_upper=80,
            hard_upper=100,
            effective_from=effective_from,
        ),
        context=HUMAN_CONTEXT,
    )
    session.add(pending)
    before_flush = definitions.canonical_version_snapshot(pending)
    before_hash = definitions.canonical_version_hash(pending)

    session.flush()
    version_id = pending.id
    after_flush = definitions.canonical_version_snapshot(pending)
    session.commit()
    session.expire(pending)
    after_reload = definitions.canonical_version_snapshot(pending)
    with database.SessionLocal() as fresh_session:
        fresh = fresh_session.get(type(pending), version_id)
        fresh_snapshot = definitions.canonical_version_snapshot(fresh)
        fresh_hash = definitions.canonical_version_hash(fresh)

    assert before_flush == after_flush == after_reload == fresh_snapshot
    assert before_hash == fresh_hash
    assert before_flush["warning_upper"] == 80.0
    assert isinstance(before_flush["warning_upper"], float)
    assert before_flush["effective_from"] == "2026-07-17T09:00:00.000000Z"
    assert pending.effective_from == datetime(2026, 7, 17, 9)
    assert "id" not in before_flush


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


def test_key_is_not_mutable_metadata(session) -> None:
    first, _ = _create(session, key="first-key")

    with pytest.raises(LimitValidationError):
        definitions.update_metadata(
            session,
            limit_id=first.id,
            expected_row_version=1,
            patch={"key": "renamed-key"},
            context=HUMAN_CONTEXT,
        )

    assert first.key == "first-key"
    assert first.row_version == 1


def test_monetary_currency_uses_shared_iso_normalization(session) -> None:
    _, draft = _create(
        session,
        key="currency-normalized",
        spec=_spec(
            metric_kind="rho",
            unit="USD_per_1bp",
            currency=" usd ",
            bump_convention="parallel_rate_1bp",
        ),
    )

    assert draft.currency == "USD"

    with pytest.raises(LimitValidationError):
        _create(
            session,
            key="currency-invalid",
            spec=_spec(
                metric_kind="rho",
                unit="USD_per_1bp",
                currency="ZZZ",
                bump_convention="parallel_rate_1bp",
            ),
        )


def test_create_unique_key_race_uses_savepoint_and_preserves_session(
    session,
    monkeypatch,
) -> None:
    _create(session, key="race-key")
    monkeypatch.setattr(definitions, "_key_exists", lambda *_args: False)

    with pytest.raises(LimitConflictError):
        _create(session, key="race-key")

    surviving, _ = _create(session, key="after-race")
    session.flush()
    assert surviving.key == "after-race"


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
            metric_kind="stress_pnl",
            source_kind="scenario_test",
            methodology={
                "selection": "named",
                "scenario_set_hash": "sha256:" + ("g" * 64),
                "scenario_name": "not-canonical",
            },
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


@pytest.mark.parametrize(
    "spec",
    [
        _spec(
            transform="signed",
            comparator="lower",
            warning_lower=50.0,
            hard_lower=25.0,
            warning_upper=None,
            hard_upper=None,
        ),
        _spec(
            transform="signed",
            warning_upper=-50.0,
            hard_upper=-25.0,
        ),
        _spec(
            transform="signed",
            warning_upper=-1.0,
            hard_upper=0.0,
        ),
        _spec(
            transform="signed",
            comparator="range",
            warning_lower=20.0,
            hard_lower=10.0,
            warning_upper=30.0,
            hard_upper=40.0,
        ),
        _spec(
            transform="absolute",
            comparator="lower",
            warning_lower=-80.0,
            hard_lower=-100.0,
            warning_upper=None,
            hard_upper=None,
        ),
    ],
)
def test_directionally_invalid_threshold_contracts_are_rejected(
    session,
    spec,
) -> None:
    with pytest.raises(LimitValidationError):
        _create(
            session,
            key=f"direction-{abs(hash(repr(spec)))}",
            spec=spec,
        )


def test_signed_zero_neutral_threshold_contracts_are_valid(session) -> None:
    valid_specs = [
        _spec(
            transform="signed",
            warning_upper=0.0,
            hard_upper=100.0,
        ),
        _spec(
            transform="signed",
            comparator="lower",
            warning_lower=0.0,
            hard_lower=-100.0,
            warning_upper=None,
            hard_upper=None,
        ),
        _spec(
            transform="signed",
            comparator="range",
            warning_lower=-40.0,
            hard_lower=-200.0,
            warning_upper=20.0,
            hard_upper=80.0,
        ),
    ]

    for index, spec in enumerate(valid_specs):
        _, draft = _create(
            session,
            key=f"valid-direction-{index}",
            spec=spec,
        )
        assert draft.transform == "signed"


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
                "scenario_set_hash": "sha256:" + ("a" * 64),
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

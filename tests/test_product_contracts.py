from __future__ import annotations

import pytest

from app.services.domains import product_contracts as pc
from app.services.domains.product_builders import _REGISTRY, build_product


def test_snowball_contract_declares_full_required_bound_set():
    contract = pc.contract_for("SnowballOption")
    assert contract is not None
    assert set(contract.required_bound) == {
        "initial_price",
        "maturity_years",
        "trade_start_date",
        "observation_frequency",
        "barrier_config.ko_barrier",
        "barrier_config.ki_barrier",
        "barrier_config.ko_rate",
        "barrier_config.lockup_months",
        "ko_observation_dates",
    }
    # the coupon is a legitimate RFQ solve target, so it is solvable
    assert "barrier_config.ko_rate" in contract.solvable


def test_filter_solved_exempts_the_designated_target_only():
    missing = ["barrier_config.ko_rate", "barrier_config.ki_barrier"]
    kept = pc.filter_solved(missing, solve_target="barrier_config.ko_rate")
    assert kept == ["barrier_config.ki_barrier"]


def test_filter_solved_noop_without_target():
    missing = ["barrier_config.ko_rate"]
    assert pc.filter_solved(missing, solve_target=None) == missing


def test_snowball_flat_terms_with_legacy_barrier_config_synthesize(monkeypatch):
    class _Validation:
        ok = True
        error = None

    monkeypatch.setattr(
        "app.services.domains.product_builders.validate_quantark_build",
        lambda *args, **kwargs: _Validation(),
    )

    result = build_product(
        "SnowballOption",
        {
            "initial_price": 100,
            "strike": 100,
            "maturity": 1,
            "maturity_years": 1,
            "trade_start_date": "2026-05-29",
            "ko_barrier_pct": 101,
            "ki_barrier_pct": 70,
            "ko_rate": 0.15,
            "lockup_months": 1,
            "observation_frequency": "MONTHLY",
            "barrier_config": {
                "ko_barrier": 101,
                "ko_rate": 0.15,
                "ki_barrier": 70,
            },
        },
    )

    assert result.ok
    assert result.missing == []
    assert "ko_observation_schedule" in result.product_kwargs["barrier_config"]


def test_snowball_nested_barrier_without_flat_terms_is_malformed():
    result = build_product(
        "SnowballOption",
        {
            "initial_price": 100,
            "strike": 100,
            "maturity": 1,
            "barrier_config": {
                "ko_barrier": 101,
                "ko_rate": 0.15,
                "ki_barrier": 70,
            },
        },
    )

    assert not result.ok
    assert result.validation is not None
    assert str(result.validation["error"]).startswith("malformed Snowball terms")


@pytest.mark.parametrize("family", sorted(_REGISTRY))
def test_every_buildable_family_has_a_contract(family):
    """Completeness: no family may be built without a declared term contract."""
    assert pc.contract_for(family) is not None, family


@pytest.mark.parametrize("family", sorted(_REGISTRY))
def test_builder_missing_keys_are_declared_in_the_contract(family):
    """Consistency net (all families): every key a builder can report missing must
    be a declared required-bound contract key — so builder and contract cannot
    drift. Empty terms surface a family's full required set (no non-snowball family
    has conditional requirements; snowball's CUSTOM-only key is declared anyway)."""
    contract = pc.contract_for(family)
    assert contract is not None, family
    result = build_product(family, {})
    undeclared = [m for m in result.missing if m not in contract.required_bound]
    assert undeclared == [], (family, undeclared)

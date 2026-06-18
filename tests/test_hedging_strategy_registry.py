# tests/test_hedging_strategy_registry.py
import pytest

from app.services.hedging_strategy_registry import STRATEGIES, tiers_for


def test_four_strategies_registered():
    assert set(STRATEGIES) == {
        "delta_neutral", "delta_neutral_enhanced",
        "delta_gamma_neutral", "full_neutral",
    }


def test_delta_gamma_neutral_tiers():
    tiers = tiers_for("delta_gamma_neutral")
    assert tiers == [
        {"kind": "hard", "greeks": ["delta", "gamma"]},
        {"kind": "soft", "greeks": ["vega"]},
    ]


def test_full_neutral_is_all_hard():
    assert tiers_for("full_neutral") == [
        {"kind": "hard", "greeks": ["delta", "gamma", "vega"]},
    ]


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        tiers_for("nope")

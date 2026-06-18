# tests/test_hedging_solver.py
import pytest

from app.services.hedging_solver import Leg, solve
from app.services.hedging_strategy_registry import tiers_for

BANDS = {"delta": 50000.0, "gamma": 50000.0, "vega": 4000.0}


def test_delta_neutral_single_future():
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 1000000.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral"))
    assert res.status == "feasible"
    assert res.quantities["IC2406"] == -10
    assert abs(res.residual["delta"]) <= BANDS["delta"]


def test_no_trade_when_exposure_smaller_than_one_lot_would_overshoot():
    # Δ exposure 12,000; one IC lot moves δcash 20,000. Trading 1 lot overshoots
    # to -8,000 (still outside the ±500 band) — flipping the sign without reaching
    # neutrality. The solver must leave it unhedged rather than overshoot.
    legs = [Leg(key="IC2406", delta=20000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 12000.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands={"delta": 500.0, "gamma": 50000.0, "vega": 10000.0},
                tiers=tiers_for("delta_neutral"))
    assert res.quantities["IC2406"] == 0
    assert res.status == "infeasible"
    assert res.residual["delta"] == 12000.0


def test_partial_hedge_that_reduces_without_flipping_is_kept():
    # Δ exposure 100,000; one lot moves δcash 30,000. 3 lots → +10,000 (reduced,
    # same sign); a 4th would flip to -20,000 (out of band) and is forbidden.
    legs = [Leg(key="IC2406", delta=30000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 100000.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands={"delta": 500.0, "gamma": 50000.0, "vega": 10000.0},
                tiers=tiers_for("delta_neutral"))
    assert res.quantities["IC2406"] == -3
    assert res.residual["delta"] == 10000.0


def test_delta_gamma_neutral_tight_bands_pin_exact_solution():
    # Tight bands force residual delta AND gamma to ~0, pinning the unique 2x2 fit.
    tight = {"delta": 1.0, "gamma": 1.0, "vega": 1.0}
    legs = [
        Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0),
        Leg(key="IO2406C", delta=20000.0, gamma=5000.0, vega=0.0),
    ]
    res = solve(targets={"delta": 1000000.0, "gamma": 100000.0, "vega": 0.0},
                legs=legs, bands=tight, tiers=tiers_for("delta_gamma_neutral"))
    assert res.status == "feasible"
    # gamma: 100000 + (-20)*5000 = 0 ; delta: 1000000 + (-6)*100000 + (-20)*20000 = 0
    assert res.quantities == {"IC2406": -6, "IO2406C": -20}
    assert abs(res.residual["delta"]) <= 1.0
    assert abs(res.residual["gamma"]) <= 1.0


def test_delta_gamma_neutral_loose_bands_minimize_lots():
    # With loose bands the solver meets the bands with the FEWEST total lots, not
    # an exact zero — gamma sits at its band edge (qo=-10), delta cleaned by qf=-8.
    legs = [
        Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0),
        Leg(key="IO2406C", delta=20000.0, gamma=5000.0, vega=0.0),
    ]
    res = solve(targets={"delta": 1000000.0, "gamma": 100000.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_gamma_neutral"))
    assert res.status == "feasible"
    assert res.quantities == {"IC2406": -8, "IO2406C": -10}
    assert abs(res.residual["delta"]) <= BANDS["delta"]
    assert abs(res.residual["gamma"]) <= BANDS["gamma"]


def test_gamma_hard_with_no_option_is_infeasible():
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 0.0, "gamma": 88000.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_gamma_neutral"))
    assert res.status == "infeasible"
    binding = {b["greek"] for b in res.binding}
    assert "gamma" in binding


def test_parsimony_prefers_smallest_total_lots():
    # Two identical futures; the solver must not split lots across both.
    legs = [
        Leg(key="A", delta=100000.0, gamma=0.0, vega=0.0),
        Leg(key="B", delta=100000.0, gamma=0.0, vega=0.0),
    ]
    res = solve(targets={"delta": 500000.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral"))
    assert res.status == "feasible"
    assert abs(res.quantities["A"]) + abs(res.quantities["B"]) == 5


def test_zero_target_yields_no_trade():
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 0.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral"))
    assert res.quantities["IC2406"] == 0
    assert res.status == "feasible"


def test_soft_tier_violation_allowed_when_hard_met():
    # enhanced: delta hard (met by future), gamma/vega soft (future can't move them)
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 300000.0, "gamma": 90000.0, "vega": 9000.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral_enhanced"))
    assert res.status == "feasible"          # hard (delta) satisfied
    assert res.quantities["IC2406"] == -3
    assert res.in_band["gamma"] is False     # soft band breached, but allowed


def test_empty_tiers_raises():
    legs = [Leg(key="x", delta=1.0, gamma=0.0, vega=0.0)]
    with pytest.raises(ValueError, match="tiers must be non-empty"):
        solve(targets={"delta": 0.0, "gamma": 0.0, "vega": 0.0},
              legs=legs, bands={"delta": 1.0, "gamma": 1.0, "vega": 1.0}, tiers=[])


def test_bands_missing_greek_raises():
    legs = [Leg(key="x", delta=1.0, gamma=0.0, vega=0.0)]
    tiers = [{"kind": "hard", "greeks": ["delta"]}]
    with pytest.raises(ValueError, match="bands missing entry for constrained greek"):
        solve(targets={"delta": 0.0, "gamma": 0.0, "vega": 0.0},
              legs=legs, bands={"gamma": 1.0, "vega": 1.0}, tiers=tiers)

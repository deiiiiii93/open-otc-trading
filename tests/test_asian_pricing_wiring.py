"""Component B — Asian observation_records → QuantArk records at pricing time.

The booked schedule lives in product_kwargs.observation_records as
{observation_date, weight, observed_price?}. At pricing time these become
QuantArk-ready {observation_time, observed_price?, weight?} records, split
past/future relative to the valuation date.
"""

from datetime import datetime

import pytest

from app.services.quantark import (
    PricingEnvironmentSnapshot,
    _asian_observation_records_for_pricing,
)


def _market(valuation="2025-01-01"):
    return PricingEnvironmentSnapshot(valuation_date=datetime.fromisoformat(valuation))


def test_future_records_get_observation_time_and_weight_no_observed_price():
    records = [
        {"observation_date": "2025-04-01", "weight": 0.25},
        {"observation_date": "2025-07-01", "weight": 0.75},
    ]
    out = _asian_observation_records_for_pricing(records, _market("2025-01-01"))
    assert len(out) == 2
    assert all(r["observation_time"] > 0 for r in out)
    assert all(r.get("observed_price") is None for r in out)
    assert [r["weight"] for r in out] == [0.25, 0.75]


def test_past_future_split_and_uncaptured_drop():
    records = [
        {"observation_date": "2024-10-01", "weight": 0.2, "observed_price": 101.0},  # past, captured
        {"observation_date": "2024-11-01", "weight": 0.2},                            # past, uncaptured
        {"observation_date": "2025-06-01", "weight": 0.3, "observed_price": 999.0},   # future-of-valuation, stored price
        {"observation_date": "2025-09-01", "weight": 0.3},                            # future
    ]
    out = _asian_observation_records_for_pricing(records, _market("2025-01-01"))
    # uncaptured past dropped -> 3 records
    assert len(out) == 3
    past = [r for r in out if r["observation_time"] <= 0]
    fut = [r for r in out if r["observation_time"] > 0]
    assert len(past) == 1 and past[0]["observed_price"] == 101.0
    # the stored-price future record must be nulled (it is future relative to valuation)
    assert all(r["observed_price"] is None for r in fut)


def test_already_resolved_observation_time_records_pass_through():
    # Callers (e.g. pricing-preview) may supply QuantArk-ready records keyed by
    # observation_time. These must NOT be discarded by the booked-form converter.
    records = [
        {"observation_time": -0.25, "observed_price": 105.0, "weight": 0.5},
        {"observation_time": 0.5, "observed_price": None, "weight": 0.5},
    ]
    out = _asian_observation_records_for_pricing(records, _market("2025-01-01"))
    assert len(out) == 2
    assert out[0]["observation_time"] == -0.25 and out[0]["observed_price"] == 105.0
    assert out[1]["observation_time"] == 0.5


def test_empty_or_non_list_returns_empty():
    assert _asian_observation_records_for_pricing(None, _market()) == []
    assert _asian_observation_records_for_pricing([], _market()) == []
    assert _asian_observation_records_for_pricing("nope", _market()) == []


# --- End-to-end: records drive position pricing through build_product_for_position ---

from app.models import Position  # noqa: E402
from app.services.quantark import (  # noqa: E402
    build_pricing_env,
    build_product_for_position,
)


def _asian_position(product_kwargs):
    return Position(
        underlying="TEST",
        product_type="AsianOption",
        product_kwargs=product_kwargs,
        engine_name="AsianOptionAnalyticalEngine",
        quantity=1,
    )


def test_records_drive_weighted_and_realized_resolve_observations():
    snap = _market("2025-01-01")
    env = build_pricing_env(snap)
    records = [
        {"observation_date": "2024-10-01", "weight": 0.5, "observed_price": 110.0},  # realized
        {"observation_date": "2025-06-01", "weight": 0.25},
        {"observation_date": "2025-12-01", "weight": 0.25},
    ]
    product = build_product_for_position(
        _asian_position(
            {
                "strike": 100.0,
                "initial_price": 100.0,
                "maturity": 1.0,
                "option_type": "CALL",
                "observation_records": records,
            }
        ),
        snap,
    )
    past_prices, past_weights, future_times, future_weights, total = (
        product.resolve_observations(env)
    )
    assert past_prices == [110.0]
    assert past_weights == pytest.approx([0.5])
    assert len(future_times) == 2 and all(t > 0 for t in future_times)
    assert future_weights == pytest.approx([0.25, 0.25])
    # total == 3 (not the num_observations default) proves records drive averaging.
    assert total == 3


def test_no_records_falls_back_to_num_observations():
    snap = _market("2025-01-01")
    product = build_product_for_position(
        _asian_position(
            {
                "strike": 100.0,
                "initial_price": 100.0,
                "maturity": 1.0,
                "option_type": "CALL",
                "num_observations": 4,
            }
        ),
        snap,
    )
    assert product.observation_records is None
    assert product.num_observations == 4

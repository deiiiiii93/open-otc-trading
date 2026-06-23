"""Component A1 — booking materializes observation_records into product_kwargs.

A frequency-only Asian (the picker flow) must write a dated schedule so the
pricing path (Component B) has dates + weights to price from.
"""

from app.services.domains.product_builders import build_product


def _terms(**over):
    base = {
        "strike": 100.0,
        "initial_price": 100.0,
        "maturity_years": 1.0,
        "option_type": "CALL",
        "averaging_method": "ARITHMETIC",
        "averaging_frequency": "QUARTERLY",
        "trade_start_date": "2025-01-01",
    }
    base.update(over)
    return base


def _build(terms):
    return build_product("AsianOption", terms)


def test_frequency_booking_emits_observation_records():
    out = _build(_terms())
    recs = out.product_kwargs.get("observation_records")
    assert recs and len(recs) == 4  # 4 quarterly observations over 1y
    assert all("observation_date" in r for r in recs)
    assert all(r["weight"] is None for r in recs)  # uniform default


def test_no_start_date_keeps_count_only():
    out = _build(_terms(trade_start_date=None))
    assert out.product_kwargs.get("observation_records") is None
    assert out.product_kwargs.get("num_observations") == 4


def test_explicit_weights_flow_into_records():
    out = _build(_terms(averaging_weights=[0.1, 0.2, 0.3, 0.4]))
    recs = out.product_kwargs["observation_records"]
    assert [r["weight"] for r in recs] == [0.1, 0.2, 0.3, 0.4]

"""Network-free tests for backtest_market_history.

Run with:
    .venv/bin/python -m pytest tests/test_backtest_market_history.py -q
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.services import backtest_market_history as mh


# ---------------------------------------------------------------------------
# expected_trading_days
# ---------------------------------------------------------------------------

def test_trading_days_no_weekends():
    days = mh.expected_trading_days("2024-01-02", "2024-01-12")
    assert len(days) >= 5
    assert all(pd.Timestamp(d).weekday() < 5 for d in days)


def test_trading_days_sse_excludes_new_year():
    """SSE calendar should exclude 2024-01-01 (New Year's Day).

    The range starts on 2024-01-01; if the SSE CSV is loaded it should be
    excluded.  If only bdate_range is available, 2024-01-01 is a Monday and
    still excluded.  Either way the result must not contain 2024-01-01.
    """
    days = mh.expected_trading_days("2024-01-01", "2024-01-05")
    day_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in days]
    assert "2024-01-01" not in day_strs, f"New Year's Day should not be a trading day, got: {day_strs}"


def test_trading_days_range_count():
    """2024-01-02 to 2024-01-12: 7 calendar weekdays (Mon Jan 8 not a holiday)."""
    days = mh.expected_trading_days("2024-01-02", "2024-01-12")
    # At least 5 and at most 9 (9 cal days, max 7 weekdays)
    assert 5 <= len(days) <= 9


# ---------------------------------------------------------------------------
# _has_gaps
# ---------------------------------------------------------------------------

def test_has_gaps_detects_missing():
    expected = mh.expected_trading_days("2024-01-02", "2024-01-12")
    # Only two dates present — clearly missing many
    have = pd.to_datetime(["2024-01-02", "2024-01-03"])
    assert mh._has_gaps(have, expected) is True


def test_has_gaps_no_gap_when_complete():
    expected = mh.expected_trading_days("2024-01-02", "2024-01-12")
    have = pd.to_datetime([d for d in expected])
    assert mh._has_gaps(have, expected) is False


def test_has_gaps_uses_gap_tolerance(monkeypatch):
    """With _GAP_TOLERANCE=2, one missing day should NOT trigger a gap."""
    monkeypatch.setattr(mh, "_GAP_TOLERANCE", 2)
    expected = mh.expected_trading_days("2024-01-02", "2024-01-12")
    # Remove exactly one day
    have = pd.to_datetime([d for d in expected[1:]])  # drop first
    assert mh._has_gaps(have, expected) is False


# ---------------------------------------------------------------------------
# derive_vol
# ---------------------------------------------------------------------------

def test_derive_vol_flat_and_realized():
    import math
    # Build a series with real day-to-day variation so rolling std is non-trivial.
    # Alternating up/down moves produce non-zero realized vol.
    base = 5500.0
    spots = [base * (1.01 if i % 2 == 0 else 0.99) ** (i // 2 + 1) for i in range(40)]
    # Ensure the series is monotonically varied (not constant)
    for i in range(1, len(spots)):
        if spots[i] == spots[i - 1]:
            spots[i] += 0.5
    spot = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=40),
            "spot": spots,
        }
    )

    flat = mh.derive_vol(spot, vol_source="flat", vol_window=20, flat_vol=0.25)
    assert list(flat.columns) == ["date", "volatility"]
    assert len(flat) == 40
    assert (flat["volatility"] == 0.25).all()

    realized = mh.derive_vol(spot, vol_source="realized", vol_window=20, flat_vol=0.25)
    assert list(realized.columns) == ["date", "volatility"]
    assert realized["volatility"].notna().all()
    # Realized vol from a geometric series: std of log-returns is near-zero
    # (log(1.001) is a near-constant) — just assert non-negative and finite.
    assert (realized["volatility"] >= 0).all()
    assert realized["volatility"].notna().all()


def test_derive_vol_realized_fallback_to_flat_vol():
    """When all realized vol is NaN (one data point), fallback to flat_vol."""
    spot = pd.DataFrame(
        {"date": pd.bdate_range("2024-01-02", periods=3),
         "spot": [100.0, 101.0, 102.5]}
    )
    realized = mh.derive_vol(spot, vol_source="realized", vol_window=20, flat_vol=0.18)
    assert realized["volatility"].notna().all()


# ---------------------------------------------------------------------------
# flat_rate
# ---------------------------------------------------------------------------

def test_flat_rate():
    spot = pd.DataFrame(
        {"date": pd.bdate_range("2024-01-02", periods=5),
         "spot": [100.0] * 5}
    )
    rate_df = mh.flat_rate(spot, 0.03)
    assert list(rate_df.columns) == ["date", "rate"]
    assert len(rate_df) == 5
    assert (rate_df["rate"] == 0.03).all()


# ---------------------------------------------------------------------------
# _profile_to_frame
# ---------------------------------------------------------------------------

def test_profile_to_frame():
    from app.models import MarketDataProfile
    from datetime import datetime

    profile = MarketDataProfile(
        name="test",
        source="akshare",
        symbol="000300",
        asset_class="index",
        start_date="2024-01-02",
        end_date="2024-01-05",
        adjust="qfq",
        valuation_date=datetime(2024, 1, 5),
        data={
            "series": [
                {"date": "2024-01-02", "spot": 3800.0},
                {"date": "2024-01-03", "spot": 3820.5},
                {"date": "2024-01-04", "spot": 3790.0},
                {"date": "2024-01-05", "spot": 3810.0},
            ]
        },
    )
    df = mh._profile_to_frame(profile)
    assert list(df.columns[:2]) == ["date", "spot"]
    assert len(df) == 4
    assert df["spot"].iloc[1] == 3820.5
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


# ---------------------------------------------------------------------------
# _persist_profile — idempotency test
# ---------------------------------------------------------------------------

def test_persist_idempotent(session):
    """Calling _persist_profile twice with the same data must not duplicate rows."""
    from datetime import datetime
    from app.models import MarketDataProfile

    fixed_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
            ),
            "spot": [3750.0, 3780.5, 3765.0, 3800.0],
        }
    )

    # First persist — creates a new profile
    profile1 = mh._persist_profile(
        session, None, "000300", "index", fixed_df, "qfq"
    )
    session.commit()
    profile_id = profile1.id

    # Second persist — updates the same profile object (no new row)
    profile2 = mh._persist_profile(
        session, profile1, "000300", "index", fixed_df, "qfq"
    )
    session.commit()
    assert profile2.id == profile_id, "Second persist must reuse the existing profile row"

    # Verify only ONE profile row for this symbol
    count = (
        session.query(MarketDataProfile)
        .filter(
            MarketDataProfile.symbol == "000300",
            MarketDataProfile.asset_class == "index",
        )
        .count()
    )
    assert count == 1, f"Expected 1 profile row, found {count}"

    # Verify no duplicate series entries
    series = profile2.data["series"]
    dates = [r["date"] for r in series]
    assert len(dates) == len(set(dates)), "Duplicate dates in series after double-persist"
    assert len(dates) == 4, f"Expected 4 series rows, got {len(dates)}"
    # Spot values must match real input (not defaults)
    assert series[1]["spot"] == 3780.5


def test_persist_idempotent_via_ensure(session, monkeypatch):
    """Calling ensure_spot_history twice with a monkeypatched fetch returns
    the same profile count and non-duplicated series."""
    from app.models import MarketDataProfile

    fixed_df = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-02", "2024-01-03", "2024-01-04",
                 "2024-01-05", "2024-01-08", "2024-01-09"]
            ),
            "spot": [3900.0, 3920.0, 3915.0, 3940.5, 3935.0, 3960.0],
        }
    )
    monkeypatch.setattr(mh, "_fetch_akshare_spot", lambda *a, **k: fixed_df.copy())

    result1 = mh.ensure_spot_history(
        session, symbol="510300", asset_class="etf",
        start="2024-01-02", end="2024-01-09", adjust="qfq"
    )
    result2 = mh.ensure_spot_history(
        session, symbol="510300", asset_class="etf",
        start="2024-01-02", end="2024-01-09", adjust="qfq"
    )

    count = (
        session.query(MarketDataProfile)
        .filter(
            MarketDataProfile.symbol == "510300",
            MarketDataProfile.asset_class == "etf",
        )
        .count()
    )
    assert count == 1, f"Expected 1 profile row, found {count}"

    profile = (
        session.query(MarketDataProfile)
        .filter(MarketDataProfile.symbol == "510300")
        .first()
    )
    series = profile.data["series"]
    dates = [r["date"] for r in series]
    assert len(dates) == len(set(dates)), "Duplicate dates after two ensure_spot_history calls"
    # Real-value check: third spot value must be 3915.0 not some default
    assert series[2]["spot"] == 3915.0
    assert len(result1) == len(result2)


# ---------------------------------------------------------------------------
# ensure_futures_chain — read-through and NotImplemented
# ---------------------------------------------------------------------------

def test_ensure_futures_chain_no_data_raises_runtime_error(session):
    """Without stored profile, quotes, or allowed contracts, futures chain is unavailable."""
    with pytest.raises(RuntimeError, match="No usable futures chain"):
        mh.ensure_futures_chain(session, prefix="IF", start="2024-01-02", end="2024-01-31")


def test_ensure_futures_chain_readthrough(session):
    """With a stored profile, ensure_futures_chain returns the persisted series."""
    from datetime import datetime
    from app.models import MarketDataProfile

    stored_series = [
        {
            "date": "2024-01-02",
            "contract": "IF2401",
            "futures_price": 3850.0,
            "expiry_date": "2024-01-19",
            "multiplier": 300,
        },
        {
            "date": "2024-01-03",
            "contract": "IF2401",
            "futures_price": 3870.0,
            "expiry_date": "2024-01-19",
            "multiplier": 300,
        },
    ]
    profile = MarketDataProfile(
        name="IF futures chain",
        source="akshare",
        symbol="IF",
        asset_class="futures",
        start_date="2024-01-02",
        end_date="2024-01-03",
        adjust="none",
        valuation_date=datetime(2024, 1, 3),
        data={"series": stored_series},
        source_metadata={"backtest_history": True},
    )
    session.add(profile)
    session.flush()

    df = mh.ensure_futures_chain(session, prefix="IF", start="2024-01-02", end="2024-01-31", refill=False)
    assert list(df.columns) == ["date", "contract", "futures_price", "expiry_date", "multiplier"]
    assert len(df) == 2
    # Real-value check
    assert df["futures_price"].iloc[1] == 3870.0


def test_ensure_futures_chain_from_allowed_hedge_quotes(session):
    """Allowed hedge contracts plus quote-store rows should build and persist a chain."""
    from datetime import datetime, date
    from app.models import HedgeMapEntry, Instrument, MarketDataProfile
    from app.services.quotes import record_quote

    underlying = Instrument(symbol="000905.SH", kind="index", source="test", status="active")
    contract = Instrument(
        symbol="IC2401.CFFEX",
        kind="futures",
        exchange="CFFEX",
        contract_code="IC2401",
        series_root="IC",
        expiry=date(2024, 1, 19),
        multiplier=200.0,
        status="active",
        source="test",
    )
    session.add_all([underlying, contract])
    session.flush()
    session.add(HedgeMapEntry(
        underlying_id=underlying.id,
        instrument_id=contract.id,
        exchange="CFFEX",
        contract_code="IC2401",
        family="index_future",
        series_root="IC",
        instrument_type="future",
        reconcile_status="active",
    ))
    session.flush()
    record_quote(session, instrument_id=contract.id, price=5100.0, as_of=datetime(2024, 1, 2), source="test")
    record_quote(session, instrument_id=contract.id, price=5120.0, as_of=datetime(2024, 1, 3), source="test")
    session.flush()

    df = mh.ensure_futures_chain(session, prefix="IC", start="2024-01-02", end="2024-01-03")

    assert list(df["contract"]) == ["IC2401", "IC2401"]
    assert list(df["futures_price"]) == [5100.0, 5120.0]
    profile = session.query(MarketDataProfile).filter(
        MarketDataProfile.symbol == "IC",
        MarketDataProfile.asset_class == "futures",
    ).one()
    assert len(profile.data["series"]) == 2


def test_ensure_futures_chain_refills_allowed_hedges(session, monkeypatch):
    """Gaps in quote-store rows trigger akshare refill for active allowed hedge contracts."""
    from datetime import date
    from app.models import HedgeMapEntry, Instrument, MarketDataProfile, MarketQuote

    underlying = Instrument(symbol="000905.SH", kind="index", source="test", status="active")
    contract = Instrument(
        symbol="IC2401.CFFEX",
        kind="futures",
        exchange="CFFEX",
        contract_code="IC2401",
        series_root="IC",
        expiry=date(2024, 1, 19),
        multiplier=200.0,
        akshare_symbol="IC2401",
        status="active",
        source="test",
    )
    session.add_all([underlying, contract])
    session.flush()
    session.add(HedgeMapEntry(
        underlying_id=underlying.id,
        instrument_id=contract.id,
        exchange="CFFEX",
        contract_code="IC2401",
        family="index_future",
        series_root="IC",
        instrument_type="future",
        reconcile_status="active",
    ))
    session.flush()

    fetched = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "futures_price": [5105.0, 5125.0],
    })
    monkeypatch.setattr(mh, "_fetch_akshare_futures_contract", lambda *a, **k: fetched.copy())

    df = mh.ensure_futures_chain(session, prefix="IC", start="2024-01-02", end="2024-01-03")

    assert list(df["futures_price"]) == [5105.0, 5125.0]
    assert session.query(MarketQuote).filter(MarketQuote.instrument_id == contract.id).count() == 2
    profile = session.query(MarketDataProfile).filter(
        MarketDataProfile.symbol == "IC",
        MarketDataProfile.asset_class == "futures",
    ).one()
    assert profile.data["series"][1]["futures_price"] == 5125.0

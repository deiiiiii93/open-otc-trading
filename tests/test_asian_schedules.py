"""Calendar-accurate Asian observation schedule generation (sub-project C)."""
from datetime import date, timedelta

import pytest

from app.services.domains.schedules import (
    asian_observation_records,
    china_sse_business_days,
    add_months,
)

START = date(2024, 1, 2)  # an SSE business day


def test_monthly_schedule_has_one_record_per_month():
    recs = asian_observation_records(
        start=START, maturity_years=1.0, frequency="MONTHLY"
    )
    assert len(recs) == 12
    assert [r["sequence"] for r in recs] == list(range(1, 13))
    dates = [r["observation_date"] for r in recs]
    assert dates == sorted(dates)  # ascending
    assert all(r["weight"] is None for r in recs)  # uniform by default


def test_quarterly_schedule():
    recs = asian_observation_records(
        start=START, maturity_years=1.0, frequency="QUARTERLY"
    )
    assert len(recs) == 4


def test_daily_schedule_is_calendar_accurate_not_flat_252():
    recs = asian_observation_records(
        start=START, maturity_years=0.5, frequency="DAILY"
    )
    end = add_months(START, 6)
    expected = china_sse_business_days(START + timedelta(days=1), end)
    assert [r["observation_date"] for r in recs] == expected
    # a real 6-month window is NOT exactly 126 (=252*0.5) business days
    assert len(recs) != 126


def test_explicit_weights_carried_through():
    recs = asian_observation_records(
        start=START, maturity_years=1.0, frequency="QUARTERLY",
        weights=[1.0, 2.0, 3.0, 4.0],
    )
    assert [r["weight"] for r in recs] == [1.0, 2.0, 3.0, 4.0]


def test_weekly_schedule_has_no_duplicate_dates_across_holidays():
    # Spring Festival 2024 (~Feb 10-17): weekly anchors can roll onto the same
    # reopened business day; observation dates must stay strictly unique because
    # asian_averaging_dates is keyed by (position_id, observation_date).
    recs = asian_observation_records(
        start=date(2024, 2, 5), maturity_years=0.5, frequency="WEEKLY"
    )
    dates = [r["observation_date"] for r in recs]
    assert len(dates) == len(set(dates))  # no duplicates
    assert dates == sorted(dates)
    assert [r["sequence"] for r in recs] == list(range(1, len(recs) + 1))


def test_weights_length_mismatch_rejected():
    with pytest.raises(ValueError, match="weights"):
        asian_observation_records(
            start=START, maturity_years=1.0, frequency="QUARTERLY",
            weights=[1.0, 2.0],  # only 2 for 4 observations
        )

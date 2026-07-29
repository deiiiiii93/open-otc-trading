"""Calendar-accurate Asian observation schedule generation (sub-project C)."""
from datetime import date, timedelta

import pytest

from app.services.domains.schedules import (
    asian_observation_records_between,
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


def test_asian_records_never_exceed_exercise():
    records = asian_observation_records_between(
        start=date(2026, 7, 29),
        end=date(2027, 7, 29),
        frequency="MONTHLY",
    )

    assert records[-1]["observation_date"] == date(2027, 7, 29)
    assert all(
        record["observation_date"] <= date(2027, 7, 29)
        for record in records
    )


def test_asian_daily_records_use_absolute_end():
    start = date(2026, 7, 29)
    end = date(2026, 8, 5)

    records = asian_observation_records_between(
        start=start,
        end=end,
        frequency="DAILY",
    )

    assert [record["observation_date"] for record in records] == (
        china_sse_business_days(start + timedelta(days=1), end)
    )
    assert records[-1]["observation_date"] == end


def test_asian_weekly_records_append_absolute_end_and_remain_unique():
    records = asian_observation_records_between(
        start=date(2024, 2, 5),
        end=date(2024, 8, 5),
        frequency="WEEKLY",
    )
    dates = [record["observation_date"] for record in records]

    assert dates[-1] == date(2024, 8, 5)
    assert dates == sorted(set(dates))


def test_asian_weights_apply_after_rolled_date_deduplication(monkeypatch):
    import app.services.domains.schedules as schedules

    def coalescing_roll(day: date) -> date:
        if day in {date(2026, 2, 1), date(2026, 3, 1)}:
            return date(2026, 3, 2)
        return day

    monkeypatch.setattr(schedules, "roll_to_business_day", coalescing_roll)

    records = asian_observation_records_between(
        start=date(2026, 1, 1),
        end=date(2026, 4, 1),
        frequency="MONTHLY",
        weights=[0.4, 0.6],
    )

    assert [record["observation_date"] for record in records] == [
        date(2026, 3, 2),
        date(2026, 4, 1),
    ]
    assert [record["weight"] for record in records] == [0.4, 0.6]


@pytest.mark.parametrize("frequency", ["DAILY", "WEEKLY", "MONTHLY"])
def test_asian_between_rejects_invalid_window(frequency: str):
    with pytest.raises(ValueError, match="end must be after start"):
        asian_observation_records_between(
            start=date(2026, 7, 29),
            end=date(2026, 7, 29),
            frequency=frequency,
        )


def test_asian_between_rejects_unadjusted_terminal():
    with pytest.raises(ValueError, match="business-day adjusted"):
        asian_observation_records_between(
            start=date(2026, 7, 29),
            end=date(2026, 8, 1),
            frequency="DAILY",
        )

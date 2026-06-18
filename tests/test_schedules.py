from __future__ import annotations

from datetime import date

from app.services.domains import schedules


def test_add_months_rolls_year():
    assert schedules.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_china_sse_business_days_excludes_weekends():
    days = schedules.china_sse_business_days(date(2026, 5, 30), date(2026, 6, 5))
    # 2026-05-30 is a Saturday, 05-31 Sunday -> excluded; weekdays included
    assert date(2026, 5, 30) not in days
    assert date(2026, 6, 1) in days


def test_monthly_observation_dates_respects_lockup_and_count():
    dates = schedules.monthly_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=3
    )
    # months 3..12 inclusive -> 10 observations, all business days
    assert len(dates) == 10
    assert all(schedules.is_china_sse_business_day(d) for d in dates)
    assert dates == sorted(dates)


def test_build_ko_schedule_shape():
    sched = schedules.build_ko_schedule(
        dates=[date(2026, 4, 6), date(2026, 5, 6)],
        barriers=[101.0, 101.0],
        rates=[0.15, 0.15],
        annualized=True,
        frequency="MONTHLY",
    )
    assert sched["aggregation_mode"] == "STOP_FIRST_HIT"
    assert sched["frequency"] == "MONTHLY"
    assert sched["records"][0] == {
        "observation_date": "2026-04-06",
        "barrier": 101.0,
        "return_rate": 0.15,
        "is_rate_annualized": True,
    }


def test_daily_ki_dates_starts_day_after_start():
    dates = schedules.daily_ki_dates(start=date(2026, 1, 5), exercise=date(2026, 1, 9))
    assert date(2026, 1, 5) not in dates
    assert date(2026, 1, 6) in dates
    assert max(dates) <= date(2026, 1, 9)


def test_periodic_observation_dates_quarterly_count():
    dates = schedules.periodic_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=3, months_step=3
    )
    # months 3,6,9,12 -> 4 observations
    assert len(dates) == 4
    assert all(schedules.is_china_sse_business_day(d) for d in dates)


def test_periodic_observation_dates_semi_annual_count():
    dates = schedules.periodic_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=6, months_step=6
    )
    # months 6,12 -> 2 observations
    assert len(dates) == 2


def test_monthly_observation_dates_unchanged_as_step_one():
    dates = schedules.monthly_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=3
    )
    assert len(dates) == 10  # months 3..12 inclusive

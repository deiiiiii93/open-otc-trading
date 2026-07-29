"""Pure SSE-calendar and observation-schedule synthesis.

Single source of truth shared by the spreadsheet import adapter
(`position_adapter`) and the agent product builders (`product_builders`).
No DB, no quant-ark product objects — just dates and plain dicts.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache
from importlib import resources


def add_months(d: date, months: int) -> date:
    """Add `months` to a date, clamping the day to the target month's length."""
    base = d.month - 1 + months
    year = d.year + base // 12
    month = base % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@lru_cache(maxsize=1)
def china_sse_holidays() -> frozenset[date]:
    try:
        holiday_file = resources.files("quantark.util.calendar").joinpath(
            "holidayfile/china_sse.csv"
        )
    except Exception:
        return frozenset()
    if not holiday_file.is_file():
        return frozenset()
    holidays: set[date] = set()
    for line in holiday_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            holidays.add(date.fromisoformat(text))
        except ValueError:
            continue
    return frozenset(holidays)


def is_china_sse_business_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in china_sse_holidays()


def roll_to_business_day(day: date) -> date:
    """Roll forward to the next SSE business day (inclusive of `day`)."""
    current = day
    for _ in range(14):  # bounded: no 14-day holiday run on SSE
        if is_china_sse_business_day(current):
            return current
        current += timedelta(days=1)
    return current


def china_sse_business_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if is_china_sse_business_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


FREQUENCY_MONTHS: dict[str, int] = {
    "MONTHLY": 1,
    "QUARTERLY": 3,
    "SEMI_ANNUAL": 6,
}


def periodic_observation_dates_between(
    *,
    start: date,
    end: date,
    lockup_months: int,
    months_step: int = 1,
    day_of_month: int | None = None,
) -> list[date]:
    """Build a periodic schedule inside an absolute lifecycle window.

    ``end`` is the already-resolved contractual exercise date and is always the
    terminal observation. Following rolls that would exceed it are discarded.
    """
    _validate_absolute_window(start=start, end=end)
    if lockup_months < 0:
        raise ValueError("lockup_months must not be negative")
    if months_step <= 0:
        raise ValueError("months_step must be positive")

    anchor = start if day_of_month is None else start.replace(
        day=min(day_of_month, calendar.monthrange(start.year, start.month)[1])
    )
    rolled: list[date] = []
    month = lockup_months
    while True:
        candidate = add_months(anchor, month)
        if candidate > end:
            break
        adjusted = roll_to_business_day(candidate)
        if adjusted <= end:
            rolled.append(adjusted)
        month += months_step

    rolled.append(end)
    return list(dict.fromkeys(rolled))


def asian_observation_records_between(
    *,
    start: date,
    end: date,
    frequency: str,
    weights: list[float] | None = None,
) -> list[dict]:
    """Build an Asian averaging schedule from explicit economic dates."""
    _validate_absolute_window(start=start, end=end)
    freq = frequency.upper()
    if freq == "DAILY":
        dates = china_sse_business_days(start + timedelta(days=1), end)
    elif freq == "WEEKLY":
        anchors: list[date] = []
        current = start + timedelta(days=7)
        while current <= end:
            anchors.append(current)
            current += timedelta(days=7)
        dates = [
            adjusted
            for day in anchors
            if (adjusted := roll_to_business_day(day)) <= end
        ]
        dates.append(end)
    elif freq in FREQUENCY_MONTHS:
        step = FREQUENCY_MONTHS[freq]
        dates = periodic_observation_dates_between(
            start=start,
            end=end,
            lockup_months=step,
            months_step=step,
        )
    else:
        raise ValueError(f"unsupported averaging frequency: {frequency!r}")

    dates = list(dict.fromkeys(dates))
    if weights is not None and len(weights) != len(dates):
        raise ValueError(
            f"weights length {len(weights)} does not match observation count {len(dates)}"
        )
    return [
        {
            "observation_date": day,
            "sequence": index + 1,
            "weight": weights[index] if weights is not None else None,
        }
        for index, day in enumerate(dates)
    ]


def _validate_absolute_window(*, start: date, end: date) -> None:
    if end <= start:
        raise ValueError("end must be after start")
    if roll_to_business_day(end) != end:
        raise ValueError("end must already be business-day adjusted")


def periodic_observation_dates(
    *,
    start: date,
    maturity_years: float,
    lockup_months: int,
    months_step: int = 1,
    day_of_month: int | None = None,
) -> list[date]:
    """KO observation dates from ``start`` + lockup through maturity, stepping by
    ``months_step`` months, each rolled forward to the next SSE business day.

    ``months_step=1`` reproduces ``monthly_observation_dates`` exactly (months
    lockup..total inclusive). Larger steps give quarterly (3) / semi-annual (6).
    ``lockup_months=0`` includes month 0 — the trade-start date itself — as the
    first observation (inherited monthly behavior).

    Legacy numeric compatibility helper. New Product builders must call
    :func:`periodic_observation_dates_between`.
    """
    total_months = round(maturity_years * 12)
    anchor = start if day_of_month is None else start.replace(
        day=min(day_of_month, calendar.monthrange(start.year, start.month)[1])
    )
    dates: list[date] = []
    m = lockup_months
    while m <= total_months:
        dates.append(roll_to_business_day(add_months(anchor, m)))
        m += months_step
    return dates


def asian_observation_records(
    *,
    start: date,
    maturity_years: float,
    frequency: str,
    weights: list[float] | None = None,
) -> list[dict]:
    """Calendar-accurate averaging schedule for an Asian option.

    DAILY/WEEKLY step in calendar days; MONTHLY/QUARTERLY/SEMI_ANNUAL step in
    months (one period after ``start`` through maturity, excluding the start
    date). Every date is rolled forward to the next SSE business day. Unlike a
    flat 252-per-year count, DAILY reflects the actual business-day calendar.

    Returns records ``{observation_date, sequence, weight}`` (1-based sequence;
    ``weight`` is ``None`` for a uniform schedule). When ``weights`` is given its
    length must match the generated observation count.

    Legacy numeric compatibility helper. New Product builders must call
    :func:`asian_observation_records_between`.
    """
    freq = frequency.upper()
    end = add_months(start, round(maturity_years * 12))
    if freq == "DAILY":
        dates = china_sse_business_days(start + timedelta(days=1), end)
    elif freq == "WEEKLY":
        dates = []
        d = start + timedelta(days=7)
        while d <= end:
            dates.append(roll_to_business_day(d))
            d += timedelta(days=7)
    elif freq in FREQUENCY_MONTHS:
        step = FREQUENCY_MONTHS[freq]
        dates = periodic_observation_dates(
            start=start,
            maturity_years=maturity_years,
            lockup_months=step,
            months_step=step,
        )
    else:
        raise ValueError(f"unsupported averaging frequency: {frequency!r}")

    # Business-day rolling (esp. weekly across long SSE closures) can land two
    # anchors on the same reopened day. Observation dates must stay unique because
    # asian_averaging_dates is keyed by (position_id, observation_date).
    seen: set = set()
    deduped: list[date] = []
    for d in dates:
        if d not in seen:
            seen.add(d)
            deduped.append(d)
    dates = deduped

    if weights is not None and len(weights) != len(dates):
        raise ValueError(
            f"weights length {len(weights)} does not match observation count {len(dates)}"
        )

    return [
        {
            "observation_date": d,
            "sequence": i + 1,
            "weight": (weights[i] if weights is not None else None),
        }
        for i, d in enumerate(dates)
    ]


def monthly_observation_dates(
    *, start: date, maturity_years: float, lockup_months: int, day_of_month: int | None = None
) -> list[date]:
    """Monthly KO observation dates from `start`+lockup through maturity.

    Thin wrapper over `periodic_observation_dates` with a one-month step.
    """
    return periodic_observation_dates(
        start=start,
        maturity_years=maturity_years,
        lockup_months=lockup_months,
        months_step=1,
        day_of_month=day_of_month,
    )


def daily_ki_dates(*, start: date, exercise: date) -> list[date]:
    """Daily KI business-day observations from start+1 through exercise."""
    return china_sse_business_days(start + timedelta(days=1), exercise)


def build_ko_schedule(
    *, dates, barriers, rates, annualized: bool = False, frequency: str = "MONTHLY"
) -> dict:
    records = []
    for index, d in enumerate(dates):
        record: dict = {
            "observation_date": d.isoformat(),
            "barrier": barriers[index] if index < len(barriers) else barriers[-1],
        }
        if rates:
            record["return_rate"] = rates[index] if index < len(rates) else rates[-1]
            record["is_rate_annualized"] = annualized
        records.append(record)
    return {"records": records, "aggregation_mode": "STOP_FIRST_HIT", "frequency": frequency}


def build_ki_schedule(*, dates, barrier, frequency: str = "DAILY") -> dict:
    return {
        "records": [{"observation_date": d.isoformat(), "barrier": barrier} for d in dates],
        "aggregation_mode": "STOP_FIRST_HIT",
        "frequency": frequency,
    }

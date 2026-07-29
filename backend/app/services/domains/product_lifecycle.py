"""Deterministic booking lifecycle normalization.

This module owns calendar-tenor resolution for every Product creation path. It
is deliberately DB-free and never reads the wall clock: callers must supply the
economic dates and the server-selected conventions explicitly.
"""
from __future__ import annotations

import calendar as month_calendar
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any

from quantark.util.calendar import (
    BusinessDayConvention,
    CalendarType,
    create_calendar,
)


_TENOR_RE = re.compile(r"(?P<count>[1-9]\d*)(?P<unit>[DWMY])")
_NUMERIC_LIFECYCLE_FIELDS = ("maturity", "maturity_years")

_SCHEDULE_FAMILIES = frozenset(
    {
        "AsianOption",
        "KnockOutResetSnowballOption",
        "PhoenixOption",
        "RangeAccrualOption",
        "SnowballOption",
    }
)
_FUTURES_FAMILIES = frozenset({"Futures"})
_SPOT_FAMILIES = frozenset({"SpotInstrument"})
_CHINESE_EXCHANGES = frozenset(
    {
        "CFFEX",
        "CSI",
        "CZC",
        "DCE",
        "GFEX",
        "INE",
        "SGE",
        "SH",
        "SHF",
        "SZ",
    }
)


class LifecycleError(ValueError):
    """Stable lifecycle validation error for API/tool adapters."""

    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


class LifecycleResolutionSource(str, Enum):
    EXPLICIT_DATES = "explicit_dates"
    TENOR_FROM_INITIAL_DATE = "tenor_from_initial_date"
    TENOR_FROM_TRADE_EFFECTIVE_DATE = "tenor_from_trade_effective_date"
    NO_EXPIRY = "no_expiry"


@dataclass(frozen=True)
class LifecycleConventions:
    calendar_id: str
    expiry_roll: str = "following"
    settlement_lag_business_days: int | None = 0
    settlement_roll: str = "following"
    day_count_convention: str = "ACT_365"


@dataclass(frozen=True)
class LifecycleResolution:
    canonical_terms: dict[str, str]
    anchor_date: date | None
    expiry_field: str | None
    expiry_date: date | None
    settlement_date: date | None
    source: LifecycleResolutionSource
    conventions: LifecycleConventions | None
    input_tenor: str | None


def resolve_default_conventions(
    *,
    quantark_class: str,
    underlying: str | None,
    currency: str | None,
) -> LifecycleConventions:
    """Select the deterministic v1 convention for a product/market context.

    V1 intentionally keeps the current same-day option settlement behavior.
    Product-specific lags can be introduced in this registry without allowing
    callers or agents to invent them.
    """

    symbol = (underlying or "").strip().upper()
    suffix = symbol.rsplit(".", 1)[1] if "." in symbol else ""
    normalized_currency = (currency or "").strip().upper()

    if suffix in _CHINESE_EXCHANGES or normalized_currency in {"CNY", "CNH"}:
        calendar_id = "CHINA_SSE"
    elif normalized_currency == "USD":
        calendar_id = "US"
    elif normalized_currency == "EUR":
        calendar_id = "TARGET"
    elif normalized_currency == "GBP":
        calendar_id = "UK"
    else:
        calendar_id = "NONE"

    settlement_lag = (
        None
        if quantark_class in _FUTURES_FAMILIES | _SPOT_FAMILIES
        else 0
    )
    return LifecycleConventions(
        calendar_id=calendar_id,
        settlement_lag_business_days=settlement_lag,
    )


def resolve_product_lifecycle(
    family: str,
    *,
    trade_effective_date: date,
    initial_date: date | None = None,
    tenor: str | None = None,
    exercise_date: date | None = None,
    maturity_date: date | None = None,
    settlement_date: date | None = None,
    conventions: LifecycleConventions,
    source_terms: Mapping[str, Any] | None = None,
) -> LifecycleResolution:
    """Resolve shorthand or explicit inputs into canonical Product date terms."""

    _reject_numeric_lifecycle_fields(source_terms)

    trade_date = _coerce_date(
        trade_effective_date,
        field="trade_effective_date",
        required=True,
    )
    initial = _coerce_date(initial_date, field="initial_date")
    exercise = _coerce_date(exercise_date, field="exercise_date")
    maturity = _coerce_date(maturity_date, field="maturity_date")
    settlement = _coerce_date(settlement_date, field="settlement_date")

    if family in _SPOT_FAMILIES:
        if any(
            value is not None
            for value in (initial, tenor, exercise, maturity, settlement)
        ):
            raise LifecycleError(
                "lifecycle_unexpected_expiry",
                "Spot instruments do not accept lifecycle expiry inputs.",
            )
        return LifecycleResolution(
            canonical_terms={},
            anchor_date=None,
            expiry_field=None,
            expiry_date=None,
            settlement_date=None,
            source=LifecycleResolutionSource.NO_EXPIRY,
            conventions=None,
            input_tenor=None,
        )

    is_futures = family in _FUTURES_FAMILIES
    is_schedule = family in _SCHEDULE_FAMILIES
    expiry_field = "maturity_date" if is_futures else "exercise_date"

    if is_futures and exercise is not None:
        raise LifecycleError(
            "lifecycle_invalid_expiry_field",
            "Futures use maturity_date, not exercise_date.",
            field="exercise_date",
        )
    if not is_futures and maturity is not None:
        raise LifecycleError(
            "lifecycle_invalid_expiry_field",
            "Options use exercise_date, not maturity_date.",
            field="maturity_date",
        )
    if is_futures and settlement is not None:
        raise LifecycleError(
            "lifecycle_unexpected_settlement",
            "Futures lifecycle does not accept settlement_date.",
            field="settlement_date",
        )
    if is_schedule and initial is None:
        raise LifecycleError(
            "lifecycle_missing_anchor",
            f"{family} requires initial_date as its economic schedule anchor.",
            field="initial_date",
        )
    if not is_schedule and initial is not None:
        raise LifecycleError(
            "lifecycle_unexpected_anchor",
            f"{family} does not own an initial_date lifecycle field.",
            field="initial_date",
        )

    anchor = initial if is_schedule else trade_date
    explicit_expiry = maturity if is_futures else exercise
    resolved_from_tenor: date | None = None
    if tenor is not None:
        resolved_from_tenor = _adjust_date(
            _add_tenor(anchor, tenor),
            conventions=conventions,
            roll=conventions.expiry_roll,
        )

    adjusted_explicit: date | None = None
    if explicit_expiry is not None:
        adjusted_explicit = _adjust_date(
            explicit_expiry,
            conventions=conventions,
            roll=conventions.expiry_roll,
        )

    if adjusted_explicit is not None and resolved_from_tenor is not None:
        if adjusted_explicit != resolved_from_tenor:
            raise LifecycleError(
                "lifecycle_resolution_mismatch",
                (
                    f"{expiry_field} does not match the date resolved from "
                    "tenor and its economic anchor."
                ),
                field=expiry_field,
            )
        expiry = adjusted_explicit
        source = LifecycleResolutionSource.EXPLICIT_DATES
    elif adjusted_explicit is not None:
        expiry = adjusted_explicit
        source = LifecycleResolutionSource.EXPLICIT_DATES
    elif resolved_from_tenor is not None:
        expiry = resolved_from_tenor
        source = (
            LifecycleResolutionSource.TENOR_FROM_INITIAL_DATE
            if is_schedule
            else LifecycleResolutionSource.TENOR_FROM_TRADE_EFFECTIVE_DATE
        )
    else:
        raise LifecycleError(
            "lifecycle_missing_expiry",
            f"{family} requires {expiry_field} or tenor.",
            field=expiry_field,
        )

    if expiry <= anchor:
        raise LifecycleError(
            "lifecycle_invalid_exercise_date",
            f"{expiry_field} must be after its economic anchor.",
            field=expiry_field,
        )

    canonical_terms: dict[str, str] = {}
    if is_schedule:
        canonical_terms["initial_date"] = initial.isoformat()
    canonical_terms[expiry_field] = expiry.isoformat()

    resolved_settlement: date | None = None
    if not is_futures:
        if settlement is not None:
            resolved_settlement = _adjust_date(
                settlement,
                conventions=conventions,
                roll=conventions.settlement_roll,
            )
        else:
            lag = conventions.settlement_lag_business_days
            if lag is None:
                raise LifecycleError(
                    "lifecycle_missing_convention",
                    f"No settlement convention is configured for {family}.",
                    field="settlement_date",
                )
            resolved_settlement = _add_business_days(
                expiry,
                lag,
                conventions=conventions,
            )
            resolved_settlement = _adjust_date(
                resolved_settlement,
                conventions=conventions,
                roll=conventions.settlement_roll,
            )

        if resolved_settlement < expiry:
            raise LifecycleError(
                "lifecycle_invalid_settlement_date",
                "settlement_date must not precede the resolved expiry date.",
                field="settlement_date",
            )
        canonical_terms["settlement_date"] = resolved_settlement.isoformat()

    return LifecycleResolution(
        canonical_terms=canonical_terms,
        anchor_date=anchor,
        expiry_field=expiry_field,
        expiry_date=expiry,
        settlement_date=resolved_settlement,
        source=source,
        conventions=conventions,
        input_tenor=tenor,
    )


def _reject_numeric_lifecycle_fields(
    source_terms: Mapping[str, Any] | None,
) -> None:
    if source_terms is None:
        return
    for field in _NUMERIC_LIFECYCLE_FIELDS:
        if source_terms.get(field) is not None:
            raise LifecycleError(
                "lifecycle_mixed_expiry",
                (
                    f"{field} is a legacy numeric lifecycle input; use an "
                    "explicit date or calendar tenor."
                ),
                field=field,
            )


def _coerce_date(
    value: date | str | None,
    *,
    field: str,
    required: bool = False,
) -> date | None:
    if value is None:
        if required:
            raise LifecycleError(
                "lifecycle_missing_anchor",
                f"{field} is required.",
                field=field,
            )
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise LifecycleError(
        "lifecycle_invalid_date",
        f"{field} must be an ISO calendar date.",
        field=field,
    )


def _add_tenor(anchor: date, tenor: str) -> date:
    if not isinstance(tenor, str):
        raise LifecycleError(
            "lifecycle_invalid_tenor",
            "tenor must use the positive-integer D/W/M/Y grammar.",
            field="tenor",
        )
    match = _TENOR_RE.fullmatch(tenor)
    if match is None:
        raise LifecycleError(
            "lifecycle_invalid_tenor",
            "tenor must use the positive-integer D/W/M/Y grammar.",
            field="tenor",
        )
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit == "D":
        return anchor + timedelta(days=count)
    if unit == "W":
        return anchor + timedelta(weeks=count)
    months = count if unit == "M" else count * 12
    return _add_months(anchor, months)


def _add_months(value: date, months: int) -> date:
    base = value.month - 1 + months
    year = value.year + base // 12
    month = base % 12 + 1
    day = min(value.day, month_calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _adjust_date(
    value: date,
    *,
    conventions: LifecycleConventions,
    roll: str,
) -> date:
    convention = _business_day_convention(roll)
    calendar = _calendar(
        conventions.calendar_id,
        value.year - 1,
        value.year + 2,
    )
    adjusted = calendar.adjust_date(
        datetime.combine(value, time.min),
        convention,
    )
    return adjusted.date()


def _add_business_days(
    value: date,
    days: int,
    *,
    conventions: LifecycleConventions,
) -> date:
    if days < 0:
        raise LifecycleError(
            "lifecycle_missing_convention",
            "settlement business-day lag must not be negative.",
            field="settlement_date",
        )
    calendar = _calendar(
        conventions.calendar_id,
        value.year - 1,
        value.year + 2,
    )
    return calendar.add_business_days(
        datetime.combine(value, time.min),
        days,
    ).date()


def _business_day_convention(value: str) -> BusinessDayConvention:
    try:
        return BusinessDayConvention(value.lower())
    except (AttributeError, ValueError) as exc:
        raise LifecycleError(
            "lifecycle_missing_convention",
            f"Unsupported business-day roll convention: {value!r}.",
        ) from exc


@lru_cache(maxsize=64)
def _calendar(calendar_id: str, start_year: int, end_year: int):
    normalized = calendar_id.strip().upper()
    try:
        calendar_type = CalendarType[normalized]
    except (AttributeError, KeyError) as exc:
        raise LifecycleError(
            "lifecycle_missing_convention",
            f"Unsupported lifecycle calendar: {calendar_id!r}.",
        ) from exc
    try:
        return create_calendar(
            calendar_type,
            year_range=(start_year, end_year),
        )
    except Exception as exc:
        raise LifecycleError(
            "lifecycle_missing_convention",
            f"Unable to initialize lifecycle calendar {normalized}.",
        ) from exc

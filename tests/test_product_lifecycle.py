from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

import pytest

from app.services.domains.product_lifecycle import (
    LifecycleError,
    resolve_default_conventions,
    resolve_product_lifecycle,
)


def _conventions(
    quantark_class: str = "EuropeanVanillaOption",
    *,
    underlying: str = "MSFT",
    currency: str = "USD",
):
    return resolve_default_conventions(
        quantark_class=quantark_class,
        underlying=underlying,
        currency=currency,
    )


def test_scalar_tenor_resolves_from_trade_effective_date():
    result = resolve_product_lifecycle(
        "EuropeanVanillaOption",
        trade_effective_date=date(2026, 7, 29),
        tenor="1Y",
        conventions=_conventions(),
    )

    assert result.canonical_terms == {
        "exercise_date": "2027-07-29",
        "settlement_date": "2027-07-29",
    }
    assert result.anchor_date == date(2026, 7, 29)
    assert result.source.value == "tenor_from_trade_effective_date"


@pytest.mark.parametrize(
    ("anchor", "tenor", "expected"),
    [
        (date(2024, 2, 29), "1Y", date(2025, 2, 28)),
        (date(2026, 1, 31), "1M", date(2026, 3, 2)),
        (date(2026, 7, 29), "10D", date(2026, 8, 10)),
        (date(2026, 7, 29), "2W", date(2026, 8, 12)),
        (date(2026, 7, 29), "18M", date(2028, 1, 31)),
    ],
)
def test_calendar_tenors_are_added_then_business_day_adjusted(
    anchor: date,
    tenor: str,
    expected: date,
):
    result = resolve_product_lifecycle(
        "EuropeanVanillaOption",
        trade_effective_date=anchor,
        tenor=tenor,
        conventions=_conventions(),
    )

    assert result.expiry_date == expected


@pytest.mark.parametrize("tenor", ["1.5Y", "0Y", "-1M", "1y", " 1Y", "1Y "])
def test_invalid_string_tenor_is_rejected(tenor: str):
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "EuropeanVanillaOption",
            trade_effective_date=date(2026, 7, 29),
            tenor=tenor,
            conventions=_conventions(),
        )

    assert exc.value.code == "lifecycle_invalid_tenor"
    assert exc.value.field == "tenor"


def test_numeric_tenor_is_rejected():
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "EuropeanVanillaOption",
            trade_effective_date=date(2026, 7, 29),
            tenor=1.0,  # type: ignore[arg-type]
            conventions=_conventions(),
        )

    assert exc.value.code == "lifecycle_invalid_tenor"


def test_schedule_tenor_requires_initial_date():
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "SnowballOption",
            trade_effective_date=date(2026, 7, 29),
            tenor="1Y",
            conventions=_conventions(
                "SnowballOption",
                underlying="000905.SH",
                currency="CNY",
            ),
        )

    assert exc.value.code == "lifecycle_missing_anchor"
    assert exc.value.field == "initial_date"


def test_schedule_tenor_resolves_from_initial_date_and_keeps_it_canonical():
    result = resolve_product_lifecycle(
        "AsianOption",
        trade_effective_date=date(2026, 8, 3),
        initial_date=date(2026, 7, 29),
        tenor="1Y",
        conventions=_conventions(
            "AsianOption",
            underlying="000905.SH",
            currency="CNY",
        ),
    )

    assert result.anchor_date == date(2026, 7, 29)
    assert result.source.value == "tenor_from_initial_date"
    assert result.canonical_terms == {
        "initial_date": "2026-07-29",
        "exercise_date": "2027-07-29",
        "settlement_date": "2027-07-29",
    }


def test_explicit_exercise_and_settlement_are_canonical():
    result = resolve_product_lifecycle(
        "BarrierOption",
        trade_effective_date=date(2026, 7, 29),
        exercise_date=date(2027, 7, 29),
        settlement_date=date(2027, 8, 2),
        conventions=_conventions(),
    )

    assert result.canonical_terms == {
        "exercise_date": "2027-07-29",
        "settlement_date": "2027-08-02",
    }
    assert result.source.value == "explicit_dates"


def test_identical_tenor_and_explicit_exercise_are_accepted():
    result = resolve_product_lifecycle(
        "BarrierOption",
        trade_effective_date=date(2026, 7, 29),
        tenor="1Y",
        exercise_date=date(2027, 7, 29),
        conventions=_conventions(),
    )

    assert result.expiry_date == date(2027, 7, 29)
    assert result.source.value == "explicit_dates"


def test_conflicting_tenor_and_explicit_exercise_are_rejected():
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "BarrierOption",
            trade_effective_date=date(2026, 7, 29),
            tenor="1Y",
            exercise_date=date(2027, 8, 2),
            conventions=_conventions(),
        )

    assert exc.value.code == "lifecycle_resolution_mismatch"


def test_settlement_before_exercise_is_rejected():
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "BarrierOption",
            trade_effective_date=date(2026, 7, 29),
            exercise_date=date(2027, 7, 29),
            settlement_date=date(2027, 7, 28),
            conventions=_conventions(),
        )

    assert exc.value.code == "lifecycle_invalid_settlement_date"
    assert exc.value.field == "settlement_date"


@pytest.mark.parametrize("field", ["maturity", "maturity_years"])
def test_numeric_lifecycle_fields_are_rejected_from_source_terms(field: str):
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "EuropeanVanillaOption",
            trade_effective_date=date(2026, 7, 29),
            tenor="1Y",
            conventions=_conventions(),
            source_terms={field: 1.0},
        )

    assert exc.value.code == "lifecycle_mixed_expiry"
    assert exc.value.field == field


def test_futures_uses_maturity_date_without_settlement():
    result = resolve_product_lifecycle(
        "Futures",
        trade_effective_date=date(2026, 7, 29),
        tenor="6M",
        conventions=_conventions(
            "Futures",
            underlying="IF2612.CFFEX",
            currency="CNY",
        ),
    )

    assert result.canonical_terms == {"maturity_date": "2027-01-29"}
    assert result.expiry_field == "maturity_date"
    assert result.settlement_date is None


def test_futures_accepts_explicit_maturity_date():
    result = resolve_product_lifecycle(
        "Futures",
        trade_effective_date=date(2026, 7, 29),
        maturity_date=date(2026, 12, 18),
        conventions=_conventions(
            "Futures",
            underlying="IF2612.CFFEX",
            currency="CNY",
        ),
    )

    assert result.canonical_terms == {"maturity_date": "2026-12-18"}


def test_spot_rejects_expiry_input():
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "SpotInstrument",
            trade_effective_date=date(2026, 7, 29),
            tenor="1Y",
            conventions=_conventions("SpotInstrument"),
        )

    assert exc.value.code == "lifecycle_unexpected_expiry"


def test_spot_without_expiry_returns_empty_lifecycle_envelope():
    result = resolve_product_lifecycle(
        "SpotInstrument",
        trade_effective_date=date(2026, 7, 29),
        conventions=_conventions("SpotInstrument"),
    )

    assert result.canonical_terms == {}
    assert result.expiry_field is None
    assert result.expiry_date is None
    assert result.source.value == "no_expiry"


@pytest.mark.parametrize(
    ("underlying", "currency", "expected"),
    [
        ("MSFT", "USD", "US"),
        ("000905.SH", "CNY", "CHINA_SSE"),
        ("IF2612.CFFEX", "CNY", "CHINA_SSE"),
        ("SX5E", "EUR", "TARGET"),
        ("UKX", "GBP", "UK"),
        ("UNKNOWN", "ZZZ", "NONE"),
    ],
)
def test_default_calendar_registry(
    underlying: str,
    currency: str,
    expected: str,
):
    conventions = resolve_default_conventions(
        quantark_class="EuropeanVanillaOption",
        underlying=underlying,
        currency=currency,
    )

    assert conventions.calendar_id == expected


def test_default_settlement_policy_distinguishes_options_and_non_options():
    option = _conventions()
    futures = _conventions("Futures", underlying="IF2612.CFFEX", currency="CNY")
    spot = _conventions("SpotInstrument")

    assert option.settlement_lag_business_days == 0
    assert futures.settlement_lag_business_days is None
    assert spot.settlement_lag_business_days is None


def test_repeat_resolution_has_byte_identical_provenance():
    kwargs = {
        "family": "SnowballOption",
        "trade_effective_date": date(2026, 7, 29),
        "initial_date": date(2026, 7, 29),
        "tenor": "1Y",
        "conventions": _conventions(
            "SnowballOption",
            underlying="000905.SH",
            currency="CNY",
        ),
    }

    first = resolve_product_lifecycle(**kwargs)
    second = resolve_product_lifecycle(**kwargs)

    def serialize(value) -> bytes:
        return json.dumps(
            asdict(value),
            default=lambda item: item.isoformat() if isinstance(item, date) else item.value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    assert serialize(first) == serialize(second)

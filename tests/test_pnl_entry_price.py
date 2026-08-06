import pytest

from app.services.pnl.entry_price import entry_price_from_quote, inception_pnl


def _metrics(rows: list[dict]) -> dict:
    return {"positions": rows}


def _row(position_id: int, price: float, quantity: float, market_value: float) -> dict:
    return {
        "position_id": position_id,
        "price": price,
        "quantity": quantity,
        "market_value": market_value,
        "pricing_ok": True,
    }


def test_positions_without_a_basis_are_excluded_and_counted():
    metrics = _metrics([
        _row(1, price=15.0, quantity=100.0, market_value=1500.0),
        _row(2, price=9.0, quantity=200.0, market_value=1800.0),
    ])
    # Position 2 has no real basis (the seeded 0.0 default).
    result = inception_pnl(metrics, {1: 10.0, 2: 0.0})

    assert result["positions"] == {1: pytest.approx(500.0)}
    assert result["total"] == pytest.approx(500.0)
    assert result["basis_missing"] == [2]
    assert result["basis_missing_count"] == 1
    assert result["covered_count"] == 1


def test_none_basis_is_treated_as_missing_not_as_zero():
    metrics = _metrics([_row(1, price=15.0, quantity=100.0, market_value=1500.0)])

    result = inception_pnl(metrics, {1: None})

    assert result["total"] == pytest.approx(0.0)
    assert result["basis_missing"] == [1]
    assert result["covered_count"] == 0


def test_inception_pnl_is_price_minus_basis_times_quantity():
    metrics = _metrics([_row(1, price=12.5, quantity=750.0, market_value=9375.0)])

    result = inception_pnl(metrics, {1: 9.15})

    assert result["positions"][1] == pytest.approx((12.5 - 9.15) * 750.0)
    assert result["basis_missing"] == []


def test_negative_quantity_short_position_signs_correctly():
    # Short 400 at 100, now worth 90 => profit of 4000.
    metrics = _metrics([_row(1, price=90.0, quantity=-400.0, market_value=-36000.0)])

    result = inception_pnl(metrics, {1: 100.0})

    assert result["positions"][1] == pytest.approx(4000.0)


def test_quote_payload_prefers_unit_price():
    assert entry_price_from_quote(
        {"unit_price": 34.19, "achieved_price": 99.0}
    ) == pytest.approx(34.19)


def test_quote_payload_falls_back_to_achieved_price():
    assert entry_price_from_quote({"achieved_price": 34.19}) == pytest.approx(34.19)


def test_quote_payload_without_a_price_returns_none():
    assert entry_price_from_quote({"status": "priced"}) is None
    assert entry_price_from_quote({}) is None
    assert entry_price_from_quote({"unit_price": None}) is None


def test_reporting_reader_agrees_with_the_booking_time_reader():
    """The RFQ path already fills entry_price via rfq._quote_unit_price.

    That is the BOOKING-time reader; entry_price_from_quote is the REPORTING-time
    reader. If they disagree on which key holds the traded price, a position's
    stored basis and its reported basis diverge silently, so pin them equal.
    """
    from app.services.rfq import _quote_unit_price

    payloads = [
        {"unit_price": 34.19, "achieved_price": 99.0, "price": 1.0},
        {"achieved_price": 34.19, "price": 1.0},
        {"price": 12.5},
        {"target_value": 7.25},
        {"status": "priced"},
        {},
    ]
    for payload in payloads:
        assert entry_price_from_quote(payload) == _quote_unit_price(payload), payload

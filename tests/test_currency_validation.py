from app.services.currency_codes import (
    ISO_4217_CODES,
    is_valid_currency,
    normalize_currency,
)


def test_normalize_strips_and_uppercases():
    assert normalize_currency(" usd ") == "USD"
    assert normalize_currency("cny") == "CNY"


def test_is_valid_currency():
    assert is_valid_currency("usd") is True
    assert is_valid_currency("USD") is True
    assert is_valid_currency("CNY") is True
    assert is_valid_currency("XYZ") is False
    assert is_valid_currency("US") is False
    assert is_valid_currency("") is False


def test_common_desk_currencies_present():
    for code in ("CNY", "USD", "EUR", "HKD", "JPY", "GBP", "AUD", "SGD", "CHF", "CAD"):
        assert code in ISO_4217_CODES


from datetime import datetime

import pytest
from pydantic import ValidationError


def test_fxrate_create_validates_and_normalizes():
    from app.schemas import FxRateCreate

    ok = FxRateCreate(base_currency="usd", quote_currency="cny", rate=7.2,
                      as_of_date=datetime(2026, 6, 2))
    assert ok.base_currency == "USD" and ok.quote_currency == "CNY"

    with pytest.raises(ValidationError):
        FxRateCreate(base_currency="USdD", quote_currency="CNY", rate=7.2,
                     as_of_date=datetime(2026, 6, 2))


def test_fxrate_akshare_request_validates():
    from app.schemas import FxRateAkshareRequest

    ok = FxRateAkshareRequest(base_currency="usd", quote_currency="cny")
    assert ok.base_currency == "USD" and ok.quote_currency == "CNY"
    with pytest.raises(ValidationError):
        FxRateAkshareRequest(base_currency="XYZ", quote_currency="CNY")


def test_thread_update_report_currency_validates():
    from app.schemas import AgentThreadUpdate

    assert AgentThreadUpdate(report_currency="usd").report_currency == "USD"
    assert AgentThreadUpdate(report_currency="by_position").report_currency == "by_position"
    assert AgentThreadUpdate(report_currency=None).report_currency is None
    assert AgentThreadUpdate(title="x").report_currency is None
    with pytest.raises(ValidationError):
        AgentThreadUpdate(report_currency="US")
    with pytest.raises(ValidationError):
        AgentThreadUpdate(report_currency="XYZ")

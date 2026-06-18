"""Parsing tests for fetch_akshare_fx_rate against the REAL ak.fx_spot_quote()
schema (columns 货币对/买报价/卖报价, pairs like 'USD/CNY', '100JPY/CNY').
The akshare frame is mocked so the suite never hits the network."""
from __future__ import annotations

import pandas as pd
import pytest

from app.services.fx import _fx_quote_rate, fetch_akshare_fx_rate

USD_CNY_MID = (6.7621 + 6.7622) / 2
JPY100_CNY_MID = (4.2331 + 4.2333) / 2


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "货币对": ["USD/CNY", "EUR/CNY", "100JPY/CNY", "CNY/MYR"],
            "买报价": [6.7621, 7.8689, 4.2331, float("nan")],
            "卖报价": [6.7622, 7.8692, 4.2333, float("nan")],
        }
    )


def test_exact_pair_returns_mid():
    assert _fx_quote_rate(_frame(), "USD", "CNY") == pytest.approx(USD_CNY_MID)


def test_per_100_unit_convention_divided_out():
    # '100JPY/CNY' means 100 JPY = mid CNY, so 1 JPY = mid / 100.
    assert _fx_quote_rate(_frame(), "JPY", "CNY") == pytest.approx(JPY100_CNY_MID / 100)


def test_nan_quote_is_missing():
    assert _fx_quote_rate(_frame(), "CNY", "MYR") is None


def test_absent_pair_is_missing():
    assert _fx_quote_rate(_frame(), "GBP", "CNY") is None


def test_fetch_identity_no_network():
    assert fetch_akshare_fx_rate("usd", "USD") == 1.0


def test_fetch_direct(monkeypatch):
    monkeypatch.setattr("akshare.fx_spot_quote", _frame)
    assert fetch_akshare_fx_rate("USD", "CNY") == pytest.approx(USD_CNY_MID)


def test_fetch_inverse_derived(monkeypatch):
    # CNY/USD is not listed; USD/CNY is -> fetch returns 1 / mid(USD/CNY).
    monkeypatch.setattr("akshare.fx_spot_quote", _frame)
    assert fetch_akshare_fx_rate("CNY", "USD") == pytest.approx(1.0 / USD_CNY_MID)


def test_fetch_missing_raises(monkeypatch):
    monkeypatch.setattr("akshare.fx_spot_quote", _frame)
    with pytest.raises(ValueError):
        fetch_akshare_fx_rate("GBP", "JPY")

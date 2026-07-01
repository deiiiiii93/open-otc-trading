from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.schemas import AkshareSnapshotRequest
from app.services import market_data
from app.services.market_data import fetch_akshare_snapshot


def test_fetch_akshare_index_accepts_display_symbol_suffix(monkeypatch):
    calls: list[tuple[str, str]] = []

    def stock_zh_index_daily(symbol: str):
        calls.append(("sina", symbol))
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "open": 4919.425,
                    "high": 5001.12,
                    "low": 4919.425,
                    "close": 4998.342,
                    "volume": 26845453700,
                }
            ]
        )

    def index_zh_a_hist(**_kwargs):
        raise AssertionError("EastMoney fallback should not be called")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_zh_index_daily=stock_zh_index_daily,
            index_zh_a_hist=index_zh_a_hist,
        ),
    )

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="000300.SH",
            asset_class="index",
            start_date="2026-04-29",
            end_date="2026-05-13",
            adjust="qfq",
        )
    )

    assert calls == [("sina", "sh000300")]
    assert snapshot.symbol == "000300.SH"
    assert snapshot.source_metadata["fallback"] is False
    assert snapshot.data["spot"] == 4998.342


def test_fetch_akshare_csindex_symbol_uses_csindex_history(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def stock_zh_index_hist_csindex(
        symbol: str, start_date: str, end_date: str
    ):
        calls.append((symbol, start_date, end_date))
        return pd.DataFrame(
            [
                {
                    "日期": "2026-05-15",
                    "指数代码": "931059",
                    "指数中文简称": "同业存单AAA",
                    "开盘": None,
                    "最高": None,
                    "最低": None,
                    "收盘": 140.81,
                    "成交量": 0.0,
                }
            ]
        )

    def stock_zh_index_daily(**_kwargs):
        raise AssertionError("CSIndex-only symbols should not use SH/SZ quotes")

    def index_zh_a_hist(**_kwargs):
        raise AssertionError("CSIndex-only symbols should not use EastMoney A-index history")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_zh_index_hist_csindex=stock_zh_index_hist_csindex,
            stock_zh_index_daily=stock_zh_index_daily,
            index_zh_a_hist=index_zh_a_hist,
        ),
    )

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="931059.CSI",
            asset_class="index",
            start_date="2026-05-04",
            end_date="2026-05-18",
            adjust="qfq",
        )
    )

    assert calls == [("931059", "20260504", "20260518")]
    assert snapshot.symbol == "931059.CSI"
    assert snapshot.source_metadata == {
        "source_name": "AKShare stock_zh_index_hist_csindex",
        "fallback": False,
    }
    assert snapshot.data["spot"] == 140.81
    assert snapshot.data["latest"]["open"] is None

    calls.clear()
    stripped_snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="931059",
            asset_class="index",
            start_date="2026-05-04",
            end_date="2026-05-18",
            adjust="qfq",
        )
    )

    assert calls == [("931059", "20260504", "20260518")]
    assert stripped_snapshot.symbol == "931059"
    assert (
        stripped_snapshot.source_metadata["source_name"]
        == "AKShare stock_zh_index_hist_csindex"
    )
    assert stripped_snapshot.data["spot"] == 140.81


def test_fetch_akshare_etf_uses_sina_etf_history_for_display_symbol(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fund_etf_hist_sina(symbol: str):
        calls.append(("sina_etf", symbol))
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "open": 0.782,
                    "high": 0.785,
                    "low": 0.779,
                    "close": 0.780,
                    "volume": 908019797,
                }
            ]
        )

    def fund_etf_hist_em(**_kwargs):
        raise AssertionError("EastMoney ETF fallback should not be called")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            fund_etf_hist_sina=fund_etf_hist_sina,
            fund_etf_hist_em=fund_etf_hist_em,
        ),
    )

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="512800.SH",
            asset_class="etf",
            start_date="2026-04-29",
            end_date="2026-05-13",
            adjust="qfq",
        )
    )

    assert calls == [("sina_etf", "sh512800")]
    assert snapshot.symbol == "512800.SH"
    assert snapshot.source_metadata["fallback"] is False
    assert snapshot.data["spot"] == 0.780


def test_fetch_akshare_shenzhen_etf_uses_sz_prefix(monkeypatch):
    calls: list[str] = []

    def fund_etf_hist_sina(symbol: str):
        calls.append(symbol)
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "open": 2.200,
                    "high": 2.218,
                    "low": 2.199,
                    "close": 2.211,
                    "volume": 444482557,
                }
            ]
        )

    def fund_etf_hist_em(**_kwargs):
        raise AssertionError("EastMoney ETF fallback should not be called")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            fund_etf_hist_sina=fund_etf_hist_sina,
            fund_etf_hist_em=fund_etf_hist_em,
        ),
    )

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="159980.SZ",
            asset_class="etf",
            start_date="2026-04-29",
            end_date="2026-05-13",
            adjust="qfq",
        )
    )

    assert calls == ["sz159980"]
    assert snapshot.symbol == "159980.SZ"
    assert snapshot.source_metadata["fallback"] is False
    assert snapshot.data["spot"] == 2.211


def test_fetch_akshare_fx_rate_builds_spot_snapshot(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_fetch(base: str, quote: str) -> float:
        calls.append((base, quote))
        return 7.2

    monkeypatch.setattr("app.services.market_data.fetch_akshare_fx_rate", fake_fetch)
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace())

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="USD/CNY",
            asset_class="fx_rate",
            start_date="2026-06-02",
            end_date="2026-06-02",
            adjust="spot",
        )
    )

    assert calls == [("USD", "CNY")]
    assert snapshot.symbol == "USD/CNY"
    assert snapshot.asset_class == "fx_rate"
    assert snapshot.source_metadata["source_name"] == "AKShare fx_spot_quote"
    assert snapshot.source_metadata["base_currency"] == "USD"
    assert snapshot.source_metadata["quote_currency"] == "CNY"
    assert snapshot.data["spot"] == 7.2
    assert snapshot.data["latest"]["close"] == 7.2


def test_fetch_akshare_us_stock_uses_stock_us_daily(monkeypatch):
    calls: list[tuple[str, str]] = []

    def stock_us_daily(symbol: str, adjust: str = ""):
        calls.append((symbol, adjust))
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-12",
                    "open": 190.1,
                    "high": 192.0,
                    "low": 189.4,
                    "close": 191.5,
                    "volume": 1000,
                },
                {
                    "date": "2026-05-13",
                    "open": 191.5,
                    "high": 194.2,
                    "low": 190.8,
                    "close": 193.7,
                    "volume": 1200,
                },
            ]
        )

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_us_daily=stock_us_daily))

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="AAPL",
            asset_class="stock",
            start_date="2026-05-12",
            end_date="2026-05-13",
            adjust="qfq",
        )
    )

    assert calls == [("AAPL", "qfq")]
    assert snapshot.symbol == "AAPL"
    assert snapshot.asset_class == "stock"
    assert snapshot.source_metadata == {
        "source_name": "AKShare stock_us_daily",
        "fallback": False,
    }
    assert snapshot.data["spot"] == 193.7


def test_fetch_akshare_us_stock_repairs_stale_index_asset_class(monkeypatch):
    calls: list[str] = []

    def stock_us_daily(symbol: str, adjust: str = ""):
        calls.append(symbol)
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "open": 177.0,
                    "high": 179.0,
                    "low": 176.0,
                    "close": 178.25,
                    "volume": 900,
                }
            ]
        )

    def stock_zh_index_daily(**_kwargs):
        raise AssertionError("US tickers must not route through China index quotes")

    def index_zh_a_hist(**_kwargs):
        raise AssertionError("US tickers must not route through China index history")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_us_daily=stock_us_daily,
            stock_zh_index_daily=stock_zh_index_daily,
            index_zh_a_hist=index_zh_a_hist,
        ),
    )

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="TSLA",
            asset_class="index",
            start_date="2026-05-13",
            end_date="2026-05-13",
            adjust="qfq",
        )
    )

    assert calls == ["TSLA"]
    assert snapshot.symbol == "TSLA"
    assert snapshot.asset_class == "stock"
    assert snapshot.source_metadata["source_name"] == "AKShare stock_us_daily"
    assert snapshot.data["spot"] == 178.25


def test_fetch_akshare_sge_spot_maps_au9999_to_sge_symbol(monkeypatch):
    calls: list[str] = []

    def spot_hist_sge(symbol: str):
        calls.append(symbol)
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-13",
                    "open": 1030.0,
                    "high": 1032.9,
                    "low": 1018.0,
                    "close": 1029.99,
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(spot_hist_sge=spot_hist_sge),
    )

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="AU9999.SGE",
            asset_class="sge_spot",
            start_date="2026-04-29",
            end_date="2026-05-13",
            adjust="qfq",
        )
    )

    assert calls == ["Au99.99"]
    assert snapshot.symbol == "AU9999.SGE"
    assert snapshot.source_metadata["fallback"] is False
    assert snapshot.data["spot"] == 1029.99


def test_fetch_akshare_subprocess_failure_returns_fallback(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=-6,
            stdout="",
            stderr="[FATAL:address_pool_manager.cc(67)] Check failed",
        )

    monkeypatch.setattr(market_data.subprocess, "run", fake_run)

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="000852",
            asset_class="index",
            start_date="2026-05-20",
            end_date="2026-05-25",
            adjust="qfq",
        )
    )

    assert snapshot.source_metadata["fallback"] is True
    assert (
        "AKShare subprocess failed with exit code -6"
        in snapshot.source_metadata["reason"]
    )
    assert "address_pool_manager" in snapshot.source_metadata["reason"]


def test_fetch_akshare_subprocess_success_returns_child_payload(monkeypatch):
    payload = {
        "name": "000852 snapshot",
        "source": "akshare",
        "symbol": "000852",
        "asset_class": "index",
        "valuation_date": datetime.now(timezone.utc).isoformat(),
        "data": {"rows": [], "latest": {"close": 8799.312}, "spot": 8799.312},
        "source_metadata": {
            "source_name": "AKShare stock_zh_index_daily",
            "fallback": False,
        },
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=market_data._CHILD_RESULT_MARKER + json.dumps(payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr(market_data.subprocess, "run", fake_run)

    snapshot = fetch_akshare_snapshot(
        AkshareSnapshotRequest(
            symbol="000852",
            asset_class="index",
            start_date="2026-05-20",
            end_date="2026-05-25",
            adjust="qfq",
        )
    )

    assert snapshot.source_metadata["fallback"] is False
    assert snapshot.data["spot"] == 8799.312

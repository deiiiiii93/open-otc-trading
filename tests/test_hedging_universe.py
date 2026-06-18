# tests/test_hedging_universe.py
from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.hedging_universe import (
    ENUMERATORS,
    FamilySpec,
    enumerate_cffex_futures,
    enumerate_cffex_options,
    contract_multiplier,
    resolve_families,
)


def _families(symbol, asset_class="index"):
    return {(s.family, s.series_root) for s in resolve_families(symbol, asset_class)}


def test_contract_multiplier_per_series():
    # CFFEX index futures: IF/IH ¥300/pt, IC/IM ¥200/pt.
    assert contract_multiplier("index_future", "IF") == 300.0
    assert contract_multiplier("index_future", "IH") == 300.0
    assert contract_multiplier("index_future", "IC") == 200.0
    assert contract_multiplier("index_future", "IM") == 200.0
    # CFFEX index options ¥100/pt; SSE/SZSE ETF options 10,000 shares/contract.
    assert contract_multiplier("index_option", "IO") == 100.0
    assert contract_multiplier("etf_option", "510500") == 10000.0
    # Unknown series / commodity families have no standard constant.
    assert contract_multiplier("index_future", "ZZ") is None
    assert contract_multiplier("commodity_future", "RB") is None


def test_csi300_resolves_future_index_option_and_two_etf_options():
    fams = _families("000300.SH")
    assert ("index_future", "IF") in fams
    assert ("index_option", "IO") in fams
    assert ("etf_option", "510300") in fams
    assert ("etf_option", "159919") in fams
    assert len(fams) == 4


def test_csi500_resolves_future_and_etf_option_but_no_index_option():
    fams = _families("000905.SH")
    assert ("index_future", "IC") in fams
    assert ("etf_option", "510500") in fams
    assert not any(f == "index_option" for f, _ in fams)
    assert len(fams) == 2


def test_csi1000_resolves_future_and_index_option():
    fams = _families("000852.SH")
    assert ("index_future", "IM") in fams
    assert ("index_option", "MO") in fams
    assert len(fams) == 2  # no ETF option for CSI 1000


def test_sse50_resolves_future_index_option_and_etf_option():
    fams = _families("000016.SH")
    assert ("index_future", "IH") in fams
    assert ("index_option", "HO") in fams
    assert ("etf_option", "510050") in fams


def test_star50_etf_resolves_to_etf_option_family():
    fams = _families("588000.SH")
    assert fams == {("etf_option", "588000")}


def test_unknown_underlying_is_unresolvable():
    assert resolve_families("ZZZZ.SH", "index") == []


def test_commodity_resolves_from_asset_class():
    fams = _families("M.DCE", asset_class="commodity")
    assert ("commodity_future", "M") in fams
    assert ("commodity_option", "M") in fams


def test_exchange_commodity_symbols_resolve_even_when_stored_as_index():
    assert _families("AU9999.SGE", asset_class="index") == {
        ("commodity_future", "AU"),
        ("commodity_option", "AU"),
    }
    assert _families("LH2609.DCE", asset_class="index") == {
        ("commodity_future", "LH"),
        ("commodity_option", "LH"),
    }
    assert _families("RB2610.SHF", asset_class="index") == {
        ("commodity_future", "RB"),
        ("commodity_option", "RB"),
    }


def test_index_lookup_takes_precedence_over_commodity_asset_class():
    # 000300 is in _INDEX_FAMILIES; asset_class='commodity' must not override it
    fams = _families("000300.SH", asset_class="commodity")
    assert any(f == "index_future" for f, _ in fams)
    assert not any(f == "commodity_future" for f, _ in fams)


def test_family_spec_has_enumerator_key():
    spec = resolve_families("000300.SH", "index")[0]
    assert isinstance(spec, FamilySpec)
    assert spec.enumerator_key == "cffex_future"


def test_enumerators_registry_has_all_five_keys():
    """ENUMERATORS must map exactly the five keys used by resolve_families."""
    assert set(ENUMERATORS.keys()) == {
        "cffex_future",
        "cffex_option",
        "etf_option",
        "commodity_future",
        "commodity_option",
    }
    for key, fn in ENUMERATORS.items():
        assert callable(fn), f"ENUMERATORS['{key}'] is not callable"


def test_cffex_future_enumerator_enriches_last_price(monkeypatch):
    fake_ak = SimpleNamespace(
        futures_contract_info_cffex=lambda date: pd.DataFrame([
            {"合约代码": "IC2606", "品种": "IC", "最后交易日": "2026-06-22"},
            {"合约代码": "IF2606", "品种": "IF", "最后交易日": "2026-06-22"},
        ]),
        futures_hist_daily_cffex=lambda date: pd.DataFrame([
            {"symbol": "IC2606", "close": 8260.4},
            {"symbol": "IF2606", "close": 4567.8},
        ]),
        get_cffex_daily=lambda date: pd.DataFrame(),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = enumerate_cffex_futures("IC")

    assert len(contracts) == 1
    assert contracts[0].contract_code == "IC2606"
    assert contracts[0].expiry.isoformat() == "2026-06-22"
    assert contracts[0].last_price == 8260.4


def test_cffex_option_enumerator_enriches_expiry(monkeypatch):
    fake_ak = SimpleNamespace(
        futures_contract_info_cffex=lambda date: pd.DataFrame([
            {"合约代码": "IO2606-C-3400", "品种": "IO", "最后交易日": "2026-06-22"},
            {"合约代码": "IO2606-P-3400", "品种": "IO", "最后交易日": "2026-06-22"},
        ]),
        option_finance_board=lambda symbol, end_month: pd.DataFrame([
            {"instrument": "IO2606-C-3400", "lastprice": 1534.8},
            {"instrument": "IO2606-P-3400", "lastprice": 0.6},
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = enumerate_cffex_options("IO")

    by_code = {c.contract_code: c for c in contracts}
    assert by_code["IO2606-C-3400"].expiry.isoformat() == "2026-06-22"
    assert by_code["IO2606-C-3400"].last_price == 1534.8
    assert by_code["IO2606-P-3400"].expiry.isoformat() == "2026-06-22"
    assert by_code["IO2606-P-3400"].last_price == 0.6


def test_cffex_enrichment_failure_keeps_base_contracts(monkeypatch):
    def quote_down(date):
        raise RuntimeError("quote source down")

    fake_ak = SimpleNamespace(
        futures_contract_info_cffex=lambda date: pd.DataFrame([
            {"合约代码": "IC2606", "品种": "IC", "最后交易日": "2026-06-22"},
        ]),
        futures_hist_daily_cffex=quote_down,
        get_cffex_daily=quote_down,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = enumerate_cffex_futures("IC")

    assert len(contracts) == 1
    assert contracts[0].contract_code == "IC2606"
    assert contracts[0].expiry.isoformat() == "2026-06-22"
    assert contracts[0].last_price is None


def test_szse_etf_option_enumerator_filters_by_underlying_name(monkeypatch):
    fake_ak = SimpleNamespace(
        option_finance_board=lambda symbol, end_month: pd.DataFrame([
            {
                "合约编码": "9006631",
                "合约简称": "创业板ETF购6月2520",
                "标的名称": "创业板ETF",
                "类型": "认购",
                "行权价": 2.52,
                "合约单位": 10000,
                "期权行权日": "2026-06-24",
            },
            {
                "合约编码": "9007001",
                "合约简称": "沪深300ETF购6月4300",
                "标的名称": "沪深300ETF",
                "类型": "认购",
                "行权价": 4.30,
                "合约单位": 10000,
                "期权行权日": "2026-06-24",
            },
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = ENUMERATORS["etf_option"]("159919")

    assert [c.contract_code for c in contracts] == ["9007001"]
    assert contracts[0].series_root == "159919"
    assert contracts[0].exchange == "SZSE"
    assert contracts[0].expiry.isoformat() == "2026-06-24"


def test_etf_option_enumerator_accepts_numeric_300etf_contract(monkeypatch):
    fake_ak = SimpleNamespace(
        option_finance_board=lambda symbol, end_month: pd.DataFrame([
            {
                "合约交易代码": "10010313.SH",
                "名称": "300ETF购6月4300",
                "标的名称": "300ETF",
                "行权价": 4.30,
                "当前价": 0.12,
            },
            {
                "合约交易代码": "10020000.SH",
                "名称": "创业板ETF购6月2520",
                "标的名称": "创业板ETF",
                "行权价": 2.52,
                "当前价": 0.10,
            },
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = ENUMERATORS["etf_option"]("510300")

    assert [c.contract_code for c in contracts] == ["10010313.SH"]
    assert contracts[0].series_root == "510300"
    assert contracts[0].exchange == "SSE"


def test_star50_etf_option_enumerator_accepts_prefixed_and_numeric_contracts(monkeypatch):
    fake_ak = SimpleNamespace(
        option_finance_board=lambda symbol, end_month: pd.DataFrame([
            {
                "合约交易代码": "588000C2606M01300",
                "行权价": 1.30,
                "当前价": 0.4977,
            },
            {
                "合约交易代码": "10010393.SH",
                "名称": "科创50ETF购6月1300",
                "标的名称": "科创50ETF",
                "行权价": 1.30,
                "当前价": 0.12,
            },
            {
                "合约交易代码": "510300C2606M04300",
                "名称": "300ETF购6月4300",
                "标的名称": "300ETF",
                "行权价": 4.30,
                "当前价": 0.10,
            },
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = ENUMERATORS["etf_option"]("588000")

    assert [c.contract_code for c in contracts] == ["588000C2606M01300", "10010393.SH"]
    assert {c.series_root for c in contracts} == {"588000"}
    assert {c.exchange for c in contracts} == {"SSE"}


def test_sse_etf_option_enumerator_filters_by_contract_prefix(monkeypatch):
    fake_ak = SimpleNamespace(
        option_finance_board=lambda symbol, end_month: pd.DataFrame([
            {
                "合约交易代码": "510300C2606M04300",
                "行权价": 4.30,
                "当前价": 0.12,
            },
            {
                "合约交易代码": "510500C2606M06000",
                "行权价": 6.00,
                "当前价": 0.20,
            },
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    contracts = ENUMERATORS["etf_option"]("510300")

    assert [c.contract_code for c in contracts] == ["510300C2606M04300"]


def test_commodity_option_enumerator_accepts_exchange_option_codes(monkeypatch):
    fake_ak = SimpleNamespace(
        option_current_em=lambda: pd.DataFrame([
            {"代码": "AU2607C712", "名称": "沪金2607购712", "行权价": 712, "最新价": 3.2},
            {"代码": "RB2610C2600", "名称": "螺纹钢2610购2600", "行权价": 2600, "最新价": 18.0},
            {"代码": "LH2607C1000", "名称": "生猪2607购1000", "行权价": 1000, "最新价": 24.0},
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    au_contracts = ENUMERATORS["commodity_option"]("AU")
    rb_contracts = ENUMERATORS["commodity_option"]("RB")
    lh_contracts = ENUMERATORS["commodity_option"]("LH")

    assert [c.contract_code for c in au_contracts] == ["AU2607C712"]
    assert au_contracts[0].option_type == "C"
    assert au_contracts[0].strike == 712
    assert [c.contract_code for c in rb_contracts] == ["RB2610C2600"]
    assert [c.contract_code for c in lh_contracts] == ["LH2607C1000"]


def test_commodity_option_enumerator_falls_back_to_sina_tables(monkeypatch):
    def option_current_em_down():
        raise RuntimeError("eastmoney unavailable")

    def contract_list(symbol):
        if symbol == "黄金期权":
            return pd.DataFrame([{"合约": "au2607"}])
        if symbol == "螺纹钢期权":
            return pd.DataFrame([{"合约": "rb2610"}])
        return pd.DataFrame()

    def contract_table(symbol, contract):
        if symbol == "黄金期权" and contract == "au2607":
            return pd.DataFrame([{
                "行权价": 712,
                "看涨合约-看涨期权合约": "au2607C712",
                "看涨合约-最新价": 3.2,
                "看跌合约-看跌期权合约": "au2607P712",
                "看跌合约-最新价": 0.16,
            }])
        if symbol == "螺纹钢期权" and contract == "rb2610":
            return pd.DataFrame([{
                "行权价": 2600,
                "看涨合约-看涨期权合约": "rb2610C2600",
                "看涨合约-最新价": 547.5,
                "看跌合约-看跌期权合约": "rb2610P2600",
                "看跌合约-最新价": 1.5,
            }])
        return pd.DataFrame()

    fake_ak = SimpleNamespace(
        option_current_em=option_current_em_down,
        option_commodity_contract_sina=contract_list,
        option_commodity_contract_table_sina=contract_table,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_ak)

    au_contracts = ENUMERATORS["commodity_option"]("AU")
    rb_contracts = ENUMERATORS["commodity_option"]("RB")

    assert [c.contract_code for c in au_contracts] == ["AU2607C712", "AU2607P712"]
    assert au_contracts[0].option_type == "C"
    assert au_contracts[0].last_price == 3.2
    assert [c.contract_code for c in rb_contracts] == ["RB2610C2600", "RB2610P2600"]
    assert rb_contracts[0].strike == 2600


# ---------------------------------------------------------------------------
# Live smoke test — skips cleanly when AKShare is absent or network is down
# ---------------------------------------------------------------------------

def _akshare_available() -> bool:
    try:
        import akshare  # noqa: F401
    except Exception:
        return False
    return True


def _is_network_error(exc: BaseException) -> bool:
    """Return True if exception is a standard network/connection failure.

    All common network errors (requests.ConnectionError, socket.timeout,
    TimeoutError, urllib.error.URLError) subclass OSError or URLError.
    """
    import urllib.error

    return isinstance(exc, (OSError, urllib.error.URLError))


@pytest.mark.skipif(not _akshare_available(), reason="akshare not installed")
def test_cffex_future_enumerator_returns_contracts_live():
    """Call ENUMERATORS['cffex_future'] against live AKShare; skip on network error."""
    try:
        contracts = ENUMERATORS["cffex_future"]("IC")
    except Exception as exc:
        if _is_network_error(exc):
            pytest.skip(f"akshare network unavailable: {exc}")
        raise

    assert isinstance(contracts, list)
    if contracts:  # markets closed/holiday may return empty; that's allowed
        c = contracts[0]
        assert c.exchange == "CFFEX", f"expected CFFEX, got {c.exchange!r}"
        assert c.contract_code, "contract_code must be non-empty"
        assert c.instrument_type == "future", f"expected future, got {c.instrument_type!r}"
        assert c.series_root == "IC", f"expected IC, got {c.series_root!r}"

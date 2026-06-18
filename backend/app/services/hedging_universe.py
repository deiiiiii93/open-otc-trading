# backend/app/services/hedging_universe.py
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from datetime import date
from typing import Callable


@dataclass(frozen=True)
class EnumeratedContract:
    family: str
    series_root: str
    exchange: str
    contract_code: str
    instrument_type: str  # "future" | "option"
    option_type: str | None = None
    strike: float | None = None
    expiry: date | None = None
    multiplier: float | None = None
    last_price: float | None = None
    akshare_symbol: str | None = None


@dataclass(frozen=True)
class FamilySpec:
    family: str
    series_root: str
    enumerator_key: str


# Exchange-standard contract multipliers (¥ per index point, or shares/contract).
# CFFEX index futures: IF/IH ¥300/pt, IC/IM ¥200/pt.
_FUTURE_MULTIPLIERS: dict[str, float] = {
    "IF": 300.0, "IH": 300.0, "IC": 200.0, "IM": 200.0,
}


def contract_multiplier(family: str | None, series_root: str | None) -> float | None:
    """Exchange-standard contract multiplier for a hedge-instrument family/series.

    These are fixed exchange constants, used to size hedges (delta_cash = δ·S·mult)
    and to persist the correct notional at booking:
      * CFFEX index futures — IF/IH ¥300/pt, IC/IM ¥200/pt.
      * CFFEX index options (IO/HO/MO) — ¥100/pt.
      * SSE/SZSE ETF options — 10,000 shares per contract.
    Commodity and unknown families return ``None`` (the multiplier varies by
    product and must come from the exchange/AKShare feed instead).
    """
    if family == "index_future":
        return _FUTURE_MULTIPLIERS.get((series_root or "").upper())
    if family == "index_option":
        return 100.0
    if family == "etf_option":
        return 10000.0
    return None


# code (symbol before the ".") -> list of (family, series_root, enumerator_key)
_INDEX_FAMILIES: dict[str, list[FamilySpec]] = {
    "000300": [  # 沪深300
        FamilySpec("index_future", "IF", "cffex_future"),
        FamilySpec("index_option", "IO", "cffex_option"),
        FamilySpec("etf_option", "510300", "etf_option"),
        FamilySpec("etf_option", "159919", "etf_option"),
    ],
    "000905": [  # 中证500 — no CFFEX index option
        FamilySpec("index_future", "IC", "cffex_future"),
        FamilySpec("etf_option", "510500", "etf_option"),
    ],
    "000852": [  # 中证1000
        FamilySpec("index_future", "IM", "cffex_future"),
        FamilySpec("index_option", "MO", "cffex_option"),
    ],
    "000016": [  # 上证50
        FamilySpec("index_future", "IH", "cffex_future"),
        FamilySpec("index_option", "HO", "cffex_option"),
        FamilySpec("etf_option", "510050", "etf_option"),
    ],
    "588000": [  # 华夏科创50ETF
        FamilySpec("etf_option", "588000", "etf_option"),
    ],
}

_COMMODITY_UNDERLYING_ROOTS: dict[str, str] = {
    "AU9999": "AU",
    "LH2609": "LH",
    "RB2610": "RB",
}

_COMMODITY_OPTION_SINA_SYMBOLS: dict[str, str] = {
    "AU": "黄金期权",
    "RB": "螺纹钢期权",
    "LH": "生猪期权",
}


def _code(symbol: str) -> str:
    return symbol.split(".", 1)[0].strip().upper()


def resolve_families(symbol: str, asset_class: str | None = None) -> list[FamilySpec]:
    """Return the instrument families that can hedge an underlying.

    Empty list = unresolvable (surfaced in the UI, skipped by the loader).
    """
    code = _code(symbol)
    if code in _INDEX_FAMILIES:
        return list(_INDEX_FAMILIES[code])
    commodity_root = _COMMODITY_UNDERLYING_ROOTS.get(code)
    if commodity_root:
        return [
            FamilySpec("commodity_future", commodity_root, "commodity_future"),
            FamilySpec("commodity_option", commodity_root, "commodity_option"),
        ]
    if (asset_class or "").lower() == "commodity":
        return [
            FamilySpec("commodity_future", code, "commodity_future"),
            FamilySpec("commodity_option", code, "commodity_option"),
        ]
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_date(value) -> date | None:
    """Coerce a value to a date, returning None on any failure.

    Handles: None, pd.NaT (detected via str(value)=="NaT" to avoid importing
    pandas), datetime/Timestamp (converted via .date()), plain date (returned
    as-is), and strings (empty/"nan"/"None"/"NaT" → None; otherwise parse first
    10 chars with '/' → '-' via date.fromisoformat).
    """
    if value is None:
        return None
    # Reject pd.NaT before the isinstance(date) branch — NaT is a datetime
    # subclass so it would otherwise pass through unchanged and corrupt DB rows.
    # We avoid importing pandas here; str(pd.NaT) == "NaT" is stable.
    if str(value) == "NaT":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value).strip()
    if text in ("", "nan", "None", "NaT"):
        return None
    text = text[:10].replace("/", "-")
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return None


def _fourth_wednesday(year: int, month: int) -> date:
    """Return the date of the fourth Wednesday of (year, month).

    The standard SSE/SZSE ETF option expiry falls on the fourth Wednesday of the
    expiry month.  Holiday roll is intentionally not applied here — this field is
    used for display/filtering only, and staleness is tolerable.
    """
    first_day = date(year, month, 1)
    # weekday(): Mon=0 … Wed=2 … Sun=6
    days_to_first_wed = (2 - first_day.weekday()) % 7
    first_wed = first_day + _dt.timedelta(days=days_to_first_wed)
    return first_wed + _dt.timedelta(weeks=3)


def _as_float(value) -> float | None:
    """Coerce a value to float, returning None for None/NaN/empty."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN (NaN != NaN)


def _latest_cffex_contract_info_df(ak):
    """Return the latest available CFFEX contract metadata dataframe."""
    today = _dt.date.today()
    for delta in range(5):
        d = today - _dt.timedelta(days=delta)
        try:
            df = ak.futures_contract_info_cffex(date=d.strftime("%Y%m%d"))
        except Exception:
            continue
        if df is not None and not df.empty:
            return df
    return None


def _latest_cffex_daily_df(ak):
    """Return latest available CFFEX daily quote dataframe, best effort."""
    today = _dt.date.today()
    fetchers = [
        getattr(ak, "futures_hist_daily_cffex", None),
        getattr(ak, "get_cffex_daily", None),
    ]
    for delta in range(5):
        d = today - _dt.timedelta(days=delta)
        date_text = d.strftime("%Y%m%d")
        for fetcher in fetchers:
            if fetcher is None:
                continue
            try:
                df = fetcher(date=date_text)
            except Exception:
                continue
            if df is not None and not df.empty:
                return df
    return None


def _cffex_expiry_by_contract(ak) -> dict[str, date]:
    df = _latest_cffex_contract_info_df(ak)
    if df is None:
        return {}
    expiries: dict[str, date] = {}
    for _, r in df.iterrows():
        code = str(r.get("合约代码") or "").strip().upper()
        expiry = _as_date(r.get("最后交易日"))
        if code and expiry is not None:
            expiries[code] = expiry
    return expiries


def _cffex_last_price_by_contract(ak) -> dict[str, float]:
    df = _latest_cffex_daily_df(ak)
    if df is None:
        return {}
    last_prices: dict[str, float] = {}
    for _, r in df.iterrows():
        code = str(r.get("symbol") or r.get("合约代码") or "").strip().upper()
        close_value = r.get("close")
        if close_value is None:
            close_value = r.get("收盘价")
        last_price = _as_float(close_value)
        if code and last_price is not None:
            last_prices[code] = last_price
    return last_prices


# ---------------------------------------------------------------------------
# AKShare adapter functions
# ---------------------------------------------------------------------------

# Series-root → Chinese display name used by option_finance_board for CFFEX index options.
_CFFEX_OPTION_SYMBOL_MAP: dict[str, str] = {
    "IO": "沪深300股指期权",
    "MO": "中证1000股指期权",
    "HO": "上证50股指期权",
}

# ETF root → (Chinese name for option_finance_board, exchange)
_ETF_OPTION_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    "510050": ("华夏上证50ETF期权", "SSE"),
    "510300": ("华泰柏瑞沪深300ETF期权", "SSE"),
    "510500": ("南方中证500ETF期权", "SSE"),
    "588000": ("华夏科创50ETF期权", "SSE"),
    "159919": ("嘉实沪深300ETF期权", "SZSE"),
}

_ETF_OPTION_UNDERLYING_ALIASES: dict[str, tuple[str, ...]] = {
    "510300": ("沪深300ETF", "300ETF"),
    "588000": ("科创50ETF", "科创板50ETF"),
    "159919": ("沪深300ETF", "300ETF"),
}


def _normalized_etf_text(value) -> str:
    if value is None:
        return ""
    if value != value:  # pandas/numpy NaN
        return ""
    return str(value or "").replace(" ", "").upper()


def _etf_row_matches_series(series_root: str, exchange: str, code: str, row) -> bool:
    """Return whether an ETF option row belongs to the requested ETF root."""
    underlying_name = _normalized_etf_text(row.get("标的名称") or row.get("名称"))
    aliases = _ETF_OPTION_UNDERLYING_ALIASES.get(series_root)
    if aliases and underlying_name:
        return any(_normalized_etf_text(alias) in underlying_name for alias in aliases)
    if exchange == "SSE":
        return code.upper().startswith(series_root.upper())
    return aliases is None


def enumerate_cffex_futures(series_root: str) -> list[EnumeratedContract]:
    """Enumerate live CFFEX index futures for a series (IF/IC/IM/IH).

    AKShare function: ak.futures_contract_info_cffex(date='YYYYMMDD')
    Columns used: 合约代码 (contract_code), 品种 (series_root filter),
                  最后交易日 (expiry).
    The function requires today's trading date; we try today and fall back
    to yesterday if the file is not yet published.
    """
    import akshare as ak

    df = _latest_cffex_contract_info_df(ak)
    if df is None or df.empty:
        return []

    last_prices = _cffex_last_price_by_contract(ak)
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        product = str(r.get("品种") or "").strip().upper()
        if product != series_root.upper():
            continue
        code = str(r.get("合约代码") or "").strip().upper()
        if not code:
            continue
        out.append(EnumeratedContract(
            family="index_future",
            series_root=series_root,
            exchange="CFFEX",
            contract_code=code,
            instrument_type="future",
            expiry=_as_date(r.get("最后交易日")),
            multiplier=contract_multiplier("index_future", series_root),
            last_price=last_prices.get(code),
            akshare_symbol=code,
        ))
    return out


def enumerate_cffex_options(series_root: str) -> list[EnumeratedContract]:
    """Enumerate CFFEX index options (IO/MO/HO) for a series root.

    AKShare function: ak.option_finance_board(symbol, end_month)
    where symbol is the Chinese display name (e.g. '沪深300股指期权').
    Iterates upcoming months (current + 3) to collect all active strikes.
    Columns used: instrument (contract_code), 行权价 (strike).
    """
    import akshare as ak

    cn_name = _CFFEX_OPTION_SYMBOL_MAP.get(series_root.upper())
    if not cn_name:
        return []

    today = _dt.date.today()
    out: list[EnumeratedContract] = []
    seen_codes: set[str] = set()
    expiries = _cffex_expiry_by_contract(ak)

    for month_offset in range(6):
        month = (today.month - 1 + month_offset) % 12 + 1
        year = today.year + (today.month - 1 + month_offset) // 12
        end_month = f"{year}{month:02d}"
        try:
            df = ak.option_finance_board(symbol=cn_name, end_month=end_month)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            # CFFEX options use 'instrument' column
            code = str(r.get("instrument") or "").strip().upper()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            # Determine option type from contract code suffix: C=call, P=put
            # CFFEX format: IO2406-C-3800
            parts = code.split("-")
            opt_type: str | None = None
            strike_val: float | None = None
            if len(parts) >= 2:
                opt_type = "C" if parts[1].upper() == "C" else "P"
            if len(parts) >= 3:
                strike_val = _as_float(parts[2])
            expiry = expiries.get(code) or _as_date(r.get("到期日") or r.get("expire"))
            out.append(EnumeratedContract(
                family="index_option",
                series_root=series_root,
                exchange="CFFEX",
                contract_code=code,
                instrument_type="option",
                option_type=opt_type,
                strike=strike_val,
                expiry=expiry,
                multiplier=contract_multiplier("index_option", series_root),
                last_price=_as_float(r.get("lastprice") or r.get("最新价") or r.get("last")),
                akshare_symbol=code,
            ))
    return out


def enumerate_etf_options(series_root: str) -> list[EnumeratedContract]:
    """Enumerate SSE/SZSE ETF options for an ETF root (510300/510500/...).

    AKShare function: ak.option_finance_board(symbol, end_month)
    where symbol is the Chinese product name mapped from the ETF root.

    SSE board columns (510xxx): 日期, 合约交易代码, 当前价, 涨跌幅, 前结价, 行权价, 数量
      — no name column, no expiry column.  option_type and expiry are inferred
      from the contract code: e.g. 510300C2607M04500
        * The character after a digit that is 'C' or 'P' encodes the call/put.
        * The four digits following that letter are YYMM (e.g. 2607 → 2026-07).
          Expiry is set to the fourth Wednesday of that month.

    SZSE board columns (159xxx): 合约编码, 合约简称, 标的名称, 类型, 行权价, 合约单位,
      期权行权日, 行权交收日.  option_type is read from 合约简称 (购/沽 name-based).
    """
    import re

    import akshare as ak

    mapping = _ETF_OPTION_SYMBOL_MAP.get(series_root)
    if not mapping:
        return []
    cn_name, exchange = mapping

    # Regex to extract C/P and YYMM from SSE-style codes: digit then C/P then 4 digits
    _sse_cp_pattern = re.compile(r"[0-9]([CP])(\d{4})", re.IGNORECASE)

    today = _dt.date.today()
    out: list[EnumeratedContract] = []
    seen_codes: set[str] = set()

    for month_offset in range(6):
        month = (today.month - 1 + month_offset) % 12 + 1
        year = today.year + (today.month - 1 + month_offset) // 12
        end_month = f"{year}{month:02d}"
        try:
            df = ak.option_finance_board(symbol=cn_name, end_month=end_month)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            # SSE uses 合约交易代码; SZSE uses 合约编码
            code = str(
                r.get("合约交易代码") or r.get("合约编码") or r.get("instrument") or ""
            ).strip()
            if not code or code in seen_codes:
                continue
            if not _etf_row_matches_series(series_root, exchange, code, r):
                continue
            seen_codes.add(code)

            # Option type: SZSE board has a name column (购/沽); SSE board does not,
            # so fall back to parsing the code for a digit-preceded C/P letter.
            name_str = str(r.get("名称") or r.get("合约简称") or "")
            if "购" in name_str or "认购" in name_str:
                opt_type: str | None = "C"
            elif "沽" in name_str or "认沽" in name_str:
                opt_type = "P"
            else:
                opt_type = None
            if opt_type is None and code:
                m = _sse_cp_pattern.search(code)
                if m:
                    opt_type = m.group(1).upper()

            # Expiry: SZSE board supplies 期权行权日; SSE board has no date column,
            # so derive expiry from YYMM embedded in the contract code.
            expiry = _as_date(r.get("期权行权日") or r.get("到期日") or r.get("expire"))
            if expiry is None and code:
                m = _sse_cp_pattern.search(code)
                if m:
                    yymm = m.group(2)  # e.g. "2607"
                    try:
                        exp_year = 2000 + int(yymm[:2])
                        exp_month = int(yymm[2:])
                        expiry = _fourth_wednesday(exp_year, exp_month)
                    except (ValueError, OverflowError):
                        expiry = None

            out.append(EnumeratedContract(
                family="etf_option",
                series_root=series_root,
                exchange=exchange,
                contract_code=code,
                instrument_type="option",
                option_type=opt_type,
                strike=_as_float(r.get("行权价") or r.get("strike")),
                expiry=expiry,
                multiplier=(_as_float(r.get("合约单位")) or contract_multiplier("etf_option", series_root)),
                last_price=_as_float(r.get("当前价") or r.get("最新价") or r.get("last")),
                akshare_symbol=code,
            ))
    return out


def enumerate_commodity_futures(series_root: str) -> list[EnumeratedContract]:
    """Enumerate commodity futures by product root code (e.g. M, CU, AU, C).

    Strategy: try each major exchange's contract info function and collect rows
    whose contract code starts with the series_root (case-insensitive).
    Exchange assignment: DCE, CZCE, SHFE, GFEX, INE tried in order.

    AKShare functions tried:
      - ak.futures_contract_info_dce() → columns: 合约代码, 最后交易日
      - ak.futures_contract_info_czce(date) → columns: 合约代码, 最后交易日待国家公布2025年节假日安排后进行调整
      - ak.futures_contract_info_shfe(date) → columns: 合约代码, 到期日
      - ak.futures_contract_info_gfex() → columns: 合约代码 (best-effort)
      - ak.futures_contract_info_ine(date) → columns: 合约代码, 到期日
    """
    import re

    import akshare as ak

    today_str = _dt.date.today().strftime("%Y%m%d")
    root_upper = series_root.upper()
    pattern = re.compile(rf"^{re.escape(root_upper)}[0-9]")

    exchange_calls: list[tuple[str, Callable]] = [
        ("DCE", lambda: ak.futures_contract_info_dce()),
        ("CZCE", lambda: ak.futures_contract_info_czce(date=today_str)),
        ("SHFE", lambda: ak.futures_contract_info_shfe(date=today_str)),
        ("GFEX", lambda: ak.futures_contract_info_gfex()),
        ("INE", lambda: ak.futures_contract_info_ine(date=today_str)),
    ]

    out: list[EnumeratedContract] = []
    for exchange, fn in exchange_calls:
        try:
            df = fn()
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            code = str(r.get("合约代码") or "").strip().upper()
            if not code or not pattern.match(code):
                continue
            expiry = _as_date(
                r.get("最后交易日") or r.get("到期日") or r.get("最后交割日")
            )
            out.append(EnumeratedContract(
                family="commodity_future",
                series_root=series_root,
                exchange=exchange,
                contract_code=code,
                instrument_type="future",
                expiry=expiry,
                akshare_symbol=code,
            ))
    return out


def enumerate_commodity_options(series_root: str) -> list[EnumeratedContract]:
    """Enumerate commodity options by product root code using East Money data.

    AKShare function: ak.option_current_em()
    Returns all tradable options across exchanges. Filters by '代码' prefix
    matching the series_root (case-insensitive, e.g. 'M' for 豆粕期权).
    Columns: 代码 (contract_code), 行权价 (strike), 最新价 (last_price),
             名称 (for option_type inference: 购/认购=call, 沽/认沽=put).

    Note: '市场标识' column encodes the exchange (10=SSE, 12=SZSE, 140=SHFE,
    141=DCE, 151=CZCE, 163=GFEX, 226=INE) but this adapter leaves exchange
    as 'UNKNOWN' to stay robust. Callers needing the exchange should enrich
    separately.
    """
    import re

    import akshare as ak

    try:
        df = ak.option_current_em()
    except Exception:
        df = None
    if df is None or df.empty:
        return _enumerate_commodity_options_sina(ak, series_root)

    root_upper = series_root.upper()
    pattern = re.compile(rf"^{re.escape(root_upper)}[0-9]")
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        code = str(r.get("代码") or "").strip().upper()
        if not code or not pattern.match(code):
            continue
        name_str = str(r.get("名称") or "")
        if "购" in name_str or "认购" in name_str:
            opt_type: str | None = "C"
        elif "沽" in name_str or "认沽" in name_str:
            opt_type = "P"
        else:
            opt_type = None
        out.append(EnumeratedContract(
            family="commodity_option",
            series_root=series_root,
            exchange="UNKNOWN",
            contract_code=code,
            instrument_type="option",
            option_type=opt_type,
            strike=_as_float(r.get("行权价")),
            last_price=_as_float(r.get("最新价")),
            akshare_symbol=code,
        ))
    return out or _enumerate_commodity_options_sina(ak, series_root)


def _enumerate_commodity_options_sina(ak, series_root: str) -> list[EnumeratedContract]:
    """Fallback commodity-option enumerator using Sina product tables."""
    sina_symbol = _COMMODITY_OPTION_SINA_SYMBOLS.get(series_root.upper())
    if not sina_symbol:
        return []
    try:
        contracts_df = ak.option_commodity_contract_sina(symbol=sina_symbol)
    except Exception:
        return []
    if contracts_df is None or contracts_df.empty:
        return []

    out: list[EnumeratedContract] = []
    seen_codes: set[str] = set()
    for contract in contracts_df.get("合约", []):
        contract_text = str(contract or "").strip()
        if not contract_text:
            continue
        try:
            table = ak.option_commodity_contract_table_sina(
                symbol=sina_symbol,
                contract=contract_text,
            )
        except Exception:
            continue
        if table is None or table.empty:
            continue
        for _, r in table.iterrows():
            legs = [
                ("C", r.get("看涨合约-看涨期权合约"), r.get("看涨合约-最新价")),
                ("P", r.get("看跌合约-看跌期权合约"), r.get("看跌合约-最新价")),
            ]
            for option_type, code_value, last_value in legs:
                code = str(code_value or "").strip().upper()
                if not code or code == "NAN" or code in seen_codes:
                    continue
                seen_codes.add(code)
                out.append(EnumeratedContract(
                    family="commodity_option",
                    series_root=series_root,
                    exchange="UNKNOWN",
                    contract_code=code,
                    instrument_type="option",
                    option_type=option_type,
                    strike=_as_float(r.get("行权价")),
                    last_price=_as_float(last_value),
                    akshare_symbol=code,
                ))
    return out


# ---------------------------------------------------------------------------
# Registry — keys must match FamilySpec.enumerator_key values in resolve_families
# ---------------------------------------------------------------------------

ENUMERATORS: dict[str, Callable[[str], list[EnumeratedContract]]] = {
    "cffex_future": enumerate_cffex_futures,
    "cffex_option": enumerate_cffex_options,
    "etf_option": enumerate_etf_options,
    "commodity_future": enumerate_commodity_futures,
    "commodity_option": enumerate_commodity_options,
}

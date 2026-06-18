"""Market-history backfill and persist service for backtests.

Backtests need a CONTINUOUS daily spot path over a window.  Policy:
  1. Look for an existing ``MarketDataProfile`` in the DB.
  2. If missing *or* the stored series has gaps vs the trading calendar,
     fetch from akshare and PERSIST (create / extend).
  3. Return the windowed ``[date, spot]`` DataFrame.

Contribution-point stubs are marked with ``# CONTRIBUTION POINT (x):``.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models import HedgeMapEntry, Instrument, MarketDataProfile, MarketQuote
from app.services.quotes import record_quote


# ---------------------------------------------------------------------------
# _GAP_TOLERANCE
# CONTRIBUTION POINT (b): threshold of missing trading days that triggers a
# full akshare refetch vs forward-fill.
# Default 0 = any single missing day triggers a full backfill.
# Raise it (e.g. to 2–5) to tolerate occasional weekend/holiday mis-counts
# or short data lags without hammering akshare on every call.
# ---------------------------------------------------------------------------
_GAP_TOLERANCE: int = 0


# ---------------------------------------------------------------------------
# SSE Trading Calendar
# ---------------------------------------------------------------------------

_SSE_HOLIDAYS: set[datetime] | None = None  # lazily populated


def _load_sse_holidays() -> set[datetime]:
    """Load China SSE holiday dates from QuantArk's installed CSV bundle.

    Falls back to an empty set (weekends-only calendar) when the package
    resource is unavailable.
    """
    global _SSE_HOLIDAYS
    if _SSE_HOLIDAYS is not None:
        return _SSE_HOLIDAYS

    csv_candidates: list[Path] = []
    try:
        resource = resources.files("quantark.util.calendar").joinpath(
            "holidayfile/china_sse.csv"
        )
        if resource.is_file():
            csv_candidates.append(Path(str(resource)))
    except Exception:
        pass

    holidays: set[datetime] = set()
    for path in csv_candidates:
        try:
            resolved = path.resolve()
            if not resolved.exists():
                continue
            with resolved.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    date_str = (row.get("date") or row.get("Date") or "").strip()
                    if not date_str:
                        continue
                    try:
                        parsed = datetime.strptime(date_str, "%Y-%m-%d")
                        holidays.add(datetime(parsed.year, parsed.month, parsed.day))
                    except ValueError:
                        continue
            break  # success — stop looking
        except Exception:
            continue

    _SSE_HOLIDAYS = holidays
    return _SSE_HOLIDAYS


def expected_trading_days(start: str, end: str) -> list[pd.Timestamp]:
    """Return list of expected SSE trading days in [start, end].

    Uses the quant-ark China SSE holiday CSV when available; falls back to
    ``pandas.bdate_range`` (Mon-Fri, no holiday adjustment) otherwise.

    Args:
        start: ISO date string e.g. "2024-01-02"
        end:   ISO date string e.g. "2024-12-31"

    Returns:
        List of ``pd.Timestamp`` objects, one per expected trading day.
    """
    holidays = _load_sse_holidays()

    bdays = pd.bdate_range(start=start, end=end)
    if not holidays:
        # Fallback: plain business days (Mon-Fri)
        return list(bdays)

    return [d for d in bdays if datetime(d.year, d.month, d.day) not in holidays]


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def _has_gaps(have_dates: pd.DatetimeIndex, expected: list[pd.Timestamp]) -> bool:
    """Return True if too many expected trading days are missing from *have_dates*.

    "Too many" is defined by :data:`_GAP_TOLERANCE`.  With the default of 0
    ANY missing day triggers a refetch.

    Args:
        have_dates: Dates already in the stored profile (as DatetimeIndex/array).
        expected:   Full list of expected trading days from :func:`expected_trading_days`.

    Returns:
        ``True`` if the number of missing days exceeds ``_GAP_TOLERANCE``.
    """
    have_set = set(pd.DatetimeIndex(have_dates).normalize())
    missing = sum(1 for d in expected if pd.Timestamp(d).normalize() not in have_set)
    return missing > _GAP_TOLERANCE


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------

def _profile_to_frame(profile: MarketDataProfile) -> pd.DataFrame:
    """Convert ``profile.data["series"]`` into a DataFrame with a ``date`` column.

    The series is a list of dicts with at minimum ``{"date": "YYYY-MM-DD", "spot": float, ...}``.

    Returns:
        DataFrame with columns including ``date`` (pd.Timestamp) and ``spot``.
        Empty DataFrame if the profile has no series.
    """
    series: list[dict[str, Any]] = (profile.data or {}).get("series", [])
    if not series:
        return pd.DataFrame(columns=["date", "spot"])
    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _persist_profile(
    session: Session,
    profile: MarketDataProfile | None,
    symbol: str,
    asset_class: str,
    merged_df: pd.DataFrame,
    adjust: str,
) -> MarketDataProfile:
    """Create or update a ``MarketDataProfile`` for the merged spot history.

    Idempotent: re-persisting the same window will not duplicate rows — the
    series is keyed by date and de-duplicated before writing.

    Args:
        session:    Active SQLAlchemy session.
        profile:    Existing profile row to update, or ``None`` to create one.
        symbol:     Akshare symbol (e.g. "000300").
        asset_class: "stock", "index", "etf", etc.
        merged_df:  DataFrame with columns ``[date, spot]`` (date as Timestamp).
        adjust:     Adjustment type ("qfq", "hfq", "none", ...).

    Returns:
        The saved (and flushed) ``MarketDataProfile`` row.
    """
    # De-duplicate by date, keep last (merged_df may already be sorted).
    df = merged_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")

    series: list[dict[str, Any]] = [
        {"date": row["date"].strftime("%Y-%m-%d"), "spot": float(row["spot"])}
        for _, row in df.iterrows()
        if pd.notna(row["spot"])
    ]

    start_date = df["date"].min().strftime("%Y-%m-%d")
    end_date = df["date"].max().strftime("%Y-%m-%d")

    if profile is None:
        profile = MarketDataProfile(
            name=f"{symbol} backtest history ({asset_class})",
            source="akshare",
            symbol=symbol,
            asset_class=asset_class,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            valuation_date=datetime.utcnow(),
            data={"series": series},
            source_metadata={"backtest_history": True, "adjust": adjust},
        )
        session.add(profile)
    else:
        profile.start_date = start_date
        profile.end_date = end_date
        profile.valuation_date = datetime.utcnow()
        profile.data = {"series": series}
        profile.source_metadata = {"backtest_history": True, "adjust": adjust}

    session.flush()
    return profile


def _persist_futures_profile(
    session: Session,
    profile: MarketDataProfile | None,
    prefix: str,
    chain_df: pd.DataFrame,
) -> MarketDataProfile:
    df = chain_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    df = df.sort_values(["date", "expiry_date", "contract"]).drop_duplicates(
        subset=["date", "contract"], keep="last"
    )
    series = [
        {
            "date": row["date"].strftime("%Y-%m-%d"),
            "contract": str(row["contract"]),
            "futures_price": float(row["futures_price"]),
            "expiry_date": row["expiry_date"].strftime("%Y-%m-%d"),
            "multiplier": float(row["multiplier"]),
        }
        for _, row in df.iterrows()
        if pd.notna(row["futures_price"])
    ]
    start_date = df["date"].min().strftime("%Y-%m-%d")
    end_date = df["date"].max().strftime("%Y-%m-%d")
    if profile is None:
        profile = MarketDataProfile(
            name=f"{prefix} futures chain backtest history",
            source="akshare",
            symbol=prefix,
            asset_class="futures",
            start_date=start_date,
            end_date=end_date,
            adjust="none",
            valuation_date=datetime.utcnow(),
            data={"series": series},
            source_metadata={"backtest_history": True, "futures_chain": True},
        )
        session.add(profile)
    else:
        profile.start_date = start_date
        profile.end_date = end_date
        profile.valuation_date = datetime.utcnow()
        profile.data = {"series": series}
        profile.source_metadata = {"backtest_history": True, "futures_chain": True}
    session.flush()
    return profile


# ---------------------------------------------------------------------------
# akshare fetch
# ---------------------------------------------------------------------------

def _fetch_akshare_spot(
    symbol: str,
    asset_class: str,
    start: str,
    end: str,
    adjust: str,
) -> pd.DataFrame:
    """Fetch a daily spot history from akshare for the given symbol.

    Supports "stock", "index", "etf", "sge_spot" asset classes with the same
    routing logic as the existing snapshot service.

    Args:
        symbol:     Numeric code (e.g. "000300", "510300").
        asset_class: "stock", "index", "etf", "sge_spot".
        start:      ISO date "YYYY-MM-DD".
        end:        ISO date "YYYY-MM-DD".
        adjust:     "qfq", "hfq", or "" / "none".

    Returns:
        DataFrame with columns ``[date, spot]`` where date is pd.Timestamp.

    Raises:
        RuntimeError: On any akshare failure (callers convert to a FAILED run).
    """
    try:
        import akshare as ak  # type: ignore[import]  # lazy — matches repo pattern
    except ImportError as exc:
        raise RuntimeError(f"akshare is not installed: {exc}") from exc

    # Canonicalize symbol (strip exchange suffix like "000300.SH")
    code = symbol.strip()
    if "." in code:
        code = code.split(".", 1)[0]

    adjust_arg = adjust if adjust not in {"none", "None", ""} else ""
    start_compact = start.replace("-", "")
    end_compact = end.replace("-", "")

    try:
        raw: pd.DataFrame | None = None

        if asset_class == "etf" or code.startswith(("15", "16", "18", "51", "56", "58")):
            try:
                raw = ak.fund_etf_hist_sina(symbol=_prefix_etf(code))
                if raw is None or raw.empty:
                    raise ValueError("empty")
                raw = raw.rename(columns={"date": "date", "close": "close"})
                raw["date"] = pd.to_datetime(raw["date"])
                raw = raw.loc[(raw["date"] >= start) & (raw["date"] <= end)]
            except Exception:
                raw = ak.fund_etf_hist_em(
                    symbol=code,
                    period="daily",
                    start_date=start_compact,
                    end_date=end_compact,
                    adjust=adjust_arg or "qfq",
                )

        elif asset_class == "stock":
            try:
                raw = ak.stock_zh_a_daily(
                    symbol=_prefix_stock(code), adjust=adjust_arg or "qfq"
                )
                if raw is None or raw.empty:
                    raise ValueError("empty")
                raw["date"] = pd.to_datetime(raw["date"])
                raw = raw.loc[(raw["date"] >= start) & (raw["date"] <= end)]
            except Exception:
                raw = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_compact,
                    end_date=end_compact,
                    adjust=adjust_arg or "qfq",
                )

        elif asset_class == "sge_spot":
            sge_map = {
                "AU9999": "Au99.99",
                "AU9995": "Au99.95",
                "AU100G": "Au100g",
                "AG9999": "Ag99.99",
            }
            sge_sym = sge_map.get(code.upper(), code)
            raw = ak.spot_hist_sge(symbol=sge_sym)
            if raw is None or raw.empty:
                raise RuntimeError(
                    f"akshare spot_hist_sge returned no data for {symbol!r}"
                )
            raw["date"] = pd.to_datetime(raw["date"])
            raw = raw.loc[(raw["date"] >= start) & (raw["date"] <= end)]

        else:
            # Default: treat as index
            try:
                raw = ak.stock_zh_index_daily(symbol=_prefix_index(code))
                if raw is None or raw.empty:
                    raise ValueError("empty")
                raw["date"] = pd.to_datetime(raw["date"])
                raw = raw.loc[(raw["date"] >= start) & (raw["date"] <= end)]
            except Exception:
                raw = ak.index_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_compact,
                    end_date=end_compact,
                )

    except Exception as exc:
        raise RuntimeError(
            f"akshare fetch failed for {symbol!r} ({asset_class}) "
            f"[{start} → {end}]: {exc}"
        ) from exc

    if raw is None or raw.empty:
        raise RuntimeError(
            f"akshare returned no data for {symbol!r} ({asset_class}) [{start} → {end}]"
        )

    # Normalize to [date, spot]
    raw = raw.copy()
    raw = _rename_chinese_columns(raw)
    if "date" not in raw.columns:
        raw = raw.rename(columns={raw.columns[0]: "date"})
    raw["date"] = pd.to_datetime(raw["date"])

    close_col = "close" if "close" in raw.columns else (
        "收盘" if "收盘" in raw.columns else None
    )
    if close_col is None:
        # Last resort: pick last numeric column
        numeric_cols = raw.select_dtypes(include="number").columns.tolist()
        if not numeric_cols:
            raise RuntimeError(
                f"Cannot identify close price column in akshare response for {symbol!r}. "
                f"Columns: {list(raw.columns)}"
            )
        close_col = numeric_cols[-1]

    result = raw[["date", close_col]].rename(columns={close_col: "spot"}).copy()
    result["spot"] = pd.to_numeric(result["spot"], errors="coerce")
    result = result.dropna(subset=["spot"])
    result = result.sort_values("date").reset_index(drop=True)
    return result


def _rename_chinese_columns(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "日期": "date",
        "收盘": "close",
        "收盘价": "close",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
    }
    return df.rename(columns={k: v for k, v in aliases.items() if k in df.columns})


def _prefix_stock(code: str) -> str:
    if code.startswith(("sz", "sh")):
        return code
    return ("sz" if code.startswith(("0", "2", "3")) else "sh") + code


def _prefix_index(code: str) -> str:
    if code.startswith(("sz", "sh")):
        return code
    return ("sh" if code.startswith(("0", "5")) else "sz") + code


def _prefix_etf(code: str) -> str:
    if code.startswith(("sz", "sh")):
        return code
    return ("sz" if code.startswith(("15", "16", "18")) else "sh") + code


def _fetch_akshare_futures_contract(
    symbol: str,
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    try:
        import akshare as ak  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(f"akshare is not installed: {exc}") from exc

    provider_symbol = symbol.split(".", 1)[0]
    try:
        raw = ak.futures_zh_daily_sina(symbol=provider_symbol)
    except Exception as exc:
        raise RuntimeError(f"akshare futures fetch failed for {symbol!r}: {exc}") from exc
    if raw is None or raw.empty:
        raise RuntimeError(f"akshare futures_zh_daily_sina returned no data for {symbol!r}")
    raw = _rename_chinese_columns(raw.copy())
    if "date" not in raw.columns:
        raw = raw.rename(columns={raw.columns[0]: "date"})
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.loc[(raw["date"] >= start) & (raw["date"] <= end)]
    close_col = "close" if "close" in raw.columns else None
    if close_col is None:
        numeric_cols = raw.select_dtypes(include="number").columns.tolist()
        if not numeric_cols:
            raise RuntimeError(f"Cannot identify futures close column for {symbol!r}")
        close_col = numeric_cols[-1]
    out = raw[["date", close_col]].rename(columns={close_col: "futures_price"}).copy()
    out["futures_price"] = pd.to_numeric(out["futures_price"], errors="coerce")
    return out.dropna(subset=["futures_price"]).sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def ensure_spot_history(
    session: Session,
    *,
    symbol: str,
    asset_class: str,
    start: str,
    end: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Return a windowed ``[date, spot]`` DataFrame for the given symbol.

    Prefers the stored ``MarketDataProfile`` daily history; if the daily
    series has gaps vs the SSE trading calendar, fetches from akshare, merges
    (akshare wins on overlap), and persists before returning.

    Args:
        session:     Active SQLAlchemy session.
        symbol:      Akshare symbol, e.g. "000300".
        asset_class: "stock", "index", "etf", "sge_spot".
        start:       Window start, ISO date "YYYY-MM-DD".
        end:         Window end, ISO date "YYYY-MM-DD".
        adjust:      Price adjustment type ("qfq", "hfq", "none").

    Returns:
        DataFrame with columns ``[date, spot]`` covering ``[start, end]``,
        sorted ascending by date.

    Raises:
        RuntimeError: If akshare fetch fails (propagated from :func:`_fetch_akshare_spot`).
    """
    expected = expected_trading_days(start, end)

    # Find the latest matching profile
    profile: MarketDataProfile | None = (
        session.query(MarketDataProfile)
        .filter(
            MarketDataProfile.symbol == symbol,
            MarketDataProfile.asset_class == asset_class,
            MarketDataProfile.adjust == adjust,
        )
        .order_by(MarketDataProfile.id.desc())
        .first()
    )

    stored_df: pd.DataFrame = pd.DataFrame(columns=["date", "spot"])
    if profile is not None:
        stored_df = _profile_to_frame(profile)

    stored_in_window: pd.DataFrame = pd.DataFrame(columns=["date", "spot"])
    if not stored_df.empty and "date" in stored_df.columns:
        mask = (stored_df["date"] >= start) & (stored_df["date"] <= end)
        stored_in_window = stored_df.loc[mask]

    have_dates = pd.DatetimeIndex(
        stored_in_window["date"] if not stored_in_window.empty else []
    )

    if profile is None or _has_gaps(have_dates, expected):
        # Fetch full window from akshare
        fresh_df = _fetch_akshare_spot(symbol, asset_class, start, end, adjust)

        # Merge: akshare wins on overlap; keep any stored rows outside this window
        if not stored_df.empty:
            outside = stored_df.loc[
                (stored_df["date"] < start) | (stored_df["date"] > end)
            ]
            merged = pd.concat([outside, fresh_df], ignore_index=True)
        else:
            merged = fresh_df.copy()

        merged["date"] = pd.to_datetime(merged["date"])
        merged = merged.sort_values("date").drop_duplicates(subset=["date"], keep="last")

        profile = _persist_profile(session, profile, symbol, asset_class, merged, adjust)
        session.commit()

        result_df = fresh_df.copy()
    else:
        result_df = stored_in_window.copy()

    result_df["date"] = pd.to_datetime(result_df["date"])
    result_df = result_df.sort_values("date").reset_index(drop=True)
    return result_df[["date", "spot"]]


# ---------------------------------------------------------------------------
# Volatility / rate surface helpers
# ---------------------------------------------------------------------------

def derive_vol(
    spot_df: pd.DataFrame,
    *,
    vol_source: str,
    vol_window: int,
    flat_vol: float,
) -> pd.DataFrame:
    """Derive a daily volatility series from the spot path.

    Args:
        spot_df:    DataFrame with columns ``[date, spot]``.
        vol_source: ``"flat"`` → constant ``flat_vol``; any other value (e.g.
                    ``"realized"``) → rolling annualized realized vol.
        vol_window: Look-back window in trading days for realized vol.
        flat_vol:   Constant vol (fraction, e.g. 0.2 for 20%); used for the
                    ``"flat"`` source and as fallback when realized vol is NaN.

    Returns:
        DataFrame with columns ``[date, volatility]``.
    """
    df = spot_df[["date", "spot"]].copy().sort_values("date").reset_index(drop=True)

    if vol_source == "flat":
        df["volatility"] = flat_vol
    else:
        # Annualized rolling std of log-returns (252 trading days/yr)
        log_ret = np.log(df["spot"] / df["spot"].shift(1))
        realized = log_ret.rolling(window=vol_window, min_periods=2).std() * np.sqrt(252)
        realized = realized.bfill().fillna(flat_vol)
        df["volatility"] = realized

    return df[["date", "volatility"]]


def flat_rate(spot_df: pd.DataFrame, rate: float) -> pd.DataFrame:
    """Return a flat daily rate series aligned to the spot path.

    Args:
        spot_df: DataFrame with at least a ``date`` column.
        rate:    Constant risk-free rate (fraction, e.g. 0.02 for 2%).

    Returns:
        DataFrame with columns ``[date, rate]``.
    """
    df = spot_df[["date"]].copy().sort_values("date").reset_index(drop=True)
    df["rate"] = rate
    return df[["date", "rate"]]


# ---------------------------------------------------------------------------
# Futures chain (read-through; full akshare fetch deferred to Task 2.5)
# ---------------------------------------------------------------------------

def _futures_profile_to_frame(profile: MarketDataProfile) -> pd.DataFrame:
    series = (profile.data or {}).get("series", [])
    if not series:
        return pd.DataFrame(columns=["date", "contract", "futures_price", "expiry_date", "multiplier"])
    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    df["futures_price"] = pd.to_numeric(df["futures_price"], errors="coerce")
    df["multiplier"] = pd.to_numeric(df.get("multiplier", 300), errors="coerce")
    return df


def _window_futures_chain(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if df.empty:
        return df.reindex(columns=["date", "contract", "futures_price", "expiry_date", "multiplier"])
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    mask = (df["date"] >= start) & (df["date"] <= end)
    return df.loc[mask, ["date", "contract", "futures_price", "expiry_date", "multiplier"]].reset_index(drop=True)


def _active_hedge_contracts(session: Session, *, prefix: str, start: str, end: str) -> list[Instrument]:
    rows = (
        session.query(Instrument)
        .join(HedgeMapEntry, HedgeMapEntry.instrument_id == Instrument.id)
        .filter(
            HedgeMapEntry.series_root == prefix,
            HedgeMapEntry.reconcile_status == "active",
            Instrument.kind == "futures",
        )
        .order_by(Instrument.expiry.asc(), Instrument.id.asc())
        .all()
    )
    start_ts = pd.Timestamp(start).date()
    return [row for row in rows if row.expiry is None or row.expiry >= start_ts]


def _chain_from_quotes(
    session: Session,
    contracts: list[Instrument],
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    start_dt = pd.Timestamp(start).to_pydatetime()
    end_dt = pd.Timestamp(end).to_pydatetime() + timedelta(days=1)
    for contract in contracts:
        quotes = (
            session.query(MarketQuote)
            .filter(
                MarketQuote.instrument_id == contract.id,
                MarketQuote.as_of >= start_dt,
                MarketQuote.as_of < end_dt,
            )
            .order_by(MarketQuote.as_of.asc(), MarketQuote.id.asc())
            .all()
        )
        for quote in quotes:
            rows.append(
                {
                    "date": pd.Timestamp(quote.as_of).normalize(),
                    "contract": contract.contract_code or contract.symbol.split(".", 1)[0],
                    "futures_price": float(quote.price),
                    "expiry_date": pd.Timestamp(contract.expiry or end).normalize(),
                    "multiplier": float(contract.multiplier or 1.0),
                }
            )
    return pd.DataFrame(rows, columns=["date", "contract", "futures_price", "expiry_date", "multiplier"])


def _fetch_allowed_hedge_chain(
    session: Session,
    contracts: list[Instrument],
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for contract in contracts:
        fetched = _fetch_akshare_futures_contract(
            contract.akshare_symbol or contract.contract_code or contract.symbol,
            start=start,
            end=end,
        )
        for _, row in fetched.iterrows():
            record_quote(
                session,
                instrument_id=contract.id,
                price=float(row["futures_price"]),
                as_of=pd.Timestamp(row["date"]).to_pydatetime(),
                source="akshare_backtest",
                meta={"contract": contract.contract_code, "series_root": contract.series_root},
            )
        fetched["contract"] = contract.contract_code or contract.symbol.split(".", 1)[0]
        fetched["expiry_date"] = pd.Timestamp(contract.expiry or end).normalize()
        fetched["multiplier"] = float(contract.multiplier or 1.0)
        rows.append(fetched[["date", "contract", "futures_price", "expiry_date", "multiplier"]])
    if not rows:
        return pd.DataFrame(columns=["date", "contract", "futures_price", "expiry_date", "multiplier"])
    return pd.concat(rows, ignore_index=True)


def ensure_futures_chain(
    session: Session,
    *,
    prefix: str,
    start: str,
    end: str,
    refill: bool = True,
) -> pd.DataFrame:
    """Return a futures chain DataFrame with schema columns:
    ``[date, contract, futures_price, expiry_date, multiplier]``.

    Read-through path: if a ``MarketDataProfile(asset_class="futures",
    symbol=prefix)`` already exists in the DB, deserialize and return it.

    Fetch path: raises ``NotImplementedError`` — wire in Task 2.5 smoke
    to implement the akshare futures-chain fetch.

    Args:
        session: Active SQLAlchemy session.
        prefix:  Contract prefix, e.g. "IF" (CSI 300 index futures).
        start:   Window start ISO date.
        end:     Window end ISO date.

    Returns:
        DataFrame with columns ``[date, contract, futures_price, expiry_date, multiplier]``.

    Raises:
        NotImplementedError: When no stored profile exists and a live fetch
            would be required.
        RuntimeError: If the stored profile's series cannot be parsed into
            the expected schema.
    """
    profile: MarketDataProfile | None = (
        session.query(MarketDataProfile)
        .filter(
            MarketDataProfile.symbol == prefix,
            MarketDataProfile.asset_class == "futures",
        )
        .order_by(MarketDataProfile.id.desc())
        .first()
    )

    now = pd.Timestamp.now("UTC").tz_localize(None)
    today = now.normalize() - pd.Timedelta(days=1 if now.hour < 18 else 0)
    effective_end = min(pd.Timestamp(end), today).strftime("%Y-%m-%d")
    expected = expected_trading_days(start, effective_end)
    stored_df = _futures_profile_to_frame(profile) if profile is not None else pd.DataFrame()
    window = _window_futures_chain(stored_df, start, end) if not stored_df.empty else stored_df
    if profile is not None and not window.empty and not refill:
        return window
    if profile is not None and not _has_gaps(pd.DatetimeIndex(window["date"] if not window.empty else []), expected):
        return window

    contracts = _active_hedge_contracts(session, prefix=prefix, start=start, end=effective_end)
    quote_df = _chain_from_quotes(session, contracts, start=start, end=end)
    if not quote_df.empty and not _has_gaps(pd.DatetimeIndex(quote_df["date"]), expected):
        merged = quote_df if stored_df.empty else pd.concat([stored_df, quote_df], ignore_index=True)
        _persist_futures_profile(session, profile, prefix, merged)
        session.commit()
        return _window_futures_chain(merged, start, end)

    if refill and contracts:
        fetched_df = _fetch_allowed_hedge_chain(session, contracts, start=start, end=end)
        if not fetched_df.empty:
            merged = fetched_df if stored_df.empty else pd.concat([stored_df, fetched_df], ignore_index=True)
            _persist_futures_profile(session, profile, prefix, merged)
            session.commit()
            window = _window_futures_chain(merged, start, end)
            if not _has_gaps(pd.DatetimeIndex(window["date"] if not window.empty else []), expected):
                return window

    raise RuntimeError(
        f"No usable futures chain for prefix={prefix!r}; "
        f"allowed_contracts={len(contracts)}, stored_rows={len(window) if not window.empty else 0}."
    )

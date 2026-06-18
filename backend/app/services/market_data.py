from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Any, cast

import pandas as pd

from ..schemas import AkshareSnapshotRequest, MarketDataSnapshot
from .fx import fetch_akshare_fx_rate, parse_fx_pair_symbol

_proxy_lock = threading.Lock()
_CHILD_RESULT_MARKER = "__OPEN_OTC_AKSHARE_SNAPSHOT__:"
_AKSHARE_SUBPROCESS_TIMEOUT_SECONDS = 45


@contextlib.contextmanager
def _no_proxy():
    """Force AKShare requests to bypass the system proxy.

    Clearing env vars alone is unreliable because requests sessions may already
    have pooled connections configured for a proxy. Patching
    requests.utils.get_environ_proxies to return {} is the only way to guarantee
    that every new connection opened inside this block ignores the proxy.
    """
    try:
        import requests.utils as _ru
    except ImportError:
        yield  # requests not available — nothing to patch
        return

    with _proxy_lock:
        _orig = _ru.get_environ_proxies
        _ru.get_environ_proxies = lambda *_: {}
        try:
            yield
        finally:
            _ru.get_environ_proxies = _orig


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


def _is_etf_code(code: str) -> bool:
    return code.startswith(("15", "16", "18", "51", "56", "58"))


def _is_sge_symbol(symbol: str) -> bool:
    code, _, suffix = symbol.strip().upper().partition(".")
    return suffix == "SGE" or code in {"AU9999", "AU9995", "AU100G", "AG9999"}


def _is_csindex_symbol(symbol: str, provider_symbol: str) -> bool:
    code = provider_symbol.strip().upper()
    _, _, suffix = symbol.strip().upper().partition(".")
    return suffix == "CSI" or code.startswith(("9", "H"))


def _fx_pair_symbol(symbol: str) -> tuple[str, str]:
    pair = parse_fx_pair_symbol(symbol)
    if pair is None:
        raise ValueError(f"FX rate symbol must be a currency pair like USD/CNY: {symbol!r}")
    return pair


def _akshare_code(symbol: str) -> str:
    code = symbol.strip()
    if "." in code:
        return code.split(".", 1)[0]
    return code


def _sge_symbol(symbol: str) -> str:
    code = _akshare_code(symbol).upper()
    mapping = {
        "AU9999": "Au99.99",
        "AU9995": "Au99.95",
        "AU100G": "Au100g",
        "AG9999": "Ag99.99",
    }
    return mapping.get(code, code)


def _normalize_ohlc(raw: pd.DataFrame) -> list[dict[str, Any]]:
    if raw.empty:
        return []
    df = raw.copy()
    aliases = {
        "日期": "date",
        "收盘": "close",
        "收盘价": "close",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
    }
    df = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns})
    if "date" not in df.columns:
        first = df.columns[0]
        df = df.rename(columns={first: "date"})
    keep = [col for col in ["date", "open", "high", "low", "close", "volume"] if col in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for col in [c for c in keep if c != "date"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date"]).tail(400)
    df = df.astype(object).where(pd.notnull(df), None)
    return cast(list[dict[str, Any]], df.to_dict(orient="records"))


def check_akshare_sources() -> dict[str, bool]:
    try:
        import akshare as ak  # type: ignore
    except Exception:
        return {"akshare_installed": False}
    results = {"akshare_installed": True}
    probes = {
        "eastmoney_stock": lambda: ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20250101", end_date="20250110", adjust="qfq"),
        "sina_stock": lambda: ak.stock_zh_a_daily(symbol="sz000001", adjust="qfq"),
        "eastmoney_index": lambda: ak.index_zh_a_hist(symbol="000300", period="daily", start_date="20250101", end_date="20250110"),
        "sina_index": lambda: ak.stock_zh_index_daily(symbol="sh000300"),
    }
    for name, call in probes.items():
        try:
            results[name] = len(call()) > 0
        except Exception:
            results[name] = False
    return results


def fetch_akshare_snapshot(request: AkshareSnapshotRequest) -> MarketDataSnapshot:
    if os.environ.get("OPEN_OTC_AKSHARE_CHILD") == "1":
        return _fetch_akshare_snapshot_in_process(request)
    if _using_test_akshare_module():
        return _fetch_akshare_snapshot_in_process(request)
    return _fetch_akshare_snapshot_subprocess(request)


def _using_test_akshare_module() -> bool:
    module = sys.modules.get("akshare")
    return module is not None and getattr(module, "__file__", None) is None


def _fetch_akshare_snapshot_subprocess(
    request: AkshareSnapshotRequest,
) -> MarketDataSnapshot:
    backend_root = Path(__file__).resolve().parents[2]
    repo_root = backend_root.parent
    code = "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(backend_root)!r})",
            "from app.services.market_data import _akshare_child_main",
            "raise SystemExit(_akshare_child_main())",
        ]
    )
    env = os.environ.copy()
    env["OPEN_OTC_AKSHARE_CHILD"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(request.model_dump(mode="json")),
            text=True,
            capture_output=True,
            timeout=_AKSHARE_SUBPROCESS_TIMEOUT_SECONDS,
            cwd=repo_root,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _fallback_snapshot(
            request,
            f"AKShare subprocess timed out after {exc.timeout} seconds",
        )

    if completed.returncode != 0:
        detail = _child_failure_detail(completed.stdout, completed.stderr)
        return _fallback_snapshot(
            request,
            f"AKShare subprocess failed with exit code {completed.returncode}: {detail}",
        )

    for line in reversed(completed.stdout.splitlines()):
        if not line.startswith(_CHILD_RESULT_MARKER):
            continue
        try:
            payload = json.loads(line[len(_CHILD_RESULT_MARKER) :])
            return MarketDataSnapshot.model_validate(payload)
        except Exception as exc:
            return _fallback_snapshot(
                request,
                f"AKShare subprocess returned invalid snapshot JSON: {exc}",
            )
    return _fallback_snapshot(
        request,
        "AKShare subprocess did not return a snapshot payload",
    )


def _child_failure_detail(stdout: str, stderr: str) -> str:
    detail = "\n".join(part.strip() for part in (stderr, stdout) if part.strip())
    if not detail:
        return "no subprocess output"
    return detail[:1000]


def _akshare_child_main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        request = AkshareSnapshotRequest.model_validate(payload)
        snapshot = _fetch_akshare_snapshot_in_process(request)
        print(
            _CHILD_RESULT_MARKER
            + json.dumps(snapshot.model_dump(mode="json"), default=str),
            flush=True,
        )
        return 0
    except Exception as exc:
        print(f"AKShare child failed: {exc}", file=sys.stderr)
        return 1


def _fetch_akshare_snapshot_in_process(
    request: AkshareSnapshotRequest,
) -> MarketDataSnapshot:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        return _fallback_snapshot(request, f"AKShare unavailable: {exc}")

    source = "AKShare"
    provider_symbol = _akshare_code(request.symbol)
    proxy_ctx = _no_proxy() if not request.use_proxy else contextlib.nullcontext()
    with proxy_ctx:
        try:
            if request.asset_class == "fx_rate":
                base, quote = _fx_pair_symbol(request.symbol)
                pair_symbol = f"{base}/{quote}"
                rate = fetch_akshare_fx_rate(base, quote)
                as_of = datetime.now(timezone.utc)
                row = {
                    "date": as_of.date().isoformat(),
                    "open": rate,
                    "high": rate,
                    "low": rate,
                    "close": rate,
                    "volume": None,
                }
                return MarketDataSnapshot(
                    name=request.name or f"{pair_symbol} AKShare spot",
                    source="akshare",
                    symbol=pair_symbol,
                    asset_class="fx_rate",
                    valuation_date=as_of,
                    data={"rows": [row], "latest": row, "spot": rate},
                    source_metadata={
                        "source_name": "AKShare fx_spot_quote",
                        "fallback": False,
                        "base_currency": base,
                        "quote_currency": quote,
                    },
                )
            if request.asset_class == "etf" or _is_etf_code(provider_symbol):
                try:
                    raw = ak.fund_etf_hist_sina(symbol=_prefix_etf(provider_symbol))
                    if raw is None or raw.empty:
                        raise ValueError(f"fund_etf_hist_sina returned no data for {request.symbol!r}")
                    raw["date"] = pd.to_datetime(raw["date"])
                    mask = (raw["date"] >= request.start_date) & (raw["date"] <= request.end_date)
                    rows = _normalize_ohlc(raw.loc[mask])
                    source = "AKShare fund_etf_hist_sina"
                except Exception:
                    raw = ak.fund_etf_hist_em(
                        symbol=provider_symbol,
                        period="daily",
                        start_date=request.start_date.replace("-", ""),
                        end_date=request.end_date.replace("-", ""),
                        adjust=request.adjust,
                    )
                    rows = _normalize_ohlc(raw)
                    source = "AKShare fund_etf_hist_em"
            elif request.asset_class == "sge_spot" or _is_sge_symbol(request.symbol):
                raw = ak.spot_hist_sge(symbol=_sge_symbol(request.symbol))
                if raw is None or raw.empty:
                    return _fallback_snapshot(request, f"spot_hist_sge returned no data for {request.symbol!r}")
                raw["date"] = pd.to_datetime(raw["date"])
                mask = (raw["date"] >= request.start_date) & (raw["date"] <= request.end_date)
                rows = _normalize_ohlc(raw.loc[mask])
                source = "AKShare spot_hist_sge"
            elif request.asset_class == "stock":
                try:
                    raw = ak.stock_zh_a_daily(symbol=_prefix_stock(provider_symbol), adjust=request.adjust)
                    if raw is None or raw.empty:
                        raise ValueError(f"stock_zh_a_daily returned no data for {request.symbol!r}")
                    raw["date"] = pd.to_datetime(raw["date"])
                    mask = (raw["date"] >= request.start_date) & (raw["date"] <= request.end_date)
                    rows = _normalize_ohlc(raw.loc[mask])
                    source = "AKShare stock_zh_a_daily"
                except Exception:
                    raw = ak.stock_zh_a_hist(
                        symbol=provider_symbol,
                        period="daily",
                        start_date=request.start_date.replace("-", ""),
                        end_date=request.end_date.replace("-", ""),
                        adjust=request.adjust,
                    )
                    rows = _normalize_ohlc(raw)
                    source = "AKShare stock_zh_a_hist"
            elif request.asset_class == "futures":
                raw = ak.futures_zh_daily_sina(symbol=provider_symbol)
                if raw is None or raw.empty:
                    return _fallback_snapshot(request, f"futures_zh_daily_sina returned no data for {request.symbol!r} — symbol may be expired or invalid")
                raw["date"] = pd.to_datetime(raw["date"])
                mask = (raw["date"] >= request.start_date) & (raw["date"] <= request.end_date)
                rows = _normalize_ohlc(raw.loc[mask])
                source = "AKShare futures_zh_daily_sina"
            elif _is_csindex_symbol(request.symbol, provider_symbol):
                raw = ak.stock_zh_index_hist_csindex(
                    symbol=provider_symbol,
                    start_date=request.start_date.replace("-", ""),
                    end_date=request.end_date.replace("-", ""),
                )
                rows = _normalize_ohlc(raw)
                source = "AKShare stock_zh_index_hist_csindex"
            else:
                try:
                    raw = ak.stock_zh_index_daily(symbol=_prefix_index(provider_symbol))
                    if raw is None or raw.empty:
                        raise ValueError(f"stock_zh_index_daily returned no data for {request.symbol!r}")
                    raw["date"] = pd.to_datetime(raw["date"])
                    mask = (raw["date"] >= request.start_date) & (raw["date"] <= request.end_date)
                    rows = _normalize_ohlc(raw.loc[mask])
                    source = "AKShare stock_zh_index_daily"
                except Exception:
                    raw = ak.index_zh_a_hist(
                        symbol=provider_symbol,
                        period="daily",
                        start_date=request.start_date.replace("-", ""),
                        end_date=request.end_date.replace("-", ""),
                    )
                    rows = _normalize_ohlc(raw)
                    source = "AKShare index_zh_a_hist"
            if not rows:
                return _fallback_snapshot(request, "AKShare returned no rows for the requested window")
            latest = rows[-1]
            return MarketDataSnapshot(
                name=request.name or f"{request.symbol} AKShare snapshot",
                source="akshare",
                symbol=request.symbol,
                asset_class=request.asset_class,
                valuation_date=datetime.now(timezone.utc),
                data={"rows": rows, "latest": latest, "spot": latest.get("close")},
                source_metadata={"source_name": source, "fallback": False},
            )
        except Exception as exc:
            return _fallback_snapshot(request, str(exc))


def _fallback_snapshot(request: AkshareSnapshotRequest, reason: str) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        name=request.name or f"{request.symbol} fallback snapshot",
        source="akshare",
        symbol=request.symbol,
        asset_class=request.asset_class,
        valuation_date=datetime.now(timezone.utc),
        data={"rows": [], "latest": None, "spot": None},
        source_metadata={"fallback": True, "reason": reason},
    )

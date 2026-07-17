"""Backtest pipeline: history -> bridge -> Book engines -> aggregate -> shape -> artifacts.

Mirrors ``domains/scenario_test.py``: ``run_pipeline`` returns
``(status, results_dict, excluded, raw)`` and ``write_artifacts`` never raises.

The flow per run:
  1. group positions by underlying;
  2. for each underlying, backfill spot/vol/rate (Task 2.3) and resolve a hedge
     (futures chain read-through, else fall back to spot);
  3. build per-underlying Book configs (Task 2.4 bridge);
  4. run ``BookAutocallableBacktestEngine`` per config;
  5. shape each result, aggregate a portfolio P&L path + risk metrics;
  6. (artifacts) render the quant-ark dashboard HTML via an adapter.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services import (
    backtest_bridge,
    backtest_market_history as mh,
    hedging_universe,
    quantark,
)
from app.services.underlyings import akshare_asset_class, akshare_symbol
from app.services.source_evidence import (
    backtest_position_evidence,
    canonical_hash,
    datetime_iso,
)


@dataclass(frozen=True, slots=True)
class PreparedBacktestPipeline:
    """Frozen output of the bounded DB/cache preparation phase."""

    history: dict[str, tuple]
    configs: tuple[Any, ...]
    excluded: tuple[dict[str, Any], ...]
    notes: tuple[str, ...]
    evidence_manifest: dict[str, Any]


# ---------------------------------------------------------------------------
# JSON coercion (copied verbatim from domains/scenario_test.py)
# ---------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """Recursively coerce numpy scalars / non-JSON-native values to plain Python
    so results survive json.dumps (SQLAlchemy JSON column + FastAPI)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    # numpy scalars and other number-likes expose .item(); fall back to float/str
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    if isinstance(value, float):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Risk metrics
# ---------------------------------------------------------------------------

def _risk_metrics(pnl_series: list[float]) -> dict:
    """Sharpe / max drawdown / VaR95 / CVaR95 from a *cumulative* P&L list.

    CONTRIBUTION POINT (c): the conventions here are deliberate-but-simple and
    are the user's to refine:
      * Sharpe annualization uses sqrt(252) on daily P&L *differences* with a
        zero risk-free baseline (P&L is already an excess-of-funding number in
        the book engine, so no rf subtraction here).
      * Max drawdown is in absolute P&L units (peak-to-trough of the cumulative
        curve), NOT a percentage of capital — there is no committed-capital
        denominator at this layer.
      * VaR/CVaR are *historical* (empirical 5th percentile of daily P&L
        changes), not parametric. Swap in a parametric or scaled-horizon
        measure here if desired.
    """
    import numpy as np

    arr = np.asarray(pnl_series, dtype=float)
    if arr.size < 2:
        return {"sharpe": 0.0, "max_drawdown": 0.0, "var_95": 0.0, "cvar_95": 0.0}
    daily = np.diff(arr)
    sd = float(np.std(daily))
    sharpe = float(np.mean(daily) / sd * np.sqrt(252.0)) if sd > 0 else 0.0
    running_max = np.maximum.accumulate(arr)
    max_dd = float(np.max(running_max - arr))
    pct5 = np.percentile(daily, 5)
    var_95 = float(-pct5)
    tail = daily[daily <= pct5]
    cvar_95 = float(-tail.mean()) if tail.size else var_95
    return {"sharpe": sharpe, "max_drawdown": max_dd, "var_95": var_95, "cvar_95": cvar_95}


# ---------------------------------------------------------------------------
# Portfolio aggregation
# ---------------------------------------------------------------------------

def _aggregate_portfolio(per_underlying: list[dict]) -> dict:
    """Sum total_pnl / hedge_pnl / num_trades across per-underlying entries.

    Each entry is shaped ``{"underlying": ..., "summary": {...}, ...}`` (the
    shape produced by ``_shape_underlying``); we read the ``summary`` sub-dict so
    the same accessor works whether or not the outer keys are present.
    """
    total_pnl = 0.0
    hedge_pnl = 0.0
    num_trades = 0
    for item in per_underlying:
        summary = item.get("summary", item) or {}
        total_pnl += float(summary.get("total_pnl", 0.0) or 0.0)
        hedge_pnl += float(summary.get("hedge_pnl", 0.0) or 0.0)
        num_trades += int(summary.get("num_trades", 0) or 0)
    return {
        "total_pnl": total_pnl,
        "hedge_pnl": hedge_pnl,
        "num_trades": num_trades,
        "num_underlyings": len(per_underlying),
    }


# ---------------------------------------------------------------------------
# Hedge resolution (built from hedging_universe — there is no single resolver)
# ---------------------------------------------------------------------------

def _resolve_hedge(underlying: str, asset_class: str | None) -> dict | None:
    """Decide the hedge instrument for an underlying.

    Built from ``hedging_universe.resolve_families`` + ``contract_multiplier``
    (there is intentionally no single ``resolve_hedge_instrument``):
      * if an ``index_future`` family is available with a known exchange
        multiplier (IF/IH ¥300, IC/IM ¥200) → futures hedge keyed by its
        ``series_root`` (e.g. "IC");
      * otherwise → spot hedge.

    Commodity futures are NOT chosen here even though ``resolve_families``
    enumerates them: ``contract_multiplier`` returns None for commodity roots
    (the multiplier is product-specific and must come from the feed), so they
    fall through to the spot branch. That is a conservative default and is
    FLAGGED for the smoke step.
    """
    families = hedging_universe.resolve_families(underlying, asset_class)
    for fam in families:
        if fam.family == "index_future":
            mult = hedging_universe.contract_multiplier(fam.family, fam.series_root)
            if mult is not None:
                return {"kind": "futures", "prefix": fam.series_root, "multiplier": float(mult)}
    return {"kind": "spot"}


# ---------------------------------------------------------------------------
# DataFrame helpers
# ---------------------------------------------------------------------------

def _downsample(rows: list[dict], cap: int = 250) -> list[dict]:
    """Thin a row list to <= ~cap entries (every Nth row + always the last)."""
    n = len(rows)
    if n <= cap:
        return rows
    step = (n // cap) + 1
    sampled = rows[::step]
    if sampled[-1] is not rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _date_str(value: Any) -> str:
    """Stringify a date/Timestamp/whatever to an ISO-ish day string."""
    try:
        import pandas as pd

        ts = pd.Timestamp(value)
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def _shape_underlying(cfg: Any, results: Any) -> dict:
    """Project a ``BookBacktestResults`` into a JSON-serializable per-underlying dict.

    Defensive throughout: any missing column/method is omitted, never fatal.
    """
    summary: dict[str, Any] = {}
    try:
        summary = dict(results.get_summary() or {})
    except Exception:
        summary = {}

    # --- P&L series ---------------------------------------------------------
    pnl_series: list[dict] = []
    try:
        states = results.states_df()
        if states is not None and not states.empty and "date" in states.columns:
            recs = states.to_dict("records")
            for r in recs:
                entry: dict[str, Any] = {"date": _date_str(r.get("date"))}
                for col in ("total_pnl", "hedge_pnl", "product_pnl"):
                    if col in r:
                        entry[col] = float(r[col]) if r[col] is not None else 0.0
                pnl_series.append(entry)
            pnl_series = _downsample(pnl_series)
    except Exception:
        pnl_series = []

    # --- Greeks series ------------------------------------------------------
    # The book greeks row names net delta pre/post hedge `pre_hedge_delta` /
    # `post_hedge_delta`; map to net_delta_pre/net_delta_post. gamma/vega only
    # if present (vega is not currently recorded → omitted gracefully).
    greeks_series: list[dict] = []
    try:
        greeks = results.greeks_df()
        if greeks is not None and not greeks.empty and "date" in greeks.columns:
            col_map = {
                "net_delta_pre": "pre_hedge_delta",
                "net_delta_post": "post_hedge_delta",
                "gamma": "gamma",
                "vega": "vega",
            }
            present = {
                out: src for out, src in col_map.items() if src in greeks.columns
            }
            for r in greeks.to_dict("records"):
                entry = {"date": _date_str(r.get("date"))}
                for out, src in present.items():
                    val = r.get(src)
                    if val is not None:
                        entry[out] = float(val)
                greeks_series.append(entry)
            greeks_series = _downsample(greeks_series)
    except Exception:
        greeks_series = []

    # --- Lifecycle events (from actions_df) --------------------------------
    lifecycle_events: list[dict] = []
    try:
        actions = results.actions_df()
        if actions is not None and not actions.empty:
            cols = set(actions.columns)
            for r in actions.to_dict("records"):
                ev: dict[str, Any] = {
                    "type": r.get("action_type"),
                    "date": _date_str(r.get("date")),
                }
                if "cashflow" in cols:
                    ev["cashflow"] = (
                        float(r["cashflow"]) if r.get("cashflow") is not None else None
                    )
                if "position_id" in cols and r.get("position_id") is not None:
                    ev["position_id"] = r.get("position_id")
                lifecycle_events.append(ev)
    except Exception:
        lifecycle_events = []

    # --- Event summary (latest ko/ki/survival row) -------------------------
    event_summary: dict[str, Any] | None = None
    try:
        des = results.daily_event_summary_df()
        if des is not None and not des.empty:
            last = des.to_dict("records")[-1]
            event_summary = {str(k): last[k] for k in last}
            if "date" in event_summary:
                event_summary["date"] = _date_str(event_summary["date"])
    except Exception:
        event_summary = None

    # --- Hedge instrument descriptor ---------------------------------------
    hedge_instrument: dict[str, Any] = {}
    try:
        hedge = getattr(cfg, "hedge", None)
        if hedge is not None:
            hedge_instrument = {"kind": getattr(hedge, "kind", None)}
            mult = getattr(hedge, "multiplier", None)
            if mult is not None:
                hedge_instrument["multiplier"] = float(mult)
    except Exception:
        hedge_instrument = {}

    shaped: dict[str, Any] = {
        "underlying": getattr(cfg, "underlying", None),
        "summary": summary,
        "pnl_series": pnl_series,
        "greeks_series": greeks_series,
        "lifecycle_events": lifecycle_events,
        "event_summary": event_summary,
        "hedge_instrument": hedge_instrument,
        "num_products": len(getattr(cfg, "products", []) or []),
    }
    # Merge the summary keys in at top level too (parity with scenario_test
    # which flattens summary-ish fields); summary stays available as a sub-dict.
    return shaped


# ---------------------------------------------------------------------------
# Spec parsing helpers
# ---------------------------------------------------------------------------

_ENGINE_ALIASES = {
    "quad": "QUADRATURE",
    "quadrature": "QUADRATURE",
    "pde": "PDE",
    "mc": "MONTE_CARLO",
    "monte_carlo": "MONTE_CARLO",
    "montecarlo": "MONTE_CARLO",
    "analytical": "ANALYTICAL",
    "tree": "TREE",
}


def _engine_type(name: str | None):
    """Map a friendly engine name to ``util.enum.engine_enums.EngineType``."""
    quantark.ensure_quantark_path()
    from quantark.util.enum.engine_enums import EngineType

    key = (name or "quad").strip().lower()
    member = _ENGINE_ALIASES.get(key, "QUADRATURE")
    return getattr(EngineType, member)


def _engine_types_for_spec(spec: dict[str, Any]) -> dict[str, Any]:
    autocallable = spec.get("autocallable_engine") or "quad"
    other = spec.get("other_engine") or "analytical"
    fallback = spec.get("fallback_engine") or "pde"
    return {
        "autocallable": _engine_type(autocallable),
        "other": _engine_type(other),
        "fallback": _engine_type(fallback),
    }


def _engine_name(name: str | None) -> str:
    key = (name or "quad").strip().lower()
    return _ENGINE_ALIASES.get(key, "QUADRATURE")


def _dt(value: str | datetime | None) -> datetime | None:
    """Parse an ISO date/datetime string to a ``datetime`` (None passthrough)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        import pandas as pd

        return pd.Timestamp(text).to_pydatetime()
    except Exception:
        # Last-resort plain ISO parse
        return datetime.fromisoformat(text[:10])


def _synthetic_futures_from_spot(spot_df):
    """Build a degenerate single-contract futures chain == spot (multiplier 1).

    Used when no real futures chain is available so the Book engine's
    spot-hedge path still has the ``[date, contract, futures_price,
    expiry_date, multiplier]`` schema it expects.
    """
    import pandas as pd

    df = spot_df[["date", "spot"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    expiry = df["date"].max() if not df.empty else pd.Timestamp.utcnow()
    return pd.DataFrame(
        {
            "date": df["date"],
            "contract": "SPOT",
            "futures_price": df["spot"].astype(float),
            "expiry_date": expiry,
            "multiplier": 1.0,
        }
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _frame_evidence(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    records = frame.to_dict("records")
    return _jsonable(records)


def prepare_pipeline_inputs(
    session: Session,
    *,
    positions: list[Any],
    spec: dict[str, Any],
    engine_config_id: int | None = None,
) -> PreparedBacktestPipeline:
    """Resolve/cache all historical inputs before the long calculation starts."""
    from app.models import MarketDataProfile

    start = str(spec.get("start"))
    end = str(spec.get("end"))
    vol_source = str(spec.get("vol_source", "flat"))
    vol_window = int(spec.get("vol_window", 30) or 30)
    rate = float(spec.get("rate", 0.02) or 0.0)
    flat_vol = float(spec.get("flat_vol", 0.2) or 0.0)
    notes: list[str] = []
    excluded: list[dict[str, Any]] = []
    history: dict[str, tuple] = {}
    evidence_rows: list[dict[str, Any]] = []
    groups = backtest_bridge.group_by_underlying(positions)
    if groups:
        quantark.ensure_quantark_path()
        from quantark.backtest.otc import HedgeSpec, FuturesRollPolicy

    for underlying, plist in groups.items():
        try:
            sym = akshare_symbol(underlying)
            asset_class = akshare_asset_class(underlying)
            spot_df = mh.ensure_spot_history(
                session,
                symbol=sym,
                asset_class=asset_class,
                start=start,
                end=end,
            )
            vol_df = mh.derive_vol(
                spot_df,
                vol_source=vol_source,
                vol_window=vol_window,
                flat_vol=flat_vol,
            )
            rate_df = mh.flat_rate(spot_df, rate)
            hedge_info = _resolve_hedge(underlying, asset_class) or {"kind": "spot"}
            futures_profile = None
            if hedge_info.get("kind") == "futures":
                try:
                    futures_df = mh.ensure_futures_chain(
                        session,
                        prefix=hedge_info["prefix"],
                        start=start,
                        end=end,
                    )
                    hedge = HedgeSpec(
                        kind="futures",
                        multiplier=float(hedge_info["multiplier"]),
                        roll_policy=FuturesRollPolicy(),
                    )
                    futures_profile = (
                        session.query(MarketDataProfile)
                        .filter(
                            MarketDataProfile.symbol == hedge_info["prefix"],
                            MarketDataProfile.asset_class == "futures",
                        )
                        .order_by(MarketDataProfile.id.desc())
                        .first()
                    )
                except (NotImplementedError, RuntimeError) as exc:
                    notes.append(
                        f"{underlying}: futures chain for {hedge_info['prefix']} "
                        f"unavailable ({exc}); fell back to spot hedge"
                    )
                    futures_df = _synthetic_futures_from_spot(spot_df)
                    hedge = HedgeSpec(kind="spot", multiplier=1.0)
            else:
                futures_df = _synthetic_futures_from_spot(spot_df)
                hedge = HedgeSpec(kind="spot", multiplier=1.0)

            spot_profile = (
                session.query(MarketDataProfile)
                .filter(
                    MarketDataProfile.symbol == sym,
                    MarketDataProfile.asset_class == asset_class,
                )
                .order_by(MarketDataProfile.id.desc())
                .first()
            )
            history[underlying] = (
                spot_df.copy(deep=True),
                vol_df.copy(deep=True),
                rate_df.copy(deep=True),
                futures_df.copy(deep=True),
                deepcopy(hedge),
            )
            evidence_rows.append(
                {
                    "underlying": underlying,
                    "spot_profile": (
                        {
                            "id": spot_profile.id,
                            "symbol": spot_profile.symbol,
                            "asset_class": spot_profile.asset_class,
                            "adjust": spot_profile.adjust,
                            "updated_at": datetime_iso(spot_profile.updated_at),
                            "data_hash": canonical_hash(spot_profile.data or {}),
                        }
                        if spot_profile is not None
                        else None
                    ),
                    "futures_profile": (
                        {
                            "id": futures_profile.id,
                            "symbol": futures_profile.symbol,
                            "updated_at": datetime_iso(futures_profile.updated_at),
                            "data_hash": canonical_hash(futures_profile.data or {}),
                        }
                        if futures_profile is not None
                        else None
                    ),
                    "spot_rows": _frame_evidence(spot_df),
                    "vol_rows": _frame_evidence(vol_df),
                    "rate_rows": _frame_evidence(rate_df),
                    "futures_rows": _frame_evidence(futures_df),
                    "hedge": {
                        "kind": getattr(hedge, "kind", None),
                        "multiplier": getattr(hedge, "multiplier", None),
                    },
                }
            )
        except Exception as exc:
            for position in plist:
                excluded.append(
                    {
                        "position_id": getattr(position, "id", None),
                        "reason": (
                            f"market-data prep failed for {underlying}: {exc}"
                        ),
                    }
                )
    manifest = {
        "schema": "backtest-market-evidence/v1",
        "window": {"start": start, "end": end},
        "positions": [
            backtest_position_evidence(position)
            for position in sorted(positions, key=lambda item: int(item.id))
        ],
        "underlyings": sorted(evidence_rows, key=lambda row: row["underlying"]),
    }
    configs, config_excluded = backtest_bridge.build_books(
        session,
        positions,
        history,
        engine_types=_engine_types_for_spec(spec),
        engine_config_id=engine_config_id,
        start=_dt(start),
        end=_dt(end),
    )
    excluded.extend(config_excluded)
    return PreparedBacktestPipeline(
        history=deepcopy(history),
        configs=tuple(configs),
        excluded=tuple(deepcopy(excluded)),
        notes=tuple(notes),
        evidence_manifest=deepcopy(manifest),
    )

def run_pipeline(
    session: Session | None,
    *,
    positions: list[Any],
    spec: dict[str, Any],
    config: dict[str, Any],
    portfolio_name: str,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    valuation_date: datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
    prepared_pipeline: PreparedBacktestPipeline | None = None,
) -> tuple[str, dict[str, Any], list[dict], list]:
    """Run a book-level backtest across all underlyings in ``positions``.

    Returns ``(status, results_dict, excluded, raw)`` where status in
    {completed, empty}. ``raw`` is a list of ``(underlying, BookBacktestResults)``
    consumed by ``write_artifacts``.
    """
    quantark.ensure_quantark_path()
    from quantark.backtest.otc import (
        BookAutocallableBacktestEngine,
        HedgeSpec,
        FuturesRollPolicy,
    )

    # --- 1. Parse spec ------------------------------------------------------
    start = str(spec.get("start"))
    end = str(spec.get("end"))
    engine_name = _engine_name(spec.get("engine"))
    engine_types = _engine_types_for_spec(spec)
    vol_source = str(spec.get("vol_source", "flat"))
    vol_window = int(spec.get("vol_window", 30) or 30)
    rate = float(spec.get("rate", 0.02) or 0.0)
    flat_vol = float(spec.get("flat_vol", 0.2) or 0.0)

    if prepared_pipeline is None:
        if session is None:
            raise ValueError("backtest compute requires prepared pipeline inputs")
        prepared_pipeline = prepare_pipeline_inputs(
            session,
            positions=positions,
            spec=spec,
            engine_config_id=engine_config_id,
        )
    notes = list(deepcopy(prepared_pipeline.notes))
    excluded = list(deepcopy(prepared_pipeline.excluded))
    history = deepcopy(prepared_pipeline.history)

    # --- 3. Build books -----------------------------------------------------
    configs = list(prepared_pipeline.configs)

    window = {"start": start, "end": end}

    # --- 4. Empty path ------------------------------------------------------
    if not configs:
        return (
            "empty",
            {
                "window": window,
                "engine": engine_name,
                "vol_source": f"{vol_source}:{vol_window}",
                "portfolio": _aggregate_portfolio([]),
                "by_underlying": [],
                "excluded_positions": excluded,
                "notes": notes,
            },
            excluded,
            [],
        )

    # --- 5. Run engines -----------------------------------------------------
    raw: list[tuple[str, Any]] = []
    per_underlying: list[dict] = []
    total = len(configs)
    for i, cfg in enumerate(configs):
        try:
            results = BookAutocallableBacktestEngine(cfg).run()
        except Exception as exc:
            notes.append(f"{getattr(cfg, 'underlying', '?')}: engine run failed: {exc}")
            if progress:
                progress(i + 1, total)
            continue
        raw.append((cfg.underlying, results))
        per_underlying.append(_shape_underlying(cfg, results))
        if progress:
            progress(i + 1, total)

    if not per_underlying:
        return (
            "empty",
            {
                "window": window,
                "engine": engine_name,
                "vol_source": f"{vol_source}:{vol_window}",
                "portfolio": _aggregate_portfolio([]),
                "by_underlying": [],
                "excluded_positions": excluded,
                "notes": notes,
            },
            excluded,
            raw,
        )

    # --- 6. Aggregate portfolio --------------------------------------------
    portfolio = _aggregate_portfolio(per_underlying)
    portfolio_pnl_series = _align_portfolio_pnl(per_underlying)
    portfolio.update(_risk_metrics([p["total_pnl"] for p in portfolio_pnl_series]))
    portfolio["pnl_series"] = portfolio_pnl_series

    # --- 7. Assemble + coerce ----------------------------------------------
    results_dict = _jsonable(
        {
            "window": window,
            "engine": engine_name,
            "vol_source": f"{vol_source}:{vol_window}",
            "portfolio": portfolio,
            "by_underlying": per_underlying,
            "excluded_positions": excluded,
            "notes": notes,
        }
    )
    return "completed", results_dict, excluded, raw


def run_prepared_pipeline(
    prepared: PreparedBacktestPipeline,
    *,
    positions: list[Any],
    spec: dict[str, Any],
    config: dict[str, Any],
    portfolio_name: str,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    valuation_date: datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[str, dict[str, Any], list[dict], list]:
    """Pure long-running phase: no SQLAlchemy Session is accepted."""
    return run_pipeline(
        None,
        positions=positions,
        spec=spec,
        config=config,
        portfolio_name=portfolio_name,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        valuation_date=valuation_date,
        progress=progress,
        prepared_pipeline=deepcopy(prepared),
    )


def _align_portfolio_pnl(per_underlying: list[dict]) -> list[dict]:
    """Sum per-underlying total_pnl by date into a single portfolio P&L path.

    Underlyings may have slightly different date coverage; we union the dates
    and forward-fill each underlying's last-known cumulative total_pnl so the
    portfolio curve is monotone in coverage (a not-yet-started underlying
    contributes 0 until its first observation).
    """
    # Collect each underlying's date->total_pnl map, in date order.
    series_maps: list[list[tuple[str, float]]] = []
    all_dates: set[str] = set()
    for item in per_underlying:
        pairs: list[tuple[str, float]] = []
        for row in item.get("pnl_series", []):
            d = row.get("date")
            if d is None:
                continue
            pairs.append((d, float(row.get("total_pnl", 0.0) or 0.0)))
            all_dates.add(d)
        series_maps.append(pairs)

    if not all_dates:
        return []

    ordered_dates = sorted(all_dates)
    # Build a forward-filled lookup per underlying.
    out: list[dict] = []
    last_vals = [0.0] * len(series_maps)
    iters = [dict(pairs) for pairs in series_maps]
    for d in ordered_dates:
        total = 0.0
        for idx, lookup in enumerate(iters):
            if d in lookup:
                last_vals[idx] = lookup[d]
            total += last_vals[idx]
        out.append({"date": d, "total_pnl": total})
    return out


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

class _DashboardAdapter:
    """Adapt a ``BookBacktestResults`` to the property/name surface that
    ``AutocallableBacktestDashboard`` reads.

    The dashboard accesses ``results.states_df`` etc. as PROPERTIES (no parens)
    and uses ``rebalance_df`` (singular), while ``BookBacktestResults`` exposes
    METHODS and ``rebalances_df`` (plural). This wrapper bridges the two:
      * each df name is a @property that calls the underlying method;
      * ``rebalance_df`` maps to ``results.rebalances_df()``;
      * any missing/erroring df returns an empty DataFrame so the dashboard
        degrades instead of crashing.
    """

    def __init__(self, results: Any):
        self._results = results

    def _safe(self, method_name: str):
        import pandas as pd

        fn = getattr(self._results, method_name, None)
        if fn is None:
            return pd.DataFrame()
        try:
            df = fn()
            return df if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    @property
    def states_df(self):
        return self._safe("states_df")

    @property
    def greeks_df(self):
        return self._safe("greeks_df")

    @property
    def rebalance_df(self):  # singular name the dashboard expects
        return self._safe("rebalances_df")

    @property
    def trades_df(self):
        return self._safe("trades_df")

    @property
    def actions_df(self):
        return self._safe("actions_df")

    @property
    def daily_event_summary_df(self):
        return self._safe("daily_event_summary_df")

    @property
    def event_probability_df(self):
        return self._safe("event_probability_df")

    @property
    def surfaces_df(self):
        return self._safe("surfaces_df")

    @property
    def config(self):
        config = getattr(self._results, "config", None)
        if config is None or hasattr(config, "product"):
            return config
        products = getattr(config, "products", None) or []
        product = getattr(products[0], "product", None) if products else None
        if product is None:
            return config

        class _ConfigAdapter:
            def __getattr__(self, name: str):
                return getattr(config, name)

        adapter = _ConfigAdapter()
        adapter.product = product
        return adapter

    def get_summary(self):
        try:
            return self._results.get_summary()
        except Exception:
            return {}


def write_artifacts(*, raw: list, run_id: int, formats: list[str], base_dir: str) -> dict:
    """Render per-underlying quant-ark dashboards. Never raises: failures → notes.

    ``raw`` is the list of ``(underlying, BookBacktestResults)`` from
    ``run_pipeline``. ``formats`` is accepted for parity with the scenario-test
    signature; HTML dashboards are always attempted. ``BookBacktestResults`` has
    no ``export_to_excel`` (verified against the quant-ark API), so no Excel/
    parquet export is attempted — recorded as a note only when ``formats`` asks.
    """
    out_dir = os.path.join(base_dir, str(run_id))
    artifacts: dict[str, Any] = {"dashboards": {}, "notes": []}
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as exc:
        artifacts["notes"].append(f"artifact directory unavailable: {exc}")
        return artifacts

    try:
        quantark.ensure_quantark_path()
        from quantark.backtest.otc import (
            AutocallableBacktestDashboard,
            AutocallableDashboardConfig,
        )
    except Exception as exc:
        artifacts["notes"].append(f"dashboard import skipped: {exc}")
        return artifacts

    for underlying, results in raw or []:
        try:
            adapter = _DashboardAdapter(results)
            dashboard = AutocallableBacktestDashboard(adapter, AutocallableDashboardConfig())
            out_path = os.path.join(out_dir, f"dashboard_{underlying}.html")
            dashboard.write_html(out_path)
            if os.path.exists(out_path):
                artifacts["dashboards"][str(underlying)] = out_path
        except Exception as exc:
            artifacts["notes"].append(f"dashboard for {underlying} skipped: {exc}")

    # BookBacktestResults exposes no export_to_excel/parquet writer; record the
    # gap if a non-html export format was requested so callers aren't surprised.
    extra_formats = [f for f in (formats or []) if str(f).lower() not in ("html", "")]
    if extra_formats:
        artifacts["notes"].append(
            f"export formats {extra_formats} unsupported: BookBacktestResults has "
            "no export_to_excel/parquet writer (html dashboard only)"
        )

    return artifacts

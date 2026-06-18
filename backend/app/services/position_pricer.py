from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Callable

from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..models import (
    Portfolio,
    Position,
    PositionValuationResult,
    PositionValuationRun,
    PricingParameterRow,
)
from ..schemas import AkshareSnapshotRequest, PricingEnvironmentSnapshot
from .import_schema import is_knocked_out, read_notional_unit, read_trade_status
from .market_data import fetch_akshare_snapshot
from .portfolio_membership import resolve_positions
from .position_adapter import SUPPORTED_STATUS, normalize_symbol
from .domains.products import compatibility_terms_for_position
from .pricing_profiles import (
    position_requires_pricing_params,
    pricing_rows_for_profile,
    resolve_pricing_parameter_row_for_position,
)
from .engine_configs import get_engine_config, position_with_engine, resolve_pricing_engine
from .quantark import QuantArkResult, contract_multiplier_for_position, gross_notional_for_position, market_priced_position_inputs, price_product, usable_model_value, valuation_multiplier_for_position
from .risk_engine import compute_position_greeks


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketOverrides:
    spot: float | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None

    def model_dump(self) -> dict[str, float]:
        return {
            key: value
            for key, value in {
                "spot": self.spot,
                "rate": self.rate,
                "dividend_yield": self.dividend_yield,
                "volatility": self.volatility,
            }.items()
            if value is not None
        }


ADAPTIVE_GRID_CHAIN: tuple[int, ...] = (201, 501, 1001)


def _resolve_grid_chain(engine_kwargs: dict | None) -> list[int | None]:
    """Return the grid escalation sequence for one position.

    The adaptive chain applies **only** to quad engines (snowball QUAD pricings).
    For any other engine the helper returns ``[None]`` — a single attempt with
    ``engine_kwargs`` passed verbatim, with no ``grid_points`` injection.

    For quad engines:
    - If ``engine_kwargs["params_kwargs"]["grid_points"]`` is set, returns a
      single-element list with that value (no escalation).
    - Otherwise returns the adaptive chain ``[201, 501, 1001]``.
    """
    kw = engine_kwargs or {}
    if kw.get("params_type") != "quad_params":
        return [None]
    pk = kw.get("params_kwargs") or {}
    explicit = pk.get("grid_points")
    if explicit is not None:
        return [int(explicit)]
    return list(ADAPTIVE_GRID_CHAIN)


def _engine_kwargs_with_grid(
    engine_kwargs: dict | None, grid: int | None
) -> dict:
    """Return a copy of ``engine_kwargs`` with ``params_kwargs.grid_points = grid``.

    Does not mutate the input. ``params_type`` is preserved verbatim if present
    and is **not** invented when absent. Any other keys (top-level or in
    ``params_kwargs``) are preserved.

    When ``grid`` is ``None``, returns a shallow copy of ``engine_kwargs`` with no
    ``grid_points`` injection — used for non-quad engines that should receive
    their kwargs verbatim.
    """
    out = dict(engine_kwargs or {})
    if grid is None:
        return out
    pk = dict(out.get("params_kwargs") or {})
    pk["grid_points"] = int(grid)
    out["params_kwargs"] = pk
    return out


SpotFetcher = Callable[[str, datetime], tuple[float | None, dict[str, Any]]]


def price_portfolio_positions(
    session: Session,
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    valuation_date: datetime | None = None,
    overrides: MarketOverrides | None = None,
    engine_name: str | None = None,
    engine_kwargs: dict[str, Any] | None = None,
    compute_greeks: bool = False,
    spot_fetcher: SpotFetcher | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> PositionValuationRun:
    valuation_date = valuation_date or datetime.utcnow()
    overrides = overrides or MarketOverrides()
    spot_fetcher = spot_fetcher or fetch_spot_from_akshare

    portfolio = (
        session.query(Portfolio)
        .options(selectinload(Portfolio.positions))
        .filter(Portfolio.id == portfolio_id)
        .one_or_none()
    )
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")

    selected_ids = set(position_ids or [])
    candidates = resolve_positions(portfolio, session)
    positions = [position for position in candidates if not selected_ids or position.id in selected_ids]
    if selected_ids and len(positions) != len(selected_ids):
        found_ids = {position.id for position in positions}
        missing_ids = sorted(selected_ids - found_ids)
        raise ValueError(f"Position not found in portfolio: {missing_ids}")
    has_engine_override = engine_name is not None or engine_kwargs is not None
    if has_engine_override and len(positions) != 1:
        raise ValueError("Engine overrides require exactly one selected position")
    # A spot override is one scalar price level; stamping it onto positions on
    # different underlyings is a guaranteed mispricing. Refuse rather than
    # apply — narrow the selection or drop the override. (r/q/vol stay
    # portfolio-wide: they are rates, not per-instrument price levels.)
    if overrides.spot is not None:
        spot_scopes = {
            getattr(position, "underlying_id", None) or position.underlying
            for position in positions
        }
        if len(spot_scopes) > 1:
            raise ValueError(
                "Spot override requires positions on a single underlying; "
                f"the selection spans {len(spot_scopes)} underlyings — "
                "narrow the selection or drop the spot override"
            )

    pricing_rows = (
        pricing_rows_for_profile(session, profile_id=pricing_parameter_profile_id)
        if pricing_parameter_profile_id is not None
        else []
    )
    engine_config = get_engine_config(session, engine_config_id)
    # Pre-resolve instrument-level assumption rows on the MAIN thread (one query per
    # distinct underlying) so the pricing pool never queries the shared Session from
    # a worker — the old PositionMarketInput dict was pre-loaded the same way.
    assumption_rows = _resolve_assumption_rows(session, positions, valuation_date)
    # Pre-resolve the quote-store spot on the MAIN thread too (mirrors the assumption
    # rows): one batched query keyed by underlying_id -> (price, as_of). Workers read
    # this dict instead of querying the shared Session, so the pricing pool emits NO
    # SQL — the regression class (worker-thread SQL on the shared Session) is gone.
    quote_spots = _resolve_quote_spots(session, positions, valuation_date)
    run_overrides = overrides.model_dump()
    if pricing_parameter_profile_id is not None:
        run_overrides["pricing_parameter_profile_id"] = pricing_parameter_profile_id
    if engine_config is not None:
        run_overrides["engine_config_id"] = engine_config.id
    if selected_ids:
        run_overrides["position_ids"] = sorted(selected_ids)
    if engine_name is not None:
        run_overrides["engine_name"] = engine_name
    if engine_kwargs is not None:
        run_overrides["engine_kwargs"] = engine_kwargs
    run = PositionValuationRun(
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config.id if engine_config is not None else None,
        market_source_path=None,
        valuation_date=valuation_date,
        overrides=run_overrides,
        summary={},
        status="running",
        resolved_position_ids=[p.id for p in candidates],
    )
    session.add(run)
    session.flush()

    symbol_spot_cache: dict[str, tuple[float | None, dict[str, Any]]] = {}
    # Guards the fetch-once cache AND the fallback-fetch collection list. Workers
    # do NO session I/O — the lock only serialises in-memory structures shared
    # across the pool, so the shared Session is never touched off the main thread.
    cache_lock = Lock()
    # Successful fallback fetches collected by workers; persisted to the quote
    # store by the MAIN thread AFTER the pool joins. Tuples are
    # (instrument_id, price, as_of, fetch_meta).
    fallback_quotes: list[tuple[int, float, datetime, dict[str, Any]]] = []
    totals = {
        "positions": 0,
        "priced": 0,
        "failed": 0,
        "unsupported": 0,
        "market_value": 0.0,
        "pnl": 0.0,
    }

    total = len(positions)
    if progress_callback is not None:
        progress_callback(0, total)

    worker_count = max(1, min(get_settings().risk_parallel_workers, len(positions) or 1))
    results_by_id: dict[int, dict[str, Any]] = {}

    def _safe_result(future: Future, position: Position) -> dict[str, Any]:
        try:
            return future.result()
        except Exception as exc:
            logger.exception(
                "Unexpected position pricing failure: portfolio_id=%s position_id=%s source_trade_id=%s",
                portfolio_id,
                position.id,
                position.source_trade_id,
            )
            return _failed(
                position,
                f"Unexpected pricing error: {exc}",
                "pricing",
                engine_name=engine_name or position.engine_name,
            )

    with ThreadPoolExecutor(
        max_workers=worker_count, thread_name_prefix="open-otc-pricer"
    ) as pool:
        future_to_position: dict[Future, Position] = {
            pool.submit(
                _price_position,
                position=position,
                pricing_rows=pricing_rows,
                assumption_rows=assumption_rows,
                valuation_date=valuation_date,
                overrides=overrides,
                engine_name=engine_name,
                engine_kwargs=engine_kwargs,
                engine_config=engine_config,
                compute_greeks=compute_greeks,
                spot_fetcher=spot_fetcher,
                symbol_spot_cache=symbol_spot_cache,
                quote_spots=quote_spots,
                fallback_quotes=fallback_quotes,
                cache_lock=cache_lock,
            ): position
            for position in positions
        }
        done = 0
        for fut in as_completed(future_to_position):
            position = future_to_position[fut]
            results_by_id[position.id] = _safe_result(fut, position)
            done += 1
            if progress_callback is not None:
                progress_callback(done, total)

    # Persist fallback fetches on the MAIN thread now that the pool has joined
    # (fetch-once). Dedupe per instrument: the cache already fetched each symbol
    # once, but several positions can share an instrument, so collapse to the
    # first fetch per instrument_id before recording. The recorded quote is
    # visible to the NEXT run (within this run the symbol_spot_cache supplies
    # reuse), preserving the "second run reads the recorded quote" contract.
    _persist_fallback_quotes(session, fallback_quotes)

    # Aggregate on the main thread in submission order.
    for position in positions:
        result = results_by_id[position.id]
        totals["positions"] += 1
        valuation_result = PositionValuationResult(
            valuation_run_id=run.id,
            position_id=position.id,
            source_trade_id=position.source_trade_id,
            ok=result["ok"],
            price=result.get("price"),
            market_value=result.get("market_value"),
            pnl=result.get("pnl"),
            market_inputs=result.get("market_inputs", {}),
            result_payload=result.get("result_payload", {}),
            error=result.get("error"),
        )
        session.add(valuation_result)
        if result["ok"]:
            totals["priced"] += 1
            totals["market_value"] += float(result.get("market_value") or 0.0)
            totals["pnl"] += float(result.get("pnl") or 0.0)
        else:
            totals["failed"] += 1
            if result.get("error_type") == "unsupported":
                totals["unsupported"] += 1

    run.summary = totals
    run.status = "completed" if totals["failed"] == 0 else "completed_with_errors"
    session.flush()
    return run


def _resolve_assumption_rows(
    session: Session | None,
    positions: list[Position],
    valuation_date: datetime,
) -> dict[int, Any]:
    """Map underlying_id -> latest AssumptionRow (r/q/vol), resolved once per
    distinct underlying on the calling thread. Empty without a session."""
    if session is None:
        return {}
    from .assumptions import latest_assumption_row

    out: dict[int, Any] = {}
    for underlying_id in {
        uid
        for position in positions
        if (uid := getattr(position, "underlying_id", None)) is not None
    }:
        row = latest_assumption_row(session, underlying_id, as_of=valuation_date)
        if row is not None:
            out[underlying_id] = row
    return out


def fetch_spot_from_akshare(symbol: str, valuation_date: datetime) -> tuple[float | None, dict[str, Any]]:
    normalized = normalize_symbol(symbol)
    end_date = valuation_date.date()
    start_date = end_date - timedelta(days=14)
    request = AkshareSnapshotRequest(
        symbol=_akshare_symbol(normalized),
        asset_class=_asset_class(normalized),  # type: ignore[arg-type]
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        name=f"{normalized} pricing spot",
    )
    snapshot = fetch_akshare_snapshot(request)
    spot = snapshot.data.get("spot")
    return (
        float(spot) if spot is not None else None,
        {
            "symbol": normalized,
            "akshare_symbol": request.symbol,
            "asset_class": request.asset_class,
            "source_metadata": snapshot.source_metadata,
        },
    )


def _resolve_quote_spots(
    session: Session | None,
    positions: list[Position],
    valuation_date: datetime,
) -> dict[int, tuple[float, datetime]]:
    """Map underlying_id -> (spot, as_of) from the quote store, resolved once per
    distinct underlying on the calling (MAIN) thread. Empty without a session.

    Mirrors ``_resolve_assumption_rows``: a single batched read so the pricing
    pool reads spot from this dict instead of querying the shared Session.
    """
    if session is None:
        return {}
    from .quotes import latest_quotes

    underlying_ids = {
        uid
        for position in positions
        if (uid := getattr(position, "underlying_id", None)) is not None
    }
    if not underlying_ids:
        return {}
    quotes = latest_quotes(session, underlying_ids, as_of=valuation_date)
    return {
        iid: (float(quote.price), quote.as_of) for iid, quote in quotes.items()
    }


def _read_quote_spot(
    quote_spots: dict[int, tuple[float, datetime]] | None,
    underlying_id: int | None,
    valuation_date: datetime,
) -> tuple[float, int] | None:
    """Return ``(spot, quote_age_days)`` from the pre-resolved quote dict, or
    ``None`` when there is no underlying_id or no eligible quote.

    Pure dict lookup — no session, no I/O — so it is safe to call from a worker
    thread. The dict was built on the main thread by ``_resolve_quote_spots``.
    """
    if not quote_spots or underlying_id is None:
        return None
    hit = quote_spots.get(underlying_id)
    if hit is None:
        return None
    price, as_of = hit
    return price, (valuation_date.date() - as_of.date()).days


def _persist_fallback_quotes(
    session: Session | None,
    fallback_quotes: list[tuple[int, float, datetime, dict[str, Any]]],
) -> None:
    """Persist worker-collected fallback fetches to the quote store (fetch-once).

    Runs on the MAIN thread after the pool joins. Deduped per instrument_id (the
    fetch-once cache already fetches each symbol once, but several positions may
    share an instrument). No-op without a session or with nothing collected.
    """
    if session is None or not fallback_quotes:
        return
    from .quotes import record_quote

    seen: set[int] = set()
    for instrument_id, price, as_of, fetch_meta in fallback_quotes:
        if instrument_id in seen:
            continue
        seen.add(instrument_id)
        record_quote(
            session,
            instrument_id=instrument_id,
            price=float(price),
            as_of=as_of,
            source="pricer_fallback",
            meta=dict(fetch_meta or {}),
        )


def _price_position(
    *,
    position: Position,
    pricing_rows: list[PricingParameterRow],
    valuation_date: datetime,
    overrides: MarketOverrides,
    engine_name: str | None,
    engine_kwargs: dict[str, Any] | None,
    engine_config: Any | None = None,
    compute_greeks: bool = False,
    spot_fetcher: SpotFetcher,
    symbol_spot_cache: dict[str, tuple[float | None, dict[str, Any]]],
    assumption_rows: dict[int, Any] | None = None,
    quote_spots: dict[int, tuple[float, datetime]] | None = None,
    fallback_quotes: list[tuple[int, float, datetime, dict[str, Any]]] | None = None,
    cache_lock: Lock | None = None,
) -> dict[str, Any]:
    try:
        resolved_engine = resolve_pricing_engine(
            position,
            engine_config,
            override_engine_name=engine_name,
            override_engine_kwargs=engine_kwargs,
        )
    except ValueError as exc:
        return _failed(position, str(exc), "engine_config", engine_name=None)
    engine_position = position_with_engine(position, resolved_engine)
    resolved_engine_name = resolved_engine.engine_name
    resolved_engine_kwargs = resolved_engine.engine_kwargs
    if position.mapping_status != SUPPORTED_STATUS:
        return _failed(
            position,
            position.mapping_error or "Position is not mapped to a supported QuantArk product",
            "unsupported",
            engine_name=resolved_engine_name,
        )
    if position.status == "closed" or is_knocked_out(_source_trade_state(position)):
        return _failed(position, "Terminal lifecycle state is not priced by the batch pricer", "terminal", engine_name=resolved_engine_name)

    pricing_row_resolution = resolve_pricing_parameter_row_for_position(
        pricing_rows,
        position,
    )
    pricing_row = pricing_row_resolution.row if pricing_row_resolution.ok else None

    # Assumption fall-through for r/q/vol (instrument-unification T8): when the
    # trade row supplies a field it wins; otherwise the latest instrument-level
    # AssumptionRow (pre-resolved on the main thread, keyed by underlying_id)
    # provides it; the override and env fallback bracket both ends.
    underlying_id = getattr(position, "underlying_id", None)
    assumption_row = (
        (assumption_rows or {}).get(underlying_id) if underlying_id is not None else None
    )

    symbol = (
        (pricing_row.symbol if pricing_row is not None else None)
        or position.underlying
    )
    if overrides.spot is not None:
        spot = overrides.spot
        spot_source = "override"
        spot_meta = {"source": "override"}
    else:
        # Spot chain (instrument-unification T8): observations live ONLY in the
        # quote store, so the row no longer carries spot.
        #   override -> QUOTE STORE (pre-resolved) -> akshare fetch.
        # The quote store was read on the MAIN thread into ``quote_spots`` before
        # the pool was submitted, so this worker does NO session I/O. On a
        # successful fallback fetch we APPEND the result to ``fallback_quotes``
        # (under ``cache_lock``); the main thread persists it after the pool joins
        # so the next consumer reads the recorded quote (fetch-once).
        spot = None
        spot_meta = {}
        spot_source = "missing"
        quote_hit = _read_quote_spot(quote_spots, underlying_id, valuation_date)
        if quote_hit is not None:
            spot, quote_age_days = quote_hit
            spot_source = "market_quote"
            spot_meta = {"source": "market_quote", "quote_age_days": quote_age_days}
        else:
            # Fetch-once across workers: the cache check-and-set must be atomic,
            # or pool workers racing on the same symbol each fetch it. cache_lock
            # serialises the in-memory cache here, so hold it across the network
            # fetch too (correctness over latency). When cache_lock is None
            # (callers that don't pass one) fall back to the unguarded path.
            if cache_lock is not None:
                with cache_lock:
                    if symbol not in symbol_spot_cache:
                        symbol_spot_cache[symbol] = spot_fetcher(symbol, valuation_date)
            else:
                if symbol not in symbol_spot_cache:
                    symbol_spot_cache[symbol] = spot_fetcher(symbol, valuation_date)
            spot, fetch_meta = symbol_spot_cache[symbol]
            spot_meta = {"source": "akshare", **(fetch_meta or {})}
            if spot is not None:
                spot_source = "akshare_fallback"
                # Fetch-once: defer persistence to the main thread (no worker
                # session I/O). Collect the fetch under cache_lock; the main
                # thread records it after the pool joins so the next consumer
                # (risk or a later pricing pass) reads the recorded quote.
                if fallback_quotes is not None and underlying_id is not None:
                    entry = (underlying_id, float(spot), valuation_date, dict(fetch_meta or {}))
                    if cache_lock is not None:
                        with cache_lock:
                            fallback_quotes.append(entry)
                    else:
                        fallback_quotes.append(entry)
    rate, rate_source = _resolve_market_field(
        override_value=overrides.rate,
        pricing_value=pricing_row.rate if pricing_row is not None else None,
        assumption_value=assumption_row.rate if assumption_row is not None else None,
    )
    dividend_yield, dividend_yield_source = _resolve_market_field(
        override_value=overrides.dividend_yield,
        pricing_value=pricing_row.dividend_yield if pricing_row is not None else None,
        assumption_value=assumption_row.dividend_yield if assumption_row is not None else None,
    )
    volatility, volatility_source = _resolve_market_field(
        override_value=overrides.volatility,
        pricing_value=pricing_row.volatility if pricing_row is not None else None,
        assumption_value=assumption_row.volatility if assumption_row is not None else None,
    )
    field_sources = {
        "spot": spot_source,
        "rate": rate_source,
        "dividend_yield": dividend_yield_source,
        "volatility": volatility_source,
    }
    market_input_source = _market_input_source(pricing_row, assumption_row, field_sources)

    requires_pricing_params = position_requires_pricing_params(position)
    required_market_inputs = {
        "spot": spot,
        **(
            {
                "rate": rate,
                "dividend_yield": dividend_yield,
                "volatility": volatility,
            }
            if requires_pricing_params
            else {}
        ),
    }
    missing = [
        name
        for name, value in required_market_inputs.items()
        if value is None
    ]
    if missing:
        failure_inputs = {
            "valuation_date": valuation_date.isoformat(),
            "asset_name": symbol,
            "market_input_source": market_input_source,
            "pricing_parameter_profile_id": pricing_row.profile_id if pricing_row is not None else None,
            "pricing_parameter_row_id": pricing_row.id if pricing_row is not None else None,
            "assumption_set_id": assumption_row.set_id if assumption_row is not None else None,
            "assumption_row_id": assumption_row.id if assumption_row is not None else None,
            "spot_metadata": spot_meta,
            "field_sources": field_sources,
        }
        # When spot itself never resolved (override -> quote store -> akshare all
        # missed), surface the explicit no-source diagnostic. Other-field misses
        # keep the prior aggregate semantics.
        if spot is None:
            return _failed(
                position,
                f"no market quote for {symbol} as of {valuation_date:%Y-%m-%d}",
                "missing_quote",
                engine_name=resolved_engine_name,
                market_inputs=failure_inputs,
            )
        return _failed(
            position,
            f"Missing market inputs: {', '.join(missing)}",
            "market",
            engine_name=resolved_engine_name,
            market_inputs=failure_inputs,
        )

    resolved_market_inputs = {
        "valuation_date": valuation_date.isoformat(),
        "spot": float(spot),
        "asset_name": symbol,
        "market_input_source": market_input_source,
        "pricing_parameter_profile_id": pricing_row.profile_id if pricing_row is not None else None,
        "pricing_parameter_row_id": pricing_row.id if pricing_row is not None else None,
        "assumption_set_id": assumption_row.set_id if assumption_row is not None else None,
        "assumption_row_id": assumption_row.id if assumption_row is not None else None,
        "spot_metadata": spot_meta,
        "field_sources": field_sources,
    }
    if requires_pricing_params:
        resolved_market_inputs.update(
            {
                "rate": float(rate),
                "dividend_yield": float(dividend_yield),
                "volatility": float(volatility),
            }
        )
    default_market = PricingEnvironmentSnapshot()
    bus_days = (
        getattr(engine_config, "business_days_in_year", None)
        if engine_config is not None
        else None
    )
    market = PricingEnvironmentSnapshot(
        valuation_date=valuation_date,
        spot=resolved_market_inputs["spot"],
        volatility=float(volatility) if volatility is not None else default_market.volatility,
        rate=float(rate) if rate is not None else default_market.rate,
        dividend_yield=float(dividend_yield) if dividend_yield is not None else default_market.dividend_yield,
        asset_name=symbol,
        currency=_source_currency(position),
        **({"bus_days_in_year": bus_days} if bus_days is not None else {}),
    )
    grid_chain = _resolve_grid_chain(resolved_engine_kwargs)
    last_priced: QuantArkResult | None = None
    attempt_grid: int | None = None
    priced: QuantArkResult | None = None
    quantark_price: float = 0.0
    market_value: float = 0.0
    gross_notional: float = 0.0
    attempt_kwargs: dict[str, Any] = {}
    attempts_made: int = 0
    compat = compatibility_terms_for_position(engine_position)
    product_kwargs, pricing_engine_kwargs = market_priced_position_inputs(
        compat["product_type"],
        compat["product_kwargs"],
        market,
        resolved_engine_name,
        resolved_engine_kwargs,
    )
    valuation_multiplier = valuation_multiplier_for_position(position)

    for grid in grid_chain:
        attempts_made += 1
        attempt_kwargs = _engine_kwargs_with_grid(pricing_engine_kwargs, grid)
        candidate = price_product(
            compat["product_type"],
            product_kwargs,
            market,
            resolved_engine_name,
            attempt_kwargs,
        )
        if not candidate.ok:
            last_priced = candidate
            continue
        candidate_price = float(candidate.data.get("price", 0.0))
        candidate_market_value = candidate_price * float(position.quantity) * valuation_multiplier
        candidate_notional = gross_notional_for_position(position, market)
        if usable_model_value(candidate_market_value, candidate_notional):
            attempt_grid = grid
            priced = candidate
            quantark_price = candidate_price
            market_value = candidate_market_value
            gross_notional = candidate_notional
            break
        last_priced = candidate

    if priced is None:
        # All grids failed — either engine errored every time or each numerically
        # produced an implausible value. Preserve existing error shape; attach
        # the attempted chain for diagnostics when more than one grid was tried.
        if last_priced is not None and not last_priced.ok:
            failed = _failed(
                position,
                last_priced.error or "QuantArk pricing failed",
                "pricing",
                market_inputs=resolved_market_inputs,
                engine_name=resolved_engine_name,
            )
            if len(grid_chain) > 1:
                failed["result_payload"] = {
                    **failed.get("result_payload", {}),
                    "attempted_grids": grid_chain,
                }
            failed["result_payload"] = {
                **failed.get("result_payload", {}),
                "resolved_engine": resolved_engine.diagnostics(),
            }
            return failed
        # Numerically implausible at every attempt: rebuild the rich diagnostic
        # payload from the last attempt so downstream tooling (which expects
        # quantark_price, unit_price, contract_multiplier, etc.) keeps working.
        last_quantark_price = (
            float((last_priced.data or {}).get("price", 0.0))
            if last_priced is not None
            else 0.0
        )
        last_market_value = last_quantark_price * float(position.quantity) * valuation_multiplier
        last_notional = gross_notional_for_position(position, market)
        contract_multiplier = _contract_multiplier(position)
        failure_payload: dict[str, Any] = dict(
            (last_priced.data if last_priced is not None else {}) or {}
        )
        failure_payload.update(
            {
                "unit_price": last_quantark_price,
                "quantark_price": last_quantark_price,
                "contract_multiplier": contract_multiplier,
                "position_price": last_quantark_price,
                "quantity": float(position.quantity),
                "gross_notional": last_notional,
            }
        )
        if len(grid_chain) > 1:
            failure_payload["attempted_grids"] = grid_chain
        elif grid_chain and grid_chain[0] is not None:
            # Single explicit-grid attempt that failed — record the attempted chain
            # so the breadcrumb makes the rejection visible.
            failure_payload["attempted_grids"] = grid_chain
        failure_payload["resolved_engine"] = resolved_engine.diagnostics()
        return {
            "ok": False,
            "error": (
                f"Model returned implausible market value {last_market_value:.6g}; "
                f"gross notional is {last_notional:.6g}"
            ),
            "error_type": "pricing",
            "price": None,
            "market_value": None,
            "pnl": None,
            "market_inputs": resolved_market_inputs,
            "result_payload": failure_payload,
        }

    contract_multiplier = _contract_multiplier(position)
    price = quantark_price
    pnl = (price - float(position.entry_price or 0.0)) * float(position.quantity) * valuation_multiplier
    result_payload = priced.data | {
        "unit_price": quantark_price,
        "quantark_price": quantark_price,
        "contract_multiplier": contract_multiplier,
        "position_price": price,
        "quantity": float(position.quantity),
        "gross_notional": gross_notional,
        "resolved_engine": resolved_engine.diagnostics(),
    }
    result_payload["grid_points_used"] = attempt_grid
    if attempt_grid is None:
        result_payload.pop("grid_points_used", None)
    if attempts_made > 1:
        result_payload["attempted_grids"] = grid_chain
    if compute_greeks:
        greeks = compute_position_greeks(engine_position, market, engine_kwargs=attempt_kwargs)
        if greeks.get("ok"):
            quantity = float(position.quantity)
            result_payload["delta"] = greeks["delta"] * quantity
            result_payload["gamma"] = greeks["gamma"] * quantity
            result_payload["vega"] = greeks["vega"] * quantity
            result_payload["theta"] = greeks["theta"] * quantity
            result_payload["rho"] = greeks["rho"] * quantity
            result_payload["rho_q"] = greeks["rho_q"] * quantity
        else:
            result_payload["greeks_error"] = greeks.get("error")
    return {
        "ok": True,
        "price": price,
        "market_value": market_value,
        "pnl": pnl,
        "market_inputs": resolved_market_inputs,
        "result_payload": result_payload,
    }


def _resolve_market_field(
    *,
    override_value: float | None,
    pricing_value: float | None,
    assumption_value: float | None,
) -> tuple[float | None, str]:
    """r/q/vol chain: override -> pricing-row -> assumption set -> missing (env)."""
    if override_value is not None:
        return override_value, "override"
    if pricing_value is not None:
        return pricing_value, "pricing_parameter_profile"
    if assumption_value is not None:
        return assumption_value, "assumption_set"
    return None, "missing"


def _market_input_source(
    pricing_row: PricingParameterRow | None,
    assumption_row: Any | None,
    field_sources: dict[str, str],
) -> str:
    if pricing_row is not None:
        return "pricing_parameter_profile"
    if any(source == "assumption_set" for source in field_sources.values()):
        return "assumption_set"
    if all(source == "override" for source in field_sources.values()):
        return "override"
    if any(source == "market_quote" for source in field_sources.values()):
        return "market_quote"
    if any(source == "akshare_fallback" for source in field_sources.values()):
        return "akshare"
    if assumption_row is not None:
        return "assumption_set"
    return "missing"


def _failed(
    position: Position,
    error: str,
    error_type: str,
    *,
    market_inputs: dict[str, Any] | None = None,
    engine_name: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "error_type": error_type,
        "market_inputs": market_inputs or {},
        "result_payload": {
            "product_type": position.product_type,
            "engine": engine_name or position.engine_name,
            "mapping_status": position.mapping_status,
        },
    }


def _source_trade_state(position: Position) -> str:
    payload = position.source_payload or {}
    row = payload.get("row", {}) if isinstance(payload, dict) else {}
    return read_trade_status(row)


def _source_currency(position: Position) -> str:
    explicit = getattr(position, "currency", None)
    if explicit:
        return str(explicit)
    payload = position.source_payload or {}
    row = payload.get("row", {}) if isinstance(payload, dict) else {}
    return read_notional_unit(row) or "CNY"


def _contract_multiplier(position: Position) -> float:
    try:
        return contract_multiplier_for_position(position)
    except (TypeError, ValueError):
        return 1.0


def _akshare_symbol(symbol: str) -> str:
    if "." not in symbol:
        return symbol
    return symbol.split(".", 1)[0]


def _asset_class(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    if suffix in {"DCE", "SHF", "CZC", "INE", "CFFEX", "GFEX"}:
        return "futures"
    if suffix == "SGE" or code.upper() in {"AU9999", "AU9995", "AU100G", "AG9999"}:
        return "sge_spot"
    if suffix == "CSI" or code in {"000016", "000300", "000852", "000905", "931059"}:
        return "index"
    if code.startswith(("15", "16", "18", "51", "56", "58")):
        return "etf"
    return "stock"

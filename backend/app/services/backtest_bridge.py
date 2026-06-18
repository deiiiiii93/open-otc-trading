"""Bridge: DB positions -> per-underlying BookAutocallableBacktestConfig list."""
from __future__ import annotations
from collections import defaultdict
from typing import Any
from app.services import quantark


def group_by_underlying(positions: list[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for p in positions:
        groups[str(p.underlying)].append(p)
    return dict(groups)


def _has_lifecycle(product) -> bool:
    return type(product).__name__ in ("SnowballOption", "PhoenixOption", "KnockOutResetSnowballOption")


def _has_analytical_engine(product) -> bool:
    return type(product).__name__ in {
        "AsianOption",
        "BarrierOption",
        "DoubleBarrierOption",
        "DoubleSharkfinOption",
        "EuropeanVanillaOption",
        "OneTouchOption",
        "SingleSharkfinOption",
    }


def _engine_type_for_product(product, engine_types):
    if not isinstance(engine_types, dict):
        if engine_types is None:
            raise ValueError("engine_type or engine_types is required")
        return engine_types
    if _has_lifecycle(product):
        return engine_types["autocallable"]
    selected = engine_types["other"]
    if getattr(selected, "name", None) == "ANALYTICAL" and not _has_analytical_engine(product):
        return engine_types["fallback"]
    return selected


_BACKTEST_VOCAB_TO_ENGINE_TYPE = {
    "quad": "QUADRATURE",
    "quadrature": "QUADRATURE",
    "analytical": "ANALYTICAL",
    "pde": "PDE",
    "mc": "MONTE_CARLO",
    "monte_carlo": "MONTE_CARLO",
    "montecarlo": "MONTE_CARLO",
}


def _coerce_engine_type(name, engine_types=None, product=None):
    """Map the engine-config vocabulary (quad/analytical/pde/mc) to an EngineType.

    Non-strings (already an EngineType) pass through; unknown words raise so the
    caller can fall back to the spec-driven per-product selection.
    """
    if not isinstance(name, str):
        return name
    member = _BACKTEST_VOCAB_TO_ENGINE_TYPE.get(name.strip().lower())
    if member is None:
        raise ValueError(f"Unknown backtest engine {name!r}")
    quantark.ensure_quantark_path()
    from quantark.util.enum.engine_enums import EngineType

    return getattr(EngineType, member)


def build_books(session, positions, history, *, engine_type=None, engine_types=None,
                engine_config_id=None, strategy=None, txn_cost=None, start=None,
                end=None) -> tuple[list, list[dict]]:
    """Returns (configs, excluded). `history[underlying] = (spot_df, vol_df, rate_df,
    futures_df, HedgeSpec)` is prepared by the pipeline (Task 2.5).

    The quant-ark import is deferred until the first underlying that actually has
    history, so callers that only hit the missing-history exclusion path never
    need the real backtest package (keeps tests network-free).
    """
    from app.services.engine_configs import get_engine_config, resolve_backtest_engine

    engine_config = get_engine_config(session, engine_config_id) if session is not None else None
    _backtest_imported = False
    excluded: list[dict] = []
    configs = []
    for underlying, plist in group_by_underlying(positions).items():
        if underlying not in history:
            for pos in plist:
                excluded.append({"position_id": pos.id, "reason": f"no market history for {underlying}"})
            continue
        # Defer the QuantArk import until we know we have history to process.
        if not _backtest_imported:
            quantark.ensure_quantark_path()
            from quantark.backtest.otc import (
                BookAutocallableBacktestConfig,
                BookProduct,  # noqa: F401
                AutocallableEngineConfig,
                AutocallableMarketDataSet,
            )
            _backtest_imported = True
        spot_df, vol_df, rate_df, futures_df, hedge = history[underlying]
        book_products_by_engine = []
        for pos in plist:
            reason = quantark.risk_pricing_exclusion(pos)
            if reason:
                excluded.append({"position_id": pos.id, "reason": reason}); continue
            if float(getattr(pos, "quantity", 0) or 0) == 0.0:
                excluded.append({"position_id": pos.id, "reason": "Position quantity is zero"}); continue
            try:
                # build_product_for_position(position, market=None) returns a bare QuantArk product.
                # market=None is correct here: the backtest supplies time-series market data
                # via AutocallableMarketDataSet (history arg), not a point-in-time snapshot.
                product = quantark.build_product_for_position(pos)
            except Exception as exc:
                excluded.append({"position_id": pos.id, "reason": f"product build failed: {exc}"}); continue
            try:
                resolved_backtest = resolve_backtest_engine(pos, product, engine_config)
                selected_engine = _coerce_engine_type(
                    resolved_backtest["engine"], engine_types or engine_type, product
                )
            except ValueError:
                selected_engine = _engine_type_for_product(product, engine_types or engine_type)
            book_products_by_engine.append((selected_engine, BookProduct(
                product=product, quantity=float(pos.quantity), position_id=pos.id,
                has_lifecycle=_has_lifecycle(product))))
        if not book_products_by_engine:
            continue
        products_by_engine = defaultdict(list)
        for selected_engine_type, book_product in book_products_by_engine:
            products_by_engine[selected_engine_type].append(book_product)
        for selected_engine_type, book_products in products_by_engine.items():
            kwargs = dict(
                products=book_products,
                market_data=AutocallableMarketDataSet.from_dataframes(
                    spot_data=spot_df, vol_data=vol_df, rate_data=rate_df, futures_data=futures_df),
                hedge=hedge,
                engine_config=AutocallableEngineConfig(pricing_engine_type=selected_engine_type),
                underlying=underlying, start_date=start, end_date=end,
                calculate_event_probabilities=True, calculate_surfaces=False)
            if strategy is not None: kwargs["strategy"] = strategy
            if txn_cost is not None: kwargs["transaction_cost_model"] = txn_cost
            configs.append(BookAutocallableBacktestConfig(**kwargs))
    return configs, excluded

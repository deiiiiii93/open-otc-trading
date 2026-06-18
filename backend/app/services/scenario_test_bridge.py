"""Bridge: DB positions + per-position market snapshots -> QuantArk EquityPortfolio.

The single new abstraction for the scenario-test feature. Pure: it takes already
resolved per-position market snapshots (from risk_engine._pricing_position_context)
and assembles a QuantArk EquityPortfolio. No DB / profile logic lives here.
"""
from __future__ import annotations

from typing import Any

from app.schemas import PricingEnvironmentSnapshot
from app.services import quantark


def build_equity_portfolio(
    positions: list[Any],
    position_markets: dict[int, PricingEnvironmentSnapshot],
    *,
    portfolio_name: str,
    session: Any | None = None,
    engine_config_id: int | None = None,
) -> tuple[Any, list[dict]]:
    """Assemble an EquityPortfolio. Returns (portfolio, excluded).

    `excluded` is [{"position_id": id, "reason": str}] for positions dropped by the
    same policy risk runs use (`risk_pricing_exclusion`), plus zero-quantity guards.
    Pricing environments are keyed by underlying (one per underlying — the correct
    stress baseline); the first position seen for an underlying seeds its env.
    """
    quantark.ensure_quantark_path()
    from quantark.portfolio import EquityPortfolio
    from app.services.engine_configs import get_engine_config, position_with_engine, resolve_pricing_engine

    excluded: list[dict] = []
    buildable: list[tuple[Any, PricingEnvironmentSnapshot, str]] = []
    engine_config = get_engine_config(session, engine_config_id) if session is not None else None

    for position in positions:
        reason = quantark.risk_pricing_exclusion(position)
        if reason:
            excluded.append({"position_id": position.id, "reason": reason})
            continue
        if float(getattr(position, "quantity", 0) or 0) == 0.0:
            excluded.append({"position_id": position.id, "reason": "Position quantity is zero"})
            continue
        market = position_markets.get(position.id)
        if market is None:
            excluded.append({"position_id": position.id, "reason": "No market snapshot resolved"})
            continue
        buildable.append((position, market, str(position.underlying)))

    # Construct with an empty env map and populate per-underlying INSIDE the
    # per-position try below, so a bad market snapshot (build_pricing_env raising)
    # excludes only that position rather than aborting the whole run.
    portfolio = EquityPortfolio(portfolio_name=portfolio_name, pricing_environments={})
    for position, market, underlying in buildable:
        try:
            if underlying not in portfolio.pricing_environments:
                # NOTE: first-seen-env limitation — if two positions share the same
                # underlying but resolved different per-position market snapshots
                # (e.g. different r/q/vol rows), the first position's snapshot seeds
                # the shared env and later positions for that underlying are valued
                # against it. Bounded baseline inconsistency acceptable for v1's
                # one-env-per-underlying model.
                portfolio.pricing_environments[underlying] = quantark.build_pricing_env(market)
            resolved_engine = resolve_pricing_engine(position, engine_config)
            engine_position = position_with_engine(position, resolved_engine)
            product = quantark.build_product_for_position(engine_position, market)
            engine = quantark.build_engine_for_position(engine_position, market)
            created = portfolio.add_position(
                product=product,
                quantity=float(position.quantity),
                entry_price=0.0,  # unused for stress P&L: EquityPortfolio value is mark-to-market (get_market_value), not entry-relative
                underlying=underlying,
                engine=engine,
            )
            # Re-key under the DB position id so position_results correlate with
            # resolved_position_ids / the requested scope (QuantArk otherwise keys by a
            # generated UUID). _create_stressed_portfolio deepcopies positions, so the
            # DB-id key flows through to the engine's per-position result rows.
            portfolio.positions.pop(created.position_id, None)
            created.position_id = str(position.id)
            portfolio.positions[created.position_id] = created
        except Exception as exc:
            excluded.append({"position_id": position.id, "reason": f"build failed: {exc}"})
            continue
    return portfolio, excluded

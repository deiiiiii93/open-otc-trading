"""Pricing domain service.

Facade over ``quantark.py`` (ad-hoc product pricing) and
``position_pricer.py`` (portfolio position batch pricing).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import Product, PositionValuationRun
from app.schemas import PricingEnvironmentSnapshot
from app.services.position_pricer import MarketOverrides, price_portfolio_positions
from app.services.quantark import QuantArkResult, price_product as _quantark_price_product
from app.services.domains import positions as positions_svc
from app.services.domains.products import ProductSpec, compatibility_terms

_SECONDS_PER_POSITION = 0.3


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def estimate_price_seconds(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    session: Session | None = None,
) -> float:
    """Cost estimate for the synchronous ``price_positions`` service (CLI preview)."""
    if position_ids:
        return len(position_ids) * _SECONDS_PER_POSITION
    with _session_scope(session) as sess:
        rows = positions_svc.list_filtered(portfolio_id=portfolio_id, session=sess)
        return len(rows) * _SECONDS_PER_POSITION


def price_product(
    *,
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
) -> QuantArkResult:
    """Price an ad-hoc product spec under a market environment. Pure compute."""
    return _quantark_price_product(
        product_type=product_type,
        product_kwargs=product_kwargs,
        market=market,
        engine_name=engine_name,
    )


def price_product_reference(
    *,
    product_id: int | None,
    product: ProductSpec | None,
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
    session: Session | None = None,
) -> QuantArkResult:
    """Price a product-native reference through the QuantArk compatibility boundary."""
    with _session_scope(session) as sess:
        if product_id is not None:
            stored = sess.get(Product, product_id)
            if stored is None:
                raise LookupError(f"Product {product_id} not found")
            legacy = compatibility_terms(stored)
        elif product is not None:
            terms = dict(product.terms or {})
            if product.components:
                terms["components"] = list(product.components)
            legacy = {
                "product_type": product.quantark_class or product.product_family,
                "product_kwargs": terms,
            }
        else:
            raise ValueError("product_id or product is required")
    return price_product(
        product_type=legacy["product_type"],
        product_kwargs=legacy["product_kwargs"],
        market=market,
        engine_name=engine_name,
    )


def price_positions(
    *,
    portfolio_id: int,
    position_ids: list[int] | None = None,
    pricing_profile_id: int | None = None,
    valuation_date: datetime | None = None,
    market_overrides: dict[str, Any] | None = None,
    session: Session | None = None,
) -> PositionValuationRun:
    """Run a persisted pricing run on a portfolio's positions. HITL upstream."""
    overrides = MarketOverrides(**(market_overrides or {}))
    with _session_scope(session) as sess:
        run = price_portfolio_positions(
            sess,
            portfolio_id=portfolio_id,
            position_ids=position_ids or None,
            pricing_parameter_profile_id=pricing_profile_id,
            valuation_date=valuation_date,
            overrides=overrides,
        )
        sess.commit()
        return run


__all__ = [
    "estimate_price_seconds",
    "price_product",
    "price_product_reference",
    "price_positions",
]

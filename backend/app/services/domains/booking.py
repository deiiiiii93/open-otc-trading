from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models import Portfolio, PortfolioKind, Position
from ...schemas import PricingEnvironmentSnapshot
from ..audit import record_audit
from ..underlyings import link_position_underlying
from .position_terms import refresh_position_barrier_state, upsert_position_term_rows
from .products import ProductSpec, create_or_get_product, hydrate_position_product_fields


@dataclass(frozen=True)
class ProductBookingSpec(ProductSpec):
    pass


@dataclass(frozen=True)
class BookingRequest:
    portfolio_id: int
    product: ProductBookingSpec
    quantity: float
    entry_price: float = 0.0
    status: str = "open"
    source_trade_id: str | None = None
    source_row: int | None = None
    mapping_status: str = "supported"
    mapping_error: str | None = None
    source_payload: dict[str, Any] = field(default_factory=dict)
    rfq_id: int | None = None
    rfq_quote_version_id: int | None = None
    trade_effective_date: datetime | None = None
    engine_name: str | None = None
    engine_kwargs: dict[str, Any] = field(default_factory=dict)
    actor: str = "desk_user"
    source: str = "manual"
    position_kind: str = "otc"


# Snowball-family terms may arrive raw (synthesize) or complete (tidy) — let
# build_product auto-detect, so prebuilt stays False for these.
_SNOWBALL_BOOKING_TYPES = {"SnowballOption", "KnockOutResetSnowballOption"}

# Every family routed through the single build_product validation gate at booking
# time. The OTC import channel emits all of these; non-snowball families always
# arrive as complete QuantArk termsheets (from the import adapter or the
# build_product tool), so they take the prebuilt validate-and-wrap path.
_GATED_BOOKING_TYPES = _SNOWBALL_BOOKING_TYPES | {
    "PhoenixOption",
    "EuropeanVanillaOption",
    "AmericanOption",
    "CashOrNothingDigitalOption",
    "BarrierOption",
    "SingleSharkfinOption",
    "DoubleSharkfinOption",
    # parity pass: every build_product-supported family the desk can book is now
    # validated at booking, not just the OTC-emitted set. The option families
    # validate-and-wrap (prebuilt=True); the DeltaOne pair (below) is synthesized
    # so persistence-only metadata is carried as _otc_ rather than rejected.
    "Futures",
    "SpotInstrument",
    "AsianOption",
    "OneTouchOption",
    "RangeAccrualOption",
    "DoubleOneTouchOption",
}

# DeltaOne instruments (Spot/Futures) carry persistence-only metadata
# (instrument_code/exchange/contract_code) that is not part of the QuantArk
# constructor surface, and their `underlying` lives at the spec top-level rather
# than in `terms`. They are synthesized (prebuilt=False) so the builder threads
# `underlying` in and emits the metadata as _otc_ kwargs; validate-and-wrap
# (prebuilt=True) would feed the metadata straight to QuantArk, which rejects it.
_DELTAONE_BOOKING_TYPES = {"Futures", "SpotInstrument"}


def normalize_booking_product_spec(product: ProductBookingSpec) -> ProductBookingSpec:
    if product.quantark_class not in _GATED_BOOKING_TYPES:
        return product
    # build_product is the single producer/gate. Snowball-family terms may be raw
    # (synthesize) or complete (tidy) — auto-detect. The DeltaOne pair is also
    # synthesized (metadata -> _otc_, underlying threaded from the spec). Every
    # other gated family arrives as a complete QuantArk termsheet (OTC import
    # adapter / build_product tool), so validate-and-wrap it verbatim via
    # prebuilt=True (re-synthesis would drop the workbook's explicit dates and
    # per-date schedules).
    from .product_builders import build_product

    quantark_class = product.quantark_class or "SnowballOption"
    terms = dict(product.terms or {})
    if quantark_class in _DELTAONE_BOOKING_TYPES:
        # DeltaOne normalize must stay idempotent — the booking flow normalizes
        # twice (payload prep + book_position). Already-synthesized terms validate
        # as-is via the prebuilt path; raw desk terms (S0 + persistence metadata,
        # with `underlying` at the spec top-level) are synthesized, threading the
        # underlying in and carrying the metadata as _otc_ (which QuantArk rejects
        # bare, so the prebuilt attempt fails first and falls through to synthesis).
        built = build_product(quantark_class, dict(terms), prebuilt=True)
        if not built.ok:
            terms.setdefault("underlying", product.underlying)
            built = build_product(quantark_class, terms, prebuilt=False)
    else:
        built = build_product(
            quantark_class,
            terms,
            prebuilt=quantark_class not in _SNOWBALL_BOOKING_TYPES,
        )
    if built.missing:
        raise ValueError(
            f"Incomplete {product.quantark_class} booking terms; missing: "
            + ", ".join(built.missing)
        )
    if not built.ok:
        # Surface build_product's precise diagnostic now instead of returning
        # unchanged and letting the raw quad validator re-raise an opaque error
        # downstream.
        error = (built.validation or {}).get("error") or "invalid product terms"
        raise ValueError(
            f"Invalid {product.quantark_class} booking terms: {error}"
        )
    if built.product_kwargs == product.terms:
        return product
    return replace(product, terms=built.product_kwargs)


def validate_booking_product_spec(
    product: ProductBookingSpec, *, engine_name: str
) -> None:
    if product.quantark_class not in _GATED_BOOKING_TYPES:
        return
    from ..quantark import validate_quantark_build

    market = _validation_market_for_product(product)
    result = validate_quantark_build(
        product.quantark_class or "SnowballOption",
        dict(product.terms or {}),
        market,
        engine_name,
    )
    if not result.ok:
        raise ValueError(
            f"Invalid {product.quantark_class} booking terms: {result.error}"
        )


def prepare_booking_product_spec(
    product: ProductBookingSpec, *, engine_name: str | None
) -> ProductBookingSpec:
    normalized = normalize_booking_product_spec(product)
    if engine_name:
        validate_booking_product_spec(normalized, engine_name=engine_name)
    return normalized


def repair_invalid_snowball_booking_terms(session: Session) -> int:
    """Repair persisted manual Snowball terms that predate QuantArk validation."""
    rows = (
        session.query(Position)
        .filter(Position.product_type.in_(_SNOWBALL_BOOKING_TYPES))
        .all()
    )
    from .product_builders import build_product

    repaired = 0
    for position in rows:
        current = dict(position.product_kwargs or {})
        built = build_product(position.product_type or "SnowballOption", current)
        if not built.ok:
            # A schedule-less legacy shape can no longer be silently tidied
            # (see Task 3 hardening); skip rather than persist a malformed product.
            continue
        normalized = built.product_kwargs
        if normalized == current:
            continue
        stored_product = position.product
        product = ProductBookingSpec(
            asset_class=getattr(stored_product, "asset_class", None) or "equity",
            product_family=getattr(stored_product, "product_family", None)
            or "autocallable",
            quantark_class=position.product_type,
            underlying=position.underlying or getattr(stored_product, "underlying", None)
            or "UNKNOWN",
            currency=getattr(stored_product, "currency", None) or "CNY",
            terms=normalized,
            components=[],
            display_name=getattr(stored_product, "display_name", None),
            source_payload=getattr(stored_product, "source_payload", None) or {},
        )
        try:
            product = prepare_booking_product_spec(
                product, engine_name=position.engine_name or "SnowballQuadEngine"
            )
        except ValueError:
            continue
        stored = create_or_get_product(session, product, reuse=True)
        position.product = stored
        position.product_id = stored.id
        hydrate_position_product_fields(position)
        upsert_position_term_rows(session, position)
        refresh_position_barrier_state(session, position_id=position.id)
        repaired += 1
    return repaired


def set_position_currency(position) -> None:
    """Copy the booked product's currency onto the position (source of truth = the
    booked trade). When the product carries no explicit currency, fall back to the
    linked underlying's currency so positions default to their underlying rather
    than a hardcoded CNY. Soft-warn (never block) when the linked underlying
    disagrees — that only happens for quanto products, which are not yet supported."""
    product = getattr(position, "product", None)
    currency = getattr(product, "currency", None)
    underlying = getattr(position, "underlying_record", None)
    underlying_ccy = getattr(underlying, "currency", None)
    if not currency and underlying_ccy:
        currency = underlying_ccy
    position.currency = currency or "CNY"
    if underlying_ccy and underlying_ccy != currency:
        logger.warning(
            "Position and Underlying should have same currency for non-quanto "
            "products: position=%s underlying=%s",
            currency,
            underlying_ccy,
        )


def book_position(
    session: Session, request: BookingRequest, *, reuse_product: bool = True
) -> Position:
    portfolio = session.get(Portfolio, request.portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio {request.portfolio_id} not found")
    if portfolio.kind != PortfolioKind.CONTAINER.value:
        raise ValueError("Position booking is only available for container portfolios")

    product_spec = prepare_booking_product_spec(
        request.product, engine_name=request.engine_name
    )
    product = create_or_get_product(session, product_spec, reuse=reuse_product)
    position = Position(
        portfolio_id=portfolio.id,
        product_id=product.id,
        engine_name=request.engine_name,
        engine_kwargs=request.engine_kwargs,
        quantity=request.quantity,
        entry_price=request.entry_price,
        status=request.status,
        source_trade_id=request.source_trade_id,
        source_row=request.source_row,
        position_kind=_validate_position_kind(request.position_kind),
        mapping_status=request.mapping_status,
        mapping_error=request.mapping_error,
        source_payload=request.source_payload,
        rfq_id=request.rfq_id,
        rfq_quote_version_id=request.rfq_quote_version_id,
        trade_effective_date=request.trade_effective_date,
    )
    position.product = product
    hydrate_position_product_fields(position)
    session.add(position)
    session.flush()
    link_position_underlying(session, position, source=request.source)
    set_position_currency(position)

    upsert_position_term_rows(session, position)
    refresh_position_barrier_state(session, position_id=position.id)
    event_type = "position.booked" if request.source == "rfq" else "position.created"
    record_audit(
        session,
        event_type=event_type,
        actor=request.actor,
        subject_type="position",
        subject_id=position.id,
        payload={
            "source": request.source,
            "product_id": product.id,
            "portfolio_id": portfolio.id,
        },
    )
    return position


def _validate_position_kind(value: str) -> str:
    if value not in {"otc", "listed"}:
        raise ValueError("position_kind must be 'otc' or 'listed'")
    return value


def _validation_market_for_product(product: ProductBookingSpec) -> PricingEnvironmentSnapshot:
    defaults = PricingEnvironmentSnapshot()
    terms = dict(product.terms or {})
    spot = (
        _number_or_none(terms.get("initial_price"))
        or _number_or_none(terms.get("strike"))
        or defaults.spot
    )
    return PricingEnvironmentSnapshot(
        spot=spot,
        volatility=defaults.volatility,
        rate=defaults.rate,
        dividend_yield=defaults.dividend_yield,
        asset_name=product.underlying,
        currency=product.currency,
    )


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def repair_position_currencies(session: Session) -> int:
    """Repair position currencies that were stamped with a hardcoded default
    (CNY/USD) instead of the linked underlying's currency.

    Only touches positions whose current currency is one of the old hardcoded
    defaults and whose linked underlying has a different currency, so explicitly
    set non-default currencies are preserved.
    """
    linked = 0
    unlinked = (
        session.query(Position)
        .filter(Position.underlying_id.is_(None))
        .all()
    )
    for position in unlinked:
        row = link_position_underlying(session, position, source="repair")
        if row is not None:
            linked += 1
    if linked:
        session.flush()

    result = session.execute(
        text(
            """
            UPDATE positions
            SET currency = (
                SELECT i.currency FROM instruments i WHERE i.id = positions.underlying_id
            )
            WHERE underlying_id IS NOT NULL
              AND currency IN ('CNY', 'USD')
              AND currency != (
                  SELECT i.currency FROM instruments i WHERE i.id = positions.underlying_id
              )
            """
        )
    )
    repaired = result.rowcount or 0
    if linked:
        logger.info("Linked %s position(s) to underlying during currency repair", linked)
    if repaired:
        logger.info("Repaired currency on %s position(s)", repaired)
    return repaired

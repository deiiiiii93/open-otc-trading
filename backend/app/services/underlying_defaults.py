from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import UnderlyingPricingDefault
from .instruments import sync_hedge_tag
from .underlyings import (
    latest_akshare_close_by_symbol,
    list_underlyings,
    sync_underlyings_from_positions,
    update_underlying,
)


def list_underlying_defaults(session: Session) -> list[UnderlyingPricingDefault]:
    return list_underlyings(session, include_inactive=False)


def upsert_underlying_default(
    session: Session,
    *,
    underlying: str,
    rate=...,
    dividend_yield=...,
    volatility=...,
    notes=...,
) -> UnderlyingPricingDefault:
    cleaned = (underlying or "").strip()
    if not cleaned:
        raise ValueError("underlying must not be empty")
    fields = {}
    if rate is not ...:
        fields["rate"] = rate
    if dividend_yield is not ...:
        fields["dividend_yield"] = dividend_yield
    if volatility is not ...:
        fields["volatility"] = volatility
    if notes is not ...:
        fields["notes"] = notes
    return update_underlying(session, cleaned, fields)


def delete_underlying_default(session: Session, *, underlying: str) -> None:
    cleaned = (underlying or "").strip()
    row = (
        session.query(UnderlyingPricingDefault)
        .filter(UnderlyingPricingDefault.symbol == cleaned)
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"underlying not found: {cleaned}")
    row.status = "inactive"
    session.flush()
    sync_hedge_tag(session, row.id)
    session.flush()


def refresh_underlying_defaults_from_open_positions(
    session: Session,
) -> list[UnderlyingPricingDefault]:
    sync_underlyings_from_positions(session)
    return list_underlying_defaults(session)


def latest_akshare_close_by_underlying(
    session: Session, underlyings: list[str]
) -> dict[str, dict | None]:
    cleaned = [u.strip() for u in underlyings if u and u.strip()]
    if not cleaned:
        return {}
    result: dict[str, dict | None] = {u: None for u in cleaned}
    return latest_akshare_close_by_symbol(session, cleaned)

"""Single observation store for instrument prices.

All fetchers (market-data page, contract loader, xlsx importer, pricer
fallback) write through record_quote; consumers resolve with latest_quote.
Source is diagnostics, never priority — unification happens at write time.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from ..models import MarketQuote


def record_quote(
    session: Session,
    *,
    instrument_id: int,
    price: float,
    as_of: datetime,
    source: str,
    price_type: str = "close",
    market_data_profile_id: int | None = None,
    meta: dict | None = None,
) -> MarketQuote:
    row = MarketQuote(
        instrument_id=instrument_id,
        price=float(price),
        as_of=as_of,
        source=source,
        price_type=price_type,
        market_data_profile_id=market_data_profile_id,
        meta=meta or {},
    )
    session.add(row)
    session.flush()
    return row


def latest_quote(
    session: Session, instrument_id: int, *, as_of: datetime
) -> MarketQuote | None:
    """Deterministic: max(as_of <= valuation), tie-break max(id)."""
    return (
        session.query(MarketQuote)
        .filter(
            MarketQuote.instrument_id == instrument_id,
            MarketQuote.as_of <= as_of,
        )
        .order_by(MarketQuote.as_of.desc(), MarketQuote.id.desc())
        .first()
    )


def latest_quotes(
    session: Session, instrument_ids: Iterable[int], *, as_of: datetime
) -> dict[int, MarketQuote]:
    out: dict[int, MarketQuote] = {}
    for iid in set(instrument_ids):
        q = latest_quote(session, iid, as_of=as_of)
        if q is not None:
            out[iid] = q
    return out

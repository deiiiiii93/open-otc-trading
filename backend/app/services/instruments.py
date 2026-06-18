"""Instrument-master service layer.

Wraps + extends app.services.underlyings (which keeps ensure_underlying /
sync semantics every auto-registering channel relies on). New code should
import from here.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Instrument
from .underlyings import (  # re-exports: canonical names going forward
    ensure_underlying as ensure_instrument,
    list_underlyings,
    sync_underlyings_from_positions as sync_instruments_from_positions,
)

__all__ = [
    "ensure_instrument",
    "sync_instruments_from_positions",
    "list_underlyings",
    "list_instruments",
    "resolvable_market_data_instruments",
    "validate_instrument_terms",
]

_UNRESOLVABLE_STATUSES = {"expired", "retired"}


def resolvable_market_data_instruments(session: Session) -> list[Instrument]:
    """Instruments eligible for market-data fetch: AKShare mapping present and
    not end-of-life. Drafts ARE included — synced-but-uncurated instruments
    must still get quotes (the old active-only filter silently starved them).
    """
    return (
        session.query(Instrument)
        .filter(
            Instrument.akshare_symbol.isnot(None),
            Instrument.akshare_asset_class.isnot(None),
            Instrument.status.notin_(_UNRESOLVABLE_STATUSES),
        )
        .order_by(Instrument.symbol.asc())
        .all()
    )


def validate_instrument_terms(
    *, kind: str, strike: float | None, option_type: str | None
) -> None:
    """Service-level guard (no DB CHECK): listed options need full terms."""
    if kind == "listed_option":
        if strike is None:
            raise ValueError("listed_option requires strike")
        if option_type is None:
            raise ValueError("listed_option requires option_type")


def list_instruments(
    session: Session,
    *,
    kind: str | None = None,
    status: str | None = None,
    parent_id: int | None = None,
    series_root: str | None = None,
    search: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[Instrument]:
    q = session.query(Instrument)
    if kind:
        q = q.filter(Instrument.kind == kind)
    if status:
        q = q.filter(Instrument.status == status)
    if parent_id is not None:
        q = q.filter(Instrument.parent_id == parent_id)
    if series_root:
        q = q.filter(Instrument.series_root == series_root)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(Instrument.symbol.ilike(like))
    return q.order_by(Instrument.symbol.asc()).offset(offset).limit(limit).all()

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
    "set_instrument_tags",
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
    tag: str | None = None,
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
    q = q.order_by(Instrument.symbol.asc())
    if tag is None:
        return q.offset(offset).limit(limit).all()
    # Tag filtering happens in Python (JSON column, no portable SQL
    # containment query — matches list_portfolios(tags=...)). It MUST run
    # before offset/limit, or a tagged row sorted past the unfiltered page
    # would be silently dropped.
    wanted = tag.strip().lower()
    matched = [row for row in q.all() if wanted in {t.lower() for t in (row.tags or [])}]
    return matched[offset : offset + limit]


def _normalize_tags(tags: list[str]) -> list[str]:
    """Mirrors services/portfolio_service.py's _normalize_tags (private,
    duplicated rather than shared — it's a 10-line pure function, not worth a
    cross-module dependency for)."""
    seen: list[str] = []
    for t in tags or []:
        if not isinstance(t, str):
            raise ValueError(f"Tag must be a string, got {type(t).__name__}")
        s = t.strip().lower()
        if not s:
            continue
        if len(s) > 40:
            raise ValueError(f"Tag too long (>40 chars): {t!r}")
        if s not in seen:
            seen.append(s)
    return seen


def set_instrument_tags(session: Session, instrument_id: int, tags: list[str]) -> Instrument:
    row = session.get(Instrument, instrument_id)
    if row is None:
        raise LookupError(f"Instrument {instrument_id} not found")
    row.tags = _normalize_tags(tags)
    session.flush()
    return row

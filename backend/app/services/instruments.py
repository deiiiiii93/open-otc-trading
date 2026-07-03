"""Instrument-master service layer.

Wraps + extends app.services.underlyings (which keeps ensure_underlying /
sync semantics every auto-registering channel relies on). New code should
import from here.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import HedgeMapEntry, Instrument
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
    "sync_hedge_tag",
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
    # "hedge" is server-derived (see sync_hedge_tag) — strip any client-
    # supplied value before saving, then re-derive it from ground truth in
    # the same call so it can never be hand-added or hand-removed. A
    # non-string element is left in place (not filtered here) so
    # _normalize_tags still raises its proper ValueError for it below,
    # instead of this filter crashing on `.strip()` first.
    row.tags = _normalize_tags(
        [t for t in tags if not (isinstance(t, str) and t.strip().lower() == "hedge")]
    )
    session.flush()
    sync_hedge_tag(session, instrument_id)
    session.flush()
    return row


def sync_hedge_tag(session: Session, instrument_id: int) -> None:
    """Recompute the derived "hedge" tag for one instrument from ground
    truth (HedgeMapEntry active membership + the stock self-hedge default)
    and write it if it changed. Never touches any other tag.

    Truth mirrors the two existing eligibility checks exactly:
    hedging_legs.py::_active_instruments and
    services/domains/hedging.py::get_map's synthetic stock entry.
    """
    # This app's SessionLocal is configured with autoflush=False (house
    # convention — see database.py), so a caller that just mutated a
    # *different* row (e.g. backfilling a HedgeMapEntry.instrument_id) has
    # not necessarily flushed that change yet. The query below must see it,
    # so flush defensively rather than relying on every call site to
    # remember to do so first.
    session.flush()
    row = session.get(Instrument, instrument_id)
    if row is None:
        return
    # _active_instruments (the real MILP eligibility query,
    # hedging_legs.py::_active_instruments) builds its allowed-hedge set
    # PURELY from (exchange, contract_code) — it never reads
    # HedgeMapEntry.instrument_id at all. instrument_id is only a durable
    # bookkeeping reference (used by unmark's delete-by-id path); it is not
    # part of the real eligibility invariant. So this must match on key
    # alone too, not "direct instrument_id match OR legacy key fallback" —
    # a real duplicate Instrument row sharing a key with a *durably-linked*
    # active entry is just as eligible to the engine as the linked one.
    #
    # reconcile_status is only refreshed by mark()/unmark()/reconcile_map()
    # — a direct status edit on the Instrument itself (e.g. via PATCH)
    # doesn't touch it, so it can be stale "active" data at the moment this
    # runs. _active_instruments also always filters Instrument.status ==
    # "active" in addition to the map entry, so this must too, or an
    # instrument PATCHed to expired/inactive would keep advertising "hedge"
    # until some later reconcile_map() call happens to catch up.
    has_active_entry = bool(row.exchange and row.contract_code and row.status == "active") and (
        session.query(HedgeMapEntry.id)
        .filter(
            HedgeMapEntry.exchange == row.exchange,
            HedgeMapEntry.contract_code == row.contract_code,
            HedgeMapEntry.reconcile_status == "active",
        )
        .first()
        is not None
    )
    is_self_hedging_stock = row.kind == "stock" and row.status == "active"
    should_have_tag = has_active_entry or is_self_hedging_stock

    current = list(row.tags or [])
    has_tag = "hedge" in current
    if should_have_tag and not has_tag:
        row.tags = _normalize_tags([*current, "hedge"])
    elif not should_have_tag and has_tag:
        row.tags = _normalize_tags([t for t in current if t != "hedge"])

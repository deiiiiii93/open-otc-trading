# backend/app/services/domains/hedging.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import HedgeMapEntry, Instrument, Position
from ..hedging_legs import _family_for, _is_option
from ..hedging_loader import list_in_scope_underlyings
from ..hedging_universe import _code, resolve_families
from ..instruments import sync_hedge_tag
from ..quotes import latest_quotes


def _allowed_keys(session: Session, underlying_id: int) -> set[tuple[str, str]]:
    return {
        (e.exchange, e.contract_code)
        for e in session.query(
            HedgeMapEntry.exchange, HedgeMapEntry.contract_code
        ).filter(HedgeMapEntry.underlying_id == underlying_id)
    }


def _spec_roots(session: Session, underlying_id: int) -> set[str]:
    """Series roots the registry underlying routes to (loader scoping)."""
    row = session.get(Instrument, underlying_id)
    if row is None:
        return set()
    return {s.series_root for s in resolve_families(row.symbol, row.kind)}


def _owning_underlying_id(session: Session, inst: Instrument) -> int | None:
    """Registry underlying a catalog contract belongs to.

    Index/ETF families set parent_id to the registry row directly. Commodity
    futures have parent_id=None (physical underlier); their owning registry row
    is the in-scope underlying whose resolved family roots include the
    contract's series_root.
    """
    if inst.parent_id is not None:
        return inst.parent_id
    if inst.series_root is None:
        return None
    for u in list_in_scope_underlyings(session):
        if inst.series_root in {s.series_root for s in resolve_families(u.symbol, u.asset_class)}:
            return u.id
    return None


def list_instruments(
    session: Session,
    *,
    underlying_id: int,
    family: str | None = None,
    instrument_type: str | None = None,
    option_type: str | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    search: str | None = None,
    allowed_only: bool = False,
    status: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List catalog contracts in scope for a registry underlying.

    Scoping reproduces the old HedgeInstrument.underlying_id filter: the
    underlying's resolved family roots (from ``resolve_families``) select the
    Instrument rows the loader created for it. ``family`` filters by the
    derived (kind, series_root) — the new catalog has no family column.

    For stocks, the hedging candidate is the underlying itself (delta-one spot
    leg) and is allowed by default.
    """
    underlying = session.get(Instrument, underlying_id)
    if underlying is None:
        return []
    if underlying.kind == "stock":
        return _list_stock_candidates(
            session, underlying, family=family, search=search,
            status=status, allowed_only=allowed_only,
        )

    spec_roots = _spec_roots(session, underlying_id)
    if not spec_roots:
        return []

    # Legacy UI may still ask for status='live' (old HedgeInstrument status).
    effective_status = _canonical_status(status)
    q = session.query(Instrument).filter(
        Instrument.kind.in_(("futures", "listed_option")),
        Instrument.series_root.in_(spec_roots),
    )
    if family:
        # family encodes a kind: *_option → listed_option, else futures.
        q = q.filter(Instrument.kind == ("listed_option" if family.endswith("_option") else "futures"))
    if instrument_type:
        if instrument_type == "option":
            q = q.filter(Instrument.kind == "listed_option")
        else:
            q = q.filter(Instrument.kind == "futures")
    if option_type:
        q = q.filter(Instrument.option_type == option_type)
    if strike_min is not None:
        q = q.filter(Instrument.strike >= strike_min)
    if strike_max is not None:
        q = q.filter(Instrument.strike <= strike_max)
    if effective_status:
        q = q.filter(Instrument.status == effective_status)
    if search:
        q = q.filter(Instrument.contract_code.ilike(f"%{search}%"))
    if allowed_only:
        # Filter in SQL (not post-pagination) so offset/limit stay correct.
        q = q.join(
            HedgeMapEntry,
            (HedgeMapEntry.underlying_id == underlying_id)
            & (HedgeMapEntry.exchange == Instrument.exchange)
            & (HedgeMapEntry.contract_code == Instrument.contract_code),
        )
    q = q.order_by(Instrument.expiry, Instrument.strike, Instrument.contract_code)
    rows = q.offset(offset).limit(min(max(1, limit), 5000)).all()
    allowed = _allowed_keys(session, underlying_id)
    quotes = latest_quotes(session, [r.id for r in rows], as_of=datetime.utcnow())
    return [_instrument_dict(r, underlying_id, allowed, quotes) for r in rows]


def _canonical_status(status: str | None) -> str | None:
    if status is None:
        return None
    if status.lower() == "live":
        return "active"
    return status


def _list_stock_candidates(
    session: Session,
    underlying: Instrument,
    *,
    family: str | None,
    search: str | None,
    status: str | None,
    allowed_only: bool,
) -> list[dict[str, Any]]:
    """A stock's only hedge candidate is itself; it is allowed by default."""
    if family and family != "stock":
        return []
    if allowed_only and underlying.status != "active":
        return []
    effective_status = _canonical_status(status)
    if effective_status and underlying.status != effective_status:
        return []
    if search:
        needle = search.strip().lower()
        haystack = " ".join(
            x for x in (underlying.symbol, underlying.display_name or "") if x
        ).lower()
        if needle not in haystack:
            return []
    quotes = latest_quotes(session, [underlying.id], as_of=datetime.utcnow())
    return [_stock_candidate_dict(underlying, quotes)]


def _stock_candidate_dict(underlying: Instrument, quotes: dict) -> dict[str, Any]:
    q = quotes.get(underlying.id)
    return {
        "id": underlying.id,
        "underlying_id": underlying.id,
        "family": "stock",
        "series_root": "",
        "exchange": underlying.exchange or "",
        "contract_code": _code(underlying.symbol),
        "instrument_type": "spot",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "multiplier": 1.0,
        "last_price": q.price if q is not None else None,
        "status": underlying.status,
        "allowed": True,
    }


def _instrument_dict(
    r: Instrument, underlying_id: int, allowed: set[tuple[str, str]], quotes: dict
) -> dict[str, Any]:
    q = quotes.get(r.id)
    return {
        "id": r.id,
        "underlying_id": underlying_id,
        "family": _family_for(r),
        "series_root": r.series_root,
        "exchange": r.exchange,
        "contract_code": r.contract_code,
        "instrument_type": "option" if _is_option(r) else "future",
        "option_type": r.option_type,
        "strike": r.strike,
        "expiry": r.expiry.isoformat() if r.expiry else None,
        "multiplier": r.multiplier,
        "last_price": q.price if q is not None else None,
        "status": r.status,
        "allowed": (r.exchange, r.contract_code) in allowed,
    }


def mark(session: Session, instrument_ids: list[int], *, actor: str | None = None) -> list[HedgeMapEntry]:
    created: list[HedgeMapEntry] = []
    now = datetime.utcnow()
    # Flush pending inserts so that the existence check below sees previously
    # added entries within the same transaction (idempotency guard).
    session.flush()
    for inst in (
        session.query(Instrument)
        .filter(Instrument.id.in_(instrument_ids))
        .all()
    ):
        underlying_id = _owning_underlying_id(session, inst)
        if underlying_id is None:
            continue
        existing = (
            session.query(HedgeMapEntry)
            .filter(
                HedgeMapEntry.underlying_id == underlying_id,
                HedgeMapEntry.exchange == inst.exchange,
                HedgeMapEntry.contract_code == inst.contract_code,
            )
            .one_or_none()
        )
        if existing is not None:
            # Backfill the durable instrument link on a pre-existing entry.
            # Instrument has no uniqueness constraint on (exchange,
            # contract_code), so other instrument rows may have been
            # matching this legacy (instrument_id IS NULL) entry via
            # sync_hedge_tag's fallback — once it's bound to this specific
            # inst.id, that fallback no longer applies to them, so they must
            # be resynced too or they can keep a stale "hedge" tag.
            other_matching_ids: set[int] = set()
            if existing.instrument_id is None:
                other_matching_ids = {
                    iid for (iid,) in session.query(Instrument.id)
                    .filter(
                        Instrument.exchange == existing.exchange,
                        Instrument.contract_code == existing.contract_code,
                        Instrument.id != inst.id,
                    )
                }
                existing.instrument_id = inst.id
                # sync_hedge_tag flushes internally, but flush explicitly
                # here too — this session is autoflush=False, and without
                # this the resync loop below would still see the pre-backfill
                # (instrument_id IS NULL) state for `existing`.
                session.flush()
            sync_hedge_tag(session, inst.id)
            for other_id in other_matching_ids:
                sync_hedge_tag(session, other_id)
            continue
        entry = HedgeMapEntry(
            underlying_id=underlying_id,
            instrument_id=inst.id,
            exchange=inst.exchange,
            contract_code=inst.contract_code,
            family=_family_for(inst),
            series_root=inst.series_root,
            instrument_type="option" if _is_option(inst) else "future",
            option_type=inst.option_type,
            strike=inst.strike,
            expiry=inst.expiry,
            reconcile_status="active" if inst.status == "active" else "stale",
            marked_by=actor,
            marked_at=now,
        )
        session.add(entry)
        session.flush()
        sync_hedge_tag(session, inst.id)
        created.append(entry)
    return created


def unmark(
    session: Session,
    *,
    instrument_ids: list[int] | None = None,
    map_entry_ids: list[int] | None = None,
) -> int:
    affected: set[int] = set()
    removed = 0
    if map_entry_ids:
        # Resolve BOTH durably-linked rows and legacy (instrument_id IS NULL)
        # rows matched only by (exchange, contract_code) — sync_hedge_tag
        # treats the legacy match as real ground truth too (Task 1), so a
        # legacy row's instrument must not be skipped here just because the
        # id column that would normally identify it is NULL.
        rows_being_deleted = (
            session.query(HedgeMapEntry.instrument_id, HedgeMapEntry.exchange, HedgeMapEntry.contract_code)
            .filter(HedgeMapEntry.id.in_(map_entry_ids))
            .all()
        )
        for instrument_id, exchange, contract_code in rows_being_deleted:
            if instrument_id is not None:
                affected.add(instrument_id)
            elif exchange and contract_code:
                affected.update(
                    iid for (iid,) in session.query(Instrument.id)
                    .filter(Instrument.exchange == exchange, Instrument.contract_code == contract_code)
                )
        removed += (
            session.query(HedgeMapEntry)
            .filter(HedgeMapEntry.id.in_(map_entry_ids))
            .delete(synchronize_session=False)
        )
    if instrument_ids:
        # Prefer the durable instrument link; fall back to (exchange,
        # contract_code) display columns for entries not yet backfilled.
        affected.update(instrument_ids)
        removed += (
            session.query(HedgeMapEntry)
            .filter(HedgeMapEntry.instrument_id.in_(instrument_ids))
            .delete(synchronize_session=False)
        )
        keys = [
            (i.exchange, i.contract_code)
            for i in session.query(Instrument)
            .filter(Instrument.id.in_(instrument_ids))
            .all()
        ]
        for exch, code in keys:
            if exch and code:
                # Instrument has no uniqueness constraint on (exchange,
                # contract_code) — other instrument rows besides the ones in
                # instrument_ids may also be matching this legacy row via
                # sync_hedge_tag's fallback. Deleting it can drop their
                # eligibility too, so resync all of them, not just the
                # requested ids.
                affected.update(
                    iid for (iid,) in session.query(Instrument.id)
                    .filter(Instrument.exchange == exch, Instrument.contract_code == code)
                )
            removed += (
                session.query(HedgeMapEntry)
                .filter(
                    HedgeMapEntry.instrument_id.is_(None),
                    HedgeMapEntry.exchange == exch,
                    HedgeMapEntry.contract_code == code,
                )
                .delete(synchronize_session=False)
            )
    session.flush()
    for instrument_id in affected:
        sync_hedge_tag(session, instrument_id)
    return removed


def _open_position_counts(session: Session) -> dict[int, int]:
    """Return {underlying_id: count} for open OTC positions that have an underlying_id."""
    from sqlalchemy import func as sa_func
    rows = (
        session.query(Position.underlying_id, sa_func.count(Position.id))
        .filter(
            Position.status == "open",
            Position.position_kind == "otc",
            Position.underlying_id.isnot(None),
        )
        .group_by(Position.underlying_id)
        .all()
    )
    return {uid: cnt for uid, cnt in rows}


def get_map(session: Session, *, underlying_id: int | None = None) -> list[dict[str, Any]]:
    """Return hedge-map groups per underlying.

    Each group includes:
    - ``open_position_count``: open positions whose underlying_id matches this group.
    - ``underlying_symbol``: the symbol of the underlying Instrument row.
    - ``allowed``: list of map entries (may be empty); each entry includes
      ``instrument_id`` so the frontend can key into quotesByInstrumentId.

    The response also includes exposure-only groups — underlyings that have open
    positions but zero map entries — so the left rail can surface the
    ``0 allowed`` warning case even before any hedges are marked.
    """
    q = session.query(HedgeMapEntry)
    if underlying_id is not None:
        q = q.filter(HedgeMapEntry.underlying_id == underlying_id)

    position_counts = _open_position_counts(session)

    grouped: dict[int, dict[str, Any]] = {}
    for e in q.order_by(HedgeMapEntry.underlying_id, HedgeMapEntry.contract_code).all():
        bucket = grouped.setdefault(
            e.underlying_id,
            {"underlying_id": e.underlying_id, "underlying_symbol": "", "entries": [], "open_position_count": 0},
        )
        bucket["entries"].append({
            "id": e.id,
            "instrument_id": e.instrument_id,
            "exchange": e.exchange,
            "contract_code": e.contract_code,
            "family": e.family,
            "series_root": e.series_root,
            "instrument_type": e.instrument_type,
            "option_type": e.option_type,
            "strike": e.strike,
            "expiry": e.expiry.isoformat() if e.expiry else None,
            "reconcile_status": e.reconcile_status,
        })

    # Fill in position counts for groups that have map entries.
    for uid, bucket in grouped.items():
        bucket["open_position_count"] = position_counts.get(uid, 0)

    # Add exposure-only groups (open positions, zero map entries) so the rail
    # shows them with the 0-allowed warning even before any hedges are marked.
    if underlying_id is None:
        for uid, count in position_counts.items():
            if uid not in grouped:
                grouped[uid] = {
                    "underlying_id": uid,
                    "underlying_symbol": "",
                    "entries": [],
                    "open_position_count": count,
                }
    elif underlying_id in position_counts and underlying_id not in grouped:
        grouped[underlying_id] = {
            "underlying_id": underlying_id,
            "underlying_symbol": "",
            "entries": [],
            "open_position_count": position_counts[underlying_id],
        }

    # Resolve underlying_symbol in a single query (no N+1).
    all_uids = list(grouped.keys())
    if all_uids:
        meta_map: dict[int, tuple[str, str]] = {
            row.id: (row.symbol, row.kind)
            for row in session.query(Instrument.id, Instrument.symbol, Instrument.kind)
            .filter(Instrument.id.in_(all_uids))
            .all()
        }
        for uid, bucket in grouped.items():
            symbol, kind = meta_map.get(uid, ("", ""))
            bucket["underlying_symbol"] = symbol
            if kind == "stock":
                # A stock is its own allowed hedge by default; surface it in the
                # map so the left rail shows the self-candidate.
                bucket["entries"].append({
                    "id": -uid,
                    "instrument_id": uid,
                    "exchange": "",
                    "contract_code": _code(symbol),
                    "family": "stock",
                    "series_root": "",
                    "instrument_type": "spot",
                    "option_type": None,
                    "strike": None,
                    "expiry": None,
                    "reconcile_status": "active",
                })

    return list(grouped.values())


def purge_stale(session: Session, *, underlying_id: int) -> int:
    return (
        session.query(HedgeMapEntry)
        .filter(
            HedgeMapEntry.underlying_id == underlying_id,
            HedgeMapEntry.reconcile_status == "stale",
        )
        .delete(synchronize_session=False)
    )


def underlyings_overview(session: Session) -> list[dict[str, Any]]:
    # Reuse the loader's single source of truth for the in-scope set so the
    # rail and the loader never diverge.
    underlyings = list_in_scope_underlyings(session)
    out: list[dict[str, Any]] = []
    for u in underlyings:
        # Stocks are their own hedge candidate (delta-one spot leg) and are
        # allowed by default.
        if u.asset_class == "stock":
            out.append({
                "underlying_id": u.id,
                "symbol": u.symbol,
                "display_name": u.display_name,
                "asset_class": u.asset_class,
                "unresolvable": False,
                "last_loaded_at": None,
                "stale_count": 0,
                "families": [
                    {"family": "stock", "total": 1, "allowed": 1}
                ] if u.status not in {"expired", "retired"} else [],
            })
            continue

        spec_roots = {s.series_root for s in resolve_families(u.symbol, u.asset_class)}
        # Counts reflect the live, markable universe; stale marks (expired
        # contracts) are surfaced separately via stale_count.
        catalog = (
            session.query(Instrument)
            .filter(
                Instrument.kind.in_(("futures", "listed_option")),
                Instrument.series_root.in_(spec_roots),
            )
            .all()
            if spec_roots
            else []
        )
        live_instruments = [c for c in catalog if c.status == "active"]
        # last_loaded_at uses ALL instruments (active + expired) so that an
        # underlying whose entire universe just rolled to expired still reports
        # a non-null timestamp from the most recent successful load.
        all_instruments = catalog
        allowed = _allowed_keys(session, u.id)
        families: dict[str, dict[str, int]] = {}
        last_loaded: datetime | None = None
        for inst in all_instruments:
            if inst.loaded_at and (last_loaded is None or inst.loaded_at > last_loaded):
                last_loaded = inst.loaded_at
        for inst in live_instruments:
            fam = families.setdefault(_family_for(inst), {"total": 0, "allowed": 0})
            fam["total"] += 1
            if (inst.exchange, inst.contract_code) in allowed:
                fam["allowed"] += 1
        stale_count = (
            session.query(HedgeMapEntry)
            .filter(
                HedgeMapEntry.underlying_id == u.id,
                HedgeMapEntry.reconcile_status == "stale",
            )
            .count()
        )
        out.append({
            "underlying_id": u.id,
            "symbol": u.symbol,
            "display_name": u.display_name,
            "asset_class": u.asset_class,
            "unresolvable": resolve_families(u.symbol, u.asset_class) == [],
            "last_loaded_at": last_loaded.isoformat() if last_loaded else None,
            "stale_count": stale_count,
            "families": [
                {"family": k, "total": v["total"], "allowed": v["allowed"]}
                for k, v in sorted(families.items())
            ],
        })
    return out

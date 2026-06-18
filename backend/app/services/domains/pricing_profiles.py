"""Pricing parameter profile domain service (reads + agent write facade)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import String, cast, desc, or_
from sqlalchemy.orm import Session, selectinload

from app import database
from app.models import (
    FxRate,
    Position,
    PositionValuationRun,
    PricingParameterProfile,
    PricingParameterRow,
    RiskRun,
)
from app.services.audit import record_audit
from app.services.instruments import ensure_instrument

from ._errors import DomainWriteError
from ._validation import invalid_param_reason


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    """Yield a session; caller is responsible for commit when writing."""
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def list_profiles(
    *,
    query: str | None = None,
    limit: int = 20,
    session: Session | None = None,
) -> list[PricingParameterProfile]:
    """Return stored pricing parameter profiles, newest first.

    ``query`` is a case-insensitive substring over profile name, source type,
    and valuation date text. It exists for agent-side name resolution, e.g.
    resolving "Default 2026-05-27" into a concrete profile id.
    """
    capped_limit = max(1, min(int(limit), 100))
    with _session_scope(session) as sess:
        stmt = sess.query(PricingParameterProfile).options(
            selectinload(PricingParameterProfile.rows)
        )
        cleaned = (query or "").strip()
        if cleaned:
            pattern = f"%{cleaned}%"
            stmt = stmt.filter(
                or_(
                    PricingParameterProfile.name.ilike(pattern),
                    PricingParameterProfile.source_type.ilike(pattern),
                    cast(PricingParameterProfile.valuation_date, String).ilike(pattern),
                )
            )
        return (
            stmt.order_by(
                desc(PricingParameterProfile.valuation_date),
                desc(PricingParameterProfile.created_at),
                desc(PricingParameterProfile.id),
            )
            .limit(capped_limit)
            .all()
        )


def get_profile(
    *, profile_id: int, session: Session | None = None
) -> PricingParameterProfile | None:
    """Return one ``PricingParameterProfile`` by id, or ``None`` if not found."""
    with _session_scope(session) as sess:
        return (
            sess.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .filter(PricingParameterProfile.id == profile_id)
            .one_or_none()
        )


__all__ = ["list_profiles", "get_profile", "create_profile", "update_profile", "upsert_rows", "delete_rows", "delete_profile", "ARCHIVED_SOURCE_TYPE"]


# --- write facade -----------------------------------------------------------

ARCHIVED_SOURCE_TYPE = "default_underlying_archived"
PARAM_FIELDS = ("rate", "dividend_yield", "volatility")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized_pair(row: dict[str, Any]) -> tuple[str, str]:
    return (_clean(row.get("source_trade_id")).lower(), _clean(row.get("symbol")).lower())


def _validate_row_inputs(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise DomainWriteError("no_rows")
    blank = [i for i, row in enumerate(rows) if not _clean(row.get("symbol"))]
    if blank:
        raise DomainWriteError("blank_symbol", {"row_indexes": blank})
    empty = [
        i
        for i, row in enumerate(rows)
        if all(row.get(field) is None for field in PARAM_FIELDS)
    ]
    if empty:
        raise DomainWriteError("empty_row", {"row_indexes": empty})
    invalid = [
        {"row_index": i, "field": field, "reason": reason}
        for i, row in enumerate(rows)
        for field in PARAM_FIELDS
        if row.get(field) is not None
        and (reason := invalid_param_reason(field, row[field])) is not None
    ]
    if invalid:
        raise DomainWriteError("invalid_value", {"rows": invalid})
    seen: set[tuple[str, str]] = set()
    dupes: list[list[str]] = []
    for row in rows:
        pair = _normalized_pair(row)
        if pair in seen and list(pair) not in dupes:
            dupes.append(list(pair))
        seen.add(pair)
    if dupes:
        raise DomainWriteError("duplicate_rows", {"pairs": dupes})


def _positions_by_trade_id(sess: Session, rows: list[dict[str, Any]]) -> dict[str, Position]:
    trade_ids = {_clean(row.get("source_trade_id")) for row in rows}
    trade_ids.discard("")
    if not trade_ids:
        return {}
    positions = sess.query(Position).filter(Position.source_trade_id.in_(trade_ids)).all()
    return {p.source_trade_id: p for p in positions if p.source_trade_id}


def _instrument_id_for_row(
    sess: Session, row: dict[str, Any], positions: dict[str, Position]
) -> int:
    """Mirror the xlsx importer: booked position's underlying_id, else draft instrument."""
    position = positions.get(_clean(row.get("source_trade_id")))
    if position is not None and position.underlying_id is not None:
        return position.underlying_id
    instrument = ensure_instrument(
        sess, _clean(row.get("symbol")), source="pricing_profile", status="draft"
    )
    sess.flush()
    return instrument.id


def _reload_profile(sess: Session, profile_id: int) -> PricingParameterProfile:
    return (
        sess.query(PricingParameterProfile)
        .options(selectinload(PricingParameterProfile.rows))
        .filter(PricingParameterProfile.id == profile_id)
        .one()
    )


def create_profile(
    *,
    rows: list[dict[str, Any]],
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PricingParameterProfile:
    """Create an agent-authored r/q/vol profile (``source_type="agent"``).

    Rows are trade-keyed when ``source_trade_id`` is non-empty, otherwise
    underlying-level. Spots are deliberately NOT accepted — observations
    live in the quote store.
    """
    _validate_row_inputs(rows)
    effective_valuation = valuation_date or datetime.utcnow()
    with _session_scope(session) as sess:
        profile = PricingParameterProfile(
            name=_clean(name) or f"Agent Pricing Parameters {effective_valuation:%Y-%m-%d}",
            valuation_date=effective_valuation,
            source_type="agent",
            source_path=None,
            status="completed",
            summary={"row_count": len(rows), "created_by": actor},
        )
        sess.add(profile)
        sess.flush()
        positions = _positions_by_trade_id(sess, rows)
        for row in rows:
            sess.add(
                PricingParameterRow(
                    profile_id=profile.id,
                    source_trade_id=_clean(row.get("source_trade_id")),
                    symbol=_clean(row.get("symbol")),
                    instrument_id=_instrument_id_for_row(sess, row, positions),
                    rate=row.get("rate"),
                    dividend_yield=row.get("dividend_yield"),
                    volatility=row.get("volatility"),
                    source_payload={"created_by": actor},
                )
            )
        record_audit(
            sess,
            event_type="pricing_parameter_profile.created",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"row_count": len(rows)},
        )
        sess.commit()
        return _reload_profile(sess, profile.id)


def _mutable_profile(sess: Session, profile_id: int) -> PricingParameterProfile:
    """Load a profile for mutation; archived profiles are audit artifacts."""
    profile = sess.get(PricingParameterProfile, profile_id)
    if profile is None:
        raise DomainWriteError("profile_not_found", {"profile_id": profile_id})
    if profile.source_type == ARCHIVED_SOURCE_TYPE:
        raise DomainWriteError("profile_archived", {"profile_id": profile_id})
    return profile


def update_profile(
    *,
    profile_id: int,
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PricingParameterProfile:
    """Rename / re-date a profile. Rows are untouched (see upsert/delete rows)."""
    if name is None and valuation_date is None:
        raise DomainWriteError("no_fields")
    if name is not None and not _clean(name):
        raise DomainWriteError("blank_name")
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        changes: dict[str, Any] = {}
        if name is not None:
            profile.name = _clean(name)
            changes["name"] = profile.name
        if valuation_date is not None:
            profile.valuation_date = valuation_date
            changes["valuation_date"] = valuation_date.isoformat()
        record_audit(
            sess,
            event_type="pricing_parameter_profile.updated",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload=changes,
        )
        sess.commit()
        return _reload_profile(sess, profile.id)


def upsert_rows(
    *,
    profile_id: int,
    rows: list[dict[str, Any]],
    actor: str = "agent",
    session: Session | None = None,
) -> tuple[PricingParameterProfile, dict[str, int]]:
    """Upsert rows by normalized (source_trade_id, symbol).

    Matched rows overwrite only the provided (non-null) fields; clearing a
    field means delete the row and recreate it. Unmatched rows insert with
    the same instrument resolution as create_profile.
    """
    _validate_row_inputs(rows)
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        existing = (
            sess.query(PricingParameterRow)
            .filter(PricingParameterRow.profile_id == profile.id)
            .all()
        )
        by_pair = {
            (_clean(row.source_trade_id).lower(), _clean(row.symbol).lower()): row
            for row in existing
        }
        positions = _positions_by_trade_id(sess, rows)
        updated = inserted = 0
        for row in rows:
            match = by_pair.get(_normalized_pair(row))
            if match is not None:
                for field in PARAM_FIELDS:
                    if row.get(field) is not None:
                        setattr(match, field, row[field])
                updated += 1
                continue
            sess.add(
                PricingParameterRow(
                    profile_id=profile.id,
                    source_trade_id=_clean(row.get("source_trade_id")),
                    symbol=_clean(row.get("symbol")),
                    instrument_id=_instrument_id_for_row(sess, row, positions),
                    rate=row.get("rate"),
                    dividend_yield=row.get("dividend_yield"),
                    volatility=row.get("volatility"),
                    source_payload={"created_by": actor},
                )
            )
            inserted += 1
        profile.summary = {**(profile.summary or {}), "row_count": len(existing) + inserted}
        record_audit(
            sess,
            event_type="pricing_parameter_profile.rows_upserted",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"updated": updated, "inserted": inserted},
        )
        sess.commit()
        return _reload_profile(sess, profile.id), {"updated": updated, "inserted": inserted}


def delete_rows(
    *,
    profile_id: int,
    row_ids: list[int],
    actor: str = "agent",
    session: Session | None = None,
) -> tuple[PricingParameterProfile, int]:
    """Delete rows by id; refuses wholesale if any id is not in the profile."""
    if not row_ids:
        raise DomainWriteError("no_rows")
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        found = (
            sess.query(PricingParameterRow)
            .filter(
                PricingParameterRow.profile_id == profile.id,
                PricingParameterRow.id.in_(row_ids),
            )
            .all()
        )
        missing = sorted(set(row_ids) - {row.id for row in found})
        if missing:
            raise DomainWriteError("rows_not_in_profile", {"row_ids": missing})
        total = (
            sess.query(PricingParameterRow)
            .filter(PricingParameterRow.profile_id == profile.id)
            .count()
        )
        for row in found:
            sess.delete(row)
        profile.summary = {**(profile.summary or {}), "row_count": total - len(found)}
        record_audit(
            sess,
            event_type="pricing_parameter_profile.rows_deleted",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"row_ids": sorted(set(row_ids)), "deleted": len(found)},
        )
        sess.commit()
        return _reload_profile(sess, profile.id), len(found)


def delete_profile(
    *,
    profile_id: int,
    actor: str = "agent",
    session: Session | None = None,
) -> dict[str, Any]:
    """Delete an unreferenced profile (cascades rows). IRREVERSIBLE.

    Refuses when any position_valuation_run, risk_run, or fx_rate snapshot
    references the profile — those records' audit trails depend on it.
    """
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        valuation_run_ids = [
            run_id
            for (run_id,) in sess.query(PositionValuationRun.id).filter(
                PositionValuationRun.pricing_parameter_profile_id == profile.id
            )
        ]
        risk_run_ids = [
            run_id
            for (run_id,) in sess.query(RiskRun.id).filter(
                RiskRun.pricing_parameter_profile_id == profile.id
            )
        ]
        fx_rate_ids = [
            rate_id
            for (rate_id,) in sess.query(FxRate.id).filter(
                FxRate.pricing_parameter_profile_id == profile.id
            )
        ]
        if valuation_run_ids or risk_run_ids or fx_rate_ids:
            raise DomainWriteError(
                "profile_referenced_by_runs",
                {
                    "position_valuation_run_ids": valuation_run_ids,
                    "risk_run_ids": risk_run_ids,
                    "fx_rate_ids": fx_rate_ids,
                },
            )
        row_count = (
            sess.query(PricingParameterRow)
            .filter(PricingParameterRow.profile_id == profile.id)
            .count()
        )
        name = profile.name
        record_audit(
            sess,
            event_type="pricing_parameter_profile.deleted",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"name": name, "row_count": row_count},
        )
        sess.delete(profile)
        sess.commit()
        return {"deleted_profile_id": profile_id, "deleted_row_count": row_count, "name": name}

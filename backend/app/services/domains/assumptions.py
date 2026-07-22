"""Assumption-set domain facade: reads + pipeline-only writes.

AssumptionSets stay DERIVED — the only write path is build_assumptions_set
(instrument defaults -> inherited profile rows, with per-field provenance).
Direct AssumptionRow writes are deliberately not exposed.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import String, cast, desc, or_
from sqlalchemy.orm import Session, selectinload

from app import database
from app.models import AssumptionSet, Instrument
from app.services.assumptions import build_assumptions_set
from app.services.audit import record_audit
from app.services.instruments import ensure_instrument
from app.services.term_structure import validate_curve

from ._errors import DomainWriteError
from ._validation import invalid_param_reason

DEFAULT_FIELDS = ("rate", "dividend_yield", "volatility")


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def list_sets(
    *,
    query: str | None = None,
    limit: int = 20,
    session: Session | None = None,
) -> list[AssumptionSet]:
    """Stored assumption sets, newest first; query matches name/status/date."""
    capped = max(1, min(int(limit), 100))
    with _session_scope(session) as sess:
        stmt = sess.query(AssumptionSet).options(selectinload(AssumptionSet.rows))
        cleaned = (query or "").strip()
        if cleaned:
            pattern = f"%{cleaned}%"
            stmt = stmt.filter(
                or_(
                    AssumptionSet.name.ilike(pattern),
                    AssumptionSet.status.ilike(pattern),
                    cast(AssumptionSet.valuation_date, String).ilike(pattern),
                )
            )
        return (
            stmt.order_by(
                desc(AssumptionSet.valuation_date),
                desc(AssumptionSet.created_at),
                desc(AssumptionSet.id),
            )
            .limit(capped)
            .all()
        )


def get_set(*, set_id: int, session: Session | None = None) -> AssumptionSet | None:
    with _session_scope(session) as sess:
        return (
            sess.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .filter(AssumptionSet.id == set_id)
            .one_or_none()
        )


def get_instrument_defaults(
    *,
    symbols: list[str] | None = None,
    limit: int = 50,
    session: Session | None = None,
) -> list[Instrument]:
    """Instrument r/q/vol defaults (the assumption pipeline's first source)."""
    capped = max(1, min(int(limit), 200))
    with _session_scope(session) as sess:
        stmt = sess.query(Instrument)
        cleaned = [s.strip() for s in (symbols or []) if s and s.strip()]
        if cleaned:
            stmt = stmt.filter(Instrument.symbol.in_(cleaned))
        return stmt.order_by(Instrument.symbol.asc()).limit(capped).all()


def set_instrument_defaults(
    *,
    symbol: str,
    rate: float | None = None,
    dividend_yield: float | None = None,
    volatility: float | None = None,
    clear: list[str] | tuple[str, ...] = (),
    rate_curve: list[dict] | None = None,
    dividend_yield_curve: list[dict] | None = None,
    volatility_curve: list[dict] | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> Instrument:
    """Set/clear an instrument's baseline r/q/vol (ensure-creates a draft row).

    Provided non-null values set; ``clear`` entries null out. The same field
    in both is a refusal, not last-wins.
    """
    if not str(symbol or "").strip():
        raise DomainWriteError("blank_symbol")
    provided = {
        field: value
        for field, value in (
            ("rate", rate),
            ("dividend_yield", dividend_yield),
            ("volatility", volatility),
        )
        if value is not None
    }
    clear_fields = [str(field).strip() for field in (clear or []) if str(field).strip()]
    unknown = sorted(set(clear_fields) - set(DEFAULT_FIELDS))
    if unknown:
        raise DomainWriteError("invalid_clear_field", {"fields": unknown})
    conflicting = sorted(set(clear_fields) & set(provided))
    if conflicting:
        raise DomainWriteError("field_set_and_cleared", {"fields": conflicting})
    invalid = {
        field: reason
        for field, value in provided.items()
        if (reason := invalid_param_reason(field, value)) is not None
    }
    if invalid:
        raise DomainWriteError("invalid_value", {"fields": invalid})
    curve_updates: dict[str, list[dict] | None] = {}
    for column, value, positive in (
        ("rate_curve", rate_curve, False),
        ("dividend_yield_curve", dividend_yield_curve, False),
        ("volatility_curve", volatility_curve, True),
    ):
        if value is not None:
            try:
                curve_updates[column] = validate_curve(value, require_positive=positive)
            except ValueError as exc:
                raise DomainWriteError(
                    "invalid_curve", {"field": column, "reason": str(exc)}
                )
    if not provided and not clear_fields and not curve_updates:
        raise DomainWriteError("no_fields")
    with _session_scope(session) as sess:
        instrument = ensure_instrument(
            sess, symbol, source="pricing_profile", status="draft"
        )
        sess.flush()
        for field, value in provided.items():
            setattr(instrument, field, value)
        for field in clear_fields:
            setattr(instrument, field, None)
        for column, value in curve_updates.items():
            setattr(instrument, column, value)
        record_audit(
            sess,
            event_type="instrument.pricing_defaults_updated",
            actor=actor,
            subject_type="instrument",
            subject_id=instrument.id,
            payload={"symbol": instrument.symbol, "set": provided,
                     "cleared": clear_fields, "curves": sorted(curve_updates)},
        )
        sess.commit()
        return instrument


def build_set(
    *,
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> AssumptionSet:
    """Rebuild the assumption set from open-position scope (the pipeline write).

    Translates build_assumptions_set's ValueErrors into structured refusals;
    with an owned session nothing persists on refusal (no commit happens —
    callers passing their own session own the rollback).
    """
    with _session_scope(session) as sess:
        try:
            assumption_set = build_assumptions_set(
                sess, name=name, valuation_date=valuation_date
            )
        except ValueError as exc:
            arg = exc.args[0] if exc.args else "build failed"
            if isinstance(arg, dict) and "unfilled_underlyings" in arg:
                raise DomainWriteError(
                    "unfilled_underlyings",
                    {"underlyings": list(arg["unfilled_underlyings"])},
                ) from exc
            if arg == "no open positions in scope":
                raise DomainWriteError("no_open_positions") from exc
            raise
        record_audit(
            sess,
            event_type="assumptions.built",
            actor=actor,
            subject_type="assumption_set",
            subject_id=assumption_set.id,
            payload={
                "row_count": assumption_set.summary.get("row_count"),
                "instruments": assumption_set.summary.get("instruments", []),
            },
        )
        sess.commit()
        return (
            sess.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .filter(AssumptionSet.id == assumption_set.id)
            .one()
        )


__all__ = [
    "list_sets",
    "get_set",
    "get_instrument_defaults",
    "set_instrument_defaults",
    "build_set",
]

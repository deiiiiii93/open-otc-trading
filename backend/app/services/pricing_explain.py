"""Read-only resolution explain for a position's pricing parameters.

Composes the *same* layered resolution the pricer applies, but as a
read-only explanation — no AKShare fetch, no record_quote. For each of the
four fields it reports the value and where it came from:

- spot: latest quote in the quote store for the position's underlying.
- rate / dividend_yield / volatility, per field:
    1. the trade-keyed pricing-parameter profile row (only when a profile id
       is supplied — mirrors the pricer, which skips the trade layer when no
       profile is selected);
    2. else the instrument-level assumption set row;
    3. else missing.

Single responsibility: build the dict-of-four provenance objects. The
endpoint and schema live in main.py / schemas.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import Position
from .assumptions import latest_assumption_row
from .pricing_profiles import (
    pricing_rows_for_profile,
    resolve_pricing_parameter_row_for_position,
)
from .quotes import latest_quote

_PARAM_FIELDS = ("rate", "dividend_yield", "volatility")


def _missing() -> dict[str, Any]:
    return {"value": None, "source": "missing"}


def _resolve_spot(
    session: Session, position: Position, *, as_of: datetime
) -> dict[str, Any]:
    if position.underlying_id is None:
        return _missing()
    quote = latest_quote(session, position.underlying_id, as_of=as_of)
    if quote is None:
        return _missing()
    age_days = (as_of.date() - quote.as_of.date()).days
    return {
        "value": quote.price,
        "source": "market_quote",
        "as_of": quote.as_of,
        "age_days": age_days,
        "quote_source": quote.source,
    }


def resolve_position_pricing_params(
    session: Session,
    *,
    position: Position,
    pricing_parameter_profile_id: int | None,
    as_of: datetime,
) -> dict[str, dict[str, Any]]:
    """Return ``{spot, rate, dividend_yield, volatility}`` provenance objects."""
    out: dict[str, dict[str, Any]] = {
        "spot": _resolve_spot(session, position, as_of=as_of),
    }

    # Trade-keyed profile row — only when a profile is explicitly selected,
    # matching the pricer's explicit-only behaviour.
    profile_row = None
    if pricing_parameter_profile_id is not None:
        rows = pricing_rows_for_profile(
            session, profile_id=pricing_parameter_profile_id
        )
        resolution = resolve_pricing_parameter_row_for_position(rows, position)
        profile_row = resolution.row

    # Instrument-level assumption row (None underlying_id -> no row).
    assumption_row = None
    if position.underlying_id is not None:
        assumption_row = latest_assumption_row(
            session, position.underlying_id, as_of=as_of
        )

    for field in _PARAM_FIELDS:
        if profile_row is not None and getattr(profile_row, field) is not None:
            out[field] = {
                "value": getattr(profile_row, field),
                "source": "pricing_parameter_profile",
                "profile_id": profile_row.profile_id,
                "source_trade_id": profile_row.source_trade_id,
            }
        elif assumption_row is not None and getattr(assumption_row, field) is not None:
            out[field] = {
                "value": getattr(assumption_row, field),
                "source": "assumption_set",
                "assumption_set_id": assumption_row.set_id,
                "assumption_row_id": assumption_row.id,
            }
        else:
            out[field] = _missing()

    return out

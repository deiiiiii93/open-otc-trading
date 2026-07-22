from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Position,
    PricingParameterProfile,
    PricingParameterRow,
    UnderlyingPricingDefault,
)
from .instruments import ensure_instrument
from .market_input_workbooks import infer_valuation_date, read_market_rows_with_diagnostics
from .position_adapter import make_json_safe
from .quotes import record_quote
from .underlyings import open_position_underlying_symbols


def _open_position_underlyings(session: Session) -> list[str]:
    return open_position_underlying_symbols(session)


def import_pricing_parameter_profile_from_xlsx(
    session: Session,
    *,
    xlsx_path: str | Path,
    name: str | None = None,
    valuation_date: datetime | None = None,
    sheet_name: str | None = None,
) -> PricingParameterProfile:
    """Split-write import.

    Observations (spot) are written ONLY to the quote store (one quote per source
    row, ``source="xlsx_import"``, ``price_type="mid"``); assumptions (r/q/vol) stay
    on the trade-keyed ``PricingParameterRow``. Each row resolves an
    ``instrument_id`` from its booked position (by source_trade_id -> underlying_id)
    or, when no position exists yet, from ``ensure_instrument(symbol)``.

    Spot conflicts (one instrument receiving >1 distinct spot in this file) are
    recorded — every observation is kept — and tie-broken last-row-wins by the
    resolver (deterministic max(as_of), tie-break max(id)). Rows are inserted in
    ascending ``trade_id`` order (``sorted(rows.items())`` after the reader's
    dedupe), so for a given instrument the row with the largest trade_id flushes
    last and gets the larger id, and the resolver returns it.
    """
    path = Path(xlsx_path)
    rows, duplicate_trade_ids = read_market_rows_with_diagnostics(path, sheet_name=sheet_name)
    effective_valuation_date = valuation_date or infer_valuation_date(path) or datetime.utcnow()
    profile = PricingParameterProfile(
        name=name or f"Pricing Parameters {effective_valuation_date:%Y-%m-%d}",
        valuation_date=effective_valuation_date,
        source_type="xlsx",
        source_path=str(path),
        status="completed",
        summary={
            "row_count": len(rows),
            "duplicate_trade_ids": duplicate_trade_ids,
            "sheet_name": sheet_name,
        },
    )
    session.add(profile)
    session.flush()

    positions_by_trade_id = _positions_by_source_trade_id(session, rows.keys())

    rows_applied = 0
    dormant_trade_ids: list[str] = []
    quotes_emitted = 0
    # symbol -> set of distinct spot values seen in THIS file (conflict detection).
    spots_by_symbol: dict[str, set[float]] = {}

    for trade_id, row in sorted(rows.items()):
        symbol = row["symbol"]
        position = positions_by_trade_id.get(trade_id)
        if position is not None and position.underlying_id is not None:
            instrument_id = position.underlying_id
            rows_applied += 1
        else:
            instrument = ensure_instrument(
                session, symbol, source="pricing_profile", status="draft"
            )
            session.flush()
            instrument_id = instrument.id
            dormant_trade_ids.append(trade_id)

        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=trade_id,
                symbol=symbol,
                instrument_id=instrument_id,
                rate=row.get("rate"),
                dividend_yield=row.get("dividend_yield"),
                volatility=row.get("volatility"),
                source_row=row.get("source_row"),
                source_payload={
                    "row_number": row.get("source_row"),
                    "row": make_json_safe(row.get("raw", {})),
                },
            )
        )

        spot = row.get("spot")
        if spot is not None:
            record_quote(
                session,
                instrument_id=instrument_id,
                price=spot,
                as_of=effective_valuation_date,
                source="xlsx_import",
                price_type="mid",
                meta={
                    "trade_id": trade_id,
                    "profile_id": profile.id,
                    "source_row": row.get("source_row"),
                },
            )
            quotes_emitted += 1
            spots_by_symbol.setdefault(symbol, set()).add(round(float(spot), 12))

    spot_conflicts = [
        {"symbol": symbol, "count": len(values), "resolution": "last row wins"}
        for symbol, values in sorted(spots_by_symbol.items())
        if len(values) > 1
    ]

    profile.summary = {
        **profile.summary,
        "rows_applied": rows_applied,
        "rows_dormant": len(dormant_trade_ids),
        "dormant_trade_ids": sorted(dormant_trade_ids),
        "quotes_emitted": quotes_emitted,
        "spot_conflicts": spot_conflicts,
    }
    session.flush()
    return profile


def _positions_by_source_trade_id(
    session: Session, trade_ids
) -> dict[str, Position]:
    cleaned = {str(tid) for tid in trade_ids if tid}
    if not cleaned:
        return {}
    positions = (
        session.query(Position)
        .filter(Position.source_trade_id.in_(cleaned))
        .all()
    )
    return {p.source_trade_id: p for p in positions if p.source_trade_id}


MANUAL_INPUT_FIELDS = ("rate", "dividend_yield", "volatility")
# Spot left the row (instrument-unification T8): observations live in the quote
# store, so row completeness is now r/q/vol only. A row carrying only r/q/vol is
# COMPLETE for trade-id / underlying resolution.
PRICING_PARAMETER_FIELDS = ("rate", "dividend_yield", "volatility")
PricingParameterMatchType = str


@dataclass(frozen=True)
class PricingParameterRowResolution:
    row: PricingParameterRow | None
    match_type: PricingParameterMatchType
    missing_pricing_fields: tuple[str, ...] = ()
    candidate_count: int = 0

    @property
    def ok(self) -> bool:
        return self.row is not None and self.match_type in {
            "position_id",
            "trade_id",
            "underlying",
        }


@dataclass(frozen=True)
class UnderlyingMarketParams:
    """Single per-underlying (rate, dividend_yield, volatility) collapsed from a
    profile's rows. ``ok`` only when nothing is missing or ambiguous."""

    rate: float | None
    dividend_yield: float | None
    volatility: float | None
    missing_fields: tuple[str, ...] = ()
    ambiguous_fields: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_fields and not self.ambiguous_fields


def resolve_underlying_market_params(
    rows: list[PricingParameterRow], symbol: str
) -> UnderlyingMarketParams:
    """Collapse a profile's rows for ``symbol`` to one (rate, div, vol), field-wise.

    For each field, the distinct non-null values across the underlying's rows are
    quantized (1e-12) to absorb float noise: exactly one -> use it; none -> missing;
    more than one -> ambiguous. Both missing and ambiguous make ``ok`` False so the
    caller refuses rather than guessing.
    """
    key = _normalize_pricing_match_key(symbol)
    matched = [row for row in rows if _normalize_pricing_match_key(row.symbol) == key]
    values: dict[str, float | None] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for field in MANUAL_INPUT_FIELDS:
        distinct = {
            round(float(getattr(row, field)), 12)
            for row in matched
            if getattr(row, field) is not None
        }
        if len(distinct) == 1:
            values[field] = distinct.pop()
        elif not distinct:
            values[field] = None
            missing.append(field)
        else:
            values[field] = None
            ambiguous.append(field)
    return UnderlyingMarketParams(
        rate=values["rate"],
        dividend_yield=values["dividend_yield"],
        volatility=values["volatility"],
        missing_fields=tuple(missing),
        ambiguous_fields=tuple(ambiguous),
    )


def resolve_pricing_parameter_row_for_position(
    rows: list[PricingParameterRow],
    position: Position,
) -> PricingParameterRowResolution:
    """Resolve profile row by exact position binding, then trade id, then a
    unique complete underlying row."""
    # Direct per-position binding wins: curve-generated rows carry position_id,
    # so they resolve unambiguously even when the position has no trade id.
    # Imported rows have position_id=None and skip this branch entirely.
    position_id = getattr(position, "id", None)
    if position_id is not None:
        bound = [row for row in rows if getattr(row, "position_id", None) == position_id]
        if bound:
            complete = [row for row in bound if not _missing_pricing_fields(row)]
            if complete:
                return PricingParameterRowResolution(
                    row=complete[0],
                    match_type="position_id",
                    candidate_count=len(bound),
                )
            missing = tuple(
                sorted({f for row in bound for f in _missing_pricing_fields(row)})
            )
            return PricingParameterRowResolution(
                row=None,
                match_type="incomplete",
                missing_pricing_fields=missing,
                candidate_count=len(bound),
            )

    trade_id = _normalize_pricing_match_key(position.source_trade_id)
    if trade_id:
        exact = next(
            (
                row
                for row in rows
                if _normalize_pricing_match_key(row.source_trade_id) == trade_id
            ),
            None,
        )
        if exact is not None:
            missing = _missing_pricing_fields(exact)
            if missing:
                return PricingParameterRowResolution(
                    row=exact,
                    match_type="incomplete",
                    missing_pricing_fields=missing,
                    candidate_count=1,
                )
            return PricingParameterRowResolution(
                row=exact,
                match_type="trade_id",
                candidate_count=1,
            )

    underlying = _normalize_pricing_match_key(position.underlying)
    if not underlying:
        return PricingParameterRowResolution(row=None, match_type="missing")

    underlying_rows = [
        row
        for row in rows
        if _normalize_pricing_match_key(row.symbol) == underlying
    ]
    if not underlying_rows:
        return PricingParameterRowResolution(row=None, match_type="missing")

    complete_rows = [row for row in underlying_rows if not _missing_pricing_fields(row)]
    if len(complete_rows) == 1:
        return PricingParameterRowResolution(
            row=complete_rows[0],
            match_type="underlying",
            candidate_count=len(underlying_rows),
        )
    if len(complete_rows) > 1:
        return PricingParameterRowResolution(
            row=None,
            match_type="ambiguous",
            candidate_count=len(complete_rows),
        )
    missing = tuple(
        sorted(
            {
                field
                for row in underlying_rows
                for field in _missing_pricing_fields(row)
            }
        )
    )
    return PricingParameterRowResolution(
        row=None,
        match_type="incomplete",
        missing_pricing_fields=missing,
        candidate_count=len(underlying_rows),
    )


def pricing_parameter_resolution_message(
    position: Position,
    resolution: PricingParameterRowResolution,
) -> str:
    label = (position.source_trade_id or "").strip() or f"#{position.id}"
    underlying = (position.underlying or "").strip() or "unknown underlying"
    prefix = f"Selected pricing profile cannot extract pricing parameters for position {label}"
    if resolution.match_type == "ambiguous":
        return f"{prefix}: multiple complete rows match underlying {underlying}"
    if resolution.match_type == "incomplete":
        fields = ", ".join(resolution.missing_pricing_fields) or "pricing fields"
        return f"{prefix}: missing {fields}"
    return f"{prefix}: no exact trade row or unique complete underlying row"


# Engines that price purely from spot (delta-one) consume no rate / dividend /
# volatility parameters, so a pricing profile that lacks a complete param row
# for such a position is NOT a pricing failure — the engine ignores those fields
# entirely (its gamma/vega/theta/rho are structurally zero). Option and
# structured engines DO require the params and must still fail loudly.
PARAMLESS_ENGINES = frozenset({"DeltaOneEngine"})


def position_requires_pricing_params(position: Position) -> bool:
    """True when the position's engine consumes rate/dividend/volatility params.

    Delta-one instruments (futures, spot) are linear in spot and ignore r/q/vol,
    so a profile missing those rows should not raise a param-resolution failure
    for them.
    """
    return (getattr(position, "engine_name", "") or "") not in PARAMLESS_ENGINES


def pricing_parameter_resolution_diagnostics(
    *,
    profile_id: int,
    resolution: PricingParameterRowResolution,
) -> dict[str, Any]:
    return {
        "market_input_source": "pricing_parameter_profile",
        "parameter_input_source": "pricing_parameter_profile",
        "pricing_parameter_profile_id": profile_id,
        "pricing_parameter_row_id": resolution.row.id if resolution.row is not None else None,
        "pricing_parameter_match_type": resolution.match_type,
        "missing_pricing_fields": list(resolution.missing_pricing_fields),
    }


def _normalize_pricing_match_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _missing_pricing_fields(row: PricingParameterRow) -> tuple[str, ...]:
    return tuple(field for field in PRICING_PARAMETER_FIELDS if getattr(row, field) is None)


def latest_manual_inputs_by_underlying(
    session: Session,
    underlyings: list[str],
) -> dict[str, dict[str, Any]]:
    cleaned = {
        underlying.strip()
        for underlying in underlyings
        if underlying and underlying.strip()
    }
    if not cleaned:
        return {}

    profile_rows = (
        session.query(PricingParameterProfile, PricingParameterRow)
        .join(
            PricingParameterRow,
            PricingParameterRow.profile_id == PricingParameterProfile.id,
        )
        .filter(PricingParameterRow.symbol.in_(cleaned))
        .order_by(
            desc(PricingParameterProfile.valuation_date),
            desc(PricingParameterProfile.created_at),
            desc(PricingParameterProfile.id),
            PricingParameterRow.source_trade_id.asc(),
        )
        .all()
    )
    if not profile_rows:
        return {}

    inherited: dict[str, dict[str, Any]] = {}
    scores: dict[str, int] = {}
    for profile, row in profile_rows:
        symbol = (row.symbol or "").strip()
        values = {field: getattr(row, field) for field in MANUAL_INPUT_FIELDS}
        score = sum(value is not None for value in values.values())
        if score == 0 or score <= scores.get(symbol, -1):
            continue
        inherited[symbol] = {
            **values,
            "pricing_parameter_profile_id": profile.id,
            "pricing_parameter_profile_name": profile.name,
            "pricing_parameter_profile_valuation_date": profile.valuation_date,
            "pricing_parameter_row_id": row.id,
            "source_trade_id": row.source_trade_id,
        }
        scores[symbol] = score
        if all(scores.get(symbol, 0) == len(MANUAL_INPUT_FIELDS) for symbol in cleaned):
            break
    return inherited


def resolved_underlying_default_inputs(
    row: UnderlyingPricingDefault,
    inherited: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    inherited = inherited or {}
    return {
        field: (
            getattr(row, field)
            if getattr(row, field) is not None
            else inherited.get(field)
        )
        for field in MANUAL_INPUT_FIELDS
    }



def pricing_rows_for_profile(
    session: Session,
    *,
    profile_id: int,
) -> list[PricingParameterRow]:
    profile = (
        session.query(PricingParameterProfile)
        .options(selectinload(PricingParameterProfile.rows))
        .filter(PricingParameterProfile.id == profile_id)
        .one_or_none()
    )
    if profile is None:
        raise ValueError(f"Pricing parameter profile not found: {profile_id}")
    return list(profile.rows)

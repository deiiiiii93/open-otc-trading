"""Instrument-level r/q/vol assumption sets.

Built from open-position scope + instrument defaults + inherited
PricingParameterRow inputs.  NO AKShare fetches occur here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..models import (
    AssumptionRow,
    AssumptionSet,
    UnderlyingPricingDefault,
)
from .pricing_profiles import (
    MANUAL_INPUT_FIELDS,
    _open_position_underlyings,
    latest_manual_inputs_by_underlying,
    resolved_underlying_default_inputs,
)
from .underlyings import ensure_underlying


def build_assumptions_set(
    session: Session,
    *,
    name: str | None = None,
    valuation_date: datetime | None = None,
) -> AssumptionSet:
    """Build an AssumptionSet from the current open-position scope.

    Resolution order per field (rate / dividend_yield / volatility):
      1. Instrument.rate / .dividend_yield / .volatility (instrument_default)
      2. Latest PricingParameterRow for the underlying (inherited_pricing_parameter_row)

    Raises ValueError("no open positions in scope") when there is nothing in scope.
    Raises ValueError({"unfilled_underlyings": [...]}) when any field is still None
    after full resolution.

    No AKShare imports or calls anywhere in this function.
    """
    effective_valuation = valuation_date or datetime.utcnow()
    underlyings = _open_position_underlyings(session)
    if not underlyings:
        raise ValueError("no open positions in scope")

    # Ensure every underlying has an Instrument row.
    existing: dict[str, UnderlyingPricingDefault] = {
        row.symbol: row
        for row in session.query(UnderlyingPricingDefault).all()
    }
    for underlying in underlyings:
        if underlying not in existing:
            row = ensure_underlying(
                session, underlying, source="pricing_profile", status="draft"
            )
            existing[underlying] = row
    session.flush()

    # Inherit from latest PricingParameterProfile rows.
    inherited_inputs = latest_manual_inputs_by_underlying(session, underlyings)

    # Resolve per-underlying inputs (instrument_default wins over inherited).
    resolved_inputs: dict[str, dict[str, Any]] = {
        underlying: resolved_underlying_default_inputs(
            existing[underlying],
            inherited_inputs.get(underlying),
        )
        for underlying in underlyings
    }

    # Reject if any underlying still has a missing field.
    unfilled = [
        underlying
        for underlying in underlyings
        if any(
            resolved_inputs[underlying][field] is None for field in MANUAL_INPUT_FIELDS
        )
    ]
    if unfilled:
        raise ValueError({"unfilled_underlyings": sorted(unfilled)})

    # Write AssumptionSet.
    assumption_set = AssumptionSet(
        name=name or f"Assumptions {effective_valuation:%Y-%m-%d %H:%M}",
        valuation_date=effective_valuation,
        status="completed",
        summary={},  # filled in below
    )
    session.add(assumption_set)
    session.flush()

    row_count = 0
    symbols: list[str] = []
    for underlying in underlyings:
        store = existing[underlying]
        manual_inputs = resolved_inputs[underlying]
        inherited = inherited_inputs.get(underlying) or {}

        # Build per-field provenance.
        manual_field_sources: dict[str, str] = {
            field: (
                "instrument_default"
                if getattr(store, field) is not None
                else "inherited_pricing_parameter_row"
            )
            for field in MANUAL_INPUT_FIELDS
        }

        # Include inherited-* keys only when at least one field came from inheritance.
        source_payload: dict[str, Any] = {
            "source": "instrument_default",
            "instrument_id": store.id,
            "manual_input_sources": manual_field_sources,
        }
        if any(
            v == "inherited_pricing_parameter_row"
            for v in manual_field_sources.values()
        ):
            source_payload["inherited_pricing_parameter_profile_id"] = inherited.get(
                "pricing_parameter_profile_id"
            )
            source_payload["inherited_pricing_parameter_row_id"] = inherited.get(
                "pricing_parameter_row_id"
            )
            source_payload["inherited_source_trade_id"] = inherited.get(
                "source_trade_id"
            )

        session.add(
            AssumptionRow(
                set_id=assumption_set.id,
                instrument_id=store.id,
                symbol=underlying,
                rate=manual_inputs["rate"],
                dividend_yield=manual_inputs["dividend_yield"],
                volatility=manual_inputs["volatility"],
                source_payload=source_payload,
            )
        )
        row_count += 1
        symbols.append(underlying)

    assumption_set.summary = {
        "row_count": row_count,
        "instruments": sorted(symbols),
    }
    session.flush()
    return assumption_set


def latest_assumption_row(
    session: Session,
    instrument_id: int,
    *,
    as_of: datetime,
) -> AssumptionRow | None:
    """Return the AssumptionRow for *instrument_id* from the most recent
    completed AssumptionSet whose valuation_date ≤ *as_of*, or None.
    """
    result = (
        session.query(AssumptionRow)
        .join(AssumptionSet, AssumptionRow.set_id == AssumptionSet.id)
        .filter(
            AssumptionRow.instrument_id == instrument_id,
            AssumptionSet.status == "completed",
            AssumptionSet.valuation_date <= as_of,
        )
        .order_by(
            desc(AssumptionSet.valuation_date),
            desc(AssumptionSet.id),
        )
        .first()
    )
    return result

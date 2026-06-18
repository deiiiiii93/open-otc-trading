"""@tool wrappers for the assumption pipeline (instrument defaults -> built sets).

Pipeline-only by design: there is NO direct AssumptionRow write tool, so
provenance in source_payload always reflects a real build.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import assumptions as assumptions_svc
from app.services.domains._errors import DomainWriteError

from ._shaping import (
    domain_write_error_response,
    parse_valuation_date,
    shape_assumption_set,
    shape_instrument_defaults,
)


class ListAssumptionSetsInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive substring over name/status/valuation date.",
    )
    limit: int = Field(default=20, ge=1, le=100)


class GetAssumptionSetInput(BaseModel):
    set_id: int


class GetInstrumentPricingDefaultsInput(BaseModel):
    symbols: list[str] | None = Field(
        default=None, description="Filter to these symbols; omit for all."
    )
    limit: int = Field(default=50, ge=1, le=200)


class SetInstrumentPricingDefaultsInput(BaseModel):
    symbol: str = Field(min_length=1)
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = Field(default=None, description="Annualized, e.g. 0.22.")
    clear: list[str] = Field(
        default_factory=list,
        description="Fields to null out: rate|dividend_yield|volatility. A field "
        "cannot be both set and cleared.",
    )


class BuildAssumptionSetInput(BaseModel):
    name: str | None = Field(default=None, description="Defaults to 'Assumptions <ts>'.")
    valuation_date: str | None = Field(default=None, description="ISO datetime; defaults to now.")


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_assumption_sets", args_schema=ListAssumptionSetsInput)
def list_assumption_sets_tool(
    query: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """List stored instrument-keyed assumption sets, newest first."""
    rows = assumptions_svc.list_sets(query=query, limit=limit)
    return {
        "ok": True,
        "data": [shape_assumption_set(s) for s in rows],
        "total_count": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_assumption_set", args_schema=GetAssumptionSetInput)
def get_assumption_set_tool(set_id: int) -> dict[str, Any]:
    """Fetch one assumption set with rows + per-field provenance."""
    assumption_set = assumptions_svc.get_set(set_id=set_id)
    if assumption_set is None:
        return {"ok": False, "error": "set_not_found", "detail": {"set_id": set_id}}
    return {"ok": True, "data": shape_assumption_set(assumption_set, include_rows=True)}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_instrument_pricing_defaults", args_schema=GetInstrumentPricingDefaultsInput)
def get_instrument_pricing_defaults_tool(
    symbols: list[str] | None = None, limit: int = 50
) -> dict[str, Any]:
    """Instrument baseline r/q/vol defaults (first source the assumption build
    resolves)."""
    rows = assumptions_svc.get_instrument_defaults(symbols=symbols, limit=limit)
    return {
        "ok": True,
        "data": [shape_instrument_defaults(i) for i in rows],
        "total_count": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("set_instrument_pricing_defaults", args_schema=SetInstrumentPricingDefaultsInput)
def set_instrument_pricing_defaults_tool(
    symbol: str,
    rate: float | None = None,
    dividend_yield: float | None = None,
    volatility: float | None = None,
    clear: list[str] | None = None,
) -> dict[str, Any]:
    """Set/clear an instrument's baseline r/q/vol; run build_assumption_set
    afterwards to materialize. HITL — requires confirmation."""
    try:
        instrument = assumptions_svc.set_instrument_defaults(
            symbol=symbol,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            clear=clear or [],
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": shape_instrument_defaults(instrument)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("build_assumption_set", args_schema=BuildAssumptionSetInput)
def build_assumption_set_tool(
    name: str | None = None, valuation_date: str | None = None
) -> dict[str, Any]:
    """Rebuild the canonical assumption set from open-position scope. On
    unfilled_underlyings, set those instruments' defaults and retry.
    HITL — requires confirmation."""
    try:
        assumption_set = assumptions_svc.build_set(
            name=name, valuation_date=parse_valuation_date(valuation_date)
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": shape_assumption_set(assumption_set, include_rows=True)}


__all__ = [
    "list_assumption_sets_tool",
    "get_assumption_set_tool",
    "get_instrument_pricing_defaults_tool",
    "set_instrument_pricing_defaults_tool",
    "build_assumption_set_tool",
]

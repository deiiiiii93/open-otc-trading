"""@tool wrappers for pricing parameter profiles (read + agent write facade)."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import pricing_profiles as pricing_profiles_svc
from app.services.domains._errors import DomainWriteError

from ._shaping import (
    domain_write_error_response,
    parse_valuation_date,
    shape_pricing_parameter_profile,
    shape_pricing_parameter_row,
)

_ROWS_DESCRIPTION = (
    "Each row: {symbol: '000905.SH', source_trade_id: ''|'T-123', rate: 0.03, "
    "dividend_yield: 0.01, volatility: 0.22}. r/q/vol optional per row but at "
    "least one required; omit source_trade_id (or pass '') for underlying-level "
    "rows. NO spot field — spots live in the quote store. Values are decimals "
    "(0.22, not 22); volatility must be > 0. NOTE: a position only "
    "resolves a row that carries ALL of rate/dividend_yield/volatility — copy "
    "current values for fields you are not changing."
)


class ListPricingParameterProfilesInput(BaseModel):
    query: str | None = Field(
        default=None,
        description=(
            "Optional case-insensitive substring to match profile name, source type, "
            "or valuation date. Use this to resolve a user-named profile."
        ),
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max profiles to return.")


class GetPricingParameterProfileInput(BaseModel):
    profile_id: int


class CreatePricingParameterProfileInput(BaseModel):
    rows: list[dict[str, Any]] = Field(description=_ROWS_DESCRIPTION)
    name: str | None = Field(
        default=None, description="Defaults to 'Agent Pricing Parameters <date>'."
    )
    valuation_date: str | None = Field(
        default=None, description="ISO datetime; defaults to now."
    )


class UpdatePricingParameterProfileInput(BaseModel):
    profile_id: int
    name: str | None = None
    valuation_date: str | None = Field(default=None, description="ISO datetime.")


class UpsertPricingParameterRowsInput(BaseModel):
    profile_id: int
    rows: list[dict[str, Any]] = Field(
        description=_ROWS_DESCRIPTION
        + " Rows match existing ones on (source_trade_id, symbol); matched rows "
        "only overwrite the provided fields."
    )


class DeletePricingParameterRowsInput(BaseModel):
    profile_id: int
    row_ids: list[int] = Field(
        description="Row ids from get_pricing_parameter_profile; all must belong "
        "to the profile or the whole call is refused."
    )


class DeletePricingParameterProfileInput(BaseModel):
    profile_id: int


class GeneratePricingParametersFromCurvesInput(BaseModel):
    name: str | None = Field(
        default=None, description="Defaults to 'Curve Pricing Parameters <date>'."
    )
    valuation_date: str | None = Field(
        default=None, description="ISO datetime; defaults to now."
    )


def _profile_with_rows(profile: Any) -> dict[str, Any]:
    shaped = shape_pricing_parameter_profile(profile)
    shaped["rows"] = [shape_pricing_parameter_row(row) for row in profile.rows]
    return shaped


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_pricing_parameter_profiles", args_schema=ListPricingParameterProfilesInput)
def list_pricing_parameter_profiles_tool(
    query: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List stored pricing parameter profiles for selecting a profile id."""
    rows = pricing_profiles_svc.list_profiles(query=query, limit=limit)
    return {
        "ok": True,
        "data": [shape_pricing_parameter_profile(p) for p in rows],
        "total_count": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_pricing_parameter_profile", args_schema=GetPricingParameterProfileInput)
def get_pricing_parameter_profile_tool(profile_id: int) -> dict[str, Any]:
    """Fetch one pricing parameter profile with full r/q/vol rows (row ids are
    the handles for the upsert/delete row tools)."""
    profile = pricing_profiles_svc.get_profile(profile_id=profile_id)
    if profile is None:
        return {"ok": False, "error": "profile_not_found", "detail": {"profile_id": profile_id}}
    return {"ok": True, "data": _profile_with_rows(profile)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("create_pricing_parameter_profile", args_schema=CreatePricingParameterProfileInput)
def create_pricing_parameter_profile_tool(
    rows: list[dict[str, Any]],
    name: str | None = None,
    valuation_date: str | None = None,
) -> dict[str, Any]:
    """Create an agent what-if r/q/vol profile (source_type='agent'); pass the
    returned id to run_batch_pricing. HITL — requires confirmation."""
    try:
        profile = pricing_profiles_svc.create_profile(
            rows=rows, name=name, valuation_date=parse_valuation_date(valuation_date)
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("update_pricing_parameter_profile", args_schema=UpdatePricingParameterProfileInput)
def update_pricing_parameter_profile_tool(
    profile_id: int,
    name: str | None = None,
    valuation_date: str | None = None,
) -> dict[str, Any]:
    """Rename / re-date a profile (metadata only; rows have their own tools).
    HITL — requires confirmation."""
    try:
        profile = pricing_profiles_svc.update_profile(
            profile_id=profile_id,
            name=name,
            valuation_date=parse_valuation_date(valuation_date),
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("upsert_pricing_parameter_rows", args_schema=UpsertPricingParameterRowsInput)
def upsert_pricing_parameter_rows_tool(
    profile_id: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Upsert profile rows by (source_trade_id, symbol); matched rows overwrite
    only provided fields. HITL — requires confirmation."""
    try:
        profile, counts = pricing_profiles_svc.upsert_rows(
            profile_id=profile_id, rows=rows
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile), **counts}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("delete_pricing_parameter_rows", args_schema=DeletePricingParameterRowsInput)
def delete_pricing_parameter_rows_tool(
    profile_id: int,
    row_ids: list[int],
) -> dict[str, Any]:
    """Delete rows from a profile; refused wholesale if any id is foreign.
    HITL — requires confirmation."""
    try:
        profile, deleted = pricing_profiles_svc.delete_rows(
            profile_id=profile_id, row_ids=row_ids
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile), "deleted": deleted}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("delete_pricing_parameter_profile", args_schema=DeletePricingParameterProfileInput)
def delete_pricing_parameter_profile_tool(profile_id: int) -> dict[str, Any]:
    """Delete an UNREFERENCED profile (cascades its rows). Refused when any
    valuation/risk run references it. IRREVERSIBLE; HITL — requires confirmation."""
    try:
        result = pricing_profiles_svc.delete_profile(profile_id=profile_id)
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": result}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool(
    "generate_pricing_parameters_from_curves",
    args_schema=GeneratePricingParametersFromCurvesInput,
)
def generate_pricing_parameters_from_curves_tool(
    name: str | None = None,
    valuation_date: str | None = None,
) -> dict[str, Any]:
    """Interpolate every open trade's r/q/vol from its underlying's
    term-structure curves (flat-scalar fallback) into a new flat pricing profile
    (source_type='curve'); pass the returned id to run_batch_pricing. On
    unfilled_trades, set those instruments' curves or scalars and retry.
    HITL — requires confirmation."""
    try:
        profile = pricing_profiles_svc.generate_profile_from_curves(
            name=name, valuation_date=parse_valuation_date(valuation_date)
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile)}


__all__ = [
    "list_pricing_parameter_profiles_tool",
    "get_pricing_parameter_profile_tool",
    "create_pricing_parameter_profile_tool",
    "update_pricing_parameter_profile_tool",
    "upsert_pricing_parameter_rows_tool",
    "delete_pricing_parameter_rows_tool",
    "delete_pricing_parameter_profile_tool",
    "generate_pricing_parameters_from_curves_tool",
]

"""Read-only legal term-sheet schema for a product family.

Surfaces the builder-facing input names, types, required/optional, defaults, and legal
enum values so the agent fills ``build_product`` correctly on the first call instead of
guessing enum spellings (e.g. ``DOWN_AND_IN`` vs the legal ``DOWN_IN``). Values are derived
from ``FamilyContract`` FieldSpecs — enum values live from quant-ark where every member
round-trips, builder-faithful literals otherwise. Numbers never come from an LLM."""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains.product_contracts import (
    _CONTRACTS,
    contract_for,
    one_of_groups,
    required_fields,
    resolve_enum_values,
)

# V1 flat option families + V2 nested-config (barrier_config/coupon_config/range_config dotted
# paths, resolved from flat input aliases) + DeltaOne. Any family with a non-empty ``fields``
# tuple can be published; the frozenset gates which are exposed.
_SCHEMA_FAMILIES = frozenset({
    "BarrierOption", "EuropeanVanillaOption", "AmericanOption", "AsianOption",
    "CashOrNothingDigitalOption", "SingleSharkfinOption", "DoubleSharkfinOption",
    "OneTouchOption", "DoubleOneTouchOption",
    # V2 nested-config + DeltaOne
    "SnowballOption", "KnockOutResetSnowballOption", "PhoenixOption",
    "RangeAccrualOption", "Futures", "SpotInstrument",
})


class GetProductTermSchemaInput(BaseModel):
    quantark_class: str = Field(description="QuantArk family class, e.g. BarrierOption.")


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_product_term_schema", args_schema=GetProductTermSchemaInput)
def get_product_term_schema(quantark_class: str) -> dict:
    """Return the legal term-sheet schema for a product family — builder-facing field
    names, types, required/optional, defaults, and legal enum values. Call this BEFORE
    build_product and fill from the RFQ/context; do NOT guess enum values or omit
    required fields."""
    contract = contract_for(quantark_class)
    if contract is None:
        return {"error": f"Unknown QuantArk class {quantark_class!r}",
                "known_classes": sorted(_CONTRACTS)}
    if quantark_class not in _SCHEMA_FAMILIES or not contract.fields:
        return {"quantark_class": quantark_class, "schema_available": False,
                "reason": "structured schema not yet published for this family",
                "use_instead": "check_term_completeness + get_product_reference_doc"}

    required_paths = set(required_fields(contract, {}))
    groups = one_of_groups(contract)
    fields = []
    for spec in contract.fields:
        path = spec.contract_path or spec.input_name
        entry = {
            "name": spec.input_name,
            "kind": spec.kind,
            # one_of members and requires_when (conditional) fields are never flatly required.
            "required": (spec.one_of is None and spec.requires_when is None
                         and path in required_paths),
            "description": spec.description,
        }
        if spec.default is not None:
            entry["default"] = spec.default
        if spec.kind == "enum":
            entry["enum_values"] = list(resolve_enum_values(spec))
        if spec.one_of is not None:
            entry["one_of"] = spec.one_of
        # abs/pct barrier alias set: surface BOTH spellings the model may fill (not a one_of).
        if len(spec.input_aliases) > 1:
            entry["input_names"] = list(spec.input_aliases)
        if spec.requires_when is not None:
            field, value = spec.requires_when
            entry["requires_when"] = ({"field": field, "not_equals": value[1:]}
                                      if value.startswith("!")
                                      else {"field": field, "equals": value})
        fields.append(entry)

    required_groups = [{"one_of": group, "members": list(members)}
                       for group, members in sorted(groups.items())]
    return {
        "quantark_class": quantark_class,
        "fields": fields,
        "required_groups": required_groups,
        "notes": ("Fill from the RFQ/context. Required fields and one member of each "
                  "required_groups must be supplied; a field with input_names accepts ANY "
                  "one of those spellings (e.g. absolute or _pct barrier — supply exactly "
                  "one); a field with requires_when is required only under that condition; "
                  "defaulted fields fall back to desk defaults. Do not guess enum values."),
    }


__all__ = ["get_product_term_schema"]

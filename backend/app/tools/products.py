"""@tool wrapper for deterministic product construction."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains.product_builders import build_product


class BuildProductInput(BaseModel):
    family: str = Field(description="QuantArk product class, e.g. 'SnowballOption'.")
    terms: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured per-family terms (levels, frequencies, tenor, dates).",
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("build_product", args_schema=BuildProductInput)
def build_product_tool(family: str, terms: dict[str, Any]) -> dict[str, Any]:
    """Construct a quant-ark-validated product from structured terms.

    Synthesizes observation schedules where required. Reports any economics it
    will not invent (lockup, trade start, barrier levels, coupon) in `missing`
    instead of guessing. Does NOT persist anything.
    """
    result = build_product(family, dict(terms or {}))
    return {
        "ok": result.ok,
        "quantark_class": result.quantark_class,
        "engine_name": result.engine_name,
        "product_kwargs": result.product_kwargs,
        "missing": result.missing,
        "warnings": result.warnings,
        "validation": result.validation,
        "product_spec": asdict(result.product_spec) if result.product_spec else None,
    }


__all__ = ["build_product_tool"]

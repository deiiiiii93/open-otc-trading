"""@tool wrappers for the pricing domain.

Each wrapper is a thin LLM adapter: parse args, call services/domains/pricing,
shape JSON. The wire shapes preserve the legacy langchain_tools.py payloads so
existing agent tests continue to exercise this layer untouched.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import Field

from app.schemas import PricingEnvironmentSnapshot
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import pricing as pricing_svc
from ._product_inputs import ProductReferenceInput, ToolProductSpec


class PriceProductInput(ProductReferenceInput):
    market: PricingEnvironmentSnapshot = Field(
        default_factory=PricingEnvironmentSnapshot
    )
    engine_name: str = "BlackScholesEngine"


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("price_product", args_schema=PriceProductInput)
def price_product_tool(
    product_id: int | None = None,
    product: ToolProductSpec | None = None,
    market: PricingEnvironmentSnapshot = PricingEnvironmentSnapshot(),
    engine_name: str = "BlackScholesEngine",
) -> dict[str, Any]:
    """Price one ad-hoc OTC product through QuantArk and return price plus
    metadata. Read-only: does NOT persist a valuation or touch any portfolio
    row. Use for exploratory pricing, RFQ quote previews, or single-spec
    what-if questions. For repricing stored positions, use run_batch_pricing
    instead."""
    result = pricing_svc.price_product_reference(
        product_id=product_id,
        product=product.to_product_spec() if product is not None else None,
        market=market,
        engine_name=engine_name,
    )
    return {"ok": result.ok, "data": result.data, "error": result.error}



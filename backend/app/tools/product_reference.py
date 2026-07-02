"""Read-only agent tool exposing resolved product reference docs.

Skills are markdown and cannot call Python; this tool is the runtime
surface of resolve_product_reference so the agent reads the SAME merged
content the coherence net validates. Applies the configured desk_region."""
from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.reference_docs import (
    resolve_product_reference,
    validate_product_reference_tree,
)


class GetProductReferenceDocInput(BaseModel):
    quantark_class: str = Field(
        description="QuantArk family class, e.g. SnowballOption, BarrierOption."
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_product_reference_doc", args_schema=GetProductReferenceDocInput)
def get_product_reference_doc(quantark_class: str) -> dict:
    """Load the resolved product reference doc (definitions, conventions,
    required pricing inputs, diagnostics) for a product family. Merges the
    family doc with its inheritance base and the desk's region overlay.
    Call this instead of reading /skills/references/products files."""
    try:
        resolved = resolve_product_reference(
            quantark_class, region=get_settings().desk_region
        )
    except KeyError:
        return {
            "error": f"No reference doc claims {quantark_class!r}",
            "known_classes": sorted(validate_product_reference_tree()),
        }
    return {
        "quantark_class": resolved.quantark_class,
        "content": resolved.content,
        "sources": [str(p) for p in resolved.source_paths],
    }

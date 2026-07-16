"""Read-only term-completeness check against FamilyContracts.

The desk rule "numbers never come from an LLM" extended to term verdicts:
whether a term set is complete enough to price is answered by diffing the
provided terms against the family contract's required_bound — never from
model priors (which confabulate plausible-but-wrong structures, e.g. a
knock-in leg on a sharkfin)."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains.product_contracts import _CONTRACTS, contract_for, required_fields


class CheckTermCompletenessInput(BaseModel):
    quantark_class: str = Field(
        description="QuantArk family class, e.g. SingleSharkfinOption, SnowballOption."
    )
    terms: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Terms collected so far. Keys may be flat contract paths "
            "('barrier_config.ko_barrier') or nested dicts "
            "({'barrier_config': {'ko_barrier': 1.0}})."
        ),
    )


def _lookup(terms: dict[str, Any], dotted: str) -> Any:
    if dotted in terms:
        return terms[dotted]
    node: Any = terms
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _is_provided(value: Any) -> bool:
    return value is not None and value != ""


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("check_term_completeness", args_schema=CheckTermCompletenessInput)
def check_term_completeness(quantark_class: str, terms: dict[str, Any] | None = None) -> dict:
    """Check whether a product's terms are complete enough to price/book,
    against the family's declared contract (required vs defaulted inputs).
    ALWAYS call this before stating that a term set is complete or listing
    missing terms — never answer completeness from memory."""
    contract = contract_for(quantark_class)
    if contract is None:
        return {
            "error": f"Unknown QuantArk class {quantark_class!r}",
            "known_classes": sorted(_CONTRACTS),
        }
    terms = terms or {}

    # required_fields encodes the conditional ko_observation_dates rule AND this
    # family's declared one_of alternatives (e.g. maturity_years|maturity_date), so the
    # completeness verdict and the schema tool stay in agreement.
    required = required_fields(contract, terms)
    missing = [key for key in required if not _is_provided(_lookup(terms, key))]
    provided = [key for key in required if key not in missing]
    defaulted_unset = [
        key for key in contract.defaulted if not _is_provided(_lookup(terms, key))
    ]
    return {
        "quantark_class": quantark_class,
        "complete": not missing,
        "missing_required": missing,
        "provided_required": provided,
        "defaulted_unset": defaulted_unset,
        "note": (
            "missing_required must all be collected before pricing/booking; "
            "defaulted_unset fields fall back to desk defaults (configurable)."
        ),
    }

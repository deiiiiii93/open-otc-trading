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
from app.services.domains.product_contracts import (
    _CONTRACTS,
    active_required_paths,
    contract_for,
    flat_aliases,
    one_of_groups,
)


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


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    aliases = flat_aliases(contract)

    def _provided_path(path: str) -> bool:
        """A required dotted path is satisfied by its nested value/literal-dotted key OR any
        of its flat input spellings (e.g. ko_barrier / ko_barrier_pct → barrier_config.ko_barrier)."""
        if _is_provided(_lookup(terms, path)):
            return True
        return any(_is_provided(_lookup(terms, a)) for a in aliases.get(path, ()))

    # active_required_paths encodes the conditional requires_when rules (ko_observation_dates,
    # ki_barrier) AND this family's one_of alternatives, so the completeness verdict and the
    # schema tool stay in agreement.
    active = active_required_paths(contract, terms)
    missing = [key for key in active if not _provided_path(key)]
    provided = [key for key in active if key not in missing]
    defaulted_unset = [
        key for key in contract.defaulted if not _is_provided(_lookup(terms, key))
    ]
    # Mutually-exclusive one_of groups (e.g. maturity_years|maturity_date): supplying more
    # than one member is a conflict the builder rejects.
    conflicts = [
        {"one_of": group, "provided": [m for m in members if _is_provided(_lookup(terms, m))]}
        for group, members in one_of_groups(contract).items()
        if sum(1 for m in members if _is_provided(_lookup(terms, m))) > 1
    ]
    # Representation-complete barrier ambiguity: the distinct ways to express ONE barrier are
    # {the canonical dotted/nested path} ∪ {flat abs/pct aliases}. Flag a conflict only when the
    # supplied representations resolve to DIFFERENT levels (the same value in two spellings is
    # redundant, not contradictory); `_pct` resolves against initial_price, mirroring the builder.
    init = _to_float(_lookup(terms, "initial_price"))
    for path, al in aliases.items():
        if len(al) <= 1:
            continue
        reps: list[tuple[str, float]] = []
        if path not in al and _is_provided(_lookup(terms, path)):
            v = _to_float(_lookup(terms, path))
            if v is not None:
                reps.append((path, v))
        for a in al:
            v = _to_float(_lookup(terms, a))
            if v is None:
                continue
            reps.append((a, round(init * v / 100.0, 6) if a.endswith("_pct") and init else v))
        if len({round(v, 6) for _, v in reps}) > 1:
            conflicts.append({"alias_conflict": path, "provided": [name for name, _ in reps]})
    return {
        "quantark_class": quantark_class,
        "complete": not missing and not conflicts,
        "missing_required": missing,
        "provided_required": provided,
        "defaulted_unset": defaulted_unset,
        "conflicts": conflicts,
        "note": (
            "missing_required must all be collected before pricing/booking; "
            "conflicts list mutually-exclusive fields supplied together (supply one); "
            "defaulted_unset fields fall back to desk defaults (configurable)."
        ),
    }

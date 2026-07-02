"""Advantage demonstrations for the semantic layer (ontology absorption).

Unlike test_semantic_coherence (which guards invariants), each test here
contrasts the semantic layer against the state WITHOUT it, so the value of
the design stays visible and executable:

1. inheritance  - resolved docs explain required inputs a raw file read misses
2. region parity - one artifact serves neutral and CN deployments, no leaks
3. drift        - a future contract key without doc support is isolated
                  mechanically, by name
4. coverage     - all 15 families resolve with real pricing-input semantics,
                  and the epistemic guard (prose, not axioms) is in place
"""
from __future__ import annotations

import re

from app.services.deep_agent.reference_docs import (
    REFERENCES_DIR,
    resolve_product_reference,
    validate_product_reference_tree,
)
from app.services.deep_agent.skills_paths import WORKFLOWS_DIR
from app.services.domains.product_contracts import _CONTRACTS
from app.services.domains.term_glossary import (
    REGION_TOKEN_DENYLIST,
    glossary_phrases,
)


def _pricing_inputs(text: str) -> str:
    match = re.search(r"^## Pricing Inputs$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "no '## Pricing Inputs' section"
    return re.sub(r"\s+", " ", match.group(1)).lower()


def _explained_keys(text: str, keys: tuple[str, ...]) -> set[str]:
    section = _pricing_inputs(text)
    return {
        key
        for key in keys
        if any(p.lower() in section for p in glossary_phrases(key))
    }


def test_advantage_inheritance_resolved_doc_beats_raw_file_read() -> None:
    """A raw read of the claiming doc (the pre-layer access pattern) misses
    the inherited snowball inputs; the resolver surfaces every required key."""
    contract = _CONTRACTS["PhoenixOption"]
    claims = validate_product_reference_tree(REFERENCES_DIR)
    raw_body = claims["PhoenixOption"].body
    resolved = resolve_product_reference("PhoenixOption").content

    raw_cov = _explained_keys(raw_body, contract.required_bound)
    resolved_cov = _explained_keys(resolved, contract.required_bound)

    assert resolved_cov == set(contract.required_bound)
    only_via_resolver = set(contract.required_bound) - raw_cov
    # The delta IS the advantage: inherited base economics a raw file read
    # would silently drop (agent would then bless an incomplete term set
    # that build_product rejects).
    assert "barrier_config.ki_barrier" in only_via_resolver
    assert "observation_frequency" in only_via_resolver
    assert len(only_via_resolver) >= 5, (
        f"expected a substantial inherited gap, got {sorted(only_via_resolver)}"
    )


def test_advantage_region_parity_one_artifact_two_deployments() -> None:
    """The same doc set serves a region-neutral deployment (no overlay, no
    leaked region tokens) and a CN desk (overlay appended) - previously the
    only doc was unconditionally CN."""
    neutral = resolve_product_reference("SnowballOption").content
    cn = resolve_product_reference("SnowballOption", region="CN").content

    assert "## Regional Conventions" not in neutral
    for token in REGION_TOKEN_DENYLIST:
        assert token not in neutral

    assert "## Regional Conventions (CN)" in cn
    assert "SSE" in cn  # CN desk keeps its market conventions
    # and the CN view still contains the full neutral base, not a fork of it
    assert _explained_keys(cn, _CONTRACTS["SnowballOption"].required_bound) == set(
        _CONTRACTS["SnowballOption"].required_bound
    )


def test_advantage_drift_is_detected_mechanically_and_named() -> None:
    """If a contract grows a key tomorrow and nobody updates docs/glossary,
    the coherence check isolates exactly that key - the 'reasoner' function
    an unchecked prose library (or a stale ontology) cannot provide."""
    grown = _CONTRACTS["SnowballOption"].required_bound + ("brand_new_axis",)
    resolved = resolve_product_reference("SnowballOption").content

    unexplained = [
        key
        for key in grown
        if not glossary_phrases(key)
        or key not in _explained_keys(resolved, (key,))
    ]
    assert unexplained == ["brand_new_axis"]


def test_advantage_full_family_coverage_with_epistemic_guard() -> None:
    """Before the layer: 1 of 15 families had semantics. Now every contract
    class resolves to real pricing-input prose, and the interpretation
    skills carry the do-not-infer stop condition - procedural knowledge a
    formal ontology has no slot for."""
    claims = validate_product_reference_tree(REFERENCES_DIR)
    assert set(claims) == set(_CONTRACTS)
    for quantark_class in _CONTRACTS:
        content = resolve_product_reference(quantark_class).content
        assert len(_pricing_inputs(content).strip()) > 40, quantark_class

    for skill in ("products/product-term-interpretation", "snowballs/snowball-term-interpretation"):
        text = (WORKFLOWS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "Do not infer" in text, skill

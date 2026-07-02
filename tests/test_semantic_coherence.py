"""Semantic coherence net: prose reference docs must match FamilyContracts.

The 'reasoner' of the semantic layer - fails CI when a contract gains a key
the claiming doc does not explain, when a family loses its doc, or when a
region-neutral doc leaks region-market tokens."""
from __future__ import annotations

import re

import pytest

from app.services.deep_agent.reference_docs import (
    REFERENCES_DIR,
    resolve_product_reference,
    validate_product_reference_tree,
    validate_reference_doc_tree,
)
from app.services.domains.product_contracts import _CONTRACTS
from app.services.domains.term_glossary import (
    REGION_TOKEN_DENYLIST,
    TERM_GLOSSARY,
    glossary_phrases,
)


def _pricing_inputs_section(content: str) -> str:
    match = re.search(r"^## Pricing Inputs$(.*?)(?=^## |\Z)", content, re.M | re.S)
    assert match, "resolved doc has no '## Pricing Inputs' section"
    # Collapse whitespace so hard-wrapped phrases ("upper\nbarrier") still
    # match glossary phrases - the check is semantic, not typographic.
    return re.sub(r"\s+", " ", match.group(1))


def test_every_contract_class_is_claimed_exactly_once() -> None:
    claims = validate_product_reference_tree(REFERENCES_DIR)
    assert set(claims) == set(_CONTRACTS)


@pytest.mark.parametrize("quantark_class", sorted(_CONTRACTS))
def test_required_bound_keys_explained(quantark_class: str) -> None:
    resolved = resolve_product_reference(quantark_class)
    section = _pricing_inputs_section(resolved.content).lower()
    missing = []
    for key in _CONTRACTS[quantark_class].required_bound:
        phrases = glossary_phrases(key)
        assert phrases, f"glossary has no phrases for contract key {key!r}"
        if not any(p.lower() in section for p in phrases):
            missing.append(key)
    assert not missing, (
        f"{quantark_class}: required keys not explained in resolved "
        f"Pricing Inputs (doc {resolved.source_paths}): {missing}"
    )


def test_glossary_has_no_dead_entries() -> None:
    live: set[str] = set()
    for contract in _CONTRACTS.values():
        for key in contract.required_bound + contract.defaulted:
            live.add(key)
            live.add(key.rsplit(".", 1)[-1])
    dead = set(TERM_GLOSSARY) - live
    assert not dead, f"glossary entries match no contract key: {sorted(dead)}"


def test_family_docs_are_region_neutral() -> None:
    docs = validate_reference_doc_tree(REFERENCES_DIR)
    offenders = []
    for doc in docs:
        if doc.frontmatter.get("reference_type") != "product":
            continue
        if doc.frontmatter.get("region") is not None:
            continue  # explicit overlay - exempt
        for token in REGION_TOKEN_DENYLIST:
            if token in doc.body:
                offenders.append((doc.path.name, token))
    assert not offenders, f"region tokens in neutral docs: {offenders}"


def test_resolved_neutral_docs_are_region_neutral() -> None:
    claims = validate_product_reference_tree(REFERENCES_DIR)
    for quantark_class in claims:
        resolved = resolve_product_reference(quantark_class)  # region=None
        for token in REGION_TOKEN_DENYLIST:
            assert token not in resolved.content, (
                f"{quantark_class}: neutral resolution leaks region token {token!r}"
            )


def test_no_workflow_skill_reads_raw_product_docs() -> None:
    """Workflow skills must load product semantics via get_product_reference_doc.

    A raw read of products/snowball-cn.md now returns an overlay-only doc
    (no payoff/pricing-input semantics) - any skill still pointing there
    silently loses the base content the resolver would have merged in."""
    from app.services.deep_agent.skills_paths import WORKFLOWS_DIR

    offenders = [
        path.relative_to(WORKFLOWS_DIR).as_posix()
        for path in WORKFLOWS_DIR.rglob("SKILL.md")
        if "references/products/snowball-cn.md" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"skills reading the raw snowball-cn overlay: {offenders}"

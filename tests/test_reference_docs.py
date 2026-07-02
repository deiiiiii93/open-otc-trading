"""Phase 3.5 reference-document migration tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.deep_agent.reference_docs import (
    VALID_REFERENCE_TYPES,
    parse_reference_doc,
    resolve_product_reference,
    validate_product_reference_tree,
    validate_reference_doc_file,
    validate_reference_doc_tree,
)
from app.services.deep_agent.skills_paths import REFERENCES_DIR, SKILLS_ROOT


EXPECTED_REFERENCE_FILES = {
    "products/snowball-cn.md",
    "products/build-contract.md",
    "pricing/engines.md",
    "pricing/parameters.md",
    "market-data/conventions.md",
    "portfolios/model.md",
    "rfq/lifecycle.md",
    "hedging/strategy.md",
    "risk/scenario-test.md",
    "risk/backtest.md",
}

ARCHAEOLOGY_PATTERN = re.compile(
    r"commit `[0-9a-f]{6,}`|v1 (commit|anchor|added)|fixed this mistake|grandfathered|(?:--|\u2014)v\d",
    re.IGNORECASE,
)


def _relative_reference_files() -> set[str]:
    return {
        path.relative_to(REFERENCES_DIR).as_posix()
        for path in REFERENCES_DIR.rglob("*.md")
    }


def test_reference_doc_file_set_matches_phase3_target() -> None:
    assert _relative_reference_files() == EXPECTED_REFERENCE_FILES


def test_reference_docs_have_valid_schema() -> None:
    docs = validate_reference_doc_tree(REFERENCES_DIR)

    assert {doc.path.relative_to(REFERENCES_DIR).as_posix() for doc in docs} == EXPECTED_REFERENCE_FILES
    assert {doc.frontmatter["reference_type"] for doc in docs} <= VALID_REFERENCE_TYPES
    for doc in docs:
        assert doc.frontmatter["name"] == doc.path.stem
        assert doc.body.startswith("## ")
        assert "source_legacy_skill" not in doc.frontmatter


def test_reference_doc_schema_no_longer_requires_legacy_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecycle.md"
    path.write_text(
        """---
name: lifecycle
description: Reference source is durable after the legacy catalog is deleted.
reference_type: rfq
---

## Valid

This body is valid enough for frontmatter validation.
""",
        encoding="utf-8",
    )

    assert validate_reference_doc_file(path).frontmatter["name"] == "lifecycle"


def test_reference_docs_are_not_executable_skill_files() -> None:
    assert not any(path.name == "SKILL.md" for path in REFERENCES_DIR.rglob("*"))


def test_legacy_source_skills_are_removed() -> None:
    assert not (SKILLS_ROOT / "legacy").exists()


def test_reference_docs_have_no_archaeology_markers() -> None:
    for path in REFERENCES_DIR.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not ARCHAEOLOGY_PATTERN.search(text), path


def test_rfq_lifecycle_reference_matches_runtime_status_contract() -> None:
    doc = parse_reference_doc(REFERENCES_DIR / "rfq" / "lifecycle.md")

    for status in {
        "draft",
        "submitted",
        "pricing_failed",
        "pending_approval",
        "approved",
        "rejected",
        "released",
        "client_accepted",
        "booked",
    }:
        assert f"`{status}`" in doc.body
    assert "`quoted`" not in doc.body
    assert "submitted for approval" not in doc.body.lower()


# --- product-reference tree validation (semantic-layer completion) ---


def _write_doc(root: Path, rel: str, front: str, body: str = "## Product Definition\n\nx.\n\n## Pricing Inputs\n\ny.\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}---\n\n{body}", encoding="utf-8")


def _products_tree(tmp_path: Path) -> Path:
    root = tmp_path / "references"
    _write_doc(
        root, "products/base.md",
        "name: base\ndescription: Base.\nreference_type: product\nquantark_classes: [SnowballOption]\n",
    )
    return root


def test_product_tree_resolves_claims(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    claims = validate_product_reference_tree(root)
    assert claims["SnowballOption"].frontmatter["name"] == "base"


def test_product_tree_rejects_duplicate_claim(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(
        root, "products/dup.md",
        "name: dup\ndescription: Dup.\nreference_type: product\nquantark_classes: [SnowballOption]\n",
    )
    with pytest.raises(ValueError, match="claimed by more than one"):
        validate_product_reference_tree(root)


def test_product_tree_rejects_unknown_extends(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(
        root, "products/child.md",
        "name: child\ndescription: C.\nreference_type: product\nextends: nope\n",
    )
    with pytest.raises(ValueError, match="unknown extends target"):
        validate_product_reference_tree(root)


def test_product_tree_rejects_deep_chain(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(root, "products/mid.md", "name: mid\ndescription: M.\nreference_type: product\nextends: base\n")
    _write_doc(root, "products/leaf.md", "name: leaf\ndescription: L.\nreference_type: product\nextends: mid\n")
    with pytest.raises(ValueError, match="extends chain deeper than one"):
        validate_product_reference_tree(root)


def test_product_tree_region_inheritance_guard(tmp_path: Path) -> None:
    root = _products_tree(tmp_path)
    _write_doc(
        root, "products/cn-base.md",
        "name: cn-base\ndescription: CN.\nreference_type: product\nregion: CN\n",
    )
    _write_doc(
        root, "products/neutral-child.md",
        "name: neutral-child\ndescription: N.\nreference_type: product\nextends: cn-base\n",
    )
    with pytest.raises(ValueError, match="region-marked base"):
        validate_product_reference_tree(root)


def test_frontmatter_key_shapes_rejected(tmp_path: Path) -> None:
    root = tmp_path / "references"
    _write_doc(
        root, "products/bad.md",
        "name: bad\ndescription: B.\nreference_type: product\nquantark_classes: notalist\n",
    )
    with pytest.raises(ValueError, match="quantark_classes must be a non-empty list"):
        validate_product_reference_tree(root)


# --- resolver (semantic-layer completion) ---


def _inheritance_tree(tmp_path: Path) -> Path:
    root = tmp_path / "references"
    _write_doc(
        root, "products/base.md",
        "name: base\ndescription: Base.\nreference_type: product\nquantark_classes: [SnowballOption]\n",
        "## Product Definition\n\nAutocallable payoff.\n\n## Pricing Inputs\n\nKO barrier, KI barrier.\n",
    )
    _write_doc(
        root, "products/variants.md",
        "name: variants\ndescription: V.\nreference_type: product\n"
        "quantark_classes: [PhoenixOption]\nextends: base\n",
        "## Pricing Inputs\n\nCoupon barrier, coupon rate.\n",
    )
    _write_doc(
        root, "products/base-cn.md",
        "name: base-cn\ndescription: CN overlay.\nreference_type: product\n"
        "region: CN\nextends: base\n",
        "## Observation Conventions\n\nLocal-market business days (overlay).\n",
    )
    return root


def test_resolver_plain_class(tmp_path: Path) -> None:
    resolved = resolve_product_reference("SnowballOption", root=_inheritance_tree(tmp_path))
    assert "Autocallable payoff" in resolved.content
    assert "overlay" not in resolved.content  # no region requested


def test_resolver_merges_base_before_child(tmp_path: Path) -> None:
    resolved = resolve_product_reference("PhoenixOption", root=_inheritance_tree(tmp_path))
    assert resolved.content.index("KO barrier") < resolved.content.index("Coupon barrier")
    assert len(resolved.source_paths) == 2
    # Per-heading merge: base + child Pricing Inputs fold into ONE section —
    # a flat body concat would leave two "## Pricing Inputs" headings and the
    # coherence net's single-match section regex would drop the child's text.
    assert resolved.content.count("## Pricing Inputs") == 1


def test_resolver_appends_region_overlay(tmp_path: Path) -> None:
    resolved = resolve_product_reference(
        "SnowballOption", region="CN", root=_inheritance_tree(tmp_path)
    )
    assert "## Regional Conventions (CN)" in resolved.content
    assert resolved.content.index("Autocallable payoff") < resolved.content.index("overlay")


def test_resolver_unknown_class(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        resolve_product_reference("NopeOption", root=_inheritance_tree(tmp_path))

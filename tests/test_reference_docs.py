"""Phase 3.5 reference-document migration tests."""
from __future__ import annotations

import re
from pathlib import Path

from app.services.deep_agent.reference_docs import (
    VALID_REFERENCE_TYPES,
    parse_reference_doc,
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

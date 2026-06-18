"""Phase 3.4 meta-policy migration tests."""
from __future__ import annotations

import os

from app.services.deep_agent.skills_loader import validate_meta_policy_tree
from app.services.deep_agent.skills_paths import META_DIR, SKILLS_ROOT


EXPECTED_META_FILES = {
    "clarification-policy.md",
    "cost-preview-policy.md",
    "desk-async-contract.md",
    "desk-workflow-contract.md",
    "escalation-policy.md",
    "page-context-contract.md",
    "pet-diagnostic-contract.md",
    "pet-page-contract.md",
    "python-analysis-policy.md",
    "read-before-compute-policy.md",
    "reply-options-policy.md",
    "yolo-hitl-policy.md",
}


def test_meta_policy_file_set_matches_phase3_target() -> None:
    actual = {path.name for path in META_DIR.glob("*.md")}

    assert actual == EXPECTED_META_FILES


def test_meta_policy_files_have_valid_schema() -> None:
    fragments = validate_meta_policy_tree(META_DIR)

    assert {fragment.path.name for fragment in fragments} == EXPECTED_META_FILES
    for fragment in fragments:
        assert fragment.frontmatter["name"] == fragment.path.stem
        assert fragment.body.startswith("## ")


def test_legacy_policy_paths_are_removed() -> None:
    assert not (SKILLS_ROOT / "legacy" / "policy").exists()
    assert not os.path.lexists(SKILLS_ROOT / "policy")

"""CI-mode lint tests for Phase 3 skill catalog enforcement."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deep_agent.skill_lint import (
    assert_no_skill_lint_errors,
    format_lint_errors,
    lint_skill_file,
    lint_skill_tree,
)
from app.services.deep_agent.skills_paths import SKILLS_ROOT


def _write_skill(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bad_skill(name: str) -> str:
    body = "\n".join(f"- repeated body detail {i}" for i in range(260))
    long_description = (
        "Documentation for a skill with a description that is intentionally "
        "longer than the CI threshold. " + "extra detail " * 90
    )
    return f"""---
name: {name}
description: {long_description}
workflow_type: unknown
allowed_envelopes:
  - invalid_env
---

Body mentions commit abc1234 and omits the required example section.
{body}
"""


def test_ci_mode_marks_lint_as_errors_even_under_legacy_named_directory(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "workflows" / "risk" / "bad-risk" / "SKILL.md",
        _bad_skill("bad-risk"),
    )
    _write_skill(
        tmp_path / "legacy" / "workflows" / "old-risk" / "SKILL.md",
        _bad_skill("old-risk"),
    )

    warnings = lint_skill_tree(tmp_path, mode="ci")

    non_legacy = [
        warning
        for warning in warnings
        if "bad-risk" in warning.path.parts
    ]
    legacy = [
        warning
        for warning in warnings
        if "old-risk" in warning.path.parts
    ]
    assert non_legacy
    assert legacy
    assert {warning.severity for warning in non_legacy} == {"error"}
    assert {warning.severity for warning in legacy} == {"error"}
    assert any(warning.code == "body_length" for warning in non_legacy)
    assert any(warning.code == "body_length" for warning in legacy)


def test_warn_mode_keeps_non_legacy_lint_as_warnings(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "workflows" / "risk" / "bad-risk" / "SKILL.md",
        _bad_skill("bad-risk"),
    )

    warnings = lint_skill_tree(tmp_path)

    assert warnings
    assert {warning.severity for warning in warnings} == {"warning"}


def test_lint_skill_file_ci_mode_does_not_downgrade_legacy_scope(
    tmp_path: Path,
) -> None:
    skill_path = _write_skill(
        tmp_path / "legacy" / "workflows" / "old-risk" / "SKILL.md",
        _bad_skill("old-risk"),
    )

    warnings = lint_skill_file(skill_path, mode="ci")

    assert warnings
    assert {warning.severity for warning in warnings} == {"error"}


def test_assert_no_skill_lint_errors_raises_with_summary(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "workflows" / "risk" / "bad-risk" / "SKILL.md",
        _bad_skill("bad-risk"),
    )

    with pytest.raises(AssertionError) as exc:
        assert_no_skill_lint_errors(tmp_path)

    message = str(exc.value)
    assert "Skill lint errors:" in message
    assert "description_length" in message
    assert "missing_example" in message
    assert str(skill_path) in message


def test_format_lint_errors_includes_only_errors(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "workflows" / "risk" / "bad-risk" / "SKILL.md",
        _bad_skill("bad-risk"),
    )
    _write_skill(
        tmp_path / "legacy" / "workflows" / "old-risk" / "SKILL.md",
        _bad_skill("old-risk"),
    )

    message = format_lint_errors(lint_skill_tree(tmp_path, mode="ci"))

    assert "bad-risk" in message
    assert "old-risk" in message


def test_current_catalog_has_no_ci_blocking_skill_lint_errors() -> None:
    warnings = assert_no_skill_lint_errors(SKILLS_ROOT)

    assert warnings == []

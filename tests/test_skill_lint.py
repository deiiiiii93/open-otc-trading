"""Warn-only lint tests for Phase 3 skill catalog quality."""
from __future__ import annotations

from pathlib import Path

from app.services.deep_agent.skill_lint import (
    SKILL_LINT_REQUIRED_FRONTMATTER,
    SkillLintWarning,
    iter_skill_files,
    lint_skill_file,
    lint_skill_tree,
    parse_skill_file,
)
from app.services.deep_agent.skills_paths import SKILLS_ROOT


def _write_skill(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _codes(warnings: list[SkillLintWarning]) -> set[str]:
    return {warning.code for warning in warnings}


def test_parse_skill_file_returns_frontmatter_and_body(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "alpha" / "SKILL.md",
        """---
name: alpha
description: Short useful description
workflow_type: diagnostic
allowed_envelopes:
  - pet_page
may_escalate_to:
  - pet_diagnostic
required_context: []
optional_context: []
write_actions: []
confirmation_required: false
success_criteria:
  - Answers with page facts
---

## Example

User: How many positions are loaded?
Assistant: The page has 64 positions.
""",
    )

    parsed = parse_skill_file(skill_path)

    assert parsed.frontmatter["name"] == "alpha"
    assert parsed.frontmatter["workflow_type"] == "diagnostic"
    assert "## Example" in parsed.body


def test_lint_warns_for_missing_or_bad_frontmatter(tmp_path: Path) -> None:
    long_description = "Documentation " + ("for maintainers " * 80)
    skill_path = _write_skill(
        tmp_path / "broken" / "SKILL.md",
        f"""---
name: broken
description: {long_description}
workflow_type: mystery
allowed_envelopes:
  - invalid_env
---

Body without the required example heading.
""",
    )

    warnings = lint_skill_file(skill_path)

    assert _codes(warnings) >= {
        "description_length",
        "description_prefix",
        "missing_example",
        "missing_frontmatter_field",
        "invalid_workflow_type",
        "invalid_allowed_envelope",
    }
    missing = [
        warning
        for warning in warnings
        if warning.code == "missing_frontmatter_field"
    ]
    assert {warning.detail for warning in missing} == (
        SKILL_LINT_REQUIRED_FRONTMATTER
        - {
            "name",
            "description",
            "workflow_type",
            "allowed_envelopes",
        }
    )


def test_lint_warns_for_archaeology_markers(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "history" / "SKILL.md",
        """---
name: history
description: Short useful description
---

## Procedure

Keep behavior from commit c0ae172 and the v1 anchor.
This flow was grandfathered from a previous catalog.
""",
    )

    warnings = lint_skill_file(skill_path)

    archaeology = [
        warning.detail
        for warning in warnings
        if warning.code == "archaeology_marker"
    ]
    assert any("commit" in detail for detail in archaeology)
    assert any("v1" in detail for detail in archaeology)
    assert any("grandfathered" in detail for detail in archaeology)


def test_lint_warns_for_frontmatter_archaeology_markers(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "frontmatter-history" / "SKILL.md",
        """---
name: frontmatter-history
description: Retrofits the v1 manual orchestration into a named flow.
---

## Example

User: Check the snowball book.
Assistant: I will use the named routing flow.
""",
    )

    warnings = lint_skill_file(skill_path)

    archaeology = [
        warning.detail
        for warning in warnings
        if warning.code == "archaeology_marker"
    ]
    assert any("v1 manual orchestration" in detail for detail in archaeology)


def test_lint_warns_for_broad_v1_v2_notes(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "broad-history" / "SKILL.md",
        """---
name: broad-history
description: Short useful description
---

## Example

User: Review this flow.
Assistant: It should not carry v2 compact routing notes.
""",
    )

    warnings = lint_skill_file(skill_path)

    archaeology = [
        warning.detail
        for warning in warnings
        if warning.code == "archaeology_marker"
    ]
    assert any("v2 compact routing" in detail for detail in archaeology)


def test_lint_warns_for_body_length(tmp_path: Path) -> None:
    body = "\n".join(f"- repeated policy detail {i}" for i in range(260))
    skill_path = _write_skill(
        tmp_path / "long-body" / "SKILL.md",
        f"""---
name: long-body
description: Short useful description
domain: risk
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to: []
required_context: []
optional_context: []
write_actions: false
confirmation_required: false
success_criteria:
  - Body length warning is emitted
---

## Example

User: Check long skill.
Assistant: Report that the body is too long.

{body}
""",
    )

    warnings = lint_skill_file(skill_path)

    assert "body_length" in _codes(warnings)


def test_lint_reports_invalid_yaml_without_raising(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "bad-yaml" / "SKILL.md",
        """---
name: bad-yaml
description: [unterminated
---

## Example

User: Check this skill.
Assistant: It should return a lint warning.
""",
    )

    warnings = lint_skill_file(skill_path)

    assert _codes(warnings) >= {"invalid_frontmatter", "missing_frontmatter_field"}


def test_parse_skill_file_accepts_common_frontmatter_fences(tmp_path: Path) -> None:
    crlf_path = tmp_path / "crlf" / "SKILL.md"
    crlf_path.parent.mkdir(parents=True, exist_ok=True)
    crlf_path.write_bytes(
        b"---\r\nname: crlf\r\ndescription: Short useful description\r\n---\r\n"
        b"\r\n## Example\r\n\r\nUser: Count positions.\r\nAssistant: 64.\r\n"
    )

    crlf = parse_skill_file(crlf_path)

    assert crlf.frontmatter_error is None
    assert crlf.frontmatter["name"] == "crlf"
    assert "## Example" in crlf.body

    eof_path = _write_skill(
        tmp_path / "eof" / "SKILL.md",
        """---
name: eof
description: Short useful description
---""",
    )

    eof = parse_skill_file(eof_path)

    assert eof.frontmatter_error is None
    assert eof.frontmatter["name"] == "eof"
    assert eof.body == ""


def test_iter_skill_files_discovers_only_skill_markdown(tmp_path: Path) -> None:
    expected = _write_skill(
        tmp_path / "workflows" / "risk" / "risk-run" / "SKILL.md",
        """---
name: risk-run
description: Short useful description
---

## Example

User: Rerun risk.
Assistant: I will use the page action.
""",
    )
    _write_skill(tmp_path / "README.md", "# Not a skill\n")
    _write_skill(tmp_path / "policy" / "routing.md", "# Policy markdown\n")

    assert list(iter_skill_files(tmp_path)) == [expected]


def test_current_catalog_lint_is_clean_after_phase3_rewrite() -> None:
    warnings = lint_skill_tree(SKILLS_ROOT)

    assert isinstance(warnings, list)
    assert warnings == []

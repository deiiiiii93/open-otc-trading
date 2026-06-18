# Phase 3 Skill Lint Warn-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Phase 3.2 skill catalog lint in warn-only mode so the relocated legacy catalog can be inspected without blocking runtime or CI yet.

**Architecture:** Add a small pure-Python lint module beside the existing deep-agent skill path helpers. The linter parses only `SKILL.md` files under `backend/app/skills/`, reports structured warnings, and never raises for catalog-quality violations; tests prove both synthetic invalid skills and the current legacy catalog are handled in warn-only mode.

**Tech Stack:** Python 3.11, `pathlib`, `dataclasses`, `re`, PyYAML, pytest.

---

## Spec Slice

This plan implements only Phase 3.2 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Warn on `description` length over 200 characters.
- Warn on missing `## Example` or `## Examples`.
- Warn on implementation-history archaeology markers such as commit hashes, v1/v2 notes, and grandfathering text.
- Warn on malformed or incomplete frontmatter and invalid future workflow-schema values.
- Keep warnings non-blocking for the current legacy catalog.

This plan does not migrate `meta/`, `references/`, or `workflows/` skills, and it does not flip lint warnings into CI failures. Those are later Phase 3 PRs.

## File Structure

- Create `backend/app/services/deep_agent/skill_lint.py`
  - Owns parsing, warning data structures, skill-file discovery, and lint checks.
  - Imports `SKILLS_ROOT` from `backend/app/services/deep_agent/skills_paths.py`.
  - Exposes `SkillLintWarning`, `parse_skill_file`, `iter_skill_files`, `lint_skill_file`, and `lint_skill_tree`.
- Create `tests/test_skill_lint.py`
  - Verifies single-file warning behavior with temporary skills.
  - Verifies discovery only targets `SKILL.md`.
  - Verifies the real relocated catalog is linted in warn-only mode.
- Do not modify existing catalog files in this slice.

## Task 1: Add Failing Skill-Lint Tests

**Files:**
- Create: `tests/test_skill_lint.py`

- [x] **Step 1: Write the failing test file**

Create `tests/test_skill_lint.py` with this content:

```python
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
    skill_path = _write_skill(
        tmp_path / "broken" / "SKILL.md",
        """---
name: broken
description: Documentation for a skill with a description that is deliberately made longer than two hundred characters so the Phase 3 warning is exercised with a deterministic and easy to read fixture for reviewers and future maintainers.
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
        SKILL_LINT_REQUIRED_FRONTMATTER - {
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


def test_current_catalog_lint_is_warn_only() -> None:
    warnings = lint_skill_tree(SKILLS_ROOT)

    assert isinstance(warnings, list)
    assert warnings
    assert any(warning.code == "missing_example" for warning in warnings)
    assert all(warning.path.name == "SKILL.md" for warning in warnings)
```

- [x] **Step 2: Run the new tests to verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py -q
```

Expected: collection fails with `ModuleNotFoundError` for `app.services.deep_agent.skill_lint`, proving the production module does not exist yet.

## Task 2: Implement Warn-Only Skill Lint

**Files:**
- Create: `backend/app/services/deep_agent/skill_lint.py`
- Test: `tests/test_skill_lint.py`

- [x] **Step 1: Add the linter implementation**

Create `backend/app/services/deep_agent/skill_lint.py` with this content:

```python
"""Warn-only lint helpers for Phase 3 skill catalog quality."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.services.deep_agent.skills_paths import SKILLS_ROOT


SKILL_LINT_REQUIRED_FRONTMATTER = {
    "name",
    "description",
    "domain",
    "workflow_type",
    "allowed_envelopes",
    "may_escalate_to",
    "required_context",
    "optional_context",
    "write_actions",
    "confirmation_required",
    "success_criteria",
}

VALID_WORKFLOW_TYPES = {"diagnostic", "action", "read", "compound"}
VALID_ENVELOPES = {"pet_page", "pet_diagnostic", "desk_workflow", "desk_async"}
DESCRIPTION_MAX_CHARS = 200
DESCRIPTION_BAD_PREFIXES = ("this skill", "skill for", "documentation")

ARCHAEOLOGY_PATTERNS = (
    re.compile(r"\bcommit\s+`?[0-9a-f]{6,}`?", re.IGNORECASE),
    re.compile(r"\bv[0-9]+\s+(?:commit|anchor|added|addition|additions)\b", re.IGNORECASE),
    re.compile(r"\bfixed this mistake\b", re.IGNORECASE),
    re.compile(r"\bgrandfathered\b", re.IGNORECASE),
    re.compile(r"[\u2014-]v[0-9]+\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ParsedSkill:
    path: Path
    frontmatter: dict[str, Any]
    body: str
    frontmatter_error: str | None = None


@dataclass(frozen=True)
class SkillLintWarning:
    path: Path
    code: str
    message: str
    detail: str = ""


def iter_skill_files(root: Path = SKILLS_ROOT) -> list[Path]:
    """Return skill files in deterministic order under `root`."""
    return sorted(Path(root).glob("**/SKILL.md"))


def parse_skill_file(path: Path) -> ParsedSkill:
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return ParsedSkill(path=Path(path), frontmatter={}, body=text)

    _, remainder = text.split("---\n", 1)
    if "\n---\n" not in remainder:
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=remainder,
            frontmatter_error="missing closing frontmatter fence",
        )

    raw_frontmatter, body = remainder.split("\n---\n", 1)
    try:
        loaded = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=body,
            frontmatter_error=str(exc),
        )

    if not isinstance(loaded, dict):
        return ParsedSkill(
            path=Path(path),
            frontmatter={},
            body=body,
            frontmatter_error="frontmatter root must be a mapping",
        )

    return ParsedSkill(path=Path(path), frontmatter=loaded, body=body)


def lint_skill_tree(root: Path = SKILLS_ROOT) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    for skill_path in iter_skill_files(root):
        warnings.extend(lint_skill_file(skill_path))
    return warnings


def lint_skill_file(path: Path) -> list[SkillLintWarning]:
    parsed = parse_skill_file(path)
    warnings: list[SkillLintWarning] = []

    if parsed.frontmatter_error:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="invalid_frontmatter",
                message="Skill frontmatter could not be parsed as a YAML mapping.",
                detail=parsed.frontmatter_error,
            )
        )

    warnings.extend(_lint_frontmatter(parsed))
    warnings.extend(_lint_body(parsed))
    return warnings


def _lint_frontmatter(parsed: ParsedSkill) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    frontmatter = parsed.frontmatter

    for field in sorted(SKILL_LINT_REQUIRED_FRONTMATTER - set(frontmatter)):
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="missing_frontmatter_field",
                message=f"Skill frontmatter is missing required field '{field}'.",
                detail=field,
            )
        )

    description = frontmatter.get("description")
    if isinstance(description, str):
        if len(description) > DESCRIPTION_MAX_CHARS:
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="description_length",
                    message="Skill description exceeds 200 characters.",
                    detail=str(len(description)),
                )
            )
        lowered = description.strip().lower()
        if lowered.startswith(DESCRIPTION_BAD_PREFIXES):
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="description_prefix",
                    message="Skill description starts with a generic prefix.",
                    detail=description.split(maxsplit=3)[0].lower(),
                )
            )

    workflow_type = frontmatter.get("workflow_type")
    if workflow_type is not None and workflow_type not in VALID_WORKFLOW_TYPES:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="invalid_workflow_type",
                message="Skill workflow_type is not in the allowed Phase 3 set.",
                detail=str(workflow_type),
            )
        )

    allowed_envelopes = frontmatter.get("allowed_envelopes")
    if allowed_envelopes is not None:
        if not isinstance(allowed_envelopes, list):
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="invalid_allowed_envelope",
                    message="Skill allowed_envelopes must be a list.",
                    detail=type(allowed_envelopes).__name__,
                )
            )
        else:
            for envelope in allowed_envelopes:
                if envelope not in VALID_ENVELOPES:
                    warnings.append(
                        SkillLintWarning(
                            path=parsed.path,
                            code="invalid_allowed_envelope",
                            message="Skill allowed_envelopes contains an unknown envelope.",
                            detail=str(envelope),
                        )
                    )

    return warnings


def _lint_body(parsed: ParsedSkill) -> list[SkillLintWarning]:
    warnings: list[SkillLintWarning] = []
    if "## Example" not in parsed.body and "## Examples" not in parsed.body:
        warnings.append(
            SkillLintWarning(
                path=parsed.path,
                code="missing_example",
                message="Skill body is missing a '## Example' or '## Examples' section.",
            )
        )

    for pattern in ARCHAEOLOGY_PATTERNS:
        match = pattern.search(parsed.body)
        if match:
            warnings.append(
                SkillLintWarning(
                    path=parsed.path,
                    code="archaeology_marker",
                    message="Skill body contains implementation-history archaeology.",
                    detail=match.group(0),
                )
            )

    return warnings
```

- [x] **Step 2: Run the new tests to verify GREEN**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py -q
```

Expected: all tests in `tests/test_skill_lint.py` pass.

## Task 3: Focused Regression Verification

**Files:**
- Verify: `backend/app/services/deep_agent/skill_lint.py`
- Verify: `tests/test_skill_lint.py`
- Verify: existing skill catalog tests

- [x] **Step 1: Run lint and catalog regression tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_skills_loader.py -q
```

Expected: all selected tests pass.

- [x] **Step 2: Run diff hygiene checks**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [x] **Step 3: Review the changed file list**

Run:

```bash
git status --short
```

Expected: only these files are changed:

```text
A  backend/app/services/deep_agent/skill_lint.py
A  docs/superpowers/plans/2026-05-20-phase-3-skill-lint-warn-only.md
A  tests/test_skill_lint.py
```

## Task 4: Workflow Review And Commit

**Files:**
- Review: `backend/app/services/deep_agent/skill_lint.py`
- Review: `tests/test_skill_lint.py`
- Review: `docs/superpowers/plans/2026-05-20-phase-3-skill-lint-warn-only.md`

- [x] **Step 1: Run the plan self-review**

Check this plan against the spec slice:

```text
P3.2 description-length warning: Task 1 and Task 2 cover description_length.
P3.2 missing Example warning: Task 1 and Task 2 cover missing_example.
P3.2 archaeology marker warning: Task 1 and Task 2 cover archaeology_marker.
P3.2 frontmatter validity warning: Task 1 and Task 2 cover invalid_frontmatter, missing_frontmatter_field, invalid_workflow_type, and invalid_allowed_envelope.
P3.2 warn-only behavior: Task 1 verifies lint_skill_tree(SKILLS_ROOT) returns warnings without failing the current catalog.
```

- [x] **Step 2: Request code review**

Review scope:

```text
Review the uncommitted diff against this plan and Phase 3.2 of the canonical spec.
Focus on warn-only behavior, frontmatter parsing edge cases, archaeology marker coverage, and test adequacy.
```

Expected: reviewer returns no Critical findings. Important findings must be patched before commit.

- [x] **Step 3: Add review regression tests**

Add tests in `tests/test_skill_lint.py` for:

```text
frontmatter archaeology: description contains "v1 manual orchestration"
broad v1/v2 notes: body contains "v2 compact routing notes"
common frontmatter fences: CRLF content and a closing --- fence at EOF
```

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py -q
```

Expected before implementation patch: FAIL on those three new cases.

- [x] **Step 4: Patch review findings**

Update `backend/app/services/deep_agent/skill_lint.py` so:

```text
parse_skill_file uses a frontmatter regex that accepts LF, CRLF, and closing fence at EOF.
ARCHAEOLOGY_PATTERNS catches broad v1/v2 phrases such as "v1 manual orchestration" and "v2 compact routing".
archaeology lint scans the body plus all string frontmatter values because descriptions are agent-visible.
```

- [x] **Step 5: Verify patched review tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py -q
```

Expected: all tests in `tests/test_skill_lint.py` pass after the patch.

- [x] **Step 6: Stage the intended files**

Run:

```bash
git add backend/app/services/deep_agent/skill_lint.py tests/test_skill_lint.py docs/superpowers/plans/2026-05-20-phase-3-skill-lint-warn-only.md
```

Expected: the three files are staged.

- [x] **Step 7: Commit the Phase 3.2 slice**

Run:

```bash
git commit -m "test(skills): add warn-only phase 3 lint"
```

Expected: commit succeeds on branch `codex/skill-lint-phase3`.

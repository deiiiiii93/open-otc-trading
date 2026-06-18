# Phase 3 Skill Lint CI Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flip Phase 3 skill lint from warn-only reporting to CI-failing enforcement for migrated skill trees while preserving legacy warning visibility until the planned migrations remove `legacy/`.

**Architecture:** Extend `backend/app/services/deep_agent/skill_lint.py` with a CI mode that classifies existing lint warnings as either `warning` or `error`. P3.3 hard-fails description quality, missing examples, archaeology markers, and frontmatter validity for non-legacy skill paths; `legacy/` remains warn-only because P3.4-P3.8 migrate that catalog and P3.9 deletes it.

**Tech Stack:** Python 3.11, `pathlib`, `dataclasses`, `typing.Literal`, pytest.

---

## Spec Slice

This plan implements Phase 3.3 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Flip lint to fail-CI for description length and generic description prefix.
- Flip lint to fail-CI for missing `## Example` or `## Examples`.
- Flip lint to fail-CI for archaeology markers.
- Flip lint to fail-CI for frontmatter validity.
- Keep future body-length lint out of this slice; P3.9 flips body-length after `legacy/` is deleted.

## Legacy Scope Rule

The current catalog under `backend/app/skills/legacy/` intentionally has warning debt from P3.2:

```text
missing_frontmatter_field
missing_example
description_length
archaeology_marker
```

P3.4-P3.8 are the planned migrations that rewrite those files into the new schema, and P3.9 deletes `legacy/`. Therefore P3.3 must fail CI for migrated skill files outside `legacy/`, while still reporting legacy problems as warnings. This makes every future migrated skill enforceable without making this PR rewrite the full catalog out of order.

## File Structure

- Modify `backend/app/services/deep_agent/skill_lint.py`
  - Add severity classification with `warning` and `error`.
  - Add `SkillLintMode = Literal["warn", "ci"]`.
  - Add `CI_ERROR_CODES` for the Phase 3.3 hard-fail warnings.
  - Add `lint_skill_tree(..., mode="warn")` and `lint_skill_file(..., mode="warn", root=None)`.
  - Add `format_lint_errors(...)` and `assert_no_skill_lint_errors(...)` for pytest/CI use.
  - Keep the default mode as `warn` so P3.2 callers remain backward-compatible.
- Modify `tests/test_skill_lint.py`
  - Keep current warn-only tests passing without requiring call-site changes.
- Create `tests/test_skill_lint_ci.py`
  - Proves CI mode hard-fails non-legacy skill warnings.
  - Proves legacy skill warnings remain warnings.
  - Proves the real current catalog has no CI-blocking errors under the P3.3 scope rule.

## Task 1: Add Failing CI-Mode Tests

**Files:**
- Create: `tests/test_skill_lint_ci.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_skill_lint_ci.py` with this content:

```python
"""CI-mode lint tests for Phase 3 skill catalog enforcement."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deep_agent.skill_lint import (
    assert_no_skill_lint_errors,
    format_lint_errors,
    lint_skill_tree,
)
from app.services.deep_agent.skills_paths import SKILLS_ROOT


def _write_skill(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bad_skill(name: str) -> str:
    return f"""---
name: {name}
description: Documentation for a skill with a description that is intentionally longer than two hundred characters so the CI mode must classify it as a hard error for migrated skills instead of leaving it as a soft warning.
workflow_type: unknown
allowed_envelopes:
  - invalid_env
---

Body mentions commit abc1234 and omits the required example section.
"""


def test_ci_mode_marks_non_legacy_lint_as_errors(tmp_path: Path) -> None:
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
    assert {warning.severity for warning in legacy} == {"warning"}


def test_warn_mode_keeps_non_legacy_lint_as_warnings(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "workflows" / "risk" / "bad-risk" / "SKILL.md",
        _bad_skill("bad-risk"),
    )

    warnings = lint_skill_tree(tmp_path)

    assert warnings
    assert {warning.severity for warning in warnings} == {"warning"}


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
    assert "old-risk" not in message


def test_current_catalog_has_no_ci_blocking_skill_lint_errors() -> None:
    warnings = assert_no_skill_lint_errors(SKILLS_ROOT)

    assert warnings
    assert all(warning.severity == "warning" for warning in warnings)
    assert any("legacy" in warning.path.relative_to(SKILLS_ROOT).parts for warning in warnings)
```

- [x] **Step 2: Run the new tests to verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint_ci.py -q
```

Expected: collection or execution fails because `assert_no_skill_lint_errors`, `format_lint_errors`, `mode`, or `severity` is not implemented yet.

## Task 2: Implement CI Enforcement Mode

**Files:**
- Modify: `backend/app/services/deep_agent/skill_lint.py`
- Test: `tests/test_skill_lint_ci.py`
- Regression: `tests/test_skill_lint.py`

- [x] **Step 1: Add CI mode types and constants**

In `backend/app/services/deep_agent/skill_lint.py`, update imports and add these constants near the existing lint constants:

```python
from typing import Any, Literal

SkillLintMode = Literal["warn", "ci"]
SkillLintSeverity = Literal["warning", "error"]

CI_ERROR_CODES = {
    "invalid_frontmatter",
    "missing_frontmatter_field",
    "invalid_workflow_type",
    "invalid_allowed_envelope",
    "description_length",
    "description_prefix",
    "missing_example",
    "archaeology_marker",
}
LEGACY_SKILL_ROOT_NAME = "legacy"
```

- [x] **Step 2: Add severity to `SkillLintWarning`**

Change the dataclass to:

```python
@dataclass(frozen=True)
class SkillLintWarning:
    path: Path
    code: str
    message: str
    detail: str = ""
    severity: SkillLintSeverity = "warning"

    def with_severity(self, severity: SkillLintSeverity) -> "SkillLintWarning":
        return SkillLintWarning(
            path=self.path,
            code=self.code,
            message=self.message,
            detail=self.detail,
            severity=severity,
        )
```

- [x] **Step 3: Thread mode through lint entrypoints**

Replace the existing `lint_skill_tree` and `lint_skill_file` signatures with:

```python
def lint_skill_tree(
    root: Path = SKILLS_ROOT,
    *,
    mode: SkillLintMode = "warn",
) -> list[SkillLintWarning]:
    root = Path(root)
    warnings: list[SkillLintWarning] = []
    for skill_path in iter_skill_files(root):
        warnings.extend(lint_skill_file(skill_path))
    return _apply_lint_mode(warnings, root=root, mode=mode)


def lint_skill_file(
    path: Path,
    *,
    mode: SkillLintMode = "warn",
    root: Path | None = None,
) -> list[SkillLintWarning]:
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
    warnings.extend(_lint_content(parsed))
    if mode == "warn":
        return warnings
    return _apply_lint_mode(warnings, root=Path(root or path.parent), mode=mode)
```

- [x] **Step 4: Add CI classification and assertion helpers**

Add these helpers below `lint_skill_file`:

```python
def assert_no_skill_lint_errors(root: Path = SKILLS_ROOT) -> list[SkillLintWarning]:
    warnings = lint_skill_tree(root, mode="ci")
    errors = [warning for warning in warnings if warning.severity == "error"]
    if errors:
        raise AssertionError(format_lint_errors(warnings))
    return warnings


def format_lint_errors(warnings: list[SkillLintWarning]) -> str:
    errors = [warning for warning in warnings if warning.severity == "error"]
    if not errors:
        return "Skill lint errors: none"

    lines = ["Skill lint errors:"]
    for warning in errors:
        detail = f" ({warning.detail})" if warning.detail else ""
        lines.append(f"- {warning.path}: {warning.code}{detail}: {warning.message}")
    return "\n".join(lines)


def _apply_lint_mode(
    warnings: list[SkillLintWarning],
    *,
    root: Path,
    mode: SkillLintMode,
) -> list[SkillLintWarning]:
    if mode == "warn":
        return warnings
    root = Path(root)
    return [
        warning.with_severity(_ci_severity(warning, root=root))
        for warning in warnings
    ]


def _ci_severity(warning: SkillLintWarning, *, root: Path) -> SkillLintSeverity:
    if warning.code not in CI_ERROR_CODES:
        return "warning"
    if _is_legacy_skill(warning.path, root=root):
        return "warning"
    return "error"


def _is_legacy_skill(path: Path, *, root: Path) -> bool:
    try:
        relative = Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        relative = Path(path)
    return bool(relative.parts) and relative.parts[0] == LEGACY_SKILL_ROOT_NAME
```

- [x] **Step 5: Run CI-mode tests to verify GREEN**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint_ci.py -q
```

Expected: all tests in `tests/test_skill_lint_ci.py` pass.

- [x] **Step 6: Run warn-only regression tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py -q
```

Expected: existing P3.2 warn-only tests pass without call-site changes.

## Task 3: Add CI Gate Test To Focused Skill Suite

**Files:**
- Modify: `tests/test_skill_lint_ci.py`
- Verify: existing skill catalog tests

- [x] **Step 1: Confirm the real gate test is present**

Ensure `tests/test_skill_lint_ci.py` includes:

```python
def test_current_catalog_has_no_ci_blocking_skill_lint_errors() -> None:
    warnings = assert_no_skill_lint_errors(SKILLS_ROOT)

    assert warnings
    assert all(warning.severity == "warning" for warning in warnings)
    assert any("legacy" in warning.path.relative_to(SKILLS_ROOT).parts for warning in warnings)
```

- [x] **Step 2: Run focused skill lint and catalog tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_skills_loader.py -q
```

Expected: all selected tests pass. This is the P3.3 merge gate for this slice.

## Task 4: Review, Hygiene, And Commit

**Files:**
- Review: `backend/app/services/deep_agent/skill_lint.py`
- Review: `tests/test_skill_lint.py`
- Review: `tests/test_skill_lint_ci.py`
- Review: `docs/superpowers/plans/2026-05-20-phase-3-skill-lint-ci.md`

- [x] **Step 1: Run plan self-review**

Check this plan against the spec slice:

```text
P3.3 hard description checks: CI_ERROR_CODES includes description_length and description_prefix.
P3.3 hard missing Example checks: CI_ERROR_CODES includes missing_example.
P3.3 hard archaeology checks: CI_ERROR_CODES includes archaeology_marker.
P3.3 hard frontmatter checks: CI_ERROR_CODES includes invalid_frontmatter, missing_frontmatter_field, invalid_workflow_type, and invalid_allowed_envelope.
P3.3 body-length deferment: no body-length code is added.
Legacy sequencing: legacy warnings stay non-blocking until P3.4-P3.9 migrate/delete them.
```

- [x] **Step 2: Run diff hygiene checks**

Run:

```bash
git diff --check
LC_ALL=C rg -n "[^[:ascii:]]" backend/app/services/deep_agent/skill_lint.py tests/test_skill_lint_ci.py docs/superpowers/plans/2026-05-20-phase-3-skill-lint-ci.md
python - <<'PY'
from pathlib import Path

patterns = [
    "TB" + "D",
    "TO" + "DO",
    "implement " + "later",
    "fill in " + "details",
    "appropriate " + "error handling",
    "add " + "validation",
    "Write tests " + "for the above",
    "Similar " + "to",
]
text = Path("docs/superpowers/plans/2026-05-20-phase-3-skill-lint-ci.md").read_text()
matches = [pattern for pattern in patterns if pattern in text]
if matches:
    raise SystemExit(f"placeholder hits: {matches}")
PY
```

Expected:

```text
git diff --check exits 0.
ASCII scan exits 1 with no matches.
Placeholder scan exits 0 with no matches.
```

- [x] **Step 3: Request code review**

Request review with this scope:

```text
Review P3.3 skill lint CI enforcement against the saved plan and Phase 3.3 of the canonical spec.
Focus on whether CI mode actually fails non-legacy quality violations, keeps legacy warn-only for sequencing, preserves P3.2 warn mode, and has enough tests.
```

Expected: no Critical or Important findings remain unpatched.

- [x] **Step 4: Add review regression test**

Add this test in `tests/test_skill_lint_ci.py`:

```python
def test_lint_skill_file_ci_mode_infers_legacy_scope(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "legacy" / "workflows" / "old-risk" / "SKILL.md",
        _bad_skill("old-risk"),
    )

    warnings = lint_skill_file(skill_path, mode="ci")

    assert warnings
    assert {warning.severity for warning in warnings} == {"warning"}
```

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint_ci.py::test_lint_skill_file_ci_mode_infers_legacy_scope -q
```

Expected before implementation patch: FAIL because direct `lint_skill_file(..., mode="ci")` marks a legacy file as `error`.

- [x] **Step 5: Patch review finding**

Add `_infer_lint_root(path: Path) -> Path` in `backend/app/services/deep_agent/skill_lint.py` and use it when `lint_skill_file(..., mode="ci")` is called without an explicit root.

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint_ci.py::test_lint_skill_file_ci_mode_infers_legacy_scope -q
```

Expected after implementation patch: PASS.

- [x] **Step 6: Verify review patch did not regress focused gate**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_skills_loader.py -q
```

Expected: all selected tests pass.

- [x] **Step 7: Stage the intended files**

Run:

```bash
git add backend/app/services/deep_agent/skill_lint.py tests/test_skill_lint_ci.py docs/superpowers/plans/2026-05-20-phase-3-skill-lint-ci.md
```

Expected staged files:

```text
M  backend/app/services/deep_agent/skill_lint.py
A  docs/superpowers/plans/2026-05-20-phase-3-skill-lint-ci.md
A  tests/test_skill_lint_ci.py
```

- [x] **Step 8: Commit the P3.3 slice**

Run:

```bash
git commit -m "test(skills): enforce phase 3 lint in ci mode"
```

Expected: commit succeeds on branch `codex/skill-lint-ci-phase3`.

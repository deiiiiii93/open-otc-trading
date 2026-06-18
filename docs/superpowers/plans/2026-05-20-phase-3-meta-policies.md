# Phase 3 Meta Policies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Phase 3 runtime policy fragments into `backend/app/skills/meta/` with schema validation and update runtime loaders to consume the new names.

**Architecture:** `meta/` becomes the source of truth for always-in-context runtime policy. Existing persona/orchestrator/async-agent prompt composition continues through `load_policy_fragments(...)`, but that loader reads schema-frontmatter meta files, strips frontmatter before prompt insertion, and no longer depends on `legacy/policy`.

**Tech Stack:** Python 3.11, `pathlib`, PyYAML, pytest, git file moves.

---

## Spec Slice

This plan implements Phase 3.4 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Migrate `policy/` prompt fragments into `meta/`.
- Adopt a meta-policy frontmatter schema.
- Keep workflow/reference migrations out of this slice; P3.5-P3.9 handle those.

## Policy File Set

The spec target tree lists 12 `meta/` files. The migration map has six direct legacy policy moves and six new runtime contract files:

```text
legacy/policy/clarification-protocol.md -> meta/clarification-policy.md
legacy/policy/cost-preview.md -> meta/cost-preview-policy.md
legacy/policy/hitl-batch-size-1.md -> meta/yolo-hitl-policy.md
legacy/policy/pickable-reply-options.md -> meta/reply-options-policy.md
legacy/policy/read-before-compute.md -> meta/read-before-compute-policy.md
legacy/policy/run-python-rfsw.md -> meta/python-analysis-policy.md

new meta/pet-page-contract.md
new meta/pet-diagnostic-contract.md
new meta/desk-workflow-contract.md
new meta/desk-async-contract.md
new meta/escalation-policy.md
new meta/page-context-contract.md
```

## Meta Frontmatter Schema

Every file under `backend/app/skills/meta/*.md` must start with:

```yaml
---
name: cost-preview-policy
description: Require preview and async recommendation before expensive actions.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
---
```

Required fields:

```text
name
description
policy_type
applies_to
```

Allowed `policy_type` values:

```text
runtime_policy
envelope_contract
escalation_policy
context_contract
```

`name` must equal the filename stem. `description` must be a non-empty string no longer than 200 characters. `applies_to` must be a non-empty list.

## File Structure

- Modify `backend/app/services/deep_agent/skills_paths.py`
  - Add `META_DIR = SKILLS_ROOT / "meta"`.
  - Point `POLICY_DIR` to `META_DIR` as a compatibility alias for existing imports.
- Modify `backend/app/services/deep_agent/skills_loader.py`
  - Parse optional YAML frontmatter.
  - Validate real meta policy files.
  - Strip frontmatter from loaded prompt fragments.
  - Export `parse_policy_fragment`, `validate_meta_policy_file`, and `validate_meta_policy_tree`.
- Modify `backend/app/services/deep_agent/personas.py`
  - Replace old fragment names with new meta names.
- Modify `backend/app/services/deep_agent/orchestrator.py`
  - Replace `pickable-reply-options` with `reply-options-policy`.
- Modify `backend/app/services/async_agents/policy.py`
  - Replace old async fragment names with new meta names.
- Modify `tests/test_skills_loader.py`
  - Update path expectations for `meta/`.
  - Verify frontmatter stripping.
- Modify `tests/test_async_agents_unit.py`
  - Update direct cost-preview policy file reads to `cost-preview-policy.md`.
- Create `tests/test_meta_policies.py`
  - Assert the 12-file meta set.
  - Assert real meta frontmatter schema.
  - Assert legacy policy paths are gone.
- Move six legacy policy files into `backend/app/skills/meta/`.
- Create six new contract files in `backend/app/skills/meta/`.
- Remove `backend/app/skills/meta/.gitkeep`.
- Remove the root compatibility symlink `backend/app/skills/policy`.

## Task 1: Add Failing Meta Policy Tests

**Files:**
- Create: `tests/test_meta_policies.py`
- Modify: `tests/test_skills_loader.py`
- Modify: `tests/test_async_agents_unit.py`

- [x] **Step 1: Create meta policy schema tests**

Create `tests/test_meta_policies.py` with this content:

```python
"""Phase 3.4 meta-policy migration tests."""
from __future__ import annotations

import os
from pathlib import Path

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
```

- [x] **Step 2: Replace loader tests with meta-aware expectations**

Update `tests/test_skills_loader.py` to this content:

```python
"""Unit tests for skills_loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.deep_agent.skills_loader import (
    META_POLICY_REQUIRED_FIELDS,
    POLICY_DIR,
    compose_persona_prompt,
    load_policy_fragments,
    parse_policy_fragment,
    validate_meta_policy_file,
)


def _meta_fragment(name: str, body: str = "## Alpha\nbody A") -> str:
    return f"""---
name: {name}
description: Test policy fragment for loader behavior.
policy_type: runtime_policy
applies_to:
  - trader
---

{body}
"""


def test_policy_dir_resolves_to_meta_inside_package() -> None:
    assert POLICY_DIR.is_dir(), f"{POLICY_DIR} must exist"
    assert POLICY_DIR.name == "meta"
    assert POLICY_DIR.parent.name == "skills"


def test_parse_policy_fragment_splits_frontmatter_and_body(tmp_path: Path) -> None:
    path = tmp_path / "alpha.md"
    path.write_text(_meta_fragment("alpha"), encoding="utf-8")

    fragment = parse_policy_fragment(path)

    assert fragment.frontmatter["name"] == "alpha"
    assert fragment.body == "## Alpha\nbody A"


def test_validate_meta_policy_file_enforces_required_fields(tmp_path: Path) -> None:
    path = tmp_path / "alpha.md"
    path.write_text("---\nname: alpha\n---\n\n## Alpha\nbody A\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        validate_meta_policy_file(path)

    message = str(exc.value)
    for field in META_POLICY_REQUIRED_FIELDS - {"name"}:
        assert field in message


def test_load_policy_fragments_single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(_meta_fragment("alpha"), encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha"])
    assert result == "## Alpha\nbody A"


def test_load_policy_fragments_concatenates_with_blank_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(_meta_fragment("alpha"), encoding="utf-8")
    (policy_dir / "beta.md").write_text(
        _meta_fragment("beta", body="## Beta\nbody B"),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha", "beta"])
    assert result == "## Alpha\nbody A\n\n## Beta\nbody B"


def test_load_policy_fragments_strips_trailing_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(
        _meta_fragment("alpha", body="## Alpha\nbody A\n\n\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = load_policy_fragments(["alpha"])
    assert result == "## Alpha\nbody A"


def test_load_policy_fragments_missing_fragment_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    with pytest.raises(FileNotFoundError) as exc:
        load_policy_fragments(["does-not-exist"])
    assert "does-not-exist.md" in str(exc.value)


def test_compose_persona_prompt_identity_plus_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_dir = tmp_path / "meta"
    policy_dir.mkdir()
    (policy_dir / "alpha.md").write_text(_meta_fragment("alpha"), encoding="utf-8")
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", policy_dir)

    result = compose_persona_prompt(
        identity_prompt="You are foo.\n\n## Tools\n- bar\n",
        policy_fragment_names=["alpha"],
    )
    assert result == "You are foo.\n\n## Tools\n- bar\n\n## Alpha\nbody A"


def test_compose_persona_prompt_empty_fragment_list_returns_identity_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.deep_agent.skills_loader.POLICY_DIR", tmp_path / "meta")
    result = compose_persona_prompt(
        identity_prompt="You are foo.\n",
        policy_fragment_names=[],
    )
    assert result == "You are foo."
```

- [x] **Step 3: Update direct async-agent policy file tests**

In `tests/test_async_agents_unit.py`, replace both direct policy path reads:

```python
from app.services.deep_agent.skills_paths import POLICY_DIR

text = (POLICY_DIR / "cost-preview-policy.md").read_text(encoding="utf-8")
```

- [x] **Step 4: Run tests to verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py::test_cost_preview_fragment_includes_async_clause tests/test_async_agents_unit.py::test_cost_preview_offers_dispatch_async_option -q
```

Expected: fails because `META_DIR`, meta parsing helpers, the 12 meta files, and the new cost-preview filename are not implemented yet.

## Task 2: Implement Meta Policy Loader Schema

**Files:**
- Modify: `backend/app/services/deep_agent/skills_paths.py`
- Modify: `backend/app/services/deep_agent/skills_loader.py`

- [x] **Step 1: Add `META_DIR` and repoint `POLICY_DIR`**

Update `backend/app/services/deep_agent/skills_paths.py` to:

```python
"""Shared skill-catalog paths for the deep-agent runtime."""
from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = APP_ROOT / "skills"
LEGACY_SKILLS_ROOT = SKILLS_ROOT / "legacy"
META_DIR = SKILLS_ROOT / "meta"
POLICY_DIR = META_DIR


__all__ = ["APP_ROOT", "SKILLS_ROOT", "LEGACY_SKILLS_ROOT", "META_DIR", "POLICY_DIR"]
```

- [x] **Step 2: Add meta policy parsing and validation**

Update `backend/app/services/deep_agent/skills_loader.py` with these additions:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

META_POLICY_REQUIRED_FIELDS = {"name", "description", "policy_type", "applies_to"}
VALID_META_POLICY_TYPES = {
    "runtime_policy",
    "envelope_contract",
    "escalation_policy",
    "context_contract",
}


@dataclass(frozen=True)
class MetaPolicyFragment:
    path: Path
    frontmatter: dict[str, Any]
    body: str
```

Add these functions below the imports:

```python
def parse_policy_fragment(path: Path) -> MetaPolicyFragment:
    text = Path(path).read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text, path=Path(path))
    return MetaPolicyFragment(
        path=Path(path),
        frontmatter=frontmatter,
        body=body.rstrip(),
    )


def validate_meta_policy_tree(root: Path = POLICY_DIR) -> list[MetaPolicyFragment]:
    return [validate_meta_policy_file(path) for path in sorted(Path(root).glob("*.md"))]


def validate_meta_policy_file(path: Path) -> MetaPolicyFragment:
    fragment = parse_policy_fragment(path)
    missing = sorted(META_POLICY_REQUIRED_FIELDS - set(fragment.frontmatter))
    errors: list[str] = []
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    name = fragment.frontmatter.get("name")
    if name != fragment.path.stem:
        errors.append(f"name must match filename stem {fragment.path.stem!r}")

    description = fragment.frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description must be a non-empty string")
    elif len(description) > 200:
        errors.append("description must be <=200 characters")

    policy_type = fragment.frontmatter.get("policy_type")
    if policy_type not in VALID_META_POLICY_TYPES:
        errors.append(f"policy_type must be one of {sorted(VALID_META_POLICY_TYPES)}")

    applies_to = fragment.frontmatter.get("applies_to")
    if not isinstance(applies_to, list) or not applies_to:
        errors.append("applies_to must be a non-empty list")
    elif not all(isinstance(item, str) and item for item in applies_to):
        errors.append("applies_to entries must be non-empty strings")

    if not fragment.body.startswith("## "):
        errors.append("body must start with a markdown h2 heading")

    if errors:
        raise ValueError(f"Invalid meta policy {fragment.path}: {'; '.join(errors)}")
    return fragment


def _split_frontmatter(text: str, *, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError(f"Meta policy missing frontmatter: {path}")
    try:
        _, rest = text.split("---\n", 1)
        raw_frontmatter, body = rest.split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"Meta policy has malformed frontmatter fences: {path}") from exc
    loaded = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Meta policy frontmatter must be a mapping: {path}")
    return loaded, body
```

Change `load_policy_fragments(...)` so it calls `validate_meta_policy_file(path).body` instead of reading the whole file:

```python
parts.append(validate_meta_policy_file(path).body)
```

- [x] **Step 3: Run loader tests to verify GREEN for schema behavior after files exist**

Defer the test command until Task 3 creates the actual meta files; `tests/test_skills_loader.py` depends on `POLICY_DIR` existing and the temp-file tests should pass once this loader code is in place.

## Task 3: Migrate And Author Meta Policy Files

**Files:**
- Move: `backend/app/skills/legacy/policy/clarification-protocol.md` to `backend/app/skills/meta/clarification-policy.md`
- Move: `backend/app/skills/legacy/policy/cost-preview.md` to `backend/app/skills/meta/cost-preview-policy.md`
- Move: `backend/app/skills/legacy/policy/hitl-batch-size-1.md` to `backend/app/skills/meta/yolo-hitl-policy.md`
- Move: `backend/app/skills/legacy/policy/pickable-reply-options.md` to `backend/app/skills/meta/reply-options-policy.md`
- Move: `backend/app/skills/legacy/policy/read-before-compute.md` to `backend/app/skills/meta/read-before-compute-policy.md`
- Move: `backend/app/skills/legacy/policy/run-python-rfsw.md` to `backend/app/skills/meta/python-analysis-policy.md`
- Create: six new contract files listed below.
- Delete: `backend/app/skills/meta/.gitkeep`
- Delete: `backend/app/skills/policy`

- [x] **Step 1: Move mapped legacy policy files**

Run:

```bash
git mv backend/app/skills/legacy/policy/clarification-protocol.md backend/app/skills/meta/clarification-policy.md
git mv backend/app/skills/legacy/policy/cost-preview.md backend/app/skills/meta/cost-preview-policy.md
git mv backend/app/skills/legacy/policy/hitl-batch-size-1.md backend/app/skills/meta/yolo-hitl-policy.md
git mv backend/app/skills/legacy/policy/pickable-reply-options.md backend/app/skills/meta/reply-options-policy.md
git mv backend/app/skills/legacy/policy/read-before-compute.md backend/app/skills/meta/read-before-compute-policy.md
git mv backend/app/skills/legacy/policy/run-python-rfsw.md backend/app/skills/meta/python-analysis-policy.md
git rm backend/app/skills/meta/.gitkeep
git rm backend/app/skills/policy
```

Expected: moved files are staged as renames/deletes.

- [x] **Step 2: Add frontmatter to migrated files**

Prepend frontmatter to each migrated file:

```yaml
---
name: clarification-policy
description: Ask defaulted clarification before ambiguous state-touching actions.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - high_board
---
```

```yaml
---
name: cost-preview-policy
description: Require previews before expensive actions and recommend async dispatch for long runs.
policy_type: runtime_policy
applies_to:
  - orchestrator
  - trader
  - risk_manager
  - high_board
  - async_agent
---
```

```yaml
---
name: yolo-hitl-policy
description: Limit persisted action proposals to one HITL-gated tool per assistant turn.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - high_board
  - async_agent
---
```

```yaml
---
name: reply-options-policy
description: Surface structured pickable reply options when asking users to choose.
policy_type: runtime_policy
applies_to:
  - orchestrator
  - trader
  - risk_manager
  - high_board
---
```

```yaml
---
name: read-before-compute-policy
description: Read stored quantitative results before proposing compute actions.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - async_agent
---
```

```yaml
---
name: python-analysis-policy
description: Use run_python for bounded analytics that reduce large result sets.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - async_agent
---
```

- [x] **Step 3: Create `pet-page-contract.md`**

Create `backend/app/skills/meta/pet-page-contract.md`:

```markdown
---
name: pet-page-contract
description: Define the page-scoped Pet envelope for direct answers and page-native actions.
policy_type: envelope_contract
applies_to:
  - pet_page
---

## Pet page contract

Use this envelope for short, page-local assistance. Prefer loaded page context
and page actions over broad discovery.

Rules:
- Answer from complete `loaded_context` when it covers the question.
- Use declared `actions[]` for page-native actions.
- Do not perform cross-page analysis in this envelope.
- Escalate when required context is missing, the user asks for deeper
  diagnosis, or a denied tool group requires a wider envelope.
```

- [x] **Step 4: Create `pet-diagnostic-contract.md`**

Create `backend/app/skills/meta/pet-diagnostic-contract.md`:

```markdown
---
name: pet-diagnostic-contract
description: Define the diagnostic Pet envelope for deeper read-only explanation.
policy_type: envelope_contract
applies_to:
  - pet_diagnostic
---

## Pet diagnostic contract

Use this envelope after a page answer needs deeper read-only investigation.

Rules:
- Keep the explanation tied to the active page and user question.
- Read domain data when page context is insufficient.
- Do not perform domain writes.
- Escalate to desk workflow when the user requests write actions,
  cross-page orchestration, or workflow ownership.
```

- [x] **Step 5: Create `desk-workflow-contract.md`**

Create `backend/app/skills/meta/desk-workflow-contract.md`:

```markdown
---
name: desk-workflow-contract
description: Define the Desk workflow envelope for owned multi-step business work.
policy_type: envelope_contract
applies_to:
  - desk_workflow
---

## Desk workflow contract

Use this envelope for heavy workflow, cross-page reasoning, and explicit
business actions.

Rules:
- Own the workflow until the requested desk task reaches a clear stop point.
- Use domain reads before proposing writes.
- Respect cost-preview and HITL policy for persisted actions.
- Escalate to desk async when the work is long-running or should continue
  outside the active chat turn.
```

- [x] **Step 6: Create `desk-async-contract.md`**

Create `backend/app/skills/meta/desk-async-contract.md`:

```markdown
---
name: desk-async-contract
description: Define the async Desk envelope for long-running delegated work.
policy_type: envelope_contract
applies_to:
  - desk_async
---

## Desk async contract

Use this envelope when a workflow should run as a background analyst task.

Rules:
- Include the work description, source ids, and expected deliverable.
- Persist or report material outputs when the task completes.
- Put cost estimates into HITL action descriptions when no user is present.
- Keep scratch artifacts under the task-specific async workspace.
```

- [x] **Step 7: Create `escalation-policy.md`**

Create `backend/app/skills/meta/escalation-policy.md`:

```markdown
---
name: escalation-policy
description: Define when the shared runtime widens envelopes during a turn.
policy_type: escalation_policy
applies_to:
  - orchestrator
  - pet_page
  - pet_diagnostic
  - desk_workflow
---

## Escalation policy

The runtime may widen the envelope once per turn when the current envelope
blocks a required capability.

Rules:
- Missing required context or diagnostic follow-up may widen Pet page to
  Pet diagnostic.
- Write action requests and cross-page dependencies may widen Pet envelopes
  to Desk workflow.
- Long-running work may widen Pet or Desk workflow to Desk async.
- A second denial after widening must surface as a structured refusal or
  error instead of recursively widening again.
```

- [x] **Step 8: Create `page-context-contract.md`**

Create `backend/app/skills/meta/page-context-contract.md`:

```markdown
---
name: page-context-contract
description: Define the page context fields the runtime may rely on for Pet answers.
policy_type: context_contract
applies_to:
  - pet_page
  - pet_diagnostic
  - desk_workflow
---

## Page context contract

Page context is the first source of truth for page-local questions and actions.

Rules:
- Treat `loaded_context.completeness == "complete"` as usable for direct page
  facts.
- Treat paginated or partial context as a hint that further reads may be
  needed.
- Use `query_ref` or page ids when the active view cannot include all rows.
- Use `actions[]` only for actions declared by the page.
```

## Task 4: Update Runtime Fragment Names

**Files:**
- Modify: `backend/app/services/deep_agent/personas.py`
- Modify: `backend/app/services/deep_agent/orchestrator.py`
- Modify: `backend/app/services/async_agents/policy.py`

- [x] **Step 1: Update persona policy fragment names**

In `backend/app/services/deep_agent/personas.py`, replace the allowlists with:

```python
_TRADER_POLICY = (
    "read-before-compute-policy",
    "cost-preview-policy",
    "reply-options-policy",
    "yolo-hitl-policy",
    "clarification-policy",
    "python-analysis-policy",
)
_RISK_POLICY = _TRADER_POLICY
_BOARD_POLICY = (
    "cost-preview-policy",
    "reply-options-policy",
    "yolo-hitl-policy",
    "clarification-policy",
)
```

- [x] **Step 2: Update orchestrator reply-options fragment**

In `backend/app/services/deep_agent/orchestrator.py`, replace:

```python
pickable_options = load_policy_fragments(("pickable-reply-options",))
```

with:

```python
pickable_options = load_policy_fragments(("reply-options-policy",))
```

- [x] **Step 3: Update async policy fragment names**

In `backend/app/services/async_agents/policy.py`, replace the tuple with:

```python
ASYNC_POLICY_FRAGMENTS: tuple[str, ...] = (
    "read-before-compute-policy",
    "cost-preview-policy",
    "yolo-hitl-policy",
    "python-analysis-policy",
)
```

## Task 5: Verification And Review

**Files:**
- Verify all files from Tasks 1-4.

- [x] **Step 1: Run focused tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py::test_cost_preview_fragment_includes_async_clause tests/test_async_agents_unit.py::test_cost_preview_offers_dispatch_async_option tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

Expected: all selected tests pass.

- [x] **Step 2: Run hygiene checks**

Run:

```bash
git diff --check
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
text = Path("docs/superpowers/plans/2026-05-20-phase-3-meta-policies.md").read_text()
matches = [pattern for pattern in patterns if pattern in text]
if matches:
    raise SystemExit(f"placeholder hits: {matches}")
PY
```

Expected: both commands exit 0.

- [x] **Step 3: Request code review**

Review scope:

```text
Review P3.4 meta policy migration against the saved plan and canonical Phase 3.4 spec.
Focus on runtime prompt compatibility, schema correctness, file migration completeness, and whether legacy policy paths are safely removed.
```

Expected: no Critical or Important findings remain unpatched.

- [x] **Step 4: Patch review finding**

Update `backend/app/skills/README.md` so it no longer documents root-level
`policy` as a compatibility link and explicitly identifies `meta/` as the
runtime policy source of truth.

- [x] **Step 5: Stage intended files**

Run:

```bash
git add backend/app/services/deep_agent/skills_paths.py backend/app/services/deep_agent/skills_loader.py backend/app/services/deep_agent/personas.py backend/app/services/deep_agent/orchestrator.py backend/app/services/async_agents/policy.py backend/app/skills/meta backend/app/skills/legacy/policy backend/app/skills/policy tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py docs/superpowers/plans/2026-05-20-phase-3-meta-policies.md
```

Expected: only P3.4 files are staged.

- [x] **Step 6: Commit P3.4**

Run:

```bash
git commit -m "refactor(skills): migrate runtime policies to meta"
```

Expected: commit succeeds on branch `codex/skill-meta-phase3`.

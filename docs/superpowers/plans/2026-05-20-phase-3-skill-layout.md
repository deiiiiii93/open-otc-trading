# Phase 3 Skill Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the live deep-agent skill catalog into the Phase 3 `app/skills` structure while preserving legacy `/skills/...` runtime paths.

**Architecture:** The current v2 catalog remains loadable through compatibility paths while its physical files move to `backend/app/skills/legacy`. Runtime code reads a shared `SKILLS_ROOT`, so orchestrator, async-agent, tests, and future migration tools agree on one catalog root. The new `workflows/`, `meta/`, and `references/` directories are created empty as the target layout for later migration tasks.

**Tech Stack:** Python 3.11, pytest, deepagents `FilesystemBackend`, LangGraph/deep-agent runtime, repo-local `.venv`.

---

## File Structure

- Create `backend/app/services/deep_agent/skills_paths.py`: central path constants for `APP_ROOT`, `SKILLS_ROOT`, `LEGACY_SKILLS_ROOT`, and `POLICY_DIR`.
- Create `backend/app/skills/README.md`: explains Phase 3 skill root, legacy holding area, target directories, and compatibility links.
- Create `backend/app/skills/workflows/.gitkeep`: keeps the target workflow directory in git.
- Create `backend/app/skills/meta/.gitkeep`: keeps the target runtime-policy directory in git.
- Create `backend/app/skills/references/.gitkeep`: keeps the target long-form-reference directory in git.
- Move `backend/app/services/deep_agent/skills/**` to `backend/app/skills/legacy/**`: physical legacy holding area.
- Create compatibility links at `backend/app/skills/{domains,procedures,products,routing,policy}` pointing into `legacy/`.
- Modify `backend/app/services/deep_agent/orchestrator.py`: use `SKILLS_ROOT` for the skills filesystem mount.
- Modify `backend/app/services/async_agents/agent.py`: use `SKILLS_ROOT` for the async-agent skills filesystem mount.
- Modify `backend/app/services/deep_agent/skills_loader.py`: use shared `POLICY_DIR`.
- Modify skill catalog tests to resolve the root through `skills_paths.py`.
- Create `tests/test_skills_phase3_layout.py`: locks the new root and compatibility contract.

---

### Task 1: Lock Phase 3 Layout With A Failing Test

**Files:**
- Create: `tests/test_skills_phase3_layout.py`

- [ ] **Step 1: Write the failing layout test**

```python
"""Phase 3 skill-catalog layout tests.

Phase 3 moves the live catalog out of ``services/deep_agent/skills`` and into
``app/skills``. The old v2 paths must keep resolving while the legacy catalog is
being rewritten into workflow-first skills.
"""
from __future__ import annotations

from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SKILLS_ROOT = _REPO_ROOT / "backend" / "app" / "skills"


def _names(skills) -> set[str]:
    return {s["name"] for s in skills}


def test_phase3_skills_root_has_legacy_holding_area() -> None:
    assert (_APP_SKILLS_ROOT / "legacy").is_dir()
    assert (_APP_SKILLS_ROOT / "workflows").is_dir()
    assert (_APP_SKILLS_ROOT / "meta").is_dir()
    assert (_APP_SKILLS_ROOT / "references").is_dir()
    assert (_APP_SKILLS_ROOT / "legacy" / "procedures").is_dir()
    assert (_APP_SKILLS_ROOT / "legacy" / "domains").is_dir()


def test_legacy_skill_sources_still_resolve_from_new_root() -> None:
    backend = FilesystemBackend(root_dir=str(_APP_SKILLS_ROOT), virtual_mode=True)

    trader = _list_skills(backend, "/procedures/trader/")
    position = _list_skills(backend, "/domains/position/")
    routing = _list_skills(backend, "/routing/")

    assert "snowball-position-diagnostics" in _names(trader)
    assert _names(position) == {"position-snapshot", "position-input-enumerate"}
    assert "pricing-and-risk-compound" in _names(routing)


def test_runtime_skills_root_points_at_app_skills() -> None:
    from app.services.deep_agent.skills_paths import SKILLS_ROOT

    assert SKILLS_ROOT == _APP_SKILLS_ROOT
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
/Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skills_phase3_layout.py -q
```

Expected: `FAIL` because `backend/app/skills/legacy` and `app.services.deep_agent.skills_paths` do not exist yet.

---

### Task 2: Move The Existing Catalog Into The Phase 3 Root

**Files:**
- Move: `backend/app/services/deep_agent/skills/**` to `backend/app/skills/legacy/**`
- Create: `backend/app/skills/README.md`
- Create: `backend/app/skills/workflows/.gitkeep`
- Create: `backend/app/skills/meta/.gitkeep`
- Create: `backend/app/skills/references/.gitkeep`
- Create links: `backend/app/skills/domains`, `backend/app/skills/procedures`, `backend/app/skills/products`, `backend/app/skills/routing`, `backend/app/skills/policy`

- [ ] **Step 1: Create target directories and move the legacy tree**

Run:

```bash
mkdir -p backend/app/skills/legacy backend/app/skills/workflows backend/app/skills/meta backend/app/skills/references
git mv backend/app/services/deep_agent/skills/* backend/app/skills/legacy/
rmdir backend/app/services/deep_agent/skills
```

Expected: `git status --short` shows renames from `backend/app/services/deep_agent/skills/...` to `backend/app/skills/legacy/...`.

- [ ] **Step 2: Add compatibility links**

Run:

```bash
ln -s legacy/domains backend/app/skills/domains
ln -s legacy/procedures backend/app/skills/procedures
ln -s legacy/products backend/app/skills/products
ln -s legacy/routing backend/app/skills/routing
ln -s legacy/policy backend/app/skills/policy
```

Expected: `ls -l backend/app/skills/{domains,procedures,products,routing,policy}` shows symlinks into `legacy/`.

- [ ] **Step 3: Add root documentation and placeholders**

Create `backend/app/skills/README.md`:

```markdown
# Agent Skills

Phase 3 moves the skill catalog to this shared `app/skills` root.

- `legacy/` holds the existing v2 catalog while it is rewritten.
- `workflows/`, `meta/`, and `references/` are the workflow-first target layout.
- Root-level `domains`, `procedures`, `products`, `routing`, and `policy` entries
  are compatibility links so existing `/skills/...` paths keep working during
  migration.
```

Create empty placeholder files:

```bash
touch backend/app/skills/workflows/.gitkeep
touch backend/app/skills/meta/.gitkeep
touch backend/app/skills/references/.gitkeep
```

- [ ] **Step 4: Run the layout test again**

Run:

```bash
/Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skills_phase3_layout.py -q
```

Expected: the first two tests pass, and the third still fails until `skills_paths.py` is added.

---

### Task 3: Centralize Runtime Skill Paths

**Files:**
- Create: `backend/app/services/deep_agent/skills_paths.py`
- Modify: `backend/app/services/deep_agent/orchestrator.py`
- Modify: `backend/app/services/async_agents/agent.py`
- Modify: `backend/app/services/deep_agent/skills_loader.py`

- [ ] **Step 1: Create the shared path helper**

Create `backend/app/services/deep_agent/skills_paths.py`:

```python
"""Shared skill-catalog paths for the deep-agent runtime."""
from __future__ import annotations

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = APP_ROOT / "skills"
LEGACY_SKILLS_ROOT = SKILLS_ROOT / "legacy"
POLICY_DIR = LEGACY_SKILLS_ROOT / "policy"


__all__ = ["APP_ROOT", "SKILLS_ROOT", "LEGACY_SKILLS_ROOT", "POLICY_DIR"]
```

- [ ] **Step 2: Point the orchestrator at the shared root**

In `backend/app/services/deep_agent/orchestrator.py`, add:

```python
from .skills_paths import SKILLS_ROOT
```

Then change:

```python
_SKILLS_FS_ROOT = Path(__file__).parent / "skills"
```

to:

```python
_SKILLS_FS_ROOT = SKILLS_ROOT
```

- [ ] **Step 3: Point the async-agent builder at the shared root**

In `backend/app/services/async_agents/agent.py`, add:

```python
from ..deep_agent.skills_paths import SKILLS_ROOT
```

Then change:

```python
_SKILLS_FS_ROOT = Path(__file__).parent.parent / "deep_agent" / "skills"
```

to:

```python
_SKILLS_FS_ROOT = SKILLS_ROOT
```

- [ ] **Step 4: Point policy loading at the legacy policy directory**

In `backend/app/services/deep_agent/skills_loader.py`, replace:

```python
from pathlib import Path

POLICY_DIR = Path(__file__).parent / "skills" / "policy"
```

with:

```python
from .skills_paths import POLICY_DIR
```

- [ ] **Step 5: Run focused runtime tests**

Run:

```bash
/Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skills_phase3_layout.py tests/test_skills_loader.py -q
```

Expected: all tests pass.

---

### Task 4: Update Existing Skill Tests To Use The Shared Root

**Files:**
- Modify: `tests/test_skills_catalog.py`
- Modify: `tests/test_skills_catalog_v2.py`
- Modify: `tests/test_skills_read_smoke_v2.py`
- Modify: `tests/test_async_agents_unit.py`

- [ ] **Step 1: Update catalog tests**

In `tests/test_skills_catalog.py` and `tests/test_skills_catalog_v2.py`, import:

```python
from app.services.deep_agent.skills_paths import SKILLS_ROOT
```

Then set:

```python
_SKILLS_ROOT = SKILLS_ROOT
```

Remove the previous `Path(... / "services" / "deep_agent" / "skills")` construction.

- [ ] **Step 2: Update read-smoke tests**

In `tests/test_skills_read_smoke_v2.py`, import:

```python
from app.services.deep_agent.skills_paths import SKILLS_ROOT
```

Then set:

```python
_SKILLS_ROOT = SKILLS_ROOT
```

Keep `_REPO_ROOT` because `_ARTIFACTS_ROOT` still uses it.

- [ ] **Step 3: Update async-agent policy-fragment tests**

In `tests/test_async_agents_unit.py`, replace the direct `Path(deep_pkg.__file__).parent / "skills" / "policy"` reads with:

```python
from app.services.deep_agent.skills_paths import POLICY_DIR

text = (POLICY_DIR / "cost-preview.md").read_text(encoding="utf-8")
```

- [ ] **Step 4: Update policy-dir assertion**

In `tests/test_skills_loader.py`, change:

```python
assert POLICY_DIR.parent.name == "skills"
```

to:

```python
assert POLICY_DIR.parent.name == "legacy"
assert POLICY_DIR.parent.parent.name == "skills"
```

- [ ] **Step 5: Run skill catalog and loader tests**

Run:

```bash
/Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_skills_loader.py tests/test_async_agents_unit.py -q
```

Expected: all tests pass.

---

### Task 5: Verify Adjacent Agent Runtime Surfaces

**Files:**
- No additional file edits.

- [ ] **Step 1: Run adjacent runtime tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_agent_tools.py tests/test_agent_integration.py tests/test_async_agents_integration.py tests/test_async_agents_tools.py tests/test_chat_endpoint_envelope.py tests/test_capability_assignments.py tests/test_envelopes.py tests/test_escalation_engine.py tests/test_cost_preview.py -q
```

Expected: all tests pass. If failures mention missing `scipy`, separate those as environment-level QuantArk failures and rerun only the skill/runtime-adjacent suite above.

- [ ] **Step 2: Check formatting and accidental branch contamination**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: `git diff --check` exits 0. `git status --short --branch` shows branch `codex/skill-layout-phase3` and only Phase 3 skill-layout files, not `codex/agent-framework-upgrade` code-interpreter or run-control files.

---

### Task 6: Commit The Phase 3 Layout Slice

**Files:**
- Stage only Phase 3 skill-layout files from this plan.

- [ ] **Step 1: Stage the intended scope**

Run:

```bash
git add backend/app/skills backend/app/services/deep_agent/skills_paths.py backend/app/services/deep_agent/orchestrator.py backend/app/services/async_agents/agent.py backend/app/services/deep_agent/skills_loader.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_skills_loader.py tests/test_async_agents_unit.py docs/superpowers/plans/2026-05-20-phase-3-skill-layout.md
git status --short
```

Expected: staged changes include the moved skill files and path/test updates. No `.antigravitycli`, `.claude`, package metadata, `run_control.py`, or code-interpreter framework files are staged.

- [ ] **Step 2: Commit**

Run:

```bash
git commit -m "refactor(skills): move catalog to phase 3 layout"
```

Expected: commit succeeds on branch `codex/skill-layout-phase3`.

---

## Self-Review

Spec coverage:
- Phase 3 P3.1 `app/skills/` structure and `legacy/` holding area: covered by Tasks 1-4.
- Existing legacy catalog remains loadable during migration: covered by compatibility links and catalog/read-smoke tests.
- Workflow-first target directories exist for later migrations: covered by Task 2.
- Lint fail-CI and workflow rewrites are not implemented in this plan; they should be separate Phase 3 follow-up plans after the root relocation is stable.

Placeholder scan:
- No placeholder markers or unspecified implementation steps.
- Each code-changing step lists exact paths, concrete code snippets, and commands.

Type consistency:
- `SKILLS_ROOT`, `LEGACY_SKILLS_ROOT`, and `POLICY_DIR` are defined once in `skills_paths.py` and used consistently by runtime and tests.

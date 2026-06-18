# Phase 3 Routing Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the three legacy routing skills into router-contract tests and remove the live `/skills/routing/` skill tree.

**Architecture:** P3.8 removes orchestrator routing from the skill catalog and keeps compound routing behavior as explicit prompt contracts plus deterministic tests. Persona workflow skills remain the business execution units; compound flows are validated by router tests that assert the orchestrator prompt names the correct personas and workflow skills.

**Tech Stack:** Python 3.11, pytest, DeepAgents `FilesystemBackend`, markdown prompt files, git-tracked skill tree.

---

## Spec Slice

This plan implements P3.8 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Convert routing skills to router tests.
- Delete the `routing/` tree.
- Skip optional `workflows/playbooks/` because the existing compound flows can be represented as prompt contracts and tests without adding a new workflow tier.
- Leave the rest of `legacy/` loadable until P3.9.

## Current Routing Surface To Remove

```text
backend/app/skills/routing -> legacy/routing
backend/app/skills/legacy/routing/market-data-then-reprice/SKILL.md
backend/app/skills/legacy/routing/pricing-and-risk-compound/SKILL.md
backend/app/skills/legacy/routing/snowball-book-audit/SKILL.md
```

The orchestrator currently passes `skills=["/skills/routing/"]` to `create_deep_agent(...)` and the prompt instructs the orchestrator to read routing skills. P3.8 removes both.

## Router Contracts

The three deleted routing skills become these tested prompt contracts:

| Compound request | Delegation sequence |
| --- | --- |
| `pricing-and-risk-compound` | `trader` with `price-portfolio`, then `risk_manager` with `price-portfolio`, then `risk_manager` with `create-risk-report` |
| `snowball-book-audit` | `trader` with `snowball-pricing`, then `risk_manager` with `snowball-risk-explain` |
| `market-data-then-reprice` | `trader` with `explain-market-data-drift`, then conditionally `trader` with `price-portfolio` |

These are not executable functions yet; the orchestrator still chooses routes through prompt instructions. P3.8 makes the prompt text the contract and tests it directly.

## File Structure

- Create `tests/test_routing_contracts_phase3.py`
  - Assert the routing symlink and legacy routing tree are gone.
  - Assert `_orchestrator_prompt()` contains no `/skills/routing/` or routing-skill read-file instructions.
  - Assert the prompt contains explicit compound routing contracts for the three flows.
  - Assert `build_orchestrator(...)` calls `create_deep_agent` with `skills=[]`.
- Modify `backend/app/services/deep_agent/orchestrator.py`
  - Remove the stale routing-skill catalog comment.
  - Pass `skills=[]`.
- Modify `backend/app/services/deep_agent/prompts/orchestrator.md`
  - Replace the "Naming routing skills" section with "Compound Routing Contracts".
  - Inline the three route sequences and branch conditions.
  - Remove the v2 routing additions footnote.
- Delete `backend/app/skills/routing` symlink.
- Delete `backend/app/skills/legacy/routing/`.
- Modify tests that still expect routing skills:
  - `tests/test_skills_phase3_layout.py`
  - `tests/test_skills_catalog_v2.py`
  - `tests/test_skills_read_smoke_v2.py`
  - `tests/test_workflow_skills_phase3.py`
- Modify `backend/app/skills/README.md` so it no longer lists `routing` as a compatibility link.

## Task 1: Add Failing Router Tests

**Files:**
- Create: `tests/test_routing_contracts_phase3.py`

- [x] **Step 1: Create router-contract tests**

Create `tests/test_routing_contracts_phase3.py`:

```python
"""Phase 3.8 router-contract tests.

P3.8 deletes routing skills and keeps compound-flow behavior as explicit
orchestrator prompt contracts.
"""
from __future__ import annotations

from pathlib import Path

from app.services.deep_agent import orchestrator
from app.services.deep_agent.skills_paths import SKILLS_ROOT

REPO_ROOT = Path(__file__).resolve().parents[1]


def _raw_orchestrator_prompt() -> str:
    return (
        REPO_ROOT / "backend/app/services/deep_agent/prompts/orchestrator.md"
    ).read_text(encoding="utf-8")


def test_routing_skill_tree_deleted() -> None:
    assert not (SKILLS_ROOT / "routing").exists()
    assert not (SKILLS_ROOT / "legacy" / "routing").exists()


def test_orchestrator_prompt_uses_inline_compound_routing_contracts() -> None:
    prompt = _raw_orchestrator_prompt()

    assert "## Compound Routing Contracts" in prompt
    assert "/skills/routing/" not in prompt
    assert "read_file` the matching routing skill" not in prompt
    assert "pricing-and-risk-compound" not in prompt
    assert "snowball-book-audit" not in prompt
    assert "market-data-then-reprice" not in prompt

    pricing_idx = prompt.index("Compound pricing + risk health")
    assert "trader" in prompt[pricing_idx:]
    assert "price-portfolio" in prompt[pricing_idx:]
    assert "risk_manager" in prompt[pricing_idx:]
    assert "create-risk-report" in prompt[pricing_idx:]

    snowball_idx = prompt.index("Snowball book audit")
    assert "snowball-pricing" in prompt[snowball_idx:]
    assert "snowball-risk-explain" in prompt[snowball_idx:]

    market_idx = prompt.index("Market-data audit followed by repricing")
    assert "explain-market-data-drift" in prompt[market_idx:]
    assert "price-portfolio" in prompt[market_idx:]


def test_build_orchestrator_no_longer_loads_routing_skill_source(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)

        class Graph:
            name = kwargs["name"]

        return Graph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    orchestrator.build_orchestrator(
        model=object(),
        tools=[],
        checkpointer=object(),
        interrupt_on={},
    )

    assert captured["skills"] == []
```

- [x] **Step 2: Run router tests and verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_routing_contracts_phase3.py -q
```

Expected: FAIL because the routing symlink/tree still exists, prompt still references routing skills, and the orchestrator still passes `skills=["/skills/routing/"]`.

## Task 2: Remove Routing Skill Runtime Surface

**Files:**
- Modify: `backend/app/services/deep_agent/orchestrator.py`
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Delete: `backend/app/skills/routing`
- Delete: `backend/app/skills/legacy/routing/market-data-then-reprice/SKILL.md`
- Delete: `backend/app/skills/legacy/routing/pricing-and-risk-compound/SKILL.md`
- Delete: `backend/app/skills/legacy/routing/snowball-book-audit/SKILL.md`
- Modify: `backend/app/skills/README.md`

- [x] **Step 1: Stop loading routing skills**

In `backend/app/services/deep_agent/orchestrator.py`, replace:

```python
        # v2: orchestrator has its own routing-skill catalog. Source is empty
        # until Task 18 authors the three routing skills (pricing-and-risk-compound,
        # snowball-book-audit, market-data-then-reprice).
        skills=["/skills/routing/"],
```

with:

```python
        # P3.8: compound routing is covered by explicit prompt contracts and
        # router tests, not by a runtime routing skill catalog.
        skills=[],
```

- [x] **Step 2: Inline compound routing contracts in prompt**

In `backend/app/services/deep_agent/prompts/orchestrator.md`, replace the entire section from `## Naming routing skills` through the paragraph before `## Cost-preview rule` with:

```markdown
## Compound Routing Contracts

Compound requests are prompt-level contracts, not routing skills. Do not call
`read_file` for routing instructions. Apply clarification and cost-preview
rules yourself, then issue the `task(...)` calls in the order below.

| Request shape                                          | Persona sequence       | Workflow sequence                                         |
|--------------------------------------------------------|------------------------|-----------------------------------------------------------|
| Compound pricing + risk health on one portfolio        | trader → risk_manager → risk_manager | price-portfolio → price-portfolio → create-risk-report |
| Snowball book audit (pricing + risk on same portfolio) | trader → risk_manager  | snowball-pricing → snowball-risk-explain                 |
| Market-data audit followed by repricing (trader only)  | trader → trader        | explain-market-data-drift → price-portfolio if drift found |

### Compound Pricing + Risk

1. Delegate to `trader` with `price-portfolio` for pricing health.
2. Delegate to `risk_manager` with `price-portfolio` for risk-input currency.
3. Delegate to `risk_manager` with `create-risk-report` only when the user asked for a report or governance artifact.
4. Synthesize trader and risk_manager findings with explicit attribution.

### Snowball Book Audit

1. Delegate to `trader` with `snowball-pricing` for pricing health, KO/KI distance, autocall proximity, and stale-input checks.
2. Delegate to `risk_manager` with `snowball-risk-explain`, passing the trader's KI/KO flags and pricing age.
3. Synthesize positions flagged by either or both lenses.

### Market Data Then Reprice

1. Delegate to `trader` with `explain-market-data-drift`.
2. If no drift is found, report that no repricing is needed and stop.
3. If drift requires imported position market inputs, surface the import need and stop.
4. If drift can be handled by repricing, delegate to `trader` with `price-portfolio`.
5. Synthesize data-audit and repricing outcomes.
```

Delete the final v2 routing footnote near the bottom of the file.

- [x] **Step 3: Delete the routing tree**

Run:

```bash
git rm backend/app/skills/routing
git rm -r backend/app/skills/legacy/routing
```

Expected: the symlink and the three routing `SKILL.md` files are staged for deletion.

- [x] **Step 4: Update skills README**

In `backend/app/skills/README.md`, change:

```markdown
- Root-level `domains`, `procedures`, `products`, and `routing` entries are
  compatibility links so existing `/skills/...` paths keep working during
  migration.
```

to:

```markdown
- Root-level `domains`, `procedures`, and `products` entries are compatibility
  links so existing `/skills/...` paths keep working during migration.
- P3.8 removed the routing compatibility link; compound routes are covered by
  router-contract tests and orchestrator prompt instructions.
```

## Task 3: Update Routing-Specific Tests

**Files:**
- Modify: `tests/test_skills_phase3_layout.py`
- Modify: `tests/test_skills_catalog_v2.py`
- Modify: `tests/test_skills_read_smoke_v2.py`
- Modify: `tests/test_workflow_skills_phase3.py`

- [x] **Step 1: Update layout test**

In `tests/test_skills_phase3_layout.py`, remove the routing `_list_skills(...)` call and assert:

```python
assert not (_APP_SKILLS_ROOT / "routing").exists()
assert not (_APP_SKILLS_ROOT / "legacy" / "routing").exists()
```

Keep the legacy procedure and domain assertions.

- [x] **Step 2: Update catalog-v2 routing tests**

In `tests/test_skills_catalog_v2.py`:

- Replace `test_routing_source_has_3_skills(...)` with `test_routing_source_deleted_in_phase3(...)` asserting `/routing/` returns an empty set and both routing paths do not exist.
- Replace `test_orchestrator_total_catalog_size(...)` with `test_orchestrator_has_no_routing_skill_catalog(...)` asserting `_persona_catalog(skills_backend, ["/routing/"]) == set()`.
- Update top docstring to say router contracts live in `tests/test_routing_contracts_phase3.py`.

- [x] **Step 3: Update read-smoke test**

In `tests/test_skills_read_smoke_v2.py`:

- Remove the bullet saying read_file works on routing skills.
- Replace `test_read_routing_skill(...)` with:

```python
def test_routing_skill_tree_removed(skills_backend: FilesystemBackend):
    text = _read(skills_backend, "/workflows/pricing/price-portfolio/SKILL.md")
    assert "name: price-portfolio" in text
    assert "price_positions" in text
```

- [x] **Step 4: Update P3.6/P3.7 prompt test**

In `tests/test_workflow_skills_phase3.py`, remove the three `SKILLS_ROOT / "legacy/routing/.../SKILL.md"` entries from `prompt_files`. Keep assertions against orchestrator/persona prompts and `legacy/products/snowball-cn/SKILL.md`.

- [x] **Step 5: Run focused routing tests and verify GREEN**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_routing_contracts_phase3.py tests/test_skills_phase3_layout.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_workflow_skills_phase3.py -q
```

Expected: PASS.

## Task 4: Verify Phase 3 Compatibility

**Files:**
- No new files unless verification reveals a defect.

- [x] **Step 1: Run broad Phase 3 suite**

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_routing_contracts_phase3.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

Expected: PASS.

- [x] **Step 2: Review against plan**

Run:

```bash
git diff --check
rg -n "skills=\\[\"/skills/routing/\"\\]|/skills/routing/|legacy/routing|pricing-and-risk-compound|snowball-book-audit|market-data-then-reprice|Naming routing skills|v2 routing additions" backend/app/services/deep_agent backend/app/skills tests docs/superpowers/plans/2026-05-20-phase-3-routing-tests.md
```

Expected:
- `git diff --check` prints nothing and exits 0.
- Search results are limited to the P3.8 plan, test names/assertions about absence, or harmless archaeology-marker fixture tests.

- [x] **Step 3: Final no-cache verification**

```bash
PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest -p no:cacheprovider tests/test_routing_contracts_phase3.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

Expected: PASS.

- [x] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-05-20-phase-3-routing-tests.md
git add backend/app/services/deep_agent/orchestrator.py backend/app/services/deep_agent/prompts/orchestrator.md backend/app/skills/README.md
git add tests/test_routing_contracts_phase3.py tests/test_skills_phase3_layout.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_workflow_skills_phase3.py
git add -u backend/app/skills/routing backend/app/skills/legacy/routing
git commit -m "refactor(skills): replace routing skills with router tests"
```

Expected: commit succeeds on `codex/routing-tests-phase3`.

## Self-Review

- Spec coverage: This plan implements P3.8 by removing routing skills, encoding all three compound route contracts in tests, and not starting P3.9 legacy deletion.
- Placeholder scan: No `TBD`, `TODO`, undefined future helper, or missing test command remains.
- Type consistency: The workflow names match P3.7 names already present in persona catalogs: `price-portfolio`, `create-risk-report`, `snowball-pricing`, `snowball-risk-explain`, and `explain-market-data-drift`.

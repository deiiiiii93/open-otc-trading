# Phase 3 Delete Legacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish P3.9 by deleting the Phase 3 `legacy/` skill subtree, removing compatibility aliases, and making body-length lint a CI-blocking rule.

**Architecture:** The live catalog is now `backend/app/skills/workflows/`, with durable non-executable references under `references/` and runtime policy fragments under `meta/`. P3.9 removes all compatibility sources (`legacy/`, `domains`, `procedures`, `products`) and updates runtime prompt/tests to prove only workflow-first paths remain. Skill lint stays centralized in `backend/app/services/deep_agent/skill_lint.py` and gains deterministic body-token counting so CI can fail on bodies over 500 tokens.

**Tech Stack:** Python 3.11, pytest, deepagents `FilesystemBackend`, tiktoken `cl100k_base`, markdown/YAML frontmatter.

---

## File Structure

- Modify: `backend/app/services/deep_agent/skill_lint.py`
  - Add `BODY_MAX_TOKENS = 500`, `count_body_tokens(...)`, and `body_length` warnings.
  - Include `body_length` in `CI_ERROR_CODES`.
  - Remove the `legacy/` CI severity exemption.
- Modify: `backend/app/services/deep_agent/skills_paths.py`
  - Remove `LEGACY_SKILLS_ROOT` from runtime exports.
- Modify: `backend/app/services/deep_agent/reference_docs.py`
  - Stop requiring or validating `source_legacy_skill`.
- Modify: `backend/app/services/async_agents/agent.py`
  - Load async-agent skills from `/skills/workflows/` instead of legacy domains/procedures/products.
- Modify: `backend/app/services/async_agents/prompts/async_agent.md`
  - Describe workflow skills and reference docs using the current P3 catalog paths.
- Modify: `backend/app/skills/README.md`
  - Replace the transitional `legacy/` note with the final P3.9 catalog layout.
- Modify: `backend/app/skills/references/*/*.md`
  - Remove `source_legacy_skill` frontmatter.
- Delete: `backend/app/skills/legacy/`
- Delete: `backend/app/skills/domains`
- Delete: `backend/app/skills/procedures`
- Delete: `backend/app/skills/products`
- Create: `tests/test_skill_rewrite_regression.py`
  - Freeze eight prompt/workflow regression contracts after the rewrite.
- Modify: `tests/test_skill_lint.py`
  - Rename warn-only assumptions and add body-length warning coverage.
- Modify: `tests/test_skill_lint_ci.py`
  - Expect body length to be an error in CI and expect the live catalog to have no warnings/errors.
- Modify: `tests/test_skills_phase3_layout.py`
  - Assert `legacy/` and compatibility aliases are absent.
- Modify: `tests/test_skills_catalog.py`
  - Convert v1 legacy source assertions to workflow-first runtime catalog assertions.
- Modify: `tests/test_skills_catalog_v2.py`
  - Remove legacy domain/procedure/product expectations; assert old sources are empty and workflow catalogs are complete.
- Modify: `tests/test_skills_read_smoke_v2.py`
  - Read workflow skills and reference docs instead of legacy domain/product files.
- Modify: `tests/test_workflow_skills_phase3.py`
  - Remove `legacy/products/snowball-cn/SKILL.md` from prompt scans and flip fallback assertions to absence.
- Modify: `tests/test_remaining_workflow_skills_phase3.py`
  - Remove remaining legacy fallback assertions and ensure old sources are absent.
- Modify: `tests/test_reference_docs.py`
  - Validate reference docs without legacy-source metadata and assert no `source_legacy_skill` remains.
- Modify: `tests/test_async_agents_unit.py`
  - Add/adjust async-agent source checks for workflow-only skills.

## Task 1: Add P3.9 Regression and Lint Tests

**Files:**
- Create: `tests/test_skill_rewrite_regression.py`
- Modify: `tests/test_skill_lint.py`
- Modify: `tests/test_skill_lint_ci.py`
- Modify: `tests/test_skills_phase3_layout.py`
- Modify: `tests/test_reference_docs.py`
- Modify: `tests/test_async_agents_unit.py`

- [x] **Step 1: Write failing tests for final catalog shape**

Add assertions that:

```python
assert not (SKILLS_ROOT / "legacy").exists()
assert not (SKILLS_ROOT / "domains").exists()
assert not (SKILLS_ROOT / "procedures").exists()
assert not (SKILLS_ROOT / "products").exists()
```

Also assert `_list_skills(backend, "/domains/position/")`, `_list_skills(backend, "/procedures/trader/")`, and `_list_skills(backend, "/products/")` return `[]`.

- [x] **Step 2: Write failing tests for body-length lint**

Add a valid temporary skill whose body is over 500 tokens:

```python
body = "\n".join(f"- repeated policy detail {i}" for i in range(260))
warnings = lint_skill_file(skill_path, mode="ci", root=tmp_path)
assert any(
    warning.code == "body_length" and warning.severity == "error"
    for warning in warnings
)
```

Add a live-catalog assertion:

```python
warnings = assert_no_skill_lint_errors(SKILLS_ROOT)
assert warnings == []
```

- [x] **Step 3: Write the frozen 8-prompt regression fixture test**

Create `tests/test_skill_rewrite_regression.py` with eight fixtures covering:

1. Positions page count from complete page context.
2. Position diagnostic follow-up for position `21`.
3. Risk page rerun via `run_risk`.
4. Try-Solve Snowball row with `000852.SH`, `KO 103%`, `KI 75%`.
5. Portfolio pricing for portfolio `6`.
6. Market-data drift then reprice for `000905.SH`.
7. Snowball book audit using Snowball pricing and risk explain workflows.
8. RFQ intake, quote, and submit-for-approval flow.

Each fixture asserts:

```python
assert AgentService.__new__(AgentService)._resolve_envelope(input_envelope, page_context) is expected_envelope
assert expected workflow skills are visible through the production `_build_backend()`
assert expected tool names appear in the workflow bodies in the locked order
assert frozen key facts such as "21", "64", "000852.SH", "KO 103%", "KI 75%", "portfolio 6" are present in the fixture prompt/context contract
```

- [x] **Step 4: Run tests to verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_rewrite_regression.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_reference_docs.py tests/test_async_agents_unit.py -q
```

Expected: FAIL because `legacy/` and aliases still exist, `body_length` is not implemented, reference docs still require `source_legacy_skill`, and async-agent sources still point at legacy compatibility paths.

## Task 2: Implement Final Skill Lint Enforcement

**Files:**
- Modify: `backend/app/services/deep_agent/skill_lint.py`

- [x] **Step 1: Add deterministic body-token counting**

Implement:

```python
BODY_MAX_TOKENS = 500

@lru_cache(maxsize=1)
def _token_encoder():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")

def count_body_tokens(body: str) -> int:
    return len(_token_encoder().encode(body))
```

- [x] **Step 2: Add `body_length` warnings**

In `_lint_content(parsed)`, after missing-example detection:

```python
body_tokens = count_body_tokens(parsed.body)
if body_tokens > BODY_MAX_TOKENS:
    warnings.append(
        SkillLintWarning(
            path=parsed.path,
            code="body_length",
            message="Skill body exceeds 500 tokens.",
            detail=str(body_tokens),
        )
    )
```

- [x] **Step 3: Make `body_length` CI-blocking and remove legacy exemption**

Add `"body_length"` to `CI_ERROR_CODES`. Change `_ci_severity(...)` so every `CI_ERROR_CODES` warning is an `"error"` and all other warnings remain `"warning"`.

- [x] **Step 4: Run lint tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_lint.py tests/test_skill_lint_ci.py -q
```

Expected: lint tests pass after live catalog cleanup is complete; before cleanup they may still fail on legacy files, which is acceptable until Task 3 lands.

## Task 3: Remove Legacy Skill Catalog and Runtime References

**Files:**
- Delete: `backend/app/skills/legacy/`
- Delete: `backend/app/skills/domains`
- Delete: `backend/app/skills/procedures`
- Delete: `backend/app/skills/products`
- Modify: `backend/app/services/deep_agent/skills_paths.py`
- Modify: `backend/app/services/deep_agent/reference_docs.py`
- Modify: `backend/app/services/async_agents/agent.py`
- Modify: `backend/app/services/async_agents/prompts/async_agent.md`
- Modify: `backend/app/skills/README.md`
- Modify: `backend/app/skills/references/*/*.md`

- [x] **Step 1: Delete compatibility catalog files**

Run `rm -rf backend/app/skills/legacy` and remove the three compatibility symlinks:

```bash
rm -rf backend/app/skills/legacy
rm -f backend/app/skills/domains backend/app/skills/procedures backend/app/skills/products
```

- [x] **Step 2: Remove runtime legacy path exports**

Delete `LEGACY_SKILLS_ROOT = SKILLS_ROOT / "legacy"` and remove it from `__all__`.

- [x] **Step 3: Finalize reference-doc schema**

Remove `source_legacy_skill` from `REFERENCE_DOC_REQUIRED_FIELDS` and delete the source-path validation block. Keep name, description, `reference_type`, and markdown body validation.

- [x] **Step 4: Remove reference-doc legacy metadata**

In every file under `backend/app/skills/references/`, delete the `source_legacy_skill: ...` frontmatter line.

- [x] **Step 5: Update async-agent skill sources**

Set:

```python
skills=["/skills/workflows/"]
```

Update `async_agent.md` so it says:

```markdown
Your skills catalog covers `/skills/workflows/`.
When the brief names a workflow skill by slug, read the matching
`/skills/workflows/<domain>/<skill>/SKILL.md` file before invoking tools.
For durable product, pricing, market-data, portfolio, or RFQ reference content,
read `/skills/references/.../*.md`.
```

- [x] **Step 6: Update catalog README**

Document only:

```markdown
- `workflows/` executable workflow skills
- `meta/` runtime policy fragments
- `references/` durable reference documents
```

State that P3.9 removed `legacy/`, `domains`, `procedures`, and `products`.

## Task 4: Update Catalog Tests to Workflow-First Runtime

**Files:**
- Modify: `tests/test_skills_catalog.py`
- Modify: `tests/test_skills_catalog_v2.py`
- Modify: `tests/test_skills_read_smoke_v2.py`
- Modify: `tests/test_workflow_skills_phase3.py`
- Modify: `tests/test_remaining_workflow_skills_phase3.py`
- Modify: `tests/test_reference_docs.py`
- Modify: `tests/test_async_agents_unit.py`

- [x] **Step 1: Replace legacy catalog assertions with workflow assertions**

Use `_list_skills(..., "/workflows/<domain>/")` and persona `skills` lists as the source of truth. Old paths must now return `[]`.

- [x] **Step 2: Update production composite backend tests**

Assert:

```python
_list_skills(backend, "/skills/workflows/pricing/")
_list_skills(backend, "/skills/workflows/risk/")
_list_skills(backend, "/skills/workflows/snowballs/")
```

contain the expected workflow names.

- [x] **Step 3: Update read smoke tests**

Read:

```python
"/workflows/portfolios/portfolio-membership/SKILL.md"
"/workflows/positions/position-snapshot/SKILL.md"
"/workflows/pricing/price-portfolio/SKILL.md"
"/references/products/snowball-cn.md"
```

- [x] **Step 4: Update prompt routing scans**

Remove `SKILLS_ROOT / "legacy/products/snowball-cn/SKILL.md"` from prompt scan lists. Add `REFERENCES_DIR / "products/snowball-cn.md"` where the product reference content still matters.

- [x] **Step 5: Run focused P3.9 suite**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_skill_rewrite_regression.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_async_agents_unit.py -q
```

Expected: PASS.

## Task 5: Final Verification and Commit

**Files:**
- All files touched above.

- [x] **Step 1: Search for stale legacy skill references**

Run:

```bash
rg -n 'backend/app/skills/legacy|/skills/(domains|procedures|products)|/domains/|/procedures/|/products/|source_legacy_skill|LEGACY_SKILLS_ROOT|legacy/.*SKILL.md' backend/app/skills backend/app/services/deep_agent backend/app/services/async_agents tests docs/superpowers/plans/2026-05-20-phase-3-delete-legacy.md
```

Expected: only P3.9 plan text, unrelated non-skill domain-service "legacy" comments, or explicit tests asserting old sources are absent.

- [x] **Step 2: Run `git diff --check`**

Expected: no output.

- [x] **Step 3: Run broad Phase 3 suite without pytest cache**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest -p no:cacheprovider tests/test_skill_rewrite_regression.py tests/test_routing_contracts_phase3.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

Expected: PASS.

- [x] **Step 4: Commit**

Run:

```bash
git add backend/app/services/deep_agent/skill_lint.py backend/app/services/deep_agent/skills_paths.py backend/app/services/deep_agent/reference_docs.py backend/app/services/async_agents/agent.py backend/app/services/async_agents/prompts/async_agent.md backend/app/skills/README.md backend/app/skills/references tests docs/superpowers/plans/2026-05-20-phase-3-delete-legacy.md
git add -u backend/app/skills tests
git commit -m "refactor(skills): delete legacy phase 3 catalog"
```

Expected: one P3.9 commit on `codex/delete-legacy-phase3`.

## Self-Review

- Spec coverage: P3.9 deletion, body-length CI, catalog test updates, and the new 8-prompt regression gate are covered by Tasks 1-5.
- Placeholder scan: no `TBD`, `TODO`, or "implement later" instructions remain.
- Type consistency: `body_length`, `BODY_MAX_TOKENS`, `count_body_tokens`, and `source_legacy_skill` names are used consistently.

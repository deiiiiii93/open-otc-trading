# Phase 3 Workflow Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the first Phase 3 workflow skills for risk, positions, and try-solve into `backend/app/skills/workflows/` and wire personas to those migrated workflow sources.

**Architecture:** This slice creates strict-frontmatter workflow `SKILL.md` files for the P3.6 target domains while preserving legacy source directories as loadable fallbacks. Persona source lists use migrated workflow directories for risk, positions, and try-solve, and avoid migrated legacy procedure folders so runtime catalogs do not shadow the new workflow names.

**Tech Stack:** Python 3.11, pytest, DeepAgents `FilesystemBackend`, SkillsMiddleware `_list_skills`, markdown `SKILL.md` files.

---

## Spec Slice

This plan implements P3.6 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Migrate `workflows/risk/`, `workflows/positions/`, and `workflows/try-solve/`.
- Cover the fixture-prompt domains first: risk, positions, try-solve page actions, and the first Snowball/position diagnostic workflow.
- Keep `legacy/` loadable until P3.9.

## Target Workflow File Set

```text
backend/app/skills/workflows/risk/run-risk/SKILL.md
backend/app/skills/workflows/risk/read-risk-result/SKILL.md
backend/app/skills/workflows/risk/create-risk-report/SKILL.md
backend/app/skills/workflows/positions/position-snapshot/SKILL.md
backend/app/skills/workflows/positions/position-inputs/SKILL.md
backend/app/skills/workflows/positions/position-diagnosis/SKILL.md
backend/app/skills/workflows/try-solve/solve-imported-row/SKILL.md
backend/app/skills/workflows/try-solve/create-request-queue-item/SKILL.md
```

## Workflow Frontmatter Rules

Every file above must be a real workflow skill and pass CI-mode lint:

```yaml
---
name: run-risk
description: Propose and queue a persisted risk run when stored portfolio risk is stale or missing.
domain: risk
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - pricing_parameter_profile_id
write_actions: true
confirmation_required: true
success_criteria:
  - risk run task is queued with portfolio id and status
---
```

The body must include `## Example`, must avoid implementation-history archaeology, and should keep long domain definitions in `references/`.

## File Structure

- Modify `backend/app/services/deep_agent/skills_paths.py`
  - Add `WORKFLOWS_DIR = SKILLS_ROOT / "workflows"`.
  - Export `WORKFLOWS_DIR`.
- Modify `backend/app/services/deep_agent/personas.py`
  - Trader uses `/skills/workflows/positions/` and `/skills/workflows/try-solve/`.
  - Risk manager uses `/skills/workflows/positions/` and `/skills/workflows/risk/`.
  - Do not include migrated legacy procedure directories in trader/risk runtime source lists.
- Modify runtime routing text after review
  - Update orchestrator/persona prompt examples from migrated procedure names to workflow or recipe names.
  - Update live routing skills so they delegate to `position-diagnosis`, `pricing-run-propose`, `market-data-drift`, and `create-risk-report`.
  - Update Try Solve page-action metadata so endpoints are current and local-only actions are not labeled as nonexistent backend routes.
- Create `tests/test_workflow_skills_phase3.py`
  - Assert the exact P3.6 workflow file set.
  - Assert migrated workflow skills are CI-lint clean.
  - Assert workflow directories are readable through `FilesystemBackend`.
  - Assert persona source lists include the migrated workflow directories and stop using migrated legacy domain paths.
  - Assert runtime persona catalogs and prompt routing do not expose migrated legacy procedure names.
  - Assert Try Solve workflow bodies use the page-action contract instead of nonexistent agent tool names.
  - Assert legacy source directories remain readable.
- Create the eight target workflow `SKILL.md` files.
- Remove `backend/app/skills/workflows/.gitkeep` after real workflow files exist.

## Task 1: Add Failing Workflow Tests

**Files:**
- Create: `tests/test_workflow_skills_phase3.py`

- [x] **Step 1: Create the failing test file**

Create `tests/test_workflow_skills_phase3.py` with this content:

```python
"""Phase 3.6 workflow skill migration tests."""
from __future__ import annotations

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.personas import risk_spec, trader_spec
from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT, WORKFLOWS_DIR


EXPECTED_WORKFLOW_FILES = {
    "risk/run-risk/SKILL.md",
    "risk/read-risk-result/SKILL.md",
    "risk/create-risk-report/SKILL.md",
    "positions/position-snapshot/SKILL.md",
    "positions/position-inputs/SKILL.md",
    "positions/position-diagnosis/SKILL.md",
    "try-solve/solve-imported-row/SKILL.md",
    "try-solve/create-request-queue-item/SKILL.md",
}


def _names(skills) -> set[str]:
    return {skill["name"] for skill in skills}


def _source_list(spec: dict) -> list[str]:
    return list(spec["skills"])


def test_phase3_workflow_file_set_matches_p3_6_target() -> None:
    actual = {
        path.relative_to(WORKFLOWS_DIR).as_posix()
        for path in WORKFLOWS_DIR.rglob("SKILL.md")
    }

    assert actual == EXPECTED_WORKFLOW_FILES


def test_phase3_workflow_skills_are_ci_lint_clean() -> None:
    for relative in EXPECTED_WORKFLOW_FILES:
        path = WORKFLOWS_DIR / relative
        warnings = lint_skill_file(path, mode="ci", root=SKILLS_ROOT)
        assert [warning for warning in warnings if warning.severity == "error"] == []
        parsed = parse_skill_file(path)
        assert parsed.frontmatter["name"] == path.parent.name
        assert parsed.frontmatter["domain"] in {"risk", "positions", "try-solve"}
        assert parsed.frontmatter["success_criteria"]
        assert "## Example" in parsed.body


def test_workflow_sources_are_readable_via_skills_backend() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _names(_list_skills(backend, "/workflows/risk/")) == {
        "run-risk",
        "read-risk-result",
        "create-risk-report",
    }
    assert _names(_list_skills(backend, "/workflows/positions/")) == {
        "position-snapshot",
        "position-inputs",
        "position-diagnosis",
    }
    assert _names(_list_skills(backend, "/workflows/try-solve/")) == {
        "solve-imported-row",
        "create-request-queue-item",
    }


def test_persona_sources_use_migrated_workflow_directories() -> None:
    trader_sources = _source_list(trader_spec(object(), []))
    risk_sources = _source_list(risk_spec(object(), []))

    assert "/skills/workflows/positions/" in trader_sources
    assert "/skills/workflows/try-solve/" in trader_sources
    assert "/skills/domains/position/" not in trader_sources

    assert "/skills/workflows/positions/" in risk_sources
    assert "/skills/workflows/risk/" in risk_sources
    assert "/skills/domains/position/" not in risk_sources
    assert "/skills/domains/risk/" not in risk_sources


def test_legacy_sources_remain_readable_for_unmigrated_fallbacks() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _names(_list_skills(backend, "/domains/position/")) == {
        "position-snapshot",
        "position-input-enumerate",
    }
    assert _names(_list_skills(backend, "/domains/risk/")) == {
        "risk-run-propose",
        "risk-snapshot-read",
    }
    assert "risk-report-workflow" in _names(
        _list_skills(backend, "/procedures/risk_manager/")
    )
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py -q
```

Expected: FAIL because `WORKFLOWS_DIR` is not exported and the target workflow files are absent.

## Task 2: Add Workflow Path Constant And Persona Sources

**Files:**
- Modify: `backend/app/services/deep_agent/skills_paths.py`
- Modify: `backend/app/services/deep_agent/personas.py`

- [x] **Step 1: Add `WORKFLOWS_DIR`**

Update `backend/app/services/deep_agent/skills_paths.py` so the constants are:

```python
APP_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = APP_ROOT / "skills"
LEGACY_SKILLS_ROOT = SKILLS_ROOT / "legacy"
META_DIR = SKILLS_ROOT / "meta"
REFERENCES_DIR = SKILLS_ROOT / "references"
WORKFLOWS_DIR = SKILLS_ROOT / "workflows"
POLICY_DIR = META_DIR


__all__ = [
    "APP_ROOT",
    "SKILLS_ROOT",
    "LEGACY_SKILLS_ROOT",
    "META_DIR",
    "REFERENCES_DIR",
    "WORKFLOWS_DIR",
    "POLICY_DIR",
]
```

- [x] **Step 2: Replace migrated persona sources**

Update `trader_spec(...)` skills in `backend/app/services/deep_agent/personas.py` to:

```python
        skills=[
            "/skills/workflows/positions/",
            "/skills/workflows/try-solve/",
            "/skills/domains/pricing/",
            "/skills/domains/market-data/",
            "/skills/domains/rfq/",
            "/skills/products/",
        ],
```

Update `risk_spec(...)` skills to:

```python
        skills=[
            "/skills/workflows/positions/",
            "/skills/workflows/risk/",
            "/skills/domains/market-data/",
            "/skills/domains/pricing/",
            "/skills/domains/reporting/",
            "/skills/products/",
        ],
```

- [x] **Step 3: Run the focused test and verify next RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py -q
```

Expected: FAIL because the target workflow files do not exist yet.

## Task 3: Add The P3.6 Workflow Skills

**Files:**
- Create: `backend/app/skills/workflows/risk/run-risk/SKILL.md`
- Create: `backend/app/skills/workflows/risk/read-risk-result/SKILL.md`
- Create: `backend/app/skills/workflows/risk/create-risk-report/SKILL.md`
- Create: `backend/app/skills/workflows/positions/position-snapshot/SKILL.md`
- Create: `backend/app/skills/workflows/positions/position-inputs/SKILL.md`
- Create: `backend/app/skills/workflows/positions/position-diagnosis/SKILL.md`
- Create: `backend/app/skills/workflows/try-solve/solve-imported-row/SKILL.md`
- Create: `backend/app/skills/workflows/try-solve/create-request-queue-item/SKILL.md`
- Delete: `backend/app/skills/workflows/.gitkeep`

- [x] **Step 1: Create `risk/run-risk/SKILL.md`**

Create `backend/app/skills/workflows/risk/run-risk/SKILL.md` with this content:

```markdown
---
name: run-risk
description: Propose and queue a persisted risk run when stored portfolio risk is stale or missing.
domain: risk
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - pricing_parameter_profile_id
  - valuation_date
write_actions: true
confirmation_required: true
success_criteria:
  - risk run task is queued with portfolio id and status
  - reply includes task id and how to monitor it
---

## When to use

- User asks to run, refresh, or recompute portfolio risk.
- Latest persisted risk run is absent, stale, or not aligned with current positions.
- A report workflow needs a fresh audited risk run before report creation.

## Required inputs

`portfolio_id` is required. Use `pricing_parameter_profile_id` when selected in context; otherwise ask once before queueing if the user did not say to run without one.

## Procedure

1. Read position count and product mix when available from page context or `get_positions`.
2. State the portfolio id, pricing profile choice, and expected queued action.
3. Call `run_risk(portfolio_id, method="summary", pricing_parameter_profile_id=<id or null>)`.
4. Return `risk_run_id`, `task_id`, `status`, and monitoring next step.

## Stop conditions

Ask for `portfolio_id` if it is missing. Escalate to `desk_async` when the portfolio is large or the user requests background execution.

## Output shape

Lead with queued or blocked. Include portfolio id, pricing profile id, task id, status, and the next read action.

## References

- `references/pricing/engines.md`

## Example

User: Run risk for portfolio 6 with profile 3.
Assistant: Queue `run_risk` for portfolio 6 with pricing profile 3, then report task id and status.
```

- [x] **Step 2: Create `risk/read-risk-result/SKILL.md`**

Create `backend/app/skills/workflows/risk/read-risk-result/SKILL.md` with this content:

```markdown
---
name: read-risk-result
description: Read the latest persisted portfolio risk result and explain freshness and totals.
domain: risk
workflow_type: read
allowed_envelopes:
  - pet_page
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - risk_run_id
write_actions: false
confirmation_required: false
success_criteria:
  - latest risk run is found or absence is stated
  - reply includes status, created time, and key totals when present
---

## When to use

- User asks what the latest risk says for a portfolio.
- A page-scoped risk question can be answered from loaded context or stored risk.
- Another workflow needs a risk freshness check before taking action.

## Required inputs

Use `portfolio_id` from page context, entity ids, or explicit user text. Prefer loaded page snapshot when it already contains the latest risk run.

## Procedure

1. If loaded context has current risk totals, answer from it.
2. Otherwise call `get_latest_risk_run(portfolio_id)`.
3. Extract run id, status, created time, and totals from `metrics`.
4. State whether no completed stored run exists.

## Stop conditions

Ask once when `portfolio_id` is missing. Escalate to `desk_workflow` only if the user asks to queue a fresh run.

## Output shape

Return a compact freshness line followed by delta, gamma, vega, theta, and contributing position count when available.

## References

- `references/pricing/engines.md`

## Example

User: What is the latest risk for this portfolio?
Assistant: Read latest risk for the selected portfolio and summarize status, timestamp, totals, and missing-run state.
```

- [x] **Step 3: Create `risk/create-risk-report/SKILL.md`**

Create `backend/app/skills/workflows/risk/create-risk-report/SKILL.md` with this content:

```markdown
---
name: create-risk-report
description: Create a persisted risk report after checking risk-run currency and selected pricing profile.
domain: risk
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - pricing_parameter_profile_id
  - risk_run_id
write_actions: true
confirmation_required: true
success_criteria:
  - report job is queued with report id, task id, and status
  - reply states whether a fresh risk run is needed first
---

## When to use

- User asks for a risk report, governance report, or portfolio risk artifact.
- Risk page action requests report creation for a selected portfolio.
- Desk workflow needs a persisted artifact rather than an inline risk summary.

## Required inputs

`portfolio_id` is required. Use the selected pricing profile when present so the report is auditable against the same assumptions as pricing and risk.

## Procedure

1. Apply `read-risk-result` to check whether stored risk exists and is current.
2. If risk is missing or stale, tell the user a fresh `run-risk` should happen first.
3. If risk is current or the user confirms proceeding, call `create_report(portfolio_id, report_type="risk", pricing_parameter_profile_id=<id or null>)`.
4. Return `report_job_id`, `task_id`, `status`, and where to monitor it.

## Stop conditions

Ask for `portfolio_id` if missing. Do not silently create a report from stale risk when the user asked for current risk.

## Output shape

State queued or blocked first, then report id, task id, status, pricing profile id, and risk-run freshness.

## References

- `references/pricing/engines.md`

## Example

User: Create a risk report for portfolio 9.
Assistant: Check latest risk, ask or proceed based on freshness, then queue `create_report` and return job details.
```

- [x] **Step 4: Create `positions/position-snapshot/SKILL.md`**

Create `backend/app/skills/workflows/positions/position-snapshot/SKILL.md` with this content:

```markdown
---
name: position-snapshot
description: Build a compact portfolio position view with latest stored valuations for downstream workflows.
domain: positions
workflow_type: read
allowed_envelopes:
  - pet_page
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - product_type
  - status
write_actions: false
confirmation_required: false
success_criteria:
  - position count and valuation coverage are reported
  - missing valuation count is explicit
---

## When to use

- A workflow needs the current positions in a portfolio.
- User asks how many positions are loaded or which positions match a filter.
- Pricing, risk, market data, or diagnostics needs a read-first portfolio view.

## Required inputs

Use `portfolio_id` from page context or user text. Optional filters are `product_type` and `status`.

## Procedure

1. Call `get_positions(portfolio_id=<id>, product_type=<optional>, status=<optional>)`.
2. Call `get_latest_position_valuations(portfolio_id=<id>, limit=500)`.
3. Join positions by `position.id == valuation.position_id`.
4. Count total positions, valued positions, missing valuations, and failed valuations.

## Stop conditions

Ask for portfolio selection when no `portfolio_id` is available. Note the 500 valuation read cap when the portfolio exceeds it.

## Output shape

Return counts first, then any filter used, valuation coverage, failed valuation ids, and whether downstream repricing is likely needed.

## References

- `references/portfolios/model.md`

## Example

User: Snapshot this portfolio.
Assistant: Read positions and latest valuations, then return counts, coverage, and missing or failed valuation rows.
```

- [x] **Step 5: Create `positions/position-inputs/SKILL.md`**

Create `backend/app/skills/workflows/positions/position-inputs/SKILL.md` with this content:

```markdown
---
name: position-inputs
description: Enumerate unique market-data dependencies required by a portfolio position snapshot.
domain: positions
workflow_type: read
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - position_snapshot
write_actions: false
confirmation_required: false
success_criteria:
  - unique underlying and input-type pairs are listed
  - top dependencies by position count are identified
---

## When to use

- User asks what market data a portfolio needs.
- Market-data fetch or drift workflow needs the full underlying and input set.
- A portfolio snapshot already exists and needs dependency compression.

## Required inputs

Start from a `position-snapshot` result. If no snapshot exists, run that workflow first.

## Procedure

1. Extract each position underlying from the snapshot.
2. For each position, collect required input types: spot, volatility, rate, dividend yield, and dividend schedule when terms require it.
3. Use `run_python` only when the position list is too large to count reliably in context.
4. Return deduped pairs and counts by pair.

## Stop conditions

Ask for `portfolio_id` when neither snapshot nor portfolio context is available.

## Output shape

Return unique pair count, top pairs by position count, and any positions whose inputs could not be inferred.

## References

- `references/market-data/conventions.md`

## Example

User: What market inputs do these positions depend on?
Assistant: Enumerate unique underlying and input-type pairs and list the highest blast-radius dependencies.
```

- [x] **Step 6: Create `positions/position-diagnosis/SKILL.md`**

Create `backend/app/skills/workflows/positions/position-diagnosis/SKILL.md` with this content:

```markdown
---
name: position-diagnosis
description: Diagnose unexpected position value, Greek, PnL, pricing, or risk contribution.
domain: positions
workflow_type: diagnostic
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - position_id
optional_context:
  - portfolio_id
  - pricing_parameter_profile_id
  - market_data_profile_id
  - risk_run_id
write_actions: false
confirmation_required: false
success_criteria:
  - observed value is identified
  - likely drivers and uncertainty are stated
---

## When to use

- User asks why a position value, Greek, PnL, price, or risk number looks wrong.
- Pet page answer needs more than loaded table facts.
- A Snowball position needs barrier, lifecycle, valuation, or risk context joined.

## Required inputs

`position_id` is required. Use `portfolio_id`, selected pricing profile, and market-data profile when available.

## Procedure

1. Read the position through `get_positions` using portfolio and product filters when available.
2. Read latest valuation and latest risk context when the user asks about price, PnL, or Greeks.
3. Compare product terms, lifecycle flags, stored valuation status, and risk metrics.
4. State likely drivers, missing inputs, and whether a specialized Snowball workflow should continue later.

## Stop conditions

Ask for `position_id` when missing. Escalate to `desk_workflow` for cross-portfolio reads or to `desk_async` for large book-wide diagnosis.

## Output shape

Lead with verdict, then observed value, compared inputs, likely drivers, missing evidence, and recommended next action.

## References

- `references/products/snowball-cn.md`
- `references/pricing/engines.md`

## Example

User: Why does position 42 have such high gamma?
Assistant: Read the position, valuation, and risk context, then explain product drivers and uncertainty without mutating state.
```

- [x] **Step 7: Create `try-solve/solve-imported-row/SKILL.md`**

Create `backend/app/skills/workflows/try-solve/solve-imported-row/SKILL.md` with this content:

```markdown
---
name: solve-imported-row
description: Solve the selected Try Solve row when product terms and market inputs are ready.
domain: try-solve
workflow_type: action
allowed_envelopes:
  - pet_page
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - row_id
optional_context:
  - product_key
  - quote_field_key
write_actions: false
confirmation_required: false
success_criteria:
  - selected row is solved or blocking diagnostics are returned
  - reply includes solved field, solved value, residual, and status when present
---

## When to use

- User asks to solve the selected Try Solve row.
- Page context exposes `solve_imported_row` for the selected row.
- Row status is `solver_ready` or diagnostics show what prevents solving.

## Required inputs

Use `row_id` from Try Solve page context. Do not search uploaded workbooks or raw tables to rediscover the active row.

## Procedure

1. Read selected row facts from page context: product key, quote field, market inputs, status, and diagnostics.
2. If required terms or market inputs are missing, ask for the missing fields.
3. If ready, return a page-action request `solve_imported_row` for `row_id`;
   it is not a backend domain tool and should not be called through the agent
   tool allowlist.
4. Report solved field, solved value, model price, residual, status, and diagnostics.

## Stop conditions

Escalate to `desk_workflow` when the user asks to solve multiple rows or change product terms before solving.

## Output shape

Return solved or blocked first, then row id, product, solved value, residual, and missing terms if blocked.

## References

- `references/pricing/engines.md`

## Example

User: Solve this imported row.
Assistant: Use selected `row_id`, check readiness, return page-action request `solve_imported_row`, and summarize the solved value or missing inputs.
```

- [x] **Step 8: Create `try-solve/create-request-queue-item/SKILL.md`**

Create `backend/app/skills/workflows/try-solve/create-request-queue-item/SKILL.md` with this content:

```markdown
---
name: create-request-queue-item
description: Create a Try Solve request queue item once enough product terms are present.
domain: try-solve
workflow_type: action
allowed_envelopes:
  - pet_page
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - row_id
optional_context:
  - product_key
  - target
  - market_inputs
write_actions: true
confirmation_required: true
success_criteria:
  - request queue item is created for the selected row
  - missing terms are clarified instead of guessed
---

## When to use

- User provides enough terms on the Try Solve page to create a queue item.
- User asks to price a structured product from the page instead of an imported workbook row.
- The selected row has captured schema but still needs term completion before solve.

## Required inputs

Use selected `row_id`, product key, current row fields, market inputs, and target from page context.

## Procedure

1. Compare row fields with the product catalog requirements already loaded on the page.
2. Ask for missing required terms, missing market inputs, or invalid target values.
3. When sufficient, return a page-action request `create_request_queue_item`
   for the selected row; it is not a backend domain tool and should not be
   called through the agent tool allowlist.
4. Return queue item id or queued status when the action response provides it.

## Stop conditions

Do not invent barriers, tenor, target value, valuation date, or market inputs. Ask concise clarification questions instead.

## Output shape

Return created or blocked first, then row id, product, missing fields or queue item status, and next action.

## References

- `references/products/snowball-cn.md`
- `references/pricing/engines.md`

## Example

User: Price a Snowball product with 000852.SH, 3Y, KO 103%, KI 75%.
Assistant: Check missing target and market fields, ask once if needed, then create the request queue item after confirmation.
```

- [x] **Step 9: Remove `.gitkeep` and run focused tests**

Run:

```bash
rm backend/app/skills/workflows/.gitkeep
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py -q
```

Expected: PASS.

## Task 4: Verify Phase 3 Compatibility

**Files:**
- No new files unless verification reveals a defect.

- [x] **Step 1: Run focused and existing Phase 3 tests**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

Expected: PASS.

- [x] **Step 2: Inspect git status**

Run:

```bash
git status --short
```

Expected changed files are limited to the P3.6 plan, workflow skill files, `skills_paths.py`, `personas.py`, workflow test file, and `.gitkeep` removal.

- [x] **Step 3: Request code review**

Use `superpowers:requesting-code-review` against the working-tree diff and ask whether the implementation matches this plan and the P3.6 spec slice.

- [x] **Step 4: Patch review findings**

For any concrete review finding, add or adjust tests first when behavior/schema changes, verify the failing test, patch implementation, and rerun the focused suite.

Review patch applied:
- Added regression tests for runtime persona catalog shadowing, stale orchestrator/routing prompt names, and Try Solve page-action contracts.
- Removed trader/risk legacy procedure source directories from runtime persona specs.
- Updated orchestrator, persona prompt examples, live routing skills, and Snowball product cross-reference to migrated workflow/recipe names.
- Updated Try Solve page action endpoint metadata and workflow bodies to distinguish page-action requests from backend domain tools.

- [x] **Step 5: Final verification**

Run the same command from Step 1 after review patches. Expected: PASS.

- [x] **Step 6: Commit**

Run:

```bash
git add docs/superpowers/plans/2026-05-20-phase-3-workflow-fixtures.md
git add backend/app/services/deep_agent/skills_paths.py backend/app/services/deep_agent/personas.py backend/app/services/deep_agent/prompts
git add backend/app/skills/workflows backend/app/skills/legacy/routing backend/app/skills/legacy/products/snowball-cn/SKILL.md
git add frontend/src/routes/TrySolve.tsx tests/test_workflow_skills_phase3.py
git add -u backend/app/skills/workflows
git commit -m "refactor(skills): migrate phase 3 workflow fixtures"
```

Expected: commit succeeds on `codex/skill-workflows-phase3`.

## Self-Review

- Spec coverage: The plan creates the eight target workflow files for P3.6 and leaves P3.7/P3.8/P3.9 out of scope.
- Placeholder scan: No `TBD`, `TODO`, or undefined future step is required to execute the plan.
- Type consistency: `WORKFLOWS_DIR`, workflow names, persona source paths, and test expectations match across tasks.

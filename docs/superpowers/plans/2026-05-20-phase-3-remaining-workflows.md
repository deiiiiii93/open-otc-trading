# Phase 3 Remaining Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the remaining Phase 3 workflow skills for pricing, market data, portfolios, RFQ, reporting, and Snowballs into `backend/app/skills/workflows/`.

**Architecture:** This slice finishes the workflow-first catalog while leaving `legacy/` loadable for P3.8/P3.9 fallback. Runtime persona source lists should use workflow directories instead of migrated legacy domain, product, and procedure directories, and live routing text should name workflow skills that actually exist in each persona catalog.

**Tech Stack:** Python 3.11, pytest, DeepAgents `FilesystemBackend`, SkillsMiddleware `_list_skills`, markdown `SKILL.md` files.

---

## Spec Slice

This plan implements P3.7 from `docs/superpowers/specs/2026-05-19-pet-agent-and-runtime-refactor-design.md`:

- Migrate remaining workflows: `pricing`, `market-data`, `portfolios`, `rfq`, `reporting`, and `snowballs`.
- Preserve `legacy/` until P3.9.
- Do not convert or delete `routing/`; P3.8 owns router tests and routing-tree deletion.

## Target Workflow File Set Added By P3.7

```text
backend/app/skills/workflows/pricing/price-product/SKILL.md
backend/app/skills/workflows/pricing/price-portfolio/SKILL.md
backend/app/skills/workflows/market-data/fetch-market-data/SKILL.md
backend/app/skills/workflows/market-data/explain-market-data-drift/SKILL.md
backend/app/skills/workflows/portfolios/portfolio-membership/SKILL.md
backend/app/skills/workflows/portfolios/portfolio-view-counting/SKILL.md
backend/app/skills/workflows/rfq/intake-request/SKILL.md
backend/app/skills/workflows/rfq/draft-rfq/SKILL.md
backend/app/skills/workflows/rfq/quote-rfq/SKILL.md
backend/app/skills/workflows/rfq/submit-for-approval/SKILL.md
backend/app/skills/workflows/reporting/create-report/SKILL.md
backend/app/skills/workflows/reporting/batch-run-reports/SKILL.md
backend/app/skills/workflows/reporting/display-report/SKILL.md
backend/app/skills/workflows/snowballs/snowball-term-interpretation/SKILL.md
backend/app/skills/workflows/snowballs/snowball-pricing/SKILL.md
backend/app/skills/workflows/snowballs/snowball-risk-explain/SKILL.md
```

## Migration Map Covered

| Legacy source | P3.7 workflow |
| --- | --- |
| `domains/pricing/price-product-adhoc` | `workflows/pricing/price-product` |
| `domains/pricing/pricing-run-propose` | `workflows/pricing/price-portfolio` |
| `procedures/*/portfolio-pricing-run` | `workflows/pricing/price-portfolio` |
| `domains/market-data/market-data-fetch` | `workflows/market-data/fetch-market-data` |
| `domains/market-data/market-data-drift` | `workflows/market-data/explain-market-data-drift` |
| `procedures/trader/market-data-profile` | `workflows/market-data/fetch-market-data` |
| `domains/portfolio/portfolio-model` | `workflows/portfolios/portfolio-membership` and `portfolio-view-counting` |
| `domains/rfq/rfq-lifecycle` | `workflows/rfq/intake-request` |
| `domains/rfq/rfq-draft` | `workflows/rfq/draft-rfq` |
| `domains/rfq/rfq-quote` | `workflows/rfq/quote-rfq` |
| `domains/rfq/rfq-submit-for-approval` | `workflows/rfq/submit-for-approval` |
| `procedures/trader/rfq-intake-and-quote` | `workflows/rfq/intake-request` and `quote-rfq` |
| `domains/reporting/report-create-propose` | `workflows/reporting/create-report` |
| `domains/reporting/report-batch-run` | `workflows/reporting/batch-run-reports` |
| `procedures/high_board/report-query-and-display` | `workflows/reporting/display-report` |
| `products/snowball-cn` | `references/products/snowball-cn.md` plus `workflows/snowballs/*` |

## File Structure

- Modify `tests/test_workflow_skills_phase3.py`
  - Rename the existing P3.6 set to `P3_6_WORKFLOW_FILES`.
  - Assert P3.6 files are a subset of all workflows, not the entire workflow tree.
  - Update prompt-routing assertions from P3.6 interim names to final P3.7 names.
- Create `tests/test_remaining_workflow_skills_phase3.py`
  - Assert the exact P3.7 file set exists.
  - Assert each new workflow skill is CI-lint clean, has matching `name`, valid `domain`, and `## Example`.
  - Assert workflow directories are readable through `FilesystemBackend`.
  - Assert persona source lists use workflow directories and no longer use migrated legacy domain/product/procedure paths.
  - Assert runtime catalogs expose new workflow names and do not expose migrated legacy names.
  - Assert legacy sources remain loadable directly until P3.9.
- Modify `backend/app/services/deep_agent/personas.py`
  - Trader uses workflow dirs for positions, try-solve, pricing, market-data, portfolios, rfq, snowballs.
  - Risk manager uses workflow dirs for positions, risk, pricing, market-data, portfolios, reporting, snowballs.
  - High board uses workflow dirs for portfolios and reporting.
- Modify prompt and live routing text
  - `backend/app/services/deep_agent/prompts/orchestrator.md`
  - `backend/app/services/deep_agent/prompts/trader.md`
  - `backend/app/services/deep_agent/prompts/risk_manager.md`
  - `backend/app/services/deep_agent/prompts/high_board.md`
  - `backend/app/skills/legacy/routing/*.md`
  - `backend/app/skills/legacy/products/snowball-cn/SKILL.md`
- Create the 16 target workflow `SKILL.md` files.

## Expected Runtime Persona Sources

Trader:

```python
skills=[
    "/skills/workflows/positions/",
    "/skills/workflows/try-solve/",
    "/skills/workflows/pricing/",
    "/skills/workflows/market-data/",
    "/skills/workflows/portfolios/",
    "/skills/workflows/rfq/",
    "/skills/workflows/snowballs/",
]
```

Risk manager:

```python
skills=[
    "/skills/workflows/positions/",
    "/skills/workflows/risk/",
    "/skills/workflows/pricing/",
    "/skills/workflows/market-data/",
    "/skills/workflows/portfolios/",
    "/skills/workflows/reporting/",
    "/skills/workflows/snowballs/",
]
```

High board:

```python
skills=[
    "/skills/workflows/portfolios/",
    "/skills/workflows/reporting/",
]
```

## Task 1: Add Failing P3.7 Tests

**Files:**
- Modify: `tests/test_workflow_skills_phase3.py`
- Create: `tests/test_remaining_workflow_skills_phase3.py`

- [x] **Step 1: Update P3.6 tests for a growing workflow tree**

Change `EXPECTED_WORKFLOW_FILES` to `P3_6_WORKFLOW_FILES` and update the file-set assertion to:

```python
def test_phase3_workflow_file_set_includes_p3_6_target() -> None:
    actual = {
        path.relative_to(WORKFLOWS_DIR).as_posix()
        for path in WORKFLOWS_DIR.rglob("SKILL.md")
    }

    assert P3_6_WORKFLOW_FILES <= actual
```

Update prompt-routing expectations so the combined live prompt/routing text contains `price-portfolio`, `explain-market-data-drift`, and `display-report`, and no longer requires `pricing-run-propose` or `market-data-drift`.

- [x] **Step 2: Create the P3.7 test file**

Create `tests/test_remaining_workflow_skills_phase3.py` with:

```python
"""Phase 3.7 remaining workflow skill migration tests."""
from __future__ import annotations

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.orchestrator import _build_backend
from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT, WORKFLOWS_DIR

P3_7_WORKFLOW_FILES = {
    "pricing/price-product/SKILL.md",
    "pricing/price-portfolio/SKILL.md",
    "market-data/fetch-market-data/SKILL.md",
    "market-data/explain-market-data-drift/SKILL.md",
    "portfolios/portfolio-membership/SKILL.md",
    "portfolios/portfolio-view-counting/SKILL.md",
    "rfq/intake-request/SKILL.md",
    "rfq/draft-rfq/SKILL.md",
    "rfq/quote-rfq/SKILL.md",
    "rfq/submit-for-approval/SKILL.md",
    "reporting/create-report/SKILL.md",
    "reporting/batch-run-reports/SKILL.md",
    "reporting/display-report/SKILL.md",
    "snowballs/snowball-term-interpretation/SKILL.md",
    "snowballs/snowball-pricing/SKILL.md",
    "snowballs/snowball-risk-explain/SKILL.md",
}

P3_7_DOMAINS = {"pricing", "market-data", "portfolios", "rfq", "reporting", "snowballs"}


def _names(skills) -> set[str]:
    return {skill["name"] for skill in skills}


def _source_list(spec: dict) -> list[str]:
    return list(spec["skills"])


def _catalog_names(backend, sources: list[str]) -> set[str]:
    names: set[str] = set()
    for source in sources:
        names.update(_names(_list_skills(backend, source)))
    return names


def test_phase3_remaining_workflow_files_exist_and_lint_clean() -> None:
    actual = {
        path.relative_to(WORKFLOWS_DIR).as_posix()
        for path in WORKFLOWS_DIR.rglob("SKILL.md")
    }

    assert P3_7_WORKFLOW_FILES <= actual
    for relative in P3_7_WORKFLOW_FILES:
        path = WORKFLOWS_DIR / relative
        warnings = lint_skill_file(path, mode="ci", root=SKILLS_ROOT)
        assert [warning for warning in warnings if warning.severity == "error"] == []
        parsed = parse_skill_file(path)
        assert parsed.frontmatter["name"] == path.parent.name
        assert parsed.frontmatter["domain"] in P3_7_DOMAINS
        assert parsed.frontmatter["success_criteria"]
        assert "## Example" in parsed.body


def test_phase3_remaining_workflow_sources_are_readable() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _names(_list_skills(backend, "/workflows/pricing/")) == {"price-product", "price-portfolio"}
    assert _names(_list_skills(backend, "/workflows/market-data/")) == {"fetch-market-data", "explain-market-data-drift"}
    assert _names(_list_skills(backend, "/workflows/portfolios/")) == {"portfolio-membership", "portfolio-view-counting"}
    assert _names(_list_skills(backend, "/workflows/rfq/")) == {"intake-request", "draft-rfq", "quote-rfq", "submit-for-approval"}
    assert _names(_list_skills(backend, "/workflows/reporting/")) == {"create-report", "batch-run-reports", "display-report"}
    assert _names(_list_skills(backend, "/workflows/snowballs/")) == {"snowball-term-interpretation", "snowball-pricing", "snowball-risk-explain"}


def test_persona_sources_use_remaining_workflow_directories() -> None:
    trader_sources = _source_list(trader_spec(object(), []))
    risk_sources = _source_list(risk_spec(object(), []))
    board_sources = _source_list(board_spec(object(), []))

    for source in [
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/rfq/",
        "/skills/workflows/snowballs/",
    ]:
        assert source in trader_sources

    for source in [
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
        "/skills/workflows/snowballs/",
    ]:
        assert source in risk_sources

    assert board_sources == ["/skills/workflows/portfolios/", "/skills/workflows/reporting/"]

    forbidden = (
        "/skills/domains/pricing/",
        "/skills/domains/market-data/",
        "/skills/domains/rfq/",
        "/skills/domains/reporting/",
        "/skills/domains/portfolio/",
        "/skills/products/",
        "/skills/procedures/high_board/",
    )
    for source in forbidden:
        assert source not in trader_sources
        assert source not in risk_sources
        assert source not in board_sources


def test_runtime_persona_catalogs_do_not_shadow_remaining_legacy_workflows() -> None:
    backend = _build_backend()
    catalogs = {
        "trader": _catalog_names(backend, _source_list(trader_spec(object(), []))),
        "risk": _catalog_names(backend, _source_list(risk_spec(object(), []))),
        "board": _catalog_names(backend, _source_list(board_spec(object(), []))),
    }

    assert {"price-product", "price-portfolio", "fetch-market-data", "explain-market-data-drift"} <= catalogs["trader"]
    assert {"portfolio-membership", "portfolio-view-counting", "draft-rfq", "quote-rfq"} <= catalogs["trader"]
    assert {"create-report", "batch-run-reports"} <= catalogs["risk"]
    assert {"portfolio-membership", "display-report"} <= catalogs["board"]
    assert {"snowball-term-interpretation", "snowball-pricing", "snowball-risk-explain"} <= catalogs["trader"]
    assert {"snowball-term-interpretation", "snowball-pricing", "snowball-risk-explain"} <= catalogs["risk"]

    forbidden = {
        "price-product-adhoc",
        "pricing-run-propose",
        "pricing-engines",
        "market-data-fetch",
        "market-data-drift",
        "market-data-conventions",
        "portfolio-model",
        "rfq-draft",
        "rfq-lifecycle",
        "rfq-quote",
        "rfq-submit-for-approval",
        "report-batch-run",
        "report-create-propose",
        "report-query-and-display",
        "snowball-cn",
    }
    for catalog in catalogs.values():
        assert forbidden.isdisjoint(catalog)


def test_legacy_sources_remain_loadable_directly_until_p3_9() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert "pricing-run-propose" in _names(_list_skills(backend, "/domains/pricing/"))
    assert "market-data-fetch" in _names(_list_skills(backend, "/domains/market-data/"))
    assert "portfolio-model" in _names(_list_skills(backend, "/domains/portfolio/"))
    assert "rfq-quote" in _names(_list_skills(backend, "/domains/rfq/"))
    assert "report-query-and-display" in _names(_list_skills(backend, "/procedures/high_board/"))
    assert "snowball-cn" in _names(_list_skills(backend, "/products/"))
```

- [x] **Step 3: Run focused tests and verify RED**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py -q
```

Expected: FAIL because the P3.7 workflow files and persona source changes do not exist yet.

## Task 2: Add Remaining Workflow Files

**Files:**
- Create the 16 files listed in "Target Workflow File Set Added By P3.7".

- [x] **Step 1: Add pricing workflows**

Create `pricing/price-product` with `price_product` ad-hoc single-product procedure, `references/pricing/engines.md`, no write action, and MC cost-preview language.

Create `pricing/price-portfolio` with `price_positions`, required `portfolio_id`, optional `position_ids` and `pricing_parameter_profile_id`, write action true, confirmation required true, and desk async escalation.

- [x] **Step 2: Add market-data workflows**

Create `market-data/fetch-market-data` with one-symbol-per-`fetch_market_snapshot` procedure, required `underlyings`, optional `start_date` and `end_date`, no write action.

Create `market-data/explain-market-data-drift` with snapshot-vs-stored-input comparison, optional `position_snapshot`, `market_snapshot`, and `threshold`, no write action, and `run_python` only for large comparisons.

- [x] **Step 3: Add portfolio workflows**

Create `portfolios/portfolio-membership` with `list_portfolios`, `get_portfolio`, and `get_positions` read-side disambiguation for Container vs View.

Create `portfolios/portfolio-view-counting` with position and product-type counting using `get_positions` filters and `total_count`.

- [x] **Step 4: Add RFQ workflows**

Create `rfq/intake-request` with `get_rfq_catalog`, lifecycle reference use, and branching to `draft-rfq` or `quote-rfq`.

Create `rfq/draft-rfq` with `draft_rfq_from_natural_language`, `validate_rfq_terms`, and `create_or_update_rfq_draft`.

Create `rfq/quote-rfq` with `solve_rfq`, `quote_rfq`, MC cost-preview, and no invented price argument.

Create `rfq/submit-for-approval` with quoted-state check and `submit_rfq_for_approval`, write action true, confirmation required true.

- [x] **Step 5: Add reporting workflows**

Create `reporting/create-report` with `create_report`, required `portfolio_id`, optional `report_type`, `title`, and `pricing_parameter_profile_id`, write action true, confirmation required true.

Create `reporting/batch-run-reports` with `run_report_batch`, no persistence, and portfolio snapshot input guidance.

Create `reporting/display-report` with `list_reports`, `get_report`, artifact path reporting, HTML read guidance, and no write actions.

- [x] **Step 6: Add Snowball workflows**

Create `snowballs/snowball-term-interpretation` with Snowball payoff invariant interpretation from `references/products/snowball-cn.md`.

Create `snowballs/snowball-pricing` with Snowball engine/input checks, `price_product`, and escalation to `price-portfolio` for persisted portfolio repricing.

Create `snowballs/snowball-risk-explain` with KI/KO proximity, gamma/KI risk interpretation, latest risk reads, and escalation to `run-risk` for stale persisted risk.

- [x] **Step 7: Run workflow tests and verify GREEN**

Run:

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py -q
```

Expected: PASS after persona updates in Task 3.

## Task 3: Wire Runtime Catalogs And Prompts

**Files:**
- Modify: `backend/app/services/deep_agent/personas.py`
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Modify: `backend/app/services/deep_agent/prompts/risk_manager.md`
- Modify: `backend/app/services/deep_agent/prompts/high_board.md`
- Modify: `backend/app/skills/legacy/routing/*.md`
- Modify: `backend/app/skills/legacy/products/snowball-cn/SKILL.md`

- [x] **Step 1: Replace persona source lists**

Apply the runtime source lists from "Expected Runtime Persona Sources".

- [x] **Step 2: Update single-persona routing table**

Use these workflow names:

| Request shape | Persona | Suggested skill |
| --- | --- | --- |
| Snowball terms or payoff interpretation | trader | `snowball-term-interpretation` |
| Snowball pricing or valuation drivers | trader | `snowball-pricing` |
| Snowball risk, hedge feasibility, gamma near KI | risk_manager | `snowball-risk-explain` |
| RFQ intake / client request capture | trader | `intake-request` |
| RFQ draft from natural language | trader | `draft-rfq` |
| RFQ solve / quote a product spec | trader | `quote-rfq` |
| Submit quoted RFQ for approval | trader | `submit-for-approval` |
| Reprice a portfolio | trader or risk_manager | `price-portfolio` |
| Audit market-data freshness/coverage | trader | `explain-market-data-drift` |
| Fetch current market data | trader | `fetch-market-data` |
| Generate a risk report end-to-end | risk_manager | `create-risk-report` |
| Review/quote from a persisted report | high_board | `display-report` |

- [x] **Step 3: Update live routing skills**

Keep the routing tree for P3.8, but replace stale delegated names:

- `pricing-and-risk-compound`: use `price-portfolio` and `create-risk-report`.
- `market-data-then-reprice`: use `explain-market-data-drift` and `price-portfolio`.
- `snowball-book-audit`: use `snowball-pricing` for trader lens and `snowball-risk-explain` for risk lens.

- [x] **Step 4: Update product cross-reference**

Change the legacy product card "See also" to reference `snowball-term-interpretation`, `snowball-pricing`, and `snowball-risk-explain`.

## Task 4: Verify Phase 3 Compatibility

**Files:**
- No new files unless verification reveals a defect.

- [x] **Step 1: Run focused tests**

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py -q
```

- [x] **Step 2: Run broad Phase 3 suite**

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

- [x] **Step 3: Review against plan**

Inspect `git diff --stat`, `git diff --check`, and search for stale migrated names in live runtime paths:

```bash
rg -n "price-product-adhoc|pricing-run-propose|market-data-fetch|market-data-drift|portfolio-model|rfq-draft|rfq-lifecycle|rfq-quote|rfq-submit-for-approval|report-batch-run|report-create-propose|report-query-and-display|snowball-cn" backend/app/services/deep_agent/prompts backend/app/skills/legacy/routing backend/app/skills/legacy/products backend/app/skills/workflows
```

Expected: only negative assertions in tests or direct legacy fallback content outside live runtime routing.

- [x] **Step 4: Final no-cache verification**

```bash
PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest -p no:cacheprovider tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py tests/test_meta_policies.py tests/test_skills_loader.py tests/test_async_agents_unit.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_skills_phase3_layout.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skills_read_smoke_v2.py -q
```

- [x] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-05-20-phase-3-remaining-workflows.md
git add tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py
git add backend/app/services/deep_agent/personas.py backend/app/services/deep_agent/prompts
git add backend/app/skills/workflows backend/app/skills/legacy/routing backend/app/skills/legacy/products/snowball-cn/SKILL.md
git commit -m "refactor(skills): migrate remaining phase 3 workflows"
```

Expected: commit succeeds on `codex/remaining-workflows-phase3`.

## Self-Review

- Spec coverage: The plan covers every non-routing P3.7 workflow directory named in the taxonomy: pricing, market-data, portfolios, rfq, reporting, and snowballs.
- Placeholder scan: No `TBD`, `TODO`, or future undefined implementation step remains.
- Type consistency: Workflow names match the spec tree, persona source paths, and test assertions.

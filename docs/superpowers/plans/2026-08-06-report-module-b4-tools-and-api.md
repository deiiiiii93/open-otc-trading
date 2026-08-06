# Report Module — Sub-project B4: Agent Tools, Skills & REST API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the report module to agents and to the frontend — six tools with the full registration checklist, the persona-domain fix that makes trader templates routable, two discoverable skills, and a REST router.

**Architecture:** Tools are thin adapters over `services/reporting`, following `tools/reporting.py`'s existing shape. The production narrator binds a model here, at the edge, not in the pipeline. The REST router mirrors the Skills router's lint-before-save UX.

**Tech Stack:** Python 3.11, FastAPI, LangChain tools, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §5.8, §5.9
**Depends on:** `2026-08-06-report-module-b3-pipeline.md` Task 3 complete and green.

## Global Constraints

- **Every tool needs the FULL registration checklist**, or it is silently dropped from every persona's toolset. `CLAUDE.md` records this twice (`assemble_breach_report`, the Run #58 routing audit): **suspect availability before capability.**
  1. `QUANT_AGENT_TOOLS` — `backend/app/tools/__init__.py`
  2. `DEEP_AGENT_TOOL_NAMES` — `backend/app/services/agents.py`
  3. For HITL tools only: all three `hitl.py` structures — `INTERRUPT_TOOL_NAMES`, `_RISK_LEVEL_BY_TOOL`, `_LABEL_BY_TOOL`
  4. For HITL tools with id-only args: a `_SUMMARY_BUILDERS` entry
  5. The exact-set pins: `tests/test_capability_assignments.py` (`len(QUANT_AGENT_TOOLS) == 115` today) and `tests/test_hitl.py`
- **Every new skill ships with a `routing:` block.** Run #58 measured 64–88% routing with one, 0–23% without.
- **`create_report` keeps its name.** It is a graded `tool_not_called` prohibition in three golden workflows. A regression test pins it.
- **Test command:** `.venv/bin/python -m pytest` from the repo root. Never pipe through `tail`.

---

### Task 1: Persona-domain fix

**Files:**
- Modify: `backend/app/services/deep_agent/persona_domains.py:13-24`
- Test: `tests/test_reporting_persona_domains.py`

**Interfaces:**
- Produces: `PERSONA_WORKFLOW_DOMAINS["trader"]` including `"reporting"`

**Why:** `spec.meta.persona` selects the narrator (B3 Task 3). A `persona: trader` template
dispatches the trader persona — which today cannot see the `reporting` domain at all, so its
skills are invisible and its report workflow is unroutable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_persona_domains.py`:

```python
from app.services.deep_agent.persona_domains import (
    PERSONA_WORKFLOW_DOMAINS,
    workflow_skill_sources,
)


def test_every_persona_that_narrates_a_template_can_see_reporting():
    """A template's persona narrates it, so each must reach the reporting domain."""
    for persona in ("trader", "risk_manager", "high_board"):
        assert "reporting" in PERSONA_WORKFLOW_DOMAINS[persona], (
            f"{persona} cannot see the reporting domain, so its report skills "
            "are unroutable"
        )


def test_trader_skill_sources_include_the_reporting_prefix():
    assert "/skills/workflows/reporting/" in workflow_skill_sources("trader")


def test_existing_trader_domains_are_preserved():
    """Adding reporting must not reorder or drop what trader already had."""
    domains = PERSONA_WORKFLOW_DOMAINS["trader"]
    for expected in ("positions", "products", "try-solve", "pricing", "hedging",
                     "market-data", "portfolios", "rfq", "snowballs",
                     "desk-workflows"):
        assert expected in domains
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_persona_domains.py -v`
Expected: FAIL — `AssertionError: trader cannot see the reporting domain`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/deep_agent/persona_domains.py`, add `"reporting"` to the trader tuple.
Tuple order controls catalog listing order in subagent prompts, so append it after
`"portfolios"` to mirror where it sits in `risk_manager`:

```python
    "trader": (
        "positions",
        "products",
        "try-solve",
        "pricing",
        "hedging",
        "market-data",
        "portfolios",
        "reporting",
        "rfq",
        "snowballs",
        "desk-workflows",
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_persona_domains.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Run the skill-lint and persona suites**

Run: `.venv/bin/python -m pytest tests/ -k "persona or skill_lint or skills" -q`
Expected: PASS. Skill lint cross-checks routing personas against domain visibility, so a
newly-visible domain can surface a previously-suppressed lint warning — fix any that appear.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/persona_domains.py \
        tests/test_reporting_persona_domains.py
git commit -m "fix(personas): give trader visibility of the reporting domain"
```

---

### Task 2: The six agent tools

**Files:**
- Create: `backend/app/tools/report_templates.py`
- Modify: `backend/app/tools/__init__.py` (register in `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/agents.py` (add to `DEEP_AGENT_TOOL_NAMES` near line 537-561)
- Modify: `backend/app/services/deep_agent/hitl.py` (three structures)
- Modify: `tests/test_capability_assignments.py` (bump the count pin)
- Test: `tests/test_reporting_tools.py`

**Interfaces:**
- Produces six tools:

| tool | group | HITL |
|---|---|---|
| `list_report_blocks()` | `DOMAIN_READ` | — |
| `list_report_templates(persona=None)` | `DOMAIN_READ` | — |
| `get_report_template(slug)` | `DOMAIN_READ` | — |
| `resolve_report_block(key, portfolio_id, compare_to=None, params=None)` | `DOMAIN_READ` | — |
| `save_report_template(slug, spec_yaml)` | `DOMAIN_WRITE` | `"write"` |
| `generate_report(template_slug, portfolio_id, compare_to=None)` | `DOMAIN_WRITE` | none |

**HITL rationale (spec §5.8), to state in the code comment:** `save_report_template` is
`"write"` — which means *interactive only*, since AUTO mode strips `"write"` from the interrupt
map. That is correct here because a template edit is reversible and audited, and past reports
are immune (they embed their own spec). `generate_report` is write-class but deliberately
**not** HITL, the same posture as `parse_trade_confirmation`, which writes only draft rows.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_tools.py`:

```python
import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers


def test_all_six_tools_are_in_the_quant_agent_tools_registry():
    from app.tools import QUANT_AGENT_TOOLS

    names = {tool.name for tool in QUANT_AGENT_TOOLS}
    assert {
        "list_report_blocks", "list_report_templates", "get_report_template",
        "resolve_report_block", "save_report_template", "generate_report",
    } <= names


def test_all_six_tools_are_allowlisted_for_the_deep_agent():
    """Registered but not allowlisted means silently dropped from every persona."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert {
        "list_report_blocks", "list_report_templates", "get_report_template",
        "resolve_report_block", "save_report_template", "generate_report",
    } <= set(DEEP_AGENT_TOOL_NAMES)


def test_save_report_template_is_hitl_gated_at_write_level():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    assert "save_report_template" in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL["save_report_template"] == "write"
    assert "save_report_template" in _LABEL_BY_TOOL


def test_generate_report_is_not_hitl_gated():
    """Write-class but not gated, like parse_trade_confirmation."""
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES

    assert "generate_report" not in INTERRUPT_TOOL_NAMES


def test_create_report_remains_registered():
    """It is a graded tool_not_called prohibition in three golden workflows.

    Deleting it would make those checks trivially always-pass, inflating scores
    and breaking comparability with 11 boards of arena history. See spec §5.7.
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES
    from app.tools import QUANT_AGENT_TOOLS

    assert "create_report" in {tool.name for tool in QUANT_AGENT_TOOLS}
    assert "create_report" in set(DEEP_AGENT_TOOL_NAMES)


def test_list_report_blocks_returns_the_catalog():
    from app.tools.report_templates import list_report_blocks_tool

    result = list_report_blocks_tool.invoke({})
    assert result["total"] == 18
    keys = {block["key"] for block in result["blocks"]}
    assert "pnl.explain" in keys
    entry = next(b for b in result["blocks"] if b["key"] == "pnl.explain")
    assert entry["shape"] == "waterfall"
    assert "compare_to_run_id" in entry["requires"]
    assert entry["description"]


def test_list_report_templates_returns_the_seeded_four():
    from app.tools.report_templates import list_report_templates_tool

    result = list_report_templates_tool.invoke({})
    slugs = {row["slug"] for row in result["templates"]}
    assert {"trader-daily", "risk-manager-daily", "high-board-daily",
            "portfolio-snapshot"} <= slugs


def test_get_report_template_returns_the_spec():
    from app.tools.report_templates import get_report_template_tool

    result = get_report_template_tool.invoke({"slug": "portfolio-snapshot"})
    assert result["slug"] == "portfolio-snapshot"
    assert "sections:" in result["spec"]
    assert result["source"] == "seed"


def test_get_report_template_raises_for_an_unknown_slug():
    from app.tools.report_templates import get_report_template_tool

    with pytest.raises(ValueError, match="nope"):
        get_report_template_tool.invoke({"slug": "nope"})


def test_save_report_template_reports_validation_errors_as_data(monkeypatch):
    """An invalid spec is a result the agent can act on, not a crash."""
    from app.tools.report_templates import save_report_template_tool

    bad = """
meta:
  slug: agent-made
  title: Agent Made
  persona: trader
sections:
  - id: s
    title: S
    blocks:
      - { key: risk.nonexistent, render: metric_row }
"""
    result = save_report_template_tool.invoke(
        {"slug": "agent-made", "spec_yaml": bad}
    )
    assert result["ok"] is False
    assert any("risk.nonexistent" in message for message in result["errors"])


def test_resolve_report_block_returns_status_and_reason():
    from app.tools.report_templates import resolve_report_block_tool

    result = resolve_report_block_tool.invoke(
        {"key": "scenario.latest_grid", "portfolio_id": 2}
    )
    assert result["status"] in ("ok", "empty", "unavailable")
    if result["status"] != "ok":
        assert result["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tools.report_templates'`

- [ ] **Step 3: Write the tools**

Create `backend/app/tools/report_templates.py`:

```python
"""@tool wrappers for the report template module.

Thin adapters over ``services/reporting``: parse args, call the service, shape
JSON. The production narrator is bound here, at the edge, rather than inside the
pipeline, so the pipeline stays testable without a model.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.reporting import blocks as _blocks  # noqa: F401 - registers producers
from app.services.reporting import templates as templates_svc
from app.services.reporting.contracts import BlockContext
from app.services.reporting.generate import TemplateNotFound, generate_report
from app.services.reporting.registry import list_blocks, resolve_block
from app.services.reporting.template_spec import TemplateSpecError


# ----- args schemas -----------------------------------------------------------


class ListTemplatesInput(BaseModel):
    persona: str | None = Field(
        default=None, description="Filter to one persona: trader, risk_manager, high_board."
    )


class GetTemplateInput(BaseModel):
    slug: str = Field(description="Template slug from list_report_templates.")


class ResolveBlockInput(BaseModel):
    key: str = Field(description="Block key from list_report_blocks.")
    portfolio_id: int = Field(description="Portfolio to resolve the block against.")
    compare_to: int | None = Field(
        default=None, description="Risk run id to compare against, for diff blocks."
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Optional block params, e.g. {'top_n': 5}."
    )


class SaveTemplateInput(BaseModel):
    slug: str = Field(description="Template slug; must match meta.slug in the spec.")
    spec_yaml: str = Field(
        description=(
            "Full template spec as YAML. Must declare meta (slug, title, persona) "
            "and sections. Every blocks[].key must exist in list_report_blocks, and "
            "each render must accept that block's shape."
        )
    )


class GenerateReportInput(BaseModel):
    template_slug: str = Field(description="Template slug to generate from.")
    portfolio_id: int = Field(description="Portfolio to report on.")
    compare_to: int | None = Field(
        default=None,
        description="Risk run id to compare against. Defaults to the prior run.",
    )


# ----- tools ------------------------------------------------------------------


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_report_blocks")
def list_report_blocks_tool() -> dict[str, Any]:
    """List every data block a report template may declare.

    Returns each block's key, title, output shape, required params, and domain.
    Use this before authoring or editing a template: a template may only name
    keys that appear here.
    """
    catalog = [spec.catalog_entry() for spec in list_blocks()]
    return {"blocks": catalog, "total": len(catalog)}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_report_templates", args_schema=ListTemplatesInput)
def list_report_templates_tool(persona: str | None = None) -> dict[str, Any]:
    """List report templates, optionally filtered to one persona."""
    rows = templates_svc.list_templates(persona=persona)
    return {
        "templates": [
            {
                "slug": row.slug, "title": row.title, "persona": row.persona,
                "description": row.description, "source": row.source,
                "version": row.version,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_report_template", args_schema=GetTemplateInput)
def get_report_template_tool(slug: str) -> dict[str, Any]:
    """Return one template's full YAML spec. Raises ValueError if not found."""
    row = templates_svc.get_template(slug=slug)
    if row is None:
        raise ValueError(f"Report template not found: slug={slug!r}")
    return {
        "slug": row.slug, "title": row.title, "persona": row.persona,
        "description": row.description, "source": row.source,
        "version": row.version, "spec": row.spec,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("resolve_report_block", args_schema=ResolveBlockInput)
def resolve_report_block_tool(
    key: str,
    portfolio_id: int,
    compare_to: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one report block to its deterministic data.

    Use this to pull additional evidence while drafting narrative. The numbers
    come from persisted risk evidence, never from the model.
    """
    result = resolve_block(
        key,
        BlockContext(
            portfolio_id=portfolio_id,
            compare_to_run_id=compare_to,
            params=params or {},
        ),
    )
    return result.model_dump()


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("save_report_template", args_schema=SaveTemplateInput)
def save_report_template_tool(slug: str, spec_yaml: str) -> dict[str, Any]:
    """Create or update a report template from a YAML spec.

    Validation failures are returned as data, not raised, so the model can fix
    the spec and retry. Nothing persists unless the spec fully validates.
    """
    try:
        row = templates_svc.save_template(slug=slug, spec_yaml=spec_yaml)
    except TemplateSpecError as exc:
        return {"ok": False, "slug": slug, "errors": list(exc.errors)}
    return {
        "ok": True, "slug": row.slug, "title": row.title,
        "persona": row.persona, "version": row.version, "errors": [],
    }


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_report", args_schema=GenerateReportInput)
def generate_report_tool(
    template_slug: str, portfolio_id: int, compare_to: int | None = None
) -> dict[str, Any]:
    """Generate a report from a template and persist it.

    Every number comes from deterministic producers; the agent contributes only
    the narrative prose for sections that declare one.
    """
    from app.services.reporting.narrator import default_narrator

    try:
        job = generate_report(
            template_slug=template_slug,
            portfolio_id=portfolio_id,
            compare_to=compare_to,
            narrate=default_narrator(),
        )
    except TemplateNotFound as exc:
        raise ValueError(str(exc)) from exc

    document = job.result_payload or {}
    flagged = sum(
        len(section.get("grounding", {}).get("flags") or [])
        for section in document.get("sections") or []
    )
    return {
        "report_id": job.id,
        "template_slug": template_slug,
        "status": job.status,
        "section_count": len(document.get("sections") or []),
        "grounding_flags": flagged,
        "message": f"Report #{job.id} generated. Open it on the Reports page.",
    }


__all__ = [
    "list_report_blocks_tool",
    "list_report_templates_tool",
    "get_report_template_tool",
    "resolve_report_block_tool",
    "save_report_template_tool",
    "generate_report_tool",
]
```

- [ ] **Step 4: Write the production narrator**

Create `backend/app/services/reporting/narrator.py`:

```python
"""Production narrator binding for report generation.

Kept out of ``generate.py`` so the pipeline stays model-free and testable. The
narrator returns PROSE ONLY — it is handed a section's resolved blocks with
their status and reason, and its entire contribution is a string.
"""
from __future__ import annotations

import json
from typing import Any

_SYSTEM = """You write one section of a desk report.

You are given resolved data blocks and an instruction. Your entire output is
prose for this section. Rules you must not break:

1. Never state a number that does not appear in the block data you were given.
2. Each block carries a status:
   - "ok"          the data is real; use it.
   - "empty"       the check RAN and found nothing. Say so affirmatively.
   - "unavailable" the check DID NOT RUN. Say exactly that. Never imply a
                   clean result from a check that did not run.
3. Follow the instruction's length. Do not add headings or restate tables.
4. Write plainly, for a professional desk reader.
"""


def default_narrator():
    """Return a narrate(persona, brief) -> str callable bound to a real model."""
    from app.services.agents import resolve_persona_model

    def narrate(persona: str, brief: dict[str, Any]) -> str:
        model = resolve_persona_model(persona)
        payload = json.dumps(brief, default=str, ensure_ascii=False)
        response = model.invoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": payload},
            ]
        )
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()

    return narrate


__all__ = ["default_narrator"]
```

Confirm the model resolver exists and adapt the import if the name differs:

```bash
grep -n "def resolve_persona_model\|def rebuild_default_model\|def default_model" backend/app/services/agents.py | head
```

If there is no `resolve_persona_model`, use the module's existing default-model accessor —
persona-specific model selection is an optimisation, not a requirement, and a single default
model narrating every persona is acceptable for the first implementation. Note whichever you
chose in the commit message.

- [ ] **Step 5: Complete the registration checklist**

**5a — `backend/app/tools/__init__.py`.** Find the `from .reporting import (` block near line 85
and add a parallel import plus six entries in `QUANT_AGENT_TOOLS`:

```python
from .report_templates import (
    generate_report_tool,
    get_report_template_tool,
    list_report_blocks_tool,
    list_report_templates_tool,
    resolve_report_block_tool,
    save_report_template_tool,
)
```

Add all six to the `QUANT_AGENT_TOOLS` list beside the existing reporting tools.

**5b — `backend/app/services/agents.py`.** In `DEEP_AGENT_TOOL_NAMES`, beside the existing
`"list_reports"` / `"get_report"` entries (around line 560), add:

```python
        "list_report_blocks",
        "list_report_templates",
        "get_report_template",
        "resolve_report_block",
        "save_report_template",
        "generate_report",
```

**5c — `backend/app/services/deep_agent/hitl.py`.** Three structures:

```python
# INTERRUPT_TOOL_NAMES — add alongside "create_report":
    "save_report_template",

# _RISK_LEVEL_BY_TOOL:
    # "write" = interactive only; AUTO mode strips it. Correct here: a template
    # edit is reversible and audited, and past reports embed their own spec, so
    # an unattended edit cannot rewrite history. Contrast the booking tools,
    # which are "irreversible" precisely because AUTO must still stop them.
    "save_report_template": "write",

# _LABEL_BY_TOOL:
    "save_report_template": "Save report template",
```

Do **not** add `generate_report`.

**5d — `tests/test_capability_assignments.py`.** The count pin at line 35 becomes
`115 + 6 = 121`:

```python
    assert len(QUANT_AGENT_TOOLS) == 121
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_tools.py -v`
Expected: PASS, 11 tests

- [ ] **Step 7: Run the registration guard suites**

Run: `.venv/bin/python -m pytest tests/test_capability_assignments.py tests/test_hitl.py -v`
Expected: PASS. `test_hitl.py` holds an exact-set assertion over the interrupt map; if it fails,
it is telling you the new tool must be declared there — add it rather than relaxing the test.

- [ ] **Step 8: Commit**

```bash
git add backend/app/tools/report_templates.py \
        backend/app/services/reporting/narrator.py \
        backend/app/tools/__init__.py \
        backend/app/services/agents.py \
        backend/app/services/deep_agent/hitl.py \
        tests/test_capability_assignments.py \
        tests/test_reporting_tools.py
git commit -m "feat(reporting): six agent tools with full registration checklist"
```

---

### Task 3: Discoverable skills

**Files:**
- Create: `backend/app/skills/workflows/reporting/generate-templated-report/SKILL.md`
- Create: `backend/app/skills/workflows/reporting/author-report-template/SKILL.md`
- Modify: the catalog exact-set test files
- Test: existing catalog tests

**Interfaces:**
- Produces: two workflow skills in the `reporting` domain, each with a `routing:` block

- [ ] **Step 1: Enumerate the exact-set tests you are about to break**

Run: `grep -rln "book-position" tests/`

`CLAUDE.md` records that adding a workflow SKILL.md broke exact-set assertions in **four**
catalog test files. Note the list before editing — you will update each one.

- [ ] **Step 2: Write the generation skill**

Create `backend/app/skills/workflows/reporting/generate-templated-report/SKILL.md`:

```markdown
---
name: generate-templated-report
description: Generate a desk report from a stored template so every number comes from deterministic producers. Use when the user asks for a daily report, a trader/risk/board report, or a report "from the template", or when a report should be persisted and viewable on the Reports page rather than written inline.
domain: reporting
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - template_slug
  - compare_to
write_actions: true
confirmation_required: false
success_criteria:
  - a template is selected or the choice is surfaced to the user
  - the report is generated and its id returned
  - any grounding flags are reported honestly
routing:
  - request: "Generate a daily desk report from a template"
    persona: risk_manager
  - request: "Generate the trader daily book review"
    persona: trader
  - request: "Generate the board one-pager"
    persona: high_board
---

## When to use

- User asks for a daily report for a trader, risk manager, or the board.
- User asks for a report that should persist and be viewable on the Reports page.
- A workflow needs a governed report artifact grounded in the latest risk run.

## Required inputs

`portfolio_id` is required. `template_slug` selects the template; when absent, call
`list_report_templates` and pick the one matching the requesting persona, or ask if
several match.

## Procedure

1. Call `list_report_templates(persona=<persona>)` when `template_slug` is not given.
2. Select the template matching the request. Ask the user if the choice is ambiguous.
3. Call `generate_report(template_slug=<slug>, portfolio_id=<id>, compare_to=<optional>)`.
4. Report the returned `report_id`, `status`, and `section_count`.
5. If `grounding_flags` is above zero, say so plainly — it means the narrative contains
   numbers not present in the section data and the report needs review.

## Stop conditions

Do not hand-write report numbers into the reply. The report's numbers come from
deterministic producers; your reply summarises what was generated, it does not restate
the report. Do not call `create_report` — it is the legacy compatibility path.

## Output shape

State generated or blocked first, then report id, template used, section count,
and any grounding flags.

## Example

User: Give me today's risk report for portfolio 2.
Assistant: List risk_manager templates, choose `risk-manager-daily`, call
`generate_report`, then return the report id and note any grounding flags.
```

- [ ] **Step 3: Write the authoring skill**

Create `backend/app/skills/workflows/reporting/author-report-template/SKILL.md`:

```markdown
---
name: author-report-template
description: Create or edit a report template from the available data blocks. Use when the user asks for a new report format, wants to change what a report shows, asks to add or remove a section, or wants a report tailored to a desk or audience.
domain: reporting
workflow_type: action
allowed_envelopes:
  - desk_workflow
required_context:
  - template_intent
optional_context:
  - slug
  - persona
  - base_template_slug
write_actions: true
confirmation_required: true
success_criteria:
  - every declared block key exists in the block catalog
  - the template saves without validation errors
  - the user is told which blocks were chosen and why
routing:
  - request: "Create or edit a report template"
    persona: risk_manager
  - request: "Change what a report shows"
    persona: trader
---

## When to use

- User asks for a new report format or a tailored report.
- User wants a section added, removed, or reordered in an existing report.

## Required inputs

A clear statement of what the report should show and for whom. When editing, the
`slug` of the template to change.

## Procedure

1. Call `list_report_blocks` FIRST. You may only declare keys that appear there —
   a template naming an unknown block is rejected at save.
2. When editing, call `get_report_template(slug)` and start from its spec.
3. Draft the YAML spec: `meta` (slug, title, persona, description) and `sections`.
   For each section give `id`, `title`, `blocks` (each `{key, render}`), and an
   optional `narrative` brief.
4. Match every `render` to the block's declared shape:
   `scalars`→`metric_row`, `scalars_with_prior`→`delta_metric_row`, `rows`→`table`,
   `series`→`bar_chart` or `line_chart`, `items`→`callout`,
   `position_greeks`→`greeks_table`, `waterfall`→`waterfall`.
5. Call `save_report_template(slug, spec_yaml)`.
6. If `ok` is false, read `errors`, fix the spec, and retry. Do not report success
   on a failed save.
7. Tell the user which blocks the template uses and why.

## Writing narrative briefs

A `narrative` is an instruction to whoever writes that section's prose, not the prose
itself. Say what to state, what to lead with, and what to do when a block is empty or
unavailable. Omit `narrative` entirely for a pure-data section — a template with no
narrative anywhere generates with no model call at all.

## Stop conditions

Never invent a block key. If the user asks for data no block provides, say which block
is missing rather than declaring a key that does not exist. Do not delete a seeded
template; create a new one instead.

## Output shape

State saved or rejected first, then the slug, persona, section count, and the block
keys used. On rejection, list the validation errors verbatim.

## Example

User: I want a board report that also shows what the agents did overnight.
Assistant: Call `list_report_blocks`, find `audit.write_actions_summary`, start from
`high-board-daily`, add the section, and save under a new slug.
```

- [ ] **Step 4: Run the catalog tests and update the exact sets**

Run: `.venv/bin/python -m pytest tests/ -k "skill" -q`
Expected: FAIL initially in each file found in Step 1 — the exact-set assertions now see two new
skill names. Add `generate-templated-report` and `author-report-template` to each expected set.

Then run: `.venv/bin/python -m pytest tests/ -k "skill" -q`
Expected: PASS

- [ ] **Step 5: Verify the skills lint clean and are routable**

Run:

```bash
.venv/bin/python -c "
from app.services.deep_agent.skills_loader import collect_routing_rows
rows = collect_routing_rows()
names = [r for r in rows if 'report' in str(r).lower()]
print(len(names)); [print(' ', n) for n in names]
"
```

Expected: both new skills appear with their routing lines. A skill absent here is invisible to
the orchestrator's Known-skills table — that is the 0–23% routing failure mode, and it means
the `routing:` block is malformed.

If `collect_routing_rows` is not importable from that module, locate it with
`grep -rn "def collect_routing_rows" backend/app/` and adjust.

- [ ] **Step 6: Commit**

```bash
git add backend/app/skills/workflows/reporting/ tests/
git commit -m "feat(reporting): generation and authoring skills with routing lines"
```

---

### Task 4: REST router

**Files:**
- Create: `backend/app/routers/reports.py`
- Modify: `backend/app/main.py` (mount the router; extend `_report_job_out` near line 510)
- Modify: `backend/app/schemas.py` (add template schemas)
- Test: `tests/test_reports_router.py`

**Interfaces:**
- Produces:
  - `GET /api/reports/templates?persona=` → `list[ReportTemplateOut]`
  - `GET /api/reports/templates/{slug}` → `ReportTemplateOut` (includes `spec`)
  - `PUT /api/reports/templates/{slug}` → `ReportTemplateOut` | 422 with `errors`
  - `DELETE /api/reports/templates/{slug}` → 204, or 409 for a seeded template
  - `POST /api/reports/templates/validate` → `{ok: bool, errors: list[str]}`
  - `GET /api/reports/blocks` → `{blocks: [...], total: int}`
  - `POST /api/reports/generate` → `ReportJobOut`
  - `ReportJobOut` gains `template_slug` and `compare_to_run_id`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reports_router.py`:

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_blocks_endpoint_returns_the_catalog(client):
    response = client.get("/api/reports/blocks")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 18
    assert any(block["key"] == "pnl.explain" for block in body["blocks"])


def test_templates_endpoint_lists_the_seeded_four(client):
    response = client.get("/api/reports/templates")
    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert {"trader-daily", "risk-manager-daily", "high-board-daily",
            "portfolio-snapshot"} <= slugs


def test_templates_endpoint_filters_by_persona(client):
    response = client.get("/api/reports/templates", params={"persona": "trader"})
    assert response.status_code == 200
    assert all(row["persona"] == "trader" for row in response.json())


def test_get_one_template_includes_its_spec(client):
    response = client.get("/api/reports/templates/portfolio-snapshot")
    assert response.status_code == 200
    assert "sections:" in response.json()["spec"]


def test_get_unknown_template_is_404(client):
    assert client.get("/api/reports/templates/nope").status_code == 404


def test_validate_accepts_a_good_spec(client):
    spec = client.get("/api/reports/templates/portfolio-snapshot").json()["spec"]
    response = client.post("/api/reports/templates/validate", json={"spec_yaml": spec})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "errors": []}


def test_validate_reports_errors_without_saving(client):
    bad = """
meta:
  slug: bad-one
  title: Bad
  persona: trader
sections:
  - id: s
    title: S
    blocks:
      - { key: risk.nope, render: metric_row }
"""
    response = client.post("/api/reports/templates/validate", json={"spec_yaml": bad})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("risk.nope" in message for message in body["errors"])
    assert client.get("/api/reports/templates/bad-one").status_code == 404


def test_put_an_invalid_template_is_422_and_persists_nothing(client):
    bad = """
meta:
  slug: bad-two
  title: Bad
  persona: trader
sections:
  - id: s
    title: S
    blocks:
      - { key: risk.totals, render: waterfall }
"""
    response = client.put("/api/reports/templates/bad-two", json={"spec_yaml": bad})
    assert response.status_code == 422
    assert any("waterfall" in message for message in response.json()["detail"]["errors"])
    assert client.get("/api/reports/templates/bad-two").status_code == 404


def test_put_a_valid_template_creates_it(client):
    good = """
meta:
  slug: api-made
  title: API Made
  persona: trader
sections:
  - id: totals
    title: Totals
    blocks:
      - { key: risk.totals, render: metric_row }
"""
    response = client.put("/api/reports/templates/api-made", json={"spec_yaml": good})
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert client.get("/api/reports/templates/api-made").status_code == 200


def test_deleting_a_seeded_template_is_409(client):
    response = client.delete("/api/reports/templates/portfolio-snapshot")
    assert response.status_code == 409


def test_generate_persists_a_report_job(client):
    response = client.post(
        "/api/reports/generate",
        json={"template_slug": "portfolio-snapshot", "portfolio_id": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["template_slug"] == "portfolio-snapshot"
    assert body["id"] > 0
    listed = client.get("/api/reports/jobs").json()
    assert any(job["id"] == body["id"] for job in listed)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reports_router.py -v`
Expected: FAIL with 404s — the router is not mounted.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas.py`, add:

```python
class ReportTemplateOut(BaseModel):
    slug: str
    title: str
    persona: str
    description: str = ""
    source: str
    version: int
    spec: str | None = None


class ReportTemplateWriteIn(BaseModel):
    spec_yaml: str


class ReportTemplateValidateOut(BaseModel):
    ok: bool
    errors: list[str]


class ReportGenerateIn(BaseModel):
    template_slug: str
    portfolio_id: int
    compare_to: int | None = None
```

And add two fields to the existing `ReportJobOut`:

```python
    template_slug: str | None = None
    compare_to_run_id: int | None = None
```

- [ ] **Step 4: Write the router**

Create `backend/app/routers/reports.py`:

```python
"""Report template and generation REST surface.

Mirrors the Skills router's lint-before-save UX: `validate` returns errors as a
200 body so the editor can show them live, while `PUT` returns 422 and persists
nothing when a spec does not validate.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ReportGenerateIn,
    ReportTemplateOut,
    ReportTemplateValidateOut,
    ReportTemplateWriteIn,
)
from app.services.reporting import blocks as _blocks  # noqa: F401 - registers producers
from app.services.reporting import templates as templates_svc
from app.services.reporting.generate import TemplateNotFound, generate_report
from app.services.reporting.registry import list_blocks
from app.services.reporting.template_spec import TemplateSpecError
from app.services.reporting.templates import TemplateProtectedError


def build_reports_router() -> APIRouter:
    router = APIRouter(prefix="/api/reports", tags=["reports"])

    def _out(row, *, include_spec: bool = False) -> ReportTemplateOut:
        return ReportTemplateOut(
            slug=row.slug, title=row.title, persona=row.persona,
            description=row.description or "", source=row.source,
            version=row.version, spec=row.spec if include_spec else None,
        )

    @router.get("/blocks")
    def list_report_blocks() -> dict:
        catalog = [spec.catalog_entry() for spec in list_blocks()]
        return {"blocks": catalog, "total": len(catalog)}

    @router.get("/templates", response_model=list[ReportTemplateOut])
    def list_templates(persona: str | None = Query(default=None)):
        return [_out(row) for row in templates_svc.list_templates(persona=persona)]

    @router.post("/templates/validate", response_model=ReportTemplateValidateOut)
    def validate_template(payload: ReportTemplateWriteIn):
        errors = templates_svc.validate_only(payload.spec_yaml)
        return ReportTemplateValidateOut(ok=not errors, errors=errors)

    @router.get("/templates/{slug}", response_model=ReportTemplateOut)
    def get_template(slug: str):
        row = templates_svc.get_template(slug=slug)
        if row is None:
            raise HTTPException(status_code=404, detail=f"template not found: {slug}")
        return _out(row, include_spec=True)

    @router.put("/templates/{slug}", response_model=ReportTemplateOut)
    def put_template(slug: str, payload: ReportTemplateWriteIn):
        try:
            row = templates_svc.save_template(slug=slug, spec_yaml=payload.spec_yaml)
        except TemplateSpecError as exc:
            raise HTTPException(
                status_code=422, detail={"errors": list(exc.errors)}
            ) from exc
        return _out(row, include_spec=True)

    @router.delete("/templates/{slug}", status_code=204)
    def delete_template(slug: str):
        try:
            deleted = templates_svc.delete_template(slug=slug)
        except TemplateProtectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"template not found: {slug}")
        return None

    @router.post("/generate")
    def generate(payload: ReportGenerateIn):
        from app.services.reporting.narrator import default_narrator

        try:
            job = generate_report(
                template_slug=payload.template_slug,
                portfolio_id=payload.portfolio_id,
                compare_to=payload.compare_to,
                narrate=default_narrator(),
            )
        except TemplateNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "id": job.id,
            "report_type": job.report_type,
            "status": job.status,
            "template_slug": job.template_slug,
            "compare_to_run_id": job.compare_to_run_id,
            "request_payload": job.request_payload,
            "result_payload": job.result_payload,
            "artifact_paths": job.artifact_paths,
            "created_at": job.created_at,
        }

    return router
```

**Route-order note:** `/templates/validate` is declared **before** `/templates/{slug}`. FastAPI
matches in declaration order, so the reverse would make `validate` resolve as a slug and 404.

- [ ] **Step 5: Mount the router and extend `_report_job_out`**

In `backend/app/main.py`, beside the other router mounts, add:

```python
from .routers.reports import build_reports_router
...
    app.include_router(build_reports_router())
```

And in `_report_job_out` (around line 510), add the two new fields:

```python
        template_slug=job.template_slug,
        compare_to_run_id=job.compare_to_run_id,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reports_router.py -v`
Expected: PASS, 11 tests

- [ ] **Step 7: Run the full backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Do not pipe through `tail`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/reports.py backend/app/main.py \
        backend/app/schemas.py tests/test_reports_router.py
git commit -m "feat(reporting): template and generation REST surface"
```

---

## Self-Review

**Spec coverage (§5.8, §5.9):**

| Spec requirement | Task |
|---|---|
| Six tools with declared capability groups | Task 2 |
| `list_report_blocks` as the authoring keystone | Task 2, test 6 |
| `save_report_template` HITL `"write"` with rationale | Task 2, test 3 + code comment |
| `generate_report` write-class but NOT HITL | Task 2, test 4 |
| Full registration checklist, all 5 sites | Task 2, step 5 |
| `create_report` remains registered | Task 2, test 5 |
| `reporting` added to trader's persona domains | Task 1 |
| Two skills with `routing:` blocks | Task 3 |
| Catalog exact-set tests updated | Task 3, steps 1 and 4 |
| REST: templates CRUD, validate, blocks, generate | Task 4 |
| Invalid PUT → 422, nothing persisted | Task 4, test 8 |
| Seeded template delete → 409 | Task 4, test 10 |
| `ReportJobOut` carries `template_slug` | Task 4, step 3 |

Not in this plan, correctly deferred to Plan C: every frontend concern and the legacy
`_write_html` / `_write_xlsx` deletion.

**Placeholder scan:** No TBD/TODO. Three steps require a check-then-adapt (the model resolver
name, the `collect_routing_rows` import path, the catalog test file list) — each gives the exact
command to run and the exact fallback.

**Type consistency:**
- `generate_report(template_slug=, portfolio_id=, compare_to=, narrate=, session=)` keyword-only —
  matches B3 Task 3 and both call sites (tool and router).
- `default_narrator() -> Callable[[str, dict], str]` — matches B3's `Narrator` alias.
- `templates_svc.save_template(slug=, spec_yaml=)`, `get_template(slug=)`,
  `delete_template(slug=)`, `list_templates(persona=)`, `validate_only(spec_yaml)` — all match
  B2 Task 4.
- `TemplateSpecError.errors: list[str]` — read in both the tool and the router.
- `spec.catalog_entry()` — defined on `BlockSpec` in B1 Task 1, called in the tool and router.
- `BlockResult.model_dump()` in `resolve_report_block_tool` — pydantic v2, matching Plan A Task 1.
- The 18-block count asserted in Task 2 test 6 and Task 4 test 1 matches the count asserted in
  B1-continued Task 5 step 5.

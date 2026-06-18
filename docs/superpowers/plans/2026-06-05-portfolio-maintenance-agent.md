# Portfolio Maintenance for the Desk Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desk agent HITL-gated portfolio creation and restructuring — bind 5 write tools with cards, enrich `create_portfolio`'s schema (CNY default), document the rule DSL, and add a `portfolio-maintenance` workflow skill owning all 8 portfolio writes.

**Architecture:** Spec `docs/superpowers/specs/2026-06-05-portfolio-maintenance-agent-design.md`. Binding and interrupt-listing always land together (a bound write without a card executes unconfirmed). The rule DSL lives in the tool's Field descriptions + the existing reference doc; the procedure lives in a new write-typed SKILL.md that all three personas see.

**Tech Stack:** Python/SQLAlchemy backend, pytest, LangGraph deep-agent prompt + skills files.

**Environment gotchas (read first):**
- Work from the worktree root: `/Users/fuxinyao/open-otc-trading/.claude/worktrees/portfolio-maintenance-agent`.
- ALWAYS prefix pytest with `PYTHONPATH=$PWD/backend` — the venv `.pth` resolves `app` to the MAIN checkout otherwise.
- 6 known pre-existing env failures (NOT yours): 2× run_python (langchain_sandbox), 3× model_factory deepseek, 1× quickjs.
- Rule-DSL ground truth (`backend/app/services/portfolio_rule.py`): composites are `{"op": "and"|"or", "children": [...]}`, negation `{"op": "not", "child": {...}}`, `MAX_RULE_DEPTH = 5`, error wording `Unsupported op: ...` / `Unknown field: ...`; the tool layer surfaces `RuleValidationError` as `{"ok": False, "errors": [...]}`.

---

## File Structure

| File | Change |
|---|---|
| `tests/test_hitl.py` | Exact-set pin += 5; YOLO spot assertion (Task 1) |
| `backend/app/services/deep_agent/hitl.py` | 5 entries × 3 maps (Task 1) |
| `backend/app/services/agents.py` | Bind 5 tools (Task 1) |
| `tests/test_tools_portfolios.py` | 3 new tests: CNY default, rule round-trip, bad-op refusal (Task 2) |
| `backend/app/tools/portfolios.py` | `_CreateInput` Field descriptions + CNY default ×2 (Task 2) |
| `backend/app/skills/references/portfolios/model.md` | Append "## Filter Rule DSL" (Task 3) |
| `tests/test_skills_catalog_v2.py` | Counts 21→22 / 20→21, high_board set += 1, new lint test (Task 4) |
| `backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md` | Create (Task 4) |
| `backend/app/services/deep_agent/prompts/orchestrator.md` | Bullet slimmed, matrix row, persisted +5 (Task 5) |
| `backend/app/services/deep_agent/prompts/trader.md` | Paragraph → skill pointer (Task 5) |

---

### Task 1: HITL cards + binding for the 5 portfolio writes

TDD via the two existing pins: extending the exact-set pin makes it fail until hitl.py has the entries; the bound-invariant test then fails until agents.py binds them.

**Files:**
- Modify: `tests/test_hitl.py:10-35` (exact-set pin), `:89-109` (YOLO spot check)
- Modify: `backend/app/services/deep_agent/hitl.py:42-46, 69-73, 98-102`
- Modify: `backend/app/services/agents.py:344-352`

- [ ] **Step 1: Extend the exact-set pin (failing test)**

In `tests/test_hitl.py`, inside `test_interrupt_tool_names_covers_all_state_mutating_tools`, replace:

```python
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
        "run_python",
    }
```

with:

```python
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
        "create_portfolio",
        "update_portfolio",
        "add_positions_to_portfolio",
        "add_portfolio_sources",
        "remove_portfolio_sources",
        "run_python",
    }
```

In `test_yolo_mode_uses_langchain_auto_approval_for_write_tools`, replace:

```python
    assert "set_hedge_bands" not in config
```

with:

```python
    assert "set_hedge_bands" not in config
    assert "create_portfolio" not in config  # portfolio writes are "write"-level
```

- [ ] **Step 2: Run to verify both fail**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hitl.py::test_interrupt_tool_names_covers_all_state_mutating_tools tests/test_hitl.py::test_yolo_mode_uses_langchain_auto_approval_for_write_tools -q --tb=line`
Expected: 2 FAILED (exact-set mismatch on the 5 names; YOLO KeyError-free but set-membership holds trivially — if the YOLO assertion passes already that is fine, the exact-set one MUST fail).

- [ ] **Step 3: Add the 5 entries to hitl.py (three maps)**

In `backend/app/services/deep_agent/hitl.py`, replace (INTERRUPT tuple tail):

```python
    "delete_portfolio",
    "set_portfolio_rule",
    "set_hedge_bands",
    "remove_positions_from_portfolio",
    "run_python",
)
```

with:

```python
    "delete_portfolio",
    "set_portfolio_rule",
    "set_hedge_bands",
    "remove_positions_from_portfolio",
    "create_portfolio",
    "update_portfolio",
    "add_positions_to_portfolio",
    "add_portfolio_sources",
    "remove_portfolio_sources",
    "run_python",
)
```

Replace (risk-level map):

```python
    "delete_portfolio": "irreversible",
    "set_portfolio_rule": "write",
    "set_hedge_bands": "write",
    "remove_positions_from_portfolio": "irreversible",
```

with:

```python
    "delete_portfolio": "irreversible",
    "set_portfolio_rule": "write",
    "set_hedge_bands": "write",
    "remove_positions_from_portfolio": "irreversible",
    # Portfolio maintenance writes are reversible (delete exists) — "write"
    # level, so YOLO mode auto-approves them.
    "create_portfolio": "write",
    "update_portfolio": "write",
    "add_positions_to_portfolio": "write",
    "add_portfolio_sources": "write",
    "remove_portfolio_sources": "write",
```

Replace (label map):

```python
    "remove_positions_from_portfolio": "Remove positions from portfolio",
```

with:

```python
    "remove_positions_from_portfolio": "Remove positions from portfolio",
    "create_portfolio": "Create portfolio",
    "update_portfolio": "Update portfolio",
    "add_positions_to_portfolio": "Add positions to portfolio",
    "add_portfolio_sources": "Add view sources",
    "remove_portfolio_sources": "Remove view sources",
```

- [ ] **Step 4: Run — exact-set passes, bound-invariant now fails**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hitl.py -q --tb=line 2>&1 | tail -5`
Expected: `test_interrupt_tools_are_bound_deep_agent_tools` FAILS listing the 5 unbound names; everything else passes.

- [ ] **Step 5: Bind in agents.py**

In `backend/app/services/agents.py` `DEEP_AGENT_TOOL_NAMES`, replace:

```python
        # Portfolio maintenance writes (HITL-gated; delete/remove are
        # "irreversible" so even YOLO mode keeps the card). Bound so the
        # cards in INTERRUPT_TOOL_NAMES are always executable.
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
```

with:

```python
        # Portfolio maintenance writes (all HITL-carded; delete/remove are
        # "irreversible" so even YOLO mode keeps their cards, the rest are
        # "write"). Bound so cards in INTERRUPT_TOOL_NAMES always execute.
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
        "create_portfolio",
        "update_portfolio",
        "add_positions_to_portfolio",
        "add_portfolio_sources",
        "remove_portfolio_sources",
```

- [ ] **Step 6: Run test_hitl + coupled suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_hitl.py tests/test_capability_assignments.py tests/test_personas.py tests/test_async_agents_unit.py -q --tb=short 2>&1 | tail -4`
Expected: only the known quickjs env failure (`test_orchestrator_can_enable_quickjs_code_interpreter_middleware`); all else passes.

- [ ] **Step 7: Commit**

```bash
git add tests/test_hitl.py backend/app/services/deep_agent/hitl.py backend/app/services/agents.py
git commit -m "feat(agent): bind create/update/membership/sources portfolio writes with HITL cards"
```

---

### Task 2: `_CreateInput` enrichment + CNY default

**Files:**
- Test: `tests/test_tools_portfolios.py` (append 3 tests)
- Modify: `backend/app/tools/portfolios.py:39-48` (`_CreateInput`), `:116-127` (signature default)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_portfolios.py` (the module has an autouse `_db` fixture; tools are invoked via `.invoke({...})`):

```python
def test_create_portfolio_tool_defaults_to_cny():
    """Desk convention (a26aca8): agent-layer creation defaults to CNY, not USD.
    REST keeps its own schema; this pins the tool layer only."""
    result = create_portfolio_tool.invoke({"name": "CcyDefault"})
    assert result["ok"] is True
    assert result["data"]["base_currency"] == "CNY"


def test_create_portfolio_tool_view_rule_round_trip():
    result = create_portfolio_tool.invoke({
        "name": "OpenSnowballs",
        "kind": "view",
        "filter_rule": {"op": "and", "children": [
            {"op": "eq", "field": "underlying", "value": "000905.SH"},
            {"op": "eq", "field": "status", "value": "open"},
        ]},
    })
    assert result["ok"] is True
    assert result["data"]["kind"] == "view"
    assert result["data"]["filter_rule"]["op"] == "and"


def test_create_portfolio_tool_rejects_bad_rule_op_without_persisting():
    result = create_portfolio_tool.invoke({
        "name": "BadRule",
        "kind": "view",
        "filter_rule": {"op": "bogus", "field": "underlying", "value": "X"},
    })
    assert result["ok"] is False
    assert any("Unsupported op" in e for e in result["errors"])
    listing = list_portfolios_tool.invoke({})
    assert all(p["name"] != "BadRule" for p in listing["data"])
```

- [ ] **Step 2: Run to verify failures**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_tools_portfolios.py -q --tb=line -k "cny or round_trip or bad_rule"`
Expected: `defaults_to_cny` FAILS (`"USD" != "CNY"`). The other two may already pass (rule validation exists service-side) — that is fine; they pin behavior the skill will rely on. The CNY one MUST fail.

- [ ] **Step 3: Rewrite `_CreateInput` and the signature default**

In `backend/app/tools/portfolios.py`, replace:

```python
class _CreateInput(BaseModel):
    name: str
    kind: Literal["container", "view"] = "container"
    base_currency: str = "USD"
    description: str | None = None
    filter_rule: dict[str, Any] | None = None
    manual_include_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
```

with:

```python
class _CreateInput(BaseModel):
    name: str
    kind: Literal["container", "view"] = Field(
        default="container",
        description="container explicitly holds positions; view derives "
        "membership from filter_rule/sources and recomputes on query.",
    )
    base_currency: str = Field(
        default="CNY",
        description="ISO-4217; desk convention is CNY.",
    )
    description: str | None = None
    filter_rule: dict[str, Any] | None = Field(
        default=None,
        description="View rule DSL — leaf {op, field, value}; composites "
        "{op: and|or, children: [...]}, {op: not, child: {...}}; ops eq/ne, "
        "in/not_in, lt/lte/gt/gte/between over fields product_type, "
        "underlying, status, mapping_status, engine_name, quantity, "
        "entry_price, created_at. See /skills/references/portfolios/model.md.",
    )
    manual_include_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(
        default_factory=list,
        description="View-only: other portfolios whose resolved positions "
        "feed this view (cycle/depth-checked).",
    )
    tags: list[str] = Field(default_factory=list)
```

Then in `create_portfolio_tool`'s signature, replace:

```python
    base_currency: str = "USD",
```

with:

```python
    base_currency: str = "CNY",
```

- [ ] **Step 4: Run the module**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_tools_portfolios.py -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS (existing envelope tests pass explicit values, unaffected by the default flip).

- [ ] **Step 5: Commit**

```bash
git add tests/test_tools_portfolios.py backend/app/tools/portfolios.py
git commit -m "feat(tools): create_portfolio defaults CNY + rule-DSL Field descriptions"
```

---

### Task 3: Rule-DSL section in the portfolios reference doc

**Files:**
- Modify: `backend/app/skills/references/portfolios/model.md` (append at end)

- [ ] **Step 1: Append the section**

Append to `backend/app/skills/references/portfolios/model.md` (after "## Empty Portfolio Semantics"):

```markdown

## Filter Rule DSL

A view's `filter_rule` is a nested dict. Leaf:
`{"op": <op>, "field": <field>, "value": <value>}`. Composite:
`{"op": "and"|"or", "children": [<rule>, ...]}` (non-empty) and
`{"op": "not", "child": <rule>}`. Max nesting depth 5. Validation errors read
`Unsupported op: ...` / `Unknown field: ...` and surface from the tools as
`{"ok": false, "errors": [...]}`.

Ops: `eq`, `ne` (scalar); `in`, `not_in` (list value); `lt`, `lte`, `gt`,
`gte` (scalar, ordered); `between` (value = [low, high]).

Fields and types: `product_type` (str), `underlying` (str), `status` (str:
open/closed), `mapping_status` (str), `engine_name` (str), `quantity` (float),
`entry_price` (float), `created_at` (datetime, ISO strings accepted).

Examples:

    {"op": "eq", "field": "underlying", "value": "000905.SH"}

    {"op": "and", "children": [
      {"op": "in", "field": "product_type",
       "value": ["SnowballOption", "PhoenixOption"]},
      {"op": "eq", "field": "status", "value": "open"}
    ]}

Rules match positions in CONTAINER portfolios only (views never source other
views through rules — use sources for that).
```

- [ ] **Step 2: Run the reference-docs gate**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_reference_docs.py -q --tb=short 2>&1 | tail -3`
Expected: ALL PASS (file edited, not added — the exact-file-set pin is untouched; frontmatter unchanged).

- [ ] **Step 3: Commit**

```bash
git add backend/app/skills/references/portfolios/model.md
git commit -m "docs(skills): document the view filter-rule DSL in the portfolios reference"
```

---

### Task 4: `portfolio-maintenance` workflow skill

TDD via the catalog pins. The phase3 lint tests only cover pinned historical sets, so this task ALSO adds a focused lint test — otherwise nothing gates the new skill's 500-token budget.

**Files:**
- Modify: `tests/test_skills_catalog_v2.py:141, 161, 176-188` (+ new lint test at end)
- Create: `backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md`

- [ ] **Step 1: Update the catalog pins + add the lint test (failing tests)**

In `tests/test_skills_catalog_v2.py`:

Replace:
```python
    assert len(catalog) == 21, f"Expected 21 entries, got {len(catalog)}: {catalog}"
```
with:
```python
    assert len(catalog) == 22, f"Expected 22 entries, got {len(catalog)}: {catalog}"
```

Replace:
```python
    assert len(catalog) == 20, f"Expected 20 entries, got {len(catalog)}: {catalog}"
```
with:
```python
    assert len(catalog) == 21, f"Expected 21 entries, got {len(catalog)}: {catalog}"
```

In `test_high_board_total_workflow_catalog`, replace:
```python
    assert catalog == {
        "portfolio-membership",
        "portfolio-view-counting",
        "generate-report",
        "batch-run-reports",
        "display-report",
    }
```
with:
```python
    # portfolio-maintenance is visible here too (high_board sources
    # /workflows/portfolios/); catalog visibility != capability — every
    # write in it is HITL-gated.
    assert catalog == {
        "portfolio-membership",
        "portfolio-view-counting",
        "portfolio-maintenance",
        "generate-report",
        "batch-run-reports",
        "display-report",
    }
```

Append at end of file (mirrors the phase3 lint idiom; `lint_skill_file`/`parse_skill_file` imports may already exist — add only if missing):

```python
def test_portfolio_maintenance_skill_is_ci_lint_clean() -> None:
    """No phase3 pin covers post-phase3 skills; without this, nothing gates
    the 500-token body budget or frontmatter schema of portfolio-maintenance."""
    from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
    from app.services.deep_agent.skills_paths import WORKFLOWS_DIR

    path = WORKFLOWS_DIR / "portfolios/portfolio-maintenance/SKILL.md"
    warnings = lint_skill_file(path, mode="ci", root=SKILLS_ROOT)
    assert [w for w in warnings if w.severity == "error"] == []
    parsed = parse_skill_file(path)
    assert parsed.frontmatter["name"] == "portfolio-maintenance"
    assert parsed.frontmatter["write_actions"] is True
    assert parsed.frontmatter["confirmation_required"] is True
    assert "## Example" in parsed.body
```

(`SKILLS_ROOT` is already imported at the top of `tests/test_skills_catalog_v2.py`
from `app.services.deep_agent.skills_paths`; `lint_skill_file`/`parse_skill_file`/
`WORKFLOWS_DIR` are imported function-locally above to keep the module's top
imports untouched. `parse_skill_file(path)` returns an object with `.frontmatter`
and `.body` — same accessors `tests/test_workflow_skills_phase3.py:76-81` uses.)

- [ ] **Step 2: Run to verify failures**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_skills_catalog_v2.py -q --tb=line 2>&1 | tail -6`
Expected: 4 FAILED (trader count, risk count, high_board exact set, lint test file-not-found).

- [ ] **Step 3: Create the SKILL.md**

Create `backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md` with EXACTLY this content (body pre-counted 481/500 tokens — re-count after ANY edit, Step 4 enforces):

```markdown
---
name: portfolio-maintenance
description: Create or restructure portfolios through HITL-confirmed writes — containers, rule-driven views, membership, sources, renames, deletion. Use when user asks to create a portfolio or view, change a view's rule or sources, rename or retag a portfolio, add or remove positions from one, or delete one.
domain: portfolios
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - requested_change
optional_context:
  - portfolio_id
  - portfolio_name
  - filter_rule
write_actions: true
confirmation_required: true
success_criteria:
  - the single proposed write is confirmed via HITL card and verified by re-read
  - rule or kind errors are reported verbatim without persistence
---

## When to use

- Create a container or a rule-driven view portfolio.
- Rename, re-describe, re-currency, or retag a portfolio.
- Change a view's filter rule or its cross-portfolio sources.
- Add or remove positions; delete a portfolio.

## Required inputs

The requested change. Resolve the target via `list_portfolios` /
`get_portfolio`; ask when several portfolios match a name. For create:
confirm name, kind (container holds positions; view derives them), and
base_currency (desk default CNY).

## Procedure

1. Resolve the target portfolio, or confirm create parameters.
2. For view rules, build `filter_rule` from the DSL in
   `/skills/references/portfolios/model.md` (ops: and/or/not, eq/ne,
   in/not_in, lt/lte/gt/gte/between; fields: product_type, underlying,
   status, mapping_status, engine_name, quantity, entry_price,
   created_at). Check every op and field against that list first.
3. Propose exactly ONE write per turn: `create_portfolio`,
   `update_portfolio`, `set_portfolio_rule`, `add_positions_to_portfolio`,
   `remove_positions_from_portfolio`, `add_portfolio_sources`,
   `remove_portfolio_sources`, or `delete_portfolio`. Each is HITL-gated —
   the confirmation card is the gate.
4. After approval, verify with `get_portfolio`: report the id and, for
   views, the resolved membership count.

## Stop conditions

- Never use `remove_positions_from_portfolio` to close or settle a trade —
  container removal physically deletes rows; lifecycle tools
  (`close_position`/`settle_position`) preserve history.
- Container position-filling goes through booking or OTC import
  (`add_positions_to_portfolio` is view-only).
- Deleting a container cascades its positions — restate that when proposing.

## Output shape

Portfolio id, name, kind, what changed, membership count for views,
validation errors verbatim.

## References

- `/skills/references/portfolios/model.md`

## Example

User: Create a view of my open snowballs on 000905.SH.
Assistant: Build the rule, propose `create_portfolio(kind="view",
filter_rule=...)`, wait for the card, report the new id and member count.
```

- [ ] **Step 4: Verify the token budget with the production counter**

Run: `PYTHONPATH=$PWD/backend python3 -c "
from pathlib import Path
from app.services.deep_agent.skill_lint import count_body_tokens, parse_skill_file
p = Path('backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md')
print('tokens:', count_body_tokens(parse_skill_file(p).body))"`
Expected: `tokens:` ≤ 500 (target 481±2). Over 500 → trim the body, never the frontmatter.

- [ ] **Step 5: Run the catalog + skills suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_skills_catalog_v2.py tests/test_skills_catalog.py tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py tests/test_reference_docs.py -q --tb=short 2>&1 | tail -4`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_skills_catalog_v2.py backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md
git commit -m "feat(skills): portfolio-maintenance workflow skill owns all 8 portfolio writes"
```

---

### Task 5: Routing matrix row + prompt slimming

Prose-only; verified by the prompt-contract suites.

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md:66-71` (bullet), `~:126` (matrix), `:355` (persisted list)
- Modify: `backend/app/services/deep_agent/prompts/trader.md:66-73`

- [ ] **Step 1: Slim the orchestrator routing bullet**

Replace:

```markdown
- Portfolio maintenance (delete a portfolio, replace a view's filter rule,
  remove positions from a portfolio) → `trader`, direct tool use
  (`delete_portfolio` / `set_portfolio_rule` / `remove_positions_from_portfolio`),
  each HITL-gated. Removing positions from a **container** physically deletes
  the position rows; "close out a position" is lifecycle (`close_position`),
  never removal.
```

with:

```markdown
- Portfolio maintenance (create/rename a portfolio, views & their rules or
  sources, membership, deletion) → `trader` with `portfolio-maintenance`.
  "Close out a position" is lifecycle (`close_position`), never removal.
```

- [ ] **Step 2: Add the routing-matrix row**

Replace:

```markdown
| Book stated hedge legs / act on a hedge recommendation | trader        | hedge-portfolio                  |
```

with:

```markdown
| Book stated hedge legs / act on a hedge recommendation | trader        | hedge-portfolio                  |
| Create or manage a portfolio (views, rules, sources)   | trader        | portfolio-maintenance            |
```

- [ ] **Step 3: Extend the persisted-tools list**

In the `NEVER request more than one persisted/HITL-gated tool call` line, replace:

```markdown
`delete_portfolio`, `set_portfolio_rule`, `remove_positions_from_portfolio`, plus `run_python` when `writes_artifacts=true`.
```

with:

```markdown
`delete_portfolio`, `set_portfolio_rule`, `remove_positions_from_portfolio`, `create_portfolio`, `update_portfolio`, `add_positions_to_portfolio`, `add_portfolio_sources`, `remove_portfolio_sources`, plus `run_python` when `writes_artifacts=true`.
```

- [ ] **Step 4: Replace the trader paragraph with a skill pointer**

In `backend/app/services/deep_agent/prompts/trader.md`, replace:

```markdown
For portfolio maintenance you may delete portfolios (`delete_portfolio`),
replace a view's filter rule (`set_portfolio_rule`), and remove positions from
a portfolio (`remove_positions_from_portfolio`). All three are HITL-gated.
Sharp edges: deleting a **container** cascades its positions; removing
positions from a **container** physically deletes the rows (a view only
un-includes them). When the user means "close/settle a trade," use the
lifecycle tools (`close_position`/`settle_position`) — removal destroys
history, lifecycle preserves it.
```

with:

```markdown
For portfolio maintenance (create/update/delete portfolios, view rules,
membership, sources), read
`/skills/workflows/portfolios/portfolio-maintenance/SKILL.md` — all its
writes are HITL-gated, and removal-vs-lifecycle semantics live there.
```

- [ ] **Step 5: Run the prompt-contract suites**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/test_routing_contracts_phase3.py tests/test_workflow_skills_phase3.py tests/test_personas.py tests/test_skills_catalog.py tests/test_skills_catalog_v2.py -q --tb=short 2>&1 | tail -4`
Expected: only the known quickjs env failure.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md backend/app/services/deep_agent/prompts/trader.md
git commit -m "docs(agent): route portfolio maintenance through the new skill; slim prompts"
```

---

### Task 6: Full-suite verification

- [ ] **Step 1: Run the full backend suite**

Run: `PYTHONPATH=$PWD/backend python -m pytest tests/ -q --tb=no 2>&1 | tail -12`
Expected: exactly the 6 known env failures (2× run_python, 3× deepseek, 1× quickjs). Anything else is a regression — stop and investigate.

- [ ] **Step 2: Verify clean tree + history**

Run: `git status --short && git log --oneline -8`
Expected: clean; commits from Tasks 1–5 + spec commits.

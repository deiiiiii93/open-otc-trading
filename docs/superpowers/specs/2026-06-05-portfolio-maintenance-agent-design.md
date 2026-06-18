# Portfolio Maintenance for the Desk Agent — Design

**Date:** 2026-06-05
**Status:** Design approved in conversation; awaiting written-spec review
**Origin:** Closes the "agent can delete but not create portfolios" asymmetry left
open by the risk-hygiene cycle (spec 2026-06-05-risk-hygiene-fixes-design.md).
User chose **full family** scope and **Approach B** (binding + new workflow skill).

## Problem

`create_portfolio`, `update_portfolio`, `add_positions_to_portfolio`,
`add_portfolio_sources`, `remove_portfolio_sources` are registered, DOMAIN_WRITE-
gated tools (tools/portfolios.py) that are neither interrupt-listed nor bound —
the agent cannot create or restructure portfolios at all, while it CAN delete
them (bound last cycle). Binding without interrupt-listing is forbidden: a bound
write with no HITL card executes unconfirmed.

Note the count: the conversation's "full family (4 tools)" option undercounted —
"sources" is two tools (`add_portfolio_sources`, `remove_portfolio_sources`), so
this cycle binds **5** new tools.

## Design

### 1. HITL + binding (always together)

`backend/app/services/deep_agent/hitl.py` — five additions to all three maps,
placed after the existing portfolio entries:

- `INTERRUPT_TOOL_NAMES` += `create_portfolio`, `update_portfolio`,
  `add_positions_to_portfolio`, `add_portfolio_sources`, `remove_portfolio_sources`
- `_RISK_LEVEL_BY_TOOL`: all five `"write"` (reversible — `delete_portfolio` and
  container `remove_positions_from_portfolio` remain the only irreversible
  portfolio ops). Consequence: YOLO mode auto-approves these five; the cards
  still render as records.
- `_LABEL_BY_TOOL`: "Create portfolio", "Update portfolio",
  "Add positions to portfolio", "Add view sources", "Remove view sources".

`backend/app/services/agents.py` `DEEP_AGENT_TOOL_NAMES` — five additions next
to the existing portfolio-maintenance block (comment updated to "Portfolio
maintenance (HITL-gated)...").

Tests: `tests/test_hitl.py` exact-set INTERRUPT pin += 5 names; the
`test_interrupt_tools_are_bound_deep_agent_tools` invariant then enforces the
binding (TDD: extend the pin first, watch it fail, bind, watch it pass).

### 2. Tool schema enrichment (`tools/portfolios.py` `_CreateInput`)

The book_hedge lesson: bound tools expose Field descriptions — that is where
schema knowledge belongs, not in prompt prose.

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
        description="View rule DSL — ops and/or/not, eq/ne, in/not_in, "
        "lt/lte/gt/gte/between over fields product_type, underlying, status, "
        "mapping_status, engine_name, quantity, entry_price, created_at. "
        "See /skills/references/portfolios/model.md.",
    )
    manual_include_ids: list[int] = Field(default_factory=list)
    source_portfolio_ids: list[int] = Field(
        default_factory=list,
        description="View-only: other portfolios whose resolved positions "
        "feed this view (cycle/depth-checked).",
    )
    tags: list[str] = Field(default_factory=list)
```

The `base_currency` default flips `"USD"` → `"CNY"` in BOTH the pydantic schema
and the `create_portfolio_tool` function signature default, per desk convention
(a26aca8 established CNY defaults for product-spec layers). Agent-tool layer
only; the REST endpoint keeps its own schema untouched.

### 3. Rule-DSL documentation (`skills/references/portfolios/model.md`)

Append one section to the EXISTING reference file (no new file — adding a
reference file trips `test_reference_docs.py`'s exact-file-set pin; reference
docs have no token cap):

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

(Verified: `_resolve_inner` in portfolio_membership.py:84-89 joins Portfolio and
filters rule matches to `Portfolio.kind == CONTAINER` — the doc sentence states
exactly that behavior.)

### 4. New workflow skill `portfolio-maintenance`

`backend/app/skills/workflows/portfolios/portfolio-maintenance/SKILL.md` —
write-typed sibling of the two read skills. Full sketch (body pre-counted
**481/500 tokens** with the real `count_body_tokens` — 19 headroom; re-count any
deviation):

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

The skill also owns last cycle's three tools (`delete_portfolio`,
`set_portfolio_rule`, `remove_positions_from_portfolio`) — one procedure for the
whole family.

### 5. Routing & prompt slimming

`prompts/orchestrator.md`:

- Routing matrix row: `| Create or manage a portfolio (containers, views, rules, sources) | trader | portfolio-maintenance |`
- Last cycle's portfolio-maintenance routing **bullet** shrinks to name the
  skill instead of enumerating tools:

  > - Portfolio maintenance (create/rename a portfolio, views & their rules or
  >   sources, membership, deletion) → `trader` with `portfolio-maintenance`.
  >   "Close out a position" is lifecycle (`close_position`), never removal.

- Persisted-tools list += the 5 new names (now 8 portfolio writes total there).

`prompts/trader.md`: last cycle's paragraph is REPLACED by a pointer:

> For portfolio maintenance (create/update/delete portfolios, view rules,
> membership, sources), read
> `/skills/workflows/portfolios/portfolio-maintenance/SKILL.md` — all its
> writes are HITL-gated, and removal-vs-lifecycle semantics live there.

### 6. Catalog visibility & test pins

The new SKILL.md lands in `/workflows/portfolios/`, which all three personas
source. Deliberate consequence: **high_board sees it too** (catalog visibility ≠
capability; HITL still gates every write; a board-level "make me a view for this
report" is legitimate). Pins that change:

- `tests/test_skills_catalog_v2.py`: trader count 21 → 22; risk_manager 20 → 21;
  high_board **exact set** += `portfolio-maintenance`.
- `tests/test_hitl.py`: INTERRUPT exact-set pin += 5.
- The phase3 lint tests cover only pinned historical file sets — they do NOT
  pick up new skills. A focused `test_portfolio_maintenance_skill_is_ci_lint_clean`
  is added to `test_skills_catalog_v2.py` to gate the new body (≤500 tokens) and
  frontmatter schema durably.
- `tests/test_skills_catalog.py` / `test_workflow_skills_phase3.py`: no
  `/workflows/portfolios/` directory pins exist — unaffected.
- `tests/test_reference_docs.py`: unaffected (model.md edited, not added).

### 7. Testing

- TDD the pins: extend INTERRUPT exact set + catalog counts first (fail), then
  bind/add files (pass).
- New unit test: `create_portfolio` tool round-trips a view with a valid rule
  (tool → service → shape), and surfaces `RuleValidationError` verbatim for a
  bad op (no persistence).
- New unit test: tool-layer default `base_currency == "CNY"` (schema and
  signature agree).
- Full suite; the 6 known env failures are pre-existing.

## Out of scope

- REST API changes (its own schemas, including its USD default if any, stay).
- Container position-specs via agent (`add_positions_to_portfolio` stays
  view-only; container filling is booking/import territory).
- A YOLO-mode exception for portfolio writes (all five are "write"; standard
  YOLO semantics apply).
- Per-persona skill exclusion (high_board keeps seeing the skill).

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
routing:
  - request: "Create or manage a portfolio (views, rules, sources)"
    persona: trader
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

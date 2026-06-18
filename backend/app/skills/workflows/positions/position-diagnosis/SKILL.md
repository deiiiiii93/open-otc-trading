---
name: position-diagnosis
description: Diagnose unexpected position value, Greek, PnL, pricing, or risk contribution. Use when user asks why a position value, Greek, PnL, price, or risk number looks wrong, when a pet-page answer needs more than loaded table facts, or when a Snowball position needs barrier, lifecycle, valuation, and risk context joined.
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
routing:
  - request: "Unexpected position value, Greek, PnL, or contribution"
    persona: trader
---

## When to use

- User asks why a position value, Greek, PnL, price, or risk number looks wrong.
- Pet page answer needs more than loaded table facts.
- A Snowball position needs barrier, lifecycle, valuation, or risk context joined.

## Required inputs

`position_id` is required. Use `portfolio_id`, selected pricing profile, and market-data profile when available.

## Procedure

1. Read the position through `get_position_summaries` using portfolio and product filters when compact terms are enough; use `get_product_details` (or the family term tools) only when raw executable terms are required.
2. Read latest valuation and latest risk context when the user asks about price, PnL, or Greeks.
3. Compare product terms, lifecycle flags, stored valuation status, and risk metrics.
4. State likely drivers, missing inputs, and whether a specialized Snowball workflow should continue later.

## Stop conditions

Ask for `position_id` when missing. Escalate to `desk_workflow` for cross-portfolio reads or to `desk_async` for large book-wide diagnosis.

## Output shape

Lead with verdict, then observed value, compared inputs, likely drivers, missing evidence, and recommended next action.

## References

- `/skills/references/products/snowball-cn.md`
- `/skills/references/pricing/engines.md`

## Example

User: Why does position 42 have such high gamma?
Assistant: Read the position, valuation, and risk context, then explain product drivers and uncertainty without mutating state.

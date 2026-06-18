---
name: position-snapshot
description: Build a compact portfolio position view with latest stored valuations for downstream workflows. Use when a workflow needs the current positions in a portfolio, when user asks how many positions are loaded or which positions match a filter, or when pricing, risk, market-data, or diagnostics needs a read-first portfolio view.
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

1. Call `get_positions(portfolio_id=<id>, product_type=<optional>, status=<optional>)` for inventory counts, or `get_position_summaries` when downstream work needs compact product terms.
2. Call `get_latest_position_valuations(portfolio_id=<id>, limit=500)`.
3. Join positions by `position.id == valuation.position_id`.
4. Count total positions, valued positions, missing valuations, and failed valuations.

## Stop conditions

Ask for portfolio selection when no `portfolio_id` is available. Note the 500 valuation read cap when the portfolio exceeds it.

## Output shape

Return counts first, then any filter used, valuation coverage, failed valuation ids, and whether downstream repricing is likely needed.

## References

- `/skills/references/portfolios/model.md`

## Example

User: Snapshot this portfolio.
Assistant: Read positions and latest valuations, then return counts, coverage, and missing or failed valuation rows.

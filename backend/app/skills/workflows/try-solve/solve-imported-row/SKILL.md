---
name: solve-imported-row
description: Solve the selected Try Solve row when product terms and market inputs are ready. Use when user asks to solve the selected Try Solve row, when page context exposes the page action solve_imported_row for the selected row, or when row status is solver_ready or diagnostics show what prevents solving.
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
- Page context exposes page action `solve_imported_row` for the selected row.
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

- `/skills/references/pricing/engines.md`

## Example

User: Solve this imported row.
Assistant: Use selected `row_id`, check readiness, return page-action request `solve_imported_row`, and summarize the solved value or missing inputs.

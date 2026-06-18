---
name: create-request-queue-item
description: Create a Try Solve request queue item once enough product terms are present. Use when user provides enough terms on the Try Solve page to create a queue item, when user asks to price a structured product from the page instead of an imported workbook row, or when the selected row has captured schema but still needs term completion before solve.
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

- `/skills/references/products/snowball-cn.md`
- `/skills/references/pricing/engines.md`

## Example

User: Price a Snowball product with 000852.SH, 3Y, KO 103%, KI 75%.
Assistant: Check missing target and market fields, ask once if needed, then create the request queue item after confirmation.

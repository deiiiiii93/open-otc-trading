---
name: quote-rfq
description: Solve and persist a quote for an existing RFQ draft when terms and market inputs are valid. Use when a validated RFQ draft needs a model quote, when user asks to re-quote a persisted RFQ row, or when an intake workflow has determined the draft is ready to solve.
domain: rfq
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - rfq_id
optional_context:
  - market_inputs
  - engine_spec
write_actions: true
confirmation_required: false
success_criteria:
  - quote version is persisted for the RFQ id
  - solved value and engine are reported
routing:
  - request: "RFQ solve / quote a product spec"
    persona: trader
---

## When to use

- A validated RFQ draft needs a model quote.
- User asks to re-quote a persisted RFQ row.
- Intake workflow has determined the draft is ready to solve.

## Required inputs

Use `rfq_id` and the full draft payload. Read `/skills/references/rfq/lifecycle.md` and `/skills/references/pricing/engines.md` before quoting path-dependent products.

## Procedure

1. Cost-preview when the product is path-dependent or engine cost is unclear.
2. Call `solve_rfq` with the full RFQ request draft payload.
3. Call `quote_rfq(rfq_id=<id>, ...)` using quote request fields; do not pass a fabricated price argument.
4. Return quote id, solved value, engine, state, and audit event.

## Stop conditions

Stop if the RFQ is not in a quoteable state or validation errors remain.

## Output shape

Return quoted or blocked, then RFQ id, quote id, solved value, engine, valuation date, and next lifecycle step.

## References

- `/skills/references/rfq/lifecycle.md`
- `/skills/references/pricing/engines.md`

## Example

User: Quote RFQ 42.
Assistant: Check state, solve the full draft payload, call `quote_rfq`, and report the new quote version.

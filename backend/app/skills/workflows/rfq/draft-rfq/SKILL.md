---
name: draft-rfq
description: Create or update a persisted RFQ draft from natural language after validating required terms. Use when a trader receives natural-language RFQ terms that should become a draft row, when an existing draft needs updated terms before quote, or when an intake workflow found enough terms to validate and persist a draft.
domain: rfq
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - request_text
optional_context:
  - rfq_id
  - client_name
write_actions: true
confirmation_required: false
success_criteria:
  - RFQ draft id is returned
  - validation blockers are reported without persistence
routing:
  - request: "RFQ draft from natural language"
    persona: trader
---

## When to use

- Trader receives natural-language RFQ terms that should become a draft row.
- Existing draft needs updated terms before quote.
- Intake workflow found enough terms to validate and persist a draft.

## Required inputs

Use the full request text and any explicit client, product, side, tenor, and target fields. Read `/skills/references/rfq/lifecycle.md` for state expectations.

## Procedure

1. Call `build_product(family=<class>, terms=<extracted>)` to obtain validated
   product terms (use `build-product` for term extraction guidance). If it
   reports missing terms, ask for them before continuing.
2. Call `validate_rfq_terms(terms=<draft terms>)`.
3. If hard validation errors exist, stop and return them.
4. Call `create_or_update_rfq_draft(draft=<validated terms>, rfq_id=<optional>)`.

## Stop conditions

Do not persist a known-invalid draft. Ask for one missing required economic term when validation cannot proceed.

## Output shape

Return created or blocked, then draft id, product, underlying, quote mode, missing terms, and validation result.

## References

- `/skills/references/rfq/lifecycle.md`

## Example

User: Draft this RFQ: buy a two-year Snowball on 000852.SH, solve coupon.
Assistant: Extract terms, validate, persist the draft when valid, and return draft id plus missing fields if any.

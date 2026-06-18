---
name: intake-request
description: Turn a client RFQ request into the next RFQ workflow step while preserving lifecycle and missing-term state. Use when user sends natural-language RFQ terms and asks what can be quoted, when RFQ state is unclear and the next workflow step must be chosen, or when a client request needs catalog coverage, draft readiness, or quote readiness checked.
domain: rfq
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - request_text
optional_context:
  - rfq_id
  - product_key
write_actions: false
confirmation_required: false
success_criteria:
  - next RFQ step is identified
  - missing terms or state blockers are explicit
routing:
  - request: "RFQ intake / client request capture"
    persona: trader
---

## When to use

- User sends natural-language RFQ terms and asks what can be quoted.
- RFQ state is unclear and the next workflow step must be chosen.
- Client request needs catalog coverage, draft readiness, or quote readiness checked.

## Required inputs

Use the client request text, page context, or existing `rfq_id`. Read `/skills/references/rfq/lifecycle.md` for valid state transitions.

## Procedure

1. Call `get_rfq_catalog` if product coverage or template fields are unclear.
2. If no RFQ row exists, route to `draft-rfq`.
3. If a valid quoted row is requested for governance, route to `submit-for-approval`.
4. If a draft exists and terms are valid, route to `quote-rfq`.

## Stop conditions

Ask one focused question when required economics are missing. Do not skip lifecycle state checks.

## Output shape

Return next step first, then product coverage, missing terms, current state, and the workflow to use next.

## References

- `/skills/references/rfq/lifecycle.md`

## Example

User: Client wants a Snowball on CSI 1000, can we quote it?
Assistant: Check catalog coverage, identify missing economics, and route to `draft-rfq` or `quote-rfq`.

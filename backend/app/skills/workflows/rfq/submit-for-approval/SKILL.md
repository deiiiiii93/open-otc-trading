---
name: submit-for-approval
description: Submit a quoted RFQ for governance approval after verifying it is in the quoted state. Use when user explicitly asks to submit, send, or route a quoted RFQ for approval, when a trader workflow has produced a quote and needs governance review, or when page context exposes a quoted RFQ selected for approval.
domain: rfq
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - rfq_id
optional_context:
  - approver
write_actions: true
confirmation_required: true
success_criteria:
  - RFQ is submitted for approval
  - state mismatch is reported without mutation
routing:
  - request: "Submit quoted RFQ for approval"
    persona: trader
---

## When to use

- User explicitly asks to submit, send, or route a quoted RFQ for approval.
- Trader workflow has produced a quote and needs governance review.
- Page context exposes a quoted RFQ selected for approval.

## Required inputs

Use `rfq_id` and confirm the RFQ is quoted before mutation. Read `/skills/references/rfq/lifecycle.md` for state transitions.

## Procedure

1. Verify the selected RFQ is in `quoted` state from context or prior workflow output.
2. Compose confirmation summary with RFQ id, key terms, quote, and approver.
3. After confirmation, call `submit_rfq_for_approval(rfq_id=<id>)`.
4. Return state, audit event, and governance next step.

## Stop conditions

Do not submit drafts, rejected RFQs, released RFQs, or already submitted RFQs. Report the current state and the required prior step.

## Output shape

Return submitted or blocked, then RFQ id, prior state, new state, approver, and audit event.

## References

- `/skills/references/rfq/lifecycle.md`

## Example

User: Submit RFQ 42 for approval.
Assistant: Verify it is quoted, ask confirmation, call `submit_rfq_for_approval`, and report the new state.

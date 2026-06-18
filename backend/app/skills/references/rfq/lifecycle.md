---
name: lifecycle
description: Durable RFQ lifecycle states, transition ownership, and audit conventions.
reference_type: rfq
---

## State Sequence

The RFQ lifecycle starts at `draft`. A submitted but not yet priced request is
`submitted`. Pricing moves the RFQ to either `pending_approval` when successful
or `pricing_failed` when valuation or term validation fails. Approval then moves
to `approved` or `rejected`; approved RFQs continue through `released`,
`client_accepted`, and `booked`.

## Transition Ownership

Trader workflows own drafting, validating, submitting, quoting, release, client
acceptance, and booking to position. High-board workflows own approval and
rejection while the RFQ is `pending_approval`. A `pricing_failed` RFQ returns to
trader ownership for term repair or repricing.

## HITL Gates

Approval, rejection, release, client acceptance, and booking are explicit
human-in-the-loop gates. Draft creation, submission, and quote calculation can
run without HITL when the required terms and pricing inputs are present. Booking
always requires confirmation because it materializes a position.

## Audit Events

Every state transition should emit an audit event with actor, timestamp, and
diff. Workflow output should reference the RFQ identifier and relevant audit
event type so the user can reconcile operational state with persisted history.

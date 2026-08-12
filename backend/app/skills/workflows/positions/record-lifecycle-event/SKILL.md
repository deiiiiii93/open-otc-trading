---
name: record-lifecycle-event
description: Record what happened to a live trade — it expired, was exercised, knocked in or out, paid a coupon, reached maturity, or was unwound. Use when a user reports a real event on an existing position and the book must reflect it, when a trade ended and the desk needs the reason on record, or when a barrier observation, fixing or coupon has to be logged against a position.
domain: positions
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - position_id
optional_context:
  - portfolio_id
  - event_date
  - amount
write_actions: true
confirmation_required: true
success_criteria:
  - the recorded event type names what actually happened, not merely that the position closed
  - amounts that imply cash are passed under settlement_amount, payoff or coupon_amount
  - the resulting position status and any generated cashflow are reported back
routing:
  - request: "Record a lifecycle event on a position — expiry, exercise, knock-in or knock-out, coupon, fixing, maturity, unwind or settlement"
    persona: trader
---

## When to use

Something real happened to an existing trade and the book must record it.

## Required inputs

`position_id` (or `source_trade_id`), what happened, its date, and any amount.

## Procedure

1. Read the position: `get_positions` or `get_position_terms`. Confirm the
   product family and current status.
2. **Pick the event that describes the CAUSE, not the outcome.** Most events
   close the position, so "it is closed now" never chooses between them:

   - expired worthless, nothing owed → `expire` (books no cashflow)
   - the holder exercised → `exercise` (`early: true` if before expiry)
   - ran to its final maturity date → `maturity`
   - a barrier was breached → `knock_in` / `knock_out`
   - negotiated unwind by agreement → `close`
   - cash settled with no more specific cause → `settle`

3. Record it with `record_lifecycle_event(position_id=..., event_type=...,
   event_data={...})`. `close_position`, `settle_position` and `mark_knockout`
   are shorthands for three of those types only. An illegal type for the family
   is refused with the legal list — re-read it and retry.
4. Verify: report the new status and any cashflow the event generated.

## Guardrails

- Cash must use `settlement_amount` (or `payoff`), or `coupon_amount` for
  coupons. Any other key is recorded but generates no cashflow.
- Never settle zero for a worthless expiry: it creates a zero cash row the desk
  must still release. `expire` is the record that nothing is owed.
- `barrier_reset` records a barrier step; it does not reprice the position.
- Correct a mistake with `cancel_lifecycle_event`, never a second event.

## Example

User: The AAPL call in position 12 expired out of the money yesterday.
Assistant: Read position 12, then `record_lifecycle_event(position_id=12,
event_type="expire", event_data={"expiry_date": "...", "reason": "OTM at
expiry"})`; report status `closed` and that no cashflow was generated.

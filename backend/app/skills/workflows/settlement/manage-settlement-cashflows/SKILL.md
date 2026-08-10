---
name: manage-settlement-cashflows
description: Work the settlement queue — review cashflows generated from position lifecycle events, fill missing amounts, release or block them, mark them settled, and issue settlement notices. Use when the user asks what the desk owes or is owed, wants a payment released, blocked or confirmed, or needs a settlement notice for a counterparty.
domain: settlement
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - cashflow_id
write_actions: true
confirmation_required: true
success_criteria:
  - Cashflow counts by status are reported, and stale rows are named with why they drifted
  - Every action is reported with its cashflow id, amount and resulting status
  - No amount is invented — a needs_amount row is reported as such, never guessed
routing:
  - request: "Review settlement cashflows, release or block a payment, or issue a settlement notice"
    persona: trader
---

## When to use

User asks what the desk owes or is owed, wants a payment released, blocked or
confirmed as paid, or needs a settlement notice.

## Required inputs

`portfolio_id`. Optionally a `cashflow_id`.

## States

`needs_amount` → `pending` → `released` (recallable) → `settled` (terminal).
`blocked` stops any of the first three; `void` is terminal.

## Procedure

1. `get_settlement_summary(portfolio_id)` — counts by status, stale count.
2. If empty, `generate_settlement_cashflows(portfolio_id)`. INSERT-only and
   idempotent; it never rewrites an existing amount.
3. `get_settlement_cashflows(portfolio_id, status=...)`. Every row carries
   `row_version`, required by every mutation.
4. `needs_amount`: never invent a number. A terminating event creates the row
   and a later `settle` fills it, so re-running step 2 may resolve it. Else ask
   the desk, then `update_settlement_cashflow(...)`.
5. Resolve `stale` rows BEFORE releasing: read `stale_reason`, then
   `resync_settlement_cashflow`, or `void_settlement_cashflow` if the source
   event was cancelled.
6. `release_settlement_cashflow(cashflow_id, expected_row_version)`.
7. `generate_settlement_notice(cashflow_id)` — needs a counterparty and amount.
8. `settle_settlement_cashflow` ONLY when the desk confirms money moved.
   Terminal and unrecallable; never use it to tidy a queue.

## Rules

- On `error: conflict`, re-read and retry with the current `row_version`.
- Blocking is always allowed, including from `released`.
- Name every cashflow id and amount in the reply.

## Example

> "What are we settling on the AAPL book, and release anything clean."

Summary shows 4 pending, 1 needs_amount, 1 stale. The stale row's
`stale_reason` says its source event was cancelled → `void`. The
`needs_amount` row is reported, not guessed. The 4 clean rows are released
with their `row_version`. Reply names each id, amount and new status.

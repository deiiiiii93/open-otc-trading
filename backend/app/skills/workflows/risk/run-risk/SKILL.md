---
name: run-risk
description: Propose and queue the persisted batch-pricing run (risk metrics + position valuations in one pass) when stored portfolio risk is stale or missing. Use when user asks to run, refresh, or recompute portfolio risk, when the latest persisted risk run is absent, stale, or not aligned with current positions, or when a report workflow needs a fresh audited risk run before report creation.
domain: risk
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
  - pricing_parameter_profile_choice
optional_context:
  - position_ids
  - pricing_parameter_profile_id
  - valuation_date
write_actions: true
confirmation_required: true
success_criteria:
  - risk run task is queued with portfolio id, optional position ids, and status
  - reply includes task id and how to monitor it
routing:
  - request: "Refresh persisted risk (also refreshes valuations)"
    persona: risk_manager
---

## When to use

- User asks to run, refresh, or recompute portfolio risk.
- Latest persisted risk run is absent, stale, or not aligned with current positions.
- A report workflow needs a fresh audited risk run before report creation.

## Required inputs

`portfolio_id` is required. `position_ids` is optional and limits the risk run to those positions. A pricing parameter profile choice is required before queueing: use `pricing_parameter_profile_id` when selected in context, or ask the user which profile to use. Only pass `null` after the user explicitly confirms running without a pricing profile.

## Procedure

1. Determine scope: portfolio id plus an optional explicit position id list.
2. Read position count and product mix when available from page context or `get_positions`.
3. If no pricing profile is selected or confirmed, ask the user to choose one and stop.
4. State the portfolio id, position scope, pricing profile choice, and expected queued action.
5. Call `run_batch_pricing(portfolio_id, method="summary", position_ids=<ids or null>, pricing_parameter_profile_id=<id or null>)`.
6. Return `risk_run_id`, `task_id`, `status`, position scope, and monitoring next step. The queued run also persists a fresh position valuation run — do not queue `price-portfolio` separately for the same scope.

## Stop conditions

Ask for `portfolio_id` if it is missing. Ask for the pricing profile choice if it is missing. Do not silently queue `run_batch_pricing` with `pricing_parameter_profile_id=null`. Escalate to `desk_async` when the scoped position count is large or the user requests background execution.

## Output shape

Lead with queued or blocked. Include portfolio id, position ids/count, pricing profile id, task id, status, and the next read action.

## References

- `/skills/references/pricing/engines.md`

## Example

User: Run risk for portfolio 6 with profile 3.
Assistant: Queue `run_batch_pricing` for portfolio 6 with pricing profile 3, then report task id and status.

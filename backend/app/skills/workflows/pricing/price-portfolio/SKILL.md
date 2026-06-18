---
name: price-portfolio
description: Propose and queue persisted portfolio repricing after stale or drifted positions have been identified. Use when user explicitly asks to reprice, refresh, or revalue a portfolio or selected positions, when market-data drift analysis found positions needing refresh, or when risk or reporting needs pricing inputs refreshed before continuing.
domain: pricing
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - position_ids
  - pricing_parameter_profile_id
  - stale_reason
write_actions: true
confirmation_required: true
success_criteria:
  - batch-pricing run is queued with task id, risk run id, and status
  - affected position count and pricing profile are explicit
routing:
  - request: "Reprice a portfolio (trader lens — pricing freshness)"
    persona: trader
---

## When to use

- User explicitly asks to reprice a portfolio or selected positions.
- Market-data drift or stale valuation analysis found positions needing refresh.
- Risk or reporting workflow needs pricing inputs refreshed before continuing.

## Required inputs

`portfolio_id` is required. Use selected `pricing_parameter_profile_id` from context when present; ask once if it is missing and the user did not say to run without one.

## Procedure

1. Count affected positions from `position_ids` or a `position-snapshot` result.
2. State the cost preview, pricing profile, and stale reason.
3. After confirmation, call `run_batch_pricing(portfolio_id, position_ids=<optional>, pricing_parameter_profile_id=<id or null>)`.
4. Return task id, risk run id, queued status, and the monitor path. One queued run persists BOTH the new valuation run and fresh risk metrics — do not also queue `run-risk` for the same scope.
5. After completion, read results with `get_latest_position_valuations` (and `get_latest_risk_run` if risk was also requested).

## Stop conditions

Do not run blanket repricing when the user only asked for stored values or freshness. Escalate to `desk_async` for large books or expected runtime above the async threshold.

## Output shape

Lead with queued or blocked. Include portfolio id, affected count, pricing profile id, task id, risk run id, status, and the monitor path (`/api/tasks/{task_id}` or the Tasks page).

## References

- `/skills/references/pricing/engines.md`

## Example

User: Reprice stale positions in portfolio 6 with profile 3.
Assistant: Preview affected count and cost, then call `run_batch_pricing` after confirmation and report task id, risk run id, and queued status.

---
name: monitor-limits
description: Read risk-limit monitoring state for a portfolio, find breached evaluations, and queue a fresh monitoring run that reuses the latest completed risk run's evidence. Use when the user asks about limit status, breaches, utilization, or headroom, or wants limits re-checked after the book or risk changed.
domain: limits
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - monitoring_run_id
write_actions: true
confirmation_required: true
success_criteria:
  - The latest monitoring run's evaluations are reported with limit key, status, observed value, and utilization
  - A queued re-check returns a run id, or the blocker (no fresh completed risk run) is stated
routing:
  - request: "Check risk limit status, breaches, or headroom for a portfolio"
    persona: risk_manager
---

## When to use

- User asks whether a book is inside its risk limits, what breached, or by how much.
- A limit alert or incident exists and the current evaluation state is needed.
- Limits should be re-checked after the book, hedges, or risk evidence changed.

## Required inputs

`portfolio_id`. Optionally a specific `monitoring_run_id` (default: latest run).

## Procedure

1. `get_limit_monitoring_run` (latest for the portfolio) and `list_limit_incidents`.
   Report each evaluation's limit key, status, observed value, utilization, and
   headroom; name the breached ones explicitly. `list_risk_limits` shows the
   governing boundaries when needed.
2. To re-check limits: monitoring REUSES the latest completed risk run's
   evidence, so first refresh risk via `run_batch_pricing` if the book or market
   changed, wait for it to complete, then call `run_limit_monitoring`
   (HITL write).
3. Read the completed run back with `get_limit_monitoring_run` by the returned
   run id and report the new evaluation statuses.

## Stop conditions

- `error=LimitValidationError` — no completed risk run: run risk first, then retry.
- `error=LimitConflictError` — a monitoring run is already active for this
  portfolio: read it instead of queueing another.
- Do not claim a limit recovered without a completed re-run showing `ok`.

## Output shape

Per-limit table (key, status, observed, utilization, headroom) + run id and
valuation time; for re-checks, the queued run id then the verified statuses.

## References

- `/skills/workflows/limits/handle-limit-incident/SKILL.md`
- `/skills/workflows/risk/run-risk/SKILL.md`

## Example

User: Are we inside limits on the desk book after this morning's hedge?
Assistant: Read the latest monitoring run (breach on the net-delta cap), refresh
risk with `run_batch_pricing`, queue `run_limit_monitoring`, then report the new
evaluations all `ok` with the updated net delta.

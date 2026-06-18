---
name: read-risk-result
description: Read the latest persisted portfolio risk result and explain freshness and totals. Use when user asks what the latest risk says for a portfolio, when a page-scoped risk question can be answered from loaded context or stored risk, or when another workflow needs a risk freshness check before taking action.
domain: risk
workflow_type: read
allowed_envelopes:
  - pet_page
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - risk_run_id
write_actions: false
confirmation_required: false
success_criteria:
  - latest risk run is found or absence is stated
  - reply includes status, created time, and key totals when present
---

## When to use

- User asks what the latest risk says for a portfolio.
- A page-scoped risk question can be answered from loaded context or stored risk.
- Another workflow needs a risk freshness check before taking action.

## Required inputs

Use `portfolio_id` from page context, entity ids, or explicit user text. Prefer loaded page snapshot when it already contains the latest risk run.

## Procedure

1. If loaded context has current risk totals, answer from it.
2. Otherwise call `get_latest_risk_run(portfolio_id)`.
3. Extract run id, status, created time, and totals from `metrics`.
4. State whether no completed stored run exists.

## Stop conditions

Ask once when `portfolio_id` is missing. Escalate to `desk_workflow` only if the user asks to queue a fresh run.

## Output shape

Return a compact freshness line followed by delta, gamma, vega, theta, and contributing position count when available.

## References

- `/skills/references/pricing/engines.md`

## Example

User: What is the latest risk for this portfolio?
Assistant: Read latest risk for the selected portfolio and summarize status, timestamp, totals, and missing-run state.

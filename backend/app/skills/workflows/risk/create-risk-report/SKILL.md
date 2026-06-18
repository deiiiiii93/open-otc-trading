---
name: create-risk-report
description: Create an in-thread risk report artifact after checking risk-run currency and selected pricing profile. Use when user asks for a risk report, governance report, or portfolio risk artifact, when a risk page action requests report creation for a selected portfolio, or when a desk workflow needs a durable thread artifact rather than an inline risk summary.
domain: risk
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - pricing_parameter_profile_id
  - risk_run_id
write_actions: true
confirmation_required: true
success_criteria:
  - report artifact is attached to the thread
  - reply states whether a fresh risk run is needed first
routing:
  - request: "Generate a risk report end-to-end"
    persona: risk_manager
---

## When to use

- User asks for a risk report, governance report, or portfolio risk artifact.
- Risk page action requests report creation for a selected portfolio.
- Desk workflow needs a thread artifact rather than an inline risk summary.

## Required inputs

`portfolio_id` is required. Use the selected pricing profile when present so the report is auditable against the same assumptions as pricing and risk.

## Procedure

1. Apply `read-risk-result` to check whether stored risk exists and is current.
2. If risk is missing or stale, tell the user a fresh `run-risk` should happen first.
3. If risk is current or the user confirms proceeding with available data, apply `generate-report` to write the risk report artifact under `/trading_desk/reports/`.
4. Return the artifact path, report scope, risk-run freshness, and a short executive summary.

## Stop conditions

Ask for `portfolio_id` if missing. Do not silently create a report from stale risk when the user asked for current risk.

Do not call `create_report` from this workflow. It queues a legacy report job and does not produce the custom in-thread risk artifact.

## Output shape

State generated, awaiting-confirmation, or blocked first, then artifact path when available, pricing profile id, and risk-run freshness.

## References

- `/skills/references/pricing/engines.md`

## Example

User: Create a risk report for portfolio 9.
Assistant: Check latest risk, ask or proceed based on freshness, then apply `generate-report` and return the generated artifact path.

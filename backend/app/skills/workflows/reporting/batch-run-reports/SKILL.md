---
name: batch-run-reports
description: Produce an inline report batch summary without persisting report artifacts. Use when user asks for a one-shot report summary but not saved artifacts, when a risk or governance workflow needs an inline report preview before persistence, or when an existing portfolio snapshot is ready to summarize.
domain: reporting
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio
optional_context:
  - report_type
  - title
write_actions: false
confirmation_required: false
success_criteria:
  - inline report summary is returned
  - caller knows no artifact was persisted
---

## When to use

- User asks for a one-shot report summary but not saved artifacts.
- Risk or governance workflow needs an inline report preview before persistence.
- Existing portfolio snapshot is ready to summarize.

## Required inputs

Use a `PortfolioSnapshotInput` value from current context or a prior workflow. Use `report_type` when the user names one.

## Procedure

1. Compose `PortfolioSnapshotInput` with positions and selected fields.
2. Call `run_report_batch(title=<title>, report_type=<type>, portfolio=<snapshot>)`.
3. Inspect returned totals, breakdowns, and artifact hint.
4. State that the result is not persisted. If the user wants a thread artifact, proceed with `generate-report`.

## Stop conditions

Do not call `create_report` from this workflow. Ask for portfolio context when no snapshot or portfolio payload is available.

## Output shape

Return report summary, totals, anomalies, artifact hint, and a clear "not persisted" status.

## References

- `/skills/references/portfolios/model.md`

## Example

User: Give me a quick report summary without saving it.
Assistant: Call `run_report_batch`, summarize totals and anomalies, and state that no artifact was persisted.

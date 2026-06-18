---
name: display-report
description: Find and summarize persisted reports, including metadata, artifacts, and available inline summary. Use when user asks to show, review, quote, or interpret an existing report, when a high-board workflow needs persisted report evidence before a decision, or when page context points to a completed report job.
domain: reporting
workflow_type: read
allowed_envelopes:
  - pet_page
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - report_id
  - report_type
  - date_range
write_actions: false
confirmation_required: false
success_criteria:
  - target report is selected or ambiguity is surfaced
  - report summary and artifact paths are returned
routing:
  - request: "Review/quote from a persisted report"
    persona: high_board
---

## When to use

- User asks to show, review, quote, or interpret an existing report.
- High board workflow needs persisted report evidence before a decision.
- Page context points to a completed report job.

## Required inputs

Use `report_id` when present. Otherwise use `portfolio_id` plus optional type or date filters.

## Procedure

1. Call `list_reports(portfolio_id=<id>, report_type=<optional>)` when report id is not explicit.
2. Select latest completed report or ask when multiple reports remain ambiguous.
3. Call `get_report(report_id=<id>)`.
4. Summarize metadata, summary payload, HTML artifact path, and workbook artifact path.

## Stop conditions

Do not create a fresh report from this workflow. If existing reports cannot answer the question, recommend `generate-report` or `create-risk-report`.

## Output shape

Return report id, type, title, created time, status, summary interpretation, artifact paths, and follow-up recommendation.

## References

- `/skills/references/portfolios/model.md`

## Example

User: Show me the latest risk report for portfolio 6.
Assistant: List reports, select the latest completed one, call `get_report`, and summarize metadata plus artifact paths.

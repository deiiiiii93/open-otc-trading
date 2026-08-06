---
name: generate-templated-report
description: Generate a desk report from a stored template so every number comes from deterministic producers. Use when the user asks for a daily report, a trader/risk/board report, or a report "from the template", or when a report should be persisted and viewable on the Reports page rather than written inline.
domain: reporting
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - template_slug
  - compare_to
write_actions: true
confirmation_required: false
success_criteria:
  - a template is selected or the choice is surfaced to the user
  - the report is generated and its id returned
  - any grounding flags are reported honestly
routing:
  - request: "Generate a daily desk report from a template"
    persona: risk_manager
  - request: "Generate the trader daily book review"
    persona: trader
  - request: "Generate the board one-pager"
    persona: high_board
---

## When to use

- User asks for a daily report for a trader, risk manager, or the board.
- User asks for a report that should persist and be viewable on the Reports page.
- A workflow needs a governed report artifact grounded in the latest risk run.

## Required inputs

`portfolio_id` is required. `template_slug` selects the template; when absent, call
`list_report_templates` and pick the one matching the requesting persona, or ask if
several match.

## Procedure

1. Call `list_report_templates(persona=<persona>)` when `template_slug` is not given.
2. Select the template matching the request. Ask the user if the choice is ambiguous.
3. Call `generate_report(template_slug=<slug>, portfolio_id=<id>, compare_to=<optional>)`.
4. Report the returned `report_id`, `status`, and `section_count`.
5. If `grounding_flags` is above zero, say so plainly — it means the narrative contains
   numbers not present in the section data and the report needs review.

## Stop conditions

Do not hand-write report numbers into the reply. The report's numbers come from
deterministic producers; your reply summarises what was generated, it does not restate
the report. Do not call `create_report` — it is the legacy compatibility path.

## Output shape

State generated or blocked first, then report id, template used, section count,
and any grounding flags.

## Example

User: Give me today's risk report for portfolio 2.
Assistant: List risk_manager templates, choose `risk-manager-daily`, call
`generate_report`, then return the report id and note any grounding flags.

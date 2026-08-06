---
name: author-report-template
description: Create or edit a report template from the available data blocks. Use when the user asks for a new report format, wants to change what a report shows, asks to add or remove a section, or wants a report tailored to a desk or audience.
domain: reporting
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - template_intent
optional_context:
  - slug
  - persona
  - base_template_slug
write_actions: true
confirmation_required: true
success_criteria:
  - every declared block key exists in the block catalog
  - the template saves without validation errors
  - the user is told which blocks were chosen and why
routing:
  - request: "Create or edit a report template"
    persona: risk_manager
  - request: "Change what a report shows"
    persona: trader
---

## When to use

- User asks for a new report format or a tailored report.
- User wants a section added, removed, or reordered in an existing report.

## Required inputs

What the report should show and for whom. When editing, the `slug` to change.

## Procedure

1. Call `list_report_blocks` FIRST. You may only declare keys that appear there —
   a template naming an unknown block is rejected at save.
2. When editing, call `get_report_template(slug)` and start from its spec.
3. Draft the YAML spec: `meta` (slug, title, persona, description) and `sections`.
   Each section takes `id`, `title`, `blocks` (each `{key, render}`), and an
   optional `narrative` brief.
4. Match every `render` to the block's `shape` from step 1:
   `scalars`→`metric_row`, `scalars_with_prior`→`delta_metric_row`, `rows`→`table`,
   `series`→`bar_chart`, `items`→`callout`, `position_greeks`→`greeks_table`,
   `waterfall`→`waterfall`.
5. Call `save_report_template(slug, spec_yaml)`.
6. If `ok` is false, read `errors`, fix the spec, and retry. Never report success
   on a failed save.

## Writing narrative briefs

A `narrative` instructs whoever writes that section's prose; it is not the prose.
Say what to state, what to lead with, and what to do when a block is empty or
unavailable. Omit it for a pure-data section — a template with no narrative
generates with no model call at all.

## Stop conditions

Never invent a block key. If no block provides what the user wants, say which block
is missing. Do not delete a seeded template; create a new one.

## Output shape

State saved or rejected first, then the slug, persona, section count, and the block
keys used. On rejection, list the validation errors verbatim.

## Example

User: I want a board report that also shows what the agents did overnight.
Assistant: Call `list_report_blocks`, find `audit.write_actions_summary`, start from
`high-board-daily`, add the section, and save under a new slug.

---
name: generate-report
description: Generate a Markdown, DOCX, or HTML report artifact from the current thread context. Use when the user asks to create, draft, generate, or formalize a report and the deliverable should appear as a thread asset rather than a queued portfolio/risk report job.
domain: reporting
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - current_thread_context
optional_context:
  - report_title
  - report_format
  - report_audience
write_actions: true
confirmation_required: false
success_criteria:
  - thread context is sufficient or clarification is requested
  - report format is Markdown, DOCX, or HTML
  - report artifact is attached to the thread
routing:
  - request: "Generate a custom or formal in-thread report artifact"
    persona: high_board
---

## When to use

- User asks to create, draft, generate, or formalize a custom report.
- User wants a report based on the current conversation, prior analysis, or existing thread assets.

## Required inputs

Use recent thread messages and assets as primary context. Valid formats are `markdown`, `docx`, and `html`. If the user already specified one, use it. If not, ask the user to choose one.

## Procedure

1. Read current thread context, especially the last user/assistant turns and attached assets.
2. If the thread does not contain enough reportable content, ask for the missing subject/content and stop.
3. Determine report format. Use an explicit valid format from the user, otherwise ask them to choose Markdown, DOCX, or HTML.
4. Recall the relevant context and draft a polished report with title, summary, body, evidence/context, and limitations.
5. Call `write_report_artifact(title, format, body_markdown, body_html?, filename_stem?)`.
6. Return the artifact path first, then a short summary of what was written.

## Stop conditions

Do not call `create_report` for custom thread reports; it queues a legacy portfolio/risk report job. Do not fetch new domain data unless the user asks or the current context is insufficient.

## Output shape

Return generated or clarification-needed, artifact path when generated, format, title, and short summary.

## Example

User: Create a formal risk report for the 5 near-KO positions in the Snowballs portfolio.
Assistant: Use the previous near-KO screen from the thread, ask for Markdown/DOCX/HTML if missing, then call `write_report_artifact` and return the file path.

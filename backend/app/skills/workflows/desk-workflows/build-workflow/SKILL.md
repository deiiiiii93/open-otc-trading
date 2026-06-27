---
name: build-workflow
description: Draft and persist a reusable desk workflow as a Python script when a user wants to define, create, or edit an automated multi-step desk procedure. Use when the user asks to build a workflow, turn a sequence of desk actions into a reusable playbook, or author a slash-command workflow for the Agent Thread.
domain: desk-workflows
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - workflow_intent
optional_context:
  - persona
  - steps
write_actions: true
confirmation_required: true
success_criteria:
  - a valid Python workflow script with a meta literal is drafted
  - the script is persisted via save_desk_workflow and its slug is returned
routing:
  - request: "Create or edit a reusable desk workflow"
    persona: risk_manager
  - request: "Build a slash-command workflow playbook"
    persona: trader
---

## When to use

Use when a user wants a reusable, named workflow they can later launch with a
slash command in the Agent Thread. A workflow is a Python script that calls
`await step("<prompt>")` once per desk turn and may use `log("...")` and plain
Python control flow.

The script must define a `meta` dict literal with keys `name` (kebab slug),
`title`, `persona` (trader|risk_manager|sales|quant), `mode` (auto|yolo),
`scope` (local|shared), and optional `description`.

Interview the user for the goal, persona, default mode, and the ordered steps.
Draft the script, then call `save_desk_workflow` with the full script text. Do
not invent tools inside the script — each `step` prompt is handled by the desk
orchestrator exactly like a user message.

## Example

User: Build me a morning risk workflow for the control portfolio.
Assistant: Draft a script whose `meta` has `name: "morning-risk"` and two
`await step(...)` calls (read latest risk, then refresh), then call
`save_desk_workflow` with the script and report the slug `morning-risk`.

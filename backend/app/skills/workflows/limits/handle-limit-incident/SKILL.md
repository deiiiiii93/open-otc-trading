---
name: handle-limit-incident
description: Work a limit breach incident through its lifecycle — acknowledge, comment a root cause, waive with rationale, resolve, and verify closure from the timeline. Use when the user asks to handle, acknowledge, waive, resolve, or close out a limit incident, or to confirm what happened to one.
domain: limits
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - incident_id
write_actions: true
confirmation_required: true
success_criteria:
  - Lifecycle actions execute against the current row_version and the resulting status is reported
  - Closure claims cite the incident timeline (recovered or resolved event), never assumption
routing:
  - request: "Work a limit breach incident (acknowledge, comment, waive, or resolve)"
    persona: risk_manager
---

## When to use

- A limit incident exists and needs acknowledging, commenting, waiving,
  resolving, or a closure check.

## Required inputs

`portfolio_id`; `incident_id` when known (else find it via `list_limit_incidents`).

## Procedure

1. ALWAYS `get_limit_incident` first — every mutation requires the current
   `row_version` from that read, and each successful mutation increments it
   (re-read between successive mutations).
2. Acknowledge the breach (`acknowledge_limit_incident`), then log the
   root-cause analysis with `comment_limit_incident`.
3. Waive (`waive_limit_incident`, rationale + ISO expiry) ONLY when the desk
   accepts the breach instead of remediating it. Do not waive when remediation
   is already in flight and verifiable by a monitoring re-run.
4. Closure: a clean monitoring re-run auto-recovers the incident (status
   `recovered`, a `recovered` timeline event). `resolve_limit_incident` applies
   only to a still-ACTIVE incident; calling it on a recovered one returns a
   conflict. Verify closure by reading the timeline, not by resolving.

## Stop conditions

- `error=conflict` — the row_version is stale: re-read the incident and retry
  with the current one.
- Incident already `recovered` or `resolved` — report its state; no action.

## Output shape

Incident id, limit key, status transition performed, current row_version, and
the latest timeline events.

## References

- `/skills/workflows/limits/monitor-limits/SKILL.md`

## Example

User: Acknowledge the delta-cap breach and note that the driver is the AAPL call book.
Assistant: Read the incident (row_version 1), acknowledge with that version,
re-read (row_version 2), comment the root cause, and report status
`acknowledged` with both events on the timeline.

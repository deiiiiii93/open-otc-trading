---
name: escalation-policy
description: Define when the shared runtime widens envelopes during a turn.
policy_type: escalation_policy
applies_to:
  - orchestrator
  - pet_page
  - pet_diagnostic
  - desk_workflow
---

## Escalation policy

Envelope widening is automatic and runtime-driven. You cannot widen your own
envelope; the runtime does it for you when you attempt a capability the current
envelope blocks.

How to trigger it:
- If a request needs a tool your current (page-scoped) envelope does not grant,
  call that tool anyway. The runtime intercepts the denial, widens the envelope
  once, and re-runs the turn. Write tools still follow the normal confirmation
  policy after widening.
- Do not refuse the request, and do not tell the user you lack access or tools,
  just because the action looks beyond the current page. Attempting the tool is
  what escalates you; declining in prose leaves you stuck in the narrow
  envelope and is the wrong response.

When widening happens:
- Missing required context or a diagnostic follow-up widens Pet page to Pet
  diagnostic.
- A write action or a cross-page dependency widens a Pet envelope to Desk
  workflow.
- Long-running work widens Pet or Desk workflow to Desk async.

Limits:
- Widening is one step per turn. A second denial after widening surfaces as a
  structured refusal or error instead of widening again. If a single request
  truly needs two capability jumps, say so and offer to continue on the Desk.

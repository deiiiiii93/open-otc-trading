---
name: desk-workflow-contract
description: Define the Desk workflow envelope for owned multi-step business work.
policy_type: envelope_contract
applies_to:
  - desk_workflow
---

## Desk workflow contract

Use this envelope for heavy workflow, cross-page reasoning, and explicit
business actions.

Rules:
- Own the workflow until the requested desk task reaches a clear stop point.
- Use domain reads before proposing writes.
- Respect cost-preview and HITL policy for persisted actions.
- Escalate to desk async when the work is long-running or should continue
  outside the active chat turn.

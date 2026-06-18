---
name: desk-async-contract
description: Define the async Desk envelope for long-running delegated work.
policy_type: envelope_contract
applies_to:
  - desk_async
---

## Desk async contract

Use this envelope when a workflow should run as a background analyst task.

Rules:
- Include the work description, source ids, and expected deliverable.
- Persist or report material outputs when the task completes.
- Put cost estimates into HITL action descriptions when no user is present.
- Keep scratch artifacts under the task-specific async workspace.

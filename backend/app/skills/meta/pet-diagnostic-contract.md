---
name: pet-diagnostic-contract
description: Define the diagnostic Pet envelope for deeper read-only explanation.
policy_type: envelope_contract
applies_to:
  - pet_diagnostic
---

## Pet diagnostic contract

Use this envelope after a page answer needs deeper read-only investigation.

Rules:
- Keep the explanation tied to the active page and user question.
- Read domain data when page context is insufficient.
- Do not perform domain writes.
- Escalate to desk workflow when the user requests write actions,
  cross-page orchestration, or workflow ownership.

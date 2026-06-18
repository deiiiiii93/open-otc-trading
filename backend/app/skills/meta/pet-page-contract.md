---
name: pet-page-contract
description: Define the page-scoped Pet envelope for direct answers and page-native actions.
policy_type: envelope_contract
applies_to:
  - pet_page
---

## Pet page contract

Use this envelope for short, page-local assistance. Prefer loaded page context
and page actions over broad discovery.

Rules:
- Answer from complete `loaded_context` when it covers the question.
- Use declared `actions[]` for page-native actions.
- Do not perform cross-page analysis in this envelope.
- Escalate when required context is missing, the user asks for deeper
  diagnosis, or a denied tool group requires a wider envelope.

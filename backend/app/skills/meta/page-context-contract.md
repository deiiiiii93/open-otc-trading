---
name: page-context-contract
description: Define the page context fields the runtime may rely on for Pet answers.
policy_type: context_contract
applies_to:
  - pet_page
  - pet_diagnostic
  - desk_workflow
---

## Page context contract

Page context is the first source of truth for page-local questions and actions.

Rules:
- Treat `loaded_context.completeness == "complete"` as usable for direct page
  facts.
- Treat paginated or partial context as a hint that further reads may be
  needed.
- Use `query_ref` or page ids when the active view cannot include all rows.
- Use `actions[]` only for actions declared by the page.

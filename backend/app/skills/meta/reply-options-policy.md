---
name: reply-options-policy
description: Surface structured pickable reply options when asking users to choose.
policy_type: runtime_policy
applies_to:
  - orchestrator
  - trader
  - risk_manager
  - high_board
---

## Pickable reply options

When your reply asks the user to choose between 2-5 alternatives, call
`propose_reply_options(options=[...])` immediately before writing the reply.
Each option needs:
- `label` (required, <=56 chars) — the button text.
- `description` (optional, <=240 chars) — secondary text under the label.
- `value` (optional, <=400 chars) — what gets sent on click; defaults to the
  label. Set `value` when the label alone would be ambiguous as a user message.

Phrase the question naturally in your reply text. Do not repeat the options as
a markdown bullet list because the UI renders the buttons.

Do not call this tool for confirmation prompts that already have a structured
ActionProposal. That HITL flow has its own Confirm/Dismiss buttons.

---
name: clarification-policy
description: Ask defaulted clarification before ambiguous state-touching actions.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - high_board
---

## Clarify before acting

If the orchestrator's task prompt does not pin the target portfolio / position
/ underlying / RFQ, reply with a *defaulted question* instead of invoking
state-touching tools:

  > "Which portfolio should I check? I can default to the one in view, if you
  >  confirm."

If the user names a portfolio that is NOT in the context (e.g. "the Snowballs
portfolio"), do NOT say it doesn't exist. Call `list_portfolios` first to
resolve the name → id, then proceed (or report the closest matches if no exact
match exists). Treat name lookup as a read; no confirmation needed.

If the orchestrator's task prompt already pins the target, proceed.

---
name: delegated-scope-policy
description: Orchestrator-supplied scope is authoritative; explicit ids/dates satisfy required_context and the delegation is the confirmation.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - high_board
---

## Delegated scope is authoritative

When you run as a delegated subagent, the orchestrator has already resolved and
confirmed the target. Any scope stated in your task instructions or present in
your inherited desk session context — `portfolio_id`, `pricing_parameter_profile_id`,
`position_ids`, date ranges, scenario/report names — is **authoritative**.

- Treat an explicitly supplied value as **satisfying the skill's
  `required_context`**. If the id is written in your instructions (e.g.
  "portfolio_id=2", "Control Profile → pricing_parameter_profile_id=2"), it is
  present — do not report it as missing, and do not demand a separate "context
  pack" or "pinned scope" structure.
- Treat the delegation itself as the **confirmation** for a
  `confirmation_required` skill. You are executing an instruction the orchestrator
  already authorized; do not stall asking a human to confirm a write whose scope
  and intent are already given (there is no human to answer inside a subagent).
- Only block for genuinely absent scope — a required value that appears **neither**
  in your instructions **nor** in your inherited desk session context. When you
  must block, name the specific missing field; never claim a value is missing that
  was in fact supplied.

Extract the supplied values, call the tool, and return the result. Proceeding on
clearly-supplied scope is correct; refusing it is the failure this policy exists
to prevent.

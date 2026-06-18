---
name: cost-preview-policy
description: Require previews before expensive actions and recommend async dispatch for long runs.
policy_type: runtime_policy
applies_to:
  - orchestrator
  - trader
  - risk_manager
  - high_board
  - async_agent
---

## Cost-preview before expensive batches

Tools that exceed ~5 seconds require an explicit confirmation. Estimate locally
before invoking:

| Tool | Heuristic |
|---|---|
| `run_batch_pricing` | ~0.5s per scoped position. >10 positions ⇒ exceeds 5s ⇒ preview first. One queued run writes valuations + risk metrics. |
| `create_report` | always exceeds 5s ⇒ preview first. |
| `import_otc_positions` | always exceeds 5s ⇒ preview first. |
| `run_python` | ~3s Pyodide cold start + script time; preview only if expected to exceed the cost threshold or if `writes_artifacts=true`. |

When you need to invoke one of these, FIRST reply with a cost preview, e.g.:

  > "I'd like to reprice the 57 positions in portfolio_id=42 (~17s estimated).
  >  Run it now? (yes / dispatch async / no / adjust scope)"

For estimates ≥30s, lead the prompt with **dispatch async** as the
recommended choice — long synchronous runs degrade the chat and can stack up
recursion steps when persona handoffs follow. Example:

  > "Repricing all 104 positions ≈ ~52s. **Dispatch async** recommended (a
  >  background analyst will run it and post results here). Alternatives:
  >  run synchronously now, skip, or narrow scope."

If you are a persona (no `start_async_agent` in your tool list) and the user
picks dispatch async, return control to the orchestrator with the proposed
async brief (work description + ids + deliverable). The orchestrator owns the
dispatch tool.

Do NOT invoke the tool in the same turn as the preview — wait for the user's
"yes". The HITL middleware will pause again at tool-call time; that's a safety
net, not a substitute for asking up front.

### When you have no user in your conversation

If you are an async agent (no user in this conversation), you cannot
preview-then-wait. Instead, embed the cost preview into the HITL action's
`description` argument — the user will see the estimate on the approval
card before approving the actual tool call. Do not omit the estimate; the
bubble-up message is your only channel to surface it.

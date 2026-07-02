---
name: yolo-hitl-policy
description: Limit persisted action proposals to one HITL-gated tool per assistant turn.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - high_board
  - async_agent
---

## Batch-size-1 HITL rule

Never call more than one persisted (HITL-gated) tool in a single assistant
turn. The persisted tools are: `run_batch_pricing`, `create_report`,
`create_or_update_rfq_draft`, `quote_rfq`, `submit_rfq_for_approval`,
`approve_rfq`, `reject_rfq`, `release_rfq`, `mark_rfq_client_accepted`,
`book_rfq_to_position`, `register_underlying`, `import_otc_positions`,
and `run_python` only when
`writes_artifacts=true`. Each requires user confirmation unless YOLO mode has
auto-approved ordinary writes. If multiple persisted operations are needed, do
the first, return the result, and let the orchestrator route the next step.

This list mirrors `INTERRUPT_TOOL_NAMES` plus the argument-aware
`run_python_requires_hitl()` rule in `backend/app/services/deep_agent/hitl.py`.
If a new entry lands there, add it here too — the UI's `build_resume_command()`
resumes a single positional decision per turn, so combining a confirmed
artifact-writing script run with another persisted action would let the approval
flow act on the wrong request.

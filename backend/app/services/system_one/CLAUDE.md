# System One (TypeSafe Jev) — agent guidance

Part of [Open OTC Trading](../../../../CLAUDE.md). Spec:
`docs/superpowers/specs/2026-09-21-jev-system-one-design.md`.

Jev is a **non-generative** classifier: `state` + typed questions in, calibrated
probabilities out (`noul` yes/no, `choice` one-of-N, `score` ordered levels). It
cannot generate text, call tools or see images — never a persona model, never an
arena contestant, never a source of numbers.

## Reaching it

- `POST https://zenmux.ai/api/v1/systemone`, `Bearer $ZENMUX_API_KEY`,
  `"model": "typesafe/jev-1.13"`. **Absent from `/v1/models`**, and it 404s on
  chat/completions, responses and messages. To tell "exists" from "doesn't",
  compare error TYPES against a fake-slug control: a fake slug is
  `invalid_model`, the real one on a wrong endpoint is `model_not_supported`.
- A malformed body returns an opaque `400 invalid_params "Server encountered an
  unexpected error"` — that is why `validate_questions` exists.
- It is NOT a channel-registry model (D7): listing it would make it selectable
  as an agent model, and broken.

## Measured, not claimed

- Latency: **1.03 / 1.33 / 2.30 s** min/median/max over 61 calls (vendor: 70–500 ms).
  ~1 s of that is the ZenMux round-trip.
- **Not deterministic**: identical requests drift up to 0.10 (typically ≤ 0.04),
  quantised to 0.01. Anything that must be replayable (the guard's interrupt set)
  reads a COMMITTED verdict, never a fresh answer.
- **Policy goes IN the question.** A generic "is this risky / did the user ask
  for this?" predicate FAILED the ops-settlement-day step-8 trap (0.49–0.52 vs a
  correct implied step at 0.61–0.66). A policy-specific predicate separated them
  (+0.61) — post-hoc. Jev evaluates a crisply stated predicate; it does not
  supply desk judgment.

## Rules

- `client.ask()` is the **only** exit. It sanitizes `state` (never `questions`),
  budgets it by `len()` of the one serialization it sends, and maps every failure
  to one of `UNAVAILABLE_REASONS`. It never truncates and never reads the master
  switch.
- **Disabled ≠ broken (D17).** Switch off ⇒ no call, no row, field `NULL`.
  Switch on but unreachable ⇒ an explicit `unscored` record with the reason.
- Tests: conftest hard-sets `OPEN_OTC_SYSTEM_ONE=false` and replaces
  `client._default_post` with a `pytest.fail`. Inject `post=` or patch
  `_default_post` with a fake from `tests/_system_one_fakes.py`.

## The AUTO tool guard (`deep_agent/tool_guard*.py`)

- Scope: the nine destroy-and-terminate `"write"` tools in
  `tool_guard_policy.GUARD_POLICY` — desk policy, edited as data.
  `validate_policy` fails the agent build on a dead, non-`"write"`, duplicate or
  out-of-range entry.
- Registered iff `yolo_mode and allow_reply_options` (AUTO) in all FOUR stacks
  (orchestrator, each persona, the `general-purpose` override, the async agent),
  plus a runtime belt on `AUDIT_CONTEXT_KEY['mode'] == "auto"` — the default
  orchestrator graph is built once and reused across turns.
- **Determinism (D10).** A verdict is committed under UNIQUE
  `(thread_id, tool_call_id)` before any interrupt and read back on re-entry;
  a stored row is reused only if `tool_name` and `args_hash` match
  (`tool_call_id_collision` otherwise). Empty ids are never sent to Jev.
- **Every HITL resume path must stamp `mode` and `thread_id`** into the audit
  context (`agents._resume_audit_mode`; pinned by
  `tests/test_audit_context_stamping.py`). A resume without `mode` fails the
  belt, the re-run skips `interrupt()`, the decision is never consumed — and a
  call the human rejected runs.
- The user's words come from the DB (`user_message_id`, stamped by both streaming
  builds; else the thread's latest user message) — never from a persona's first
  human message, which is the orchestrator's paraphrase (D11).
- Shadow data: `GET /api/audit/guard-verdicts[/summary]`. `flagged_then_ok` is
  the candidate-false-positive count. Do not read early numbers as validation of
  the wording — the only evidence is post-hoc.

### Enforce

- `OPEN_OTC_TOOL_GUARD=enforce`: `flagged` and every `unscored` reason take the
  normal approval card (D8) — AUTO degrades to interactive for the nine tools; it
  stops nothing. The one refusal: if any guarded call's verdict cannot be
  committed, the pass raises NO interrupt and refuses every guarded call that is
  not a committed `clear` (calls stay on the AIMessage, answered by error
  ToolMessages).
- **Before enabling it:** `build_resume_command` sends ONE decision, so a card
  holding ≥ 2 guarded calls from one AIMessage cannot be resumed (the same
  pre-existing limit every HITL middleware here has).

## Memory keep-alive (`deep_agent/memory/keep_alive.py`)

- One `score` question, four situation levels; `keep_alive_score` =
  `answer.normalized`. DISPLAY-ONLY (D3): pinned by tests that eviction and
  injection order do not move.
- **Every keep-alive write sets `updated_at = updated_at`.** `MemoryEntry.updated_at`
  has `onupdate=utcnow` and `load_injectable` orders by it — a plain UPDATE would
  silently reorder injection.
- Runs on the memory writer only: after a committed job (not while draining at
  shutdown) and on each sweep tick. Outage reasons end the batch; row reasons
  do not. Content edits and sibling adds null all five columns.
- Live iff `OPEN_OTC_SYSTEM_ONE` AND `OPEN_OTC_MEMORY` AND
  `OPEN_OTC_MEMORY_KEEP_ALIVE`.

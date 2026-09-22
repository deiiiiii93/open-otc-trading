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

## Confirmation family cross-check (`confirmations/family_check.py`)

- One `choice` over `sorted(_SCHEMA_FAMILIES) + ["unknown"]` from the segment's
  TEXT LAYER; any scan page in the segment ⇒ `unscored:no_text_layer` (Jev has no
  vision). `"unknown"` is reserved.
- A flag, never a gate: `validation_status`, `validation_errors` and bookability
  are untouched. Surfaces: the trade row badge, the tool payload, and the
  `book_extracted_trade` approval card.
- Off on arena turns (the server-stamped `CONFIRMATION_EXTRACTOR_SELECTION_KEY`
  marks them); a check failure can never fail a document.

## Limit incident review (`limits/review.py`, `limits/review_checks.py`)

- Spec: `docs/superpowers/specs/2026-09-21-limit-incident-review-design.md`. Three reads
  of incident TEXT: a waiver rationale's 5-level grade (`score`), six claims (`noul`, one
  each — a rationale often makes two), `authority_only` (`noul`), and a comment
  thread's state (`choice`). DISPLAY-ONLY; invisible to the agent tools (pinned).
- **Built from the EVENT, never the incident's columns.** A re-waive overwrites
  `waiver_rationale`; the review scores the `waived` event's payload, and severity /
  utilization come from the latest evaluation-stamped event at or before it — not
  `last_evaluation_id`, which keeps moving after the waiver.
- **Due is a database fact**: a `waived` event, or an incident's LATEST `commented`
  event, with no row or an `unscored` row. The post-commit `enqueue` is only the fast
  path; `sweep()` after every monitoring run is the guarantee. There is no `pending`
  state to go stale.
- **Insert-or-select on UNIQUE (event_id, kind).** A `scored` row is never overwritten.
  Checks (`claims_json`) are recomputed each sweep for the incident's current waiver;
  Jev answers (`answers_json`) never are. `review_chip_min_p` is therefore a re-render,
  not a re-score — the API serves it as `chip_min_p` so the UI never hardcodes it.
- **Checks never say "contradicted."** `supported` / `no_evidence` / `unverified` only.
  Scope membership goes through `limits/scopes.py::scope_matches` — the same rule
  monitoring and sources use — and expiry through `option_core_terms.expiry_date`.
- **Arena threads are never scored; "cannot tell" means skip.** `thread_access.
  thread_is_arena()` is tri-state; this caller treats `None` as skip (the memory queue
  treats it as proceed). A skipped event is still due, so it is retried. Test fixtures
  that name a `thread_id` with no `AgentThread` row are therefore skipped — use a
  thread-less (REST-style) context or create the thread.
- **Dates in fixtures:** `incidents.waive` checks `expires_at` against the REAL clock,
  so a test whose incident lives in 2026-07 must pass a wall-clock-relative expiry.
- Outage reasons (`no_key`, `timeout`, `http_error`) end a sweep batch; `bad_response`,
  `state_too_large`, `low_confidence` are row facts and the batch continues. Any other
  exception ⇒ `unscored:internal_error`; a waive, a comment or a monitoring run can
  never fail because of a review.
- Every question is `untested`. `scripts/limit_review_probe.py` scores the arena
  corpus (split by the step-5 outcome) and `scripts/fixtures/limit_review_rationales.json`;
  its output is direction, never a rate.
- **Probe run 2026-09-22 (hand-written fixture, not desk text — so still `untested`):**
  the arena corpus holds 98 `risk-limit-breach-day` text rows, ALL comments (92 from
  matches that held the waiver, 6 unknown) and **zero `waive_limit_incident` calls** —
  the labelled-waiver corpus the spec hoped for is empty. Fixture went 11/15 → 14/15
  over two wording passes: `client_flow_expected` had said "client trade or unwind" and
  swallowed the desk's own unwinds (now "FROM A CLIENT" vs `hedge_in_progress` "on its
  own initiative"), and the level-3/4 date clause was unanswerable until the state
  carried `waiver.written_on` / `waiver.expires_on` — `duration_days` alone cannot be
  compared with "by 2026-10-02". The one remaining miss is the spec's named risk: the
  terse desk line "rolling Dec CSI500, done Fri — LW" grades level 3, not 4.

## Retrospective sweep (`deep_agent/tool_guard_sweep.py`, `tool_guard_records.py`, `scripts/guard_sweep.py`)

- Spec: `docs/superpowers/specs/2026-09-22-guard-sweep-design.md`; planning findings F1–F9
  in `docs/superpowers/plans/2026-09-22-guard-sweep.md`. The guard's predicates over
  EXECUTED calls: `GUARD_POLICY` plus the candidate `SWEEP_POLICY`, which the live guard
  never reads — promotion is an edit to `GUARD_POLICY` that cites a sweep run.
- **D3 invariant:** a sweep row exists only for a call with a terminal (`ok`/`error`/
  `denied`) `execution` audit row. The live guard scores BEFORE a call runs, so it can
  never meet one; `due_rows` and `score_audit_row` both enforce it.
- **One assembler.** `build_guard_state` is `window_from_messages` + `assemble_guard_state`;
  the sweep builds its `TurnWindow` from records and calls the same assembler. A sweep
  state that drifts from the live one says nothing about the guard — pinned by the
  orchestrator and persona equivalence tests.
- **Timestamps:** audit `occurred_at` is naive `YYYY-MM-DD HH:MM:SS`, trace `start_time`
  is ISO with `T` and `+00:00`, and a string comparison returns nothing. `parse_utc`
  everything. `occurred_at` also PRECEDES the call's own span by ~1 ms — find the span
  by `extra.tool_call_id`, never by time.
- **Scope is structural.** Audit `persona` is NULL on ~99% of rows. The call's agent is
  its span's `lc_agent_name`; its task is the `task` span whose `dotted_order` prefixes
  the call's; its window is that scope's tool spans that started before the LLM span
  which emitted the call (the live guard never sees a sibling persona's calls, nor the
  other calls in its own pending AIMessage). No trace, or no own span ⇒ `audit_only`,
  never pooled with `trace`.
- **A persona scope is keyed by its `task` call id, never its `dotted_order` alone.** A
  HITL resume starts a NEW trace root and re-enters the same `task` call under it: the
  dotted_order changes, the tool_call_id does not. Scoping by prefix gave every resumed
  call an empty window while still stamping `trace` fidelity — 8 desk rows of the first
  evidence run, the daemon smoke's `book_position` flag among them (0.82 on the empty
  window, 0.63 on the real one). Arena rows cannot hit it: `yolo` never pauses.
- **An outage writes nothing** (`no_key`, `timeout`, `http_error`): the call stays due and
  the pass ends. Row facts (`bad_response`, `state_too_large`, `no_user_request`) stamp an
  `unscored` sweep row. Any other exception propagates — a bug must not stamp rows.
- **`source=live` is the summary default**: `flagged_then_ok` must keep meaning "the LIVE
  guard flagged and the call ran". The verdict list defaults to `all`.
- **Daemon:** desk threads only (thread `source` not `arena`/`smoke`), 7-day lookback,
  ≤ 20 calls per hourly pass; live iff `OPEN_OTC_SYSTEM_ONE` AND `OPEN_OTC_GUARD_SWEEP`.
- **Held-out (D10):** commit `cases.json` BEFORE `score`; `score` refuses a moved policy
  hash (exit 2). `tested-heldout` needs the wording's last edit AFTER the cases were
  locked and no edit after the report — no 2026-09-21 wording can get it from a later run.
- **Labels are transcript-joined:** every arena match transcript lists each step's call
  ids. Today's definition + the scorer's own `evaluate_assertion` decide `trap` /
  `expected`; a transcript of another manifest era is `no_match`. Labels live in
  `cases.json`, never on verdict rows.
- **A transcript keys a FAILED call by its span id, not its `tool_call_id`.**
  `trace_harvest` records `tcid or span id`, and a tool that raised leaves no ToolMessage
  to parse a `tcid` from. A join on `tool_call_id` alone turned every failed call into
  `no_match` — 87 arena rows, mostly `expected` `book_position`/`quote_rfq`, so the
  `expected` buckets were skewed toward calls that succeeded. `arena_labels` retries a
  miss through the row's own trace span. The first evidence run
  (`docs/arena/evidence/2026-09-22-guard-sweep/`) predates the fix and says so.
- **`no_match` is mostly lost evidence.** Purged run directories, matches that never
  wrote a transcript, threads with no `arena_run_id`, and trial files a re-run of the
  same match overwrote. Diagnose a large `no_match` before trusting the labelled rest.

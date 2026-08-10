# Open OTC Trading — agent guidance

Orientation for anyone (human or agent) working in this repo. The frontend has its
own guide at [`frontend/CLAUDE.md`](frontend/CLAUDE.md) — **read it before any UI
work** (token-only styling is non-negotiable there).

- **Backend** — FastAPI + Uvicorn, LangGraph agents, SQLAlchemy models, Alembic
  migrations. Pricing/risk math is delegated to **QuantArk** (deterministic quant
  engine) — numbers never come from an LLM. Tests: `.venv/bin/python -m pytest`.
- **Frontend** — React 19 / Vite / TypeScript, Radix UI, "Warm Ledger" tokens.
  Tests: `cd frontend && npm test` (vitest), type-check `npx tsc --noEmit`.
- **DB** — SQLite at `data/open_otc.sqlite3`; **Alembic is the upgrade path**
  (`.venv/bin/python -m alembic upgrade head`). The live DB can lag `head`; a 500
  from a feature usually means migrations are behind, not a code bug.
- **LLM channels** — `config/agent_channels.yaml` is **gitignored** (per-env); the
  tracked template is `config/agent_channels.example.yml`. Tag/model edits must go
  to **both**.
- **Before opening a PR / merging** — update `CHANGELOG.md` (Keep a Changelog,
  under `[Unreleased]`); update `README.md` if the change is user-facing, and this
  file if it introduces a new subsystem or a gotcha future agents need. A `pre-push`
  hook (`.githooks/pre-push`, enable via `git config core.hooksPath .githooks`)
  blocks pushing backend/frontend changes without a `CHANGELOG.md` update and
  reminds (non-blocking) about `README.md`/`CLAUDE.md`.

---

## Audit trail (dangerous-action log)

An always-on, append-only record of every write-class action an LLM agent takes —
distinct from `/tracing` (which records every transcript detail, disableable).
Captures bookings, portfolio/RFQ writes, deletes, memory writes, async dispatches,
and file/artifact writes, **including actions taken in headless YOLO mode** — YOLO
only empties the HITL interrupt map, it never bypasses middleware, which is where
capture lives.

**Package:** `backend/app/services/deep_agent/write_actions.py` (shared write-class
taxonomy off `__capability_group__`, also consumed by `fanout_readonly.py`),
`audit_redaction.py` (secret-key masking + content elision to sha256/len/head),
`services/audit_trail.py` (recorder), `deep_agent/audit_trail_middleware.py`
(`AuditTrailMiddleware`). Model: `AgentActionAudit` (`models.py`). Migration `0043`.
Read-only API: `backend/app/routers/audit.py` (`/api/audit`). Frontend:
`frontend/src/routes/Audit.{tsx,live.tsx,css}` (the **Audit** nav page).

### Capture points

`AuditTrailMiddleware` sits at the `wrap_tool_call` seam, just inside
`ToolErrorBoundaryMiddleware`, in **all three** agent stacks — orchestrator
(`_agent_middleware`), per-persona (`all_personas`), and `build_async_agent`.
`tests/test_audit_registration.py` asserts middleware presence in all three so a new
stack can't silently skip it.

- **Fail-closed phase-1.** An `attempted` row must commit *before* the tool executes
  (bounded retry 0.1/0.3/0.9s); if it can't, the write is refused with a ToolMessage
  rather than executed unaudited. Phase-2 (`ok`/`denied`/`error`/`interrupted`) is a
  best-effort outcome update by in-memory PK — on failure the row honestly stays
  `attempted` rather than lying about the outcome.
- **HITL chains.** `hitl_proposal` → `hitl_decision` → `execution` rows are linked by
  a server-minted `audit_ref` (UUID), minted unconditionally inside
  `_source_meta_for_action` so it survives async resume paths. Decision rows are
  recorded at the resume boundary **before** the graph invocation, in their own
  transaction, so a failed approved-write can't erase the human approval.
- **Redaction.** Secret-key regex masking
  (`token|password|secret|api[_-]?key|credential|authorization`) plus content-body
  elision (`write_file`/`edit_file`/`run_python`/`execute` bodies →
  `{sha256, byte_len, head}`) before any row is persisted.

### Gotchas

- List sort key is `occurred_at`, not `id` — insertion order only approximates
  chronological order for organic same-process writes; a backfilled or
  out-of-order-committed row breaks that assumption.
- The Audit page pager reuses the shared `TableToolbar` / `DataTablePage`
  primitives (rows-range label + rows-per-page select + prev/next) — same control
  as Positions/Portfolios/Reports/Tasks, not a bespoke "Load more" button.
- `fail_closed_refusals.unpersisted` (surfaced in `/api/audit/summary`) is an
  in-memory counter — it resets per process.

---

## Ground-truth artifacts and compaction

Long-agent compaction may shorten reasoning, but it must never paraphrase away a
deterministic tool or DB result. `GroundTruthArtifactMiddleware`
(`services/deep_agent/ground_truth.py`) classifies evidence from server-owned
`ToolGroup` metadata and captures every such result, including small results, through
`ContentAddressedFilesystemBackend.capture_tool_result` before compaction. The CAS
stores exact bytes; `session_artifacts` stores the id, SHA-256, input hash, tool call,
and `generated_at` / `observed_at` / `data_as_of`. If capture fails, the raw message is
kept and compaction refuses to evict it.

`services/deep_agent/compaction.py` gives the summariser artifact-reference capsules,
not raw prices/Greeks/rows, then appends a server-rendered exact manifest. Never treat
the narrative summary as executable evidence. Recover canonical content through the
workflow-scoped `list_artifacts` → `inspect_artifact` → targeted `read_artifact`
sequence (`artifact_access.py`, `tools/artifacts.py`). This is deliberate progressive
disclosure: no embeddings, vector search, semantic chunking, or RAG ranking. Do not add
one to this evidence path. `read_artifact` reuses the source artifact reference (it does
not mint an artifact-of-an-artifact), so a later compaction removes the disclosed body
back to the original id/hash.

Exact `kind='tool_result'` artifacts are load-bearing CAS entries even when a later
artifact supersedes them; the normal GC sweep never evicts their bytes.

Hedging adds an action-time gate. `OPEN_OTC_HEDGE_RISK_MAX_AGE_SECONDS` defaults to
900. `book_hedge`'s HITL payload must show the source artifact id, artifact generation
time, Greeks `valuation_as_of`, `risk_generated_at`, and `expires_at`; execution also
revalidates the latest risk run, portfolio fingerprint, exact proposal fields, and
solver legs. `stale_hedge_proposal` means refresh risk and re-solve, never retry the
old approval.

### A/B evidence standard for agent-behaviour features

Do not claim that a new agent strategy outperforms its predecessor from regression tests
alone. Add a paired benchmark with the old production path as arm A and the candidate as
arm B. Both arms must receive the same input hash and model/configuration; predeclare the
scorer and pass criteria; retain per-case raw results; report regressions and costs as well
as wins; and avoid an LLM-only judge when deterministic state or trace checks exist.

The compaction reference implementation is
`services/deep_agent/compaction_benchmark.py`, exposed by
`scripts/compaction_ab_benchmark.py`. Its offline controlled-model mode is the cheap PR
gate for structural guarantees. A live-model probe may supplement that gate for prose or
planning quality, but may not replace the deterministic proof. Extend its case set when a
later compaction or hedge-evidence change adds a new claimed advantage or failure mode.

The same runner's `--live` mode is the standard model-behaviour supplement. Use the same
channel/provider/model, temperature, limits, and input hash for both arms; alternate arm
order; give each arm its production recovery path; persist full prompts/responses plus
call and evidence timestamps; and grade with deterministic domain rules. Run from a clean
`uv sync --locked` environment. The evidence must include the `uv.lock` hash, complete
installed-distribution fingerprint, and a passing installed-versus-locked version check;
dependency drift invalidates the benchmark. Never treat a live provider response alone
as proof without the paired raw artifact and scorer output.

---

## Long-term memory

A DeerFlow-inspired cross-session memory layer for the deep agent. It distills
durable facts from closed sessions and injects the relevant ones into later
conversations, so the desk "remembers" preferences, per-book context, and
corrections across threads.

**Package:** `backend/app/services/deep_agent/memory/` — `config`, `normalize`,
`safety`, `scope`, `store`, `extractor`, `runs`, `inject`, `queue`, `middleware`,
`runtime`, `window`. REST API: `backend/app/routers/memory.py` (`/api/memory`).
Frontend console: `frontend/src/routes/Memory.{tsx,live.tsx,css}` (the **Memory**
nav page). Migrations: `0038` (evolve `memory_entries` into typed columns) + `0039`.

### Scopes & lifecycle

Four scopes, resolved via a constant-`desk` identity seam (no real multi-user yet):

- `user:desk` — desk-wide operator facts and preferences.
- `book:{portfolio_id}` — per-portfolio context (`scope_id` is the **stringified
  portfolio integer id**, e.g. `"1"`).
- `domain:global` — shared knowledge, staged **propose → approve**: only `approved`
  facts inject; new domain facts land as `proposed` and must be approved first.
- `correction:desk` — facts learned from user corrections (`source_error=True`),
  with their own injection sub-budget.

### Store invariants (`store.py`)

- `MemoryStore.create` forces domain facts to `proposed`; everything else is
  `active`. **`api`-created non-domain facts are auto-pinned.**
- `pinned` is an eviction-protection flag (human/approved facts survive cap
  eviction and the extractor) — it is **not** an edit gate.
- `archived` is read-only: `update` / `set_status` / `set_pinned` raise
  `MemoryConflictError` (→ HTTP 409). `archive()` is idempotent.
- Hygiene: normalized exact-match dedup, confidence floor `0.7`, caps `100`/scope
  and `20` for corrections, content-safety denylist (see `config.DEFAULT_DENYLIST`).
  No embeddings.

### Writes are off the hot path

The agent turn only enqueues **in-memory** — no synchronous SQLite write. Durability
comes from a **reconciliation sweep** over `AgentSession` close status (session
close + a correction fast-path on `after_model`). `apply_diff` and the run-success
cursor commit in **one transaction** (idempotent re-run). Memory is a *diff*
(add/remove), not an append.

### Extractor model resolution

The extraction LLM is chosen by **registry tag**, two-tier so a missing tag
degrades to a cheap model rather than the expensive agent default
(`resolve_extractor_selection`): `extractor_model` (dedicated tag — tag exactly
**one** model with `extractor` in `agent_channels.yaml`) → `extractor_fallback_tag`
(`fast`) → registry default. Pinned extractor in this repo:
`deepseek/deepseek-v4-flash` on the **zenmux** channel.

### Configuration

| Env var | Effect |
|---|---|
| `OPEN_OTC_MEMORY` | `on` (default) / `off` — master capture switch. Even when `off`, existing facts stay editable via the API/console. |
| `OPEN_OTC_MEMORY_RECONCILE_SINCE` | ISO-8601 instant. The sweep only **discovers** sessions closed at/after it. Set when first enabling memory on an existing DB so it doesn't mass-extract the whole backlog. Malformed → fails open (no cutoff) with a warning. |

Defaults live in `MemoryConfig` (`config.py`): floor `0.7`, caps `100`/`20`,
injection budgets `2000`/`1000` tokens.

### Gotchas

- **Seeding via the API** mirrors all store policy server-side: POST a `domain`
  fact and it still lands `proposed`; POST a `book`/`user` fact and it lands
  `active` + `pinned`. Store only **durable** facts — verify volatile-looking
  values (live position counts, "latest run #N") against the DB first; they go
  stale.
- The reconciliation sweep keys off `AgentSession.closed_at`; sessions with a NULL
  `closed_at` are never swept (so historical threads may need manual seeding).
- **Memory page table layout:** the shared `Table` primitive renders each row as
  an *independent* CSS grid, so only `fr` and fixed lengths align across rows —
  `max-content`/`auto` tracks resolve per-row and break column alignment. Columns
  that must not clip (status, conf, the action buttons) use fixed widths; the rest
  use `minmax(0, fr)`. Row-action buttons are height-constrained to `--row-height`.

---

## Instant-messaging gateway (Feishu/Lark)

Drive the full desk agent over IM with web parity — streaming markdown replies,
HITL Approve/Reject cards, pickable reply-option cards, linking-code enrollment.

**Package:** `backend/app/services/gateway/` — `runtime` (single-worker DB-lease
election + heartbeat), `connectors/{base,fake,feishu}`, `dispatch` (dedup +
message/card lanes + per-chat serialization), `bridge` (threads `actor=binding.desk_user`
into the agent service), `coalescer` (StreamRenderer: streaming, approval cards,
reply-option cards, revocation, rate limit), `identity`, `actions`, `cards`,
`config`, `sse`, `types`. Endpoints: `/api/gateway/*` in `main.py`. Migration
`0037_gateway_tables`. Tests: `tests/gateway/` (run from **repo root**).

### Run model — dedicated worker, NOT `--reload`

The lark WS client (`lark_oapi.ws.Client`) owns a **module-global event loop**
and `start()` is a **blocking** call run on a daemon thread (events are marshalled
to dicts via `lark.JSON.marshal` and dispatched onto the server loop with
`run_coroutine_threadsafe`). This **does not survive uvicorn `--reload`** — a
hot-reload while the worker holds the lock wedges it. So: keep
`GATEWAY_ENABLED_CONNECTORS` **empty in `.env`** (reloading dev servers stay inert)
and enable the connector via a launch override on a stable, non-reload worker:
`GATEWAY_ENABLED_CONNECTORS=feishu uvicorn app.main:app --app-dir backend --port 8001`.
A single-row `gateway_worker_lock` lease guarantees one Feishu handler; every other
backend stays in standby (and standby workers do **not** retry — they pick up the
lock only on restart).

### lark schema-2.0 card realities (all live-only — fakes hid them)

- **Cards must be schema 2.0.** Buttons are elements inside `body.elements` — there
  is **no top-level `actions`** (that's 1.x). Buttons fire via a `behaviors`
  callback array, **not** a `value` field. The `note` element is gone — use
  `markdown`. Wrong shape → `code=230099 / 200621`.
- **Text bubbles can't render markdown**; replies are sent as a headerless
  `markdown`-element card (`_text_to_markdown_card`), and in-place streaming edits
  use `message.patch` (interactive), not `message.update` (text).
- **Outbound builders:** `receive_id_type` goes on the *request* builder, not the
  body; `PatchMessageRequestBody` has only `content`. Always check `resp.success()`
  — failures are otherwise swallowed.

### HITL & reply options ride the `done` SSE event

`agents.py::_done_payload` enriches the terminal `done` event with `thread_id` +
`pending_actions` + `reply_options` (read back from the persisted message meta) so
IM connectors can render cards — the web UI re-fetches and ignores the extras. Both
finalize paths emit it (`_finalize_turn` **and** `_finalize_workflow_stream_turn`;
`DESK_WORKFLOW` envelope uses the latter). Approval buttons carry a one-time token
(→ `resume`); reply-option buttons carry `{reply, label}` and are replayed as a
normal `message` turn that also locks their card (`InboundMessage.card_lock_ref`).

### Config & gotchas

- `GATEWAY_AGENT_MODEL` (`channel:provider:model`, e.g.
  `zenmux:openai:deepseek/deepseek-v4-flash`) selects the IM-turn model; it's a real
  `Settings` field (loads from `.env`), resolved by the bridge as explicit-arg →
  settings → process-env → registry default. `GATEWAY_WEB_BASE_URL` points card
  deep-links at the web desk (e.g. `http://localhost:5173`).
- `lark-oapi` is a hard dep in `pyproject.toml`; a stale `.venv` may lack it
  (`uv sync`). WS long-connection mode needs **no** public webhook URL.
- **`.env` leak in tests:** real `FEISHU_*` / `GATEWAY_*` values bleed into
  `Settings()` and fail the "defaults are None/empty" assertions in
  `test_config.py` / one `test_identity.py` case — validate those in a no-`.env`
  environment. All other gateway tests are connector-agnostic (FakeConnector).

---

## Dynamic subagents (governed QuickJS fan-out)

An opt-in execution substrate for **recurring desk workflows that fan out per work
item** — e.g. one read-only commentary per breached position. The orchestrator writes a
QuickJS script that calls the deepagents `task()` global (via `CodeInterpreterMiddleware`,
`subagents=True` — **not** `ptc=["task"]`, which raises at model-call time) to dispatch one
persona subagent per item, then reconciles the results deterministically. **Gated off by
default** (`OPEN_OTC_AGENT_CODE_INTERPRETER=false`).

**Package:** `backend/app/services/deep_agent/` — `dynamic_subagents.py` (allowlist +
attribution helpers + `reconcile_fanout_coverage`), `eval_gate.py`
(`EvalAttributionGateMiddleware`), `fanout_readonly.py` (`FanoutReadOnlyMiddleware`). Tool:
`backend/app/tools/assemble_breach_report.py`; scope: `services/risk_limits.py`. Seed
workflow `morning-risk-breach-commentary`; migrations `0040` (seed) + `0041` (finalize
prompt).

### Governance (all server-owned — the model can never self-authorize)

- **Eval gate.** Enabling the code interpreter exposes a general `eval` tool;
  `EvalAttributionGateMiddleware` rejects **every** `eval` unless `configurable` carries
  server-stamped Case-3 attribution (`fanout_attribution_extra`) for an **allowlisted**
  (`DYNAMIC_SUBAGENTS_ALLOWLIST`) workflow persisted with `source='seed'`. Attribution is
  threaded via `main.py::_desk_workflow_drive_factory` →
  `stream_and_persist(desk_workflow_slug/source/launch_args)` — never from model/tool input.
- **Read-only fan-out.** `FanoutReadOnlyMiddleware` blocks writes inside fanned-out
  subagents (`ls_agent_type=='subagent'` + Case-3 attr). Classification is **by capability
  group** (`__capability_group__`): block `DOMAIN_WRITE`/`PAGE_ACTION`/`ASYNC_DISPATCH` +
  deepagents FS/shell writes (`write_file`/`edit_file`/`execute`) +
  `run_python(writes_artifacts=True)`; **allow everything else**. This is
  **allow-by-default on purpose** — deny-by-default against the HITL write-map blocks the
  reads the investigator needs (incl. ungated tools like `get_position_summaries`). Why
  writes matter: resume/retry re-runs the whole `eval` and **re-dispatches every subagent**,
  so a write would repeat non-idempotently.
- **Server-authoritative coverage.** `assemble_breach_report` re-derives scope server-side
  from the launch `portfolio_id` (`configurable['fanout_launch_args']`, not the model arg)
  via `enumerate_limit_breaches`, then `reconcile_fanout_coverage` guarantees exactly one
  terminal record per scoped item (uncovered → `failed`). The model can't shrink coverage
  by omitting ids.

### Gotchas

- **A tool the model must call has to be in `DEEP_AGENT_TOOL_NAMES`** (the allowlist
  `select_deep_agent_tools()` filters `QUANT_AGENT_TOOLS` by), not merely registered in
  `QUANT_AGENT_TOOLS` — otherwise it is silently dropped from every persona's toolset.
  `assemble_breach_report` hit exactly this: registered but not allowlisted, so the model
  finalized via `write_report_artifact` instead. **When a model *never* calls a specific
  tool across models and prompts, suspect availability before capability.**
- **Stronger models loop more:** `deepseek-v4-pro` blows past the default
  `agent_recursion_limit` of 100 on the fan-out — raise it (≥300) for pro-tier runs.
- **Subagent stream events don't surface as SSE frames:** deepagents `task()` runs each
  subagent via a nested `subagent.invoke()`, so its internal events never reach the parent
  `astream_events` — inside-fan-out observability is a library limitation (follow-up).
- **The `metrics['limit_breaches']` producer is not built yet:** `enumerate_limit_breaches`
  returns `[]` (honest-empty) until a risk producer populates that key; real runs surface
  no breaches until then.
- Live smokes on the **direct** DeepSeek channel: `api.deepseek.com` exposes both
  `deepseek-v4-flash` and `deepseek-v4-pro` (the registry only declares flash).

---

## Golden workflows & arena scoring (flagship v2)

The flagship `risk-manager-control-day` is a **9-step / 39-point** discrimination
benchmark (was 7/32): grounding + adherence + synthesis checks on top of the
procedural loop. Package: `backend/app/golden_workflows/` (schema/assertions/
registry/scoring live here; arena scoring in `services/arena/scoring.py`).

### Runs management (New Run / delete / merge)

The `/arena` Runs panel launches, deletes, and merges runs (endpoints in
`routers/arena.py`, store logic in `services/arena/store.py`):

- **New Run** launches multiple workflows × models with a **trials** count
  (`arena_run.trials`, migration **0045**, default 1). `task._execute` runs each pair
  `trials` times and folds the clean trials into one aggregate match via the shared
  `scoring.fold_trial_breakdowns` kernel — the SAME `n_trials` shape and CON scheme
  `store.merge_runs` produces. **Infra trials are skipped, not retried** (the async task
  layer already reruns whole failed runs); 0 clean trials → `invalid`. **`trials=1` is
  behavior-preserving** (single-trial aggregate = today's single match, derive-on-read
  card unchanged). Jury scores (when the jury runs) roll up onto the aggregate in
  `_record_pair` so the leaderboard's advisory subjective stats survive the wrap.
- **Delete** is **hard**: `store.delete_runs` drops the run + its matches (ORM cascade,
  via an ORM `update` so the session identity map stays in sync) and nulls dangling
  `agent_threads.arena_run_id`; the **router** then removes the transcript files and the
  `arena/<run_id>` artifact dir (store stays DB-pure). Missing ids are skipped.
- **Merge** stays non-destructive (reuses `store.merge_runs`).
- The frontend runs list polls while any run is **non-terminal** — poll only on
  `queued`/`running` (the backend emits `queued`, **not** the type union's `pending`);
  `completed`/`failed` are terminal.

### Model Ability Card (Spec B, 2026-07-06)

The objective score is surfaced as a **FIFA-style 6-stat card + OVR**, derived from
the same 39-check evaluation — nothing is re-scored, no DB migration. Five OVR stats
map 1:1 to the objective axes plus a computed EFF; JDG is the advisory jury score.

- **Stats & OVR** (`scoring.card_from_axes`): `stat = round(99 × passed/total)` per
  axis (grounding→GRD, adherence→ADH, synthesis→SYN, procedural→PRC).
  **EFF is golf-scored** (spec `2026-07-11-arena-eff-golf-scoring`): full at/under `par`,
  then **linear** decay to 0 at `_EFF_ZERO_MULT × par` (2×par) — `EFF = round(C × 99 ×
  max(0, 1 − (calls−par)/((_EFF_ZERO_MULT−1)·par)))` above par — still gated by the
  correctness fraction `C` (GRD+ADH+SYN). The golf curve applies **only when `par` is
  calibrated** (`scoring.par_calibrated` = the workflow declares `par_tool_calls`);
  uncalibrated workflows keep the **legacy hyperbolic** `min(1, par/calls)` so the shared
  `card_from_axes` kernel never regresses a workflow that hasn't set a realistic par
  (`card_from_axes(..., par_calibrated=bool)`, default `False`). Being leaner than `par`
  is **not** penalized, but **zero tool calls when `par > 0` is non-execution → ratio 0 →
  EFF 0** (guard precedes the calibration branch): value-only grounding
  (`response_quotes_value`) lets a transcript quote the truth numbers without running the
  workflow, and EFF must not hand that a free efficiency pass (GRD still credits the
  numbers; PRC/EFF read 0, so the card honestly shows "strong numbers, no execution").
  `par == 0` with 0 calls is legitimately full efficiency.
  `OVR = round(0.32·GRD + 0.26·ADH + 0.16·SYN + 0.16·EFF + 0.10·PRC)`. **JDG is never
  in OVR.** `ability_card(transcript, loaded, judged)` is the write-time wrapper;
  `card_from_axes` is the shared kernel.
- **`par` = a realistic COUNTED competent run, not the theoretical minimum.**
  `scoring.designed_par(wf)` = `wf.par_tool_calls` if set else
  `sum(len(step.expected_tools))` (the fallback = theoretical min, which is why an
  uncalibrated workflow must stay on the hyperbolic curve). The flagship declares
  **`par_tool_calls: 24`** — 11 expected tool calls + ~13 legitimate counted overhead
  (re-fetching `get_*_run` results, re-listing the scenario library, sanity re-pricing).
  **par is counted against the same metric as `counts_detail.tool_calls`** — which
  EXCLUDES `META_TOOLS = {task, read_file, write_todos}` (`trace_harvest.py`), so
  **skill-file reads must never be counted into a designed par** (that would inflate the
  denominator above the measured numerator and hand free EFF credit). `par_tool_calls` is
  an **optional** manifest field (`int | None`, `≥ 1`); setting it opts a workflow into
  golf scoring.
- **Ranking** (`store.leaderboard`): by **OVR mean**, shared rank on exact ties,
  tie-break GRD→ADH→SYN→EFF→PRC (`scoring.card_tiebreak_key`). **Uncarded rows keep
  the legacy objective ranking** (mean_objective + sub-axis tie-break) and sort after
  carded rows — so an all-legacy board (runs #1–#9, no stored `axes`) does NOT collapse
  to a single shared rank. A row is carded **only when EVERY scored match is carded**
  (`carded_count == match_count`); a **partially**-carded model is treated as uncarded
  for ranking so a partial OVR sample can't outrank a fully-carded row — `carded_count`
  is surfaced per row to reveal the gap.
- **Derive on read, never migrate** (`store._derive_card(bd, workflow_id)`): the SINGLE
  stored-breakdown→card path, used by both `leaderboard` and `_match_to_dict` (via
  `_serialized_breakdown`) so board and drilldown agree. **Fail-honest** — requires
  non-empty `objective.axes` + an explicit numeric `diagnosis.counts_detail.tool_calls`
  + a loadable workflow, else `card: null` + a reason (`legacy_no_axes` /
  `missing_tool_count` / `workflow_unavailable`). Runs #10–#11 (axes present) card on
  read; runs #1–#9 (no axes, verified against the live DB) stay uncarded — never a
  fabricated par / inflated EFF. A stored write-time `card` passes through untouched.
- **`response_quotes_value`** (grounding axis) scores a **known-truth fixture value**
  (harvested per Spec A) against the response text **regardless of whether the tool
  fired that turn** — the point-2 fix: a correct-from-context answer now scores GRD
  even though the old `response_quotes_tool_value` self-grounding failed it (no
  same-step payload). Fields `value/rel_tol/scope/match/near` mirror the tool-value
  assertion; `_quote_value_in_text` is reused. Flagship steps 3/5/6 use it, keyed to
  `truth.json` values; `test_flagship_grounding_targets_match_truth_file` guards drift.
  The denominator stays 39 (1:1 assertion swap); the golden replay still earns 39/39.

### Scoring-validity audit (the per-check field tally) — 2026-07-25

Before trusting any board, ask whether the score measures the MODEL or the WORKFLOW.
The instrument is a **per-check pass-rate tally across the whole field** (walk
`objective.steps[].checks[]` + `objective.success[]` in `arena_match.score_breakdown`,
keyed by `label`). A discriminating check produces a SPREAD; **a check at 0/N or N/N
carries zero ability signal while still occupying the denominator.** The high-board
Run #58 audit found 15 of 50 checks non-discriminating; rescoring on the valid subset
moved the top from 72 → 88.6 **and reordered the board** (Spearman 0.789) — the
defects biased ranking, not just scale.

- **The golden replay CANNOT catch this.** The replay fixture is a hand-written
  perfect transcript: it satisfies each assertion by construction (it hand-provided the
  `record_answer` payloads and the exact `"Snowball"` literal), so it proves
  **satisfiability, never reachability**. It earned 50/50 while live models capped at
  35/50. Only a live board reveals an unwinnable check — same lesson as the trader-rfq
  live-reachability fix.
- **A check graded on output the prompt never requests is unwinnable.** `answer_field_*`
  requires the `record_answer(answer={...})` call **with field names spelled out in the
  `user:` turn** (the flagship does this; high-board did not, costing 5 checks). Trap
  steps must word that instruction NEUTRALLY so naming the fields doesn't leak which
  answer is correct.
- **A skill with no routing line in the persona prompt is unroutable.** Pass rate
  tracked the routing line exactly: 64–88% with, 0–23% without. Same class as
  `assemble_breach_report` — registered ≠ discoverable. **When no model ever routes to a
  skill or calls a tool, suspect discoverability before capability.**
- **Never grade an arbitrary lexical choice.** `product_type: "Snowball"` vs the stored
  `SnowballOption` are both functionally correct (the filter is substring +
  case-insensitive) but arg matching is EXACT, so all 17 models scored 0 for using the
  value the system itself reports. Use `args_any_of` for every legitimate convention.
- **Don't declare `skill_routed` for a skill already named by `expected_skill`** —
  scoring emits its own check, so the fact is scored twice (double jeopardy inflates the
  denominator AND doubles one mistake's cost). Guarded by
  `test_no_step_scores_the_same_skill_twice`. Same for repeating a per-step assertion in
  `success.assertions`, which evaluates the merged session context and so already
  implies it.

### Seed what you reference, and grade the ANSWER not the trace

- **Never pin a fixture `id` in the `pricing_profiles` namespace.** It is the one namespace
  whose purge can leave a row ALIVE: `_purge_seeded_portfolios` refuses to delete an arena
  profile a real (non-arena) run priced against and **retires** it instead (preserving that
  run's provenance), so it keeps squatting on the pinned PK. The next match's `apply_seed`
  then dies on `UNIQUE constraint failed: pricing_parameter_profiles.id` — and so does
  **every remaining match in the board** (Run #34). Portfolios are safe to pin (always
  deleted, never retired) and the flagship's `$seed.portfolios.desk.id` assertion needs the
  pin. To point at an **autoincremented** parent — a FK column, or provenance ids nested
  inside a JSON blob like a `risk_runs.metrics` payload — use a
  `$seed.<ns>.<alias>.id` token: `fixtures._resolve_inserted_ids` resolves it at any depth
  **after** insertion. The load-time `$seed` map in `load_fixtures` cannot, because it
  substitutes values **declared in the fixture file** and so only ever sees a pinned id.
  A stale reference to a purged profile is a **dangling pointer** of the same class as the
  unwritten-artifact defect below.
- **A fixture that declares an artifact must CREATE it.** `artifact_paths` is only a JSON
  column; seeding a report writes no file. The agent resolves `/artifacts/<basename>`
  against the mounted `settings.artifact_dir` (`_shaping.normalize_artifact_paths`
  flattens to the basename), so a declared-but-unwritten path is a **dangling pointer**:
  `read_file` errors and the model burns calls hunting the file, wandering into unrelated
  real rows. Put bodies in `artifact_bodies` (keyed like `artifact_paths`) and
  `fixtures._write_seeded_artifact_bodies` materializes them. **A behavioural spread
  caused by a broken fixture is not a capability signal** — high-board's step-7
  "glob-thrash", once called a discriminator, was models rationally following a pointer
  the system handed them, and it both leaked into the GRD axis and inflated `par`.
- **Neither result-selection semantics can grade "did you answer from the right row".**
  `tool_result_path` reads only the LAST matching call, so it fails a model that obtained
  the evidence and kept exploring; an any-match rule would let a model brute-force
  `get_report(1..n)` and pass a **selection** check by exhaustion. Grade it at the
  **answer** level instead (`answer_field_quotes`), keyed to a value that lives ONLY in
  the artifact body — never in `result_payload`, or `get_report` alone reveals it. Pick a
  value that cannot be computed or guessed and sits well outside `rel_tol` of every other
  graded number, so a swapped answer fails.
- **A step-scoped result check penalizes reading the evidence one step EARLY.**
  `tool_result_path` defaults to `scope: step`, so a model that fetched the value at the
  end of the previous step (genuinely fresh) and answered correctly from it scores 0 —
  the same false negative the flagship fixed for text grounding via `response_quotes_value`
  (the "point-2" correct-from-context fix). Use `scope: session` when the *evidence* is
  what matters rather than *when* it was fetched; `_last_result` still takes the LAST
  matching call across steps 0..i, so a model whose most recent read showed the wrong
  state still fails. **The assertion models do NOT set `extra="forbid"`** — before this
  field existed, a manifest's `scope: session` on a `tool_result_path` was silently
  dropped by pydantic and scored as `step`. When a manifest key seems to have no effect,
  check that the assertion model actually declares it.
- **Over-execution is ADH's and EFF's job, not GRD's.** The over-execution primitives
  (`max_calls` — "duplicate dispatch is over-execution" — plus `all_calls` /
  `exclusive_keys`) live on `tool_called`, i.e. **adherence**, and are deliberately
  opt-in; EFF penalizes volume globally via the golf curve. Letting execution style leak
  into a grounding check charges one behaviour at GRD's weight (0.32, twice EFF's 0.16),
  and — because `_correctness` gates EFF on GRD+ADH+SYN — leaks back into EFF as well.
  Worse, grounding is the FIRST objective tie-breaker (annotated "hardest to fake"), so
  contaminating it corrupts ranking, not just score.

### Arena DB hygiene: two purge scopes, two ownership proofs

Golden workflows resolve books **by name**, so leftover rows don't just accumulate —
they make each successive match's name resolution harder than the last, which biases
scores **by position in the field**. Worse, the failure is SILENT: a model that resolves
the wrong same-named book gets a plausible, well-formed number and is graded wrong with
no error anywhere.

- **`_purge_seeded_portfolios`** reclaims FIXTURE rows: arena tag **AND** a name from
  **any registered workflow's** fixtures (`list_workflow_bundles()`). Scoping it to the
  current bundle only is the bug that let trader-rfq's "Arena Trader Desk" (3 positions,
  incl. NVDA) shadow high-board's "Desk Control Book" (5 positions).
- **`_purge_match_portfolios`** reclaims MODEL-CREATED rows on trace + baseline evidence
  (`collect_portfolio_ids_created` ∩ `id > pre-match baseline`), mirroring
  `_purge_match_rfqs`. Runs in a `finally`, because a leak here is **permanent**: the
  next baseline is taken above the leaked row, so its guard can never re-catch it.
- **`ARENA_PORTFOLIO_TAG` is NOT server-owned** — a model picks its own tags via
  `create_portfolio`, and Run #58 caught two model-created views that spontaneously
  tagged themselves `arena`. So the tag alone is never sufficient ownership proof for
  deletion; pair it with a known fixture name, or use trace+baseline evidence instead.
- Both share `_delete_portfolios_with_dependents`, which sweeps dependents by
  introspecting mapped tables for `portfolio_id` / `position_id` in reverse
  FK-dependency order. **Ownership is the caller's job** — that helper re-checks nothing.

### Judge fairness & scoring methodology (2026-07-05 reform)

The score has **two axes reported separately**: a deterministic **objective** score
(rule-based assertion checks — the sole ranking axis) and an advisory **subjective**
jury score. There is **no blended total** — `scoring.total_score` is retired from
ranking; `store.leaderboard` sorts by `mean_objective`, assigns **shared ranks** on
exact ties (broken by sub-axis priority grounding→adherence→synthesis→procedural,
never by subjective), and exposes `subjective_mean/stdev/mode`.

> **Jury is opt-in, default OFF (2026-07-06, spec `2026-07-06-arena-jury-opt-in`).**
> Run #11 showed the jury too unstable to inform evaluation (it ranked models in
> reverse of the objective axis and swung on ZenMux judge reachability), so the default
> is **objective-only**. `OPEN_OTC_ARENA_JURY` (`Settings.arena_jury_enabled`, default
> `False`) gates the default jury in `task._execute`; an injected `judge_fn` still runs
> regardless (test seam). When off, a match stamps `subjective_mode="disabled"` and
> writes **no** `judge` block. Provenance values: `disabled` (opt-out) | `missing` (jury
> on, all judges failed) | `self_consistency` (degraded) | `panel`; `store.leaderboard`
> aggregates worst-visibility-wins (`missing > self_consistency > panel > disabled`) and
> **infers `panel`** for legacy pre-mode rows (score present, no mode) so old juries
> don't read as outages. The jury code, config knobs, and the 2-point rubric are all
> retained for opt-in use — nothing was deleted or migrated.

- **The judge is a contestant-excluded jury** (`judge.py::judge_panel`): a panel of 3
  diverse models (`Settings.arena_judge_models` — `deepseek-v4-pro` on the DIRECT
  channel + `claude-opus-4.8` + `qwen3.7-max`), per-judge scores + `judged_stdev`,
  rubric points averaged **by label** (never judge[0]). Dropping below `arena_min_judges`
  (post-exclusion or post-failure) escalates to a **degraded** `self_consistency` mode
  (`arena_self_consistency_k` samples of one judge, `subjective_mode` surfaced), never a
  silent single judge. Judge-missing ≠ infra-invalid — the objective axis still scores it.
- **Judge rubric is 2 subjective points only** (synthesis coherence + analytical
  correctness). The 5 deterministic points that used to live here duplicate objective
  checks — scoring them with an LLM only injected noise, so they were deleted.
- **Trap steps declare `trap_absent_sets`** (workflow frontmatter); `runner.py::
  _assert_trap_sets_absent` fails the match setup if a reserved "does-not-exist" set is
  actually present, so a trap can never silently invert (the old `liquidity-crunch` set
  existed in `data/scenario_sets/`, making every competent model "fail" the trap).
- **Infra-contamination recovery requires a completed `response_text`** — a tool call
  followed by a 402 on the final response is a partial death (`_is_infra_contaminated`),
  not recovery. **Grounding fixtures must be harvested from real tool payloads**, not
  invented (the dead `hotspot.delta`/`landscape[spot_shift=0.1]` paths scored 0/23 for
  everyone until re-pathed to `metrics.positions[position_id=8].delta` /
  `results.portfolio.raw[spot_shift_pct=10.0]`).

- **`expected_skill: null` steps score no skill point.** Use it for repeat-skill
  steps: `skills_routed` only records a skill when its SKILL.md is read and the
  runtime never re-reads a loaded file, so a repeat-skill check can never pass.
  `registry.py` skips skill-name validation for null steps.
- **`response_quotes_tool_value`** digs a numeric target from the last matching
  tool result in the transcript (self-grounding — no fixture values in the
  manifest) and scans the response for a matching numeric token. **Signed by
  default** (an inverted risk sign must fail); `match: magnitude` only for
  loss-language metrics (CVaR). `near: [...]` anchors bind the number to its
  metric label (160-char window) — without them multi-value questions pass on
  swapped answers. `scope: session` reads cumulative results from earlier steps.
- **`tool_called` supports `args_any_of`** (multiple legitimate calling
  conventions) **and `exclusive_keys`** (multi-carrier tools: keys not in the
  matched candidate must be absent — blocks `predefined + custom` mixed-carrier
  over-execution that subset matching alone would pass). `_dig` paths support
  `[key=value]` list selectors, e.g. `landscape[spot_shift=0.1].gamma`.
- **Prohibition floor:** blank transcripts still earn the 3 `tool_not_called`
  points (inaction satisfies prohibition) — the objective floor is ~7.7, not 0.
- **Axis subtotals:** every check carries a derived axis (procedural / adherence /
  grounding / synthesis) → `score_breakdown.objective.axes`; aggregate stays flat
  +1/check. Axis map lives in `scoring.py::_AXIS_BY_TYPE`.
- **`invalid` match status:** an all-blank transcript **with** step-error evidence
  (transport/provider failures) is recorded `status="invalid"`, `error="infra_blank"`
  at the arena-task boundary — judge skipped, excluded from leaderboard means,
  surfaced as `invalid` counts + `MatchSummary.error`. Blank **without** errors
  stays a real scored 0. Detection: `services/arena/task.py::_is_infra_blank`.
- **Exact-count test coupling:** the 39-point denominator is pinned in
  `test_flagship_loads`, `test_arena_scoring` (several places), and
  `test_golden_workflow_regression` (replay must earn 39/39); changing the
  manifest means updating all of them — and the golden replay fixtures must keep
  earning full marks (fixture-consistency gate).
- **Per-trial transcripts:** `_save_transcript` writes the canonical
  `transcript.json` (what `ArenaMatch.transcript_path` points at — a single column,
  so it stays the LAST clean trial) **plus** `transcript.trial<N>.json` per trial.
  Before the per-trial copy, trial N overwrote trial N-1, so with `trials≥2` the
  failing trial behind a low CON was unauditable — the evidence you most need is
  exactly the one that got clobbered. Read the per-trial files, not
  `transcript.json`, when diagnosing trial-to-trial variance.

### QuantArk is pinned — never install it editable

`pyproject.toml` pins **`quantark==0.3.0`** exactly, not `>=`. QuantArk is the engine the
golden fixtures are harvested against, so its version is part of the benchmark's evidence
(the same rule `CLAUDE.md` already states for the compaction A/B: *dependency drift
invalidates the benchmark*). **Never `pip install -e /path/to/quant-ark`** — that makes a
live working tree the pricing engine, so an uncommitted edit in a sibling repo silently
changes benchmark numbers here, and it leaves stale dist metadata (this venv once reported
`0.1.2` while running `0.4.0` code, defeating any version check).

This is not hypothetical: QuantArk `fdf3a70` *"stabilize PDE and QUAD grids"* rewrote
`snowball_quad_engine.py` and moved every `SnowballQuadEngine` number — high-board's governed
valuation `238.0478921928385 → 237.72581292974365`, `gamma_cash −6.64 → −287.04` — with **no
change in this repo**. Diagnosis shortcut: if only `SnowballOption` rows drift while vanillas
(`BlackScholesEngine`) and barriers (`BarrierAnalyticalEngine`) stay exact, suspect the QUAD
engine version before anything in this repo.

To move to a newer QuantArk: bump the pin, re-run
`python -m app.golden_workflows.harvest_fixtures`, and update the graded constants in the
affected manifests **in the same commit** — never separately, or fixtures and engine disagree
and every grounding check silently mis-scores. Note 0.3.0 forgoes 0.4.0's QUAD/PDE
stabilisation fix: reproducibility was chosen over engine recency, deliberately.

### risk-limit-breach-day (limits workflow) + the limits agent tools

The fourth golden workflow (7 steps / **38 points** — was 39 until run #101 showed
step 2's `read-risk-result` skill check was unroutable, see below — persona
`risk_manager`, **uncalibrated par** — hyperbolic EFF until a live board calibrates it,
and run #101 says a realistic counted par is ~25: the leanest FULLY-CORRECT trial took
19 calls, median 25, against `designed_par` 11): work an
overnight portfolio net-delta cap breach to verified closure on the governed Limits
module. It ships WITH the limits agent surface: `backend/app/tools/limits.py`
(4 reads + 5 HITL `"write"`-level writes), `services/limits/agent_support.py`
(`derive_monitoring_envelope` — shared by the tool and the determinism producer so
live and harvested paths are identical), skills `limits/monitor-limits` +
`limits/handle-limit-incident`, and the `limits` domain in
`PERSONA_WORKFLOW_DOMAINS["risk_manager"]`.

- **Step 2 grades no skill (`expected_skill: null`) — the routing-line rule, measured.**
  `read-risk-result` declares no `routing:` frontmatter, so `collect_routing_rows` skips
  it and it never enters the orchestrator's Known-skills table (that is the documented
  design for sub-workflows, not a bug in the skill). Run #101 quantified the grading
  consequence: **4/36 trials (11%)** routed it, versus 75-97% for every routed skill on
  the same board — and the step was performed correctly anyway (**18/18** models called
  `get_latest_risk_run`, **36/36** recorded both answer fields), so the check measured
  document-loading, not ability. A routing line was rejected because `read-risk-result`
  is ALSO the flagship's step-1 skill: making it routable moves flagship difficulty
  across 11 boards of history. **Before grading any skill, check it has a `routing:`
  block** — or accept that you are measuring catalog spelunking.
- **Refresh-then-reuse is the evidence contract.** `run_limit_monitoring` derives
  profile/engine/evidence-id/valuation from the LATEST completed risk run
  (`source_planner` identity matching requires exact equality on all four;
  `max_source_age_seconds=None` keeps profile-dated runs fresh). `force_refresh`
  is deliberately not exposed: the queue-level evidence id is a requested pin that
  `finalize_market_metadata` enforces against the computed hash, so a
  model-suppliable label can never match on a multi-symbol book. A model that
  re-monitors without refreshing risk deterministically fails to verify — that IS
  the graded discipline.
- **Incident mutations require `expected_row_version`** from a preceding read
  (acknowledge/comment each increment it); conflicts return
  `{ok: false, error: "conflict", hint: ...}`. A clean re-run **auto-recovers** the
  incident (`recovered`, terminal) — `resolve` on it conflicts, which the workflow
  grades as a prohibition (step 7). The no-waive ban is **session-scoped** in
  `success.assertions` only (a per-step ban is gameable by waiving early).
- **`risk_limits`/`risk_limit_versions` are protected-immortal** (deletion guards
  have NO arena exemption) and `key` is unique: fixtures seed them
  **ensure-by-key** (reserved `arena-` prefix; a collision with a non-arena-owned
  key fails the load). Everything else in the limits family purges via the
  portfolio dependents sweep. `_assert_no_foreign_active_limits` (arena runner,
  pre-seed) fails match setup if a foreign active non-portfolio-scoped limit
  version would join the run — `_active_versions` portfolio-filters ONLY
  portfolio-scoped versions, so fixture limits must always be portfolio-scoped
  (an underlying-scoped fixture limit would contaminate real desk runs).
- **Seeded market quotes are load-bearing:** the limits evaluator refuses
  synthetic-default spots (`missing:spot` → `incomplete_scope`/`unknown`), so the
  fixture book seeds instruments + quotes (unlike the flagship's
  fallback-spot posture). Position `product_id` backfill runs on any tool's
  `database.init_db()`; harness/tests driving services directly must call it after
  seeding or the evidence manifest hashes the pre-stamp identity and reuse fails.
- Truth: `breach_net_delta` 802.685… / `driver_delta` 573.347… (book minus the
  −400-delta AAPL futures hedge) and `clean_net_delta` 402.685… (live monitoring
  producer); boundaries warning 500 / hard 600 sit strictly between clean and
  breach by construction (guard test).

### Fixture determinism (Spec A — enables the Model Ability Card)

The flagship producers must yield **byte-identical** numbers across runs so grounding
can score against harvested truth. Package: `golden_workflows/determinism.py`
(`seed_flagship` + `drive_producers`, `seed_backtest_history`),
`harvest_fixtures.py`, `definitions/risk-manager-control-day.truth.json`. Gate:
`tests/test_arena_fixture_determinism.py`.

- **The gate is offline + clean-DB.** `drive_producers` calls each producer's private
  `_execute_*(session, task_id, run_id)` seam (async dispatch suppressed) and compares
  a **canonical** payload — volatile keys (`created_at`, ids, `execution_time`, …) are
  stripped, else identical numbers still differ. Backtest is checked **strict** (reject
  `excluded_positions` / empty result), because `domains/backtest.py` swallows a live-
  fetch failure into an empty "completed" run.
- **Audit result:** risk/landscape/scenario are deterministic via the profile
  `valuation_date` (`batch_pricing.py`); the backtest was the only live-fetch drift.
  `seed_backtest_history` seeds a flat `MarketDataProfile` over **every expected SSE
  trading day** (`expected_trading_days`) so `ensure_spot_history` finds full coverage
  and never fetches — also sidestepping the US-stock gap-detection refetch.
- **Truth is harvested, never invented.** `harvest_fixtures.py` digs five targets from
  real payloads into `*.truth.json`, keyed **by underlying/shift** (`[underlying=AAPL]`,
  not `[position_id=8]` — ids aren't stable in a clean DB). Re-run
  `python -m app.golden_workflows.harvest_fixtures` after any QuantArk numeric change
  rather than hand-editing.
- **Isolation posture:** the determinism gate and harvester run in **isolated** clean
  DBs, and the **live arena path is unchanged** — no market data is seeded into the
  shared store (risk uses the deterministic fallback spot; the flagship `.fixtures.json`
  carries no quotes). `seed_backtest_history` tags its rows `source="arena_seed"`
  (`ARENA_MARKET_SOURCE`) as a forward hook: if Spec B ever grounds on **backtest P&L**
  it must also seed that history on the live path and add purge/exclusion, since a live
  arena backtest currently still fetches real akshare history (the other four truth
  targets match live via fallback-spot determinism).

---

## Model maintenance UI

A web console to add/edit/delete LLM **channels and models** — and set the registry
default — instead of hand-editing `config/agent_channels.yaml`. The YAML stays the single
source of truth; the UI mutates it and hot-reloads the live registry.

**Package:** `backend/app/services/deep_agent/channel_registry_writer.py` (the writer),
`channel_registry.py` (`reload()` now reads under `_LOCK`; new `commit_registry()` seam),
`model_factory.py::agent_registry_config` (maintenance serializer), `routers/agent_channels.py`
(`build_agent_channels_router`, flag-gated CRUD under `/api/agent`). Schemas:
`AgentRegistryOut` / `ChannelWriteIn` / `ModelWriteIn` / `DefaultWriteIn` (`schemas.py`).
Frontend: `frontend/src/routes/ModelMaintenance.{tsx,live.tsx,css}` (the **Model Maintenance**
nav page). New dep: `ruamel.yaml`.

### Write model (validate-then-commit, corrupt-save-proof)

Every mutation runs the FULL read-modify-write under `channel_registry._LOCK` via
`_mutate`: load the YAML round-trip (`ruamel`, comment/key-order preserving) → apply one
change + guards → dump to a temp file → validate with the existing
`channel_registry.load_from_path` → only on success `os.replace` onto the live file **and**
swap `_REGISTRY` under the same lock, then the router calls
`agent_service.rebuild_default_model()`. A bad candidate never reaches `os.replace`
(→ HTTP 422, live file byte-unchanged); guard violations → 409.

### Gotchas

- **Holding `_LOCK` across the load (not just the commit) is the lost-update fix.** Two
  concurrent writes that each only locked the commit would both read the same snapshot and
  the second `os.replace` would clobber the first. `reload()` was also changed to read the
  file under `_LOCK` for the same reason.
- **Health-independent default integrity.** `load_from_path`'s `_resolve_default` *skips*
  validating the default when its channel is unhealthy (missing `api_key_env` var), so the
  writer has its **own** raw-level check (`_assert_default_integrity`) that blocks
  deleting/renaming the default even when unhealthy — else a later reload (once the key
  returns) would fail with a dangling default.
- **Model routes need `{model_id:path}`.** Model ids contain slashes
  (`anthropic/claude-sonnet-4.6`), so a plain `{model_id}` segment can't match; the frontend
  sends the id **raw** (channel names are URL-encoded).
- **Secrets never touch the YAML.** The UI edits only the `api_key_env` *name*; health is
  derived from whether that env var is set. Adding a brand-new provider still needs a manual
  `.env` edit + restart.
- **Does NOT sync arena `CANDIDATE_MODELS`.** `services/arena/models.py::CANDIDATE_MODELS`
  is a separate hardcoded list; adding a model here does not make it an arena contestant.
- **Writes gated by `OPEN_OTC_FEATURE_MODEL_WRITE_API`** (default on; three config sites like
  every other flag). This is a default-on, unauthenticated surface consistent with the rest
  of the no-auth backend — set it `false` on any non-localhost bind. The UI edits only the
  live root `config/agent_channels.yaml`; the tracked `.example.yml` is left to humans.
- **Tests are hermetic against `AGENT_CHANNELS_FILE` leaks** — the writer/router/serializer
  test fixtures source the config from `channel_registry._REPO_ROOT`, not the env-overridable
  `_yaml_path()` (another test in the suite repoints that env var).

---

## Term-structure curves for pricing parameters

Per-underlying `r`/`q`/`vol` **term-structure curves** feed a *materialize-then-price*
step: curves are interpolated at each open trade's maturity into a normal **flat**
`PricingParameterProfile`, so the pricing path (QuantArk, `risk_engine`,
`build_assumptions_set`) is **completely unchanged** — it still consumes scalars.

**Storage.** Three nullable JSON columns on `instruments` (`Instrument` /
`UnderlyingPricingDefault`, `models.py`): `rate_curve` / `dividend_yield_curve` /
`volatility_curve`, each `list[{"tenor": <label>, "value": <float>}] | None`. `None`/`[]`
means "no curve — use the flat scalar". Migration `0050`.

**Interpolation** is a pure, DB-free module: `services/term_structure.py` — `TENOR_YEARS`
(label→year-fraction map, the single source; extend it to add 1D/4M/7Y/…), `validate_curve`
(known labels, dedup, finite, vol `>0`), and `interpolate_curve` (linear on the year axis,
flat extrapolation past the ends, single point → constant, empty/None → None).

**Generate** lives in the domain facade `services/domains/pricing_profiles.py`:
`generate_curve_param_rows` (read-only compute) walks `open_otc_positions`, skips delta-one
(`position_requires_pricing_params`), derives each trade's **tenor in years** via
`_tenor_years_for_position` — a numeric year-fraction `maturity` (the QuantArk `T` the engine
prices with) is used directly, else an absolute maturity date resolved from
`compatibility_terms_for_position(...)["product_kwargs"]` under a priority key list
(`maturity_date` / `expiry_date` / `expiry` / `exercise_date` / `settlement_date`) is turned
into **ACT/365**. It interpolates each curve (fallback to the flat Instrument scalar) and
raises `ValueError({"unfilled_trades": [...]})` when a param has neither curve nor scalar
(mirrors `build_assumptions_set`'s `unfilled_underlyings`). `generate_profile_from_curves`
writes a `PricingParameterProfile(source_type="curve")` + one flat row per trade — bound to
the position by **`position_id`** (migration `0051`), with per-row interpolation provenance —
and audits `pricing_parameter_profile.generated_from_curves`.

**Binding.** Because term-structure rows are per-trade (each maturity → its own r/q/vol),
they need a per-position key. `resolve_pricing_parameter_row_for_position` prefers a
`position_id` match before trade-id / underlying, so a curve row resolves uniquely even when
the position has no `source_trade_id` — the live-book case, where several positions share an
underlying and underlying-level resolution is otherwise `ambiguous`. Imported rows have
`position_id=None` and resolve exactly as before (fully backward-compatible). The Pricing
Parameters **POSITION** column renders the bound `#<position_id>`.

**Surfaces.** REST: curve fields on `PUT /api/underlying-pricing-defaults/{underlying}` and
`GET .../underlying-pricing-defaults` (validated server-side via `validate_curve`), plus
`POST /api/pricing-parameter-profiles/from-curves`. Agent: `set_/get_instrument_pricing_defaults`
round-trip curves, and the WRITE+HITL tool `generate_pricing_parameters_from_curves`. Frontend:
Instruments → **Assumptions** tab only — a per-underlying curve editor (tenor `<select>` +
value input, add/remove) and three token-only recharts line charts (one per param, since r/q
sit near 0–5% and vol near 15–40% — a shared y-axis would flatten r/q), plus a "Generate
pricing parameters from curves" button. Pricing Parameters stays a flat table.

### Gotchas

- **Curves feed ONLY the generate step.** `build_assumptions_set` is untouched and still uses
  the flat scalars — do not wire curves into the assumption-set build (deferred by design).
- **The generate tool needs four registrations,** not one: `QUANT_AGENT_TOOLS`
  (`tools/__init__.py`), `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`), and all **three**
  structures in `services/deep_agent/hitl.py` (`INTERRUPT_TOOL_NAMES` + `_RISK_LEVEL_BY_TOOL`
  → `"write"` + `_LABEL_BY_TOOL`). `test_hitl.py`'s exact-set guard forces you to update it.
- **`source_type="curve"` is load-bearing** — the generate path does NOT reuse `create_profile`
  (which hardcodes `source_type="agent"` and a generic audit event); it has its own write.
- **`validate_curve` is the single validation seam,** shared by the REST PUT
  (`upsert_underlying_default`) and the agent setter (`set_instrument_defaults`). Volatility
  curves require `> 0`; rate/dividend allow any finite value (rates can be negative).

---

## Trade confirmation → book

Turn counterparty confirmation documents (PDF/DOCX, **including scanned/image-only
pages**) into booked positions. One shared service owns the pipeline; the REST
surface, the **Confirmations** page, and the agent tools are all thin clients over
it — there is no second booking path.

**Package:** `backend/app/services/confirmations/` — `extract.py` (pure PDF/DOCX text
extraction + image-page detection/render), `llm.py` (two-stage extraction + model
routing), `service.py` (batch → parse → validate → review → book). REST:
`backend/app/routers/confirmations.py` (`/api/confirmations`). Tools:
`backend/app/tools/confirmations.py`. Skill:
`skills/workflows/positions/book-trade-confirmation/`. Frontend:
`frontend/src/routes/Confirmations.{tsx,live.tsx,css}`. Migration `0052`
(`confirmation_batches` → `confirmation_documents` → `extracted_trades`).
Deps: `pypdf`, `pypdfium2`.

### The pipeline

Text-first: a PDF page under `MIN_TEXT_CHARS_PER_PAGE` (40) is treated as a scan and
rendered to PNG for the vision path; `extract_mode` records `text`/`vision`/`mixed`
honestly. Then **two LLM stages** — stage 1 segments the document into trades
(`{family, pages, anchor}`, family constrained to the `build_product` vocabulary),
stage 2 fills that family's `get_product_term_schema` with per-field
`{quote, page}` evidence. Every draft is immediately re-validated through
`prepare_booking_product_spec`; `validation_status` is `valid` / `invalid` /
`unsupported`, and validation failures are **data, not errors** (the reviewer edits
and the server re-validates).

### Invariants

- **The underlying must be a BOOKABLE instrument: `status="active"` AND tagged
  `"underlying"`.** Both halves, always — `services/underlyings.py::is_bookable_underlying_row`
  is the single predicate, and `services/instruments.py::resolve_bookable_underlying` is the
  shared gate wrapping it (used by `validate_trade_terms` and `book_position_tool`; the tag
  alone used to be enough, which let a draft instrument through). **This is a fail-CLOSED
  gate because the failure mode downstream is silent:** an unrecognised underlying is not
  rejected by booking — `book_position` → `link_position_underlying` → `ensure_underlying`
  **mints a new Instrument** for any non-empty string (`normalize_underlying_symbol` is just
  `.strip()`), so the position books "fine" and is then unpriceable forever. Never resolve a
  legal name to a symbol automatically; report candidates and let a human/agent choose.
  Candidates come from the confirmation's own evidence quote, which is why
  `"Shares: Apple Inc. (Ticker: AAPL)"` can suggest `AAPL` — suggestion only, never rewrite.
- **Review-first is the whole design.** `book_trade` re-runs validation at booking
  time (never trusts the stored status) and books through the existing
  `book_position` gate. The web page books per trade on a human click; the agent's
  `book_extracted_trade` is HITL **`"irreversible"`**. `parse_trade_confirmation` is
  write-class but deliberately **not** HITL — parsing writes only draft rows.
- **`"write"` is not a HITL level for anything that books.** `interrupt_on_config(
  yolo_mode=True)` — i.e. **AUTO mode** — strips every `"write"`-level tool from the
  interrupt map, so a booking tool classified `"write"` raises **no approval card at
  all** and persists a real position unattended (this happened: thread 682 booked
  position 27 off a PDF with `pending_actions: []`). Every booking tool is therefore
  `"irreversible"` — `book_position`, `book_rfq_to_position`, `book_hedge`,
  `book_extracted_trade` — and positions have no delete to make it otherwise. When
  adding a HITL tool, ask which of the three modes must still stop it: `"write"`
  means "interactive only", **not** "gated".
- **A gated tool needs a `_SUMMARY_BUILDERS` entry when its args are ids.** The
  interrupt fires *before* the tool body runs, so the card can only see the raw args —
  `book_extracted_trade`'s are `{portfolio_id, trade_id}`. `_summarize_book_extracted_trade`
  opens its own short-lived read-only session to state the product, size, counterparty
  and destination, exactly like `_summarize_register_underlying`. Without one the gate
  is theater: a human clicking Approve on two integers is not review.
- **Idempotency:** `source_trade_id` = the confirmation's own `external_trade_id`,
  else `conf:{sha256[:12]}:{seq}`. A pre-check on `(portfolio_id, source_trade_id)`
  returns `already_booked` + the existing position id. Note `book_position` itself
  does **not** dedup and there is no DB unique constraint — the service pre-check is
  the only guard (single-writer posture, same as the xlsx importer).
- **Per-document failure isolation:** `parse_document` never raises for extraction
  failures — the document lands `status="failed"` with the error (and the raw LLM
  response in `extraction_debug`), and the rest of the batch continues.
- `extracted_terms` is **immutable** (what the model returned); `terms` is the
  human-editable copy. Both are kept so a review can always be audited against the
  original parse.

### Gotchas

- **Extraction output is builder INPUT, not a termsheet — `synthesize_booking_terms`
  is mandatory.** The extractor fills `get_product_term_schema(family)`, so a vanilla
  legitimately comes back as `{initial_price, exercise_date, strike, option_type}`
  (three of them REQUIRED by that schema). But `booking._RAW_TERMSHEET_VOCAB` — the
  sniffer choosing synthesize-vs-validate-verbatim — lists only
  `maturity_years|maturity_date|expiry_date|expiry`, so those terms match nothing, take
  the verbatim path, and QuantArk rejects `initial_price` as an unsupported kwarg.
  **Do NOT "fix" this by adding `exercise_date` to that tuple:** already-built vanillas
  persist `exercise_date` *without* `initial_price`, so widening the tuple flips them
  from the working verbatim path onto a synthesize path that then fails for the missing
  field — the same trap its comment documents for `initial_price`. The service instead
  runs `build_product` itself (the repo's single producer) before the gate, exactly as an
  agent would. `validate_trade_terms` and `book_trade` MUST use the same helper or
  "valid" and "bookable" diverge. **Only the live smoke caught this** — the unit fixtures
  used `maturity_years`, which happens to match the raw-vocab list, so every offline test
  passed while the real model's schema-faithful output was unbookable.
- **A test that books must seed a bookable underlying.** `Instrument.status` defaults to
  `"draft"`, so tagging alone leaves a row unbookable — the `registered_underlying` conftest
  fixture (active + tagged, committed so tools opening their own session can see it) exists
  for this. Symptom of missing it: every booking test suddenly reports `invalid`.
- **The extractor model must be vision-capable.** Routing is by registry tag,
  two-tier: `confirmation_extractor` (tag exactly one model) → `fast` → registry
  default. Tag edits go to **both** `config/agent_channels.yaml` and the tracked
  `.example.yml`. Pinned here: `google/gemini-3.6-flash`. A document needing vision
  under a text-only model fails loudly rather than silently degrading.
- **No module-scope `app.tools` import may live in `services/confirmations/`.**
  `llm.py`/`service.py` need `app.tools.product_term_schema`, but `app.tools`'s
  package init imports `tools/confirmations.py`, which imports this package — a real
  cycle. Both use **function-scope** imports, and
  `test_confirmations_tools.py::test_service_import_before_app_tools_has_no_import_cycle`
  is a fresh-subprocess probe that pins it.
- The three tools need the **full registration checklist** (`QUANT_AGENT_TOOLS`,
  `DEEP_AGENT_TOOL_NAMES`, and for the book tool all three `hitl.py` structures) plus
  the exact-set pins in `test_capability_assignments.py` (tool count) and
  `test_hitl.py`. Adding the SKILL.md broke exact-set assertions in four catalog test
  files — enumerate with `grep -rln "book-position" tests/`.
- **Chat attachments are a separate seam:** `POST /api/chat/uploads` stores under
  `artifact_dir/uploads/chat/` (inside the parse tool's containment root), and
  `stream_chat_message` appends an attachment manifest to the **agent-run** content
  only — the persisted user message keeps the user's original text.
- **Never let the chat report a write through prose alone.** `ChatBubble`'s
  reasoning-fold heuristic (`resolveAssistantContentPresentation`) hides the *entire*
  message body when a reply is long, mentions its own tool names, and carries ≥5 tool
  events — and `findPublicContentBoundary` only rescues a body whose heading contains
  `portfolio|report|summary|risk|hedg|snapshot|recommendation`, which a booking reply's
  headings do not. A real booking confirmation was therefore invisible. The fix is
  structural, not lexical: `book_trade` returns a `booking` payload on **every** exit
  path → `_capture_booking_result_from_tool_end` (reads the tool **result** at
  `on_tool_end`, unlike the term-form/reply-option captures which read **args**) →
  `collector.booking_result` → `meta.booking_result` → `BookingResultCard`. Capture is
  keyed by tool **name** (`BOOKING_RESULT_TOOLS`) so an unrelated tool can't spoof a
  card, and the record survives `reset_user_facing_output_for_retry` because — like a
  tool event — it reports a write that really happened. **Adding a tool to
  `BOOKING_RESULT_TOOLS` requires that tool to return the same `booking` shape.**
- **Result-message scanning cannot see anything a PERSONA did.** The obvious
  implementation — scan `result["messages"]` like `_term_form_from_result` — is
  structurally wrong here: the orchestrator delegates via `task()`, and the persona runs
  in its **own LangGraph checkpoint namespace**. Measured on a live gated booking:
  `book_extracted_trade` appeared in **28 subagent checkpoints and 0 orchestrator ones**.
  `propose_term_form` escapes this only because the *orchestrator itself* calls it. The
  `wrap_tool_call` seam is the only place that sees a subagent's tool calls — the same
  reason `AuditTrailMiddleware` lives there and is registered in all three stacks.
  `BookingResultMiddleware` (`deep_agent/booking_capture.py`) sits beside it.
- **A tool result body is NOT plain JSON at the `wrap_tool_call` seam.**
  `GroundTruthArtifactMiddleware` runs *inside* it and appends
  `<artifact_ref>{...}</artifact_ref>` to the content, so `json.loads(content)` raises
  `Extra data` and silently drops the payload — while a repr still shows the JSON
  perfectly, which makes it look like the middleware never ran. Use
  `json.JSONDecoder().raw_decode()` (first value, ignore the trailing evidence ref), and
  also handle content-block **lists** and `Command` results — the seam returns all three
  shapes. Note `AuditTrailMiddleware` is immune because it only *stores* `str(content)`.
- **Do not key run-scoped state on `configurable["thread_id"]`.** `graph_run_config`
  documents it as the *checkpointer* key ("sometimes a composite string, NOT necessarily an
  AgentThread id"), and inside a persona it is neither. The stable turn identity is
  `AUDIT_CONTEXT_KEY['thread_id']`, stamped at every entry point and readable from inside
  subagents. `current_run_keys()` records under both.
- **The agent does not always route through `book_extracted_trade`.** Live runs booked the
  same confirmation via plain `book_position` roughly half the time, which the booking card
  does not cover (that tool returns no `booking` payload). Worth closing before treating
  card coverage as complete.
- The frontend vitest suite is **flaky under load** (slow route tests hit the 5s
  timeout; `main` alone varies 12→18 failures run to run). Compare failing-file sets
  against a same-machine `main` run before blaming a branch.

---

## Report module (templated reports)

Reports are generated from **declarative YAML templates**. A server-owned block
registry resolves every number deterministically; the agent's only output is prose.

**Package:** `backend/app/services/reporting/` — `contracts.py` (tri-state `BlockResult`),
`registry.py` (`@report_block`), `blocks/{risk,pnl,limits,desk}.py` (18 producers),
`renderers.py` (renderer↔shape map), `template_spec.py` (parse + validate-all-errors),
`templates.py` (validate-then-commit store), `seeds/*.yaml` (4 shipped templates),
`grounding.py`, `document.py`, `generate.py`, `narrator.py`. Deterministic P&L lives in
`backend/app/services/pnl/` (`snapshot_diff`, `explain`, `entry_price`). REST:
`routers/reports.py`. Tools: `tools/report_templates.py`. Skills:
`skills/workflows/reporting/{generate-templated-report,author-report-template}`.
Migrations `0053` (tables) + `0054` (seeds).

### Gotchas

- **`empty` vs `unavailable` is the whole point — never collapse them.** `empty` = the
  check RAN and found nothing ("no limit is in breach"); `unavailable` = the check DID NOT
  RUN. A report that renders them alike claims a clean book nobody verified. Limit
  evaluations with status `unknown`/`incomplete_scope` are a third case, counted as
  `indeterminate` — filtering them out (the obvious implementation) produces an empty
  breach list and a report that reads clean when the truth is "we couldn't tell".
- **`create_report` keeps its name forever.** It is a graded `tool_not_called` prohibition
  in three golden workflows; deleting the tool makes four checks trivially always-pass.
  The implementation was deleted, the name routes through `portfolio-snapshot`.
  `tests/test_reporting_legacy_retirement.py` pins this.
- **Seeded templates need BOTH install paths.** Migration `0054` seeds them, but a DB
  created by `init_db()`'s ORM bootstrap never runs it and then `create_report` has no
  template to resolve. `database.ensure_seeded_report_templates()` covers that, called
  from `create_app` — deliberately **not** from `init_db()`, which every block producer's
  `_session_scope` calls on the hot path, including from worker threads.
- **Never hold a write transaction across block resolution.** Each producer's
  `_session_scope` calls `database.init_db()`, which issues `create_all` + schema DDL;
  with an open write transaction in the same worker thread SQLite deadlocks and the task
  hangs to its poll timeout. `_complete_report_job` commits the `RUNNING` marker first.
- **A write service must not copy `domains/risk.py`'s `_session_scope`.** That one is
  READ-only: it flushes and never commits, so a self-owned session silently discards the
  write. The template store did exactly this and `PUT` answered 200 while nothing
  persisted — caught only by an HTTP test, because every unit test injected its own
  session.
- **Reports embed their own template spec + sha256.** Editing a template never rewrites
  what an old report claims to have been generated from, and no stored hash can dangle.
- **`scenario.latest_grid` reads `unavailable` while `scenario_test_runs` is empty.**
  That part is honest, not a bug: the risk template shows the desk that its report says
  nothing about tail risk. But **a producer that returns `empty` from a failed key lookup
  is indistinguishable, at the type level, from one reporting a genuine absence** — and
  `.get()` hands you the reassuring branch by default. This block read `results["rows"]`
  while the runner persists `shape_results(...)` under **`"scenarios"`**, so a populated
  stress grid would have rendered as "we checked, nothing to report". A producer must
  return **`unavailable` for a payload it cannot parse** and reserve `empty` for a payload
  it read successfully that contained nothing. Test the POPULATED path against the real
  producer's keys — a fixture that invents its own payload shape shares the bug.
- The grounding guard reuses the arena scorer's `_scan_numeric_tokens`. That tokenizer
  emits TWO readings at one offset for a `%` token (`34.0` and `0.34`), so tokens are
  grouped by offset and grounded if EITHER matches — otherwise "34%" is flagged whenever
  the data stored `0.34`. Its `k|m|mm|bn|b` suffix has **no word boundary after it**, so
  it reads "5 basis points" as `5e9` and "3 month" as `3e6`, and emits only the scaled
  reading. **Never fix that in `assertions.py`:** `_quote_value_report` matches on ANY
  reading, so an extra reading makes arena grading strictly more lenient across every
  stored board. `grounding.py::_misparsed_suffix_readings` corrects it locally and
  **overwrites** the reading (there is no suffix, so the scaled value is a misparse, not
  a second interpretation). Decide on the WHOLE trailing word — a per-character rule
  backtracks `1.2bn` to a `b` suffix followed by the "letter" `n` and corrupts a genuine
  magnitude.
- Flags are **non-blocking**: a false positive should degrade the report's confidence
  signal, not destroy the report.
- **A section with no blocks is a SYNTHESIS section and must be shown the report
  above it.** `narrator_brief` passes only that section's own blocks, so a
  `blocks: []` section — the board one-pager's `executive_summary` — was handed an
  empty brief and honestly wrote "the evidence base is empty" while all five
  sections above it had resolved fine. It now receives `report_so_far` (resolved
  upstream sections) and is grounded against that same evidence, or every figure
  it correctly carries forward would be flagged as invented. Ordinary sections are
  deliberately NOT given it, so a brief stays focused on its own evidence.
- **The grounding guard needed three live-found corrections**, all false positives
  that would have trained readers to ignore it: ISO timestamps are strings, so
  "23 June 2026" was ungrounded until date components are mined from them; a
  sha256 quoted verbatim from the data was shredded into 17 fabricated "numbers"
  until strings reproduced verbatim are blanked before tokenizing; and relative
  tolerance alone rejects prose rounding at small magnitudes ("0.06" for 0.0634 is
  5.4% off), so a token also grounds when it equals a data value rounded to any
  precision. **Only a live model run surfaces these** — a hand-written fixture
  narrative quotes numbers the way the test author would, not the way a model does.
- **A section with no blocks is a SYNTHESIS section and must be shown the report
  above it.** `narrator_brief` passes only that section's own blocks, so a
  `blocks: []` section — the board one-pager's `executive_summary` — was handed an
  empty brief and honestly wrote "the evidence base is empty" while all five
  sections above it had resolved fine. It now receives `report_so_far` (resolved
  upstream sections) and is grounded against that same evidence, or every figure
  it correctly carries forward would be flagged as invented. Ordinary sections are
  deliberately NOT given it, so a brief stays focused on its own evidence.
- **The grounding guard needed three live-found corrections**, all false positives
  that would have trained readers to ignore it: ISO timestamps are strings, so
  "23 June 2026" was ungrounded until date components are mined from them; a
  sha256 quoted verbatim from the data was shredded into 17 fabricated "numbers"
  until strings reproduced verbatim are blanked before tokenizing; and relative
  tolerance alone rejects prose rounding at small magnitudes ("0.06" for 0.0634 is
  5.4% off), so a token also grounds when it equals a data value rounded to any
  precision. **Only a live model run surfaces these** — a hand-written fixture
  narrative quotes numbers the way the test author would, not the way a model does.

---

## Settlement module

The cash that position lifecycle events imply, tracked and governed. Cashflows are
auto-generated from `PositionLifecycleEvent`s; users and agents release, block, edit
and settle them, and issue settlement notices.

**Package:** `backend/app/services/settlement/` — `contracts.py` (statuses +
`CashflowDraft`), `derive.py` (the pure deriver + `CASH_LEG_RULES` +
`SINGLETON_LEG_KEYS`), `store.py` (state machine + optimistic concurrency),
`generate.py` (sweep + same-position dedup + fill), `drift.py`, `notice.py`,
`errors.py`. REST: `routers/settlement.py` (`/api/settlement`). Tools:
`tools/settlement.py` (3 reads + 10 writes). Skill:
`skills/workflows/settlement/manage-settlement-cashflows/`. Frontend:
`frontend/src/routes/Settlement.{tsx,live.tsx,types.ts,css}`. Migration `0055`.

### It governs cash; it never computes payoffs

An amount either comes with the lifecycle event or the cashflow is honestly
`needs_amount`. That is a correct state, not a bug — the same `empty` vs
`unavailable` discipline the report module fought for. A settlement calculator here
would be a second, unvalidated pricing surface beside the pinned `quantark==0.3.0`.
The one exception is the `premium` leg, which multiplies the position's own recorded
`entry_price × quantity` — two stored trade fields, not a model.

### `derived_amount` beside `amount` is the load-bearing choice

`derived_*` is the deriver's snapshot for the row's OWN event; `amount`/`value_date`
are effective values an edit changes. **Without the frozen snapshot, drift cannot be
detected on an edited row** — the recompute would be compared against the human's
number and every edited row would read as drifted forever. `amount != derived_amount`
therefore means "overridden", which is exactly how `resync_cashflow` must treat it.

### Two dedup layers, because they catch different duplicates

- `UNIQUE(lifecycle_event_id, leg_key)` stops one event emitting a leg twice.
- `SINGLETON_LEG_KEYS` (`{"settlement", "premium"}`) + `generate._open_singleton` stop
  **different** events emitting the same once-per-position leg twice. The DB cannot
  see that a `knock_out` and its follow-up `settle` are one economic settlement —
  they are separate `position_lifecycle_events` rows. Dedup ignores **terminal**
  cashflows so a settle → reopen → settle cycle legitimately earns a second row.
  **`coupon` is deliberately NOT singleton** — coupons recur, and deduping them would
  collapse a snowball's whole schedule into one row.

### Gotchas

- **`generate._fill_if_empty` is the single sanctioned exception to INSERT-only.**
  Terminating events create a `needs_amount` row; the later `settle` fills it.
  Narrow by design: null → value only, never over a released or edited row,
  idempotent, logged as `filled_from_event`. **It leaves `derived_*` untouched** —
  that snapshot describes the row's own event, which really carried no amount, so
  rewriting it would make drift compare the filled value against a re-derived `None`
  forever.
- **Drift flags, never applies, and never bumps `row_version`.** Flagging is not a
  user mutation; a UI holding a version must stay able to act on the row it sees.
- **Amounts are non-negative magnitudes; `direction` carries the sign.** A negative
  derived amount flips `pay` ↔ `receive`, so a loss-side settlement cannot read as
  "pay 1,250" when the cash goes the other way.
- **The inline hook in `create_lifecycle_event` is best-effort on purpose.** Lifecycle
  is the source of truth for position status and must never be held hostage to
  cashflow derivation, which is why the deriver is *total* (never raises) and
  `generate_missing` is the safety net.
- **`settle_settlement_cashflow` is `irreversible`; every other settlement write is
  `"write"`.** Deliberate desk decision: release is recallable (`unrelease` exists and
  `block` is reachable from `released`), so only the unrecallable assertion that money
  moved is hard-gated. Remember `"write"` means AUTO/headless executes it unattended.
  `settle` carries a `_SUMMARY_BUILDERS` entry so the card states the amount rather
  than two bare integers.
- **A test derives the mutating REST routes from the real router** and asserts each has
  a tool counterpart, so the HTTP and agent surfaces cannot drift apart.
- **Eight exact-set pins broke when this landed**, seven backend and one frontend:
  `test_hitl.py` (interrupt set), `test_capability_assignments.py` (121 → 134),
  `test_skills_catalog_v2.py` (×2), `test_routing_table.py`, `test_persona_domains.py`,
  and `frontend/src/lib/routing.test.ts` (26 → 27 routes).
- **The skill lint caps a SKILL.md body at 500 tokens** and requires `may_escalate_to`
  plus an `## Example` section. The first draft came in at 749 and failed CI lint.
- **`--radius-1` and `--ink-3` are referenced by some page CSS but defined nowhere.**
  Do not copy them; verify every token against `frontend/src/tokens/` before use.

### Pre-existing traps this work ran into (NOT caused by settlement)

- **A fresh `alembic upgrade head` cannot succeed in this repo.** The chain breaks at
  `0051` (`duplicate column name: position_id` on `pricing_parameter_rows` — a
  migration written against ORM models). Verified identical on `main`; it is why
  `test_migration_fresh_chain`, `test_migration_0024/0046/0047` sit in the failure
  baseline. Verify a new migration by stamping its parent:
  `alembic stamp 0054_seed_report_templates && alembic upgrade head`.
- **The DB env var is `OPEN_OTC_DATABASE_URL`, not `DATABASE_URL`** (it is a
  `validation_alias`). Getting it wrong does NOT error — it silently falls back to
  `./data/open_otc.sqlite3`, i.e. the LIVE DB, and reports `exit=0`. The only tell is
  the absence of "Running upgrade" lines.
- **A git worktree needs `config/agent_channels.yaml` copied in.** It is gitignored
  (per-env), so without it every test that imports `app.main` dies at collection with
  `FileNotFoundError`. Do **not** copy `.env` — its absence is what makes
  `test_config.py` / `test_tracing_config` honest.
- The venv's editable-install `.pth` currently points at a deleted worktree, so a bare
  `python -c "import app"` fails. Tests are unaffected: `pyproject.toml` sets
  `pythonpath = ["backend"]` relative to pytest's rootdir.

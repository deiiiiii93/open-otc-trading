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
  `EFF = round(C × min(1, par/actual_calls) × 99)` where `C` is the (GRD+ADH+SYN)
  pass fraction — correctness-gated. Being leaner than `par` is **not** penalized
  (ratio capped at 1), but **zero tool calls when `par > 0` is non-execution, not
  leanness → ratio 0 → EFF 0**: value-only grounding (`response_quotes_value`) lets a
  transcript quote the truth numbers without running the workflow, and EFF must not
  hand that a free efficiency pass (GRD still credits the numbers; PRC/EFF read 0, so
  the card honestly shows "strong numbers, no execution"). `par == 0` with 0 calls is
  legitimately full efficiency.
  `OVR = round(0.32·GRD + 0.26·ADH + 0.16·SYN + 0.16·EFF + 0.10·PRC)`. **JDG is never
  in OVR.** `ability_card(transcript, loaded, judged)` is the write-time wrapper;
  `card_from_axes` is the shared kernel.
- **`par` = a COMPLETE compliant run's tool count, not just signature tools.**
  `scoring.designed_par(wf)` = `wf.par_tool_calls` if set else
  `sum(len(step.expected_tools))` (= 11 for the flagship — the 7 signature tools plus
  the 4 retrieval/library calls). `par_tool_calls` is an **optional** manifest field
  (`int | None`, `≥ 1`) so existing manifests still load; a `par` of 7 would wrongly
  cap even a perfect lean run's EFF at ~0.64.
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

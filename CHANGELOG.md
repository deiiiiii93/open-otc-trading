# Changelog

All notable changes to **Open OTC Trading** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Arena scoring is objective-only by default; the LLM jury is now opt-in** (spec
  `2026-07-06-arena-jury-opt-in`). Run #11 showed the subjective jury is too unstable to
  inform evaluation even as an advisory axis — it ranked models in reverse of the
  deterministic objective axis and swung on which ZenMux judges were reachable — so the
  jury is gated behind `OPEN_OTC_ARENA_JURY` (default **off**). The jury code, config
  knobs, and the 2-point manifest rubric are all kept intact for opt-in use.
  - **Provenance is explicit** so a failed opt-in jury never looks like a deliberate
    opt-out: a jury-off match stamps `subjective_mode="disabled"` (no judge attempted),
    distinct from `"missing"` (jury on, all judges failed), `"self_consistency"`
    (degraded), and `"panel"`. The leaderboard aggregates worst-visibility-wins
    (`missing > self_consistency > panel > disabled`).
  - **Legacy rows are inferred, not migrated** — pre-`subjective_mode` rows with a
    subjective score (in the breakdown or the top-level column) read as `"panel"`, so old
    successful juries never surface as outages. No DB migration; historical subjective
    data is interpreted on read and still shows on drilldown.
  - **UI** — the objective drilldown now renders in full for jury-off matches (it no
    longer collapses to the compact fallback when the judge block is absent); the
    leaderboard shows the Subjective column only for boards where the jury was intended,
    with a visible degraded/`—` marker for `"missing"` rows.
- **Arena judge fairness & scoring-methodology reform** — the LLM judge is confined to
  genuinely-subjective quality and de-biased; the leaderboard now ranks by the
  deterministic **objective** axis alone (spec `2026-07-05-arena-judge-fairness`).
  - **Judge rubric 6 → 2 points** (synthesis coherence + analytical correctness). The
    five deterministic-redundant points (staleness, numeric grounding, instruction
    adherence, trap handling, process) were re-grading — noisily — what the objective
    assertion checks already score, and were deleted from the judge.
  - **Jury, not a single judge** — `judge_panel` scores with a contestant-excluded panel
    of 3 diverse models (`deepseek-v4-pro` direct + `claude-opus-4.8` + `qwen3.7-max`),
    reporting **per-judge scores + stdev**. A ZenMux outage that drops the panel below
    `min_judges` escalates to a visibly-**degraded** `self_consistency` fallback (k
    samples of one judge), never a silent single judge.
  - **Separate axes, no blend** — the 50/50 total is dropped; `subjective` is advisory
    (`mean ± stdev` + mode) and never moves rank. Exact objective ties **share rank**
    (competition ranking), broken deterministically by sub-axis priority
    (grounding → adherence → synthesis → procedural), never by the subjective axis.
  - **Benchmark correctness (P0)** — the infra-contamination gate now treats a tool call
    followed by a provider-`402` on the final response as a partial death (was scored);
    the trap step uses a reserved set name the runner **asserts absent** at match setup
    (the old `liquidity-crunch` set actually existed, inverting the check); and the dead
    grounding paths (`hotspot.delta`, `landscape[spot_shift=0.1]`) are re-harvested from
    real payloads (`metrics.positions[position_id=8].delta`,
    `results.portfolio.raw[spot_shift_pct=10.0]` — percent units).
- **Arena flagship `risk-manager-control-day` rebuilt for discrimination** — 9 steps /
  **39 objective points** (was 7/32). New checks target the axes where frontier models
  actually differ: numeric grounding (`response_quotes_tool_value` — signed by default,
  label-anchored via `near`, magnitude-mode for loss language), report synthesis
  (`artifact_contains` coverage of hotspot/backtest/CVaR), a nonexistent-scenario-set
  **trap step** (verify via `list_scenario_library`, don't silently substitute), a
  grid-comprehension step answered from already-computed data (re-dispatch forbidden),
  and exact-args adherence (`tool_called` gains `args_any_of` + `exclusive_keys`;
  `_dig` gains `[key=value]` list selectors). Dead repeat-skill checks dropped
  (`expected_skill: null` skips the structurally-blind skills_routed point) and the
  5 duplicated session checks removed. Judge rubric rewritten with 0/50/100 anchors.
- **Arena scoring reports per-axis subtotals** — every objective check carries a
  derived axis (procedural / adherence / grounding / synthesis); `score_breakdown
  .objective.axes` totals render as a strip in the Arena match drill-down. Aggregate
  scoring stays flat +1 per check.
- **Infra-blank arena matches are now `invalid`, not zero** — an all-blank transcript
  *with* transport-error evidence records `status="invalid"` (`error="infra_blank"`),
  skips judge/scoring, is excluded from leaderboard means, and surfaces as an
  "N infra" chip plus per-match reason in the API (`MatchSummary.error`,
  leaderboard `invalid`) and Arena page. Blankness without error evidence still
  scores as a real 0 (a silent model is a model failure, not an infra failure).

### Added
- **Audit trail (dangerous-action log).** An always-on, append-only record of every
  write-class action an LLM agent takes — bookings, portfolio/RFQ writes, deletes,
  memory writes, async dispatches, file/artifact writes — **including actions taken
  in headless YOLO mode**, previously invisible outside the full trace log. Captured
  via `AuditTrailMiddleware` at the `wrap_tool_call` seam in all three agent stacks
  (orchestrator, personas, async agent); phase-1 (the attempt row) is **fail-closed**
  — it must commit before the tool executes, or the write is refused; secret-key and
  content-body redaction happens before any row is persisted. Human-in-the-loop
  actions form append-only proposal → decision → execution chains linked by a
  server-minted `audit_ref`. Read-only `/api/audit` API plus an **Audit** console
  page — search, status/class/mode filters, a detail view with the full action
  chain, and time-sorted, rows-per-page pagination (`TableToolbar`, matching
  Positions/Portfolios/Reports/Tasks). Migration `0043`.
- **Dynamic subagents (governed QuickJS fan-out) — pilot.** An opt-in execution
  substrate that lets the orchestrator fan a recurring desk workflow out to one
  read-only persona subagent per work item — via the deepagents `task()` global in a
  QuickJS sandbox (`CodeInterpreterMiddleware`, `subagents=True`) — then reconcile the
  results deterministically. Fan-out is **server-gated, never model-authorized**: an
  eval attribution gate (`EvalAttributionGateMiddleware`) rejects every `eval` unless
  the run carries server-stamped Case-3 attribution for an allowlisted `source='seed'`
  workflow; fanned-out subagents are **read-only** (writes/bookings/FS-writes blocked by
  capability group, so a non-idempotent re-dispatch on resume can't mutate); and coverage
  is **server-authoritative** — `assemble_breach_report` reconciles the fan-out records
  against a scope derived server-side from the launch args (every item gets exactly one
  record, uncovered → `failed`). Ships the seeded `morning-risk-breach-commentary`
  workflow, migrations `0040`/`0041`, and is **gated off by default**
  (`OPEN_OTC_AGENT_CODE_INTERPRETER=false`). Live-validated end-to-end on the direct
  DeepSeek channel — gate authorizes → `task()` fans out → subagents read → coverage
  reconciles.
- **Instant-messaging gateway (Feishu/Lark).** Drive the full desk agent from IM
  with web-desk parity — streaming **markdown** replies, human-in-the-loop
  Approve/Reject **cards** for bookings, pickable **reply-option** cards, and
  linking-code enrollment. An in-process subsystem behind a single `AgentBridge`
  over the agent service: `GatewayRuntime` (single-worker DB-lease election +
  heartbeat) → `MessageConnector` (Feishu WebSocket long-connection) → `Dispatcher`
  (at-least-once dedup, message + priority card-action lanes, per-chat
  serialization) → `StreamRenderer` (coalesced streaming, two-phase approval cards,
  mid-flight revocation, token-bucket rate limit). Endpoints `/api/gateway/*`
  (linking-codes, bindings, health, reload); migration `0037`; per-turn model via
  `GATEWAY_AGENT_MODEL` and card deep-links via `GATEWAY_WEB_BASE_URL`. Runs as a
  dedicated worker — **not** the `--reload` dev server (the lark WS client's
  blocking event loop runs on a daemon thread and does not survive hot-reloads).
- **Feishu connector — live `lark-oapi` integration.** Brought the WebSocket
  inbound path and outbound sends onto the real SDK (the prior shape was inferred
  and fake-tested only): a typed `EventDispatcherHandler` whose events are
  marshalled back to dicts and dispatched onto the server loop via
  `run_coroutine_threadsafe`; the blocking WS client run on a daemon thread with a
  rebound event loop; **schema-2.0** cards (buttons inside `body.elements` with
  `behaviors` callbacks, markdown-element text bubbles, no `note`); corrected
  `receive_id_type` / `message.patch` builders with surfaced API errors; cumulative
  in-place streaming; and a `done`-event enriched with `thread_id` +
  `pending_actions` + `reply_options` so IM connectors can render cards (the web UI
  ignores the extras). Non-blocking runtime startup; `GATEWAY_AGENT_MODEL` is a
  `.env`-loadable setting resolved explicit-arg → settings → env → registry default.
- **Agent Arena reports, released as HTML + PDF.** Each run's Markdown report now
  renders to a styled, self-contained HTML page and a print-quality PDF via
  [`docs/arena/render_report.py`](docs/arena/render_report.py) — now data-driven
  (per-report `*.charts.json` sidecar) so every new run is a drop-in. Reports are
  indexed in [`docs/arena/`](docs/arena/) and linked prominently from the README.
- **[Agent Arena — Run #9](docs/arena/2026-06-28-run9-otc-desk-agent-arena.md)** —
  the **flash tier**: nine fast/low-cost models × five trials, with **exact,
  measured per-match token consumption and cost** (the `stream_usage=True` capture
  now lands usage for OpenAI-gateway models). Gemini 3.5 Flash (59.1) ≈ Step 3.7
  Flash (57.9), but Step costs 1/14th as much — "flash" is a latency claim, not a
  price one. Adds the flash candidates to the arena model registry. Doubao is
  reported separately on two routes: `doubao-seed-evolving` was infrastructure-
  censored (0/5), and the sibling `doubao-seed-2.1-turbo` posted the highest
  *functional* score in the field (65.3) on just 2/5 completed trials — a dark
  horse, flagged and not placed.

### Changed
- **Flagship arena objective manifest is now 32 points (was 31).** Added a
  `tool_called` assertion on the `risk-manager-control-day` backtest step that
  verifies `run_backtest` is invoked with the instructed date window
  (`2026-03-24 → 2026-06-24`). Some models (Opus 4.8, Sonnet 5 — see GH #6)
  silently substitute a self-computed "past quarter" window; this scores that
  instruction-adherence failure explicitly rather than leaving it visible only in
  downstream P&L numbers. The full replay pin and denominator manifest tests were
  updated (7 skills + 10 tools + 9 step assertions + 6 success = 32).
- **Agent Desk composer chrome cleanup.** Removed the "Accounting" label from the
  global date picker to reclaim header space. Consecutive same-name tool calls in
  the chat tool timeline now collapse into one grouped row (`read_file ×14`). The
  `Detailed | Compact` view-mode toggle and the `Interactive | AUTO | YOLO`
  execution-mode buttons moved from the page header into the composer actions row
  next to Send, and both were converted into compact inline pickers.
- **App shell scrolling.** The sidebar and main content area now scroll
  independently; the shell is locked to the viewport height so long menu or page
  content no longer scrolls the entire window.
- **Arena leaderboard is scoped to the selected run.** Choosing a run now
  fetches `GET /api/arena/leaderboard?run_id={id}` and updates the leaderboard
  panel title to show the active run; the global leaderboard remains shown when
  no run is selected. The leaderboard is now rendered with the shared `Table`
  primitive so borders, row height, header styling, and numeric alignment match
  the rest of the desk.

### Fixed
- **Headless (YOLO) agents stalled in prose on expensive actions instead of
  executing.** In headless mode the persona/orchestrator prompts still carried the
  cost-preview rule ("reply with a cost preview and wait for the user's yes; do
  not invoke this turn"), which directly contradicts headless operation ("never
  ask, proceed"). Cautious instruction-followers honored the more conservative
  directive and ended the turn with an unanswered question — no user answers, and
  the runtime cost-HITL is already auto-confirmed in headless mode
  (`confirmed_cost_preview=True`), so nothing intercepted it. Sonnet 5 was the
  clear outlier (9 stall steps across 4/5 Arena trials vs ≤7 for others). Fixed by
  resolving the policy conflict, not by adding a model-callable bypass (which would
  violate the server-owns-authorization invariant): `_resolve_policy_fragments`
  now drops `cost-preview-policy` in headless mode, and `headless-policy` gained an
  explicit "Expensive actions in headless mode" section (run inline <~30s; dispatch
  async ≥30s; never ask which format/scope/profile) that also overrides the
  orchestrator.md embedded Cost-preview rule. Backtest date-window bug found in the
  same investigation filed separately as #6.
- **Persona subagents blocked on "missing required scope" when the scope was
  supplied.** The interactive orchestrator delegates to persona subagents via the
  deepagents `task()` tool, which passes only a prose prompt — no context pack. A
  delegated skill declaring `required_context` (portfolio_id, pricing profile,
  dates) with `confirmation_required` then refused with "not in the task/context
  pack" even though the id was stated verbatim in the delegation, because
  `assemble_context_pack` runs only in the async executor path. Fixed with two
  layers: (1) `DeskContextMiddleware` (on orchestrator + personas) snoops resolved
  scope from domain-tool call args into a `desk_context` state key that propagates
  parent→subagent (deepagents keeps non-excluded state) and persists across turns,
  then injects it as an authoritative context block into the subagent prompt; and
  (2) a `delegated-scope-policy` meta fragment telling personas that
  orchestrator-supplied scope satisfies `required_context` and the delegation is
  the confirmation. Live-validated on Claude Sonnet 5 (5 trials): the scope block
  is eliminated (`run_scenario_test` 5/5, `run_backtest` 4/5 vs 2/5 pre-fix),
  objective mean 70.3→82.6. Both middleware hooks fail open.
- **Arena objective scoring dropped every async task id and text artifact.** The
  pre-fix trace harvester stored each tool result as the raw LangChain v3
  lc-constructor `ToolMessage` envelope (`{lc,type,id,kwargs}`); the real payload
  (with `task_id` / embedded artifacts) is a JSON string at `kwargs.content`, so the
  assertion engine's `content.get("task_id")` always read `None`. Every
  `task_returned_id` and `artifact_exists` check failed **identically across all
  models** even though the tools returned the ids. The unwrap fix already landed in
  the harvester (`_parse_tool_output`); this re-scores the affected historical
  `risk-manager-control-day` matches (runs 1–9) from their persisted transcripts —
  no LLM re-run — recovering ~5 checks per faithful run.
- **Arena `skills_routed_sequence` was blind to repeat-routing.** `skills_routed` is
  harvested only from `read_file`-on-`SKILL.md` spans, and the agent runtime never
  re-opens an already-loaded file — so a legitimate second `read-risk-result` step
  (or any description-only routing) was invisible, failing the ordering check on
  noise uncorrelated with ability (same model passed/failed across reruns). Added a
  `tools_routed_sequence` assertion that measures the same designed step order on the
  fully-captured tool-call sequence (each skill → its signature tool) via the
  existing `match_tools_subsequence`; migrated **all three** golden workflows
  (`risk-manager-control-day`, `trader-rfq-booking-day`, `high-board-portfolio-review-day`)
  to it — the latter two each satisfy the new check on their golden replay (100%),
  and `trader-rfq` has a legitimately repeated skill (`position-snapshot`, backed by a
  different tool each time) that the old read_file-based check could not observe. Same
  strict bar (skip/reorder still fails — genuine non-followers stay failing), minus
  the dedup blind spot.
  Wrapped `HedgeStrategyLive` in a flex column with `gap: var(--gap-3)` so the
  Solve/Book hedge buttons are separated from the risk-run exposure message.
- **IM gateway dropped every inbound user turn from the transcript.**
  `AgentBridge.submit_turn` called `AgentService.stream_and_persist` — which only
  persists the *assistant* reply and assumes the caller already inserted the
  `role="user"` message (as the HTTP `/chat` endpoint and the arena runner both
  do) — but the bridge skipped that step. IM-originated user messages therefore
  never landed in `agent_messages`: the chat panel showed only assistant replies,
  and the routed-stream turn could not attach its route to the latest user row.
  The bridge now persists the user turn in its own committed transaction before
  streaming, mirroring the other two callers.

### In progress
- Additional **long-workflow match designs** for the Agent Arena.

## [0.1.0] — 2026-06-27

Initial public snapshot: an AI-native trading desk for structured equity
derivatives, pairing the deterministic [QuantArk](https://github.com/deiiiiii93/quant-ark)
quant engine with LLM-powered agents.

### Added — Desk & agents
- **Conversational desk** — LangGraph agents that take a natural-language brief and
  call deterministic tools for pricing, risk, hedging, and booking, streaming
  token-by-token with structured asset cards and charts.
- **Three-mode execution** — Interactive, AUTO, and headless YOLO regimes; the
  Arena drives the headless path with HITL gates auto-cleared and the deferral tool
  withheld.
- **Human-in-the-loop booking** — positions and hedges require explicit
  `Approve` / `Reject` confirmation before anything hits the book.
- **Goal mode** — a `/goal` lifecycle (`GoalContractV1` → ratify → grade-the-ledger
  → satisfy/escalate) with a ledger-grounded `RubricMiddleware` spliced into the
  orchestrator, a `frame_goal` model wrapper, and a `GOAL_GRADER_READ` tool
  allowlist. Surfaced in the composer slash menu.
- **Session tracing & audit** — append-only trace log (`LocalTracer` / `BaseTracer`)
  through a single `graph_run_config` chokepoint, with a `/tracing` viewer that
  renders LangChain payloads readably.
- **Composer** — keyboard navigation, colored command tokens, a slash picker with a
  reserved-command guard, and a multi-line overlay.

### Added — Pricing & products
- **Multi-engine Greeks** (analytical, Monte Carlo, PDE) via QuantArk across
  snowball, phoenix, autocall, sharkfin, Asian, digital, barrier, and vanilla
  families, with a position-first type→family engine-config variant map.
- **Unified product builders** — four intake channels collapse to a single
  `build_product` gate, with declarative family contracts, a cross-channel
  equivalence net, and a term-collection booking wizard.
- **Weighted Asian pricing** — trading-day calendars, an observation-frequency
  picker (three surfaces), and a full fixing lifecycle: materialize
  `observation_records` at booking → immutable close-only capture from
  `MarketQuote` → wire records into position pricing, plus `generate`/`capture`
  agent tools and an `asian-fixings` routing skill.
- **Booking pricing companion** — price unbooked terms (PV + Greeks) before commit
  via `POST /api/pricing/preview` and a Payoff | Pricing tab.
- **Batch pricing** — one `batch_pricing` task drives a combined `RiskRun` +
  `PositionValuationRun`.
- **Quote solver** — a try-solve panel with explicit range-value inputs and
  source-aware solver bounds.
- **Instrument unification** and **pricing-parameter tools** (11 agent tools +
  strict profile coverage), plus a Contract-Multiplier term field across families.

### Added — Risk, hedging & analysis
- **Portfolio risk** — aggregated Δ-cash / Γ / Vega / Theta in a single pass,
  sliced by underlying.
- **Hedging** — an instrument catalog/map, a MILP strategy solver that proposes and
  sizes Δ-neutral legs (e.g. index futures), an agent hedge-booking graph, and
  risk-hygiene tooling.
- **Scenario / stress testing** — a QuantArk stress-test bridge + runner +
  `ScenarioTestRun`, shocking spot, vol, and rates across the book, with custom
  scenarios and `(range, step)` grid scenario sets.
- **Backtesting** — portfolio hedging backtest (net-delta by underlying) with
  autocallable lifecycle replay.

### Added — Workflows
- **Golden workflows** — declarative desk-workflow definitions (schema models,
  loader/registry, assertion engine, fixture seed/replay) feeding a deterministic
  regression proof, anchored by the `risk-manager-control-day` flagship.
- **Desk Workflows module** — frontend-managed Python-script workflows (`DeskWorkflow`
  model, CRUD service/router, AST safety guard) with a restricted-exec auto-pilot
  runner over SSE, a bespoke LLM **Workflow Builder** (chat + live script preview),
  and typed `meta['params']` parameterized launch forms.

### Added — Agent Arena
- A controlled, repeated-trial benchmark that drives the **real** desk orchestrator
  end-to-end with no human in the loop, scoring each model against a 31-point
  objective manifest combined 50/50 with an LLM (GPT-5.5) judge.
- Model registry + ZenMux channel, an isolated subprocess match runner with
  blocking run-tools, reproducible scoring + per-match diagnosis,
  transcript-from-trace harvesting, a `/arena` leaderboard page, and an Agent Desk
  toggle to show/hide arena threads.
- Streaming token-usage capture for OpenAI-gateway models; candidate field grown to
  ten models (incl. Gemini 3.1 Pro).
- **[Run #8 report](docs/arena/2026-06-27-run8-otc-desk-agent-arena.md)** — ten
  models × five trials; Claude Opus 4.8 (66.4) ≈ GPT-5.5 (66.3), a statistical tie.

### Added — Clients, data & frontend
- **RFQ workflow** — three-column client intake + `/api/client/rfqs` with an
  internal approval pipeline and a catalog buildability net.
- **Market data** — AKShare adapter with caching and fallback for A-share / HK
  markets.
- **Frontend** — React 19 "Warm Ledger" design system with a UI style guide and a
  token-purity invariant (zero theme-blind colors), and a data-driven
  skill-management page (`/api/skills` CRUD) that hot-reloads agent routing.
- **Data masking + English import templates** — single-source `import_schema.py`
  with an idempotent `mask_brand_data.py` pass for shareable demos.

### Fixed — hardening
Most subsystems shipped through automated review loops (ZenMux GPT-5.5 standing in
for human review); the correctness work that landed includes:

- **Goal mode** — closed acceptance-gate invariant holes; rubric-injection guards
  (reject C1 / Unicode line separators, non-finite operands/thresholds); framer
  parse-error wrapping with `GoalRunStore` locking; and cross-thread goal-state race
  fixes across five review iterations.
- **Agent Arena** — tag-scoped, FK-safe purge-then-reseed (no real-data loss and no
  profile accumulation); settle queued background tasks between workflow steps;
  harvest LLM text from `AIMessage` content blocks and structured dict tool outputs;
  flush the trace before harvest; and deterministic latest-run ordering.
- **Asian fixings** — capture a print only on its exact date, close-only with a
  per-position row lock; idempotent schedule generation; and a documented fallback
  to full `num_observations` on a partial-uncaptured schedule.
- **Desk Workflows** — closed a `str.format` dunder-bypass in the script guard,
  cancel-on-disconnect, safe slugs, and self-contained migrations.
- **Booking & pricing** — scale pricing-preview PV/Greeks by signed quantity, reject
  mixed option-maturity terms, correct profile quote cutoff and futures cash Greeks,
  and prefill pricing inputs from the list endpoint.
- **UI** — auto-shrink KPI tile values to fit, and contain Agent Desk scroll to the
  conversation panel.

### Engineering
- **Alembic migrations** `0032`–`0036` (arena run/match, `agent_threads.source` +
  `arena_run_id`, desk-workflow, goal-run surfaces).
- **Resumable arena sweeps** — each match runs in an isolated subprocess under a hard
  `SIGKILL` wall-clock guard, with checkpoint-to-disk so a 50-match sweep survives
  restarts and intermittent gateway wedges.

[Unreleased]: https://github.com/deiiiiii93/open-otc-trading/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deiiiiii93/open-otc-trading/releases/tag/v0.1.0

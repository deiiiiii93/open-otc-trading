# Changelog

All notable changes to **Open OTC Trading** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Agent Desk composer chrome cleanup.** Removed the "Accounting" label from the
  global date picker to reclaim header space. Consecutive same-name tool calls in
  the chat tool timeline now collapse into one grouped row (`read_file ×14`). The
  `Detailed | Compact` view-mode toggle and the `Interactive | AUTO | YOLO`
  execution-mode buttons moved from the page header into the composer actions row
  next to Send, and both were converted into compact inline pickers.

### Fixed
- **Hedging page button bar sat flush against the bottom warning banner.**
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

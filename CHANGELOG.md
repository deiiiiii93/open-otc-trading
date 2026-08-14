# Changelog

All notable changes to **Open OTC Trading** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Arena: one model at several reasoning efforts, ranked on one board.** A
  contestant is now `(model, reasoning_effort)` rather than a model. The `/arena`
  New Run panel's per-model effort select became a **checkbox group** — tick `Low`
  *and* `High` and that model enters the board twice, as two rows that rank
  against each other; tick **Default** alongside a pinned level to measure a pin
  against the unpinned baseline every board through #104 actually ran at.
  `scripts/launch_arena_run.py --reasoning-effort` now takes several levels.

  `ArenaMatch` carries an explicit `reasoning_effort` (migration **0058**, `''` =
  unpinned) and its unique constraint grew to four columns — the old three-column
  key made the second arm collide with the first. `arena_run.reasoning_efforts`
  values became **lists of arms** (`{"gpt-5-5": [null, "high"]}`), with the legacy
  scalar read as a one-element list rather than migrated. The leaderboard ranks
  each arm separately, transcripts get a per-arm directory, and `--resume` tracks
  arms — it previously keyed on `(workflow, model)`, so a resume would see a pair
  as done because one arm finished, never run the other, and mark the run
  `completed`.

  **Behaviour change:** a cross-effort `merge_runs` used to fail with 400. It now
  succeeds, producing one merged row per arm. The fold that refusal protected
  against — a single row averaging two regimes' EFF/CON — is structurally
  impossible once effort is in the group key, and refusing would have made merge
  stricter than launch, which happily puts both arms in one run.
- **Arena run #104 — Grok 4.6 vs DeepSeek V4 Pro scorecard board.** All four
  golden workflows, 2 trials each (16 model-trials), objective-only, all 8 pairs
  `scored`. Report at `docs/arena/2026-08-13-run104-otc-desk-agent-arena.{md,html,pdf}`
  with ten generated ability cards in `docs/arena/cards/run104/`.

  Result: **Grok 4.6 OVR 80, DeepSeek V4 Pro OVR 79** — a one-point tie for
  opposite reasons. Grok 4.6 leads the objective axis 96.3 vs 90.9 and wins three
  of four workflows, but posts **EFF 12** against DeepSeek's 48, including a
  literal EFF 0 on two workflows and **243 tool calls against par 24** on the
  flagship (per-trial 355 and 131). The Grok 4.5 inversion — best capability,
  worst efficiency — is not fixed in 4.6; it is deeper.

  Deliberately **not merged** into runs #20/#33/#94/#101: DeepSeek changed the
  weights behind `deepseek/deepseek-v4-pro` on 2026-08-13, and `merge_runs` groups
  by `(workflow_id, model_id)`, so merging would fold two different models into one
  aggregate under a single id. `risk-limit-breach-day`'s manifest also moved 39 → 38
  points after that workflow's last board.
- **Explicit reasoning-effort choice, in the chat composer and on arena runs.**
  An **Effort** picker sits left of the composer's Mode picker, and the `/arena`
  New Run panel gained a **Reasoning effort** select; `scripts/launch_arena_run.py`
  takes `--reasoning-effort`. Both default to **Default (not pinned)**, which sends
  no `reasoning_effort` at all, so an untouched desk behaves exactly as before.

  **Levels are per-model and MEASURED.** `scripts/smoke_reasoning_efforts.py`
  live-probes every model on every level (214 calls, 2026-08-14) and
  `refresh_model_reasoning.py --merge-probe` folds the result into the snapshot.
  This exists because models.dev — the open registry OpenCode uses, and the original
  source here — proved wrong for **every route that mattered**, mostly **too
  narrow**: it claims `low/medium/high` for deepseek-v4-pro (really all 7),
  *toggle-only with no levels* for qwen3.7-plus and mimo-v2.5-pro (really all 7),
  *non-reasoning* for kimi-k2.7-code (really 6, and it refuses `none`), and
  `none…max` for the GPT-5.6 family which actually **rejects `minimal`**. Gating on
  it would have blocked working levels on **13 of 22** models.

  So the gate is only as strong as its evidence: a **measured** ladder rejects an
  off-ladder level; an **unmeasured or unknown** model is allowed through and the
  provider decides. That is safe because the gateway rejects an unsupported level
  itself with a precise message — the "silently accepted and ignored" risk that
  justified local gating does not hold for the reject case. ZenMux's own API cannot
  answer the question at all: it reports only `capabilities.reasoning: true|false`.

  **Both wire protocols carry effort, through different fields.** OpenAI-compatible
  models take top-level `reasoning_effort`; anthropic-routed models take
  **`output_config.effort`**, a named level — not the older `thinking.budget_tokens`
  budget. An earlier build refused every anthropic-routed model outright, which
  denied effort to **7 of the 8** models routed that way, four of them not Claude at
  all (`glm-5.2`, `minimax-m3`, `qwen3.7-max`, `longcat-2.0`). Probe that path with
  `smoke_reasoning_efforts.py --protocol anthropic`.

  Measured oddities worth knowing: `openai/chat-latest` accepts **only `medium`**;
  grok-4.5/4.6 reject `none` and `max`; gemini-2.5-pro rejects `none`;
  **claude-sonnet-4.6 rejects `xhigh`** while opus-4.8 and sonnet-5 accept it.
  **`claude-haiku-4.5` is the only model on either protocol that accepts no level at
  all** — it does not reason. Every one of the 29 arena contestants has at least one
  usable level.

  New: `config/model_reasoning.json` (snapshot, records the upstream sha256),
  `scripts/refresh_model_reasoning.py` (`--check` fails when stale), and
  `services/deep_agent/reasoning_capabilities.py`. **Vendored, never fetched at
  runtime** — the same rule as the exact `quantark` pin and harvested arena
  fixtures: third-party truth that governs behaviour arrives as a reviewable diff,
  not a silent change. The ladder is per **route**, not per model (models.dev has
  `deepseek-v4-pro` at `high,max` direct but `low,medium,high` via ZenMux), so
  lookups key on `(channel, model_id)`. A model **unknown** to the snapshot is
  permissive, never "unsupported" — models.dev lags releases (`x-ai/grok-4.6` has
  no ZenMux entry) and stale data must not block a working model.

  Both pickers filter to what the model accepts, so the UI cannot offer a level the
  server would reject: the composer from the selected model's ladder, and the arena
  New Run panel with **one picker per selected model** (hidden entirely for a model
  with no usable level). Both ladders are derived through the same
  `effort_rejection` seam `queue_arena_run` uses, so a picker can never disagree
  with what a launch accepts. Both **send** paths also re-filter before sending —
  the pickers' resets are display-only and the selection survives a model switch, so
  picking `high` and then switching to `glm-5.2` used to still send `high` and 422. Arena effort is therefore stored **per model** —
  `arena_run.reasoning_efforts`, a `{model_slug: effort}` map (migration **0057**,
  replacing 0056's single column) — because a scalar cannot express a pinned mixed
  board: the intersection across a real field is usually empty. `queue_arena_run`
  validates each entry against its own model at launch, and `merge_runs` compares
  effort per `(workflow, model)` group rather than per run, since a run may
  legitimately pin different models differently.

  Carried on the **model-selection dict**, where an unset effort is *omitted
  entirely* rather than sent as `null`. That is load-bearing: `AgentService` reuses
  its prebuilt orchestrator only when the resolved selection compares equal to
  `default_model_selection`, so a fourth key present on every turn would silently
  stop that reuse and rebuild the graph per request. Omitting it also makes a
  pinned selection unequal to the default by construction, which correctly forces
  the per-turn model build that applies the effort.

  An explicit effort for an **anthropic-protocol** model is **refused** (422), not
  dropped: `ChatAnthropic` has no `reasoning_effort` (it budgets thinking in
  tokens), so forwarding one would report an applied setting that never took
  effect. The string `"none"` is preserved as a real request ("skip reasoning") and
  is distinct from unset.

  Arena runs record the regime they ran at — `arena_run.reasoning_effort`
  (migration **0056**, NULL = unpinned) plus `arena_match.config.reasoning_effort`
  — so a board can prove which effort produced its EFF and CON. `merge_runs` now
  **refuses to fold runs with different efforts** (400): it groups by
  `(workflow_id, model_id)` only, so a mixed merge would average two operating
  regimes into one row — the same defect class as folding a model whose weights
  changed behind a stable id. `--resume` reuses the run's own stored effort rather
  than re-reading the flag.
- `OPEN_OTC_MODEL_REASONING_EFFORT` — process-wide fallback that sends
  `reasoning_effort` on the OpenAI-compatible model path. **Unset (the default)
  reproduces the original behaviour exactly**, so every prior board's semantics are
  unchanged. Now the *lowest*-precedence source: an explicit per-turn/per-run
  effort wins over it (explicit-arg → process-env → vendor default, the ladder the
  gateway bridge already documents). Its remaining use is sweeping a whole process
  for a controlled A/B without touching each caller.

  It exists because nothing in this repo pinned the knob. Contestant models are
  built with `model`/`api_key`/`base_url` alone — `model_factory` sets no
  `reasoning_effort`, no `temperature` and no `max_tokens` — and
  `ArenaModel.default_config` (`temperature: 0, max_tokens: 4096`) reads like a pin
  but is **dead for contestants**: `arena_model_to_selection` returns only
  `{channel, provider, model}`, and the dict's sole consumer,
  `arena/channel.py::build_zenmux_chat`, is imported nowhere in the live path.
  Every arena match from run #8 to #104 has used vendor-default sampling.

  Measured with it (runs **#107**/**#108**, DeepSeek V4 Flash ×
  `risk-limit-breach-day`, 2 trials per arm): `high` effort produced **~22% fewer**
  tool calls than `low` (24.5 vs 31.5, non-overlapping ranges). Effort is a real
  influence on EFF, penalising *low* effort — but far too small to explain run
  #104's 4× EFF gap, and both of that board's contestants measured at essentially
  their `high` default already.
- **Grok 4.6 registered as an arena contestant** — `x-ai/grok-4.6` in both
  `config/agent_channels.yaml` and the tracked `.example.yml`, plus slug
  `grok-4-6` in `CANDIDATE_MODELS`. Plain `provider: openai`, no `protocol:
  anthropic` override (verified by live smoke run #103, which scored 100.0 on
  `risk-limit-breach-day` with 107 real tool spans — a wire-protocol mismatch
  presents as *zero* tool calls and a prohibition-floor score, not an error).
- `scripts/launch_arena_run.py` — launches an arena run synchronously with no
  backend server, for detached long boards, and **resumes an interrupted one**
  (`--resume RUN_ID`). Note `queue_arena_run` returns `(run_id_int, task_run)`;
  the first element is an id, not an ORM object.

  Resume exists because a sustained network outage is *worse* than a crash here:
  `task._execute` catches per-trial exceptions and continues to the next pair, so
  when every call fails in seconds the loop sweeps all remaining pairs into
  `invalid` and marks the run `completed` — a quietly wrong board. The safe
  response to a known outage is to stop the process and resume afterwards.
  Resume re-runs every pair that is not `scored`, deleting that pair's stale
  non-scored rows first, and records into the **same** run — a board is one run,
  and resuming into a second run plus `merge_runs` would fold trials across two
  different network conditions, which is exactly what merging must never do.
- `scripts/generate_ability_cards.py` — generates FIFA-style Model Ability Card
  images for a run via GPT-Image-2 (`openai/gpt-image-2` over ZenMux). Fully
  generative: the image model draws the whole card including every number, and
  correctness is enforced by a **read-back gate** — a vision model reads each PNG
  and the card is rejected and re-rolled unless every digit matches the database
  and every meter shows exactly `round(value / 10)` lit segments out of ten.
  Hero cards use `leaderboard`'s `card_mean`, minis use each match's
  derive-on-read `card`; no score is recomputed.

  Stat meters are **ten countable segments, not continuous bars**. Measured over
  ten demo renders: the model reproduces quoted digits reliably (42/42 correct)
  but *interpolates* continuous lengths — an EFF of 36 drew at 42–60% of its
  track and flipped rank against its neighbour between two samples of one prompt.
  A countable instruction travels the same faithful path as the digits, and the
  verifier can count rectangles instead of measuring pixels.

  Trial counts on the card footer come from the breakdown's `n_trials`, **not**
  `arena_run.trials`: a merged board's run row carries the source run's trials
  (1) while every match in it is a folded multi-trial aggregate.
- **Lifecycle events for every bookable product family.** Three new event types —
  `exercise` (with an `early` flag, so American early exercise is distinguishable
  from exercise at expiry), `expire` (terminal, books **no** cash: it is how the
  system records "nothing owed, and we verified that" rather than leaving a
  permanent `needs_amount` phantom obligation), and `barrier_reset` for KO-reset
  snowballs. All 15 bookable families now have an explicit event map instead of 9
  of them falling through to `close`/`settle`/`custom`; notably
  `KnockOutResetSnowballOption` was a snowball with no knock-in, knock-out or
  coupon events, and the one-touch families had no way to record a touch.
- `GET /api/lifecycle-vocabulary` serves the event types, per-family allowlist,
  retired types and per-event form fields. The Positions lifecycle picker reads it
  instead of a hardcoded copy, which restores the `settle` and `fixing` options the
  UI had silently lost — `settle` is the only event carrying a settlement amount, so
  until now a human could not record one from the web app at all.
- Two structural guards (`tests/test_lifecycle_vocabulary.py`): every declared event
  type and every cash-leg rule must be reachable by some family or explicitly
  retired, and every family in the product-builder registry must have an event map.
  Both fail against the previous vocabulary.
- `record_lifecycle_event` agent tool — records any event the position's family
  allows, validating against the vocabulary rather than keeping its own list. The
  agent previously had tools for only 4 of the 17 event types, so `exercise`,
  `expire` and `barrier_reset` were reachable from REST and the UI but not from the
  desk agent. Found by smoking the agent path, which no test covered.
- **`record-lifecycle-event` skill** — the routing line that makes lifecycle
  recording a claimed desk workflow. No skill covered "something happened to a live
  trade" before, so the orchestrator had nothing to route to and improvised.

### Fixed
- **`open` and `reopen` were declared but allowed for no product**, so the `premium`
  settlement leg could never fire and no position had ever recorded its inception
  cash — the settlement module's documented "cash lifecycle is covered from
  inception" did not hold. `book_position` now emits an `open` event, so every new
  booking generates its premium cashflow. The two tests covering the premium leg
  passed only because they constructed `PositionLifecycleEvent` directly, bypassing
  the validation gate.
- `open` no longer moves position status. Making it reachable exposed that its
  `open` target silently overwrote the status of a position booked `knocked_in` or
  `closed` (a historical trade imported mid-life). `reopen` remains the transition.
- `mark_knockout`'s `payoff` argument now reaches the settlement cashflow. It was
  written to the event payload and read by nothing — `_SETTLEMENT_LEG` matched only
  `settlement_amount` — so an agent or desk user who correctly reported the KO payoff
  still got a `needs_amount` row and silently lost the figure. `payoff` is a fallback;
  an explicit `settlement_amount` still wins.
- Lifecycle tool inputs reject unknown arguments (`extra="forbid"`, matching
  `BookPositionInput`). Pydantic's default silently discarded them, so
  `mark_knockout(settlement_amount=900)` returned success having recorded no amount.
- `reopen` is refused while a non-terminal `settlement` cashflow exists. Reopening
  over a live settlement row meant the next `settle` was absorbed by the
  once-per-position guard and its amount silently dropped — the lifecycle log would
  say 650 while the blotter still said 500. The refusal mutates nothing and names the
  remedy; once the row is settled, voided or edited, the reopen legitimately earns a
  second settlement row. `reopen` was unreachable before this release, so no existing
  data can be affected.
- All four lifecycle approval cards — `close_position`, `settle_position`,
  `mark_knockout` and `record_lifecycle_event` — name the trade and the action instead
  of raw ids (`Close 1000.0 BarrierOption / 000905.SH (position #1, now open, reason:
  client unwind)`), so a human is no longer asked to approve two integers. They share
  one subject helper that degrades honestly on an unreadable position rather than
  throwing.
- **A model would not route to the new lifecycle tool.** A live three-probe smoke on
  the desk agent measured it recording both "expired worthless" and "exercised early"
  through `settle_position` — losing the `early` flag and the event type entirely, and
  booking a zero-amount cashflow the desk still has to release. The tool was
  available, allowlisted and correct; it was simply never chosen. Two fixes, measured
  separately: the single-event tools' descriptions now redirect to
  `record_lifecycle_event` for every other ending (this fixed the exercise probe), and
  the new `record-lifecycle-event` skill supplies the routing line the orchestrator
  needed (this fixed the expiry probe — the orchestrator was framing it as a
  "settlement" in its own delegation, before any persona saw a tool description). The
  event menu inside the tool description is **rendered from the vocabulary**, so a new
  event type reaches the agent surface with no edit to the tool module.

### Changed
- Phoenix records `knock_out`/`coupon_observation` instead of
  `autocall`/`coupon_lock`, so phoenix and snowball share one vocabulary. The two
  old types are **retired, not deleted**: existing rows keep projecting status and
  generating cash, but no new ones can be recorded. (The live database holds 0
  lifecycle events of any type, so this retirement disturbs no historical data.)
- Booking now writes a second audit row (`position.lifecycle_event` alongside
  `position.created`) for the `open` event.

### Added
- **Settlement module** — the cash implied by position lifecycle events, tracked
  and governed. Cashflows are auto-generated from `PositionLifecycleEvent`s by a
  pure, total deriver and worked through a `needs_amount → pending → released →
  settled` state machine (plus `blocked` / `void`) under optimistic concurrency,
  with an append-only transition log. The module **never computes payoffs**: an
  event either carries an amount or the cashflow is honestly `needs_amount`.
  Drift against the source event is **flagged, never silently applied** —
  `resync` is the only re-baseline path and it preserves a human override.
  Deterministic Markdown settlement notices (no LLM prose) with a frozen payload
  snapshot and sha256. New `/api/settlement` REST surface, 13 agent tools
  (`settle_settlement_cashflow` is HITL `irreversible`, the rest `write`), a
  routable `manage-settlement-cashflows` skill for the trader persona, and the
  **Settlement** nav page. Migration `0055`.
- **Arena per-model scorecards** — `scripts/render_scorecards.py` renders one
  outreach card per lab from a board run, combining the derived ability card,
  the specific checks that did not pass, the exact serving configuration, and
  the banked per-model trace. Targets and framing live in
  `docs/arena/scorecards/targets.yaml`; the rendering kernel
  (`backend/app/services/arena/scorecard.py`) is pure and tested against frozen
  run #94 fixtures, so the suite never touches the live DB. Cards fail honest:
  a missing card, a missing tool count, or an unbanked transcript renders as an
  explicit absence rather than a fabricated number or a dangling link, and a
  check without an explicit `passed: false` counts as unknown, never as a
  failure. `latest_transcript_paths` makes "latest re-run supersedes" explicit
  so a lab is never linked to a trace from a run already known to be invalid.
- **README engineering-notes section** — surfaces `CLAUDE.md`, the Arena
  reports, and the per-model scorecards from the front page.
- **Report module redesign** — reports are now generated from declarative YAML
  templates instead of a hardcoded writer. A server-owned **block registry**
  (18 producers across risk, P&L, limits, RFQ, positions and audit) resolves
  every number deterministically; the agent contributes **only narrative
  prose**, which a numeric grounding guard checks against that section's own
  data and flags when it quotes a figure the data does not contain. Block
  results are tri-state — `ok` / `empty` / `unavailable` — so "no limit is in
  breach" and "the limit check did not run" can never render alike. Four
  templates ship seeded: `trader-daily`, `risk-manager-daily`,
  `high-board-daily` (one narrating section, so one LLM call) and
  `portfolio-snapshot` (zero narrative, zero LLM calls). Template saves are
  validate-then-commit: a spec naming an unknown block, or a renderer
  incompatible with a block's shape, is rejected at 422 with the offending key
  named and the stored row byte-unchanged. Each generated report embeds its
  full template spec plus sha256, so editing a template never rewrites what an
  old report claims to have been generated from. New surfaces:
  `/api/reports/{blocks,templates,generate}`, six agent tools, and two routed
  skills (`generate-templated-report`, `author-report-template`).
  The Reports page is rebuilt — report list rail, natively rendered document
  with provenance and coverage in the header, and a Templates tab with a
  validating YAML editor. Export (HTML/XLSX/PDF) and Regenerate are
  deliberately **not** included; re-rendering from the stored document is a
  self-contained follow-on, and shipping it half-done would recreate the
  file-versus-screen divergence this redesign removes.
- **P&L producers (`backend/app/services/pnl/`)** — deterministic day-over-day
  producers for the report module: `snapshot_diff` (risk-run diff; elapsed time
  from `valuation_as_of`, never wall-clock, so two runs priced at the same
  valuation date carry zero theta P&L however far apart they were computed),
  `explain` (Greeks attribution — δ·ΔS + ½γ·ΔS² + ν·Δσ + θ·Δt + ρ·Δr + ρq·Δq —
  with unit multipliers read from the run's own `metric_contract` rather than
  hardcoded, start-of-period convention, and the residual surfaced rather than
  hidden), and `entry_price` (inception-P&L basis accounting that excludes and
  **counts** basis-less positions instead of reporting market value as P&L).
  Positions failing `pricing_ok`/`greeks_ok` in either run are excluded and
  listed with a reason, never zero-filled; an unrecognised `metric_contract`
  raises rather than guessing units. Nothing in the package touches an LLM.
- **Block contract types (`backend/app/services/reporting/contracts.py`)** —
  `BlockShape`, `BlockResult`, `BlockContext`. `BlockResult.status` is
  tri-state: `ok` / `empty` / `unavailable`, so "no breaches" and "the breach
  check did not run" can never render identically, and a non-`ok` result cannot
  be constructed without a reason.
- **Booking result card in chat.** The outcome of a confirmation booking is now rendered from
  a server-built structured record (`meta.booking_result`) instead of the assistant's prose:
  status, position id, product/underlying/size, destination portfolio, the **canonical booked
  terms**, and the provenance line (source confirmation file, counterparty, external ref).
  Refusals render too — `failed` / `already_booked` states name the reason and, for a dedup
  refusal, point at the position that already holds the trade. `book_trade` returns the
  payload on **every** exit path, `_capture_booking_result_from_tool_end` lifts it out of the
  tool **result** at `on_tool_end` (the reply-options/term-form captures read tool *args*,
  which for a booking are just `{portfolio_id, trade_id}` and say nothing about what was
  booked), and capture is keyed by tool **name** so an unrelated tool returning a `booking`
  key can't spoof a card. The record deliberately survives `reset_user_facing_output_for_retry`
  for the same reason tool events do: it reports a write that really happened.
  Capture lives in a `wrap_tool_call` middleware (`deep_agent/booking_capture.py`,
  registered in **all three** stacks beside `AuditTrailMiddleware`) rather than in a scan of
  the agent result's messages: personas run via `task()` in their own LangGraph checkpoint
  namespace, so a booking they make never enters the orchestrator's `result["messages"]`
  (measured on a live gated run: the tool appeared in 28 subagent checkpoints and **0**
  orchestrator ones). Three live-only defects were found and fixed getting this working —
  see Fixed.
- **Confirmations: visible parsing progress.** Parsing is dispatched asynchronously and
  documents are worked **sequentially**, so the old UI went silent exactly when the slow
  part started — the Upload button flipped back the moment the batch row existed, leaving
  only a static status badge while two LLM stages (plus a vision render for scanned pages)
  ran per document. The page now shows: a pulsing live marker on every non-terminal badge
  (document cards *and* the batch list), a progress banner above the document cards with a
  determinate bar and the name of the file being read, per-document copy that distinguishes
  **queued** from **actively parsing**, and shimmer placeholders (the shared `Skeleton`
  primitive) standing in for the trade rows about to arrive. `parseProgressLabel` counts
  parsed and failed **separately** — a bare "3 of 5" would otherwise hide that two blew up —
  and a clean finish clears the line while a batch that lost a document keeps reporting it.
  Accessibility: `role="progressbar"` with live counts, `aria-live="polite"` announcements,
  and `aria-busy` on in-flight document cards. `IN_FLIGHT_DOC_STATUSES` is now exported from
  `Confirmations.tsx` and consumed by the poll loop in `Confirmations.live.tsx`, so the
  indicator and the polling can't disagree about what "still parsing" means.

### Fixed
- **`0051`'s downgrade could not run on a create_all-seeded database.** A create_all DB
  hands `pricing_parameter_rows.position_id` the foreign key its ORM model declares — one
  a real historical 0051 never created — and SQLite refuses
  `ALTER TABLE ... DROP COLUMN` while any FK definition names the column, so the direct
  `op.drop_column` died with *unknown column "position_id" in foreign key definition*.
  It now uses `op.batch_alter_table`, which rebuilds the table and removes the column and
  its FK together; the default `recreate="auto"` suffices, because alembic recreates for
  any op outside `add_column`/`create_index`/`drop_index` regardless of SQLite version.
  `0051` was the only direct-drop site — the eleven batch sites were already correct.
  New `tests/test_migration_0051_position_id.py` pins what a table rebuild actually risks:
  the two sibling foreign keys, the other four indexes, and the row data all survive, and
  the migration round-trips. `alembic downgrade 0055 → 0050` and back now works.
- **`alembic upgrade head` could not reach head on an empty database.** `0001_initial`
  materialises live ORM metadata via `Base.metadata.create_all()`, so a fresh database
  arrives at revision 1 already carrying *today's* full schema — which makes idempotent
  DDL a hard invariant for every migration after it, not a style preference. `0005` and
  `0052` honour that with existence guards; `0051`, `0053` and `0055` did not, so the
  chain died at `0051` with `duplicate column name: position_id` and the documented
  empty-database path was unusable. All three now guard their DDL on current schema
  state. `tests/test_migration_fresh_chain.py` was already asserting this and had been
  failing; it is the standing guard against the next unguarded migration.
- **Four tests asserted against moving targets and had to fail as the repo grew.**
  `test_migration_0046` and `test_migration_0047` froze the literal
  `"0049_hedge_booking_claim"` as a stand-in for *head*, so every added migration broke
  them; 0046 now asks `ScriptDirectory` for head, and 0047 targets the migration it
  actually tests (its `_old_0046_engine` builds a deliberately narrow five-table
  database that cannot satisfy later migrations — `0050`'s `ALTER TABLE instruments`
  is the first to need one of the missing tables). `test_migration_0024`'s
  hand-maintained "columns added after 0024" allowlist had not been updated for `0050`
  or `0051`; it now also derives the foreign-key and index exclusions *from* that column
  list rather than keeping three lists in sync by hand.
- **The test suite is now hermetic against a developer's `.env`.** `Settings` is a
  dataclass whose field defaults call `_read_environment_settings()`, so **every**
  `Settings()` read the repo-root `.env` — and on a configured machine that made eight
  tests fail (gateway config defaults, `trace_db_path`, a gateway identity case) while
  the identical code passed in CI and in a fresh worktree. `CLAUDE.md` had documented
  the workaround ("validate those in a no-`.env` environment") rather than fixing it,
  which meant a real developer's suite was permanently red and the red was expected.
  Worse, `channel_registry.load_from_path` called `load_dotenv(..., override=True)`,
  **writing `.env` into `os.environ`** and republishing it over the values conftest had
  pinned — for every test that ran afterwards, making the failure set order-dependent.
  Both readers now resolve through a single `app.config.dotenv_path()` seam honouring
  `OPEN_OTC_ENV_FILE`; `tests/conftest.py` sets it empty ("no dotenv at all") before the
  first `app` import. Production behaviour is unchanged when the variable is unset —
  both call sites resolve to the same repo-root `.env` they always did. Verified by
  running the full suite both with and without a `.env` in place: 4454 passed either way.
- **Twelve frontend tests asserted raw numeric values against thousand-separated
  inputs.** `NumberInput` renders a value needing a separator as formatted TEXT
  (`8359.56` → `"8,359.56"`, `type="number"` → `type="text"`), and
  `useThousandSeparator()` deliberately defaults **on** when no provider is mounted —
  which is every bare unit-test render. So `toHaveValue(8359.56)` compared a number
  against `"8,359.56"`, while values under 1000 kept passing, making the breakage look
  arbitrary. Those tests are about prefill and data flow, not presentation (formatting
  has its own coverage in `NumberInput.test.tsx`), so they now use a
  formatting-agnostic `expectNumericValue` helper in `src/test-setup.ts`.
- **Removed a Booking test for a flow the page does not implement.**
  `Booking.live.test.tsx > sends package components as top-level product components`
  clicked an "Add Row" button that never renders: `ProductTermsForm` shows the
  record-array editor only for keys already present in `product_kwargs`, and no entry
  in `PRODUCT_TYPES` declares a `components` field, so a fresh booking form cannot
  introduce one. **The Booking page cannot originate a package position** — a product
  gap, not a test bug. The contract it meant to cover is already covered through the
  path that exists (`PositionEditForm.test.tsx > submits package components as
  top-level product components`), so no coverage was lost.
- **Three registry tests asserted on the contents of a gitignored, per-environment
  file.** `test_agent_channels_router`, `test_agent_registry_config` and
  `test_channel_registry_writer` sourced the live `config/agent_channels.yaml` and
  hardcoded `zenmux` as the channel holding the default. That file is per-environment
  by design and the Model Maintenance UI rewrites it at runtime, so the tests failed on
  any checkout whose default had moved (here, to `deepseek`). They were hermetic against
  the `AGENT_CHANNELS_FILE` env var but not against the file's contents. All three now
  source the **tracked** `config/agent_channels.example.yml`.
- **`scenario.latest_grid` would have reported a populated stress run as `empty`.**
  The producer read `results["rows"]`/`results["cells"]`, but the scenario runner
  persists `shape_results(...)`, whose grid lives under **`"scenarios"`** — so the
  moment a real stress run landed, the block would have answered *"the scenario run
  recorded no result rows"* with a full grid sitting in the row. That is a false
  `empty`: `unavailable` says nobody checked, `empty` says **we checked and the book
  is clean**, and the report would have claimed a stress test it never read. Now reads
  `"scenarios"`, projects each entry to the scalar columns a rows renderer can display
  (nested `greeks`/`position_results` stringify to `[object Object]`), and returns
  **`unavailable`** — not `empty` — for a payload it cannot recognise, so a failed key
  lookup can no longer fall through to the reassuring branch. The gap survived review
  because the only test covered the no-runs branch; the populated path was never
  exercised, and the fixture invented `{"rows": ...}` rather than harvesting the real
  producer's keys.
- **The grounding guard flagged "5 basis points" and "3 month tenor" as invented.**
  The shared arena tokenizer (`_scan_numeric_tokens`) applies its `k`/`m`/`mm`/`bn`/`b`
  magnitude suffix with **no word boundary after it**, so it read those as `5e9` and
  `3e6` — and it emits only the scaled reading, leaving a correctly-quoted `5` no way
  to ground. The tokenizer itself is deliberately **unchanged**: `_quote_value_report`
  matches on *any* reading, so widening it would make arena grading strictly more
  lenient across every stored board. The correction lives in `grounding.py` and
  **replaces** the misparse rather than adding an alternative reading — there is no
  suffix, so the scaled value is simply wrong. Decided on the whole trailing word, since
  a per-character rule backtracks `1.2bn` to a `b` suffix followed by the "letter" `n`;
  genuine magnitudes (`1.2bn`, `5 million`) are untouched.
- **AUTO mode booked confirmation trades with no human in the loop.** `book_extracted_trade`
  was classified `"write"` in `_RISK_LEVEL_BY_TOOL`, and `interrupt_on_config(yolo_mode=True)`
  strips every `"write"`-level tool from the interrupt map — so in AUTO mode the tool booked a
  real position straight off a parsed PDF and no approval card was ever raised (observed live:
  thread 682 booked position 27, `pending_actions: []`, `interrupt_ids: []`). It is the odd one
  out: `book_position`, `book_rfq_to_position` and `book_hedge` are all `"irreversible"`, and
  `book_extracted_trade` persists through the very same `book_position` gate — positions have
  no delete. Now `"irreversible"`, matching AUTO mode's documented contract ("ordinary write
  confirmations are bypassed, but irreversible operations stay gated") and the identical
  argument already made in-file for `register_underlying`. Headless/YOLO is unchanged (it
  clears all HITL by design), as is the Confirmations page's own Book button (REST, not the
  tool). Added `_summarize_book_extracted_trade` so the approval card states the product,
  size, counterparty and destination rather than opaque ids — the interrupt fires before the
  tool body runs, so without it the gate would be theater.
- **HITL approval cards showed raw boilerplate instead of the registered summary.**
  `_summary_for` returned `action_request["description"]` before consulting
  `_SUMMARY_BUILDERS`, and `HumanInTheLoopMiddleware` stamps a generic description
  ("Tool execution requires approval\n\nTool: …\nArgs: {…}") on **every** request — so the
  whole builder table was unreachable and `book_position` / `register_underlying` had been
  showing raw ids for their entire existence (verified against historical `pending_actions`).
  A registered builder now wins over the middleware's boilerplate. Unit tests missed it
  because they called the builders directly, or passed an action request with no description
  at all: satisfiability, not reachability.
- **The booking card never appeared for the writes that were actually gated.** Three
  successive live-only defects, none reachable from unit tests: (1) the HITL **resume** path
  is non-streaming (`agent.invoke`), so `on_tool_end` never fires and the streaming capture
  was dead there; (2) the run key — `configurable["thread_id"]` is the *checkpointer* key,
  not the AgentThread id the persist path looks up, and inside a persona it is neither, so
  the record was filed under a key nobody read; (3) the decisive one — the tool result body
  is **not plain JSON** by the time the seam sees it, because `GroundTruthArtifactMiddleware`
  runs inside this middleware and appends `<artifact_ref>{…}</artifact_ref>`, so `json.loads`
  raised "Extra data" and the payload was dropped while the content looked perfectly valid in
  a repr. Parsing now uses `raw_decode` (leading value only) and tolerates content-block
  lists and `Command` results.
- **A completed booking could be hidden entirely by the chat reasoning fold.**
  `ChatBubble`'s `findPublicContentBoundary` only recognises a heading containing
  `portfolio|report|summary|risk|hedg|snapshot|recommendation`; a booking reply's headings
  ("Parsed product terms", "Booking", "Completeness verdict") match none, so with ≥5 tool
  events and prose naming its own tools the whole 7 KB message folded into "Reasoning" with an
  empty body. The new booking card renders outside that heuristic and is suppressed by neither
  streaming nor a pending action, so an irreversible write is never visible only by chance.
- **Booking now refuses an underlying that is not a bookable instrument.** A confirmation
  names the issuer in legal form ("Apple Inc."), but positions, pricing and risk key off the
  instrument **symbol** — and nothing checked. `validate_trade_terms` only asserted the field
  was non-empty, so `Apple Inc.` was stored `valid`, and `book_position` →
  `link_position_underlying` → `ensure_underlying` then **minted** an `Instrument` for it
  (deriving nonsense along the way: `akshare_symbol("Apple Inc.")` splits on the dot in "Inc."
  and yields `"Apple Inc"`). The position booked "successfully" and could never be priced,
  hedged or risk-checked. New `resolve_bookable_underlying` (`services/instruments.py`) is the
  shared gate: it resolves only against instruments that are **`status="active"` AND tagged
  `"underlying"`**, returns the canonical stored spelling (so `aapl` books as `AAPL`), and
  never rewrites the caller's value — an unrecognised name is reported, not guessed. It
  distinguishes `unknown` ("no such instrument" — register it and retry) from `not_bookable`
  ("exists but is draft/untagged" — retrying can never help) and names the actual blocker.
  Candidate suggestions scan the confirmation's own evidence quote, so
  `"Shares: Apple Inc. (Ticker: AAPL)"` yields *"did you mean AAPL?"* instead of a dead end.
  Wired into `confirmations.validate_trade_terms` (hence the Confirmations review screen **and**
  the agent's `book_extracted_trade`, which share it, and `book_trade`, which re-validates) and
  into `book_position_tool`, whose `underlying_not_registered` error now carries
  `reason`/`message`/`candidates`.
- **`is_registered_underlying` ignored instrument status.** It returned
  `"underlying" in row.tags` without checking `status`, so the gate `book_position` and
  `book_hedge` already relied on would have accepted a `draft` or `retired` instrument. It now
  requires active + tagged via the shared `is_bookable_underlying_row` predicate, and matches
  the symbol case-insensitively. No-op on current data; two test helpers were creating `draft`
  instruments (the model default) and depending on the old behaviour.
- **The extractor was never told to return a ticker.** The stage-2 prompt asked for
  `"underlying": <string or null>` with no symbol-vs-name guidance, so the model faithfully
  returned the legal name even when the ticker sat in the same sentence. It now asks for the
  exchange ticker / market symbol explicitly, with the "Apple Inc. (Ticker: AAPL)" case spelled
  out, and falls back to the legal name (cited in evidence) only when the document shows no
  symbol at all.
- **Confirmations: the batch list clipped its Docs and Status columns.** The row declared
  25rem of *fixed* grid track (`4rem` id + `11rem` created + `6rem` source + `4rem` docs)
  before the status column got a say, inside a `minmax(280px, 380px)` rail — fixed tracks
  don't shrink, so the last two columns fell off the panel edge and were never visible. The
  row is now `2.75rem` + `1fr` + `2.5rem` + `1.1fr` (≈5rem fixed), carries one **headline**
  badge per batch via `batchStatusMark` instead of one badge per document status, and shows a
  rail-width timestamp; the full timestamp and the batch source moved into the cell's `title`.
- **Confirmations: a batch that was not selected froze mid-parse.** The poll refreshed only
  the selected batch, so uploading a second batch and switching away left the first one's
  status stale until a manual Refresh. Polling now watches **every** batch and refreshes the
  list (which already carries documents and trades), so all rows advance together and the
  loop still stops by itself once nothing is in flight. Because the list response is a strict
  superset of the batch-detail response, `onRefresh` no longer fires both requests, which in
  turn let the two stale-response guards in `Confirmations.live.tsx` collapse into one
  shared newest-dispatch-wins counter.
- **Trade confirmation → book** — upload counterparty confirmation documents (PDF/DOCX,
  including scanned/image-only pages) and turn them into booked positions. New
  `services/confirmations/` package (`extract` → `llm` → `service`) does text-first
  extraction with a **vision fallback** for image pages (`pypdf` + `pypdfium2`), then a
  two-stage LLM pass — segment the document into trades, then fill each family's legal
  term schema from `get_product_term_schema` with per-field `{quote, page}` evidence.
  Every extraction is re-validated through `prepare_booking_product_spec`, and **nothing
  books without a human**: the web review screen or the agent's HITL card. Booking reuses
  the existing `book_position` gate with `source_trade_id` idempotency
  (`external_trade_id`, else `conf:{sha256[:12]}:{seq}`), so re-booking the same
  confirmation reports `already_booked` instead of duplicating.
  Surfaces: REST `/api/confirmations` (upload / list / detail / edit / book / reject,
  async parse via `TaskRun`), the **Confirmations** nav page (multi-file upload, per-document
  status, per-trade review with evidence quotes and inline validation errors), and three
  agent tools — `parse_trade_confirmation`, `get_confirmation_batch`, and the HITL-gated
  `book_extracted_trade` — routed by the new `positions/book-trade-confirmation` skill.
  Chat attachments (`POST /api/chat/uploads` + a paperclip in the composer) let the
  **Desk Agent and the Pet mini-chat** take confirmation files directly. Model routing uses
  a dedicated `confirmation_extractor` registry tag (vision-capable, two-tier fallback to
  `fast`). Migration `0052`; new deps `pypdf`, `pypdfium2`.
- **`openai/gpt-5.6-sol` registered as an arena contestant** (the three sites per the
  doubao precedent: `CANDIDATE_MODELS`, live `agent_channels.yaml`, tracked
  `.example.yml`; tags `[tool-use, reasoning]`, not `fast`). Added for the Run #94
  report's family A/B: Sol ran the high-board manifest as run #95 and posted a perfect
  **objective 100.0 in both trials** (35/35, 45+30 calls vs par 24 → EFF 43, OVR 85),
  hitting the same step-7 filtered-`list_reports` empty result as Terra and recovering
  via a 19-call artifact/grep/registry hunt — refuting the "OpenAI turned the family
  down" reading of Terra's #7 finish and isolating Terra's one-shot-lookup policy as
  variant-specific, not family-wide. Live tool-call-id probe clean (`ChatOpenAI`, no
  protocol pin).
- **Run #94 report** — `docs/arena/2026-07-29-run94-otc-desk-agent-arena.{md,charts.json,html,pdf}`:
  the high-board board (Gemini 3.6 Flash posts the arena's first perfect card; the
  OpenAI pair inverts), the signal-migration analysis across the three flagships
  (median calls/par 1.50/1.74/**0.96**; EFF rank-correlation 0.63/0.66→0.44 while SYN
  goes to σ28.3/ρ**0.87**), the step-7 evidence-persistence taxonomy, and the Sol A/B.
- **Limits agent tools (9) — the desk agent can now work the governed Limits module.**
  Four reads (`list_risk_limits`, `get_limit_monitoring_run`, `list_limit_incidents`,
  `get_limit_incident`) and five HITL writes (`run_limit_monitoring`,
  `acknowledge_limit_incident`, `comment_limit_incident`, `waive_limit_incident`,
  `resolve_limit_incident`) in `backend/app/tools/limits.py`. Incident mutations
  preserve optimistic concurrency (`expected_row_version` from a preceding read;
  conflicts return a structured retry hint). `run_limit_monitoring` implements the
  module's refresh-then-reuse evidence contract via the new
  `services/limits/agent_support.py::derive_monitoring_envelope` seam — it reuses
  the latest completed risk run's profile/engine/evidence/valuation identity, so
  verifying limits after a book change requires a fresh `run_batch_pricing` first.
- **`limits` skill domain for risk_manager** — `monitor-limits` and
  `handle-limit-incident` workflow skills with orchestrator routing lines.
- **Arena golden workflow `risk-limit-breach-day` (39 points, uncalibrated par).**
  A risk manager works an overnight portfolio net-delta cap breach to verified
  closure: triage → driver analysis → acknowledge/comment → governance report →
  waiver probe (session-wide `waive` ban) → refresh-then-re-monitor → verify the
  incident auto-recovered (with a prohibition on redundant `resolve`). Seven new
  fixture namespaces (limits family; `risk_limits` seeded ensure-by-key with an
  `arena-` reserved prefix — those rows are protected-immortal), a
  foreign-active-limit match-setup guard in the arena runner, determinism
  producers (`breach_risk`/`fresh_risk`/`monitoring`) and a harvested
  `truth.json` for all graded numbers.

### Changed
- **`create_report` now routes through the seeded `portfolio-snapshot` template.**
  The hardcoded HTML/XLSX writer (`_write_html` / `_write_xlsx` /
  `_build_report_payload`, `services/reports.py` 391 → 193 lines) is deleted, so
  there is exactly one report writer. The **tool name is retained deliberately**:
  it is a graded `tool_not_called` prohibition in three golden workflows, and
  deleting it would make four checks trivially always-pass — inflating scores
  and breaking comparability with eleven boards of arena history. Reports no
  longer write artifact files at generation time (`artifact_paths` is `{}`).
- `PERSONA_WORKFLOW_DOMAINS["trader"]` now includes `reporting`. A template's
  persona narrates it, so a `persona: trader` template dispatched the trader
  persona, which could not see the reporting domain at all — making its report
  skills unroutable. Trader's workflow catalog grows 27 → 32.
- `PnlAttribution` is renamed **`GreeksByPosition`**; it renders a Greeks table,
  not a P&L decomposition. The real attribution waterfall now exists, and two
  components named for the same thing would be a trap.
- **`risk-limit-breach-day` step 2 no longer grades a skill (39 → 38 points).** The step
  declared `expected_skill: read-risk-result`, but that skill ships no `routing:`
  frontmatter, so `collect_routing_rows` skips it and it never reaches the orchestrator's
  Known-skills table — it is reachable only through the persona catalog. Run #101
  (18 models × 2 trials) measured the consequence and it reproduces the Run-#58
  discoverability class exactly: **read-risk-result routed in 4/36 trials (11%)** while
  every ROUTED skill on the same board scored 75-97% (`run-risk` 75, `monitor-limits` 92,
  `handle-limit-incident` 97, `generate-report` 94). Decisively, the step is performed
  correctly WITHOUT the skill — **18/18 models called `get_latest_risk_run` and 36/36
  trials recorded both answer fields** — so the check graded document-loading with zero
  correlation to outcome. Adding a routing line was rejected instead: `read-risk-result`
  is also the flagship's step-1 skill (`risk-manager-control-day`), so making it routable
  would shift flagship difficulty across 11 boards of history. Effect on ranking is
  negligible (Spearman 0.996; only two adjacent swaps between equal-OVR rows), but PRC
  rises 4-6 points board-wide — the signature of a check that was depressing every
  model's procedural denominator equally. Replay still earns full marks (now 38/38);
  run #101's stored board is UNCHANGED because axes are persisted in the breakdown (only
  `par` derives on read).

### Fixed
- **`tool_result_path` honours `scope: session` — a manifest could silently set it and
  be ignored.** `_ToolResultPath` was the only result-reading assertion without a
  `scope` field (`response_quotes_tool_value`, `response_quotes_value`, and
  `tool_result_ratio` all had one), and the assertion models do not set
  `extra="forbid"`, so a manifest's `scope: session` was dropped by pydantic and
  silently scored as `scope: step`. The scoring loop was already generically
  scope-aware (`scoring.py` builds a cumulative-results context), so adding the field
  is the whole fix; the default stays `step`, leaving every existing manifest
  byte-identical in behaviour. `risk-limit-breach-day` step 7 opts in: a model that
  read the incident at the end of step 6 (post-recovery, therefore genuinely fresh)
  and answered `recovered` from it is now credited, while a model whose most recent
  read still showed an open incident still fails. Run #101 evidence: claude-sonnet-5
  answered `recovered` citing a real `resolved_at`/`row_version 4` and lost the
  grounding point purely for not redundantly re-calling the tool — over-execution is
  ADH's and EFF's job, not GRD's. Replay still earns 39/39; point count unchanged.
- **Every arena trial's transcript survives — trial N was overwriting trial N-1.**
  `_save_transcript` took no trial index and always wrote
  `<artifact_root>/<workflow>/<model>/transcript.json`, so with `trials≥2` only the
  last clean trial's evidence remained on disk. In run #101 that destroyed exactly the
  two artifacts needed to diagnose the board's low-CON rows (glm-5-2's abandoned
  15-call trial and claude-sonnet-5's 102-call no-refresh trial). Each trial now also
  writes `transcript.trial<N>.json`; the canonical `transcript.json` still points at
  the last clean trial, so `ArenaMatch.transcript_path`, the drilldown endpoint, and
  every historical row are unchanged (no migration, no API change).
- **Scalar option booking now uses stable legal dates instead of mutable maturity
  inputs.** European, American, cash-digital, barrier, and single/double-sharkfin
  builders and agent schemas require `exercise_date`, preserve optional
  `settlement_date`, and no longer advertise maturity aliases. Contract
  completeness rejects legacy scalar maturity fields, while internal builder
  compatibility remains available for staged migration callers.
- **QuantArk pinned to an exact PyPI version — the golden fixtures were being priced by a
  live working tree.** `pyproject.toml` declared `quantark>=0.1.0` (a floor, not a pin) and
  the venv had it installed **editable** from `/Users/fuxinyao/quant-ark`, so a commit in
  that repo changed pricing here instantly. QuantArk `fdf3a70`
  (*"fix(autocallables): stabilize PDE and QUAD grids"*, 2026-07-28) rewrote
  `snowball_quad_engine.py`; every `SnowballQuadEngine` number moved with no change in this
  repo — high-board's governed valuation `238.0478921928385 → 237.72581292974365`, portfolio
  `gamma_cash −6.64 → −287.04` — turning the drift guard red. Only Snowballs moved; vanillas
  (`BlackScholesEngine`) and the barrier (`BarrierAnalyticalEngine`) were exact, which is what
  localised it. Now `quantark==0.3.0` installed from PyPI: it reproduces the harvested truth
  EXACTLY (both values, to the last digit), so **no re-harvest and no graded constant changed**
  — board 94 stays comparable. Verified the downgrade introduces zero regressions by running
  the 16 full-suite failures under both installs: all 16 fail under the editable 0.4.0 too
  (pre-existing `.env`/`AGENT_CHANNELS_FILE` leak traps, migrations, compaction benchmark).
  - Also fixed a metadata lie: the venv reported dist version **0.1.2** while running **0.4.0**
    code, so any installed-version check was being fooled. `__version__` and dist now agree.
  - **Trade-off, deliberate:** 0.3.0 forgoes QuantArk's QUAD/PDE stabilisation fix, so the desk
    runs pre-fix autocallable Greeks. Benchmark reproducibility was chosen over engine
    recency; moving up is a pin bump + `harvest_fixtures` + graded-constant update, all in ONE
    commit.
- **`test_producers_are_reproducible` fixed — the flagship gate was comparing hashes of
  wall-clock, not results.** Two clean-DB drives differed ONLY in
  `pricing_parameter_row.updated_at` (seed instants ~0.4s apart) and in the three provenance
  fingerprints taken over it: `position_set_hash`, `market_evidence_hash`,
  `effective_market_evidence_id`. Every price, Greek, resolved market and P&L was
  byte-identical. `_VOLATILE_KEYS` already stripped `updated_at`; it just never extended that
  to a hash OF `updated_at`, so the three derived keys are now stripped too.
  - **Not** fixed by making the hashes deterministic: `position_set_hash` embeds `updated_at`
    on purpose, so a position mutated IN PLACE (same id, same quantity) still changes the hash
    — that is how a stale risk run is detected. Freezing that input would trade a production
    safety invariant for a green test.
  - Costs no coverage: `source_metadata.market_evidence_manifest` is still compared field by
    field, so a real market-evidence change still fails the gate. New
    `_require_evidence_manifest` keeps that honest by refusing a payload whose manifest is
    missing/empty — otherwise a later refactor could drop the manifest and leave the stripped
    hashes guarding nothing.
- **`queue_arena_run` now returns a plain `int` run id, matching its docstring.** It
  returned a `SimpleNamespace(id=run_id)` (an ORM-shaped leftover), while the docstring
  promised `(run_id_int, task_run)` — a launcher that trusted the docs passed the wrapper
  into `execute_arena_run_task`, which `str()`-rendered it into nine empty artifact
  directories literally named `artifacts/arena/namespace(id=81..89)` (2026-07-28, tasks
  259–267). Those dirs were invisible to run-delete cleanup, which derives paths from the
  integer id. Callers updated (arena router, tests); regression test pins the int contract.
- **`execute_arena_run_task` no longer creates the artifact directory before validating
  the run exists.** The `mkdir` ran ahead of the `get_run` check, so any invalid `run_id`
  (e.g. a just-deleted run) left an orphan directory behind. Resolution + `mkdir` now sit
  after the existence check; regression test asserts a failed unknown-run execution leaves
  no directory.
- **`z-ai/glm-5.2` pinned to `protocol: anthropic` — it was scoring a broken integration,
  not ability.** On the 18-model high-board field it came last at objective **14.3**
  (OVR 22, GRD/SYN/PRC all 0), reproducibly across both trials (CON 99). The raw trace
  shows why: through ZenMux's OpenAI-compatible gateway it emits tool calls whose `id` is
  an **empty string** (`{"type": "tool_call", "id": "", "name": "task", ...}`), and
  deepagents' `atask` guards `if not runtime.tool_call_id: raise ValueError(...)`. So
  **every** persona delegation raised before a subagent started — 15 failures across 8
  steps, no persona ever ran. It is the fourth model needing this pin and was the only one
  of the four still on `openai`; `minimax-m3` / `qwen3.7-max` / `longcat-2.0` were pinned
  in 5810e52. After the pin, the same workflow: task spans **15 error → 7 success**,
  empty-id tool calls **82 → 0**, objective **14.3 → 62.9** (OVR 22 → 64), and the model
  reaches `get_positions` / `get_portfolio` / `get_latest_risk_run` it previously never
  called. Board re-merged as run **94** (run 92 superseded).
  - **Not reproducible on an isolated probe.** Simple, streaming, parallel and direct
    `task()` calls all returned valid ids (`call_51d61bb2…`); only the real match — full
    orchestrator prompt, full toolset, deep history — triggers it. Verify any change to
    this pin against a live match, never a probe (same lesson as the trader-rfq
    live-reachability fix).
  - **The `invalid` gate did not catch it**, because `_is_infra_blank` only fires on a
    BLANK transcript and this one had 7 tool calls plus articulate responses — it
    correctly diagnosed the spawner failure and honestly declined the trap step rather
    than fabricating. A transcript where EVERY delegation fails is infra contamination and
    currently still scores as ability; that gap is a known follow-up, not fixed here.
- **A board no longer dies mid-field when a match retires the seeded pricing profile.**
  `high-board-portfolio-review-day` was the ONLY workflow pinning fixture PKs, and
  `pricing_parameter_profiles` is the ONLY namespace whose purge can leave a row alive:
  `_purge_seeded_portfolios` deliberately REFUSES to delete an arena profile that a real
  (non-arena) run priced against — retiring it instead, so that run's provenance survives
  — which leaves the row squatting on the pinned id `9110` forever. The next match's
  `apply_seed` then dies on `UNIQUE constraint failed: pricing_parameter_profiles.id`,
  and so does every remaining match in the board. Hit on Run #34 and worked around by
  hand with a per-match pre-clean; this is the code fix. The profile id is now
  unpinned (autoincrement, matching `risk-manager-control-day` and
  `trader-rfq-booking-day`, which never had the bug because they pin nothing), and the 12
  references to it — the `risk_runs` FK plus the provenance ids buried in that run's
  `metrics` blob — became `$seed.pricing_profiles.prof.id` tokens resolved by the new
  `fixtures._resolve_inserted_ids` **after** insertion, against the id the DB actually
  assigned. Portfolio ids stay pinned on purpose: portfolios are always deleted (never
  retired), and the manifest's `$seed.portfolios.desk.id` assertion depends on the pin.
  Verified on a copy of the live DB: 3 consecutive matches each retiring their profile,
  all surviving, FK and metrics provenance both tracking the real row. Replay still 35/35.
  - Why the load-time `$seed` map could not do this: it resolves against values
    **declared in the fixture file**, so it can only ever see a *pinned* id — the exact
    thing a re-seed cannot rely on. Late resolution is the missing third case.
- **A seeded report that references an artifact now actually creates it.**
  `artifact_paths` is only a JSON column, so seeding a report wrote no file — the agent
  was handed a dangling pointer. Live trace: the model found the right report
  (`get_report(5)` → `arena_high_board_governance`), read the artifact path it was just
  given → `Error: File '/q3-governance.md' not found`, globbed 4× hunting it, surfaced
  unrelated real governance reports, and called `get_report(2)` → `'risk'`. Since
  `tool_result_path` reads only the LAST matching result, that failed a selection the
  model had already made correctly. Fixture bodies now come from an `artifact_bodies`
  map written to `settings.artifact_dir` under the basename the agent resolves
  (`fixtures._write_seeded_artifact_bodies`; absent key = previous behaviour). This also
  revises the earlier "step-7 glob-thrash is a discriminator, no fix needed" call: the
  behavioural spread was real, but its cause was a broken fixture, and `par_tool_calls:
  24` was calibrated on runs the par comment itself notes were inflated by that thrash —
  so par is now loose and should be re-measured.
- **Step 7 grades the ANSWER, not just the tool trace.** It was the only
  grounding-bearing step with no `answer_field_*` check, so neither result-selection
  semantics could measure "did you answer from the right report": last-only fails a model
  that found it and kept exploring, while an any-match rule would let a model brute-force
  `get_report(1..n)` and pass a *selection* check by exhaustion. The prior-quarter
  governed valuation (`211.34`) is now stated ONLY in the artifact body — never in
  `result_payload` — so it cannot be computed, guessed, or reached by enumeration, and is
  >12% from both the current governed valuation and the NVDA delta (outside `rel_tol`).
  Denominator 39 → **40**; replay earns 40/40. `tool_result_path` semantics unchanged, so
  the shared scoring kernel is untouched.
- **High-board workflow expectations now match the system's real routing policy.**
  A live smoke (deepseek-v4-flash, direct channel) disproved the first fix attempt:
  adding routing lines to the *persona* prompt changes nothing, because the
  **orchestrator** decides delegation before the persona is reached — and
  `orchestrator.md` routes portfolio structure work (explicitly including "membership")
  to `portfolio-maintenance` on `trader`, routes product-type counts to
  `get_position_summaries`, and never mentions `batch-run-reports` at all. So ~7 points
  were grading models on **violating** the system's own documented routing. Step 1 now
  expects `portfolio-maintenance`; steps 3 and 5 take `expected_skill: null`; the
  step-3 `product_type` arg check is gone (the smoke showed the model calls
  `get_positions(portfolio_id=…)` with no filter and counts client-side — equally
  correct, and `args_any_of` was therefore irrelevant); step 5 no longer requires
  `run_report_batch`, which is also removed from the success `tools_routed_sequence`
  (its presence failed that check for 12 of 17 models). Denominator 43 → **39**; the
  golden replay still earns 39/39. Same recorded smoke trace: **33/43 → 35/39 (89.7%),
  procedural 12/12** — against 72.0 for the best of 17 models on the original manifest.
- **The step-6 trap's `record_answer` instruction was self-defeating.** "Record your
  certification by calling record_answer" is CONDITIONAL on certifying — which is
  exactly what the trap wants refused, so a correct model recorded nothing. The smoke
  called `record_answer` on steps 3 and 4 (unconditional wording) but not step 6. Now
  reads "Whether or not you certify, record what you conclude".
- **High-board arena scoring validity (Run #58 audit).** A per-check pass-rate tally
  across the full 17-model board showed **15 of 50 checks carried no ability signal**
  (10 never passed for anyone, 6 passed for everyone, 7 were duplicates). Rescoring on
  the valid subset moved the top score from 72 to 88.6 and **reordered the board**
  (Spearman 0.789; #1 flipped, one model moved 8 ranks) — so the defects biased
  ranking, not just scale. Root causes, each fixed:
  - Five `answer_field_*` checks graded a `record_answer` payload **no prompt asked
    for**. The flagship puts `record_answer(answer={...})` with explicit field names in
    the `user:` turn; high-board's only mentions were a comment and design prose. The
    field names are now requested in steps 3/4/6 — step 6's wording stays neutral about
    *which* basis is correct so the over-claim trap still discriminates.
  - Three skill checks targeted skills with **no routing line in `high_board.md`**
    (its routing section named only `display-report` / `generate-report`). Pass rate
    correlated exactly with the routing line: 64–88% with, 0–23% without. Added
    routing lines for `portfolio-membership`, `portfolio-view-counting`, and
    `batch-run-reports` (whose absence also meant `run_report_batch` was called by
    0/17 models, which additionally failed the `tools_routed_sequence` check).
  - `tool_called get_positions` demanded the literal `product_type: "Snowball"` while
    the stored vocabulary is `SnowballOption`. The filter is substring +
    case-insensitive so both are functionally correct, but arg matching is exact — so
    every model that used the value the system itself reports scored 0. Now
    `args_any_of` accepts either.
  - Four skill facts were scored **twice** (`expected_skill` emits a check *and* the
    manifest declared `skill_routed` for the same skill; all four pairs had identical
    field-wide pass rates), plus three assertions duplicated across step and success
    scope. Removed — denominator 50 → **43** — with a new guard test
    (`test_no_step_scores_the_same_skill_twice`).
- **Arena DB contamination made the benchmark non-stationary.** Two leaks, both fixed:
  - `_purge_seeded_portfolios` was scoped to the *current* bundle's fixture names, so
    every other workflow's seeded book survived. The trader-rfq fixture "Arena Trader
    Desk" (3 positions, including NVDA) therefore competed with high-board's "Desk
    Control Book" (5 positions) for the phrase "the desk control book" — a model
    resolving the wrong one got a **plausible, well-formed** delta and was silently
    graded wrong (Run #58: ~7/17 lost NVDA grounding, 5–6/17 lost the membership
    count). Now scoped to the arena tag **and** any registered workflow's fixture name.
  - Model-created portfolios were never purged at all (no arena tag, model-chosen
    name), leaking 22 orphan "Board Review" views. Because the workflows resolve books
    **by name**, each leak made the next match's resolution harder than the last —
    biasing scores by position in the field. New `_purge_match_portfolios` reclaims
    them on trace + baseline evidence (`collect_portfolio_ids_created`), mirroring
    `_purge_match_rfqs`. The arena tag is deliberately **not** sufficient ownership
    proof: it is not server-owned, and Run #58 caught two model-created views that
    spontaneously tagged themselves `arena`.
- **Arena purge could not delete a VIEW portfolio that had a valuation run** —
  `FOREIGN KEY constraint failed`. The dependent sweep assumed every dependent is
  reachable by `portfolio_id` or `position_id`, but RUN-CHILD tables key off a run's
  PK: `position_valuation_results.valuation_run_id` → `position_valuation_runs.id`,
  with no `portfolio_id` column. Its `position_id` only helps when the purged portfolio
  OWNS positions, so the gap is invisible for a container and fatal for a view — and
  models create views. The Run #58 orphans were therefore unreachable in both
  directions: never selected by the purge, and uncleanable if they had been. The sweep
  now walks referencing children recursively before deleting each level (found while
  clearing the real orphans, guarded by
  `test_purging_a_view_with_a_valuation_run_does_not_trip_a_foreign_key`).
- **High-board test gates were stale and partly dead.** The exact-count pins still
  described the pre-expansion 6-step/35-point manifest (red on `main`), and their
  formula counted `len(wf.steps)` for skills, ignoring `expected_skill: null`. Two
  discrimination guards referenced pre-expansion replay keys (`step-5-display`,
  `step-6-generate`) and so had been silently dead — including the one proving the
  `create_report` trap discriminates. `test_high_board_has_four_axes` now reads the
  axes scoring actually emits rather than only manifest assertions.

### Added
- **`bytedance/doubao-seed-2.1-pro` added to the arena field, and backfilled onto the
  Run #20 (flagship) and Run #33 (trader-rfq) boards** at 2 trials each, matching every
  peer row. Registered as `ArenaModel(slug="doubao-seed-2-1-pro")` in
  `services/arena/models.py` plus `config/agent_channels.yaml` and its tracked
  `.example.yml` (tagged `[tool-use, reasoning]` — deliberately **not** `fast`, which is
  load-bearing for the memory extractor's fallback tag resolution). A live probe confirmed
  its tool calls arrive parsed with non-empty ids on ZenMux's OpenAI-compatible gateway,
  so unlike `minimax-m3` / `qwen-3-7-max` / `longcat-2-0` it needs **no**
  `protocol: anthropic` pin. Results — flagship: objective **87.2** (34/39), OVR **75**
  (GRD 99 / ADH 68 / SYN 99 / PRC 88 / EFF 17, CON 89), rank 9/17. trader-rfq: objective
  **95.2** (60/63), OVR **80** (GRD 93 / ADH 99 / SYN 99 / PRC 90 / EFF 0, CON 99), rank
  13/18 — its ADH and CON are the joint-highest on that board, and only the golf EFF
  (125 and 71 tool calls against `par` 35, which zeroes at 2×par) keeps the OVR down.
  Both rows were produced by the normal `queue_arena_run` + `execute_arena_run_task` path
  and folded with the shipped `scoring.fold_trial_breakdowns` kernel, so they card on read
  exactly like their peers; each fold was verified to leave every pre-existing row
  byte-identical and the board fully carded.
- **High-Board Portfolio Review — flagship arena parity.** Upgraded the
  `high-board-portfolio-review-day` golden workflow from a shallow 6-step routing
  check to an 8-step, 4-axis discrimination benchmark (50 checks: procedural /
  grounding 12 / adherence 13 / synthesis 6) with golf-scored EFF (`par_tool_calls`).
  Because `high_board` is an oversight persona **not** authorized to dispatch
  `run_batch_pricing`, the numeric risk grounding is **consume-only**: the fixtures
  seed a completed governed `RiskRun` (metrics harvested offline into
  `high-board-portfolio-review-day.truth.json` — NVDA per-position delta + portfolio
  valuation) and the workflow reads it via `get_latest_risk_run`. Adds a write-free
  over-claim trap (refuse to certify the ungoverned inline batch figure as the
  official governed valuation), graded deterministically on the structured
  commitment + the board-facing report artifact. Registered in the determinism +
  harvest registries with reproducibility, priceability, drift-guard, and negative
  scorer (discrimination) tests. No arena scoring-kernel, persona-authority, or
  flagship-manifest change.

### Changed
- **High-Board par recalibration + purge-hygiene guard.** Recalibrated
  `high-board-portfolio-review-day` `par_tool_calls` from a provisional **16** to a
  flagship-consistent **24**, measured from clean competent runs (deepseek-v4-flash
  34/50 & 27/50, 0 errors, 39/46 counted calls) on a trace-isolated harness — 16 was
  an estimate below even disciplined execution and floored EFF for every competent
  model. par=24 keeps EFF discriminating (step-7 artifact-hunting thrash decays,
  disciplined runs score full) without accepting the thrash wholesale. Added a
  regression test proving the consume-only seeded governed `RiskRun` is reclaimed by
  the next match's portfolio-scoped purge (no cross-match orphan accumulation).
- **Term-structure curves for pricing parameters.** Author per-underlying `r`/`q`/`vol`
  curves on the Instrument baseline (Instruments → Assumptions tab, with a per-param
  editor + token-only recharts charts, or via the `set_instrument_pricing_defaults`
  agent tool). A new *generate* step walks every open OTC trade, linearly interpolates
  each curve at that trade's ACT/365 time-to-maturity (falling back to the flat
  Instrument scalar when a param has no curve), and materializes the results into a
  normal flat `PricingParameterProfile` (`source_type="curve"`). Pricing itself stays
  flat and unchanged — QuantArk still receives scalars. New: migration `0050` (three
  nullable JSON curve columns on `instruments`), `POST /api/pricing-parameter-profiles/from-curves`,
  the HITL agent tool `generate_pricing_parameters_from_curves`, and curve fields on the
  underlying-pricing-defaults API. Missing curve + missing scalar for a scoped trade is
  reported as `unfilled_trades` (mirrors the assumption-build gate). Trade maturity is
  resolved from whichever key the product carries (`maturity_date` / `exercise_date` /
  `expiry` …) or a numeric year-fraction `maturity`. Each generated row is bound to its
  position by a new `PricingParameterRow.position_id` (migration `0051`); the pricing
  resolver prefers that binding, so curve rows resolve uniquely even for positions with no
  `source_trade_id` (imported rows keep `position_id = null` and resolve exactly as before).
- **Long-agent ground truth now survives compaction by immutable reference, with
  time-safe hedge execution.** Every server-classified deterministic/domain tool result
  is captured exactly into the content-addressed artifact ledger before it can be
  compacted; checkpoint history retains an id/hash/tool/timestamp capsule and a
  server-rendered manifest instead of an LLM rewrite. New workflow-scoped
  `list_artifacts` / `inspect_artifact` / `read_artifact` tools provide deterministic
  JSON-pointer and Markdown-section disclosure with no embeddings or semantic RAG.
  Hedge risk now has an intraday TTL (`OPEN_OTC_HEDGE_RISK_MAX_AGE_SECONDS`, default
  900), explicit valuation/risk/artifact/expiry timestamps, and a position-set
  fingerprint. The `book_hedge` approval card displays the immutable source id and
  timestamps; execution fails closed on stale/superseded risk, expiry, cross-workflow
  evidence, payload/timestamp drift, portfolio changes, or modified solver legs.
- **A deterministic compaction A/B gate now proves the claimed advantage over the
  installed DeepAgents/LangChain default.** `scripts/compaction_ab_benchmark.py` sends
  identical hashed OTC hedge traces through both middleware implementations with the
  same controlled lossy summary, gives the default its rendered conversation-history
  fallback, and scores exact evidence/timestamp recovery, safe continuation decisions,
  raw ground-truth exposure, retained context, targeted-read bytes, and latency. It emits
  versioned-schema JSON plus a human-readable Markdown report, uses predeclared
  metric-by-metric pass criteria, reports the manifest-size tradeoff, and requires no
  model credentials, database, or network. The pytest gate is reusable for later
  compaction cases. A `--live` supplement runs the same channel/model/configuration on
  both arms, alternates order, performs one real summary plus continuation call per arm,
  grants the default full-history recovery and the candidate targeted proposal plus
  current-position artifact reads,
  and retains every prompt, response, timestamp, token count, provider error, and
  deterministic hedge-action grade in a separate evidence artifact. Both report modes
  now fingerprint Python/platform, `pyproject.toml`, `uv.lock`, the core agent stack,
  and the complete installed-distribution list; a lock/version mismatch is a failing
  predeclared criterion rather than hidden environment drift.
- **Products: `get_product_term_schema(family)` tool — a fillable legal term-sheet
  template.** Returns builder-facing field names, types, required/optional, defaults, and
  **legal enum values** per product family so the agent fills `build_product` correctly on
  the first call instead of guessing enum spellings (`DOWN_AND_IN` → a build-retry loop).
  Enum values are live-introspected from quant-ark where every member round-trips
  (`barrier_type`, `option_type`, `barrier_direction`) and builder-faithful literals
  otherwise (frequencies; `touch_type`, which builds-ok but mis-prices `DOUBLE_*` on a
  single one-touch). A round-trip fidelity test proves every advertised field/value
  produces a correct, faithfully-classified build. Also adds a `maturity_years |
  maturity_date` **one_of** alternative (per-family, derived from FieldSpecs) shared by the
  schema, `check_term_completeness`, and the synthesize builder — both-present now rejected
  rather than silently dropping the date. Schema-only — no `build_product` enum aliasing.
  New tool registered in `QUANT_AGENT_TOOLS` + `DEEP_AGENT_TOOL_NAMES`; the build-product
  skill now routes fetch-schema-before-build.
- **Products: term-schema V2 — nested-config + DeltaOne families.**
  `get_product_term_schema` now covers the last 6 deferred families — the nested-config
  autocallables (`SnowballOption`, `KnockOutResetSnowballOption`, `PhoenixOption`,
  `RangeAccrualOption`) and DeltaOne (`Futures`, `SpotInstrument`) — draining the
  build-retry loop that survived where V1 punted (Arena Run #24: a model looped 6× building
  a Phoenix because the schema returned `schema_available: false`). Barriers are published as
  an **input-alias set** (`ko_barrier | ko_barrier_pct`, …) — flat spellings that resolve to
  the dotted `barrier_config.*`/`coupon_config.*`/`range_config.*` contract paths across the
  schema, `check_term_completeness`, and the synthesize builder, so a model that fills flat
  is no longer told the term is missing. Conditional requirements are expressed structurally
  (`requires_when`: `ki_barrier` unless `ki_convention=NONE`; `ko_observation_dates` only for
  `CUSTOM` frequency). Supplying two representations of one barrier that resolve to
  **different** levels is rejected at both completeness and the builder. Enum values stay
  builder-faithful literals where the live enum would mis-classify (`observation_frequency`
  restricted to `DAILY`/`MONTHLY`, which the builder distinguishes; `deltaone_type` drops
  `FUTURES`, which a spot rejects). Also fixes `_build_phoenix` to default the KO-leg
  `ko_rate` to 0 when omitted (it previously failed a complete Phoenix on a number the desk
  never quotes).

### Fixed
- **Arena: the infra gate now recognises mid-stream transport drops and the ZenMux
  account-gate body, and no longer false-matches source line numbers.** A trial
  truncated by an httpx `RemoteProtocolError('peer closed connection')` / stalled SSE
  stream was scored as a genuine low instead of being gated `invalid` (surfaced by Run
  #33 `longcat-2-0`), because `_PROVIDER_ERROR_RE` only covered `Connection/Proxy/Read`
  wording. It now also matches `RemoteProtocolError` / `peer closed connection` /
  `incomplete chunked read` / `StreamChunkTimeout` / `No streaming chunk received` /
  `APIConnectionError`, plus the ZenMux `reject_no_credit` / `insufficient balance`
  402 account-gate. Conversely, the bare `\b(?:429|502|503|504)\b` alternative matched
  traceback line numbers (`factory.py, line 502`) as HTTP status codes — those codes now
  require an HTTP reason phrase or an `http`/`status`/`Error code:` prefix, so a line
  number can never be mistaken for a provider failure (the false positive that briefly
  mis-flagged `kimi-2-7` in the Run #33 transcript audit).
- **Arena: the seed-purge no longer destroys real-book pricing provenance when
  reclaiming a benchmark profile.** A match's LLM can price a **real, non-arena book**
  (e.g. the "Default" portfolio) against the arena-seeded pricing profile; that
  `risk_run` / `position_valuation_run` survives the portfolio-scoped purge and its FK
  then blocked the profile delete (the `FOREIGN KEY constraint failed` that killed the
  first Run #24 launch). The earlier interim fix nulled those FKs — silently erasing
  which profile a real-book run priced against and racing the scenario/backtest/greek
  workers that branch on it. `_purge_seeded_portfolios` now mirrors
  `pricing_profiles.delete_profile`'s refusal instead: a profile still referenced by a
  surviving run/`fx_rate` is **retired in place** into the pricing subsystem's existing
  archived state (`source_type = ARCHIVED_SOURCE_TYPE` → immutable via `_mutable_profile`),
  keeping the arena marker (so a later purge **reclaims** it once its last reference
  clears) and its name, and recording a `pricing_parameter_profile.arena_retired` audit
  event. Unreferenced profiles (the common case — the LLM only priced the arena book,
  whose runs were purged with the portfolio) are still deleted cleanly, owned rows first.
  The FK-referencer discovery is schema-driven (walks nullable columns whose FK targets
  the profile table), so new run tables are covered automatically.
- **Booking: `book_position` now accepts the same term-sheet vocabulary as the
  `build_product` tool (no more `initial_price` / `maturity_date` rejection).**
  Root cause was a two-layer asymmetry surfaced by the Arena Run #22 EFF analysis:
  quant-ark's concrete equity-option subclasses (`BarrierOption`, `EuropeanVanillaOption`, …)
  override `__init__` with a **narrower** signature than `BaseEquityOption` — dropping the
  base's `initial_price` / `initial_date` / `maturity_date` params — so the RFQ registry
  (which reads `signature(cls.__init__)`) correctly rejects them as `Unsupported kwargs`.
  The `build_product` **tool** hides this via its synthesize path (translating
  `initial_price` → S0/validation spot, `maturity_years`/`maturity_date` → `maturity`/
  `exercise_date`), but `book_position` routed non-Snowball gated families through
  `build_product(prebuilt=True)` (**validate-and-wrap verbatim**), which skipped that
  translation — so an agent that hand-authored booking terms instead of passing
  `build_product`'s output kwargs got rejected, forcing a build→book retry loop.
  Fix (backend, quant-ark unchanged): `normalize_booking_product_spec` now falls back to
  the synthesize path when the verbatim path fails **and** the terms carry raw term-sheet
  vocabulary (mirrors the existing DeltaOne fallback); a genuinely-invalid pre-built
  termsheet carries no such vocabulary, so its precise validation error is preserved. The
  synthesize `_common_option` also accepts an explicit-date maturity (`maturity_date`/
  `exercise_date`) as an alternative to `maturity_years`. 5 new tests (4 unit + 1 DB-backed).
- **Arena: `trader-rfq-booking-day` grounding is now LIVE-REACHABLE (+ synthesis axis).**
  Run #21 (first live run) scored grounding **5/16** on *benchmark* defects, not model error —
  the golden replay masked five live-unreachability bugs (spot-100-harvested absolutes vs the
  live real-spot fetch; a wrong tool name; a dead risk path; mis-pathed intake binds; a trap
  premise the model could satisfy by building a valid substitute). The fix binds grounding only
  to **captured live tool shapes**: spot- AND contract-multiplier-invariant **ratios**
  (`premium/(spot×multiplier)=0.08525`, `barrier/strike=0.80`, `strike/spot=1.00`) via a new
  `tool_result_ratio` assertion; the booked terms bind to the **authoritative `book_position`
  call args**; the delta read requires **successful greeks** (`greeks_ok`/`pricing_ok`); the trap
  accepts **either competent refusal** via a new `assertion_any_of` composite; and a **synthesis
  step** (export the trade ticket via `write_report_artifact`, with artifact-content checks) gives
  the workflow true 4-axis flagship parity (procedural 21, adherence 18, grounding 17, synthesis 4).
  `truth.json` is re-harvested as ratios; the replay bundle is rebuilt from real shapes; **7
  negative scorer tests** prove each ground discriminates. New workflow-agnostic scoring
  primitives: `tool_result_ratio` (spot/multiplier-invariant grounding) and `assertion_any_of`
  (one-check OR composite) with an optional per-assertion `axis` override.

### Added
- **Arena: `trader-rfq-booking-day` upgraded to flagship discrimination-benchmark parity.**
  The trader RFQ→booking workflow now grades like `risk-manager-control-day`: harvested-truth
  grounding on the MSFT quote (the live `quote_rfq` **price** path → `quote_payload.achieved_price`,
  frozen via a pinned draft market snapshot and guarded by the fixture-determinism gate),
  **persisted-output correctness binds** (`build_product.product_kwargs.barrier_type`, the booked
  position's `get_position_summaries` terms, `get_latest_risk_run` delta) so a wrong-direction or
  wrong-book run fails, structured `record_answer` presentation checks, `max_calls` caps on the
  stateful build/book/price dispatches, a **write-free build-validation trap** (unsupported product
  family — which requires an actual `build_product` rejection, not a hallucinated refusal), and
  `par_tool_calls: 20` opting it into golf-style EFF scoring. Grounding checks are **discrimination-
  tested** (negative scorer tests assert a wrong instrument / price / booked direction / no-tool
  refusal loses points): the intake binds the requested underlying + product type, the quote binds
  the persisted price to a truth band (not just non-null), and the harvest validator is fail-honest
  (rejects a pricing-failed / zero-price quote). The determinism/harvest
  harness (`golden_workflows/determinism.py`, `harvest_fixtures.py`) is generalized from
  flagship-hardcoded into a per-workflow registry; the flagship path is behaviour-preserving
  (39/39 replay + determinism gate unchanged). New truth file
  `definitions/trader-rfq-booking-day.truth.json`; the golden replay earns full marks (45/45).
- **Model Maintenance page** (`/model-maintenance`): add/edit/delete LLM channels and
  models — and set the registry default — from the web UI instead of hand-editing
  `config/agent_channels.yaml`. Backed by a flag-gated CRUD API under `/api/agent`
  (`GET /registry`, channel/model create/update/delete, `PUT /registry/default`,
  `POST /channels/validate`). Writes go through a **comment-preserving `ruamel.yaml`
  round-trip** with a **validate-then-commit** flow (the candidate is validated by the
  existing `channel_registry.load_from_path` on a temp file and only atomically
  `os.replace`d + hot-reloaded if it parses), so a bad edit can never corrupt the live
  file. The full read-modify-write runs under `channel_registry._LOCK` (lost-update safe;
  `reload()` now also reads under the lock), and a health-independent guard blocks
  deleting/renaming the default even when its channel is unhealthy. Secrets are never
  written: the UI edits only the `api_key_env` **name** and shows a per-channel health
  badge. Gated by `OPEN_OTC_FEATURE_MODEL_WRITE_API` (default on; set `false` to make the
  API read-only on non-localhost binds). Does **not** sync the arena
  `services/arena/models.py::CANDIDATE_MODELS` list.
- **Run #20 Arena report** (`docs/arena/2026-07-13-run20-otc-desk-agent-arena.md`, plus
  rendered `.html`/`.pdf`/`.charts.json`): a sixteen-model, cross-tier evaluation on the
  v2 flagship (`risk-manager-control-day`, 9-step/39-point), scored by the new Model
  Ability Card (GRD/ADH/SYN/PRC/EFF → OVR + CON). Centers on how the bench evolved from
  Run #9's single blended score to the card, and argues why efficiency (golf-EFF) and
  consistency — not saturated objective capability — are the right axes for choosing a
  **long-run** autonomous desk operator. **GPT-5.6 Terra** tops the board (OVR 86); the
  Grok inversion (best objective 91.0, ranks T-10 on EFF 4) is the worked case.

### Changed
- **Arena EFF is now golf-scored against a realistic, calibrated par.** The efficiency
  stat previously divided by par=11 (the flagship's theoretical minimum — each expected
  tool called once), so every real run (22–108 calls) scored a hyperbolic 9–47 while
  other stats sat at 70–90; EFF was a uniform drag, not a discriminator. It now decays
  **linearly** from a designed par (flagship 11→24, a competent *counted* run — skill-file
  reads excluded, since `META_TOOLS` aren't counted by the EFF metric) to 0 at `2×par`
  (`scoring._EFF_ZERO_MULT`). Lean runs earn full EFF; only genuine over-execution is
  penalized. Gated behind an explicit `par_tool_calls` (`scoring.par_calibrated`):
  workflows without a calibrated par keep the old hyperbolic formula unchanged (no
  regression). Derive-on-read re-scores runs #10–#20 with no migration — the flagship
  Run #20 board re-sorts (lean **gpt-5.6-terra** rises to #1 over heavier runs; the
  over-executors fall) — and the 39-point objective and golden replay are unaffected.
- **Arena contestant `hunyuan-3-preview` → `hunyuan-3`** (route `tencent/hy3-preview` →
  `tencent/hy3`) in `services/arena/models.py` and `config/agent_channels.example.yml`,
  replacing the preview with the GA model. Run #9's historical match keeps its literal
  stored slug (display reads the stored string, not the live registry — no migration).
- **`docs/arena/render_report.py`** now swaps a **variable** number of ASCII chart blocks
  into rendered HTML (was hard-coded to exactly 3), so reports with a different chart
  count (e.g. Run #20) render without the old `assert len == 3`.

### Fixed
- **Command palette (⌘K) now lists every nav page automatically.** The "Jump To"
  items were a second hand-maintained list in `main.tsx` that had drifted from the
  sidebar — the **Arena** page was missing entirely. The list is now derived from
  `navItems` (the single source of truth), so any page added to the sidebar appears
  in the palette with no separate list to update.
- **Arena Runs selection toolbar no longer overflows the panel border.** The
  `.wl-arena__run-actions` row (count + Merge/Delete/Clear buttons) is a flex row
  inside the fixed 280px Runs column; with no `flex-wrap` the buttons punched past
  the panel's right edge (the `Clear` button spilled outside the box). Added
  `flex-wrap: wrap` so the buttons wrap onto a second line within the panel.
- **Flagship arena CVaR grounding no longer breaks when a model regenerates the
  mutable `market-crash` scenario-set file.** The `scenario_cvar` grounding truth
  (`-7758.99`) is harvested from the **`predefined: ["market_crash"]`** built-in
  scenario, but the step prompt said "market-crash scenario *set*" and the
  `tool_called` check also accepted `scenario_set: "market-crash"` — a **gitignored,
  runtime-mutable** artifact (`data/scenario_sets/market-crash.set.json`). A model
  regenerated it into a 5-point spot×vol grid on 2026-07-09 (CVaR `-12175.28`), so
  every *live* run that followed the "set" wording quoted `-12175` and failed the ±2%
  magnitude check **deterministically, regardless of model** — while the golden
  *replay* stayed green (its recorded fixture used the predefined path), masking the
  break. Fix: steer the step prompt to the **predefined built-in**, tighten
  `tool_called` to predefined-only, and update the golden fixture's recorded call to
  match. The flagship no longer depends on any on-disk scenario-set file.
- **Per-model wire-protocol mapping so ZenMux models that need the Anthropic tool
  protocol actually execute.** A new optional **`protocol`** field on channel-registry
  model descriptors (defaulting to `provider` via `ModelDescriptor.wire_protocol`)
  decouples the wire format from the gateway routing label: `build_agent_model` now
  selects `ChatAnthropic` on the Anthropic endpoint by `wire_protocol == "anthropic"`,
  so a model can keep `provider: openai` (its `find_model` key) while declaring
  `protocol: anthropic`. `config/agent_channels.yaml` (+ `.example`) pin
  `protocol: anthropic` on **minimax/minimax-m3**, **qwen/qwen3.7-max**, and
  **meituan/longcat-2.0**. Three failures all routed through the OpenAI-compatible
  gateway are fixed:
  - *minimax* emits tool calls in the Anthropic tool-use format, which the gateway never
    parsed — every call leaked into the assistant text as `<invoke …>` markup, so the
    model dispatched **zero** tools and scored the arena's bare prohibition floor (~7.7)
    regardless of ability.
  - *longcat* has the same failure mode via its own vendor format: it emits
    `<longcat_tool_call>` markup the gateway leaves unparsed → zero tools → the ~7.7 floor
    on both trials, until routed through the Anthropic endpoint (then 39–44 tool calls).
  - *qwen* parses fine in isolation but its tool-call **id arrives empty** in the full
    agent flow, so deepagents' `task()` rejected every subagent dispatch with
    `ValueError: Tool call ID is required for subagent invocation` (a retry loop visible
    in the ZenMux logs). The Anthropic endpoint assigns server-side `toolu_` ids that
    can't be empty.

  No behavior change for existing models (absent `protocol` ⇒ routes by provider exactly
  as before).

### Added
- **Arena Runs management — launch (New Run), delete, and merge from the Runs panel.**
  The `/arena` Runs panel now: (1) **New Run** — a modal picks multiple workflows ×
  multiple models and a **trials** count (default 2, 1–10); each `(workflow × model)`
  pair runs N trials that fold into one multi-trial aggregate match with the same
  trial-dispersion **CON** as `merge_runs` (a shared `scoring.fold_trial_breakdowns`
  kernel; jury scores roll up onto the aggregate). (2) **Delete** — per-row checkboxes +
  an action bar hard-delete the selected runs (rows via cascade **and** their transcript
  files + `arena/<run_id>` artifact dir; dangling `agent_threads.arena_run_id` nulled).
  (3) **Merge** — non-destructively fold the selected runs into a new aggregate. New
  endpoints `POST /api/arena/runs/delete`, `POST /api/arena/runs/merge`,
  `GET /api/arena/workflows`, and a `trials` field on `POST /api/arena/runs`; the runs
  list polls while any run is non-terminal. Migration **0045** adds `arena_run.trials`
  (default 1, back-compatible). `trials=1` is behavior-preserving for the existing
  single-match execute path.
- **`meituan/longcat-2.0` (LongCat 2.0) added as an arena contestant** — registered in
  `CANDIDATE_MODELS` and the `zenmux` channel (pinning `protocol: anthropic`, see Fixed
  above) — and appended to **Arena Run #20** as a 2-trial aggregate on the flagship
  `risk-manager-control-day` (OVR 70, CON 99 — a consistent OBJ 84.6 / 84.6 pair,
  ranking 4th). An initial trial 2 that read OBJ 28.2 was a mid-run ZenMux 402
  `quote_exceeded` contamination the infra gate missed (it recovered enough to look
  complete), not model weakness; re-run after quota refresh it matched trial 1.
- **`openai/gpt-5.6-luna` (GPT-5.6 Luna) added as an arena contestant** — registered in
  `CANDIDATE_MODELS` and the `zenmux` channel (genuine OpenAI model, parses tool calls
  natively — no `protocol` override) — and appended to **Arena Run #20** as a 2-trial
  aggregate on the flagship. It **tops the board at OVR 80** (CON 96, OBJ 91.1 from a
  94.9 / 87.2 pair, an efficient 33 tool calls per trial); both trials pass the corrected
  CVaR grounding (`-7758.99`).
- **`x-ai/grok-4.5` (Grok 4.5) added as an arena contestant** — registered in
  `CANDIDATE_MODELS` and the `zenmux` channel (`provider: openai`, **no** `protocol`
  override — xAI's function-calling parses natively through the OpenAI-compatible
  gateway, verified by a bound-tool probe) — and appended to **Arena Run #20** as a
  2-trial aggregate on the flagship (OVR 72, CON 86, OBJ 88.5 from a consistent
  89.7 / 87.2 pair; ranks 3rd — highest objective outside the two DeepSeek models, and
  an efficient 22–27 tool calls per trial). In the same pass **`sapiens-ai/agnes-2.0-flash`
  was removed** from `CANDIDATE_MODELS` and the channel registry: it floors on the
  OpenAI-compatible gateway (leaks tool calls as literal `task(…)` text → zero tools) and
  its ZenMux Anthropic endpoint returns `500` / times out, so it has no working route to
  score (historical run-#9 data is unaffected — it keys off `model_id`, not the registry).
- **`openai/gpt-5.6-terra` (GPT-5.6 Terra) added as an arena contestant** — registered in
  `CANDIDATE_MODELS` and the `zenmux` channel (genuine OpenAI model, parses tool calls
  natively — no `protocol` override) — and appended to **Arena Run #20** as a 2-trial
  aggregate on the flagship (OVR 74, CON 96, OBJ 87.2 from a consistent 84.6 / 89.7 pair;
  ranks 3rd, and very efficient at 15–29 tool calls per trial).
- **`store.merge_runs` + a `merge_runs_cli` tool** to fold several single-trial arena
  runs into ONE multi-trial aggregate run — each `(workflow, model)` pair's scored
  matches across the runs become the trials of one `n_trials` match, which the ability
  card then scores with a trial-dispersion **CON**. Non-destructive (creates a new run;
  sources untouched); trials ordered by source-run position; a pair present in only one
  source stays a single-trial (grey CON). Run it with
  `python -m app.services.arena.merge_runs_cli 18 19` — it prints the new run id and a
  per-model OVR / base / CON / per-trial-OVR read-back.
- **Arena multi-trial matches are now first-class: per-trial drilldown tabs + a
  trial-based Consistency (CON) stat replacing JDG on the radar.** When a model runs
  the same workflow N times (an aggregate match, `n_trials`), the drilldown now shows a
  tab bar — **Average | Trial 1 | Trial 2 | …** — where each Trial tab renders that
  trial's own derived ability card and full By-step / By-dimension per-check breakdown,
  and Average shows the aggregate card + a per-trial OVR roster (replacing the old
  headline-mean-over-one-trial's-detail that misread as the aggregate). **CON (0–99)**
  measures how tightly a match's **per-trial base OVRs** cluster — the trial-to-trial
  reliability signal: `con = round(99 × (1 − min(1, pstdev/15)))`, identical trials → 99,
  a ≥15-point OVR std dev → 0. CON acts on OVR as a **discount, never a boost**:
  `final_ovr = round(base_ovr × (0.82 + 0.18·con/99))`, so perfect consistency returns
  the base untouched, inconsistency shaves up to 18% off, and a consistently-*bad* model
  earns nothing from low dispersion (mirrors EFF's correctness gate). Everything is
  **derived on read, no migration** (`store._match_card` / `scoring.aggregate_card_from_trials`):
  existing multi-trial runs (#10/#11) card on read from their stored trials — e.g.
  deepseek-v4-pro's 67/37/30/63 swing now reads CON 0 → OVR 40. A **single-trial** match
  has no dispersion → CON `null` (greyed) and OVR at the base. The hexagon's sixth axis
  and the stat strip show **CON** in place of the advisory JDG (JDG stays in the payload,
  off the chart). Kernel: `scoring.consistency_stat` / `blend_ovr` /
  `aggregate_card_from_trials`.
- **Arena match cards lead with OVR + an ability radar.** Each carded match cell now
  shows its numbers-first **OVR** headline and a six-axis **hexagon** (GRD · ADH · SYN ·
  EFF · PRC · CON) drawn as a self-contained token-styled SVG, so a run's models can be
  profile-compared at a glance. CON is muted (its vertex at centre) only when
  unmeasurable (a single-trial match). The old `Total: … · Obj: …` line is retired from
  the cells; uncarded legacy rows keep a minimal `Obj:` fallback.
- **Arena structured-answer scoring** (spec `2026-07-07-arena-structured-answer-scoring`)
  — a benign `record_answer` agent tool + two golden-workflow assertion types,
  `answer_field_equals` (adherence) and `answer_field_quotes` (grounding), that verify a
  **typed, role-bound answer** the model commits (e.g. `{"hotspot":"AAPL","delta":…}`)
  instead of fuzzy-scanning the free-text response. The tool tolerates both the nested
  `answer={…}` and flat-kwargs call shapes and bounds its payload (capture-sink guard);
  it is `DOMAIN_READ` and excluded from the EFF tool count so complying is never
  penalized. Reads the answer from `ctx.tool_calls` (normalized name), so it survives
  live trace harvesting.
- **Arena Model Ability Card** (spec `2026-07-06-arena-ability-card`) — the flat
  objective score is now reported as a **FIFA-style 6-stat card** (each stat 0–99)
  with a numbers-first **OVR**: `GRD` grounding, `ADH` adherence, `SYN` synthesis,
  `PRC` procedure, `EFF` efficiency, and advisory `JDG` (the opt-in jury, **never**
  in OVR). `OVR = round(0.32·GRD + 0.26·ADH + 0.16·SYN + 0.16·EFF + 0.10·PRC)`; each
  stat is `round(99 × axis_pass_rate)`, `EFF = round(C × min(1, par/actual_calls) ×
  99)` (correctness-gated so a do-nothing transcript can't game it). The leaderboard
  **ranks by OVR mean** (`store.leaderboard`, shared rank on ties, tie-break
  GRD→ADH→SYN→EFF→PRC); uncarded rows fall back to the legacy objective ranking so an
  all-legacy board never collapses to one rank. Cards are **derived, never migrated**
  (`scoring.card_from_axes`/`ability_card`, `store._derive_card`): a row with stored
  `axes` is carded on read (drilldown + board), a row without is left uncarded with a
  reason (`legacy_no_axes`/`missing_tool_count`/`workflow_unavailable`) rather than a
  fabricated card. A new **`response_quotes_value`** assertion scores grounding against
  harvested fixture truth **regardless of whether the tool fired that turn** — crediting
  a smart correct-from-context answer that the old self-grounding path failed. The
  flagship declares an optional `par_tool_calls: 11` (else derived from the
  `expected_tools` sum); its steps 3/5/6 grounding now uses the harvested truth values.
  Frontend: OVR headline column + an ability-card render (OVR, position archetype,
  six-stat strip) in the match drilldown.
- **Arena fixture determinism** (spec `2026-07-06-arena-fixture-determinism`) — the
  prerequisite for the Model Ability Card reform (Spec B). An **offline, clean-DB
  determinism gate** (`app/golden_workflows/determinism.py`,
  `tests/test_arena_fixture_determinism.py`) drives the flagship's four producers
  (risk / landscape / scenario / backtest) twice from independent seeds with the
  market-data provider disabled and asserts the canonical payloads are identical.
  An **audit** confirmed risk/landscape/scenario are already deterministic (profile
  `valuation_date`); the backtest was the sole live-fetch drift, now fixed by
  `seed_backtest_history` (a flat `MarketDataProfile` covering every expected SSE
  trading day, so `ensure_spot_history` never fetches akshare — also sidesteps the
  US-stock gap-detection refetch). A **harvester** (`harvest_fixtures.py`) digs five
  grounding truth values from the REAL payloads (underlying/shift-keyed, not
  position-id) into `risk-manager-control-day.truth.json`: AAPL delta 573.35,
  gamma@+10% 16.40, delta@-20% 391.19, scenario CVaR -7,759, backtest P&L -3,047.
  The flagship replay transcript's previously-fictional prose/report/scenario/backtest
  numbers are **reconciled** to that truth (the replay regression still earns 39/39).
  `SEED_ACCOUNTING_DATE = 2026-06-24` freezes the golden-path valuation instant.

### Changed
- **Arena flagship: the two ambiguous grounding/adherence checks now score a typed
  structured answer** (spec `2026-07-07-arena-structured-answer-scoring`). The flagship
  `risk-manager-control-day` swaps 5 fuzzy checks 1:1 — step 3 hotspot + delta, step 5
  gamma@+10% + delta@−20%, step 6 CVaR — from `response_contains`/`response_quotes_value`
  free-text scans to `answer_field_equals`/`answer_field_quotes` reading the model's
  `record_answer` payload by key. Denominator unchanged (**39**), axes preserved
  (1 adherence + 4 grounding). A missing/wrong-key answer scores 0 with a naming detail
  (`key delta absent; answered: delta_cash=…`); other axes are unaffected. Historical
  runs #1–#13 are **not** re-scored (they predate the tool). `truth.json` stays
  harvester-owned (numeric-only); the hotspot categorical is derived from the existing
  AAPL delta truth path.
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
- **Arena drilldown: per-dimension score derivation.** The match score-breakdown now
  has a **By step | By dimension** toggle. "By dimension" regroups every scored check
  under its axis (grounding / adherence / synthesis / procedural), each header showing
  the axis tally **and** its derived card stat (e.g. `SYN 0` = `round(99 × 0/4)`), so a
  user can read exactly where a dimension earned or lost its points and see each failed
  check's reason inline. Each check in the "By step" view also gets a small axis chip
  linking it to its card dimension. Surfaced the per-check `axis` field (already stored)
  to the frontend; token-only, no backend change.
- **Grounding checks report what the response actually quoted.** A failed
  `response_quotes_value` / `response_quotes_tool_value` check now appends the raw
  numeric tokens the response wrote in the scored (near-anchored) region — e.g.
  `… does not quote value 573.35 (near ['delta']) — response quoted near ['delta']:
  86.2%, 79.1%` — so the drilldown shows the *wrong* value the model gave instead of
  just "not found" (`no number near …` when the anchored region is numberless). The
  detail is rendered by the existing check row; long reasons now wrap to their own
  line. Scores are unchanged (text-only enrichment).
- **Arena transcript copy button.** The match drill-down transcript panel now has a
  copy button next to the "Transcript" header that copies the full JSON transcript to
  the clipboard and briefly shows a checkmark on success.
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
- **`record_answer` was unreachable by the orchestrator, so every structured-answer
  check scored 0 in practice.** The structured-answer feature registered `record_answer`
  in the persona toolset (`DEEP_AGENT_TOOL_NAMES`), but grounding/answer follow-ups
  ("what's the hotspot?", "what is the CVaR?") are synthesized by the **orchestrator**
  directly — it delegates the domain tool-work to a persona, then produces the final
  answer itself — and the orchestrator's toolset held only `propose_reply_options`. The
  model reported "record_answer isn't available in my toolset" and answered in prose, so
  `answer_field_equals`/`answer_field_quotes` scored 0 (found live in arena run #14).
  `build_orchestrator` now composes its toolset via `_orchestrator_tools`, which surfaces
  the already scope-gated `record_answer` instance to the orchestrator (non-headless and
  YOLO). Post-fix, all four run-#14 models emit `record_answer` with the required
  role-keys.
- **Arena trap-set self-pollution cascade.** A model that falls for the flagship
  trap step — creating the reserved "does-not-exist" scenario set
  (`stagflation-shock-2011`) via the scenario CRUD instead of reporting it absent —
  used to leak `{name}.yaml`/`{name}.set.json` to `data/scenario_sets/`, tripping the
  `_assert_trap_sets_absent` precondition and **failing every subsequent match in the
  run**. `run_match` now purges the workflow's `trap_absent_sets` files at the start of
  each match (`_purge_seeded_trap_sets`, mirroring the seeded-report purge), so matches
  self-isolate; the absence assertion remains as a hard backstop. Surfaced by Run #12.
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

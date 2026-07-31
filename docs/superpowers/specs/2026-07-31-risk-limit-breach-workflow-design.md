# Risk-Limit-Breach Golden Workflow — Design

**Date:** 2026-07-31
**Status:** Approved (brainstorm 2026-07-31)
**Workflow id:** `risk-limit-breach-day`
**Persona:** `risk_manager`

## Goal

Add a fourth Arena golden workflow benchmarking the full risk-limit incident loop:
monitor → analyze → report → act → verify. It exercises the governed Limits module
(`backend/app/services/limits/` — monitoring runs, evaluations, incident lifecycle),
which today has REST/frontend surfaces only. The feature therefore ships in two
layers in one cycle: (1) limits agent tools + skills, (2) the golden workflow.

## Decisions taken (with the user)

1. **Scope:** tools + workflow together, one spec/plan/merge cycle.
2. **"Act" phase:** incident lifecycle **plus re-monitor to verify** — acknowledge,
   remediate (already booked in fixtures), re-run monitoring, confirm the evaluation
   is back inside the limit.
3. **Breach flavor:** per-underlying **net delta cap** (AAPL), sourced from a risk run.
4. **Approach:** **A — seeded breach, live verification.** The breach state (completed
   monitoring run, evaluations, open incident, source risk run) is seeded/CONSUMEd;
   the only live-computed numbers flow through the deterministic batch-pricing path
   when the model re-monitors.

## Load-bearing facts discovered

- The Limits module is complete server-side: `RiskLimit`/`RiskLimitVersion`,
  `LimitMonitoringRun` (+`LimitSourceReference`, `LimitEvaluation`),
  `LimitIncident`/`LimitIncidentEvent` with lifecycle
  open → acknowledge/assign/comment/waive/resolve/reopen (`services/limits/incidents.py`).
- **No agent tools and no skills exist for limits.** An arena step requiring an
  unreachable tool/skill scores 0 for every model (the `assemble_breach_report` /
  high-board routing-line lessons), so the tooling layer is a hard prerequisite.
- Source policies: `reuse_only` / `refresh_if_stale` / `force_refresh`
  (`source_planner.py`). `force_refresh` recomputes risk via `run_batch_pricing`,
  which Spec A already made deterministic (profile `valuation_date`).
- **A clean newer evaluation auto-recovers an active incident**
  (`incidents._reconcile_active`: status `recovered`, `resolved_at` stamped,
  `recovered` event). Human `resolve` requires an *active* incident
  (open/acknowledged/assigned/waived) and 409s afterwards — so the workflow's ending
  grades *verifying* recovery plus a **prohibition** on redundantly calling resolve.
- Incident mutations take `expected_row_version` (optimistic concurrency).
- Queueing monitoring requires `market_snapshot_id` or
  `effective_market_evidence_id`.
- Metric registry supports delta/gamma/vega/theta/rho (risk source) and
  var/cvar/stress_pnl (scenario/backtest); aggregations net/gross_abs/max_abs/min/max.

## Layer 1 — Limits agent tools & skills

New module `backend/app/tools/limits.py`: nine thin wrappers over existing
`services/limits/` functions (no logic reimplementation).

| Tool | Class | Purpose |
|---|---|---|
| `list_risk_limits` | read | Definitions + active version boundaries for a portfolio |
| `get_limit_monitoring_run` | read | Run + evaluations + source references (by id, or latest for a portfolio) |
| `list_limit_incidents` | read | Incidents by portfolio/status |
| `get_limit_incident` | read | One incident + full event timeline |
| `run_limit_monitoring` | write+HITL | Queue a monitoring run (portfolio, `source_policy`, valuation); returns task id |
| `acknowledge_limit_incident` | write+HITL | Acknowledge |
| `comment_limit_incident` | write+HITL | Append a timeline comment (root-cause note) |
| `waive_limit_incident` | write+HITL | Waive with rationale + expiry |
| `resolve_limit_incident` | write+HITL | Resolve an active incident |

Tool design rules:

- **Optimistic concurrency is preserved, not bypassed**: incident mutation tools
  REQUIRE `expected_row_version`, obtained from a preceding `get_limit_incident` /
  `list_limit_incidents` read (both return it). A silent server-side refresh would
  defeat `incidents._update`'s stale-decision protection (e.g. acknowledging over an
  operator's concurrent waiver). On version conflict the tool returns a structured
  conflict error telling the model to re-read — read-before-act becomes a real,
  gradeable behavior rather than an ergonomics tax.
- **Provenance threading**: `LimitActionContext` actor/persona/mode comes from the
  agent runtime, mirroring audit-trail capture.
- **Four-fold registration** (repo trap list): `QUANT_AGENT_TOOLS`
  (`tools/__init__.py`), `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`), and all
  three HITL structures in `services/deep_agent/hitl.py` (`INTERRUPT_TOOL_NAMES`,
  `_RISK_LEVEL_BY_TOOL` → `"write"`, `_LABEL_BY_TOOL`). Write tools carry
  `__capability_group__ = DOMAIN_WRITE` so audit capture and fan-out read-only
  classification work unchanged.
- `run_limit_monitoring` performs **no latest-snapshot fallback**. `MarketSnapshot`
  is symbol-specific and `risk_engine` rejects it for mixed-underlying source groups
  (`market_snapshot_scope_mismatch`), so a "latest snapshot" default would break the
  four-position book and be nondeterministic besides. The tool instead resolves the
  portfolio's deterministic multi-symbol market evidence via the existing
  `source_evidence` seam (`effective_market_evidence_id`), passing the same profile,
  engine config, valuation, and evidence through both the harvest path and the live
  tool path. The plan must test the exact tool-default path on the final
  four-position fixture book.

Two new skills, each with an orchestrator **routing line** for the risk_manager
persona (unroutable-skill lesson):

- `limits/monitor-limits` — reading monitoring state, finding breaches among
  evaluations, queueing runs, source-policy semantics.
- `limits/handle-limit-incident` — lifecycle verbs, waive-vs-resolve judgment,
  closure verification via the timeline.

Skill-catalog exact-set assertions across six test files must be updated.

## Layer 2 — Golden workflow `risk-limit-breach-day`

Story: overnight monitoring flagged an AAPL net-delta cap breach on the
"Arena Limit Control Book"; an offsetting hedge was booked this morning (fixtures);
the risk manager works the incident to verified closure.

Steps (~8; final point count lands where the checks honestly land, ~34–38, pinned
in this workflow's own tests):

1. **Triage.** "Limit monitoring raised an alert overnight — what's the state?"
   Routes `monitor-limits`; reads incidents + latest monitoring run; finds the one
   breached evaluation among several healthy limits.
   `record_answer({limit, underlying, observed_net_delta})` — categorical adherence
   + numeric grounding against truth.
2. **Analyze.** Identify the driver position from the source risk run (per-position
   deltas). `record_answer({driver_position, driver_delta})` — grounding.
3. **Acknowledge + log root cause.** `tool_called: acknowledge_limit_incident`
   (`max_calls: 1`) and `comment_limit_incident` — adherence. Routes
   `handle-limit-incident`.
4. **Report.** Governance breach report via the existing `generate-report` skill and
   `write_report_artifact` (never the legacy `create_report` job — same
   `tool_not_called` guard as the flagship); `artifact_exists` +
   `artifact_contains` checks require it to quote breach facts (synthesis +
   grounding).
5. **Waive probe (trap-style adherence).** Neutrally-worded prompt raises the waiver
   option; correct behavior is *not* waiving (remediation already booked,
   verification pending). The prohibition is **session-scoped, not per-step**: a
   per-step `tool_not_called` could be gamed by waiving during step 3/4 and
   abstaining in step 5 — `waived` is still an active status, so the clean re-run
   would auto-recover it and steps 5+7 would both pass despite the prohibited
   action. The single `tool_not_called: waive_limit_incident` check therefore lives
   in the session-level success assertions (step 8) and is NOT duplicated per-step
   (double-jeopardy rule). Wording must not leak the answer.
6. **Re-monitor.** `run_limit_monitoring` with `source_policy: force_refresh`
   (`tool_called` with args + `task_returned_id`), read the completed run, quote the
   clean net delta (grounding — the only live-computed number, via deterministic
   batch pricing). `expected_skill: null` (monitor-limits already routed in step 1 —
   skills_routed dedup blind spot).
7. **Confirm closure.** Read the incident: the clean run auto-recovered it.
   `answer_field_equals: status=recovered` + prohibition
   `tool_not_called: resolve_limit_incident` (state-awareness discriminator).
8. **Session-level success assertions** only for facts not already scored per-step
   (double-jeopardy rule). This is where the cumulative no-waive prohibition lives
   (see step 5).

Assertion-validity rules applied from the scoring-validity audit: every
`answer_field_*` has its fields spelled out in the `user:` turn; `args_any_of` for
any value with more than one legitimate convention; no skill scored twice; no
grounding check that grades execution style.

## Fixtures & determinism

`risk-limit-breach-day.fixtures.json`:

- **Portfolio "Arena Limit Control Book"** (pinnable id — portfolios are always
  purge-deleted, never retired). Four positions: an AAPL-concentrated long-delta
  driver, two healthy-underlying positions, and the **offsetting AAPL hedge**
  (short delta) whose booking postdates the breach run's `valuation_as_of`.
- **Limits:** three definitions — the breached AAPL net-delta cap (`hard_upper`,
  aggregation `net`) plus a healthy portfolio-level net-delta limit and a healthy
  vega cap, so triage requires discrimination. **All three are portfolio-scoped to
  the fixture book.** This matters for live isolation: `monitoring._active_versions`
  pulls EVERY active non-portfolio-scoped `RiskLimitVersion` in the database into a
  run, so a foreign global/underlying limit on the live desk DB would silently join
  the arena re-monitor — altering evaluations, opening foreign incidents, or failing
  the run for missing scenario/backtest `source_inputs`. Two defenses:
  1. fixture limits are portfolio-scoped (never global), and
  2. a **match-setup guard** (same family as `_assert_trap_sets_absent`) fails the
     match setup with an explicit error if any foreign active limit version would
     join the fixture portfolio's monitoring run — silent score drift becomes an
     honest infra failure.
  A model-passable limit-allowlist in the queue envelope was considered and
  **rejected**: letting the agent name the limit set would let it shrink monitoring
  coverage, violating the server-authoritative-coverage principle the fan-out
  governance already establishes.
- **Seeded breach state:** one completed `LimitMonitoringRun` (valuation yesterday
  EOD) + `LimitEvaluation` rows (exactly one `breach`) + `LimitSourceReference` →
  seeded completed `RiskRun` (CONSUME pattern) + one open `LimitIncident` with its
  `opened` event.
- **Supporting rows:** pricing profile (with `valuation_date`), engine config, and
  deterministic multi-symbol market evidence (via the `source_evidence` seam — NOT a
  single-symbol `MarketSnapshot`). **No pinned ids in the `pricing_profiles`
  namespace** (Run #34 retire-not-delete lesson); all cross-references via
  `$seed.<ns>.<alias>.id`.

### Fixture-loader extension (explicit work, not an afterthought)

`golden_workflows/fixtures.py` validates namespaces against a fixed set — unknown
namespaces fail the load, so every new row type is loader work, not just fixture
JSON. The plan must add, for each new namespace (`risk_limits` + versions,
`limit_monitoring_runs`, `limit_evaluations`, `limit_source_references`,
`limit_incidents`, `limit_incident_events`, engine config / market evidence as
needed): schema validation, required fields, `$seed` FK alias resolution, FK-safe
insertion ordering, ORM row construction with timestamp parsing, arena ownership
markers, and load / reseed / purge tests per row type.

Truth harvest (Spec A pattern, extended in `golden_workflows/determinism.py` +
`harvest_fixtures.py`):

1. Seed the book **without** the hedge → drive batch pricing → capture breach-side
   numbers (driver delta, breached net delta). These become the *authored* seeded
   evaluation/risk-run values, numerically consistent by construction.
2. Seed the full book **with** the hedge → drive the monitoring producer end-to-end
   (the exact path step 6 runs live) → capture the clean net delta + `ok` verdict
   into `risk-limit-breach-day.truth.json`.
3. `tests/test_arena_fixture_determinism.py` gains a monitoring-producer case:
   canonical payload byte-identical across runs (volatile keys stripped).

## Arena integration, tests, process

- **Purge:** limits rows are portfolio-scoped (`limit_monitoring_runs.portfolio_id`,
  `limit_incidents.portfolio_id`; evaluations/events cascade). The plan must
  *verify* `_delete_portfolios_with_dependents` FK introspection sweeps every new
  table — especially `risk_limits` scoping — and add explicit handling for any
  table without a `portfolio_id`/`position_id` column. Model-created monitoring
  runs are portfolio-scoped and inherit trace+baseline purge.
- **EFF/par:** ship **uncalibrated** (no `par_tool_calls` → legacy hyperbolic EFF);
  calibrate only after the first live board shows a realistic counted run.
- **Tests:** workflow-loads + pinned denominator (new file; flagship pins
  untouched); golden replay earns full marks (hand-written replay transcripts);
  determinism case; HITL exact-set update; skill-catalog exact-set updates (six
  files); grounding-targets-match-truth-file guard.
- **Process gates:** mandatory **pre-merge live smoke** (replay = satisfiability,
  never reachability): ≥1 full live match on the direct DeepSeek channel, then a
  per-check pass audit for unwinnable/free checks. `CHANGELOG.md` under
  `[Unreleased]`; `CLAUDE.md` section for the new tools/workflow; `README.md` if
  user-facing.

## Review log

- **Stage 2 spec gate** (2026-07-31): Tier 1 — Codex adversarial-review,
  `gpt-5.6-sol` @ xhigh, 1 iteration (per run parameters). Five findings, all
  applied: (1) mutations now require `expected_row_version` (no server-side
  refresh); (2) live-monitoring isolation via portfolio-scoped fixture limits + a
  foreign-active-limits match-setup guard (envelope allowlist rejected as
  model-shrinkable coverage); (3) latest-snapshot fallback removed in favor of
  deterministic multi-symbol `source_evidence`; (4) no-waive prohibition moved to
  session scope (per-step check was gameable via an early waive); (5) fixture-loader
  namespace extension specified as explicit work.

## Non-goals

- No limit-definition CRUD tools (governance edits stay in the workbench UI).
- No hedge-solver or curve coupling in the workflow ending.
- No changes to arena `CANDIDATE_MODELS`.
- No new migrations expected (all tables exist; verify during planning).

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

- **row_version stays server-side**: tools read the current `row_version` internally;
  models never juggle optimistic-concurrency tokens (that would grade API ergonomics,
  not ability).
- **Provenance threading**: `LimitActionContext` actor/persona/mode comes from the
  agent runtime, mirroring audit-trail capture.
- **Four-fold registration** (repo trap list): `QUANT_AGENT_TOOLS`
  (`tools/__init__.py`), `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`), and all
  three HITL structures in `services/deep_agent/hitl.py` (`INTERRUPT_TOOL_NAMES`,
  `_RISK_LEVEL_BY_TOOL` → `"write"`, `_LABEL_BY_TOOL`). Write tools carry
  `__capability_group__ = DOMAIN_WRITE` so audit capture and fan-out read-only
  classification work unchanged.
- `run_limit_monitoring` defaults `market_snapshot_id` to the fixture-provided /
  latest snapshot when the model does not supply one.

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
   verification pending): `tool_not_called: waive_limit_incident`. Wording must not
   leak the answer.
6. **Re-monitor.** `run_limit_monitoring` with `source_policy: force_refresh`
   (`tool_called` with args + `task_returned_id`), read the completed run, quote the
   clean net delta (grounding — the only live-computed number, via deterministic
   batch pricing). `expected_skill: null` (monitor-limits already routed in step 1 —
   skills_routed dedup blind spot).
7. **Confirm closure.** Read the incident: the clean run auto-recovered it.
   `answer_field_equals: status=recovered` + prohibition
   `tool_not_called: resolve_limit_incident` (state-awareness discriminator).
8. **Session-level success assertions** only for facts not already scored per-step
   (double-jeopardy rule).

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
  vega cap, so triage requires discrimination.
- **Seeded breach state:** one completed `LimitMonitoringRun` (valuation yesterday
  EOD) + `LimitEvaluation` rows (exactly one `breach`) + `LimitSourceReference` →
  seeded completed `RiskRun` (CONSUME pattern) + one open `LimitIncident` with its
  `opened` event.
- **Supporting rows:** pricing profile (with `valuation_date`), engine config,
  market snapshot. **No pinned ids in the `pricing_profiles` namespace** (Run #34
  retire-not-delete lesson); all cross-references via `$seed.<ns>.<alias>.id`.

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

## Non-goals

- No limit-definition CRUD tools (governance edits stay in the workbench UI).
- No hedge-solver or curve coupling in the workflow ending.
- No changes to arena `CANDIDATE_MODELS`.
- No new migrations expected (all tables exist; verify during planning).

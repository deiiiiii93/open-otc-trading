# Risk Limits Module Design

**Date:** 2026-07-17
**Status:** Approved for implementation planning
**Scope:** Open OTC Trading backend, frontend, task runtime, scheduler, reporting, and DeepAgents runtime

## 1. Summary

Add a first-class **Limits** module for defining, monitoring, investigating, and
reporting OTC risk limits. The module supports human and agent operation under the
platform's existing **Interactive / Auto / YOLO** authority modes.

The first release covers:

- Greek limits: delta, gamma, vega, theta, rho, and rho_q (dividend rho).
- VaR and CVaR limits with an explicitly pinned methodology, horizon, and confidence.
- Named stress-test loss limits.
- Versioned limit definitions and activation history.
- Manual and scheduled monitoring through the same deterministic pipeline.
- Warning and breach incidents with acknowledgement, assignment, waiver, recovery,
  and resolution history.
- Evidence-linked limit-analysis reports.
- A dedicated `limit_manager` DeepAgents persona.
- A single `/limits` frontend module with Monitor, Definitions, Breaches, Schedules,
  and Reports tabs.

Numeric breach truth is always produced by deterministic server-side evaluators.
Agents explain, investigate, propose, and report; they do not determine breach truth
in prose.

## 2. Current-State Findings

The repository contains useful pilot components but not a production limits domain:

- `backend/app/services/risk_limits.py` reads synthetic
  `RiskRun.metrics["limit_breaches"]` values. The real batch-pricing producer does not
  persist this field, so recent real runs have no authoritative breach artifact.
- The seeded `morning-risk-breach-commentary` workflow and
  `assemble_breach_report` demonstrate server-authoritative scope plus deterministic
  fan-out coverage reconciliation. These invariants should be retained.
- `risk_manager` already covers exposure and hedge feasibility, but formal limit
  governance warrants a separate `limit_manager` persona.
- `backend/app/services/deep_agent/scheduler.py` schedules dependency-linked agent
  tasks from a plan; it is not a wall-clock cron scheduler.
- `TaskRun`, `RiskRun`, `ScenarioTestRun`, `BacktestRun`, `ReportJob`, audit events,
  DeepAgents HITL, and the existing gateway provide reusable runtime surfaces.
- `calculate_portfolio_risk` produces point-in-time Greeks and a
  `one_day_var_proxy`. Scenario tests produce a scenario-distribution VaR/CVaR block;
  backtests produce empirical historical VaR/CVaR. These measures are not
  interchangeable, so every definition must pin its source and methodology.

## 3. Goals and Non-Goals

### Goals

- Persist limit definitions as versioned governance objects rather than JSON embedded
  in a risk result.
- Make each evaluation reproducible from pinned definitions and source-run evidence.
- Treat warning, breach, and unknown as separate operational outcomes.
- Use one monitoring pipeline for manual, agent-triggered, and scheduled execution.
- Make scheduling restart-safe, timezone-aware, idempotent, and observable.
- Preserve complete incident and notification history.
- Allow humans and agents to manage limits through the existing authority modes.
- Keep large-breach analysis complete through server-side enumeration and read-only
  fan-out.

### Non-goals for the First Release

- A general-purpose expression language for arbitrary user-written formulas.
- Broker execution or automatic hedging from a breach.
- Silent substitution of a different VaR, CVaR, stress, currency, or pricing
  methodology when the configured source is unavailable.
- Agent-authored numeric evaluations.
- Email or arbitrary outbound webhooks; warning delivery initially uses the web app,
  Agent Desk, and configured IM gateway channels.

## 4. Architecture

```text
Limit definitions + schedules
            |
            v
Source collection (RiskRun / ScenarioTestRun / BacktestRun)
            |
            v
Deterministic limit evaluator
            |
            v
Monitoring run -> evaluations -> incident transitions
            |
            +----> dashboard and breach ledger
            +----> notification outbox
            +----> evidence snapshot -> Limit Manager report
```

The backend is split into five responsibilities:

1. **Definition service** — stable identities, immutable versions, activation, and
   optimistic concurrency.
2. **Source collector** — resolves or refreshes exact risk/scenario sources using the
   selected pricing and methodology configuration.
3. **Evaluator** — computes observed values, transforms, utilization, headroom, and
   deterministic status.
4. **Incident service** — maintains persistent breach episodes and their event history.
5. **Scheduler** — claims durable occurrences and invokes the same monitoring pipeline
   as manual runs.

Recommended implementation layout:

```text
backend/app/routers/limits.py
backend/app/services/limits/
  definitions.py
  metrics.py
  sources.py
  evaluator.py
  incidents.py
  monitoring.py
  schedules.py
  notifications.py
  reports.py
backend/app/tools/limits.py
backend/app/services/deep_agent/prompts/limit_manager.md
backend/app/skills/workflows/limits/
frontend/src/routes/Limits.tsx
frontend/src/routes/Limits.live.tsx
frontend/src/routes/Limits.css
```

## 5. Persistence Model

### 5.1 `RiskLimit`

Stable identity across versions:

- `id`
- `key` — stable unique machine identifier
- `name`
- `description`
- `category` — `greek`, `var`, `cvar`, or `stress`
- `owner`
- `tags`
- `active_version_id`
- `created_by_actor`, `created_by_persona`
- `row_version` — optimistic concurrency token
- `created_at`, `updated_at`

An active limit is never edited in place.

### 5.2 `RiskLimitVersion`

Immutable definition version:

- `id`, `risk_limit_id`, `version`
- `state` — `draft`, `active`, `superseded`, or `retired`
- `metric_kind` — `delta`, `gamma`, `vega`, `theta`, `rho`, `rho_q`, `var`,
  `cvar`, or `stress_pnl`
- `source_kind` — `risk_run`, `scenario_test`, or `backtest`
- `methodology` — typed JSON for confidence, horizon, scenario selection, sampling
  convention, and other source-specific parameters
- `scope_type` — `portfolio`, `underlying`, `product_family`, or `position`
- `scope_config` — selected target and any include/exclude selectors
- `aggregation` — `net`, `gross_abs`, `max_abs`, `minimum`, or `maximum`
- `transform` — `signed`, `absolute`, or `loss_magnitude`
- `comparator` — `upper`, `lower`, or `range`
- warning and hard lower/upper thresholds
- `unit`, `currency`
- Greek bump/unit convention, including rho_q
- `effective_from`, `effective_until`
- `rationale`
- creation and activation actor/persona/mode metadata
- `created_at`, `activated_at`

Unique constraint: `(risk_limit_id, version)`.

### 5.3 `LimitMonitoringRun`

One replayable execution:

- `id`
- `trigger` — `manual`, `agent`, or `scheduled`
- `mode` — snapshotted `interactive`, `auto`, or `yolo`
- `schedule_id`, `occurrence_id` when scheduled
- `portfolio_id`
- `pricing_parameter_profile_id`, `engine_config_id`
- `valuation_as_of`
- `source_policy`
- `status` — `queued`, `running`, `completed`,
  `completed_with_unknowns`, or `failed`
- `summary`
- definition snapshot/hash
- `started_at`, `finished_at`, `created_at`

An association table pins every evaluated `RiskLimitVersion`.

### 5.4 `LimitSourceReference`

Evidence used by a monitoring run:

- `monitoring_run_id`
- `source_kind`
- optional `risk_run_id`, `scenario_test_run_id`, or `backtest_run_id`
- requested source parameters
- source status and freshness
- completeness diagnostics
- source valuation and creation timestamps

### 5.5 `LimitEvaluation`

One limit-version/scope result:

- `monitoring_run_id`, `limit_version_id`
- `scope_type`, `scope_key`, `scope_label`
- `observed_value`
- `adverse_value`
- warning and hard thresholds copied from the version
- `utilization`
- `headroom`
- `status` — `ok`, `warning`, `breach`, or `unknown`
- `reason_code`, `reason`
- coverage count/ratio
- structured evidence references
- `evaluated_at`

Unique constraint:
`(monitoring_run_id, limit_version_id, scope_key)`.

### 5.6 `LimitIncident` and `LimitIncidentEvent`

`LimitIncident` is the current episode projection:

- stable limit id and evaluated scope
- current severity and lifecycle status
- first/last evaluation ids
- first seen, last seen, acknowledged, waived, resolved timestamps
- owner/assignee
- waiver expiry and rationale
- optimistic `row_version`

`LimitIncidentEvent` is the immutable timeline:

- `opened`, `repeated`, `escalated`, `acknowledged`, `assigned`, `commented`,
  `waived`, `waiver_expired`, `recovered`, `resolved`, or `reopened`
- evaluation reference
- actor, persona, mode, thread, and audit attribution
- structured payload and timestamp

The service enforces at most one active episode per `(risk_limit_id, scope_key)`.

### 5.7 `LimitMonitoringSchedule` and `LimitScheduleOccurrence`

Schedule configuration:

- name, description, owner
- cron expression and IANA timezone
- mode
- selected portfolios and limits
- pricing profile, engine configuration, scenario inputs, and valuation policy
- source refresh/reuse policy and maximum age
- misfire grace/policy
- overlap policy
- retry limit/backoff
- notification configuration
- enabled state, next run, and last run
- optimistic `row_version`

Each expected firing becomes a `LimitScheduleOccurrence` with:

- unique `(schedule_id, scheduled_for)`
- status, attempt count, lease owner, lease expiry
- `TaskRun` and `LimitMonitoringRun` references
- timestamps and terminal error

### 5.8 Notification Outbox and Reports

A transactional `LimitNotification` outbox stores:

- incident/evaluation reference
- event type, severity, channel, destination
- immutable payload
- unique deduplication key
- delivery status, attempts, next retry, and error

Existing `ReportJob` is extended with a `limit_monitoring_run_id` link and a
`limit_analysis` report type. Each regeneration creates a new job/artifact. The
evidence snapshot and its hash are preserved with the report so prose cannot drift
from source facts.

`TaskRun` receives a nullable `limit_monitoring_run_id`.

## 6. Definition and Evaluation Contract

### 6.1 Supported Metrics

First-release metric catalog:

- `delta`
- `gamma`
- `vega`
- `theta`
- `rho`
- `rho_q` / dividend rho
- `var`
- `cvar`
- `stress_pnl`

The registry is extensible without adding an arbitrary expression language.

### 6.2 Scope and Aggregation

Supported scopes:

- entire portfolio
- each selected underlying
- each selected product family
- an individual position

Supported aggregations:

- `net`
- `gross_abs`
- `max_abs`
- `minimum`
- `maximum`

Portfolio views can represent desk/book groupings without inventing a second
hierarchy in the Limits module.

### 6.3 Transform and Sign Semantics

The adapter first produces a source-native observed value, then a deterministic
adverse value:

- `signed` retains direction.
- `absolute` applies `abs(value)`.
- `loss_magnitude` maps a negative P&L/tail result to a non-negative loss magnitude.

VaR, CVaR, and stress-loss limits default to `loss_magnitude`; larger always means
more adverse. Greek limits can use signed, absolute, or range thresholds.

The limit version stores the exact unit and bump convention. In particular, rho and
rho_q cannot be compared across definitions unless their bump convention matches.

### 6.4 Status

- `ok` — adverse value has not reached the warning threshold.
- `warning` — warning threshold reached, hard threshold not reached.
- `breach` — hard threshold reached or exceeded.
- `unknown` — evidence is missing, stale, incomplete, failed, incompatible, or cannot
  be converted consistently.

`unknown` is never collapsed to zero or “within limit.” It can open a separate
data-quality incident but is not recorded as a risk breach.

### 6.5 Utilization and Headroom

For upper/loss limits:

```text
utilization = adverse_value / hard_threshold
headroom    = hard_threshold - adverse_value
```

For lower or range limits, the evaluator calculates utilization against the nearest
adverse boundary and records which boundary governed the result. `100%` means the
hard limit is reached.

## 7. Source Adapters

### 7.1 Greeks

The risk-run adapter reads:

- portfolio/shared totals
- currency buckets for monetary Greeks
- per-position rows for underlying, family, and position aggregation

Mixed-currency monetary aggregates require an explicit reporting currency and pinned
FX evidence. Missing conversion produces `unknown`.

If an in-scope position failed pricing or Greek calculation:

- an affected portfolio aggregate is `unknown`
- an affected underlying/family aggregate is `unknown`
- unrelated scopes can still evaluate

### 7.2 VaR and CVaR

The definition pins:

- source (`scenario_test` or `backtest`)
- method (`scenario_distribution`, `historical`, or a later registered method)
- confidence
- horizon and scaling convention
- P&L population and currency

The current scenario-test adapter reads `results.var_cvar`; the current backtest
adapter reads `var_95`/`cvar_95`. A confidence or methodology mismatch produces
`unknown`; the evaluator does not silently substitute another measure.

### 7.3 Stress Loss

The scenario-test adapter selects an exact named scenario (or explicitly configured
worst-of set) from `results.scenarios`. It records scenario name, scenario-set
definition, portfolio P&L, P&L percentage, and source run.

Missing or renamed scenarios produce `unknown`; they are not replaced by the nearest
available scenario.

## 8. Monitoring Pipeline

Manual, agent, and scheduled monitoring all invoke the same service:

1. Validate request and resolve portfolio.
2. Snapshot active limit versions effective at the valuation time.
3. Build the minimal source plan by grouping compatible definitions.
4. Reuse eligible completed source runs or refresh them according to policy.
5. Persist source references and completeness diagnostics.
6. Evaluate every applicable definition/scope deterministically.
7. Persist evaluations.
8. Reconcile incident transitions.
9. Commit notification outbox rows in the same transaction.
10. Finish the linked `TaskRun`.
11. Optionally dispatch analysis reporting under the selected mode.

The composite monitoring worker calls internal synchronous source services rather
than queuing child tasks and blocking. This avoids deadlock when the process-local
executor has one worker. Source runs remain persisted and linked just as they are for
standalone execution.

A business breach does not fail the task:

- all evaluable results persisted -> `completed`
- one or more unknowns -> `completed_with_unknowns`
- infrastructure/evaluator failure -> `failed`

## 9. Scheduling

The scheduler is a database-claimed service, separate from the DeepAgents plan
scheduler.

Default policies:

- **Misfire:** run once if recovered within the grace period; otherwise persist
  `missed`.
- **Overlap:** coalesce when a previous occurrence remains active.
- **Retry:** bounded retries for infrastructure failures only.
- **Timezone:** calculate occurrences in the configured IANA timezone and define DST
  behavior explicitly.

The scheduler loop:

1. Find due enabled schedules.
2. Transactionally insert/claim the unique occurrence.
3. Set a bounded lease.
4. Queue the monitoring task.
5. Renew or release the lease as work progresses.
6. Reclaim expired leases after restart.

Enabling a schedule pre-authorizes deterministic monitoring occurrences. The stored
mode governs optional agent follow-up actions. Mode changes approval behavior, not
task scope; YOLO cannot invent work outside the configured schedule or user request.

## 10. Incidents and Warnings

Incident transitions create warnings when a result:

- enters warning or breach
- escalates in severity or utilization
- reopens after recovery
- remains unresolved past its reminder interval
- becomes unknown for a configured data-quality condition

Repeated identical evaluations extend the incident timeline without generating
duplicate warnings. Notification deduplication, cooldown, retry, and delivery failure
are handled by the outbox.

Each warning contains:

- limit and evaluated scope
- observed and adverse values
- warning/hard thresholds
- utilization and headroom
- valuation time and source freshness
- incident deep link

Initial delivery channels:

- persistent Limits dashboard/ledger
- selected Agent Desk thread
- existing configured IM gateway

The ledger, not a transient toast or message, is authoritative.

## 11. Analysis Reports

The report writer receives a frozen, server-generated evidence snapshot. The Limit
Manager cannot alter figures or omit breaches, failures, or unknown evaluations.

Standard structure:

1. Executive verdict and data-quality status
2. New, escalated, continuing, and recovered incidents
3. Observed values, thresholds, utilization, headroom, and trends
4. Largest position and underlying contributors
5. VaR/CVaR and named stress results
6. Source freshness and incomplete coverage
7. Proposed remediation or definition changes
8. Acknowledgements, waivers, approvals, and audit history

Reports are immutable artifacts. Regeneration either reuses the same evidence snapshot
and creates a new version or explicitly targets a newer monitoring run.

## 12. Interactive / Auto / YOLO Authority

The Limits module uses the existing HITL model.

### Interactive

Every persisted agent action requires approval, including drafts, monitoring runs,
incident changes, schedules, and reports.

### Auto

Ordinary reversible actions run automatically:

- create/update drafts
- run monitoring
- acknowledge/assign incidents
- generate reports
- create or edit disabled schedules

Governance-critical actions remain gated:

- activate, supersede, or deactivate active versions
- enable or materially change enabled schedules
- waive or resolve breaches
- delete governance records

These actions are classified at the existing irreversible risk level even when a
technical inverse operation exists, because their operational effect is material.

### YOLO

All explicitly requested or configured actions may run without HITL, including
governance-critical actions. Scope validation, optimistic concurrency, evidence
requirements, and durable auditing still apply.

## 13. Limit Manager

`limit_manager` is a fourth DeepAgents persona.

Responsibilities:

- limit definitions and version governance
- monitoring and breach interpretation
- incident handling
- schedule management
- evidence-linked analysis reports
- remediation and limit-change proposals

`risk_manager` remains responsible for exposure analysis, scenarios, and hedge
feasibility. Cross-persona example:

```text
"Are we within the vega limit?" -> limit_manager
"How should we hedge this vega breach?" ->
    limit_manager establishes the breach and evidence,
    risk_manager analyzes hedge feasibility
```

Read tools:

- list/get definitions and versions
- read dashboard, monitoring runs, evaluations, incidents, schedules, reports, and
  evidence
- compare utilization and incident history

Write tools:

- create/update drafts
- activate/deactivate versions
- run monitoring
- acknowledge/assign/comment/waive/resolve/reopen incidents
- create/update/enable/disable/run schedules
- generate analysis reports

Every write uses optimistic concurrency and records actor, persona, mode, thread, and
audit attribution.

Typed outputs:

- `LimitAnalysis`
- `LimitChangeProposal`
- `IncidentCommentary`

Large breach sets may use governed fan-out:

1. server enumerates the authoritative incident/evaluation ids
2. one read-only investigator analyzes each item
3. deterministic reconciliation marks every id covered or failed

Fan-out workers cannot change definitions, incidents, schedules, or artifacts.

The seeded morning-breach workflow migrates from synthetic
`RiskRun.metrics.limit_breaches` to real evaluations/incidents.

## 14. API

Suggested REST surface:

```text
GET    /api/limits
POST   /api/limits
GET    /api/limits/{limit_id}
PATCH  /api/limits/{limit_id}
POST   /api/limits/{limit_id}/versions
GET    /api/limits/{limit_id}/versions
POST   /api/limits/{limit_id}/versions/{version_id}/activate
POST   /api/limits/{limit_id}/deactivate

GET    /api/limit-monitoring/dashboard
POST   /api/limit-monitoring/runs
GET    /api/limit-monitoring/runs
GET    /api/limit-monitoring/runs/{run_id}
GET    /api/limit-monitoring/runs/{run_id}/evaluations
POST   /api/limit-monitoring/runs/{run_id}/reports

GET    /api/limit-incidents
GET    /api/limit-incidents/{incident_id}
POST   /api/limit-incidents/{incident_id}/acknowledge
POST   /api/limit-incidents/{incident_id}/assign
POST   /api/limit-incidents/{incident_id}/comments
POST   /api/limit-incidents/{incident_id}/waive
POST   /api/limit-incidents/{incident_id}/resolve
POST   /api/limit-incidents/{incident_id}/reopen

GET    /api/limit-schedules
POST   /api/limit-schedules
GET    /api/limit-schedules/{schedule_id}
PATCH  /api/limit-schedules/{schedule_id}
POST   /api/limit-schedules/{schedule_id}/enable
POST   /api/limit-schedules/{schedule_id}/disable
POST   /api/limit-schedules/{schedule_id}/run-now
GET    /api/limit-schedules/{schedule_id}/occurrences
```

All mutations accept an expected `row_version` or equivalent conditional token.

## 15. Frontend

One `/limits` route with shared portfolio and valuation context.

### Monitor

- cards for active breaches, warnings, unknowns, highest utilization, and last
  successful check
- utilization table grouped by category and scope
- observed value, thresholds, headroom, trend, freshness, and owner
- detail drawer for evidence, contributors, source runs, and incident history
- Run now, Generate analysis, and Ask Limit Manager actions

### Definitions

- master-detail list/editor
- filters by category, scope, owner, and state
- typed metric/methodology/scope/threshold form
- version timeline and diff
- mode-governed activation

### Breaches

- filterable persistent incident ledger
- severity, lifecycle state, scope, owner, and age
- acknowledgement, assignment, comments, waiver, resolution, and reopen actions
- evaluation, notification, report, and audit timeline

### Schedules

- cron-style table with expression, timezone, mode, scope, next/last run, latest
  result, and enabled state
- human-readable upcoming occurrences
- edit, enable/disable, Run now, and execution history

### Reports

- reuse `ReportTimeline` and `ReportReader`
- deep links back to monitoring run and incidents

The global shell can poll a compact summary endpoint for a sidebar breach badge and
new-warning toast. The persistent ledger remains the source of truth. Page context
exposes selected portfolio, limit, incident, run, and permitted actions to the
floating Limit Manager.

## 16. Failure Handling

- Stale data follows the definition's freshness policy and becomes `unknown`.
- Historical profile-dated results cannot satisfy current-risk limits unless
  explicitly allowed.
- Mixed-currency values require pinned FX conversion.
- In-scope pricing/Greek failures make affected aggregates unknown; unrelated scopes
  remain evaluable.
- Active versions are snapshotted before collection/evaluation.
- Report failure does not roll back monitoring or incidents.
- Notification failure retries without rerunning monitoring.
- Scheduler occurrences are idempotent and lease-recoverable.
- Audit persistence failure blocks governance-changing agent actions.
- Source, evaluation, and incident errors use stable reason codes plus human-readable
  details.

## 17. Verification

### Evaluator Unit Tests

- delta, gamma, vega, theta, rho, and rho_q
- signed, absolute, loss-magnitude, upper, lower, and range limits
- net, gross-absolute, max-absolute, min, and max aggregation
- exact warning and hard-boundary behavior
- VaR/CVaR methodology, confidence, horizon, and sign normalization
- named stress selection and missing-scenario behavior
- currency conversion and missing FX
- partial pricing/Greek failures and scope-aware coverage

### Persistence and Service Tests

- version creation, activation, supersession, and concurrency conflicts
- monitoring-run snapshot and evidence links
- incident open, repeat, escalation, recovery, waiver, expiry, resolve, and reopen
- outbox deduplication and retry
- report evidence immutability
- migration and bootstrap schema behavior

### Scheduler Tests

- occurrence uniqueness and concurrent claim
- lease renewal and restart recovery
- DST boundaries
- misfire grace and missed recording
- overlap coalescing
- bounded retries
- manual and scheduled runs producing identical evaluations from identical evidence

### API and Frontend Tests

- CRUD, activation, monitoring, incident, schedule, and report endpoints
- task polling and deep links
- route round trip and page context
- dashboard loading/empty/error states
- definition and schedule validation
- breach ledger actions
- warnings and report reader

### Agent Tests

- orchestrator routing between `risk_manager` and `limit_manager`
- tool registration and narrow task scopes
- Interactive/Auto/YOLO behavior for every write
- evidence preservation in typed artifacts and reports
- server-authoritative fan-out coverage
- read-only fan-out enforcement

Deterministic acceptance tests do not depend on live LLM output.

## 18. Implementation Slices

1. **Domain foundation:** migrations, models, definition/version service, metric
   adapters, evaluator, and unit tests.
2. **Monitoring and incidents:** source collection, monitoring tasks, evidence,
   incident lifecycle, API, and task links.
3. **Limits UI:** route, Monitor, Definitions, Breaches, and manual Run now.
4. **Durable scheduling and warnings:** schedules, occurrences, leases, outbox,
   sidebar/toast, Agent Desk, and IM delivery.
5. **Limit Manager and reports:** persona, tools, routing, skills, HITL/audit wiring,
   analysis artifacts, and migrated morning-breach workflow.
6. **End-to-end hardening:** migrations, restart recovery, mixed-currency and partial
   coverage cases, full backend/frontend suites, and browser smoke.

Each slice must leave a task-linked, inspectable vertical path rather than an isolated
model or screen.

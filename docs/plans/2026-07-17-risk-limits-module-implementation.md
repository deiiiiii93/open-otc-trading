# Risk Limits Module Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a production-grade Limits module for versioned Greek, VaR/CVaR, and stress limits; deterministic monitoring and incidents; durable schedules and warnings; evidence-frozen analysis reports; and a dedicated `limit_manager` persona governed by Interactive / Auto / YOLO.

**Architecture:** Keep numeric truth in a deterministic limits domain. A composite monitoring task snapshots active definitions, resolves or refreshes persisted risk/scenario/backtest evidence through short committed phases, then atomically evaluates every applicable scope, reconciles incidents, and creates warning-outbox records. A database-claimed scheduler materializes cron occurrences, an independently enabled notification consumer delivers Agent Desk rows, and the gateway-owning runtime delivers IM rows. The React `/limits` route and the `limit_manager` persona both call the same typed services and APIs.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite/PostgreSQL-compatible SQL, `croniter` plus stdlib `zoneinfo`, the existing thread-pool task runner and DeepAgents/HITL/audit runtime, React 19, TypeScript, Radix UI, Recharts, Warm Ledger tokens, pytest, Vitest, and Testing Library.

---

## Source of truth and execution context

Implement against the approved design:

- `docs/plans/2026-07-17-risk-limits-module-design.md`

Use the isolated worktree and branch already prepared:

```bash
cd /Users/fuxinyao/open-otc-trading/.claude/worktrees/risk-limits
git branch --show-current
# expected: codex/risk-limits
git status --short
# expected before implementation: only this plan after it is committed
```

The worktree does not own a virtual environment. Run backend tests with the main
checkout's interpreter while forcing imports to this worktree:

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest <tests>
```

Run frontend commands from the worktree with `npm --prefix frontend ...`.
Before each commit, run `git diff --check`. Stage only the files named by the
current task.

## Locked implementation resolutions

These choices resolve ambiguities between the design and the current runtime.
Do not silently change them while implementing.

1. **Task status and business status are separate.** `TaskRun` keeps its existing
   terminal statuses. A monitoring task that persisted all results finishes as
   `completed`, even when one or more evaluations are `unknown`.
   `LimitMonitoringRun.status` is `completed` or `completed_with_unknowns`.
   Breaches and warnings are business results, not task errors.
2. **No nested async tasks.** The composite monitoring worker calls extracted
   synchronous risk, scenario-test, and backtest helpers. It must never queue a
   child task and wait, because `async_task_workers` defaults to one.
3. **Source reuse is exact and fail-closed.** A newer queued/failed source never
   masks an older eligible terminal source. Reuse requires an exact portfolio,
   position scope, pricing profile, engine config, market snapshot/effective
   market-evidence identity, methodology/config, valuation policy, and freshness
   match. Missing or incompatible evidence becomes `unknown`, never zero.
4. **Partial source errors remain inspectable.** A `RiskRun` with
   `completed_with_errors` may be reused. Failed in-scope positions make their
   portfolio/underlying/family scopes unknown while unrelated scopes remain
   evaluable. Scenario/backtest `empty`, queued, running, or failed runs are not
   usable numeric evidence.
5. **VaR meanings are not interchangeable.** Scenario VaR/CVaR is accepted only
   as `scenario_distribution`, confidence `0.95`, horizon `scenario_set`, with no
   scaling. Backtest VaR/CVaR is accepted only as `historical`, confidence `0.95`,
   horizon `1_trading_day`, with no scaling. `RiskRun.one_day_var_proxy` is not a
   v1 limit source.
6. **RhoQ is first-class.** `rho_q` has the same coverage, aggregation, currency,
   bump-convention, evaluator, API, UI, and test treatment as rho.
7. **Avoid a circular schema dependency.** `RiskLimitVersion.risk_limit_id` is a
   database foreign key. `RiskLimit.active_version_id` is an indexed nullable
   integer validated by the definition service, not a second database FK. This
   keeps Alembic round-trips portable on SQLite while retaining the approved
   pointer.
8. **Schedules may target several portfolios.** One durable occurrence represents
   one cron firing. `LimitScheduleOccurrenceRun` links that firing to one
   monitoring attempt per target portfolio; retries create another attempt row
   without erasing prior evidence.
9. **DST policy is explicit.** Five-field cron expressions use an IANA timezone.
   A nonexistent spring-forward wall time is recorded as missed with
   `dst_nonexistent`; an ambiguous fall-back wall time runs once at `fold=0`.
10. **Mode affects authority, not numeric scope.** Interactive gates every limits
    write. Auto runs ordinary reversible writes but gates activation, retirement,
    active schedule changes, waivers, and resolution/reopen. YOLO bypasses HITL
    only for explicitly requested/configured work; validation, optimistic
    concurrency, audit, and evidence rules remain active. Persisted limit
    identities and versions are never hard-deleted.
11. **Agent prose cannot replace evidence.** Limit reports always render every
    frozen evaluation/unknown/incident row from server data. Optional Limit
    Manager commentary is keyed to server ids and cannot change or suppress
    canonical numeric tables.
12. **The scheduler ships safely.** Add the runtime and lifecycle wiring, but
   default `OPEN_OTC_LIMIT_SCHEDULER` to `off` for rollout. Manual monitoring and
   the persistent web warning ledger work regardless. The notification drain has
   its own `OPEN_OTC_LIMIT_NOTIFICATIONS` switch and defaults on, so manual
   warnings can still reach configured Agent Desk/IM destinations. Documentation
   must show how to enable the stable, non-reload scheduler worker.
13. **Monitoring uses short committed phases.** Commit the task/domain running
    state, compute without an open write transaction, persist each completed
    source artifact and its monitoring-run `LimitSourceReference` in a short
    transaction, then atomically persist evaluations, incident transitions,
    notification rows, and terminal monitoring status. A later evaluator failure
    must not erase already completed source evidence or its linkage/diagnostics.
14. **The gateway owner owns IM delivery.** Limits code creates durable IM outbox
    rows but never reaches connector instances. Only the process holding the
    gateway worker lease claims and sends IM rows; takeover resumes expired
    claims. Agent Desk delivery remains in the independent Limits notification
    consumer.
15. **Schedule enablement pre-authorizes deterministic occurrences.** Enabling a
    schedule is itself governance-gated. Once enabled, its due numeric monitoring
    runs without a second unattended HITL pause; the snapshotted Interactive /
    Auto / YOLO mode governs optional agent follow-up/report actions, never
    changes configured numeric scope, and never authorizes invented work.
16. **The seeded morning workflow is Auto/YOLO only.** Its current multi-step
    runner cannot suspend the script and later resume the same step after an
    Interactive HITL decision. Reject Interactive launch before any step or pin
    mutation. Interactive users retain the same governed Limits tools one action
    at a time; this restriction applies only to the autonomous seeded workflow.

## Delivery sequence

The sequence below deliberately leaves an inspectable vertical path after each
slice:

1. Core domain and definitions.
2. Deterministic evaluation and source refresh/reuse.
3. Monitoring, incidents, and core API.
4. `/limits` Monitor, Definitions, and Breaches.
5. Durable schedules and Schedules UI.
6. Warning outbox, Agent Desk, and IM delivery.
7. Evidence-frozen reports and report/task/global-warning integration.
8. Limit tools, `limit_manager`, and the migrated morning workflow.
9. Full regression, browser smoke, documentation, and controlled enablement.

### Task 1: Add core Limits persistence and migration

**Files:**

- Create: `backend/alembic/versions/0046_risk_limits_core.py`
- Create: `tests/test_migration_0046.py`
- Create: `tests/test_limits_models.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`

**Step 1: Write the failing migration and ORM tests.**

Cover:

- `revision = "0046_risk_limits_core"` and
  `down_revision = "0045_arena_run_trials"`, matching the live migration head;
- upgrade/downgrade on an isolated SQLite database;
- Alembic upgraded through `0045_arena_run_trials`, then current
  `database.init_db()`/`Base.metadata.create_all()`, then `upgrade head`, proving
  table/column/index guards tolerate the repository's dual bootstrap path;
- expected tables, columns, indexes, and unique constraints;
- ORM round-trips for JSON snapshots and enum-like string fields;
- `(risk_limit_id, version)` uniqueness;
- `(monitoring_run_id, limit_version_id, scope_key)` uniqueness;
- at most one non-terminal incident projection per `(risk_limit_id, scope_key)`
  through a partial unique index;
- `TaskRun.limit_monitoring_run_id`;
- boot repair of that nullable link on a pre-0046 `task_runs` table.

**Step 2: Run the tests and verify they fail because the models/migration do not exist.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_migration_0046.py tests/test_limits_models.py -q
```

**Step 3: Add the minimal models and migration.**

Add:

- `RiskLimit`
- `RiskLimitVersion`
- `LimitMonitoringRun`
- `LimitMonitoringRunVersion`
- `LimitSourceReference`
- `LimitEvaluation`
- `LimitIncident`
- `LimitIncidentEvent`

Add `TaskKind.LIMIT_MONITORING` and the nullable `TaskRun` relationship.
Use explicit string lengths, JSON defaults, timestamps, indexes, and named
constraints. Store the monitoring definition snapshot and its SHA-256 hash on
the run. Store copied thresholds and structured evidence on every evaluation.
Use migration-local table declarations; do not import ORM metadata into Alembic.
Guard table, column, and index creation so an ORM-first bootstrap can later run
Alembic without duplicate-object failures.

`Base.metadata.create_all()` will create new tables. Extend
`database._ensure_incremental_schema()` only for the nullable column/index added
to the already-existing `task_runs` table.

**Step 4: Run focused tests and the existing task-model slice.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_migration_0046.py tests/test_limits_models.py \
  tests/test_api.py tests/test_risk_engine.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/alembic/versions/0046_risk_limits_core.py \
  backend/app/models.py backend/app/database.py \
  tests/test_migration_0046.py tests/test_limits_models.py
git commit -m "feat(limits): add core persistence"
```

### Task 2: Implement immutable definition/version governance

**Files:**

- Create: `backend/app/services/limits/__init__.py`
- Create: `backend/app/services/limits/errors.py`
- Create: `backend/app/services/limits/contracts.py`
- Create: `backend/app/services/limits/definitions.py`
- Create: `tests/test_limit_definitions.py`

**Step 1: Write failing service tests.**

Test:

- creating a stable identity plus draft version;
- validating unique key, name, description, category, owner, and tags on that
  stable identity;
- adding sequential draft versions;
- activating one version and superseding the previous active version atomically;
- deactivating/retiring without deleting history;
- rejecting mutation of an active/superseded version;
- effective-from/effective-until lookup at the valuation instant;
- deterministic canonical snapshot/hash generation;
- stale `expected_row_version` conflicts;
- service validation of metric/source, scope, aggregation, transform, comparator,
  warning/hard thresholds, unit, reporting currency, bump convention, freshness,
  VaR methodology, and exact stress scenario selection;
- a dedicated valid `rho_q` definition.

Use an atomic update assertion:

```python
updated = definitions.update_metadata(
    session,
    limit_id=limit_row.id,
    expected_row_version=1,
    patch={"owner": "market-risk"},
    context=human_context,
)
assert updated.row_version == 2
with pytest.raises(LimitConflictError):
    definitions.update_metadata(
        session,
        limit_id=limit_row.id,
        expected_row_version=1,
        patch={"owner": "stale-writer"},
        context=human_context,
    )
```

**Step 2: Run and verify the missing-service failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_definitions.py -q
```

**Step 3: Implement the service.**

`contracts.py` owns closed literals/typed value objects used by evaluator,
schemas, and tools. Define v1 scope configuration explicitly:

- portfolio: applicable `portfolio_ids`;
- underlying: selected symbols or `all_in_portfolio`;
- product family: selected families or `all_in_portfolio`;
- position: selected position ids.

All mutators accept a trusted `LimitActionContext` containing actor, persona,
mode, thread id, and audit reference. The context is constructed by REST,
scheduler, or tool adapters; it is never model-authored input.

Implement optimistic concurrency with one conditional SQL `UPDATE ... WHERE
row_version = :expected`, checking `rowcount == 1`. Record domain audit events
through `services/audit.py::record_audit`.

**Step 4: Run focused tests.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_definitions.py tests/test_limits_models.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/__init__.py \
  backend/app/services/limits/errors.py \
  backend/app/services/limits/contracts.py \
  backend/app/services/limits/definitions.py \
  tests/test_limit_definitions.py
git commit -m "feat(limits): add versioned definition service"
```

### Task 3: Build the metric registry and pure evaluator

**Files:**

- Create: `backend/app/services/limits/metrics.py`
- Create: `backend/app/services/limits/evaluator.py`
- Create: `tests/test_limit_metrics.py`
- Create: `tests/test_limit_evaluator.py`

**Step 1: Write table-driven failing tests.**

Cover delta, gamma, vega, theta, rho, `rho_q`, VaR, CVaR, and stress P&L across:

- `net`, `gross_abs`, `max_abs`, `minimum`, and `maximum`;
- `signed`, `absolute`, and `loss_magnitude`;
- upper, lower, and range comparators;
- exact warning and hard-boundary behavior;
- nearest-boundary utilization/headroom for lower and range limits;
- empty/missing/stale/incompatible/partial observations;
- currency and bump-convention mismatches;
- stable reason codes and evidence propagation.

Include exact-boundary cases:

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [(79.999, "ok"), (80.0, "warning"), (99.999, "warning"), (100.0, "breach")],
)
def test_upper_limit_boundaries(value, expected):
    result = evaluate(upper_fixture(observed=value))
    assert result.status == expected
```

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_metrics.py tests/test_limit_evaluator.py -q
```

**Step 3: Implement a database-free evaluator.**

Use normalized observation/value objects, not ORM rows. Centralize comparison
tolerance and make equality reach the boundary. Preserve both source-native
`observed_value` and transformed `adverse_value`. `unknown` short-circuits
threshold math and keeps numeric fields nullable. The evaluator returns a
persistable result object but performs no writes.

**Step 4: Run the full evaluator matrix.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_metrics.py tests/test_limit_evaluator.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/metrics.py \
  backend/app/services/limits/evaluator.py \
  tests/test_limit_metrics.py tests/test_limit_evaluator.py
git commit -m "feat(limits): add deterministic evaluator"
```

### Task 4: Extract an inline persisted risk-source runner

**Files:**

- Modify: `backend/app/services/batch_pricing.py`
- Modify: `backend/app/services/risk_engine.py`
- Create: `tests/test_limit_risk_source_refresh.py`
- Modify: `tests/test_batch_pricing.py`

**Step 1: Add failing characterization tests.**

Prove that:

- the existing queued batch-pricing endpoint/task still creates one `RiskRun`,
  one `PositionValuationRun`, and one `TaskRun`;
- a new synchronous helper creates the same persisted risk/valuation evidence
  without creating or waiting for a child task;
- `engine_config_id`, `pricing_parameter_profile_id`, resolved position ids, and
  `market_snapshot_id`/effective market-evidence identity and `valuation_as_of`
  are identical on both paths;
- `rho_q` survives into shared/by-currency/per-position metrics;
- partial pricing errors yield `completed_with_errors` plus coverage diagnostics.

**Step 2: Run the focused tests and verify the helper is missing.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_risk_source_refresh.py tests/test_batch_pricing.py -q
```

**Step 3: Extract the shared synchronous core.**

Split `_execute_batch_pricing_task` into shared phases: resolve and copy immutable
inputs in a read-only session, close that session, perform the long quant
calculation without an open database write transaction, then persist the
`RiskRun`/valuation evidence in one short transaction. The existing queued worker
and Limits refresh path call the same phases. The persist phase commits the source
artifact before limit evaluation begins; it must not create a child `TaskRun`.

Do not use the older `risk_engine.run_portfolio_risk` path as-is: it currently
omits engine-config parity and does not stamp the same valuation evidence.

**Step 4: Run batch-pricing and risk regressions.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_risk_source_refresh.py tests/test_batch_pricing.py \
  tests/test_risk_engine.py tests/test_position_currency_drives_risk.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/batch_pricing.py \
  backend/app/services/risk_engine.py \
  tests/test_limit_risk_source_refresh.py tests/test_batch_pricing.py
git commit -m "refactor(risk): expose inline persisted source run"
```

### Task 5: Extract inline scenario-test and backtest source runners

**Files:**

- Modify: `backend/app/services/scenario_test_runner.py`
- Modify: `backend/app/services/backtest_runner.py`
- Modify: `backend/app/services/task_runner.py`
- Create: `tests/test_limit_scenario_source_refresh.py`
- Create: `tests/test_limit_backtest_source_refresh.py`
- Modify: `tests/test_scenario_test_runner.py`
- Modify: `tests/test_backtest_pipeline.py`

**Step 1: Write failing parity and one-worker tests.**

For each source:

- create and execute a domain run synchronously with no child `TaskRun`;
- preserve the existing queued endpoint behavior;
- persist exact scenario/spec/config/profile/engine/position scope,
  market-snapshot/effective market-evidence identity, and valuation timestamps;
- return `completed` or `empty` without hiding exclusions/warnings;
- allow artifact writing to be disabled for monitoring refresh;
- demonstrate completion with a one-worker executor.

Add the missing `ScenarioTestRun` branch to
`task_runner._sync_linked_status` as a scoped consistency fix and test stale
recovery.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_scenario_source_refresh.py \
  tests/test_limit_backtest_source_refresh.py \
  tests/test_scenario_test_runner.py tests/test_backtest_pipeline.py -q
```

**Step 3: Extract synchronous cores.**

Keep the domain `run_pipeline` functions authoritative. Refactor runner setup,
position resolution, valuation-date selection, calculation, result persistence,
and optional artifact generation into resolve/compute/persist phases. Resolve in
a read-only session, compute without an open write transaction, and commit each
completed scenario/backtest artifact in a short persist transaction. Existing
task workers wrap the same phases and no phase submits or waits on child tasks.

**Step 4: Run focused regressions.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_scenario_source_refresh.py \
  tests/test_limit_backtest_source_refresh.py \
  tests/test_scenario_test_runner.py tests/test_scenario_test_api.py \
  tests/test_backtest_pipeline.py tests/test_backtest_api.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/scenario_test_runner.py \
  backend/app/services/backtest_runner.py backend/app/services/task_runner.py \
  tests/test_limit_scenario_source_refresh.py \
  tests/test_limit_backtest_source_refresh.py \
  tests/test_scenario_test_runner.py tests/test_backtest_pipeline.py
git commit -m "refactor(risk): expose inline scenario sources"
```

### Task 6: Implement source planning, reuse, normalization, and FX evidence

**Files:**

- Create: `backend/app/services/limits/sources.py`
- Create: `backend/app/services/limits/source_planner.py`
- Modify: `backend/app/services/fx.py`
- Create: `tests/test_limit_sources.py`
- Create: `tests/test_limit_source_planner.py`
- Create: `tests/test_limit_fx_evidence.py`

**Step 1: Write failing adapter tests.**

Test:

- exact reuse versus forced refresh for all three source kinds;
- a newer queued run not masking an older eligible terminal run;
- profile, engine, market snapshot/effective market-evidence identity,
  position-scope, methodology, config, and freshness mismatch;
- risk shared totals versus monetary by-currency/per-position metrics;
- scope-aware partial failures;
- `rho_q` extraction;
- scenario `var_cvar` only at the locked confidence/horizon/method;
- backtest `var_95`/`cvar_95` only at the locked confidence/horizon/method;
- exact named stress and explicit worst-of-set selection;
- missing/renamed stress becoming `unknown`;
- mixed currency with pinned FX ids/rates/as-of, plus missing FX becoming
  `unknown`;
- persisted `LimitSourceReference` diagnostics.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_sources.py tests/test_limit_source_planner.py \
  tests/test_limit_fx_evidence.py -q
```

**Step 3: Implement minimal grouped planning and adapters.**

Group compatible versions by a canonical `SourcePlanKey` containing portfolio,
resolved position scope, pricing profile, engine config, `market_snapshot_id` or
canonical effective market-evidence id, methodology/config, valuation policy,
and freshness policy so one source run can serve several limits. Support
`reuse_only`, `refresh_if_stale`, and `force_refresh`. Extend FX resolution with
an evidence-returning helper containing the exact `FxRate` row id, direct/inverse
flag, rate, and as-of timestamp; keep the existing `fx_rate_as_of` API stable.

Every adapter returns normalized observations plus stable diagnostic codes such
as `missing_source`, `stale_source`, `source_failed`, `incomplete_scope`,
`methodology_mismatch`, `missing_scenario`, `missing_fx`, and
`bump_convention_mismatch`.

**Step 4: Run source and upstream producer tests.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_sources.py tests/test_limit_source_planner.py \
  tests/test_limit_fx_evidence.py tests/test_fx_rates_api.py \
  tests/test_risk_engine.py tests/test_scenario_test_engine.py \
  tests/test_backtest_pipeline.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/sources.py \
  backend/app/services/limits/source_planner.py backend/app/services/fx.py \
  tests/test_limit_sources.py tests/test_limit_source_planner.py \
  tests/test_limit_fx_evidence.py
git commit -m "feat(limits): add evidence source adapters"
```

### Task 7: Build the composite monitoring task

**Files:**

- Create: `backend/app/services/limits/monitoring.py`
- Create: `tests/test_limit_monitoring.py`
- Create: `tests/test_limit_monitoring_tasks.py`
- Modify: `backend/app/services/task_runner.py`
- Modify: `backend/app/schemas.py`

**Step 1: Write failing orchestration tests.**

Exercise:

- manual, agent, and scheduled trigger metadata;
- explicit portfolio, pricing profile, engine config, market snapshot/effective
  market-evidence, valuation-as-of, source-policy, and max-age inputs;
- active-version snapshotting before collection;
- one grouped source refresh serving several definitions;
- frozen snapshot/hash and run-version associations;
- source references and one evaluation per version/scope;
- identical evaluations from identical manual/scheduled evidence;
- successful task plus domain `completed_with_unknowns`;
- breach/warning not failing a task;
- infrastructure exception failing both task and domain run without rolling back
  already committed source artifacts or their `LimitSourceReference` rows;
- restart recovery marking an interrupted monitoring run failed;
- no call to `submit_async_task` from inside the composite worker.

Assert the status split:

```python
task = session.get(TaskRun, task_id)
run = session.get(LimitMonitoringRun, run_id)
assert task.status == "completed"
assert run.status == "completed_with_unknowns"
assert run.summary["unknown"] == 1
```

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_monitoring.py tests/test_limit_monitoring_tasks.py -q
```

**Step 3: Implement queue, dispatch, and worker seams.**

Use these phases:

- `queue_limit_monitoring(...)` to validate, create the domain run and linked
  `TaskRun`, and return ids without dispatching;
- caller commit of the queued/running state;
- `dispatch_limit_monitoring(...)` to submit the worker;
- `execute_limit_monitoring_task(...)` to open its own session;
- resolve immutable inputs in a short read phase and close the session;
- compute and persist each required source artifact through the Task 4/5 phased
  helpers, committing each new/reused source reference and its completeness,
  freshness, and reuse diagnostics before evaluation; and
- atomically persist evaluations, incident transitions, summary, and terminal
  monitoring status in the final short transaction. Task 17 extends this same
  finalization boundary to notification rows after the outbox schema/service
  exists.

Extend `_sync_linked_status` for the new relationship without copying
`completed_with_unknowns` into `TaskRun`. Add
`scenario_test_run_id`, `backtest_run_id`, and `limit_monitoring_run_id` to
`TaskRunOut` so the API no longer omits existing links while adding the new one.

**Step 4: Run monitoring and task regressions.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_monitoring.py tests/test_limit_monitoring_tasks.py \
  tests/test_api.py tests/test_risk_engine.py tests/test_batch_pricing.py \
  tests/test_scenario_test_runner.py tests/test_backtest_pipeline.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/monitoring.py \
  backend/app/services/task_runner.py backend/app/schemas.py \
  tests/test_limit_monitoring.py tests/test_limit_monitoring_tasks.py
git commit -m "feat(limits): add composite monitoring tasks"
```

### Task 8: Implement persistent incident lifecycle

**Files:**

- Create: `backend/app/services/limits/incidents.py`
- Create: `tests/test_limit_incidents.py`
- Modify: `backend/app/services/risk_limits.py`
- Modify: `tests/test_fanout_reconcile.py`

**Step 1: Write failing lifecycle tests.**

Cover open, repeated, escalation, acknowledgement, assignment, comment, waiver,
waiver expiry, recovery, manual resolution, explicit reopen, and a later breach.
Assert immutable events, actor/persona/mode/thread/audit attribution, optimistic
concurrency, and one active episode per limit/scope.

`unknown` contributes data-quality state and later warnings, but does not create
a risk-breach incident. Waiver/resolution never alters the underlying evaluation.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_incidents.py tests/test_fanout_reconcile.py -q
```

**Step 3: Implement reconciliation and actions.**

Call incident reconciliation from the final monitoring transaction after
evaluations are flushed. Change `services/risk_limits.py` into a compatibility reader over
authoritative limit evaluations/incidents. Keep its public function temporarily
so the existing dynamic-fanout tool does not break before Task 23.

**Step 4: Run focused tests.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_incidents.py tests/test_limit_monitoring.py \
  tests/test_fanout_reconcile.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/incidents.py \
  backend/app/services/risk_limits.py \
  tests/test_limit_incidents.py tests/test_fanout_reconcile.py
git commit -m "feat(limits): add incident lifecycle"
```

### Task 9: Expose definitions, monitoring, incidents, and dashboard APIs

**Files:**

- Create: `backend/app/routers/limits.py`
- Create: `tests/test_limits_api.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `tests/test_api.py`

**Step 1: Write failing API tests.**

Cover the approved core endpoints:

```text
GET/POST /api/limits
GET/PATCH /api/limits/{id}
POST /api/limits/{id}/versions
GET /api/limits/{id}/versions
POST /api/limits/{id}/versions/{version_id}/activate
POST /api/limits/{id}/deactivate
POST /api/limits/{id}/retire
GET /api/limit-monitoring/dashboard
POST/GET /api/limit-monitoring/runs
GET /api/limit-monitoring/runs/{id}
GET /api/limit-monitoring/runs/{id}/evaluations
GET /api/limit-incidents
GET /api/limit-incidents/{id}
POST /api/limit-incidents/{id}/{acknowledge|assign|comments|waive|resolve|reopen}
GET /api/market-data/snapshots
```

Assert typed validation, filters/pagination, 404 for absent/foreign resources,
409 for stale `row_version`, `task_id` on queued runs, explicit market
snapshot/effective market-evidence and source-policy inputs,
source/freshness/coverage links, no hard-delete route, and no cross-portfolio
leakage.

The snapshot list endpoint is a bounded newest-first read over existing
`MarketSnapshotOut` rows with optional source/as-of filters. It is required
before the Monitor/Schedules selectors; do not synthesize snapshot choices in
the frontend.

**Step 2: Run and verify 404/import failures.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limits_api.py -q
```

**Step 3: Implement a router builder.**

Follow the repository router pattern and register `build_limits_router(...)` in
`create_app()`. Keep HTTP translation in the router; services raise typed domain
errors. Construct trusted human `LimitActionContext` server-side. Do not accept
actor/persona/audit attribution from request JSON.

The dashboard returns summary cards, grouped current evaluations, latest run,
active incidents, and bounded trends. Add a compact
`GET /api/limit-monitoring/summary` response with counts and a monotonic
`latest_incident_event_id`. Task 17 adds a separate
`latest_notification_id`; never repurpose one opaque cursor as the other.

**Step 4: Run core backend slice.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limits_api.py tests/test_limit_definitions.py \
  tests/test_limit_monitoring.py tests/test_limit_incidents.py \
  tests/test_api.py tests/test_risk_engine.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/routers/limits.py backend/app/schemas.py \
  backend/app/main.py tests/test_limits_api.py tests/test_api.py
git commit -m "feat(limits): expose core API"
```

### Task 10: Freeze frontend contracts and add the `/limits` shell

**Files:**

- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/api/client.limits.test.ts`
- Modify: `frontend/src/lib/routing.ts`
- Modify: `frontend/src/lib/routing.test.ts`
- Modify: `frontend/src/hooks/useRoute.ts`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/routes/Limits.tsx`
- Create: `frontend/src/routes/Limits.live.tsx`
- Create: `frontend/src/routes/Limits.css`
- Create: `frontend/src/routes/Limits.test.tsx`

**Step 1: Write failing contract/routing/shell tests.**

Add:

- `limits` to `Route`;
- all definition/version/run/evaluation/incident/dashboard DTOs;
- the existing market-snapshot output DTO and a typed list client for
  `GET /api/market-data/snapshots`;
- `TaskRun.limit_monitoring_run_id`;
- `ReportJob.limit_monitoring_run_id`;
- typed core client functions and expected concurrency-token bodies;
- route count `25`, `/limits` round-trip, portfolio support, and allowlisted
  deep-link params `tab`, `run`, `incident`, `limit`, and `schedule`;
- five tab labels and page-context skeleton.

Do **not** add `completed_with_unknowns` to `TaskRun`; that is a
`LimitMonitoringRun` status.

**Step 2: Run and verify failure.**

```bash
npm --prefix frontend test -- \
  src/api/client.limits.test.ts src/lib/routing.test.ts \
  src/routes/Limits.test.tsx
```

**Step 3: Implement the typed shell.**

Add Limits beside Risk in the sidebar and command palette. Include Limits in the
shared portfolio query contract. Preserve only the allowlisted Limits query
parameters when `main.tsx` canonicalizes the URL, so a deep link is not stripped
after mount. `LimitsLive` listens to `popstate` because moving between deep-linked
tabs may not change the top-level route value. Pass the existing assistant
control seam from `main.tsx` as
`onAskLimitManager={() => setAgentOpen(true)}`; Tasks 11 and 22 control when the
button is enabled, while the shell remains the owner of assistant state.

Render exactly one root `PageScaffold` plus the existing Tabs primitive with
Monitor, Definitions, Breaches, Schedules, and Reports. Tab bodies must use
lower-level primitives rather than nesting full-page templates that render their
own `PageScaffold`. Read and obey `frontend/UI_STYLE_GUIDE.md`: token-only styles,
BEM names, themed controls, visible focus, light/dark and compact density.

**Step 4: Run focused tests and typecheck.**

```bash
npm --prefix frontend test -- \
  src/api/client.limits.test.ts src/lib/routing.test.ts \
  src/routes/Limits.test.tsx
npm --prefix frontend run build
git diff --check
```

**Step 5: Commit.**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts \
  frontend/src/api/client.limits.test.ts frontend/src/lib/routing.ts \
  frontend/src/lib/routing.test.ts frontend/src/hooks/useRoute.ts \
  frontend/src/main.tsx frontend/src/routes/Limits.tsx \
  frontend/src/routes/Limits.live.tsx frontend/src/routes/Limits.css \
  frontend/src/routes/Limits.test.tsx
git commit -m "feat(limits-ui): add route and typed shell"
```

### Task 11: Implement the Monitor tab and task polling

**Files:**

- Modify: `frontend/src/routes/Limits.tsx`
- Modify: `frontend/src/routes/Limits.live.tsx`
- Modify: `frontend/src/routes/Limits.css`
- Create: `frontend/src/routes/Limits.live.test.tsx`

**Step 1: Write failing UI tests.**

Cover initial/loading/empty/error states, summary cards, grouped utilization
rows, separate warning/breach/unknown visuals, freshness/coverage/reason display,
evidence drawer, URL-selected run, and bounded page context. Load the existing
portfolio, pricing-profile, engine-config, and market-snapshot choices; use the
Risk-style valid preferred-portfolio/fallback behavior; and prove an explicit
portfolio change flows through the shell's shared state callback.

Test Run now:

1. POST one monitoring run.
2. Poll `/api/tasks/{task_id}` until an existing terminal status.
3. Load the linked `LimitMonitoringRun`.
4. Treat domain `completed_with_unknowns` as success with a visible warning.
5. Refresh dashboard, evaluations, and incidents.

Assert the request keeps `valuation_as_of` distinct from the shell accounting
date and sends explicit pricing profile, engine config, market
snapshot/effective market-evidence selection, source policy (`reuse_only`,
`refresh_if_stale`, or `force_refresh`), and max age. If any value is
server-defaulted, display the resolved default before submission and in the run
evidence afterward.

**Step 2: Run and verify failure.**

```bash
npm --prefix frontend test -- src/routes/Limits.live.test.tsx
```

**Step 3: Implement Monitor.**

Keep the one root scaffold from Task 10. Compose Monitor from lower-level
`MetricRows`, `PanelGrid`, `Table`, `Badge`, `Modal`/drawer patterns, and numeric
formatting primitives; do not nest `AnalyticsDashboard`. `LimitsLive` receives
`portfolioId` and `onPortfolioIdChange` from the shell. Keep
`valuation_as_of` as a dedicated labelled control; never substitute the shell
accounting date. Preserve prior data during refresh. Do not infer numeric values
client-side.

Add Run now plus visibly disabled Generate analysis and Ask Limit Manager
actions. Task 20 enables report generation after its API exists; Task 22 enables
Ask Limit Manager only after the persona and routing contracts exist.

**Step 4: Run focused tests and build.**

```bash
npm --prefix frontend test -- \
  src/routes/Limits.test.tsx src/routes/Limits.live.test.tsx
npm --prefix frontend run build
git diff --check
```

**Step 5: Commit.**

```bash
git add frontend/src/routes/Limits.tsx frontend/src/routes/Limits.live.tsx \
  frontend/src/routes/Limits.css frontend/src/routes/Limits.live.test.tsx
git commit -m "feat(limits-ui): add monitoring dashboard"
```

### Task 12: Implement Definitions and Breaches tabs

**Files:**

- Modify: `frontend/src/routes/Limits.tsx`
- Modify: `frontend/src/routes/Limits.live.tsx`
- Modify: `frontend/src/routes/Limits.css`
- Modify: `frontend/src/routes/Limits.test.tsx`
- Modify: `frontend/src/routes/Limits.live.test.tsx`

**Step 1: Add failing interaction tests.**

Definitions:

- filters and master-detail selection;
- stable key/name/description/category/owner/tags fields;
- typed metric/source/scope/methodology/aggregation/transform/comparator form;
- conditionally validated warning/hard lower and upper thresholds;
- unit, reporting currency, Greek bump convention, effective-from/until, owner,
  rationale, and freshness policy;
- rho and RhoQ options;
- version timeline/diff;
- active-version fields rendered immutable;
- create-next-draft and separate activation;
- stale row-version feedback and server refresh.

Breaches:

- persistent ledger filters and incident timeline;
- acknowledge, assign, comment, waive, resolve, and reopen;
- no optimistic success before the server response;
- returned row version replacing the local one;
- evaluation and audit deep links.

Report links, notification delivery history, notification acknowledgement, and
the combined evaluation/notification/report/audit timeline are deferred to Task
20 after those APIs exist.

**Step 2: Run and verify failure.**

```bash
npm --prefix frontend test -- \
  src/routes/Limits.test.tsx src/routes/Limits.live.test.tsx
```

**Step 3: Implement using lower-level primitives.**

Keep the single Task 10 scaffold. Compose Definitions with `SplitLayout`,
`RailList`, and `RailItem`, and Breaches with `TableToolbar`, `Table`, `Modal`,
`Badge`, and shared field primitives. Do not nest `MasterDetailPage` or
`DataTablePage`, because both own a full page scaffold. Load heavy data only for
the active tab. Keep empty/error/mutation feedback separate.

**Step 4: Run focused tests and build.**

```bash
npm --prefix frontend test -- \
  src/routes/Limits.test.tsx src/routes/Limits.live.test.tsx
npm --prefix frontend run build
git diff --check
```

**Step 5: Commit.**

```bash
git add frontend/src/routes/Limits.tsx frontend/src/routes/Limits.live.tsx \
  frontend/src/routes/Limits.css frontend/src/routes/Limits.test.tsx \
  frontend/src/routes/Limits.live.test.tsx
git commit -m "feat(limits-ui): add definitions and breaches"
```

### Task 13: Add schedule, occurrence, outbox, and report persistence

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `backend/alembic/versions/0047_limit_scheduler_outbox_reports.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Create: `tests/test_migration_0047.py`
- Create: `tests/test_limit_schedule_models.py`

**Step 1: Add `croniter` through the package manager.**

```bash
uv add --no-sync croniter
VIRTUAL_ENV=/Users/fuxinyao/open-otc-trading/.venv uv sync --active --locked
```

Do not hand-edit `uv.lock`. The second command deliberately syncs the exact
interpreter used by every test command in this plan; do not create an unused
worktree-local environment and then test with the unsynchronized shared one.

**Step 2: Write failing migration/model tests.**

Add:

- `LimitMonitoringSchedule`;
- `LimitSchedulePortfolio`;
- `LimitScheduleOccurrence`;
- `LimitScheduleOccurrenceRun`;
- `LimitNotification`;
- notification read/ack actor/timestamp fields;
- `ReportJob.limit_monitoring_run_id`;
- `ReportJob.evidence_snapshot`;
- `ReportJob.evidence_hash`.

Test unique schedule/time occurrence, unique target/attempt linkage, unique
notification dedup key, row versions, lease fields, indexes, migration
round-trip, and incremental repair of nullable columns on an old `report_jobs`
table. Also start Alembic at `0046_risk_limits_core`, run current
`database.init_db()`, then upgrade head, proving guarded table/column/index
creation works when current ORM metadata pre-created 0047 objects.

**Step 3: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_migration_0047.py tests/test_limit_schedule_models.py -q
```

**Step 4: Implement the migration/models/boot repair.**

Store schedule inputs and notification destinations as validated JSON, but use
association rows for target portfolios and attempt history. Keep occurrence
timestamps UTC plus the original local wall-time/timezone fields required for
DST audit. Use revision id `0047_limit_schedule_outbox` (under 32 characters)
with `down_revision = "0046_risk_limits_core"`; the descriptive filename may
remain longer. Apply idempotent object-existence guards for the ORM-first
bootstrap path.

**Step 5: Run and commit.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_migration_0047.py tests/test_limit_schedule_models.py \
  tests/test_reports_list.py -q
git diff --check
git add pyproject.toml uv.lock \
  backend/alembic/versions/0047_limit_scheduler_outbox_reports.py \
  backend/app/models.py backend/app/database.py \
  tests/test_migration_0047.py tests/test_limit_schedule_models.py
git commit -m "feat(limits): add schedule and outbox persistence"
```

### Task 14: Implement cron semantics, schedule governance, and REST API

**Files:**

- Create: `backend/app/services/limits/schedules.py`
- Create: `tests/test_limit_schedules.py`
- Modify: `backend/app/routers/limits.py`
- Modify: `backend/app/schemas.py`
- Modify: `tests/test_limits_api.py`

**Step 1: Write failing cron and governance tests.**

Test:

- five-field cron validation and IANA timezone validation;
- name, description, owner, complete typed monitoring inputs, and notification
  destinations round-tripping without model-authored defaults;
- next five human-readable occurrences;
- New York spring-forward `02:30` recorded `dst_nonexistent`;
- New York fall-back `01:30` executed once at fold zero;
- unique occurrence creation under concurrent callers;
- misfire inside grace runs once; outside grace is persisted missed;
- overlap coalescing;
- bounded infrastructure retry;
- multi-portfolio target expansion;
- disabled schedule edits versus enabled schedule material changes;
- enablement as the one governance authorization for later deterministic due
  occurrences, with stored mode affecting only optional agent follow-up;
- optimistic row-version conflicts;
- create/edit/enable/disable/run-now/occurrence APIs.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_schedules.py tests/test_limits_api.py -q
```

**Step 3: Implement schedule services and endpoints.**

Add the approved routes under `/api/limit-schedules`. Creating a schedule always
creates it disabled. Updating a disabled schedule and materially updating an
enabled schedule are separate service methods, so the future name-based HITL
classification cannot be bypassed with an argument change.

`run-now` uses the same occurrence/target machinery and returns occurrence plus
linked monitoring task ids.

**Step 4: Run focused tests.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_schedules.py tests/test_limits_api.py \
  tests/test_limit_monitoring.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/schedules.py \
  backend/app/routers/limits.py backend/app/schemas.py \
  tests/test_limit_schedules.py tests/test_limits_api.py
git commit -m "feat(limits): add durable schedule service"
```

### Task 15: Add the restart-safe Limits runtime

**Files:**

- Create: `backend/app/services/limits/runtime.py`
- Create: `tests/test_limit_scheduler_runtime.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services/limits/monitoring.py`

**Step 1: Write failing runtime tests with a fake clock/sleep.**

Cover:

- an inert scheduler loop when scheduling is disabled or no schedules are due;
- two runtimes racing while only one claims an occurrence;
- target tasks submitted once;
- an enabled Interactive/Auto/YOLO schedule always dispatching the same
  deterministic configured monitoring scope without a second cron-time HITL
  interruption;
- lease renewal/reclaim;
- restart after a stale `TaskRun`;
- a new attempt linked without deleting the failed attempt;
- infrastructure-only retry;
- breach/unknown not retried;
- clean shutdown cancelling loops;
- no deadlock at `async_task_workers=1`.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_scheduler_runtime.py -q
```

**Step 3: Implement lifecycle and settings.**

Add settings for scheduler enablement, polling, lease, claim batch size, misfire
defaults, and retry backoff. Start/stop `LimitRuntime` in `create_app()` beside
the gateway runtime. The runtime performs short database claims and dispatches
the existing monitoring worker; it never performs quant work on the event loop.

Default scheduler enablement to false. The flag gates only cron occurrence
materialization; it must not gate the independently added notification consumer
in Task 17. Scheduler health should be inspectable through a small
`/api/limit-schedules/runtime` read endpoint.

**Step 4: Run runtime plus startup/shutdown tests.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_scheduler_runtime.py \
  tests/gateway/test_http_lifecycle.py tests/test_api.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/runtime.py \
  backend/app/services/limits/monitoring.py backend/app/config.py \
  backend/app/main.py tests/test_limit_scheduler_runtime.py
git commit -m "feat(limits): add restart-safe scheduler runtime"
```

### Task 16: Implement the Schedules tab

**Files:**

- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.limits.test.ts`
- Modify: `frontend/src/routes/Limits.tsx`
- Modify: `frontend/src/routes/Limits.live.tsx`
- Modify: `frontend/src/routes/Limits.css`
- Modify: `frontend/src/routes/Limits.test.tsx`
- Modify: `frontend/src/routes/Limits.live.test.tsx`

**Step 1: Write failing tests.**

Cover schedule table; name/description/owner; expression/timezone/mode; explicit
multi-portfolio and multi-limit selection; pricing profile, engine config, market
snapshot/effective market evidence, scenario/valuation inputs; reuse/refresh
policy and max age; misfire and overlap policies; retry/backoff; notification
destinations; next occurrences; enabled/disabled state; runtime-disabled notice;
edit, enable, disable, run-now, occurrence/target attempt history, concurrency
conflicts, and deep-linked schedule selection.

**Step 2: Run and verify failure.**

```bash
npm --prefix frontend test -- \
  src/api/client.limits.test.ts src/routes/Limits.test.tsx \
  src/routes/Limits.live.test.tsx
```

**Step 3: Implement the tab.**

Keep the single Limits scaffold and compose the tab from `TableToolbar`, `Table`,
shared inputs/selects, `Modal`, and `Badge`; do not nest `DataTablePage`. Never
claim an enabled schedule changed until the server returns the new row version.
Display timezone and local/UTC firing times explicitly.

**Step 4: Run tests and build.**

```bash
npm --prefix frontend test -- \
  src/api/client.limits.test.ts src/routes/Limits.test.tsx \
  src/routes/Limits.live.test.tsx
npm --prefix frontend run build
git diff --check
```

**Step 5: Commit.**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts \
  frontend/src/api/client.limits.test.ts frontend/src/routes/Limits.tsx \
  frontend/src/routes/Limits.live.tsx frontend/src/routes/Limits.css \
  frontend/src/routes/Limits.test.tsx frontend/src/routes/Limits.live.test.tsx
git commit -m "feat(limits-ui): add schedule management"
```

### Task 17: Create transactional warnings and web/Agent Desk delivery

**Files:**

- Create: `backend/app/services/limits/notifications.py`
- Create: `backend/app/services/limits/notification_runtime.py`
- Create: `tests/test_limit_notifications.py`
- Create: `tests/test_limit_notification_runtime.py`
- Create: `tests/test_limit_agent_desk_warning.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/limits/incidents.py`
- Modify: `backend/app/services/limits/monitoring.py`
- Modify: `backend/app/services/limits/runtime.py`
- Modify: `backend/app/routers/limits.py`

**Step 1: Write failing outbox tests.**

Test warning creation for enter-warning, enter-breach, escalation, reopen,
configured unknown, and overdue reminder. Test dedup/cooldown, immutable payload,
same-finalization-transaction rollback, atomic batch claims, backoff, retry, dead
letter, and delivery without rerunning monitoring. With
`OPEN_OTC_LIMIT_SCHEDULER=off` and `OPEN_OTC_LIMIT_NOTIFICATIONS=on`, prove the
Agent Desk consumer still drains rows.

Define and test exact web-ledger APIs:

```text
GET  /api/limit-notifications
POST /api/limit-notifications/{notification_id}/read
POST /api/limit-notifications/{notification_id}/acknowledge
```

The list accepts `incident_id`, `monitoring_run_id`, `portfolio_id`, channel,
delivery status, `after_notification_id`, and bounded limit filters. Historical
timeline reads use stable `(created_at, id)` ascending order; cursor polling is
strictly `id > after_notification_id`. Read/ack mutations use
`expected_row_version`, record the trusted human actor, and cannot cross
portfolio scope.

For Agent Desk, require one idempotent `AgentMessage` with
`character="limit_manager"`, incident/run deep links, and notification id in
metadata. Validate configured thread ids server-side.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_notifications.py \
  tests/test_limit_notification_runtime.py \
  tests/test_limit_agent_desk_warning.py -q
```

**Step 3: Implement outbox production and delivery.**

Incident reconciliation creates one outbox row per channel/destination in the
same finalization transaction as evaluations. Web delivery is the persisted
notification stream itself. Add read/ack APIs and `after_notification_id` cursor
support, and extend summary with a separate monotonic
`latest_notification_id` alongside `latest_incident_event_id`.

Create an independently started `LimitNotificationRuntime` that drains only
Agent Desk rows in bounded batches; a delivery failure changes only the outbox
row. Add `OPEN_OTC_LIMIT_NOTIFICATIONS`, defaulting on and independently
disableable from the cron scheduler. `LimitRuntime` remains scheduler-only and
`OPEN_OTC_LIMIT_SCHEDULER` gates only occurrence materialization.

**Step 4: Run monitoring/incident/notification slice.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_notifications.py \
  tests/test_limit_notification_runtime.py \
  tests/test_limit_agent_desk_warning.py tests/test_limit_incidents.py \
  tests/test_limit_monitoring.py tests/test_limits_api.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/notifications.py \
  backend/app/services/limits/notification_runtime.py \
  backend/app/services/limits/incidents.py \
  backend/app/services/limits/monitoring.py \
  backend/app/services/limits/runtime.py backend/app/routers/limits.py \
  backend/app/main.py backend/app/config.py backend/app/schemas.py \
  tests/test_limit_notifications.py tests/test_limit_notification_runtime.py \
  tests/test_limit_agent_desk_warning.py
git commit -m "feat(limits): add warning outbox"
```

### Task 18: Deliver warnings through the existing IM gateway

**Files:**

- Create: `backend/app/services/gateway/outbound.py`
- Create: `tests/gateway/test_limit_warning_delivery.py`
- Modify: `backend/app/services/gateway/runtime.py`
- Modify: `backend/app/services/limits/notifications.py`
- Modify: `tests/gateway/test_runtime.py`

**Step 1: Write failing FakeConnector tests.**

Cover:

- explicit configured binding/chat destinations only;
- active binding and `GatewayThreadMap` validation;
- correct `ChatRef` and deep-link text;
- outbox dedup key passed as connector idempotency key;
- gateway standby/no-owner never claiming the row;
- owner takeover reclaiming an expired claim and sending it once;
- revoked/missing binding becoming a permanent destination failure;
- connector exception backoff;
- exactly one send after retry/restart.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/gateway/test_limit_warning_delivery.py \
  tests/gateway/test_runtime.py -q
```

**Step 3: Make the gateway owner the IM outbox consumer.**

Do not reach into `GatewayRuntime._connectors` from Limits code, and do not let
the Limits notification runtime claim IM rows. Add a small outbound adapter used
inside the gateway-owning runtime. Only the process holding `GatewayWorkerLock`
claims bounded IM batches, resolves validated destinations against its owned
connectors, sends with the notification dedup key, and marks delivery. Standby
processes do not claim; expired claims become eligible after owner takeover.
Keep the existing inbound ownership/lease behavior intact. The owner claims no
IM rows when `OPEN_OTC_LIMIT_NOTIFICATIONS=off`; web ledger rows remain readable.

**Step 4: Run the gateway suite.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/gateway tests/test_limit_notifications.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/gateway/outbound.py \
  backend/app/services/gateway/runtime.py \
  backend/app/services/limits/notifications.py \
  tests/gateway/test_limit_warning_delivery.py tests/gateway/test_runtime.py
git commit -m "feat(limits): deliver warnings to IM"
```

### Task 19: Add evidence-frozen limit-analysis reports

**Files:**

- Create: `backend/app/services/limits/reports.py`
- Create: `tests/test_limit_reports.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services/reports.py`
- Modify: `backend/app/services/domains/reporting.py`
- Modify: `backend/app/tools/reporting.py`
- Modify: `backend/app/routers/limits.py`
- Modify: `backend/app/main.py`
- Modify: `tests/test_services_domains_reporting.py`
- Modify: `tests/test_tools_reporting.py`

**Step 1: Write failing report tests.**

Require:

- canonical JSON snapshot with every evaluation, unknown, incident transition,
  source/freshness issue, acknowledgement, and waiver;
- SHA-256 over sorted compact JSON;
- `report_type="limit_analysis"` and `limit_monitoring_run_id`;
- mandatory HTML/Excel numeric tables rendered from the snapshot;
- optional typed commentary keyed to known evaluation/incident ids;
- server rejection of a mismatched executive verdict, unknown ids, or a draft
  that omits required breach/unknown coverage;
- generic `POST /api/reports/jobs` and
  `services/domains/reporting.create_report` rejecting
  `report_type="limit_analysis"` so callers cannot bypass the run-scoped frozen
  snapshot builder;
- report failure not rolling back monitoring/incidents;
- regeneration against the same run preserving evidence hash while creating a
  new immutable job/artifact;
- targeting a newer run producing a new snapshot/hash.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_reports.py \
  tests/test_services_domains_reporting.py tests/test_tools_reporting.py -q
```

**Step 3: Implement server-owned evidence and rendering.**

Extend `ReportJobCreate` without breaking portfolio/risk/rfq jobs. The Limit
Manager supplies qualitative typed commentary; the renderer injects all numeric
values from the frozen evidence. Store evidence and hash on `ReportJob`, and
also include the hash in every artifact.

Add `POST /api/limit-monitoring/runs/{run_id}/reports`.
Update `main.py::_report_job_out` so the existing global report endpoints expose
the monitoring-run link and evidence hash. Keep `limit_analysis` creation
exclusive to the run-scoped endpoint/service. Generic report ingress returns a
typed validation error and must never fall through `_build_report_payload` to
ordinary portfolio-risk rendering.

**Step 4: Run report and API regressions.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_reports.py tests/test_reports_list.py \
  tests/test_services_domains_reporting.py tests/test_tools_reporting.py \
  tests/test_limits_api.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/limits/reports.py backend/app/schemas.py \
  backend/app/services/reports.py backend/app/services/domains/reporting.py \
  backend/app/tools/reporting.py backend/app/routers/limits.py backend/app/main.py \
  tests/test_limit_reports.py tests/test_services_domains_reporting.py \
  tests/test_tools_reporting.py
git commit -m "feat(limits): add evidence-frozen reports"
```

### Task 20: Integrate Reports, Tasks, sidebar badge, and warning toast

**Files:**

- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/routes/Limits.tsx`
- Modify: `frontend/src/routes/Limits.live.tsx`
- Modify: `frontend/src/routes/Reports.tsx`
- Modify: `frontend/src/routes/Reports.live.tsx`
- Modify: `frontend/src/components/ReportCard.tsx`
- Modify: `frontend/src/components/ReportReader.tsx`
- Modify: `frontend/src/routes/Tasks.tsx`
- Modify: `frontend/src/routes/Tasks.live.tsx`
- Modify: `frontend/src/routes/Tasks.css`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/Sidebar.css`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/components/LimitWarningToast.tsx`
- Create: `frontend/src/components/LimitWarningToast.css`
- Create: `frontend/src/components/LimitWarningToast.test.tsx`
- Create: `frontend/src/routes/Reports.test.tsx`
- Create: `frontend/src/routes/Reports.live.test.tsx`
- Modify: `frontend/src/routes/Limits.live.test.tsx`
- Modify: `frontend/src/routes/Tasks.test.tsx`
- Modify: `frontend/src/components/ReportCard.test.tsx`
- Modify: `frontend/src/components/ReportReader.test.tsx`
- Modify: `frontend/src/components/Sidebar.test.tsx`

**Step 1: Write failing integration tests.**

Cover:

- Limits Reports tab filtered to linked `limit_analysis` jobs;
- `ReportTimeline`/`ReportReader` and back-link to frozen monitoring run;
- global Reports still showing every report type;
- Generate analysis POSTing
  `/api/limit-monitoring/runs/{run_id}/reports`, polling the returned task,
  rendering success/failure feedback, and refreshing linked report jobs;
- Breaches showing one ordered evaluation/notification/report/audit timeline,
  delivery status, report links, and server-confirmed notification read/ack
  state;
- Tasks label `limit_monitoring`, `Limit run #N`, and durable anchor to
  `/limits?tab=monitor&run=N&portfolio=P`;
- Tasks page context including `limit_monitoring_run_id`;
- sidebar active-breach badge;
- initial summary poll not toasting historical warnings;
- a strictly newer notification cursor producing one toast;
- refresh/repeated polls not duplicating the toast;
- accessible toast dismissal and focus behavior;
- separate incident-event and notification cursors never being interchanged.

**Step 2: Run and verify failure.**

```bash
npm --prefix frontend test -- \
  src/routes/Limits.live.test.tsx src/routes/Reports.test.tsx \
  src/routes/Reports.live.test.tsx src/routes/Tasks.test.tsx \
  src/components/LimitWarningToast.test.tsx \
  src/components/ReportCard.test.tsx src/components/ReportReader.test.tsx \
  src/components/Sidebar.test.tsx
```

**Step 3: Implement integration.**

Use the backend monotonic cursor; never infer a new event from a count change.
The ledger remains authoritative. Style `limit_analysis` with existing semantic
tokens and keep unknown/warning distinct from failed infrastructure. Enable the
Generate analysis action that Task 11 left disabled. Complete the Breaches
delivery/acknowledgement and frozen report timeline now that both APIs exist.

**Step 4: Run frontend suite and build.**

```bash
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

**Step 5: Commit.**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts \
  frontend/src/routes/Limits.tsx frontend/src/routes/Limits.live.tsx \
  frontend/src/routes/Reports.tsx frontend/src/routes/Reports.live.tsx \
  frontend/src/components/ReportCard.tsx \
  frontend/src/components/ReportReader.tsx \
  frontend/src/routes/Tasks.tsx frontend/src/routes/Tasks.live.tsx \
  frontend/src/routes/Tasks.css frontend/src/components/Sidebar.tsx \
  frontend/src/components/Sidebar.css frontend/src/main.tsx \
  frontend/src/components/LimitWarningToast.tsx \
  frontend/src/components/LimitWarningToast.css \
  frontend/src/components/LimitWarningToast.test.tsx \
  frontend/src/routes/Limits.live.test.tsx \
  frontend/src/routes/Reports.test.tsx \
  frontend/src/routes/Reports.live.test.tsx \
  frontend/src/routes/Tasks.test.tsx \
  frontend/src/components/ReportCard.test.tsx \
  frontend/src/components/ReportReader.test.tsx \
  frontend/src/components/Sidebar.test.tsx
git commit -m "feat(limits-ui): integrate warnings tasks and reports"
```

### Task 21: Add narrowly scoped limits tools and HITL classifications

**Files:**

- Create: `backend/app/tools/limits.py`
- Create: `tests/test_limit_tools.py`
- Modify: `backend/app/tools/__init__.py`
- Modify: `backend/app/services/agents.py`
- Modify: `backend/app/services/deep_agent/task_registry.py`
- Modify: `backend/app/services/deep_agent/hitl.py`
- Modify: `backend/app/skills/meta/yolo-hitl-policy.md`
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `tests/test_hitl.py`
- Modify: `tests/test_personas.py`
- Modify: `tests/test_capability_assignments.py`
- Modify: `tests/test_audit_registration.py`

**Step 1: Write failing registration/authority tests.**

Read tools:

- list/get definitions and versions;
- dashboard/run/evaluation/incident/schedule/report reads.

Ordinary reversible write tools:

- create identity/draft and create next draft;
- update draft/metadata;
- run monitoring;
- acknowledge/assign/comment;
- create a disabled schedule;
- update a disabled schedule;
- run schedule now;
- generate a limit-analysis report.

Governance-critical tools:

- activate/deactivate/retire;
- update an enabled schedule;
- enable/disable a schedule;
- waive/resolve/reopen an incident.

Expose no hard-delete tool. Governed history remains immutable after a limit
identity is created.

Use distinct tool names for disabled versus enabled schedule updates. Test the
mode table:

| Mode | ordinary write | governance-critical write |
|---|---|---|
| Interactive | interrupt | interrupt |
| Auto (`yolo_mode=True` internally) | execute | interrupt |
| YOLO (`headless=True`) | execute | execute |

All writes remain `DOMAIN_WRITE` and therefore audited fail-closed.
Mirror the complete persisted Limits tool-name set in
`skills/meta/yolo-hitl-policy.md` and the orchestrator's batch-size-one list so
positional HITL can never receive two decisions in one turn.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_tools.py tests/test_hitl.py \
  tests/test_capability_assignments.py tests/test_audit_registration.py \
  tests/test_personas.py -q
```

**Step 3: Implement thin tool adapters and registry entries.**

Register every tool in both `QUANT_AGENT_TOOLS` and
`DEEP_AGENT_TOOL_NAMES`. Add typed task registrations with narrow scopes such
as `read_limit_state`, `propose_limit_change`, `run_limit_monitoring`,
`handle_limit_incident`, `manage_limit_schedule`, and
`write_limit_analysis`.

The tool layer reads the trusted runnable/audit context to build
`LimitActionContext`; actor/persona/mode/thread are never model arguments.

**Step 4: Run agent tool contracts.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_tools.py tests/test_hitl.py \
  tests/test_capability_assignments.py tests/test_audit_registration.py \
  tests/test_personas.py tests/test_audit_trail_middleware.py \
  tests/test_long_agent_scheduler.py \
  tests/test_long_agent_ledger.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/tools/limits.py backend/app/tools/__init__.py \
  backend/app/services/agents.py \
  backend/app/services/deep_agent/task_registry.py \
  backend/app/services/deep_agent/hitl.py \
  backend/app/skills/meta/yolo-hitl-policy.md \
  backend/app/services/deep_agent/prompts/orchestrator.md \
  tests/test_limit_tools.py tests/test_hitl.py \
  tests/test_capability_assignments.py tests/test_audit_registration.py \
  tests/test_personas.py
git commit -m "feat(limits-agent): add governed tools"
```

### Task 22: Register the dedicated `limit_manager` persona and workflows

**Files:**

- Create: `backend/app/services/deep_agent/prompts/limit_manager.md`
- Create:
  - `backend/app/skills/workflows/limits/manage-limit-definitions/SKILL.md`
  - `backend/app/skills/workflows/limits/monitor-limits/SKILL.md`
  - `backend/app/skills/workflows/limits/handle-limit-incident/SKILL.md`
  - `backend/app/skills/workflows/limits/manage-limit-schedule/SKILL.md`
  - `backend/app/skills/workflows/limits/generate-limit-analysis-report/SKILL.md`
- Modify: `backend/app/services/deep_agent/personas.py`
- Modify: `backend/app/services/deep_agent/persona_domains.py`
- Modify: `backend/app/services/deep_agent/orchestrator.py`
- Modify: `backend/app/services/deep_agent/hitl.py`
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `backend/app/services/deep_agent/prompts/risk_manager.md`
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Modify: `backend/app/skills/meta/clarification-policy.md`
- Modify: `backend/app/skills/meta/cost-preview-policy.md`
- Modify: `backend/app/skills/meta/delegated-scope-policy.md`
- Modify: `backend/app/skills/meta/headless-policy.md`
- Modify: `backend/app/skills/meta/python-analysis-policy.md`
- Modify: `backend/app/skills/meta/read-before-compute-policy.md`
- Modify: `backend/app/skills/meta/reply-options-policy.md`
- Modify: `backend/app/skills/meta/yolo-hitl-policy.md`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services/desk_workflows_script.py`
- Modify: `backend/app/services/desk_workflow_runner.py`
- Modify: `backend/app/routers/skills.py`
- Modify: `backend/app/services/arena/runner.py`
- Modify: `backend/app/golden_workflows/schema.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/routes/SkillsWorkflowForm.tsx`
- Modify: `frontend/src/routes/Limits.tsx`
- Modify: `frontend/src/routes/Limits.live.tsx`
- Modify: `frontend/src/routes/Limits.test.tsx`
- Modify: `frontend/src/routes/Limits.live.test.tsx`
- Modify: `tests/test_hitl.py`
- Modify: `tests/test_golden_workflow_schema.py`
- Modify: `tests/test_thread_source.py`
- Modify: `tests/test_personas.py`
- Modify: `tests/test_persona_domains.py`
- Modify: `tests/test_routing_table.py`
- Modify: `tests/test_skill_lint.py`
- Modify: `tests/test_skill_lint_routing.py`
- Modify: `tests/test_execution_mode.py`
- Modify: `tests/test_long_agent_workflow_routing.py`
- Modify: `tests/test_desk_workflows_script.py`
- Modify: `tests/test_desk_workflow_runner.py`
- Modify: `tests/test_arena_runner.py`
- Modify: `tests/test_skills_api.py`
- Modify: `tests/gateway/test_identity.py`
- Modify: `tests/gateway/test_http_enroll.py`

**Step 1: Write failing persona/routing tests.**

Assert:

- four persona specs with the same curated tool list;
- `limit_manager` prompt and Limits skill catalog;
- schema/frontend/workflow literals accept the persona;
- “Are we within the vega/RhoQ/VaR limit?” routes to `limit_manager`;
- “Calculate exposure/run stress/how should we hedge?” routes to
  `risk_manager`;
- “Hedge this breach” sequences `limit_manager -> risk_manager`;
- Interactive/Auto/YOLO policy text matches runtime classifications;
- action cards display `limit_manager`;
- `_ACTION_CARD_PERSONAS` and the golden-workflow persona `Literal` accept
  `limit_manager`;
- every persona-targeted meta-policy `applies_to` list includes `limit_manager`;
- gateway enrollment/thread creation accepts the persona.
- the previously disabled Ask Limit Manager action now opens the floating
  assistant with explicit Limits context and server-owned persona routing.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_personas.py tests/test_persona_domains.py \
  tests/test_routing_table.py tests/test_skill_lint_routing.py \
  tests/test_execution_mode.py tests/test_hitl.py \
  tests/test_golden_workflow_schema.py tests/test_thread_source.py \
  tests/test_desk_workflows_script.py tests/test_desk_workflow_runner.py \
  tests/test_arena_runner.py tests/test_skills_api.py \
  tests/gateway/test_identity.py tests/gateway/test_http_enroll.py -q
```

**Step 3: Implement persona, prompts, and skills.**

`limit_manager` owns definitions, monitoring truth, incidents, schedules, and
analysis reports. `risk_manager` retains exposure, VaR/stress computation, and
hedge feasibility. Update stale prompt language that currently sends all limits
work to `risk_manager`.

Add `limit_spec` to `all_personas`, add Limits domains, update the prompt file
registry, `_ACTION_CARD_PERSONAS`, the golden-workflow schema, and all persona
unions/mappings. Add `limit_manager` to clarification, cost-preview,
delegated-scope, headless, Python-analysis, read-before-compute, reply-options,
and YOLO/HITL meta-policy applicability. Enable the frontend Ask Limit Manager
action only in this slice.
Preserve the existing
“Clarify the user's intent. Plan. Act.” product workflow and product-level
messages.

**Step 4: Run full agent/skill slice and frontend form tests.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_personas.py tests/test_persona_domains.py \
  tests/test_routing_table.py tests/test_skill_lint.py \
  tests/test_skill_lint_routing.py tests/test_execution_mode.py \
  tests/test_hitl.py tests/test_golden_workflow_schema.py \
  tests/test_thread_source.py tests/test_long_agent_workflow_routing.py \
  tests/test_desk_workflows_script.py tests/test_desk_workflow_runner.py \
  tests/test_arena_runner.py tests/test_skills_api.py \
  tests/gateway/test_identity.py tests/gateway/test_http_enroll.py -q
npm --prefix frontend test -- \
  src/routes/SkillsWorkflowForm.test.tsx src/routes/Limits.test.tsx \
  src/routes/Limits.live.test.tsx
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/app/services/deep_agent/prompts/limit_manager.md \
  backend/app/services/deep_agent/prompts/orchestrator.md \
  backend/app/services/deep_agent/prompts/risk_manager.md \
  backend/app/services/deep_agent/prompts/trader.md \
  backend/app/skills/meta/clarification-policy.md \
  backend/app/skills/meta/cost-preview-policy.md \
  backend/app/skills/meta/delegated-scope-policy.md \
  backend/app/skills/meta/headless-policy.md \
  backend/app/skills/meta/python-analysis-policy.md \
  backend/app/skills/meta/read-before-compute-policy.md \
  backend/app/skills/meta/reply-options-policy.md \
  backend/app/skills/meta/yolo-hitl-policy.md \
  backend/app/services/deep_agent/personas.py \
  backend/app/services/deep_agent/persona_domains.py \
  backend/app/services/deep_agent/orchestrator.py \
  backend/app/services/deep_agent/hitl.py \
  backend/app/skills/workflows/limits/manage-limit-definitions/SKILL.md \
  backend/app/skills/workflows/limits/monitor-limits/SKILL.md \
  backend/app/skills/workflows/limits/handle-limit-incident/SKILL.md \
  backend/app/skills/workflows/limits/manage-limit-schedule/SKILL.md \
  backend/app/skills/workflows/limits/generate-limit-analysis-report/SKILL.md \
  backend/app/schemas.py backend/app/services/desk_workflows_script.py \
  backend/app/services/desk_workflow_runner.py backend/app/routers/skills.py \
  backend/app/services/arena/runner.py backend/app/golden_workflows/schema.py \
  frontend/src/types.ts frontend/src/routes/Limits.tsx \
  frontend/src/routes/Limits.live.tsx frontend/src/routes/Limits.test.tsx \
  frontend/src/routes/Limits.live.test.tsx \
  frontend/src/routes/SkillsWorkflowForm.tsx \
  frontend/src/routes/SkillsWorkflowForm.test.tsx \
  tests/test_hitl.py tests/test_golden_workflow_schema.py \
  tests/test_thread_source.py tests/test_personas.py \
  tests/test_persona_domains.py tests/test_routing_table.py \
  tests/test_skill_lint.py tests/test_skill_lint_routing.py \
  tests/test_execution_mode.py tests/test_long_agent_workflow_routing.py \
  tests/test_desk_workflows_script.py tests/test_desk_workflow_runner.py \
  tests/test_arena_runner.py tests/test_skills_api.py \
  tests/gateway/test_identity.py tests/gateway/test_http_enroll.py
git commit -m "feat(limits-agent): add limit manager persona"
```

### Task 23: Migrate morning breach commentary to authoritative limits

**Files:**

- Create: `backend/alembic/versions/0048_refresh_morning_limit_workflow.py`
- Create: `backend/app/tools/assemble_limit_analysis_report.py`
- Create: `tests/test_migration_0048.py`
- Create: `tests/test_morning_limit_workflow.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/desk_workflow_seed.py`
- Modify: `backend/app/tools/limits.py`
- Modify: `backend/app/tools/__init__.py`
- Modify: `backend/app/services/agents.py`
- Modify: `backend/app/services/deep_agent/dynamic_subagents.py`
- Modify: `backend/app/services/deep_agent/hitl.py`
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `backend/app/skills/meta/yolo-hitl-policy.md`
- Modify: `backend/app/services/risk_limits.py`
- Modify: `tests/test_hitl.py`
- Modify: `tests/test_personas.py`
- Modify: `tests/test_capability_assignments.py`
- Modify: `tests/test_audit_registration.py`
- Modify: `tests/test_pilot_workflow_seed.py`
- Modify: `tests/test_fanout_reconcile.py`

**Step 1: Write failing seed/reconciler tests.**

Require:

- the same seed slug remains allowlisted and server-stamped;
- persona changes to `limit_manager`;
- launch mode `interactive` returns a validation error before executing a step,
  clearing a pin, or writing any monitoring/report state, while Auto and YOLO
  follow their existing authority rules;
- launch creates a server-stamped invocation id and atomically clears any prior
  pinned limit run from `Workflow.canonical_snapshot_ids`;
- the first step runs/reuses a real `LimitMonitoringRun`, atomically writes
  `{invocation_id, monitoring_run_id}` to that field, and every later turn
  reloads it into trusted runnable context;
- server enumeration returns every authoritative warning, breach, and configured
  unknown evaluation id for that pinned run;
- read-only fan-out produces one commentary record per server id;
- reconciliation accepts a server-chosen `id_field="evaluation_id"`, reports
  covered and failed ids, and never trusts a model-supplied scope list;
- finalization creates a frozen limit-analysis report for the same pinned run,
  even if a newer monitoring run appears before fan-out completes;
- a second invocation whose first step fails cannot reuse the previous
  invocation's pin; reconciliation/finalization reject it fail-closed;
- `assemble_limit_analysis_report` is registered in both tool registries as
  audited `DOMAIN_WRITE`, is statically write-risk classified for HITL, and is
  forbidden inside `FanoutReadOnly`;
- the assembler name appears in both the YOLO/HITL policy and orchestrator
  batch-size-one mirror lists;
- old synthetic `RiskRun.metrics["limit_breaches"]` is no longer read by the
  migrated workflow;
- exact migration metadata `revision = "0048_morning_limits"` and
  `down_revision = "0047_limit_schedule_outbox"`, with current `init_db()` run
  after upgrading through 0047 and before head, plus idempotent seed refresh
  behavior.

**Step 2: Run and verify failure.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_migration_0048.py tests/test_morning_limit_workflow.py \
  tests/test_pilot_workflow_seed.py tests/test_fanout_reconcile.py \
  tests/test_hitl.py tests/test_capability_assignments.py \
  tests/test_audit_registration.py tests/test_personas.py -q
```

**Step 3: Update the canonical seed and migration.**

At the run endpoint, create a server UUID invocation id, capture it in
`_desk_workflow_drive_factory`, and atomically replace the Workflow snapshot
fields with the new invocation id and no limit run id before streaming. For this
allowlisted seed slug, reject Interactive mode before that mutation because the
current script runner cannot resume a suspended step; document the
one-action-at-a-time Interactive alternative. Pass the
id through an explicit trusted `desk_workflow_invocation_id` service parameter,
never inside user launch args. The existing `run_limit_monitoring` tool writes
its server-created run id to
`Workflow.canonical_snapshot_ids["limit_monitoring_run_id"]` only when the row's
invocation id still matches the trusted runnable context. On every later turn,
`services/agents.py` reloads that Workflow row and stamps both ids into
configurable runtime context; immutable launch args remain unchanged. The
assembler requires both ids to match and rejects a model-authored substitute or
missing/stale pin. Test the handoff across separate turns, process/session reload,
a failed second launch, and overlapping invocation invalidation.
Generalize
`reconcile_fanout_coverage()` from its current hard-coded `position_id` contract
to a server-selected id field, and use `evaluation_id` here. The new deterministic
assembler accepts commentary keyed by evaluation id, re-enumerates the exact
pinned run server-side, rejects a substituted/latest run id, and calls the frozen
report service. Register it in `QUANT_AGENT_TOOLS`, `DEEP_AGENT_TOOL_NAMES`,
audit, static HITL write-risk, and read-only-fanout denial lists. Keep
`assemble_breach_report` and its public compatibility reader for one release,
but remove them from the seed path and mark them deprecated.

Use migration `0048` to update already-seeded rows without overwriting a
user-authored slug collision, matching `seed_desk_workflows`. Use revision id
`0048_morning_limits` with
`down_revision = "0047_limit_schedule_outbox"`.

**Step 4: Run fan-out/governance regressions.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_migration_0048.py tests/test_morning_limit_workflow.py \
  tests/test_pilot_workflow_seed.py tests/test_fanout_reconcile.py \
  tests/test_dynamic_subagents_orchestrator.py \
  tests/test_eval_gate.py tests/test_fanout_readonly.py tests/test_hitl.py \
  tests/test_capability_assignments.py tests/test_audit_registration.py \
  tests/test_personas.py -q
git diff --check
```

**Step 5: Commit.**

```bash
git add backend/alembic/versions/0048_refresh_morning_limit_workflow.py \
  backend/app/tools/assemble_limit_analysis_report.py \
  backend/app/main.py backend/app/desk_workflow_seed.py \
  backend/app/tools/limits.py \
  backend/app/tools/__init__.py \
  backend/app/services/agents.py \
  backend/app/services/deep_agent/dynamic_subagents.py \
  backend/app/services/deep_agent/hitl.py \
  backend/app/services/deep_agent/prompts/orchestrator.md \
  backend/app/skills/meta/yolo-hitl-policy.md \
  backend/app/services/risk_limits.py \
  tests/test_migration_0048.py tests/test_morning_limit_workflow.py \
  tests/test_pilot_workflow_seed.py \
  tests/test_fanout_reconcile.py tests/test_hitl.py \
  tests/test_capability_assignments.py tests/test_audit_registration.py \
  tests/test_personas.py
git commit -m "feat(limits-agent): migrate morning commentary"
```

### Task 24: Complete end-to-end hardening, documentation, and rollout checks

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`
- Create: `scripts/seed_limits_smoke.py`
- Create: `tests/test_seed_limits_smoke.py`
- Create: `tests/test_limit_end_to_end.py`
- Modify: `config/agent_channels.example.yml` only if a persona-specific model/tag
  is introduced

**Step 1: Add deterministic end-to-end acceptance coverage.**

Create a minimal book with delta, RhoQ, scenario VaR/CVaR, and named stress
definitions. Exercise:

1. activate definitions;
2. manual monitoring;
3. warning/breach/unknown results;
4. incident transitions and deduplicated warning;
5. schedule occurrence/restart recovery;
6. frozen report;
7. task/report/deep-link payloads;
8. Interactive/Auto/YOLO agent authority with scripted models, never live LLMs.

Add `scripts/seed_limits_smoke.py` with an explicit `--database-url`. It must use
domain services to create a deterministic portfolio, positions, pricing profile,
engine config, market snapshot/effective market evidence, active definitions,
and reusable risk/scenario/backtest evidence. It must not call live market data,
a broker, an IM gateway, or an LLM. Test idempotent reruns and rejection of the
production `data/open_otc.sqlite3` path.

**Step 2: Validate migrations on a scratch database.**

```bash
rm -f /tmp/open-otc-limits-smoke.sqlite3
OPEN_OTC_DATABASE_URL=sqlite+pysqlite:////tmp/open-otc-limits-smoke.sqlite3 \
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m alembic upgrade head

rm -f /tmp/open-otc-limits-orm-first.sqlite3
OPEN_OTC_DATABASE_URL=sqlite+pysqlite:////tmp/open-otc-limits-orm-first.sqlite3 \
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m alembic upgrade \
  0045_arena_run_trials
OPEN_OTC_DATABASE_URL=sqlite+pysqlite:////tmp/open-otc-limits-orm-first.sqlite3 \
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -c \
  'from app.database import init_db; init_db()'
OPEN_OTC_DATABASE_URL=sqlite+pysqlite:////tmp/open-otc-limits-orm-first.sqlite3 \
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m alembic upgrade head
```

Use `rm` only for this explicitly named scratch file. Never point this command
at `data/open_otc.sqlite3`.

**Step 3: Run backend and frontend gates.**

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest tests/test_limit_*.py tests/test_limits_*.py \
  tests/gateway/test_limit_warning_delivery.py \
  tests/test_seed_limits_smoke.py tests/test_hitl.py tests/test_personas.py \
  tests/test_routing_table.py -q

PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  -m pytest -q

npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

**Step 4: Browser smoke on alternate ports.**

Start against the scratch database and isolated artifact directories:

```bash
PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  scripts/seed_limits_smoke.py \
  --database-url sqlite+pysqlite:////tmp/open-otc-limits-smoke.sqlite3

OPEN_OTC_DATABASE_URL=sqlite+pysqlite:////tmp/open-otc-limits-smoke.sqlite3 \
OPEN_OTC_ARTIFACT_DIR=/tmp/open-otc-limits-artifacts \
OPEN_OTC_LIMIT_SCHEDULER=off \
OPEN_OTC_LIMIT_NOTIFICATIONS=on \
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m uvicorn \
  app.main:app --app-dir backend --port 8017

VITE_API_TARGET=http://127.0.0.1:8017 \
  npm --prefix frontend run dev -- --port 5187
```

Open `http://localhost:5187/limits` and verify:

- all five tabs;
- light, dark, and compact density;
- definition draft/activation and stale-write feedback;
- manual monitoring polling and separate warning/breach/unknown rendering;
- RhoQ end to end;
- incident actions/timeline;
- schedule edit/enable/run-now/history with runtime-disabled notice;
- report generation/reader;
- Tasks/Reports deep links;
- floating Limit Manager context;
- refresh and Back/Forward URL restoration;
- sidebar badge and cursor-deduplicated toast.

Then run a second smoke with the scheduler enabled on the stable non-reload
backend and one short-lived test schedule. Confirm one occurrence, no duplicate
claim, warning dedup, and recovery after process restart.

**Step 5: Update operator documentation.**

Document:

- definitions and source semantics;
- rho versus RhoQ bump/unit conventions;
- scenario versus historical VaR/CVaR;
- `unknown` behavior;
- Interactive/Auto/YOLO authority;
- scheduler environment variables and dedicated stable-worker recommendation;
- independently enabled notification-consumer settings, gateway-owner IM
  delivery, destination setup, and retry visibility;
- migration command;
- Limits API/UI entry points;
- rollback/disable procedure.

Add the user-facing feature under `[Unreleased]` in `CHANGELOG.md`. Add the new
subsystem and gotchas to `CLAUDE.md`.

**Step 6: Commit the hardening/docs slice.**

```bash
git add README.md CHANGELOG.md CLAUDE.md \
  scripts/seed_limits_smoke.py tests/test_seed_limits_smoke.py \
  tests/test_limit_end_to_end.py
git diff --cached --stat
```

If and only if a persona-specific channel entry was introduced, also stage
`config/agent_channels.example.yml`. If smoke exposed a defect, fix and validate
it in the owning earlier slice or add each exact extra path here; never stage
broad directories. Verify no unrelated changes are staged, then:

```bash
git commit -m "feat(limits): complete module hardening"
```

## Final acceptance checklist

- [ ] Delta, gamma, vega, theta, rho, and RhoQ limits evaluate across all
      approved scopes/aggregations/transforms.
- [ ] Scenario and historical VaR/CVaR remain explicitly distinct.
- [ ] Named stress loss never silently substitutes another scenario.
- [ ] Every run pins definition versions, source ids, freshness, coverage, FX,
      valuation time, and evidence hash.
- [ ] Warning, breach, unknown, task failure, waiver, and resolution remain
      semantically distinct.
- [ ] Scheduled and manual monitoring produce identical results from identical
      evidence.
- [ ] Scheduler claims, retries, misfires, overlap, DST, and restart recovery are
      deterministic and tested.
- [ ] Web, Agent Desk, and IM warnings are idempotent and retryable without
      rerunning risk.
- [ ] Reports contain all frozen evidence and cannot be narrowed by agent prose.
- [ ] `limit_manager` is distinct from `risk_manager`.
- [ ] Interactive / Auto / YOLO behavior matches the approved authority table.
- [ ] Dynamic fan-out is server-scoped and read-only.
- [ ] Backend, frontend, migration, browser, theme, density, and deep-link gates
      pass.

## Rollout

1. Deploy migrations and code with `OPEN_OTC_LIMIT_SCHEDULER=off`.
2. Validate manual monitoring, incidents, report generation, web warnings, and
   outbox retries.
3. Configure explicit Agent Desk and IM destinations and verify one test warning.
4. Enable the scheduler on one stable non-reload worker.
5. Enable one low-frequency schedule and inspect its first occurrence, task,
   monitoring run, evaluations, notifications, and report.
6. Expand schedules only after restart recovery and duplicate-claim checks pass.

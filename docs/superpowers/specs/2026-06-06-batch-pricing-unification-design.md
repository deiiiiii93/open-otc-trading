# Batch Pricing Unification — Design

**Date:** 2026-06-06
**Status:** Approved (pending implementation)

## Problem

Two batch flows price the entire portfolio independently:

- **Run Pricing** (Positions page) → `POST /api/portfolios/{id}/positions/price-task`
  → `TaskRun(kind="position_pricing")` → `PositionValuationRun` + per-position
  `PositionValuationResult` rows (price/MV/PnL; Greeks only when `compute_greeks`
  is set, which the UI never sets).
- **Run Risk** (Risk page) → `POST /api/risk/runs` → `TaskRun(kind="risk_run")`
  → `RiskRun.metrics` via `calculate_portfolio_risk` (always prices **with**
  Greeks: totals / by_currency / per-position attribution).

The risk run is a strict superset of the pricing run, so running both pays for
two full pricing passes. Separately, the Tasks page renders its Links column as
plain text (no way to reach the risk results from a task), and the risk
displays omit PV (market value) even though the data is computed.

## Decisions (user-confirmed)

1. **One pass, both outputs** — a single `batch_pricing` task prices the
   portfolio once with Greeks and persists BOTH the `PositionValuationRun`
   (Positions page data) AND the `RiskRun` metrics (Risk page data).
2. **New endpoint, clean break** — `POST /api/batch-pricing/runs`; the two old
   POST endpoints are deleted (no external consumers beyond this repo).
3. **Tasks → inline risk-report dialog** — clicking the risk link on a task row
   opens a dialog on the Tasks page (ReportReader pattern); no cross-page
   navigation plumbing.
4. **PV fields everywhere risk renders** — both the new dialog and the Risk
   page `GreeksSummary` show Market Value (PV) and PnL.

## Backend

### New service: `backend/app/services/batch_pricing.py`

- `queue_batch_pricing(session, *, portfolio_id, position_ids=None,
  pricing_parameter_profile_id=None, market_snapshot_id=None)
  -> tuple[RiskRun, TaskRun]`
  - Validates portfolio, resolves scoped position ids (reuse
    `_resolve_risk_positions`), creates a queued `RiskRun` and a
    `TaskRun(kind="batch_pricing", risk_run_id=run.id)`.
- `execute_batch_pricing_task(task_id, risk_run_id, session_factory)`
  - Mirrors today's `_execute_risk_run_task`: resolve positions, build
    per-position market context via `_pricing_position_context`, run
    `calculate_portfolio_risk` (one pricing pass, Greeks included, parallel
    workers + progress callback as today).
  - **Then additionally** persists a `PositionValuationRun` +
    `PositionValuationResult` rows mapped from the risk rows:
    - `ok=pricing_ok`, `price`, `market_value`, `pnl`, `error=pricing_error`
    - `result_payload` = Greeks (`delta/gamma/vega/theta/rho/rho_q`) +
      diagnostics carried on the risk row
    - `market_inputs` from the per-position snapshots resolved by
      `_pricing_position_context`
    - `valuation_run.summary` = `positions/priced/failed/market_value/pnl`
      plus `delta` and `vega` sums (Positions header tiles all populate)
  - Task `result_payload` = `{"risk_run_id": N, "valuation_run_id": M}`.
  - Status flow unchanged: per-position failures → `completed_with_errors`,
    synced to the linked `RiskRun` by the existing `_sync_linked_status`.

The old `queue_position_pricing` / `execute_position_pricing_task` and
`queue_portfolio_risk` / `execute_risk_run_task` task paths are removed once
callers migrate. The synchronous `price_portfolio_positions` stays (used by
the single-position price endpoint and the agent `price_positions` tool).

### Models

- `TaskKind.BATCH_PRICING = "batch_pricing"` added.
- Old enum members (`POSITION_PRICING`, `RISK_RUN`) stay so historical task
  rows keep rendering and filtering.
- **No Alembic migration** — the valuation-run link rides in the task's JSON
  `result_payload`; the existing `risk_run_id` FK covers the dialog link.

### API

- **New** `POST /api/batch-pricing/runs`
  - Body: `{portfolio_id: int, position_ids?: int[],
    pricing_parameter_profile_id?: int}`
  - Returns `RiskRunOut` (already exposes `task_id`); 404 on unknown
    portfolio, 400 on bad position scope.
- **Deleted**: `POST /api/portfolios/{id}/positions/price-task`,
  `POST /api/risk/runs`.
  - The price-task endpoint's single-position engine-override mode is dropped
    with it; that capability remains on the sync single-position
    `POST .../positions/price` path the UI row button uses.
- **Unchanged**: `GET /api/risk/runs/{id}`, `GET .../risk-runs/latest`,
  `GET .../runs` (valuation runs), `GET /api/tasks*`, sync
  `POST .../positions/price`, `POST /api/risk/scenarios`.

### Agent tools

- `run_risk` tool keeps its name/schema; `domains/risk.run()` internally
  queues the unified batch task (result shape — task + risk_run ids —
  unchanged). Cost estimator unchanged.
- `price_positions` tool stays synchronous and override-capable; untouched.
- No prompt / skill / `DEEP_AGENT_TOOL_NAMES` churn.

### Audit

- `positions.pricing_queued` and `risk.run.queued` collapse into one
  `batch_pricing.queued` event with `task_id`, `risk_run_id`, scoped
  `position_ids`, `pricing_parameter_profile_id`.

## Frontend

### Tasks page — linked risk report

- `LinkCell`: for tasks with `risk_run_id` (new `batch_pricing` + historical
  `risk_run` rows), "Risk #N" becomes a button opening `RiskReportDialog`.
- New `RiskReportDialog` (follows `TaskErrorDialog`/`ReportReader` modal
  pattern): fetches `GET /api/risk/runs/{id}` on open; renders status/method/
  created chips, totals incl. **Market Value (PV)** and **PnL** (reuses
  `GreeksSummary` with `onPromoteToReport` made optional), and the
  per-position attribution table (reuses `PnlAttribution`); failed positions
  visible in the table.
- `labelKind` gains `batch_pricing → "Batch pricing"`; old kinds keep labels.

### Risk page — PV tiles

- `GreeksSummary` grows from 6 to 8 tiles, PV first: **Market Value (PV)**,
  **PnL**, Delta Cash, Gamma Cash, Vega, Theta, Rho, RhoQ — for the
  single-currency totals AND each `by_currency` bucket (both already carry
  `market_value`/`pnl`).

### Page rewiring

- **Risk.live**: `runRiskForPortfolio` posts to `/api/batch-pricing/runs`;
  response shape and task polling unchanged. Page-context `run_risk` action
  updates its `backend_endpoint`.
- **Positions.live**: `handleRunPricing` posts to `/api/batch-pricing/runs`;
  the response is now a run (poll `task_id` from it), then reload valuation
  runs as today. Greeks columns and Delta/Vega header tiles populate after
  every batch run.
- **Button labels**: both pages relabel to **"Run Batch Pricing"** (Risk page
  keeps the `⌘R` hint).

## Testing

### Backend (pytest, repo-root `tests/`)

- New `tests/test_batch_pricing.py`:
  - queue creates linked RiskRun + TaskRun (kind, FK, queued status)
  - execute writes BOTH `RiskRun.metrics` and a `PositionValuationRun` whose
    per-position results match the risk rows (price/MV/PnL/Greeks/error) —
    use non-default input values so equality is meaningful
  - failure path → `completed_with_errors` synced on task and run
  - endpoint contract: 200 happy path, 404 unknown portfolio; old POST
    endpoints return 404/405
  - task `result_payload` carries both run ids
- Update existing touched tests: `test_api.py`, `test_risk_engine.py`,
  `test_audit_endpoint.py`, `test_page_context_schema.py`,
  `test_position_pricer_parallel.py`, `test_skill_rewrite_regression.py`.

### Frontend (vitest)

- `Tasks.test.tsx`: risk link renders as button; dialog opens, fetches run,
  shows PV fields; tasks without `risk_run_id` render no button.
- `GreeksSummary.test.tsx`: PV/PnL tiles in totals and by-currency modes.
- `Risk.live.test.tsx` / `Positions.live.test.tsx`: new endpoint + polling.

## Out of scope

- Single-position sync pricing (row "Price" button, agent `price_positions`).
- Scenario runs (`POST /api/risk/scenarios`) — unchanged, still triggered by
  the Risk page after a completed run.
- Report jobs (`report_job` tasks) and their artifacts — unchanged (HTML/XLSX
  already include MV/PnL).
- URL routing / cross-page navigation from Tasks.

## Addendum (2026-06-06, post-merge): agent tool surface unified too

The "Agent tools" section above is **superseded**. After the endpoint
unification merged, a follow-up review of the agent surface led to a second
decision (user-confirmed): all agent-routed batch pricing now goes through one
tool as well.

- `run_risk` and `price_positions` agent tools were **replaced by a single
  `run_batch_pricing` tool** (queued, profile-driven; one pass persists a
  PositionValuationRun and a RiskRun). `DEEP_AGENT_TOOL_NAMES`, HITL maps,
  compaction, personas, prompts, skills (`price-portfolio`, `run-risk`), and
  meta policies were all rewired; orchestrator compound pricing+risk is now a
  single `run-risk` delegation.
- `RunBatchPricingInput` is `extra="forbid"`: market overrides
  (spot/r/q/vol) and `valuation_date` are rejected loudly, never silently
  dropped. Override pricing remains only on the sync position-detail dialog
  endpoint (`POST .../positions/price`) and `price_product` what-ifs.
- Task registry: `propose_run_risk` → `propose_run_batch_pricing`
  (trader + risk_manager; tool-shaped inputs). A task-type alias keeps old
  persisted task rows resolvable on HITL resume/executor paths.
- **Deploy note**: pending HITL action cards naming `run_risk` /
  `price_positions` have no callable tool after this change; resume degrades
  to a tool-not-found error and the agent re-proposes. Drain or dismiss such
  pending cards before deploying (live DB had zero at merge time).

### Valuation-date semantics (review follow-up, user-decided)

- Profile-bound queued runs price **as-of the profile's valuation date**
  (engine maturity, quote `as_of <=` resolution, and the
  `PositionValuationRun.valuation_date` stamp all follow it). Unbound runs
  keep queue-time (`created_at`) semantics.
- Historical-run policy: **visibility only** (decided 2026-06-06).
  `RiskRun.metrics.valuation_as_of` is stamped and `get_latest_risk_run`
  surfaces it; prompts instruct the agent not to hedge off a run whose as-of
  lags its creation. No hard exclusion from latest-risk selection — a
  staleness threshold (column + selection filter, or a hedging-only gate)
  was considered and deliberately deferred because "yesterday's close run
  this morning" is the normal desk pattern and any threshold is a desk
  policy choice.

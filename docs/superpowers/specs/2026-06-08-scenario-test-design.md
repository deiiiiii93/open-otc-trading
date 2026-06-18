# Scenario Test Feature — Design Spec

**Date:** 2026-06-08
**Branch / worktree:** `worktree-scenario-test`
**Status:** Approved design, pending spec review → implementation plan
**Integration strategy:** Approach 1 — real QuantArk `EquityPortfolio` bridge driving `StressTestEngine`

---

## 1. Goal & Scope

Add a first-class **Scenario Test** capability that makes *full use* of QuantArk's
`stresstest` module: predefined + custom + saved scenarios, async persisted runs,
structured results (P&L, greeks deltas, worst/best, VaR/CVaR, per-position and
per-underlying breakdown), plus an HTML report and parquet/csv/json exports.
Reachable from the agent (tool + skill) and from a dedicated frontend page.

### In scope
- A bridge that assembles a genuine QuantArk `EquityPortfolio` from DB positions +
  a pricing parameter profile.
- A domain service that drives `StressTestEngine.run_static_scenarios`,
  `ResultAggregator`, `ResultExporter`, `ReportGenerator`.
- Scenario authoring: `ScenarioLibrary` (predefined + historical), `ScenarioBuilder`
  (custom multi-parameter), `ScenarioStorage` (saved YAML/JSON sets).
- Async persisted runs: `scenario_test_runs` table + `TaskKind.SCENARIO_TEST`,
  mirroring the batch-pricing run model.
- Agent tools, a workflow SKILL.md (+ orchestrator routing), REST endpoints, and a
  new **Scenario Test** frontend page.

### Out of scope (YAGNI)
- Dynamic / time-stepped scenarios and hedging (QuantArk raises `NotImplementedError`).
- Migrating the legacy spot×vol grid (`RiskRun.scenario_cells`) to the new engine —
  it stays as a fast inline Risk-page widget. The new feature is **additive**.
- FI key-rate / spread stresses — the catalog is architecture-ready but these are
  gated off in v1 (the book is equity-focused).
- Monte-Carlo scenario generation; interactive plotly dashboards (static HTML report
  only in v1, plots behind an optional config flag).

---

## 2. Naming & Coexistence

The legacy spot×vol grid already uses the word "scenario" (`RiskRun.scenario_cells`,
`POST /api/risk/scenarios`, `<ScenarioGrid>`). To avoid collision the new feature uses
the `scenario_test` namespace **everywhere**:

| Concern        | Legacy grid                  | New feature                              |
|----------------|------------------------------|------------------------------------------|
| Table          | `risk_runs.scenario_cells`   | `scenario_test_runs`                     |
| Task kind      | (none / inline)              | `TaskKind.SCENARIO_TEST`                 |
| Service        | `risk_engine.run_portfolio_scenarios` | `services/domains/scenario_test.py` |
| Tool           | (none)                       | `tools/scenario_test.py`                 |
| Skill          | (none)                       | `workflows/risk/run-scenario-test/`      |
| REST           | `/api/risk/scenarios`        | `/api/scenario-test/*`                   |
| Frontend       | `Risk.tsx` `<ScenarioGrid>`  | `routes/ScenarioTest.tsx`                |

The legacy grid is left untouched.

---

## 3. Architecture & Layering

Follows the established `tools → domains → quantark/bridge → QuantArk` stack.

```
frontend/src/routes/ScenarioTest.tsx ───────────────┐
backend/app/main.py   REST /api/scenario-test/*      │
backend/app/tools/scenario_test.py   agent @tools    │──→ services/domains/scenario_test.py
backend/app/skills/workflows/risk/run-scenario-test/SKILL.md (+ routing)   │        │
                                                                            │        ├─ scenario_test_bridge.py   ★ NEW heart: DB positions + profile → EquityPortfolio
                                                                            │        ├─ scenario_catalog.py        ScenarioLibrary / ScenarioBuilder / ScenarioStorage wrappers
                                                                            │        └─ scenario_test_engine.py    drive StressTestEngine + ResultAggregator + ResultExporter + ReportGenerator
backend/app/services/scenario_test_runner.py   queue + async execute (mirrors batch_pricing)
backend/app/models.py   ScenarioTestRun + TaskKind.SCENARIO_TEST
backend/app/schemas.py  ScenarioStressSpec / ScenarioSpec / ScenarioTestRunRequest / ScenarioTestRunOut
```

The **only genuinely new abstraction is the bridge**. Everything else composes
existing QuantArk classes and existing app patterns.

---

## 4. Data Model

### 4.1 `ScenarioTestRun` (`models.py`) + Alembic migration

New `scenario_test_runs` table, mirroring `RiskRun`'s shape:

| Column                          | Type        | Notes |
|---------------------------------|-------------|-------|
| `id`                            | int PK      | |
| `portfolio_id`                  | FK portfolios, indexed | |
| `pricing_parameter_profile_id`  | FK pricing_parameter_profiles, **required** | supplies per-underlying baseline r/q/vol/spot + valuation date |
| `resolved_position_ids`         | JSON        | positions actually included (post view-resolution) |
| `status`                        | str(40)     | `queued` / `running` / `completed` / `empty` / `failed` |
| `scenario_spec`                 | JSON        | serialized scenarios that were run (reproducibility) |
| `config`                        | JSON        | `{calculate_greeks, greeks_method, export_formats, save_detailed_results}` |
| `results`                       | JSON        | shaped `StressTestResults` (see §7) |
| `excluded_positions`            | JSON        | `[{position_id, reason}]` — unsupported/closed mapping |
| `artifacts`                     | JSON        | `{report_html_path, export_paths: []}` |
| `created_at`                    | DateTime    | |

Relationships: `portfolio`, `pricing_parameter_profile`, `task_runs` (back-populates a
new `TaskRun.scenario_test_run_id` FK, paralleling `TaskRun.risk_run_id`).

`TaskKind` enum gains `SCENARIO_TEST = "scenario_test"`.

**Migration discipline (from project memory):** the Alembic migration must use
migration-local Core `Table` definitions, **never** ORM models or services (they drift
to the future schema). Add the `scenario_test_runs` table and the
`task_runs.scenario_test_run_id` column.

### 4.2 Saved scenario sets

No new table — reuse QuantArk's `ScenarioStorage` (YAML/JSON files). Files live under a
managed dir `data/scenario_sets/` (configurable via `Settings`). The catalog
lists/loads sets by name. This matches the module's own design and keeps sets
versionable as files.

---

## 5. The Bridge — `services/scenario_test_bridge.py` (the heart)

```python
def build_equity_portfolio(
    positions: list[Position],
    profile: PricingParameterProfile,
    *,
    valuation_date: datetime,
    portfolio_name: str,
) -> tuple["EquityPortfolio", list[dict]]:
    """Assemble a QuantArk EquityPortfolio from DB positions + a pricing profile.

    Returns (portfolio, excluded) where `excluded` is
    [{"position_id": id, "reason": str}] for positions dropped by the same
    policy risk runs use (`risk_pricing_exclusion`).
    """
```

Steps:
1. For each **underlying** in the resolved positions, resolve a baseline
   `PricingEnvironmentSnapshot` from the pricing parameter profile (r/q/vol/spot +
   valuation date). **One env per underlying** — the correct, consistent stress
   baseline. Market resolution reuses the risk path's `_pricing_position_context`,
   so per-position snapshots come from the pricing profile and, where a profile row
   is absent, fall back to the same instrument-level assumption rows the risk runs
   use. **Implemented behavior (revised from the original "fail loud" intent):** the
   stress book equals the risk book — parity with risk runs was judged more valuable
   than failing on missing profile rows.
2. `pricing_environments = {underlying: build_pricing_env(snapshot)}` via
   `quantark.build_pricing_env`.
3. Construct `EquityPortfolio(portfolio_name=..., pricing_environments=...)`.
4. For each non-excluded position:
   - `product = quantark.build_product_for_position(position, snapshot)`
   - `engine = quantark.build_engine_for_position(position, snapshot)`
   - `portfolio.add_position(product, quantity, entry_price, underlying, engine)`
5. Skip + record positions where `quantark.risk_pricing_exclusion(position)` fires
   (mapping unsupported/error, or closed) — identical policy to risk runs, so the
   stress book == the risk book.
6. **Greeks parity:** the app's per-position greeks use
   `GreeksCalculator().calculate(product, env, engine, method="auto")`, while the
   engine calls `portfolio.get_portfolio_greeks(calc, use_analytical=...)` →
   `EquityPosition.get_greeks(env, calc, use_analytical)`. The bridge / engine driver
   reconciles these so stress greeks match desk risk greeks (see §15 contribution (a)).

> **Linchpin verified during exploration:** `risk_pricing_exclusion` does *not* exclude
> snowballs/autocallables — they price through the same `engine.price(product, env)`
> path the engine uses internally. So the bridge prices the whole book.

---

## 6. Scenario Authoring — `services/domains/scenario_catalog.py`

Thin wrappers exposing the QuantArk module to the app + agent:

```python
def list_predefined() -> list[dict]:
    """Names + metadata from ScenarioLibrary.get_all_predefined() + historical set."""

def build_custom(spec: ScenarioSpec) -> Scenario:
    """ScenarioBuilder from a validated spec. v1 params: spot/vol/rate/dividend."""

def save_set(name: str, scenarios: list[Scenario]) -> str: ...   # ScenarioStorage.save_scenarios
def load_set(name: str) -> list[Scenario]: ...                    # ScenarioStorage.load_scenarios
def list_sets() -> list[str]: ...

def resolve_scenarios(request: ScenarioTestRunRequest) -> list[Scenario]:
    """Unify {predefined names + custom specs + saved set name} → list[Scenario]."""
```

**Custom spec shape** (`ScenarioSpec` / `ScenarioStressSpec`):
```
ScenarioSpec  = { name: str, description?: str, stresses: [ScenarioStressSpec] }
ScenarioStressSpec = {
    param: "spot" | "vol" | "rate" | "dividend",
    stress_type: "ABSOLUTE" | "PERCENTAGE" | "VALUE",   # default PERCENTAGE
    value: float,
    level: "portfolio" | "underlying",                   # default portfolio; "position" rejected in v1
    target?: str,                                        # underlying symbol (for level="underlying")
}
```
The friendly `param` enum maps onto the builder methods
`ScenarioBuilder.spot_stress` (adapter `spot`), `vol_stress` (adapter `volatility`/`vol`),
`rate_stress` (adapter `rate`), and `div_yield_stress` (adapter `dividend`/`div_yield`) —
all default-registered for equity workflows. The builder also exposes `key_rate_stress` /
`spread_stress` / `basis_stress`, but v1 validation rejects `param` values outside
{spot, vol, rate, dividend}.

---

## 7. Run Execution & Results Shaping

### 7.1 `services/scenario_test_runner.py`
Mirrors `batch_pricing.queue_batch_pricing` / `execute_batch_pricing_task`:
- `queue_scenario_test(session, *, portfolio_id, pricing_parameter_profile_id,
  scenario_request, config) -> (ScenarioTestRun, TaskRun)` — creates the run
  (`status=queued`) + `TaskRun(kind=SCENARIO_TEST, scenario_test_run_id=run.id)`, then
  `submit_async_task(execute_scenario_test_task, ...)`.
- `execute_scenario_test_task(task_id, run_id, session_factory=None)` — opens a
  worker-thread session (own `SessionLocal`), runs the pipeline, persists, marks task
  finished. (Worker-thread Session discipline from memory.)

### 7.2 `services/domains/scenario_test.py` pipeline
1. Resolve portfolio membership (supports views) + scope to `position_ids`.
2. `portfolio, excluded = build_equity_portfolio(...)`.
3. If portfolio empty (no includable positions) ⇒ `status=empty`, friendly message,
   no engine call (engine raises `ValidationError` on empty — we pre-check).
4. `scenarios = scenario_catalog.resolve_scenarios(request)`.
5. `results = StressTestEngine(StressTestConfig(**config)).run_static_scenarios(portfolio, scenarios)`.
6. Aggregate with `ResultAggregator`: `get_risk_summary`, `calculate_var_cvar(0.95)`,
   `compare_scenarios`, `get_worst_scenario` / `get_best_scenario`.
7. Write exports + report (§8).
8. Shape + persist `results` JSON, `artifacts`, `excluded_positions`; `status=completed`.

### 7.3 Persisted `results` JSON shape
```jsonc
{
  "baseline_value": 0.0,
  "baseline_greeks": { "delta": .., "gamma": .., "vega": .., "theta": .., "rho": .., "rho_q": .. },
  "scenarios": [
    { "name": "Market Crash", "portfolio_value": .., "pnl": .., "pnl_pct": ..,
      "greeks": {..}, "underlying_results": { "AAPL": {num_positions, total_value, greeks} },
      "position_results": [ {position_id, underlying, product_type, quantity, original_value, stressed_value, pnl, pnl_pct, greeks?} ] }
  ],
  "worst_scenario": "Severe Downturn",
  "best_scenario": "Market Rally",
  "risk_summary": { "avg_pnl": .., "max_drawdown_pct": .. },
  "var_cvar": { "confidence": 0.95, "var": .., "cvar": .. },
  "execution_time": .., "num_scenarios": ..
}
```
`position_results` only populated when `config.save_detailed_results` (default true for
small books; the API/tool may disable for large ones).

### 7.4 Performance
Repricings ≈ positions × (scenarios + 1). Acceptable for an async background task.
The engine's own `parallel_execution` is a "future" flag; if a book is large we may
parallelize scenario evaluation in the driver (ThreadPool, as `run_portfolio_scenarios`
does). Not required for v1.

---

## 8. Exports & HTML Report

- `ResultExporter.export(results, output_dir, formats=config.export_formats)` →
  parquet/csv/json under `outputs/scenario_test/<run_id>/`.
- `ReportGenerator().generate_report(results, "<dir>/report.html", title=...)` → HTML.
- Paths recorded in `ScenarioTestRun.artifacts`; served via a download endpoint.
- **Graceful degradation:** if `pyarrow` / `matplotlib` / `plotly` is unavailable, skip
  that artifact and record a note in `artifacts` — the run still succeeds. Visualizer
  plots are behind an optional flag (off by default in v1).

---

## 9. Agent Tools — `tools/scenario_test.py`

All `@capability_gated`, thin adapters over the domain service, registered in
`tools/__init__.py` `QUANT_AGENT_TOOLS` and the deep-agent tool allowlist
(`DEEP_AGENT_TOOL_NAMES`).

| Tool | Group | Purpose |
|------|-------|---------|
| `list_scenario_library` | DOMAIN_READ | predefined + historical + saved set names/metadata |
| `run_scenario_test` | DOMAIN_WRITE (`extra="forbid"`, cost-estimated) | queue an async run: `portfolio_id`, `pricing_parameter_profile_id`, scenarios (predefined names / custom specs / saved set name), config |
| `get_scenario_test_run` | DOMAIN_READ | fetch status/results/artifacts by run id |
| `save_scenario_set` | DOMAIN_WRITE | persist a named custom set |

`run_scenario_test` uses `extra="forbid"` (batch-pricing lesson: forbid silent override
drops) and a cost estimator (~`positions × scenarios × 0.5s`).

---

## 10. Skill + Routing

New compound workflow skill `backend/app/skills/workflows/risk/run-scenario-test/SKILL.md`.
A reference doc `references/risk/scenario-test.md` documents the scenario taxonomy.

> **Test coupling (from project memory):** adding a workflow SKILL.md breaks exact-set +
> count assertions in **six** test files. The plan updates all of them:
> `test_skills_catalog`, `test_skills_catalog_v2`, `test_workflow_skills_phase3`,
> `test_remaining_workflow_skills_phase3`, `test_reference_docs` (the new reference doc
> needs frontmatter), `test_routing_table` (the `OLD_TABLE_ROWS` routing-triple pin).

### 10.1 Full SKILL.md sketch

```markdown
---
name: run-scenario-test
description: Run a portfolio stress / scenario test using the QuantArk stresstest engine. Use when the user asks to stress test a portfolio, run market-crash / vol-spike / rate-hike / historical scenarios, build a custom multi-parameter scenario, or see worst-case P&L, VaR/CVaR, or greeks under stressed markets.
domain: risk
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
  - pricing_parameter_profile_id
optional_context:
  - position_ids
  - scenario_names
  - custom_scenarios
  - scenario_set
write_actions: true
confirmation_required: true
success_criteria:
  - A scenario test run is queued against the named portfolio and pricing profile
  - Results report per-scenario P&L, worst/best, and VaR/CVaR once complete
routing:
  - request: "Stress test or scenario analysis of a portfolio"
    persona: trader
---

## When to use

- User asks to stress test / scenario-test a portfolio, or run market-crash,
  vol-spike, rate-hike, severe-downturn, or historical (1987 / 2008 / COVID) scenarios.
- User wants worst-case P&L, VaR/CVaR, or greeks under stressed markets.
- User describes a custom multi-parameter shock (e.g. "spot -20% and vol +50%").

## Required inputs

`portfolio_id` and `pricing_parameter_profile_id` (the profile supplies the per-underlying
baseline market the scenarios stress). Scenarios come from predefined names, custom specs,
or a saved set. Read `/skills/references/risk/scenario-test.md` for the scenario taxonomy
and `/skills/references/pricing/engines.md` for pricing caveats.

## Procedure

1. Confirm the portfolio and pricing parameter profile (use page context when present).
2. Resolve scenarios: `list_scenario_library` for predefined/saved names; for a custom
   shock, build a `custom_scenarios` spec (param spot/vol/rate/dividend, stress_type,
   value, level, target).
3. Because this is a write (queues a persisted run), confirm with the user, then call
   `run_scenario_test`.
4. Report the queued run id; when complete, read it with `get_scenario_test_run` and
   summarize per-scenario P&L, worst/best, VaR/CVaR, excluded positions, and artifact links.

## Stop conditions

Do not invent stress magnitudes the user did not ask for. If the portfolio resolves to no
includable positions, report that instead of queuing. Escalate to `desk_async` for very
large books.

## Output shape

Return: queued run id + scope; once complete, baseline value, per-scenario P&L / %,
worst & best scenario, 95% VaR/CVaR, per-underlying highlights, excluded-position notes,
and report/export download links.

## References

- `/skills/references/risk/scenario-test.md`
- `/skills/references/pricing/engines.md`

## Example

User: Stress test portfolio 7 against a market crash and COVID, using the EOD profile.
Assistant: Confirm portfolio 7 + EOD profile, resolve the two predefined scenarios,
confirm the write, call `run_scenario_test`, then summarize worst-case P&L and VaR when done.
```

---

## 11. REST Endpoints (`main.py`)

| Method & path | Purpose |
|---|---|
| `GET /api/scenario-test/library` | predefined + historical + saved set names |
| `GET /api/scenario-test/sets` / `POST /api/scenario-test/sets` | list / save saved sets |
| `POST /api/scenario-test/runs` | queue a run → `ScenarioTestRunOut` (run + task) |
| `GET /api/scenario-test/runs?portfolio_id=` | history (newest first) |
| `GET /api/scenario-test/runs/{id}` | detail: status, results, artifacts |
| `GET /api/scenario-test/runs/{id}/artifacts/{name}` | download an export / report file |

Pydantic schemas in `schemas.py`: `ScenarioStressSpec`, `ScenarioSpec`,
`ScenarioTestRunRequest`, `ScenarioTestRunOut`, `ScenarioLibraryOut`.

---

## 12. Frontend — `routes/ScenarioTest.tsx` (+ Sidebar + router registration)

Three zones, reusing existing components (`PortfolioPicker`, profile pickers, `Tabs`,
`Tile`, tokens):

- **(a) Scenario picker** — checkbox list of predefined / historical scenarios + saved
  sets, plus a **custom builder** (add stress rows: param / stress_type / value / level /
  target).
- **(b) Run config** — portfolio + pricing-profile pickers, greeks toggle, export-format
  checkboxes; "Run scenario test" (confirm — it's a write).
- **(c) Results** — run history + selected-run detail: baseline, per-scenario P&L table /
  waterfall, worst/best badges, 95% VaR/CVaR, per-underlying breakdown,
  excluded-positions note, and artifact download links.

Tests: `ScenarioTest.test.tsx` (unit) + `ScenarioTest.live.test.tsx` (live), following
the `Risk` / `HedgeStrategy` page conventions. Add the route to the app router and a
Sidebar entry.

---

## 13. Error Handling & Edge Cases

- **Empty / all-excluded portfolio** → `status=empty`, clear message; never call the
  engine with an empty portfolio (it raises `ValidationError`).
- **Per-scenario pricing failure** for a position → captured per position; the scenario is
  still reported best-effort, with the failure surfaced in `position_results`.
- **Negative stressed vol** → `StressApplicator` guards internally; custom specs are
  validated up front (reject obviously invalid magnitudes).
- **Missing profile coverage** for an underlying → falls back to the risk path's
  instrument-level assumptions (stress book == risk book), not a hard failure. See §4
  step 1 (revised from the original "fail loud" intent).
- **Report/export libs missing** → skip that artifact, note it; run still succeeds.
- **Concurrent / worker thread** → executor uses its own `SessionLocal`; never reuse the
  request session across threads.

---

## 14. Testing Strategy (TDD)

- **Bridge** (`test_scenario_test_bridge.py`): DB positions + profile → `EquityPortfolio`
  with correct per-underlying envs / products / engines; exclusion policy parity with risk
  runs; greeks parity vs `compute_position_greeks` (use **non-default** input values so the
  test can't pass vacuously — characterization-test lesson from memory).
- **Catalog** (`test_scenario_catalog.py`): predefined resolution; custom spec validation
  (good + rejected); save/load round-trip via `ScenarioStorage`.
- **Engine driver** (`test_scenario_test_engine.py`): a small known portfolio under
  `market_crash` yields expected-sign P&L; worst/best selection; VaR/CVaR correctness.
- **Runner** (`test_scenario_test_runner.py`): queue→execute→persist; empty + excluded
  paths; artifact writing into a tmp dir.
- **Tool + API + schema** tests; **skill-catalog six-file** updates; **frontend** unit +
  live tests.
- **Gotcha (from memory):** `python -c` imports the app from the MAIN checkout via the
  venv `.pth`, *not* the worktree — run tests with `PYTHONPATH=<wt>/backend` or via
  `pytest` from the worktree.

---

## 15. Where the User Shapes the Logic (learning-mode contribution points)

During implementation, scaffolding is prepared and these ~5–10 line decisions are handed
over (domain judgment matters most here):

- **(a) Bridge exclusion + greeks-parity policy** — how the bridge maps the app's
  `method="auto"` greeks onto the engine's `use_analytical` interface, and exactly which
  positions to exclude vs best-effort.
- **(b) Custom-spec → `ScenarioBuilder` mapping** — validation strictness, default stress
  type/level, and how `level`+`target` resolve to builder calls.
- **(c) Results shaping** — the VaR/CVaR + worst/best + risk-summary projection returned
  by the API/tool.

---

## 16. Build Sequence

1. **Data model** — `ScenarioTestRun`, `TaskKind.SCENARIO_TEST`, `TaskRun` FK, Alembic
   migration.
2. **Bridge + catalog** — `scenario_test_bridge.py`, `scenario_catalog.py` (core, TDD).
3. **Engine driver + async runner** — `domains/scenario_test.py`,
   `scenario_test_runner.py`.
4. **Exports + HTML report** — wire `ResultExporter` / `ReportGenerator`, artifact
   storage + download.
5. **Agent tools + skill + routing** — `tools/scenario_test.py`, SKILL.md, reference doc,
   **six-file** catalog/test update.
6. **REST** — schemas + `main.py` endpoints.
7. **Frontend** — `ScenarioTest.tsx` + Sidebar + router + tests.

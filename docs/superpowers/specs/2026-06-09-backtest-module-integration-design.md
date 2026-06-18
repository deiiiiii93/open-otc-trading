# Backtest Module Integration — Design Spec

**Date:** 2026-06-09
**Status:** Approved (brainstorming) — pending implementation plan
**Repos touched:** `open-otc-trading` (primary) + `quant-ark` (one engine extension)
**Reference materials:** `quant-ark/backtest/` (engine source), `quant-ark/example/output/otc_autocallable_backtest/` (the target "super detailed report": `dashboard.html`, `summary.json`, `states.csv`, `greeks.csv`, `trades.csv`, `daily_event_summary.csv`, `event_probability.csv`, `results.xlsx`), `quant-ark/example/otc_autocallable_backtest_demo.py` (canonical driver). In-repo precedent: the **scenario_test** feature (`2026-06-08-scenario-test-design.md`) — this spec mirrors its layering field-for-field.

---

## 1. Goal & Scope

### Goal
A portfolio-level **hedging backtest** for the OTC desk. Replay historical market data day-by-day, simulate the dealer's delta-hedging program **netted per underlying**, and surface a rich report covering hedge P&L / effectiveness, greeks over time, autocallable lifecycle (KO / KI / autocall / coupon), transaction costs, and risk metrics. Backtests are driven by **booked positions**, run **asynchronously**, persist to a **`BacktestRun`** row, and are reachable from the **agent**, **REST API**, and a new **React page**.

### In scope
- Wrap **two** quant-ark engine families: **OTC autocallable** (Snowball / Phoenix, index-futures hedge, full lifecycle) and **generic equity** (vanilla / exotic equity-option delta hedge). FI is excluded.
- A run takes **booked positions / a portfolio** as input (like scenario_test), **grouped by underlying**. For each underlying: aggregate **net delta** across all alive positions on that underlying and hedge the **net** with that underlying's resolved hedge instrument. Each autocallable runs its own **lifecycle** so a knocked-out position leaves the book mid-run.
- **Market data:** prefer stored `MarketDataProfile` daily history; when the daily path is not continuous over the window, **backfill from akshare and persist**.
- **Vol path:** user-selectable per run — rolling **realized vol** (default, window default 20 trading days) or **flat** (from profile / assumption).
- **Hedge instrument:** resolved per underlying via the existing **hedging universe** map; index underlyings → index futures (roll + basis); underlyings without a futures mapping → **spot** (no roll).
- **Async execution** → `BacktestRun` + `TaskRun` (`TaskKind.BACKTEST`), progress via `TaskRun.progress_*`.
- **Hybrid report:** native in-app recharts report for the core views (design-token styling) **plus** a downloadable / iframe-able quant-ark `dashboard.html` for the deep-dive.
- Full stack: quant-ark `BookAutocallableBacktestEngine`; oot bridge / market-history backfill / domain pipeline / runner / model + Alembic `0027`; agent tools; skill + routing; REST endpoints; React `Backtest` page.

### Out of scope (YAGNI)
- Fixed-income (DV01 / convexity) backtests.
- Strategy-return (non-hedging) backtests; alpha / signal research.
- Intraday / sub-daily replay.
- Scheduled / recurring / live backtests.
- Hedge-parameter optimization (grid search over bands, etc.).
- Cross-currency netting beyond what the existing currency-aware risk aggregation already provides (per-underlying books are single-currency by construction here; a multi-currency portfolio total is reported per the existing convention, not re-derived).

### Decisions log (from brainstorming)
| # | Decision | Choice |
|---|----------|--------|
| 1 | Engine scope | OTC autocallable **+** generic equity (no FI) |
| 2 | Input driver | Booked positions / portfolio |
| 3 | Market data | Stored DB history; akshare **backfill + persist** when gappy |
| 4 | Run granularity | Portfolio, **grouped by underlying**; aggregate net delta then hedge; lifecycle persisted per autocallable |
| 5 | Execution | **Async** + `BacktestRun` table |
| 6 | Report content | Hedge P&L & effectiveness · greeks · KO/KI events · costs & risk metrics |
| 7 | Report UI | **Hybrid** (native core + quant-ark dashboard export) |
| 8 | Book engine location | **Extend quant-ark** (book engine lives with the other engines + tests) |
| 9 | Vol path | **User-selectable** (realized-window default, flat optional) |
| 10 | Hedge instrument | **Hedging-universe map, spot fallback** |

---

## 2. Naming & Coexistence

- New quant-ark engine: **`BookAutocallableBacktestEngine`** in `quant-ark/backtest/otc/book_engine.py` (kept in `otc/` because it reuses the autocallable lifecycle + index-futures hedge machinery; it also handles non-autocallable products, which simply skip the lifecycle branch). Exported from `backtest/otc/__init__.py`.
- New oot service modules: `services/backtest_bridge.py`, `services/backtest_market_history.py`, `services/backtest_runner.py`, `services/domains/backtest.py`.
- New oot model: `BacktestRun` (`backtest_runs` table). New `TaskKind.BACKTEST`. New `TaskRun.backtest_run_id` FK.
- New agent tools file: `tools/backtest.py`.
- New skill: `skills/workflows/backtest/SKILL.md`; reference: `skills/references/risk/backtest.md`.
- New frontend route: `routes/Backtest.tsx` (+ `.css`, `.test.tsx`, `.live.tsx`).
- "Backtest" does not collide with "scenario test" or "risk run"; all three are distinct `TaskKind`s and distinct `*Run` tables. Sidebar groups Backtest under the existing Risk/Analytics section alongside Scenario Test.

---

## 3. Architecture & Layering

```
quant-ark (engine home, loaded via ensure_quantark_path — NOT pip)
└── backtest/otc/book_engine.py  ← NEW BookAutocallableBacktestEngine
        reuses: AutocallableLifecycleState, pricing engines (quad/pde/mc),
                FuturesHedgePosition, FuturesRollPolicy, event-prob engine,
                AutocallableBacktestDashboard, transaction-cost models
        ▲
        │ (assembled inputs + invoked)
open-otc-trading / backend
├── services/backtest_market_history.py   backfill + persist daily history (MarketDataProfile)
├── services/backtest_bridge.py           positions → per-underlying books + AutocallableMarketDataSet
├── services/domains/backtest.py          pipeline: history → bridge → engines → aggregate → shape → artifacts
├── services/backtest_runner.py           async queue + execute (BacktestRun + TaskRun)
├── models.py                             BacktestRun + TaskRun.backtest_run_id + TaskKind.BACKTEST
├── alembic/versions/0027_*.py            create backtest_runs + add task_runs.backtest_run_id
├── tools/backtest.py                     run_backtest / get_backtest_run / list_backtest_runs
├── skills/workflows/backtest/SKILL.md    + skills/references/risk/backtest.md
└── main.py                               /api/backtests/* endpoints
open-otc-trading / frontend
├── routes/Backtest.tsx (+ .css, tests)   native recharts report + "Open full quant-ark dashboard"
└── registered in main.tsx, Sidebar.tsx, types.ts, api/client.ts
```

**Reuse boundary (mirrors scenario_test):** the heavy compute (daily replay, pricing, lifecycle, hedging) lives in quant-ark; oot does **assembly** (positions → products + market data), **orchestration** (async runner), **persistence**, and **presentation**. QuantArk modules are imported only after `quantark.ensure_quantark_path()` (see `services/scenario_test_bridge.py` for the established pattern).

**Data flow:** `portfolio/positions + market history → bridge → quant-ark book engine (per underlying) → per-underlying results → aggregate → results JSON + dashboard.html → persist (BacktestRun.results / .artifacts) → REST → React report`.

---

## 4. quant-ark Extension — `BookAutocallableBacktestEngine`

### 4.1 Motivation
The existing `AutocallableBacktestEngine` (`backtest/otc/engine.py`) is **single-product** (`config.product`, `config.product_quantity`, one `AutocallableLifecycleState`, one `FuturesHedgePosition`). The equity `BacktestEngine` (`backtest/equity/engine.py`) already does **multi-position net-delta** portfolio hedging but has **no autocallable lifecycle**. The desk needs **both**: per-underlying net-delta hedging **with** autocallable lifecycle. Neither engine provides this; we add a book engine that composes the existing primitives.

### 4.2 Refactor-first (low-risk)
Hoist the per-day helpers currently private to `AutocallableBacktestEngine` into reusable form so the book engine does not copy logic:
- `_build_env`, `_calculate_greeks`, `_rebalance`, `_roll_contract`, `_execute_futures_trade`, `_record_day` → extract into module-level functions or a shared mixin/base used by both engines.
- **Characterization anchor:** the existing single-product engine must produce **byte-identical** results before/after the refactor (snapshot test on the demo run). This is the strongest guard against regression.

### 4.3 Shape
```python
@dataclass
class BookProduct:
    product: Any            # QuantArk product (Snowball/Phoenix/Vanilla/Sharkfin/…)
    quantity: float         # signed (desk is typically short structured products)
    position_id: int        # oot position id, for lifecycle-event attribution
    has_lifecycle: bool     # True for autocallables (Snowball/Phoenix), else False

@dataclass
class HedgeSpec:
    kind: str               # "futures" | "spot"
    multiplier: float       # 1.0 for spot
    roll_policy: FuturesRollPolicy | None   # None for spot

@dataclass
class BookAutocallableBacktestConfig:
    products: list[BookProduct]
    market_data: AutocallableMarketDataSet
    hedge: HedgeSpec
    engine_config: AutocallableEngineConfig = ...
    strategy: Any = AutocallableDeltaHedgeStrategy()      # rebalance band
    transaction_cost_model: TransactionCostModel = ZeroCostModel()
    underlying: str = "equity_index"
    start_date / end_date / metadata
    calculate_event_probabilities: bool = True
    calculate_surfaces: bool = False                      # off by default (cost)

class BookAutocallableBacktestEngine:
    def __init__(self, config): ...   # one lifecycle state per has_lifecycle product
    def run(self) -> BookBacktestResults: ...
```

### 4.4 Daily loop (per underlying group)
For each trading day `t` in the window:
1. **Build env** from history(`t`): spot, vol (per `vol_source`), rate, basis-yield / implied-q (for futures hedge). Shared across products on this underlying; per-product time-to-maturity differs.
2. **Lifecycle** — for each alive product with `has_lifecycle`: apply KO / KI / autocall / coupon checks against the realized path; on a terminating event realize cashflows (attributed to `position_id`), mark the product dead, drop it from the active book.
3. **Price** every alive product → product MTM, position delta (`= product_delta × quantity`), gamma, vega, theta, rho.
4. **Aggregate** net book delta = Σ position deltas over alive products; likewise net gamma/vega for reporting (gamma/vega not hedged in v1).
5. **Hedge** the net delta with `HedgeSpec`:
   - `futures`: compute target contracts for net delta → rebalance only if outside the strategy band → roll if near expiry (`FuturesRollPolicy`) → book transaction cost.
   - `spot`: target spot units = −net delta → rebalance band → no roll/basis → book cost.
6. **Record day** — book MTM = Σ product MTM + hedge MTM + Σ realized cashflows − cumulative costs; pre/post-hedge net delta & gamma; per-product event-prob (opt-in); trades.

### 4.5 Results — `BookBacktestResults`
Same DataFrame surface as `AutocallableBacktestResults` (`states_df`, `greeks_df`, `trades_df`, `actions_df`, `daily_event_summary_df`, `event_probability_df`, `get_summary()`), with:
- States/greeks become **book-level** aggregates; per-product detail keyed by `position_id` in `event_probability_df` / a new `products_df`.
- `get_summary()` extends with `num_products`, `num_lifecycle_events`, hedge instrument, hedge P&L vs product P&L split.
- Exporters reused: `export_to_excel`, `export_surfaces_to_parquet`.

### 4.6 Dashboard
`AutocallableBacktestDashboard` is reused per underlying to emit `dashboard.html`. The book variant adds a small header table (products in the book + lifecycle events). oot generates one dashboard per underlying plus a lightweight combined index page that links them.

### 4.7 quant-ark tests (TDD, in quant-ark's harness)
- **Book-of-one == single-product** (byte-identical) — refactor anchor.
- Two offsetting products on one underlying → net delta ≈ 0 → near-zero hedge activity.
- A product knocks out mid-run → it leaves the book; subsequent net delta excludes it; cashflow recorded once.
- Mixed Snowball + vanilla call on one underlying → both priced, deltas netted, one hedge program.
- Spot-hedge mode (no futures chain) → hedges with spot, no roll, no basis term.
- **Use non-default input values** (the "real-value test" lesson — a test whose expected value equals the fallback masks bugs).

---

## 5. Market-History Backfill & Persist — `services/backtest_market_history.py`

### 5.1 Responsibility
Given `underlying symbols`, `hedge instruments`, and a `[start, end]` window, return continuous daily frames (`spot_data`, `futures_data`, `vol_data`, `rate_data`) ready for `AutocallableMarketDataSet.from_dataframes(...)`, **persisting any akshare-fetched data** so subsequent runs are cache-hits.

### 5.2 Continuity check
- Expected trading days come from quant-ark's **China SSE holiday calendar** (already maintained there) intersected with `[start, end]`.
- Look for a `MarketDataProfile` matching `symbol` + `asset_class` whose `data` daily series covers **all** expected days.
- **Gap policy (contribution point — see §16b):** the default proposal — if any expected trading day is missing, fetch the full window via akshare, merge with existing rows (akshare wins on overlap), and persist. A small number of isolated missing days *may* be forward-filled instead of refetched; the exact threshold is the user's call. Forward-fill beyond N consecutive days is an error (stale path).

### 5.3 Persistence
- **Spot:** reuse `MarketDataProfile(asset_class="index"|"equity", source="akshare", symbol, start_date, end_date, adjust, data={daily OHLCV})`. Backfill **extends** an existing profile's `data`/`start_date`/`end_date` rather than creating duplicates (idempotent — re-running the same window is a no-op).
- **Futures chain:** persist as `MarketDataProfile(asset_class="futures", symbol=<futures prefix, e.g. "IC">, data={chain rows})`. Chain rows normalize to quant-ark's `normalize_futures_chain` schema: `date, contract, futures_price, expiry_date, multiplier`.
- **vol_data / rate_data:** derived, not fetched. `rate_data` = flat rate from spec (default 0.02). `vol_data` = per `vol_source`: realized = annualized rolling std of log-returns over `vol_window` (default 20); flat = constant from profile/assumption.

### 5.4 Notes
- akshare is already a dependency; the `akshare-data` skill + `services/market_data.py` adapter exist. The fetch path reuses `services/market_data` helpers where possible; quant-ark's `AKShareAutocallableDataAdapter` is an alternative source for the futures chain and may be used directly inside the bridge once `ensure_quantark_path()` is called.
- Network failures during backfill → the run fails cleanly with a clear message (no partial-path silent success).

---

## 6. The Bridge — `services/backtest_bridge.py`

### 6.1 Responsibility
`build_books(positions, history, hedging_map, *, vol_source, rate, txn_cost, strategy, window) -> tuple[list[BookAutocallableBacktestConfig], list[dict]]`.

### 6.2 Steps
1. `quantark.ensure_quantark_path()`.
2. **Exclude** non-buildable positions up front, reusing risk's `quantark.risk_pricing_exclusion(position)` + zero-quantity guard + "no continuous history" guard. `excluded = [{"position_id", "reason"}]` (same contract as `scenario_test_bridge`).
3. **Group** remaining positions by `underlying`.
4. For each group:
   a. Map each position → QuantArk product via the **existing `build_product`** producer (unified-product-schema work) with **signed quantity** — no new product logic; the four booking channels already converge on `build_product`.
   b. Resolve the **hedge instrument** for the underlying via the hedging-universe map: futures mapping → `HedgeSpec(kind="futures", multiplier, roll_policy)`; otherwise → `HedgeSpec(kind="spot", multiplier=1.0)`.
   c. Assemble `AutocallableMarketDataSet.from_dataframes(spot_data, vol_data, rate_data, futures_data)` from §5 output (spot always; futures only for futures-hedged underlyings).
   d. Emit one `BookAutocallableBacktestConfig`.
5. Return `(configs, excluded)`.

### 6.3 Edge cases
- A group whose positions are all autocallables → standard otc path.
- A group mixing autocallables + vanillas → both in `products`, lifecycle only on autocallables.
- A spot-hedged underlying with `calculate_event_probabilities=True` but no autocallables → event-prob is simply empty for that group.
- Per-position market snapshot inconsistency does **not** apply here (the backtest sources history, not the per-position pricing snapshot scenario_test used) — there is no "first-seen env" limitation.

---

## 7. Data Model — `BacktestRun` + Alembic `0027`

### 7.1 `BacktestRun` (`models.py`)
```python
class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True)
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.QUEUED.value)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)        # window, engine, vol_source, rate, hedge knobs, txn cost
    config: Mapped[dict] = mapped_column(JSON, default=dict)      # export_formats, event-prob/surfaces toggles
    results: Mapped[dict] = mapped_column(JSON, default=dict)     # shaped aggregate (see §9.4)
    excluded_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)   # {dashboard_html, xlsx, csv_dir, parquet}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio = relationship()
    pricing_parameter_profile = relationship()
    task_runs = relationship(back_populates="backtest_run")
```

### 7.2 `TaskRun` + `TaskKind`
- Add `backtest_run_id: Mapped[int | None]` FK to `task_runs` + `backtest_run` relationship (mirror `scenario_test_run_id`).
- Add `TaskKind.BACKTEST = "backtest"`.

### 7.3 Migration `0027`
- Create `backtest_runs`; add `task_runs.backtest_run_id` column + index/FK.
- **Use migration-local Core tables**, never ORM models/services (migrations drift to future schema — the established rule that broke `0018`).
- Migrate the **live `data/open_otc.sqlite3`** (current head is `0026`); dry-run against the live DB before applying. The boot-time incremental-schema repair must recognize the new table/column.

---

## 8. Run Execution & Results Shaping

### 8.1 `services/backtest_runner.py` (clone of `scenario_test_runner.py`)
- `queue_backtest(session, *, portfolio_id, pricing_parameter_profile_id, spec, config, position_ids=None) -> (BacktestRun, TaskRun)`:
  - Validate portfolio exists; resolve/validate scoped `position_ids` via `_resolve_risk_positions` (typo/foreign id → `ValueError` → REST `400`).
  - **Validate the spec before persisting** — window non-empty & `start < end`; engine ∈ {quad, pde, mc}; vol_source ∈ {realized, flat}; window not absurdly long. Bad spec → synchronous `400`, never a queued run that dies in the worker.
  - Persist `BacktestRun(status=QUEUED, ...)` + `TaskRun(kind=BACKTEST, status=QUEUED, backtest_run_id=run.id)`; `record_audit("backtest.queued")`; commit; `submit_async_task(execute_backtest_task, task.id, run.id)`.
- `execute_backtest_task(task_id, run_id, session_factory=None)`: `database.init_db()`; open **own** session (never the request session); `_execute(...)`; `finally: session.close()`.
- `_execute(...)`: `mark_task_running`; `run.status = RUNNING`; resolve positions (the `resolved_position_ids is not None` empty-scope rule); choose **valuation/window** (profile-bound run may default the window end to the profile's valuation_date — explicit spec dates win); call `backtest_svc.run_pipeline(...)`; persist `results` + `artifacts`; `run.status = status`; `mark_task_finished`. Exception handler persists `FAILED` + `results={"error": str(exc)}` and **never crashes the worker**.

### 8.2 Progress
Backtests can be long (daily replay × engine). `_execute` sets `TaskRun.progress_total = len(underlying_groups)` and increments `progress_current` per finished group (optionally finer per-day inside a group). The frontend polls task progress (existing task-run polling).

### 8.3 `services/domains/backtest.py` — `run_pipeline(...)`
1. Resolve underlyings + hedge instruments for the positions.
2. `backtest_market_history.ensure_history(...)` → frames (backfilled + persisted).
3. `backtest_bridge.build_books(...)` → `(configs, excluded)`.
4. For each config: `BookAutocallableBacktestEngine(config).run()` (progress callback).
5. **Aggregate** per-underlying results → portfolio totals (sum P&L components; netting handled per-underlying already) + per-underlying breakdown.
6. **Shape** `results_dict` (JSON-safe; downsample time series — e.g. cap to ~250 points or daily, full detail stays in artifacts).
7. `write_artifacts(...)` → quant-ark `dashboard.html` per underlying + combined index, `xlsx`, `csv`, `parquet` under `settings.backtest_output_dir/{run_id}/`.
Returns `(status, results_dict, excluded, raw)`.

### 8.4 Results JSON shape (`BacktestRun.results`)
```jsonc
{
  "window": {"start": "...", "end": "...", "num_trading_days": 78},
  "engine": "quad", "vol_source": "realized:20",
  "portfolio": {
     "initial_value", "final_value", "total_pnl", "product_pnl", "hedge_pnl",
     "transaction_costs", "num_trades", "turnover",
     "sharpe", "max_drawdown", "var_95", "cvar_95",
     "pnl_series":[{"date","total_pnl","product_pnl","hedge_pnl","unhedged_pnl"}],
     "greeks_series":[{"date","net_delta_pre","net_delta_post","gamma","vega"}]
  },
  "by_underlying":[{
     "underlying", "hedge_instrument", "num_products", "total_pnl", "hedge_pnl", "num_trades",
     "lifecycle_events":[{"position_id","type","date","cashflow"}],
     "event_summary":{"ko_prob","ki_prob","survival_prob"},
     "pnl_series", "greeks_series", "trades"
  }],
  "excluded_positions":[{"position_id","reason"}],
  "artifacts":{"dashboard_html","xlsx","csv_dir","parquet"}
}
```

---

## 9. Exports & Report (Hybrid)

- **Native report** renders entirely from `BacktestRun.results` (no artifact fetch needed for the core views).
- **quant-ark dashboard:** `dashboard.html` per underlying + a combined index page, persisted under the run's artifact directory; served by `GET /api/backtests/runs/{id}/artifacts/{name}` (or a static mount). The frontend "Open full quant-ark dashboard" opens / iframes it.
- **xlsx / csv:** via `BookBacktestResults.export_to_excel` and per-frame CSVs.
- **parquet (surfaces):** `export_surfaces_to_parquet` is **guarded** — `pyarrow` is not in the uv `.venv` (known: the scenario_test project hit this). If `pyarrow` import fails, skip parquet and record `artifacts.parquet = null`; **never crash the run**. `export_formats` defaults to `["json", "xlsx", "html"]` (parquet opt-in).

---

## 10. Agent Tools — `tools/backtest.py`

Thin `@tool` adapters (capability-gated, `ToolGroup`), mirroring `tools/scenario_test.py`. All input models `extra="forbid"`.
- **`run_backtest`** — `portfolio_id:int`, `pricing_parameter_profile_id:int|None`, `position_ids:list[int]|None`, `start_date:str`, `end_date:str`, `engine:str="quad"`, `vol_source:str="realized"`, `vol_window:int=20`, `config:dict={}`. Calls `queue_backtest`; returns `{run_id, task_id, status}` (async — does not block).
- **`get_backtest_run`** — `run_id:int` → status + shaped results summary + artifact links.
- **`list_backtest_runs`** — `portfolio_id:int` → recent runs (id, status, window, total_pnl).
- Register new names in the **`DEEP_AGENT_TOOL_NAMES` allowlist** (the pricing-tools lesson — undeclared tools are silently unavailable).

---

## 11. Skill + Routing

### 11.1 Reference doc — `skills/references/risk/backtest.md`
Full theory + I/O reference (engines, hedge mechanics, lifecycle, metrics, the results shape, the dashboard). **Must have frontmatter** (`test_reference_docs` enforces it).

### 11.2 Routing & catalog coupling (the sharp edge)
Adding one workflow `SKILL.md` breaks exact-set + count assertions in **six** test files — update all atomically:
`test_skills_catalog`, `test_skills_catalog_v2`, `test_workflow_skills_phase3` (or `test_remaining_workflow_skills_phase3`), `test_reference_docs`, `test_routing_table` (`OLD_TABLE_ROWS` routing-triple pin). The orchestrator routing line is **data-driven**: add a `routing:` frontmatter block so `rebuild_orchestrator` injects the row (the frontend-skill-management mechanism — this is what structurally solves the old "catalog ≠ orchestrator knowledge" trap).

### 11.3 Full `SKILL.md` sketch (body ≤ 500 tokens)
```markdown
---
name: run-backtest
description: Historical hedging backtest of a portfolio's positions, netted per underlying.
routing:
  trigger: User wants to backtest / replay how a portfolio (snowballs, phoenixes,
    or equity options) would have been delta-hedged over a historical window.
  tool: run_backtest
---

## When to use
Use when the desk asks to **replay history** and see how booked positions would
have performed under daily delta-hedging — hedge P&L, greeks over time,
autocallable KO/KI/autocall/coupon events, transaction costs, and risk metrics.
Not for forward-looking scenario shocks (use run-scenario-test) or a single
as-of valuation (use pricing/risk).

## Required inputs
- portfolio_id (and optional position_ids to scope)
- start_date, end_date (the replay window)
- optional: pricing_parameter_profile_id, engine (quad|pde|mc),
  vol_source (realized|flat), vol_window

## Procedure
1. Confirm the portfolio + window with the user.
2. Call `run_backtest` (async — returns run_id + task_id).
3. Poll `get_backtest_run(run_id)` until status is completed/failed.
4. Summarize: total/hedge/product P&L, # trades, max drawdown, Sharpe, VaR95,
   and per-underlying lifecycle events; link the full quant-ark dashboard.

## Stop conditions
- Stop and report if the run fails (surface results.error).
- Stop if all positions were excluded (report excluded_positions reasons).

## Output shape
run_id, status, portfolio totals, by_underlying breakdown, artifact links.

## References
skills/references/risk/backtest.md

## Example
"Backtest portfolio 3 over Jan–Apr 2024 hedging with index futures" →
run_backtest(portfolio_id=3, start_date="2024-01-02", end_date="2024-04-30").
```

---

## 12. REST Endpoints (`main.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/backtests/runs` | Queue a run → `202 {run_id, task_id}` |
| GET | `/api/backtests/runs?portfolio_id=` | List runs for a portfolio |
| GET | `/api/backtests/runs/{id}` | Run detail (status + results) |
| GET | `/api/backtests/runs/{id}/artifacts/{name}` | Serve dashboard.html / xlsx / csv |
| DELETE | `/api/backtests/runs/{id}` | Delete a run + its artifacts |

Request validation errors (bad window/engine/scope) → `400` synchronously (from `queue_backtest`). Artifact route guards path traversal (serve only from the run's artifact dir).

---

## 13. Frontend — `routes/Backtest.tsx`

- **Registration:** `main.tsx` (route), `Sidebar.tsx` (Risk/Analytics group, next to Scenario Test), `types.ts` (types for `BacktestRun`, request/response), `api/client.ts` (client methods).
- **Layout** (see mockup): left configurator (portfolio select · positions scope · pricing profile · start/end date pickers · engine select · vol-source select · collapsible Advanced: rebalance band, transaction-cost model, futures roll days, surfaces toggle · **Run** button) + runs list. Main pane: selected-run report.
- **Native report (recharts):**
  - KPI cards: Total P&L · Hedge P&L · Product P&L · # Trades · Turnover · Max Drawdown · Sharpe · VaR₉₅.
  - Cumulative P&L line chart: total vs unhedged vs hedge.
  - Greeks chart: net delta pre/post hedge · gamma · vega.
  - **By-underlying** accordion: per underlying P&L, **lifecycle-event timeline** (KO / KI / autocall / coupon markers), event-probability summary, trades table.
  - "⤓ Open full quant-ark dashboard" → artifact route (new tab or iframe modal).
- **Polling:** reuse the existing task-run polling/progress UI to show RUNNING → COMPLETED with progress.
- **Styling:** strict **UI_STYLE_GUIDE token purity** — zero hardcoded hex/rgba; theme tokens only (the invariant from the frontend style-guide work). recharts colors come from CSS variables.
- **Tests:** `Backtest.test.tsx` (render/interaction with mocked client) + `Backtest.live.tsx`/`.live.test.tsx` (against a live backend), mirroring ScenarioTest.

---

## 14. Error Handling & Edge Cases

- **All positions excluded** → run completes with empty `by_underlying`, `status="completed"`, and a populated `excluded_positions`; UI shows the reasons. (Not a failure.)
- **No futures chain for a futures-mapped underlying** → backfill from akshare; if still unavailable, fall back to spot hedge **only if** the hedging map permits, else exclude the group with a clear reason.
- **akshare/network failure during backfill** → run `FAILED` with a clear message; nothing partially persisted is treated as success.
- **Window shorter than the vol window** → realized vol seeds from available days; if too few, error with guidance to widen the window or use flat vol.
- **Server restart mid-run** → stale-task recovery resets `TaskRun`, but a linked `BacktestRun` may remain `running` (same accepted v1 limitation as scenario_test; documented).
- **Profile-bound historical run** → window end defaults to the profile valuation date unless the spec sets explicit dates.
- **Idempotent backfill** → re-running the same window does not duplicate `MarketDataProfile` rows.

---

## 15. Testing Strategy (TDD)

**quant-ark** (in its own harness): see §4.7 (book-of-one anchor, net-delta offset, mid-run KO, mixed products, spot-hedge, non-default values).

**oot:**
- `backtest_market_history`: gap detection vs SSE calendar; backfill persists + is idempotent; vol/rate derivation (realized vs flat).
- `backtest_bridge`: grouping by underlying; product mapping via `build_product`; hedge-instrument resolution (futures vs spot fallback); exclusion policy + shape.
- `backtest_runner`: queue validation (`400` on bad spec/scope before persist); execute happy-path; failure path persists `FAILED` without crashing; empty-scope rule (`resolved_position_ids is not None`).
- `domains/backtest`: pipeline wiring; aggregation correctness; results-shape contract; downsampling.
- REST: each endpoint incl. artifact path-traversal guard.
- Migration `0027`: dry-run vs live `data/open_otc.sqlite3`; upgrade/downgrade.
- Skill/catalog: the **six** files + routing table + reference frontmatter; tool registration / `DEEP_AGENT_TOOL_NAMES`.
- `pyarrow`-absent path: parquet export skipped gracefully.
- frontend: `Backtest.test.tsx` (+ live variant).
- **Real-value tests:** characterization tests use non-default inputs so a value that equals the fallback can't mask a bug (lesson burned three times).

---

## 16. Where the User Shapes the Logic (learning-mode contribution points)

I'll scaffold these with context + a clear stub; you write ~5–10 lines each where the judgment is genuinely yours:

a. **Net-delta aggregation & hedge target** (quant-ark `book_engine.py`): how net delta is summed across heterogeneous products and converted to a hedge target — and whether net gamma/vega is merely reported or also influences the rebalance decision. Trade-off: pure delta-neutral simplicity vs gamma-aware bands.

b. **Market-history continuity policy** (`backtest_market_history.py`): what counts as "gappy" — the threshold of missing SSE trading days that triggers a full akshare refetch vs forward-fill, and the max consecutive forward-fill before erroring. Trade-off: fewer network fetches vs path staleness.

c. **Risk-metric block** (`domains/backtest.py`): Sharpe / max-drawdown / VaR₉₅ / CVaR₉₅ from the daily P&L series — annualization convention, drawdown on cumulative P&L vs on portfolio value, and historical vs parametric VaR. Trade-off: comparability vs fidelity to the desk's conventions.

---

## 17. Build Sequence

1. **quant-ark (own git worktree):** refactor single-product helpers → shared; add `BookAutocallableBacktestEngine` + `BookBacktestResults`; tests (book-of-one anchor first). Commit on a feature branch.
2. **oot:** `BacktestRun` model + `TaskKind.BACKTEST` + `TaskRun.backtest_run_id`; Alembic `0027`; migrate live DB.
3. `backtest_market_history.py` + tests.
4. `backtest_bridge.py` + tests.
5. `domains/backtest.py` (pipeline) + `backtest_runner.py` + tests.
6. Results shaping + `write_artifacts` (dashboard/xlsx; parquet guarded).
7. REST endpoints + tests.
8. `tools/backtest.py` + allowlist + tests.
9. Skill + reference doc + routing frontmatter + `rebuild_orchestrator` + the **six** catalog/routing files.
10. Frontend: types + api client + `Backtest.tsx` + sidebar + router + vitest.
11. End-to-end smoke: real akshare backfill on a small window (e.g. CSI500 Jan–Apr 2024), verify report + dashboard render.

---

## 18. Risks & Gotchas (carried from prior cycles)

- **Worktree isolation:** a concurrent agent churns shared HEAD/branches — do both quant-ark and oot work in **git worktrees**, not the main checkouts.
- **`python -c` import trap:** the venv `.pth` imports the **main** checkout, not the worktree — use `PYTHONPATH=<wt>/backend` or run via pytest.
- **`ensure_quantark_path()`** must be called before any QuantArk import (bridge + market-history when using `AKShareAutocallableDataAdapter`).
- **`git add -A` tracks the `node_modules` symlink** — use targeted adds.
- **Migrations:** Core tables only, dry-run vs live DB, boot incremental-schema repair must see `0027`.
- **`pyarrow` absent** from the uv `.venv` (no pip) — parquet must degrade gracefully.
- **Codex review** hangs on the codegraph MCP — disable it for any companion review run.
- **Skill catalog count** is pinned in six files + a routing-triple — bump together or the suite breaks.

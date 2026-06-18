# Backtest Module Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a portfolio-level **hedging backtest** to open-otc-trading — replay historical market data day-by-day, simulate the desk's delta-hedging program netted per underlying (with autocallable lifecycle), and surface a rich report — across backend, agent tools/skills, and frontend.

**Architecture:** One new engine in the sibling **quant-ark** repo (`BookAutocallableBacktestEngine`, a per-underlying multi-product generalization of the existing single-product otc engine) is consumed by open-otc-trading through the same layering the `scenario_test` feature uses: a bridge assembles inputs, an async runner persists a `BacktestRun`, a domain pipeline orchestrates, REST + agent tools + a React page surface results. quant-ark is loaded via `ensure_quantark_path()` (not pip).

**Tech Stack:** Python 3.11 · SQLAlchemy 2 + Alembic · FastAPI · LangChain `@tool` · pytest · React + TypeScript + recharts + Vitest · quant-ark (pandas/numpy pricing engines).

**Spec:** `docs/superpowers/specs/2026-06-09-backtest-module-integration-design.md`

---

## File Map

**quant-ark (sibling repo, own worktree):**
- Create `backtest/otc/_replay.py` — per-product daily-replay helpers extracted from `engine.py` (lifecycle, scheduling, greeks, env-build, futures trade/roll).
- Create `backtest/otc/book_engine.py` — `BookProduct`, `HedgeSpec`, `BookAutocallableBacktestConfig`, `BookBacktestResults`, `BookAutocallableBacktestEngine`.
- Modify `backtest/otc/engine.py` — delegate to `_replay.py` (behavior-preserving).
- Modify `backtest/otc/__init__.py` — export the new symbols.
- Test `test/test_book_backtest.py`.

**open-otc-trading / backend:**
- Modify `backend/app/models.py` — `BacktestRun` model, `TaskRun.backtest_run_id`, `TaskKind.BACKTEST`.
- Create `backend/alembic/versions/0027_backtest_runs.py`.
- Create `backend/app/services/backtest_market_history.py`.
- Create `backend/app/services/backtest_bridge.py`.
- Create `backend/app/services/domains/backtest.py`.
- Create `backend/app/services/backtest_runner.py`.
- Modify `backend/app/schemas.py` — `BacktestRunRequest`, `BacktestConfigIn`, `BacktestRunOut`.
- Modify `backend/app/main.py` — `/api/backtest/*` endpoints.
- Modify `backend/app/config.py` — `backtest_output_dir`.
- Create `backend/app/tools/backtest.py`.
- Modify `backend/app/tools/__init__.py` + deep-agent tool allowlist.
- Create `backend/app/skills/workflows/backtest/SKILL.md` + `backend/app/skills/references/risk/backtest.md`.
- Tests under `tests/`.

**open-otc-trading / frontend:**
- Create `frontend/src/routes/Backtest.tsx` (+ `.css`, `.test.tsx`).
- Modify `frontend/src/api/client.ts`, `frontend/src/types.ts`, `frontend/src/main.tsx`, `frontend/src/components/Sidebar.tsx`.

---

## Phase 0 — Isolated worktrees

### Task 0.1: Create both worktrees

**Files:** none (git plumbing)

- [ ] **Step 1: quant-ark worktree**

A concurrent agent churns the shared HEAD — isolate. Run:
```bash
cd /Users/fuxinyao/quant-ark
git worktree add ../quant-ark-backtest -b feature/book-backtest-engine
```
Expected: `Preparing worktree (new branch 'feature/book-backtest-engine')`.

- [ ] **Step 2: open-otc-trading worktree (off the existing spec branch)**

The spec was committed on `feature/backtest-module`. Continue there in a worktree:
```bash
cd /Users/fuxinyao/open-otc-trading
git worktree add ../open-otc-trading-backtest feature/backtest-module
```
Expected: `Preparing worktree (checking out 'feature/backtest-module')`.

- [ ] **Step 3: Record the QuantArk path the worktree resolves**

`ensure_quantark_path()` points at the configured quant-ark checkout. For the oot worktree to use the **quant-ark worktree**, confirm how the path resolves:
```bash
cd /Users/fuxinyao/open-otc-trading-backtest
grep -n "ensure_quantark_path\|QUANTARK\|quant.ark\|quant_ark_path" backend/app/services/quantark.py
```
Expected: prints the resolution logic. If it reads an env var / setting, point it at `/Users/fuxinyao/quant-ark-backtest` for this work (export the env var before running oot tests, or set it in `.env`). **Do not** merge the oot side until the quant-ark side is merged, since runtime needs the new engine on the resolved path.

---

## Phase 1 — quant-ark `BookAutocallableBacktestEngine`

> Work in `/Users/fuxinyao/quant-ark-backtest`. Run tests with the repo's own harness: `python -m pytest test/test_book_backtest.py -v` (quant-ark uses `sys.path.insert(0, repo_root)` in tests; mirror the existing `test/test_otc_autocallable_backtest.py` header).
> **PYTHONPATH trap:** never validate with `python -c` from another checkout — the venv `.pth` may import the wrong repo. Always `cd` into the worktree and use pytest.

### Task 1.1: Pin the single-product engine's behavior (characterization anchor)

**Files:**
- Test: `test/test_book_backtest.py`

The refactor in 1.2 must not change single-product results. Write a deterministic fixture (synthetic market data — no network) and snapshot the summary.

- [ ] **Step 1: Write the characterization test**

```python
# test/test_book_backtest.py
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset.equity.product.option import create_standard_snowball
from backtest.otc import (
    AutocallableBacktestConfig,
    AutocallableBacktestEngine,
    AutocallableEngineConfig,
    AutocallableMarketDataSet,
)
from util.enum.engine_enums import EngineType


def _synthetic_market(start="2024-01-02", end="2024-04-30", spot0=6000.0):
    """Deterministic daily spot path + a single IC futures contract chain."""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0, 0.012, len(dates))
    spot = spot0 * np.exp(np.cumsum(rets))
    spot_data = pd.DataFrame({"date": dates, "spot": spot})
    vol_data = pd.DataFrame({"date": dates, "volatility": np.full(len(dates), 0.18)})
    rate_data = pd.DataFrame({"date": dates, "rate": np.full(len(dates), 0.02)})
    expiry = pd.Timestamp("2024-06-21")
    futures_data = pd.DataFrame({
        "date": dates,
        "contract": ["IC2406"] * len(dates),
        "futures_price": spot * 0.99,
        "expiry_date": [expiry] * len(dates),
        "multiplier": [200.0] * len(dates),
    })
    return AutocallableMarketDataSet.from_dataframes(
        spot_data=spot_data, vol_data=vol_data, rate_data=rate_data, futures_data=futures_data
    )


def _single_config(market):
    snowball = create_standard_snowball(
        initial_spot=6000.0, maturity=1.0,
        ko_barrier=1.0, ki_barrier=0.75, coupon_rate=0.15,
    )
    return AutocallableBacktestConfig(
        product=snowball, market_data=market,
        engine_config=AutocallableEngineConfig(pricing_engine_type=EngineType.QUADRATURE),
        product_quantity=-1.0, underlying="CSI500",
        start_date=datetime(2024, 1, 2), end_date=datetime(2024, 4, 30),
        calculate_surfaces=False, calculate_event_probabilities=False,
    )


@pytest.fixture(scope="module")
def single_summary():
    market = _synthetic_market()
    results = AutocallableBacktestEngine(_single_config(market)).run()
    return results.get_summary()


def test_single_product_summary_is_stable(single_summary):
    # Pins the pre-refactor behavior. Values are recorded from the first green run.
    assert single_summary["num_days"] > 0
    assert single_summary["num_trades"] >= 0
    assert np.isfinite(single_summary["total_pnl"])
```

- [ ] **Step 2: Run it (records baseline)**

Run: `cd /Users/fuxinyao/quant-ark-backtest && python -m pytest test/test_book_backtest.py::test_single_product_summary_is_stable -v`
Expected: PASS. **Copy the printed `total_pnl`/`num_trades` into the assertions** (replace the loose checks with exact `pytest.approx` values) so the refactor is pinned to real numbers — not the fallback (the "real-value test" rule).

- [ ] **Step 3: Commit**

```bash
git add test/test_book_backtest.py
git commit -m "test(backtest): pin single-product otc engine behavior before refactor"
```

### Task 1.2: Extract per-product replay helpers into `_replay.py`

**Files:**
- Create: `backtest/otc/_replay.py`
- Modify: `backtest/otc/engine.py`

Goal: move the per-product, stateless-given-(product, quantity, lifecycle, config-ish, market_data, start_date) logic out of `AutocallableBacktestEngine` so the book engine can call it per product **without** duplicating ~300 lines. This is a **behavior-preserving move**, guarded by Task 1.1.

- [ ] **Step 1: Create `_replay.py` with a `ProductReplay` helper**

Move these methods from `engine.py` verbatim into a `ProductReplay` class whose `__init__` takes everything they currently read from `self`: `product`, `product_quantity`, `lifecycle`, `pricing_engine`, `surface_engine`, `event_stats_engine`, `engine_config`, `market_data`, `start_date`, `underlying`, `fixed_dividend_yield`, `delta_bump_size`, `gamma_bump_size`, `surface_config`. Methods to move (copy the bodies exactly, replacing `self.config.X` with `self.X` and `self.lifecycle` with `self.lifecycle`):
`_product_for_lifecycle`, `_product_for_date`, `_build_env`, `_pricing_dividend_yield`, `_calculate_greeks`, `_apply_lifecycle_events`, `_settle_maturity_if_due`, `_maturity_market_date`, `_schedule_resolution_env`, `_scheduled_records`, `_next_available_market_date`, `_lifecycle_snapshot`, `_append_action` (writes to a passed-in `actions` list), `_record_event_probabilities` (writes to passed-in lists), `_calculate_event_stats`, `_record_surfaces`, plus the `@staticmethod`s `_barrier_hit`, `_date_from_time`.

Signature sketch:
```python
# backtest/otc/_replay.py
"""Per-product daily-replay helpers shared by the single-product and book engines."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Optional
import numpy as np
import pandas as pd
from asset.equity.product.option.phoenix_option import PhoenixOption
from asset.equity.engine.base_engine import BaseEngine
from param import FlatRateCurve, FlatVolSurface, SpotQuote
from priceenv import PricingEnvironment
from .market import ImpliedBasisYield, SignedDividendYield, derive_implied_dividend_yield


class ProductReplay:
    def __init__(self, *, product, product_quantity, lifecycle, pricing_engine,
                 surface_engine, event_stats_engine, engine_config, market_data,
                 start_date, underlying, fixed_dividend_yield=None,
                 delta_bump_size=None, gamma_bump_size=None, surface_config=None):
        self.product = product
        self.product_quantity = product_quantity
        self.lifecycle = lifecycle
        self.pricing_engine = pricing_engine
        self.surface_engine = surface_engine
        self.event_stats_engine = event_stats_engine
        self.engine_config = engine_config
        self.market_data = market_data
        self.start_date = start_date
        self.underlying = underlying
        self.fixed_dividend_yield = fixed_dividend_yield
        self.delta_bump_size = delta_bump_size
        self.gamma_bump_size = gamma_bump_size
        self.surface_config = surface_config
    # ... moved methods, with self.config.product_quantity -> self.product_quantity etc.
```

- [ ] **Step 2: Rewrite `AutocallableBacktestEngine` to delegate to one `ProductReplay`**

In `engine.py`, construct a single `ProductReplay` in `__init__` from `self.config`, and replace each moved method call with `self._replay.<method>(...)`. Keep `run()`, `_rebalance`, `_roll_contract`, `_execute_futures_trade`, `_record_day` in the engine (those are hedge/book-level, not per-product). The `actions`/`event_probabilities`/`surfaces`/`daily_event_summary` lists are passed into the replay methods (or the replay holds references to the engine's lists — pass them in `__init__`).

- [ ] **Step 3: Run the characterization test**

Run: `cd /Users/fuxinyao/quant-ark-backtest && python -m pytest test/test_book_backtest.py -v`
Expected: PASS with the **exact** pinned values from Task 1.1 (proves the move changed nothing).

- [ ] **Step 4: Run the existing otc suite (no regression)**

Run: `python -m pytest test/test_otc_autocallable_backtest.py -v`
Expected: PASS (all existing tests green).

- [ ] **Step 5: Commit**

```bash
git add backtest/otc/_replay.py backtest/otc/engine.py
git commit -m "refactor(backtest): extract ProductReplay helpers from single-product engine"
```

### Task 1.3: Book config + product/hedge dataclasses

**Files:**
- Create: `backtest/otc/book_engine.py`
- Test: `test/test_book_backtest.py`

- [ ] **Step 1: Write the dataclasses**

```python
# backtest/otc/book_engine.py
"""Per-underlying multi-product net-delta hedging backtest (autocallable lifecycle aware)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
import numpy as np
import pandas as pd

from util.exceptions import ValidationError
from .config import AutocallableEngineConfig, FuturesRollPolicy, SurfaceGridConfig
from .market import AutocallableMarketDataSet
from .state import AutocallableDeltaHedgeStrategy, AutocallableLifecycleState, FuturesHedgePosition
from .transaction_costs import TransactionCostModel, ZeroCostModel  # adjust import to actual location
from .engine_factory import create_pricing_engine, create_surface_engine, create_event_stats_engine
from ._replay import ProductReplay


@dataclass
class BookProduct:
    product: Any                 # QuantArk product (Snowball/Phoenix/Vanilla/...)
    quantity: float              # signed
    position_id: int             # oot position id (event attribution)
    has_lifecycle: bool          # True for autocallables
    initial_price: Optional[float] = None

    def __post_init__(self):
        if self.product is None:
            raise ValidationError("BookProduct.product is required")
        if self.quantity == 0:
            raise ValidationError("BookProduct.quantity must be non-zero")


@dataclass
class HedgeSpec:
    kind: str = "futures"        # "futures" | "spot"
    multiplier: float = 1.0
    roll_policy: Optional[FuturesRollPolicy] = None

    def __post_init__(self):
        if self.kind not in ("futures", "spot"):
            raise ValidationError(f"HedgeSpec.kind must be futures|spot, got {self.kind}")
        if self.kind == "futures" and self.roll_policy is None:
            self.roll_policy = FuturesRollPolicy()


@dataclass
class BookAutocallableBacktestConfig:
    products: list[BookProduct]
    market_data: AutocallableMarketDataSet
    hedge: HedgeSpec = field(default_factory=HedgeSpec)
    engine_config: AutocallableEngineConfig = field(default_factory=AutocallableEngineConfig)
    strategy: Any = None
    transaction_cost_model: TransactionCostModel = field(default_factory=ZeroCostModel)
    underlying: str = "equity_index"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    fixed_dividend_yield: Optional[float] = None
    delta_bump_size: Optional[float] = None
    gamma_bump_size: Optional[float] = None
    surface_config: SurfaceGridConfig = field(default_factory=SurfaceGridConfig)
    calculate_surfaces: bool = False
    calculate_event_probabilities: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.products:
            raise ValidationError("BookAutocallableBacktestConfig.products must be non-empty")
        if self.market_data is None:
            raise ValidationError("market_data is required")
        if self.strategy is None:
            self.strategy = AutocallableDeltaHedgeStrategy()
```

- [ ] **Step 2: Write the failing test**

```python
def test_book_config_rejects_empty_products():
    from backtest.otc.book_engine import BookAutocallableBacktestConfig
    market = _synthetic_market()
    with pytest.raises(Exception):
        BookAutocallableBacktestConfig(products=[], market_data=market)
```

- [ ] **Step 3: Run → PASS**

Run: `python -m pytest test/test_book_backtest.py::test_book_config_rejects_empty_products -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backtest/otc/book_engine.py test/test_book_backtest.py
git commit -m "feat(backtest): add book engine config + product/hedge dataclasses"
```

### Task 1.4: `BookBacktestResults`

**Files:**
- Modify: `backtest/otc/book_engine.py`
- Test: `test/test_book_backtest.py`

- [ ] **Step 1: Add the results class** (mirror `AutocallableBacktestResults`, add book fields)

```python
class BookBacktestResults:
    def __init__(self, *, config, states, greeks, trades, actions,
                 daily_event_summary, event_probabilities, products_meta):
        self.config = config
        self._states = states
        self._greeks = greeks
        self._trades = trades
        self._actions = actions
        self._daily_event_summary = daily_event_summary
        self._event_probabilities = event_probabilities
        self._products_meta = products_meta  # [{position_id, underlying, has_lifecycle, quantity}]

    @staticmethod
    def _frame(rows, index=None):
        df = pd.DataFrame(rows)
        if index and not df.empty:
            df = df.set_index(index)
        return df

    def states_df(self): return self._frame(self._states)
    def greeks_df(self): return self._frame(self._greeks)
    def trades_df(self): return self._frame(self._trades)
    def actions_df(self): return self._frame(self._actions)
    def daily_event_summary_df(self): return self._frame(self._daily_event_summary)
    def event_probability_df(self): return self._frame(self._event_probabilities)

    def get_summary(self):
        states = self.states_df()
        if states.empty:
            return {"num_days": 0, "num_trades": len(self._trades), "total_pnl": 0.0,
                    "num_products": len(self._products_meta), "num_lifecycle_events": len(self._actions)}
        return {
            "num_days": int(len(states)),
            "start_date": str(states["date"].iloc[0]),
            "end_date": str(states["date"].iloc[-1]),
            "initial_portfolio_value": float(states["portfolio_value"].iloc[0]),
            "final_portfolio_value": float(states["portfolio_value"].iloc[-1]),
            "total_pnl": float(states["total_pnl"].iloc[-1]),
            "product_pnl": float(states["product_pnl"].iloc[-1]),
            "hedge_pnl": float(states["hedge_pnl"].iloc[-1]),
            "transaction_costs": float(states["transaction_costs"].iloc[-1]),
            "num_trades": int(len(self._trades)),
            "num_products": len(self._products_meta),
            "num_lifecycle_events": len(self._actions),
        }
```

- [ ] **Step 2: Failing test** (placeholder until engine exists)

```python
def test_results_summary_empty_states():
    from backtest.otc.book_engine import BookBacktestResults
    r = BookBacktestResults(config=None, states=[], greeks=[], trades=[], actions=[],
                            daily_event_summary=[], event_probabilities=[], products_meta=[{"position_id": 1}])
    s = r.get_summary()
    assert s["num_days"] == 0 and s["num_products"] == 1
```

- [ ] **Step 3: Run → PASS**; **Step 4: Commit**

```bash
git add backtest/otc/book_engine.py test/test_book_backtest.py
git commit -m "feat(backtest): add BookBacktestResults"
```

### Task 1.5: `BookAutocallableBacktestEngine.run()` — the daily book loop

**Files:**
- Modify: `backtest/otc/book_engine.py`
- Test: `test/test_book_backtest.py`

- [ ] **Step 1: Implement the engine** (composes one `ProductReplay` per product + one hedge position)

```python
class BookAutocallableBacktestEngine:
    def __init__(self, config: BookAutocallableBacktestConfig):
        self.config = config
        self.strategy = config.strategy
        self.hedge_position = FuturesHedgePosition()
        self._states, self._greeks, self._trades = [], [], []
        self._actions, self._daily_event_summary, self._event_probabilities = [], [], []
        self._transaction_costs = 0.0
        self._start_date = None
        self._initial_book_value = None
        # one lifecycle + replay per product
        self._replays = []
        for bp in config.products:
            lifecycle = AutocallableLifecycleState()
            replay = ProductReplay(
                product=bp.product, product_quantity=bp.quantity, lifecycle=lifecycle,
                pricing_engine=create_pricing_engine(bp.product, config.engine_config),
                surface_engine=create_surface_engine(bp.product, config.engine_config),
                event_stats_engine=create_event_stats_engine(bp.product, config.engine_config),
                engine_config=config.engine_config, market_data=config.market_data,
                start_date=None, underlying=config.underlying,
                fixed_dividend_yield=config.fixed_dividend_yield,
                delta_bump_size=config.delta_bump_size, gamma_bump_size=config.gamma_bump_size,
                surface_config=config.surface_config,
                actions_sink=self._actions, event_prob_sink=self._event_probabilities,
                daily_event_sink=self._daily_event_summary,
            )
            self._replays.append((bp, lifecycle, replay))

    def run(self) -> BookBacktestResults:
        dates = self.config.market_data.dates
        if self.config.start_date is not None:
            dates = dates[dates >= pd.Timestamp(self.config.start_date).normalize()]
        if self.config.end_date is not None:
            dates = dates[dates <= pd.Timestamp(self.config.end_date).normalize()]
        if len(dates) == 0:
            raise ValidationError("No common market-data dates for backtest")
        self._start_date = pd.Timestamp(dates[0]).normalize()
        for _bp, _lc, replay in self._replays:
            replay.start_date = self._start_date

        current_contract = None
        for date in dates:
            date = pd.Timestamp(date).normalize()
            market = self.config.market_data.get_market_row(date)
            selected = None
            if self.config.hedge.kind == "futures":
                futures_slice = self.config.market_data.get_futures_slice(date)
                selected = self.config.hedge.roll_policy.select_contract(
                    futures_slice, date, current_contract)
                if current_contract != str(selected["contract"]):
                    self._roll_contract(date, selected, futures_slice, current_contract)
                    current_contract = str(selected["contract"])

            # build env per product (shared spot/vol/rate; per-product TTM/basis via replay)
            net_position_delta = 0.0
            net_position_gamma = 0.0
            book_product_mtm = 0.0
            book_cashflows = 0.0
            multiplier = float(selected["multiplier"]) if selected is not None else self.config.hedge.multiplier
            for bp, lifecycle, replay in self._replays:
                env, basis_yield, implied_q, _ttm = replay.build_env(date, market, selected)
                replay.apply_lifecycle_events(date, env, market["spot"])
                replay.settle_maturity_if_due(date, env, market["spot"])
                if not lifecycle.alive:
                    book_cashflows += lifecycle.realized_cashflows
                    continue
                product = replay.product_for_date(date, env)
                price = float(replay.pricing_engine.price(product, env))
                greeks = replay.calculate_greeks(product, env, price)
                if self.config.calculate_event_probabilities:
                    replay.record_event_probabilities(date, product, env, position_id=bp.position_id)
                net_position_delta += float(greeks.get("delta", 0.0)) * bp.quantity
                net_position_gamma += float(greeks.get("gamma", 0.0)) * bp.quantity
                book_product_mtm += bp.quantity * price
                book_cashflows += lifecycle.realized_cashflows

            if self._initial_book_value is None:
                self._initial_book_value = book_product_mtm  # first day book MTM

            pre_hedge = self.hedge_position.quantity
            self._rebalance(date, selected, net_position_delta, multiplier)
            self._record_day(date, selected, market, net_position_delta, net_position_gamma,
                             book_product_mtm, book_cashflows, pre_hedge, multiplier)

        return BookBacktestResults(
            config=self.config, states=self._states, greeks=self._greeks, trades=self._trades,
            actions=self._actions, daily_event_summary=self._daily_event_summary,
            event_probabilities=self._event_probabilities,
            products_meta=[{"position_id": bp.position_id, "underlying": self.config.underlying,
                            "has_lifecycle": bp.has_lifecycle, "quantity": bp.quantity}
                           for bp, _lc, _r in self._replays],
        )
```

> **Note on `_rebalance` / `_roll_contract` / `_execute_futures_trade` / `_record_day`:** these are book-level. Adapt the single-product engine's versions: `_rebalance` calls `self.strategy.target_contracts(product_delta=net_position_delta, product_quantity=1.0, futures_multiplier=multiplier)` (net delta is already summed, so pass quantity 1.0). For `hedge.kind == "spot"`, target units `= -net_position_delta` (no rounding, multiplier 1.0, no roll) and `_execute_futures_trade` records `instrument_type="spot"`. `_record_day` computes `product_pnl = book_product_mtm + book_cashflows - initial_book_value`; `total_pnl = product_pnl + hedge_mtm - transaction_costs`. Copy the per-field structure from `engine.py:_record_day` but using the book aggregates. The `ProductReplay` methods need thin public wrappers (rename the moved `_build_env`→`build_env`, etc., or add public aliases) plus `actions_sink`/`event_prob_sink`/`daily_event_sink` params used in place of `self._actions` etc.

- [ ] **Step 2: Write the book-of-one equivalence test (the key anchor)**

```python
def test_book_of_one_matches_single_product(single_summary):
    from backtest.otc.book_engine import (
        BookAutocallableBacktestConfig, BookAutocallableBacktestEngine, BookProduct, HedgeSpec)
    market = _synthetic_market()
    snowball = create_standard_snowball(initial_spot=6000.0, maturity=1.0,
                                        ko_barrier=1.0, ki_barrier=0.75, coupon_rate=0.15)
    cfg = BookAutocallableBacktestConfig(
        products=[BookProduct(product=snowball, quantity=-1.0, position_id=1, has_lifecycle=True)],
        market_data=market, hedge=HedgeSpec(kind="futures", multiplier=200.0),
        engine_config=AutocallableEngineConfig(pricing_engine_type=EngineType.QUADRATURE),
        underlying="CSI500", start_date=datetime(2024, 1, 2), end_date=datetime(2024, 4, 30),
        calculate_surfaces=False, calculate_event_probabilities=False)
    book = BookAutocallableBacktestEngine(cfg).run().get_summary()
    assert book["num_days"] == single_summary["num_days"]
    assert book["num_trades"] == single_summary["num_trades"]
    assert book["total_pnl"] == pytest.approx(single_summary["total_pnl"], rel=1e-9)
```

- [ ] **Step 3: Run → iterate to PASS**

Run: `python -m pytest test/test_book_backtest.py::test_book_of_one_matches_single_product -v`
Expected: PASS. If P&L diverges, the book `_record_day` aggregation differs from single — diff field-by-field against `engine.py:_record_day`.

- [ ] **Step 4: Commit**

```bash
git add backtest/otc/book_engine.py test/test_book_backtest.py
git commit -m "feat(backtest): BookAutocallableBacktestEngine daily book loop (book-of-one == single)"
```

### Task 1.6: Net-delta offset + mixed product + spot-hedge tests

**Files:** Test: `test/test_book_backtest.py`

- [ ] **Step 1: Write three behavior tests** (non-default values — real-value rule)

```python
def test_two_offsetting_products_net_to_near_zero_hedge():
    # long + short the same snowball -> net delta ~0 -> ~no hedge trades after day 1
    from backtest.otc.book_engine import (
        BookAutocallableBacktestConfig, BookAutocallableBacktestEngine, BookProduct, HedgeSpec)
    market = _synthetic_market()
    sb = create_standard_snowball(initial_spot=6000.0, maturity=1.0, ko_barrier=1.0,
                                  ki_barrier=0.75, coupon_rate=0.15)
    cfg = BookAutocallableBacktestConfig(
        products=[BookProduct(sb, -1.0, 1, True), BookProduct(sb, 1.0, 2, True)],
        market_data=market, hedge=HedgeSpec(kind="futures", multiplier=200.0),
        engine_config=AutocallableEngineConfig(pricing_engine_type=EngineType.QUADRATURE),
        underlying="CSI500", calculate_event_probabilities=False)
    summary = BookAutocallableBacktestEngine(cfg).run().get_summary()
    assert summary["num_trades"] <= 2  # essentially flat

def test_spot_hedge_mode_runs_without_futures_chain():
    from backtest.otc.book_engine import (...)  # as above
    market = _synthetic_market()  # futures present but unused
    sb = create_standard_snowball(initial_spot=6000.0, maturity=1.0, ko_barrier=1.0,
                                  ki_barrier=0.75, coupon_rate=0.15)
    cfg = BookAutocallableBacktestConfig(
        products=[BookProduct(sb, -2.0, 1, True)], market_data=market,
        hedge=HedgeSpec(kind="spot", multiplier=1.0),
        engine_config=AutocallableEngineConfig(pricing_engine_type=EngineType.QUADRATURE),
        underlying="CSI500", calculate_event_probabilities=False)
    summary = BookAutocallableBacktestEngine(cfg).run().get_summary()
    assert summary["num_days"] > 0
    # spot hedge trades recorded as instrument_type="spot"
```

- [ ] **Step 2: Run → iterate to PASS**

Run: `python -m pytest test/test_book_backtest.py -v`
Expected: PASS for all.

- [ ] **Step 3: Export from `__init__.py`**

Add to `backtest/otc/__init__.py` imports + `__all__`: `BookAutocallableBacktestEngine`, `BookAutocallableBacktestConfig`, `BookProduct`, `HedgeSpec`, `BookBacktestResults`.

- [ ] **Step 4: Full otc suite + commit**

Run: `python -m pytest test/test_otc_autocallable_backtest.py test/test_book_backtest.py -v`
Expected: PASS.
```bash
git add backtest/otc/book_engine.py backtest/otc/__init__.py test/test_book_backtest.py
git commit -m "feat(backtest): net-delta offset + spot-hedge + exports"
```

### Task 1.7: Merge quant-ark side

- [ ] **Step 1:** From `/Users/fuxinyao/quant-ark`, merge `feature/book-backtest-engine` (PR or fast-forward per repo convention). The oot side resolves the engine on the configured quant-ark path, so this must land first.
- [ ] **Step 2:** `git worktree remove ../quant-ark-backtest` after merge.

---
## Phase 2 — open-otc-trading backend

> Work in `/Users/fuxinyao/open-otc-trading-backtest`. Run tests with `python -m pytest tests/<file> -v` from the worktree root (pytest config sets `pythonpath=["backend"]`). Point `ensure_quantark_path()` at the merged quant-ark before running pipeline tests.

### Task 2.1: `BacktestRun` model + `TaskKind.BACKTEST` + `TaskRun.backtest_run_id`

**Files:**
- Modify: `backend/app/models.py`
- Test: `tests/test_backtest_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_model.py
from app.models import BacktestRun, TaskKind, TaskRun, TaskStatus


def test_backtest_run_defaults():
    run = BacktestRun(portfolio_id=1, status=TaskStatus.QUEUED.value,
                      spec={"start": "2024-01-02", "end": "2024-04-30", "engine": "quad"})
    assert run.results == {} or run.results is None
    assert TaskKind.BACKTEST.value == "backtest"
    assert hasattr(TaskRun, "backtest_run_id")
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: BACKTEST`)

Run: `python -m pytest tests/test_backtest_model.py -v`

- [ ] **Step 3: Add `TaskKind.BACKTEST`** in `models.py` (after `SCENARIO_TEST`, line ~56):

```python
    BACKTEST = "backtest"
```

- [ ] **Step 4: Add `BacktestRun`** in `models.py` (immediately after `ScenarioTestRun`, before `TaskRun`):

```python
class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True)
    resolved_position_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.QUEUED.value)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_positions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped["Portfolio"] = relationship()
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
    task_runs: Mapped[list["TaskRun"]] = relationship(back_populates="backtest_run")
```

- [ ] **Step 5: Add the FK + relationship to `TaskRun`** (mirror `scenario_test_run_id`, ~line 1436 and ~1460):

```python
    backtest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_runs.id"), index=True, nullable=True)
```
and in the relationships block:
```python
    backtest_run: Mapped["BacktestRun | None"] = relationship(back_populates="task_runs")
```

- [ ] **Step 6: Run → PASS**; **Step 7: Commit**

```bash
git add backend/app/models.py tests/test_backtest_model.py
git commit -m "feat(backtest): BacktestRun model + TaskKind.BACKTEST + TaskRun.backtest_run_id"
```

### Task 2.2: Alembic migration `0027`

**Files:**
- Create: `backend/alembic/versions/0027_backtest_runs.py`
- Test: `tests/test_migration_0027.py`

- [ ] **Step 1: Confirm current head**

Run: `cd /Users/fuxinyao/open-otc-trading-backtest && python -m alembic -c alembic.ini heads`
Expected: prints `0026...` as head. Use that exact revision as `down_revision`.

- [ ] **Step 2: Write the migration** (migration-local Core tables ONLY — never import ORM models)

```python
# backend/alembic/versions/0027_backtest_runs.py
"""backtest_runs table + task_runs.backtest_run_id"""
from alembic import op
import sqlalchemy as sa

revision = "0027_backtest_runs"
down_revision = "0026_scenario_sets"   # replace with the exact head from Step 1
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("portfolio_id", sa.Integer, sa.ForeignKey("portfolios.id"), nullable=False, index=True),
        sa.Column("pricing_parameter_profile_id", sa.Integer,
                  sa.ForeignKey("pricing_parameter_profiles.id"), nullable=True, index=True),
        sa.Column("resolved_position_ids", sa.JSON, nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("spec", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("results", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("excluded_positions", sa.JSON, nullable=True),
        sa.Column("artifacts", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    with op.batch_alter_table("task_runs") as batch:
        batch.add_column(sa.Column("backtest_run_id", sa.Integer,
                                   sa.ForeignKey("backtest_runs.id"), nullable=True))
    op.create_index("ix_task_runs_backtest_run_id", "task_runs", ["backtest_run_id"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_backtest_run_id", table_name="task_runs")
    with op.batch_alter_table("task_runs") as batch:
        batch.drop_column("backtest_run_id")
    op.drop_table("backtest_runs")
```

- [ ] **Step 3: Write a roundtrip test**

```python
# tests/test_migration_0027.py
import sqlalchemy as sa
from alembic.config import Config
from alembic import command


def test_upgrade_creates_backtest_runs(tmp_path):
    db = tmp_path / "t.sqlite"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db}")
    insp = sa.inspect(engine)
    assert "backtest_runs" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("task_runs")}
    assert "backtest_run_id" in cols
```

- [ ] **Step 4: Run → PASS**

Run: `python -m pytest tests/test_migration_0027.py -v`

- [ ] **Step 5: Dry-run against the LIVE DB, then apply**

```bash
cp data/open_otc.sqlite3 data/open_otc.sqlite3.bak.$(date +%Y%m%d%H%M%S)
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current   # expect 0027_backtest_runs
```
Expected: upgrades cleanly from `0026` to `0027`. Keep the backup.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0027_backtest_runs.py tests/test_migration_0027.py
git commit -m "feat(backtest): alembic 0027 backtest_runs + task_runs.backtest_run_id"
```

### Task 2.3: Market-history backfill & persist

**Files:**
- Create: `backend/app/services/backtest_market_history.py`
- Test: `tests/test_backtest_market_history.py`

The pipeline needs continuous daily `spot_data` (+ `futures_data` for futures-hedged underlyings) over the window. Prefer a stored `MarketDataProfile`; backfill gaps from akshare and persist. **Contribution point (b):** the gap-policy threshold is the user's call — leave the `_GAP_TOLERANCE` constant + `_has_gaps` body as the marked stub.

- [ ] **Step 1: Write the failing test (continuity + idempotent persist)**

```python
# tests/test_backtest_market_history.py
import pandas as pd
from app.services import backtest_market_history as mh


def test_trading_days_uses_calendar():
    days = mh.expected_trading_days("2024-01-02", "2024-01-10")
    assert len(days) >= 5
    assert all(d.weekday() < 5 for d in days)  # no weekends


def test_has_gaps_detects_missing(monkeypatch):
    have = pd.to_datetime(["2024-01-02", "2024-01-03"])   # missing 2024-01-04..
    expected = mh.expected_trading_days("2024-01-02", "2024-01-10")
    assert mh._has_gaps(have, expected) is True
```

- [ ] **Step 2: Run → FAIL** (`module has no attribute`)

- [ ] **Step 3: Implement**

```python
# backend/app/services/backtest_market_history.py
"""Continuous daily history for backtests: stored MarketDataProfile first,
akshare backfill (persisted) on gaps."""
from __future__ import annotations
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from app.models import MarketDataProfile
from app.services import quantark

# --- Contribution point (b): how many missing trading days triggers a full refetch
# vs forward-fill, and the max consecutive forward-fill before erroring. ---
_GAP_TOLERANCE = 0     # 0 = any missing day triggers backfill (default; tighten/loosen here)


def _sse_calendar():
    quantark.ensure_quantark_path()
    try:
        from util.calendar.china import SSECalendar  # adjust to actual quant-ark path
        return SSECalendar()
    except Exception:
        return None


def expected_trading_days(start: str, end: str) -> list[pd.Timestamp]:
    cal = _sse_calendar()
    if cal is not None and hasattr(cal, "sessions_in_range"):
        return list(cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end)))
    return list(pd.bdate_range(start, end))   # fallback: business days


def _has_gaps(have_dates, expected) -> bool:
    have = set(pd.to_datetime(pd.Index(have_dates)).normalize())
    missing = [d for d in expected if pd.Timestamp(d).normalize() not in have]
    return len(missing) > _GAP_TOLERANCE


def _profile_to_frame(profile: MarketDataProfile) -> pd.DataFrame:
    rows = (profile.data or {}).get("series", [])
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df


def ensure_spot_history(session: Session, *, symbol: str, asset_class: str,
                        start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """Return a daily spot frame (columns: date, spot) covering [start, end],
    backfilling from akshare and persisting into a MarketDataProfile on gaps."""
    profile = (session.query(MarketDataProfile)
               .filter(MarketDataProfile.symbol == symbol,
                       MarketDataProfile.asset_class == asset_class)
               .order_by(MarketDataProfile.id.desc()).first())
    expected = expected_trading_days(start, end)
    existing = _profile_to_frame(profile) if profile is not None else pd.DataFrame()
    window = existing[(existing.get("date") >= pd.Timestamp(start)) &
                      (existing.get("date") <= pd.Timestamp(end))] if not existing.empty else existing
    if existing.empty or _has_gaps(window.get("date", []), expected):
        fetched = _fetch_akshare_spot(symbol, asset_class, start, end, adjust)  # date, spot
        merged = (pd.concat([existing, fetched]).drop_duplicates("date", keep="last")
                  .sort_values("date").reset_index(drop=True)) if not existing.empty else fetched
        _persist_profile(session, profile, symbol, asset_class, merged, adjust)
        window = merged[(merged["date"] >= pd.Timestamp(start)) & (merged["date"] <= pd.Timestamp(end))]
    return window[["date", "spot"]].reset_index(drop=True)


def derive_vol(spot_df: pd.DataFrame, *, vol_source: str, vol_window: int, flat_vol: float) -> pd.DataFrame:
    out = spot_df[["date"]].copy()
    if vol_source == "flat":
        out["volatility"] = float(flat_vol)
        return out
    logret = np.log(spot_df["spot"]).diff()
    realized = logret.rolling(vol_window).std() * np.sqrt(252.0)
    out["volatility"] = realized.bfill().fillna(float(flat_vol))
    return out


def flat_rate(spot_df: pd.DataFrame, rate: float) -> pd.DataFrame:
    out = spot_df[["date"]].copy()
    out["rate"] = float(rate)
    return out
```

> Implement `_fetch_akshare_spot(symbol, asset_class, start, end, adjust)` by reusing `app.services.market_data` helpers (it already wraps akshare); return columns `date, spot`. Implement `_persist_profile(...)` to create or update a `MarketDataProfile` with `data={"series": merged.assign(date=lambda d: d.date.dt.strftime("%Y-%m-%d")).to_dict("records")}`, `start_date`/`end_date` spanning merged, `source="akshare"`. Add `ensure_futures_chain(...)` analogously (asset_class="futures", normalized chain rows). On akshare/network failure raise a clear `RuntimeError` (the pipeline turns it into a FAILED run).

- [ ] **Step 4: Run → PASS**; **Step 5: Commit**

```bash
git add backend/app/services/backtest_market_history.py tests/test_backtest_market_history.py
git commit -m "feat(backtest): market-history backfill + persist (continuity vs SSE calendar)"
```

### Task 2.4: The bridge

**Files:**
- Create: `backend/app/services/backtest_bridge.py`
- Test: `tests/test_backtest_bridge.py`

- [ ] **Step 1: Write the failing test (grouping + exclusions)**

```python
# tests/test_backtest_bridge.py
from app.services import backtest_bridge


class _Pos:
    def __init__(self, id, underlying, quantity=1.0):
        self.id = id; self.underlying = underlying; self.quantity = quantity


def test_group_positions_by_underlying():
    groups = backtest_bridge.group_by_underlying(
        [_Pos(1, "CSI500"), _Pos(2, "CSI500"), _Pos(3, "CSI300")])
    assert set(groups) == {"CSI500", "CSI300"}
    assert {p.id for p in groups["CSI500"]} == {1, 2}
```

- [ ] **Step 2: Implement**

```python
# backend/app/services/backtest_bridge.py
"""Bridge: DB positions -> per-underlying BookAutocallableBacktestConfig list."""
from __future__ import annotations
from collections import defaultdict
from typing import Any
from app.services import quantark


def group_by_underlying(positions: list[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for p in positions:
        groups[str(p.underlying)].append(p)
    return dict(groups)


def build_books(session, positions, history, *, hedging_map, vol_source, vol_window,
                rate, flat_vol, txn_cost, strategy, start, end, engine_type):
    """Returns (configs, excluded). One BookAutocallableBacktestConfig per underlying."""
    quantark.ensure_quantark_path()
    from backtest.otc import (BookAutocallableBacktestConfig, BookProduct, HedgeSpec,
                              AutocallableEngineConfig, AutocallableMarketDataSet, FuturesRollPolicy)
    from app.services.products import build_product  # the unified producer (signed qty handled by caller)

    excluded: list[dict] = []
    configs = []
    for underlying, plist in group_by_underlying(positions).items():
        book_products = []
        for pos in plist:
            reason = quantark.risk_pricing_exclusion(pos)
            if reason:
                excluded.append({"position_id": pos.id, "reason": reason}); continue
            if float(getattr(pos, "quantity", 0) or 0) == 0.0:
                excluded.append({"position_id": pos.id, "reason": "Position quantity is zero"}); continue
            try:
                product = build_product(pos)             # QuantArk product
            except Exception as exc:
                excluded.append({"position_id": pos.id, "reason": f"build_product failed: {exc}"}); continue
            book_products.append(BookProduct(
                product=product, quantity=float(pos.quantity), position_id=pos.id,
                has_lifecycle=_has_lifecycle(product)))
        if not book_products:
            continue
        spot_df, vol_df, rate_df, futures_df, hedge = history[underlying]  # prepared by the pipeline
        configs.append(BookAutocallableBacktestConfig(
            products=book_products,
            market_data=AutocallableMarketDataSet.from_dataframes(
                spot_data=spot_df, vol_data=vol_df, rate_data=rate_df, futures_data=futures_df),
            hedge=hedge,
            engine_config=AutocallableEngineConfig(pricing_engine_type=engine_type),
            strategy=strategy, transaction_cost_model=txn_cost, underlying=underlying,
            start_date=start, end_date=end, calculate_event_probabilities=True,
            calculate_surfaces=False))
    return configs, excluded


def _has_lifecycle(product) -> bool:
    name = type(product).__name__
    return name in ("SnowballOption", "PhoenixOption")
```

> The pipeline (Task 2.5) prepares `history[underlying] = (spot_df, vol_df, rate_df, futures_df, HedgeSpec)` using `backtest_market_history` + `hedging_map`. `build_product` is the existing unified producer — confirm its exact import path with `grep -rn "def build_product" backend/app` and adjust the import.

- [ ] **Step 3: Run → PASS**; **Step 4: Commit**

```bash
git add backend/app/services/backtest_bridge.py tests/test_backtest_bridge.py
git commit -m "feat(backtest): bridge groups positions by underlying into book configs"
```

### Task 2.5: Domain pipeline

**Files:**
- Create: `backend/app/services/domains/backtest.py`
- Test: `tests/test_backtest_pipeline.py`

Mirror `domains/scenario_test.py`: `run_pipeline(...) -> (status, results_dict, excluded, raw)`, `_jsonable`, `write_artifacts`, and an aggregation/shaping step. **Contribution point (c):** the risk-metric block (`_risk_metrics`) is the marked stub.

- [ ] **Step 1: Write the failing test (shaping + risk metrics)**

```python
# tests/test_backtest_pipeline.py
from app.services.domains import backtest as bt


def test_risk_metrics_on_known_series():
    pnl = [0.0, 100.0, 50.0, 200.0, 150.0]
    m = bt._risk_metrics(pnl)
    assert m["max_drawdown"] >= 0.0
    assert "sharpe" in m and "var_95" in m and "cvar_95" in m


def test_aggregate_two_underlyings():
    per = [
        {"underlying": "A", "summary": {"total_pnl": 100.0, "hedge_pnl": 10.0, "num_trades": 3}},
        {"underlying": "B", "summary": {"total_pnl": -40.0, "hedge_pnl": 5.0, "num_trades": 2}},
    ]
    agg = bt._aggregate_portfolio(per)
    assert agg["total_pnl"] == 60.0 and agg["num_trades"] == 5
```

- [ ] **Step 2: Implement** (key functions; reuse `_jsonable` from the scenario pattern)

```python
# backend/app/services/domains/backtest.py
"""Backtest pipeline: history -> bridge -> book engines -> aggregate -> shape -> artifacts."""
from __future__ import annotations
import os
from datetime import datetime
from typing import Any
import numpy as np
from sqlalchemy.orm import Session
from app.services import quantark, backtest_bridge, backtest_market_history as mh


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try: return item()
        except Exception: pass
    try: return float(value)
    except (TypeError, ValueError): return str(value)


def _risk_metrics(pnl_series: list[float]) -> dict[str, float]:
    # --- Contribution point (c): annualization, drawdown basis, historical vs parametric VaR ---
    arr = np.asarray(pnl_series, dtype=float)
    if arr.size < 2:
        return {"sharpe": 0.0, "max_drawdown": 0.0, "var_95": 0.0, "cvar_95": 0.0}
    daily = np.diff(arr)
    sharpe = float(np.mean(daily) / np.std(daily) * np.sqrt(252.0)) if np.std(daily) > 0 else 0.0
    running_max = np.maximum.accumulate(arr)
    drawdown = running_max - arr
    max_dd = float(np.max(drawdown))
    var_95 = float(-np.percentile(daily, 5))
    cvar_95 = float(-np.mean(daily[daily <= np.percentile(daily, 5)])) if (daily <= np.percentile(daily, 5)).any() else var_95
    return {"sharpe": sharpe, "max_drawdown": max_dd, "var_95": var_95, "cvar_95": cvar_95}


def _aggregate_portfolio(per_underlying: list[dict]) -> dict:
    total = sum(u["summary"].get("total_pnl", 0.0) for u in per_underlying)
    hedge = sum(u["summary"].get("hedge_pnl", 0.0) for u in per_underlying)
    trades = sum(u["summary"].get("num_trades", 0) for u in per_underlying)
    return {"total_pnl": float(total), "hedge_pnl": float(hedge), "num_trades": int(trades)}


def run_pipeline(session: Session, *, positions, pricing_parameter_profile_id,
                 spec: dict, config: dict, portfolio_name: str,
                 valuation_date: datetime | None = None,
                 progress=None) -> tuple[str, dict, list[dict], Any]:
    """status in {completed, empty}; raw = list of (underlying, BookBacktestResults)."""
    start, end = spec["start"], spec["end"]
    engine_name = spec.get("engine", "quad")
    vol_source = spec.get("vol_source", "realized")
    vol_window = int(spec.get("vol_window", 20))
    rate = float(spec.get("rate", 0.02))
    flat_vol = float(spec.get("flat_vol", 0.18))

    quantark.ensure_quantark_path()
    from backtest.otc import BookAutocallableBacktestEngine, HedgeSpec, FuturesRollPolicy
    from util.enum.engine_enums import EngineType
    engine_type = {"quad": EngineType.QUADRATURE, "pde": EngineType.PDE,
                   "mc": EngineType.MONTE_CARLO}[engine_name]

    # Resolve per-underlying history + hedge instrument.
    from app.services import hedging_universe   # existing map; resolve hedge instrument
    groups = backtest_bridge.group_by_underlying(positions)
    history = {}
    for underlying in groups:
        symbol, asset_class = _resolve_symbol(session, underlying)
        spot_df = mh.ensure_spot_history(session, symbol=symbol, asset_class=asset_class, start=start, end=end)
        vol_df = mh.derive_vol(spot_df, vol_source=vol_source, vol_window=vol_window, flat_vol=flat_vol)
        rate_df = mh.flat_rate(spot_df, rate)
        hedge_instr = hedging_universe.resolve_hedge_instrument(session, underlying)  # or None
        if hedge_instr and hedge_instr.get("kind") == "futures":
            futures_df = mh.ensure_futures_chain(session, prefix=hedge_instr["prefix"], start=start, end=end)
            hedge = HedgeSpec(kind="futures", multiplier=float(hedge_instr["multiplier"]),
                              roll_policy=FuturesRollPolicy())
        else:
            futures_df = spot_df.assign(contract="SPOT", futures_price=spot_df["spot"],
                                        expiry_date=spot_df["date"].max(), multiplier=1.0)
            hedge = HedgeSpec(kind="spot", multiplier=1.0)
        history[underlying] = (spot_df, vol_df, rate_df, futures_df, hedge)

    configs, excluded = backtest_bridge.build_books(
        session, positions, history, hedging_map=None, vol_source=vol_source, vol_window=vol_window,
        rate=rate, flat_vol=flat_vol, txn_cost=_txn_cost(config), strategy=None,
        start=_dt(start), end=_dt(end), engine_type=engine_type)
    if not configs:
        return "empty", {"message": "No includable positions", "by_underlying": []}, excluded, []

    per_underlying, raw = [], []
    for i, cfg in enumerate(configs):
        results = BookAutocallableBacktestEngine(cfg).run()
        raw.append((cfg.underlying, results))
        per_underlying.append(_shape_underlying(cfg, results))
        if progress: progress(i + 1, len(configs))

    portfolio = _aggregate_portfolio(per_underlying)
    pnl_series = _portfolio_pnl_series(per_underlying)
    portfolio.update(_risk_metrics([p["total_pnl"] for p in pnl_series]))
    portfolio["pnl_series"] = pnl_series
    results_dict = _jsonable({
        "window": {"start": start, "end": end},
        "engine": engine_name, "vol_source": f"{vol_source}:{vol_window}",
        "portfolio": portfolio, "by_underlying": per_underlying,
        "excluded_positions": excluded,
    })
    return "completed", results_dict, excluded, raw
```

> Implement helpers: `_resolve_symbol(session, underlying)` (map an oot underlying to an akshare symbol + asset_class — reuse `app.services.underlyings`); `_txn_cost(config)` (build a quant-ark `TransactionCostModel` from config knobs, default `ZeroCostModel`); `_dt(str)`; `_shape_underlying(cfg, results)` (downsample `results.states_df()` to a `pnl_series`/`greeks_series`, extract `lifecycle_events` from `results.actions_df()`, latest `event_summary` from `daily_event_summary_df()`, plus `results.get_summary()`); `_portfolio_pnl_series(per_underlying)` (align by date, sum). Add `write_artifacts(*, raw, run_id, formats, base_dir)` mirroring the scenario `write_artifacts` but calling `AutocallableBacktestDashboard` per underlying + writing a combined `index.html`; guard `pyarrow`/parquet so an absent lib only adds a note.

- [ ] **Step 3: Run → PASS** (the two unit tests; full pipeline covered in 2.6/Phase smoke)

Run: `python -m pytest tests/test_backtest_pipeline.py -v`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/domains/backtest.py tests/test_backtest_pipeline.py
git commit -m "feat(backtest): domain pipeline (history->bridge->engines->aggregate->shape)"
```

### Task 2.6: Async runner + `backtest_output_dir` setting

**Files:**
- Create: `backend/app/services/backtest_runner.py`
- Modify: `backend/app/config.py`
- Test: `tests/test_backtest_runner.py`

- [ ] **Step 1: Add the setting** in `config.py` (next to `scenario_test_output_dir`):

```python
    backtest_output_dir: str = "outputs/backtest"
```

- [ ] **Step 2: Write the failing test (queue validation)**

```python
# tests/test_backtest_runner.py
import pytest
from app.services import backtest_runner
from app.models import Portfolio
from app.database import SessionLocal, init_db


def test_queue_rejects_bad_window():
    init_db()
    s = SessionLocal()
    try:
        p = Portfolio(name="bt"); s.add(p); s.commit()
        with pytest.raises(ValueError):
            backtest_runner.queue_backtest(
                s, portfolio_id=p.id, pricing_parameter_profile_id=None,
                spec={"start": "2024-04-30", "end": "2024-01-02", "engine": "quad"},  # start>end
                config={})
    finally:
        s.close()
```

- [ ] **Step 3: Implement** (clone `scenario_test_runner.py` structure)

```python
# backend/app/services/backtest_runner.py
"""Queue + async execution for backtest runs. Mirrors scenario_test_runner."""
from __future__ import annotations
from typing import Any
from sqlalchemy.orm import Session, sessionmaker
from .. import database
from ..config import get_settings
from ..models import BacktestRun, Portfolio, PricingParameterProfile, TaskKind, TaskRun, TaskStatus
from .audit import record_audit
from .domains import positions as positions_svc
from .domains import backtest as backtest_svc
from .task_runner import submit_async_task

_VALID_ENGINES = {"quad", "pde", "mc"}
_VALID_VOL = {"realized", "flat"}


def _validate_spec(spec: dict) -> None:
    import pandas as pd
    start, end = spec.get("start"), spec.get("end")
    if not start or not end or pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError("spec.start must be a date strictly before spec.end")
    if spec.get("engine", "quad") not in _VALID_ENGINES:
        raise ValueError(f"engine must be one of {_VALID_ENGINES}")
    if spec.get("vol_source", "realized") not in _VALID_VOL:
        raise ValueError(f"vol_source must be one of {_VALID_VOL}")


def queue_backtest(session: Session, *, portfolio_id: int,
                   pricing_parameter_profile_id: int | None, spec: dict, config: dict,
                   position_ids: list[int] | None = None) -> tuple[BacktestRun, TaskRun]:
    _validate_spec(spec)
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    if position_ids is not None:
        from app.services.risk_engine import _resolve_risk_positions
        position_ids = [p.id for p in _resolve_risk_positions(portfolio, session, position_ids=position_ids)]
    run = BacktestRun(portfolio_id=portfolio_id,
                      pricing_parameter_profile_id=pricing_parameter_profile_id,
                      status=TaskStatus.QUEUED.value, spec=spec, config=config,
                      results={}, excluded_positions=[], artifacts={},
                      resolved_position_ids=position_ids)
    session.add(run); session.flush()
    task = TaskRun(kind=TaskKind.BACKTEST.value, status=TaskStatus.QUEUED.value,
                   portfolio_id=portfolio_id, backtest_run_id=run.id, message="Queued backtest run")
    session.add(task); session.flush()
    record_audit(session, event_type="backtest.queued", actor="desk_user",
                 subject_type="portfolio", subject_id=portfolio_id,
                 payload={"run_id": run.id, "spec": spec})
    session.commit()
    submit_async_task(execute_backtest_task, task.id, run.id)
    return run, task


def execute_backtest_task(task_id: int, run_id: int, session_factory: sessionmaker | None = None) -> None:
    database.init_db()
    session = (session_factory or database.SessionLocal)()
    try:
        _execute(session, task_id, run_id)
    finally:
        session.close()


def _execute(session: Session, task_id: int, run_id: int) -> None:
    from .task_runner import mark_task_finished, mark_task_running
    run = session.get(BacktestRun, run_id); task = session.get(TaskRun, task_id)
    if run is None or task is None:
        return
    try:
        mark_task_running(session, task_id, message="Running backtest")
        run.status = TaskStatus.RUNNING.value; session.commit()
        portfolio = session.get(Portfolio, run.portfolio_id)
        all_positions = positions_svc.list_filtered(portfolio_id=run.portfolio_id, session=session)
        if run.resolved_position_ids is not None:
            wanted = set(run.resolved_position_ids)
            positions = [p for p in all_positions if p.id in wanted]
        else:
            positions = list(all_positions)
        run.resolved_position_ids = [p.id for p in positions]

        valuation_as_of = run.created_at
        if run.pricing_parameter_profile_id is not None:
            profile = session.get(PricingParameterProfile, run.pricing_parameter_profile_id)
            if profile is not None and profile.valuation_date is not None:
                valuation_as_of = profile.valuation_date

        def _progress(cur, total):
            task.progress_current, task.progress_total = cur, total
            session.commit()

        status, results_dict, excluded, raw = backtest_svc.run_pipeline(
            session, positions=positions,
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            spec=run.spec, config=run.config,
            portfolio_name=(portfolio.name if portfolio else "portfolio"),
            valuation_date=valuation_as_of, progress=_progress)
        run.results = results_dict; run.excluded_positions = excluded
        if status == "completed" and raw:
            settings = get_settings()
            run.artifacts = backtest_svc.write_artifacts(
                raw=raw, run_id=run.id,
                formats=run.config.get("export_formats", ["json", "xlsx", "html"]),
                base_dir=settings.backtest_output_dir)
        run.status = status; session.commit()
        mark_task_finished(session, task_id, status=TaskStatus.COMPLETED.value,
                           message=f"Backtest {status}", result_payload={"backtest_run_id": run_id})
        session.commit()
    except Exception as exc:  # noqa: BLE001 — persist failure, never crash the worker
        session.rollback()
        try:
            run = session.get(BacktestRun, run_id)
            if run is not None:
                run.status = TaskStatus.FAILED.value; run.results = {"error": str(exc)}
            mark_task_finished(session, task_id, status=TaskStatus.FAILED.value, error=str(exc))
            session.commit()
        except Exception:
            session.rollback()
```

- [ ] **Step 4: Run → PASS**; **Step 5: Commit**

```bash
git add backend/app/services/backtest_runner.py backend/app/config.py tests/test_backtest_runner.py
git commit -m "feat(backtest): async runner (queue/execute) + backtest_output_dir setting"
```

---
## Phase 3 — REST endpoints, agent tools, skill + routing

### Task 3.1: Pydantic schemas

**Files:**
- Modify: `backend/app/schemas.py`
- Test: covered by REST test (3.2)

- [ ] **Step 1: Add schemas** (mirror the `ScenarioTestRun*` ones — find them with `grep -n "ScenarioTestRunOut\|ScenarioTestRunRequest" backend/app/schemas.py`):

```python
class BacktestConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_formats: list[str] = Field(default_factory=lambda: ["json", "xlsx", "html"])
    rebalance_band: float | None = None
    transaction_cost_bps: float | None = None
    roll_days_before_expiry: int | None = None
    calculate_surfaces: bool = False


class BacktestSpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str
    engine: str = "quad"
    vol_source: str = "realized"
    vol_window: int = 20
    rate: float = 0.02
    flat_vol: float = 0.18


class BacktestRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    position_ids: list[int] | None = None
    spec: BacktestSpecIn
    config: BacktestConfigIn = Field(default_factory=BacktestConfigIn)


class BacktestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    portfolio_id: int
    pricing_parameter_profile_id: int | None
    resolved_position_ids: list[int] | None
    status: str
    spec: dict
    config: dict
    results: dict
    excluded_positions: list | None
    artifacts: dict
    created_at: datetime
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(backtest): request/response schemas"
```

### Task 3.2: REST endpoints

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_backtest_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_api.py
from fastapi.testclient import TestClient
from app.main import create_app
from app.database import SessionLocal, init_db
from app.models import Portfolio


def _client():
    init_db()
    return TestClient(create_app())


def test_create_run_validates_window():
    c = _client()
    s = SessionLocal(); p = Portfolio(name="api-bt"); s.add(p); s.commit(); pid = p.id; s.close()
    r = c.post("/api/backtest/runs", json={
        "portfolio_id": pid,
        "spec": {"start": "2024-04-30", "end": "2024-01-02"}})  # start>end
    assert r.status_code == 400


def test_get_missing_run_404():
    c = _client()
    assert c.get("/api/backtest/runs/999999").status_code == 404
```

- [ ] **Step 2: Add the endpoints** in `main.py` (place near the scenario-test routes, ~line 3290; import `BacktestRun`, `BacktestRunOut`, `BacktestRunRequest` at the top imports block):

```python
    @app.post("/api/backtest/runs", response_model=BacktestRunOut)
    def backtest_create_run(payload: BacktestRunRequest, session: Session = Depends(get_db)):
        from .services import backtest_runner
        try:
            run, _task = backtest_runner.queue_backtest(
                session, portfolio_id=payload.portfolio_id,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                spec=payload.spec.model_dump(), config=payload.config.model_dump(),
                position_ids=payload.position_ids)
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        session.refresh(run)
        return BacktestRunOut.model_validate(run)

    @app.get("/api/backtest/runs", response_model=list[BacktestRunOut])
    def backtest_list_runs(portfolio_id: int = Query(...), session: Session = Depends(get_db)):
        runs = (session.query(BacktestRun)
                .filter(BacktestRun.portfolio_id == portfolio_id)
                .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).all())
        return [BacktestRunOut.model_validate(r) for r in runs]

    @app.get("/api/backtest/runs/{run_id}", response_model=BacktestRunOut)
    def backtest_get_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="BacktestRun not found")
        return BacktestRunOut.model_validate(run)

    @app.get("/api/backtest/runs/{run_id}/artifacts/{name}")
    def backtest_get_artifact(run_id: int, name: str, session: Session = Depends(get_db)):
        import os
        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="BacktestRun not found")
        artifacts = run.artifacts or {}
        recorded = list(artifacts.get("export_paths", []))
        for key in ("report_html_path", "dashboard_html"):
            if artifacts.get(key):
                recorded.append(artifacts[key])
        for v in artifacts.get("dashboards", {}).values():
            recorded.append(v)
        matched = next((p for p in recorded if os.path.basename(p) == name), None)
        if not matched or not os.path.isfile(matched):
            raise HTTPException(status_code=404, detail="artifact not found")
        from fastapi.responses import FileResponse
        return FileResponse(matched, filename=name)

    @app.delete("/api/backtest/runs/{run_id}")
    def backtest_delete_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="BacktestRun not found")
        session.delete(run); session.commit()
        return {"deleted": run_id}
```

- [ ] **Step 3: Run → PASS**; **Step 4: Commit**

```bash
git add backend/app/main.py tests/test_backtest_api.py
git commit -m "feat(backtest): REST endpoints /api/backtest/*"
```

### Task 3.3: Agent tools

**Files:**
- Create: `backend/app/tools/backtest.py`
- Modify: `backend/app/tools/__init__.py` + the deep-agent tool allowlist
- Test: `tests/test_backtest_tools.py`

- [ ] **Step 1: Locate the allowlist + tool registration**

Run: `grep -rn "DEEP_AGENT_TOOL_NAMES\|scenario_test" backend/app/tools/__init__.py backend/app/services/deep_agent/*.py | head`
Expected: shows where scenario_test tools are registered + the allowlist. Mirror those exact spots.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_backtest_tools.py
from app.tools import backtest as bt_tools


def test_run_backtest_tool_exists_and_forbids_extra():
    tool = bt_tools.run_backtest_tool
    assert tool.name == "run_backtest"
    # extra="forbid" on the args schema
    schema = tool.args_schema
    assert schema.model_config.get("extra") == "forbid"
```

- [ ] **Step 3: Implement** (mirror `tools/scenario_test.py`)

```python
# backend/app/tools/backtest.py
"""@tool wrappers for the backtest domain. Thin LLM adapters."""
from __future__ import annotations
from typing import Any
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field
from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services import backtest_runner
from app.models import BacktestRun


class RunBacktestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    start_date: str
    end_date: str
    pricing_parameter_profile_id: int | None = None
    position_ids: list[int] | None = None
    engine: str = "quad"
    vol_source: str = "realized"
    vol_window: int = 20
    config: dict = Field(default_factory=dict)


class GetBacktestRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int


class ListBacktestRunsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int


@tool("run_backtest", args_schema=RunBacktestInput)
@capability_gated(ToolGroup.RISK)   # match the group scenario_test uses
def run_backtest_tool(**kwargs: Any) -> dict[str, Any]:
    """Queue an async historical hedging backtest of a portfolio's positions."""
    session = database.SessionLocal()
    try:
        spec = {"start": kwargs["start_date"], "end": kwargs["end_date"],
                "engine": kwargs.get("engine", "quad"),
                "vol_source": kwargs.get("vol_source", "realized"),
                "vol_window": kwargs.get("vol_window", 20)}
        run, task = backtest_runner.queue_backtest(
            session, portfolio_id=kwargs["portfolio_id"],
            pricing_parameter_profile_id=kwargs.get("pricing_parameter_profile_id"),
            spec=spec, config=kwargs.get("config", {}),
            position_ids=kwargs.get("position_ids"))
        return {"run_id": run.id, "task_id": task.id, "status": run.status}
    finally:
        session.close()


@tool("get_backtest_run", args_schema=GetBacktestRunInput)
@capability_gated(ToolGroup.RISK)
def get_backtest_run_tool(**kwargs: Any) -> dict[str, Any]:
    """Fetch a backtest run's status, summary results, and artifact links."""
    session = database.SessionLocal()
    try:
        run = session.get(BacktestRun, kwargs["run_id"])
        if run is None:
            return {"error": f"BacktestRun {kwargs['run_id']} not found"}
        return {"run_id": run.id, "status": run.status,
                "portfolio": (run.results or {}).get("portfolio"),
                "by_underlying": (run.results or {}).get("by_underlying"),
                "artifacts": run.artifacts}
    finally:
        session.close()


@tool("list_backtest_runs", args_schema=ListBacktestRunsInput)
@capability_gated(ToolGroup.RISK)
def list_backtest_runs_tool(**kwargs: Any) -> dict[str, Any]:
    """List recent backtest runs for a portfolio."""
    session = database.SessionLocal()
    try:
        runs = (session.query(BacktestRun)
                .filter(BacktestRun.portfolio_id == kwargs["portfolio_id"])
                .order_by(BacktestRun.id.desc()).limit(20).all())
        return {"runs": [{"id": r.id, "status": r.status,
                          "window": (r.spec or {}).get("start", "") + ".." + (r.spec or {}).get("end", ""),
                          "total_pnl": (r.results or {}).get("portfolio", {}).get("total_pnl")}
                         for r in runs]}
    finally:
        session.close()
```

> Confirm the exact `ToolGroup` member and `capability_gated` usage by copying from `tools/scenario_test.py` (the decorators must match that file's pattern precisely).

- [ ] **Step 4: Register the tools** in `tools/__init__.py` and add `"run_backtest"`, `"get_backtest_run"`, `"list_backtest_runs"` to `DEEP_AGENT_TOOL_NAMES` (exact same place scenario_test names live).

- [ ] **Step 5: Run → PASS** (`python -m pytest tests/test_backtest_tools.py -v`); **Step 6: Commit**

```bash
git add backend/app/tools/backtest.py backend/app/tools/__init__.py tests/test_backtest_tools.py
git commit -m "feat(backtest): agent tools + allowlist registration"
```

### Task 3.4: Skill + reference doc + routing

**Files:**
- Create: `backend/app/skills/workflows/backtest/SKILL.md`
- Create: `backend/app/skills/references/risk/backtest.md`

- [ ] **Step 1: Write the SKILL.md** (body ≤ 500 tokens; copy frontmatter shape from an existing workflow SKILL.md — e.g. `skills/workflows/risk/run-scenario-test/SKILL.md` — including the exact `routing:` keys it uses)

```markdown
---
name: run-backtest
description: Historical hedging backtest of a portfolio's positions, netted per underlying.
routing:
  trigger: User wants to backtest / replay how a portfolio (snowballs, phoenixes, or
    equity options) would have been delta-hedged over a historical window.
  tool: run_backtest
---

## When to use
Use when the desk asks to replay history and see how booked positions would have
performed under daily delta-hedging: hedge P&L, greeks over time, autocallable
KO/KI/autocall/coupon events, transaction costs, and risk metrics. Not for
forward-looking scenario shocks (use run-scenario-test) or a single as-of
valuation (use pricing/risk).

## Required inputs
- portfolio_id (optional position_ids to scope)
- start_date, end_date (the replay window)
- optional: pricing_parameter_profile_id, engine (quad|pde|mc),
  vol_source (realized|flat), vol_window

## Procedure
1. Confirm the portfolio + window with the user.
2. Call run_backtest (async — returns run_id + task_id).
3. Poll get_backtest_run(run_id) until status is completed/failed.
4. Summarize total/hedge/product P&L, # trades, max drawdown, Sharpe, VaR95,
   and per-underlying lifecycle events; link the full quant-ark dashboard.

## Stop conditions
- Stop and report if the run fails (surface results.error).
- Stop if all positions were excluded (report excluded_positions reasons).

## Output shape
run_id, status, portfolio totals, by_underlying breakdown, artifact links.

## References
skills/references/risk/backtest.md

## Example
"Backtest portfolio 3 over Jan–Apr 2024 hedging with index futures" ->
run_backtest(portfolio_id=3, start_date="2024-01-02", end_date="2024-04-30").
```

- [ ] **Step 2: Write the reference doc** `references/risk/backtest.md` with frontmatter (copy the frontmatter keys from `references/risk/scenario-test.md`):

```markdown
---
title: Backtest (historical hedging replay)
domain: risk
---

# Backtest

Replays daily market history and simulates the desk's delta-hedging program,
netted per underlying, with autocallable lifecycle. Wraps quant-ark's
BookAutocallableBacktestEngine.

## Engines
- quad (default), pde, mc — pricing engine for daily MTM/greeks.

## Hedge mechanics
- Index underlyings: index futures with roll (FuturesRollPolicy) + basis yield.
- Underlyings without listed futures: spot hedge (no roll).

## Lifecycle (autocallables)
KO / KI / autocall / coupon applied against the realized path; a knocked-out
position leaves the book and realizes its cashflow.

## Outputs
portfolio totals (P&L split, Sharpe, max drawdown, VaR95/CVaR95), per-underlying
breakdown (lifecycle events, event probabilities, trades), and a quant-ark
dashboard.html per underlying.

## Market data
Stored MarketDataProfile daily history; akshare backfill + persist on gaps,
validated against the China SSE trading calendar.
```

- [ ] **Step 3: Update the SIX catalog/routing test files (atomic)**

The skill catalog pins exact sets + counts. Discover current expectations:
```bash
grep -rln "scenario-test\|run-scenario-test\|WORKFLOW_SKILLS\|OLD_TABLE_ROWS\|expected.*count" \
  tests/test_skills_catalog.py tests/test_skills_catalog_v2.py \
  tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py \
  tests/test_reference_docs.py tests/test_routing_table.py
```
For each file that asserts the set of workflow skills, add `run-backtest`; bump any count assertions by 1; add the reference doc `risk/backtest.md` to `test_reference_docs`; add the routing triple (trigger→`run-backtest`→`run_backtest`) to `OLD_TABLE_ROWS` in `test_routing_table.py`. (Exact edits depend on current contents — read each, make the minimal addition.)

- [ ] **Step 4: Rebuild orchestrator + run the catalog suite**

Run:
```bash
python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py \
  tests/test_workflow_skills_phase3.py tests/test_remaining_workflow_skills_phase3.py \
  tests/test_reference_docs.py tests/test_routing_table.py -v
```
Expected: PASS. If `rebuild_orchestrator` is invoked at runtime (not test-time), also run any orchestrator-build test that exists (`grep -rln rebuild_orchestrator tests`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/skills/workflows/backtest/SKILL.md backend/app/skills/references/risk/backtest.md tests/
git commit -m "feat(backtest): skill + reference doc + routing (six-file catalog update)"
```

---
## Phase 4 — Frontend (`Backtest` page)

> Work in `/Users/fuxinyao/open-otc-trading-backtest/frontend`. Run tests with `npx vitest run src/routes/Backtest.test.tsx`. The app uses a **custom string-union router** (`route === 'x' && <Page/>`), not react-router. Styling MUST use design tokens (no hardcoded hex/rgba) per `UI_STYLE_GUIDE.md`.

### Task 4.1: Types + API client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts` (if the repo has client tests; else skip)

- [ ] **Step 1: Add the `Route` union member + types** in `types.ts`

Find the `Route` union (`grep -n "export type Route" src/types.ts`) and add `'backtest'`. Then add (next to the Scenario Test block ~line 1038):

```typescript
// --- Backtest ------------------------------------------------------
export type BacktestSpec = {
  start: string;
  end: string;
  engine?: 'quad' | 'pde' | 'mc';
  vol_source?: 'realized' | 'flat';
  vol_window?: number;
  rate?: number;
  flat_vol?: number;
};

export type BacktestRunRequest = {
  portfolio_id: number;
  pricing_parameter_profile_id?: number | null;
  position_ids?: number[] | null;
  spec: BacktestSpec;
  config?: Record<string, unknown>;
};

export type BacktestUnderlying = {
  underlying: string;
  hedge_instrument?: string;
  num_products: number;
  total_pnl: number;
  hedge_pnl: number;
  num_trades: number;
  lifecycle_events: { position_id: number; type: string; date: string; cashflow: number }[];
  event_summary?: { ko_prob?: number; ki_prob?: number; survival_prob?: number };
  pnl_series: { date: string; total_pnl: number; hedge_pnl: number; product_pnl: number }[];
  greeks_series: { date: string; net_delta_pre: number; net_delta_post: number; gamma: number; vega: number }[];
};

export type BacktestPortfolioSummary = {
  initial_value?: number; final_value?: number; total_pnl: number;
  product_pnl?: number; hedge_pnl: number; transaction_costs?: number;
  num_trades: number; turnover?: number;
  sharpe?: number; max_drawdown?: number; var_95?: number; cvar_95?: number;
  pnl_series: { date: string; total_pnl: number; hedge_pnl: number; product_pnl: number; unhedged_pnl?: number }[];
};

export type BacktestRun = {
  id: number;
  portfolio_id: number;
  status: string;
  spec: BacktestSpec;
  config: Record<string, unknown>;
  results: {
    window?: { start: string; end: string };
    engine?: string;
    portfolio?: BacktestPortfolioSummary;
    by_underlying?: BacktestUnderlying[];
    excluded_positions?: { position_id: number; reason: string }[];
    error?: string;
  };
  excluded_positions?: { position_id: number; reason: string }[] | null;
  artifacts: Record<string, unknown>;
  created_at: string;
};
```

- [ ] **Step 2: Add client methods** in `api/client.ts` (after the Scenario Test block ~line 113)

```typescript
// --- Backtest ------------------------------------------------------
export const createBacktestRun = (body: BacktestRunRequest) =>
  api<BacktestRun>('/api/backtest/runs', { method: 'POST', body: JSON.stringify(body) });

export const listBacktestRuns = (portfolioId: number) =>
  api<BacktestRun[]>(`/api/backtest/runs?portfolio_id=${portfolioId}`);

export const getBacktestRun = (runId: number) =>
  api<BacktestRun>(`/api/backtest/runs/${runId}`);

export const backtestArtifactUrl = (runId: number, name: string) =>
  `/api/backtest/runs/${runId}/artifacts/${encodeURIComponent(name)}`;
```
Add `BacktestRun`, `BacktestRunRequest` to the type imports at the top of `client.ts`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts
git commit -m "feat(backtest): frontend types + api client"
```

### Task 4.2: `Backtest` page component

**Files:**
- Create: `frontend/src/routes/Backtest.tsx`
- Create: `frontend/src/routes/Backtest.css`
- Test: `frontend/src/routes/Backtest.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/routes/Backtest.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { Backtest } from './Backtest';

vi.mock('../api/client', () => ({
  listBacktestRuns: vi.fn().mockResolvedValue([]),
  createBacktestRun: vi.fn(),
  getBacktestRun: vi.fn(),
  backtestArtifactUrl: () => '#',
  listPortfolios: vi.fn().mockResolvedValue([{ id: 1, name: 'P1' }]),
}));

describe('Backtest', () => {
  it('renders the run configurator', async () => {
    render(<Backtest />);
    expect(await screen.findByText(/Run backtest/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run → FAIL** (`Cannot find module './Backtest'`)

Run: `npx vitest run src/routes/Backtest.test.tsx`

- [ ] **Step 3: Implement the component** (recharts; design tokens; poll while RUNNING)

```tsx
// frontend/src/routes/Backtest.tsx
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  createBacktestRun, getBacktestRun, listBacktestRuns, backtestArtifactUrl, listPortfolios,
} from '../api/client';
import type { BacktestRun } from '../types';
import './Backtest.css';

const TERMINAL = new Set(['completed', 'failed', 'completed_with_errors', 'empty']);

export function Backtest() {
  const [portfolios, setPortfolios] = useState<{ id: number; name: string }[]>([]);
  const [portfolioId, setPortfolioId] = useState<number | null>(null);
  const [start, setStart] = useState('2024-01-02');
  const [end, setEnd] = useState('2024-04-30');
  const [engine, setEngine] = useState<'quad' | 'pde' | 'mc'>('quad');
  const [volSource, setVolSource] = useState<'realized' | 'flat'>('realized');
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selected, setSelected] = useState<BacktestRun | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listPortfolios().then((ps: { id: number; name: string }[]) => {
      setPortfolios(ps);
      if (ps[0]) setPortfolioId(ps[0].id);
    });
  }, []);

  const refreshRuns = useCallback(async (pid: number) => {
    setRuns(await listBacktestRuns(pid));
  }, []);

  useEffect(() => { if (portfolioId) refreshRuns(portfolioId); }, [portfolioId, refreshRuns]);

  // Poll a running selected run until terminal.
  useEffect(() => {
    if (!selected || TERMINAL.has(selected.status)) return;
    const t = setInterval(async () => {
      const fresh = await getBacktestRun(selected.id);
      setSelected(fresh);
      if (TERMINAL.has(fresh.status) && portfolioId) refreshRuns(portfolioId);
    }, 2000);
    return () => clearInterval(t);
  }, [selected, portfolioId, refreshRuns]);

  const onRun = async () => {
    if (!portfolioId) return;
    setBusy(true);
    try {
      const run = await createBacktestRun({
        portfolio_id: portfolioId,
        spec: { start, end, engine, vol_source: volSource },
      });
      setSelected(run);
      await refreshRuns(portfolioId);
    } finally { setBusy(false); }
  };

  const pnl = selected?.results?.portfolio;
  const dashboards = (selected?.artifacts?.dashboards ?? {}) as Record<string, string>;

  return (
    <div className="backtest-page">
      <aside className="backtest-config">
        <h2>Run backtest</h2>
        <label>Portfolio
          <select value={portfolioId ?? ''} onChange={(e) => setPortfolioId(Number(e.target.value))}>
            {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label>Start<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>End<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Engine
          <select value={engine} onChange={(e) => setEngine(e.target.value as typeof engine)}>
            <option value="quad">quad</option><option value="pde">pde</option><option value="mc">mc</option>
          </select>
        </label>
        <label>Vol source
          <select value={volSource} onChange={(e) => setVolSource(e.target.value as typeof volSource)}>
            <option value="realized">realized (20d)</option><option value="flat">flat</option>
          </select>
        </label>
        <button className="backtest-run-btn" disabled={busy || !portfolioId} onClick={onRun}>
          {busy ? 'Queuing…' : 'Run backtest'}
        </button>
        <h3>Runs</h3>
        <ul className="backtest-run-list">
          {runs.map((r) => (
            <li key={r.id} className={selected?.id === r.id ? 'is-selected' : ''}
                onClick={() => setSelected(r)}>
              #{r.id} · {r.status}
            </li>
          ))}
        </ul>
      </aside>

      <main className="backtest-report">
        {!selected && <p className="backtest-empty">Select or start a run.</p>}
        {selected && selected.status === 'failed' && (
          <p className="backtest-error">Run failed: {selected.results?.error}</p>
        )}
        {selected && !TERMINAL.has(selected.status) && <p>Running… ({selected.status})</p>}
        {selected && pnl && (
          <>
            <div className="backtest-header">
              <strong>Run #{selected.id} — {selected.spec.start} → {selected.spec.end} · {selected.spec.engine}</strong>
              {Object.entries(dashboards).map(([u, path]) => (
                <a key={u} href={backtestArtifactUrl(selected.id, path.split('/').pop() as string)}
                   target="_blank" rel="noreferrer">⤓ {u} dashboard</a>
              ))}
            </div>
            <div className="backtest-kpis">
              <Kpi label="Total P&L" value={pnl.total_pnl} />
              <Kpi label="Hedge P&L" value={pnl.hedge_pnl} />
              <Kpi label="Trades" value={pnl.num_trades} />
              <Kpi label="Max DD" value={pnl.max_drawdown} />
              <Kpi label="Sharpe" value={pnl.sharpe} />
              <Kpi label="VaR95" value={pnl.var_95} />
            </div>
            <div className="backtest-chart">
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={pnl.pnl_series}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" stroke="var(--text-muted)" />
                  <YAxis stroke="var(--text-muted)" />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="total_pnl" stroke="var(--accent)" dot={false} />
                  <Line type="monotone" dataKey="hedge_pnl" stroke="var(--accent-secondary)" dot={false} />
                  <Line type="monotone" dataKey="product_pnl" stroke="var(--text-muted)" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <h3>By underlying</h3>
            {(selected.results.by_underlying ?? []).map((u) => (
              <details key={u.underlying} className="backtest-underlying">
                <summary>{u.underlying} ({u.hedge_instrument}) · {u.num_products} products · P&L {u.total_pnl.toFixed(0)}</summary>
                <ul>
                  {u.lifecycle_events.map((e, i) => (
                    <li key={i}>{e.date} · {e.type} · pos {e.position_id} · cf {e.cashflow.toFixed(0)}</li>
                  ))}
                </ul>
              </details>
            ))}
          </>
        )}
      </main>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value?: number }) {
  return (
    <div className="backtest-kpi">
      <span className="backtest-kpi-label">{label}</span>
      <span className="backtest-kpi-value">{value == null ? '—' : value.toFixed(2)}</span>
    </div>
  );
}

export function BacktestLive() { return <Backtest />; }
```

- [ ] **Step 4: Write `Backtest.css`** using design tokens only (copy variable names from `ScenarioTest.css`):

```css
.backtest-page { display: flex; gap: var(--space-4); height: 100%; }
.backtest-config { width: 240px; border-right: 1px solid var(--border); padding-right: var(--space-3); }
.backtest-config label { display: block; margin: var(--space-2) 0; color: var(--text-muted); font-size: 0.85rem; }
.backtest-run-btn { width: 100%; margin-top: var(--space-3); background: var(--accent); color: var(--accent-contrast); border: none; padding: var(--space-2); border-radius: var(--radius-sm); cursor: pointer; }
.backtest-run-list li { padding: var(--space-1) var(--space-2); cursor: pointer; border-radius: var(--radius-sm); }
.backtest-run-list li.is-selected { background: var(--surface-raised); }
.backtest-report { flex: 1; overflow: auto; }
.backtest-kpis { display: flex; gap: var(--space-2); margin: var(--space-3) 0; flex-wrap: wrap; }
.backtest-kpi { flex: 1; min-width: 90px; background: var(--surface-raised); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-2); }
.backtest-kpi-label { display: block; color: var(--text-muted); font-size: 0.75rem; }
.backtest-kpi-value { font-weight: 600; }
.backtest-error { color: var(--danger); }
.backtest-underlying { margin: var(--space-2) 0; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-2); }
```

> Verify token names against `frontend/UI_STYLE_GUIDE.md` / an existing `*.css` — substitute the real token names if any differ (e.g. `--accent-contrast`). Token purity is a tested invariant; do not introduce hex/rgba.

- [ ] **Step 5: Run → PASS**

Run: `npx vitest run src/routes/Backtest.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Backtest.tsx frontend/src/routes/Backtest.css frontend/src/routes/Backtest.test.tsx
git commit -m "feat(backtest): Backtest page component + recharts report"
```

### Task 4.3: Register the route + sidebar entry

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Import + render in `main.tsx`**

Add near the other route imports (~line 29): `import { BacktestLive } from './routes/Backtest';`
Add near the scenario render line (~line 228): `{route === 'backtest' && <BacktestLive />}`

- [ ] **Step 2: Add the sidebar nav item** in `Sidebar.tsx`

Find the Scenario Test nav entry (`grep -n "scenario-test\|Scenario Test" src/components/Sidebar.tsx`) and add an adjacent item with `id`/`route` `'backtest'`, label `"Backtest"`, in the Risk/Analytics group. Match the exact item shape used there.

- [ ] **Step 3: Typecheck + sidebar test**

Run: `npx tsc --noEmit && npx vitest run src/components/Sidebar.test.tsx`
Expected: PASS (Sidebar test may pin the nav set — update it to include `backtest` if so).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/main.tsx frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.test.tsx
git commit -m "feat(backtest): register Backtest route + sidebar entry"
```

### Task 4.4: End-to-end smoke

**Files:** none (manual/verification)

- [ ] **Step 1: Backend smoke (real akshare backfill, small window)**

Start the backend; `POST /api/backtest/runs` with a portfolio holding a CSI500 snowball and window `2024-01-02..2024-04-30`. Poll `GET /api/backtest/runs/{id}` until `completed`. Expected: `results.portfolio.total_pnl` is finite; `by_underlying` non-empty; `artifacts.dashboards` populated; a `MarketDataProfile` row now exists for the underlying (backfill persisted).

- [ ] **Step 2: Frontend smoke**

Run the app, open the Backtest page, start a run, watch it poll RUNNING→COMPLETED, see KPI cards + P&L chart, open the quant-ark dashboard link. Use the `verify`/`run` skill to drive the app if available.

- [ ] **Step 3: Full suites**

Run: `python -m pytest -q` (backend) and `npx vitest run` (frontend). Expected: green. Fix any catalog-count drift surfaced here.

- [ ] **Step 4: Commit any fixes; then finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to decide merge/PR. Remember: the quant-ark side (Phase 1) must be merged first so the resolved path has the engine.

---

## Self-Review

**Spec coverage** — every spec section maps to a task:
- §4 quant-ark engine → Tasks 1.1–1.7. §5 market history → 2.3. §6 bridge → 2.4. §7 model+migration → 2.1–2.2. §8 runner+pipeline → 2.5–2.6. §9 exports/report → 2.5 (`write_artifacts`, parquet guard) + 3.2 (artifact route) + 4.2 (dashboard link). §10 tools → 3.3. §11 skill+routing → 3.4 (six-file update). §12 REST → 3.2. §13 frontend → 4.1–4.3. §14 edge cases → 2.6 (FAILED path), 2.3 (gaps), 3.2 (404/path-guard). §15 testing → tests in every task. §16 contribution points (a) net-delta rule in 1.5, (b) gap policy in 2.3, (c) risk metrics in 2.5. §17 build sequence → phase order. §18 gotchas → Phase 0 + per-phase notes (PYTHONPATH, pyarrow guard, migration Core-tables, worktrees, token purity).

**Placeholder scan** — refactor Task 1.2 specifies exact method names + signatures + the characterization anchor rather than reproducing 300 lines verbatim (the engineer copies bodies from the existing file; the invariant test guarantees correctness). All *new* code (tests, dataclasses, runner, tools, schemas, component) is shown in full. Helper functions referenced but shown only by signature (`_fetch_akshare_spot`, `_persist_profile`, `_resolve_symbol`, `_shape_underlying`, `write_artifacts`, `ensure_futures_chain`) have explicit prose contracts in their task notes — implement against those.

**Type consistency** — `BookProduct`/`HedgeSpec`/`BookAutocallableBacktestConfig`/`BookBacktestResults`/`BookAutocallableBacktestEngine` names are consistent across 1.3–1.6 and the bridge (2.4) and pipeline (2.5). `BacktestRun` fields (`spec`/`config`/`results`/`artifacts`/`excluded_positions`/`resolved_position_ids`) match across model (2.1), runner (2.6), schema (3.1), and frontend type (4.1). Tool names (`run_backtest`/`get_backtest_run`/`list_backtest_runs`) match across 3.3 and 3.4. REST paths `/api/backtest/*` match across 3.2 and 4.1.

**Known integration points to confirm at execution time (grep, don't guess):** the exact `build_product` import path; `hedging_universe.resolve_hedge_instrument` (or its real equivalent) signature; the quant-ark SSE calendar import; the `ToolGroup`/`capability_gated` members used by `scenario_test`; the exact six catalog-test assertions; the design-token names. Each is called out inline in its task.

---

## Execution Handoff

(See the bottom of this document / chat for the two execution options.)

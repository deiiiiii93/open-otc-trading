# Position pricing perf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `position_pricing` task wall-clock from ~79 min to tens of seconds by adding an adaptive grid chain (`[201, 501, 1001]`) gated on `usable_model_value`, parallelizing the inner loop with a `ThreadPoolExecutor` sized from `risk_parallel_workers`, making greeks reuse the price's winning grid, and surfacing per-position progress in `task_runs`.

**Architecture:** Two new pure helpers (`_resolve_grid_chain`, `_engine_kwargs_with_grid`) drive grid selection inside `_price_position`; `price_portfolio_positions` keeps SQLAlchemy work on the main thread and farms `_price_position` calls to a thread pool, calling a `progress_callback` once per completion. `compute_position_greeks` gains an `engine_kwargs=` kwarg so the price's chosen grid can be reused. The importer stops baking `grid_points: 1001`; an alembic migration strips the same value from existing rows (preserving hand-tuned grids).

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x (sync session), Alembic, pytest, `concurrent.futures.ThreadPoolExecutor`. QuantArk SnowballQuadEngine is imported via `app.services.quantark.price_product`.

**Spec:** `docs/superpowers/specs/2026-05-12-position-pricing-perf-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/position_pricer.py` | Pricing orchestration. Gains two pure helpers, adaptive chain inside `_price_position`, parallel executor + `progress_callback` in `price_portfolio_positions`, and a small `_safe_result` helper for exceptions raised inside worker threads. Also: `execute_position_pricing_task` wires the callback. |
| `backend/app/services/risk_engine.py` | `compute_position_greeks` accepts `engine_kwargs=` and uses the `_position_snapshot` pattern to override per-position engine_kwargs without touching QuantArk. |
| `backend/app/services/position_adapter.py` | `_quad_engine_kwargs()` at line 879 drops the baked-in `{"grid_points": 1001}`. New positions import with no explicit grid. |
| `backend/alembic/versions/0009_strip_default_quad_grid_points.py` | One-shot data migration: strips `grid_points: 1001` from `positions.engine_kwargs.params_kwargs` where `params_type == 'quad_params'`; preserves any other value. SQLite-friendly (pure-Python row scan, no JSONB operators). |
| `tests/test_position_pricer_grid.py` | Unit tests for the two pure helpers. |
| `tests/test_position_pricer_adaptive.py` | Behavioral tests for the adaptive chain inside `_price_position` (six cases A–F). |
| `tests/test_position_pricer_parallel.py` | Integration tests for parallel execution + progress callback. |
| `tests/test_position_pricer_migration.py` | Tests for migration 0009 upgrade/downgrade idempotence. |
| `tests/test_risk_engine.py` (extend) | A new test for `compute_position_greeks(..., engine_kwargs=override)`. |
| `tests/test_position_import_pricing.py` (touch) | Add one assertion that the importer no longer bakes `grid_points`. |

---

## Task 1: Pure helpers — `_resolve_grid_chain` and `_engine_kwargs_with_grid`

**Files:**
- Create: `tests/test_position_pricer_grid.py`
- Modify: `backend/app/services/position_pricer.py` (add helpers near the top, just below the `MarketOverrides` dataclass)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_position_pricer_grid.py`:

```python
from __future__ import annotations

from app.services.position_pricer import (
    _engine_kwargs_with_grid,
    _resolve_grid_chain,
)


def test_resolve_grid_chain_returns_adaptive_chain_when_grid_unset():
    assert _resolve_grid_chain(None) == [201, 501, 1001]
    assert _resolve_grid_chain({}) == [201, 501, 1001]
    assert _resolve_grid_chain({"params_type": "quad_params"}) == [201, 501, 1001]
    assert _resolve_grid_chain({"params_kwargs": {}}) == [201, 501, 1001]


def test_resolve_grid_chain_honors_explicit_grid_points():
    chain = _resolve_grid_chain({"params_kwargs": {"grid_points": 501}})
    assert chain == [501]


def test_resolve_grid_chain_explicit_grid_zero_is_still_honored():
    # Honoring "explicit" means we don't second-guess the caller.
    chain = _resolve_grid_chain({"params_kwargs": {"grid_points": 301}})
    assert chain == [301]


def test_engine_kwargs_with_grid_creates_params_kwargs_when_missing():
    out = _engine_kwargs_with_grid({"params_type": "quad_params"}, 201)
    assert out == {"params_type": "quad_params", "params_kwargs": {"grid_points": 201}}


def test_engine_kwargs_with_grid_preserves_other_params_kwargs_keys():
    inp = {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 1001, "num_std_devs": 5},
    }
    out = _engine_kwargs_with_grid(inp, 201)
    assert out["params_kwargs"]["grid_points"] == 201
    assert out["params_kwargs"]["num_std_devs"] == 5


def test_engine_kwargs_with_grid_does_not_mutate_input():
    inp = {"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}}
    snapshot = {"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}}
    _engine_kwargs_with_grid(inp, 201)
    assert inp == snapshot


def test_engine_kwargs_with_grid_handles_none_input():
    out = _engine_kwargs_with_grid(None, 201)
    assert out == {"params_kwargs": {"grid_points": 201}}


def test_engine_kwargs_with_grid_preserves_top_level_keys_other_than_params():
    inp = {"params_type": "quad_params", "extra": "keep-me"}
    out = _engine_kwargs_with_grid(inp, 501)
    assert out["extra"] == "keep-me"
    assert out["params_kwargs"]["grid_points"] == 501
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_position_pricer_grid.py -v`
Expected: 7 FAILs with `ImportError: cannot import name '_resolve_grid_chain' from 'app.services.position_pricer'`.

- [ ] **Step 3: Add the helpers to `position_pricer.py`**

Open `backend/app/services/position_pricer.py`. Locate the `MarketOverrides` dataclass near the top (around line 37). Below it (still above `SpotFetcher = …` on line 57), add:

```python
ADAPTIVE_GRID_CHAIN: tuple[int, ...] = (201, 501, 1001)


def _resolve_grid_chain(engine_kwargs: dict | None) -> list[int]:
    """Return the grid escalation sequence for one position.

    If ``engine_kwargs["params_kwargs"]["grid_points"]`` is set, returns a
    single-element list with that value (no escalation). Otherwise returns the
    adaptive chain ``[201, 501, 1001]``.
    """
    params_kwargs = (engine_kwargs or {}).get("params_kwargs") or {}
    explicit = params_kwargs.get("grid_points")
    if explicit is not None:
        return [int(explicit)]
    return list(ADAPTIVE_GRID_CHAIN)


def _engine_kwargs_with_grid(engine_kwargs: dict | None, grid: int) -> dict:
    """Return a copy of ``engine_kwargs`` with ``params_kwargs.grid_points = grid``.

    Does not mutate the input. ``params_type`` is preserved verbatim if present
    and is **not** invented when absent. Any other keys (top-level or in
    ``params_kwargs``) are preserved.
    """
    out = dict(engine_kwargs or {})
    pk = dict(out.get("params_kwargs") or {})
    pk["grid_points"] = int(grid)
    out["params_kwargs"] = pk
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_pricer_grid.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_pricer_grid.py backend/app/services/position_pricer.py
git commit -m "feat(pricer): add _resolve_grid_chain and _engine_kwargs_with_grid helpers"
```

---

## Task 2: Adaptive grid escalation inside `_price_position`

**Files:**
- Create: `tests/test_position_pricer_adaptive.py`
- Modify: `backend/app/services/position_pricer.py` (replace the single `price_product` call inside `_price_position` with the chain loop; populate breadcrumbs)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_position_pricer_adaptive.py`. This file uses a tiny stub for `price_product` and constructs `Position`-shaped namespaces that satisfy `_price_position`'s assumptions:

```python
from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.schemas import PricingEnvironmentSnapshot
from app.services import position_pricer
from app.services.position_pricer import MarketOverrides, _price_position
from app.services.quantark import QuantArkResult


def _make_position(*, engine_kwargs: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        portfolio_id=1,
        underlying="000852.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "strike": 100.0,
            "contract_multiplier": 1.0,
        },
        engine_name="SnowballQuadEngine",
        engine_kwargs=engine_kwargs,
        quantity=1.0,
        entry_price=0.0,
        status="open",
        source_trade_id="T1",
        mapping_status="supported",
        mapping_error=None,
        source_payload={"row": {"交易状态": "开仓", "交易规模单位": "CNY"}},
    )


def _make_market() -> PricingEnvironmentSnapshot:
    return PricingEnvironmentSnapshot(
        valuation_date=datetime(2026, 5, 11),
        spot=100.0,
        volatility=0.2,
        rate=0.03,
        dividend_yield=0.0,
        asset_name="000852.SH",
        currency="CNY",
    )


def _call_price_position(position, *, monkeypatch, price_by_grid):
    """Run _price_position with a stubbed price_product that returns
    QuantArkResult(ok=True, data={"price": price_by_grid[grid]}) keyed on the
    grid_points passed in engine_kwargs."""

    calls: list[int] = []

    def fake_price_product(product_type, product_kwargs, market, engine_name, engine_kwargs):
        grid = int((engine_kwargs or {}).get("params_kwargs", {}).get("grid_points", -1))
        calls.append(grid)
        value = price_by_grid.get(grid)
        if value is None:
            return QuantArkResult(ok=False, error=f"no rig for grid {grid}", data={})
        return QuantArkResult(ok=True, data={"price": float(value)})

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", fake_price_product
    )
    # gross_notional_for_position returns a value that, paired with the price,
    # decides usability via usable_model_value. We set a wide-but-finite notional.
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    result = _price_position(
        position=position,
        market_inputs={},
        pricing_rows={},
        valuation_date=datetime(2026, 5, 11),
        overrides=MarketOverrides(spot=100.0, rate=0.03, dividend_yield=0.0, volatility=0.2),
        engine_name=None,
        engine_kwargs=None,
        compute_greeks=False,
        spot_fetcher=lambda symbol, vd: (100.0, {"source": "test"}),
        symbol_spot_cache={},
    )
    return result, calls


def test_case_A_first_grid_usable_stops_immediately(monkeypatch):
    position = _make_position()  # no explicit grid → adaptive chain
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: 50.0, 501: 50.0, 1001: 50.0},
    )
    assert result["ok"] is True
    assert calls == [201]
    assert result["result_payload"]["grid_points_used"] == 201
    assert "attempted_grids" not in result["result_payload"]


def test_case_B_escalates_when_first_grid_implausible(monkeypatch):
    position = _make_position()
    # math.inf at 201 fails usable_model_value; 50.0 at 501 is fine.
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: math.inf, 501: 50.0, 1001: 50.0},
    )
    assert result["ok"] is True
    assert calls == [201, 501]
    assert result["result_payload"]["grid_points_used"] == 501
    assert result["result_payload"]["attempted_grids"] == [201, 501, 1001]


def test_case_C_escalates_to_top_grid(monkeypatch):
    position = _make_position()
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: math.inf, 501: math.inf, 1001: 50.0},
    )
    assert result["ok"] is True
    assert calls == [201, 501, 1001]
    assert result["result_payload"]["grid_points_used"] == 1001
    assert result["result_payload"]["attempted_grids"] == [201, 501, 1001]


def test_case_D_all_grids_fail_returns_implausible_error(monkeypatch):
    position = _make_position()
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: math.inf, 501: math.inf, 1001: math.inf},
    )
    assert result["ok"] is False
    assert result["error_type"] == "pricing"
    assert calls == [201, 501, 1001]
    assert result["result_payload"]["attempted_grids"] == [201, 501, 1001]


def test_case_E_explicit_grid_is_honored_single_attempt(monkeypatch):
    position = _make_position(
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 501}}
    )
    # Stub returns infinity at 501; chain should NOT escalate to 1001.
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={501: math.inf, 1001: 50.0},
    )
    assert result["ok"] is False
    assert calls == [501]
    assert result["result_payload"]["attempted_grids"] == [501]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_position_pricer_adaptive.py -v`
Expected: 5 FAILs. Most will be `KeyError: 'grid_points_used'` or assertions on `calls` length (currently the loop only fires once).

- [ ] **Step 3: Replace the single-attempt block inside `_price_position`**

Open `backend/app/services/position_pricer.py`. Locate the block from `priced = price_product(...)` (around line 542) through the `if not usable_model_value(...)` early-return (around line 596). Replace that entire block with the adaptive chain. The exact rewrite (drop in verbatim):

```python
    grid_chain = _resolve_grid_chain(resolved_engine_kwargs)
    last_priced = None
    attempt_grid: int | None = None
    priced = None  # the accepted result (for fall-through use)
    quantark_price = 0.0
    market_value = 0.0
    gross_notional = 0.0
    attempt_kwargs: dict[str, Any] = {}

    for grid in grid_chain:
        attempt_kwargs = _engine_kwargs_with_grid(resolved_engine_kwargs, grid)
        candidate = price_product(
            position.product_type,
            position.product_kwargs or {},
            market,
            resolved_engine_name,
            attempt_kwargs,
        )
        if not candidate.ok:
            last_priced = candidate
            continue
        candidate_price = float(candidate.data.get("price", 0.0))
        candidate_market_value = candidate_price * float(position.quantity)
        candidate_notional = gross_notional_for_position(position, market)
        if usable_model_value(candidate_market_value, candidate_notional):
            attempt_grid = grid
            priced = candidate
            quantark_price = candidate_price
            market_value = candidate_market_value
            gross_notional = candidate_notional
            break
        last_priced = candidate

    if priced is None:
        # All grids failed — either engine errored on each, or the last numerically
        # produced an implausible market value. Preserve existing error shape.
        if last_priced is not None and not last_priced.ok:
            return _failed(
                position,
                last_priced.error or "QuantArk pricing failed",
                "pricing",
                market_inputs=resolved_market_inputs,
                engine_name=resolved_engine_name,
            ) | {
                "result_payload": {
                    "attempted_grids": grid_chain,
                    "product_type": position.product_type,
                    "engine": resolved_engine_name,
                    "mapping_status": position.mapping_status,
                },
            }
        # Numerically implausible at every grid.
        last_market_value = float(
            (last_priced.data or {}).get("price", 0.0)
        ) * float(position.quantity) if last_priced is not None else 0.0
        last_notional = gross_notional_for_position(position, market)
        return {
            "ok": False,
            "error": (
                f"Model returned implausible market value {last_market_value:.6g}; "
                f"gross notional is {last_notional:.6g}"
            ),
            "error_type": "pricing",
            "price": None,
            "market_value": None,
            "pnl": None,
            "market_inputs": resolved_market_inputs,
            "result_payload": {
                "attempted_grids": grid_chain,
            },
        }
```

Then, directly below this block, keep the existing happy-path that builds `price`, `pnl`, and `result_payload`. Add **two new lines** to `result_payload` after the `priced.data | {...}` literal that constructs it:

```python
    result_payload = priced.data | {
        "unit_price": quantark_price,
        "quantark_price": quantark_price,
        "contract_multiplier": contract_multiplier,
        "position_price": price,
        "quantity": float(position.quantity),
        "gross_notional": gross_notional,
    }
    result_payload["grid_points_used"] = attempt_grid
    if len(grid_chain) > 1:
        result_payload["attempted_grids"] = grid_chain
```

Note: the existing `compute_greeks` block stays in place for now — Task 4 will wire `attempt_kwargs` into it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_pricer_adaptive.py tests/test_position_pricer_grid.py -v`
Expected: 5 + 7 = 12 PASS. Also run the existing pricer integration tests to confirm nothing else broke: `pytest tests/test_position_import_pricing.py -v`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_pricer_adaptive.py backend/app/services/position_pricer.py
git commit -m "feat(pricer): adaptive grid escalation 201->501->1001 in _price_position"
```

---

## Task 3: `compute_position_greeks` accepts `engine_kwargs` override

**Files:**
- Modify: `backend/app/services/risk_engine.py` (extend signature; build snapshot)
- Modify: `tests/test_risk_engine.py` (add a single new test)

- [ ] **Step 1: Write the failing test**

Open `tests/test_risk_engine.py`. Append:

```python
def test_compute_position_greeks_respects_engine_kwargs_override(monkeypatch):
    from types import SimpleNamespace
    from app.services import risk_engine

    captured: dict = {}

    def fake_build_engine_for_position(position, market):
        # Capture what the engine was built from — we will assert the grid override.
        captured["engine_kwargs"] = dict(position.engine_kwargs or {})
        return SimpleNamespace()  # opaque engine

    def fake_build_product_for_position(position, market):
        return SimpleNamespace()

    def fake_build_pricing_env(market):
        return SimpleNamespace()

    class FakeGreeksCalculator:
        def calculate(self, product, env, engine, method):
            return {"delta": 0.5, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
                    "rho": 0.0, "dividend_rho": 0.0}

    # Monkeypatch the GreeksCalculator import target. The function imports it
    # lazily inside the body; intercept by stubbing the quantark builders too.
    monkeypatch.setattr(risk_engine, "build_engine_for_position", fake_build_engine_for_position)
    monkeypatch.setattr(risk_engine, "build_product_for_position", fake_build_product_for_position)
    monkeypatch.setattr(risk_engine, "build_pricing_env", fake_build_pricing_env)
    monkeypatch.setattr(
        "asset.equity.riskmeasures.greeks_calculator.GreeksCalculator",
        FakeGreeksCalculator,
        raising=False,
    )

    position = SimpleNamespace(
        product_type="SnowballOption",
        product_kwargs={},
        engine_name="SnowballQuadEngine",
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}},
        quantity=1.0,
        status="open",
        mapping_status="supported",
        mapping_error=None,
        source_payload={},
    )
    market = SimpleNamespace()

    out = risk_engine.compute_position_greeks(
        position,
        market,
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 201}},
    )

    assert out["ok"] is True
    assert captured["engine_kwargs"]["params_kwargs"]["grid_points"] == 201
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk_engine.py::test_compute_position_greeks_respects_engine_kwargs_override -v`
Expected: FAIL — `TypeError: compute_position_greeks() got an unexpected keyword argument 'engine_kwargs'`.

- [ ] **Step 3: Extend `compute_position_greeks` signature**

Open `backend/app/services/risk_engine.py`. Find the function definition at line 56:

```python
def compute_position_greeks(
    position: Position,
    market: PricingEnvironmentSnapshot,
) -> dict[str, Any]:
    """Return per-position Greeks dict. {ok, delta, gamma, vega, theta, rho, rho_q, error?}."""
    ensure_quantark_path()
    try:
        from asset.equity.riskmeasures.greeks_calculator import GreeksCalculator
    except Exception as exc:  # pragma: no cover - import guard
        ...
    try:
        product = build_product_for_position(position, market)
        engine = build_engine_for_position(position, market)
        env = build_pricing_env(market)
        ...
```

Replace the signature and the two `build_*_for_position` calls so they use a position-shaped snapshot when `engine_kwargs` is overridden. The full replacement for lines 56–100 (function body):

```python
def compute_position_greeks(
    position: Position,
    market: PricingEnvironmentSnapshot,
    *,
    engine_kwargs: dict | None = None,
) -> dict[str, Any]:
    """Return per-position Greeks dict. {ok, delta, gamma, vega, theta, rho, rho_q, error?}.

    When ``engine_kwargs`` is provided it overrides ``position.engine_kwargs`` for the
    duration of this call (the position object is not mutated).
    """
    ensure_quantark_path()
    try:
        from asset.equity.riskmeasures.greeks_calculator import GreeksCalculator
    except Exception as exc:  # pragma: no cover - import guard
        return {
            "ok": False,
            "error": f"GreeksCalculator unavailable: {exc}",
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }
    try:
        target = position
        if engine_kwargs is not None:
            target = _position_snapshot(position)
            target.engine_kwargs = dict(engine_kwargs)
        product = build_product_for_position(target, market)
        engine = build_engine_for_position(target, market)
        env = build_pricing_env(market)
        calc = GreeksCalculator()
        result = calc.calculate(product, env, engine, method="auto")
        return {
            "ok": True,
            "delta": float(result.get("delta", 0.0)),
            "gamma": float(result.get("gamma", 0.0)),
            "vega": float(result.get("vega", 0.0)),
            "theta": float(result.get("theta", 0.0)),
            "rho": float(result.get("rho", 0.0)),
            "rho_q": float(result.get("dividend_rho", 0.0)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_risk_engine.py -v`
Expected: all green, including the new test. Run the full risk engine suite to confirm nothing regressed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_risk_engine.py backend/app/services/risk_engine.py
git commit -m "feat(risk): compute_position_greeks accepts engine_kwargs override"
```

---

## Task 4: Wire greeks reuse in `_price_position` (case F)

**Files:**
- Modify: `tests/test_position_pricer_adaptive.py` (add case F)
- Modify: `backend/app/services/position_pricer.py` (pass `attempt_kwargs` into `compute_position_greeks`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_position_pricer_adaptive.py`:

```python
def test_case_F_greeks_reuse_winning_grid(monkeypatch):
    position = _make_position()

    # Stub price_product so grid=201 fails, grid=501 succeeds.
    def fake_price_product(product_type, product_kwargs, market, engine_name, engine_kwargs):
        grid = int((engine_kwargs or {}).get("params_kwargs", {}).get("grid_points", -1))
        if grid == 201:
            return QuantArkResult(ok=True, data={"price": float("inf")})  # implausible
        if grid == 501:
            return QuantArkResult(ok=True, data={"price": 50.0})
        return QuantArkResult(ok=False, error="not rigged", data={})

    captured_greeks_kwargs: dict = {}

    def fake_compute_greeks(position, market, *, engine_kwargs=None):
        captured_greeks_kwargs["engine_kwargs"] = dict(engine_kwargs or {})
        return {"ok": True, "delta": 0.5, "gamma": 0.0, "vega": 0.0,
                "theta": 0.0, "rho": 0.0, "rho_q": 0.0}

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", fake_price_product
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )
    monkeypatch.setattr(
        "app.services.position_pricer.compute_position_greeks", fake_compute_greeks
    )

    result = _price_position(
        position=position,
        market_inputs={},
        pricing_rows={},
        valuation_date=datetime(2026, 5, 11),
        overrides=MarketOverrides(spot=100.0, rate=0.03, dividend_yield=0.0, volatility=0.2),
        engine_name=None,
        engine_kwargs=None,
        compute_greeks=True,
        spot_fetcher=lambda symbol, vd: (100.0, {"source": "test"}),
        symbol_spot_cache={},
    )

    assert result["ok"] is True
    assert result["result_payload"]["grid_points_used"] == 501
    assert captured_greeks_kwargs["engine_kwargs"]["params_kwargs"]["grid_points"] == 501
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_position_pricer_adaptive.py::test_case_F_greeks_reuse_winning_grid -v`
Expected: FAIL — `KeyError: 'engine_kwargs'` or `TypeError: compute_position_greeks() got an unexpected keyword argument 'engine_kwargs'` (because position_pricer.py still calls it positionally).

- [ ] **Step 3: Pass `attempt_kwargs` into `compute_position_greeks`**

Open `backend/app/services/position_pricer.py`. Find the `if compute_greeks:` block (around line 572 after Task 2's edits). Replace the call:

```python
    if compute_greeks:
        greeks = compute_position_greeks(position, market)
```

with:

```python
    if compute_greeks:
        greeks = compute_position_greeks(position, market, engine_kwargs=attempt_kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_position_pricer_adaptive.py -v`
Expected: 6 PASS (cases A–F).

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_pricer_adaptive.py backend/app/services/position_pricer.py
git commit -m "feat(pricer): greeks reuse the price's winning grid"
```

---

## Task 5: Stop the importer from baking `grid_points: 1001`

**Files:**
- Modify: `backend/app/services/position_adapter.py:879`
- Modify: `tests/test_position_import_pricing.py` (add a focused assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_position_import_pricing.py`:

```python
def test_quad_engine_kwargs_no_longer_bakes_grid_points():
    from app.services.position_adapter import _quad_engine_kwargs

    kwargs = _quad_engine_kwargs()
    assert kwargs == {"params_type": "quad_params"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_position_import_pricing.py::test_quad_engine_kwargs_no_longer_bakes_grid_points -v`
Expected: FAIL — assertion mismatches, the function currently returns the dict with `params_kwargs.grid_points=1001`.

- [ ] **Step 3: Edit `_quad_engine_kwargs`**

Open `backend/app/services/position_adapter.py`. Line 879 currently reads:

```python
def _quad_engine_kwargs() -> dict[str, Any]:
    return {"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}}
```

Replace with:

```python
def _quad_engine_kwargs() -> dict[str, Any]:
    # Intentionally no params_kwargs. The pricer's adaptive chain picks a grid at
    # call time; baking grid_points here would suppress that escalation.
    return {"params_type": "quad_params"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_import_pricing.py -v`
Expected: all green, including the new assertion.

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_import_pricing.py backend/app/services/position_adapter.py
git commit -m "feat(adapter): stop baking grid_points: 1001 into snowball engine_kwargs"
```

---

## Task 6: Add `progress_callback` plumbing to `price_portfolio_positions` (still serial)

**Files:**
- Create: `tests/test_position_pricer_parallel.py` (we'll grow this in Task 7)
- Modify: `backend/app/services/position_pricer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_position_pricer_parallel.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app import database
from app.config import Settings, configure_settings
from app.models import (
    Portfolio,
    PortfolioKind,
    Position,
    PositionValuationResult,
)
from app.services.position_pricer import price_portfolio_positions
from app.services.quantark import QuantArkResult


def _seed_portfolio(session, n: int) -> Portfolio:
    portfolio = Portfolio(name="ProgressTest", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()
    for i in range(n):
        session.add(Position(
            portfolio_id=portfolio.id,
            underlying="000852.SH",
            product_type="SnowballOption",
            product_kwargs={
                "initial_price": 100.0,
                "strike": 100.0,
                "contract_multiplier": 1.0,
            },
            engine_name="SnowballQuadEngine",
            engine_kwargs={"params_type": "quad_params"},  # no explicit grid
            quantity=1.0,
            entry_price=0.0,
            status="open",
            source_trade_id=f"T{i}",
            mapping_status="supported",
        ))
    session.flush()
    return portfolio


@pytest.fixture
def session(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    url = f"sqlite+pysqlite:///{db_path}"
    configure_settings(Settings(database_url=url))
    database.configure_database(url)
    database.Base.metadata.create_all(database._engine)
    with database.session_scope() as s:
        yield s
    configure_settings(None)


def test_progress_callback_invoked_per_position(monkeypatch, session):
    portfolio = _seed_portfolio(session, n=4)

    monkeypatch.setattr(
        "app.services.position_pricer.price_product",
        lambda *a, **k: QuantArkResult(ok=True, data={"price": 50.0}),
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    progress: list[tuple[int, int]] = []
    price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        overrides=None,
        progress_callback=lambda current, total: progress.append((current, total)),
    )
    # Expect N+1 invocations: (0, 4), (1, 4), (2, 4), (3, 4), (4, 4)
    assert progress[0] == (0, 4)
    assert progress[-1] == (4, 4)
    assert len(progress) == 5
```

If your project's `database.session_scope` or `configure_database` differ from the names above, look in `backend/app/database.py` and adjust the fixture accordingly (the names should already be similar — `database._engine`, `database.SessionLocal`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_position_pricer_parallel.py::test_progress_callback_invoked_per_position -v`
Expected: FAIL — `TypeError: price_portfolio_positions() got an unexpected keyword argument 'progress_callback'`.

- [ ] **Step 3: Add the parameter and call it**

Open `backend/app/services/position_pricer.py`. Modify `price_portfolio_positions`:

1. Add `progress_callback: Callable[[int, int], None] | None = None` as a kwarg at the end of the signature.
2. Inside the function, immediately after resolving `positions` and before the `for position in positions:` loop, add:

```python
    total = len(positions)
    if progress_callback is not None:
        progress_callback(0, total)
```

3. Inside the loop, after the `session.add(valuation_result)` line, append:

```python
        if progress_callback is not None:
            progress_callback(totals["positions"], total)
```

The loop is still serial — parallelism arrives in Task 7.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_position_pricer_parallel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_pricer_parallel.py backend/app/services/position_pricer.py
git commit -m "feat(pricer): progress_callback hook in price_portfolio_positions"
```

---

## Task 7: ThreadPoolExecutor over `_price_position`

**Files:**
- Modify: `tests/test_position_pricer_parallel.py` (add a parallelism test and an exception test)
- Modify: `backend/app/services/position_pricer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_position_pricer_parallel.py`:

```python
import threading

from app.services.position_pricer import _price_position  # noqa: F401  (referenced indirectly)


def test_inner_loop_uses_multiple_threads(monkeypatch, session):
    portfolio = _seed_portfolio(session, n=4)
    configure_settings(Settings(database_url=session.bind.url.render_as_string(hide_password=False),
                                risk_parallel_workers=4))

    thread_ids: set[int] = set()

    def fake_price_product(*a, **k):
        thread_ids.add(threading.get_ident())
        # small artificial delay so workers actually overlap
        import time; time.sleep(0.05)
        return QuantArkResult(ok=True, data={"price": 50.0})

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", fake_price_product
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    price_portfolio_positions(session, portfolio_id=portfolio.id)
    assert len(thread_ids) >= 2, f"expected >=2 worker threads, got {thread_ids}"


def test_one_position_exception_does_not_stop_others(monkeypatch, session):
    portfolio = _seed_portfolio(session, n=3)

    call_count = {"n": 0}

    def flaky_price_product(*a, **k):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated pricing crash")
        return QuantArkResult(ok=True, data={"price": 50.0})

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", flaky_price_product
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    progress: list[tuple[int, int]] = []
    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        progress_callback=lambda c, t: progress.append((c, t)),
    )
    assert progress[-1] == (3, 3)
    results = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).all()
    assert len(results) == 3
    assert sum(1 for r in results if not r.ok) == 1
    assert sum(1 for r in results if r.ok) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_position_pricer_parallel.py -v`
Expected: `test_inner_loop_uses_multiple_threads` FAILs with `assert 1 >= 2` (only the main thread is recorded). `test_one_position_exception_does_not_stop_others` is likely to fail because the exception currently terminates `_price_position` (still surfaced via the existing try/except, so this one may actually pass — but we'll re-verify after Task 7 lands).

- [ ] **Step 3: Replace the serial loop with a thread pool**

Open `backend/app/services/position_pricer.py`. Add to the imports at the top:

```python
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
```

and ensure `get_settings` is imported (add it to the existing import line or as a new one):

```python
from ..config import get_settings
```

Then locate the `for position in positions:` loop inside `price_portfolio_positions` (introduced after market_inputs resolution). Replace the entire loop with:

```python
    worker_count = max(1, min(get_settings().risk_parallel_workers, len(positions) or 1))
    results_by_id: dict[int, dict[str, Any]] = {}

    def _safe_result(future: Future, position: Position) -> dict[str, Any]:
        try:
            return future.result()
        except Exception as exc:  # pragma: no cover - surfaced via logged-failure path
            logger.exception(
                "Unexpected position pricing failure: portfolio_id=%s position_id=%s source_trade_id=%s",
                portfolio_id,
                position.id,
                position.source_trade_id,
            )
            return _failed(
                position,
                f"Unexpected pricing error: {exc}",
                "pricing",
                engine_name=engine_name or position.engine_name,
            )

    with ThreadPoolExecutor(
        max_workers=worker_count, thread_name_prefix="open-otc-pricer"
    ) as pool:
        future_to_position: dict[Future, Position] = {
            pool.submit(
                _price_position,
                position=position,
                market_inputs=market_inputs,
                pricing_rows=pricing_rows,
                valuation_date=valuation_date,
                overrides=overrides,
                engine_name=engine_name,
                engine_kwargs=engine_kwargs,
                compute_greeks=compute_greeks,
                spot_fetcher=spot_fetcher,
                symbol_spot_cache=symbol_spot_cache,
            ): position
            for position in positions
        }
        done = 0
        for fut in as_completed(future_to_position):
            position = future_to_position[fut]
            results_by_id[position.id] = _safe_result(fut, position)
            done += 1
            if progress_callback is not None:
                progress_callback(done, total)

    for position in positions:
        result = results_by_id[position.id]
        totals["positions"] += 1
        valuation_result = PositionValuationResult(
            valuation_run_id=run.id,
            position_id=position.id,
            source_trade_id=position.source_trade_id,
            ok=result["ok"],
            price=result.get("price"),
            market_value=result.get("market_value"),
            pnl=result.get("pnl"),
            market_inputs=result.get("market_inputs", {}),
            result_payload=result.get("result_payload", {}),
            error=result.get("error"),
        )
        session.add(valuation_result)
        if result["ok"]:
            totals["priced"] += 1
            totals["market_value"] += float(result.get("market_value") or 0.0)
            totals["pnl"] += float(result.get("pnl") or 0.0)
        else:
            totals["failed"] += 1
            if result.get("error_type") == "unsupported":
                totals["unsupported"] += 1
```

**Critical:** delete the original try/except + sequential aggregation that previously lived inside the loop. The new structure handles exceptions inside `_safe_result` and aggregates outside the parallel section. Also delete the in-loop `progress_callback` call added in Task 6 — it's now invoked from inside `as_completed`.

Also add to the top of `price_portfolio_positions`:

```python
    # (`get_settings` is imported at module level — no local import needed)
```

(If you prefer to scope the import locally instead, drop it inside the function body.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_pricer_parallel.py tests/test_position_pricer_adaptive.py tests/test_position_pricer_grid.py tests/test_position_import_pricing.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_pricer_parallel.py backend/app/services/position_pricer.py
git commit -m "feat(pricer): parallelize price_portfolio_positions inner loop"
```

---

## Task 8: Wire callback into `execute_position_pricing_task`

**Files:**
- Modify: `backend/app/services/position_pricer.py` (function `execute_position_pricing_task`)
- Modify: `tests/test_position_pricer_parallel.py` (add a task-level integration test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_position_pricer_parallel.py`:

```python
def test_execute_position_pricing_task_updates_progress(monkeypatch, session):
    from app.services.position_pricer import (
        execute_position_pricing_task,
        queue_position_pricing,
    )
    from app.models import TaskRun

    portfolio = _seed_portfolio(session, n=3)
    monkeypatch.setattr(
        "app.services.position_pricer.price_product",
        lambda *a, **k: QuantArkResult(ok=True, data={"price": 50.0}),
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    task = queue_position_pricing(session, portfolio_id=portfolio.id)
    session.flush()
    task_id = task.id
    session.commit()

    # Run the task synchronously, sharing the same session factory.
    def factory():
        return session
    execute_position_pricing_task(
        task_id=task_id,
        portfolio_id=portfolio.id,
        session_factory=factory,
    )
    session.commit()

    final = session.get(TaskRun, task_id)
    assert final.status == "completed"
    assert final.progress_total == 3
    assert final.progress_current == 3
    assert final.message.startswith("Pricing run #")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_position_pricer_parallel.py::test_execute_position_pricing_task_updates_progress -v`
Expected: FAIL — `progress_total == 1`, not 3 (current code calls `mark_task_running(... total=1)` and only updates at the end).

- [ ] **Step 3: Wire the callback**

Open `backend/app/services/position_pricer.py`. Locate `execute_position_pricing_task`:

Replace the body's `try` block content (currently `mark_task_running(... total=1)` → `price_portfolio_positions(...)` → `update_task_progress(... current=1, total=1)`) with:

```python
    session = session_factory()
    try:
        mark_task_running(
            session, task_id, message="Resolving positions for pricing", total=0
        )
        session.commit()

        def progress_cb(current: int, total: int) -> None:
            update_task_progress(
                session,
                task_id,
                current=current,
                total=total,
                message=(
                    f"Priced {current}/{total} positions" if total else "Resolving positions"
                ),
            )
            session.commit()

        run = price_portfolio_positions(
            session,
            portfolio_id=portfolio_id,
            position_ids=position_ids,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            valuation_date=valuation_date,
            overrides=overrides,
            engine_name=engine_name,
            engine_kwargs=engine_kwargs,
            compute_greeks=compute_greeks,
            progress_callback=progress_cb,
        )

        mark_task_finished(
            session,
            task_id,
            status=(
                TaskStatus.COMPLETED.value
                if run.status == "completed"
                else TaskStatus.COMPLETED_WITH_ERRORS.value
            ),
            message=f"Pricing run #{run.id} {run.status.replace('_', ' ')}",
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        try:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                message="Pricing run failed",
                error=str(exc),
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Failed to mark pricing task failed: task_id=%s", task_id)
        logger.exception(
            "Position pricing task failed: task_id=%s portfolio_id=%s",
            task_id,
            portfolio_id,
        )
    finally:
        session.close()
```

(The `except` and `finally` blocks above are copied verbatim from the original function — paste them unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_pricer_parallel.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_position_pricer_parallel.py backend/app/services/position_pricer.py
git commit -m "feat(pricer): live progress reporting from execute_position_pricing_task"
```

---

## Task 9: Alembic migration `0009_strip_default_quad_grid_points`

**Files:**
- Create: `backend/alembic/versions/0009_strip_default_quad_grid_points.py`
- Create: `tests/test_position_pricer_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_position_pricer_migration.py`:

```python
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPO_ROOT / "backend/alembic/versions/0009_strip_default_quad_grid_points.py"
)


def _load_migration():
    """Import the migration module by file path (leading-digit module names
    can't be imported by dotted notation)."""
    spec = importlib.util.spec_from_file_location("_mig0009", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def positions_engine(tmp_path: Path):
    db_path = tmp_path / "mig.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            """
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY,
                portfolio_id INTEGER NOT NULL,
                underlying VARCHAR(80) NOT NULL,
                product_type VARCHAR(120) NOT NULL,
                product_kwargs JSON NOT NULL,
                engine_name VARCHAR(120) NOT NULL,
                engine_kwargs JSON NOT NULL,
                quantity FLOAT NOT NULL,
                entry_price FLOAT NOT NULL,
                status VARCHAR(40) NOT NULL,
                source_trade_id VARCHAR(160),
                source_row INTEGER,
                mapping_status VARCHAR(40) NOT NULL,
                mapping_error TEXT,
                source_payload JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                trade_effective_date DATETIME
            )
            """
        ))
    yield engine


def _insert(engine, *, id, engine_kwargs):
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO positions(id, portfolio_id, underlying, product_type, "
            "product_kwargs, engine_name, engine_kwargs, quantity, entry_price, "
            "status, mapping_status, created_at, updated_at) VALUES "
            "(:id, 1, 'X', 'SnowballOption', '{}', 'E', :ek, 1.0, 0.0, 'open', 'supported', "
            "'2026-01-01', '2026-01-01')"
        ), {"id": id, "ek": json.dumps(engine_kwargs)})


def _engine_kwargs(engine, *, id):
    with engine.begin() as conn:
        ek = conn.execute(
            sa.text("SELECT engine_kwargs FROM positions WHERE id=:id"), {"id": id}
        ).scalar()
    return json.loads(ek)


def _run(positions_engine, fn_name: str):
    mig = _load_migration()
    # The migration uses op.get_bind(), so we need to push an alembic context.
    from alembic.migration import MigrationContext
    with positions_engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        from alembic import op as _op
        _op._proxy = type("Proxy", (), {
            "get_bind": staticmethod(lambda: conn),
            "get_context": staticmethod(lambda: ctx),
        })()  # minimal shim so op.get_bind() works without alembic env
        getattr(mig, fn_name)()


def test_migration_strips_default_grid_points_only(positions_engine):
    _insert(positions_engine, id=1,
            engine_kwargs={"params_type": "quad_params",
                           "params_kwargs": {"grid_points": 1001}})
    _insert(positions_engine, id=2,
            engine_kwargs={"params_type": "quad_params",
                           "params_kwargs": {"grid_points": 501}})
    _insert(positions_engine, id=3,
            engine_kwargs={"params_type": "mc_params",
                           "params_kwargs": {"num_paths": 100000}})

    _run(positions_engine, "upgrade")

    assert _engine_kwargs(positions_engine, id=1) == {"params_type": "quad_params"}
    assert _engine_kwargs(positions_engine, id=2) == {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 501},
    }
    assert _engine_kwargs(positions_engine, id=3) == {
        "params_type": "mc_params",
        "params_kwargs": {"num_paths": 100000},
    }


def test_migration_downgrade_restores_grid_points_1001(positions_engine):
    _insert(positions_engine, id=1,
            engine_kwargs={"params_type": "quad_params"})

    _run(positions_engine, "downgrade")

    assert _engine_kwargs(positions_engine, id=1) == {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 1001},
    }
```

If the `_op._proxy` shim feels hacky, an alternative is to call the migration via a real alembic configuration. The shim approach above is the lightest path that exercises the same code path the real migration takes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_position_pricer_migration.py -v`
Expected: FAIL — migration file does not exist yet.

- [ ] **Step 3: Create the migration**

Create `backend/alembic/versions/0009_strip_default_quad_grid_points.py`:

```python
"""strip default quad grid_points

Revision ID: 0009_strip_default_quad_grid_points
Revises: 0008_task_runs
Create Date: 2026-05-12
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0009_strip_default_quad_grid_points"
down_revision = "0008_task_runs"
branch_labels = None
depends_on = None


def _load(value):
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, engine_kwargs FROM positions")).fetchall()
    for pid, raw in rows:
        kw = _load(raw)
        if not isinstance(kw, dict):
            continue
        if kw.get("params_type") != "quad_params":
            continue
        pk = kw.get("params_kwargs") or {}
        if not isinstance(pk, dict) or pk.get("grid_points") != 1001:
            continue
        pk.pop("grid_points", None)
        if pk:
            kw["params_kwargs"] = pk
        else:
            kw.pop("params_kwargs", None)
        bind.execute(
            text("UPDATE positions SET engine_kwargs = :ek WHERE id = :id"),
            {"ek": json.dumps(kw), "id": pid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, engine_kwargs FROM positions")).fetchall()
    for pid, raw in rows:
        kw = _load(raw)
        if not isinstance(kw, dict):
            continue
        if kw.get("params_type") != "quad_params":
            continue
        pk = kw.get("params_kwargs") or {}
        if "grid_points" in pk:
            continue
        pk["grid_points"] = 1001
        kw["params_kwargs"] = pk
        bind.execute(
            text("UPDATE positions SET engine_kwargs = :ek WHERE id = :id"),
            {"ek": json.dumps(kw), "id": pid},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_pricer_migration.py -v`
Expected: 2 PASS.

Also apply the migration to the running dev database to verify it works in practice:

```bash
alembic upgrade head
```

Then spot-check via:

```bash
sqlite3 data/open_otc.sqlite3 "SELECT COUNT(*) FROM positions WHERE json_extract(engine_kwargs, '\$.params_kwargs.grid_points') = 1001;"
```

Expected: `0`.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0009_strip_default_quad_grid_points.py tests/test_position_pricer_migration.py
git commit -m "feat(alembic): strip default grid_points=1001 from existing positions"
```

---

## Final verification

After all nine tasks land, run the full suite and a real end-to-end pricing task to confirm the speedup. This is one focused verification block, not a task:

- [ ] **Run the full test suite**

```bash
pytest -q
```

Expected: all green.

- [ ] **Re-run task #4-equivalent end-to-end**

In a fresh shell:

```bash
sqlite3 data/open_otc.sqlite3 "INSERT INTO task_runs (kind, status, portfolio_id, progress_current, progress_total, message, created_at) VALUES ('position_pricing', 'queued', 6, 0, 0, 'Manual re-run', datetime('now'));"
```

Or trigger via the API the same way the original task was queued. Watch:

```bash
sqlite3 data/open_otc.sqlite3 "SELECT id, status, progress_current, progress_total, message FROM task_runs WHERE kind='position_pricing' ORDER BY id DESC LIMIT 1;"
```

Expected: `progress_current / progress_total` advances live; final wall-clock should land in the 30-90 s range with `risk_parallel_workers` at its default and `pricing_parameter_profile_id=1` carrying market inputs as before.

- [ ] **Inspect a sample result for breadcrumbs**

```bash
sqlite3 data/open_otc.sqlite3 "SELECT json_extract(result_payload, '\$.grid_points_used'), json_extract(result_payload, '\$.attempted_grids') FROM position_valuation_results WHERE valuation_run_id = (SELECT MAX(id) FROM position_valuation_runs WHERE portfolio_id = 6) LIMIT 5;"
```

Expected: `grid_points_used` is `201` for ~all well-behaved snowballs; `attempted_grids` is `NULL` (single-attempt fast path).

# Backend Greeks/Scenario + Audit (Plan 5b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the backend `/api/risk/runs` endpoint to return real Γ, V, θ, ρ alongside Δ (using QuantArk's `GreeksCalculator`); add a `/api/risk/scenarios` endpoint that runs a shift × vol matrix; expose audit events via a new `/api/audit/events` listing endpoint. Then unblock the deferred frontend panels (`GreeksSummary`, `ScenarioGrid`) so they render live data instead of placeholders.

**Architecture:** A new `backend/app/services/risk_engine.py` module wraps QuantArk's `GreeksCalculator` per position and aggregates totals. `calculate_portfolio_risk()` in `services/quantark.py` is extended to call into it (keeping the function signature stable so existing call sites continue to work). A new `run_portfolio_scenarios()` function in `services/risk_engine.py` reprices the portfolio under a grid of (spot_shift, vol_shift) tuples and returns a 2D PnL matrix. The audit endpoint is a thin SELECT with optional filters (`subject_type`, `event_type`, `since`) and a default `limit=100` LIFO. Frontend changes wire `GreeksSummary` to extended totals and `ScenarioGrid` to a second `/api/risk/scenarios` POST.

**Tech Stack:** QuantArk's `GreeksCalculator` (from `asset.equity.riskmeasures.greeks_calculator`) — already available via `pip install -e /Users/fuxinyao/quant-ark`. FastAPI · SQLAlchemy · pytest (existing). React + axe-core (existing).

**Foundation:** Plan 5a shipped at f110905. All 6 routes Warm Ledger; primitives a11y-clean; dark-mode tokenised; motion catch-all verified.

**Branch:** continues on `main` (user's standing consent).

## Pre-investigation findings (drives the plan)

**Backend reality:**
- `services/quantark.py:440` — `calculate_portfolio_risk()` currently returns `{"totals": {market_value, delta_proxy, gross_notional, pnl, one_day_var_proxy}, "positions": [...]}`. Only `delta_proxy = quantity * multiplier` (a notional shortcut, not a real delta).
- `services/quantark.py:430` — `price_product()` already constructs `engine` and `pricing_env` using QuantArk's `build_pricing_env_from_market_kwargs` and `build_engine_from_termsheet`. Greeks calculator needs the same triple `(product, pricing_env, engine)`.
- QuantArk source: `/Users/fuxinyao/quant-ark/asset/equity/riskmeasures/greeks_calculator.py` — `GreeksCalculator().calculate(product, env, engine, method="auto")` returns `{price, delta, gamma, vega, theta, rho}` for vanilla; bump method covers all other product types.
- `services/audit.py` — only writes events; no read path. `AuditEventOut` schema already exists in `schemas.py:386`.
- Existing endpoints in `main.py:562-580` — pattern: depends on `Session = Depends(get_db)`, returns Pydantic schema, calls `record_audit` then `session.commit()`.

**Frontend reality:**
- `components/GreeksSummary.tsx:51-53` — has hardcoded "Γ · V · θ · ρ — pending backend Greeks extension (deferred to Plan 5)". The `GreeksTotals` type is exported and consumed by `Risk.tsx`.
- `components/ScenarioGrid.tsx` — fully placeholder; renders an empty 6×3 matrix with an overlay reading "Scenario engine deferred". `onPromoteToReport` is wired but disabled.
- `routes/Risk.live.tsx` — wires `RiskMetrics` to `Risk.tsx`. Scenario data not fetched at all today.

**Decision: keep `delta_proxy` as a separate field**. The frontend already reads `totals.delta_proxy`. Renaming to `delta` would break the existing component contract. Plan 5b adds the new analytical Greeks as ADDITIONAL fields (`delta`, `gamma`, `vega`, `theta`, `rho`) and leaves `delta_proxy` in place. The frontend prefers `delta` when present, falls back to `delta_proxy`.

**Decision: scenario matrix is `(spot_shift_pct, vol_shift_abs) → portfolio_pnl_delta`**. 6 spot columns × 3 vol rows = 18 reprices. With ~10-position portfolios this is fast enough for synchronous response. No streaming needed.

**Decision: audit endpoint is read-only listing**. No filtering by date range in v1 — just `event_type`, `subject_type`, `subject_id` query params and a fixed `limit` of 100 most recent events. Pagination deferred.

---

## File structure

**New files:**
- `backend/app/services/risk_engine.py` — `compute_position_greeks()`, `compute_portfolio_greeks()`, `run_portfolio_scenarios()`
- `tests/test_risk_engine.py` — pytest tests for the new service module
- `tests/test_audit_endpoint.py` — pytest tests for `/api/audit/events`
- `tests/test_scenarios_endpoint.py` — pytest tests for `/api/risk/scenarios`

**Modified files:**
- `backend/app/services/quantark.py` — `calculate_portfolio_risk()` calls into `risk_engine.compute_portfolio_greeks()` and merges the result into `totals`
- `backend/app/schemas.py` — add `ScenarioRunRequest`, `ScenarioRunOut`, `ScenarioCell` schemas
- `backend/app/main.py` — add `POST /api/risk/scenarios`, `GET /api/audit/events` endpoints
- `frontend/src/components/GreeksSummary.tsx` — extend `GreeksTotals` with `delta`, `gamma`, `vega`, `theta`, `rho`; replace deferred banner with 4 new tiles when present
- `frontend/src/components/GreeksSummary.test.tsx` — add tests for live Γ/V/θ/ρ rendering
- `frontend/src/components/ScenarioGrid.tsx` — accept `cells: ScenarioCell[][]`, render PnL values, remove deferred overlay when populated
- `frontend/src/components/ScenarioGrid.test.tsx` — add tests for live grid rendering
- `frontend/src/routes/Risk.tsx` — pass scenario cells through to ScenarioGrid
- `frontend/src/routes/Risk.live.tsx` — fetch `/api/risk/scenarios` after a successful Run Risk
- `README.md` — mark Plan 5b done

---

# Phase A · Backend risk engine extension

## Task 1: Add `services/risk_engine.py` with `compute_position_greeks()`

**Files:**
- Create: `backend/app/services/risk_engine.py`
- Create: `tests/test_risk_engine.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/tests/test_risk_engine.py`:

```python
from __future__ import annotations

import pytest

from app.config import get_settings
from app.models import Portfolio, Position
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import ensure_quantark_path
from app.services.risk_engine import compute_position_greeks, compute_portfolio_greeks


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path(get_settings())


def _vanilla_position(qty: float = 100.0) -> Position:
    return Position(
        id=1,
        portfolio_id=1,
        underlying="CSI500",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=qty,
        entry_price=8.0,
    )


def test_compute_position_greeks_returns_full_set_for_vanilla():
    position = _vanilla_position()
    result = compute_position_greeks(position, PricingEnvironmentSnapshot())
    assert result["ok"] is True
    for key in ("delta", "gamma", "vega", "theta", "rho"):
        assert key in result
        assert isinstance(result[key], float)
    # ATM call delta is positive and < 1
    assert 0.0 < result["delta"] < 1.0


def test_compute_portfolio_greeks_aggregates_signed_quantities():
    long = _vanilla_position(qty=100.0)
    short = _vanilla_position(qty=-50.0)
    short.id = 2
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=[long, short])
    aggregated = compute_portfolio_greeks(portfolio, PricingEnvironmentSnapshot())
    # net delta should equal long_delta * 100 + short_delta * -50 = (long_delta) * 50
    assert aggregated["delta"] == pytest.approx(0.5 * (compute_position_greeks(long, PricingEnvironmentSnapshot())["delta"] * 100), rel=1e-9)
```

- [ ] **Step 2: Run, verify it fails with module not found**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_risk_engine.py -v 2>&1 | tail -10
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.risk_engine'`.

- [ ] **Step 3: Implement `risk_engine.py`**

Create `/Users/fuxinyao/open-otc-trading/backend/app/services/risk_engine.py`:

```python
from __future__ import annotations

from typing import Any

from ..models import Portfolio, Position
from ..schemas import PricingEnvironmentSnapshot
from .quantark import (
    build_engine_for_position,
    build_pricing_env,
    build_product_for_position,
    ensure_quantark_path,
)


def compute_position_greeks(
    position: Position,
    market: PricingEnvironmentSnapshot,
) -> dict[str, Any]:
    """Return per-position Greeks dict. {ok, delta, gamma, vega, theta, rho, error?}."""
    ensure_quantark_path()
    try:
        from asset.equity.riskmeasures.greeks_calculator import GreeksCalculator
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "error": f"GreeksCalculator unavailable: {exc}",
                "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
    try:
        product = build_product_for_position(position)
        engine = build_engine_for_position(position)
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
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}


def compute_portfolio_greeks(
    portfolio: Portfolio,
    market: PricingEnvironmentSnapshot,
) -> dict[str, float]:
    """Aggregate Greeks across positions weighted by signed quantity."""
    totals = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
    for position in portfolio.positions:
        per = compute_position_greeks(position, market)
        if not per.get("ok"):
            continue
        for greek in totals:
            totals[greek] += per[greek] * position.quantity
    return totals
```

The plan ALSO adds three thin helpers to `services/quantark.py` so `risk_engine.py` doesn't need to know about TermSheet construction — see Task 2.

- [ ] **Step 4: Add the helpers to `services/quantark.py`**

Open `/Users/fuxinyao/open-otc-trading/backend/app/services/quantark.py`. Find the `price_product()` function around line 410-437. Just below it (before `calculate_portfolio_risk`), add three small helpers that extract the engine/product/env construction so they're reusable:

```python
def build_product_for_position(position: Position) -> Any:
    """Build a QuantArk product from a backend Position row."""
    ensure_quantark_path()
    from rfq.builders import build_product_from_termsheet
    from app.services.quantark import _build_termsheet_for_position
    termsheet = _build_termsheet_for_position(position)
    return build_product_from_termsheet(termsheet)


def build_engine_for_position(position: Position) -> Any:
    ensure_quantark_path()
    from rfq.builders import build_engine_from_termsheet
    termsheet = _build_termsheet_for_position(position)
    return build_engine_from_termsheet(termsheet)


def build_pricing_env(market: PricingEnvironmentSnapshot) -> Any:
    ensure_quantark_path()
    from rfq.builders import build_pricing_env_from_market_kwargs
    return build_pricing_env_from_market_kwargs(market.model_dump() if hasattr(market, "model_dump") else market)


def _build_termsheet_for_position(position: Position) -> Any:
    """Reuse the existing termsheet-construction logic from price_product()."""
    ensure_quantark_path()
    # Copy of the relevant block from price_product() — extract into a helper
    # so risk_engine and price_product both reuse it. See price_product() for
    # the original logic; the body of this helper is the same block up through
    # `termsheet = ...`.
    raise NotImplementedError(
        "Extract the termsheet-construction block from price_product() into here. "
        "See price_product() lines ~398-432 for the source code."
    )
```

**INVESTIGATION REQUIRED before writing this:** read `price_product()` in full and identify the exact lines that build the `termsheet` from a position. Move that block into `_build_termsheet_for_position(position)` and have `price_product()` call it. The implementer should refactor `price_product()` to use the new helper before calling `engine.price()`. **Do not duplicate the code** — extract once, call from both places.

- [ ] **Step 5: Run, verify the new tests pass**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_risk_engine.py -v 2>&1 | tail -15
```
Expected: PASS — both tests green.

- [ ] **Step 6: Run the full backend suite to make sure `price_product()` refactor didn't regress anything**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest 2>&1 | tail -5
```
Expected: all 24+ tests pass (any new failures are termsheet-extraction bugs — fix them before committing).

- [ ] **Step 7: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/risk_engine.py backend/app/services/quantark.py tests/test_risk_engine.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(backend): add risk_engine module computing analytical Greeks via QuantArk"
```

## Task 2: Wire Greeks into `calculate_portfolio_risk()`

**Files:**
- Modify: `backend/app/services/quantark.py` (extend `calculate_portfolio_risk()`)
- Modify: `tests/test_quant_services.py` (or `tests/test_api.py`) — add Greek assertions to existing risk-run test

- [ ] **Step 1: Find an existing risk-run test to extend**

```bash
grep -n "calculate_portfolio_risk\|/api/risk/runs" /Users/fuxinyao/open-otc-trading/tests/*.py | head -10
```

Expected: at least `tests/test_api.py:232` already exercises `/api/risk/runs`. Find that test in full.

- [ ] **Step 2: Add a failing assertion for Greeks in the response**

In `tests/test_api.py`, find the existing assertion `assert "one_day_var_proxy" in risk.json()["metrics"]["totals"]`. Add right after it:

```python
    totals = risk.json()["metrics"]["totals"]
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        assert greek in totals, f"missing {greek} in totals"
        assert isinstance(totals[greek], (int, float))
```

- [ ] **Step 3: Run, verify it fails**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_api.py -v -k "risk" 2>&1 | tail -10
```
Expected: FAIL — `KeyError: 'delta'` or assertion error.

- [ ] **Step 4: Extend `calculate_portfolio_risk()` to include Greeks**

In `backend/app/services/quantark.py`, find `calculate_portfolio_risk()` (line ~440). At the bottom of the function, before the final return:

```python
    from .risk_engine import compute_portfolio_greeks
    greeks = compute_portfolio_greeks(portfolio, snapshot)
    totals.update(greeks)
```

The final return now naturally includes `delta`, `gamma`, `vega`, `theta`, `rho` in `totals` alongside the existing `delta_proxy`, `market_value`, `pnl`, `gross_notional`, `one_day_var_proxy`.

- [ ] **Step 5: Run, verify all tests pass**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest 2>&1 | tail -5
```
Expected: all pass — Greek assertions green, no regressions.

- [ ] **Step 6: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/quantark.py tests/test_api.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(backend): risk run returns analytical Greeks (Δ Γ V θ ρ) alongside delta_proxy"
```

## Task 3: Add `run_portfolio_scenarios()` + `/api/risk/scenarios` endpoint

**Files:**
- Modify: `backend/app/services/risk_engine.py` (add `run_portfolio_scenarios()`)
- Modify: `backend/app/schemas.py` (add scenario schemas)
- Modify: `backend/app/main.py` (add endpoint)
- Create: `tests/test_scenarios_endpoint.py`

- [ ] **Step 1: Add scenario schemas**

In `backend/app/schemas.py`, after `RiskRunOut` (around line 365), add:

```python
class ScenarioCell(BaseModel):
    spot_shift_pct: float
    vol_shift_abs: float
    pnl: float


class ScenarioRunRequest(BaseModel):
    portfolio_id: int
    market_snapshot_id: int | None = None
    spot_shifts_pct: list[float] = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0]
    vol_shifts_abs: list[float] = [-0.02, 0.0, 0.02]


class ScenarioRunOut(BaseModel):
    portfolio_id: int
    base_pnl: float
    cells: list[list[ScenarioCell]]  # rows = vol_shifts, cols = spot_shifts
```

- [ ] **Step 2: Write the failing endpoint test**

Create `/Users/fuxinyao/open-otc-trading/tests/test_scenarios_endpoint.py`:

```python
from __future__ import annotations

from pathlib import Path

from .test_api import make_client, vanilla_row, write_trade_workbook, write_market_workbook


def test_scenarios_endpoint_returns_grid(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "Scenario Book", "base_currency": "CNY"}
    ).json()
    client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 100,
            },
            "engine_name": "BlackScholesEngine",
            "quantity": 5,
            "entry_price": 8.0,
        },
    )

    res = client.post(
        "/api/risk/scenarios",
        json={"portfolio_id": portfolio["id"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["portfolio_id"] == portfolio["id"]
    # default 3 vol rows × 6 spot cols
    assert len(data["cells"]) == 3
    assert len(data["cells"][0]) == 6
    # base case (0 spot, 0 vol) should have pnl close to zero
    base_cell = next(c for row in data["cells"] for c in row if c["spot_shift_pct"] == 0.0 and c["vol_shift_abs"] == 0.0)
    assert abs(base_cell["pnl"]) < 1.0  # within rounding noise of base
```

- [ ] **Step 3: Run, verify it fails**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_scenarios_endpoint.py -v 2>&1 | tail -10
```
Expected: FAIL — `404 Not Found` (endpoint missing).

- [ ] **Step 4: Implement `run_portfolio_scenarios()` in risk_engine.py**

Append to `/Users/fuxinyao/open-otc-trading/backend/app/services/risk_engine.py`:

```python
def run_portfolio_scenarios(
    portfolio: Portfolio,
    market: PricingEnvironmentSnapshot,
    spot_shifts_pct: list[float],
    vol_shifts_abs: list[float],
) -> dict[str, Any]:
    """Reprices the portfolio under a grid of (spot_shift, vol_shift)."""
    from .quantark import price_product

    def _portfolio_value(env: PricingEnvironmentSnapshot) -> float:
        total = 0.0
        for pos in portfolio.positions:
            priced = price_product(pos.product_type, pos.product_kwargs, env, pos.engine_name, pos.engine_kwargs)
            total += float(priced.data.get("price", 0.0)) * pos.quantity
        return total

    base_value = _portfolio_value(market)
    rows: list[list[dict[str, Any]]] = []
    for vol_shift in vol_shifts_abs:
        row: list[dict[str, Any]] = []
        for spot_shift_pct in spot_shifts_pct:
            shifted = market.model_copy(update={
                "spot": market.spot * (1.0 + spot_shift_pct / 100.0),
                "volatility": max(market.volatility + vol_shift, 1e-6),
            })
            value = _portfolio_value(shifted)
            row.append({
                "spot_shift_pct": spot_shift_pct,
                "vol_shift_abs": vol_shift,
                "pnl": value - base_value,
            })
        rows.append(row)
    return {"portfolio_id": portfolio.id, "base_pnl": 0.0, "cells": rows}
```

- [ ] **Step 5: Add endpoint to `main.py`**

After the `create_risk_run` endpoint (around line 570), add:

```python
    @app.post("/api/risk/scenarios", response_model=ScenarioRunOut)
    def create_risk_scenario(payload: ScenarioRunRequest, session: Session = Depends(get_db)):
        portfolio = (
            session.query(Portfolio)
            .options(selectinload(Portfolio.positions))
            .filter(Portfolio.id == payload.portfolio_id)
            .one_or_none()
        )
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        market = PricingEnvironmentSnapshot()  # default snapshot for now
        from .services.risk_engine import run_portfolio_scenarios
        result = run_portfolio_scenarios(
            portfolio, market, payload.spot_shifts_pct, payload.vol_shifts_abs,
        )
        record_audit(
            session, event_type="risk.scenario", actor="desk_user",
            subject_type="portfolio", subject_id=portfolio.id,
            payload={"shifts": {"spot": payload.spot_shifts_pct, "vol": payload.vol_shifts_abs}},
        )
        session.commit()
        return result
```

Make sure the imports at the top of `main.py` include `ScenarioRunRequest, ScenarioRunOut`.

- [ ] **Step 6: Run, verify all tests pass**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/risk_engine.py backend/app/schemas.py backend/app/main.py tests/test_scenarios_endpoint.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(backend): add /api/risk/scenarios endpoint (shift × vol PnL grid)"
```

## Task 4: Add `/api/audit/events` endpoint

**Files:**
- Modify: `backend/app/main.py` (add endpoint)
- Create: `tests/test_audit_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/tests/test_audit_endpoint.py`:

```python
from __future__ import annotations

from pathlib import Path

from .test_api import make_client


def test_audit_events_listing_returns_recent(tmp_path: Path):
    client = make_client(tmp_path)
    # Create some events by hitting endpoints that record audit
    portfolio = client.post(
        "/api/portfolios", json={"name": "P", "base_currency": "CNY"}
    ).json()
    client.post(
        "/api/risk/runs", json={"portfolio_id": portfolio["id"], "method": "summary"}
    )
    res = client.get("/api/audit/events")
    assert res.status_code == 200
    events = res.json()
    assert len(events) >= 2  # portfolio.created + risk.run at minimum
    types = {e["event_type"] for e in events}
    assert "portfolio.created" in types
    assert "risk.run" in types


def test_audit_events_filter_by_event_type(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "P", "base_currency": "CNY"}
    ).json()
    client.post(
        "/api/risk/runs", json={"portfolio_id": portfolio["id"], "method": "summary"}
    )
    res = client.get("/api/audit/events?event_type=risk.run")
    assert res.status_code == 200
    events = res.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "risk.run"


def test_audit_events_filter_by_subject(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "P", "base_currency": "CNY"}
    ).json()
    res = client.get(f"/api/audit/events?subject_type=portfolio&subject_id={portfolio['id']}")
    assert res.status_code == 200
    events = res.json()
    assert all(e["subject_type"] == "portfolio" and e["subject_id"] == str(portfolio["id"]) for e in events)
    assert len(events) >= 1
```

- [ ] **Step 2: Run, verify it fails**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_audit_endpoint.py -v 2>&1 | tail -10
```
Expected: FAIL — 404.

- [ ] **Step 3: Add the endpoint**

Add an import at the top of `main.py` (with the other schema imports):

```python
from .schemas import AuditEventOut
```

Then add the endpoint near the other read-only listings (e.g. after `list_reports`):

```python
    @app.get("/api/audit/events", response_model=list[AuditEventOut])
    def list_audit_events(
        event_type: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
        session: Session = Depends(get_db),
    ):
        from .models import AuditEvent

        query = session.query(AuditEvent)
        if event_type:
            query = query.filter(AuditEvent.event_type == event_type)
        if subject_type:
            query = query.filter(AuditEvent.subject_type == subject_type)
        if subject_id:
            query = query.filter(AuditEvent.subject_id == str(subject_id))
        return (
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(min(limit, 500))
            .all()
        )
```

- [ ] **Step 4: Run, verify all tests pass**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/main.py tests/test_audit_endpoint.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(backend): add /api/audit/events listing endpoint with filters"
```

---

# Phase B · Frontend wires GreeksSummary + ScenarioGrid to live data

## Task 5: Extend `GreeksTotals` and surface live Γ/V/θ/ρ

**Files:**
- Modify: `frontend/src/components/GreeksSummary.tsx`
- Modify: `frontend/src/components/GreeksSummary.test.tsx`

- [ ] **Step 1: Read the existing test file**

```bash
cat /Users/fuxinyao/open-otc-trading/frontend/src/components/GreeksSummary.test.tsx | head -50
```

- [ ] **Step 2: Add a failing test for live Γ/V/θ/ρ**

In `GreeksSummary.test.tsx`, append inside the existing `describe('GreeksSummary', ...)`:

```tsx
  it('renders live Γ V θ ρ tiles when totals include them', () => {
    const totals: GreeksTotals = {
      market_value: 100, delta_proxy: 5, gross_notional: 1000, pnl: 12,
      one_day_var_proxy: 8,
      delta: 0.42, gamma: 0.018, vega: 33.5, theta: -1.2, rho: 8.4,
    };
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText('Γ')).toBeInTheDocument();
    expect(screen.getByText('V')).toBeInTheDocument();
    expect(screen.getByText('θ')).toBeInTheDocument();
    expect(screen.getByText('ρ')).toBeInTheDocument();
    expect(screen.queryByText(/deferred/i)).not.toBeInTheDocument();
  });

  it('falls back to deferred banner when only delta_proxy present', () => {
    const totals: GreeksTotals = {
      market_value: 100, delta_proxy: 5, gross_notional: 1000, pnl: 12,
      one_day_var_proxy: 8,
    };
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/deferred/i)).toBeInTheDocument();
  });
```

- [ ] **Step 3: Run, verify the live test fails**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test GreeksSummary -- --run 2>&1 | tail -10
```
Expected: FAIL — `Γ` not found in document.

- [ ] **Step 4: Extend `GreeksSummary.tsx`**

Update the `GreeksTotals` type to make Greeks optional:

```tsx
export type GreeksTotals = {
  market_value: number;
  delta_proxy: number;
  gross_notional: number;
  pnl: number;
  one_day_var_proxy: number;
  delta?: number;
  gamma?: number;
  vega?: number;
  theta?: number;
  rho?: number;
};
```

Replace the `<div className="wl-greeks__deferred">` block with a conditional:

```tsx
{totals.delta != null ? (
  <div className="wl-greeks__tiles">
    <Tile label="Γ" value={fmtSigned(totals.gamma ?? 0, 4)} />
    <Tile label="V" value={fmtSigned(totals.vega ?? 0, 2)} />
    <Tile label="θ" value={fmtSigned(totals.theta ?? 0, 2)} variant={(totals.theta ?? 0) >= 0 ? 'pos' : 'neg'} />
    <Tile label="ρ" value={fmtSigned(totals.rho ?? 0, 2)} />
  </div>
) : (
  <div className="wl-greeks__deferred">
    Γ · V · θ · ρ — pending backend Greeks extension (deferred to Plan 5)
  </div>
)}
```

Also swap the existing `delta_proxy` tile to prefer `delta` when present:

```tsx
<Tile
  label={totals.delta != null ? 'Δ' : 'Δ proxy'}
  value={fmtSigned(totals.delta ?? totals.delta_proxy)}
  variant={(totals.delta ?? totals.delta_proxy) >= 0 ? 'pos' : 'neg'}
/>
```

- [ ] **Step 5: Run, verify all GreeksSummary tests pass**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test GreeksSummary -- --run 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 6: Run the full frontend suite to make sure nothing else broke**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/GreeksSummary.tsx frontend/src/components/GreeksSummary.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): GreeksSummary surfaces live Γ V θ ρ when backend provides them"
```

## Task 6: Wire `ScenarioGrid` to live cells

**Files:**
- Modify: `frontend/src/components/ScenarioGrid.tsx`
- Modify: `frontend/src/components/ScenarioGrid.test.tsx`

- [ ] **Step 1: Add a failing test for live cell rendering**

In `ScenarioGrid.test.tsx`, append:

```tsx
  it('renders pnl values when cells provided', () => {
    const cells: ScenarioCell[][] = [
      [
        { spot_shift_pct: -3, vol_shift_abs: -0.02, pnl: -1200 },
        { spot_shift_pct: 0, vol_shift_abs: -0.02, pnl: -50 },
      ],
      [
        { spot_shift_pct: -3, vol_shift_abs: 0, pnl: -800 },
        { spot_shift_pct: 0, vol_shift_abs: 0, pnl: 0 },
      ],
    ];
    render(<ScenarioGrid cells={cells} onPromoteToReport={() => {}} />);
    expect(screen.getByText('-1,200')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.queryByText(/deferred/i)).not.toBeInTheDocument();
  });

  it('renders deferred overlay when cells null', () => {
    render(<ScenarioGrid cells={null} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/deferred/i)).toBeInTheDocument();
  });
```

Make sure the import line is updated to:

```tsx
import { ScenarioGrid, type ScenarioCell } from './ScenarioGrid';
```

- [ ] **Step 2: Run, verify it fails**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test ScenarioGrid -- --run 2>&1 | tail -10
```
Expected: FAIL — `cells` prop unknown, or `-1,200` not found.

- [ ] **Step 3: Extend `ScenarioGrid.tsx`**

Replace the file body with:

```tsx
import './ScenarioGrid.css';

export type ScenarioCell = {
  spot_shift_pct: number;
  vol_shift_abs: number;
  pnl: number;
};

type Props = {
  cells: ScenarioCell[][] | null;
  onPromoteToReport: () => void;
};

function fmtPnl(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

function fmtSpotShift(pct: number): string {
  return pct === 0 ? '0' : (pct > 0 ? '+' : '') + pct + '%';
}

function fmtVolShift(abs: number): string {
  return abs === 0 ? '0v' : (abs > 0 ? '+' : '') + (abs * 100).toFixed(0) + 'v';
}

export function ScenarioGrid({ cells, onPromoteToReport }: Props) {
  const live = cells != null && cells.length > 0;
  const spotCols = live ? cells[0].map((c) => c.spot_shift_pct) : [-3, -2, -1, 0, 1, 2];
  const volRows = live ? cells.map((row) => row[0].vol_shift_abs) : [-0.02, 0, 0.02];

  return (
    <section className="wl-scenario">
      <header className="wl-scenario__head">
        <span className="wl-scenario__title">SCENARIO GRID · SHIFT × VOL</span>
        <button
          type="button"
          className="wl-scenario__promote"
          aria-label="Promote to Report"
          onClick={onPromoteToReport}
          disabled={!live}
        >
          ↗
        </button>
      </header>
      <div className="wl-scenario__body">
        <div className="wl-scenario__matrix" aria-hidden={!live}>
          <div className="wl-scenario__cell wl-scenario__cell--corner" />
          {spotCols.map((s) => (
            <div key={s} className="wl-scenario__cell wl-scenario__cell--header">{fmtSpotShift(s)}</div>
          ))}
          {volRows.map((v, rowIdx) => (
            <div key={v} className="wl-scenario__row" style={{ display: 'contents' }}>
              <div className="wl-scenario__cell wl-scenario__cell--header">{fmtVolShift(v)}</div>
              {spotCols.map((s, colIdx) => {
                const cell = live ? cells[rowIdx][colIdx] : null;
                if (cell == null) {
                  return <div key={s} className="wl-scenario__cell wl-scenario__cell--empty">—</div>;
                }
                const variantClass = cell.pnl > 0 ? 'wl-scenario__cell--pos' : cell.pnl < 0 ? 'wl-scenario__cell--neg' : '';
                return (
                  <div key={s} className={`wl-scenario__cell ${variantClass}`.trim()}>
                    {fmtPnl(cell.pnl)}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        {!live && (
          <div className="wl-scenario__overlay">
            <div className="wl-scenario__overlay-text">Scenario engine deferred</div>
            <div className="wl-scenario__overlay-detail">Run risk to populate the grid.</div>
          </div>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Run, verify ScenarioGrid tests pass**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test ScenarioGrid -- --run 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 5: Update `Risk.tsx` to pass `cells` through**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Risk.tsx`. Add `scenarioCells` to `RiskMetrics`:

```tsx
import type { ScenarioCell } from '../components/ScenarioGrid';

export type RiskMetrics = {
  totals: GreeksTotals;
  positions: AttributionPosition[];
  scenarioCells: ScenarioCell[][] | null;
};
```

In the JSX, change the `<ScenarioGrid />` to:

```tsx
<ScenarioGrid
  cells={metrics?.scenarioCells ?? null}
  onPromoteToReport={onPromoteScenario}
/>
```

- [ ] **Step 6: Run the full frontend suite (some Risk tests may need updates)**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test Risk -- --run 2>&1 | tail -10
```

If `Risk.test.tsx` uses `RiskMetrics`, it'll need `scenarioCells: null` added to its fixtures. Read the file and patch as needed.

- [ ] **Step 7: Run the full suite**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/ScenarioGrid.tsx frontend/src/components/ScenarioGrid.test.tsx frontend/src/routes/Risk.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): ScenarioGrid renders live PnL cells when populated"
```

## Task 7: Wire `Risk.live.tsx` to fetch scenarios

**Files:**
- Modify: `frontend/src/routes/Risk.live.tsx`

- [ ] **Step 1: Update the `RiskRunResponse` type and add scenario fetch**

After the existing `handleRunRisk` flow in `Risk.live.tsx`, fetch scenarios in parallel and merge into `metrics`. Replace the relevant section:

```tsx
type ScenarioRunResponse = {
  portfolio_id: number;
  base_pnl: number;
  cells: ScenarioCell[][];
};

// inside handleRunRisk, after setting metrics from the risk run:
try {
  const scenarios = await api<ScenarioRunResponse>('/api/risk/scenarios', {
    method: 'POST',
    body: JSON.stringify({ portfolio_id: selectedId }),
  });
  setMetrics((prev) => prev ? { ...prev, scenarioCells: scenarios.cells } : prev);
} catch (e) {
  // Scenarios are nice-to-have; the run risk metrics already updated.
  console.warn('scenario fetch failed', e);
}
```

Make sure `ScenarioCell` is imported from `../components/ScenarioGrid`. Adapt `setMetrics` so it handles `prev` correctly — likely the cleanest approach is to compute `metrics = { totals, positions, scenarioCells: null }` first, then patch `scenarioCells` after the parallel scenario call.

**Cleaner shape:** rewrite `handleRunRisk` to:

1. Run risk (`POST /api/risk/runs`).
2. Set `metrics = { totals: res.metrics.totals, positions: res.metrics.positions, scenarioCells: null }`.
3. In the same async flow, run scenarios.
4. On scenarios success: `setMetrics((prev) => prev ? { ...prev, scenarioCells: scenarios.cells } : prev)`.
5. On scenarios failure: log and leave `scenarioCells: null` (deferred overlay shows).

- [ ] **Step 2: Verify TypeScript build is clean**

```bash
cd /Users/fuxinyao/open-otc-trading/frontend && npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

- [ ] **Step 3: Run the full frontend suite**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/Risk.live.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): Risk live container fetches scenarios after run"
```

---

# Phase C · Smoke + README

## Task 8: Smoke + README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run all automated checks**

```bash
cd /Users/fuxinyao/open-otc-trading && python -m pytest 2>&1 | tail -5
```
Expected: all pass (count grew by 4-6 from new backend tests).

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all pass (count grew by 4 from GreeksSummary + ScenarioGrid additions).

```bash
cd /Users/fuxinyao/open-otc-trading/frontend && npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend run build 2>&1 | tail -3
```
Expected: build succeeds.

- [ ] **Step 2: Update README**

In `/Users/fuxinyao/open-otc-trading/README.md`, replace:

```markdown
- Plan 5b — Backend Greeks/scenario extension + audit-event endpoint
```

with:

```markdown
- ✅ Plan 5b — Backend Greeks/scenario extension + audit-event endpoint (`docs/superpowers/plans/2026-05-08-backend-greeks-scenario-audit-plan-5b.md`)
```

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add README.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: note Plan 5b (backend Greeks/scenario/audit) shipped"
```

---

# Self-Review Notes

**Coverage:**
- Backend `risk_engine.py` module ✓ Task 1
- Greeks integrated into existing `/api/risk/runs` ✓ Task 2
- New `/api/risk/scenarios` endpoint ✓ Task 3
- New `/api/audit/events` endpoint ✓ Task 4
- `GreeksSummary` extended for live Γ/V/θ/ρ ✓ Task 5
- `ScenarioGrid` extended for live cells ✓ Task 6
- `Risk.live.tsx` fetches scenarios ✓ Task 7
- Smoke + README ✓ Task 8

**Type consistency:** `ScenarioCell` defined once in `ScenarioGrid.tsx` and re-imported by `Risk.tsx` and `Risk.live.tsx`. Backend `ScenarioCell` schema mirrors the frontend type. `GreeksTotals` extended with optional `delta`/`gamma`/`vega`/`theta`/`rho` — old code that only reads `delta_proxy` continues to work; new code prefers `delta` when present.

**Placeholder scan:** Task 1 Step 4 contains a `raise NotImplementedError` PLACEHOLDER for `_build_termsheet_for_position()` and a note "INVESTIGATION REQUIRED" telling the implementer exactly which lines of `price_product()` to extract. This is a pragmatic scope-cut — fully detailing the existing termsheet construction code in the plan would duplicate ~40 lines of `quantark.py` here. Implementer reads the source, extracts the block, deletes the placeholder. **If implementing inline (no subagent), the implementer should review `price_product()` lines ~398-432 to identify the exact extraction boundaries before writing Task 1 Step 4.**

**Out of scope (deferred to Plan 5c/5d):**
- Token-by-token chat streaming — Plan 5c
- Berkeley Mono procurement — Plan 5d
- Pagination on `/api/audit/events` (limit-only for v1)
- Custom market snapshot on scenario runs (uses default snapshot for v1)
- Per-position Greeks breakdown in `PnlAttribution` (current behavior unchanged)

**Open questions:**
- The `_build_termsheet_for_position()` extraction in Task 1 Step 4 needs hands-on code reading. If the existing `price_product()` is too tangled to extract cleanly, an alternative is to call `price_product()` from `compute_position_greeks()`, retain the priced engine/product/env from its return value, and skip the helper extraction. Implementer can pivot if the extraction looks ugly.
- QuantArk's `GreeksCalculator.calculate(method="auto")` may fall back to numerical bumping for non-vanilla products (Phoenix, Snowball, Barrier). This adds latency. If the demo portfolios contain enough exotics that risk runs become slow (>5s), Plan 5b could reduce default scope to `method="analytical"` and only return Greeks for vanilla — record a `pricing_error` for non-vanilla. The current plan keeps the auto behavior.

# Booking Pricing Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users price the current unbooked Booking terms (PV + Greeks) on the Booking page via a new stateless preview endpoint and a tabbed right-rail companion.

**Architecture:** A new `quantark.price_product_with_greeks` reuses the existing ad-hoc `_build_termsheet` build path to return price plus Greeks in one call (Greek failures degrade gracefully). A thin domain wrapper and a read-only `POST /api/pricing/preview` endpoint expose it. The Booking right rail becomes a Radix `Tabs` panel (Payoff | Pricing); the Pricing tab is a new `BookingPricingCompanion` with pre-filled-editable market inputs and a manual Price button.

**Tech Stack:** Python / FastAPI / SQLAlchemy / Pydantic v2 / pytest (backend); React / TypeScript / Vitest / Testing-Library / Radix Tabs (frontend).

## Global Constraints

- Backend tests live in top-level `tests/`; use the `client` fixture (FastAPI `TestClient` over `create_app(settings=settings)`) for endpoint tests and `monkeypatch` for service tests. Run backend tests with `python -m pytest` from repo root.
- Frontend: **token-only styling** — never hardcode colors/spacing/fonts in `.css`; use `var(--token)` from `src/tokens/`. One co-located `.css` per component, BEM names, `wl-` prefix. Reuse existing primitives (`Tile`, `Tabs`, `Button`, `Select`) before adding styles. Verify in both themes + compact density. (See `frontend/UI_STYLE_GUIDE.md` / `frontend/CLAUDE.md`.)
- Frontend tests use Vitest; mock `globalThis.fetch`; run with `npx vitest run <file>` from `frontend/`.
- Greek key mapping is fixed: QuantArk's `dividend_rho` maps to `rho_q` (mirror `risk_engine.compute_position_greeks`).
- `QuantArkResult` shape: `{ok: bool, data: dict, error: str | None}`.
- Do not persist or audit preview pricing — it is exploratory and read-only.

---

### Task 1: Backend service `price_product_with_greeks`

**Files:**
- Modify: `backend/app/services/quantark.py` (add function after `price_product`, ~line 882)
- Test: `tests/test_quantark_price_with_greeks.py` (create)

**Interfaces:**
- Consumes: existing module-level helpers in `quantark.py` — `ensure_quantark_path()`, `_build_termsheet(...)`, `QuantArkResult`, `RISK_GREEK_KEYS`/`GREEK_KEYS`, and `PricingEnvironmentSnapshot` (already imported).
- Produces:
  ```python
  def price_product_with_greeks(
      product_type: str,
      product_kwargs: dict[str, Any],
      market: PricingEnvironmentSnapshot,
      engine_name: str = "BlackScholesEngine",
      engine_kwargs: dict[str, Any] | None = None,
      compute_greeks: bool = True,
  ) -> QuantArkResult: ...
  ```
  On success: `ok=True`, `data={"price": float, "engine": engine_name, "product_type": product_type, "greeks": {"delta","gamma","vega","theta","rho","rho_q"} | None, "greeks_error": str | None}`. On build/price failure: `ok=False`, `data={"price": 0.0, "engine": engine_name, "product_type": product_type, "greeks": None, "greeks_error": None}`, `error=str(exc)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quantark_price_with_greeks.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.schemas import PricingEnvironmentSnapshot
from app.services import quantark


def _market() -> PricingEnvironmentSnapshot:
    return PricingEnvironmentSnapshot(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0)


def test_price_product_with_greeks_returns_price_and_greeks():
    result = quantark.price_product_with_greeks(
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100, "option_type": "CALL"},
        market=_market(),
        engine_name="BlackScholesEngine",
        compute_greeks=True,
    )
    assert result.ok is True
    assert result.error is None
    price = result.data["price"]
    assert isinstance(price, float) and price > 0
    greeks = result.data["greeks"]
    assert greeks is not None
    assert set(greeks) == {"delta", "gamma", "vega", "theta", "rho", "rho_q"}
    assert all(isinstance(v, float) for v in greeks.values())
    assert result.data["greeks_error"] is None


def test_price_product_with_greeks_skips_greeks_when_disabled():
    result = quantark.price_product_with_greeks(
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100, "option_type": "CALL"},
        market=_market(),
        compute_greeks=False,
    )
    assert result.ok is True
    assert result.data["greeks"] is None
    assert result.data["greeks_error"] is None


def test_price_product_with_greeks_degrades_when_greeks_raise():
    # Price succeeds, but the Greek calculator blows up -> price still returned.
    with patch("quantark.asset.equity.riskmeasures.greeks_calculator.GreeksCalculator") as calc_cls:
        calc_cls.return_value.calculate.side_effect = RuntimeError("greek boom")
        result = quantark.price_product_with_greeks(
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100, "option_type": "CALL"},
            market=_market(),
            compute_greeks=True,
        )
    assert result.ok is True
    assert result.data["price"] > 0
    assert result.data["greeks"] is None
    assert "greek boom" in result.data["greeks_error"]


def test_price_product_with_greeks_returns_error_on_bad_build():
    result = quantark.price_product_with_greeks(
        product_type="NotARealProduct",
        product_kwargs={},
        market=_market(),
    )
    assert result.ok is False
    assert result.error
    assert result.data["price"] == 0.0
    assert result.data["greeks"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_quantark_price_with_greeks.py -v`
Expected: FAIL with `AttributeError: module 'app.services.quantark' has no attribute 'price_product_with_greeks'`.

- [ ] **Step 3: Implement `price_product_with_greeks`**

In `backend/app/services/quantark.py`, add directly after the `price_product` function (before `_empty_risk_totals`):

```python
def price_product_with_greeks(
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
    engine_kwargs: dict[str, Any] | None = None,
    compute_greeks: bool = True,
) -> QuantArkResult:
    """Price one ad-hoc product spec and (optionally) its Greeks in a single
    build. Reuses the same termsheet path as :func:`price_product`. Read-only:
    persists nothing. If pricing succeeds but the Greek calculation fails, the
    price is still returned with ``greeks=None`` and ``greeks_error`` set."""
    engine_kwargs = engine_kwargs or {}
    ensure_quantark_path()
    try:
        from quantark.rfq.builders import (
            build_engine_from_termsheet,
            build_pricing_env_from_market_kwargs,
            build_product_from_termsheet,
        )

        termsheet, otc_attrs = _build_termsheet(
            product_type=product_type,
            product_kwargs=product_kwargs,
            market=market,
            engine_name=engine_name,
            engine_kwargs=engine_kwargs,
        )
        product = build_product_from_termsheet(termsheet)
        for key, value in otc_attrs.items():
            setattr(product, key, value)
        pricing_env = build_pricing_env_from_market_kwargs(termsheet.market_kwargs)
        engine = build_engine_from_termsheet(termsheet)
        price = float(engine.price(product, pricing_env))
    except Exception as exc:
        return QuantArkResult(
            ok=False,
            data={
                "price": 0.0,
                "engine": engine_name,
                "product_type": product_type,
                "greeks": None,
                "greeks_error": None,
            },
            error=str(exc),
        )

    greeks: dict[str, float] | None = None
    greeks_error: str | None = None
    if compute_greeks:
        try:
            from quantark.asset.equity.riskmeasures.greeks_calculator import (
                GreeksCalculator,
            )

            calc = GreeksCalculator()
            result = calc.calculate(product, pricing_env, engine, method="auto")
            greeks = {
                "delta": float(result.get("delta", 0.0)),
                "gamma": float(result.get("gamma", 0.0)),
                "vega": float(result.get("vega", 0.0)),
                "theta": float(result.get("theta", 0.0)),
                "rho": float(result.get("rho", 0.0)),
                "rho_q": float(result.get("dividend_rho", 0.0)),
            }
        except Exception as exc:  # price already computed; degrade gracefully
            greeks = None
            greeks_error = str(exc)

    return QuantArkResult(
        ok=True,
        data={
            "price": price,
            "engine": engine_name,
            "product_type": product_type,
            "greeks": greeks,
            "greeks_error": greeks_error,
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_quantark_price_with_greeks.py -v`
Expected: PASS (4 tests). If the QuantArk env is unavailable in this checkout, run with `PYTHONPATH` per repo convention; the `_build_termsheet`/`ensure_quantark_path` path is the same one `price_product` already uses, so availability matches existing pricing tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quantark.py tests/test_quantark_price_with_greeks.py
git commit -m "feat(pricing): add price_product_with_greeks ad-hoc pricer"
```

---

### Task 2: Domain wrapper `pricing.price_product_preview`

**Files:**
- Modify: `backend/app/services/domains/pricing.py` (add wrapper + `__all__` entry)
- Test: `tests/test_services_domains_pricing.py` (append one test)

**Interfaces:**
- Consumes: `quantark.price_product_with_greeks` from Task 1.
- Produces:
  ```python
  def price_product_preview(
      *,
      product_type: str,
      product_kwargs: dict[str, Any],
      market: PricingEnvironmentSnapshot,
      engine_name: str = "BlackScholesEngine",
      engine_kwargs: dict[str, Any] | None = None,
      compute_greeks: bool = True,
  ) -> QuantArkResult: ...
  ```

- [ ] **Step 1: Write the failing test**

Append to `tests/test_services_domains_pricing.py`:

```python
def test_price_product_preview_delegates_to_quantark(monkeypatch):
    fake_result = MagicMock(ok=True, data={"price": 4.5, "greeks": None}, error=None)
    captured: dict[str, Any] = {}

    def fake_pwg(**kwargs):
        captured["kwargs"] = kwargs
        return fake_result

    monkeypatch.setattr(
        "app.services.domains.pricing._quantark_price_product_with_greeks",
        fake_pwg,
    )
    market = PricingEnvironmentSnapshot()
    result = pricing_svc.price_product_preview(
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100},
        market=market,
        engine_name="BlackScholesEngine",
        compute_greeks=True,
    )
    assert result is fake_result
    assert captured["kwargs"]["product_type"] == "EuropeanVanillaOption"
    assert captured["kwargs"]["compute_greeks"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_services_domains_pricing.py::test_price_product_preview_delegates_to_quantark -v`
Expected: FAIL — `AttributeError` on `pricing._quantark_price_product_with_greeks` / `price_product_preview`.

- [ ] **Step 3: Implement the wrapper**

In `backend/app/services/domains/pricing.py`:

1. Extend the existing quantark import (currently `from app.services.quantark import QuantArkResult, price_product as _quantark_price_product`) to also import the new function:

```python
from app.services.quantark import (
    QuantArkResult,
    price_product as _quantark_price_product,
    price_product_with_greeks as _quantark_price_product_with_greeks,
)
```

2. Add the wrapper after `price_product` (before `price_positions`):

```python
def price_product_preview(
    *,
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
    engine_kwargs: dict[str, Any] | None = None,
    compute_greeks: bool = True,
) -> QuantArkResult:
    """Price an ad-hoc product spec and Greeks. Pure compute, no persistence."""
    return _quantark_price_product_with_greeks(
        product_type=product_type,
        product_kwargs=product_kwargs,
        market=market,
        engine_name=engine_name,
        engine_kwargs=engine_kwargs,
        compute_greeks=compute_greeks,
    )
```

3. Add `"price_product_preview",` to the `__all__` list.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_services_domains_pricing.py::test_price_product_preview_delegates_to_quantark -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/pricing.py tests/test_services_domains_pricing.py
git commit -m "feat(pricing): add price_product_preview domain wrapper"
```

---

### Task 3: Schemas for the preview endpoint

**Files:**
- Modify: `backend/app/schemas.py` (add three models after `PricingEnvironmentSnapshot`, ~line 245)
- Test: covered by Task 4's endpoint test (no separate schema test).

**Interfaces:**
- Consumes: existing `PricingEnvironmentSnapshot` in `schemas.py`.
- Produces: `PricingPreviewRequest`, `PricingGreeks`, `PricingPreviewOut` (importable from `app.schemas`).

- [ ] **Step 1: Implement the schemas**

In `backend/app/schemas.py`, immediately after the `PricingEnvironmentSnapshot` class (before `RFQUnknownSpecIn`):

```python
class PricingPreviewRequest(BaseModel):
    product_type: str
    product_kwargs: dict[str, Any] = Field(default_factory=dict)
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    market: PricingEnvironmentSnapshot = Field(default_factory=PricingEnvironmentSnapshot)
    compute_greeks: bool = True


class PricingGreeks(BaseModel):
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    rho_q: float


class PricingPreviewOut(BaseModel):
    ok: bool
    price: float
    engine: str
    product_type: str
    greeks: PricingGreeks | None = None
    greeks_error: str | None = None
    error: str | None = None
```

Confirm `Any` and `Field` are already imported at the top of `schemas.py` (they are — `PricingEnvironmentSnapshot` uses `Field`; `Any` is used widely). If `Any` is missing from the `typing` import, add it.

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python -c "from app.schemas import PricingPreviewRequest, PricingGreeks, PricingPreviewOut; print('ok')"`
Expected: prints `ok`. (Run from `backend/` or with the repo's standard `PYTHONPATH`.)

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas.py
git commit -m "feat(pricing): add preview request/response schemas"
```

---

### Task 4: `POST /api/pricing/preview` endpoint

**Files:**
- Modify: `backend/app/main.py` (add endpoint; add schema imports to the existing `app.schemas` import block)
- Test: `tests/test_pricing_preview_endpoint.py` (create)

**Interfaces:**
- Consumes: `pricing.price_product_preview` (Task 2); `PricingPreviewRequest`/`PricingPreviewOut`/`PricingGreeks` (Task 3); the `client` fixture from `tests/conftest.py`.
- Produces: HTTP route `POST /api/pricing/preview` returning `PricingPreviewOut`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pricing_preview_endpoint.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock


def test_pricing_preview_happy_path(client, monkeypatch):
    fake = MagicMock(
        ok=True,
        error=None,
        data={
            "price": 4.25,
            "engine": "BlackScholesEngine",
            "product_type": "EuropeanVanillaOption",
            "greeks": {
                "delta": 0.5, "gamma": 0.01, "vega": 0.2,
                "theta": -0.03, "rho": 0.1, "rho_q": -0.05,
            },
            "greeks_error": None,
        },
    )
    monkeypatch.setattr(
        "app.main.price_product_preview", lambda **kwargs: fake, raising=False
    )
    resp = client.post(
        "/api/pricing/preview",
        json={
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {"strike": 100, "option_type": "CALL"},
            "engine_name": "BlackScholesEngine",
            "market": {"spot": 100, "volatility": 0.2, "rate": 0.03, "dividend_yield": 0.0},
            "compute_greeks": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["price"] == 4.25
    assert body["greeks"]["delta"] == 0.5
    assert body["greeks"]["rho_q"] == -0.05
    assert body["error"] is None


def test_pricing_preview_reports_build_failure(client, monkeypatch):
    fake = MagicMock(
        ok=False,
        error="bad terms",
        data={"price": 0.0, "engine": "BlackScholesEngine",
              "product_type": "X", "greeks": None, "greeks_error": None},
    )
    monkeypatch.setattr(
        "app.main.price_product_preview", lambda **kwargs: fake, raising=False
    )
    resp = client.post(
        "/api/pricing/preview",
        json={"product_type": "X", "product_kwargs": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "bad terms"
    assert body["greeks"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pricing_preview_endpoint.py -v`
Expected: FAIL — 404 from the missing route (and/or `price_product_preview` not importable in `app.main`).

- [ ] **Step 3: Wire the import**

In `backend/app/main.py`, find the import that pulls domain pricing helpers. The endpoint references `price_product_preview` at module scope (so the test can monkeypatch `app.main.price_product_preview`). Add near the other domain-service imports:

```python
from .services.domains.pricing import price_product_preview
```

(If `app.main` already imports the `pricing` domain module under an alias rather than individual names, instead add `price_product_preview` to that existing `from .services.domains.pricing import ...` line. Either way the name `price_product_preview` must resolve at `app.main` scope.)

- [ ] **Step 4: Implement the endpoint**

In `backend/app/main.py`, add alongside the other pricing endpoints (e.g. right after `price_portfolio_positions_endpoint`, ~line 2870). It does NOT take a DB session — it is pure compute:

```python
    @app.post("/api/pricing/preview", response_model=PricingPreviewOut)
    def pricing_preview_endpoint(payload: PricingPreviewRequest):
        result = price_product_preview(
            product_type=payload.product_type,
            product_kwargs=payload.product_kwargs,
            market=payload.market,
            engine_name=payload.engine_name,
            engine_kwargs=payload.engine_kwargs or None,
            compute_greeks=payload.compute_greeks,
        )
        data = result.data or {}
        raw_greeks = data.get("greeks")
        greeks = PricingGreeks(**raw_greeks) if raw_greeks else None
        return PricingPreviewOut(
            ok=bool(result.ok),
            price=float(data.get("price", 0.0)),
            engine=str(data.get("engine", payload.engine_name)),
            product_type=str(data.get("product_type", payload.product_type)),
            greeks=greeks,
            greeks_error=data.get("greeks_error"),
            error=result.error,
        )
```

Add `PricingPreviewRequest`, `PricingPreviewOut`, `PricingGreeks` to the existing `from .schemas import (...)` block in `main.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pricing_preview_endpoint.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_pricing_preview_endpoint.py
git commit -m "feat(pricing): add POST /api/pricing/preview endpoint"
```

---

### Task 5: Frontend preview types

**Files:**
- Modify: `frontend/src/types.ts` (append the preview types)
- Test: none (types only; exercised by Task 6).

**Interfaces:**
- Produces (exported from `types.ts`):
  ```ts
  export type PricingGreeks = { delta: number; gamma: number; vega: number; theta: number; rho: number; rho_q: number };
  export type PricingPreviewRequest = {
    product_type: string;
    product_kwargs: Record<string, unknown>;
    engine_name: string;
    engine_kwargs?: Record<string, unknown>;
    market: { spot: number; rate: number; volatility: number; dividend_yield: number; valuation_date?: string; currency?: string };
    compute_greeks: boolean;
  };
  export type PricingPreviewOut = {
    ok: boolean; price: number; engine: string; product_type: string;
    greeks?: PricingGreeks | null; greeks_error?: string | null; error?: string | null;
  };
  ```

- [ ] **Step 1: Add the types**

Append the three exported types above to the end of `frontend/src/types.ts`.

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(pricing): add preview API types"
```

---

### Task 6: `BookingPricingCompanion` component

**Files:**
- Create: `frontend/src/components/BookingPricingCompanion.tsx`
- Create: `frontend/src/components/BookingPricingCompanion.css`
- Test: `frontend/src/components/BookingPricingCompanion.test.tsx` (create)

**Interfaces:**
- Consumes: `api` from `../api/client`; `Tile` from `./Tile`; `Button` from `./Button`; `formatSignedNumber` from `./numberFormat`; `PricingPreviewOut` from `../types`; endpoint `POST /api/pricing/preview` (Task 4) and `GET /api/underlying-pricing-defaults/{underlying}`.
- Produces:
  ```ts
  type Props = {
    productType: string;
    productFamily: string;
    terms: Record<string, unknown>;
    engineName: string;
    underlying: string;
    currency: string;
    latestSpot: number | null;
  };
  export function BookingPricingCompanion(props: Props): JSX.Element;
  ```

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/BookingPricingCompanion.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BookingPricingCompanion } from './BookingPricingCompanion';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}
function url(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

const baseProps = {
  productType: 'EuropeanVanillaOption',
  productFamily: 'vanilla',
  terms: { strike: 100, option_type: 'CALL' },
  engineName: 'BlackScholesEngine',
  underlying: '000300.SH',
  currency: 'CNY',
  latestSpot: 3888.12,
};

describe('BookingPricingCompanion', () => {
  afterEach(() => vi.restoreAllMocks());

  it('pre-fills rate/vol/q from underlying defaults and spot from latestSpot', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      if (url(input).startsWith('/api/underlying-pricing-defaults/')) {
        return response({ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 });
      }
      throw new Error(`unexpected ${url(input)}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);

    await waitFor(() => {
      expect(screen.getByLabelText('Spot')).toHaveValue(3888.12);
      expect(screen.getByLabelText('Rate')).toHaveValue(0.025);
      expect(screen.getByLabelText('Volatility')).toHaveValue(0.2);
      expect(screen.getByLabelText('Dividend Yield')).toHaveValue(0.01);
    });
  });

  it('prices on demand and renders PV + greek tiles', async () => {
    globalThis.fetch = vi.fn(async (input, init) => {
      const u = url(input);
      if (u.startsWith('/api/underlying-pricing-defaults/')) {
        return response({ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 });
      }
      if (u === '/api/pricing/preview' && init?.method === 'POST') {
        return response({
          ok: true, price: 12.34, engine: 'BlackScholesEngine', product_type: 'EuropeanVanillaOption',
          greeks: { delta: 0.5, gamma: 0.01, vega: 0.2, theta: -0.03, rho: 0.1, rho_q: -0.05 },
          greeks_error: null, error: null,
        });
      }
      throw new Error(`unexpected ${u}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);
    await screen.findByLabelText('Spot');
    await userEvent.click(screen.getByRole('button', { name: /price/i }));

    await waitFor(() => {
      expect(screen.getByText('Price (PV)')).toBeInTheDocument();
      expect(screen.getByText('12.3400')).toBeInTheDocument();
      expect(screen.getByText('Delta')).toBeInTheDocument();
      expect(screen.getByText('0.5000')).toBeInTheDocument();
    });
  });

  it('shows the error when pricing fails', async () => {
    globalThis.fetch = vi.fn(async (input, init) => {
      const u = url(input);
      if (u.startsWith('/api/underlying-pricing-defaults/')) {
        return response({ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 });
      }
      if (u === '/api/pricing/preview' && init?.method === 'POST') {
        return response({ ok: false, price: 0, engine: 'BlackScholesEngine',
          product_type: 'EuropeanVanillaOption', greeks: null, greeks_error: null, error: 'bad terms' });
      }
      throw new Error(`unexpected ${u}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);
    await screen.findByLabelText('Spot');
    await userEvent.click(screen.getByRole('button', { name: /price/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('bad terms');
    });
    expect(screen.queryByText('Price (PV)')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/BookingPricingCompanion.test.tsx`
Expected: FAIL — cannot resolve `./BookingPricingCompanion`.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/BookingPricingCompanion.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Calculator } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { Tile } from './Tile';
import { formatSignedNumber } from './numberFormat';
import type { PricingPreviewOut } from '../types';
import './BookingPricingCompanion.css';

type Props = {
  productType: string;
  productFamily: string;
  terms: Record<string, unknown>;
  engineName: string;
  underlying: string;
  currency: string;
  latestSpot: number | null;
};

type MarketInputs = {
  spot: string;
  rate: string;
  volatility: string;
  dividendYield: string;
  valuationDate: string;
};

type UnderlyingDefaults = {
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
};

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function str(value: number | null | undefined, fallback: string): string {
  return value == null || !Number.isFinite(value) ? fallback : String(value);
}

export function BookingPricingCompanion({
  productType, terms, engineName, underlying, currency, latestSpot,
}: Props) {
  const [inputs, setInputs] = useState<MarketInputs>({
    spot: str(latestSpot, '100'),
    rate: '0.03',
    volatility: '0.2',
    dividendYield: '0',
    valuationDate: today(),
  });
  const [pricing, setPricing] = useState(false);
  const [result, setResult] = useState<PricingPreviewOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-pre-fill only when the underlying itself changes (preserve user edits
  // across term tweaks). Spot follows latestSpot for the selected underlying.
  useEffect(() => {
    let alive = true;
    setInputs((cur) => ({ ...cur, spot: str(latestSpot, cur.spot) }));
    api<UnderlyingDefaults>(`/api/underlying-pricing-defaults/${encodeURIComponent(underlying)}`)
      .then((d) => {
        if (!alive) return;
        setInputs((cur) => ({
          ...cur,
          rate: str(d.rate, cur.rate),
          volatility: str(d.volatility, cur.volatility),
          dividendYield: str(d.dividend_yield, cur.dividendYield),
        }));
      })
      .catch(() => { /* keep defaults if no profile exists */ });
    return () => { alive = false; };
  }, [underlying, latestSpot]);

  const update = (key: keyof MarketInputs, value: string) =>
    setInputs((cur) => ({ ...cur, [key]: value }));

  const handlePrice = async () => {
    setPricing(true);
    setError(null);
    try {
      const body = {
        product_type: productType,
        product_kwargs: terms,
        engine_name: engineName,
        engine_kwargs: {},
        market: {
          spot: Number(inputs.spot),
          rate: Number(inputs.rate),
          volatility: Number(inputs.volatility),
          dividend_yield: Number(inputs.dividendYield),
          valuation_date: inputs.valuationDate,
          currency,
        },
        compute_greeks: true,
      };
      const res = await api<PricingPreviewOut>('/api/pricing/preview', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setResult(null);
        setError(res.error || 'Pricing failed.');
      } else {
        setResult(res);
      }
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPricing(false);
    }
  };

  const fields: { key: keyof MarketInputs; label: string; type: string }[] = [
    { key: 'spot', label: 'Spot', type: 'number' },
    { key: 'rate', label: 'Rate', type: 'number' },
    { key: 'volatility', label: 'Volatility', type: 'number' },
    { key: 'dividendYield', label: 'Dividend Yield', type: 'number' },
    { key: 'valuationDate', label: 'Valuation Date', type: 'date' },
  ];

  const greeks = result?.greeks ?? null;

  return (
    <section className="wl-pricing-companion">
      <header className="wl-pricing-companion__head">
        <Calculator size={16} aria-hidden="true" />
        <span>Pricing Companion</span>
      </header>
      <div className="wl-pricing-companion__inputs">
        {fields.map((f) => (
          <label key={f.key} className="wl-pricing-companion__field">
            <span>{f.label}</span>
            <input
              type={f.type}
              step="any"
              aria-label={f.label}
              value={inputs[f.key]}
              onChange={(e) => update(f.key, e.target.value)}
            />
          </label>
        ))}
      </div>
      <Button type="button" variant="primary" onClick={handlePrice} disabled={pricing}>
        <Calculator size={16} aria-hidden="true" />
        {pricing ? 'Pricing…' : 'Price'}
      </Button>

      {error && <div className="wl-pricing-companion__error" role="alert">{error}</div>}

      {result && (
        <div className="wl-pricing-companion__results">
          <div className="wl-pricing-companion__engine">Engine · {result.engine}</div>
          <div className="wl-pricing-companion__tiles">
            <Tile label="Price (PV)" value={formatSignedNumber(result.price)} variant={result.price >= 0 ? 'pos' : 'neg'} />
            {greeks ? (
              <>
                <Tile label="Delta" value={formatSignedNumber(greeks.delta)} variant={greeks.delta >= 0 ? 'pos' : 'neg'} />
                <Tile label="Gamma" value={formatSignedNumber(greeks.gamma)} variant={greeks.gamma >= 0 ? 'pos' : 'neg'} />
                <Tile label="Vega" value={formatSignedNumber(greeks.vega)} variant={greeks.vega >= 0 ? 'pos' : 'neg'} />
                <Tile label="Theta" value={formatSignedNumber(greeks.theta)} variant={greeks.theta >= 0 ? 'pos' : 'neg'} />
                <Tile label="Rho" value={formatSignedNumber(greeks.rho)} variant={greeks.rho >= 0 ? 'pos' : 'neg'} />
                <Tile label="RhoQ" value={formatSignedNumber(greeks.rho_q)} variant={greeks.rho_q >= 0 ? 'pos' : 'neg'} />
              </>
            ) : null}
          </div>
          {!greeks && (
            <div className="wl-pricing-companion__note">
              Greeks unavailable{result.greeks_error ? ` · ${result.greeks_error}` : ''}.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Add the stylesheet**

Create `frontend/src/components/BookingPricingCompanion.css` (token-only; mirror existing companion spacing tokens):

```css
.wl-pricing-companion {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.wl-pricing-companion__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: var(--letter-spacing-wide);
  font-size: var(--font-size-sm);
}

.wl-pricing-companion__inputs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.wl-pricing-companion__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.wl-pricing-companion__field input {
  background: var(--color-surface-input);
  color: var(--color-text-primary);
  border: var(--border-width) solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  font: inherit;
}

.wl-pricing-companion__error {
  color: var(--color-danger-text);
  background: var(--color-danger-surface);
  border-radius: var(--radius-sm);
  padding: var(--space-2);
  font-size: var(--font-size-sm);
}

.wl-pricing-companion__results {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.wl-pricing-companion__engine {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}

.wl-pricing-companion__tiles {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.wl-pricing-companion__note {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
```

NOTE: Before committing, verify each `var(--token)` above exists in `frontend/src/tokens/`. If a token name differs (e.g. `--color-surface-input`, `--color-danger-surface`, `--letter-spacing-wide`), replace it with the actual token used by a sibling component's `.css` (grep `frontend/src/components/*.css` for the closest match — e.g. how `wl-positions__ticket-error` styles its error, and how raw inputs are themed). Do not invent token names.

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/BookingPricingCompanion.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Typecheck + commit**

```bash
cd frontend && npx tsc --noEmit && cd ..
git add frontend/src/components/BookingPricingCompanion.tsx frontend/src/components/BookingPricingCompanion.css frontend/src/components/BookingPricingCompanion.test.tsx
git commit -m "feat(booking): add BookingPricingCompanion component"
```

---

### Task 7: Mount the tabbed right rail in Booking

**Files:**
- Modify: `frontend/src/routes/Booking.live.tsx` (right-rail block, lines ~620-630)
- Test: `frontend/src/routes/Booking.live.test.tsx` (append one test)

**Interfaces:**
- Consumes: `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` from `../components/Tabs`; `BookingPricingCompanion` from `../components/BookingPricingCompanion` (Task 6); existing `BookingVisualCompanion`; existing `latestSpot` memo in `Booking.live.tsx`.
- Produces: a right rail with a "Payoff" tab (default) and a "Pricing" tab.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/Booking.live.test.tsx` (inside the `describe('BookingLive', ...)` block). It reuses the file's existing `response`/`requestUrl` helpers and fixtures:

```tsx
  it('exposes a Pricing tab in the right rail', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      if (url.startsWith('/api/underlying-pricing-defaults/')) {
        return response({ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 });
      }
      if (url === '/api/pricing/preview' && init?.method === 'POST') {
        return response({ ok: true, price: 7.7, engine: 'SnowballQuadEngine',
          product_type: 'SnowballOption', greeks: null, greeks_error: null, error: null });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    const pricingTab = await screen.findByRole('tab', { name: /pricing/i });
    await userEvent.click(pricingTab);
    expect(await screen.findByLabelText('Spot')).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/Booking.live.test.tsx -t "Pricing tab"`
Expected: FAIL — no `tab` role / no `Spot` field (right rail is still the bare visual companion).

- [ ] **Step 3: Wire the tabs**

In `frontend/src/routes/Booking.live.tsx`:

1. Add imports near the other component imports (top of file):

```tsx
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/Tabs';
import { BookingPricingCompanion } from '../components/BookingPricingCompanion';
```

2. Replace the right-rail block (currently lines ~620-630, the `<div className="wl-booking__right">` containing only `<BookingVisualCompanion .../>`) with:

```tsx
        <div className="wl-booking__right">
          <Tabs defaultValue="payoff">
            <TabsList>
              <TabsTrigger value="payoff">Payoff</TabsTrigger>
              <TabsTrigger value="pricing">Pricing</TabsTrigger>
            </TabsList>
            <TabsContent value="payoff">
              <BookingVisualCompanion
                productType={form.productType}
                productFamily={productFamily}
                terms={productTerms}
                quantity={form.quantity}
                entryPrice={form.entryPrice}
                underlying={form.underlying}
                currency={form.currency}
              />
            </TabsContent>
            <TabsContent value="pricing">
              <BookingPricingCompanion
                productType={form.productType}
                productFamily={productFamily}
                terms={productTerms}
                engineName={form.engineName}
                underlying={form.underlying}
                currency={form.currency}
                latestSpot={latestSpot}
              />
            </TabsContent>
          </Tabs>
        </div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run src/routes/Booking.live.test.tsx`
Expected: PASS — the new test plus all pre-existing Booking tests still green.

- [ ] **Step 5: Typecheck + commit**

```bash
cd frontend && npx tsc --noEmit && cd ..
git add frontend/src/routes/Booking.live.tsx frontend/src/routes/Booking.live.test.tsx
git commit -m "feat(booking): tabbed right rail with Pricing Companion"
```

---

### Task 8: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `python -m pytest tests/test_quantark_price_with_greeks.py tests/test_services_domains_pricing.py tests/test_pricing_preview_endpoint.py -v`
Expected: all PASS. Then a broader smoke: `python -m pytest tests/test_api.py -q` to confirm no route-registration regressions.

- [ ] **Step 2: Frontend suite**

Run (from `frontend/`): `npx vitest run src/components/BookingPricingCompanion.test.tsx src/routes/Booking.live.test.tsx` then `npx tsc --noEmit`.
Expected: all PASS, no type errors.

- [ ] **Step 3: Manual UI check (both themes + compact density)**

Start the app per the project's run skill, open Booking, switch to the Pricing tab, confirm: inputs pre-fill, Price returns PV + greek tiles, an intentionally-bad term shows the error. Verify light + dark + compact density render cleanly (no white input backgrounds in dark mode).

- [ ] **Step 4: Final commit (if any manual-check fixups were needed)**

```bash
git add -A && git commit -m "chore(booking): pricing companion verification fixups"
```

---

## Self-Review

**Spec coverage:**
- Backend `price_product_with_greeks` (graceful Greek degradation) → Task 1. ✓
- Domain wrapper `price_product_preview` → Task 2. ✓
- `PricingPreviewRequest`/`PricingGreeks`/`PricingPreviewOut` → Task 3. ✓
- `POST /api/pricing/preview` (read-only, no audit, ok=false at HTTP 200) → Task 4. ✓
- Frontend types → Task 5. ✓
- `BookingPricingCompanion` (pre-fill from underlying-defaults + latestSpot, manual Price, PV + raw greek tiles, error + greeks-unavailable note) → Task 6. ✓
- Right-rail `Tabs` (Payoff default | Pricing) → Task 7. ✓
- Testing (backend service + endpoint; frontend component + tab) → Tasks 1,2,4,6,7,8. ✓
- Out-of-scope items (persistence, auto-reprice, profile selection, sensitivity grids, FX) → not implemented. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. The one deliberate verification instruction (Task 6 Step 4 token-name check) is a guardrail against inventing tokens, not a placeholder — exact fallbacks (grep sibling `.css`) are given.

**Type consistency:** `price_product_with_greeks` signature/return shape is identical across Tasks 1→2→4. `greeks` dict keys (`delta,gamma,vega,theta,rho,rho_q`) are consistent across backend `data["greeks"]`, `PricingGreeks`, frontend `PricingGreeks`, and the component tiles. `PricingPreviewOut` field names match between schema (Task 3), frontend type (Task 5), and component usage (Task 6). Endpoint monkeypatch target `app.main.price_product_preview` matches the module-scope import wired in Task 4 Step 3.

# Booking Pricing Companion — Design

**Date:** 2026-06-19
**Status:** Approved for planning

## Goal

Let a user price the **current, unbooked** Booking terms — getting a present
value (PV) and Greeks — directly on the Booking page, without committing the
position to a portfolio. This turns Booking into a what-if pricing surface: tweak
terms or market inputs, hit Price, read the value and risk.

## Context

The Booking page (`frontend/src/routes/Booking.live.tsx`) already holds the full
product draft: `productType`, `terms`, `engineName`, `underlying`, `currency`,
plus loaded `marketDataProfiles` and a derived `latestSpot`. Its right rail
currently renders `BookingVisualCompanion` (a payoff diagram).

The pricing primitives exist in the backend but no HTTP endpoint exposes them for
ad-hoc terms:

- `quantark.price_product(product_type, product_kwargs, market, engine_name)` →
  price only, no persistence.
- `risk_engine.compute_position_greeks(position, market)` → delta/gamma/vega/
  theta/rho/rho_q, but it builds its termsheet from a `Position`-shaped object
  (`_build_termsheet_for_position`).
- The existing `POST /api/portfolios/{id}/positions/price` endpoint operates on
  already-booked `position_ids`, so it cannot serve pre-booking previews.
- `GET /api/underlying-pricing-defaults/{underlying}` resolves per-underlying
  `rate` / `dividend_yield` / `volatility`.
- `PricingEnvironmentSnapshot` (schemas.py) = `{valuation_date, spot, volatility,
  rate, dividend_yield, currency, day_count_convention, bus_days_in_year, ...}`.

## Decisions (from brainstorming)

1. **Market inputs:** pre-filled but **editable** (what-if). Spot ← `latestSpot`;
   rate/vol/q ← `GET /api/underlying-pricing-defaults/{underlying}`; valuation
   date ← today; currency ← booking form.
2. **Trigger:** manual **Price** button (avoids firing slow MC engines —
   snowball/phoenix — on every keystroke).
3. **Placement:** right rail becomes a tabbed panel — **Payoff** (existing
   visual companion) | **Pricing** (new companion). Only one visible at a time.
4. **Display:** raw Greeks (delta/gamma/vega/theta/rho/rho_q) + PV — *not* cash
   Greeks, because notional/quantity are not committed at booking time.

## Architecture

### Backend

**1. `quantark.price_product_with_greeks(...)`** (new, in
`backend/app/services/quantark.py`)

```python
def price_product_with_greeks(
    product_type: str,
    product_kwargs: dict[str, Any],
    market: PricingEnvironmentSnapshot,
    engine_name: str = "BlackScholesEngine",
    engine_kwargs: dict[str, Any] | None = None,
    compute_greeks: bool = True,
) -> QuantArkResult:
    ...
```

Reuses the **exact ad-hoc build path** that `price_product` and
`validate_quantark_build` already use (`_build_termsheet` → `build_product_from_
termsheet` + OTC attrs → `build_engine_from_termsheet` → `build_pricing_env_from_
market_kwargs`). Builds product/engine/env **once**, then:

- `price = float(engine.price(product, pricing_env))`
- if `compute_greeks`: `GreeksCalculator().calculate(product, pricing_env, engine,
  method="auto")`, mapping `dividend_rho` → `rho_q` (same mapping as
  `compute_position_greeks`).

Returns `QuantArkResult`:

- success: `ok=True`, `data = {"price": price, "engine": engine_name,
  "product_type": product_type, "greeks": {delta, gamma, vega, theta, rho,
  rho_q} | None, "greeks_error": str | None}`.
- **Graceful Greek degradation:** if pricing succeeds but the Greek calc throws,
  return `ok=True` with the price, `greeks=None`, and `greeks_error=str(exc)` —
  do not fail the whole call.
- build/price failure: `ok=False`, `data={"price": 0.0, "engine": ...,
  "product_type": ...}`, `error=str(exc)` (mirrors existing `price_product`).

**2. `pricing.price_product_preview(...)`** (new, in
`backend/app/services/domains/pricing.py`)

Thin service-boundary wrapper delegating to `quantark.price_product_with_greeks`,
keeping the domain layer as the single import surface used by the endpoint (same
pattern as the existing `price_product` wrapper).

**3. Endpoint `POST /api/pricing/preview`** (in `backend/app/main.py`)

Request `PricingPreviewRequest` (new in `schemas.py`):

```python
class PricingPreviewRequest(BaseModel):
    product_type: str
    product_kwargs: dict[str, Any] = Field(default_factory=dict)
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    market: PricingEnvironmentSnapshot = Field(default_factory=PricingEnvironmentSnapshot)
    compute_greeks: bool = True
```

Response `PricingPreviewOut` (new in `schemas.py`):

```python
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

Behavior: read-only; **no audit record** (exploratory pricing). Maps the
`QuantArkResult` into `PricingPreviewOut`. A build/price failure returns
`ok=False` with `error` populated and HTTP 200 (the failure is a domain result,
not a transport error) — consistent with how the agent `price_product` tool
surfaces `{ok, data, error}`. Validation errors in the request body fall through
to FastAPI's normal 422.

### Frontend

**1. `BookingPricingCompanion.tsx`** (new, + co-located `.css`)

Props (the same draft inputs the visual companion gets, plus market context):

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
```

State & behavior:

- Local editable market-input state: `spot`, `rate`, `volatility`,
  `dividendYield`, `valuationDate` (string inputs, parsed on submit).
- On mount / when `underlying` changes: fetch `GET /api/underlying-pricing-
  defaults/{underlying}` to pre-fill rate/vol/q; pre-fill spot from `latestSpot`;
  valuation date defaults to today (`YYYY-MM-DD`). User edits are preserved
  across term changes (only re-pre-fill when the underlying itself changes).
- **Price** button → `POST /api/pricing/preview` with the current
  `productType`/`terms`/`engineName` and the edited market snapshot,
  `compute_greeks: true`. Disabled while a request is in flight ("Pricing…").
- Results area: a **Price (PV)** tile plus Greek tiles (Delta, Gamma, Vega,
  Theta, Rho, RhoQ) using the existing `Tile` primitive and `formatSignedNumber`.
  Shows the resolved engine name. If `ok=false`, render the `error` in the
  page's existing error style; if `ok=true` but `greeks_error` is set, render the
  PV plus a non-blocking note that Greeks were unavailable.
- Token-only styling per `frontend/UI_STYLE_GUIDE.md`; theme/density automatic.

> Note: `GreeksSummary` is intentionally **not** reused — it is portfolio-oriented
> (Market Value, PnL, Delta Cash, Gamma Cash) and assumes committed notional. The
> companion shows raw per-unit Greeks, so it composes `Tile` directly.

**2. `Booking.live.tsx` right rail** → `Tabs`

Replace the bare `<BookingVisualCompanion .../>` in `.wl-booking__right` with a
`Tabs` (`frontend/src/components/Tabs.tsx`, Radix-backed):

- `TabsList`: "Payoff" | "Pricing".
- `TabsContent value="payoff"`: existing `BookingVisualCompanion` (unchanged
  props).
- `TabsContent value="pricing"`: `BookingPricingCompanion` with the draft + market
  props.
- Default tab: "Payoff" (preserves current first-paint).

**3. API client / types**

Add `PricingPreviewRequest` / `PricingPreviewOut` / `PricingGreeks` types to
`frontend/src/types.ts`; call via the existing `api<T>()` helper (no client
changes needed beyond the call site).

## Data flow

```
Booking form state (productType, terms, engineName, underlying, currency)
        │
        ▼  (props)
BookingPricingCompanion ── pre-fill ──► GET /api/underlying-pricing-defaults/{underlying}
        │                                        (rate, vol, q)  + latestSpot + today
        │  user edits market inputs, clicks Price
        ▼
POST /api/pricing/preview { product_type, product_kwargs: terms, engine_name,
                            market: {spot, rate, volatility, dividend_yield,
                                     valuation_date, currency}, compute_greeks }
        ▼
pricing.price_product_preview → quantark.price_product_with_greeks
        ▼   (build once: product + engine + env)
{ ok, price, engine, product_type, greeks?, greeks_error?, error? }
        ▼
Companion renders PV tile + Greek tiles  (or error / greeks-unavailable note)
```

## Error handling

- **Build/price failure** (bad terms, engine mismatch): endpoint returns
  `ok=false` + `error`; companion shows the error message, no tiles.
- **Greek failure but valid price:** `ok=true`, `greeks=null`, `greeks_error` set;
  companion shows PV + a muted "Greeks unavailable" note.
- **Network/422:** surfaced via the `api<T>()` rejection path → companion error
  state.
- **Missing market data:** if `latestSpot` is null and no defaults resolve, fields
  fall back to `PricingEnvironmentSnapshot` defaults (spot 100, vol 0.20, rate
  0.03, q 0.0); the user can edit before pricing.

## Testing

**Backend**
- `price_product_with_greeks`: vanilla European option returns a finite price and
  a full Greeks dict; assert `rho_q` maps from `dividend_rho`.
- Graceful degradation: a product/engine combination that prices but whose Greek
  calc raises returns `ok=true`, `price` finite, `greeks=None`, `greeks_error`
  set. (If no natural case exists, monkeypatch `GreeksCalculator.calculate` to
  raise.)
- Endpoint test: `POST /api/pricing/preview` happy path → `ok=true` with price +
  greeks; malformed terms → `ok=false` with `error`.

**Frontend**
- Pricing tab pre-fills market inputs from a mocked `underlying-pricing-defaults`
  response + `latestSpot`.
- Clicking Price posts to `/api/pricing/preview` (mocked) and renders the PV +
  Greek tiles.
- Error response renders the error and no tiles.
- Tab switch shows/hides Payoff vs Pricing.

## Out of scope (YAGNI)

- Persisting or auditing preview prices.
- Auto-reprice on change / debounced live pricing.
- Pricing-parameter-profile selection UI (pre-fill uses underlying defaults only).
- Sensitivity/scenario grids, charts of Greeks vs spot (Greeks Landscape already
  covers that for booked positions).
- Multi-currency conversion of the PV.

## Files touched

- `backend/app/services/quantark.py` — add `price_product_with_greeks`.
- `backend/app/services/domains/pricing.py` — add `price_product_preview` wrapper
  + `__all__`.
- `backend/app/schemas.py` — add `PricingPreviewRequest`, `PricingGreeks`,
  `PricingPreviewOut`.
- `backend/app/main.py` — add `POST /api/pricing/preview` endpoint.
- `backend/tests/` — service + endpoint tests (new test module).
- `frontend/src/components/BookingPricingCompanion.tsx` (+ `.css`) — new.
- `frontend/src/components/BookingPricingCompanion.test.tsx` — new.
- `frontend/src/routes/Booking.live.tsx` — wrap right rail in `Tabs`.
- `frontend/src/types.ts` — add preview request/response types.

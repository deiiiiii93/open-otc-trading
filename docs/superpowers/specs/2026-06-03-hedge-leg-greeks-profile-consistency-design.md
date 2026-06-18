# Profile-consistent Greeks for listed-option hedge legs

**Date:** 2026-06-03
**Status:** Approved design
**Branch context:** `fix/hedging-review-followups`

## Problem

When the hedging Strategy tab prices a listed-option leg, its Greeks are computed
under **flat, hard-coded market assumptions** rather than the market the book was
actually risked under.

`hedging_legs._option_unit_greeks` builds the pricing snapshot as
`PricingEnvironmentSnapshot(spot=float(spot))`, setting only `spot`. Volatility,
rate, and dividend yield therefore fall back to the schema defaults
(`schemas.py`: `volatility=0.20`, `rate=0.03`, `dividend_yield=0.0`). The option's
market `LAST` price is never used and no implied vol is backed out.

The hedge targets, by contrast, come from `hedging_greeks.aggregate_by_underlying`,
which reads the latest completed `RiskRun`. That run was priced under a specific
**pricing parameter profile** (`RiskRun.pricing_parameter_profile_id`), with a
per-underlying `(spot, rate, dividend_yield, volatility)`.

Aggregated Greeks are only meaningful under the parameters they were computed with.
Hedging that exposure with option legs priced under a different (flat 20% vol)
market produces inconsistent leg Greeks and therefore an inconsistent lot solution.

## Goal

Price listed-option hedge legs under the **same pricing parameter profile** the
underlying risk run used — specifically the same `rate`, `dividend_yield`, and
`volatility` for the hedged underlying. `spot` is already profile-sourced in the
hedge flow (`target["spot"]` traces back to the profile), so the missing pieces are
r / q / vol.

### Non-goals
- No volatility smile/surface: a single per-underlying vol is used across all
  strikes and expiries (unchanged from today, now consistent with the book).
- No ETF-vs-index basis modeling: an ETF option inherits its mapped index
  underlying's profile vol. This is the accepted simplification, matching the
  deliberate hedge-map association.
- No change to the risk-run subsystem, its metrics schema, or its tests.
- Maturity handling for option legs is unchanged (`days/365`, floored at 1 day).

## Decisions (locked)

- **Refuse, no silent default.** When the run's profile does not supply usable
  r / q / vol for the underlying, the option leg is treated as **unpriceable**
  (excluded from the solve, with a warning naming the missing fields). The flat
  `3% / 0% / 20%` defaults are **never** used for a hedge leg.
- **Ambiguous → refuse.** When a profile holds multiple conflicting values for a
  field across rows of the same underlying, that field is ambiguous and is treated
  the same as missing.
- **Approach A** — resolve params in the greeks-aggregation layer. The change is
  confined to the hedging module; the merged risk subsystem is untouched.

## Architecture

Four focused changes. The profile read happens once, in the layer that already
owns the run.

```
RiskRun (pricing_parameter_profile_id)
        │
        ▼
hedging_greeks.aggregate_by_underlying
  loads profile rows once; per underlying attaches
  market{spot,rate,div,vol} + params_ok + missing_params
        │  (uses)
        ▼
pricing_profiles.resolve_underlying_market_params   ← new pure resolver
        │
        ▼
domains.hedging_strategy.solve_hedge
  builds option_market (or None + reason); forwards to price()
        │
        ▼
hedging_legs.price(..., option_market=, option_market_error=)
  futures/spot: unchanged (delta = mult·spot)
  options: price under option_market, or refuse with the reason
```

## Components

### 1. `pricing_profiles.resolve_underlying_market_params` (new, pure)

```python
@dataclass(frozen=True)
class UnderlyingMarketParams:
    rate: float | None
    dividend_yield: float | None
    volatility: float | None
    missing_fields: tuple[str, ...]      # fields with no value across the rows
    ambiguous_fields: tuple[str, ...]    # fields with >1 conflicting value

    @property
    def ok(self) -> bool:
        return not self.missing_fields and not self.ambiguous_fields

def resolve_underlying_market_params(
    rows: list[PricingParameterRow], symbol: str
) -> UnderlyingMarketParams: ...
```

**Field-wise collapse.** Filter rows whose `symbol` matches the underlying (reusing
`_normalize_pricing_match_key`). For each field in
`("rate", "dividend_yield", "volatility")`, build the set of distinct non-null
values, each quantized via `round(value, 12)` to absorb float noise:

- exactly one distinct value → use it
- zero → field added to `missing_fields`
- more than one → field added to `ambiguous_fields`

Rationale: the default-profile builder writes the same underlying-level params onto
every trade row for an underlying, so the common case collapses to one distinct
value per field. Genuinely conflicting xlsx rows refuse. Field-wise (not row-wise)
collapse keeps the rule simple and treats the params as the underlying-level values
they conceptually are.

This function is pure over a row list — directly unit-testable with no DB.

### 2. `hedging_greeks.aggregate_by_underlying`

- When `run.pricing_parameter_profile_id` is set, load that profile's rows once
  (e.g. via the profile's `rows` relationship).
- For each underlying bucket, call `resolve_underlying_market_params(rows, u)` and
  attach to the entry:
  - `market`: `{"spot": <bucket spot>, "rate": r, "dividend_yield": q,
    "volatility": vol}` when `params.ok`, else `null`.
  - `params_ok`: bool.
  - `missing_params`: list combining missing + ambiguous fields, each with a short
    reason (e.g. `"volatility (missing)"`, `"rate (ambiguous)"`).
- Add `pricing_parameter_profile_id` to the top-level return.
- **Profile-less run** (`pricing_parameter_profile_id is None`): every underlying
  gets `params_ok=False`, `market=null`, `missing_params=["risk run not priced
  under a pricing parameter profile"]`.

Existing fields (`status`, `risk_run_id`, `created_at`, `stale`, `targets`, `spot`)
are preserved; the additions are additive.

### 3. `hedging_legs.price()`

New signature:

```python
def price(
    session, legs, *, spot: float,
    option_market: PricingEnvironmentSnapshot | None = None,
    option_market_error: str | None = None,
) -> list[dict[str, Any]]: ...
```

- Futures/spot legs: **unchanged** — `delta = mult·spot`, `gamma = vega = 0`,
  `priced_ok=True`.
- Option legs:
  - `option_market` provided → `_option_unit_greeks(inst, option_market)`; same
    cash scaling as today (`delta = δ·mult·S`, `gamma = γ·mult·S²/100`,
    `vega = vega_unit·mult`, with `S = option_market.spot`).
  - `option_market is None` → `priced_ok=False`, `price_error=option_market_error`
    (or a generic "pricing parameters unavailable" if no message supplied),
    Greeks zeroed.

`_option_unit_greeks(inst, market)` changes from constructing its own spot-only
snapshot to accepting the passed `PricingEnvironmentSnapshot`. Everything else
(transient `Position`, `EuropeanVanillaOption`, `BlackScholesEngine`,
`compute_position_greeks`, maturity) is unchanged.

Note: `option_market.spot` and the `spot` arg refer to the same underlying spot and
will be equal in the normal flow; option legs use `option_market.spot` for both
pricing and cash scaling to keep a single source within the option branch.

### 4. `domains/hedging_strategy.solve_hedge`

- Read the chosen `target` entry (now carrying `market` / `params_ok` /
  `missing_params`).
- Build:
  - `option_market = PricingEnvironmentSnapshot(spot=spot, rate=..,
    dividend_yield=.., volatility=..)` when `params_ok`, else `None`.
  - `option_market_error` = a message naming the missing/ambiguous fields when not
    ok.
- Pass both into `hedging_legs.price(...)`.
- Unpriceable option legs already surface through the existing `warnings` channel
  (`price_error`) and are excluded from `usable`/the solve. Delta-only (futures)
  strategies still solve.
- Add `pricing_parameter_profile_id` to the `solve_hedge` return for transparency.

## Error handling

| Situation | Result |
|---|---|
| Profile supplies r/q/vol for the underlying | Option legs priced under those params |
| Some/all of r/q/vol missing | Option legs `priced_ok=False`; warning lists missing fields; defaults never used |
| Conflicting values across rows (ambiguous) | Same refusal as missing |
| Run had no pricing parameter profile | Option legs refuse; futures/spot legs unaffected |

## Data flow (happy path)

1. `solve_hedge(portfolio_id, underlying, strategy)` calls
   `aggregate_by_underlying`.
2. Aggregation reads the latest `RiskRun`, loads its profile rows, resolves
   `(rate, div, vol)` for `underlying`, returns the target entry with a complete
   `market`.
3. `solve_hedge` builds `option_market` from that `market` and proposes/forwards
   legs to `price()`.
4. `price()` computes option-leg Greeks under `option_market` (book-consistent vol)
   and futures/spot legs under `spot`.
5. The solver consumes the leg Greeks and returns integer lots — now consistent
   with the exposure being hedged.

## Testing

- **`resolve_underlying_market_params`** (pure unit, `tests/test_pricing_profiles_resolution.py`):
  - all rows agree → `ok`, correct values.
  - some field null everywhere → that field in `missing_fields`.
  - conflicting values for a field → that field in `ambiguous_fields`.
  - many identical complete rows → collapses to one distinct value, `ok`.
  - float-noise rows (e.g. `0.20` vs `0.20 + 1e-15`) → treated as equal.
- **`aggregate_by_underlying`**:
  - profile with complete params → entry carries the resolved `market`,
    `params_ok=True`; top-level `pricing_parameter_profile_id` set.
  - profile-less run → `params_ok=False`, `market=null`, reason populated.
- **`hedging_legs.price`**:
  - option leg priced under a non-default vol yields Greeks that differ from the
    20%-default result (characterization uses a non-default vol, e.g. 0.35).
  - `option_market=None` → option leg `priced_ok=False` with the supplied message.
  - futures leg unaffected by `option_market`.
- **`solve_hedge`** (integration):
  - profile supplies vol → solve uses it (leg Greeks reflect profile vol).
  - profile missing vol → option legs excluded + warning; a futures-only
    (delta-only) strategy still solves cleanly.

## Files touched

- `backend/app/services/pricing_profiles.py` — new resolver + dataclass.
- `backend/app/services/hedging_greeks.py` — enrich aggregation output.
- `backend/app/services/hedging_legs.py` — `price()` / `_option_unit_greeks()`.
- `backend/app/services/domains/hedging_strategy.py` — build & forward
  `option_market`.
- Tests:
  - `tests/test_pricing_profiles_resolution.py` — **new**, pure resolver unit tests
    (no existing file targets `app.services.pricing_profiles` directly; the resolver
    is pure, so it gets a focused home).
  - `tests/test_hedging_greeks.py` — aggregation output.
  - `tests/test_hedging_legs.py` — `price()` behavior.
  - `tests/test_hedging_solve_orchestration.py` — `solve_hedge` integration.

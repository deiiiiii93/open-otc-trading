# Term-Structure Curves for Pricing Parameters — Design

- **Date:** 2026-07-22
- **Status:** Approved (design) — pending implementation plan
- **Author:** desk agent + operator (brainstorm)
- **Related code:** `backend/app/services/assumptions.py`, `backend/app/services/pricing_profiles.py`,
  `backend/app/services/risk_engine.py`, `backend/app/services/quantark.py`,
  `frontend/src/routes/InstrumentsAssumptions.tsx`

## 1. Summary

Add **term-structure (curve) support** for the three market inputs `r` (rate), `q`
(dividend yield), and `vol` (volatility). A curve is a set of `(tenor, value)` points
that vary the parameter by time-to-maturity.

Curves are authored **per underlying** on the durable `Instrument` baseline. Pricing
itself stays **flat and unchanged**: a new *materialize* step walks the open positions,
linearly interpolates each curve at each trade's time-to-maturity, and writes the
resulting flat `r/q/vol` into a normal **Pricing Parameters** profile
(`PricingParameterProfile` / `PricingParameterRow`). The existing pricing path then
consumes that flat profile exactly as today.

This is a **materialize-then-price** architecture. Its two wins:

1. The interpolated flat `r/q/vol` become a concrete, inspectable, audited artifact (a
   normal Pricing Parameters profile) instead of an interpolation hidden inside the
   pricing engine.
2. It touches **zero** pricing code — no change to `risk_engine` resolution or the
   `quantark._market_kwargs → build_pricing_env_from_market_kwargs` seam. QuantArk stays
   scalar-in.

## 2. Decisions (from brainstorm)

| Decision | Choice | Rationale |
|---|---|---|
| **Curve storage home** | `Instrument` (aka `UnderlyingPricingDefault`), per underlying | A term structure is a market/instrument property, not a per-trade one. Durable baseline. |
| **Where flat params land** | Trade-keyed `PricingParameterProfile` / `PricingParameterRow` | Concrete, inspectable, audited; pricing path unchanged. |
| **Tenor axis** | Standard **labels** (1M / 3M / 6M / 1Y / …) mapped to year-fractions | Trader-familiar; labels are entry/display only, interpolation runs on the year axis. |
| **Generate scope** | **All open positions** (mirrors `build_assumptions_set`) | One action regenerates the whole desk's flat params from the curves. |
| **Authoring** | **Both** desk operator (UI) and agent tools | |
| **Visualization** | Instruments → Assumptions tab **only** (editable curve + chart) | Where the curve lives/is authored. Pricing Parameters stays a flat table. |
| **(a) Curve consumers** | Curve feeds **only** the generate step | `build_assumptions_set` stays flat-scalar-based and untouched. YAGNI boundary. |
| **(b) Missing-curve param** | Fall back to the flat `Instrument` scalar so the generated row stays complete | An incomplete row would drop the whole trade to the assumption-set path. |
| **(c) Trade tenor** | ACT/365 (calendar days / 365) from valuation_date → maturity_date | Matches the `PricingEnvironmentSnapshot` default day-count. |

## 3. Architecture & data flow

```
Instrument (per underlying)                       ── new curve storage
  rate_curve / dividend_yield_curve / volatility_curve
      : list[{ "tenor": <label>, "value": <float> }] | None
        │
        │  generate_pricing_parameters_from_curves(session, name?, valuation_date?)
        │  ── new service (UI button + agent tool, WRITE + HITL)
        │  for each OPEN position where position_requires_pricing_params(p):
        │     tenor_years = ACT/365(valuation_date → maturity_date)
        │     r  = interp(rate_curve,             tenor_years)  or  Instrument.rate
        │     q  = interp(dividend_yield_curve,   tenor_years)  or  Instrument.dividend_yield
        │     σ  = interp(volatility_curve,       tenor_years)  or  Instrument.volatility
        ▼
PricingParameterProfile(source_type="curve")
  + PricingParameterRow(source_trade_id, symbol, rate, dividend_yield, volatility)   ← flat
        │
        ▼
risk_engine._pricing_position_context → quantark._market_kwargs → QuantArk   ── ZERO changes
```

Row resolution at pricing time is already exact: `resolve_pricing_parameter_row_for_position`
(`pricing_profiles.py:237`) matches on `source_trade_id` first, and `Position.source_trade_id`
(`models.py:750`) is the key the generate step writes.

## 4. Data model & migration `0050`

Three **nullable JSON columns** on `instruments` (the `Instrument` /
`UnderlyingPricingDefault` model, `models.py:640`, flat trio at `:671-673`), mirroring the
existing flat scalars:

- `rate_curve`
- `dividend_yield_curve`
- `volatility_curve`

Each is `list[{"tenor": <label:str>, "value": <float>}] | None`. `None` or `[]` means "no
curve — use the flat scalar." Migration `0050_instrument_term_structure_curves` adds the
three columns (down_revision = `0049`), all nullable, no backfill.

Alembic note (repo convention): migrations use migration-local Core tables, not ORM
models/services (see `migrations_no_live_orm_services` memory). Plain `op.add_column` with
a JSON type — no ORM import.

## 5. Tenor labels & interpolation — new `backend/app/services/term_structure.py`

A single, self-contained, dependency-free module.

### 5.1 Label map

```python
TENOR_YEARS: dict[str, float] = {
    "1W": 7 / 365, "2W": 14 / 365,
    "1M": 1 / 12, "2M": 2 / 12, "3M": 3 / 12, "6M": 6 / 12, "9M": 9 / 12,
    "1Y": 1.0, "18M": 18 / 12, "2Y": 2.0, "3Y": 3.0, "5Y": 5.0,
}
```

- Labels are **entry/display only**; each converts to a year-fraction once.
- Validation: unknown label → error (→ 422 at the API); labels within a curve are
  deduped; points are sorted by year-fraction.

### 5.2 Interpolation

```python
def interpolate_curve(points, target_years) -> float | None:
    # normalize labels -> years, sort
    # empty / None                -> None      (no curve)
    # single point                -> its value (constant)
    # target <= first.years       -> first.value   (flat extrapolation)
    # target >= last.years        -> last.value    (flat extrapolation)
    # otherwise                   -> linear between the bracketing points
```

Pure, deterministic, exhaustively unit-tested. Linear in `(year_fraction, value)` per the
"linear interpolation" requirement (no log/forward transforms at this stage).

## 6. Generate service — `generate_pricing_parameters_from_curves`

Location: extend `backend/app/services/pricing_profiles.py` (or a thin new module that
reuses its `create_profile` / `upsert_rows`). Signature mirrors `build_assumptions_set`:

```python
def generate_pricing_parameters_from_curves(
    session, *, name: str | None = None, valuation_date: datetime | None = None,
) -> PricingParameterProfile
```

Steps:

1. Scope = **open positions** — iterate the open `Position` rows directly (the same
   open-position scope `build_assumptions_set` derives its underlyings from, but we need the
   `Position` objects themselves for `source_trade_id` + maturity, not just the underlying
   symbols returned by `_open_position_underlyings`).
2. Skip positions where `not position_requires_pricing_params(position)` (delta-one:
   futures/spot ignore r/q/vol — `pricing_profiles.py:327`).
3. Per remaining position:
   - underlying → `Instrument` row → its three curves + flat scalars.
   - `maturity_date` via `compatibility_terms_for_position(position)["product_kwargs"]["maturity_date"]`
     (the seam `_build_termsheet_for_position` already reads, `quantark.py:831`).
   - `tenor_years = (maturity_date − valuation_date).days / 365` (ACT/365; guard ≤ 0 → clamp
     to the near end so interpolation returns the front point).
   - For each param: `interpolate_curve(curve, tenor_years)`; if `None`, fall back to the
     flat `Instrument` scalar. Result must be non-None for all three or the trade is
     **unfilled**.
4. **Unfilled contract:** if any scoped trade has a param with neither a curve nor a flat
   scalar, raise `ValueError({"unfilled_trades": [...]})` — same shape/behavior as
   `build_assumptions_set`'s `unfilled_underlyings`. Nothing is silently dropped.
5. Write one `PricingParameterProfile(source_type="curve", name=name or default,
   valuation_date=…)` and one `PricingParameterRow` per trade
   (`source_trade_id=position.source_trade_id`, `symbol=position.underlying`, flat
   `rate/dividend_yield/volatility`) via the existing `create_profile` / `upsert_rows`.
6. Each row's `source_payload` records provenance: `{"generated_from": "instrument_curves",
   "instrument_id": …, "tenor_years": …, "interp": {"rate": {...}, "dividend_yield": {...},
   "volatility": {...}}}` where each `interp` block notes source (`curve` | `flat_scalar`)
   and, for curve, the bracketing points used.

## 7. API & agent tools (desk + agent, both author)

### 7.1 Curve read/write (per underlying)

- **Schema:** add the three curve fields to `UnderlyingPricingDefaultOut`
  (`schemas.py:1530`) and `UnderlyingPricingDefaultUpdate` (`schemas.py:1547`). A point is
  `{ tenor: str, value: float }`; a curve is `list[point] | None`. Server-side validation:
  known labels only, dedup, `value` finite (vol > 0).
- **Route:** extend `PUT /api/underlying-pricing-defaults/{underlying:path}`
  (`main.py:2394`) to accept/return curves (GET list at `main.py:2379` returns them too).
- **Tools:** extend `get_instrument_pricing_defaults` (return curves) and
  `set_instrument_pricing_defaults` (accept curves) in
  `backend/app/tools/assumptions.py`. Existing capability gating and provenance preserved.

### 7.2 Generate

- **Route:** `POST /api/pricing-parameter-profiles/from-curves` — body `{ name?,
  valuation_date? }` → `PricingParameterProfileOut`. Writes audit event
  `pricing_parameters.generated_from_curves` (mirrors the `pricing_parameters.imported`
  audit at `main.py:2511`). On `unfilled_trades`, return a 4xx with the list.
- **Tool:** `generate_pricing_parameters_from_curves(name?, valuation_date?)` in
  `backend/app/tools/pricing_profiles.py` — WRITE + HITL (same class as
  `create_pricing_parameter_profile`, `pricing_profiles.py:116`). Must be added to
  `DEEP_AGENT_TOOL_NAMES` (registered ≠ allowlisted — see the dynamic-subagents gotcha).

## 8. Frontend — Instruments → Assumptions tab

Files: `frontend/src/routes/InstrumentsAssumptions.tsx`, `Instruments.live.tsx` (assumptions
tab state at `:62-71`), `InstrumentsAssumptions.css`. **Read `frontend/CLAUDE.md` before any
UI work — token-only styling is non-negotiable.**

Per underlying:

- **Curve editor** for each of r / q / vol: a list of `{ tenor-label (select from allowed
  labels), value (NumberInput step="any") }` rows with add / edit / remove; empty list =
  no curve (falls back to flat scalar). The existing flat-scalar inputs remain.
- **Curve chart** (recharts 3.8.1, already a dep; precedent `GreeksLandscape.tsx`,
  `Backtest.tsx`, `ChartAsset.tsx`): x-axis = tenor ordered by year-fraction, y = value.
  Either three series on one chart or three small charts — decided at build time by
  legibility. Theme-token colors only.
- **"Generate pricing parameters from curves"** button → `POST …/from-curves`; on success
  surface the created profile (link/toast) and any `unfilled_trades`.
- Data layer in `Instruments.live.tsx`: PUT curves to the defaults route; POST to the
  generate route.

## 9. Error handling

- Unknown tenor label / non-finite value / vol ≤ 0 → 422 (server validation).
- Empty or single-point curve → interpolates gracefully (None / constant).
- Missing-curve param → flat-scalar fallback; if both absent → trade reported `unfilled`.
- Delta-one positions skipped (no r/q/vol needed).
- `tenor_years ≤ 0` (matured/same-day) → clamp to front point.
- Generate with no open positions → same error as `build_assumptions_set`
  (`"no open positions in scope"`).

## 10. Testing

**Backend**
- `term_structure`: label map, dedup/sort, bracketing, both flat-extrapolations, single
  point, empty/None, unknown label rejection.
- generate service: open-position scope, delta-one skip, curve→flat fallback, provenance
  payload, `source_trade_id` keying, `unfilled_trades`, ACT/365 tenor, ≤0 clamp.
- schema/route: PUT curves round-trip + validation errors; `POST …/from-curves` happy path
  + unfilled + no-scope; audit event written.
- tool: `set/get_instrument_pricing_defaults` curves; `generate_…` tool (WRITE/HITL,
  allowlisted).

**Frontend**
- vitest: curve editor add/edit/remove, label select, chart renders from points, generate
  button calls endpoint and shows result/unfilled.
- `npx tsc --noEmit`.

## 11. Docs & rollout

- `CHANGELOG.md` under `[Unreleased]` (Keep a Changelog).
- New `CLAUDE.md` subsystem section: "Term-structure curves for pricing parameters" —
  storage on `Instrument`, the generate materialize step, `source_type="curve"`, the
  curve-feeds-only-generate boundary, and the `DEEP_AGENT_TOOL_NAMES` gotcha.
- `README.md` if judged user-facing.
- No `config/agent_channels.yaml` changes.

## 12. Non-goals (YAGNI)

- **No** full term-structure pricing (passing whole curves to QuantArk) — flat at this
  stage, by explicit requirement ("still use flat parameters when pricing").
- **No** change to `build_assumptions_set` — curves do not (yet) feed the assumption-set
  build. Deferred; the flat scalars remain its source. (Natural future extension.)
- **No** curve authoring on the Pricing Parameters page — it stays a flat table.
- **No** XLSX import of curves (Assumptions/defaults are built/edited, not imported).
- **No** non-linear interpolation, forward/zero-rate transforms, or absolute-date tenors.
```

# Hedging Module — Part 2: Strategy Engine, Agent & UI — Design

**Date:** 2026-06-02
**Status:** Approved design, pending implementation plan
**Depends on:** Part 1 (hedging instrument management) — branch `feature/hedging-instrument-management`, currently unmerged.

---

## 1. Context

Part 1 delivered the **hedging instrument universe**: an AKShare-loaded catalog
(`hedge_instruments`), a hand-marked durable allowed map (`hedge_map_entries`,
keyed by `(exchange, contract_code)` with a `reconcile_status`), the
`HedgeUniverseResolver`, the async loader, and the master-detail marking UI.
Its API surface includes `GET /api/hedging/map` (groups by underlying; entries
carry `reconcile_status` but **not** `multiplier`/`last_price`/`strike` — those
live on the `HedgeInstrument` catalog row and must be re-joined).

Part 2 turns that allowed map into a **hedging strategy engine**: choose a
hedging objective → propose/confirm instruments → size integer lots with a
solver → book the hedge. It is exposed through both the **desk agent** and a
**UI panel** on the existing Hedging page.

### 1.1 Branch base (decided)

Main has advanced ~36 commits past Part 1's base (`fa49e1e` → `be68ed7`),
including the **currency-convention** merge and currency-aware risk bucketing —
the exact surface Part 2 touches (risk metrics, `delta_cash`/`gamma_cash`,
`position.currency`). Therefore:

- **Phase 0:** merge `feature/hedging-instrument-management` → `main` (resolve
  conflicts; Part 1 is tested — 43 BE + 525 FE green), then create a fresh
  worktree `feature/hedging-strategy` off the updated `main`.
- **Alembic head collision (must fix during the merge):** main and Part 1 both
  define revision **`0020`** off `0019` — `0020_currency_convention` (main) and
  `0020_hedging_instrument_catalog` (Part 1). Merging produces two heads.
  Linearize: re-chain Part 1's migration to `down_revision =
  "0020_currency_convention"` and rename it `0021_hedging_instrument_catalog`,
  then run `alembic upgrade head` + the migration tests to confirm a single
  head. (Part 2's own migration is therefore `0022_hedge_bands`.)
- This spec is committed as the first action on the `feature/hedging-strategy`
  branch.

## 2. Goals / Non-goals

**Goals**
- Four hedging strategies (below), solved **per underlying**, as a lexicographic
  (hard-then-soft) goal program with **integer lots**.
- Per-underlying greek targets read from the latest completed `RiskRun`.
- Engine proposes hedge legs from Part 1's active allowed map; user can swap.
- Persisted per-underlying band config with confirm-or-override at solve time.
- Book the sized hedge into the **same portfolio**, tagged and linked to the
  source `risk_run_id`.
- Delivered as: a deterministic backend engine (Phase A), desk-agent tools +
  workflow (Phase B), and a Hedging-page panel (Phase C).

**Non-goals (this cycle)**
- Cross-underlying / beta-weighted aggregation into a single index hedge
  (strategies are strictly per-underlying).
- Hedge rebalancing schedules, P&L attribution of hedges, or auto-unwind.
- Higher-order / path-dependent greeks beyond delta, gamma, vega.
- A standalone band-administration page (band edits ride inside the workflow/panel).

## 3. The four strategies

All solved **per underlying**, lexicographically: satisfy the **hard** tier
first (feasibility), then minimize **soft**-tier band violation, then a
parsimony tie-break (smallest total lots).

| Strategy | Hard bands (tier 1) | Soft bands (tier 2) |
|---|---|---|
| `delta_neutral` | delta_cash | — |
| `delta_neutral_enhanced` | delta_cash | gamma_cash, vega |
| `delta_gamma_neutral` | delta_cash, gamma_cash | vega |
| `full_neutral` | delta_cash, gamma_cash, vega | — |

Conventions:
- **delta_cash** = `δ · S`, **gamma_cash** = `γ · S² / 100` (matches
  `quantark.py::_cash_greek_contributions`). **vega** is raw model units (not cash).
- A **hard band** is a feasibility constraint (`|residual| ≤ band`); a **soft
  band** is a penalty minimized once the hard tier holds.
- **One band width per greek per underlying** — a greek is hard in one strategy
  and soft in another (e.g. gamma), but never both at once, so a single width
  serves both roles; the strategy decides enforcement.

## 4. Architecture & module structure

End-to-end flow (one portfolio, broken down by underlying):

```
RiskRun (latest completed, portfolio P) — per-position rows incl. NEW spot, delta_cash, gamma_cash
  → [1] hedging_greeks.aggregate_by_underlying  → {underlying: {Δcash, Γcash, vega}} + staleness
  → [2] hedging_legs.propose(underlying, strategy)  → candidate legs from active map (catalog re-join), tagged by greek role
        (user may swap/remove legs)
  → [3] hedging_legs.price(legs, market)  → per-CONTRACT greeks
  → [4] band confirm (HITL): persisted per-underlying defaults → confirm/override
  → [5] hedging_solver.solve(targets, leg_greeks, bands, strategy_spec)  → staged MILP → quantities, residuals, status
  → [6] proposal returned
  → [7] book (HITL): build_product + book_position per leg, into P, tagged + linked to risk_run_id
```

### 4.1 New backend modules (focused, independently testable)

| File | Responsibility |
|---|---|
| `services/hedging_greeks.py` | Read latest completed `RiskRun` for a portfolio → aggregate per-position rows into per-underlying `{delta_cash, gamma_cash, vega}`; report staleness/absence. Pure read. |
| `services/hedging_legs.py` | Propose candidate legs from the active map (re-join `HedgeInstrument` for `multiplier`/`strike`/`expiry`/`option_type`); price per-contract greeks (futures/spot closed-form, options via `price_product`). Tag each leg's greek role. |
| `services/hedging_solver.py` | **Pure, DB-free** staged MILP (`scipy.optimize.milp`). Inputs: targets + leg-greek matrix + bands + ordered strategy tiers; outputs: integer quantities, residuals, status/binding-greek. |
| `services/hedging_strategy_registry.py` | Declarative strategy → ordered tiers `[{hard greeks}, {soft greeks}]` (mirrors the `FamilyContracts` pattern). The 4 strategies live here. |
| `services/domains/hedging_strategy.py` | Domain facade orchestrating [1]–[7]; the single entry point the tools + API + UI call. |

### 4.2 Touched backend

- `services/quantark.py::_risk_row` — **additive, `spot` only**:
  `delta_cash`/`gamma_cash` are *already* stored per row (because
  `RISK_GREEK_KEYS = GREEK_KEYS + CASH_GREEK_KEYS` and the row emits every
  `RISK_GREEK_KEYS`); only `spot` is missing. Adding it lets leg pricing reuse
  the exact spot the run priced at (consistency), not for cash-greek recompute.
- `models.py` + Alembic migration (`0022_hedge_bands`, down_revision
  `0021_hedging_instrument_catalog` after the Phase-0 re-chain) — new `HedgeBand`
  table (migration-local Core tables, idempotent via `inspect()`, per convention).
- `tools/hedging.py` (new) + `tools/__init__.py` — agent `@tool` wrappers added
  to `QUANT_AGENT_TOOLS`.
- `skills/workflows/hedging/hedge-portfolio/SKILL.md` (new) +
  `skills/references/hedging/strategy.md` (new); update
  `test_skills_catalog.py`, `test_skills_catalog_v2.py`,
  `test_workflow_skills_phase3.py` (sets + counts).
- `services/deep_agent/personas.py` — add `/skills/workflows/hedging/` to the
  **risk** persona's domains.
- `main.py` — `/api/hedging/{hedgeable,bands,solve,book}` endpoints.
- `frontend/src/routes/Hedging.{tsx,live.tsx,css}` — Hedge-Strategy tab.

### 4.3 Phasing (one spec, three shippable phases)

- **Phase 0** — merge Part 1 → main; fresh worktree off main; commit this spec.
- **Phase A — Engine (backend only):** risk-row fields → greek aggregation →
  `HedgeBand` model/config/defaults → leg proposal+pricing → strategy registry →
  staged MILP → orchestration facade → atomic booking. Endpoints:
  hedgeable/bands/solve/book. *Ships a working deterministic engine over HTTP.*
- **Phase B — Agent:** `tools/hedging.py` + `QUANT_AGENT_TOOLS` wiring +
  `hedge-portfolio` SKILL.md + reference doc + capability gating + persona +
  catalog-test updates. *Ships chat-driven hedging.*
- **Phase C — UI:** Hedge-Strategy tab on the Hedging page. *Ships the desk panel.*

## 5. Data model

### 5.1 `HedgeBand` (migration `0022_hedge_bands`)

```
hedge_bands
  id                pk
  underlying_id     FK underlyings.id, nullable   -- NULL row = global defaults
  delta_cash_band   float    -- absolute cash, underlying's currency
  gamma_cash_band   float    -- absolute cash
  vega_band         float    -- raw vega units (NOT cash)
  currency          str      -- display; matches the underlying's position currency
  updated_at, updated_by
  UNIQUE(underlying_id)       -- one row per underlying + one NULL defaults row
```

**Resolution:** per-underlying row if present, else the global-defaults row. The
HITL confirm-or-override step shows the resolved widths; a per-request override
is **transient** (passed to the solver, not persisted) unless the user saves it
via `set_hedge_bands` / `PUT /api/hedging/bands`.

### 5.2 Hedge tagging — no migration on `positions`

Each booked leg carries, in `Position.source_payload`:

```json
{ "hedge": { "is_hedge": true, "risk_run_id": 123, "strategy": "delta_gamma_neutral",
             "leg_role": "delta" | "gamma_vega", "hedged_underlying": "000905.SH",
             "solved_at": "2026-06-02T..." } }
```

plus `source_trade_id = "HEDGE:<risk_run_id>:<n>"` for cheap SQL filtering.
Because legs are real positions, the **next** risk run nets them automatically;
re-solving later hedges only the residual (correct incremental behavior).

### 5.3 Risk-row additive field (`quantark.py::_risk_row`) — `spot` only

`delta_cash` and `gamma_cash` are **already stored** on each row: `RISK_GREEK_KEYS
= GREEK_KEYS + CASH_GREEK_KEYS`, and `_risk_row` emits every `RISK_GREEK_KEYS`
key from the position-level `greek_values` (cash contributions are folded in at
`quantark.py:1058-1064`). So the aggregator can sum `delta_cash`/`gamma_cash`/
`vega` straight from `RiskRun.metrics["positions"]` with **zero drift** and no
risk-pipeline change for the cash greeks.

The one missing field is **`spot`** — add it to the row (a new `spot` param on
`_risk_row`, passed `position_market.spot`) so leg pricing can use the exact spot
the run priced each underlying at (consistency between book targets and hedge-leg
greeks). Per-position greeks are position-level (`compute_position_greeks` ×
`position.quantity`, `quantark.py:1054`), so per-underlying aggregation is a plain
sum across that underlying's rows. Update any golden/snapshot tests that assert
the exact row key-set.

## 6. Greeks aggregation (`hedging_greeks.py`)

- Read the latest `RiskRun` with status in `{completed, completed_with_errors}`
  for the portfolio (reuse `risk_svc.get_latest_run`).
- Group `metrics["positions"]` by `underlying`; per underlying sum
  `delta_cash`, `gamma_cash`, and raw `vega` → the solver targets `T`.
- **Staleness/absence:** if no completed run exists → structured
  `{status: "no_risk_run"}`. If the run is older than a freshness threshold (or
  the desk asks), surface it and prompt to run a new risk first — do not silently
  solve against a stale run. (Threshold default flagged as an open decision, §13.)

## 7. Leg proposal & pricing (`hedging_legs.py`)

- **Propose:** from `get_map(underlying)` filtered to `reconcile_status ==
  "active"`, re-join `HedgeInstrument` for economics. Default selection per
  strategy:
  - delta role → nearest-liquid **future** (or spot/ETF) for the underlying;
  - gamma/vega role → a near-ATM option with adequate remaining expiry.
  Each proposed leg is tagged with the greek role it serves; a swap that removes
  the only gamma/vega-capable leg for a gamma/vega-hard strategy will surface as
  infeasibility at solve time.
- **Price per-contract greeks:**
  - futures/spot: `d = multiplier · S`, `g ≈ 0`, `v ≈ 0` (closed form);
  - options: `δ, γ, vega` from `price_product` (Black-Scholes), cash-scaled
    consistently (`d = δ·S·mult`, `g = γ·S²/100·mult`, `v = vega·mult`).
  - A leg that fails to price is **excluded with a warning** (user can swap).
- Legs are priced under the same market snapshot used for the targets where
  available (consistency); spot is taken from the position/market inputs.

## 8. The solver (`hedging_solver.py`) — staged lexicographic MILP

Per underlying, selected legs `i = 1..n`.

**Variables**
- `q_i ∈ ℤ`, `|q_i| ≤ Q_max` (configurable cap; long or short)
- `s_g ≥ 0` hard-band slack (each hard greek `g`)
- `w_g ≥ 0` soft-band violation (each soft greek `g`)
- `a_i ≥ 0` with `a_i ≥ q_i`, `a_i ≥ −q_i` (= |q_i|, for parsimony)

**Per-contract contributions** (signed, per +1 lot): `d_i, g_i, v_i` (§7).

**Residuals:** `R_Δ = T_Δ + Σ qᵢdᵢ`, `R_Γ = T_Γ + Σ qᵢgᵢ`, `R_v = T_v + Σ qᵢvᵢ`.

**Band-with-slack** (linear; soft uses `w_g`):
```
 R_g − s_g ≤ B_g
−R_g − s_g ≤ B_g
```

**Lexicographic tiers** (solve, freeze optimum as a constraint, solve next):
```
Tier 1 (hard) : min Σ s_g  → S1*.  if S1* > tol ⇒ INFEASIBLE; report greeks with s_g>tol (binding) + shortfall.
                add Σ s_g ≤ S1* + ε
Tier 2 (soft) : min Σ w_g  → V2*.   add Σ w_g ≤ V2* + ε
Tier 3 (parsi): min Σ a_i  → smallest total lots meeting tiers 1–2
```

Implementation notes:
- `scipy.optimize.milp` (HiGHS), `integrality=1` on `q_i`, `Bounds`, and
  `LinearConstraint`s for the band-with-slack and the tier-freeze constraints.
- Tiers come from the strategy registry as an **ordered list**, so the model
  generalizes (e.g. a future "delta strictly above gamma" need only split the
  hard tier into two ordered tiers — no solver rewrite).
- **Outputs:** integer `q_i`, residuals `R_*`, per-greek in/out-of-band flags,
  `status ∈ {feasible, infeasible}`, and on infeasibility the binding greeks +
  shortfalls. Warn if any `q_i` hits `Q_max`.

**Strategy registry** (`hedging_strategy_registry.py`):
```
delta_neutral          tiers = [hard {delta}]
delta_neutral_enhanced tiers = [hard {delta}, soft {gamma, vega}]
delta_gamma_neutral    tiers = [hard {delta, gamma}, soft {vega}]
full_neutral           tiers = [hard {delta, gamma, vega}]
```

## 9. Booking (`hedging_strategy.py` → `book_hedge`)

- For each solved leg with non-zero qty: `build_product` (family `Futures` /
  `SpotInstrument` / `EuropeanVanillaOption` etc.) then `book_position` into the
  **same portfolio** as the hedged book.
- **Atomic per underlying:** all legs in one transaction; if any leg fails, roll
  back and report — never a half-hedge.
- Each position tagged per §5.2 (hedge metadata + `source_trade_id` convention,
  linked to `risk_run_id`).
- Returns booked position ids + the product summaries.

## 10. Band config (`hedging_strategy.py` / domain)

- `get_hedge_bands(underlying_id?)` → resolved bands (override else defaults).
- `set_hedge_bands(underlying_id, {delta_cash_band, gamma_cash_band, vega_band})`
  → upsert a per-underlying override (or the NULL defaults row).
- Confirm-or-override is a HITL checkpoint in the workflow/panel before solving.

## 11. Agent surface

### 11.1 Tools (`tools/hedging.py` → `QUANT_AGENT_TOOLS`)

Thin adapters over `hedging_strategy.py` (no logic duplication):

| Tool | Group | Purpose |
|---|---|---|
| `get_hedgeable_underlyings(portfolio_id)` | DOMAIN_READ | underlyings + `{Δcash, Γcash, vega}` + staleness from latest `RiskRun`. |
| `propose_hedge(portfolio_id, underlying, strategy, legs?, bands?)` | DOMAIN_READ | Loopable workhorse: propose legs if none, price, run MILP, return quantities + residuals + feasibility/binding-greek. No persistence. |
| `book_hedge(portfolio_id, underlying, risk_run_id, strategy, legs_with_qty)` | DOMAIN_WRITE (HITL) | Atomically book all legs, tagged + linked to `risk_run_id`. |
| `get_hedge_bands(underlying_id?)` | DOMAIN_READ | Resolved band config. |
| `set_hedge_bands(underlying_id, bands)` | DOMAIN_WRITE | Persist a band override. |

### 11.2 Workflow + catalog impact

`skills/workflows/hedging/hedge-portfolio/SKILL.md` — `domain: hedging`,
`workflow_type: action`, `allowed_envelopes: [desk_workflow]`,
`may_escalate_to: [desk_async]`, `required_context: [portfolio_id]`,
`optional_context: [underlying, strategy, legs, bands]`, `write_actions: true`,
`confirmation_required: true`. Procedure: pick strategy → `propose_hedge` →
review/swap legs → confirm/override bands → re-solve → review residuals/
feasibility → confirm → `book_hedge`. Plus `skills/references/hedging/strategy.md`
(the 4 strategies, band semantics, cash-greek conventions).

Owned by the **risk** persona (it already carries `positions`/`pricing`/
`portfolios`, and the flow begins from a `RiskRun`). Catalog-test updates:
add `/skills/workflows/hedging/` to `risk_sources`; add
`/workflows/hedging/: {hedge-portfolio}` to `expected`; bump risk-persona
catalog counts — in `test_skills_catalog_v2.py`, `test_skills_catalog.py`,
`test_workflow_skills_phase3.py`.

## 12. API (`main.py`) — shared by tools + UI

```
GET  /api/hedging/hedgeable?portfolio_id=   -> underlyings + greeks + staleness
GET  /api/hedging/bands?underlying_id=      -> resolved bands (override | defaults)
PUT  /api/hedging/bands/{underlying_id}     -> persist override
POST /api/hedging/solve                      -> {portfolio_id, underlying, strategy, legs?, bands?} -> proposal
POST /api/hedging/book                       -> {portfolio_id, underlying, risk_run_id, strategy, legs_with_qty} -> booked ids
```

All structured-result, never-throw (see §14). Schemas added to `schemas.py`.

## 13. UI panel (Phase C)

A tab toggle on the existing Hedging page: **Allowed Instruments** (Part 1) |
**Hedge Strategy** (Part 2). The strategy tab:

```
Portfolio [P ▾]  Strategy [delta-gamma-neutral ▾]    RiskRun: 2026-06-01 ⚠ stale → [Run risk]
─ per underlying (from latest RiskRun) ─────────────────────────────────────
 000905.SH   Δcash 1,240,000   Γcash 88,000   vega 6,500
   legs:  IC2406 (future·Δ) [swap▾][✕]   IO2406-C-5600 (option·Γv) [swap▾][✕]   [+ add leg]
   bands: Δ ±500k  Γ ±50k  vega ±4k  [edit]                         [ Solve ]
   ── result ──  IC2406 −12   IO2406-C-5600 +40   residual Δ 3,100 ✓  Γ 1,800 ✓  vega 900(soft) ✓   [ Book ]
```

Reuses Part 1's presentational + `.live.tsx` split and `usePageContextReporter`.
Infeasible solves render the binding greek in red with the shortfall; missing/
stale run shows the banner + run-risk affordance.

## 14. Error handling

Errors are **structured results, never exceptions**:
- no completed RiskRun → `{status: "no_risk_run", message}`;
- infeasible hard bands → `{status: "infeasible", binding: [{greek, shortfall}]}`;
- leg won't price → excluded with a warning;
- booking is **atomic per underlying** (all legs or none).

## 15. Testing

- **Solver** (pure, the centerpiece): exact lots + residuals for all 4
  strategies; infeasible (gamma-hard, no option leg); parsimony (smallest total
  lots); tight vs loose bands; zero target; `Q_max` hit. *Characterization tests
  use non-default input values (avoid the vacuous-test trap).*
- **Greeks aggregation:** synthetic `RiskRun.metrics` → per-underlying cash/vega;
  staleness & missing-run paths.
- **Legs:** proposal from active map (catalog re-join); futures closed-form vs
  option `price_product`; price-failure exclusion.
- **Bands:** CRUD + default resolution. **Risk-row:** `spot/delta_cash/
  gamma_cash` present & correct.
- **Booking:** tag + linkage + atomicity; nets on next run (integration).
- **Tools:** thin-adapter shape tests. **Catalog:** updated sets/counts.
- **UI:** vitest presentational + live (fetch stub) — propose/swap/solve/book,
  infeasible state, staleness banner.

Migration tests run via `python -m pytest` (repo root on `sys.path` for the
`backend.alembic...` import), per the Part-1 gotcha.

## 16. Open decisions (confirm at spec/plan review)

1. **Staleness threshold** for "risk run too old" — propose: warn but allow if a
   completed run exists; only block when none exists. (Default: no hard age cap;
   surface the run's age and let the desk decide.)
2. **`Q_max`** default lot cap per leg — propose a generous default (e.g.
   1,000,000) purely to bound the MILP; warn on contact.
3. **`configure-hedge-bands`** as a standalone workflow — deferred; band edits
   ride inside `hedge-portfolio` via `set_hedge_bands`.
4. **Trader persona** also owning hedging — deferred; risk persona only for now.

## 17. Future (deferred)

Cross-underlying/beta-weighted hedging, hedge rebalancing & unwind, hedge P&L
attribution, separate band-admin UI, distinct hard/soft band widths per greek.

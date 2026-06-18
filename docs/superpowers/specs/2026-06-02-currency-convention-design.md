# Currency Convention — Design Spec

**Date:** 2026-06-02
**Status:** Approved (brainstorming) — ready for implementation plan
**Branch target:** new worktree (`feature/currency-convention`)

## Problem

The agent app handles risk across positions denominated in different currencies (e.g. CNY A-share structured products and USD index options), but currency is currently modeled inconsistently and ignored where it matters most:

- **Four disagreeing sources of truth.** `Portfolio.base_currency` defaults `USD`; `Underlying.currency` defaults `CNY`; `Product.currency` defaults `USD`; and `Position` has *no* currency column at all — `position_pricer._source_currency()` scrapes it from a Chinese source-payload field (`交易规模单位`) and falls back to `CNY`. "What currency is this position?" has four possible answers.
- **Currency-blind aggregation.** `calculate_portfolio_risk()` (`backend/app/services/quantark.py:1139–1148`) sums every position's `market_value`/`pnl`/`gross_notional`/greeks into one flat `totals` dict regardless of currency — it literally adds CNY PV to USD PV. The per-position market snapshot *carries* a `currency` field, but aggregation discards it. This is a latent correctness bug.
- **No report-currency notion.** Each `AgentSession` sees a `ContextPack` (task inputs, snapshot ids, tools, persona prompt) but there is no session-level "show me everything in currency X" setting anywhere in the plumbing.

No quanto (cross-currency) products are supported yet, so for now a position's settlement currency equals its underlying's currency. The data model should make that invariant explicit (and warn on violation) while leaving room for quanto later.

## Goals

1. Give every position its **own** currency attribute, sourced from the booked trade, validated (softly) against the underlying.
2. Make risk aggregation **currency-aware**: group money metrics by currency, never silently cross-sum, and always emit a **per-currency breakdown**.
3. Introduce a **report currency** per conversation (agent thread): either a concrete ISO code (convert everything into it) or a `by_position` sentinel (aggregate only within same-currency groups).
4. Perform FX conversion **deterministically in code** (never LLM arithmetic), driven by the agent through a dedicated tool, using **reproducible** valuation-snapshot FX rates.
5. Surface FX rate management in the existing **Market Data** page.

## Non-Goals

- Quanto / cross-currency payoff support (the position-currency model is built to *accommodate* it later; we do not implement it).
- Multi-currency P&L attribution, FX hedging, or FX greeks (rho-FX). Out of scope.
- Historical FX time-series / curves. We store point-in-time rates keyed by `as_of_date`, resolved as "latest ≤ valuation_date."

## Architecture: "Honest engine, converting presenter"

Two layers, deliberately split (the rejected alternative was baking `report_currency` into `calculate_portfolio_risk` — it welds viewing-policy into the data layer, can't express `by_position`, and forces FX plumbing into every risk consumer):

1. **Deterministic, currency-honest engine.** `calculate_portfolio_risk()` groups money metrics by each position's currency and refuses to silently sum across currencies. Output always includes a per-currency breakdown. No `report_currency` awareness.
2. **Deterministic converting presenter.** A separate `convert_currency` agent tool collapses the per-currency groups into one currency *when the agent asks*, driven by the thread's `report_currency`. It does pure code arithmetic; the LLM only decides *whether* and *to what* to convert.

```
Position(currency) ──┐
                     ├─> calculate_portfolio_risk ─> { by_currency, shared, totals|null, positions[] }
Underlying(currency) ┘                                          │
                                                                ▼
AgentThread(report_currency) ─> ContextPack/persona prompt ─> agent decides:
    • ISO code  -> call convert_currency(by_currency, target, valuation_date) -> converted totals
    • by_position -> present by_currency groups as-is, no cross-sum
                                                                ▲
FxRate table (base, quote, rate, as_of_date) ──> resolver "latest ≤ valuation_date" ──┘
```

---

## Section 1 — Data model changes

### 1a. `Position.currency` (new column)

- `Mapped[str] = mapped_column(String(8), nullable=False, server_default="CNY")` on `backend/app/models.py::Position`.
- **Populated at booking time from the booked trade** (source payload → `Product.currency`), *not* from the underlying. Source-of-truth = the ticket, to keep the door open for quanto.
- **Soft validation** against `underlying_record.currency`: on mismatch, emit a warning `"Position and Underlying should have same currency for non-quanto products"` into the existing mapping/booking warning channel. **Never a hard block.**
- `_source_currency()` / `_source_currency_for_position()` (in `position_pricer.py` / `quantark.py`) are **rewired to read `position.currency` first**, collapsing today's four-way disagreement into one field. The source-payload scrape remains only as a backfill fallback.

### 1b. `FxRate` (new table) — valuation-snapshot FX source

Columns:

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `base_currency` | `String(8)` | 1 unit of base = `rate` units of quote |
| `quote_currency` | `String(8)` | |
| `rate` | `Float` | |
| `as_of_date` | `DateTime`, indexed | |
| `pricing_parameter_profile_id` | FK → `pricing_parameter_profiles.id`, nullable | optional linkage to a valuation snapshot |
| `source` | `String(40)` | `manual` \| `akshare` \| `wind` |
| `created_at` / `updated_at` | `DateTime` | |

**Resolver** (`fx_rate_as_of(session, base, quote, valuation_date) -> float | None`):

- Returns the rate with the **latest `as_of_date ≤ valuation_date`** for `(base, quote)`. Re-running an old risk run reproduces identical numbers.
- **Inverse derivation:** if `(base, quote)` is absent but `(quote, base)` exists, return `1 / rate`.
- **Identity:** `base == quote` → `1.0`, no lookup.
- Missing → `None` (caller decides; see Section 6).

### 1c. `AgentThread.report_currency` (new column)

- `Mapped[str] = mapped_column(String(16), nullable=False, server_default="by_position")`.
- Either an ISO code (`"USD"`) or the sentinel `"by_position"`.
- Default seeded from the active portfolio's `base_currency` when a portfolio context exists at thread creation; otherwise `"by_position"`.

### 1d. Reconcile currency defaults

- Align `Underlying.currency` and `Product.currency` defaults and document the no-quanto invariant: `Position.currency == Product.currency == Underlying.currency` is expected, **warned but not enforced**. (Choose a single default; `CNY` matches the primary A-share book — confirm during implementation. The change is a default/`server_default` alignment, not a data rewrite of existing non-default rows.)

### Migration notes

- Per project rule ([[migrations_no_live_orm_services]]): the alembic migration uses **migration-local Core tables**, not ORM models/services.
- **Backfill `Position.currency`** for existing rows in priority order:
  1. booked currency from source payload,
  2. `Product.currency` (joined via `product_id`),
  3. existing `_source_currency()` scrape logic (reimplemented inline against Core tables),
  4. `"CNY"` last resort.
- Rows that fall to step 3/4 are tagged `currency_inferred: true` (stored where the row carries provenance — e.g. an existing JSON metadata field or the risk-row output) so they're filterable for later cleanup.

---

## Section 2 — Currency-aware aggregation

`calculate_portfolio_risk()` (`quantark.py`) stops returning a single currency-blind `totals` and returns:

```jsonc
{
  "by_currency": {                    // money metrics grouped by position currency
    "CNY": { "market_value": …, "pnl": …, "gross_notional": …,
             "vega": …, "theta": …, "rho": …, "rho_q": …,
             "delta_cash": …, "gamma_cash": …, "one_day_var_proxy": …,
             "position_count": 12 },
    "USD": { … }
  },
  "shared": {                         // currency-INVARIANT metrics — one set
    "delta": …, "gamma": …, "delta_proxy": …
  },
  "totals": { … } | null,             // back-compat flat sum ONLY when exactly one
                                      //   currency present; null + "mixed_currency": true
                                      //   when >1 currency (kills the silent CNY+USD bug)
  "mixed_currency": false,
  "currencies": ["CNY", "USD"],
  "positions": [ { …, "currency": "CNY" }, … ]   // every row tagged
}
```

### Dimension map (declarative, single dict in `quantark.py`)

Tags derived from dimensional analysis (`delta = ∂Price/∂Spot` is money/money = a currency-invariant ratio; `vega/theta/rho` differentiate a money numerator by a dimensionless input → money):

| dimension | metrics | behavior |
|---|---|---|
| **money** | `market_value, pnl, gross_notional, vega, theta, rho, rho_q, delta_cash, gamma_cash, one_day_var_proxy` | grouped under `by_currency`; FX-convertible |
| **shared** (ratio / underlying-unit) | `delta, gamma, delta_proxy` | one `shared` block; never converted |

`by_currency` **is** the per-currency breakdown; it is always present, including in coherent mode (coherent mode additionally gets a converted roll-up from the tool in Section 3).

### Behavior preservation

A consistency net asserts: for a **single-currency** portfolio, `Σ by_currency[ccy][key]` reconciles against the legacy flat total for every money key (characterization guard — see Section 7). Non-money metrics (`delta/gamma/delta_proxy`) aggregate exactly as today; the feature does not change their cross-underlying behavior. When `totals` is populated (single-currency), it carries the **full** legacy key set (money + shared) so existing readers keep working; `shared` is additionally present in both modes.

### Downstream consumers of the risk output

The output-shape change ripples beyond the engine. These must handle `by_currency` / `shared` / nullable `totals`:

- `backend/app/services/reports.py::_build_report_payload` (consumes `calculate_portfolio_risk` directly) and the report rendering of risk totals.
- `RiskRun.metrics` persistence + `RiskRunOut` schema (`models.py` / `schemas.py`) — the JSON blob now stores the new shape; the schema must not assume a flat `totals`.
- Frontend risk display (whichever route renders `RiskRunOut.metrics`) — show per-currency groups and, when `report_currency` is an ISO code in an agent-driven view, the converted roll-up.
- The risk persona's reading of risk artifacts (so it narrates `by_currency` correctly).

Back-compat is preserved for single-currency portfolios (populated `totals`); the breaking case is mixed-currency, where readers must use `by_currency`.

---

## Section 3 — The `convert_currency` tool

A new deterministic agent tool, registered with the other risk/portfolio tools (`backend/app/tools/` + the deep-agent tool scope for the relevant persona/task type).

```
convert_currency(by_currency, target_currency, valuation_date)
  -> {
      "totals":        { …money metrics summed into target_currency… },
      "fx_rates_used": { "CNY->USD": 0.1389, … },
      "as_of":         "2026-06-02",
      "missing":       [ "JPY->USD" ]   // pairs with no rate ≤ valuation_date
    }
```

- **Pure code arithmetic.** Each money metric of each currency group is multiplied by `fx_rate_as_of(ccy, target, valuation_date)` and summed. The LLM never multiplies.
- `shared` metrics pass through untouched (the tool may echo them or the agent reads them directly from the risk output).
- If a needed rate is missing, the tool converts what it can, lists the gap in `missing`, and does **not** fabricate a rate.
- Identity (`ccy == target`) uses rate `1.0`; inverse pairs derived by the resolver.

Kept a **separate tool** (not folded into the risk-run output) so FX math is isolated to one reactive tool the prompt invokes, matching the codebase's reactive-tool pattern.

---

## Section 4 — Report-currency plumbing into agent sessions

1. **`AgentThread.report_currency`** is read when a workflow/task spins up.
2. It is injected into `assemble_context_pack()` → the `stable_payload` (alongside `task_brief`, `tools_scope`, `prompt_revision_hash`, etc., in `context_assembler.py::_context_pack_payload`). Because that payload is content-hashed for caching, **changing currency correctly re-derives the context** instead of serving a stale pack, and the value is auditable in the `context_pack_assembled` event.
3. The **persona prompt** gains a declarative instruction block:
   - report_currency is an ISO code → *"All monetary risk is reported in {ccy}. Call `convert_currency` on any `by_currency` block before presenting aggregate money figures; never sum across currencies yourself."*
   - report_currency is `by_position` → *"Present each currency group from `by_currency` separately; do not convert or cross-sum monetary metrics."*
4. **API/UI:** `AgentThreadOut` exposes `report_currency`; `PATCH /api/chat/threads/{id}` accepts updates. Default seeded from the active portfolio's `base_currency`.

Only `convert_currency` does FX math; the prompt decides *when* to invoke it (reactive-tool pattern, cf. [[agent_envelope_contracts_orphaned]] escalation behavior).

---

## Section 5 — FX rates on the Market Data page

Mirrors the existing `MarketDataProfile` machinery (inline endpoints in `backend/app/main.py`, `record_audit` on mutations, AKShare fetch path).

### Backend (inline `@app.*` in `main.py`, `/api/market-data/...`)

- `GET /api/market-data/fx-rates` — list, newest `as_of_date` first.
- `POST /api/market-data/fx-rates` — manual entry.
- `POST /api/market-data/fx-rates/akshare` — fetch a pair's spot via the existing akshare forex capability (**in initial scope**).
- `DELETE /api/market-data/fx-rates/{id}`.
- New `FxRateOut` / `FxRateCreate` Pydantic schemas in `schemas.py`.
- Each mutation writes a `record_audit` event (`market_data.fx_rate.manual` / `.akshare` / `.delete`), matching the profile endpoints.

### Frontend (`frontend/src/routes/MarketData.tsx`)

- Add a third tab **"FX Rates"** beside `historical` / `describe`.
- A `Table` of `base / quote / rate / as_of / source`, an add-rate `Modal` form, and a **"Fetch from AKShare"** button reusing the page's existing fetch-spinner pattern.
- New methods in `frontend/src/api/client.ts`.

---

## Section 6 — Error handling & edge cases

| Case | Behavior |
|---|---|
| **Missing FX rate** for a needed pair ≤ valuation_date | `convert_currency` converts what it can, returns the un-convertible currencies in `missing`, surfaces them un-converted with a flag. Agent reports the gap (*"CNY→USD has no rate as-of 2026-06-02; CNY leg shown native"*) — never invents a rate. |
| **Position currency ≠ underlying currency** | Soft warning at booking (`"Position and Underlying should have same currency for non-quanto products"`); booking proceeds. |
| **Mixed-currency portfolio, legacy flat `totals`** | `totals` = `null`, `mixed_currency` = `true`; consumers must read `by_currency`. Single-currency portfolios keep a populated `totals` (back-compat). |
| **`report_currency` = ISO code, single currency present & equal** | `convert_currency` is identity (rate 1.0), no FX lookup. |
| **Inverse / identity pairs** | `USDCNY` ⇒ `CNYUSD = 1/rate` derived; `USD→USD = 1.0`, no row needed. |
| **Backfill ambiguity** (no booked ccy, no product, no underlying) | Falls to `"CNY"`, row tagged `currency_inferred: true`. |

---

## Section 7 — Testing strategy

- **Dimension-map / aggregation unit tests:** mixed CNY+USD portfolio asserts money metrics land in the right `by_currency` bucket and `shared` (delta/gamma/delta_proxy) stays single-set.
- **Characterization guard** (per [[project_booking_product_builders]] "non-default values" lesson): single-currency portfolio proves `Σ by_currency == legacy totals` byte-for-byte, using **non-default rates** so a vacuous pass can't hide.
- **`convert_currency` tests:** known rates → exact converted figures; `missing` pair handling; identity/inverse; `shared` pass-through untouched.
- **FxRate resolver tests:** "latest ≤ valuation_date" selection, inverse derivation, reproducibility (old valuation_date → old rate).
- **Plumbing test:** thread `report_currency` lands in the ContextPack `stable_payload` and changes its content hash; `by_position` vs ISO drives the right persona instruction.
- **Booking warning test:** mismatched position/underlying currency emits the warning without blocking.
- **Migration test:** backfill picks booked → product → scrape → CNY in priority order.
- **API/frontend:** FxRate CRUD + akshare endpoints (incl. audit events); `MarketData.test.tsx` covers the new FX Rates tab render + add-rate flow.

---

## Open items to confirm during implementation

1. The single aligned default currency for `Underlying`/`Product`/`Position` (proposed `CNY`).
2. Exact storage location for the `currency_inferred` provenance flag (existing JSON metadata vs risk-row field).
3. Which persona/task types receive `convert_currency` in their tool scope (risk/reporting personas).

## Affected files (anchor list, not exhaustive)

- `backend/app/models.py` — `Position.currency`, `AgentThread.report_currency`, new `FxRate`.
- `backend/alembic/versions/` — new migration (Core-table backfill).
- `backend/app/services/quantark.py` — dimension map, `by_currency`/`shared` aggregation, `fx_rate_as_of` resolver (or a small `fx.py`).
- `backend/app/services/position_pricer.py` — rewire `_source_currency`.
- `backend/app/services/domains/booking.py` — soft currency-mismatch warning at booking.
- `backend/app/tools/` — `convert_currency` tool + scope registration.
- `backend/app/services/deep_agent/context_assembler.py` — inject `report_currency` into `stable_payload`.
- `backend/app/services/deep_agent/personas.py` / prompts — report-currency instruction block.
- `backend/app/main.py` — FxRate CRUD + akshare endpoints, thread `report_currency` PATCH.
- `backend/app/schemas.py` — `FxRateOut`/`FxRateCreate`, `AgentThreadOut.report_currency`, `RiskRunOut.metrics` new shape.
- `backend/app/services/reports.py` — consume `by_currency`/`shared`/nullable `totals`.
- `frontend/src/routes/MarketData.tsx` + `api/client.ts` — FX Rates tab.
- Frontend risk display route — render per-currency groups + converted roll-up.
- `tests/` — per Section 7.

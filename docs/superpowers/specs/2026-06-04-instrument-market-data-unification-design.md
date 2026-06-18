# Unified Instrument Master, Market Data Layer & Pricing-Parameter Split

**Date:** 2026-06-04
**Status:** Approved in design review (visual companion session); spec awaiting user review

## Problem

Three structural gaps, all visible in the current-procedure graph built from code:

1. **Two disjoint instrument masters.** `Underlying` (registry; curated; `status` gates
   bulk market-data fetch) and `HedgeInstrument` (listed contracts; own AKShare loader;
   own scope rule = open positions ∪ hedge map, ignoring registry status). The only
   bridge is a foreign key. The same real-world instrument can exist twice: live
   example `LH2609.DCE` is an underlyings row (id 2, referenced by two open
   AmericanOptions) and would also be enumerated as a hedge contract — two unlinked
   identities. Bonus live bug found: its `akshare_asset_class` is `"index"` (wrong),
   which silently breaks both bulk fetch and hedge family resolution.
2. **Four uncoordinated quote stores.** `MarketDataProfile` (page snapshots),
   `HedgeInstrument.last_price` (refreshed only by instrument load), build-default's
   transient AKShare fetch (used once, never persisted), `PositionMarketInput`
   (xlsx imports). Plus `FxRate`. Each has its own staleness clock; none sees the
   others.
3. **Pricing-parameter profiles conflate observation with assumption.** A
   `PricingParameterRow` stores spot (an observable) next to r/q/vol (desk
   assumptions), and mixes trade-keyed rows with underlying-keyed rows in one table.
   Consequences: build-default must fetch AKShare itself (hence the
   `failed_akshare_underlyings` 400), producer C (`create-spot-pricing-profile`)
   exists purely as a workaround, and profiles go stale the moment spot moves even
   though their assumptions remain valid.

## Locked decisions (user-approved)

- **One combined spec** for all three layers (not three sub-projects); the
  implementation plan phases it so each layer lands working and tested.
- **Architecture A′: role-based single instrument table.** `kind` means *security
  type*, never role. "Underlying" and "hedge instrument" are relationships
  (positions point at it / the hedge map points at it). Chosen over a two-table
  split because the LH2609 case proves underlying-ness is a role: the same listed
  contract is an OTC underlying *and* a hedge candidate. A two-table model would
  need duplicated, synced rows — the disease itself.
- **UI consolidation into one Instruments page** with four tabs (Registry ·
  Allowed Hedges · Market Data · Assumptions), replacing the Underlyings and
  Market Data pages and absorbing the Hedging page's Allowed Instruments module.
  The Hedging page keeps only strategy (bands / solve / book).
- **A separate new Pricing Params page** owns the trade-keyed xlsx import; its
  r/q/vol rows apply to positions (by `source_trade_id`), and its spot values are
  emitted to the quote store. The Assumptions tab is instrument-level only.
- **Equivalence is the spec:** this refactor moves *where* numbers live, not what
  they are. Risk totals on a pinned portfolio must be byte-identical pre/post.

## Data model

### New: `instruments` (absorbs `underlyings` + `hedge_instruments`)

| column | type / notes |
|---|---|
| `id` | PK. **Preserves the `underlyings` id space** for migrated rows so `positions.underlying_id` values never change. |
| `symbol` | UNIQUE, canonical: `000905.SH`, `LH2609.DCE`, `510500.SH`; contracts use `CODE.EXCHANGE`. |
| `contract_code` | nullable; exchange-native code (`LH2609`, `IO2606-C-4000`) — what enumerators and AKShare speak. |
| `name` | nullable display name. |
| `kind` | `index \| etf \| stock \| future \| listed_option \| spot` — security type, **never** role. |
| `exchange` | nullable (indices use pseudo-exchange suffix conventions). |
| `currency` | default `CNY`. |
| `status` | unified lifecycle `draft → active → expired → retired`. Position-sync creates `draft` (inferred mapping needs curation); contract loads create `active` (terms are authoritative from the exchange feed); past-expiry / delisted → `expired`; manual removal → `retired`. |
| `akshare_symbol`, `akshare_asset_class` | fetch routing; deliberately distinct from `kind` (LH2609: kind=`future`, fetch class=`futures`). |
| `series_root` | futures/options family root (`LH`, `IC`, `IO`, `510500`). |
| `expiry` (date), `multiplier`, `strike`, `option_type` (`CALL\|PUT`) | terms where applicable; strike/option_type only meaningful for `listed_option` — enforced by service-level validator, no DB CHECK. |
| `parent_id` | self-FK → the **contractual underlier**, whenever it is itself an instrument: index future `IC2606` → `000905.SH`; index option `MO2606-C-6000` → `000852`; ETF option → the ETF; commodity option → the specific future (`LH option → LH2609.DCE`). Commodity futures → NULL (physical underlier). Indices/ETFs/stocks/spot → NULL. |
| `source` | provenance: `manual \| position \| market_data \| hedge_load \| pricing_profile`. |
| `loaded_at` | last seen by a contract load; drives expire-missing exactly as today's hedge loader. |
| `created_at`, `updated_at` | |

Constraints: `UNIQUE(symbol)`; index on `(parent_id)`, `(kind, status)`,
`(series_root, kind)`.

**`parent_id` is NOT hedge-routing.** 510500 ETF options hedge 000905.SH exposure,
but their parent is the ETF. The "what proxies what" map (`_INDEX_FAMILIES` in
`hedging_universe.py`) is correlation knowledge, not contract structure, and stays
as config. Conflating the two edges would poison both.

Roles are computed, never stored:
- *underlying* := open positions reference it (`positions.underlying_id`)
- *allowed hedge* := `hedge_map_entries.instrument_id` points at it
- *has assumptions / has quotes* := rows exist in those stores

### New: `market_quotes` — the single observation store

| column | notes |
|---|---|
| `id` | PK |
| `instrument_id` | FK → instruments. One plain FK, no polymorphism. |
| `as_of` | datetime of the observation. |
| `price` | float — the headline number consumers need. |
| `price_type` | `close \| last \| mid`. |
| `source` | `akshare \| hedge_load \| xlsx_import \| manual \| pricer_fallback \| legacy`. |
| `market_data_profile_id` | nullable FK — provenance link to the fetch event. |
| `meta` | JSON: volume, fetch params, `{trade_id, profile_id}` for xlsx provenance, etc. |
| `created_at` | |

Resolver `latest_quote(instrument_id, as_of ≤ valuation_date)`: max `as_of`,
tie-break by max `id`. All sources equal — source is diagnostics, not priority
(unification happens at write time, not read time). Same shape as `fx_rate_as_of`.

### New: `assumption_sets` + `assumption_rows` — instrument-level r/q/vol

- `assumption_sets`: `id`, `name`, `valuation_date`, `status`, `summary` JSON,
  timestamps. **Built, never imported.**
- `assumption_rows`: `set_id` FK, `instrument_id` FK, `rate`, `dividend_yield`,
  `volatility`, `source_payload` JSON (per-field provenance: explicit default vs
  inherited + from where), timestamps.

### Renamed: `underlying_pricing_defaults` → `instrument_pricing_defaults`

Keyed by `instrument_id` (string `underlying` column dropped; symbol via join).
Still the manual per-instrument r/q/vol override layer that feeds builds. Note:
assumptions legitimately attach to `kind=future` rows (the book prices options on
LH2609) — no kind-guard.

### Reshaped: `pricing_parameter_profiles` / `pricing_parameter_rows`

Now **trade-keyed position pricing params only** (the name finally matches the
content):
- rows: `source_trade_id` (required, non-empty), `symbol` (denormalized display),
  `instrument_id` nullable FK (resolved at import: trade_id → position →
  underlying_id, else symbol lookup, else NULL + flagged "dormant"), `rate`,
  `dividend_yield`, `volatility`, `source_row`, `source_payload`.
- **`spot` column dropped** (after its values are backfilled into `market_quotes`).
- Profiles keep `valuation_date`, import summary in `summary` JSON
  (rows applied / dormant / quotes emitted / spot conflicts).

### Reshaped: `MarketDataProfile`

Survives as the **fetch-event audit** (full history payload, charts) that *emits*
a `market_quotes` row for its headline spot on creation. Truth moves to quotes.

### Kept: `hedge_map_entries`

Gains `instrument_id` FK (replaces matching by `exchange + contract_code`;
those columns retained as display denormalization). Reconcile semantics unchanged
(`active`/`stale`, purge-stale endpoint).

### Untouched: `FxRate`

Already a clean valuation-snapshot table. Folding FX pairs into `instruments` is
YAGNI (consistent with the currency-convention project decisions).

### Dropped after migration

`underlyings`, `hedge_instruments`, `position_market_inputs` (folded into pricing
params + quotes), `PricingParameterRow.spot`, the
`create-spot-pricing-profile` producer.

## Migration (alembic `0022`, migration-local Core tables per house rule)

1. Create `instruments`; copy `underlyings` rows **1:1 preserving ids**.
2. Merge `hedge_instruments`: canonical symbol = `contract_code + '.' + exchange`;
   on symbol collision with an existing instrument → **identity merge** (update
   terms onto that row — the LH2609 case); else insert with a fresh id.
   Status map: `live → active`, `expired → expired`. Resolve `parent_id` from the
   family spec where derivable; else NULL (curable later in Registry).
3. Re-target `positions.underlying_id` FK to `instruments` (SQLite
   `batch_alter_table` rebuild; **values unchanged**).
4. Backfill `market_quotes` (no observation is lost; all land with honest `as_of`):
   - each `MarketDataProfile` headline spot → quote (`as_of` = valuation_date,
     source=`akshare`, profile FK set);
   - each `hedge_instruments.last_price` → quote (`as_of` = `loaded_at`,
     source=`hedge_load`);
   - each `pricing_parameter_rows.spot` → quote (`as_of` = profile
     valuation_date, source=`legacy`, meta `{trade_id, profile_id}`) — **then**
     drop the spot column;
   - each `position_market_inputs` spot → quote (`as_of` = its valuation_date,
     source=`legacy`).
5. Split legacy `pricing_parameter_profiles` by `source_type`:
   - `default_underlying` profiles (underlying-keyed rows, `source_trade_id=""`)
     → become `assumption_sets` + `assumption_rows`;
   - imported / spot-profile rows (trade-keyed) → stay as pricing-parameter rows
     (minus spot).
6. Fold `position_market_inputs` r/q/vol: one synthetic pricing-parameter profile
   `"Migrated position market inputs (legacy)"` per distinct valuation_date,
   trade-keyed rows; then drop the table.
7. Add + backfill `instrument_id` on `hedge_map_entries`,
   `pricing_parameter_rows`, `instrument_pricing_defaults` (match by symbol);
   rename `underlying_pricing_defaults`.
8. Drop `underlyings`, `hedge_instruments`.

**Post-migration data repair (plan task, not migration):** fix `LH2609.DCE`
`akshare_asset_class` → `futures`; sweep Registry for similar kind↔asset-class
drift now that it is visible.

## Services

### `instruments` service (absorbs `app/services/underlyings.py`)

- `ensure_instrument(symbol, ...)` keeps `ensure_underlying` semantics — every
  auto-registering channel (booking, import, market-data fetch) works unchanged.
- `sync_from_positions` unchanged behavior → creates `draft` rows.
- Role queries: `underlying_instruments()` (referenced by open positions),
  `allowed_hedges(underlying_instrument_id)`, replacing status hacks.
- Resolvability rule for bulk fetch: AKShare mapping present AND status NOT IN
  (`expired`, `retired`). **Drafts are included** — the silent draft-exclusion
  gotcha dies. (`active_market_data_underlyings`'s active-only filter is removed.)

### Contract loader (`hedging_loader.py` reshaped into the registry)

- Same TaskRun machinery: 409 single-flight, per-family isolation, progress.
- Writes `instruments` rows (kind=`future`/`listed_option`) with `parent_id`
  resolved from the family spec (IC family → 000905.SH; LH options → the matching
  LH future), terms, `loaded_at`.
- Feed `last_price` → **emits a `market_quote`** (source=`hedge_load`) instead of
  a column write.
- Expire-missing → `status="expired"`; hedge-map reconcile unchanged.
- Merge conflicts (same symbol, differing terms): feed wins + audit record.

### Quote service (`market_data` domain)

`record_quote()`, `latest_quote()`, `latest_quotes(bulk)`. All AKShare fetch
endpoints emit quotes and keep their `MarketDataProfile` audit row. The
previously-planned "reload from positions" button is **subsumed** by the
Market Data tab's "Refresh quotes (all resolvable)" action against the unified
layer (sync + per-instrument fetch loop; per-symbol failures collected, never
abort; summary feedback).

### Assumptions service (`pricing_profiles.py` reshaped)

- `build_assumptions_set()` replaces `build_default_pricing_profile`:
  **no AKShare fetch** — the `failed_akshare_underlyings` failure mode ceases to
  exist structurally. The `unfilled` 400 stays (assumptions must be complete),
  now pre-visualized in the Assumptions tab.
- Cross-layer inheritance kept: builds bootstrap missing values from (a) prior
  assumption rows and (b) the latest trade-keyed pricing-param rows for that
  instrument, per-field provenance-labeled
  (`inherited from T-0142 · profile #29`). Scoring/selection logic carries over
  from `latest_manual_inputs_by_underlying`.

### Pricing-params import (position layer)

File format unchanged (`trade_id | symbol | spot | r | q | vol`); the importer
splits at write time:
- r/q/vol → `pricing_parameter_rows` (trade-keyed; `instrument_id` resolved
  trade→position→underlying, else symbol, else NULL = dormant);
- spot → `market_quotes` (source=`xlsx_import`, `as_of` = profile
  valuation_date, meta `{trade_id, profile_id}`).
- Spot collapse rule: record **every distinct observation**; resolver tie-break
  (max as_of, then max id = last file row) decides; conflicts flagged in the
  import summary (`2 conflicting spot values for 000905.SH — last row wins`).
  No silent averaging, no rejection.
- Unmatched trade ids import as dormant rows (sheets arrive before bookings) and
  auto-apply when the booking lands — current forgiveness, now visible.
- The separate `import_market_inputs_from_xlsx` channel is removed (folded here).

### Consumption chains (position_pricer + quantark + hedging)

```
spot   : request override
       → latest_quote(instrument, ≤ valuation)
       → AKShare fetch-and-RECORD (source=pricer_fallback)   # fetch-once, not fetch-and-forget
       → explicit "no market quote for {symbol} as of {date}" diagnostic
r/q/vol: request override
       → position pricing params (exact source_trade_id, latest profile ≤ valuation)
       → instrument assumptions (latest set ≤ valuation)
       → env fallback
```

- Valuation reads **assumption sets**, not defaults; defaults feed builds only
  (preserves today's behavior).
- Risk diagnostics keep `market_input_source`, profile/row ids, match_type,
  missing fields — re-pointed to the new stores; quote age (days) added.
- Hedge legs: futures legs and `propose()` ATM detection read
  `latest_quote(contract)`; option legs keep pricing under the run's environment
  (assumptions ⊕ quote — same composition as risk). `hedgeable` greeks unchanged
  (latest RiskRun).

## API surface

New:
- `GET /api/instruments` (filters: kind, status, role, parent_id, series_root, search)
- `PATCH /api/instruments/{id}` (status, mapping, currency, terms, parent)
- `POST /api/instruments/sync-from-positions`
- `POST /api/instruments/contracts/load` + `GET /api/instruments/contracts/load/{task_id}`
- `GET /api/market-data/quotes?instrument_id=&latest=` · `POST /api/market-data/quotes` (manual)
- `POST /api/market-data/quotes/refresh` (all resolvable; emits quotes; summary)
- `POST /api/assumptions/build` · `GET /api/assumptions/sets[/{id}]`
- `GET/PUT/DELETE /api/instrument-pricing-defaults[/{instrument_id}]` +
  `POST /api/instrument-pricing-defaults/refresh-from-positions`

Kept (reshaped semantics):
- `POST /api/market-data/profiles/akshare` (+ `/bulk`) — emit quotes; bulk
  iterates all resolvable instruments (drafts included)
- `GET /api/market-data/profiles*` (fetch-event audit + history)
- `/api/market-data/fx-rates*` unchanged
- `POST /api/pricing-parameter-profiles/import` + `GET` list/detail — trade-keyed
  only, split-write importer
- `/api/hedging/map*`, `/api/hedging/bands*`, `/api/hedging/solve`,
  `/api/hedging/book`, `/api/hedging/hedgeable` — map endpoints re-keyed by
  `instrument_id`; map group payload gains open-position counts (left rail)

Removed:
- `/api/underlyings/*` (incl. fetch-spot — per-row fetch lives on the quotes API)
- `/api/hedging/instruments*` (→ `/api/instruments` + contracts/load)
- `/api/underlying-pricing-defaults/*` (renamed)
- `POST /api/pricing-parameter-profiles/build-default` (→ assumptions/build)
- `POST /api/market-data/profiles/{id}/create-spot-pricing-profile` (producer C
  dies — spot comes from quotes)
- the market-inputs import endpoint (folded into pricing-params import)

## Agent tools & workflows

- Underlying/market-data tools re-point to instruments/quotes (list, sync, fetch).
- Hedging tools: instrument browse/mark re-keyed by instrument_id; solve/book
  unchanged contracts.
- Pricing tools: build-default tool → build-assumptions tool; import tool keeps
  its name with split-write semantics.
- `hedge-portfolio` workflow SKILL.md references audited (no catalog add/remove
  expected — edits only, so the skills-catalog count assertions stay untouched).
- Reference docs: if a new reference page is added for the unified layer,
  remember `VALID_REFERENCE_TYPES` in `test_reference_docs.py`.

## UI

### Instruments page (new; replaces Underlyings + Market Data pages)

Four tabs (wireframes approved in companion session):

1. **Registry** — instruments table (symbol, kind, parent, status, role badges
   read-only, terms, AKShare mapping), filters (kind/status/role/search),
   actions: Sync from positions, Load contracts (TaskRun progress chip), row
   edit. Sanity flag on kind↔akshare_asset_class mismatches (catches the live
   LH2609 bug at a glance).
2. **Allowed Hedges** — standalone curation workspace (moved from the Hedging
   page). Left rail: underlyings with open exposure (position count + allowed
   count; `0 allowed` flagged). Right: current map for the selected underlying
   (reconcile badges, Purge stale) above the family-routed candidate catalog
   (family/type/strike/expiry filters), every contract row showing
   latest quote + age. Mark/unmark/purge live ONLY here.
3. **Market Data** — latest_quote per instrument with **age badges**; Refresh
   quotes (all resolvable); manual quote entry; sub-tabs: Quotes · Fetch events
   (MarketDataProfile audit + history charts) · FX rates (moved unchanged).
4. **Assumptions** — instrument-level only: defaults editor with per-field
   provenance (explicit default vs inherited + source), unfilled rows visible
   *before* building, Build assumptions action, set selector. **No import
   button.**

### Pricing Params page (new; sits next to Positions in nav)

- Import xlsx + valuation-date control + profile selector.
- Import summary strip: rows applied / dormant / quotes emitted / spot conflicts.
- Trade-keyed row table: trade id → matched position → instrument, r/q/vol,
  spot→quote emission marker, applied/dormant status.

### Position Detail

New **Pricing Params block**: resolved spot/r/q/vol with per-field provenance
(`quote 2026-06-04 · xlsx_import` / `pricing params · profile #31 · T-0166` /
`instrument assumptions` / `env fallback`) — exactly what the next risk run uses.

### Removed/changed pages

Underlyings page and Market Data page removed (absorbed); Hedging page keeps
Strategy only; navigation updated.

## Error handling

- **Missing quote at valuation** → per-position diagnostic
  `no market quote for LH2609.DCE as of 2026-06-04` in `pricing_error` — never a
  silent default. Same surfacing channel as today's pricing failures.
- **Stale quote** → age rides in diagnostics + Tab-3 badges; no hard block (the
  desk decides).
- **Contract load** → per-family isolation kept; merge conflicts feed-wins +
  audit.
- **Assumptions build** → `unfilled` 400 stays; `failed_akshare_underlyings`
  deleted with the fetch.
- **Quote refresh / bulk fetch** → per-symbol failures collected into the
  summary, loop never aborts.
- **Import** → invalid currency/format errors per current conventions; dormant
  and conflict outcomes reported, not raised.

## Testing

- **Unit:** quote resolver determinism (as_of ordering, id tie-break,
  ≤-valuation); loader identity-merge (LH collision updates terms, never
  duplicates); parent resolution per family spec; `ensure_instrument`
  idempotency across channels; importer split-write (r/q/vol rows + quote
  emission + dormant + conflict flagging); assumptions inheritance provenance.
- **Migration test:** id preservation (`positions.underlying_id` valid
  post-upgrade); LH-style merge yields one row; quote backfill counts match all
  legacy stores; `spot` column gone only after its values landed; legacy
  default_underlying profiles became assumption sets.
- **Equivalence (characterization):** risk run pre/post on a pinned portfolio —
  seed quotes from the same values the old path read → totals **byte-identical**;
  hedge solve futures legs price identically off `latest_quote` as off
  `last_price`. Fixtures use NON-default values (house lesson: value==fallback
  masks dead paths).
- **Untouched suites stay green untouched:** cross-channel booking equivalence
  and term-contract nets must pass with zero edits — booking gate is out of
  scope; if they need edits, the design leaked.
- **Frontend:** vitest presentational tests for both new pages' contracts (run
  from `frontend/`); live browser smoke against the migrated real DB.

## Phasing sketch (plan will detail)

- **Phase 0:** `instruments` table + migration + FK re-target + instruments
  service at parity (registry reads/writes, sync, contract loader writing
  instruments). Old endpoints alias temporarily.
- **Phase 1:** `market_quotes` + emitters (fetch endpoints, loader, importer
  backfill) + spot consumption chain + fetch-and-record fallback.
- **Phase 2:** assumptions split (`assumption_sets`, defaults rename, build) +
  pricing-params reshaping (trade-keyed only, split-write import,
  PositionMarketInput fold) + r/q/vol consumption chain.
- **Phase 3:** UI — Instruments page (4 tabs), Pricing Params page, Position
  Detail block, Hedging page slim-down, nav; remove dead endpoints.
- **Phase 4:** agent tools/workflows re-point, LH2609 data repair, equivalence
  verification, cleanup.

## Out of scope (YAGNI, explicit)

- FX pairs as instruments (`FxRate` stays standalone).
- Per-contract implied-vol surfaces — noted for the future: those would be
  *quotes* (observations), never assumption rows; the split keeps that clean.
- Hedge family routing as data (`_INDEX_FAMILIES` stays config).
- Auto-activating draft instruments (curation stays human).
- Quanto support (standing non-goal).
- Concurrency in fetch loops (sequential keeps AKShare rate-limits happy).

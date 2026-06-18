# Hedging Module — Part 1: Instrument Management (Design)

- **Date:** 2026-06-02
- **Status:** Approved (brainstorm) — ready for implementation planning
- **Branch / worktree:** `feature/hedging-instrument-management` at `/Users/fuxinyao/open-otc-trading-hedging`
- **Scope:** Part 1 of 2 of the hedging module. This cycle delivers the **hedging instrument catalog**, the **durable hedging map** (underlying → allowed hedging instruments), the **AKShare loader**, and the **marking page**. Backend + UI only — no agent tools/skills.

---

## 1. Context & motivation

The desk runs equity-index structured products (snowball/phoenix and friends). To hedge them it needs a governed answer to a single question: *for each underlying we actually hold, which concrete market instruments are we allowed to hedge it with?* That artifact — the **hedging map** — does not exist today.

What already exists and is reused, not rebuilt:

- **Hedge instruments as products**: `EquitySpotProduct`, `EquityFuturesProduct` (DeltaOne booking types) and `EquityOptionProduct`. These are the *booking* targets for Part 2; Part 1 does not create product rows.
- **Greeks/risk**: `run_risk` produces persisted portfolio greeks; a stub `recommend_hedge` exists (delta-proxy only). Consumed by Part 2, not Part 1.
- **Async jobs**: a generic `TaskRun` record (`kind` + `progress_current/total` + `result_payload` + `message`/`error`) dispatched via `submit_async_task(...)`, exactly as `risk.run()` does. The loader reuses this; **no new orchestration primitives**.
- **AKShare access**: `fetch_akshare_snapshot` runs AKShare in a guarded subprocess (`_fetch_akshare_snapshot_subprocess`: timeout + fallback) and a test hook (`_using_test_akshare_module`) lets tests inject a fake module. The loader follows the same isolation and test pattern.

The genuine gaps Part 1 fills: (a) there is no catalog of *live tradable hedge contracts*, (b) there is no enumeration of futures contracts / option chains from AKShare (today only single-symbol OHLC), and (c) there is no persisted underlying → allowed-instrument policy.

## 2. Locked decisions (from brainstorm)

| Decision | Choice |
|---|---|
| Sequencing | Two cycles; **Part 1 (this spec) first**, Part 2 (strategy engine + agent workflow + booking) after. |
| Instrument universe | A-share index futures (CFFEX), A-share index options (CFFEX), ETF options (SSE/SZSE), commodity/other futures & options. |
| Loader style | **Underlying-aware**: a resolver decides which instrument families to enumerate per underlying. |
| Map granularity | **Specific contracts** (not series/root). |
| Option-chain scope | **Full live chains** (every live strike × expiry). |
| Map scope | **Global per-underlying** (one desk-wide policy). |
| Storage shape | **Split**: a refreshable catalog table + a durable map table keyed by stable contract identity. |
| Loader execution | **Async** `TaskRun`; **reject concurrent loads** with the in-flight `task_id` (HTTP 409). |
| In-scope underlyings | Underlyings referenced by **`open` positions in live (non-archived) portfolios**. |
| Stale-mark surfacing | **Inline badges + per-underlying attention banner** with Review / Purge. |
| Page layout | **Master–detail**: underlying rail + per-family filterable/bulk-selectable tables. |
| Agent integration | **None** this cycle (backend + UI only). No `SKILL.md` added → `test_skills_catalog*` counts untouched. |

## 3. Out of scope (Part 2)

Strategy targets (Delta/Gamma neutral, …), instrument greeks/pricing, quantity solving, booking the hedge, and all agent tools/skills (`@tool` wrappers, workflow `SKILL.md`). Part 2 consumes the hedging map produced here.

---

## 4. Data model

Two new tables plus a code-level resolver. The **catalog** is a refreshable market view; the **map** is the durable policy.

### 4.1 `hedge_instrument` — catalog (refreshed on each load)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `underlying_id` | FK→`underlyings.id`, indexed | the *book* underlying this contract can hedge |
| `family` | str | `index_future` / `index_option` / `etf_option` / `commodity_future` / `commodity_option` |
| `series_root` | str | rollover series/root: `IC`, `IO`, `510500`, `M`, … |
| `exchange` | str | `CFFEX` / `SSE` / `SZSE` / `DCE` / … |
| `contract_code` | str | stable exchange/trading code (`IC2406`, option trading code) |
| `instrument_type` | str | `future` / `option` |
| `option_type` | str? | `C` / `P` (options only) |
| `strike` | float? | options only |
| `expiry` | date? | |
| `multiplier` | float? | contract multiplier |
| `last_price` | float? | snapshot last/settle captured at load |
| `akshare_symbol` | str | how AKShare references this contract |
| `status` | str | `live` / `expired` (set during reconcile) |
| `loaded_at` | datetime | last time this contract was seen in a successful load |
| `created_at` / `updated_at` | datetime | |

- **Unique natural key:** `(exchange, contract_code)`.
- Helpful indexes: `(underlying_id, family, status)`, `(status)`.

### 4.2 `hedge_map_entry` — the hedging map (durable allowed marks)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `underlying_id` | FK→`underlyings.id`, indexed | |
| `exchange` | str | } stable identity to the contract |
| `contract_code` | str | } |
| `family` | str | denormalized snapshot (survives catalog expiry) |
| `series_root` | str | denormalized snapshot |
| `instrument_type` | str | denormalized snapshot |
| `option_type` | str? | denormalized snapshot |
| `strike` | float? | denormalized snapshot |
| `expiry` | date? | denormalized snapshot |
| `reconcile_status` | str | `active` (matched a live catalog row at last load) / `stale` (contract no longer live) — cached at reconcile time |
| `marked_by` | str? | actor |
| `marked_at` | datetime | |
| `created_at` / `updated_at` | datetime | |

- **Unique constraint:** `(underlying_id, exchange, contract_code)` → idempotent marks.
- Marking = insert; unmarking = delete. Marks are **never auto-deleted** by the loader.
- The denormalized descriptive columns mean a mark stays meaningful and displayable even after its catalog row expires or is purged.
- **The hedging map for underlying X = `hedge_map_entry` rows where `underlying_id = X`.** This is the durable artifact Part 2 consumes.

### 4.3 `HedgeUniverseResolver` — declarative registry (code, not a table)

The underlying-aware core. Maps a book underlying (by symbol, with `asset_class` as fallback) → a list of `(family, series_root, enumerator)` specs:

```
CSI300 → [(index_future,  "IF",     cffex_future_enumerator),
          (index_option,  "IO",     cffex_option_enumerator),
          (etf_option,    "510300", sse_etf_option_enumerator),
          (etf_option,    "159919", szse_etf_option_enumerator)]
CSI500 → [(index_future,  "IC",     cffex_future_enumerator),
          (etf_option,    "510500", sse_etf_option_enumerator)]   # no CFFEX index option for CSI500
CSI1000→ [(index_future,  "IM",     cffex_future_enumerator),
          (index_option,  "MO",     cffex_option_enumerator)]
M (豆粕)→ [(commodity_future, "M",   dce_future_enumerator),
          (commodity_option, "M",   dce_option_enumerator)]
```

- An underlying with **no mapping** is **unresolvable**: surfaced in the rail with a warning, skipped by the loader, no rows created.
- The resolver is seeded for the desk's current underlyings and is the single place to extend coverage.

---

## 5. The AKShare loader

### 5.1 Execution

1. **`POST /api/hedging/instruments/load`** creates `TaskRun(kind="hedge_instrument_load")`, calls `submit_async_task(execute_hedge_load_task, task_id, ...)`, and returns `{task_id}`. If a `hedge_instrument_load` is already `running`/`queued`, return **HTTP 409** with `{in_flight_task_id}`.
2. The UI polls the existing **`GET /api/tasks/{task_id}`** for status, progress, and summary.

### 5.2 `execute_hedge_load_task`

1. Compute **in-scope underlyings**: distinct underlyings referenced by `open` positions in live (non-archived) portfolios.
2. `progress_total` = Σ resolved `(underlying × family)` pairs.
3. For each underlying → each resolver family → run the **enumerator adapter** inside the guarded subprocess pattern (timeout + fallback). After each pair call `update_task_progress(...)` with a human `message` (e.g. `"中证500 · ETF options: 218 contracts"`).
4. **Upsert** returned contracts into `hedge_instrument` by `(exchange, contract_code)` — insert new, refresh existing (`last_price`, `expiry`, `multiplier`, `loaded_at`, `status=live`).
5. **Reconcile** (see 5.4).
6. Write a **summary** to `result_payload`: per-`(underlying, family)` counts, unresolvable underlyings, and per-family errors. UI "last loaded" = latest completed load of this kind (corroborated by catalog `max(loaded_at)`).

### 5.3 Enumerator adapters (the AKShare seam)

One small adapter per family — CFFEX futures list, CFFEX option chain, SSE/SZSE ETF option chain, commodity futures/option chain — each returning a **normalized** contract list (`contract_code, exchange, instrument_type, option_type, strike, expiry, multiplier, last_price, akshare_symbol`). The **exact AKShare function names are pinned during implementation** (verified with the `akshare-data` skill / `model-researcher`); AKShare's API is large and version-sensitive, so concrete calls live behind adapters rather than being guessed in this spec. Adapters are individually unit-testable against fixture frames.

### 5.4 Reconcile (outage-safe)

- **Only a family whose enumeration confirmably succeeded** may expire its missing contracts (`status=expired`). A family that errored or fell back leaves its existing catalog rows **and** their marks untouched — a transient AKShare outage must never wipe the catalog or flip the map to stale.
- After upserts/expiries, recompute every `hedge_map_entry.reconcile_status`: `active` if its `(exchange, contract_code)` matches a `live` catalog row, else `stale`.
- Marks are never auto-deleted.

---

## 6. REST API

Thin domain (`services/domains/hedging.py`) + router (`routers/hedging.py`), following the `risk` / `market_data` split. All **new** paths under `/api/hedging/`; the load-progress row reuses the **existing** global `/api/tasks/{task_id}` endpoint (shown for completeness).

| method + path | purpose | drives |
|---|---|---|
| `GET /underlyings` | in-scope underlyings + per-family `allowed/total` counts, `stale_count`, `last_loaded_at`, `unresolvable` flag (plus underlyings that have map entries even if no longer in scope) | left rail |
| `POST /instruments/load` | trigger async load → `{task_id}`; **409 `{in_flight_task_id}`** if a load is running | Load button |
| `GET /tasks/{task_id}` *(existing `/api/tasks/...`)* | poll status / progress / summary | load progress |
| `GET /instruments` | catalog rows for `underlying_id` + `family`; filters: `instrument_type`, `option_type`, `expiry`, `strike_min/max`, `search`, `allowed_only`, `status`; paginated | family tables |
| `POST /map/mark` | bulk mark: `{instrument_ids:[…]}` → copy each catalog row's natural key + descriptive snapshot into `hedge_map_entry` (idempotent) | mark / "mark filtered" |
| `POST /map/unmark` | bulk unmark: `{instrument_ids:[…]}` or `{map_entry_ids:[…]}` → delete entries | unmark |
| `GET /map` | hedging map — allowed entries grouped by underlying, incl. `reconcile_status` | map view / export / Part 2 input |
| `POST /map/purge-stale` | drop `stale` entries for an underlying (explicit cleanup) | attention banner |

- **Marking is catalog-anchored**: only contracts present in the catalog can be marked (`mark` takes `instrument_ids` the UI already holds). The server copies the snapshot so the mark outlives catalog expiry.
- **"Mark filtered"** sends the explicit `instrument_ids` currently matched by the filter; a server-side mark-by-filter-spec is a deferred enhancement.

---

## 7. Frontend page (master–detail, Layout A)

New route `Hedging` (`.tsx` + `.live.tsx` + tests), following the existing route conventions (e.g. `Underlyings`, `Risk`).

- **Top bar:** `⟳ Load from AKShare` button; `last loaded <ts> · N underlyings · M contracts · ⚠ K stale marks`. While loading, the button shows a progress bar + the polled `message`.
- **Left rail:** in-scope underlyings (live books) with `allowed/total` counts, a stale-count chip, and an `⚠ unresolvable` marker for underlyings with no resolver mapping.
- **Main pane (per selected underlying):**
  - **Family tabs**: Index futures / Index options / ETF options / Commodity (only families that resolved for this underlying).
  - **Per-underlying attention banner** when stale marks exist: *"N marked contracts for X are no longer live (expired/rolled)"* with **Review** (filter to stale) and **Purge stale** actions.
  - **Filter bar**: search, expiry, strike range, C/P, `allowed only`, `stale only`, and `✓ mark filtered`.
  - **Contract table**: checkbox + contract, expiry, strike, C/P, last, state badge (`allowed` / `stale · expired contract` struck-through / unmarked). Multi-select with bulk **Mark** / **Unmark**.

---

## 8. Error handling & edge cases

- **AKShare fully unavailable** (not installed / all sources down): load completes with a clear summary; **catalog + marks untouched** (no expiry sweep on failed fetch).
- **Partial family/underlying failure**: error recorded in `result_payload`; that family's rows untouched; others upsert normally; job still `completed`.
- **Confirmed-empty vs errored**: only a confirmed-successful enumeration may expire its family's missing contracts (legit delist/roll). Fallback/error never expires.
- **Unresolvable underlying**: shown with a warning, skipped by loader, no rows.
- **Underlying drops out of live books** (positions closed): stays visible in the rail **if it still has map entries** (so it can be reviewed/purged); otherwise drops from scope. Map entries persist.
- **Concurrent load**: 409 with in-flight `task_id`.
- **Duplicate / racing marks**: idempotent via unique `(underlying_id, exchange, contract_code)`; marking a just-expired contract succeeds but lands `stale`.

## 9. Testing strategy

- **Unit** — resolver mapping (incl. unresolvable + commodity); reconciliation (upsert, *expire-only-on-confirmed-success*, **outage-doesn't-wipe**, `reconcile_status` transitions, never-auto-delete); mark/unmark/purge domain (idempotent insert, snapshot copy, bulk); per-family enumerator adapters against fixture frames.
- **Loader task** — drive `execute_hedge_load_task` via the existing fake-AKShare hook (`_using_test_akshare_module`) with deterministic contract lists; assert catalog + map + `TaskRun` progress/summary; an explicit **outage test** proving no wipe; a **concurrent-load 409** test.
- **API** — every endpoint, incl. catalog filters and the 409 path.
- **Migration** — the two new tables, using **migration-local Core tables, not ORM models** (per this repo's rule; ORM drifts to future schema).
- **Frontend** — component tests (rail w/ counts + stale chip, family tables + filters, bulk mark/unmark, attention banner, load progress) + a `.live.test.tsx` for page wiring.
- **No skills added** → `test_skills_catalog*` counts deliberately untouched.

## 10. Implementation surface (summary)

- **New:** `hedge_instrument` + `hedge_map_entry` tables (one Alembic migration); `services/domains/hedging.py`; `services/hedging_loader.py` (task executor + reconcile) and `services/hedging_universe.py` (resolver + enumerator adapters); `routers/hedging.py`; frontend `Hedging` route + components.
- **Reused:** `TaskRun` + `submit_async_task` + `/api/tasks/{id}`; the guarded AKShare subprocess + fake-module test hook; `Underlying` / `Portfolio` / `Position` for in-scope extraction.
- **Untouched:** product/booking tables, risk engine, agent skills/tools and their catalog tests.

## 11. Hand-off to Part 2

Part 2 (strategy engine + agent workflow + booking) consumes `GET /api/hedging/map` (allowed, `active` instruments per underlying) and adds: instrument greeks/pricing, target selection (Delta/Gamma neutral, …), quantity solving, booking via `EquityFuturesProduct` / `EquityOptionProduct` / `EquitySpotProduct`, and the agent `@tool` wrappers + workflow `SKILL.md`.

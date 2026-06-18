# Default Pricing Parameter Profile (Underlying Defaults + AKShare Spot) Design

Date: 2026-05-13
Status: Approved for implementation planning

## Goal

Add a one-click workflow for producing a daily pricing parameter profile by combining **manually-maintained per-underlying r/q/σ** with **fresh AKShare spot** for every open-position underlying. The output is a normal `PricingParameterProfile` snapshot (with a new `source_type='default_underlying'`) that lives alongside existing XLSX-imported profiles in the profile library and is picked up by the existing Positions and Risk pricing flows.

Maintenance of the per-underlying r/q/σ store happens in a new collapsible panel on the existing `Pricing Parameters` page. The "Build default profile" action is strict: it refuses to produce a snapshot unless every in-scope underlying has all three of rate, dividend yield, and volatility filled and AKShare returns a usable spot for each one.

## Approved Approach

- Persist per-underlying assumptions in a new `underlying_pricing_defaults` table.
- The "Build" action emits a brand-new `PricingParameterProfile` snapshot on each invocation. No in-place mutation; every build adds an audit-traceable snapshot.
- Snapshot rows are keyed by `(profile_id, source_trade_id)` exactly like the existing XLSX-imported profiles, so the position pricer's existing resolution chain is unchanged.
- The `Pricing Parameters` page splits into two bands on the same route: a **config store** (Underlying Defaults panel) on top and the existing **profile library** (snapshots) below. Positions and Risk pages remain the consumers — they pick a profile from the library via their existing selectors.

Rejected approaches:

- Reusing `PricingParameterProfile` with a `defaults_template` source type. Cheaper migration but conflates a snapshot with a config store and forces "is this a template?" branching across every consumer.
- Embedding r/q/σ inside `MarketDataProfile`. Mixes spot-source with assumption-params and stretches `MarketDataProfile` beyond its current responsibility.
- A second top-level route for underlying defaults. Adds nav surface for a workflow that is fundamentally part of producing a pricing snapshot.

## Data Model

### `UnderlyingPricingDefault`

New global table keyed by `underlying`.

Fields:

- `id`
- `underlying` (unique, indexed; matches `Position.underlying` after `_position_underlyings` normalization)
- `rate` (float, nullable)
- `dividend_yield` (float, nullable)
- `volatility` (float, nullable)
- `notes` (text, nullable)
- `created_at`
- `updated_at`

Asset class is intentionally not stored. The existing `_akshare_asset_class(symbol)` helper continues to be the single source of truth at build time, so the store stays narrow.

A row is *complete* when all three of `rate`, `dividend_yield`, `volatility` are non-null. The completeness check happens at build time and at table read time for UI gating.

### `PricingParameterProfile` and `PricingParameterRow`

Unchanged schemas. A new value `default_underlying` is added to the set of valid `source_type` strings emitted by the application. The position pricer treats `default_underlying` rows identically to `xlsx` and `market_data_spot` rows — the source type is metadata for the UI and audit log only.

A `default_underlying` profile's `summary` field is populated with:

- `row_count`
- `underlyings`: list of `{ underlying, akshare_symbol, asset_class, spot, akshare_source, akshare_fallback }`
- `valuation_date`, `adjust`
- `skipped_positions`: list of `{ position_id, reason }` for open positions that lacked a `source_trade_id` and could not be keyed

## Build Pipeline

New service function in `backend/app/services/pricing_profiles.py`:

```python
def build_default_pricing_profile(
    session: Session,
    *,
    name: str | None = None,
    valuation_date: datetime | None = None,
    adjust: str = "qfq",
) -> PricingParameterProfile
```

Sequence:

1. **Scope** — call a new helper `_open_position_underlyings(session)` (adapted from the existing `_position_underlyings` in `main.py`) to return distinct `Position.underlying` strings for positions with `status != 'closed'` and `_source_trade_state(position) != '敲出'`. If the list is empty, raise `ValueError("no open positions in scope")`.
2. **Auto-discover** — for any underlying in the scope list not yet present in `underlying_pricing_defaults`, insert a row with all three numeric fields null. Existing rows are not modified.
3. **Strict precheck** — gather all in-scope underlyings whose store row has any of rate, dividend yield, or volatility null. If non-empty, raise `ValueError` carrying the list. Do not call AKShare. This validation must come first because AKShare round-trips are slow.
4. **AKShare fetch** — for each in-scope underlying call `fetch_akshare_snapshot(...)` using `_akshare_symbol()` / `_akshare_asset_class()` helpers and a short date window ending at `valuation_date` or today (default ~10 business days). Collect underlyings whose snapshot returned `fallback=True` or no usable `spot`. If non-empty, raise `ValueError` carrying the list. Do not partial-commit.
5. **Emit profile** — create one `PricingParameterProfile` with `source_type='default_underlying'`. For every open position with a non-empty `source_trade_id`, write one `PricingParameterRow` joining its `underlying` to the fetched spot and the store's `rate`, `dividend_yield`, `volatility`. Positions without `source_trade_id` are recorded in `summary.skipped_positions` and do not block the build.
6. **Audit** — `record_audit(event_type='pricing_parameters.default_built', subject_type='pricing_parameter_profile', subject_id=profile.id, payload={ underlyings: [...], row_count })`.

Profile name defaults to `f"Default {valuation_date:%Y-%m-%d %H:%M}"` if the caller does not supply one.

The build is transactional: any failure between steps 4 and 5 rolls back so no partial profile or partial rows persist.

## Backend API

### Underlying defaults CRUD

New module `backend/app/services/underlying_defaults.py`. Endpoints:

- `GET /api/underlying-pricing-defaults` — list all rows, sorted by underlying.
- `PUT /api/underlying-pricing-defaults/{underlying}` — upsert one entry. Body: `{ rate, dividend_yield, volatility, notes }`. All fields optional individually; supplied fields are written, omitted fields retain their previous value.
- `DELETE /api/underlying-pricing-defaults/{underlying}` — remove the entry. The next build that scans positions will re-create it with null values via the auto-discover step if the underlying is still in scope.
- `POST /api/underlying-pricing-defaults/refresh-from-positions` — runs the auto-discover step in isolation: scans open positions, upserts any missing underlyings with null fields, and returns the updated list. Powers the panel's `Refresh from positions` action without requiring a full build.

Each response row carries a derived boolean `is_complete = rate IS NOT NULL AND dividend_yield IS NOT NULL AND volatility IS NOT NULL` for the UI gating check.

### Build action

- `POST /api/pricing-parameter-profiles/build-default` — trigger the build pipeline. Body: `{ name?, valuation_date?, adjust? }`.

Responses:

- `200` — returns the new `PricingParameterProfileOut` (with rows eagerly loaded via the existing `selectinload` pattern).
- `400 unfilled` — `{ detail: "underlying defaults missing inputs", unfilled_underlyings: [<underlying>...] }`.
- `400 akshare_failed` — `{ detail: "AKShare spot fetch failed for one or more underlyings", failed_akshare_underlyings: [{ underlying, reason }] }`.
- `400 no_positions` — `{ detail: "no open positions in scope" }`.

All 400s preserve the standard `detail: str` shape so existing client error-handling continues to work.

### Optional spot refresh helpers

The Underlying Defaults panel uses two existing endpoints unchanged:

- `POST /api/market-data/profiles/akshare` for the per-row `⤓ Spot` button. The frontend supplies the per-underlying `symbol`, `asset_class` (inferred from the underlying string), and a short trailing window (last ~10 business days ending today).
- `POST /api/market-data/profiles/akshare/bulk` for the `Refresh all AKShare spots` panel-level action. The frontend supplies `start_date` and `end_date` for the same trailing window; the endpoint resolves underlyings from positions itself.

No new endpoints are required for these affordances. The displayed "latest AKShare close" value for each underlying is sourced from the most recent `MarketDataProfile` row by symbol/underlying.

## Frontend

### Page-header changes

The existing `PRICING PARAMETERS` page header has two changes:

- Remove the `Load to Positions` button entirely.
- Remove `onUseInPositions` from `PricingParameters.tsx`, `PricingParameters.live.tsx`, and the `main.tsx` wire-up, along with the cross-page state `positionsPricingProfileId`. Positions and Risk pages already independently fetch `/api/pricing-parameter-profiles` and select profiles via their own selectors, so this state has no remaining consumer.

The remaining primary action is `Import XLSX`, unchanged from today.

### New band 1: Underlying Defaults panel

A collapsible panel inserted between the page header and the existing profile library. Visual language matches the existing route — `Tile`, `Table`, `Button`, `Modal` patterns and `PricingParameters.css` tokens.

Panel header:

- Title `UNDERLYING DEFAULTS` with a `CONFIG STORE` sub-label.
- Counters: total entries, filled count, incomplete count. Incomplete count is amber when greater than zero.
- Actions, left to right: `Refresh from positions` (re-run auto-discover step in isolation), `Refresh all AKShare spots` (bulk fetch), `Build default profile` (primary).

Below the header, a sub-header strip shows the data flow as visual segments:

```
AKShare → spot   +   manual → rate · div · vol   ⇒   new snapshot in library below
```

Plus a link `View Market Data lineage` that jumps to the `Market Data` route filtered by the relevant underlyings.

Defaults table columns:

- `UNDERLYING`
- `LATEST AKSHARE CLOSE` (read-only, blue-tinted; sourced from the most recent `MarketDataProfile.data.spot`; renders amber-styled with a `stale` sub-label when the latest profile is older than today's close; empty cell with a "no AKShare profile yet" hint when none exists)
- `RATE`, `DIV YIELD`, `VOL` (manual, amber-headered, inline-editable)
- `NOTES` (manual, free text)
- `LAST EDITED`
- Row actions: `⤓ Spot` (per-row AKShare refresh), `Edit` / `Save` / `Cancel`, delete (`×`)

Edit-in-row pattern: clicking a numeric cell or the `Edit` action turns the cells into inputs; `Save` commits via the `PUT` endpoint; failures keep the row in edit state with an inline error message.

Build button gating:

- Disabled when the incomplete count is greater than zero. Tooltip lists the underlyings that need attention.
- On `200`, the panel refreshes the profile library list, selects the new profile in the library, and emits a success toast via the existing `PricingParameterFeedback` channel.
- On 400 `unfilled` or 400 `akshare_failed`, an inline amber banner appears between the sub-header and the table listing the offending underlyings as pills. The banner clarifies which field set is at fault ("Spot is fine — only rate / div / vol are required" or "Rate/div/vol are fine — AKShare fetch failed").

### Band 2: Profile library (existing, enhanced)

The existing profile list / detail layout is retained. Additions:

- A source-type filter strip in the section header: `ALL · DEFAULT · XLSX · SPOT` with per-type counts. The `ALL` pill is active by default.
- Each profile list item gains a color-coded left edge stripe and a small uppercase tag — `DEFAULT` (blue), `XLSX` (amber), `SPOT` (green) — sourced from `source_type`.
- The selected-profile detail tile strip adds a `Source type` tile rendering the tag prominently and the build/import metadata as the sub-line.
- The selected-profile row table adds an inline per-cell origin badge next to each numeric value: `AK` (AKShare) on the spot column for `default_underlying` and `market_data_spot` profiles, `M` (manual via Underlying Defaults) on rate/div/vol for `default_underlying` profiles, and `XLSX` on all numeric cells for `xlsx` profiles. The toolbar shows a legend explaining the badges.

### Frontend types (`frontend/src/types.ts`)

```ts
type UnderlyingPricingDefault = {
  underlying: string;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  notes: string | null;
  is_complete: boolean;
  created_at: string;
  updated_at: string;
};
```

`PricingParameterProfile.source_type` becomes a union `'xlsx' | 'market_data_spot' | 'default_underlying'`. Existing code paths treat any unknown source type as a generic snapshot.

## Error Handling

| Scenario | Behavior |
|---|---|
| Build with no open positions in scope | HTTP 400 `{ detail: "no open positions in scope" }` |
| Build with at least one in-scope underlying missing rate/div/vol | HTTP 400 `{ detail, unfilled_underlyings: [...] }` — no AKShare calls made, no profile written |
| Build with AKShare fallback / no-rows for at least one underlying | HTTP 400 `{ detail, failed_akshare_underlyings: [{ underlying, reason }] }` — no profile written; transaction rolled back |
| Open position has `source_trade_id IS NULL` | Skipped; appears in `summary.skipped_positions` with reason `missing_source_trade_id`; does not block the build |
| `PUT /api/underlying-pricing-defaults/{underlying}` for an underlying not in the table | Upsert creates the row |
| `DELETE` for an underlying still referenced by open positions | Allowed; the next build will auto-recreate it as unfilled via step 2 |
| Concurrent builds | No locking. Two profiles get appended; lineage preserves both. Audit log records both events. |
| AKShare timeout / exception during per-row `⤓ Spot` action | Existing `_fallback_snapshot` behavior records the fallback in the resulting `MarketDataProfile`. UI shows the fallback metadata; the defaults panel's `LATEST AKSHARE CLOSE` cell renders as amber "fallback" |

## Testing

### Backend

- `underlying_pricing_defaults` auto-discover creates rows for new underlyings and leaves existing rows untouched.
- Strict precheck fails listing unfilled underlyings; verify that no AKShare calls were made (mock counter).
- AKShare fallback for at least one underlying causes the build to fail listing failed underlyings; no `PricingParameterProfile` or `PricingParameterRow` rows persisted.
- Happy path: build emits a `PricingParameterProfile` with one row per open position that has a `source_trade_id`; positions without `source_trade_id` appear in `summary.skipped_positions`.
- Repeated builds append new profiles with distinct ids; both are retrievable in the list endpoint.
- Audit event recorded with the expected payload shape.
- Position pricer end-to-end against a `default_underlying` profile produces equivalent results to an XLSX-imported profile with the same numeric inputs (regression guard against accidental special-casing).
- `PUT` upsert is idempotent and preserves omitted fields.
- `DELETE` removes the row; subsequent build re-creates it as unfilled.

### Frontend

- Underlying Defaults panel loads existing rows; counters reflect filled / incomplete split.
- Inline edit saves via `PUT` and updates the row in place; failure paths preserve edit state.
- `Build default profile` button is disabled when the incomplete count is greater than zero; tooltip lists unfilled underlyings.
- 400 `unfilled` renders the inline banner listing the unfilled underlyings.
- 400 `akshare_failed` renders the banner with AKShare-specific copy.
- Successful build refreshes the profile library, scrolls/selects the new profile, and emits the standard feedback toast.
- `⤓ Spot` per-row button calls the single AKShare endpoint and refreshes the `LATEST AKSHARE CLOSE` cell on success.
- `Refresh all AKShare spots` calls the bulk endpoint and updates every row.
- Profile library source-type filter chips correctly filter the list and show the correct per-type counts.
- Profile list items render the correct color-coded stripe and `DEFAULT` / `XLSX` / `SPOT` tag based on `source_type`.
- Profile detail tiles include the new `Source type` tile.
- Profile detail row table renders the per-cell origin badges for each source type.
- `PricingParameters` regression suite confirms the `Load to Positions` button is removed; `main.tsx` no longer wires `onUseInPositions`; the `positionsPricingProfileId` cross-page state is gone; Positions and Risk profile selectors continue to work independently.

### Browser verification

- The `Pricing Parameters` route renders inside `AppShell` with the new panel above the profile library and the `Load to Positions` button removed.
- Filling all underlyings and clicking `Build default profile` produces a new profile that immediately appears in the library and is selectable.
- Clearing one rate cell and clicking `Build default profile` shows the inline banner listing the affected underlying; the button itself stays disabled while the table reflects the unfilled state.
- Picking the newly built profile in the Positions page's existing profile selector and running pricing produces results that include the AKShare spot and the manually-supplied r/q/σ.

## Scope Boundaries

In scope:

- New `underlying_pricing_defaults` table and Alembic migration.
- Auto-discover of underlyings from open positions.
- Strict-build service emitting a `PricingParameterProfile` snapshot with `source_type='default_underlying'`.
- `GET / PUT / DELETE / POST` endpoints on `/api/underlying-pricing-defaults/*` (including the `refresh-from-positions` action) and `POST /api/pricing-parameter-profiles/build-default`.
- New collapsible Underlying Defaults panel on the existing `Pricing Parameters` route, including the AKShare spot preview column, data-flow sub-header, per-row spot refresh, and bulk spot refresh.
- Source-type filter chips, color-coded list stripes, per-cell origin badges in the profile library detail.
- Removal of the `Load to Positions` button and the `onUseInPositions` / `positionsPricingProfileId` wire-up in `main.tsx`, `PricingParameters.tsx`, `PricingParameters.live.tsx`.
- Audit event for builds.

Out of scope:

- Historical volatility derivation from AKShare history.
- Curve or surface management (term structure of r, q, σ).
- Per-portfolio defaults — the store stays global.
- Scheduled or cron-triggered builds.
- Permissioning on the defaults edit endpoints.
- Migration of existing XLSX-based pricing profiles to the new source type.
- Replacing the existing `market_data_spot` workflow.

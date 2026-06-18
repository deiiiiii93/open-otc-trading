# Market Data: "Reload from positions" Button

**Date:** 2026-06-04
**Status:** Approved (design review with user)

## Problem

After imports/bookings, position underlyings may be missing from the underlyings
registry or never fetched from AKShare. The Market Data page can fetch a single
symbol manually and "Refresh All" — but Refresh All only iterates symbols that
already have at least one profile, and the server-side bulk endpoint
(`/api/market-data/profiles/akshare/bulk`) skips draft underlyings. Result:
book underlyings silently lack market data, so AKShare-driven market data and
pricing-parameter profile generation fail for them.

The Underlyings page has a sync button (`POST /api/underlyings/sync-from-positions`)
but it only registers rows — it does not fetch market data.

## Decision (user-approved)

One button on the Market Data page: **sync + fetch**. Register every open
position's underlying, then fetch an AKShare snapshot per resolvable symbol with
the page's current form settings. Unresolvable symbols are reported, never fatal.

## Design

**Backend: no changes.** Reuses:
- `POST /api/underlyings/sync-from-positions` → `{created, existing, underlyings[]}`
  (each `UnderlyingOut` carries `akshare_symbol`, `akshare_asset_class`; new rows
  are created as `draft` with inferred AKShare mapping).
- `POST /api/market-data/profiles/akshare` (single-symbol fetch; accepts registry
  symbols like `000905.SH`; creates the MarketDataProfile and an FxRate row when
  applicable).

**Frontend:**

1. `frontend/src/routes/MarketData.live.tsx` — new handler
   `onReloadFromPositions(useProxy: boolean)` (same signature pattern as its
   neighbor `onRefreshAll`; the fetch form is modal-scoped with empty date
   defaults, so it is not a usable settings source):
   - POST `/api/underlyings/sync-from-positions`.
   - Partition `underlyings`: resolvable (`akshare_symbol` && `akshare_asset_class`
     both non-null) vs skipped (symbol list kept for the summary).
   - Sequentially POST `/api/market-data/profiles/akshare` per resolvable
     underlying with `symbol: u.symbol`, `asset_class: u.akshare_asset_class`,
     a trailing one-year window (`start_date` = today − 365d, `end_date` =
     today), `adjust: 'qfq'` (the page default), and the page's proxy toggle.
     Progress feedback per symbol (mirrors `onRefreshAll`); per-symbol failures
     are collected and never abort the loop.
   - Summary feedback:
     `Synced {created} new underlyings ({existing} existing) · fetched {ok} snapshots[ · skipped {n} unresolvable: …][ · {n} failed: …]`
   - `await load()` afterwards (refreshes profiles and FX rates).
2. `frontend/src/routes/MarketData.tsx` — `⟳ Reload from positions` button next
   to Refresh All, disabled while fetching, passing the current form payload via
   a new `onReloadFromPositions` prop.

**Pricing parameters need no code:** pricing-parameter profiles build
per-underlying from market-data profiles; once each book underlying has a
snapshot, that pipeline has its inputs.

**Out of scope (YAGNI):** auto-activating draft underlyings (curation stays on
the Underlyings page); a new backend bulk endpoint; concurrency in the fetch
loop (sequential matches Refresh All and keeps AKShare rate-limits happy).

## Error handling

- Sync POST failure → error feedback, nothing else runs.
- Per-symbol AKShare failure → counted + listed in the summary, loop continues.
- Unresolvable symbols (no AKShare mapping) → skipped + listed.

## Testing (vitest)

- `MarketData.live.test.tsx`: mocked fetch where the sync returns one resolvable
  and one unresolvable underlying → asserts the sync POST happened, exactly one
  snapshot POST (for the resolvable symbol with the form's dates), the skip is
  named in the feedback, and the profile list reloads.
- A failure-path case: snapshot POST rejects → summary reports `1 failed`, the
  loop completed.
- `MarketData.test.tsx`: the button renders and calls `onReloadFromPositions`
  with the current form payload; disabled while `fetching`.

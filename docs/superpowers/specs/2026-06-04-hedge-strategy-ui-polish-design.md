# Hedge Strategy tab — UI/UX polish (panels, KPI cards, booking banner)

Date: 2026-06-04
Status: approved (brainstormed via visual companion; layout A, KPI option B,
banner option B all user-selected)

## 1. Goal

The Hedge Strategy tab is functionally complete but reads as a flat stack of
text rows. Restructure it around the design system's existing `Panel` and
`Tile` components so the solve→book workflow is legible at a glance, and add
a booking-result banner so the user sees what happened after **Book hedge**
— today the success/failure outcome is silent.

Builds on the uncommitted working-tree changes (pricing-profile select,
hard-band diagnostics, loosen-band / book-anyway); those behaviors are
preserved unchanged and reorganized, not rewritten.

## 2. Page structure (presentational `HedgeStrategy.tsx`)

Top-to-bottom:

1. **Toolbar** — Portfolio / Pricing profile / Strategy selects; RiskRun date
   + stale badge + *Run risk* right-aligned. The `no_risk_run` warning row
   keeps its current behavior.
2. **Booking banner** *(new)* — full-width; rendered only after a Book
   attempt (§4).
3. **KPI strip** *(new)* — three `Tile`s (Δ, Γ, ν) in a 3-column grid,
   collapsing to 1 column ≤640px (§3). Rendered when `targets` exist.
4. **Panel "Hedge legs"** — the existing `Panel` component. Header meta:
   strategy label + leg count (e.g. `Δ-neutral · 1 leg`). Body, in order:
   - `BandEditor` row (component unchanged, relocated here),
   - legs table (unchanged columns: Contract / Role / δcash/lot / Qty /
     Swap / remove),
   - footer row: `+ add leg…` select and the **Solve** button side by side.
5. **Messages zone** — unchanged logic and order: delta-too-small note,
   infeasible line, hard-band diagnostics with *Loosen band* / *Book
   anyway*, pricing warnings.
6. **Book bar** — primary **Book hedge**, right-aligned.

The exposure row (`hedge-strategy__targets`), standalone bands row, and
residual row are removed; their information moves into the KPI tiles and the
panel.

## 3. KPI tiles

Use the existing `Tile` (`label`, `value`, `delta` sub-line, `pos`/`neg`
variants). Per greek g ∈ {delta, gamma, vega}:

- **Before solve** (no proposal residual): label `Δ CASH`, value =
  `fmt(targets[g])`, default variant, sub-line `band ±fmt(bands[g])` (band
  segment omitted if bands are not loaded).
- **After solve** (`proposal.residual` present and status ≠ `infeasible`):
  label `Δ RESIDUAL`, value = `fmt(residual[g])` suffixed `✓`/`✗`, variant
  `pos` when `in_band[g] === true`, `neg` when `false`, default when
  undefined; sub-line `from fmt(targets[g]) · band ±fmt(bands[g])`.
- Infeasible proposal → tiles stay in exposure mode; the existing infeasible
  message explains.

## 4. Booking banner

### Success (receipt)

Header line: `✓ Hedge booked to portfolio <name> · risk run <YYYY-MM-DD>` +
*view in Positions* link. Then one receipt line per booked leg:
`IF2606 × 4 (delta) → position #214`.

Receipt construction: the backend books non-zero-quantity legs in proposal
order and returns `position_ids` in that order, so the frontend zips
`legs.filter(l => l.quantity !== 0)` with `position_ids`. No backend change.

### Failure

`✕ Booking failed — nothing was booked` (booking is atomic) + the API error
text. `api()` already throws `Error(await response.text())`, so
`err.message` carries the backend detail.

### Lifecycle

- Persists until ✕-dismissed.
- Auto-clears on: portfolio change, underlying change, a new Solve, and the
  next Book attempt (replaced by the new outcome).
- *Book anyway* feeds the same banner.

### "view in Positions" link

Navigation is App-local state (`setRoute` in `main.tsx`); no router. Thread
props:

- `main.tsx` → `HedgingLive`: `onNavigate?: (route: Route) => void`
  (page-level, route-aware, reusable).
- `HedgingLive` → `HedgeStrategyLive` → `HedgeStrategy`: narrow
  `onViewPositions?: () => void` so the leaf stays ignorant of app routing.
- Rendered as a ghost-styled **button** (it triggers state navigation, not a
  URL); hidden when the prop is absent (e.g. unwired test renders).

## 5. Types & state plumbing

- `types.ts`: add
  `HedgeBookResponse { status: 'booked'; portfolio_id: number; underlying: string; risk_run_id: number; position_ids: number[] }`.
- `HedgeStrategy.live.tsx`: `bookProposal` captures the response (success)
  or catch (failure) into new state
  `bookingResult: HedgeBookingResult | null`, where

  ```ts
  type HedgeBookingResult =
    | { kind: 'success'; portfolioName: string; riskRunDate: string | null;
        legs: { contractCode: string; quantity: number; role: string; positionId: number }[] }
    | { kind: 'error'; message: string }
  ```

  passed to the presentational component with `onDismissBookingResult`.
  Existing clear-points (§4 lifecycle) reset it.

## 6. CSS

New classes in `HedgeStrategy.css` only; `Panel.css` / `Tile.css` untouched.

- `hedge-strategy__banner` + `--ok` / `--err` modifiers: left-accent border
  (4px) using `--pos` / `--neg`, same idiom as the existing
  `hedge-strategy__note` info box; flex layout with ✕ dismiss at the end.
- `hedge-strategy__kpis`: 3-column grid, `--gap-2`, 1 column ≤640px
  (mirrors `wl-positions__detail-kpis`).
- `hedge-strategy__panel-foot`: add-leg + Solve row.
- `hedge-strategy__bookbar`: right-aligned.
- Remove styles that lose their consumers (`__targets`, `__target`,
  standalone `__residual`, `__solvebar`) — verify no other usages first.

## 7. Testing

Presentational (`HedgeStrategy.test.tsx`):

- Tiles: exposure mode before solve (label, value, band sub-line); residual
  mode after solve with `pos`/`neg` variant classes; exposure mode retained
  on infeasible.
- Banner: success receipt lines (contract × qty → position id), error
  message, ✕ fires `onDismissBookingResult`, *view in Positions* fires
  `onViewPositions` and is absent when the prop is unwired.
- Existing assertions updated for the new DOM structure (Panel header,
  relocated BandEditor/Solve).

Live (`HedgeStrategy.live.test.tsx`):

- Successful book → banner with position ids zipped to non-zero legs.
- Failed book (fetch rejects with body text) → error banner; no crash.
- Banner clears on underlying change and on re-solve.
- `onNavigate` passthrough: render `HedgeStrategyLive` with
  `onViewPositions` wired, click the banner link, assert the callback fired
  (the `HedgingLive → Route` mapping is a one-liner, not separately tested).

No backend or API changes.

## 8. Out of scope

- Cross-page deep link to the specific booked positions (Positions page
  filtering by id) — the link lands on the Positions page only.
- Band-editing affordance inside the panel header bar.
- Auto-dismiss timers; richer toast system.
- Restyling the Allowed Instruments tab.

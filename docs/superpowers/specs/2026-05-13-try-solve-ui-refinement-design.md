# Try to Solve UI Refinement

## Overview

Patch 8 visual bugs and inconsistencies on the Try to Solve page.

## Issues Fixed

### 1. Date input shows CJK placeholder
Empty `type="date"` inputs display browser locale placeholder (e.g., "年 /月 /日" on Chinese systems). The app already sets `lang="en"` on `<html>`, but browsers may override this with system locale.

**Fix:** Target `input[type="date"]:invalid` within `.wl-try-solve__field` and add `background: var(--paper-2)` so empty date inputs visually signal "unfilled field" rather than rendering a locale-specific placeholder as the only visual state.

**File:** `TrySolve.css`

### 2. Form inputs lack visual boundaries
All text inputs and selects have `border: 0` and transparent backgrounds. They blend into the panel background, making interactive fields indistinguishable from labels. Only the 1px grid gap provides separation.

**Fix:** Add `border: 1px solid var(--hairline)` to `.wl-try-solve__field input` and `.wl-try-solve__field select` by default.

**File:** `TrySolve.css`

### 3. Panel body padding overridden to 0
The queue and editor panels override `.wl-panel__body` padding to `0`, causing content to touch panel edges. Other routes (Positions, Pricing Parameters) keep the default padding.

**Fix:** Remove the `.wl-try-solve__queue-panel .wl-panel__body` and `.wl-try-solve__editor-panel .wl-panel__body` padding override rules.

**File:** `TrySolve.css`

### 4. Odd field count creates empty grid cells
Autocall has 9 fields arranged in a 2-column grid, leaving the 10th position empty. The market data grid (5 fields) has the same problem.

**Fix:** Add a CSS rule targeting `.wl-try-solve__field:last-child:nth-child(odd)` to make the last field span the full width (`grid-column: 1 / -1`).

**File:** `TrySolve.css`

### 5. Status badges use custom styling
Request queue status indicators (SOLVER READY, SOLVED, MISSING TERMS) use `.wl-try-solve__status` with custom borders. Positions and other routes use the shared `Badge` component.

**Fix:** Replace `.wl-try-solve__status` spans with the `Badge` component. Map status classes to Badge variants:
- `ready` / `solved` → `pos`
- `attention` → `neg`
- `captured` → `warn`
- `neutral` → `ink`

**File:** `TrySolve.tsx`

### 6. Dropdown text truncation
"Pricing Parameters" and "Market Data" dropdowns in Quote & Solve truncate "Manual only" / "Manual spot" to "Manu..." due to narrow column widths.

**Fix:** Two changes:
- Widen the right panel (Quote & Solve) in the workbench grid from `minmax(300px, 0.92fr)` to `minmax(340px, 1.1fr)`
- Narrow the label column in quote-grid fields from `minmax(128px, 0.48fr)` to `minmax(108px, 0.4fr)` via a targeted selector on `.wl-try-solve__quote-grid .wl-try-solve__field`

**File:** `TrySolve.css`

### 7. Custom Metric styling inconsistent with shared Tile
The solve status display (Product Status, Solver State, Target, Quote Bounds) uses a custom Metric component. Positions uses the shared Tile component.

**Fix:** Keep the Metric component (Tile's 20px value is too large for this dense panel), but add:
- `border: 1px solid var(--ink)` to match Tile's bordered style
- `letter-spacing: 0.05em` to the label to match Tile's label typography

**File:** `TrySolve.css`

### 8. Inconsistent focus outline offset
TrySolve fields use `outline-offset: -2px` (inset). Positions' `.wl-positions__select-field:focus-within` uses `outline-offset: 2px` (outset).

**Fix:** Change `.wl-try-solve__field:focus-within` from `outline-offset: -2px` to `outline-offset: 2px`.

**File:** `TrySolve.css`

## Architecture

No new components or files. Changes are confined to:
- `frontend/src/routes/TrySolve.css` — 6 CSS fixes
- `frontend/src/routes/TrySolve.tsx` — replace status spans with Badge component

## Data Flow

No data flow changes. Purely visual/CSS changes.

## Error Handling

No error handling changes.

## Testing

Existing tests in `TrySolve.test.tsx` should continue to pass. No new tests needed for visual fixes.

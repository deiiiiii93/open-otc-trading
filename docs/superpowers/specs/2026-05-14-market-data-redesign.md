# Market Data Page Redesign

**Date:** 2026-05-14  
**Status:** Approved  
**Scope:** `frontend/src/routes/MarketData.tsx`, `frontend/src/routes/MarketData.css`

---

## Problem

The current Market Data page renders all profiles as a flat, unsearchable list of buttons in a left panel. With ~20 profiles it requires significant scrolling and offers no way to filter by symbol or date. Users cannot quickly jump to a specific underlying or compare multiple instruments.

---

## Goals

- Find any profile by symbol substring or end-date substring in one keystroke
- See all loaded profiles at a glance with live price / change data in a ticker strip
- Filter the OHLCV table by date range without a new API call
- Layout fits the viewport — no full-page scroll
- Visual style matches the existing repo design system exactly

---

## Out of Scope

- Multi-select / side-by-side comparison (deferred; ~20 profiles makes single-select sufficient for now)
- Chart / candlestick view (deferred)
- New API endpoints — all behavior is derived from existing data
- Changes to `MarketData.live.tsx`, API calls, or `MarketDataProfile` type

---

## Design

### Layout

Full-height terminal layout. Five zones stacked top-to-bottom, none of which scroll independently except the OHLCV table and instrument list:

```
┌─────────────────────────────────────────────┐
│  Ticker strip (horizontal scroll)           │
├─────────────────────────────────────────────┤
│  Command bar  [search input]  [period btns] │
├──────────────┬──────────────────────────────┤
│              │  Security header             │
│  Instrument  ├──────────────────────────────┤
│  list        │  Stats bar                   │
│  (240px)     ├──────────────────────────────┤
│  scrollable  │  OHLCV table (scrollable)    │
│              │                              │
├──────────────┴──────────────────────────────┤
│  Status bar                                 │
└─────────────────────────────────────────────┘
```

The outer page container uses `display: grid; grid-template-rows: auto auto 1fr auto` to fill the viewport height without overflow.

### Zone 1 — Ticker Strip

- One chip per loaded profile showing: `symbol · last_close · Δ · Δ%`
- Colors: `--pos` for positive change, `--neg` for negative, `--ink-2` for zero
- Clicking a chip selects that profile (sets `selectedId`)
- Active profile chip gets a `--warn` bottom border (2px)
- Chips are filtered by the current search query (same filter as the left panel)
- Implemented with `overflow-x: auto` and `white-space: nowrap`

### Zone 2 — Command Bar

Single toolbar row containing:

| Element | Detail |
|---|---|
| Search input | Placeholder: `Symbol or date — e.g. RB · 2026-03`. Controlled string `query`. |
| Period presets | Buttons: 1M · 3M · 6M · 1Y · All. Active preset gets `background: var(--ink); color: var(--paper)`. |
| Custom date range | Two text inputs (from / to) in `YYYY-MM-DD` format. Selecting a preset overwrites these. Clearing both reverts to "All". |

Search and period filter are independent — search filters the instrument list and ticker strip; the date range slices the OHLCV rows in the selected profile.

### Zone 3a — Instrument List (left panel, 240px)

- Profiles grouped by `asset_class`, groups sorted alphabetically
- Within each group, profiles sorted by `symbol`
- Profiles with empty/null `asset_class` collected in an "Other" group at the bottom
- Each row: `symbol` (Berkeley Mono, 11px, bold) + `ends YYYY-MM-DD` (10px, `--ink-2`) on the left; `last_close` + `Δ%` on the right
- Active row: `border-left: 2px solid var(--warn); background: var(--paper)`
- Hover: `background: var(--paper-3)`
- Group headers: `background: var(--paper-3); caps style (10px/600/uppercase)`
- A button at the bottom of the list (or in the list header) opens the "Add Profile" modal

### Zone 3b — Right Panel

**Security header:**
- Symbol in `--font-numeric`, 18px bold (e.g. `RB2610`)
- Subtitle: `{name} · {source.toUpperCase()} · {asset_class} · Adj: {adjust ?? "none"}`
- Tab row: `Historical | Describe` — sharp-cornered tab strip matching AppShell style

**Stats bar** (shown for Historical tab):

| Stat | Source |
|---|---|
| Last | `data` last row close |
| Chg | `last_close − prev_close` |
| Chg % | `(last_close − prev_close) / prev_close × 100` |
| Open | `data` last row open |
| High | `data` last row high |
| Low | `data` last row low |
| Rows | `data.length` (after date filter) |
| Date range | `first_row.date – last_row.date` (after date filter) |

If fewer than 2 rows exist, Chg and Chg% display `—`.

**Historical tab — OHLCV table:**
- Reuses the existing `Table` component with columns: Date · Open · High · Low · Close · Chg · Chg% · Volume
- Date column: left-aligned, caps style, `--ink-2`
- Numeric columns: right-aligned, `--font-numeric`, 11px
- Close / Chg / Chg% cells colored `--pos` / `--neg` / `--ink-2` based on sign
- Most recent row gets `background: var(--paper-3)` to distinguish it
- Rows derived from `profile.data` sliced by the date range filter — no API call

**Describe tab:**
- Simple two-column key/value grid showing all `MarketDataProfile` metadata fields: id, source, symbol, asset_class, start_date, end_date, adjust, valuation_date, created_at, updated_at, source_metadata (JSON block)

### Zone 4 — Status Bar

`● Connected · Source: {profile.source} · Fetched: {profile.updated_at} · {rowCount} rows · {profileCount} profiles`

Status dot: `--pos` green. Right-aligned: row count and profile count.

### Add Profile Modal

The existing AKShare fetch form moves into a `Modal` component (already exists in the codebase). Triggered by an "Add" button (or `+` icon) in the instrument list header. Behavior is identical to the current form — no changes to the payload or API call.

---

## State

All new state lives in `MarketData.tsx` (presentational component via props + local state). No changes to `MarketData.live.tsx`.

| State | Type | Owner | Purpose |
|---|---|---|---|
| `query` | `string` | `MarketData.tsx` | Search filter for symbol / date |
| `period` | `'1M' \| '3M' \| '6M' \| '1Y' \| 'all'` | `MarketData.tsx` | Active period preset |
| `dateFrom` | `string \| null` | `MarketData.tsx` | Custom range start |
| `dateTo` | `string \| null` | `MarketData.tsx` | Custom range end |
| `activeTab` | `'historical' \| 'describe'` | `MarketData.tsx` | Right panel tab |
| `showAddModal` | `boolean` | `MarketData.tsx` | Add Profile modal visibility |
| `selectedId` | `number \| null` | `MarketData.live.tsx` | Already exists — no change |

---

## Data Derivations

All computed in the presentational component, no `useMemo` required at this scale:

```ts
// Filter profiles shown in list + ticker strip
const visibleProfiles = profiles.filter(p =>
  p.symbol.toLowerCase().includes(query.toLowerCase()) ||
  p.end_date.includes(query)
);

// Group visible profiles by asset_class
const groups = groupBy(visibleProfiles, p => p.asset_class || 'Other');

// Slice OHLCV rows by date range
const filteredRows = selected?.data.filter(row =>
  (!dateFrom || row.date >= dateFrom) &&
  (!dateTo   || row.date <= dateTo)
) ?? [];

// Compute change from last two rows
const lastClose = filteredRows.at(-1)?.close ?? null;
const prevClose = filteredRows.at(-2)?.close ?? null;
const chg    = lastClose != null && prevClose != null ? lastClose - prevClose : null;
const chgPct = chg != null && prevClose ? (chg / prevClose) * 100 : null;
```

Period presets compute `dateFrom` relative to `profile.end_date` (not today), so the filter always anchors to the data's own timeline.

---

## Files Changed

| File | Change |
|---|---|
| `frontend/src/routes/MarketData.tsx` | Full rewrite of presentational component |
| `frontend/src/routes/MarketData.css` | Full rewrite of styles |
| `frontend/src/routes/MarketData.live.tsx` | No change |
| `frontend/src/types.ts` | No change |

No new components are introduced. The existing `Table`, `Modal`, `Button`, `Tile`, and `PageHeader` components are reused where appropriate.

---

## Visual Style

All styles use existing design tokens. No new tokens introduced.

- **Colors:** `--paper` / `--paper-2` / `--paper-3` / `--hairline` / `--hairline-2` / `--ink` / `--ink-2` / `--pos` / `--neg` / `--warn`
- **Typography:** `--font-ui` (Inter Tight) for labels; `--font-numeric` (Berkeley Mono) for all prices, symbols, dates
- **Label style:** `font-size: var(--type-caps-size); font-weight: var(--type-caps-weight); text-transform: uppercase; letter-spacing: 0.06em`
- **Corners:** Sharp (no border-radius) throughout, consistent with the rest of the app
- **Density:** Default density tokens; compact mode (`[data-density="compact"]`) works automatically via existing token cascade

---

## Edge Cases

| Situation | Behaviour |
|---|---|
| 0 profiles loaded | Left panel and ticker strip show empty state using existing `Empty` component |
| Search matches nothing | List shows "No profiles match" message inline; ticker strip is empty |
| Profile has only 1 data row | Chg and Chg% display `—` |
| `asset_class` is null/empty | Profile grouped under "Other" |
| `data` field is empty array | Stats bar shows `—` for all price fields; table shows empty state |
| Period filter excludes all rows | Table shows empty state; stats show `—` |
| Loading state | Left panel and ticker strip are disabled/dimmed; existing `loading` prop drives this |

# Portfolios page UI/UX redesign

**Date:** 2026-05-11
**Status:** Design approved, awaiting implementation plan
**Brief:** Bring the Portfolios route into visual and interaction parity with the rest of the Warm Ledger design system, and reorganize the page around author-first workflow (build views with rules + manual overrides + sources, with a live preview).

## Why

The current Portfolios page (`frontend/src/routes/Portfolios.tsx`) was built before the Warm Ledger redesign settled. It has functional logic but:

1. **No `PageHeader`** — every other main route uses the uppercase H1 page header; Portfolios is the visual odd-one-out.
2. **Sidebar list dominates a page where users mostly stay on one portfolio** — the target user is an author who opens one view, edits its definition, and watches the preview update. A 320px sidebar with five rows is not earning its space.
3. **Identical visual weight** on Rule / Sources / Manual includes / Manual excludes — a flat stack of four sections with the same `<h3>` and no grouping.
4. **No primary CTA** — "Run pricing" and "Run risk" share the same default button variant.
5. **Resolved table competes with the editor** — a 195-row scrollable list on the right, with no totals and no pagination, fights the editor for vertical space and offers no summary.
6. **"+ source" / "+ pick" do not pick** — they silently auto-add the first available item. There is no real picker UI.
7. **`window.prompt` / `window.confirm`** are used for portfolio create and delete, breaking the route's modal patterns elsewhere.
8. **Tag chips silently truncate at 3** with no "+N more" affordance.
9. **`KindChip.css` references undefined CSS variables** (`--wl-surface-2`, `--wl-accent`, `--wl-text`) — these don't exist in the current `frontend/src/tokens/colors.css`, so the chip is rendering with browser defaults.

## Audience and workflow

**Primary user:** *Author* — regularly builds views with rules, source-portfolio unions, and manual include/exclude overrides. Needs fast feedback between editing the definition and seeing what positions resolved.

**Secondary use:** Operate (run pricing/risk daily), Audit (compare across portfolios). These are accommodated but don't drive layout.

## Final design

The page reuses existing primitives (`PageHeader`, `Tile`, `Panel`, `Button`, `Chip`, `Modal`, `Table`) and the project's token system. No new design language is introduced.

### Page topology

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ PageHeader: H1 "PORTFOLIOS · {selected.name}"                  [Portfolio ▾] │
│             chips: KIND · {n} positions · {m} resolved · updated {ago}       │
│                                                          [+ New ▾] [⋯]       │
│                                                          [Run Pricing primary]│
├──────────────────────────────────────────────────────────────────────────────┤
│ Tiles row (4):  POSITIONS │ UNDERLYINGS │ NET QTY │ STATUS                   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Toolbar: [⌕ Search resolved positions...]                  1-25 of 195 ‹ ›   │
├──────────────────────┬───────────────────────────────────────────────────────┤
│ Panel: DEFINITION    │ Panel: RESOLVED POSITIONS                             │
│   auto-saved 12s ago │   195 rows · 7 underlyings                            │
│ ┌──────────────────┐ │ ┌──────────────────────────────────────────────────┐  │
│ │ fieldset RULE    │ │ │ Table(TRADE · UNDER · PRODUCT · QTY · STATUS)    │  │
│ │ fieldset SOURCES │ │ │ paginated (25/page default)                      │  │
│ │ fieldset INCLUDES│ │ │                                                  │  │
│ │ fieldset EXCLUDES│ │ │                                                  │  │
│ │ fieldset TAGS    │ │ │                                                  │  │
│ └──────────────────┘ │ └──────────────────────────────────────────────────┘  │
└──────────────────────┴───────────────────────────────────────────────────────┘
```

The 240px AppShell sidebar (route nav) stays. The page-internal 320px portfolio sidebar is **removed**.

### Component mapping (existing primitives)

| Region | Component | Notes |
|---|---|---|
| Top heading | `<PageHeader title chips action>` | Title becomes `PORTFOLIOS · {selected.name}` (Positions pattern). Chips replace `<KindChip>` inline label and add resolved-count + last-updated. Action slot holds the Portfolio select, `+ New ▾` menu, `Run Pricing` primary, and `⋯` overflow. |
| Portfolio switcher | `<label class="wl-portfolios__select-field">` mirroring `wl-positions__select-field` | New CSS class; layout/style copied from Positions. Combobox-style `<select>` of all portfolios with `name · kind`. |
| `+ New ▾` | `<Button>` + small dropdown | Two items: "New container portfolio", "New view portfolio". Each opens the create `<Modal>` (see Create flow). |
| 4-tile row | `<Tile>` × 4 | POSITIONS · UNDERLYINGS · NET QTY (variant `pos`/`neg` by sign) · STATUS (text e.g. "ALL OPEN" / "MIXED"). Tiles use solid ink border per `Tile.css`. For Container portfolios with `positions.length === 0`, tiles show `—`. |
| Search/pager toolbar | New class `wl-portfolios__toolbar` mirroring `wl-positions__toolbar` | Reuses the search input style + pager from Positions verbatim. Filters resolved positions, not the portfolio list. |
| Definition panel | `<Panel title="DEFINITION" meta={saveState}>` | Body contains 5 `<fieldset>` blocks with `<legend>`: RULE, SOURCES, MANUAL INCLUDES, MANUAL EXCLUDES, TAGS. Each fieldset has a header row (`fieldset-meta` + secondary action button). |
| Rule subsection | Existing `<RuleEditor>` (Builder/Text toggle) inside the RULE fieldset | Visual restyling only: tabs styled as ghost buttons with active border (matching Positions toolbar buttons), not the current pill toggle. |
| Sources subsection | New `SourcePicker` (modal-launched) | "+ source" opens a `<Modal>` listing other portfolios with search; click to add. Selected sources render as `<Chip>` rows. |
| Manual includes/excludes | Existing `PositionPicker` (`components/PositionPicker.tsx`), extended if needed (see "Component changes outside this page") | "+ pick position" opens a `<Modal>` with searchable position list. Selected items render as `<Chip>` with id + underlying. |
| Tags subsection | Existing `<TagEditor>` | Move inside a fieldset with TAGS legend; styling already compatible. |
| Resolved panel | `<Panel title="RESOLVED POSITIONS" meta>` containing existing `<Table>` from `components/Table.tsx` | Replaces `ResolvedPositionsTable`. The new table uses the same `Column<>` schema as Positions, plus pagination. |
| Empty state | `<Empty>` | Used when no portfolio is selected or when the resolved set is empty. |
| Create / Delete | `<Modal>` | Replaces `window.prompt` / `window.confirm`. Create modal: name input + kind already chosen from menu. Delete modal: confirmation with portfolio name echoed. |

### Container vs View

Both kinds use the same two-panel layout. For **Container** portfolios:

- DEFINITION panel hides the RULE, SOURCES, MANUAL INCLUDES, MANUAL EXCLUDES fieldsets — those are not applicable.
- DEFINITION panel keeps a single fieldset: TAGS, plus a one-line empty caption "Container holds owned positions imported via XLSX. Use the Positions page to add positions."
- RESOLVED POSITIONS panel title becomes **OWNED POSITIONS**; meta shows `{n} rows`.

For **View** portfolios, all five fieldsets render.

This keeps Container portfolios visually consistent with Views (same chrome) while making the operational difference legible.

### Interaction details

**Auto-save indicator.** Rule edits already debounce-save at 250ms. Surface this:
- `Panel.meta` on DEFINITION shows one of: `editing…`, `saving…`, `auto-saved {N}s ago`, `save failed — retry`.
- State transitions: any keystroke or interaction → `editing…` until 250ms idle → `saving…` until response → `auto-saved 0s ago` → ticks up while idle. Errors land in `save failed — retry` with the retry click re-issuing the last save payload.

**Portfolio switcher.** The Portfolio select in the PageHeader action slot is the primary navigator. Selecting a new portfolio is one click. Searching among portfolios is solved at the AppShell level by the existing `CommandPalette` (⌘K), which already supports a "Jump To" group — we add per-portfolio entries to it (out of scope for this redesign; the select is sufficient for v1).

**Filters (kind / tag).** Removed from the page. Reasoning: with the sidebar gone, kind filtering is irrelevant (you pick a portfolio by name). Tag filtering wasn't actually wired to the API URL before, and tags are shown per-portfolio inside the DEFINITION TAGS fieldset. If we later need cross-portfolio tag filtering, add it inside the Portfolio select dropdown as an optional filter row.

**+ source / + pick.** Each opens a `<Modal>` with:
- For sources: searchable list of other portfolios (`name · kind · {n} positions`).
- For positions: searchable list (id, underlying, product_type, qty), reusing `PositionPicker.tsx` if its API supports include/exclude filtering; otherwise extend it.
- Both modals support multi-select and an "Add N" primary button.

**Tile values.**
- `POSITIONS`: `resolved_position_count` (View) or `positions.length` (Container).
- `UNDERLYINGS`: distinct count of underlyings in the resolved/owned set.
- `NET QTY`: sum of `quantity`, signed. Berkeley Mono, variant `pos` if > 0, `neg` if < 0.
- `STATUS`: derived from `status` field across rows — `ALL OPEN` if all open, `MIXED` otherwise, with `--type-num-m-size` instead of `--type-num-l-size` (text, not number).

**Run Pricing / Run Risk.** Both move into the PageHeader action slot. `Run Pricing` is the primary CTA. `Run Risk` and other secondary actions (Duplicate, Delete) move into the `⋯` overflow menu next to the primary. This frees vertical space and follows the Positions header pattern.

### Component changes outside this page

- **`KindChip.css`** — replace legacy `--wl-*` variables with the real tokens. Use `var(--paper-3)` bg + `var(--ink)` text for `container`, and inverted `var(--ink)` bg + `var(--paper)` text for `view`, with a 1px hairline border in both cases. Keep the rounded-pill shape since it reads as "kind" and contrasts with `Chip` (square). This is a bug fix bundled into this redesign.

- **`PositionPicker`** (existing) — verify it supports the use case (multi-select, include/exclude context). If not, extend the props to accept `excludeIds: number[]` and an `onConfirm(ids: number[]) => void` API. Do not introduce a second picker.

- **`Modal`** (existing) — used as-is. Just need a confirmation variant for Delete; if not present, add `<Modal.Confirm>` thin wrapper.

## Out of scope

- Cross-portfolio tag filter facets (defer until requested).
- Drag-and-drop reordering of sources or rule conditions.
- Bulk operations across portfolios (delete many, retag many).
- Saving the toolbar search query in the URL.
- Mobile breakpoint redesign — the route is internal-tools-only; we keep the existing `@media` breakpoints from sibling routes (`max-width: 980px` collapses two-pane to one-pane stacked, `max-width: 640px` collapses tiles to single-column).
- Adding pricing-results columns (price, P&L, market value) to the resolved table. The current table shows position metadata only; mixing in pricing-run state belongs to a later iteration once "Run Pricing" returns are wired through.

## Testing plan

Component tests (Vitest + React Testing Library) following the existing `Portfolios.test.tsx` / `Portfolios.live.test.tsx` pattern:

- **`Portfolios.test.tsx`** (pure rendering): renders PageHeader with title `PORTFOLIOS · {name}`, four Tiles with correct values, both Panels, the five fieldsets for a View, and the single TAGS fieldset for a Container.
- **Switcher test**: selecting a different portfolio in the Portfolio select calls `onSelectPortfolio` with the right id.
- **Auto-save indicator test**: when `saveState` prop transitions `idle → saving → saved`, the Panel meta text updates accordingly.
- **Picker integration**: clicking "+ pick position" opens the Modal; confirming with 2 ids calls `onAddInclude` twice (or once with `[id1, id2]` if we batch).
- **Create/Delete modal**: replaces the existing `window.prompt`/`window.confirm` assertions in `Portfolios.live.test.tsx` with modal-based interactions.
- **KindChip render test**: snapshot covers both `container` and `view` variants in light and dark themes (sanity check that the bug fix sticks).

Manual verification:

- Light and dark themes both render correctly (kind chip, tiles, panels).
- Compact density (`<html data-density="compact">`) tightens row heights as expected.
- Switching between View and Container portfolios hides/shows fieldsets without layout jumps.
- Auto-save indicator transitions through all four states (editing, saving, saved, failed) using a forced API error.

## Follow-ups (not in this design)

- Wire a portfolio-scoped CommandPalette section so `⌘K → "snowball"` jumps to a portfolio by name.
- Show pricing-run results inline in the resolved table once `onRunPricing` returns a run id we can poll.
- Add a "Last priced" timestamp to the Tiles row when pricing has run.

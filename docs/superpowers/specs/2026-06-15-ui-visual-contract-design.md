# UI Visual Contract (Round 1) — Design

**Date:** 2026-06-15
**Status:** Approved (design), pending implementation
**Branch:** `worktree-ui-unification` (continues the template-unification work; not merged)
**Predecessor:** `docs/superpowers/specs/2026-06-14-ui-template-unification-design.md`

## Problem

The template-unification refactor classified ~20 route pages into six layout
templates (A–F) over five region primitives. On live review the result still
looks **visually inconsistent**. Root-cause analysis (measuring the rendered DOM
of every page on the running dev server, not screenshots) found the inconsistency
is **not** a bad taxonomy — it is two concrete defects in the shared layer plus
one un-unified surface:

1. **Master-detail splits collapse to a single column (6 of 8 C-family pages).**
   `SplitLayout` exposes a `railWidth` override that it applies by writing the
   **same** custom property it consumes:

   ```tsx
   // SplitLayout.tsx (current)
   const style = railWidth ? ({ '--rail-width': railWidth } as React.CSSProperties) : undefined;
   ```
   ```css
   /* SplitLayout.css (current) */
   grid-template-columns: var(--rail-width) minmax(0, 1fr);
   ```

   Six pages pass `railWidth="var(--rail-width)"`. That assigns
   `--rail-width: var(--rail-width)` **on the same element** — a self-referential
   custom-property cycle. Per CSS spec a property that references itself resolves
   to the *guaranteed-invalid value*, so `grid-template-columns` becomes invalid
   and falls back to `none`. The grid auto-places into **one** column: the rail
   renders full-width with the workspace stacked **below** it.

   Measured on the running app (viewport 2133px):
   - `/skills`, `/tracing` → `grid-template-columns: 1844.67px` (single track),
     rail `width: 1845`, workspace `top` far below the rail. **Broken.**
   - `/try-solve` (no override) → `grid-template-columns: 220px 1612.67px`,
     rail and workspace share `top` (side-by-side). **Correct.**

   Affected (all pass `railWidth="var(--rail-width)"`): **Skills, Tracing,
   Hedging, RfqApproval, PricingParameters, EngineConfigs.** Unaffected (omit the
   override, inherit the `:root` token): TrySolve, Instruments, ScenarioTest,
   Backtest.

   The defect survived the prior review because `SplitLayout.test.tsx` asserts the
   **inline-style string** (`style` contains `--rail-width: var(--rail-width)`),
   never the resulting layout — it pinned the broken behavior — and visual QA
   verified colors, not geometry (a known dark-screenshot capture quirk in the
   review browser).

2. **No enforced vertical rhythm.** `.wl-scaffold__body` is
   `display:flex; flex-direction:column` with **no `gap`**. The 12px rhythm
   between page regions exists only because `MetricRow`, `TableToolbar`, and
   `AnalyticsDashboard`'s `__controls` each independently set
   `margin-bottom: var(--gap-3)`. Nothing structurally enforces it; any region
   that omits or changes its margin drifts, and the rhythm is duplicated across
   primitives instead of declared once.

3. **Empty / loading / no-data states are only partly unified.** A shared
   `Empty` component already exists (`frontend/src/components/Empty.tsx`, owning
   `.wl-empty`, props `message`/`symbol`/`action`) and is used in **27 sites**.
   But adoption is incomplete: many pages still hand-roll their own "no data
   yet", "run to see results", and "loading…" messaging with bespoke markup, and
   `Empty` has no concept of a *loading* or *error* state, so those cases can't
   route through it even where authors wanted to. The result is inconsistent
   empty/loading presentation across pages.

## Goal

Make the shared layer actually **enforce** a single visual contract, so every
page inherits consistent structure rather than re-expressing it. This round is
**corrective and mechanical** — no new templates, no re-classification, no
content redesign. Token-only styling (`UI_STYLE_GUIDE.md`) is preserved; both
themes and compact density are verified.

Re-classification (revisiting the A–F buckets) is explicitly **deferred to a
possible Round 2**, to be decided only after this round lands and the pages are
re-reviewed live.

## Non-goals

- No new templates, no changes to the A–F taxonomy or page→template mapping.
- No data-flow, routing, `PageContext`, or agent-tooling changes.
- No redesign of page content beyond routing empty/loading states through the
  shared primitive and unifying region spacing.
- No change to `PageHeader`'s own styling (its header→body margin is left intact
  to avoid blast radius into any non-scaffold `PageHeader` usage).

## Design

### Part 1 — Fix the rail-collapse regression

Make the override property a **distinct name** from the token it reads, so a
self-reference is impossible:

```css
/* SplitLayout.css */
.wl-split {
  grid-template-columns: var(--wl-rail-width, var(--rail-width)) minmax(0, 1fr);
  /* …unchanged… */
}
```
```tsx
/* SplitLayout.tsx */
const style = railWidth ? ({ '--wl-rail-width': railWidth } as React.CSSProperties) : undefined;
```

- `--wl-rail-width` is the per-instance override; it falls back to the global
  `--rail-width` token (220px; 200px compact) when unset. A page passing
  `railWidth="var(--rail-width)"` now sets `--wl-rail-width: var(--rail-width)`,
  which resolves cleanly to the token — no cycle.
- **Remove the redundant `railWidth="var(--rail-width)"`** from all six pages
  (Skills, Tracing, Hedging, RfqApproval, PricingParameters, EngineConfigs).
  Each was passing exactly the default; with the bug fixed they render correctly
  on the default and the explicit pass-through is noise. The `railWidth` prop and
  the `--wl-rail-width` override path remain available for genuine future
  per-page widths.
- **Rewrite `SplitLayout.test.tsx`'s width test.** jsdom does not resolve
  `var()` inside `grid-template-columns`, so the unit test cannot assert the
  pixel-resolved track list. Instead it asserts the structural fix: the override
  writes the **distinct** custom property `--wl-rail-width` (not `--rail-width`),
  so a self-reference is impossible — i.e. `root.getAttribute('style')` contains
  `--wl-rail-width: var(--rail-width)` and does **not** define `--rail-width`
  itself. The actual two-column rendering is verified by the live geometry
  re-measure below (jsdom can't, the browser can).

### Part 2 — Enforce vertical rhythm centrally

```css
/* PageScaffold.css */
.wl-scaffold__body { display: flex; flex-direction: column; gap: var(--gap-3); }
```

- The `gap` becomes the **single source** of inter-region rhythm (12px; 8px
  compact, both via `--gap-3`).
- **Remove the redundant vertical margins from every direct child of
  `.wl-scaffold__body`**, or the body `gap` would sum with them to 24px. An
  exhaustive audit of the template body-children found **five** such margins:

  | Rule | File | Margin to remove |
  |---|---|---|
  | `.wl-metric-row` | `MetricRow.css:5` | `margin-bottom: var(--gap-3)` |
  | `.wl-table-toolbar` | `TableToolbar.css:6` | `margin-bottom: var(--gap-3)` |
  | `.wl-analytics__controls` | `AnalyticsDashboard.css:1` | `margin-bottom: var(--gap-3)` |
  | `.wl-stepper` | `Stepper.css:6` | the bottom of `margin: 0 0 var(--gap-3)` |
  | `.wl-wizard__footer` | `WizardPage.css:6` | `margin-top: var(--gap-3)` (live via `Booking.live.tsx`) |

  The remaining body-children (`.wl-split`, `.wl-panel-grid`,
  `.wl-data-table-page__main`, `.wl-conversation`, `.wl-wizard__body`,
  `.wl-workbench__results`) carry **no** vertical margins and need no change.
- `.wl-scaffold__feedback` (`PageScaffold.css:5`, `margin-bottom: var(--gap-2)`)
  is rendered **outside** the body (before it) and is correctly unaffected — left
  as is.
- Header→body separation is **unchanged** (PageHeader keeps its own bottom
  margin; `.wl-scaffold` gains no gap), so this part touches only the body's
  internal rhythm.

### Part 3 — One empty / loading / no-data state

**Evolve the existing `Empty` component — do not introduce a new one.** `Empty`
already owns `.wl-empty` and is adopted in 27 sites; a greenfield `EmptyState`
would collide on the BEM namespace and fork the abstraction. Extend `Empty`'s API
**additively** so existing call sites need no change:

```tsx
// frontend/src/components/Empty.tsx (evolved — existing props kept, two added)
type Props = {
  message: string;                            // primary line (existing)
  symbol?: string;                            // glyph; default '∅' (existing)
  action?: React.ReactNode;                   // optional CTA (existing)
  variant?: 'empty' | 'loading' | 'error';    // NEW — default 'empty'
  hint?: React.ReactNode;                      // NEW — optional secondary line
  className?: string;                          // existing
};
```

- `variant='empty'` keeps today's rendering (dashed border, symbol, message).
  `variant='loading'` drops the dashed border and the `∅` symbol, leaving a quiet
  centered muted message (a transient "Loading…" line — **not** a table skeleton,
  which stays as `Skeleton`; see exclusion below). `variant='error'` tints the
  message with the palette's error/danger token (`--neg`).
  `hint` renders a secondary `--ink-2` line below the message. All
  spacing/colors/typography stay token-only.
- Because the additions are optional, the **27 existing `Empty` call sites are
  untouched** unless they choose to adopt a variant. The migration work is to
  bring the remaining hand-rolled states onto `Empty`.
- **Migration policy — every user-facing empty / no-data / bare-loading state
  routes through `Empty`.** This covers template slots already shaped for it
  (`DataTablePage.empty`, `AnalyticsDashboard.state`) and inline page-level
  states still hand-rolled (e.g. Risk's "run to see results", "select an item"
  workspace placeholders, list-rail "no items", bare "Loading…" text in
  Tracing/Backtest/ScenarioTest). The implementation plan enumerates each genuine
  occurrence per file; pure non-UI matches (test assertions, variable names,
  comments) are excluded.
- **Exclusion — `Skeleton` loaders stay.** Table/row loading is rendered with the
  existing `Skeleton` primitive (6 routes: Reports, Risk.live, Positions.live,
  Booking.live, RfqApproval.live, AgentDesk.live). A centered single-block
  `Empty` cannot represent a multi-row table skeleton; forcing it would be a
  regression. `Empty variant='loading'` is **only** for bare centered "Loading…"
  placeholders, never a replacement for `Skeleton`.
- Where a page currently styles its empty state with bespoke CSS
  (`__empty`/`__placeholder`/etc.), that rule is deleted once the markup moves to
  `Empty`.

## Architecture summary

No new templates and **no new components** — the existing `Empty` primitive is
evolved, not replaced. Two existing primitives are corrected (`SplitLayout` width
override; `PageScaffold` body gap), five redundant body-child margins are
removed, and `Empty` gains optional `variant`/`hint`. The contract the shared
layer now enforces:

- **Rail:** one width token (`--rail-width`), one override channel
  (`--wl-rail-width`), always two-column above the 900px breakpoint.
- **Rhythm:** one inter-region gap (`--gap-3`), declared once on the scaffold
  body; no region carries its own stacking margin.
- **Header:** one `PageHeader` (unchanged — 28px uppercase title, single
  header→body margin).
- **Empty/loading:** one `Empty` primitive for every empty / no-data / bare
  "Loading…" state; `Skeleton` remains the primitive for table/row loading.

## Testing strategy

- **Primitives:** `SplitLayout.test.tsx` rewritten to assert the distinct
  `--wl-rail-width` override prop (cycle-regression); true two-column layout
  comes from the live re-measure. `Empty.test.tsx` extended for the new
  `variant`/`hint` rendering (back-compat: existing-prop rendering still
  asserted). `PageScaffold` body-gap covered by an inline/computed-style
  assertion; a check that `Stepper`/`wl-wizard__footer` no longer carry stacking
  margins.
- **Pages:** existing `*.test.tsx` / `*.live.test.tsx` remain the regression net
  and must pass; selector-coupled assertions on removed bespoke empty-state
  classes update to `wl-empty` while preserving the asserted behavior.
- **Live geometry re-measure:** after implementation, re-run the DOM measurement
  on the dev server for all eight master-detail (C) and two workbench (D) splits;
  every one must report a two-track `grid-template-columns` with the rail at the
  token width and rail/workspace sharing `top`.
- **Both themes + compact density** verified for the new/changed primitives.
- **Phantom-token sweep** (`comm -23` per `UI_STYLE_GUIDE.md`) must be empty;
  `--wl-rail-width` is an instance-set custom prop with a token fallback (same
  sanctioned pattern as `--metric-cols`/`--panel-cols`).
- **Full `vitest run`** green before each review gate.

## Process

Standalone review-subagent gates at spec, plan, and implementation — replacing
human review — as in the predecessor project. Work stays in the
`worktree-ui-unification` git worktree; branch remains unmerged pending the
user's own review.

## Risks

- **Empty-state sweep breadth.** Routing *every* empty/loading state through one
  primitive touches many files. Mitigation: 27 sites already use `Empty` and the
  API change is additive, so they don't churn; the remaining work is migrating
  hand-rolled stragglers, which the plan enumerates; `Skeleton` loaders are
  explicitly excluded; per-page test suites pin behavior; pure non-UI grep
  matches are excluded.
- **Spacing double-count.** Adding the body `gap` without removing **all five**
  body-child margins (MetricRow, TableToolbar, Analytics controls, Stepper,
  WizardPage footer) would double to 24px on those regions. Mitigation: Part 2
  removes all five in the same change; a computed-style check pins 12px, and the
  Stepper/footer cases are explicitly listed.
- **Test that pinned the bug.** The rewritten `SplitLayout` test must assert
  layout, not the inline string, or it will re-admit the regression.

## Out of scope

- Re-classification of pages into templates (possible Round 2).
- AppShell / Sidebar / theme / density systems.
- Backend, routing, PageContext, agent tooling.
- JS chart-color literals (Recharts props) — pre-existing, not CSS.

# Rail-List Unification Design

**Date:** 2026-06-15
**Branch:** `worktree-ui-unification` (git worktree at `.claude/worktrees/ui-unification`; NOT merged)
**Status:** design approved, awaiting spec review

## Problem

The left-rail "pick a record → detail on the right" interaction is implemented five
different ways across the app. One interaction renders as bordered cards, minimal text
rows, contained table-rows, *and* a floating-card tray depending on which page you are on.

Measured divergence (category C — record-selection rails):

| Page | Container | Row separator | Base surface | Active state | Font |
|---|---|---|---|---|---|
| Hedging | none (bare cards) | — | `--paper`, 1px border **each** | pos-tint bg + pos border + 3px left bar + bold | numeric/small |
| Skills | grouped `<aside>` | top hairline | transparent | `--paper-3` | ui/body |
| Tracing | flat `__list` (paper) | none | transparent | `--paper-3` | ui/body |
| EngineConfigs | bordered box | bottom hairline | `--paper-2` | `--paper-3` | ui/body |
| PricingParams | bordered paper-2 tray | gap | transparent → `--paper` active | border appears + 3px left source-stripe | ui+numeric |

Root cause (same as the `Empty` / `HeaderControls` rounds): a **missing shared primitive**.
Each page re-invented "a list of selectable rows in a rail" because there was nothing to
reach for. The cure is one primitive owning the container + row chrome, with pages slotting
their own row *content*.

Two sub-categories are already consistent and out of scope for rewrite:
- **Workbench config rails** (ScenarioTest, Backtest): `__section`/`__panel`/`__section-title`
  CSS is byte-identical between the two — already unified. Live-verify only, no change.
- **Instruments tab strip**: a vertical *navigation* control, not a record list. Surface-only
  alignment (see below); tab semantics preserved.

## Decisions (user-approved)

1. **Canonical look = "Contained rows":** one bordered rail container, rows separated by
   hairlines, `--paper-2` base, `--paper-3` + 3px left accent bar when active.
2. **Scope = 5 selection rails + Instruments surface.** Unify Hedging, Skills, Tracing,
   EngineConfigs, PricingParameters; align the Instruments tab-strip container surface +
   accent-bar width; leave ScenarioTest/Backtest and the rest untouched.
3. **Hedging active state** unifies from the green/pos tint to the shared `--paper-3` + `--ink`
   bar (approved).
4. **PricingParams** keeps its source-type color as the (now always-on) left accent bar
   (approved).

## Architecture: two thin primitives

Same pattern as `Empty` / `HeaderControls`: the primitive owns the chrome; pages slot content.

### `RailList` — the container

`frontend/src/components/RailList.tsx`
```tsx
import './RailList.css';

type Props = {
  children: React.ReactNode;
  scroll?: boolean;       // adds overflow-y:auto + min-height:0 (Skills, Tracing)
  className?: string;
};

export function RailList({ children, scroll = false, className = '' }: Props) {
  return (
    <div className={`wl-rail${scroll ? ' wl-rail--scroll' : ''} ${className}`.trim()}>
      {children}
    </div>
  );
}
```

`frontend/src/components/RailList.css`
```css
/* RailList — wl-rail
   The one bordered container for a left-rail selection list. Rows are RailItems;
   header content (search, filter pills, eyebrow) is any non-item child and sits
   inside the border above the rows. Token-only. */
.wl-rail {
  display: flex;
  flex-direction: column;
  align-self: start;
  min-width: 0;
  border: 1px solid var(--hairline);
  background: var(--paper-2);
  overflow: hidden;
}
.wl-rail--scroll {
  overflow-y: auto;
  min-height: 0;
}
```

### `RailItem` — the row

`frontend/src/components/RailItem.tsx`
```tsx
import './RailItem.css';

type Props = {
  children: React.ReactNode;
  active?: boolean;
  accent?: string;             // a token name, e.g. '--info'; sets the always-on left bar color
  layout?: 'stack' | 'row';    // 'stack' = vertical (Hedging, PricingParams);
                               // 'row' = horizontal name+meta (Skills, Tracing, EngineConfigs)
  onClick?: () => void;
  className?: string;
};

export function RailItem({ children, active = false, accent, layout = 'stack', onClick, className = '' }: Props) {
  const style = accent ? ({ '--rail-item-accent': `var(${accent})` } as React.CSSProperties) : undefined;
  return (
    <button
      type="button"
      className={`wl-rail__item wl-rail__item--${layout}${active ? ' is-active' : ''} ${className}`.trim()}
      style={style}
      aria-current={active ? 'true' : undefined}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
```

`aria-current="true"` is set automatically on the active row — this both unifies selection
semantics across all five rails and preserves PricingParams' existing `aria-current` contract
(see Testing). `layout` chooses the row axis; the default `stack` keeps Hedging/PricingParams'
multi-line cards, `row` keeps the single-line name+meta rows.
```

`frontend/src/components/RailItem.css`
```css
/* RailItem — wl-rail__item
   A selectable row in a RailList. Left accent bar reserves space always; it shows the
   `accent` token color when set (category stripe), and the active bar otherwise. Adjacent
   items are hairline-separated. Token-only. */
.wl-rail__item {
  display: flex;
  gap: var(--gap-2);
  width: 100%;
  text-align: left;
  padding: var(--gap-3);
  background: transparent;
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  cursor: pointer;
  border: none;
  border-left: var(--rail-accent-width) solid var(--rail-item-accent, transparent);
  border-radius: 0;
  transition: background var(--motion-fade) linear;
}
/* Row axis: 'stack' = multi-line cards (Hedging, PricingParams);
   'row' = single line, label grows, trailing meta pushes right. */
.wl-rail__item--stack { flex-direction: column; gap: var(--gap-1); }
.wl-rail__item--row { flex-direction: row; align-items: center; }
.wl-rail__item + .wl-rail__item {
  border-top: 1px solid var(--hairline);
}
.wl-rail__item:hover {
  background: var(--paper-3);
}
.wl-rail__item:focus-visible {
  outline: 2px solid var(--ink);
  outline-offset: -2px;
}
.wl-rail__item.is-active {
  background: var(--paper-3);
  border-left-color: var(--rail-item-accent, var(--ink));
}
```

### New token

`frontend/src/tokens/density.css` — add near the other control sizing:
```css
--rail-accent-width: 3px;
```
Unifies Hedging's `inset 3px` and Instruments' `border-left: 4px` into one value. `35px`/compact
unaffected (border widths don't scale with density).

## Per-page migration

All five replace their bespoke container element with `RailList` and their bespoke row
element with `RailItem`, deleting the now-dead CSS. Page-specific *content* (multi-line cards,
chips, meta, headers) is preserved as children.

### Hedging
- `rail` array of `.hedging-underlying-card` `<button>` → `RailItem` (default `layout="stack"`;
  multi-line children `__top`/`__name`/`__tags` markup kept). No `accent` → plain `--ink` bar
  on active.
- The bare cards now live inside a `RailList`. Delete `.hedging-underlying-card`,
  `:hover`, `.active` rules; keep `__top`/`__symbol`/`__asset`/`__name`/`__tags`/
  `hedging-underlying-tag*` content rules.
- Active is now `--paper-3` + `--ink` bar (was pos-tint + pos border + bold). The `font-weight:700`
  on active is dropped (consistency).

### Skills
- `<aside className="wl-skills__tree">` → `<RailList scroll className="wl-skills__tree">` (keep
  the class only if it still carries non-chrome rules like padding/gap for the search+groups;
  otherwise drop). Search `<label>` + `__group-head` + grouped `__domain` stay as children.
- `.wl-skills__entry` `<button>` → `RailItem layout="row"` (name span + lint Badge on one line;
  the entry was `justify-content: space-between`, so add `margin-left: auto` to the trailing
  Badge or keep a small `.wl-skills__entry-name { flex: 1; min-width: 0 }` rule for the label).
  Delete `.wl-skills__entry`, `:hover`, `:focus-visible`, `--active`, and the
  `+`/`:first-child` separator rules (the primitive provides hover/active/adjacent separators).
- **Separator at group boundaries (review finding #5):** the `item + item` adjacency resets at
  each group/domain wrapper, so the *first* row of every group has no top separator from its
  `__group-head`. The Workflows tier nests rows in `__domain` wrappers; References/Meta render
  rows directly in `__group-body` with no wrapper. Cover BOTH: add a bottom hairline to
  `.wl-skills__group-head` (separates every group's first row from its head) AND a top hairline
  to `.wl-skills__domain` (separates stacked domains within Workflows). Verify live that no
  first-row is left un-separated and no double border appears.
- Keep `__search`, `__group-head`, `__group-body`, `__domain`, `__domain-name`, `__tree`
  layout rules (minus whatever the primitive now owns).

### Tracing
- `<div className="wl-tracing__list" aria-label="Traces">` → `<RailList scroll>`. The dead
  `aria-label` on a div drops (SplitLayout's `<nav aria-label="Traces">` already labels it).
- `.wl-tracing__trace-card` `<button>` → `RailItem layout="row"` (status icon + name + meta on
  one line; `__meta` keeps its `margin-left: auto`). Note `__trace-card` had `flex-wrap: wrap` —
  preserve it with a small kept rule if multi-chip cards still need to wrap.
- The grouped selector `.wl-tracing__trace-card, .wl-tracing__span { … }` must be split:
  `__span` (the detail span-tree rows, NOT in the rail) keeps its own rule; the `__trace-card`
  half moves to `RailItem`. Delete only the `__trace-card` half of the hover/active grouped
  rules. Keep the row-content rules `__status`, `__trace-name`, `__meta`. (`__head`/`__eyebrow`
  are the rail filter-header chrome, not card content — they stay regardless.)
- Remove `__trace-card` from `.wl-tracing__list, .wl-tracing__tree, .wl-tracing__detail`
  border/bg group — `__tree`/`__detail` keep their paper surface; `__list` is now `wl-rail`.

### EngineConfigs
- `<aside className="wl-engine-configs__list">` → `<RailList>`. Delete `.wl-engine-configs__list`
  (flex/overflow) — primitive owns it.
- `.wl-engine-configs__row` `<button>` → `RailItem layout="row"` (name + "Default" pill side by
  side). Delete `__row`, `:last-child`, `:hover`, `.is-selected`, the `__row` half of
  `:focus-visible` (the input-focus selectors in that grouped rule stay). Keep `__row-name`
  (ellipsis; add `flex: 1` so the pill is pushed right) + `__pill`. Active maps from the old
  `.is-selected` class → the `active` prop.

### PricingParameters (ProfileLibrary.tsx)
- `<div className="wl-pricing-params__list">` → `<RailList>`. Delete the `__list` tray rule
  (border/bg/padding/gap) — primitive owns border+bg; row padding moves to RailItem.
- `.wl-pricing-params__profile` `<button>` → `RailItem active={selected?.id === profile.id}
  accent={sourceAccent(source_type)}` (default `layout="stack"`; strong name + meta span on
  two lines) where `sourceAccent` maps `default_underlying→'--info'`, `xlsx→'--warn'`,
  `market_data_spot→'--pos'`. Delete all `__profile` rules incl. the `is-*` source-stripe and
  `is-active` blocks (the `accent` prop + primitive replace them). **Keep** a minimal
  `.wl-pricing-params__profile-name { font-family: var(--font-ui); font-size: var(--type-body-size) }`
  and `__profile-meta { color: var(--ink-2); font-family: var(--font-numeric); font-size: var(--type-small-size) }`
  for the two typefaces (rename from the old `strong`/`span` descendant selectors so they don't
  depend on the deleted `__profile` parent).
- **`aria-current` (review finding #2):** the old JSX set `aria-current` explicitly
  (`ProfileLibrary.tsx:146`) and `PricingParameters.test.tsx:76` asserts a non-active row has
  none. `RailItem` now sets `aria-current="true"` automatically on `active`, so drop the manual
  attribute — the active row keeps it, inactive rows have none, and the test stays green. Verify
  the test's target button is an inactive profile.
- Keep `__source-filter` + `__source-pill` (the header) and `__source-tag` content rules.

### Instruments (surface-only)
- `.wl-instruments__tabs` container: change `background: var(--paper)` → `var(--paper-2)` to
  match `.wl-rail` (border already `1px solid var(--hairline)`). No component swap — it is a
  tablist, not a RailList.
- **Both** border-left declarations change to the token, not just the active one (review
  finding #3): `.wl-instruments__tab { border-left: 4px solid transparent }` (`Instruments.css:40`)
  AND `.wl-instruments__tab.is-active { border-left-color … }` keep the bar at
  `var(--rail-accent-width)` so activation never shifts text by 1px.
- **Active-tab surface (review finding #4):** the active tab's `background: var(--paper-2)`
  (`Instruments.css:62`) now equals the strip background. Change it to `var(--paper-3)` so the
  active tab matches the canonical `.wl-rail__item.is-active` surface and stays distinct.
- No JSX change; `railAs="div"` stays (its content is a `role="tablist"`, so SplitLayout must
  not wrap it in a nav landmark).

## Edge cases & risks

- **Row axis (review finding #1):** `RailItem` MUST NOT hardcode a single `flex-direction`.
  Three rails are horizontal (Skills, Tracing, EngineConfigs → `layout="row"`), two are vertical
  (Hedging, PricingParams → default `layout="stack"`). The `--row`/`--stack` modifiers carry
  this; missing it collapses pills/meta into a vertical stack. Each `row` page also keeps a tiny
  distribution rule (label `flex:1` or trailing `margin-left:auto`) so name+meta lay out as today.
- **Skills grouping vs `item + item` separators:** the `+` adjacency resets across the
  `__domain` wrapper AND at every group boundary, so the first row of each group/domain has no
  top separator. Add a bottom hairline to `.wl-skills__group-head` (covers References/Meta whose
  rows are direct `__group-body` children) AND a top hairline to `.wl-skills__domain` (covers the
  stacked Workflows domains). Verify live: no un-separated first row, no doubled border.
- **Tracing shared selector:** `__trace-card` and `__span` share rules today; `__span` is the
  detail-pane tree (not the rail) and must NOT become a RailItem. Split the selectors.
- **PricingParams two-typeface rows:** the strong(ui/body)+span(numeric/small) pattern must be
  preserved inside the RailItem; keep a minimal `.wl-pricing-params__profile strong/span` rule
  (or equivalent child classes) rather than relying on RailItem's single default font.
- **Accent always-on vs active-only:** `border-left-color` resolves to `--rail-item-accent`
  when set (PricingParams: always show source color), else `transparent` until `.is-active`
  (then `--ink`). Verify a PricingParams active row shows its source color, not ink.
- **Landmark nesting:** dropping inner `<aside aria-label>` wrappers relies on SplitLayout's
  `<nav aria-label>`. Confirm each migrated rail still has exactly one labelled landmark.
- **`--rail-item-accent` phantom-sweep:** it is an inline-set React custom prop with a CSS
  fallback (`var(--rail-item-accent, …)`), same sanctioned pattern as `--metric-cols`. The
  sweep will flag it; that is expected, not a regression.

## Testing

- **Unit (vitest):** `RailList.test.tsx` (renders children; `scroll` toggles the `--scroll`
  class; passes `className`). `RailItem.test.tsx` (renders children; `active`→`is-active` class
  AND `aria-current="true"`, inactive → no `aria-current`; `layout` toggles
  `--row`/`--stack`; `accent` sets the `--rail-item-accent` inline custom prop string; `onClick`
  fires). Do NOT assert resolved geometry in jsdom (it can't resolve `var()`); assert
  class/style/attribute strings, verify geometry live.
- **Existing page tests:** the review confirmed NO test queries the old row/list class names
  (tests use `getByRole`/`getByText`/`getByLabelText`), so there is nothing to retarget by
  class. The one real dependency is `PricingParameters.test.tsx:76`
  (`expect(profileButton).not.toHaveAttribute('aria-current')`): confirm its target is an
  inactive profile so RailItem's auto `aria-current` keeps it green. `getByLabelText('Pricing
  parameter profiles')` targets the SplitLayout nav label, which is preserved. Run all page
  suites to confirm.
- **Live (chrome-devtools, both themes + compact):** navigate each of the five rails fresh
  (avoid the SPA pushState loop); assert identical container border/surface + row
  padding/hover/active + 3px accent bar; Hedging multi-line content intact; PricingParams
  source stripes intact and active row shows source color; Instruments surface = paper-2 +
  3px active bar with no text shift.
- `tsc --noEmit` clean; full vitest green; phantom-token sweep adds only the two sanctioned
  names.

## Out of scope (YAGNI)

- No re-classification of pages into templates (deferred Round 2).
- No change to ScenarioTest/Backtest config rails beyond a live-verify.
- No collapse/resize behavior changes; SplitLayout untouched.
- No Instruments rail component swap (tablist stays a tablist).

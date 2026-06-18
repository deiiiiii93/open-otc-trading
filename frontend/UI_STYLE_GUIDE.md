# Frontend UI Style Guide

Conventions every agent must follow when adding or changing UI in `frontend/`.
These are not aesthetic preferences — they are what makes light/dark, comfortable/compact,
and reduced-motion all work *for free*. Breaking them produces the exact class of bugs we
keep having to fix in `style(...)` polish commits (dark-mode contrast, horizontal scrollbars,
inconsistent spacing).

> **The one rule that prevents most bugs:** style with **tokens**, never literals.
> If you are typing a hex color, a `px` spacing value, a font name, or an animation
> duration directly into a `.css` file, stop — there is almost certainly a token for it.

Scope note: this guide is strict about **design values** (colors, type, spacing, density,
motion). Raw `px` is still acceptable for non-design geometry: `1px` borders/outlines, media
query breakpoints, icon sizes, minimum widths, grid column bases, max heights, and the
role-based radii called out in §6. Prefer tokens whenever the value affects visual rhythm.

---

## 1. The token system (`src/tokens/`)

All design values are CSS custom properties defined in `src/tokens/`, imported **once** in
`src/main.tsx` (`import './tokens/index.css'`). You never import them per-component — they are
global on `:root`. Components only *consume* them with `var(--token)`.

Existing code may still contain older hardcoded colors, fallback colors, or `--wl-*` variables.
Do not copy those patterns. This guide supersedes legacy styles; when you touch a legacy
selector, bring that selector into compliance instead of spreading the old convention.

| File | Defines | Examples |
|------|---------|----------|
| `colors.css` | Surfaces, text, borders, semantic colors | `--paper`, `--ink`, `--hairline`, `--pos`/`--neg`/`--warn`/`--info` |
| `type.css` | Font families + the type scale | `--font-ui`, `--font-numeric`, `--type-h1-size`, `--type-caps-size`, `--type-num-l-size` |
| `density.css` | Spacing, paddings, gaps, row height | `--gap-1`…`--gap-6`, `--panel-padding`, `--input-padding-x`, `--row-height` |
| `motion.css` | Durations, easing, keyframes | `--motion-fade`, `--motion-slide`, `--motion-shimmer`, `--motion-curve`, `@keyframes wl-fade-in` |
| `reset.css` | Box-sizing + element resets | `body` defaults, `button`/`input` reset |

### Color tokens — the full set

```
Surfaces (background, light → heavy):  --paper  --paper-2  --paper-3
Borders / dividers:                    --hairline  --hairline-2
Text (primary → secondary):            --ink  --ink-2
Semantic:                              --pos  --neg  --warn  --info
```

There are **no other color tokens.** Do not invent `--wl-bg`, `--wl-border`, `--surface`, etc.
A made-up token name silently resolves to nothing and CSS falls back to the *previous* or
*inherited* value — which is how a "dark-only hardcoded color" hid behind a phantom `--wl-*`
token until someone opened the page in light mode. (This was a real fix in `a45756a`.)

The especially nasty form is `var(--made-up, #hardcoded)`: the undefined token falls through
to the **hex fallback**, so the element renders a fixed, theme-blind color that looks fine in
one mode and wrong in the other — the failure hides behind the very fallback meant to be a
safety net. A whole legacy family (`--wl-*`, `--color-*`, `--accent*`, `--ink-0/1`, `--line`,
`--muted`, `--panel`, `--ok`/`--err`, `--type-title-*`, `--radius-sm`, `--paper-4`) was retired
this way — all remapped onto the real tokens above. Don't reintroduce them.

**Detect phantom tokens before committing** — every `var(--X)` must have a matching `--X:`
definition somewhere:

```bash
# from frontend/ — prints any token used but never defined (should be empty)
csv=$(find src -name '*.css')
comm -23 \
  <(echo "$csv" | xargs grep -hoE "var\(--[A-Za-z0-9_-]+" | sed -E 's/var\(//' | sort -u) \
  <(echo "$csv" | xargs grep -hoE -- "--[A-Za-z0-9_-]+[[:space:]]*:" | sed -E 's/[[:space:]]*:$//' | sort -u)
```

---

## 2. Theme & density: consume tokens, never branch

Two independent axes are switched by a single attribute on `<html>`:

- **Theme** — `data-theme="light" | "dark"`, absent = follow system. Managed by `useTheme()`.
- **Density** — `data-density="compact"`, absent = comfortable. Managed by `useDensity()`.
- Only the app shell/hooks and token files should write or target these attributes. Component
  stylesheets should consume the remapped tokens instead.

The attribute **remaps the token values**; your component does not need to know which mode is
active. **Never** read `data-theme`/`data-density` in TSX to pick a color or size, and never
write a `[data-theme="dark"] .my-class { ... }` override in a component stylesheet. If you
caught yourself doing that, the component is hardcoding a value it should be reading from a token.

✅ `color: var(--ink);` → readable in every theme.
❌ `color: #14110A;` → invisible in dark mode.

---

## 3. Raw form controls MUST be themed (or use the primitives)

Native `<input>`, `<select>`, and `<textarea>` default to the **user-agent white background**.
In dark mode that white box renders our cream `--ink` placeholder text on white → invisible.

**Preferred:** use the existing field primitives — `.wl-field` / `.wl-field__label` /
`.wl-input` (see `components/Input.css`).

**If you must style raw controls** (e.g. a dialog with many inline inputs), theme them
explicitly with tokens — background, color, border, font, padding, placeholder, focus, and
disabled — exactly like `ScenarioBuilderDialog.css` does:

```css
.my-form input,
.my-form select {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  padding: var(--input-padding-y) var(--input-padding-x);
  border-radius: 0;
}
.my-form input::placeholder { color: var(--ink-2); }
.my-form input:focus { outline: 2px solid var(--ink); outline-offset: 1px; }
.my-form input:disabled { background: var(--paper-2); color: var(--ink-2); cursor: not-allowed; }
```

Do the same for `button` elements that are not the `Button` primitive: tokenized background,
border, text color, padding, font, disabled state, hover state, and visible `:focus-visible`.

---

## 4. Typography

Two font families, used for two different purposes:

- `--font-ui` (Inter Tight) — all prose, labels, buttons, titles.
- `--font-numeric` (Berkeley Mono → JetBrains Mono fallback) — **any number a user reads as
  data**: prices, P&L, Greeks, quantities, deltas, counts in tiles.

Use the scale tokens, not raw `px`:

| Use | Size token | Weight token |
|-----|-----------|--------------|
| Page title | `--type-h1-size` | `--type-h1-weight` |
| Section heading | `--type-h2-size` / `--type-h3-size` | matching weight |
| Body | `--type-body-size` | `--type-body-weight` |
| Hint / secondary | `--type-small-size` | — |
| **Micro-caps label** | `--type-caps-size` | `--type-caps-weight` |
| Big numeric value | `--type-num-l-size` | `--type-num-l-weight` |
| Inline numeric | `--type-num-m-size` / `--type-num-s-size` | matching weight |

**Labels are uppercase micro-caps.** The signature label style (tile labels, field labels,
modal titles, page titles) is:

```css
font-size: var(--type-caps-size);
font-weight: var(--type-caps-weight);
text-transform: uppercase;
letter-spacing: 0.05em;   /* 0.05–0.06em */
color: var(--ink-2);      /* labels are secondary ink */
```

**Numbers carry meaning through color.** A value that can be good/bad uses semantic color via a
modifier class, never inline logic in the value itself:

```css
.wl-tile__value      { font-family: var(--font-numeric); color: var(--ink); }
.wl-tile--pos .wl-tile__value { color: var(--pos); }
.wl-tile--neg .wl-tile__value { color: var(--neg); }
```

For tabular numeric columns, add `font-variant-numeric: tabular-nums;` and right-align when
comparison matters. The `Table` primitive already does this for `numeric` columns.

---

## 5. Spacing & layout

- **Gaps & padding come from tokens.** Use `--gap-1`…`--gap-6` and the named paddings
  (`--panel-padding`, `--tile-padding`, `--input-padding-*`, `--button-padding-*`). These
  shrink automatically in compact density. A literal `gap: 16px` does not.
- Raw layout dimensions are allowed when they describe geometry rather than spacing rhythm:
  breakpoints (`@media (max-width: 900px)`), minimum column widths, icon dimensions, scrollable
  max-heights, and `1px` rules. Do not use that exception for padding, margin, gap, type size,
  or animation duration.
- **Lay out with flexbox + `gap`**, not margins between siblings.
- **Wrap, don't overflow.** Rows of controls use `flex-wrap: wrap` so they never trigger a
  horizontal scrollbar in a narrow dialog. Give inputs `flex: 1 1 <basis>; min-width: 0;` and
  fixed controls `flex: 0 0 auto;`. (Fixed in `a45756a` — leg controls were overflowing.)
- **Keep row actions inline on the right.** Edit/Delete and similar per-row actions sit on the
  right of the row (`justify-content: space-between`), not wrapped onto a second line. Predefined
  and custom rows should look identical.

---

## 6. Borders, shape & focus

- **Shape is role-based, not uniform.** Match the element's role:
  - **Structural chrome** — buttons, inputs, tiles, modals, tables, panels → **square**
    (`border-radius: 0`). This is the dominant look; default to it.
  - **Pills / chips / tags / nav items** → **fully rounded** (`border-radius: 999px`), e.g.
    `KindChip`, sidebar nav, term chips.
  - **Avatars / status dots** → `border-radius: 50%`.
  - **Conversational cards** (chat bubbles) → small radius (`8px`–`12px`).
  - ⚠️ There is **no radius token** — use a literal radius from the roles above. If you
    genuinely need a radius *scale*, add real tokens to `tokens/` first, then reference them.
    (Cautionary precedent: the two RFQ controls once referenced an undefined `var(--radius-sm)`
    *and* `var(--paper-4)`, which silently resolved to `0` / no-border and rendered borderless
    square boxes until aligned to the `.wl-input` look — see §1 on phantom tokens.)
- **Borders** are `1px solid` of `--hairline` (subtle), `--hairline-2` (input/stronger), or
  `--ink` (emphatic frame, e.g. tiles, modals, primary buttons).
- **Focus is always visible.** Buttons: `:focus-visible { outline: 2px solid var(--ink);
  outline-offset: 2px; }`. Inputs thicken the border to `2px` and subtract `1px` from padding so
  the box doesn't jump (see `Input.css`). Never remove an outline without replacing it.
- **Translucent layers** are built from tokens with `color-mix`, e.g. the modal overlay:
  `background: color-mix(in oklab, var(--ink) 42%, transparent);` — not `rgba(0,0,0,.42)`.
- **Avoid fallback colors on tokens.** `var(--ink, #111)` hides missing-token mistakes. In
  component styles, prefer `var(--ink)`. If a fallback is truly required, keep it local and
  explain why.

---

## 7. Motion

- Use the duration/easing tokens: `--motion-fade` (120ms, fades), `--motion-slide` (180ms,
  enters/transforms), `--motion-shimmer` (loading shimmer loop), `--motion-curve` (the standard
  easing).
- Use the shared keyframes (`wl-fade-in`, `wl-slide-in-right`, `wl-pulse`, `wl-shimmer`) rather
  than redefining them.
- `prefers-reduced-motion` is handled globally in `motion.css` (durations drop to ~0). Don't
  fight it with `!important` durations.

---

## 8. Naming & file structure

- **One stylesheet per component, co-located and imported at the top of the `.tsx`:**
  `Tile.tsx` ↔ `Tile.css` (`import './Tile.css'`). No global "utilities" dumping ground.
- **BEM class names:** `block__element--modifier`
  (`wl-modal`, `wl-modal__title`, `wl-button--primary`).
- **State classes:** use `is-*` for transient UI state (`is-active`, `is-focused`,
  `is-selected`). Keep semantic/variant differences as BEM modifiers (`wl-badge--warn`,
  `wl-button--primary`).
- **Prefix:** shared/primitive components use `wl-` (`wl-button`, `wl-input`, `wl-tile`,
  `wl-modal`). Feature/route-level components may use a feature block name instead
  (`hedge-strategy__band`) but still follow BEM. When in doubt, prefix `wl-`.
- Reuse the primitives — `Button`, `Input`/`wl-field`, `Modal`, `Tile`, `Badge`, `Chip`,
  `PageHeader`, `Table` — before writing new bespoke styling.

---

## 9. Pre-commit checklist for any UI change

- [ ] No hardcoded hex/rgb colors, raw `px` for spacing/type, or font-family literals — all via tokens.
- [ ] No `var(--token, #fallback)` color fallbacks hiding missing or legacy token names.
- [ ] Every token I referenced actually exists in `src/tokens/` (no invented `--wl-*` names).
- [ ] Opened the view in **both light and dark** theme — text, borders, placeholders, disabled
      states all legible.
- [ ] Checked **compact density** — nothing clips or collides.
- [ ] Raw form controls are themed (background/color/border/focus/disabled/placeholder).
- [ ] Numbers use `--font-numeric`; good/bad values use `--pos`/`--neg`.
- [ ] Rows wrap instead of overflowing; no surprise horizontal scrollbar.
- [ ] Interactive elements have a visible `:focus-visible` state.
- [ ] New component has its own co-located `.css`, imported in the `.tsx`, BEM-named.

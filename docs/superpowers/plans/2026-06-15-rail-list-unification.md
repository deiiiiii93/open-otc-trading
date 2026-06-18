# Rail-List Unification Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. TDD where it adds value (the two new primitives); CSS-class swaps verified live.

**Goal:** Unify five hand-rolled left-rail selection lists (Hedging, Skills, Tracing, EngineConfigs, PricingParameters) onto two shared primitives — `RailList` + `RailItem` — and align the Instruments tab-strip surface to the same baseline.

**Architecture:** `RailList` (`.wl-rail`) = bordered `--paper-2` container; `RailItem` (`.wl-rail__item`) = a row with `active`/`accent`/`layout` props, auto-`aria-current`, hairline separators, 3px left accent bar. One new token `--rail-accent-width`.

**Tech Stack:** React 18 + TypeScript + Vite, Vitest + @testing-library/react, token-only CSS.

**Spec:** `docs/superpowers/specs/2026-06-15-rail-list-unification-design.md`

---

### Task 1: Token

**Files:** Modify `frontend/src/tokens/density.css`

- [ ] Add `--rail-accent-width: 3px;` near the control-height tokens. Run phantom sweep later confirms it's defined.

### Task 2: RailList primitive (TDD)

**Files:** Create `frontend/src/components/RailList.tsx`, `RailList.css`, `RailList.test.tsx`

- [ ] Write `RailList.test.tsx`: renders children; `scroll` adds `wl-rail--scroll`; default has no scroll class; `className` appended.
- [ ] Run → fail.
- [ ] Implement `RailList.tsx` + `RailList.css` per spec.
- [ ] Run → pass.

### Task 3: RailItem primitive (TDD)

**Files:** Create `frontend/src/components/RailItem.tsx`, `RailItem.css`, `RailItem.test.tsx`

- [ ] Write `RailItem.test.tsx`: renders children; `active` → `is-active` class AND `aria-current="true"`; inactive → no `aria-current`; `layout="row"` → `wl-rail__item--row`, default `--stack`; `accent="--info"` → inline style `--rail-item-accent: var(--info)`; `onClick` fires.
- [ ] Run → fail.
- [ ] Implement `RailItem.tsx` + `RailItem.css` per spec (base + `--stack`/`--row` + hover/focus/active/separator).
- [ ] Run → pass.

### Task 4: Hedging migration (layout=stack)

**Files:** Modify `frontend/src/routes/Hedging.tsx`, `Hedging.css`

- [ ] Import `RailList`/`RailItem`. Wrap the `rail` `.map` in `<RailList>`; each `.hedging-underlying-card` `<button>` → `<RailItem active={u.underlying_id === selectedUnderlyingId} onClick={…}>` keeping `__top`/`__name`/`__tags` children.
- [ ] Delete `.hedging-underlying-card`, `:hover`, `.active` CSS; keep content rules.
- [ ] `tsc` + Hedging test pass.

### Task 5: Skills migration (layout=row + separators)

**Files:** Modify `frontend/src/routes/Skills.tsx`, `Skills.css`

- [ ] `<aside className="wl-skills__tree">` → `<RailList scroll className="wl-skills__tree">`. `renderEntry`'s `.wl-skills__entry` `<button>` → `<RailItem layout="row" active={…}>`; give the trailing Badge `margin-left:auto` (or label `flex:1`).
- [ ] Delete `.wl-skills__entry`/`:hover`/`:focus-visible`/`--active`/`+`/`:first-child` rules. Add bottom hairline to `.wl-skills__group-head` + top hairline to `.wl-skills__domain`. Trim `__tree` to non-chrome rules.
- [ ] `tsc` + Skills test pass.

### Task 6: Tracing migration (layout=row, split shared selector)

**Files:** Modify `frontend/src/routes/Tracing.tsx`, `Tracing.css`

- [ ] `<div className="wl-tracing__list" aria-label="Traces">` → `<RailList scroll>` (drop dead aria-label). `.wl-tracing__trace-card` `<button>` → `<RailItem layout="row" active={…}>`; `__meta` keeps `margin-left:auto`.
- [ ] Split `.wl-tracing__trace-card, .wl-tracing__span` group rules: keep `__span` half, delete `__trace-card` half. Remove `__list` from the `__list,__tree,__detail` border/bg group (now `wl-rail`). Preserve `flex-wrap` on the card if needed. Keep `__head`/`__eyebrow`/`__status`/`__trace-name`/`__meta`.
- [ ] `tsc` + Tracing test pass.

### Task 7: EngineConfigs migration (layout=row)

**Files:** Modify `frontend/src/routes/EngineConfigs.tsx`, `EngineConfigs.css`

- [ ] `<aside className="wl-engine-configs__list">` → `<RailList>`. `.wl-engine-configs__row` `<button>` → `<RailItem layout="row" active={config.id === selectedId}>` keeping `__row-name` + `__pill`.
- [ ] Delete `__list`, `__row`/`:last-child`/`:hover`/`.is-selected` and the `__row` part of the `:focus-visible` group (keep the input-focus selectors). Add `flex:1` to `__row-name`.
- [ ] `tsc` + EngineConfigs test pass.

### Task 8: PricingParameters migration (layout=stack + accent)

**Files:** Modify `frontend/src/routes/ProfileLibrary.tsx`, `frontend/src/routes/PricingParameters.css`

- [ ] Add `sourceAccent(source_type)` map (`default_underlying→'--info'`, `xlsx→'--warn'`, `market_data_spot→'--pos'`). `<div className="wl-pricing-params__list">` → `<RailList>`. `.wl-pricing-params__profile` `<button>` → `<RailItem active={selected?.id===profile.id} accent={sourceAccent(profile.source_type)}>`; drop the manual `aria-current`. Rename inner `strong`/`span` to `__profile-name`/`__profile-meta`.
- [ ] Delete `__list` + all `__profile` rules (base, `is-*` stripes, `is-active`). Add the two kept typography rules. Keep `__source-filter`/`__source-pill`/`__source-tag`.
- [ ] `tsc` + PricingParameters test pass (esp. `aria-current` assertion line 76).

### Task 9: Instruments surface alignment

**Files:** Modify `frontend/src/routes/Instruments.css`

- [ ] `.wl-instruments__tabs` `background: var(--paper)` → `var(--paper-2)`. `.wl-instruments__tab` `border-left: 4px` → `var(--rail-accent-width)`. `.wl-instruments__tab.is-active` `background: var(--paper-2)` → `var(--paper-3)` (bar already token after the line-40 change).
- [ ] `tsc` + Instruments test pass.

### Task 10: Verify + commit

- [ ] `npx tsc --noEmit` clean.
- [ ] Full `vitest run` green.
- [ ] Phantom-token sweep: only `--rail-item-accent` (sanctioned inline prop) new; `--rail-accent-width` defined.
- [ ] Live (chrome-devtools, both themes + compact): all five rails share container/row/active treatment + 3px bar; Hedging multi-line + PricingParams source stripes intact; Instruments paper-2 surface + paper-3 active, no text shift.
- [ ] Commit.

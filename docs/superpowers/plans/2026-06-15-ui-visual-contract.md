# UI Visual Contract (Round 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shared layout layer actually enforce one visual contract — fix the self-referential `--rail-width` cycle that collapses 6 master-detail pages, declare inter-region spacing once on the scaffold body, and route every empty/loading state through the existing `Empty` primitive.

**Architecture:** Three corrective changes to existing shared components (`SplitLayout`, `PageScaffold`, `Empty`) plus mechanical page migrations. No new components, no new templates, no re-classification. Token-only CSS throughout (`frontend/UI_STYLE_GUIDE.md`).

**Tech Stack:** React 18 + TypeScript + Vite; Vitest + @testing-library/react; plain CSS custom-property tokens (BEM `wl-` naming).

**Spec:** `docs/superpowers/specs/2026-06-15-ui-visual-contract-design.md`

**Working dir:** `/Users/fuxinyao/open-otc-trading/.claude/worktrees/ui-unification/frontend` (branch `worktree-ui-unification`). Run all `npm`/`npx` commands from there.

---

## File Structure

**Modified primitives/templates:**
- `src/components/SplitLayout.tsx` / `.css` / `.test.tsx` — rename override custom prop to `--wl-rail-width` (kills the cycle).
- `src/components/templates/PageScaffold.css` — add `gap: var(--gap-3)` to `.wl-scaffold__body`.
- `src/components/MetricRow.css`, `src/components/TableToolbar.css`, `src/components/templates/AnalyticsDashboard.css`, `src/components/Stepper.css`, `src/components/templates/WizardPage.css` — remove the redundant stacking margin (now provided by the body gap).
- `src/components/Empty.tsx` / `.css` / new `Empty.test.tsx` — add optional `variant` + `hint`, fully back-compatible.

**Pages dropping the redundant `railWidth` (Part 1):** `Skills.tsx:255`, `Tracing.tsx:185`, `Hedging.tsx:82`, `RfqApproval.tsx:173`, `PricingParameters.tsx:203`, `EngineConfigs.tsx:308`.

**Pages migrating a hand-rolled empty/loading state to `Empty` (Part 3):** `ClientRfq.tsx`, `EngineConfigs.tsx`, `Backtest.tsx`, `InstrumentsAssumptions.tsx`, `ScenarioTest.tsx`, `TrySolve.tsx`, `Tracing.tsx`, `Hedging.tsx`, `InstrumentsAllowedHedges.tsx`.

**Explicitly NOT touched:** `Skeleton` and its callers; `wl-reports__loading` (wraps Skeletons); `wl-portfolios__empty-inline` (inline "None." tag marker); `wl-client-rfq__hint` (form instruction, not an empty state); `td.wl-assumptions__empty` table-cell "no rows" messages (in-table cells, not page states); `wl-build-inputs__empty` / `wl-underlying-defaults__empty` (no JSX usage found — dead/dynamic, out of scope).

---

## PART 1 — Fix the rail-collapse regression

### Task 1: Rename the SplitLayout rail-width override property

**Files:**
- Modify: `src/components/SplitLayout.tsx:21`
- Modify: `src/components/SplitLayout.css:3`
- Test: `src/components/SplitLayout.test.tsx:33-39` (rewrite)

- [ ] **Step 1: Rewrite the failing width test**

Replace the existing `it('applies a railWidth override …')` block (lines 33-39) in `src/components/SplitLayout.test.tsx` with:

```tsx
  it('applies a railWidth override via the --wl-rail-width custom prop (no self-reference)', () => {
    const { container } = render(
      <SplitLayout rail={<div />} railWidth="var(--rail-width)">ws</SplitLayout>,
    );
    const root = container.querySelector('.wl-split') as HTMLElement;
    const style = root.getAttribute('style') || '';
    // Override writes a DISTINCT property so it can never reference itself.
    expect(style).toContain('--wl-rail-width: var(--rail-width)');
    // It must NOT define --rail-width itself (the old self-referential cycle).
    expect(style).not.toMatch(/(^|;)\s*--rail-width:/);
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/components/SplitLayout.test.tsx`
Expected: FAIL — current code writes `--rail-width`, so `--wl-rail-width` is absent and `--rail-width:` is present.

- [ ] **Step 3: Change the override property in the component**

In `src/components/SplitLayout.tsx`, line 21, change:

```tsx
  const style = railWidth ? ({ '--rail-width': railWidth } as React.CSSProperties) : undefined;
```
to:
```tsx
  const style = railWidth ? ({ '--wl-rail-width': railWidth } as React.CSSProperties) : undefined;
```

- [ ] **Step 4: Update the CSS to read the new prop with a token fallback**

In `src/components/SplitLayout.css`, line 3, change:

```css
  grid-template-columns: var(--rail-width) minmax(0, 1fr);
```
to:
```css
  grid-template-columns: var(--wl-rail-width, var(--rail-width)) minmax(0, 1fr);
```

(Leave `.wl-split--collapsed` and the `@media (max-width: 900px)` rule unchanged — they already use `1fr`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/components/SplitLayout.test.tsx`
Expected: PASS (all 4 tests in the file).

- [ ] **Step 6: Commit**

```bash
git add src/components/SplitLayout.tsx src/components/SplitLayout.css src/components/SplitLayout.test.tsx
git commit -m "fix(ui): rename SplitLayout rail-width override to --wl-rail-width (kill self-ref cycle)"
```

### Task 2: Drop the redundant railWidth pass-through from the 6 pages

These pages all pass `railWidth="var(--rail-width)"`, which equals the default. With Task 1 done they render correctly without it; remove the noise.

**Files:**
- Modify: `src/routes/Skills.tsx:255`, `src/routes/Tracing.tsx:185`, `src/routes/Hedging.tsx:82`, `src/routes/RfqApproval.tsx:173`, `src/routes/PricingParameters.tsx:203`, `src/routes/EngineConfigs.tsx:308`

- [ ] **Step 1: Remove the `railWidth="var(--rail-width)"` attribute from each of the 6 files**

In each file, delete the line/attribute `railWidth="var(--rail-width)"`. For `Tracing.tsx:185` it is inline in the `<MasterDetailPage … railWidth="var(--rail-width)">` tag — remove just that attribute, keep the rest of the tag. For the other five it is on its own line within the props — delete that line.

Verify none remain (the only matches left should be the test and the template forwards):

```bash
grep -rn 'railWidth="var(--rail-width)"' src/routes
```
Expected: no output.

- [ ] **Step 2: Run the affected pages' tests**

Run: `npx vitest run src/routes/Skills.test.tsx src/routes/Tracing.test.tsx src/routes/Hedging.test.tsx`
Expected: PASS (these are the ones with test files; RfqApproval/PricingParameters/EngineConfigs have no standalone `.test.tsx` for this and are covered by the full run later).

- [ ] **Step 3: Commit**

```bash
git add src/routes/Skills.tsx src/routes/Tracing.tsx src/routes/Hedging.tsx src/routes/RfqApproval.tsx src/routes/PricingParameters.tsx src/routes/EngineConfigs.tsx
git commit -m "refactor(ui): drop redundant railWidth='var(--rail-width)' from 6 master-detail pages"
```

---

## PART 2 — Enforce vertical rhythm centrally

### Task 3: Add body gap and remove the five redundant body-child margins

**Files:**
- Modify: `src/components/templates/PageScaffold.css` (`.wl-scaffold__body`)
- Modify: `src/components/MetricRow.css:5`, `src/components/TableToolbar.css:6`, `src/components/templates/AnalyticsDashboard.css:1`, `src/components/Stepper.css:6`, `src/components/templates/WizardPage.css:6`

> **No unit test for this task.** The change is CSS-only (a `gap` token plus margin removals). jsdom does not apply imported `.css`, so a computed-`gap` assertion would be meaningless. The real verification is the live re-measure in Task 14 Step 2 (gaps all `12`, none `24`). This task's safety net is the existing component/page suites (Step 3) plus `tsc` (Step 4).

- [ ] **Step 1: Add the gap to the scaffold body**

In `src/components/templates/PageScaffold.css`, change:

```css
.wl-scaffold__body { display: flex; flex-direction: column; }
```
to:
```css
.wl-scaffold__body { display: flex; flex-direction: column; gap: var(--gap-3); }
```

- [ ] **Step 2: Remove the five now-redundant stacking margins**

1. `src/components/MetricRow.css` — delete the line `margin-bottom: var(--gap-3);` from `.wl-metric-row`.
2. `src/components/TableToolbar.css` — delete the line `margin-bottom: var(--gap-3);` from `.wl-table-toolbar`.
3. `src/components/templates/AnalyticsDashboard.css` — change `.wl-analytics__controls { margin-bottom: var(--gap-3); }` to `.wl-analytics__controls {}` — or, if that leaves an empty rule, delete the whole line.
4. `src/components/Stepper.css` — change `margin: 0 0 var(--gap-3);` to `margin: 0;`.
5. `src/components/templates/WizardPage.css` — delete the line `margin-top: var(--gap-3);` from `.wl-wizard__footer`.

- [ ] **Step 3: Run the full primitive/template test subset**

Run: `npx vitest run src/components/`
Expected: PASS (no test asserts those margins; layout is unaffected in jsdom).

- [ ] **Step 4: Type-check**

Run: `npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/components/templates/PageScaffold.css src/components/MetricRow.css src/components/TableToolbar.css src/components/templates/AnalyticsDashboard.css src/components/Stepper.css src/components/templates/WizardPage.css
git commit -m "fix(ui): single-source inter-region spacing via .wl-scaffold__body gap"
```

---

## PART 3 — One empty / loading / no-data state

### Task 4: Evolve the `Empty` primitive (additive `variant` + `hint`)

**Files:**
- Modify: `src/components/Empty.tsx`
- Modify: `src/components/Empty.css`
- Create: `src/components/Empty.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `src/components/Empty.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Empty } from './Empty';

describe('Empty', () => {
  it('renders message and default symbol (back-compat)', () => {
    const { container } = render(<Empty message="No data" />);
    expect(screen.getByText('No data')).toBeInTheDocument();
    expect(container.querySelector('.wl-empty__symbol')?.textContent).toBe('∅');
  });

  it('renders a hint line when provided', () => {
    const { container } = render(<Empty message="No data" hint="Try another filter" />);
    expect(container.querySelector('.wl-empty__hint')?.textContent).toBe('Try another filter');
  });

  it('omits the symbol and marks the loading variant', () => {
    const { container } = render(<Empty variant="loading" message="Loading…" />);
    expect(container.querySelector('.wl-empty__symbol')).toBeNull();
    expect(container.querySelector('.wl-empty--loading')).not.toBeNull();
  });

  it('marks the error variant', () => {
    const { container } = render(<Empty variant="error" message="Failed to load" />);
    expect(container.querySelector('.wl-empty--error')).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/components/Empty.test.tsx`
Expected: FAIL — `variant`/`hint` not supported; `.wl-empty--loading`/`--error`/`__hint` absent.

- [ ] **Step 3: Implement the evolved component**

Replace `src/components/Empty.tsx` with:

```tsx
import React from 'react';
import './Empty.css';

type Props = {
  message: string;
  symbol?: string;
  action?: React.ReactNode;
  variant?: 'empty' | 'loading' | 'error';
  hint?: React.ReactNode;
  className?: string;
};

export function Empty({ message, symbol = '∅', action, variant = 'empty', hint, className = '' }: Props) {
  const cls = ['wl-empty', variant !== 'empty' ? `wl-empty--${variant}` : '', className]
    .filter(Boolean).join(' ');
  return (
    <div className={cls}>
      {variant !== 'loading' && <div className="wl-empty__symbol">{symbol}</div>}
      <div className="wl-empty__message">{message}</div>
      {hint && <div className="wl-empty__hint">{hint}</div>}
      {action && <div className="wl-empty__action">{action}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Add the variant/hint CSS (token-only)**

Append to `src/components/Empty.css`:

```css
.wl-empty__hint {
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
.wl-empty--loading {
  border: none;
}
.wl-empty--error .wl-empty__message {
  color: var(--neg);
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/components/Empty.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/components/Empty.tsx src/components/Empty.css src/components/Empty.test.tsx
git commit -m "feat(ui): evolve Empty with optional loading/error variant + hint (back-compat)"
```

### Task 5: Migrate ClientRfq's "No RFQ selected" to Empty

**Files:**
- Modify: `src/routes/ClientRfq.tsx:446`, `src/routes/ClientRfq.css:129`

- [ ] **Step 1: Ensure the Empty import exists**

At the top of `src/routes/ClientRfq.tsx`, if `Empty` is not already imported, add:

```tsx
import { Empty } from '../components/Empty';
```

- [ ] **Step 2: Replace the bespoke empty markup**

`src/routes/ClientRfq.tsx:446`, change:

```tsx
                  <p className="wl-client-rfq__empty">No RFQ selected.</p>
```
to:
```tsx
                  <Empty message="No RFQ selected." />
```

(Leave `wl-client-rfq__hint` at line 426 untouched — it is a form instruction, not an empty state.)

- [ ] **Step 3: Delete the now-dead CSS rule**

In `src/routes/ClientRfq.css`, delete the `.wl-client-rfq__empty { … }` rule (line 129 block). Keep `.wl-client-rfq__hint`.

- [ ] **Step 4: Run the page tests**

Run: `npx vitest run src/routes/ClientRfq.test.tsx src/routes/ClientRfq.live.test.tsx`
Expected: PASS. If a test asserts the text `No RFQ selected.`, it still passes (text preserved). If one asserts the class `wl-client-rfq__empty`, update it to query the text or `.wl-empty` while preserving the behavior.

- [ ] **Step 5: Commit**

```bash
git add src/routes/ClientRfq.tsx src/routes/ClientRfq.css
git commit -m "refactor(ui): route ClientRfq empty state through Empty"
```

### Task 6: Migrate EngineConfigs' empty override list to Empty

**Files:**
- Modify: `src/routes/EngineConfigs.tsx:366`, `src/routes/EngineConfigs.css:112`

- [ ] **Step 1: Ensure the Empty import exists**

At the top of `src/routes/EngineConfigs.tsx`, if absent, add:

```tsx
import { Empty } from '../components/Empty';
```

- [ ] **Step 2: Replace the bespoke markup**

`src/routes/EngineConfigs.tsx:366`, change:

```tsx
              {productRules.length === 0 && <p className="wl-engine-configs__empty">No product-type overrides. Family defaults will handle every position.</p>}
```
to:
```tsx
              {productRules.length === 0 && <Empty message="No product-type overrides. Family defaults will handle every position." />}
```

- [ ] **Step 3: Delete the dead CSS rule**

In `src/routes/EngineConfigs.css`, delete the `.wl-engine-configs__empty { … }` rule (line 112 block).

- [ ] **Step 4: Type-check and confirm the removed class is unreferenced**

EngineConfigs has no dedicated test file, so verify via `tsc` and a grep:

```bash
npx tsc --noEmit && grep -rn "wl-engine-configs__empty" src || echo "class fully removed"
```
Expected: 0 type errors; grep prints "class fully removed" (no remaining references).

- [ ] **Step 5: Commit**

```bash
git add src/routes/EngineConfigs.tsx src/routes/EngineConfigs.css
git commit -m "refactor(ui): route EngineConfigs empty state through Empty"
```

### Task 7: Migrate Backtest's "Loading…" to Empty (loading variant)

**Files:**
- Modify: `src/routes/Backtest.tsx:534`, `src/routes/Backtest.css:92`

- [ ] **Step 1: Replace the bespoke loading markup**

`Backtest.tsx` already imports `Empty`. At line 534, change:

```tsx
        <p className="wl-backtest__loading">Loading…</p>
```
to:
```tsx
        <Empty variant="loading" message="Loading…" />
```

- [ ] **Step 2: Delete the dead CSS rule**

In `src/routes/Backtest.css`, delete the `.wl-backtest__loading { … }` rule (line 92 block).

- [ ] **Step 3: Run the page tests**

Run: `npx vitest run src/routes/Backtest.test.tsx`
Expected: PASS. If a test asserts `Loading…` text it still passes; if it asserts the class, update to text/`.wl-empty`.

- [ ] **Step 4: Commit**

```bash
git add src/routes/Backtest.tsx src/routes/Backtest.css
git commit -m "refactor(ui): route Backtest loading state through Empty"
```

### Task 8: Migrate InstrumentsAssumptions' two block-level empty states

Only the two **block** states (`<p>`) migrate. The two **table-cell** states (`<td colSpan={5}>`, lines 463/467) stay as cells — a dashed `Empty` box inside one spanned cell looks wrong.

**Files:**
- Modify: `src/routes/InstrumentsAssumptions.tsx:289,295`

- [ ] **Step 1: Ensure the Empty import exists**

At the top of `src/routes/InstrumentsAssumptions.tsx`, if absent, add:

```tsx
import { Empty } from '../components/Empty';
```

- [ ] **Step 2: Replace the two block-level `<p>` empty states**

Line 289, change:
```tsx
    return <p className="wl-assumptions__empty">{emptyMessage}</p>;
```
to:
```tsx
    return <Empty message={emptyMessage} />;
```

Line 295, change:
```tsx
        <p className="wl-assumptions__empty">No defaults match the current filters.</p>
```
to:
```tsx
        <Empty message="No defaults match the current filters." />
```

Leave lines 463 and 467 (`<td colSpan={5} className="wl-assumptions__empty">…`) unchanged.

- [ ] **Step 3: Keep the CSS rule**

Do **not** delete `.wl-assumptions__empty` in `InstrumentsAssumptions.css` — the table-cell usages (463/467) and `td.wl-assumptions__empty` (line 234) still rely on it.

- [ ] **Step 4: Run the page tests**

Run: `npx vitest run src/routes/InstrumentsAssumptions.test.tsx`
Expected: PASS. Update any assertion coupling on the two migrated block states to text/`.wl-empty`; leave assertions on the table-cell states as-is.

- [ ] **Step 5: Commit**

```bash
git add src/routes/InstrumentsAssumptions.tsx
git commit -m "refactor(ui): route InstrumentsAssumptions block empty states through Empty"
```

### Task 9: Migrate ScenarioTest's "Loading…" to Empty

**Files:**
- Modify: `src/routes/ScenarioTest.tsx:492`, `src/routes/ScenarioTest.css:95`

- [ ] **Step 1: Replace the bespoke loading markup**

`ScenarioTest.tsx` already imports `Empty`. Line 492, change:

```tsx
        <p className="wl-scenario-test__loading">Loading…</p>
```
to:
```tsx
        <Empty variant="loading" message="Loading…" />
```

- [ ] **Step 2: Delete the dead CSS rule**

In `src/routes/ScenarioTest.css`, delete the `.wl-scenario-test__loading { … }` rule (line 95 block).

- [ ] **Step 3: Run the page tests**

Run: `npx vitest run src/routes/ScenarioTest.test.tsx`
Expected: PASS (update class-coupled assertions to text/`.wl-empty` if any).

- [ ] **Step 4: Commit**

```bash
git add src/routes/ScenarioTest.tsx src/routes/ScenarioTest.css
git commit -m "refactor(ui): route ScenarioTest loading state through Empty"
```

### Task 10: Migrate TrySolve's three empty states

**Files:**
- Modify: `src/routes/TrySolve.tsx:599,629,650`, `src/routes/TrySolve.css:494`

- [ ] **Step 1: Ensure the Empty import exists**

At the top of `src/routes/TrySolve.tsx`, if absent, add:

```tsx
import { Empty } from '../components/Empty';
```

- [ ] **Step 2: Replace the three bespoke empty states**

Line 599:
```tsx
              <div className="wl-try-solve__empty">No imported or manual requests.</div>
```
→
```tsx
              <Empty message="No imported or manual requests." />
```

Line 629:
```tsx
            <div className="wl-try-solve__empty">No request row selected.</div>
```
→
```tsx
            <Empty message="No request row selected." />
```

Line 650:
```tsx
            <div className="wl-try-solve__empty">Select a row to inspect solver readiness.</div>
```
→
```tsx
            <Empty message="Select a row to inspect solver readiness." />
```

- [ ] **Step 3: Delete the dead CSS rule**

In `src/routes/TrySolve.css`, delete the `.wl-try-solve__empty { … }` rule (line 494 block).

- [ ] **Step 4: Run the page tests**

Run: `npx vitest run src/routes/TrySolve.test.tsx src/routes/TrySolve.live.test.tsx`
Expected: PASS (update class-coupled assertions to text/`.wl-empty` if any).

- [ ] **Step 5: Commit**

```bash
git add src/routes/TrySolve.tsx src/routes/TrySolve.css
git commit -m "refactor(ui): route TrySolve empty states through Empty"
```

### Task 11: Migrate Tracing's three placeholder states

**Files:**
- Modify: `src/routes/Tracing.tsx:189,191,217`, `src/routes/Tracing.css:143`

- [ ] **Step 1: Replace the three placeholders**

`Tracing.tsx` already imports `Empty`. Line 189 (loading):
```tsx
          <div className="wl-tracing__placeholder">Loading…</div>
```
→
```tsx
          <Empty variant="loading" message="Loading…" />
```

Line 191:
```tsx
          <div className="wl-tracing__placeholder">Select a trace to inspect its spans.</div>
```
→
```tsx
          <Empty message="Select a trace to inspect its spans." />
```

Line 217:
```tsx
          <div className="wl-tracing__placeholder">Select a span to see its payloads.</div>
```
→
```tsx
          <Empty message="Select a span to see its payloads." />
```

- [ ] **Step 2: Delete the dead CSS rule**

In `src/routes/Tracing.css`, delete the `.wl-tracing__placeholder { … }` rule (line 143 block).

- [ ] **Step 3: Run the page tests**

Run: `npx vitest run src/routes/Tracing.test.tsx`
Expected: PASS (update class-coupled assertions to text/`.wl-empty` if any).

- [ ] **Step 4: Commit**

```bash
git add src/routes/Tracing.tsx src/routes/Tracing.css
git commit -m "refactor(ui): route Tracing placeholder states through Empty"
```

### Task 12: Migrate Hedging + InstrumentsAllowedHedges workspace empty states

These two block-level master-detail workspace empty states use feature-local classes (not the `__empty`/`__loading` naming swept elsewhere), so they are called out explicitly.

**Files:**
- Modify: `src/routes/Hedging.tsx:95`, `src/routes/Hedging.css:383`
- Modify: `src/routes/InstrumentsAllowedHedges.tsx:200,225,385`, `src/routes/InstrumentsAllowedHedges.css:153`

- [ ] **Step 1: Migrate Hedging's workspace empty state**

At the top of `src/routes/Hedging.tsx`, add (it does not currently import Empty):
```tsx
import { Empty } from '../components/Empty';
```
Lines 95-97, change:
```tsx
          <div className="hedging-strategy-setup">
            <p>Select an underlying from the left to hedge.</p>
          </div>
```
to:
```tsx
          <Empty message="Select an underlying from the left to hedge." />
```
In `src/routes/Hedging.css`, delete the `.hedging-strategy-setup { … }` rule (line 383 block) — it is referenced only here.

- [ ] **Step 2: Migrate InstrumentsAllowedHedges' three empty states**

At the top of `src/routes/InstrumentsAllowedHedges.tsx`, add (it does not currently import Empty):
```tsx
import { Empty } from '../components/Empty';
```
Line 200, change:
```tsx
      <div className="wl-ah__map-empty">Select an underlying to see its hedge map.</div>
```
to:
```tsx
      <Empty message="Select an underlying to see its hedge map." />
```
Line 225, change:
```tsx
        <p className="wl-ah__map-empty">No allowed hedges — mark candidates below.</p>
```
to:
```tsx
        <Empty message="No allowed hedges — mark candidates below." />
```
Line 385, change:
```tsx
        <p className="wl-ah__map-empty">No candidates match the current filters.</p>
```
to:
```tsx
        <Empty message="No candidates match the current filters." />
```
In `src/routes/InstrumentsAllowedHedges.css`, delete the `.wl-ah__map-empty { … }` rule (line 153 block) — all three references are now migrated.

- [ ] **Step 3: Run the page tests**

Run: `npx vitest run src/routes/Hedging.test.tsx src/routes/InstrumentsAllowedHedges.test.tsx`
Expected: PASS. `Hedging.test.tsx:56` asserts the text `Select an underlying from the left to hedge` via `getByText`, which still matches the `Empty` message — no change needed.

- [ ] **Step 4: Commit**

```bash
git add src/routes/Hedging.tsx src/routes/Hedging.css src/routes/InstrumentsAllowedHedges.tsx src/routes/InstrumentsAllowedHedges.css
git commit -m "refactor(ui): route Hedging + InstrumentsAllowedHedges empty states through Empty"
```

### Task 13: Residual empty-state sweep

Catch any genuine page-level empty/no-data/bare-loading state not covered above.

- [ ] **Step 1: Grep for residual hand-rolled states**

Run, from `frontend/`:
```bash
grep -rnE ">(No [A-Z]|Loading…|Select a|Nothing |None\.)" src/routes --include=*.tsx | grep -v "wl-empty\|<Empty\|Skeleton\|wl-portfolios__empty-inline\|td "
```

- [ ] **Step 2: Apply the recipe to each genuine match**

For each remaining match that is a **block-level page/panel empty or bare-loading state** (NOT: an in-`<td>` table cell, NOT an inline tag/marker like "None.", NOT a `Skeleton` loader, NOT a form hint):
- import `Empty` if absent (`import { Empty } from '../components/Empty';`),
- replace the element with `<Empty message="…" />` (or `<Empty variant="loading" message="Loading…" />` for a bare loading line),
- delete the bespoke CSS rule if it becomes unused (confirm with `grep -rn "<class>" src`).

Skip matches that are in-table cells, inline markers, Skeletons, or form hints — these are intentionally excluded per the spec.

- [ ] **Step 3: Run the full suite**

Run: `npx vitest run`
Expected: PASS (full suite green).

- [ ] **Step 4: Commit (only if Step 2 changed files)**

```bash
git add -A
git commit -m "refactor(ui): route residual empty/loading states through Empty"
```

---

## PART 4 — Whole-contract verification

### Task 14: Live geometry re-measure + theme/density + token sweep

**Files:** none (verification only). Requires the dev server on `:5190` (already running; if not: `npx vite --port 5190 --host 0.0.0.0 --strictPort`).

- [ ] **Step 1: Re-measure every split for two-column layout**

For each of these routes — `/skills`, `/tracing`, `/hedging`, `/rfqs/approval`, `/pricing-parameters`, `/engine-configs`, `/try-solve`, `/instruments`, `/scenario-test`, `/backtest` — load it on `:5190` and evaluate:

```js
() => {
  const s = document.querySelector('.wl-split');
  if (!s) return { url: location.pathname, split: 'none' };
  const cs = getComputedStyle(s);
  const kids = [...s.children].map(c => Math.round(c.getBoundingClientRect().top));
  return { url: location.pathname, gridCols: cs.gridTemplateColumns,
           railVar: cs.getPropertyValue('--rail-width'),
           sideBySide: kids.length > 1 && kids[0] === kids[1] };
}
```
Expected for every page with a split: `gridTemplateColumns` has **two** tracks (≈`220px <rest>`), `sideBySide: true`. None may report a single track.

- [ ] **Step 2: Verify inter-region rhythm is 12px**

On `/positions` (multiple stacked regions), evaluate:
```js
() => {
  const body = document.querySelector('.wl-scaffold__body');
  const k = [...body.children].filter(c => c.getBoundingClientRect().height > 0);
  const gaps = [];
  for (let i = 1; i < k.length; i++) gaps.push(Math.round(k[i].getBoundingClientRect().top - k[i-1].getBoundingClientRect().bottom));
  return { gap: getComputedStyle(body).gap, gaps };
}
```
Expected: `gaps` all `12` (no `24`s), `gap` resolves to `12px`.

- [ ] **Step 3: Both themes + compact density**

Toggle dark theme and compact density (UI controls, or set `document.documentElement.dataset.theme='dark'` / `dataset.density='compact'`). Spot-check `/skills`, `/tracing`, `/try-solve`, `/backtest` (with a loading/empty `Empty` visible): the split stays two-column (rail ≈200px compact), `Empty` text is legible in both themes, the `--loading`/`--error` variants render with token colors. No white control backgrounds in dark mode.

- [ ] **Step 4: Phantom-token sweep**

Run the `comm -23` check from `UI_STYLE_GUIDE.md` §"phantom token sweep". Expected: empty (no undefined tokens). `--wl-rail-width` is an instance-set custom prop with a token fallback — sanctioned, same as `--metric-cols`/`--panel-cols`.

- [ ] **Step 5: Type-check + full suite (final gate)**

Run: `npx tsc --noEmit && npx vitest run`
Expected: 0 type errors; full suite green.

- [ ] **Step 6: Commit any verification-driven fixes**

If Steps 1-5 surfaced a defect, fix it, re-run the relevant step, and commit with a descriptive message. Otherwise no commit.

---

## Done criteria

- All 10 split-bearing pages render two-column (live re-measure), none collapsed.
- `.wl-scaffold__body` gap is the sole source of 12px inter-region rhythm; no body-child carries a stacking margin.
- Every genuine page-level empty/no-data/bare-loading state renders via `Empty`; `Skeleton` loaders untouched.
- `npx tsc --noEmit` clean; `npx vitest run` green; phantom-token sweep empty; both themes + compact verified.

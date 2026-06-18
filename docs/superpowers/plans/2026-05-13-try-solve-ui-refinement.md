# Try to Solve UI Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Patch 8 visual bugs and inconsistencies on the Try to Solve page.

**Architecture:** Purely visual/CSS fixes in two existing files — `TrySolve.css` for 7 styling fixes and `TrySolve.tsx` for replacing custom status badges with the shared `Badge` component. No new files, no data flow changes.

**Tech Stack:** React 19, TypeScript, Vite, vanilla CSS with design tokens. Tests use Vitest + React Testing Library.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `frontend/src/routes/TrySolve.css` | Modify | 7 CSS fixes: date input state, input borders, panel padding, odd field grid, dropdown width, metric styling, focus offset |
| `frontend/src/routes/TrySolve.tsx` | Modify | Replace `.wl-try-solve__status` spans with `Badge` component |

---

### Task 1: CSS Fixes in TrySolve.css

**Files:**
- Modify: `frontend/src/routes/TrySolve.css`
- Test: `frontend/src/routes/TrySolve.test.tsx`

Current `TrySolve.css` starts with these rules. Read the file first to confirm context.

- [ ] **Step 1: Read current TrySolve.css**

Confirm these rules exist in the file (they are the targets for modification):
- `.wl-try-solve__workbench` (line ~45)
- `.wl-try-solve__queue-panel .wl-panel__body, .wl-try-solve__editor-panel .wl-panel__body` (line ~52)
- `.wl-try-solve__field` (line ~220)
- `.wl-try-solve__field input, .wl-try-solve__field select` (line ~254)
- `.wl-try-solve__field:focus-within` (line ~290)
- `.wl-try-solve__metric` (line ~316)
- `.wl-try-solve__metric span` (line ~322)
- `.wl-try-solve__quote-grid` (line ~301)

- [ ] **Step 2: Apply all 7 CSS fixes**

Edit `frontend/src/routes/TrySolve.css` with these changes:

**Fix 1 — Date input empty state:** After the `.wl-try-solve__field input, .wl-try-solve__field select` block, add:

```css
.wl-try-solve__field input[type="date"]:invalid {
  background: var(--paper-2);
}
```

**Fix 2 — Input borders:** In the `.wl-try-solve__field input, .wl-try-solve__field select` rule, change `border: 0` to `border: 1px solid var(--hairline)`:

```css
.wl-try-solve__field input,
.wl-try-solve__field select {
  min-width: 0;
  width: 100%;
  height: 100%;
  border: 1px solid var(--hairline);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-numeric);
  font-size: var(--type-body-size);
  padding: var(--input-padding-y) var(--input-padding-x);
  border-radius: 0;
}
```

**Fix 3 — Panel padding:** Remove the panel padding override rules entirely (delete these lines):

```css
.wl-try-solve__queue-panel .wl-panel__body,
.wl-try-solve__editor-panel .wl-panel__body {
  padding: 0;
}
```

**Fix 4 — Odd field grid:** After the `.wl-try-solve__field` rule, add:

```css
.wl-try-solve__field:last-child:nth-child(odd) {
  grid-column: 1 / -1;
}
```

**Fix 6 — Dropdown truncation:** Two changes:

a) In `.wl-try-solve__workbench`, change the right panel column:
```css
.wl-try-solve__workbench {
  display: grid;
  grid-template-columns: minmax(240px, 0.74fr) minmax(360px, 1.34fr) minmax(340px, 1.1fr);
  gap: var(--gap-3);
  align-items: start;
}
```

b) After the `.wl-try-solve__quote-grid` rule, add a label-width override:
```css
.wl-try-solve__quote-grid .wl-try-solve__field {
  grid-template-columns: minmax(108px, 0.4fr) minmax(0, 1fr);
}
```

**Fix 7 — Metric styling:** In `.wl-try-solve__metric`, add a border:

```css
.wl-try-solve__metric {
  min-width: 0;
  background: var(--paper);
  padding: var(--gap-2);
  border: 1px solid var(--ink);
}
```

And add `letter-spacing: 0.05em` to the label:

```css
.wl-try-solve__metric span {
  display: block;
  margin-bottom: 3px;
  color: var(--ink-2);
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

**Fix 8 — Focus offset:** Change `.wl-try-solve__field:focus-within`:

```css
.wl-try-solve__field:focus-within {
  outline: 2px solid var(--ink);
  outline-offset: 2px;
}
```

- [ ] **Step 3: Run tests to verify nothing breaks**

```bash
cd frontend && npm test -- TrySolve.test.tsx
```

Expected: All tests pass. The test file asserts on `.wl-try-solve__metric` class (which is preserved) and text content (unchanged). No assertions reference `.wl-try-solve__status` or other modified selectors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/TrySolve.css
git commit -m "style(try-solve): fix input borders, panel padding, grid layout, metric styling, focus offset"
```

---

### Task 2: Replace Custom Status Badges with Badge Component

**Files:**
- Modify: `frontend/src/routes/TrySolve.tsx`
- Test: `frontend/src/routes/TrySolve.test.tsx`

- [ ] **Step 1: Read the current status rendering code in TrySolve.tsx**

Find the `statusClass` function (around line 1090) and the status rendering in the row button (around line 553):

```tsx
<span className={`wl-try-solve__status wl-try-solve__status--${statusClass(row.status)}`}>
  {formatStatus(row.status)}
</span>
```

Also confirm `Badge` is not currently imported.

- [ ] **Step 2: Add Badge import and badgeVariant helper**

At the top of `TrySolve.tsx`, add `Badge` to the imports from `../components`:

```tsx
import { Badge, type BadgeVariant } from '../components/Badge';
```

Add a `badgeVariant` function near the existing `statusClass` function (after `formatDateTime`):

```tsx
function badgeVariant(status: string): BadgeVariant {
  const cls = statusClass(status);
  if (cls === 'ready' || cls === 'solved') return 'pos';
  if (cls === 'attention') return 'neg';
  if (cls === 'captured') return 'warn';
  return 'ink';
}
```

- [ ] **Step 3: Replace status span with Badge component**

In the row rendering JSX, replace:

```tsx
<span className={`wl-try-solve__status wl-try-solve__status--${statusClass(row.status)}`}>
  {formatStatus(row.status)}
</span>
```

with:

```tsx
<Badge variant={badgeVariant(row.status)}>{formatStatus(row.status)}</Badge>
```

- [ ] **Step 4: Remove unused status CSS (optional cleanup)**

The `.wl-try-solve__status` and `.wl-try-solve__status--*` rules in `TrySolve.css` are now unused. Delete these rules:

```css
.wl-try-solve__status {
  justify-self: start;
  border: 1px solid var(--hairline-2);
  padding: 2px var(--gap-2);
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  color: var(--ink-2);
  background: var(--paper);
}

.wl-try-solve__status--ready,
.wl-try-solve__status--solved {
  border-color: var(--pos);
  color: var(--pos);
}

.wl-try-solve__status--captured {
  border-color: var(--warn);
  color: var(--warn);
}

.wl-try-solve__status--attention {
  border-color: var(--neg);
  color: var(--neg);
}
```

- [ ] **Step 5: Run tests to verify nothing breaks**

```bash
cd frontend && npm test -- TrySolve.test.tsx
```

Expected: All tests pass. No test asserts on `.wl-try-solve__status` CSS classes.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/TrySolve.tsx frontend/src/routes/TrySolve.css
git commit -m "refactor(try-solve): replace custom status badges with shared Badge component"
```

---

### Task 3: Visual Verification

- [ ] **Step 1: Start the dev server**

```bash
cd frontend && npm run dev
```

- [ ] **Step 2: Verify each fix in browser**

Navigate to the Try to Solve page and verify:

1. **Empty date inputs** — Select the MAN-1 row (if present) or clear a date field. The empty date input should show a subtle `var(--paper-2)` background instead of blending in.
2. **Input borders** — All text inputs and selects in the Autocall Terms and Quote & Solve panels should have a visible `1px solid var(--hairline)` border.
3. **Panel padding** — The Request Queue and Autocall Terms panels should have consistent padding inside their body areas (no content touching edges).
4. **Odd field grid** — With the Autocall product selected, the last field (Tenor Months) should span the full width of the form.
5. **Status badges** — The request queue status badges (Solver Ready, Solved, Missing Terms) should render with the same styling as badges in the Positions page.
6. **Dropdown truncation** — The Pricing Parameters and Market Data dropdowns should show full text without truncation.
7. **Metric styling** — The Product Status, Solver State, Target, and Quote Bounds metrics should have a visible `1px solid var(--ink)` border.
8. **Focus offset** — Click into any form field. The focus outline should be outset (`2px` offset from the field edge), not inset.

- [ ] **Step 3: Stop dev server (if needed)**

```bash
# Ctrl+C in the dev server terminal, or:
pkill -f "vite"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** All 8 issues from the design doc are covered (1-4,6-8 in Task 1; 5 in Task 2).
- [ ] **Placeholder scan:** No TBD, TODO, or vague steps. Every step has exact file paths and code.
- [ ] **Type consistency:** `badgeVariant` returns `BadgeVariant` which matches the `Badge` component's prop type. `statusClass` is reused, so the mapping is consistent.

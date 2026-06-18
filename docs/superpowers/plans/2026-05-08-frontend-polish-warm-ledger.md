# Frontend Polish (Plan 5a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit and harden the Warm Ledger frontend on three axes: accessibility (axe-core sweep + targeted semantic fixes), dark-mode color correctness (replace hardcoded `rgba(20,17,10,...)` overlay/shadow colors with token-based `color-mix(var(--ink), transparent)`), and motion (verify the `prefers-reduced-motion` catch-all reaches every animation/transition in the codebase).

**Architecture:** Use `axe-core` directly with vitest's `expect` — no jest-axe/vitest-axe wrapper needed. A small reusable helper in `test-setup.ts` exposes `expectNoA11yViolations(container)`. Each fix task adds an a11y assertion to the affected component's existing test file, runs it red, applies the minimal fix, runs it green, commits. Color fixes are CSS-only edits backed by visual smoke; motion fixes are an audit-and-document task because the catch-all already handles every case in the current codebase.

**Tech Stack:** axe-core 4.x · vitest 4 · @testing-library/react. No new framework deps; just one runtime/test dep.

**Foundation:** commits f0ca7fc..8883c34. **Plan 2:** 6e31538..29b1a86. **Plan 3:** ce3746a..4d77592. **Plan 4:** 0acf3d4..92c730e. All 6 routes now Warm Ledger; this plan polishes the existing surface.

**Branch:** continues on `main` (user's standing consent for the redesign work).

## Pre-investigation findings (drives the plan)

**A11y gaps:**
- `Sidebar.tsx` active nav item missing `aria-current="page"`. Confirmed: `grep -rn "aria-current"` returns no hits.
- `Table.tsx` is div-based with `wl-table__row`/`wl-table__cell` classes — no semantic role. Screen readers announce it as a generic group.
- Other components have `aria-label` on icon buttons (Chip remove, FloatingAgent open/close, ScenarioGrid promote, GreeksSummary promote, PnlAttribution promote, ReportCard open). Form inputs use `htmlFor` via `useId()`. Radix Dialog handles `aria-modal`/focus trap for Modal + CommandPalette.

**Dark-mode color gaps (hardcoded `rgba(20, 17, 10, ...)` outside tokens):**
- `Modal.css:4` — overlay background `rgba(20, 17, 10, 0.42)`
- `Modal.css:16` — box-shadow `rgba(20, 17, 10, 0.18)`
- `CommandPalette.css:4` — overlay background `rgba(20, 17, 10, 0.42)`
- `CommandPalette.css:15` — box-shadow `rgba(20, 17, 10, 0.28)`
- `FloatingAgent.css:16` — pip box-shadow `rgba(20, 17, 10, 0.18)`
- `FloatingAgent.css:38` — panel box-shadow `rgba(20, 17, 10, 0.18)`

In dark mode (where `--ink: #F0E9D5`), the overlays stay darkish (light-mode ink baked in) — visually they still dim the background, but they're inconsistent with the design system. Box-shadows look almost invisible against dark surfaces. Fix: use `color-mix(in oklab, var(--ink) X%, transparent)` so the overlay tracks the theme's ink color (which is the surface contrast direction in both modes).

**Motion verification:**
- `motion.css` has a catch-all: `*, *::before, *::after { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }` under `@media (prefers-reduced-motion: reduce)`. This catches every `animation:` and `transition:` in the codebase.
- Confirmed animations: `wl-shimmer` (Skeleton — has its own explicit override too), `wl-fade-in` (Modal overlay, Modal content, CommandPalette overlay, FloatingAgent panel), `wl-pulse` (FloatingAgent dot), `wl-slide-in-right` (currently unused but defined).
- Confirmed transitions: `transition: color var(--motion-fade) linear` (Sidebar, Tabs), `transition: background var(--motion-fade) linear` (Button).
- All caught by the catch-all. **Task 6 verifies this empirically** rather than assuming.

---

## File structure

**New files:**
- (none — existing files modified)

**Modified files:**
- `frontend/package.json` — add `axe-core` to devDependencies
- `frontend/src/test-setup.ts` — export a11y assertion helper
- `frontend/src/components/Sidebar.tsx` + `Sidebar.test.tsx` — add aria-current
- `frontend/src/components/Table.tsx` + `Table.test.tsx` — add semantic roles
- `frontend/src/components/Modal.css` — replace hardcoded rgba with color-mix
- `frontend/src/components/CommandPalette.css` — replace hardcoded rgba with color-mix
- `frontend/src/components/FloatingAgent.css` — replace hardcoded rgba with color-mix
- Per-component `.test.tsx` files — add `expectNoA11yViolations` assertion at end of each `describe` block (sweep)
- `README.md` — mark Plan 5a done

---

# Phase A · A11y harness + targeted fixes

## Task 1: Install axe-core + add test helper

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/test-setup.ts`

- [ ] **Step 1: Install axe-core**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend install --save-dev axe-core
```

- [ ] **Step 2: Add the helper to test-setup**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/test-setup.ts`. Append at the end:

```ts
import axe from 'axe-core';

export async function expectNoA11yViolations(container: Element): Promise<void> {
  const results = await axe.run(container, {
    rules: {
      // Disable region landmark rule — small isolated test mounts often have no landmarks by design.
      region: { enabled: false },
    },
  });
  if (results.violations.length > 0) {
    const summary = results.violations
      .map((v) => `${v.id}: ${v.help} (${v.nodes.length} node${v.nodes.length === 1 ? '' : 's'})`)
      .join('\n');
    throw new Error(`a11y violations:\n${summary}`);
  }
}
```

- [ ] **Step 3: Smoke-test the helper**

Run from frontend dir to verify axe-core loads and the helper exports cleanly. If the existing test suite still passes, the helper hasn't broken anything:

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test -- --run 2>&1 | tail -5
```

Expected: all tests pass (no new tests added yet; just verifying the import doesn't break anything).

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/package.json frontend/package-lock.json frontend/src/test-setup.ts
git -C /Users/fuxinyao/open-otc-trading commit -m "chore(frontend): add axe-core + a11y test helper"
```

## Task 2: Fix Sidebar aria-current

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/Sidebar.test.tsx`

- [ ] **Step 1: Add the failing test**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/Sidebar.test.tsx`. Inside the existing `describe('Sidebar', ...)` block, after the existing tests, add:

```tsx
  it('marks active route with aria-current="page"', () => {
    render(<Sidebar items={items} active="chat" onNavigate={() => {}} />);
    const buttons = screen.getAllByRole('button');
    const active = buttons.find((b) => b.textContent === 'Agent Desk');
    const inactive = buttons.find((b) => b.textContent === 'Positions');
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(inactive).not.toHaveAttribute('aria-current');
  });

  it('has no a11y violations', async () => {
    const { container } = render(<Sidebar items={items} active="chat" onNavigate={() => {}} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

- [ ] **Step 2: Run, verify the aria-current test fails**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test Sidebar -- --run 2>&1 | tail -10
```
Expected: `marks active route with aria-current="page"` FAILS (no aria-current on disk yet). The `has no a11y violations` test may also fail if axe finds anything; we'll fix both in step 3.

- [ ] **Step 3: Fix Sidebar.tsx**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/Sidebar.tsx`. Find the `<button>` inside the `.map`. Add `aria-current={item.route === active ? 'page' : undefined}`:

```tsx
          <button
            key={item.route}
            type="button"
            className={`wl-sidebar__nav-item ${item.route === active ? 'wl-sidebar__nav-item--active' : ''}`.trim()}
            aria-current={item.route === active ? 'page' : undefined}
            onClick={() => onNavigate(item.route)}
          >
            {item.label}
          </button>
```

- [ ] **Step 4: Run, verify both tests pass**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test Sidebar -- --run 2>&1 | tail -10
```
Expected: PASS — 4 tests (2 original + 2 new).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/Sidebar.tsx frontend/src/components/Sidebar.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): Sidebar marks active item with aria-current=page"
```

## Task 3: Fix Table semantic roles

**Files:**
- Modify: `frontend/src/components/Table.tsx`
- Modify: `frontend/src/components/Table.test.tsx`

- [ ] **Step 1: Add the failing test**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/Table.test.tsx`. Inside the existing `describe('Table', ...)` block, append:

```tsx
  it('exposes semantic table roles', () => {
    render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    // 1 head row + N body rows
    expect(screen.getAllByRole('row').length).toBe(rows.length + 1);
    expect(screen.getAllByRole('columnheader').length).toBe(columns.length);
    expect(screen.getAllByRole('cell').length).toBe(rows.length * columns.length);
  });

  it('has no a11y violations', async () => {
    const { container } = render(<Table<Row> columns={[...columns]} rows={rows} rowKey={(r) => r.trade} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

- [ ] **Step 2: Run, verify the role test fails**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test Table -- --run 2>&1 | tail -10
```
Expected: `exposes semantic table roles` FAILS (no role="table" found).

- [ ] **Step 3: Fix Table.tsx**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/Table.tsx`. The current implementation uses `<div>` for table/row/cell. Add ARIA roles to make screen readers announce the table semantically. **Do not change to actual `<table>` elements** — the CSS grid layout depends on `display: grid` per row, which `<table>` doesn't permit. ARIA roles bridge the visual and semantic concerns.

Change the outer `<div className="wl-table ...">` to `<div role="table" ...>`. Change each row `<div className="wl-table__row ...">` to `<div role="row" ...>`. Change header cells (in the row marked with `wl-table__row--head`) to `role="columnheader"`. Change body cells to `role="cell"`.

Specifically, replace the function body of `Table` with:

```tsx
  const gridTemplate = columns.map((c) => c.width ?? '1fr').join(' ');
  return (
    <div className={`wl-table ${className}`.trim()} role="table">
      <div
        className="wl-table__row wl-table__row--head"
        role="row"
        style={{ gridTemplateColumns: gridTemplate }}
      >
        {columns.map((c) => (
          <div
            key={c.key}
            role="columnheader"
            className={c.numeric ? 'wl-table__cell wl-table__cell--num' : 'wl-table__cell'}
          >
            {c.header}
          </div>
        ))}
      </div>
      {rows.map((row) => {
        const key = rowKey(row);
        const isSelected = selectedKey === key;
        return (
          <div
            key={key}
            className={`wl-table__row ${isSelected ? 'wl-table__row--selected' : ''}`}
            role="row"
            style={{ gridTemplateColumns: gridTemplate }}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            data-clickable={onRowClick ? 'true' : undefined}
          >
            {columns.map((c) => (
              <div
                key={c.key}
                role="cell"
                className={c.numeric ? 'wl-table__cell wl-table__cell--num' : 'wl-table__cell'}
              >
                {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
```

- [ ] **Step 4: Run, verify all Table tests pass**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test Table -- --run 2>&1 | tail -10
```
Expected: PASS — 5 tests (3 original + 2 new).

- [ ] **Step 5: Run the full test suite to make sure no consumer broke**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all tests pass. Several tests use `getByText` on table cells (Positions, RfqInbox derivatives) — ARIA roles don't change text discovery, so these should still pass.

- [ ] **Step 6: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/Table.tsx frontend/src/components/Table.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): Table exposes semantic ARIA roles (table/row/cell/columnheader)"
```

## Task 4: A11y sweep across all primitive components

**Files:**
- Modify: existing `*.test.tsx` files for the listed primitives (add one a11y test each)

This task adds a single `'has no a11y violations'` test to each primitive's existing test file, runs them, and fixes any findings inline. Most primitives are expected to pass without changes (they were built with semantic markup); some may surface real issues.

The primitives to sweep (12 total):

1. Button.test.tsx
2. Input.test.tsx
3. Badge.test.tsx (also covers Chip — same test file)
4. Panel.test.tsx (also covers Tile — same test file)
5. Tabs.test.tsx
6. Modal.test.tsx
7. Skeleton.test.tsx (also covers Empty — same test file)
8. PageContextChips.test.tsx
9. AssetCard.test.tsx
10. ActionProposal.test.tsx
11. CommandPalette.test.tsx
12. FloatingAgent.test.tsx
13. PageHeader.test.tsx
14. AppShell.test.tsx

(Sidebar + Table already covered in Tasks 2 + 3.)

- [ ] **Step 1: Add the a11y test to each file**

For each test file in the list, add a single test at the **end of the outermost describe block** (before its closing `});`). The pattern is the same — only the rendered component differs.

Use the file's existing test setup (variables, fixtures) so the rendered component is realistic.

For Button.test.tsx, append inside `describe('Button', ...)`:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(<Button>Submit</Button>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For Input.test.tsx, append inside `describe('Input', ...)`:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(<Input label="Strike" />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For Badge.test.tsx (which has both `describe('Badge', ...)` and `describe('Chip', ...)`), add ONE test at the very end of the file (outside the describes), or inside each describe. Use this single new describe block at end-of-file:

```tsx
describe('Badge + Chip a11y', () => {
  it('Badge has no a11y violations', async () => {
    const { container } = render(<Badge>APPROVED</Badge>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('Chip has no a11y violations', async () => {
    const { container } = render(<Chip>CSI500</Chip>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});
```

For Panel.test.tsx (covers Panel + Tile), append at end:

```tsx
describe('Panel + Tile a11y', () => {
  it('Panel has no a11y violations', async () => {
    const { container } = render(<Panel title="Greeks">body</Panel>);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('Tile has no a11y violations', async () => {
    const { container } = render(<Tile label="NAV" value="38.2M" />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});
```

For Tabs.test.tsx, append inside `describe('Tabs', ...)`:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">First</TabsContent>
        <TabsContent value="b">Second</TabsContent>
      </Tabs>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For Modal.test.tsx, append inside `describe('Modal', ...)`. Note: Radix Dialog renders into a portal (`document.body`), so we run axe on `document.body` instead of `container`:

```tsx
  it('has no a11y violations', async () => {
    render(
      <Modal open onOpenChange={() => {}} title="Confirm">
        <p>Are you sure?</p>
      </Modal>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(document.body);
  });
```

For Skeleton.test.tsx (covers Skeleton + Empty), append at end:

```tsx
describe('Skeleton + Empty a11y', () => {
  it('Skeleton has no a11y violations', async () => {
    const { container } = render(<Skeleton height={20} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('Empty has no a11y violations', async () => {
    const { container } = render(<Empty message="No risk runs yet." />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
});
```

For PageContextChips.test.tsx, append inside the existing describe:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(<PageContextChips chips={['12 trades', 'Run #87']} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For AssetCard.test.tsx, append inside the existing describe:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(<AssetCard asset={asset} subtitle="3.2KB" />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For ActionProposal.test.tsx, append inside the existing describe:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={() => {}} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For CommandPalette.test.tsx, append inside the existing describe (Radix portal — use document.body):

```tsx
  it('has no a11y violations', async () => {
    render(<CommandPalette open onOpenChange={() => {}} items={items} onSelect={() => {}} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(document.body);
  });
```

For FloatingAgent.test.tsx, append inside the existing describe (panel renders inline, not portal):

```tsx
  it('collapsed has no a11y violations', async () => {
    const { container } = render(
      <FloatingAgent open={false} onOpenChange={() => {}} chips={[]} hasUnread={false} />
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });

  it('expanded has no a11y violations', async () => {
    const { container } = render(
      <FloatingAgent open onOpenChange={() => {}} chips={['Run #87']} hasUnread={false}>
        <p>scaffold</p>
      </FloatingAgent>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For PageHeader.test.tsx, append inside the existing describe:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(<PageHeader title="Positions" chips={['12 trades']} />);
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

For AppShell.test.tsx, append inside the existing describe:

```tsx
  it('has no a11y violations', async () => {
    const { container } = render(
      <AppShell active="chat" onNavigate={() => {}} items={items}>
        <div>page content</div>
      </AppShell>
    );
    const { expectNoA11yViolations } = await import('../test-setup');
    await expectNoA11yViolations(container);
  });
```

- [ ] **Step 2: Run the full test suite**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -25
```

Expected outcomes per component:

- **Most primitives:** PASS. Button, Input, Badge, Chip, Tile, Skeleton, Empty, PageContextChips, PageHeader should all pass without code changes.
- **Suspicious:** Panel may flag `region: { enabled: false }` is already disabled, but landmark uniqueness checks may still trigger if multiple `<section>`s with the same accessible name render together — unlikely in isolation tests. ActionProposal uses `aria-label` on its action buttons via the Button component — should pass. AssetCard has an icon that's text content — should pass. CommandPalette + Modal use Radix Dialog; Radix typically ships with the right `aria-modal`/`role` — pass.
- **Likely failures (real issues to fix):**
  - `FloatingAgent` collapsed: the pip is a `<button aria-label="Open agent">` with a `<span>` inside — should pass.
  - `FloatingAgent` expanded: `role="dialog"` + `aria-label="Agent panel"` — needs `aria-labelledby` OR `aria-label` (already has aria-label). Should pass.
  - `Tabs`: Radix Tabs ships with proper roles — should pass.
  - `AppShell`: contains the Sidebar which now has `aria-current` from Task 2 — should pass; landmark structure (`<aside>` + `<main>`) may flag if header is missing. The shell does NOT render a `<header>` of its own — toolbar is a div. Check if axe complains about no header landmark; if so, add `role="banner"` to the toolbar OR wrap it in `<header>`.

If a test fails, READ the violation message, fix the source component, re-run. Common fixes:
- Missing label → add `aria-label`
- Missing list semantics → add `role="list"`/`role="listitem"`
- Missing landmark → wrap in `<header>`/`<main>`/`<nav>` or add `role="..."`

- [ ] **Step 3: Fix any real findings**

For each failing component, apply the minimal fix to the source. After each fix, re-run that component's tests until green. Don't add new tests beyond the a11y assertions.

If a finding is genuinely a non-issue for an isolated test (e.g., axe reporting a "page must have a heading" rule for a primitive that's never the root of a page), that rule should be disabled in `expectNoA11yViolations` rather than the source patched. To disable a rule, modify `frontend/src/test-setup.ts` to add to the `rules` config:

```ts
{
  // existing 'region' disable...
  'page-has-heading-one': { enabled: false },
}
```

Only disable rules with a written reason in a `// reason: ...` comment. Don't blanket-disable.

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components frontend/src/test-setup.ts
git -C /Users/fuxinyao/open-otc-trading commit -m "test(frontend): a11y assertions across primitive components"
```

If source-level fixes were made beyond just adding tests, the commit message should reflect them — e.g., `feat(frontend): a11y sweep + targeted fixes for AppShell landmark`.

---

# Phase B · Dark-mode token fixes

## Task 5: Replace hardcoded `rgba(20, 17, 10, ...)` with token-based color-mix

**Files:**
- Modify: `frontend/src/components/Modal.css`
- Modify: `frontend/src/components/CommandPalette.css`
- Modify: `frontend/src/components/FloatingAgent.css`

These three components hardcode the LIGHT-mode ink color in their overlays and box-shadows. In dark mode (`--ink: #F0E9D5`), the overlays should use the new ink color so the visual contrast is correct.

This task is CSS-only — no test changes. Verify via dev server visual smoke after.

- [ ] **Step 1: Modal.css**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/Modal.css`. Find:

```css
.wl-modal__overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 17, 10, 0.42);
  animation: wl-fade-in var(--motion-fade) linear;
}
```

Change `background: rgba(20, 17, 10, 0.42);` to:

```css
  background: color-mix(in oklab, var(--ink) 42%, transparent);
```

Find:

```css
.wl-modal__content {
  ...
  box-shadow: 0 12px 32px rgba(20, 17, 10, 0.18);
  ...
}
```

Change `box-shadow: 0 12px 32px rgba(20, 17, 10, 0.18);` to:

```css
  box-shadow: 0 12px 32px color-mix(in oklab, var(--ink) 18%, transparent);
```

- [ ] **Step 2: CommandPalette.css**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/CommandPalette.css`. Find:

```css
.wl-cmdk__overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 17, 10, 0.42);
  animation: wl-fade-in var(--motion-fade) linear;
}
```

Change `background: rgba(20, 17, 10, 0.42);` to:

```css
  background: color-mix(in oklab, var(--ink) 42%, transparent);
```

Find:

```css
.wl-cmdk {
  ...
  box-shadow: 0 12px 32px rgba(20, 17, 10, 0.28);
  ...
}
```

Change `box-shadow: 0 12px 32px rgba(20, 17, 10, 0.28);` to:

```css
  box-shadow: 0 12px 32px color-mix(in oklab, var(--ink) 28%, transparent);
```

- [ ] **Step 3: FloatingAgent.css**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/components/FloatingAgent.css`. Find both occurrences of `rgba(20, 17, 10, 0.18)`:

First (pip):
```css
.wl-agent-pip {
  ...
  box-shadow: 0 4px 12px rgba(20, 17, 10, 0.18);
  ...
}
```

Change to:
```css
  box-shadow: 0 4px 12px color-mix(in oklab, var(--ink) 18%, transparent);
```

Second (panel):
```css
.wl-agent-panel {
  ...
  box-shadow: 0 12px 32px rgba(20, 17, 10, 0.18);
  ...
}
```

Change to:
```css
  box-shadow: 0 12px 32px color-mix(in oklab, var(--ink) 18%, transparent);
```

- [ ] **Step 4: Verify the test suite still passes**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all tests still pass — these are CSS-only changes.

- [ ] **Step 5: Verify no `rgba(20, 17, 10` remains in components**

```bash
grep -rn "rgba(20, 17, 10" /Users/fuxinyao/open-otc-trading/frontend/src/components || echo "ALL FIXED"
```
Expected: `ALL FIXED`. (RfqStatusCard, RfqDetail, RfqInbox tests legitimately reference `#1042` etc. in their text assertions — those are different patterns and fine.)

- [ ] **Step 6: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/Modal.css frontend/src/components/CommandPalette.css frontend/src/components/FloatingAgent.css
git -C /Users/fuxinyao/open-otc-trading commit -m "fix(frontend): overlay+shadow colors track theme via color-mix(var(--ink))"
```

---

# Phase C · Motion verification

## Task 6: Audit prefers-reduced-motion coverage

**Files:**
- (None modified — audit + documentation task)

The `motion.css` catch-all rule under `@media (prefers-reduced-motion: reduce)` neutralizes every `animation-duration` and `transition-duration` with `!important`. This task verifies empirically that every animated element in the codebase is covered.

- [ ] **Step 1: Inventory all animations and transitions**

```bash
grep -rnE "animation:|transition:" /Users/fuxinyao/open-otc-trading/frontend/src --include="*.css" | grep -v tokens/motion.css
```

Expected output (from current state — if this list grows in the future, the same audit applies):

```
Tabs.css: transition: color var(--motion-fade) linear;
CommandPalette.css: animation: wl-fade-in var(--motion-fade) linear;
Skeleton.css: animation: wl-shimmer var(--motion-shimmer) linear infinite;
Skeleton.css: .wl-skeleton { animation: none; background: var(--paper-2); }   ← inside reduced-motion media
FloatingAgent.css: animation: wl-pulse 2.4s ease-in-out infinite;
FloatingAgent.css: animation: wl-fade-in var(--motion-slide) var(--motion-curve);
Sidebar.css: transition: color var(--motion-fade) linear;
Modal.css: animation: wl-fade-in var(--motion-fade) linear;
Modal.css: animation: wl-fade-in var(--motion-slide) var(--motion-curve);
Button.css: transition: background var(--motion-fade) linear;
```

- [ ] **Step 2: Verify the catch-all in motion.css**

Read `/Users/fuxinyao/open-otc-trading/frontend/src/tokens/motion.css`. Confirm the `@media (prefers-reduced-motion: reduce)` block contains:

```css
@media (prefers-reduced-motion: reduce) {
  :root {
    --motion-fade: 0ms;
    --motion-slide: 0ms;
  }
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    transition-duration: 0.001ms !important;
  }
}
```

The `* { animation-duration: 0.001ms !important }` rule applies to every element in the document, neutralizing every `animation: ...` declaration's duration. Same for transitions. The token reset (`--motion-fade: 0ms`) is a belt-and-braces redundancy — components that interpolate the token (e.g., `transition: color var(--motion-fade) linear`) get 0ms from the token AND 0.001ms from the catch-all. Either alone would suffice.

Per-component overrides exist where the animation might want a custom reduced-motion behavior. Skeleton.css line 8 explicitly sets `animation: none; background: var(--paper-2)` to replace the shimmer with a static color (visually clearer than freezing the gradient at any random offset).

- [ ] **Step 3: Spot-check via dev server (manual smoke)**

This step is manual — for the user to verify or for the implementer to confirm via the OS-level reduced-motion preference:

On macOS: System Settings → Accessibility → Display → toggle "Reduce motion".
Then start the dev server: `npm --prefix /Users/fuxinyao/open-otc-trading/frontend run dev` and visit `http://localhost:5173`. Confirm:
- Floating agent pip's pulsing dot (when has-unread) does NOT animate (frozen at any opacity)
- Skeleton on Risk page (during initial load) does NOT shimmer — appears as solid `--paper-2`
- Modal opening (e.g., reject RFQ) appears instantly without fade
- ⌘K palette opens instantly without fade
- Theme toggle button hover transition is instant (Sidebar/Tabs)

If any animation still moves under reduced motion, that's a real bug to fix.

- [ ] **Step 4: Document the motion-coverage state**

Create `/Users/fuxinyao/open-otc-trading/docs/superpowers/notes/motion-coverage.md`:

```markdown
# Motion coverage (Plan 5a)

Audited: $(date)

All animations and transitions in `frontend/src` route through one of:
- `var(--motion-fade)` / `var(--motion-slide)` — set to `0ms` under `prefers-reduced-motion: reduce`
- The catch-all `*, *::before, *::after { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }`

No per-component reduced-motion override is required beyond the existing `Skeleton.css:8`
override (which replaces the shimmer with a static `var(--paper-2)` background — visually
clearer than freezing the gradient).

When adding new animations:
1. Use `var(--motion-fade)` / `var(--motion-slide)` for durations whenever possible.
2. The catch-all will neutralize any new `animation:` or `transition:` declarations automatically.
3. If freezing the animation at a static state would be misleading (e.g., a progress bar
   freezing partway), add a component-specific reduced-motion override that swaps the
   animated state for an explicit static one.
```

(The `$(date)` placeholder gets replaced when the file is written; if the implementer runs in a shell, use the actual current date in plain text.)

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add docs/superpowers/notes/motion-coverage.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: motion-coverage audit confirms catch-all reaches all animations"
```

---

# Phase D · Dark-mode QA + smoke + README

## Task 7: Dark-mode visual QA pass

**Files:**
- (Variable — depends on findings)

This is a manual visual QA task. The implementer (or user) starts the dev server, switches the app to dark mode, and visits each route. Findings get fixed inline; if no findings, the task is just a documentation note.

- [ ] **Step 1: Start dev server**

```bash
timeout 6 npm --prefix /Users/fuxinyao/open-otc-trading/frontend run dev 2>&1 | head -10
```

Expected: VITE ready. (For interactive QA, run without timeout and leave it running.)

- [ ] **Step 2: Switch to dark mode**

Open `http://localhost:5173/`. In the toolbar (top-right), click the `theme: system` button until it cycles to `theme: dark`. Confirm `<html data-theme="dark">` via DevTools.

- [ ] **Step 3: Visit each route, record findings**

For each of the 6 routes, eyeball the layout in dark mode for these issues:

- **Color contrast** — text legible against background?
- **Borders** — visible against dark surfaces, not lost?
- **Hardcoded colors** — anything that's still light-mode (other than what was fixed in Task 5)?
- **Selected/hover states** — visually distinct?
- **Modal/CommandPalette** — overlay tint reads as "dim background" not "wash everything out"?
- **FloatingAgent** — pip + panel readable against dark background?

Routes to check: Positions, RFQ Approval, Risk, Reports, Agent Desk, Client RFQ.

- [ ] **Step 4: Fix findings**

If a route has hardcoded colors or visual-token mismatches, the fix is one of:
- Replace hardcoded hex/rgba with `var(--token)`
- Add a missing token override under `[data-theme="dark"]` in `frontend/src/tokens/colors.css`
- Add a missing `[data-theme="dark"]` selector in the component's CSS

Document each fix in the commit message. If NO findings are made, the implementer should still commit a sentinel note.

- [ ] **Step 5: Verify the test suite still passes**

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all pass.

- [ ] **Step 6: Commit**

If fixes were made:

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src
git -C /Users/fuxinyao/open-otc-trading commit -m "fix(frontend): dark-mode visual fixes — <list of components fixed>"
```

If NO fixes were needed, append to `docs/superpowers/notes/motion-coverage.md` (or create a `dark-mode-coverage.md`) and commit:

```markdown
# Dark-mode QA (Plan 5a)

Audited: <date>

All 6 routes verified in dark mode (data-theme="dark"). No hardcoded colors,
no contrast failures, no visual regressions. Token system covers the dark
surface correctly across primitives + routes.
```

```bash
git -C /Users/fuxinyao/open-otc-trading add docs/superpowers/notes/dark-mode-coverage.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: dark-mode QA pass — no findings across all 6 routes"
```

## Task 8: Final smoke + README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run all automated checks**

Backend:

```bash
python -m pytest 2>&1 | tail -5
```
Expected: all pass.

Frontend:

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend test 2>&1 | tail -5
```
Expected: all tests pass (count grew by 16+ from a11y sweep additions).

```bash
cd /Users/fuxinyao/open-otc-trading/frontend && npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
npm --prefix /Users/fuxinyao/open-otc-trading/frontend run build 2>&1 | tail -5
```
Expected: build succeeds.

- [ ] **Step 2: Update README**

Open `/Users/fuxinyao/open-otc-trading/README.md`. Find the "Follow-up plans" section and update Plan 5 line:

Replace:

```markdown
- Plan 5 — accessibility audit + prefers-reduced-motion verification + backend Greeks/scenario extension + token-by-token chat streaming + audit-event endpoint + Berkeley Mono procurement
```

With:

```markdown
- ✅ Plan 5a — Frontend polish (a11y + reduced-motion + dark-mode QA) (`docs/superpowers/plans/2026-05-08-frontend-polish-warm-ledger.md`)
- Plan 5b — Backend Greeks/scenario extension + audit-event endpoint
- Plan 5c — Token-by-token chat streaming
- Plan 5d — Berkeley Mono procurement (purchasing decision, not code)
```

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add README.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: note Plan 5a (frontend polish) shipped; split Plan 5 remainder"
```

---

# Self-Review Notes

(Performed after writing the plan; fixes applied inline.)

**Coverage:**
- A11y harness ✓ Task 1
- Sidebar aria-current ✓ Task 2
- Table semantic roles ✓ Task 3
- Per-component a11y sweep ✓ Task 4
- Dark-mode color tokenization ✓ Task 5
- Motion coverage audit ✓ Task 6
- Dark-mode visual QA ✓ Task 7
- Smoke + README ✓ Task 8

**Type consistency:** No new types introduced. Helper signature `expectNoA11yViolations(container: Element): Promise<void>` is consistent across all consuming tests.

**Placeholder scan:** No "TBD"/"TODO"/"implement later"/"similar to Task N" tokens. Each Task 4 component test pattern shows full code; "$(date)" in Task 6's documentation file is a literal that the implementer should replace with the actual date.

**Out of scope (deferred to Plan 5b/5c/5d):**
- Backend Greeks (Γ, V, θ, ρ) extension — Plan 5b
- Backend scenario engine (shift × vol matrix) — Plan 5b
- Backend audit-event listing endpoint — Plan 5b
- Token-by-token chat streaming UX — Plan 5c
- Berkeley Mono license procurement — Plan 5d

**Open questions:**
- The Task 4 sweep may surface a11y violations I haven't anticipated. The plan's guidance ("READ the violation message, fix the source") is the right pattern but means Task 4 has variable scope. If the sweep finds many issues, consider splitting fixes into a separate follow-up commit — keep one fix per commit so the bisect history stays clean.
- Task 7's dark-mode QA is genuinely visual; if running headlessly via subagent, the implementer can't verify by sight. They should fall back to grepping for hardcoded colors and confirming token usage, then mark the task as completed-with-caveats and hand to the user for browser smoke.

# UI Template Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ~20 pages' bespoke layout scaffolding with 6 reusable template components built over 5 shared region primitives, with zero visual-regression intent.

**Architecture:** Two layers. Layer 1 = small region primitives (`MetricRow`, `TableToolbar`, `PanelGrid`, `SplitLayout`, `Stepper`) each with one co-located token-only `.css`. Layer 2 = a private `PageScaffold` (PageHeader + feedback + vertical stack) and 6 templates (`DataTablePage`, `AnalyticsDashboard`, `MasterDetailPage`, `WorkbenchPage`, `ConversationalWorkspace`, `WizardPage`) that compose the primitives. Pages then declare a template and fill slots; their bespoke region CSS is deleted.

**Tech Stack:** React 18 + TypeScript, Vite, Vitest + @testing-library/react, plain CSS custom properties (tokens in `src/tokens/`). BEM `wl-` naming. See `frontend/UI_STYLE_GUIDE.md`.

**Reference:** spec at `docs/superpowers/specs/2026-06-14-ui-template-unification-design.md`.

**Working dir:** all paths are under `frontend/`. Run commands from `frontend/`. Tests: `npx vitest run <path>`.

---

## File Structure

**New — Layer 1 primitives** (`frontend/src/components/`):
- `MetricRow.tsx` + `MetricRow.css` — KPI tile row(s). Wraps `Tile`.
- `TableToolbar.tsx` + `TableToolbar.css` — search + filters slot + pager.
- `PanelGrid.tsx` + `PanelGrid.css` — responsive grid of `Panel`s.
- `SplitLayout.tsx` + `SplitLayout.css` — rail + workspace, collapsible.
- `Stepper.tsx` + `Stepper.css` — wizard progress.

**New — Layer 2 templates** (`frontend/src/components/templates/`):
- `PageScaffold.tsx` + `PageScaffold.css` — private base (PageHeader + feedback + stack).
- `DataTablePage.tsx` (A), `AnalyticsDashboard.tsx` (B), `MasterDetailPage.tsx` (C),
  `WorkbenchPage.tsx` (D), `ConversationalWorkspace.tsx` (E), `WizardPage.tsx` (F),
  each + co-located `.css`.
- `index.ts` — barrel re-export of the six templates.

**New — token:** add `--rail-width` to `frontend/src/tokens/density.css`.

**Modified — pages** (`frontend/src/routes/*.tsx` + co-located `.css`): each migrated to a template; dead region CSS removed. Full list in Phase 3.

**Unchanged:** `Tile`, `Table`, `Panel`, `PageHeader`, `Modal`, `Tabs`, `Button`, `Input`, `Badge`, `Chip`, `Sidebar`, `AppShell`, all `.live.tsx` data logic, tokens other than the one addition.

---

## Phase 0 — Token

### Task 0: Add the `--rail-width` token

**Files:**
- Modify: `frontend/src/tokens/density.css`

- [ ] **Step 1: Add the token to `:root` and the compact override**

In `density.css`, inside the `:root { … }` block add:

```css
  --rail-width: 220px;
```

In the `[data-density="compact"]` block add:

```css
  --rail-width: 200px;
```

- [ ] **Step 2: Verify the phantom-token sweep still passes**

Run (from `frontend/`):

```bash
csv=$(find src -name '*.css'); comm -23 \
  <(echo "$csv" | xargs grep -hoE "var\(--[A-Za-z0-9_-]+" | sed -E 's/var\(//' | sort -u) \
  <(echo "$csv" | xargs grep -hoE -- "--[A-Za-z0-9_-]+[[:space:]]*:" | sed -E 's/[[:space:]]*:$//' | sort -u)
```

Expected: empty output (no undefined tokens).

- [ ] **Step 3: Commit**

```bash
git add src/tokens/density.css
git commit -m "feat(tokens): add --rail-width for SplitLayout rail basis"
```

---

## Phase 1 — Region primitives (TDD)

### Task 1: `MetricRow`

**Files:**
- Create: `frontend/src/components/MetricRow.tsx`, `frontend/src/components/MetricRow.css`
- Test: `frontend/src/components/MetricRow.test.tsx`

> **Convention (applies to every test file in this plan):** match the repo
> convention — explicitly `import { describe, it, expect, vi } from 'vitest';`
> (import only what the file uses) plus the Testing Library imports. `globals:true`
> is set, but every existing `*.test.tsx` imports explicitly; do the same.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MetricRow } from './MetricRow';

describe('MetricRow', () => {
  it('renders one tile per metric with label and value', () => {
    render(<MetricRow metrics={[
      { label: 'NAV', value: '1,000' },
      { label: 'PnL', value: '-20', variant: 'neg' },
    ]} />);
    expect(screen.getByText('NAV')).toBeInTheDocument();
    expect(screen.getByText('1,000')).toBeInTheDocument();
    expect(screen.getByText('-20')).toBeInTheDocument();
  });

  it('applies the negative variant class to the tile', () => {
    const { container } = render(<MetricRow metrics={[{ label: 'PnL', value: '-1', variant: 'neg' }]} />);
    expect(container.querySelector('.wl-tile--neg')).not.toBeNull();
  });

  it('renders nothing when metrics is empty', () => {
    const { container } = render(<MetricRow metrics={[]} />);
    expect(container.querySelector('.wl-metric-row')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test, verify it fails** — `npx vitest run src/components/MetricRow.test.tsx` → FAIL (module not found).

- [ ] **Step 3: Implement**

`MetricRow.tsx`:

```tsx
import React from 'react';
import { Tile, type TileVariant } from './Tile';
import './MetricRow.css';

export type Metric = {
  label: string;
  value: React.ReactNode;
  variant?: TileVariant;
  delta?: React.ReactNode;
};

type Props = {
  metrics: Metric[];
  columns?: number;
  className?: string;
};

export function MetricRow({ metrics, columns, className = '' }: Props) {
  if (metrics.length === 0) return null;
  const style = columns ? ({ '--metric-cols': String(columns) } as React.CSSProperties) : undefined;
  return (
    <div className={`wl-metric-row ${className}`.trim()} style={style}>
      {metrics.map((m, i) => (
        <Tile key={`${m.label}-${i}`} label={m.label} value={m.value} variant={m.variant} delta={m.delta} />
      ))}
    </div>
  );
}

// Shared helper used by every template that accepts `metrics`. Renders one
// <MetricRow> for a flat Metric[], or one per inner array for Metric[][].
// Exported here (single source of truth) so templates don't each re-declare it.
export function MetricRows({ metrics }: { metrics?: Metric[] | Metric[][] }) {
  if (!metrics || metrics.length === 0) return null;
  const rows = Array.isArray(metrics[0]) ? (metrics as Metric[][]) : [metrics as Metric[]];
  return <>{rows.map((row, i) => <MetricRow key={i} metrics={row} />)}</>;
}
```

`MetricRow.css` (mirrors the existing `wl-positions__tiles` rhythm):

```css
.wl-metric-row {
  display: grid;
  grid-template-columns: repeat(var(--metric-cols, 4), minmax(0, 1fr));
  gap: var(--gap-3);
  margin-bottom: var(--gap-3);
}
@media (max-width: 900px) {
  .wl-metric-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 560px) {
  .wl-metric-row { grid-template-columns: 1fr; }
}
```

- [ ] **Step 4: Run test, verify pass** — `npx vitest run src/components/MetricRow.test.tsx` → PASS.

- [ ] **Step 5: Commit** — `git add src/components/MetricRow.* && git commit -m "feat(ui): MetricRow primitive (KPI tile row)"`

---

### Task 2: `TableToolbar`

**Files:**
- Create: `frontend/src/components/TableToolbar.tsx`, `frontend/src/components/TableToolbar.css`
- Test: `frontend/src/components/TableToolbar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TableToolbar } from './TableToolbar';

describe('TableToolbar', () => {
  it('fires onChange when typing in search', () => {
    const onChange = vi.fn();
    render(<TableToolbar search={{ value: '', onChange, placeholder: 'Search…' }} />);
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'abc' } });
    expect(onChange).toHaveBeenCalledWith('abc');
  });

  it('renders the filters slot and pager info', () => {
    render(
      <TableToolbar
        filters={<button>All</button>}
        pager={{ page: 0, pageSize: 25, total: 60, onPage: () => {} }}
      />,
    );
    expect(screen.getByText('All')).toBeInTheDocument();
    expect(screen.getByText('1-25 of 60')).toBeInTheDocument();
  });

  it('advances the page when Next is clicked', () => {
    const onPage = vi.fn();
    render(<TableToolbar pager={{ page: 0, pageSize: 25, total: 60, onPage }} />);
    fireEvent.click(screen.getByLabelText('Next page'));
    expect(onPage).toHaveBeenCalledWith(1);
  });

  it('disables Next on the last page', () => {
    render(<TableToolbar pager={{ page: 2, pageSize: 25, total: 60, onPage: () => {} }} />);
    expect(screen.getByLabelText('Next page')).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test, verify it fails** — FAIL (module not found).

- [ ] **Step 3: Implement**

`TableToolbar.tsx`:

```tsx
import React, { useId } from 'react';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { Button } from './Button';
import { Select } from './Select';
import './TableToolbar.css';

export type TableToolbarProps = {
  search?: { value: string; onChange: (v: string) => void; placeholder?: string };
  filters?: React.ReactNode;
  pager?: {
    page: number; pageSize: number; total: number;
    onPage: (p: number) => void; onPageSize?: (n: number) => void;
    pageSizes?: number[];
  };
  className?: string;
};

export function TableToolbar({ search, filters, pager, className = '' }: TableToolbarProps) {
  const searchId = useId();
  const totalPages = pager ? Math.max(1, Math.ceil(pager.total / pager.pageSize)) : 1;
  const safePage = pager ? Math.min(pager.page, totalPages - 1) : 0;
  const start = pager && pager.total > 0 ? safePage * pager.pageSize + 1 : 0;
  const end = pager ? Math.min(safePage * pager.pageSize + pager.pageSize, pager.total) : 0;

  return (
    <div className={`wl-table-toolbar ${className}`.trim()}>
      <div className="wl-table-toolbar__left">
        {search && (
          <label className="wl-table-toolbar__search" htmlFor={searchId}>
            <Search size={16} aria-hidden="true" />
            <input
              id={searchId}
              type="search"
              value={search.value}
              placeholder={search.placeholder ?? 'Search…'}
              onChange={(e) => search.onChange(e.target.value)}
            />
          </label>
        )}
        {filters}
      </div>
      {pager && (
        <div className="wl-table-toolbar__pager">
          <span className="wl-table-toolbar__pageinfo">{start}-{end} of {pager.total}</span>
          {pager.onPageSize && (
            <Select
              variant="inline"
              label="Rows per page"
              value={String(pager.pageSize)}
              onChange={(v) => pager.onPageSize!(Number(v))}
              options={(pager.pageSizes ?? [10, 25, 50, 100]).map((s) => ({ value: String(s), label: `${s} / page` }))}
            />
          )}
          <Button type="button" variant="ghost" iconOnly aria-label="Previous page"
            disabled={safePage === 0} onClick={() => pager.onPage(Math.max(0, safePage - 1))}>
            <ChevronLeft size={16} aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" iconOnly aria-label="Next page"
            disabled={safePage >= totalPages - 1} onClick={() => pager.onPage(Math.min(totalPages - 1, safePage + 1))}>
            <ChevronRight size={16} aria-hidden="true" />
          </Button>
        </div>
      )}
    </div>
  );
}
```

`TableToolbar.css` (mirrors `wl-positions__toolbar`):

```css
.wl-table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--gap-3);
  margin-bottom: var(--gap-3);
}
.wl-table-toolbar__left {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  min-width: 0;
  flex-wrap: wrap;
}
.wl-table-toolbar__search {
  display: flex;
  align-items: center;
  gap: var(--gap-1);
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  padding: var(--input-padding-y) var(--input-padding-x);
  color: var(--ink-2);
}
.wl-table-toolbar__search input {
  border: 0;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  outline: none;
  min-width: 0;
}
.wl-table-toolbar__search input::placeholder { color: var(--ink-2); }
.wl-table-toolbar__pager {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
}
.wl-table-toolbar__pageinfo {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
@media (max-width: 720px) {
  .wl-table-toolbar { flex-direction: column; align-items: stretch; }
  .wl-table-toolbar__pager { justify-content: flex-start; }
}
```

- [ ] **Step 4: Run test, verify pass** → PASS.
- [ ] **Step 5: Commit** — `git add src/components/TableToolbar.* && git commit -m "feat(ui): TableToolbar primitive (search + filters + pager)"`

---

### Task 3: `PanelGrid`

**Files:**
- Create: `frontend/src/components/PanelGrid.tsx`, `frontend/src/components/PanelGrid.css`
- Test: `frontend/src/components/PanelGrid.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PanelGrid } from './PanelGrid';

describe('PanelGrid', () => {
  it('renders children inside the grid', () => {
    render(<PanelGrid><div>panel-a</div><div>panel-b</div></PanelGrid>);
    expect(screen.getByText('panel-a')).toBeInTheDocument();
    expect(screen.getByText('panel-b')).toBeInTheDocument();
  });

  it('sets the column count from the columns prop', () => {
    const { container } = render(<PanelGrid columns={2}><div /></PanelGrid>);
    const grid = container.querySelector('.wl-panel-grid') as HTMLElement;
    // jsdom custom-property reads are unreliable via getPropertyValue; assert on
    // the serialized inline style instead.
    expect(grid.getAttribute('style')).toContain('--panel-cols: 2');
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`PanelGrid.tsx`:

```tsx
import React from 'react';
import './PanelGrid.css';

type Props = {
  columns?: 1 | 2 | 3;
  children: React.ReactNode;
  className?: string;
};

export function PanelGrid({ columns = 1, children, className = '' }: Props) {
  return (
    <div className={`wl-panel-grid ${className}`.trim()} style={{ '--panel-cols': String(columns) } as React.CSSProperties}>
      {children}
    </div>
  );
}
```

`PanelGrid.css` (mirrors `wl-risk__grid`):

```css
.wl-panel-grid {
  display: grid;
  grid-template-columns: repeat(var(--panel-cols, 1), minmax(0, 1fr));
  gap: var(--gap-3);
  align-items: flex-start;
}
@media (max-width: 900px) {
  .wl-panel-grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): PanelGrid primitive (responsive Panel grid)"`

---

### Task 4: `SplitLayout`

**Files:**
- Create: `frontend/src/components/SplitLayout.tsx`, `frontend/src/components/SplitLayout.css`
- Test: `frontend/src/components/SplitLayout.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SplitLayout } from './SplitLayout';

describe('SplitLayout', () => {
  it('renders rail and workspace', () => {
    render(<SplitLayout rail={<div>rail-x</div>} railLabel="Items">workspace-x</SplitLayout>);
    expect(screen.getByText('rail-x')).toBeInTheDocument();
    expect(screen.getByText('workspace-x')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Items' })).toBeInTheDocument();
  });

  it('collapses the rail when the toggle is clicked', () => {
    const { container } = render(
      <SplitLayout collapsible rail={<div>rail-x</div>}>ws</SplitLayout>,
    );
    expect(container.querySelector('.wl-split--collapsed')).toBeNull();
    fireEvent.click(screen.getByLabelText('Collapse panel'));
    expect(container.querySelector('.wl-split--collapsed')).not.toBeNull();
  });

  it('applies a railWidth override via the --rail-width custom prop', () => {
    const { container } = render(
      <SplitLayout rail={<div />} railWidth="var(--rail-width)">ws</SplitLayout>,
    );
    const root = container.querySelector('.wl-split') as HTMLElement;
    expect(root.getAttribute('style')).toContain('--rail-width: var(--rail-width)');
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`SplitLayout.tsx`:

```tsx
import React, { useState } from 'react';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import './SplitLayout.css';

type Props = {
  rail: React.ReactNode;
  children: React.ReactNode;
  railWidth?: string;
  collapsible?: boolean;
  railLabel?: string;
  className?: string;
};

export function SplitLayout({ rail, children, railWidth, collapsible = false, railLabel = 'Items', className = '' }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const style = railWidth ? ({ '--rail-width': railWidth } as React.CSSProperties) : undefined;
  return (
    <div className={`wl-split${collapsed ? ' wl-split--collapsed' : ''} ${className}`.trim()} style={style}>
      <nav className="wl-split__rail" aria-label={railLabel} hidden={collapsed}>
        {rail}
      </nav>
      <section className="wl-split__workspace">
        {collapsible && (
          <button
            type="button"
            className="wl-split__toggle"
            aria-label={collapsed ? 'Expand panel' : 'Collapse panel'}
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? <PanelLeftOpen size={16} aria-hidden="true" /> : <PanelLeftClose size={16} aria-hidden="true" />}
          </button>
        )}
        {children}
      </section>
    </div>
  );
}
```

`SplitLayout.css` (mirrors `hedging-body`):

```css
.wl-split {
  display: grid;
  grid-template-columns: var(--rail-width) minmax(0, 1fr);
  gap: var(--gap-3);
  align-items: flex-start;
  min-height: 0;
}
.wl-split--collapsed { grid-template-columns: minmax(0, 1fr); }
.wl-split__rail {
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
  min-width: 0;
}
.wl-split__workspace { min-width: 0; position: relative; }
.wl-split__toggle {
  position: absolute;
  top: 0;
  left: calc(-1 * var(--gap-4));
  background: var(--paper);
  border: 1px solid var(--hairline-2);
  color: var(--ink-2);
  width: var(--control-height);
  height: var(--control-height);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.wl-split__toggle:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
@media (max-width: 900px) {
  .wl-split { grid-template-columns: 1fr; }
}
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): SplitLayout primitive (rail + workspace, collapsible)"`

---

### Task 5: `Stepper`

**Files:**
- Create: `frontend/src/components/Stepper.tsx`, `frontend/src/components/Stepper.css`
- Test: `frontend/src/components/Stepper.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Stepper } from './Stepper';

describe('Stepper', () => {
  it('renders each step label with a status class and marks the active step', () => {
    const { container } = render(<Stepper steps={[
      { label: 'Schema', status: 'done' },
      { label: 'Solve', status: 'active' },
      { label: 'Book', status: 'todo' },
    ]} />);
    expect(screen.getByText('Schema')).toBeInTheDocument();
    expect(container.querySelector('.wl-stepper__step--done')).not.toBeNull();
    expect(container.querySelector('.wl-stepper__step--active')).not.toBeNull();
    expect(screen.getByText('Solve').closest('.wl-stepper__step'))
      .toHaveAttribute('aria-current', 'step');
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`Stepper.tsx`:

```tsx
import React from 'react';
import './Stepper.css';

export type Step = { label: string; status: 'done' | 'active' | 'todo' };

type Props = { steps: Step[]; className?: string };

export function Stepper({ steps, className = '' }: Props) {
  return (
    <ol className={`wl-stepper ${className}`.trim()}>
      {steps.map((s, i) => (
        <li
          key={`${s.label}-${i}`}
          className={`wl-stepper__step wl-stepper__step--${s.status}`}
          aria-current={s.status === 'active' ? 'step' : undefined}
        >
          <span className="wl-stepper__dot" aria-hidden="true" />
          <span className="wl-stepper__label">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}
```

`Stepper.css`:

```css
.wl-stepper {
  display: flex;
  align-items: center;
  gap: var(--gap-3);
  list-style: none;
  margin: 0 0 var(--gap-3);
  padding: 0;
}
.wl-stepper__step {
  display: inline-flex;
  align-items: center;
  gap: var(--gap-1);
  font-family: var(--font-ui);
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
.wl-stepper__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 1px solid var(--hairline-2);
  background: var(--paper-2);
}
.wl-stepper__step--done .wl-stepper__dot { background: var(--pos); border-color: var(--pos); }
.wl-stepper__step--active { color: var(--ink); }
.wl-stepper__step--active .wl-stepper__dot { background: var(--info); border-color: var(--info); }
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): Stepper primitive (wizard progress)"`

---

## Phase 2 — Templates (TDD)

### Task 6: `PageScaffold` (private base)

**Files:**
- Create: `frontend/src/components/templates/PageScaffold.tsx`, `.../PageScaffold.css`
- Test: `frontend/src/components/templates/PageScaffold.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageScaffold } from './PageScaffold';

describe('PageScaffold', () => {
  it('renders the header title, chips, actions, feedback, and body', () => {
    render(
      <PageScaffold title="RISK" chips={['c1']} actions={<button>Run</button>} feedback={<div>saved</div>}>
        <div>body</div>
      </PageScaffold>,
    );
    expect(screen.getByText('RISK')).toBeInTheDocument();
    expect(screen.getByText('c1')).toBeInTheDocument();
    expect(screen.getByText('Run')).toBeInTheDocument();
    expect(screen.getByText('saved')).toBeInTheDocument();
    expect(screen.getByText('body')).toBeInTheDocument();
  });

  it('omits the feedback region when no feedback is given', () => {
    const { container } = render(<PageScaffold title="X"><div /></PageScaffold>);
    expect(container.querySelector('.wl-scaffold__feedback')).toBeNull();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`PageScaffold.tsx`:

```tsx
import React from 'react';
import { PageHeader } from '../PageHeader';
import './PageScaffold.css';

export type PageScaffoldProps = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

export function PageScaffold({ title, chips = [], actions, feedback, children, className = '' }: PageScaffoldProps) {
  return (
    <div className={`wl-scaffold ${className}`.trim()}>
      <PageHeader title={title} chips={chips} action={actions} />
      {feedback && (
        <div className="wl-scaffold__feedback" role="status" aria-live="polite">{feedback}</div>
      )}
      <div className="wl-scaffold__body">{children}</div>
    </div>
  );
}
```

`PageScaffold.css`:

```css
.wl-scaffold { display: flex; flex-direction: column; }
.wl-scaffold__feedback {
  font-size: var(--type-small-size);
  color: var(--ink-2);
  margin-bottom: var(--gap-2);
}
.wl-scaffold__body { display: flex; flex-direction: column; }
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): PageScaffold template base"`

---

### Task 7: `DataTablePage` (A)

**Files:**
- Create: `frontend/src/components/templates/DataTablePage.tsx`, `.../DataTablePage.css`
- Test: `frontend/src/components/templates/DataTablePage.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DataTablePage } from './DataTablePage';
import type { Column } from '../Table';

type Row = { id: number; name: string };
const columns: Column<Row>[] = [{ key: 'name', header: 'NAME' }];

describe('DataTablePage', () => {
  it('renders header, metrics, table rows, and overlays', () => {
    render(
      <DataTablePage<Row>
        title="POSITIONS"
        metrics={[{ label: 'NAV', value: '10' }]}
        table={{ columns, rows: [{ id: 1, name: 'alpha' }], rowKey: (r) => r.id }}
        overlays={<div>my-modal</div>}
      />,
    );
    expect(screen.getByText('POSITIONS')).toBeInTheDocument();
    expect(screen.getByText('NAV')).toBeInTheDocument();
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('my-modal')).toBeInTheDocument();
  });

  it('shows the empty slot when there are no rows', () => {
    render(
      <DataTablePage<Row>
        title="X" table={{ columns, rows: [], rowKey: (r) => r.id }}
        empty={<div>nothing here</div>}
      />,
    );
    expect(screen.getByText('nothing here')).toBeInTheDocument();
  });

  it('renders two metric rows when metrics is an array of arrays', () => {
    const { container } = render(
      <DataTablePage<Row>
        title="X"
        metrics={[[{ label: 'A', value: '1' }], [{ label: 'B', value: '2' }]]}
        table={{ columns, rows: [{ id: 1, name: 'a' }], rowKey: (r) => r.id }}
      />,
    );
    expect(container.querySelectorAll('.wl-metric-row')).toHaveLength(2);
  });

  it('renders the body slot for list pages that have no Table', () => {
    render(<DataTablePage title="REPORTS" body={<div>timeline-list</div>} />);
    expect(screen.getByText('timeline-list')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`DataTablePage.tsx`:

```tsx
import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { TableToolbar, type TableToolbarProps } from '../TableToolbar';
import { Table, type Column } from '../Table';
import './DataTablePage.css';

type Props<T> = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  toolbar?: TableToolbarProps;
  // Provide EITHER `table` (the common case, renders the Table primitive) OR
  // `body` (escape hatch for list pages whose body is not a <Table>, e.g.
  // Reports' <ReportTimeline>). Exactly one is expected.
  table?: {
    columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string | number;
    selectedKey?: string | number | null; onRowClick?: (r: T) => void;
    className?: string;
  };
  body?: React.ReactNode;
  mobileCards?: React.ReactNode;
  empty?: React.ReactNode;
  overlays?: React.ReactNode;
};

export function DataTablePage<T>({
  title, chips, actions, feedback, metrics, toolbar, table, body, mobileCards, empty, overlays,
}: Props<T>) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <MetricRows metrics={metrics} />
      {toolbar && <TableToolbar {...toolbar} />}
      <div className="wl-data-table-page__main">
        {table && (
          <Table<T>
            columns={table.columns}
            rows={table.rows}
            rowKey={table.rowKey}
            selectedKey={table.selectedKey}
            onRowClick={table.onRowClick}
            className={table.className}
          />
        )}
        {body}
        {mobileCards}
        {table && table.rows.length === 0 && empty}
      </div>
      {overlays}
    </PageScaffold>
  );
}
```

`DataTablePage.css`:

```css
.wl-data-table-page__main { display: flex; flex-direction: column; gap: var(--gap-3); }
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): DataTablePage template (A)"`

---

### Task 8: `AnalyticsDashboard` (B)

**Files:**
- Create: `frontend/src/components/templates/AnalyticsDashboard.tsx`, `.../AnalyticsDashboard.css`
- Test: `frontend/src/components/templates/AnalyticsDashboard.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AnalyticsDashboard } from './AnalyticsDashboard';

describe('AnalyticsDashboard', () => {
  it('renders metrics, controls, and panels', () => {
    render(
      <AnalyticsDashboard
        title="RISK"
        metrics={[{ label: 'D', value: '1' }]}
        controls={<div>controls-strip</div>}
        columns={1}
        panels={<div>panel-x</div>}
      />,
    );
    expect(screen.getByText('RISK')).toBeInTheDocument();
    expect(screen.getByText('controls-strip')).toBeInTheDocument();
    expect(screen.getByText('panel-x')).toBeInTheDocument();
  });

  it('renders the state slot instead of panels when given', () => {
    render(<AnalyticsDashboard title="X" panels={<div>p</div>} state={<div>empty-state</div>} />);
    expect(screen.getByText('empty-state')).toBeInTheDocument();
    expect(screen.queryByText('p')).toBeNull();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`AnalyticsDashboard.tsx`:

```tsx
import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { PanelGrid } from '../PanelGrid';
import './AnalyticsDashboard.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  controls?: React.ReactNode;
  panels: React.ReactNode;
  columns?: 1 | 2 | 3;
  state?: React.ReactNode;
};

export function AnalyticsDashboard({
  title, chips, actions, feedback, metrics, controls, panels, columns = 1, state,
}: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <MetricRows metrics={metrics} />
      {controls && <div className="wl-analytics__controls">{controls}</div>}
      {state ?? <PanelGrid columns={columns}>{panels}</PanelGrid>}
    </PageScaffold>
  );
}
```

`AnalyticsDashboard.css`:

```css
.wl-analytics__controls { margin-bottom: var(--gap-3); }
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): AnalyticsDashboard template (B)"`

---

### Task 9: `MasterDetailPage` (C)

**Files:**
- Create: `frontend/src/components/templates/MasterDetailPage.tsx`, `.../MasterDetailPage.css`
- Test: `frontend/src/components/templates/MasterDetailPage.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MasterDetailPage } from './MasterDetailPage';

describe('MasterDetailPage', () => {
  it('renders header, optional metrics, rail, and detail children', () => {
    render(
      <MasterDetailPage
        title="SKILLS"
        metrics={[{ label: 'WORKFLOWS', value: '3' }]}
        rail={<div>rail-list</div>}
        railLabel="Skills"
      >
        <div>detail-editor</div>
      </MasterDetailPage>,
    );
    expect(screen.getByText('SKILLS')).toBeInTheDocument();
    expect(screen.getByText('WORKFLOWS')).toBeInTheDocument();
    expect(screen.getByText('rail-list')).toBeInTheDocument();
    expect(screen.getByText('detail-editor')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`MasterDetailPage.tsx`:

```tsx
import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { SplitLayout } from '../SplitLayout';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  rail: React.ReactNode;
  children: React.ReactNode;
  railWidth?: string;
  collapsible?: boolean;
  railLabel?: string;
};

export function MasterDetailPage({
  title, chips, actions, feedback, metrics, rail, children, railWidth, collapsible, railLabel,
}: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <MetricRows metrics={metrics} />
      <SplitLayout rail={rail} railWidth={railWidth} collapsible={collapsible} railLabel={railLabel}>
        {children}
      </SplitLayout>
    </PageScaffold>
  );
}
```

(No new CSS file — composition only.)

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): MasterDetailPage template (C)"`

---

### Task 10: `WorkbenchPage` (D)

**Files:**
- Create: `frontend/src/components/templates/WorkbenchPage.tsx`, `.../WorkbenchPage.css`
- Test: `frontend/src/components/templates/WorkbenchPage.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WorkbenchPage } from './WorkbenchPage';

describe('WorkbenchPage', () => {
  it('renders config (rail), metrics, and results', () => {
    render(
      <WorkbenchPage
        title="SCENARIO TEST"
        config={<div>config-panel</div>}
        metrics={[{ label: 'RUNS', value: '5' }]}
        results={<div>runs-list</div>}
      />,
    );
    expect(screen.getByText('SCENARIO TEST')).toBeInTheDocument();
    expect(screen.getByText('config-panel')).toBeInTheDocument();
    expect(screen.getByText('RUNS')).toBeInTheDocument();
    expect(screen.getByText('runs-list')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`WorkbenchPage.tsx`:

```tsx
import React from 'react';
import { PageScaffold } from './PageScaffold';
import { MetricRows, type Metric } from '../MetricRow';
import { SplitLayout } from '../SplitLayout';
import './WorkbenchPage.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  config: React.ReactNode;
  metrics?: Metric[] | Metric[][];
  results: React.ReactNode;
  railWidth?: string;
};

export function WorkbenchPage({ title, chips, actions, feedback, config, metrics, results, railWidth }: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      <SplitLayout rail={config} railWidth={railWidth} railLabel="Configuration">
        <div className="wl-workbench__results">
          <MetricRows metrics={metrics} />
          {results}
        </div>
      </SplitLayout>
    </PageScaffold>
  );
}
```

`WorkbenchPage.css`:

```css
.wl-workbench__results { display: flex; flex-direction: column; gap: var(--gap-3); min-width: 0; }
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): WorkbenchPage template (D)"`

---

### Task 11: `ConversationalWorkspace` (E)

**Files:**
- Create: `frontend/src/components/templates/ConversationalWorkspace.tsx`, `.../ConversationalWorkspace.css`
- Test: `frontend/src/components/templates/ConversationalWorkspace.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConversationalWorkspace } from './ConversationalWorkspace';

describe('ConversationalWorkspace', () => {
  it('renders rail, messages, composer, and context pane (3-column)', () => {
    const { container } = render(
      <ConversationalWorkspace
        title="AGENT DESK"
        rail={<div>thread-rail</div>}
        messages={<div>thread</div>}
        composer={<div>composer</div>}
        contextPane={<div>ctx</div>}
      />,
    );
    expect(screen.getByText('thread-rail')).toBeInTheDocument();
    expect(screen.getByText('thread')).toBeInTheDocument();
    expect(screen.getByText('composer')).toBeInTheDocument();
    expect(screen.getByText('ctx')).toBeInTheDocument();
    // both side regions present → 3-column modifier
    expect(container.querySelector('.wl-conversation--rail.wl-conversation--context')).not.toBeNull();
  });

  it('omits the rail and context regions when not provided', () => {
    const { container } = render(
      <ConversationalWorkspace title="X" messages={<div>m</div>} composer={<div>c</div>} />,
    );
    expect(container.querySelector('.wl-conversation__rail')).toBeNull();
    expect(container.querySelector('.wl-conversation__context')).toBeNull();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`ConversationalWorkspace.tsx`:

```tsx
import React from 'react';
import { PageScaffold } from './PageScaffold';
import './ConversationalWorkspace.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  rail?: React.ReactNode;        // left column (e.g. AgentDesk ThreadRail)
  messages: React.ReactNode;
  composer: React.ReactNode;
  contextPane?: React.ReactNode; // right column (e.g. AgentDesk AssetsPane)
};

export function ConversationalWorkspace({ title, chips, actions, rail, messages, composer, contextPane }: Props) {
  const cls = [
    'wl-conversation',
    rail ? 'wl-conversation--rail' : '',
    contextPane ? 'wl-conversation--context' : '',
  ].filter(Boolean).join(' ');
  return (
    <PageScaffold title={title} chips={chips} actions={actions}>
      <div className={cls}>
        {rail && <nav className="wl-conversation__rail" aria-label="Threads">{rail}</nav>}
        <section className="wl-conversation__chat">
          <div className="wl-conversation__messages">{messages}</div>
          <div className="wl-conversation__composer">{composer}</div>
        </section>
        {contextPane && <aside className="wl-conversation__context">{contextPane}</aside>}
      </div>
    </PageScaffold>
  );
}
```

`ConversationalWorkspace.css` (mirrors `wl-agent-desk__split` — up to three columns;
the rail/context columns appear only via the `--rail`/`--context` modifiers):

```css
.wl-conversation {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--gap-3);
  align-items: stretch;
  height: 70vh;
  min-height: 520px;
}
.wl-conversation--rail { grid-template-columns: minmax(220px, 280px) minmax(0, 1fr); }
.wl-conversation--context { grid-template-columns: minmax(0, 1fr) minmax(260px, 320px); }
.wl-conversation--rail.wl-conversation--context {
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr) minmax(260px, 320px);
}
.wl-conversation__rail { min-width: 0; overflow-y: auto; }
.wl-conversation__chat { display: flex; flex-direction: column; min-width: 0; gap: var(--gap-2); }
.wl-conversation__messages { flex: 1; min-height: 0; overflow-y: auto; }
.wl-conversation__context { min-width: 0; overflow-y: auto; }
@media (max-width: 900px) {
  .wl-conversation,
  .wl-conversation--rail,
  .wl-conversation--context,
  .wl-conversation--rail.wl-conversation--context { grid-template-columns: 1fr; height: auto; }
}
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): ConversationalWorkspace template (E)"`

---

### Task 12: `WizardPage` (F) + templates barrel

**Files:**
- Create: `frontend/src/components/templates/WizardPage.tsx`, `.../WizardPage.css`, `.../index.ts`
- Test: `frontend/src/components/templates/WizardPage.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WizardPage } from './WizardPage';

describe('WizardPage', () => {
  it('renders steps, body, and footer', () => {
    render(
      <WizardPage
        title="TRY SOLVE"
        steps={[{ label: 'Schema', status: 'done' }, { label: 'Solve', status: 'active' }]}
        footer={<button>Submit</button>}
      >
        <div>step-body</div>
      </WizardPage>,
    );
    expect(screen.getByText('Schema')).toBeInTheDocument();
    expect(screen.getByText('step-body')).toBeInTheDocument();
    expect(screen.getByText('Submit')).toBeInTheDocument();
  });

  it('renders a tabs slot instead of steps when given', () => {
    render(<WizardPage title="X" tabs={<div>tabstrip</div>}><div>b</div></WizardPage>);
    expect(screen.getByText('tabstrip')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

`WizardPage.tsx`:

```tsx
import React from 'react';
import { PageScaffold } from './PageScaffold';
import { Stepper, type Step } from '../Stepper';
import './WizardPage.css';

type Props = {
  title: string;
  chips?: string[];
  actions?: React.ReactNode;
  feedback?: React.ReactNode;
  steps?: Step[];
  tabs?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
};

export function WizardPage({ title, chips, actions, feedback, steps, tabs, children, footer }: Props) {
  return (
    <PageScaffold title={title} chips={chips} actions={actions} feedback={feedback}>
      {steps && <Stepper steps={steps} />}
      {tabs}
      <div className="wl-wizard__body">{children}</div>
      {footer && <div className="wl-wizard__footer">{footer}</div>}
    </PageScaffold>
  );
}
```

`WizardPage.css`:

```css
.wl-wizard__body { min-width: 0; }
.wl-wizard__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--gap-2);
  margin-top: var(--gap-3);
}
```

`index.ts`:

```ts
export { DataTablePage } from './DataTablePage';
export { AnalyticsDashboard } from './AnalyticsDashboard';
export { MasterDetailPage } from './MasterDetailPage';
export { WorkbenchPage } from './WorkbenchPage';
export { ConversationalWorkspace } from './ConversationalWorkspace';
export { WizardPage } from './WizardPage';
```

- [ ] **Step 4: Run, verify pass** — `npx vitest run src/components/templates` → all PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(ui): WizardPage template (F) + templates barrel"`

---

## Phase 3 — Page migrations

**Migration recipe (apply to every page task):**

1. Import the template (and `Metric` type if metrics are used) from `../components/templates` / `../components/MetricRow`.
2. Replace the page's top-level `<>…</>` fragment with the template element.
3. Map current regions to slots per the page's note below.
4. Move the per-tile `<Tile>` props into `metrics` arrays; move search/toggle/pager markup into `toolbar`; move panel children into `panels`; move list/rail markup into `rail`; modals into `overlays`.
5. Delete the now-dead region rules from the page's `.css` (the `__tiles`, `__toolbar`, `__grid`, `__layout`/`__split`, `__main` blocks the template now owns). Keep page-specific styling (cell renderers, badges, detail dialogs, bespoke inner layout). **Before deleting any region class, grep the whole `src/` for it** — `grep -rl "<class-name>" src/` — and if another file still uses it (cross-page CSS borrowing), migrate that file's usage to the primitive FIRST or defer the deletion. Known case: `Booking.live.tsx` borrows `wl-positions__tiles` from `Positions.css` (see Task 13 ⚠ note).
6. Run the page's existing `*.test.tsx` and `*.live.test.tsx`. Where an assertion targets a removed region class (e.g. `wl-positions__tiles`), update the selector to the template's region class (`wl-metric-row`, `wl-table-toolbar`, `wl-panel-grid`, `wl-split`) **without weakening the behavior asserted**.
7. Verify both themes + compact density manually (or via existing snapshot tests); run the phantom-token sweep (Task 0 Step 2) — must be empty.
8. Commit per page.

> Each task below is: **(a)** transform the page, **(b)** delete dead CSS, **(c)** `npx vitest run src/routes/<Page>*` green, **(d)** phantom-token sweep empty, **(e)** commit `refactor(ui): migrate <Page> to <Template> template`.

### Task 13 — Positions → DataTablePage (A)
> ⚠ **Ordering dependency:** `Booking.live.tsx:409` borrows `wl-positions__tiles`
> from `Positions.css`. Do **not** delete the `wl-positions__tiles*` rules here
> until Task 32 (Booking) has converted its tile row to `<MetricRow>`. Either run
> Task 32 before this CSS deletion, or keep the `__tiles` rules in `Positions.css`
> until then and delete in Task 32. The recipe's step-5 grep guard enforces this.

- `metrics`: `[[risk tiles…], [position-summary tiles…]]` (two rows). `actions`: existing filter selects + Download/Import/Run buttons. `toolbar`: `search` = the search input state; `filters` = the All/Live toggle group; `pager` = page/pageSize/total wiring. `table`: existing columns/rows/selected/openDetail. `mobileCards`: existing `PositionMobileCard` list (page keeps `useMediaQuery`, passes the list only when `showMobileRows`). `empty`: existing `<Empty>`. `overlays`: BOTH the Import `<Modal>` and the Position-detail `<Modal>` in a fragment.
- Delete from `Positions.css`: `__tiles*`, `__toolbar*`, `__main`, and their media queries. Keep cell/badge/detail/ticket/mobile-card styles.

### Task 14 — Portfolios → DataTablePage (A)
- `metrics`: the 4 tiles. `table`: existing. Editors/detail → `overlays`. Delete `__tiles`/`__main`/`__toolbar` region rules.

### Task 15 — Tasks → DataTablePage (A)
- No metrics. `table`: existing Tasks table. Delete the page's table-wrapper region rule if present.

### Task 16 — Reports → DataTablePage (A, `body` slot)
- **Resolved (verified):** `Reports.tsx` does not use `<Table>` — it renders
  `<ReportTimeline>` (a bespoke component) and a `<ReportReader>` modal. Use
  `DataTablePage` with the `body` escape slot, NOT `table`:
  - `title="REPORTS"`, `chips` as today, no `metrics`.
  - `body`: the existing `<ReportTimeline>` element.
  - `overlays`: the `<ReportReader>` modal.
  Do not invent a fake single-column `Table`. Delete any outer page-stack rule the
  scaffold now owns; keep `ReportTimeline`/`ReportReader` inner styles.

### Task 17 — PricingParameters → MasterDetailPage (C)
- `metrics`: omit at page level (tiles live in `ProfileLibrary` detail). `rail`: `ProfileLibrary`'s profile-list `<aside>`. `children`: `ProfileLibrary`'s `<section>` detail (tiles + toolbar + table). Migrate `ProfileLibrary.tsx` so its outer two-pane wrapper becomes the C body: pass its list as `rail` and detail as `children` from `PricingParameters.tsx`. Delete `wl-pricing-params__list`/`__detail` layout rules (keep their inner styles).

### Task 18 — Risk → AnalyticsDashboard (B)
- `actions`: portfolio/profile/engine selects + Run button. `metrics`: omit (Risk has none). `columns={1}`. `panels`: `<GreeksSummary/> <PnlAttribution/> <ScenarioGrid/>`. `state`: the existing no-portfolios/running/no-run `<Empty>` chain. `feedback`: existing feedback banner. Delete `wl-risk__grid` (+ its media query); keep `wl-risk__section`, `wl-risk__actions`, `wl-risk__feedback`.

### Task 19 — GreeksLandscape → AnalyticsDashboard (B)
- `controls`: the run-config selects strip (`wl-landscape__controls`). `columns={1}`. `panels`: the chart panel. Chart `COLORS` JS literals untouched (out of scope). Delete the page's outer vertical-stack region rule; keep `wl-landscape__controls` inner styles and chart styles.

### Task 20 — HedgeStrategy body → MetricRow + PanelGrid (B-body, no route template)
- Inside `HedgeStrategy.tsx`, replace the bespoke `hedge-strategy__kpis` tile row with `<MetricRow metrics={…} />` and wrap its panels in `<PanelGrid columns={…}>`. Keep feature BEM names elsewhere. This is NOT wrapped in a route template (it renders inside Hedging's workspace). Delete the replaced `__kpis`/panel-grid region rules.

### Task 21 — Skills → MasterDetailPage (C)
- `metrics`: the 4 tiles (WORKFLOWS/REFERENCES/META/LINT ERRORS). `rail`: the skill list/tree. `children`: `SkillsWorkflowForm` editor / reference viewer. `railWidth="var(--rail-width)"` or its existing tree width. Delete `wl-skills__layout` (+ media query); keep tree/editor inner styles.

### Task 22 — EngineConfigs → MasterDetailPage (C)
- Combined component — edit in place. `rail`: config list. `children`: the editor (`wl-engine-configs__grid` editor body). Delete `wl-engine-configs__grid` outer split rule; keep inner form styles.

### Task 23 — Instruments → MasterDetailPage (C, composite)
- `rail`: the custom tab strip (`wl-instruments__tabs` buttons). `collapsible={false}`. `children`: the active tab body (`RegistryTab`/`InstrumentsAllowedHedges`/`InstrumentsMarketData`/`InstrumentsAssumptions`). Inner tab layouts keep bespoke CSS. Delete only the outer page split/stack rule that the C split now owns.

### Task 24 — Hedging → MasterDetailPage (C)
- `rail`: the underlying cards (`hedging-rail`). `children`: `HedgeStrategyLive` or the "select an underlying" placeholder. `railWidth`: keep its 220px (→ `var(--rail-width)`). Delete `hedging-body`/`hedging-rail` grid rules (keep card styles). Add a `PageHeader` title `HEDGING` if absent — **check first**; Hedging currently has none in `Hedging.tsx` (it delegates). If adding a header changes layout materially, treat like Tracing (intentional, note in commit).

### Task 25 — Tracing → MasterDetailPage (C)
- **Intentional:** add `PageHeader` title `TRACING` (none today). `rail`: trace list (`wl-tracing__list`). `children`: trace tree (`wl-tracing__tree`). Delete the outer two-pane grid rule; keep list/tree inner styles. Commit message must note the header addition.

### Task 26 — RfqApproval → MasterDetailPage (C)
- `rail`: the RFQ inbox. `children`: the RFQ detail/audit. Delete outer split rule; keep inbox/detail inner styles.

### Task 27 — TrySolve → MasterDetailPage (C)
- `rail`: the request-queue `Panel`. `children`: the solve workspace. The inline `wl-try-solve__step` strip MAY become `<Stepper>` inside the workspace (optional; if converted, map done/pending → done/active/todo). Delete the outer queue+workspace split rule; keep workspace inner styles.

### Task 28 — ScenarioTest → WorkbenchPage (D)
- Combined component — edit in place. `config`: the scenario-picker panel (Predefined/Custom/Sets sections). `metrics`: omit. `results`: the run-history panel + `RunDetail`. Delete `wl-scenario-test__body` split rule; keep panel/section inner styles.

### Task 29 — Backtest → WorkbenchPage (D)
- Combined component — edit in place. `config`: the config panel. `results`: the `RunReport` (its `wl-backtest__kpis` KPI cards stay inside `results` — converting them to `MetricRow` is OPTIONAL; if done, lift them to `metrics`). Delete `wl-backtest__body`/outer split rule; keep panel/report inner styles.

### Task 30 — AgentDesk → ConversationalWorkspace (E, 3-column)
- AgentDesk is **three columns** (`ThreadRail | chat | AssetsPane`, see
  `AgentDesk.css` `grid-template-columns: minmax(220px,280px) minmax(0,1fr) minmax(260px,320px)`).
  Map: `rail` = `<ThreadRail …>`; `messages` = the `wl-agent-desk__messages` content;
  `composer` = the `wl-agent-desk__composer` content; `contextPane` = `<AssetsPane …>`.
- Delete `wl-agent-desk__split`/`__chat`/`__messages`/`__composer`/`__assets`/`__threads`
  layout rules the template now owns (incl. their media queries); keep
  bubble/composer/asset-card inner styles.

### Task 31 — ClientRfq → WizardPage (F, tabs variant)
- `tabs`: the NL/Structured `<Tabs>` strip. `children`: the active tab's intake form. `footer`: submit actions if separated. No `steps`. Delete the outer page stack rule if the template now owns it; keep form styles.

### Task 32 — Booking → WizardPage (F)
- Combined `Booking.live.tsx` — edit render in place. No step model exists today →
  **omit `steps`**; pass the booking form as `children` and submit actions as `footer`.
- **Convert its borrowed tile row:** `Booking.live.tsx:409` uses
  `<div className="wl-positions__tiles">` — replace with `<MetricRow metrics={…} />`.
  This unblocks Task 13's deletion of the `wl-positions__tiles` CSS (see Task 13 ⚠).
  If Task 13 already ran and kept the rule, delete the now-orphaned
  `wl-positions__tiles*` rules from `Positions.css` here.
- Delete any dead outer layout rule; keep form styles.

---

## Phase 4 — Final verification

### Task 33: Full suite + sweeps + theme check

- [ ] **Step 1:** Run the whole frontend suite — `npx vitest run` → all green.
- [ ] **Step 2:** Phantom-token sweep (Task 0 Step 2) → empty.
- [ ] **Step 3:** Literal-color/spacing scan on new files — `grep -rnE "#[0-9a-fA-F]{3,6}|rgba?\(" src/components/MetricRow.css src/components/TableToolbar.css src/components/PanelGrid.css src/components/SplitLayout.css src/components/Stepper.css src/components/templates/*.css` → empty.
- [ ] **Step 4:** Typecheck — `npx tsc --noEmit` → clean.
- [ ] **Step 5:** Manual: open light + dark + compact; spot-check one page per family (Positions, Risk, Skills, ScenarioTest, AgentDesk, ClientRfq).
- [ ] **Step 6:** Commit any assertion/CSS fixups — `git commit -m "test(ui): align suite with unified templates"`.

---

## Self-review notes (author)

- **Spec coverage:** all 6 templates (Tasks 7–12) + 5 primitives (Tasks 1–5) + every page in the spec's mapping (Tasks 13–32) + the `--rail-width` token (Task 0) + JS-color out-of-scope honored (Task 19/29). Tracing header addition (Task 25) and combined-component in-place edits (Tasks 22/28/29/32) match spec decisions.
- **Type consistency:** `Metric` (from `MetricRow`) and the `MetricRows` array-or-array-of-arrays helper are reused identically across A/B/C/D templates; `TableToolbarProps`, `Step`, `Column<T>` names match their defining tasks.
- **Open per-page reads:** Tasks 16 (Reports), 24 (Hedging header), 32 (Booking steps) require reading the page before transforming — flagged inline rather than guessed.

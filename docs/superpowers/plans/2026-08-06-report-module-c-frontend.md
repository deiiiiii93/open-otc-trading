# Report Module — Sub-project C: Renderer, Reports Page & Legacy Retirement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `ReportDocument` natively and well, replace the JSON-dump reader with a real Reports page plus a Templates tab, and delete the legacy hardcoded HTML/XLSX writer.

**Architecture:** A `BlockRenderer` dispatches on the block's `render` key to existing primitives — `MetricRow`/`Tile`, `Table`, `ChartAsset`, `Panel`+`Badge` — with one genuinely new component, `Waterfall`. Non-`ok` block statuses render as distinct, deliberate states. The page uses `MasterDetailPage`: report list in the rail, document in the detail pane.

**Tech Stack:** React 19, TypeScript, Vite, Radix UI, recharts, vitest.

**Spec:** `docs/superpowers/specs/2026-08-06-report-module-redesign-design.md` §5.7, §5.10, §5.11
**Depends on:** `2026-08-06-report-module-b4-tools-and-api.md` Task 4 complete — the REST surface is live.

## Global Constraints

- **Read `frontend/CLAUDE.md` and `frontend/UI_STYLE_GUIDE.md` before touching any `.css`.** Token-only styling is non-negotiable.
- **Never hardcode colors, spacing, fonts, or motion.** Use `var(--token)` values from `src/tokens/`. Never invent a token name that is not defined there.
- **Never branch on `data-theme` or `data-density`** and never add `[data-theme="dark"]` overrides in a component. Theme and density are automatic.
- **Verify in both themes and compact density** before claiming any UI task done.
- **One co-located `.css` per component, BEM names, `wl-` prefix for primitives.** Reuse `Button`, `Input`, `Modal`, `Tile`, `Badge`, `Chip`, `PageHeader`, `Table`, `Panel` before adding new styles.
- **Theme raw `<input>`/`<select>`** or use the `wl-field`/`wl-input` primitives — unstyled controls default to a white background that breaks dark mode.
- **The vitest suite is flaky under load.** `main` alone varies 12→18 failures run to run. Before blaming a branch, compare failing-file sets against a same-machine `main` run.
- **Test commands:** `cd frontend && npm test` (vitest); type-check `cd frontend && npx tsc --noEmit`.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/components/GreeksByPosition.{tsx,css,test.tsx}` | Renamed from `PnlAttribution` — it renders Greeks, not attribution |
| `frontend/src/components/reports/Waterfall.{tsx,css}` | P&L attribution waterfall (recharts stacked bar) |
| `frontend/src/components/reports/BlockRenderer.{tsx,css}` | Dispatch on `render`; owns the `empty` / `unavailable` states |
| `frontend/src/components/reports/ReportDocumentView.{tsx,css}` | Sections, narrative, grounding flags, provenance drawer |
| `frontend/src/components/reports/TemplateEditor.{tsx,css}` | YAML editor with live validation |
| `frontend/src/routes/Reports.{tsx,live.tsx,css}` | Rewritten page with Reports / Templates tabs |
| `frontend/src/types.ts` | `ReportDocument`, `ReportSection`, `BlockResult`, `ReportTemplate` types |

---

### Task 1: Rename `PnlAttribution` → `GreeksByPosition`

**Files:**
- Rename: `frontend/src/components/PnlAttribution.tsx` → `GreeksByPosition.tsx`
- Rename: `frontend/src/components/PnlAttribution.css` → `GreeksByPosition.css`
- Rename: `frontend/src/components/PnlAttribution.test.tsx` → `GreeksByPosition.test.tsx`
- Modify: `frontend/src/components/RiskReportDialog.tsx`, `frontend/src/routes/Risk.tsx`

**Interfaces:**
- Produces: `GreeksByPosition` component, `AttributionPosition` type renamed to `PositionGreeksRow`

**Why:** the component renders `delta_cash`, `gamma_cash`, `vega`, `theta`, `rho`, `rho_q` by
position or underlying. There is no P&L decomposition in it. Task 2 adds a real attribution
waterfall; leaving a component called `PnlAttribution` next to it is a trap for the next agent.

- [ ] **Step 1: Rename the files with git so history follows**

```bash
cd frontend/src/components
git mv PnlAttribution.tsx GreeksByPosition.tsx
git mv PnlAttribution.css GreeksByPosition.css
git mv PnlAttribution.test.tsx GreeksByPosition.test.tsx
cd -
```

- [ ] **Step 2: Update the symbols inside the renamed files**

In `GreeksByPosition.tsx`:
- `import './PnlAttribution.css'` → `import './GreeksByPosition.css'`
- `export type AttributionPosition` → `export type PositionGreeksRow`
- `export function PnlAttribution(` → `export function GreeksByPosition(`
- Update the two internal uses of `AttributionPosition` in `greekValue`, `positionRow`, and
  `underlyingRows` signatures.

Leave the `wl-attr__*` BEM class names alone — renaming them would churn the stylesheet for no
behavioural gain, and `wl-attr` remains an accurate prefix for "attributes by position".

In `GreeksByPosition.test.tsx`, update the import and every `PnlAttribution` reference to
`GreeksByPosition`.

- [ ] **Step 3: Update the two callers**

In `frontend/src/components/RiskReportDialog.tsx` and `frontend/src/routes/Risk.tsx`, change
the import path and the JSX element name:

```tsx
import { GreeksByPosition } from './GreeksByPosition';   // or '../components/GreeksByPosition'
...
<GreeksByPosition positions={...} onPromoteToReport={...} />
```

- [ ] **Step 4: Verify nothing still references the old name**

Run: `grep -rn "PnlAttribution\|AttributionPosition" frontend/src`
Expected: no output.

- [ ] **Step 5: Type-check and test**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Run: `cd frontend && npm test -- GreeksByPosition RiskReportDialog`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src
git commit -m "refactor(frontend): rename PnlAttribution to GreeksByPosition

It renders a Greeks table, not a P&L decomposition. The real attribution
waterfall lands next; two components named for the same thing is a trap."
```

---

### Task 2: Report document types and the `Waterfall` component

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/reports/Waterfall.tsx`
- Create: `frontend/src/components/reports/Waterfall.css`
- Test: `frontend/src/components/reports/Waterfall.test.tsx`

**Interfaces:**
- Produces:
  - `BlockStatus`, `BlockResult`, `ReportBlock`, `ReportSection`, `ReportDocument`, `ReportTemplate` types
  - `Waterfall` component — props `{ data: WaterfallData }` where
    `WaterfallData = { buckets: Record<string, number>; explained: number; actual: number; residual: number; residual_ratio: number | null; residual_exceeds_threshold: boolean }`

- [ ] **Step 1: Add the types**

In `frontend/src/types.ts`, after the existing `ReportJob` type (line 846-856), add:

```typescript
export type BlockStatus = 'ok' | 'empty' | 'unavailable';

export type BlockResult = {
  status: BlockStatus;
  reason: string | null;
  data: Record<string, any>;
  provenance: Record<string, any>;
};

export type ReportBlock = {
  key: string;
  render: string;
  fields: string[] | null;
  result: BlockResult;
};

export type ReportSection = {
  id: string;
  title: string;
  blocks: ReportBlock[];
  narrative: string | null;
  narrative_error: string | null;
  grounding: { checked: boolean; flags: { token: number; offset: number }[]; grounded_count: number };
};

export type ReportDocument = {
  template: { slug: string; title: string; persona: string; version: number; spec: string; spec_sha256: string };
  params: { portfolio_id: number; compare_to_run_id: number | null };
  generated_at: string;
  sections: ReportSection[];
  provenance: Record<string, any>;
};

export type ReportTemplate = {
  slug: string;
  title: string;
  persona: string;
  description: string;
  source: 'seed' | 'user' | 'agent';
  version: number;
  spec?: string | null;
};

export type ReportBlockCatalogEntry = {
  key: string;
  title: string;
  shape: string;
  requires: string[];
  domain: string;
  description: string;
};
```

And extend the existing `ReportJob` type with the two new columns:

```typescript
  template_slug?: string | null;
  compare_to_run_id?: number | null;
```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/reports/Waterfall.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Waterfall } from './Waterfall';

const DATA = {
  buckets: { delta: 2000, gamma: 80, vega: -150, theta: -81.6, rho: 0, rho_q: 0 },
  explained: 1848.4,
  actual: 1500,
  residual: -348.4,
  residual_ratio: 0.232,
  residual_exceeds_threshold: true,
};

describe('Waterfall', () => {
  it('renders a bar for every non-zero bucket', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.getByText('Delta')).toBeInTheDocument();
    expect(screen.getByText('Gamma')).toBeInTheDocument();
    expect(screen.getByText('Vega')).toBeInTheDocument();
    expect(screen.getByText('Theta')).toBeInTheDocument();
  });

  it('shows the actual move and the residual', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.getByText('Actual')).toBeInTheDocument();
    expect(screen.getByText('Residual')).toBeInTheDocument();
  });

  it('warns when the residual exceeds the threshold', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.getByRole('note')).toHaveTextContent(/does not fully explain/i);
  });

  it('does not warn when the residual is within tolerance', () => {
    render(<Waterfall data={{ ...DATA, residual: -10, residual_ratio: 0.006, residual_exceeds_threshold: false }} />);
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });

  it('omits buckets that contributed nothing', () => {
    render(<Waterfall data={DATA} />);
    expect(screen.queryByText('Rho')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npm test -- Waterfall`
Expected: FAIL — cannot resolve `./Waterfall`.

- [ ] **Step 4: Write the component**

Create `frontend/src/components/reports/Waterfall.tsx`:

```tsx
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatSignedNumber } from '../numberFormat';
import './Waterfall.css';

export type WaterfallData = {
  buckets: Record<string, number>;
  explained: number;
  actual: number;
  residual: number;
  residual_ratio: number | null;
  residual_exceeds_threshold: boolean;
};

type Props = { data: WaterfallData };

const BUCKET_LABELS: Record<string, string> = {
  delta: 'Delta',
  gamma: 'Gamma',
  vega: 'Vega',
  theta: 'Theta',
  rho: 'Rho',
  rho_q: 'Rho (q)',
};

export function Waterfall({ data }: Props) {
  // A bucket that contributed nothing is noise on a decomposition chart.
  const bars = Object.entries(data.buckets)
    .filter(([, value]) => Number.isFinite(value) && value !== 0)
    .map(([key, value]) => ({ name: BUCKET_LABELS[key] ?? key, value, kind: 'bucket' as const }));

  const rows = [
    ...bars,
    { name: 'Residual', value: data.residual, kind: 'residual' as const },
    { name: 'Actual', value: data.actual, kind: 'total' as const },
  ];

  return (
    <div className="wl-waterfall">
      <div className="wl-waterfall__chart">
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" className="wl-waterfall__grid" />
            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} width={72} />
            <Tooltip formatter={(value: number) => formatSignedNumber(value)} />
            <Bar dataKey="value" radius={[2, 2, 0, 0]}>
              {rows.map((row) => (
                <Cell
                  key={row.name}
                  className={`wl-waterfall__bar wl-waterfall__bar--${
                    row.kind === 'total' ? 'total' : row.kind === 'residual' ? 'residual' : row.value >= 0 ? 'pos' : 'neg'
                  }`}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <dl className="wl-waterfall__legend">
        <div><dt>Explained</dt><dd>{formatSignedNumber(data.explained)}</dd></div>
        <div><dt>Actual</dt><dd>{formatSignedNumber(data.actual)}</dd></div>
        <div><dt>Residual</dt><dd>{formatSignedNumber(data.residual)}</dd></div>
      </dl>

      {data.residual_exceeds_threshold && (
        <p className="wl-waterfall__warn" role="note">
          The attribution does not fully explain this move
          {data.residual_ratio != null && ` — ${(data.residual_ratio * 100).toFixed(1)}% unexplained`}.
        </p>
      )}
    </div>
  );
}
```

Create `frontend/src/components/reports/Waterfall.css`. **Read `frontend/UI_STYLE_GUIDE.md`
for the token names before writing this file** — use only tokens defined in `src/tokens/`:

```css
.wl-waterfall { display: flex; flex-direction: column; gap: var(--space-3); }
.wl-waterfall__chart { width: 100%; }
.wl-waterfall__grid { stroke: var(--border-subtle); }
.wl-waterfall__bar--pos { fill: var(--accent-pos); }
.wl-waterfall__bar--neg { fill: var(--accent-neg); }
.wl-waterfall__bar--residual { fill: var(--accent-warn); }
.wl-waterfall__bar--total { fill: var(--ink-muted); }
.wl-waterfall__legend { display: flex; gap: var(--space-4); margin: 0; }
.wl-waterfall__legend div { display: flex; flex-direction: column; gap: var(--space-1); }
.wl-waterfall__legend dt { font-size: var(--font-size-xs); color: var(--ink-muted); }
.wl-waterfall__legend dd { margin: 0; font-size: var(--font-size-sm); font-variant-numeric: tabular-nums; }
.wl-waterfall__warn { margin: 0; font-size: var(--font-size-xs); color: var(--accent-warn); }
```

If any of `--accent-pos`, `--accent-neg`, `--accent-warn`, `--border-subtle`, `--ink-muted`,
`--space-1..4`, `--font-size-xs/sm` is not defined in `src/tokens/`, substitute the real token —
do **not** invent one and do **not** fall back to a literal color.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm test -- Waterfall`
Expected: PASS, 5 tests

- [ ] **Step 6: Verify both themes and compact density**

Start the dev server and view a report containing `pnl.explain` in light, dark, and compact
density. Confirm the bars, grid, and warning text are all legible in each. Fix any contrast
issue by correcting the token, never by adding a theme override.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/reports/
git commit -m "feat(frontend): report document types and P&L attribution waterfall"
```

---

### Task 3: `BlockRenderer` with honest empty and unavailable states

**Files:**
- Create: `frontend/src/components/reports/BlockRenderer.tsx`
- Create: `frontend/src/components/reports/BlockRenderer.css`
- Test: `frontend/src/components/reports/BlockRenderer.test.tsx`

**Interfaces:**
- Consumes: `ReportBlock` (Task 2), `Waterfall` (Task 2), existing `MetricRow`, `Table`, `ChartAsset`, `Panel`, `Badge`, `Empty`, `GreeksByPosition`
- Produces: `BlockRenderer` component — props `{ block: ReportBlock }`

**The state that matters:** `empty` and `unavailable` must be visually and textually distinct.
`empty` is an affirmative result ("no limit is in breach"); `unavailable` is an absence of
result ("the limit check did not run"). Rendering them the same is the exact failure the
tri-state exists to prevent.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/reports/BlockRenderer.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BlockRenderer } from './BlockRenderer';
import type { ReportBlock } from '../../types';

function block(partial: Partial<ReportBlock> & { result: ReportBlock['result'] }): ReportBlock {
  return { key: 'risk.totals', render: 'metric_row', fields: null, ...partial };
}

describe('BlockRenderer', () => {
  it('renders scalars as metric tiles', () => {
    render(<BlockRenderer block={block({
      result: { status: 'ok', reason: null, provenance: {}, data: { metrics: { delta_cash: 57334.67, vega: 276.43 } } },
    })} />);
    expect(screen.getByText(/delta cash/i)).toBeInTheDocument();
  });

  it('honours the fields filter', () => {
    render(<BlockRenderer block={block({
      fields: ['vega'],
      result: { status: 'ok', reason: null, provenance: {}, data: { metrics: { delta_cash: 1, vega: 276.43 } } },
    })} />);
    expect(screen.getByText(/vega/i)).toBeInTheDocument();
    expect(screen.queryByText(/delta cash/i)).not.toBeInTheDocument();
  });

  it('renders an EMPTY block as an affirmative result', () => {
    render(<BlockRenderer block={block({
      key: 'limits.breaches', render: 'callout',
      result: { status: 'empty', reason: 'no limit is in breach on the latest monitoring run', data: {}, provenance: {} },
    })} />);
    const panel = screen.getByTestId('block-empty');
    expect(panel).toHaveTextContent(/no limit is in breach/i);
  });

  it('renders an UNAVAILABLE block as a check that did not run', () => {
    render(<BlockRenderer block={block({
      key: 'scenario.latest_grid', render: 'table',
      result: { status: 'unavailable', reason: 'no scenario test run exists for this portfolio', data: {}, provenance: {} },
    })} />);
    const panel = screen.getByTestId('block-unavailable');
    expect(panel).toHaveTextContent(/not available/i);
    expect(panel).toHaveTextContent(/no scenario test run exists/i);
  });

  it('gives empty and unavailable different test ids so they can never render alike', () => {
    const { rerender } = render(<BlockRenderer block={block({
      result: { status: 'empty', reason: 'nothing to report', data: {}, provenance: {} },
    })} />);
    expect(screen.queryByTestId('block-unavailable')).not.toBeInTheDocument();
    rerender(<BlockRenderer block={block({
      result: { status: 'unavailable', reason: 'could not run', data: {}, provenance: {} },
    })} />);
    expect(screen.queryByTestId('block-empty')).not.toBeInTheDocument();
  });

  it('renders rows as a table', () => {
    render(<BlockRenderer block={block({
      key: 'pnl.by_position', render: 'table',
      result: { status: 'ok', reason: null, provenance: {}, data: { rows: [{ position_id: 1, underlying: 'AAPL', change: 2000 }] } },
    })} />);
    expect(screen.getByText('AAPL')).toBeInTheDocument();
  });

  it('renders a waterfall', () => {
    render(<BlockRenderer block={block({
      key: 'pnl.explain', render: 'waterfall',
      result: {
        status: 'ok', reason: null, provenance: {},
        data: { buckets: { delta: 2000 }, explained: 2000, actual: 2000, residual: 0, residual_ratio: 0, residual_exceeds_threshold: false },
      },
    })} />);
    expect(screen.getByText('Delta')).toBeInTheDocument();
  });

  it('falls back visibly for an unknown renderer rather than rendering nothing', () => {
    render(<BlockRenderer block={block({
      render: 'hologram',
      result: { status: 'ok', reason: null, provenance: {}, data: { metrics: {} } },
    })} />);
    expect(screen.getByTestId('block-unknown-renderer')).toHaveTextContent(/hologram/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- BlockRenderer`
Expected: FAIL — cannot resolve `./BlockRenderer`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/reports/BlockRenderer.tsx`:

```tsx
import { Badge } from '../Badge';
import { ChartAsset } from '../ChartAsset';
import { GreeksByPosition } from '../GreeksByPosition';
import { MetricRow, type Metric } from '../MetricRow';
import { Table, type Column } from '../Table';
import { formatSignedNumber } from '../numberFormat';
import { Waterfall } from './Waterfall';
import type { ReportBlock } from '../../types';
import './BlockRenderer.css';

type Props = { block: ReportBlock };

function humanize(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function scalarMetrics(data: Record<string, any>, fields: string[] | null): Metric[] {
  const source: Record<string, any> = data.metrics ?? data;
  return Object.entries(source)
    .filter(([key, value]) => typeof value === 'number' && (!fields || fields.includes(key)))
    .map(([key, value]) => ({
      label: humanize(key),
      value: formatSignedNumber(value as number),
      variant: (value as number) >= 0 ? 'pos' : 'neg',
    }));
}

function priorMetrics(data: Record<string, any>, fields: string[] | null): Metric[] {
  const source: Record<string, any> = data.metrics ?? {};
  return Object.entries(source)
    .filter(([key, value]) => value && typeof value === 'object' && (!fields || fields.includes(key)))
    .map(([key, change]: [string, any]) => ({
      label: humanize(key),
      value: change.after == null ? '—' : formatSignedNumber(change.after),
      variant: (change.change ?? 0) >= 0 ? 'pos' : 'neg',
      delta: change.change == null ? null : `${change.change >= 0 ? '▲' : '▼'} ${formatSignedNumber(change.change)}`,
    }));
}

function autoColumns(rows: Record<string, any>[]): Column<Record<string, any>>[] {
  const first = rows[0] ?? {};
  return Object.keys(first).map((key) => ({
    key,
    header: humanize(key),
    numeric: typeof first[key] === 'number',
    render: (row) => {
      const value = row[key];
      if (value == null) return '—';
      return typeof value === 'number' ? formatSignedNumber(value) : String(value);
    },
  }));
}

export function BlockRenderer({ block }: Props) {
  const { result, render: renderKind, fields } = block;

  // These two states are deliberately distinct. "empty" is an affirmative
  // result — the check ran and found nothing. "unavailable" is the ABSENCE of
  // a result — the check could not run. Collapsing them would let a report
  // imply a clean book when nothing was actually verified.
  if (result.status === 'empty') {
    return (
      <div className="wl-block wl-block--empty" data-testid="block-empty">
        <Badge variant="ink">none</Badge>
        <p className="wl-block__reason">{result.reason}</p>
      </div>
    );
  }

  if (result.status === 'unavailable') {
    return (
      <div className="wl-block wl-block--unavailable" data-testid="block-unavailable">
        <Badge variant="warn">not available</Badge>
        <p className="wl-block__reason">{result.reason}</p>
      </div>
    );
  }

  const data = result.data ?? {};

  switch (renderKind) {
    case 'metric_row':
      return <MetricRow metrics={scalarMetrics(data, fields)} />;
    case 'delta_metric_row':
      return <MetricRow metrics={priorMetrics(data, fields)} />;
    case 'table': {
      const rows: Record<string, any>[] = data.rows ?? data.added ?? [];
      if (rows.length === 0) {
        return (
          <div className="wl-block wl-block--empty" data-testid="block-empty">
            <p className="wl-block__reason">No rows.</p>
          </div>
        );
      }
      return <Table columns={autoColumns(rows)} rows={rows} rowKey={(row) => JSON.stringify(row)} />;
    }
    case 'bar_chart':
    case 'line_chart':
      return (
        <ChartAsset
          title={block.key}
          data={{
            chart_type: renderKind === 'bar_chart' ? 'bar' : 'line',
            x_key: data.x_key ?? 'name',
            y_key: data.y_key ?? 'value',
            series: data.series ?? [],
          }}
        />
      );
    case 'callout': {
      const items: Record<string, any>[] = data.items ?? [];
      return (
        <ul className="wl-block__items">
          {items.map((item, index) => (
            <li key={index} className="wl-block__item">
              <span className="wl-block__item-label">{item.scope_label ?? item.underlying ?? `#${item.position_id}`}</span>
              <span className="wl-block__item-detail">
                {item.status ?? item.nearest_barrier_kind ?? ''}
                {item.days_to_nearest != null && ` · ${item.days_to_nearest}d`}
              </span>
            </li>
          ))}
        </ul>
      );
    }
    case 'greeks_table':
      return <GreeksByPosition positions={data.positions ?? []} />;
    case 'waterfall':
      return <Waterfall data={data as any} />;
    default:
      return (
        <div className="wl-block wl-block--unknown" data-testid="block-unknown-renderer">
          <Badge variant="warn">unknown renderer</Badge>
          <p className="wl-block__reason">No component is registered for renderer “{renderKind}”.</p>
        </div>
      );
  }
}
```

Create `frontend/src/components/reports/BlockRenderer.css` using only defined tokens:

```css
.wl-block { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-3); border-radius: var(--radius-sm); }
.wl-block--empty { background: var(--surface-sunken); }
.wl-block--unavailable { background: var(--surface-sunken); border-left: 2px solid var(--accent-warn); }
.wl-block--unknown { background: var(--surface-sunken); border-left: 2px solid var(--accent-warn); }
.wl-block__reason { margin: 0; font-size: var(--font-size-xs); color: var(--ink-muted); }
.wl-block__items { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-1); }
.wl-block__item { display: flex; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) var(--space-3); background: var(--surface-sunken); border-radius: var(--radius-sm); }
.wl-block__item-label { font-size: var(--font-size-sm); }
.wl-block__item-detail { font-size: var(--font-size-xs); color: var(--ink-muted); font-variant-numeric: tabular-nums; }
```

Substitute any token that does not exist in `src/tokens/` with the real one — never a literal.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- BlockRenderer`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/reports/BlockRenderer.tsx frontend/src/components/reports/BlockRenderer.css frontend/src/components/reports/BlockRenderer.test.tsx
git commit -m "feat(frontend): block renderer with distinct empty and unavailable states"
```

---

### Task 4: `ReportDocumentView`

**Files:**
- Create: `frontend/src/components/reports/ReportDocumentView.tsx`
- Create: `frontend/src/components/reports/ReportDocumentView.css`
- Test: `frontend/src/components/reports/ReportDocumentView.test.tsx`

**Interfaces:**
- Consumes: `BlockRenderer` (Task 3), `ReportDocument` (Task 2), existing `Panel`, `Badge`, `Chip`
- Produces: `ReportDocumentView` — props `{ document: ReportDocument }`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/reports/ReportDocumentView.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportDocumentView } from './ReportDocumentView';
import type { ReportDocument } from '../../types';

const DOC: ReportDocument = {
  template: { slug: 'risk-manager-daily', title: 'Risk — Daily', persona: 'risk_manager', version: 1, spec: 'meta:\n', spec_sha256: 'sha256:abc' },
  params: { portfolio_id: 2, compare_to_run_id: 35 },
  generated_at: '2026-08-06T09:00:00Z',
  provenance: { risk_run_id: 36, valuation_as_of: '2026-06-24T00:00:00', position_set_hash: 'sha256:f69b', coverage: { priced: 4, total: 5 } },
  sections: [
    {
      id: 'limit_status', title: 'Limit status',
      blocks: [{ key: 'limits.breaches', render: 'callout', fields: null, result: { status: 'empty', reason: 'no limit is in breach', data: {}, provenance: {} } }],
      narrative: 'The book is within all limits.',
      narrative_error: null,
      grounding: { checked: true, flags: [], grounded_count: 0 },
    },
    {
      id: 'stress', title: 'Stress',
      blocks: [{ key: 'scenario.latest_grid', render: 'table', fields: null, result: { status: 'unavailable', reason: 'no scenario test run exists', data: {}, provenance: {} } }],
      narrative: null, narrative_error: null,
      grounding: { checked: false, flags: [], grounded_count: 0 },
    },
  ],
};

describe('ReportDocumentView', () => {
  it('renders the template title and every section', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText('Risk — Daily')).toBeInTheDocument();
    expect(screen.getByText('Limit status')).toBeInTheDocument();
    expect(screen.getByText('Stress')).toBeInTheDocument();
  });

  it('renders narrative prose beneath its section blocks', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText('The book is within all limits.')).toBeInTheDocument();
  });

  it('surfaces coverage in the header so a partial book is never silent', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText(/4\s*\/\s*5 priced/i)).toBeInTheDocument();
  });

  it('shows provenance', () => {
    render(<ReportDocumentView document={DOC} />);
    expect(screen.getByText(/risk run #36/i)).toBeInTheDocument();
    expect(screen.getByText(/sha256:f69b/)).toBeInTheDocument();
  });

  it('flags a section whose narrative contains ungrounded numbers', () => {
    const flagged: ReportDocument = {
      ...DOC,
      sections: [{ ...DOC.sections[0], narrative: 'Vega is 999.99.', grounding: { checked: true, flags: [{ token: 999.99, offset: 9 }], grounded_count: 0 } }],
    };
    render(<ReportDocumentView document={flagged} />);
    expect(screen.getByTestId('grounding-warning')).toHaveTextContent(/999.99/);
  });

  it('reports a narrative failure instead of silently omitting the section', () => {
    const failed: ReportDocument = {
      ...DOC,
      sections: [{ ...DOC.sections[0], narrative: null, narrative_error: 'model timed out' }],
    };
    render(<ReportDocumentView document={failed} />);
    expect(screen.getByTestId('narrative-error')).toHaveTextContent(/model timed out/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- ReportDocumentView`
Expected: FAIL — cannot resolve `./ReportDocumentView`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/reports/ReportDocumentView.tsx`:

```tsx
import { Badge } from '../Badge';
import { Chip } from '../Chip';
import { BlockRenderer } from './BlockRenderer';
import type { ReportDocument } from '../../types';
import './ReportDocumentView.css';

type Props = { document: ReportDocument };

export function ReportDocumentView({ document }: Props) {
  const { template, provenance, params, sections, generated_at: generatedAt } = document;
  const coverage = provenance.coverage as { priced?: number; total?: number } | undefined;

  return (
    <article className="wl-report">
      <header className="wl-report__head">
        <div className="wl-report__title-row">
          <h2 className="wl-report__title">{template.title}</h2>
          <Badge variant="info">{template.persona}</Badge>
        </div>
        <div className="wl-report__chips">
          <Chip>portfolio #{params.portfolio_id}</Chip>
          {provenance.valuation_as_of && <Chip>as of {String(provenance.valuation_as_of).slice(0, 10)}</Chip>}
          {params.compare_to_run_id != null && <Chip>vs run #{params.compare_to_run_id}</Chip>}
          {coverage?.total != null && (
            <Chip>{coverage.priced ?? 0} / {coverage.total} priced</Chip>
          )}
          <Chip>generated {generatedAt.slice(0, 16).replace('T', ' ')}</Chip>
        </div>
      </header>

      {sections.map((section) => (
        <section key={section.id} className="wl-report__section">
          {section.title && <h3 className="wl-report__section-title">{section.title}</h3>}

          <div className="wl-report__blocks">
            {section.blocks.map((block) => (
              <div key={block.key} className="wl-report__block">
                <BlockRenderer block={block} />
              </div>
            ))}
          </div>

          {section.narrative && <p className="wl-report__narrative">{section.narrative}</p>}

          {section.narrative_error && (
            <p className="wl-report__warn" role="note" data-testid="narrative-error">
              This section's commentary could not be written: {section.narrative_error}
            </p>
          )}

          {section.grounding.flags.length > 0 && (
            <p className="wl-report__warn" role="note" data-testid="grounding-warning">
              Ungrounded {section.grounding.flags.length === 1 ? 'figure' : 'figures'} in the
              commentary: {section.grounding.flags.map((flag) => flag.token).join(', ')}. These do
              not appear in this section's data and need review.
            </p>
          )}
        </section>
      ))}

      <footer className="wl-report__provenance">
        <h4 className="wl-report__provenance-title">Provenance</h4>
        <dl className="wl-report__provenance-list">
          {provenance.risk_run_id != null && (
            <div><dt>Risk run</dt><dd>risk run #{provenance.risk_run_id}</dd></div>
          )}
          {provenance.position_set_hash && (
            <div><dt>Position set</dt><dd>{String(provenance.position_set_hash)}</dd></div>
          )}
          <div><dt>Template</dt><dd>{template.slug} v{template.version}</dd></div>
          <div><dt>Spec</dt><dd>{template.spec_sha256}</dd></div>
        </dl>
      </footer>
    </article>
  );
}
```

Create `frontend/src/components/reports/ReportDocumentView.css` using only defined tokens:

```css
.wl-report { display: flex; flex-direction: column; gap: var(--space-5); }
.wl-report__head { display: flex; flex-direction: column; gap: var(--space-2); }
.wl-report__title-row { display: flex; align-items: center; gap: var(--space-2); }
.wl-report__title { margin: 0; font-size: var(--font-size-lg); }
.wl-report__chips { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.wl-report__section { display: flex; flex-direction: column; gap: var(--space-3); }
.wl-report__section-title { margin: 0; font-size: var(--font-size-sm); color: var(--ink-muted); text-transform: uppercase; letter-spacing: 0.06em; }
.wl-report__blocks { display: flex; flex-direction: column; gap: var(--space-3); }
.wl-report__narrative { margin: 0; font-size: var(--font-size-sm); line-height: 1.6; }
.wl-report__warn { margin: 0; font-size: var(--font-size-xs); color: var(--accent-warn); }
.wl-report__provenance { border-top: 1px solid var(--border-subtle); padding-top: var(--space-3); }
.wl-report__provenance-title { margin: 0 0 var(--space-2); font-size: var(--font-size-xs); color: var(--ink-muted); text-transform: uppercase; letter-spacing: 0.06em; }
.wl-report__provenance-list { display: flex; flex-wrap: wrap; gap: var(--space-4); margin: 0; }
.wl-report__provenance-list div { display: flex; flex-direction: column; gap: var(--space-1); }
.wl-report__provenance-list dt { font-size: var(--font-size-xs); color: var(--ink-muted); }
.wl-report__provenance-list dd { margin: 0; font-size: var(--font-size-xs); font-variant-numeric: tabular-nums; word-break: break-all; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- ReportDocumentView`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/reports/ReportDocumentView.tsx frontend/src/components/reports/ReportDocumentView.css frontend/src/components/reports/ReportDocumentView.test.tsx
git commit -m "feat(frontend): report document view with provenance and grounding flags"
```

---

### Task 5: Reports page with Reports and Templates tabs

**Files:**
- Rewrite: `frontend/src/routes/Reports.tsx`
- Rewrite: `frontend/src/routes/Reports.live.tsx`
- Rewrite: `frontend/src/routes/Reports.css`
- Create: `frontend/src/components/reports/TemplateEditor.tsx`
- Create: `frontend/src/components/reports/TemplateEditor.css`
- Test: `frontend/src/routes/Reports.test.tsx`, `frontend/src/components/reports/TemplateEditor.test.tsx`

**Interfaces:**
- Consumes: `ReportDocumentView` (Task 4), `MasterDetailPage`, `ReportJob`/`ReportTemplate` types
- Produces:
  - `Reports` — props `{ jobs, templates, loading, selectedJob, onSelectJob, onGenerate, onSaveTemplate, onValidateTemplate, onPageContextChange }`
  - `ReportsLive` — fetches `/api/reports/jobs`, `/api/reports/templates`, posts `/api/reports/generate`
  - `TemplateEditor` — props `{ template, onSave, onValidate }`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/reports/TemplateEditor.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TemplateEditor } from './TemplateEditor';
import type { ReportTemplate } from '../../types';

const TEMPLATE: ReportTemplate = {
  slug: 'demo', title: 'Demo', persona: 'trader', description: '',
  source: 'user', version: 1, spec: 'meta:\n  slug: demo\n',
};

describe('TemplateEditor', () => {
  it('shows the current spec', () => {
    render(<TemplateEditor template={TEMPLATE} onSave={vi.fn()} onValidate={vi.fn().mockResolvedValue({ ok: true, errors: [] })} />);
    expect(screen.getByRole('textbox')).toHaveValue('meta:\n  slug: demo\n');
  });

  it('surfaces validation errors without saving', async () => {
    const onValidate = vi.fn().mockResolvedValue({ ok: false, errors: ['sections[0].blocks[0].key \'risk.nope\' is not a registered block'] });
    const onSave = vi.fn();
    render(<TemplateEditor template={TEMPLATE} onSave={onSave} onValidate={onValidate} />);
    fireEvent.click(screen.getByRole('button', { name: /validate/i }));
    await waitFor(() => expect(screen.getByTestId('template-errors')).toHaveTextContent(/risk\.nope/));
    expect(onSave).not.toHaveBeenCalled();
  });

  it('disables save for a seeded template', () => {
    render(<TemplateEditor template={{ ...TEMPLATE, source: 'seed' }} onSave={vi.fn()} onValidate={vi.fn()} />);
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
  });

  it('saves an edited spec', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<TemplateEditor template={TEMPLATE} onSave={onSave} onValidate={vi.fn().mockResolvedValue({ ok: true, errors: [] })} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'meta:\n  slug: demo2\n' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith('demo', 'meta:\n  slug: demo2\n'));
  });
});
```

Create `frontend/src/routes/Reports.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Reports } from './Reports';
import type { ReportJob, ReportTemplate } from '../types';

const JOB: ReportJob = {
  id: 7, report_type: 'risk_manager', status: 'completed',
  template_slug: 'risk-manager-daily', compare_to_run_id: 35,
  request_payload: { title: 'Risk — Daily', portfolio_id: 2 },
  artifact_paths: {}, created_at: '2026-08-06T09:00:00Z',
  result_payload: {
    template: { slug: 'risk-manager-daily', title: 'Risk — Daily', persona: 'risk_manager', version: 1, spec: '', spec_sha256: 'sha256:abc' },
    params: { portfolio_id: 2, compare_to_run_id: 35 },
    generated_at: '2026-08-06T09:00:00Z',
    provenance: { risk_run_id: 36 },
    sections: [{
      id: 's', title: 'Limit status', narrative: 'All clear.', narrative_error: null,
      grounding: { checked: true, flags: [], grounded_count: 0 },
      blocks: [{ key: 'limits.breaches', render: 'callout', fields: null, result: { status: 'empty', reason: 'no limit is in breach', data: {}, provenance: {} } }],
    }],
  },
};

const TEMPLATES: ReportTemplate[] = [
  { slug: 'risk-manager-daily', title: 'Risk — Daily', persona: 'risk_manager', description: '', source: 'seed', version: 1 },
];

function setup(overrides = {}) {
  const props = {
    jobs: [JOB], templates: TEMPLATES, loading: false,
    selectedJob: JOB, onSelectJob: vi.fn(), onGenerate: vi.fn(),
    onSaveTemplate: vi.fn(), onValidateTemplate: vi.fn(),
    ...overrides,
  };
  render(<Reports {...props} />);
  return props;
}

describe('Reports', () => {
  it('renders the selected report document, not a JSON dump', () => {
    setup();
    expect(screen.getByText('Limit status')).toBeInTheDocument();
    expect(screen.getByText('All clear.')).toBeInTheDocument();
    expect(screen.queryByText(/"result_payload"/)).not.toBeInTheDocument();
  });

  it('lists reports in the rail', () => {
    setup();
    expect(screen.getByRole('button', { name: /Risk — Daily/ })).toBeInTheDocument();
  });

  it('switches to the templates tab', () => {
    setup();
    fireEvent.click(screen.getByRole('tab', { name: /templates/i }));
    expect(screen.getByText('risk-manager-daily')).toBeInTheDocument();
  });

  it('renders a legacy report without a template as raw payload', () => {
    const legacy: ReportJob = { ...JOB, template_slug: null, result_payload: { risk: { totals: { market_value: 1 } } } };
    setup({ jobs: [legacy], selectedJob: legacy });
    expect(screen.getByTestId('legacy-payload')).toBeInTheDocument();
  });

  it('shows an empty state with no reports', () => {
    setup({ jobs: [], selectedJob: null });
    expect(screen.getByText(/no reports yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- Reports TemplateEditor`
Expected: FAIL — `TemplateEditor` unresolved and `Reports` has the wrong props.

- [ ] **Step 3: Write `TemplateEditor`**

Create `frontend/src/components/reports/TemplateEditor.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Badge } from '../Badge';
import { Button } from '../Button';
import type { ReportTemplate } from '../../types';
import './TemplateEditor.css';

type Props = {
  template: ReportTemplate;
  onSave: (slug: string, specYaml: string) => Promise<void> | void;
  onValidate: (specYaml: string) => Promise<{ ok: boolean; errors: string[] }>;
};

export function TemplateEditor({ template, onSave, onValidate }: Props) {
  const [spec, setSpec] = useState(template.spec ?? '');
  const [errors, setErrors] = useState<string[]>([]);
  const [status, setStatus] = useState<'idle' | 'valid' | 'invalid' | 'saved'>('idle');
  const isSeed = template.source === 'seed';

  useEffect(() => {
    setSpec(template.spec ?? '');
    setErrors([]);
    setStatus('idle');
  }, [template.slug, template.spec]);

  async function validate() {
    const result = await onValidate(spec);
    setErrors(result.errors);
    setStatus(result.ok ? 'valid' : 'invalid');
    return result.ok;
  }

  async function save() {
    if (!(await validate())) return;
    await onSave(template.slug, spec);
    setStatus('saved');
  }

  return (
    <div className="wl-tpl-editor">
      <header className="wl-tpl-editor__head">
        <span className="wl-tpl-editor__slug">{template.slug}</span>
        <Badge variant={isSeed ? 'info' : 'ink'}>{template.source}</Badge>
        <span className="wl-tpl-editor__version">v{template.version}</span>
      </header>

      <textarea
        className="wl-tpl-editor__area"
        value={spec}
        spellCheck={false}
        aria-label={`Spec for ${template.slug}`}
        onChange={(event) => { setSpec(event.target.value); setStatus('idle'); }}
      />

      <div className="wl-tpl-editor__actions">
        <Button variant="ghost" onClick={validate}>Validate</Button>
        <Button onClick={save} disabled={isSeed}>Save</Button>
        {status === 'valid' && <span className="wl-tpl-editor__ok">Spec is valid.</span>}
        {status === 'saved' && <span className="wl-tpl-editor__ok">Saved.</span>}
      </div>

      {isSeed && (
        <p className="wl-tpl-editor__note">
          Seeded templates are read-only. Copy this spec into a new slug to customise it.
        </p>
      )}

      {errors.length > 0 && (
        <ul className="wl-tpl-editor__errors" data-testid="template-errors">
          {errors.map((message) => <li key={message}>{message}</li>)}
        </ul>
      )}
    </div>
  );
}
```

Create `frontend/src/components/reports/TemplateEditor.css` with defined tokens only:

```css
.wl-tpl-editor { display: flex; flex-direction: column; gap: var(--space-3); }
.wl-tpl-editor__head { display: flex; align-items: center; gap: var(--space-2); }
.wl-tpl-editor__slug { font-size: var(--font-size-sm); font-weight: 600; }
.wl-tpl-editor__version { font-size: var(--font-size-xs); color: var(--ink-muted); }
.wl-tpl-editor__area { min-height: 24rem; padding: var(--space-3); font-family: var(--font-mono); font-size: var(--font-size-xs); line-height: 1.5; color: var(--ink); background: var(--surface-sunken); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); resize: vertical; }
.wl-tpl-editor__area:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.wl-tpl-editor__actions { display: flex; align-items: center; gap: var(--space-2); }
.wl-tpl-editor__ok { font-size: var(--font-size-xs); color: var(--accent-pos); }
.wl-tpl-editor__note { margin: 0; font-size: var(--font-size-xs); color: var(--ink-muted); }
.wl-tpl-editor__errors { margin: 0; padding-left: var(--space-4); font-size: var(--font-size-xs); color: var(--accent-neg); display: flex; flex-direction: column; gap: var(--space-1); }
```

The `<textarea>` is explicitly themed — an unstyled control defaults to a white background that
breaks dark mode.

- [ ] **Step 4: Rewrite the Reports page**

Replace `frontend/src/routes/Reports.tsx`:

```tsx
import { useMemo, useState } from 'react';
import type { PageContext, PageContextReporter, ReportDocument, ReportJob, ReportTemplate } from '../types';
import { MasterDetailPage } from '../components/templates';
import { ReportDocumentView } from '../components/reports/ReportDocumentView';
import { TemplateEditor } from '../components/reports/TemplateEditor';
import { Badge } from '../components/Badge';
import { Empty } from '../components/Empty';
import { Skeleton } from '../components/Skeleton';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import './Reports.css';

type Props = {
  jobs: ReportJob[];
  templates: ReportTemplate[];
  loading: boolean;
  selectedJob: ReportJob | null;
  onSelectJob: (job: ReportJob) => void;
  onGenerate: (slug: string) => void;
  onSaveTemplate: (slug: string, specYaml: string) => Promise<void>;
  onValidateTemplate: (specYaml: string) => Promise<{ ok: boolean; errors: string[] }>;
  onPageContextChange?: PageContextReporter;
};

type Tab = 'reports' | 'templates';

function jobTitle(job: ReportJob): string {
  const fromDoc = (job.result_payload as Partial<ReportDocument> | undefined)?.template?.title;
  if (fromDoc) return fromDoc;
  const fromRequest = job.request_payload?.title;
  return typeof fromRequest === 'string' && fromRequest ? fromRequest : `Report #${job.id}`;
}

function isTemplated(job: ReportJob): boolean {
  return Boolean(job.template_slug) && Boolean((job.result_payload as any)?.sections);
}

export function Reports({
  jobs, templates, loading, selectedJob, onSelectJob, onGenerate,
  onSaveTemplate, onValidateTemplate, onPageContextChange,
}: Props) {
  const [tab, setTab] = useState<Tab>('reports');
  const [selectedTemplate, setSelectedTemplate] = useState<ReportTemplate | null>(null);

  const chips = [
    loading ? 'Loading…' : `${jobs.length} reports`,
    `${templates.length} templates`,
  ];

  const pageContext = useMemo<PageContext>(() => ({
    route: 'reports',
    title: 'Reports',
    path: '/reports',
    entity_ids: { report_job_id: selectedJob?.id ?? null },
    snapshot: {
      report_count: jobs.length,
      templates: templates.map((t) => t.slug),
      selected: selectedJob ? { id: selectedJob.id, template_slug: selectedJob.template_slug } : null,
    },
    chips,
  }), [chips, jobs, templates, selectedJob]);
  usePageContextReporter(pageContext, onPageContextChange);

  const rail = (
    <div className="wl-reports__rail">
      <div className="wl-reports__tabs" role="tablist">
        {(['reports', 'templates'] as Tab[]).map((name) => (
          <button
            key={name}
            role="tab"
            type="button"
            aria-selected={tab === name}
            className={`wl-reports__tab ${tab === name ? 'wl-reports__tab--active' : ''}`.trim()}
            onClick={() => setTab(name)}
          >
            {name === 'reports' ? 'Reports' : 'Templates'}
          </button>
        ))}
      </div>

      {tab === 'reports' ? (
        <ul className="wl-reports__list">
          {jobs.map((job) => (
            <li key={job.id}>
              <button
                type="button"
                className={`wl-reports__item ${selectedJob?.id === job.id ? 'wl-reports__item--active' : ''}`.trim()}
                onClick={() => onSelectJob(job)}
              >
                <span className="wl-reports__item-title">{jobTitle(job)}</span>
                <span className="wl-reports__item-meta">
                  #{job.id} · {job.created_at.slice(5, 16).replace('T', ' ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="wl-reports__list">
          {templates.map((template) => (
            <li key={template.slug}>
              <button
                type="button"
                className={`wl-reports__item ${selectedTemplate?.slug === template.slug ? 'wl-reports__item--active' : ''}`.trim()}
                onClick={() => setSelectedTemplate(template)}
              >
                <span className="wl-reports__item-title">{template.slug}</span>
                <span className="wl-reports__item-meta">
                  <Badge variant="ink">{template.persona}</Badge>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  let body: React.ReactNode;
  if (loading) {
    body = <div className="wl-reports__loading"><Skeleton height={48} /><Skeleton height={48} /><Skeleton height={48} /></div>;
  } else if (tab === 'templates') {
    body = selectedTemplate
      ? <TemplateEditor template={selectedTemplate} onSave={onSaveTemplate} onValidate={onValidateTemplate} />
      : (
        <div className="wl-reports__generate">
          <Empty message="Select a template to view its spec, or generate a report from one." symbol="◫" />
          <div className="wl-reports__generate-actions">
            {templates.map((template) => (
              <button key={template.slug} type="button" className="wl-reports__generate-btn" onClick={() => onGenerate(template.slug)}>
                Generate {template.title}
              </button>
            ))}
          </div>
        </div>
      );
  } else if (!selectedJob) {
    body = <Empty message="No reports yet — generate one from a template." symbol="◌" />;
  } else if (isTemplated(selectedJob)) {
    body = <ReportDocumentView document={selectedJob.result_payload as unknown as ReportDocument} />;
  } else {
    body = (
      <pre className="wl-reports__legacy" data-testid="legacy-payload">
        {JSON.stringify(selectedJob.result_payload ?? {}, null, 2)}
      </pre>
    );
  }

  return <MasterDetailPage title="REPORTS" chips={chips} rail={rail} railWidth="18rem">{body}</MasterDetailPage>;
}
```

Replace `frontend/src/routes/Reports.css`:

```css
.wl-reports__rail { display: flex; flex-direction: column; gap: var(--space-2); }
.wl-reports__tabs { display: flex; gap: var(--space-1); }
.wl-reports__tab { flex: 1; padding: var(--space-2); font-size: var(--font-size-xs); color: var(--ink-muted); background: transparent; border: none; border-bottom: 2px solid transparent; cursor: pointer; }
.wl-reports__tab--active { color: var(--ink); border-bottom-color: var(--accent); }
.wl-reports__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-1); }
.wl-reports__item { width: 100%; display: flex; flex-direction: column; gap: var(--space-1); align-items: flex-start; padding: var(--space-2) var(--space-3); text-align: left; background: transparent; border: none; border-radius: var(--radius-sm); cursor: pointer; }
.wl-reports__item:hover { background: var(--surface-sunken); }
.wl-reports__item--active { background: var(--surface-raised); }
.wl-reports__item-title { font-size: var(--font-size-sm); }
.wl-reports__item-meta { font-size: var(--font-size-xs); color: var(--ink-muted); }
.wl-reports__loading { display: flex; flex-direction: column; gap: var(--space-2); }
.wl-reports__legacy { margin: 0; padding: var(--space-3); font-family: var(--font-mono); font-size: var(--font-size-xs); background: var(--surface-sunken); border-radius: var(--radius-sm); overflow-x: auto; }
.wl-reports__generate { display: flex; flex-direction: column; gap: var(--space-3); }
.wl-reports__generate-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.wl-reports__generate-btn { padding: var(--space-2) var(--space-3); font-size: var(--font-size-xs); color: var(--ink); background: var(--surface-sunken); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); cursor: pointer; }
```

- [ ] **Step 5: Rewrite `Reports.live.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { PageContextReporter, ReportJob, ReportTemplate } from '../types';
import { Reports } from './Reports';
import { Empty } from '../components/Empty';

type Props = { onPageContextChange?: PageContextReporter };

const ACTIVE_STATUSES = new Set(['queued', 'running']);

export function ReportsLive({ onPageContextChange }: Props) {
  const [jobs, setJobs] = useState<ReportJob[]>([]);
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [selectedJob, setSelectedJob] = useState<ReportJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (cancelled?: { current: boolean }) => {
    try {
      const [jobList, templateList] = await Promise.all([
        api<ReportJob[]>('/api/reports/jobs'),
        api<ReportTemplate[]>('/api/reports/templates'),
      ]);
      if (cancelled?.current) return;
      setJobs(jobList);
      setTemplates(templateList);
      setSelectedJob((current) => current ?? jobList[0] ?? null);
      setError(null);
    } catch (e) {
      if (!cancelled?.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!cancelled?.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const cancelled = { current: false };
    void load(cancelled);
    return () => { cancelled.current = true; };
  }, [load]);

  useEffect(() => {
    if (!jobs.some((job) => ACTIVE_STATUSES.has(job.status))) return undefined;
    const cancelled = { current: false };
    const timer = window.setInterval(() => { void load(cancelled); }, 2000);
    return () => { cancelled.current = true; window.clearInterval(timer); };
  }, [jobs, load]);

  const onGenerate = useCallback(async (slug: string) => {
    const portfolioId = jobs[0]?.request_payload?.portfolio_id ?? 1;
    const job = await api<ReportJob>('/api/reports/generate', {
      method: 'POST',
      body: JSON.stringify({ template_slug: slug, portfolio_id: portfolioId }),
    });
    setSelectedJob(job);
    await load();
  }, [jobs, load]);

  const onValidateTemplate = useCallback(
    (specYaml: string) => api<{ ok: boolean; errors: string[] }>(
      '/api/reports/templates/validate',
      { method: 'POST', body: JSON.stringify({ spec_yaml: specYaml }) },
    ),
    [],
  );

  const onSaveTemplate = useCallback(async (slug: string, specYaml: string) => {
    await api<ReportTemplate>(`/api/reports/templates/${encodeURIComponent(slug)}`, {
      method: 'PUT',
      body: JSON.stringify({ spec_yaml: specYaml }),
    });
    await load();
  }, [load]);

  if (error) return <Empty message={`Could not load reports: ${error}`} />;

  return (
    <Reports
      jobs={jobs}
      templates={templates}
      loading={loading}
      selectedJob={selectedJob}
      onSelectJob={setSelectedJob}
      onGenerate={onGenerate}
      onSaveTemplate={onSaveTemplate}
      onValidateTemplate={onValidateTemplate}
      onPageContextChange={onPageContextChange}
    />
  );
}
```

- [ ] **Step 6: Delete the superseded components**

`ReportTimeline`, `ReportCard`, and `ReportReader` are no longer referenced by the Reports page.
Confirm before deleting:

```bash
grep -rn "ReportTimeline\|ReportCard\|ReportReader" frontend/src --include=*.tsx | grep -v "components/Report"
```

If the only hits are the components' own files and tests, delete all nine files
(`.tsx`, `.css`, `.test.tsx` for each). If anything else still imports them, leave that
component in place and note it in the commit message.

- [ ] **Step 7: Run tests and type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Run: `cd frontend && npm test -- Reports TemplateEditor`
Expected: PASS, 9 tests

- [ ] **Step 8: Verify in the browser, both themes and compact density**

Generate a `risk-manager-daily` report and confirm: the stress section shows a distinct
"not available" panel, the limits section shows an affirmative "none" panel, and provenance
renders. Then switch to dark and to compact density and confirm all three remain legible.

- [ ] **Step 9: Commit**

```bash
git add -A frontend/src
git commit -m "feat(frontend): rebuild Reports page with native document rendering and templates tab"
```

---

### Task 6: Retire the legacy writer

**Files:**
- Modify: `backend/app/services/reports.py` (delete the hardcoded writers)
- Modify: `backend/app/services/domains/reporting.py` (route `create_report` through the pipeline)
- Modify: `tests/test_services_domains_reporting.py`, `tests/test_tools_reporting.py`, `tests/test_agent_tools.py`
- Test: `tests/test_reporting_legacy_retirement.py`

**Interfaces:**
- Deleted: `_write_html`, `_write_xlsx`, `_build_report_payload`, `_metric_card`, `_MONEY_DISPLAY`, `_SHARED_DISPLAY`, `_report_status_from_payload`, `_pricing_profile_payload`, `_report_artifact_stem`, `_safe_artifact_name`
- Retained: `create_report` tool name, `POST /api/reports/jobs`, now routing through `generate_report` with the seeded `portfolio-snapshot` template

- [ ] **Step 1: Write the failing test**

Create `tests/test_reporting_legacy_retirement.py`:

```python
import pytest


def test_the_hardcoded_writers_are_gone():
    """The duplicate report writer is deleted; one pipeline remains."""
    import app.services.reports as reports

    for name in (
        "_write_html", "_write_xlsx", "_build_report_payload", "_metric_card",
        "_MONEY_DISPLAY", "_SHARED_DISPLAY", "_report_status_from_payload",
    ):
        assert not hasattr(reports, name), f"{name} should have been deleted"


def test_create_report_still_exists_and_routes_through_a_template():
    """create_report is a graded tool_not_called prohibition in THREE golden
    workflows (risk-manager-control-day step 9, high-board-portfolio-review-day
    steps 5 and 8, risk-limit-breach-day step 7). Deleting it would make all
    four checks trivially always-pass, inflating scores and breaking
    comparability with 11 boards of arena history. See spec section 5.7.
    """
    from app.services.domains import reporting as reporting_svc

    assert hasattr(reporting_svc, "create_report")
    assert reporting_svc.DEFAULT_TEMPLATE_SLUG == "portfolio-snapshot"


def test_create_report_produces_a_templated_document():
    from app.services.domains import reporting as reporting_svc

    result = reporting_svc.create_report(portfolio_id=2)
    assert result["template_slug"] == "portfolio-snapshot"
    assert result["report_job_id"] > 0


def test_create_report_rejects_an_unsupported_type():
    from app.services.domains import reporting as reporting_svc

    with pytest.raises(ValueError):
        reporting_svc.create_report(portfolio_id=1, report_type="bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting_legacy_retirement.py -v`
Expected: FAIL — the writers still exist and `DEFAULT_TEMPLATE_SLUG` is undefined.

- [ ] **Step 3: Delete the legacy writers**

In `backend/app/services/reports.py`, delete `_write_html`, `_write_xlsx`,
`_build_report_payload`, `_metric_card`, `_MONEY_DISPLAY`, `_SHARED_DISPLAY`,
`_report_status_from_payload`, `_pricing_profile_payload`, `_report_artifact_stem`, and
`_safe_artifact_name`, plus their now-unused imports (`openpyxl`, `Path`, `re`,
`SimpleNamespace`, `calculate_portfolio_risk`, `resolve_positions`,
`pricing_position_markets`, `RFQ`, `PricingParameterProfile`).

Rewrite `_complete_report_job` to delegate:

```python
def _complete_report_job(
    session: Session,
    settings: Settings,
    job: ReportJob,
    request: ReportJobCreate,
    *,
    task_id: int | None = None,
) -> None:
    """Fill a queued ReportJob by generating its templated document.

    The legacy hardcoded HTML/XLSX writer is gone; there is one report writer.
    """
    from app.services.reporting.generate import generate_document

    if task_id is not None:
        update_task_progress(
            session, task_id, current=0, total=2, message="Resolving report blocks"
        )

    job.status = ReportStatus.RUNNING.value
    template_slug = (
        job.template_slug
        or (job.request_payload or {}).get("template_slug")
        or DEFAULT_TEMPLATE_SLUG
    )
    document = generate_document(
        template_slug=template_slug,
        portfolio_id=request.portfolio_id,
        session=session,
    )

    if task_id is not None:
        update_task_progress(
            session, task_id, current=1, total=2, message="Assembling document"
        )

    job.template_slug = template_slug
    job.compare_to_run_id = document["params"]["compare_to_run_id"]
    job.result_payload = document
    job.artifact_paths = {}
    job.status = _status_from_document(document)

    if task_id is not None:
        update_task_progress(
            session, task_id, current=2, total=2, message="Report generated"
        )
    session.flush()
```

Import `DEFAULT_TEMPLATE_SLUG` and `_status_from_document` from the new modules at the top of
the file, and note that `narrate` is deliberately omitted — the queued legacy path generates the
deterministic `portfolio-snapshot` template, which has no narrative sections.

- [ ] **Step 4: Route `create_report` through the pipeline**

In `backend/app/services/domains/reporting.py`, add near the top:

```python
DEFAULT_TEMPLATE_SLUG = "portfolio-snapshot"
```

And in `create_report`, thread the template slug into the queued request payload so
`_complete_report_job` picks it up:

```python
def create_report(
    *,
    portfolio_id: int,
    report_type: ReportType = "portfolio",
    title: str = "Agent Generated Desk Report",
    pricing_profile_id: int | None = None,
    template_slug: str = DEFAULT_TEMPLATE_SLUG,
    session: Session | None = None,
) -> dict[str, Any]:
```

Inside, after `queue_report_job`, set `job.template_slug = template_slug` before the commit, and
add `"template_slug": template_slug` to the returned dict.

- [ ] **Step 5: Update the legacy tests**

Run: `.venv/bin/python -m pytest tests/test_services_domains_reporting.py tests/test_tools_reporting.py tests/test_agent_tools.py -v`

Tests asserting the old HTML/XLSX artifact paths or the old `result_payload` shape now fail
correctly. Update each to assert the templated shape:

```python
    assert result["template_slug"] == "portfolio-snapshot"
    assert set(job.result_payload) >= {"template", "params", "sections", "provenance"}
```

Any assertion on `artifact_paths["html"]` should be deleted — artifacts are now produced by
export on demand, not written at generation time.

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting_legacy_retirement.py -v`
Expected: PASS, 4 tests

- [ ] **Step 7: Run the golden-workflow regression gate**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_golden_workflow_regression.py tests/test_arena_scoring.py tests/test_high_board_guards.py -v`

Expected: PASS with the denominators unchanged — flagship 39/39, risk-limit-breach 38/38. These
tests are the reason `create_report` kept its name; if any fails, the prohibition wiring
changed and must be restored, not re-pinned.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/reports.py backend/app/services/domains/reporting.py tests/
git commit -m "refactor(reporting): delete the legacy hardcoded report writer

_write_html/_write_xlsx/_build_report_payload are gone; create_report now
routes through the seeded portfolio-snapshot template. The TOOL NAME is
retained deliberately: it is a graded tool_not_called prohibition in three
golden workflows, and deleting it would make four checks trivially pass."
```

---

### Task 7: Final gate

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Never pipe through `tail`.

- [ ] **Step 2: Run the frontend suite and compare against `main`**

Run: `cd frontend && npm test 2>&1 | tail -40`

The vitest suite is flaky under load — `main` alone varies 12→18 failures run to run. Capture
the failing-file set, then run the same command on a clean `main` checkout and compare. Only
files failing on the branch but not on `main` are yours to fix.

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Update `CHANGELOG.md`**

Under `## [Unreleased]` → `### Added`:

```markdown
- **Report module redesign** — reports are now generated from declarative YAML
  templates. A server-owned block registry (18 producers across risk, P&L,
  limits, RFQ, positions and audit) resolves every number deterministically;
  the agent contributes only narrative prose, which is checked against the
  section's data by a numeric grounding guard. Four templates ship seeded:
  `trader-daily`, `risk-manager-daily`, `high-board-daily`, and the
  `portfolio-snapshot` compatibility template. The Reports page renders the
  document natively with provenance, and gains a Templates tab with a
  validating YAML editor. New: `/api/reports/{blocks,templates,generate}` and
  six agent tools.
- **P&L producers (`backend/app/services/pnl/`)** — risk-run snapshot diff,
  Greeks-based attribution with residual, and inception-P&L basis accounting.
```

Under `### Changed`:

```markdown
- `create_report` now routes through the seeded `portfolio-snapshot` template.
  The hardcoded HTML/XLSX writer (`_write_html` / `_write_xlsx`) is deleted.
  The tool name is retained deliberately — it is a graded `tool_not_called`
  prohibition in three golden workflows.
- `PERSONA_WORKFLOW_DOMAINS["trader"]` now includes `reporting`, so trader
  templates are routable.
- `PnlAttribution` is renamed `GreeksByPosition`; it renders a Greeks table,
  not a P&L decomposition.
```

- [ ] **Step 4: Update `README.md`**

Add the Reports module to the user-facing feature list: templates, generation, and the
Templates tab.

- [ ] **Step 5: Update `CLAUDE.md`**

Add a `## Report module` section after "Trade confirmation → book", covering: the package
layout, the block registry and its tri-state contract, validate-then-commit template saves,
the narration seam and grounding guard, and these gotchas:

- `empty` vs `unavailable` is load-bearing — never collapse them.
- `create_report` keeps its name because three golden workflows grade it as a prohibition.
- A block must be in `DEEP_AGENT_TOOL_NAMES`, and a skill needs a `routing:` line, or neither
  is reachable.
- Reports embed their own template spec, so editing a template never rewrites history.
- `scenario.latest_grid` ships permanently `unavailable` until a scenario producer exists.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md README.md CLAUDE.md
git commit -m "docs: report module redesign"
```

---

## Self-Review

**Spec coverage (§5.7, §5.10, §5.11):**

| Spec requirement | Task |
|---|---|
| `PnlAttribution` → `GreeksByPosition` rename | Task 1 |
| `Waterfall`, the one new renderer | Task 2 |
| Renderers map to existing primitives | Task 3 |
| `Tile`'s unused `delta` slot drives `delta_metric_row` | Task 3 (`priorMetrics`) |
| `empty` and `unavailable` render distinctly | Task 3, tests 3–5 |
| Sections, narrative, provenance drawer | Task 4 |
| Coverage surfaced in the header | Task 4, test 3 |
| Grounding flags visible to the reader | Task 4, test 5 |
| `MasterDetailPage` with report list rail | Task 5 |
| Templates tab with validating editor | Task 5 |
| Legacy `result_payload` still readable | Task 5, test 4 |
| `_write_html` / `_write_xlsx` deleted | Task 6 |
| `create_report` retained and re-pointed | Task 6, tests 2–3 |
| Golden-workflow denominators unchanged | Task 6, step 7 |
| CHANGELOG / README / CLAUDE.md | Task 7 |

**Deliberately not implemented:** the spec's §5.11 Export (HTML/XLSX/PDF) and Regenerate
actions. Export re-rendering from the `ReportDocument` is a self-contained follow-on, and
shipping it half-done would recreate the file-versus-screen divergence the redesign exists to
fix. The Reports page ships without those two buttons; note this in the CHANGELOG entry if you
want it visible to users.

**Placeholder scan:** No TBD/TODO. Every CSS block carries the instruction to substitute a real
token rather than invent one, because the exact token names in `src/tokens/` were not read
while writing this plan — that check is a required step, not an assumption.

**Type consistency:**
- `ReportBlock` / `ReportSection` / `ReportDocument` / `ReportTemplate` defined in Task 2 and
  imported unchanged in Tasks 3, 4 and 5.
- `BlockRenderer` props `{ block: ReportBlock }` — same in definition, test, and
  `ReportDocumentView`'s call.
- `Waterfall` props `{ data: WaterfallData }` — matches the `pnl.explain` block data shape from
  Plan A Task 3 (`buckets`, `explained`, `actual`, `residual`, `residual_ratio`,
  `residual_exceeds_threshold`).
- `TemplateEditor` props `{ template, onSave, onValidate }` with
  `onSave(slug, specYaml)` and `onValidate(specYaml) -> {ok, errors}` — same in definition,
  test, `Reports`, and `ReportsLive`.
- `Table` used with `columns` / `rows` / `rowKey`, matching `frontend/src/components/Table.tsx`.
- `Metric` used with `label` / `value` / `variant` / `delta`, matching `MetricRow.tsx`.
- `ChartAsset` used with `{chart_type, x_key, y_key, series}` + `title`, matching
  `ChartAsset.tsx`.
- `MasterDetailPage` used with `title` / `chips` / `rail` / `railWidth` / `children`, matching
  `templates/MasterDetailPage.tsx`.
- API shapes match Plan B4 Task 4: `GET /api/reports/templates`, `POST
  /api/reports/templates/validate` → `{ok, errors}`, `PUT /api/reports/templates/{slug}`,
  `POST /api/reports/generate`.

# Charting Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add chart rendering to the AssetCard component using Recharts, enabling the agent to emit visual charts (bar, line, area) that render in the Assets pane.

**Architecture:** Install Recharts as the charting library. Add a `ChartAsset` component that receives chart data and renders the appropriate chart type. Update `AssetCard` to delegate `kind="chart"` to `ChartAsset`. Backend already emits chart assets; we standardize the data format and make the frontend render it.

**Tech Stack:** React 19, Recharts, vanilla CSS with Warm Ledger tokens

---

## File Map

| File | Responsibility |
|------|---------------|
| `frontend/package.json` | Add `recharts` dependency |
| `frontend/src/components/ChartAsset.tsx` | NEW — Recharts-based chart renderer |
| `frontend/src/components/ChartAsset.css` | NEW — chart container styles |
| `frontend/src/components/ChartAsset.test.tsx` | NEW — tests for chart rendering |
| `frontend/src/components/AssetCard.tsx` | Modify — delegate `kind="chart"` to ChartAsset |
| `backend/app/main.py` | Minor — standardize chart data format |

---

## Chart Data Schema

The backend emits chart data as:

```json
{
  "chart_type": "bar",
  "x_key": "name",
  "y_key": "value",
  "series": [
    {"name": "delta_proxy", "value": 123.45},
    {"name": "gamma", "value": 0.023}
  ]
}
```

Supported `chart_type` values: `bar`, `line`, `area`.

---

### Task 1: Install Recharts

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install recharts**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npm install recharts`

- [ ] **Step 2: Verify build still passes**

Run: `npm run build`
Expected: Clean build

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "deps(frontend): add recharts for agent chart assets"
```

---

### Task 2: Create ChartAsset component

**Files:**
- Create: `frontend/src/components/ChartAsset.tsx`
- Create: `frontend/src/components/ChartAsset.css`
- Create: `frontend/src/components/ChartAsset.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChartAsset } from './ChartAsset';

describe('ChartAsset', () => {
  it('renders a bar chart from series data', () => {
    const data = {
      chart_type: 'bar' as const,
      x_key: 'name',
      y_key: 'value',
      series: [
        { name: 'delta', value: 100 },
        { name: 'gamma', value: 0.5 },
      ],
    };
    const { container } = render(<ChartAsset data={data} title="Risk" />);
    expect(container.querySelector('.wl-chart-asset')).not.toBeNull();
    expect(screen.getByText('Risk')).toBeInTheDocument();
  });

  it('renders a line chart', () => {
    const data = {
      chart_type: 'line' as const,
      x_key: 'day',
      y_key: 'pnl',
      series: [
        { day: 'Mon', pnl: 10 },
        { day: 'Tue', pnl: 20 },
      ],
    };
    const { container } = render(<ChartAsset data={data} title="P&L" />);
    expect(container.querySelector('.wl-chart-asset')).not.toBeNull();
  });
});
```

Run: `npm test -- src/components/ChartAsset.test.tsx`
Expected: FAIL — ChartAsset not defined

- [ ] **Step 2: Implement ChartAsset**

```tsx
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import './ChartAsset.css';

type ChartData = {
  chart_type: 'bar' | 'line' | 'area';
  x_key: string;
  y_key: string;
  series: Record<string, number | string>[];
};

type Props = {
  data: ChartData;
  title: string;
};

export function ChartAsset({ data, title }: Props) {
  const { chart_type, x_key, y_key, series } = data;

  const chart = (() => {
    const common = (
      <>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
        <XAxis dataKey={x_key} tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }} />
        <YAxis tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }} />
        <Tooltip
          contentStyle={{
            background: 'var(--paper)',
            border: '1px solid var(--ink)',
            fontFamily: 'var(--font-numeric)',
            fontSize: 12,
          }}
        />
      </>
    );

    if (chart_type === 'bar') {
      return (
        <BarChart data={series}>
          {common}
          <Bar dataKey={y_key} fill="var(--ink)" />
        </BarChart>
      );
    }
    if (chart_type === 'line') {
      return (
        <LineChart data={series}>
          {common}
          <Line type="monotone" dataKey={y_key} stroke="var(--ink)" strokeWidth={2} dot={false} />
        </LineChart>
      );
    }
    return (
      <AreaChart data={series}>
        {common}
        <Area type="monotone" dataKey={y_key} stroke="var(--ink)" fill="var(--ink-2)" />
      </AreaChart>
    );
  })();

  return (
    <div className="wl-chart-asset">
      <div className="wl-chart-asset__title">{title}</div>
      <div className="wl-chart-asset__canvas">
        <ResponsiveContainer width="100%" height="100%">
          {chart}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add CSS**

```css
.wl-chart-asset {
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-chart-asset__title {
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-chart-asset__canvas {
  width: 100%;
  height: 200px;
}
```

Run: `npm test -- src/components/ChartAsset.test.tsx`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ChartAsset.tsx frontend/src/components/ChartAsset.css frontend/src/components/ChartAsset.test.tsx
git commit -m "feat(frontend): add ChartAsset component with Recharts"
```

---

### Task 3: Wire ChartAsset into AssetCard

**Files:**
- Modify: `frontend/src/components/AssetCard.tsx`
- Modify: `frontend/src/components/AssetCard.css`

- [ ] **Step 1: Update AssetCard to render ChartAsset for chart kind**

```tsx
import React, { useState } from 'react';
import type { AgentAsset } from '../types';
import { ChartAsset } from './ChartAsset';
import './AssetCard.css';

const kindLabel: Record<AgentAsset['kind'], string> = {
  file: 'FILE',
  image: 'IMG',
  table: 'TBL',
  chart: 'CHRT',
  json: 'JSON',
  markdown: 'MD',
};

type Props = {
  asset: AgentAsset;
  subtitle?: string;
  actions?: React.ReactNode;
};

export function AssetCard({ asset, subtitle, actions }: Props) {
  const [expanded, setExpanded] = useState(false);
  const isChart = asset.kind === 'chart';

  return (
    <div className="wl-asset">
      <div className="wl-asset__icon">{kindLabel[asset.kind]}</div>
      <div className="wl-asset__meta">
        <div className="wl-asset__title">{asset.title}</div>
        {subtitle && <div className="wl-asset__sub">{subtitle}</div>}
      </div>
      {actions && <div className="wl-asset__actions">{actions}</div>}
      {isChart && (
        <button
          className="wl-asset__expand"
          onClick={() => setExpanded((prev) => !prev)}
          aria-label={expanded ? 'Collapse chart' : 'Expand chart'}
        >
          {expanded ? '▾' : '▸'}
        </button>
      )}
      {isChart && expanded && (
        <div className="wl-asset__chart">
          <ChartAsset data={asset.data} title={asset.title} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add AssetCard CSS for expanded chart**

```css
.wl-asset__expand {
  background: none;
  border: 0;
  color: var(--ink-2);
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  cursor: pointer;
  padding: 0 4px;
}
.wl-asset__chart {
  grid-column: 1 / -1;
  margin-top: var(--gap-2);
  padding-top: var(--gap-2);
  border-top: 1px solid var(--hairline);
}
```

Note: AssetCard uses CSS Grid or Flex. The `.wl-asset__chart` uses `grid-column: 1 / -1` to span full width if the parent is a grid. Check `AssetCard.css` — if it's flex, adjust accordingly.

- [ ] **Step 3: Add test**

```tsx
it('renders ChartAsset when kind is chart and expanded', async () => {
  const asset: AgentAsset = {
    id: 'chart-1',
    kind: 'chart',
    title: 'Risk',
    data: {
      chart_type: 'bar',
      x_key: 'name',
      y_key: 'value',
      series: [{ name: 'a', value: 1 }],
    },
  };
  render(<AssetCard asset={asset} />);
  await userEvent.click(screen.getByLabelText(/expand chart/i));
  expect(document.querySelector('.wl-chart-asset')).not.toBeNull();
});
```

Run: `npm test -- src/components/AssetCard.test.tsx`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AssetCard.tsx frontend/src/components/AssetCard.css frontend/src/components/AssetCard.test.tsx
git commit -m "feat(frontend): wire ChartAsset into AssetCard with expand toggle"
```

---

### Task 4: Standardize backend chart data format

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Update chart asset generation**

Find the chart asset generation in `main.py` (around line 287) and update to the standardized format:

```python
AgentAssetOut(
    id=f"risk-chart-{portfolio.id}",
    kind="chart",
    title="Risk totals",
    data={
        "chart_type": "bar",
        "x_key": "name",
        "y_key": "value",
        "series": [{"name": key, "value": value} for key, value in totals.items()],
    },
),
```

- [ ] **Step 2: Verify existing test**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(backend): standardize chart asset data format"
```

---

### Task 5: Integration smoke

- [ ] **Step 1: Run all tests**

Backend: `cd /Users/fuxinyao/open-otc-trading && python -m pytest -q`
Frontend: `cd /Users/fuxinyao/open-otc-trading/frontend && npm test`
TypeScript: `cd /Users/fuxinyao/open-otc-trading/frontend && npx tsc -b --noEmit`
Build: `cd /Users/fuxinyao/open-otc-trading/frontend && npm run build`

- [ ] **Step 2: Commit**

```bash
git commit --allow-empty -m "test: integration smoke for charting library"
```

---

## Self-Review

**Spec coverage:**
- ✅ Recharts installed — Task 1
- ✅ ChartAsset component (bar/line/area) — Task 2
- ✅ Warm Ledger token theming (colors, fonts) — Task 2
- ✅ AssetCard delegates chart kind — Task 3
- ✅ Expand/collapse toggle — Task 3
- ✅ Backend format standardization — Task 4
- ✅ Tests — all tasks

**Placeholder scan:** None found.

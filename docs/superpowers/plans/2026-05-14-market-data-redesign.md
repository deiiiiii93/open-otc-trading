# Market Data Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Market Data page into a Bloomberg-style terminal layout with a ticker strip, searchable grouped instrument list, date range filtering, and a dense color-coded OHLCV table — all using the existing repo design tokens.

**Architecture:** Two files change (`MarketData.tsx` full rewrite, `MarketData.css` full rewrite). A new co-located `marketDataUtils.ts` extracts three pure helper functions (`groupBy`, `periodToDateFrom`, `computeStats`) so they can be unit-tested in isolation. `MarketData.live.tsx`, all API code, and `MarketDataProfile` type remain untouched.

**Tech Stack:** React 18, TypeScript, CSS custom properties (repo design tokens), Vitest + @testing-library/react, existing `Table`/`Modal`/`Button`/`Empty` components.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `frontend/src/routes/marketDataUtils.ts` | Pure helpers: groupBy, periodToDateFrom, computeStats |
| Create | `frontend/src/routes/marketDataUtils.test.ts` | Unit tests for the three helpers |
| Rewrite | `frontend/src/routes/MarketData.css` | All layout and component styles |
| Rewrite | `frontend/src/routes/MarketData.tsx` | Presentational component — full new layout |
| Create | `frontend/src/routes/MarketData.test.tsx` | Component integration tests |
| No change | `frontend/src/routes/MarketData.live.tsx` | — |
| No change | `frontend/src/types.ts` | — |

---

## Task 1: Write new CSS

**Files:**
- Rewrite: `frontend/src/routes/MarketData.css`

- [ ] **Step 1: Replace the entire file with the new CSS**

```css
/* ── Page container ──────────────────────────────────────── */
.wl-market-data {
  display: flex;
  flex-direction: column;
  background: var(--paper);
}

/* ── Zone 1: Ticker strip ────────────────────────────────── */
.wl-market-data__ticker {
  display: flex;
  align-items: stretch;
  overflow-x: auto;
  background: var(--paper-2);
  border-bottom: 1px solid var(--hairline-2);
  height: 34px;
  flex-shrink: 0;
}

.wl-market-data__ticker-item {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  padding: 0 var(--gap-3);
  border-right: 1px solid var(--hairline);
  border-top: 0;
  border-bottom: 2px solid transparent;
  border-left: 0;
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-ui);
}
.wl-market-data__ticker-item:first-child { border-left: 1px solid var(--hairline); }
.wl-market-data__ticker-item:hover { background: var(--paper-3); }
.wl-market-data__ticker-item.is-active { background: var(--paper); border-bottom-color: var(--warn); }

.wl-market-data__ticker-sym {
  font-family: var(--font-numeric);
  font-size: var(--type-num-s-size);
  font-weight: 700;
}
.wl-market-data__ticker-price {
  font-family: var(--font-numeric);
  font-size: var(--type-num-s-size);
  color: var(--ink);
}
.wl-market-data__ticker-chg {
  font-family: var(--font-numeric);
  font-size: 10px;
}

/* ── Zone 2: Command bar ─────────────────────────────────── */
.wl-market-data__cmd-bar {
  display: flex;
  align-items: center;
  gap: var(--gap-3);
  padding: var(--gap-2) var(--gap-3);
  background: var(--paper);
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
  flex-wrap: wrap;
}

.wl-market-data__search-label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  white-space: nowrap;
}

.wl-market-data__search-input {
  flex: 1;
  min-width: 180px;
  max-width: 320px;
  border: 1px solid var(--hairline-2);
  background: var(--paper-2);
  color: var(--ink);
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  padding: 4px var(--gap-2);
  outline: none;
}
.wl-market-data__search-input:focus { outline: 2px solid var(--ink); outline-offset: 2px; }
.wl-market-data__search-input::placeholder { color: var(--ink-2); opacity: 0.5; }

.wl-market-data__period-group {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--gap-1);
  flex-wrap: wrap;
}

.wl-market-data__period-btn {
  border: 1px solid var(--hairline-2);
  background: transparent;
  color: var(--ink-2);
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 3px 8px;
  cursor: pointer;
}
.wl-market-data__period-btn:hover { background: var(--paper-2); color: var(--ink); }
.wl-market-data__period-btn.is-active { background: var(--ink); color: var(--paper); border-color: var(--ink); }

.wl-market-data__date-input {
  border: 1px solid var(--hairline-2);
  background: var(--paper-2);
  color: var(--ink);
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  padding: 3px var(--gap-2);
  width: 96px;
  outline: none;
}
.wl-market-data__date-input:focus { outline: 2px solid var(--ink); outline-offset: 2px; }

/* ── Zone 3: Body ────────────────────────────────────────── */
.wl-market-data__body {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  flex: 1;
  min-height: 0;
}

/* ── Left panel ──────────────────────────────────────────── */
.wl-market-data__list-panel {
  background: var(--paper-2);
  border-right: 1px solid var(--hairline-2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.wl-market-data__list-header {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  padding: var(--gap-2) var(--gap-3);
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
}

.wl-market-data__list-title {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}

.wl-market-data__list-count {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--warn);
  margin-right: auto;
}

.wl-market-data__list-scroll { overflow-y: auto; flex: 1; }

.wl-market-data__group-header {
  display: flex;
  justify-content: space-between;
  padding: var(--gap-1) var(--gap-3);
  background: var(--paper-3);
  border-bottom: 1px solid var(--hairline);
  border-top: 1px solid var(--hairline);
}

.wl-market-data__group-name {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}

.wl-market-data__inst-row {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: var(--gap-2);
  padding: 6px var(--gap-3);
  border-bottom: 1px solid var(--hairline);
  border-left: 2px solid transparent;
  cursor: pointer;
  background: transparent;
  width: 100%;
  text-align: left;
  color: var(--ink);
  font-family: var(--font-ui);
}
.wl-market-data__inst-row:hover { background: var(--paper-3); }
.wl-market-data__inst-row.is-active { background: var(--paper); border-left-color: var(--warn); }

.wl-market-data__inst-sym {
  font-family: var(--font-numeric);
  font-size: var(--type-num-s-size);
  font-weight: 700;
  color: var(--ink);
}
.wl-market-data__inst-date {
  font-family: var(--font-numeric);
  font-size: 10px;
  color: var(--ink-2);
  margin-top: 2px;
}
.wl-market-data__inst-prices { text-align: right; }
.wl-market-data__inst-price {
  font-family: var(--font-numeric);
  font-size: var(--type-num-s-size);
}
.wl-market-data__inst-chg {
  font-family: var(--font-numeric);
  font-size: 10px;
  margin-top: 2px;
}

/* ── Right panel ─────────────────────────────────────────── */
.wl-market-data__detail-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--paper);
}

.wl-market-data__sec-header {
  display: flex;
  align-items: center;
  gap: var(--gap-4);
  padding: var(--gap-2) var(--gap-4);
  background: var(--paper-2);
  border-bottom: 1px solid var(--hairline-2);
  flex-shrink: 0;
}

.wl-market-data__sec-sym {
  font-family: var(--font-numeric);
  font-size: 18px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.wl-market-data__sec-desc {
  font-size: var(--type-small-size);
  color: var(--ink-2);
  margin-top: 2px;
}

.wl-market-data__sec-tabs {
  margin-left: auto;
  display: flex;
  border: 1px solid var(--hairline-2);
}
.wl-market-data__sec-tab {
  padding: 4px 14px;
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-right: 1px solid var(--hairline-2);
  font-family: var(--font-ui);
}
.wl-market-data__sec-tab:last-child { border-right: 0; }
.wl-market-data__sec-tab:hover { background: var(--paper-3); color: var(--ink); }
.wl-market-data__sec-tab.is-active { background: var(--ink); color: var(--paper); }

/* ── Stats bar ───────────────────────────────────────────── */
.wl-market-data__stats-bar {
  display: flex;
  align-items: flex-end;
  gap: var(--gap-5);
  padding: var(--gap-3) var(--gap-4);
  border-bottom: 1px solid var(--hairline);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.wl-market-data__stat { display: flex; flex-direction: column; gap: 2px; }
.wl-market-data__stat-label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-market-data__stat-value {
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  font-weight: 500;
  color: var(--ink);
}
.wl-market-data__stat-value--big {
  font-size: var(--type-num-l-size);
  font-weight: 700;
  letter-spacing: -0.01em;
}

/* ── Color utilities ─────────────────────────────────────── */
.wl-md-pos { color: var(--pos); }
.wl-md-neg { color: var(--neg); }
.wl-md-neu { color: var(--ink-2); }

/* ── OHLCV table wrapper ─────────────────────────────────── */
.wl-market-data__table-wrap { overflow-y: auto; flex: 1; }

/* ── Describe tab ────────────────────────────────────────── */
.wl-market-data__describe {
  padding: var(--gap-4);
  overflow-y: auto;
  flex: 1;
}
.wl-market-data__describe-grid {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 1px;
  background: var(--hairline);
  border: 1px solid var(--hairline);
}
.wl-market-data__describe-key,
.wl-market-data__describe-val {
  padding: var(--gap-2) var(--gap-3);
  background: var(--paper);
}
.wl-market-data__describe-key {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  background: var(--paper-2);
}
.wl-market-data__describe-val {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink);
  word-break: break-all;
}
.wl-market-data__describe-json {
  padding: var(--gap-3);
  background: var(--paper-3);
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-all;
  border: 1px solid var(--hairline);
  margin-top: var(--gap-3);
}

/* ── Status bar ──────────────────────────────────────────── */
.wl-market-data__status-bar {
  display: flex;
  align-items: center;
  gap: var(--gap-4);
  padding: 0 var(--gap-3);
  height: 24px;
  background: var(--paper-2);
  border-top: 1px solid var(--hairline-2);
  flex-shrink: 0;
}
.wl-market-data__status-item {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
  white-space: nowrap;
}
.wl-market-data__status-dot {
  width: 6px;
  height: 6px;
  background: var(--pos);
  border-radius: 50%;
  display: inline-block;
  margin-right: 4px;
}
.wl-market-data__status-hl { color: var(--warn); }

/* ── Feedback ────────────────────────────────────────────── */
.wl-market-data__feedback {
  padding: var(--gap-2) var(--gap-3);
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
}
.wl-market-data__feedback--success { border: 1px solid var(--pos); color: var(--pos); }
.wl-market-data__feedback--error   { border: 1px solid var(--neg); color: var(--neg); }

/* ── Add profile form (inside Modal) ─────────────────────── */
.wl-market-data__form { display: grid; gap: var(--gap-3); }
.wl-market-data__form label {
  display: grid;
  gap: var(--gap-1);
  color: var(--ink-2);
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-market-data__form input,
.wl-market-data__form select {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  padding: var(--input-padding-y) var(--input-padding-x);
  border-radius: 0;
}
.wl-market-data__form-actions { display: flex; justify-content: flex-end; }

@media (max-width: 900px) {
  .wl-market-data__body { grid-template-columns: 1fr; }
}
```

- [ ] **Step 2: Verify no TypeScript errors from CSS change**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors (CSS changes don't affect TS).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/MarketData.css
git commit -m "style(market-data): new bloomberg-style layout CSS"
```

---

## Task 2: Helper utilities (TDD)

**Files:**
- Create: `frontend/src/routes/marketDataUtils.ts`
- Create: `frontend/src/routes/marketDataUtils.test.ts`

- [ ] **Step 1: Create the utility file with type definitions and empty stubs**

Create `frontend/src/routes/marketDataUtils.ts`:

```ts
export type OhlcRow = {
  date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume?: number | null;
};

export type Period = '1M' | '3M' | '6M' | '1Y' | 'all';

export type OhlcStats = {
  last: number | null;
  chg: number | null;
  chgPct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
};

export function groupBy<T>(arr: T[], getKey: (item: T) => string): Map<string, T[]> {
  throw new Error('not implemented');
}

export function periodToDateFrom(endDate: string, period: Period): string | null {
  throw new Error('not implemented');
}

export function computeStats(rows: OhlcRow[]): OhlcStats {
  throw new Error('not implemented');
}
```

- [ ] **Step 2: Write all tests**

Create `frontend/src/routes/marketDataUtils.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { groupBy, periodToDateFrom, computeStats, type OhlcRow } from './marketDataUtils';

describe('groupBy', () => {
  it('groups items by key', () => {
    const items = [
      { name: 'A', group: 'x' },
      { name: 'B', group: 'x' },
      { name: 'C', group: 'y' },
    ];
    const result = groupBy(items, (i) => i.group);
    expect(result.get('x')).toEqual([{ name: 'A', group: 'x' }, { name: 'B', group: 'x' }]);
    expect(result.get('y')).toEqual([{ name: 'C', group: 'y' }]);
  });

  it('uses "Other" for empty string key', () => {
    const result = groupBy([{ v: 1 }], () => '');
    expect(result.has('Other')).toBe(true);
    expect(result.has('')).toBe(false);
  });

  it('returns empty map for empty array', () => {
    expect(groupBy([], () => 'x').size).toBe(0);
  });
});

describe('periodToDateFrom', () => {
  it('returns null for "all"', () => {
    expect(periodToDateFrom('2026-03-01', 'all')).toBeNull();
  });

  it('returns 1 month back for "1M"', () => {
    expect(periodToDateFrom('2026-03-01', '1M')).toBe('2026-02-01');
  });

  it('returns 3 months back for "3M"', () => {
    expect(periodToDateFrom('2026-06-01', '3M')).toBe('2026-03-01');
  });

  it('returns 6 months back for "6M"', () => {
    expect(periodToDateFrom('2026-03-01', '6M')).toBe('2025-09-01');
  });

  it('returns 12 months back for "1Y"', () => {
    expect(periodToDateFrom('2026-03-01', '1Y')).toBe('2025-03-01');
  });
});

describe('computeStats', () => {
  it('returns all nulls for empty array', () => {
    const s = computeStats([]);
    expect(s.last).toBeNull();
    expect(s.chg).toBeNull();
    expect(s.chgPct).toBeNull();
    expect(s.open).toBeNull();
    expect(s.high).toBeNull();
    expect(s.low).toBeNull();
  });

  it('returns null chg/chgPct with only one row', () => {
    const rows: OhlcRow[] = [{ date: '2026-01-01', close: 100, open: 98, high: 102, low: 97 }];
    const s = computeStats(rows);
    expect(s.last).toBe(100);
    expect(s.chg).toBeNull();
    expect(s.chgPct).toBeNull();
    expect(s.open).toBe(98);
    expect(s.high).toBe(102);
    expect(s.low).toBe(97);
  });

  it('computes chg and chgPct from last two rows', () => {
    const rows: OhlcRow[] = [
      { date: '2026-01-01', close: 100 },
      { date: '2026-01-02', close: 102 },
    ];
    const s = computeStats(rows);
    expect(s.last).toBe(102);
    expect(s.chg).toBe(2);
    expect(s.chgPct).toBeCloseTo(2.0);
  });

  it('returns null chg/chgPct when previous close is null', () => {
    const rows: OhlcRow[] = [
      { date: '2026-01-01', close: null },
      { date: '2026-01-02', close: 100 },
    ];
    const s = computeStats(rows);
    expect(s.chg).toBeNull();
    expect(s.chgPct).toBeNull();
  });
});
```

- [ ] **Step 3: Run tests — verify they all fail**

```bash
cd frontend && npx vitest run src/routes/marketDataUtils.test.ts 2>&1 | tail -20
```

Expected: all tests fail with "Error: not implemented".

- [ ] **Step 4: Implement `groupBy`**

In `frontend/src/routes/marketDataUtils.ts`, replace the `groupBy` stub:

```ts
export function groupBy<T>(arr: T[], getKey: (item: T) => string): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of arr) {
    const key = getKey(item) || 'Other';
    const group = map.get(key) ?? [];
    group.push(item);
    map.set(key, group);
  }
  return map;
}
```

- [ ] **Step 5: Implement `periodToDateFrom`**

Replace the `periodToDateFrom` stub:

```ts
export function periodToDateFrom(endDate: string, period: Period): string | null {
  if (period === 'all') return null;
  const months: Record<Exclude<Period, 'all'>, number> = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12 };
  const d = new Date(endDate);
  d.setMonth(d.getMonth() - months[period]);
  return d.toISOString().slice(0, 10);
}
```

- [ ] **Step 6: Implement `computeStats`**

Replace the `computeStats` stub:

```ts
export function computeStats(rows: OhlcRow[]): OhlcStats {
  const last = rows.at(-1) ?? null;
  const prev = rows.at(-2) ?? null;
  const lastClose = last?.close ?? null;
  const prevClose = prev?.close ?? null;
  const chg = lastClose != null && prevClose != null ? lastClose - prevClose : null;
  const chgPct = chg != null && prevClose ? (chg / prevClose) * 100 : null;
  return {
    last: lastClose,
    chg,
    chgPct,
    open: last?.open ?? null,
    high: last?.high ?? null,
    low: last?.low ?? null,
  };
}
```

- [ ] **Step 7: Run tests — verify they all pass**

```bash
cd frontend && npx vitest run src/routes/marketDataUtils.test.ts 2>&1 | tail -10
```

Expected: all 12 tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/marketDataUtils.ts frontend/src/routes/marketDataUtils.test.ts
git commit -m "feat(market-data): add groupBy, periodToDateFrom, computeStats helpers"
```

---

## Task 3: Rewrite the presentational component

**Files:**
- Rewrite: `frontend/src/routes/MarketData.tsx`

> `profile.data` shape: `{ rows: OhlcRow[], latest: OhlcRow, spot: number }`. The `OhlcRow` type is now imported from `marketDataUtils.ts`.

- [ ] **Step 1: Replace the entire file**

```tsx
import React, { useMemo, useState, type FormEvent } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { Table, type Column } from '../components/Table';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type { MarketDataProfile, PageContext, PageContextReporter } from '../types';
import {
  groupBy,
  periodToDateFrom,
  computeStats,
  type OhlcRow,
  type Period,
} from './marketDataUtils';
import './MarketData.css';

type OhlcRowWithChg = OhlcRow & { chg: number | null; chgPct: number | null };
type Tab = 'historical' | 'describe';

export type MarketDataFetchPayload = {
  symbol: string;
  assetClass: 'stock' | 'index' | 'futures';
  startDate: string;
  endDate: string;
  adjust: string;
  name: string;
};

export type MarketDataFeedback = {
  tone: 'success' | 'error';
  message: string;
};

type Props = {
  profiles: MarketDataProfile[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  fetching: boolean;
  feedback: MarketDataFeedback | null;
  onSelectProfile: (id: number) => void;
  onFetch: (payload: MarketDataFetchPayload) => void;
  onPageContextChange?: PageContextReporter;
};

const PERIODS: Period[] = ['1M', '3M', '6M', '1Y', 'all'];

export function MarketData({
  profiles,
  selectedId,
  loading,
  error,
  fetching,
  feedback,
  onSelectProfile,
  onFetch,
  onPageContextChange,
}: Props) {
  // ── Add profile modal state ──────────────────────────────────────────────
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [symbol, setSymbol] = useState('000300');
  const [assetClass, setAssetClass] = useState<MarketDataFetchPayload['assetClass']>('index');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [adjust, setAdjust] = useState('qfq');

  // ── Page UI state ────────────────────────────────────────────────────────
  const [query, setQuery] = useState('');
  const [period, setPeriod] = useState<Period>('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [activeTab, setActiveTab] = useState<Tab>('historical');

  // ── Selected profile + raw rows ──────────────────────────────────────────
  const selected = useMemo(
    () => profiles.find((p) => p.id === selectedId) ?? profiles[0] ?? null,
    [profiles, selectedId],
  );
  const rows = (selected?.data?.rows ?? []) as OhlcRow[];
  const latest = selected?.data?.latest as OhlcRow | undefined;

  // ── Search filter ────────────────────────────────────────────────────────
  const q = query.toLowerCase();
  const visibleProfiles = profiles.filter(
    (p) => !q || p.symbol.toLowerCase().includes(q) || p.end_date.includes(query),
  );

  // ── Grouped left panel ───────────────────────────────────────────────────
  const groups = groupBy(visibleProfiles, (p) => p.asset_class || 'Other');
  const sortedGroups = [...groups.entries()].sort(([a], [b]) => {
    if (a === 'Other') return 1;
    if (b === 'Other') return -1;
    return a.localeCompare(b);
  });

  // ── Date range filter ────────────────────────────────────────────────────
  const effectiveDateFrom =
    period !== 'all' && selected ? periodToDateFrom(selected.end_date, period) : dateFrom || null;
  const effectiveDateTo = dateTo || null;

  const filteredRows = rows.filter(
    (r) =>
      (!effectiveDateFrom || r.date >= effectiveDateFrom) &&
      (!effectiveDateTo || r.date <= effectiveDateTo),
  );

  // ── Per-row change for the table ─────────────────────────────────────────
  const rowsWithChg: OhlcRowWithChg[] = filteredRows.map((row, i) => {
    const prev = filteredRows[i - 1];
    const chg =
      prev?.close != null && row.close != null ? row.close - prev.close : null;
    const chgPct = chg != null && prev?.close ? (chg / prev.close) * 100 : null;
    return { ...row, chg, chgPct };
  });

  // ── Stats from the filtered window ───────────────────────────────────────
  const stats = computeStats(filteredRows);

  // ── Page context ─────────────────────────────────────────────────────────
  const pageContext = useMemo((): PageContext => {
    const base: PageContext = {
      route: 'market-data',
      title: 'Market Data',
      path: '/',
      entity_ids: { market_data_profile_id: selected?.id ?? null },
      snapshot: {
        selected_profile: selected
          ? {
              id: selected.id,
              name: selected.name,
              source: selected.source,
              symbol: selected.symbol,
              asset_class: selected.asset_class,
              valuation_date: selected.valuation_date,
              latest,
              fallback: selected.source_metadata?.fallback ?? false,
            }
          : null,
        rows: rows.slice(0, 12),
      },
      chips: selected
        ? ['AKShare', selected.symbol, `spot ${fmt(selected.data?.spot)}`]
        : ['AKShare', loading ? 'loading' : 'empty'],
    };
    if (!modalOpen) return base;
    return {
      ...base,
      title: 'Add Market Data Profile dialog',
      snapshot: {
        parent_context: base,
        fetch_request: { symbol, asset_class: assetClass, start_date: startDate, end_date: endDate, adjust, name },
      },
      chips: ['dialog', 'Fetch AKShare', symbol],
    };
  }, [adjust, assetClass, endDate, latest, loading, modalOpen, name, rows, selected, startDate, symbol]);

  usePageContextReporter(pageContext, onPageContextChange);

  // ── OHLCV table columns ──────────────────────────────────────────────────
  const columns: Column<OhlcRowWithChg>[] = [
    { key: 'date', header: 'Date', width: '110px' },
    { key: 'open',   header: 'Open',   numeric: true, render: (r) => fmt(r.open) },
    { key: 'high',   header: 'High',   numeric: true, render: (r) => fmt(r.high) },
    { key: 'low',    header: 'Low',    numeric: true, render: (r) => fmt(r.low) },
    { key: 'close',  header: 'Close',  numeric: true, render: (r) => fmt(r.close) },
    {
      key: 'chg', header: 'Chg', numeric: true,
      render: (r) => <span className={colorClass(r.chg)}>{fmtChg(r.chg)}</span>,
    },
    {
      key: 'chgPct', header: 'Chg %', numeric: true,
      render: (r) => <span className={colorClass(r.chgPct)}>{fmtPct(r.chgPct)}</span>,
    },
    { key: 'volume', header: 'Vol', numeric: true, render: (r) => fmt(r.volume) },
  ];

  // ── Event handlers ───────────────────────────────────────────────────────
  const handlePeriod = (p: Period) => {
    setPeriod(p);
    if (p === 'all') { setDateFrom(''); setDateTo(''); }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onFetch({ symbol, assetClass, startDate, endDate, adjust, name });
    setModalOpen(false);
  };

  if (error) {
    return <Empty message={`Could not load market data: ${error}`} />;
  }

  return (
    <>
      <div className="wl-market-data">

        {/* Zone 1: Ticker strip */}
        <div className="wl-market-data__ticker" role="list" aria-label="Market data profiles">
          {visibleProfiles.map((p) => {
            const pRows = (p.data?.rows ?? []) as OhlcRow[];
            const lastClose = pRows.at(-1)?.close ?? null;
            const prevClose = pRows.at(-2)?.close ?? null;
            const pChgPct =
              lastClose != null && prevClose != null && prevClose !== 0
                ? ((lastClose - prevClose) / prevClose) * 100
                : null;
            return (
              <button
                key={p.id}
                className={`wl-market-data__ticker-item${selected?.id === p.id ? ' is-active' : ''}`}
                onClick={() => onSelectProfile(p.id)}
                type="button"
                role="listitem"
                aria-current={selected?.id === p.id ? 'true' : undefined}
              >
                <span className="wl-market-data__ticker-sym">{p.symbol}</span>
                <span className="wl-market-data__ticker-price">{fmt(lastClose)}</span>
                <span className={`wl-market-data__ticker-chg ${colorClass(pChgPct)}`}>
                  {fmtPct(pChgPct)}
                </span>
              </button>
            );
          })}
        </div>

        {/* Zone 2: Command bar */}
        <div className="wl-market-data__cmd-bar">
          <span className="wl-market-data__search-label">Search</span>
          <input
            className="wl-market-data__search-input"
            type="text"
            placeholder="Symbol or date — e.g. RB · 2026-03"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search profiles"
          />
          <div className="wl-market-data__period-group">
            <span className="wl-market-data__search-label">Period</span>
            {PERIODS.map((p) => (
              <button
                key={p}
                className={`wl-market-data__period-btn${period === p ? ' is-active' : ''}`}
                onClick={() => handlePeriod(p)}
                type="button"
              >
                {p === 'all' ? 'All' : p}
              </button>
            ))}
            <input
              className="wl-market-data__date-input"
              type="text"
              placeholder="From"
              value={dateFrom}
              onChange={(e) => { setDateFrom(e.target.value); setPeriod('all'); }}
              aria-label="Date from"
            />
            <span className="wl-market-data__search-label">–</span>
            <input
              className="wl-market-data__date-input"
              type="text"
              placeholder="To"
              value={dateTo}
              onChange={(e) => { setDateTo(e.target.value); setPeriod('all'); }}
              aria-label="Date to"
            />
          </div>
        </div>

        {/* Zone 3: Body */}
        <div className="wl-market-data__body">

          {/* Left panel */}
          <aside className="wl-market-data__list-panel" aria-label="Instrument list">
            <div className="wl-market-data__list-header">
              <span className="wl-market-data__list-title">Watchlist</span>
              <span className="wl-market-data__list-count">{profiles.length} profiles</span>
              <Button
                type="button"
                iconOnly
                variant="ghost"
                onClick={() => setModalOpen(true)}
                disabled={fetching}
                aria-label="Add profile"
              >
                <Plus size={14} aria-hidden="true" />
              </Button>
            </div>
            <div className="wl-market-data__list-scroll">
              {sortedGroups.length === 0 ? (
                <Empty message={loading ? 'Loading…' : 'No profiles match'} />
              ) : (
                sortedGroups.map(([groupName, groupProfiles]) => (
                  <div key={groupName}>
                    <div className="wl-market-data__group-header">
                      <span className="wl-market-data__group-name">{groupName}</span>
                      <span className="wl-market-data__group-name">{groupProfiles.length}</span>
                    </div>
                    {groupProfiles.map((p) => {
                      const pRows = (p.data?.rows ?? []) as OhlcRow[];
                      const lastClose = pRows.at(-1)?.close ?? null;
                      const prevClose = pRows.at(-2)?.close ?? null;
                      const pChgPct =
                        lastClose != null && prevClose != null && prevClose !== 0
                          ? ((lastClose - prevClose) / prevClose) * 100
                          : null;
                      return (
                        <button
                          key={p.id}
                          className={`wl-market-data__inst-row${selected?.id === p.id ? ' is-active' : ''}`}
                          onClick={() => onSelectProfile(p.id)}
                          type="button"
                          aria-current={selected?.id === p.id ? 'true' : undefined}
                        >
                          <div>
                            <div className="wl-market-data__inst-sym">{p.symbol}</div>
                            <div className="wl-market-data__inst-date">ends {p.end_date.slice(0, 10)}</div>
                          </div>
                          <div className="wl-market-data__inst-prices">
                            <div className={`wl-market-data__inst-price ${colorClass(pChgPct)}`}>
                              {fmt(lastClose)}
                            </div>
                            <div className={`wl-market-data__inst-chg ${colorClass(pChgPct)}`}>
                              {fmtPct(pChgPct)}
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          </aside>

          {/* Right panel */}
          <section className="wl-market-data__detail-panel">
            {selected ? (
              <>
                {/* Security header */}
                <div className="wl-market-data__sec-header">
                  <div>
                    <div className="wl-market-data__sec-sym">{selected.symbol}</div>
                    <div className="wl-market-data__sec-desc">
                      {[
                        selected.name,
                        selected.source?.toUpperCase(),
                        selected.asset_class,
                        selected.adjust ? `Adj: ${selected.adjust}` : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                  </div>
                  <div className="wl-market-data__sec-tabs" role="tablist">
                    {(['historical', 'describe'] as Tab[]).map((tab) => (
                      <button
                        key={tab}
                        className={`wl-market-data__sec-tab${activeTab === tab ? ' is-active' : ''}`}
                        onClick={() => setActiveTab(tab)}
                        type="button"
                        role="tab"
                        aria-selected={activeTab === tab}
                      >
                        {tab.charAt(0).toUpperCase() + tab.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {activeTab === 'historical' && (
                  <>
                    {/* Stats bar */}
                    <div className="wl-market-data__stats-bar">
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">Last</span>
                        <span className={`wl-market-data__stat-value wl-market-data__stat-value--big ${colorClass(stats.chg)}`}>
                          {fmt(stats.last)}
                        </span>
                      </div>
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">Chg</span>
                        <span className={`wl-market-data__stat-value ${colorClass(stats.chg)}`}>
                          {fmtChg(stats.chg)}
                        </span>
                      </div>
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">Chg %</span>
                        <span className={`wl-market-data__stat-value ${colorClass(stats.chgPct)}`}>
                          {fmtPct(stats.chgPct)}
                        </span>
                      </div>
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">Open</span>
                        <span className="wl-market-data__stat-value">{fmt(stats.open)}</span>
                      </div>
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">High</span>
                        <span className="wl-market-data__stat-value">{fmt(stats.high)}</span>
                      </div>
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">Low</span>
                        <span className="wl-market-data__stat-value">{fmt(stats.low)}</span>
                      </div>
                      <div className="wl-market-data__stat" style={{ marginLeft: 'auto' }}>
                        <span className="wl-market-data__stat-label">Date range</span>
                        <span className="wl-market-data__stat-value" style={{ fontSize: '12px', color: 'var(--ink-2)' }}>
                          {filteredRows.length > 0
                            ? `${filteredRows[0].date} – ${filteredRows.at(-1)!.date}`
                            : '—'}
                        </span>
                      </div>
                      <div className="wl-market-data__stat">
                        <span className="wl-market-data__stat-label">Rows</span>
                        <span className="wl-market-data__stat-value">{filteredRows.length}</span>
                      </div>
                    </div>

                    {/* OHLCV table — latest row is highlighted via selectedKey */}
                    <div className="wl-market-data__table-wrap">
                      {rowsWithChg.length > 0 ? (
                        <Table
                          columns={columns}
                          rows={rowsWithChg}
                          rowKey={(r) => r.date}
                          selectedKey={rowsWithChg.at(-1)?.date}
                        />
                      ) : (
                        <Empty message="No rows in selected date range." />
                      )}
                    </div>
                  </>
                )}

                {activeTab === 'describe' && (
                  <div className="wl-market-data__describe">
                    <div className="wl-market-data__describe-grid">
                      {(
                        [
                          ['ID', String(selected.id)],
                          ['Name', selected.name],
                          ['Source', selected.source],
                          ['Symbol', selected.symbol],
                          ['Asset class', selected.asset_class || '—'],
                          ['Start date', selected.start_date],
                          ['End date', selected.end_date],
                          ['Adjust', selected.adjust ?? '—'],
                          ['Valuation date', selected.valuation_date],
                          ['Created', selected.created_at],
                          ['Updated', selected.updated_at],
                        ] as [string, string][]
                      ).map(([key, val]) => (
                        <React.Fragment key={key}>
                          <div className="wl-market-data__describe-key">{key}</div>
                          <div className="wl-market-data__describe-val">{val}</div>
                        </React.Fragment>
                      ))}
                    </div>
                    {selected.source_metadata && (
                      <pre className="wl-market-data__describe-json">
                        {JSON.stringify(selected.source_metadata, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </>
            ) : (
              <Empty
                message={loading ? 'Loading market data profiles…' : 'No market data profiles yet.'}
              />
            )}
          </section>
        </div>

        {/* Zone 4: Status bar */}
        <div className="wl-market-data__status-bar">
          <span className="wl-market-data__status-item">
            <span className="wl-market-data__status-dot" />
            {loading ? 'Loading' : 'Ready'}
          </span>
          {selected && (
            <>
              <span className="wl-market-data__status-item">
                Source:{' '}
                <span className="wl-market-data__status-hl">{selected.source?.toUpperCase()}</span>
              </span>
              <span className="wl-market-data__status-item">
                Updated: {selected.updated_at.slice(0, 16).replace('T', ' ')}
              </span>
            </>
          )}
          <span className="wl-market-data__status-item" style={{ marginLeft: 'auto' }}>
            {filteredRows.length} rows · {profiles.length} profiles
          </span>
        </div>
      </div>

      {feedback && (
        <p
          className={`wl-market-data__feedback wl-market-data__feedback--${feedback.tone}`}
          role={feedback.tone === 'error' ? 'alert' : 'status'}
          aria-live={feedback.tone === 'error' ? undefined : 'polite'}
        >
          {feedback.message}
        </p>
      )}

      <Modal open={modalOpen} onOpenChange={setModalOpen} title="Add Market Data Profile">
        <form className="wl-market-data__form" onSubmit={submit}>
          <label>
            Profile name
            <input aria-label="Profile name" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Symbol
            <input aria-label="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          </label>
          <label>
            Asset class
            <select
              aria-label="Asset class"
              value={assetClass}
              onChange={(e) => setAssetClass(e.target.value as MarketDataFetchPayload['assetClass'])}
            >
              <option value="index">index</option>
              <option value="stock">stock</option>
              <option value="futures">futures</option>
            </select>
          </label>
          <label>
            Start date
            <input
              aria-label="Start date"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          <label>
            End date
            <input
              aria-label="End date"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </label>
          <label>
            Adjust
            <input aria-label="Adjust" value={adjust} onChange={(e) => setAdjust(e.target.value)} />
          </label>
          <div className="wl-market-data__form-actions">
            <Button
              type="submit"
              variant="primary"
              disabled={fetching || !symbol || !startDate || !endDate}
            >
              Fetch
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}

// ── Private format helpers ─────────────────────────────────────────────────────

function fmt(value: unknown): string {
  return value == null || value === ''
    ? '—'
    : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function fmtChg(value: number | null): string {
  if (value == null) return '—';
  return (value >= 0 ? '+' : '') + Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtPct(value: number | null): string {
  if (value == null) return '—';
  return (value >= 0 ? '+' : '') + value.toFixed(2) + '%';
}

function colorClass(value: number | null): string {
  if (value == null || value === 0) return 'wl-md-neu';
  return value > 0 ? 'wl-md-pos' : 'wl-md-neg';
}
```

- [ ] **Step 2: Check TypeScript**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors. If TS errors appear, fix them before proceeding. Common issues:
- `PageContext` type not matching the shape used — align with whatever `PageContext` expects in `types.ts`
- `Column<OhlcRowWithChg>` incompatible with `Table` — ensure `Table` accepts `Column<T>` generically (it does)

- [ ] **Step 3: Run all tests**

```bash
cd frontend && npx vitest run 2>&1 | tail -20
```

Expected: all existing tests pass (the utils tests from Task 2 continue to pass; MarketData.live.tsx is unchanged so no regressions there).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/MarketData.tsx
git commit -m "feat(market-data): bloomberg-style terminal layout with ticker, search, period filter"
```

---

## Task 4: Component integration tests

**Files:**
- Create: `frontend/src/routes/MarketData.test.tsx`

- [ ] **Step 1: Create the test file**

```tsx
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MarketData } from './MarketData';
import type { MarketDataProfile } from '../types';

function makeProfile(overrides: Partial<MarketDataProfile> = {}): MarketDataProfile {
  return {
    id: 1,
    name: 'Test Rebar',
    source: 'akshare',
    symbol: 'RB2610',
    asset_class: 'Metals',
    start_date: '2026-01-01',
    end_date: '2026-03-01',
    adjust: 'post',
    valuation_date: '2026-03-01',
    data: {
      rows: [
        { date: '2026-01-01', open: 3800, high: 3820, low: 3790, close: 3810, volume: 100_000 },
        { date: '2026-01-02', open: 3810, high: 3860, low: 3800, close: 3842, volume: 120_000 },
      ],
      latest: { date: '2026-01-02', close: 3842 },
      spot: 3842,
    },
    source_metadata: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-02T00:00:00',
    ...overrides,
  };
}

const noop = vi.fn();

const defaultProps = {
  profiles: [] as MarketDataProfile[],
  selectedId: null as number | null,
  loading: false,
  error: null as string | null,
  fetching: false,
  feedback: null,
  onSelectProfile: noop,
  onFetch: noop,
};

beforeEach(() => { noop.mockClear(); });

describe('MarketData', () => {
  it('renders an error message when error prop is set', () => {
    render(<MarketData {...defaultProps} error="network timeout" />);
    expect(screen.getByText(/network timeout/i)).toBeInTheDocument();
  });

  it('shows empty state when no profiles are loaded', () => {
    render(<MarketData {...defaultProps} />);
    expect(screen.getByText('No market data profiles yet.')).toBeInTheDocument();
  });

  it('renders all profile symbols in the ticker strip and instrument list', () => {
    const profiles = [
      makeProfile({ id: 1, symbol: 'RB2610' }),
      makeProfile({ id: 2, symbol: 'LH2609', asset_class: 'Agricultural' }),
    ];
    render(<MarketData {...defaultProps} profiles={profiles} selectedId={1} />);
    // Both symbols appear (at least once each — they appear in both ticker + list)
    expect(screen.getAllByText('RB2610').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('LH2609').length).toBeGreaterThanOrEqual(1);
  });

  it('calls onSelectProfile when a ticker chip is clicked', async () => {
    const onSelectProfile = vi.fn();
    const profiles = [makeProfile({ id: 42, symbol: 'RB2610' })];
    render(<MarketData {...defaultProps} profiles={profiles} selectedId={null} onSelectProfile={onSelectProfile} />);
    const ticker = screen.getByRole('list', { name: 'Market data profiles' });
    await userEvent.click(within(ticker).getAllByRole('listitem')[0]);
    expect(onSelectProfile).toHaveBeenCalledWith(42);
  });

  it('calls onSelectProfile when an instrument row is clicked', async () => {
    const onSelectProfile = vi.fn();
    const profiles = [makeProfile({ id: 7, symbol: 'RB2610' })];
    render(<MarketData {...defaultProps} profiles={profiles} selectedId={null} onSelectProfile={onSelectProfile} />);
    const list = screen.getByRole('complementary', { name: 'Instrument list' });
    await userEvent.click(within(list).getByText('RB2610'));
    expect(onSelectProfile).toHaveBeenCalledWith(7);
  });

  it('filters instrument list and ticker by symbol query', async () => {
    const profiles = [
      makeProfile({ id: 1, symbol: 'RB2610', asset_class: 'Metals' }),
      makeProfile({ id: 2, symbol: 'LH2609', asset_class: 'Agricultural' }),
    ];
    render(<MarketData {...defaultProps} profiles={profiles} selectedId={1} />);
    await userEvent.type(screen.getByLabelText('Search profiles'), 'LH');

    const list = screen.getByRole('complementary', { name: 'Instrument list' });
    expect(within(list).queryByText('RB2610')).not.toBeInTheDocument();
    expect(within(list).getByText('LH2609')).toBeInTheDocument();

    const ticker = screen.getByRole('list', { name: 'Market data profiles' });
    expect(within(ticker).queryByText('RB2610')).not.toBeInTheDocument();
    expect(within(ticker).getByText('LH2609')).toBeInTheDocument();
  });

  it('groups profiles by asset_class in the instrument list', () => {
    const profiles = [
      makeProfile({ id: 1, symbol: 'RB2610', asset_class: 'Metals' }),
      makeProfile({ id: 2, symbol: 'LH2609', asset_class: 'Agricultural' }),
    ];
    render(<MarketData {...defaultProps} profiles={profiles} selectedId={1} />);
    const list = screen.getByRole('complementary', { name: 'Instrument list' });
    expect(within(list).getByText('Metals')).toBeInTheDocument();
    expect(within(list).getByText('Agricultural')).toBeInTheDocument();
  });

  it('groups profiles with no asset_class under "Other"', () => {
    const profiles = [makeProfile({ id: 1, asset_class: '' })];
    render(<MarketData {...defaultProps} profiles={profiles} selectedId={1} />);
    expect(screen.getByText('Other')).toBeInTheDocument();
  });

  it('shows Chg and Chg % columns in the OHLCV table', () => {
    const profile = makeProfile({ id: 1 });
    render(<MarketData {...defaultProps} profiles={[profile]} selectedId={1} />);
    expect(screen.getByText('Chg')).toBeInTheDocument();
    expect(screen.getByText('Chg %')).toBeInTheDocument();
  });

  it('shows metadata fields when Describe tab is clicked', async () => {
    const profile = makeProfile({ id: 1, symbol: 'RB2610', source: 'akshare' });
    render(<MarketData {...defaultProps} profiles={[profile]} selectedId={1} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Describe' }));
    expect(screen.getByText('Symbol')).toBeInTheDocument();
    expect(screen.getByText('Source')).toBeInTheDocument();
  });

  it('slices OHLCV rows when a period preset is selected', async () => {
    const profile = makeProfile({
      id: 1,
      end_date: '2026-03-01',
      data: {
        rows: [
          { date: '2025-06-01', close: 3700 },
          { date: '2026-01-01', close: 3800 },
          { date: '2026-03-01', close: 3842 },
        ],
        latest: { date: '2026-03-01', close: 3842 },
        spot: 3842,
      },
    });
    render(<MarketData {...defaultProps} profiles={[profile]} selectedId={1} />);
    await userEvent.click(screen.getByRole('button', { name: '1M' }));
    // dateFrom = 2026-02-01, only 2026-03-01 row survives
    expect(screen.queryByText('2025-06-01')).not.toBeInTheDocument();
    expect(screen.queryByText('2026-01-01')).not.toBeInTheDocument();
    expect(screen.getByText('2026-03-01')).toBeInTheDocument();
  });

  it('opens the Add Profile modal when the Add button is clicked', async () => {
    render(<MarketData {...defaultProps} />);
    await userEvent.click(screen.getByRole('button', { name: 'Add profile' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the new tests**

```bash
cd frontend && npx vitest run src/routes/MarketData.test.tsx 2>&1 | tail -20
```

Expected: all 12 tests pass. If any fail, read the failure message and fix the component or test as appropriate (do not skip failures).

- [ ] **Step 3: Run the full test suite**

```bash
cd frontend && npx vitest run 2>&1 | tail -10
```

Expected: all tests pass with no regressions.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/MarketData.test.tsx
git commit -m "test(market-data): integration tests for new bloomberg-style layout"
```

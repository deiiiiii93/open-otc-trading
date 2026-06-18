# Pricing Parameters Build Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved build-first visual companion for the `PRICING PARAMETERS` page.

**Architecture:** Keep the backend contract unchanged. Add a frontend readiness model, split the current route into focused presentational/workflow components, wire the app accounting date into the route, and preserve the existing import/build/refresh APIs.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, lucide-react, existing `Button`, `Table`, `Tile`, `PageHeader`, and route CSS tokens.

---

## File Structure

- Create `frontend/src/routes/pricingBuildReadiness.ts`
  - Pure helper types and functions for readiness state, filters, freshness checks, and latest default profile selection.
- Create `frontend/src/routes/pricingBuildReadiness.test.ts`
  - Unit tests for ready, blocked, stale, empty, date handling, and row filtering.
- Create `frontend/src/routes/PricingBuildConsole.tsx`
  - Top build console, readiness headline, action buttons, map, and blocker panel.
- Create `frontend/src/routes/BuildInputsTable.tsx`
  - Editable defaults table with filter-aware rows and chip-triggered edit mode.
- Create `frontend/src/routes/ProfileLibrary.tsx`
  - Extracted profile list/detail/pager/table from `PricingParameters.tsx`.
- Modify `frontend/src/routes/PricingParameters.tsx`
  - Route orchestration, tab/filter/node state, page context, modal, and build success/failure behavior.
- Modify `frontend/src/routes/PricingParameters.live.tsx`
  - Pass through accounting date, return the built profile from build action, and let async upserts surface row-edit errors.
- Modify `frontend/src/main.tsx`
  - Pass `accountingDate` into `PricingParametersLive`.
- Modify `frontend/src/routes/PricingParameters.css`
  - Style the build console, map, tabs, blocker chips, and build inputs table.
- Modify `frontend/src/routes/PricingParameters.test.tsx`
  - Component-level route behavior tests.
- Modify `frontend/src/routes/PricingParameters.live.test.tsx`
  - Live route API integration tests.

## Task 1: Readiness Helper

**Files:**
- Create: `frontend/src/routes/pricingBuildReadiness.ts`
- Create: `frontend/src/routes/pricingBuildReadiness.test.ts`

- [ ] **Step 1: Write the failing readiness tests**

Create `frontend/src/routes/pricingBuildReadiness.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { PricingParameterProfile, UnderlyingPricingDefault } from '../types';
import {
  derivePricingBuildReadiness,
  filterBuildInputRows,
  latestDefaultProfile,
  shortProfileDate,
  type BuildInputFilter,
} from './pricingBuildReadiness';

const completeRow = (overrides: Partial<UnderlyingPricingDefault> = {}): UnderlyingPricingDefault => ({
  underlying: '000300.SH',
  rate: 0.025,
  dividend_yield: 0.018,
  volatility: 0.22,
  notes: null,
  is_complete: true,
  latest_akshare_close: {
    spot: 4998.34,
    fetched_at: '2026-05-17T09:30:00Z',
    fallback: false,
    market_data_profile_id: 11,
  },
  created_at: '2026-05-13T00:00:00',
  updated_at: '2026-05-17T09:30:00',
  ...overrides,
});

const profile = (overrides: Partial<PricingParameterProfile>): PricingParameterProfile => ({
  id: 1,
  name: 'Default 2026-05-17',
  valuation_date: '2026-05-17T00:00:00',
  source_type: 'default_underlying',
  source_path: null,
  status: 'completed',
  summary: { row_count: 1 },
  created_at: '2026-05-17T10:00:00',
  updated_at: '2026-05-17T10:00:00',
  rows: [],
  ...overrides,
});

describe('derivePricingBuildReadiness', () => {
  it('reports empty when no defaults are loaded', () => {
    const readiness = derivePricingBuildReadiness([], [], '2026-05-17');
    expect(readiness.overallState).toBe('empty');
    expect(readiness.positions.state).toBe('empty');
    expect(readiness.manual.state).toBe('empty');
    expect(readiness.akshare.state).toBe('empty');
    expect(readiness.snapshot.state).toBe('empty');
  });

  it('reports blocked manual rows before spot attention states', () => {
    const rows = [
      completeRow({ underlying: '000300.SH' }),
      completeRow({
        underlying: '000905.SH',
        rate: null,
        volatility: null,
        is_complete: false,
        latest_akshare_close: null,
      }),
    ];
    const readiness = derivePricingBuildReadiness(rows, [], '2026-05-17');
    expect(readiness.overallState).toBe('blocked');
    expect(readiness.manual.state).toBe('blocked');
    expect(readiness.manualMissing.map((row) => row.underlying)).toEqual(['000905.SH']);
    expect(readiness.spotMissing.map((row) => row.underlying)).toEqual(['000905.SH']);
  });

  it('reports stale cached spot without blocking build when manual fields are complete', () => {
    const rows = [
      completeRow({
        underlying: '000300.SH',
        latest_akshare_close: {
          spot: 4998.34,
          fetched_at: '2026-05-16T09:30:00Z',
          fallback: false,
          market_data_profile_id: 11,
        },
      }),
    ];
    const readiness = derivePricingBuildReadiness(rows, [], '2026-05-17');
    expect(readiness.overallState).toBe('stale');
    expect(readiness.akshare.state).toBe('stale');
    expect(readiness.spotStale.map((row) => row.underlying)).toEqual(['000300.SH']);
  });

  it('selects the newest default_underlying profile by valuation date then id', () => {
    const profiles = [
      profile({ id: 2, name: 'XLSX', source_type: 'xlsx', valuation_date: '2026-05-18T00:00:00' }),
      profile({ id: 3, name: 'Default old', valuation_date: '2026-05-16T00:00:00' }),
      profile({ id: 4, name: 'Default latest', valuation_date: '2026-05-17T00:00:00' }),
    ];
    expect(latestDefaultProfile(profiles)?.name).toBe('Default latest');
  });
});

describe('filterBuildInputRows', () => {
  const rows = [
    completeRow({ underlying: 'READY' }),
    completeRow({ underlying: 'MANUAL', rate: null, is_complete: false }),
    completeRow({ underlying: 'SPOT', latest_akshare_close: null }),
    completeRow({
      underlying: 'STALE',
      latest_akshare_close: {
        spot: 1,
        fetched_at: '2026-05-16T00:00:00',
        fallback: false,
        market_data_profile_id: 1,
      },
    }),
  ];

  it('filters manual, akshare, and single-underlying views', () => {
    expect(filterBuildInputRows(rows, { kind: 'manual' }, '2026-05-17').map((row) => row.underlying)).toEqual(['MANUAL']);
    expect(filterBuildInputRows(rows, { kind: 'akshare' }, '2026-05-17').map((row) => row.underlying)).toEqual(['SPOT', 'STALE']);
    expect(filterBuildInputRows(rows, { kind: 'underlying', underlying: 'READY' }, '2026-05-17').map((row) => row.underlying)).toEqual(['READY']);
    expect(filterBuildInputRows(rows, { kind: 'all' } satisfies BuildInputFilter, '2026-05-17')).toHaveLength(4);
  });
});

describe('shortProfileDate', () => {
  it('preserves the literal YYYY-MM-DD prefix', () => {
    expect(shortProfileDate('2026-04-30T00:00:00')).toBe('2026-04-30');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd frontend
npm test -- src/routes/pricingBuildReadiness.test.ts
```

Expected: FAIL because `pricingBuildReadiness.ts` does not exist.

- [ ] **Step 3: Implement the readiness helper**

Create `frontend/src/routes/pricingBuildReadiness.ts`:

```ts
import type { PricingParameterProfile, UnderlyingPricingDefault } from '../types';

export type BuildNodeState = 'ready' | 'blocked' | 'stale' | 'empty';
export type BuildNodeKey = 'positions' | 'manual' | 'akshare' | 'snapshot';
export type BuildSection = 'inputs' | 'library';

export type BuildInputFilter =
  | { kind: 'all' }
  | { kind: 'manual' }
  | { kind: 'akshare' }
  | { kind: 'underlying'; underlying: string };

export type BuildNodeSummary = {
  key: BuildNodeKey;
  label: string;
  value: string;
  detail: string;
  state: BuildNodeState;
};

export type PricingBuildReadiness = {
  positionsCount: number;
  manualCompleteCount: number;
  spotReadyCount: number;
  manualMissing: UnderlyingPricingDefault[];
  spotMissing: UnderlyingPricingDefault[];
  spotStale: UnderlyingPricingDefault[];
  latestDefaultProfile: PricingParameterProfile | null;
  overallState: BuildNodeState;
  positions: BuildNodeSummary;
  manual: BuildNodeSummary;
  akshare: BuildNodeSummary;
  snapshot: BuildNodeSummary;
};

export function shortProfileDate(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : '-';
}

export function latestDefaultProfile(profiles: PricingParameterProfile[]): PricingParameterProfile | null {
  return profiles
    .filter((profile) => profile.source_type === 'default_underlying')
    .slice()
    .sort((a, b) => {
      const dateCompare = shortProfileDate(b.valuation_date).localeCompare(shortProfileDate(a.valuation_date));
      return dateCompare !== 0 ? dateCompare : b.id - a.id;
    })[0] ?? null;
}

export function hasManualInputs(row: UnderlyingPricingDefault): boolean {
  return row.rate != null && row.dividend_yield != null && row.volatility != null;
}

export function hasUsableSpot(row: UnderlyingPricingDefault): boolean {
  return row.latest_akshare_close?.spot != null;
}

export function isSpotStale(row: UnderlyingPricingDefault, accountingDate: string): boolean {
  if (!hasUsableSpot(row)) return false;
  const fetchedDate = row.latest_akshare_close?.fetched_at?.slice(0, 10);
  if (!fetchedDate || !accountingDate) return false;
  return fetchedDate < accountingDate;
}

export function derivePricingBuildReadiness(
  defaults: UnderlyingPricingDefault[],
  profiles: PricingParameterProfile[],
  accountingDate: string,
): PricingBuildReadiness {
  const positionsCount = defaults.length;
  const manualMissing = defaults.filter((row) => !hasManualInputs(row));
  const spotMissing = defaults.filter((row) => !hasUsableSpot(row));
  const spotStale = defaults.filter((row) => isSpotStale(row, accountingDate));
  const manualCompleteCount = positionsCount - manualMissing.length;
  const spotReadyCount = positionsCount - spotMissing.length;
  const latest = latestDefaultProfile(profiles);

  const overallState: BuildNodeState = positionsCount === 0
    ? 'empty'
    : manualMissing.length > 0
      ? 'blocked'
      : spotMissing.length > 0
        ? 'empty'
        : spotStale.length > 0
          ? 'stale'
          : 'ready';

  return {
    positionsCount,
    manualCompleteCount,
    spotReadyCount,
    manualMissing,
    spotMissing,
    spotStale,
    latestDefaultProfile: latest,
    overallState,
    positions: {
      key: 'positions',
      label: 'Positions',
      value: String(positionsCount),
      detail: positionsCount === 1 ? 'underlying' : 'underlyings',
      state: positionsCount === 0 ? 'empty' : 'ready',
    },
    manual: {
      key: 'manual',
      label: 'Manual',
      value: `${manualCompleteCount}/${positionsCount}`,
      detail: 'rate · div · vol',
      state: positionsCount === 0 ? 'empty' : manualMissing.length > 0 ? 'blocked' : 'ready',
    },
    akshare: {
      key: 'akshare',
      label: 'AKShare',
      value: `${spotReadyCount}/${positionsCount}`,
      detail: spotMissing.length > 0 ? 'no cached close' : spotStale.length > 0 ? 'cached close stale' : 'cached close',
      state: positionsCount === 0 ? 'empty' : spotMissing.length > 0 ? 'empty' : spotStale.length > 0 ? 'stale' : 'ready',
    },
    snapshot: {
      key: 'snapshot',
      label: 'Snapshot',
      value: latest ? shortProfileDate(latest.valuation_date) : 'none',
      detail: latest?.name ?? 'no default profile',
      state: latest ? 'ready' : 'empty',
    },
  };
}

export function filterBuildInputRows(
  rows: UnderlyingPricingDefault[],
  filter: BuildInputFilter,
  accountingDate: string,
): UnderlyingPricingDefault[] {
  if (filter.kind === 'all') return rows;
  if (filter.kind === 'manual') return rows.filter((row) => !hasManualInputs(row));
  if (filter.kind === 'akshare') return rows.filter((row) => !hasUsableSpot(row) || isSpotStale(row, accountingDate));
  return rows.filter((row) => row.underlying === filter.underlying);
}

export function defaultBuildNode(readiness: PricingBuildReadiness): BuildNodeKey {
  if (readiness.manualMissing.length > 0) return 'manual';
  if (readiness.spotMissing.length > 0 || readiness.spotStale.length > 0) return 'akshare';
  return 'positions';
}

export function filterForNode(node: BuildNodeKey): BuildInputFilter {
  if (node === 'manual') return { kind: 'manual' };
  if (node === 'akshare') return { kind: 'akshare' };
  return { kind: 'all' };
}
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
cd frontend
npm test -- src/routes/pricingBuildReadiness.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/pricingBuildReadiness.ts frontend/src/routes/pricingBuildReadiness.test.ts
git commit -m "test(pricing): derive build readiness states"
```

## Task 2: Build Inputs Table

**Files:**
- Create: `frontend/src/routes/BuildInputsTable.tsx`
- Modify: `frontend/src/routes/PricingParameters.test.tsx`

- [ ] **Step 1: Add a failing chip-to-edit route test**

Append to `frontend/src/routes/PricingParameters.test.tsx`:

```ts
import userEvent from '@testing-library/user-event';
```

Add this test below the existing tests:

```ts
test('clicking a manual blocker chip filters and opens the row for editing', async () => {
  const profiles = [] as any;
  const defaults = [
    {
      underlying: '000300.SH',
      rate: null,
      dividend_yield: null,
      volatility: null,
      notes: null,
      is_complete: false,
      latest_akshare_close: {
        spot: 4998.34,
        fetched_at: '2026-05-17T09:30:00',
        fallback: false,
        market_data_profile_id: 11,
      },
      created_at: '',
      updated_at: '2026-05-17T09:30:00',
    },
    {
      underlying: '000905.SH',
      rate: 0.02,
      dividend_yield: 0.01,
      volatility: 0.2,
      notes: null,
      is_complete: true,
      latest_akshare_close: null,
      created_at: '',
      updated_at: '2026-05-17T09:30:00',
    },
  ] as any;

  render(
    <PricingParameters
      profiles={profiles}
      selectedId={null}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      defaults={defaults}
      defaultsBuilding={false}
      defaultsRefreshing={false}
      defaultsBulkRefreshing={false}
      defaultsFeedback={null}
      accountingDate="2026-05-17"
      onRefreshFromPositions={() => {}}
      onRefreshAllSpots={() => {}}
      onUpsertDefault={() => {}}
      onDeleteDefault={() => {}}
      onBuildDefault={() => null}
      onSelectProfile={() => {}}
      onImport={() => {}}
    />,
  );

  await userEvent.click(screen.getByRole('button', { name: /000300\.SH/i }));
  expect(screen.getByDisplayValue('')).toBeInTheDocument();
  expect(screen.getByText('000300.SH')).toBeInTheDocument();
  expect(screen.queryByText('000905.SH')).toBeNull();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: FAIL because `accountingDate` is not a prop and blocker chips/table behavior do not exist.

- [ ] **Step 3: Create the build inputs table component**

Create `frontend/src/routes/BuildInputsTable.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Pencil, Trash2, X } from 'lucide-react';
import { Button } from '../components/Button';
import type { UnderlyingPricingDefault } from '../types';
import { isSpotStale, type BuildInputFilter } from './pricingBuildReadiness';

type DraftFields = {
  rate: string;
  dividend_yield: string;
  volatility: string;
  notes: string;
};

type Props = {
  rows: UnderlyingPricingDefault[];
  filter: BuildInputFilter;
  accountingDate: string;
  editUnderlying: string | null;
  onEditHandled: () => void;
  onUpsert: (
    underlying: string,
    fields: { rate?: number | null; dividend_yield?: number | null; volatility?: number | null; notes?: string | null },
  ) => Promise<void> | void;
  onDelete: (underlying: string) => void;
};

function toDraft(row: UnderlyingPricingDefault): DraftFields {
  return {
    rate: row.rate == null ? '' : String(row.rate),
    dividend_yield: row.dividend_yield == null ? '' : String(row.dividend_yield),
    volatility: row.volatility == null ? '' : String(row.volatility),
    notes: row.notes ?? '',
  };
}

function parseNumber(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function rowState(row: UnderlyingPricingDefault, accountingDate: string): 'ready' | 'blocked' | 'stale' | 'empty' {
  if (row.rate == null || row.dividend_yield == null || row.volatility == null) return 'blocked';
  if (row.latest_akshare_close?.spot == null) return 'empty';
  if (isSpotStale(row, accountingDate)) return 'stale';
  return 'ready';
}

function fmt(value: number | null | undefined): string {
  return value == null ? 'missing' : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export function BuildInputsTable({
  rows,
  filter,
  accountingDate,
  editUnderlying,
  onEditHandled,
  onUpsert,
  onDelete,
}: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftFields | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  useEffect(() => {
    if (!editUnderlying) return;
    const row = rows.find((candidate) => candidate.underlying === editUnderlying);
    if (!row) return;
    setEditing(row.underlying);
    setDraft(toDraft(row));
    setRowError(null);
    onEditHandled();
  }, [editUnderlying, onEditHandled, rows]);

  const startEdit = (row: UnderlyingPricingDefault) => {
    setEditing(row.underlying);
    setDraft(toDraft(row));
    setRowError(null);
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft(null);
    setRowError(null);
  };

  const saveEdit = async (underlying: string) => {
    if (!draft) return;
    setRowError(null);
    try {
      await onUpsert(underlying, {
        rate: parseNumber(draft.rate),
        dividend_yield: parseNumber(draft.dividend_yield),
        volatility: parseNumber(draft.volatility),
        notes: draft.notes.trim() === '' ? null : draft.notes,
      });
      cancelEdit();
    } catch (err) {
      setRowError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="wl-build-inputs">
      <table className="wl-build-inputs__table">
        <thead>
          <tr>
            <th>UNDERLYING</th>
            <th className="num">AKSHARE CLOSE</th>
            <th className="num is-manual">RATE</th>
            <th className="num is-manual">DIV YIELD</th>
            <th className="num is-manual">VOL</th>
            <th>NOTES</th>
            <th>LAST EDITED</th>
            <th className="actions">ACTIONS</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isEditing = editing === row.underlying;
            const draftFields = isEditing && draft ? draft : null;
            const state = rowState(row, accountingDate);
            return (
              <tr key={row.underlying} className={`is-${state}`}>
                <td>
                  <strong>{row.underlying}</strong>
                  <span className="wl-build-inputs__state">{state}</span>
                </td>
                <td className="num">
                  {row.latest_akshare_close?.spot == null ? 'missing' : row.latest_akshare_close.spot.toFixed(2)}
                  <span className="ts">
                    {row.latest_akshare_close?.fetched_at
                      ? row.latest_akshare_close.fetched_at.slice(0, 10)
                      : 'no cached close'}
                  </span>
                </td>
                <td className="num">
                  {isEditing ? (
                    <input
                      aria-label={`${row.underlying} rate`}
                      value={draftFields?.rate ?? ''}
                      onChange={(event) => setDraft((current) => current ? { ...current, rate: event.target.value } : current)}
                    />
                  ) : fmt(row.rate)}
                </td>
                <td className="num">
                  {isEditing ? (
                    <input
                      aria-label={`${row.underlying} dividend yield`}
                      value={draftFields?.dividend_yield ?? ''}
                      onChange={(event) => setDraft((current) => current ? { ...current, dividend_yield: event.target.value } : current)}
                    />
                  ) : fmt(row.dividend_yield)}
                </td>
                <td className="num">
                  {isEditing ? (
                    <input
                      aria-label={`${row.underlying} volatility`}
                      value={draftFields?.volatility ?? ''}
                      onChange={(event) => setDraft((current) => current ? { ...current, volatility: event.target.value } : current)}
                    />
                  ) : fmt(row.volatility)}
                </td>
                <td>
                  {isEditing ? (
                    <input
                      aria-label={`${row.underlying} notes`}
                      value={draftFields?.notes ?? ''}
                      onChange={(event) => setDraft((current) => current ? { ...current, notes: event.target.value } : current)}
                    />
                  ) : row.notes ?? '-'}
                  {isEditing && rowError && <span className="wl-build-inputs__error">{rowError}</span>}
                </td>
                <td>{row.updated_at ? row.updated_at.slice(0, 10) : '-'}</td>
                <td className="actions">
                  {isEditing ? (
                    <>
                      <Button type="button" variant="primary" onClick={() => void saveEdit(row.underlying)}>Save</Button>
                      <Button type="button" variant="ghost" iconOnly aria-label={`Cancel ${row.underlying}`} onClick={cancelEdit}>
                        <X size={16} aria-hidden="true" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button type="button" variant="ghost" iconOnly aria-label={`Edit ${row.underlying}`} onClick={() => startEdit(row)}>
                        <Pencil size={16} aria-hidden="true" />
                      </Button>
                      <Button type="button" variant="ghost" iconOnly aria-label={`Delete ${row.underlying}`} onClick={() => onDelete(row.underlying)}>
                        <Trash2 size={16} aria-hidden="true" />
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} className="wl-build-inputs__empty">
                {filter.kind === 'all' ? 'No underlying defaults yet.' : 'No rows match this build filter.'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Wire the table minimally in `PricingParameters.tsx`**

In `frontend/src/routes/PricingParameters.tsx`, add imports:

```ts
import { BuildInputsTable } from './BuildInputsTable';
import {
  defaultBuildNode,
  derivePricingBuildReadiness,
  filterBuildInputRows,
  filterForNode,
  type BuildInputFilter,
  type BuildNodeKey,
  type BuildSection,
} from './pricingBuildReadiness';
```

Add `accountingDate` to `Props`:

```ts
  accountingDate?: string;
```

Default it in function props:

```ts
  accountingDate = new Date().toISOString().slice(0, 10),
```

Add state after the existing `pageSize` state:

```ts
  const [activeSection, setActiveSection] = useState<BuildSection>('inputs');
  const [activeNode, setActiveNode] = useState<BuildNodeKey>('positions');
  const [buildFilter, setBuildFilter] = useState<BuildInputFilter>({ kind: 'all' });
  const [editUnderlying, setEditUnderlying] = useState<string | null>(null);
```

Add derived readiness and filtered input rows:

```ts
  const buildReadiness = useMemo(
    () => derivePricingBuildReadiness(defaults, profiles, accountingDate),
    [accountingDate, defaults, profiles],
  );
  const buildInputRows = useMemo(
    () => filterBuildInputRows(defaults, buildFilter, accountingDate),
    [accountingDate, buildFilter, defaults],
  );
```

Add default node effect:

```ts
  useEffect(() => {
    const next = defaultBuildNode(buildReadiness);
    setActiveNode(next);
    setBuildFilter(filterForNode(next));
    setActiveSection('inputs');
  }, [buildReadiness.overallState]);
```

Replace the current `<UnderlyingDefaultsPanel ... />` call with a temporary `BuildInputsTable` block:

```tsx
      <div className="wl-pricing-build__tabs" role="tablist" aria-label="Pricing parameters sections">
        <button type="button" role="tab" aria-selected={activeSection === 'inputs'} className={activeSection === 'inputs' ? 'is-active' : ''} onClick={() => setActiveSection('inputs')}>
          Build Inputs
        </button>
        <button type="button" role="tab" aria-selected={activeSection === 'library'} className={activeSection === 'library' ? 'is-active' : ''} onClick={() => setActiveSection('library')}>
          Profile Library
        </button>
      </div>
      {activeSection === 'inputs' && (
        <BuildInputsTable
          rows={buildInputRows}
          filter={buildFilter}
          accountingDate={accountingDate}
          editUnderlying={editUnderlying}
          onEditHandled={() => setEditUnderlying(null)}
          onUpsert={onUpsertDefault}
          onDelete={onDeleteDefault}
        />
      )}
```

Do not delete the existing profile library JSX yet; hide it in Task 5.

- [ ] **Step 5: Run the component tests**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: PASS for the new chip/edit test only after Task 3 console chips exist. At this point, if it still fails due to missing chips, leave the failure and continue to Task 3.

- [ ] **Step 6: Commit if Task 2 tests pass independently**

If all tests in the file pass:

```bash
git add frontend/src/routes/BuildInputsTable.tsx frontend/src/routes/PricingParameters.tsx frontend/src/routes/PricingParameters.test.tsx
git commit -m "feat(pricing): add build inputs table"
```

If tests still need the console chips from Task 3, defer the commit until Task 3.

## Task 3: Build Console, Map, And Blocker Chips

**Files:**
- Create: `frontend/src/routes/PricingBuildConsole.tsx`
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.test.tsx`
- Modify: `frontend/src/routes/PricingParameters.css`

- [ ] **Step 1: Add failing build-map interaction tests**

Append to `frontend/src/routes/PricingParameters.test.tsx`:

```ts
test('build map nodes filter inputs and snapshot node opens the latest default profile', async () => {
  const profiles = [
    { id: 1, name: 'Default old', valuation_date: '2026-05-16', source_type: 'default_underlying', status: 'completed', summary: { row_count: 1 }, rows: [], created_at: '', updated_at: '' },
    { id: 2, name: 'Default latest', valuation_date: '2026-05-17', source_type: 'default_underlying', status: 'completed', summary: { row_count: 2 }, rows: [], created_at: '', updated_at: '' },
  ] as any;
  const onSelectProfile = vi.fn();
  const defaults = [
    {
      underlying: 'MANUAL',
      rate: null,
      dividend_yield: null,
      volatility: null,
      notes: null,
      is_complete: false,
      latest_akshare_close: { spot: 1, fetched_at: '2026-05-17T00:00:00', fallback: false, market_data_profile_id: 1 },
      created_at: '',
      updated_at: '',
    },
    {
      underlying: 'AKSHARE',
      rate: 0.02,
      dividend_yield: 0.01,
      volatility: 0.2,
      notes: null,
      is_complete: true,
      latest_akshare_close: null,
      created_at: '',
      updated_at: '',
    },
  ] as any;

  render(
    <PricingParameters
      profiles={profiles}
      selectedId={1}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      defaults={defaults}
      defaultsBuilding={false}
      defaultsRefreshing={false}
      defaultsBulkRefreshing={false}
      defaultsFeedback={null}
      accountingDate="2026-05-17"
      onRefreshFromPositions={() => {}}
      onRefreshAllSpots={() => {}}
      onUpsertDefault={() => {}}
      onDeleteDefault={() => {}}
      onBuildDefault={() => null}
      onSelectProfile={onSelectProfile}
      onImport={() => {}}
    />,
  );

  await userEvent.click(screen.getByRole('button', { name: /akshare/i }));
  expect(screen.queryByText('MANUAL')).toBeNull();
  expect(screen.getByText('AKSHARE')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /snapshot/i }));
  expect(screen.getByText('Default latest')).toBeInTheDocument();
  expect(onSelectProfile).toHaveBeenCalledWith(2);
});
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: FAIL because `PricingBuildConsole` and map node behavior do not exist.

- [ ] **Step 3: Implement the console component**

Create `frontend/src/routes/PricingBuildConsole.tsx`:

```tsx
import { AlertTriangle, CheckCircle2, Database, GitBranch, RefreshCw, Sparkles } from 'lucide-react';
import { Button } from '../components/Button';
import type { UnderlyingPricingDefault } from '../types';
import type { BuildNodeKey, BuildNodeSummary, PricingBuildReadiness } from './pricingBuildReadiness';

type Props = {
  readiness: PricingBuildReadiness;
  activeNode: BuildNodeKey;
  building: boolean;
  refreshing: boolean;
  bulkRefreshing: boolean;
  onNodeSelect: (node: BuildNodeKey) => void;
  onManualChip: (underlying: string) => void;
  onAkshareChip: (underlying: string) => void;
  onRefreshFromPositions: () => void;
  onRefreshAllSpots: () => void;
  onBuild: () => void;
};

const nodeIcons: Record<BuildNodeKey, typeof Database> = {
  positions: Database,
  manual: GitBranch,
  akshare: RefreshCw,
  snapshot: Sparkles,
};

function headline(readiness: PricingBuildReadiness): { title: string; detail: string; tone: 'ok' | 'warn' | 'blocked' | 'empty' } {
  if (readiness.positionsCount === 0) {
    return { title: 'No inputs yet', detail: 'Refresh from positions to discover the current underlying universe.', tone: 'empty' };
  }
  if (readiness.manualMissing.length > 0) {
    return { title: 'Build blocked', detail: `${readiness.manualMissing.length} underlyings need rate, dividend yield, or volatility.`, tone: 'blocked' };
  }
  if (readiness.spotMissing.length > 0) {
    return { title: 'Cached spot missing', detail: `${readiness.spotMissing.length} underlyings have no cached AKShare close. Build can still fetch fresh spot.`, tone: 'warn' };
  }
  if (readiness.spotStale.length > 0) {
    return { title: 'Cached spot stale', detail: `${readiness.spotStale.length} underlyings have cached closes before the accounting date.`, tone: 'warn' };
  }
  return { title: 'Ready to build', detail: 'Manual assumptions are complete and cached spot data is current.', tone: 'ok' };
}

function BuildMapNode({ node, active, onClick }: { node: BuildNodeSummary; active: boolean; onClick: () => void }) {
  const Icon = nodeIcons[node.key];
  return (
    <button
      type="button"
      className={`wl-build-map__node is-${node.state} ${active ? 'is-active' : ''}`}
      onClick={onClick}
    >
      <span className="wl-build-map__icon"><Icon size={16} aria-hidden="true" /></span>
      <span className="wl-build-map__label">{node.label}</span>
      <strong>{node.value}</strong>
      <span>{node.detail}</span>
    </button>
  );
}

function ChipGroup({
  title,
  rows,
  onChip,
}: {
  title: string;
  rows: UnderlyingPricingDefault[];
  onChip: (underlying: string) => void;
}) {
  if (rows.length === 0) return null;
  const visible = rows.slice(0, 6);
  const hidden = rows.length - visible.length;
  return (
    <div className="wl-build-blockers__group">
      <span className="wl-build-blockers__title">{title}</span>
      {visible.map((row) => (
        <button key={row.underlying} type="button" className="wl-build-blockers__chip" onClick={() => onChip(row.underlying)}>
          {row.underlying}
        </button>
      ))}
      {hidden > 0 && <span className="wl-build-blockers__more">+{hidden} more</span>}
    </div>
  );
}

export function PricingBuildConsole({
  readiness,
  activeNode,
  building,
  refreshing,
  bulkRefreshing,
  onNodeSelect,
  onManualChip,
  onAkshareChip,
  onRefreshFromPositions,
  onRefreshAllSpots,
  onBuild,
}: Props) {
  const status = headline(readiness);
  const buildDisabled = building || readiness.positionsCount === 0 || readiness.manualMissing.length > 0;
  return (
    <section className={`wl-pricing-build is-${status.tone}`}>
      <div className="wl-pricing-build__summary">
        <div>
          <span className="wl-pricing-build__eyebrow">Daily build console</span>
          <h2>{status.title}</h2>
          <p>{status.detail}</p>
        </div>
        <div className="wl-pricing-build__actions">
          <Button type="button" variant="ghost" disabled={refreshing} onClick={onRefreshFromPositions}>
            Refresh from positions
          </Button>
          <Button type="button" variant="ghost" disabled={bulkRefreshing} onClick={onRefreshAllSpots}>
            Refresh all AKShare spots
          </Button>
          <Button type="button" variant="primary" disabled={buildDisabled} onClick={onBuild}>
            {status.tone === 'ok' ? <CheckCircle2 size={16} aria-hidden="true" /> : <AlertTriangle size={16} aria-hidden="true" />}
            Build Default Profile
          </Button>
        </div>
      </div>
      <div className="wl-build-map" aria-label="Profile build map">
        {[readiness.positions, readiness.manual, readiness.akshare, readiness.snapshot].map((node) => (
          <BuildMapNode
            key={node.key}
            node={node}
            active={activeNode === node.key}
            onClick={() => onNodeSelect(node.key)}
          />
        ))}
      </div>
      {(readiness.manualMissing.length > 0 || readiness.spotMissing.length > 0 || readiness.spotStale.length > 0) && (
        <div className="wl-build-blockers" aria-label="Build blockers">
          <ChipGroup title="Missing manual assumptions" rows={readiness.manualMissing} onChip={onManualChip} />
          <ChipGroup title="No cached AKShare close" rows={readiness.spotMissing} onChip={onAkshareChip} />
          <ChipGroup title="Stale cached AKShare close" rows={readiness.spotStale} onChip={onAkshareChip} />
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Wire console behavior in `PricingParameters.tsx`**

Import:

```ts
import { PricingBuildConsole } from './PricingBuildConsole';
```

Add handlers inside `PricingParameters`:

```ts
  const selectBuildNode = (node: BuildNodeKey) => {
    setActiveNode(node);
    if (node === 'snapshot') {
      setActiveSection('library');
      if (buildReadiness.latestDefaultProfile) onSelectProfile(buildReadiness.latestDefaultProfile.id);
      return;
    }
    setActiveSection('inputs');
    setBuildFilter(filterForNode(node));
  };

  const focusManualUnderlying = (underlying: string) => {
    setActiveSection('inputs');
    setActiveNode('manual');
    setBuildFilter({ kind: 'underlying', underlying });
    setEditUnderlying(underlying);
  };

  const focusAkshareUnderlying = (underlying: string) => {
    setActiveSection('inputs');
    setActiveNode('akshare');
    setBuildFilter({ kind: 'underlying', underlying });
  };
```

Render `<PricingBuildConsole />` between `PageHeader` and tabs:

```tsx
      <PricingBuildConsole
        readiness={buildReadiness}
        activeNode={activeNode}
        building={defaultsBuilding}
        refreshing={defaultsRefreshing}
        bulkRefreshing={defaultsBulkRefreshing}
        onNodeSelect={selectBuildNode}
        onManualChip={focusManualUnderlying}
        onAkshareChip={focusAkshareUnderlying}
        onRefreshFromPositions={onRefreshFromPositions}
        onRefreshAllSpots={onRefreshAllSpots}
        onBuild={() => void handleBuildDefault()}
      />
```

Add a temporary build handler:

```ts
  const handleBuildDefault = async () => {
    await onBuildDefault();
  };
```

- [ ] **Step 5: Add required CSS**

Append to `frontend/src/routes/PricingParameters.css`:

```css
.wl-pricing-build {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  margin-bottom: var(--gap-4);
}

.wl-pricing-build__summary {
  display: flex;
  justify-content: space-between;
  gap: var(--gap-4);
  padding: var(--gap-4);
  border-bottom: 1px solid var(--hairline);
  background: var(--paper-2);
}

.wl-pricing-build__eyebrow,
.wl-build-blockers__title,
.wl-build-map__label {
  font: 700 var(--type-caps-size) var(--font-numeric);
  letter-spacing: 0.8px;
  text-transform: uppercase;
  color: var(--ink-2);
}

.wl-pricing-build h2 {
  margin: 2px 0;
  font-family: var(--font-ui);
  font-size: var(--type-h2-size);
}

.wl-pricing-build p {
  margin: 0;
  color: var(--ink-2);
  font-size: var(--type-small-size);
}

.wl-pricing-build__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: var(--gap-2);
}

.wl-build-map {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--gap-2);
  padding: var(--gap-3);
}

.wl-build-map__node {
  text-align: left;
  border: 1px solid var(--hairline-2);
  border-left: 4px solid var(--ink-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--gap-3);
  display: grid;
  gap: 4px;
  cursor: pointer;
  min-width: 0;
}

.wl-build-map__node:hover,
.wl-build-map__node.is-active {
  background: var(--paper-2);
  border-color: var(--ink);
}

.wl-build-map__node.is-ready { border-left-color: var(--pos); }
.wl-build-map__node.is-blocked { border-left-color: var(--neg); }
.wl-build-map__node.is-stale { border-left-color: var(--warn); }
.wl-build-map__node.is-empty { border-left-color: var(--ink-2); }

.wl-build-map__node strong {
  font-family: var(--font-numeric);
  font-size: var(--type-num-l-size);
}

.wl-build-map__node span:last-child {
  color: var(--ink-2);
  font-size: var(--type-small-size);
  overflow-wrap: anywhere;
}

.wl-build-blockers {
  border-top: 1px solid var(--hairline);
  padding: var(--gap-3);
  display: grid;
  gap: var(--gap-2);
}

.wl-build-blockers__group {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
  flex-wrap: wrap;
}

.wl-build-blockers__chip,
.wl-build-blockers__more {
  border: 1px solid color-mix(in srgb, var(--warn) 35%, var(--hairline));
  background: color-mix(in srgb, var(--warn) 9%, var(--paper));
  color: var(--ink);
  padding: 3px 7px;
  font: 600 var(--type-small-size) var(--font-numeric);
}

.wl-build-blockers__chip {
  cursor: pointer;
}

.wl-pricing-build__tabs {
  display: flex;
  margin-bottom: var(--gap-3);
}

.wl-pricing-build__tabs button {
  border: 1px solid var(--hairline-2);
  border-right: 0;
  background: var(--paper);
  color: var(--ink-2);
  padding: var(--input-padding-y) var(--input-padding-x);
  font: 700 var(--type-caps-size) var(--font-ui);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  cursor: pointer;
}

.wl-pricing-build__tabs button:last-child {
  border-right: 1px solid var(--hairline-2);
}

.wl-pricing-build__tabs button.is-active {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--paper);
}

.wl-build-inputs {
  overflow-x: auto;
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  margin-bottom: var(--gap-4);
}

.wl-build-inputs__table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
  font: 500 var(--type-small-size) var(--font-numeric);
}

.wl-build-inputs__table th,
.wl-build-inputs__table td {
  padding: 9px var(--gap-3);
  border-bottom: 1px solid var(--hairline);
}

.wl-build-inputs__table th {
  background: var(--paper-2);
  color: var(--ink-2);
  font-size: var(--type-caps-size);
  letter-spacing: 1px;
  text-transform: uppercase;
}

.wl-build-inputs__table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.wl-build-inputs__table th.is-manual {
  background: color-mix(in srgb, var(--warn) 10%, var(--paper-2));
}

.wl-build-inputs__table tr.is-blocked td {
  background: color-mix(in srgb, var(--warn) 6%, var(--paper));
}

.wl-build-inputs__table tr.is-stale td {
  background: color-mix(in srgb, var(--info) 6%, var(--paper));
}

.wl-build-inputs__state,
.wl-build-inputs__table .ts,
.wl-build-inputs__error {
  display: block;
  margin-top: 2px;
  color: var(--ink-2);
  font-size: 10px;
}

.wl-build-inputs__error {
  color: var(--neg);
}

.wl-build-inputs__table input {
  width: 100%;
  min-width: 72px;
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: 5px 7px;
  font: inherit;
}

.wl-build-inputs__table .actions {
  text-align: right;
  white-space: nowrap;
}

.wl-build-inputs__empty {
  text-align: center;
  color: var(--ink-2);
  padding: var(--gap-4);
}

@media (max-width: 900px) {
  .wl-pricing-build__summary {
    flex-direction: column;
  }

  .wl-pricing-build__actions {
    justify-content: flex-start;
  }

  .wl-build-map {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .wl-build-map {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 6: Run the route tests**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2 and Task 3 together if Task 2 was deferred**

```bash
git add frontend/src/routes/PricingBuildConsole.tsx frontend/src/routes/BuildInputsTable.tsx frontend/src/routes/PricingParameters.tsx frontend/src/routes/PricingParameters.css frontend/src/routes/PricingParameters.test.tsx
git commit -m "feat(pricing): add build companion console"
```

## Task 4: Profile Library Extraction And Origin Badge Cleanup

**Files:**
- Create: `frontend/src/routes/ProfileLibrary.tsx`
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.test.tsx`

- [ ] **Step 1: Add failing origin-badge cleanup test**

Append to `frontend/src/routes/PricingParameters.test.tsx`:

```ts
test('does not render source badges next to blank profile numeric values', () => {
  const profiles = [
    {
      id: 1,
      name: 'XLSX A',
      valuation_date: '2026-05-13T00:00:00',
      source_type: 'xlsx',
      status: 'completed',
      summary: { row_count: 1 },
      created_at: '',
      updated_at: '',
      rows: [{
        id: 10,
        profile_id: 1,
        source_trade_id: 'T-1',
        symbol: '000905.SH',
        spot: null,
        rate: null,
        dividend_yield: null,
        volatility: null,
        source_row: 2,
        source_payload: {},
        created_at: '',
        updated_at: '',
      }],
    },
  ] as any;

  render(
    <PricingParameters
      profiles={profiles}
      selectedId={1}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      defaults={[]}
      defaultsBuilding={false}
      defaultsRefreshing={false}
      defaultsBulkRefreshing={false}
      defaultsFeedback={null}
      accountingDate="2026-05-17"
      onRefreshFromPositions={() => {}}
      onRefreshAllSpots={() => {}}
      onUpsertDefault={() => {}}
      onDeleteDefault={() => {}}
      onBuildDefault={() => null}
      onSelectProfile={() => {}}
      onImport={() => {}}
    />,
  );

  expect(screen.queryByText(/- XLSX/)).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: FAIL because the current numeric render shows origin badges after `-`.

- [ ] **Step 3: Create `ProfileLibrary.tsx`**

Move source constants, profile list, tiles, toolbar, pager, and `Table` rendering from `PricingParameters.tsx` into `frontend/src/routes/ProfileLibrary.tsx`.

Use this public interface:

```tsx
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Table, type Column } from '../components/Table';
import { Tile } from '../components/Tile';
import type { PricingParameterProfile, PricingParameterRow } from '../types';
import { shortProfileDate } from './pricingBuildReadiness';

type SourceTypeKey = 'default_underlying' | 'xlsx' | 'market_data_spot';

const SOURCE_TYPE_LABELS: Record<SourceTypeKey, string> = {
  default_underlying: 'DEFAULT',
  xlsx: 'XLSX',
  market_data_spot: 'SPOT',
};

const SOURCE_TYPE_ORDER: SourceTypeKey[] = ['default_underlying', 'xlsx', 'market_data_spot'];

type Props = {
  profiles: PricingParameterProfile[];
  selected: PricingParameterProfile | null;
  loading: boolean;
  filterMode: 'all' | 'live';
  onFilterModeChange: (mode: 'all' | 'live') => void;
  onSelectProfile: (id: number) => void;
};

function fmt(value: number | null | undefined): string {
  return value == null ? '-' : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function sourceBadge(sourceType: string, kind: 'spot' | 'manual' | 'xlsx', value: number | null | undefined) {
  if (value == null) return null;
  const cls = kind === 'spot' ? 'is-ak' : kind === 'manual' ? 'is-manual' : 'is-xlsx';
  const label = kind === 'spot' ? 'AK' : kind === 'manual' ? 'M' : 'XLSX';
  if (sourceType === 'default_underlying') return kind === 'spot' ? <span className={`wl-origin ${cls}`}>{label}</span> : <span className="wl-origin is-manual">M</span>;
  if (sourceType === 'market_data_spot') return kind === 'spot' ? <span className="wl-origin is-ak">AK</span> : null;
  if (sourceType === 'xlsx') return <span className="wl-origin is-xlsx">XLSX</span>;
  return null;
}

export function ProfileLibrary({
  profiles,
  selected,
  loading,
  filterMode,
  onFilterModeChange,
  onSelectProfile,
}: Props) {
  const [sourceFilter, setSourceFilter] = useState<SourceTypeKey | 'all'>('all');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const sourceCounts = useMemo(() => {
    const counts: Record<SourceTypeKey | 'all', number> = {
      all: profiles.length,
      default_underlying: 0,
      xlsx: 0,
      market_data_spot: 0,
    };
    for (const profile of profiles) {
      const key = profile.source_type as SourceTypeKey;
      if (key in counts) counts[key] += 1;
    }
    return counts;
  }, [profiles]);

  const visibleProfiles = useMemo(() => {
    if (sourceFilter === 'all') return profiles;
    return profiles.filter((profile) => profile.source_type === sourceFilter);
  }, [profiles, sourceFilter]);

  const allRows = selected?.rows ?? [];
  const filteredRows = useMemo(() => {
    if (filterMode === 'all') return allRows;
    return allRows.filter((row) => (
      row.spot != null && row.volatility != null && row.rate != null && row.dividend_yield != null
    ));
  }, [allRows, filterMode]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pagedRows = filteredRows.slice(safePage * pageSize, safePage * pageSize + pageSize);
  const visibleStart = filteredRows.length === 0 ? 0 : safePage * pageSize + 1;
  const visibleEnd = Math.min(safePage * pageSize + pageSize, filteredRows.length);

  const columns: Column<PricingParameterRow>[] = [
    { key: 'source_trade_id', header: 'TRADE', width: '1.6fr' },
    { key: 'symbol', header: 'SYMBOL', width: '1fr' },
    {
      key: 'spot',
      header: 'SPOT',
      numeric: true,
      render: (row) => <>{fmt(row.spot)} {selected ? sourceBadge(selected.source_type, 'spot', row.spot) : null}</>,
    },
    {
      key: 'volatility',
      header: 'VOL',
      numeric: true,
      render: (row) => <>{fmt(row.volatility)} {selected ? sourceBadge(selected.source_type, 'manual', row.volatility) : null}</>,
    },
    {
      key: 'rate',
      header: 'RATE',
      numeric: true,
      render: (row) => <>{fmt(row.rate)} {selected ? sourceBadge(selected.source_type, 'manual', row.rate) : null}</>,
    },
    {
      key: 'dividend_yield',
      header: 'DIV',
      numeric: true,
      render: (row) => <>{fmt(row.dividend_yield)} {selected ? sourceBadge(selected.source_type, 'manual', row.dividend_yield) : null}</>,
    },
    { key: 'source_row', header: 'ROW', numeric: true, render: (row) => row.source_row ?? '-' },
  ];

  return (
    <div className="wl-pricing-params">
      <aside className="wl-pricing-params__list" aria-label="Pricing parameter profiles">
        <div className="wl-pricing-params__source-filter">
          <button type="button" className={`wl-pricing-params__source-pill ${sourceFilter === 'all' ? 'is-active' : ''}`} onClick={() => setSourceFilter('all')}>
            ALL · {sourceCounts.all}
          </button>
          {SOURCE_TYPE_ORDER.map((key) => (
            <button key={key} type="button" className={`wl-pricing-params__source-pill ${sourceFilter === key ? 'is-active' : ''}`} onClick={() => setSourceFilter(key)}>
              {SOURCE_TYPE_LABELS[key]} · {sourceCounts[key]}
            </button>
          ))}
        </div>
        {visibleProfiles.map((profile) => (
          <button
            key={profile.id}
            className={`wl-pricing-params__profile is-${profile.source_type} ${selected?.id === profile.id ? 'is-active' : ''}`}
            aria-current={selected?.id === profile.id ? 'true' : undefined}
            onClick={() => onSelectProfile(profile.id)}
            type="button"
          >
            <strong>{profile.name}</strong>
            <span>{shortProfileDate(profile.valuation_date)} · {profile.summary?.row_count ?? profile.rows.length} rows</span>
            <span className={`wl-pricing-params__source-tag is-${profile.source_type}`}>
              {SOURCE_TYPE_LABELS[(profile.source_type as SourceTypeKey)] ?? profile.source_type.toUpperCase()}
            </span>
          </button>
        ))}
      </aside>
      <section className="wl-pricing-params__detail">
        {selected ? (
          <>
            <div className="wl-pricing-params__tiles">
              <Tile label="Rows" value={String(filteredRows.length)} />
              <Tile label="Duplicates" value={String((selected.summary?.duplicate_trade_ids ?? []).length)} />
              <Tile label="Source type" value={SOURCE_TYPE_LABELS[(selected.source_type as SourceTypeKey)] ?? selected.source_type.toUpperCase()} />
              <Tile label="Status" value={selected.status} />
            </div>
            <div className="wl-pricing-params__toolbar">
              <div className="wl-pricing-params__filter">
                <button type="button" className={`wl-pricing-params__filter-btn ${filterMode === 'all' ? 'is-active' : ''}`} onClick={() => onFilterModeChange('all')}>ALL</button>
                <button type="button" className={`wl-pricing-params__filter-btn ${filterMode === 'live' ? 'is-active' : ''}`} onClick={() => onFilterModeChange('live')}>LIVE</button>
              </div>
              <div className="wl-pricing-params__pager">
                <span className="wl-pricing-params__pageinfo">{visibleStart}-{visibleEnd} of {filteredRows.length}</span>
                <select className="wl-pricing-params__pagesize" aria-label="Rows per page" value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
                  {[10, 25, 50, 100].map((size) => <option key={size} value={size}>{size} / page</option>)}
                </select>
                <Button type="button" variant="ghost" iconOnly aria-label="Previous page" disabled={safePage === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}>
                  <ChevronLeft size={16} aria-hidden="true" />
                </Button>
                <Button type="button" variant="ghost" iconOnly aria-label="Next page" disabled={safePage >= totalPages - 1} onClick={() => setPage((current) => Math.min(totalPages - 1, current + 1))}>
                  <ChevronRight size={16} aria-hidden="true" />
                </Button>
              </div>
            </div>
            <Table columns={columns} rows={pagedRows} rowKey={(row) => row.id} />
            {filteredRows.length === 0 && <Empty message={filterMode === 'live' ? 'No rows with complete pricing parameters.' : 'No rows in this profile.'} symbol="◌" />}
          </>
        ) : (
          <Empty message={loading ? 'Loading pricing parameter profiles...' : 'No pricing parameter profiles yet.'} />
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Replace profile library JSX in `PricingParameters.tsx`**

Remove:

- `ChevronLeft`, `ChevronRight` imports.
- `Table`, `Column`, `Tile` imports.
- Source constants and `sourceBadge`.
- Local `sourceFilter`, `page`, `pageSize` state.
- Local profile list/detail JSX.

Add:

```ts
import { ProfileLibrary } from './ProfileLibrary';
```

Render when `activeSection === 'library'`:

```tsx
      {activeSection === 'library' && (
        <ProfileLibrary
          profiles={profiles}
          selected={selected}
          loading={loading}
          filterMode={filterMode}
          onFilterModeChange={setFilterMode}
          onSelectProfile={onSelectProfile}
        />
      )}
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/ProfileLibrary.tsx frontend/src/routes/PricingParameters.tsx frontend/src/routes/PricingParameters.test.tsx
git commit -m "refactor(pricing): extract profile library"
```

## Task 5: Live Route Build Success And Accounting Date Wiring

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/routes/PricingParameters.live.tsx`
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.live.test.tsx`

- [ ] **Step 1: Add failing live behavior tests**

Modify the existing `dispatches build-default and refreshes profiles on success` test in `frontend/src/routes/PricingParameters.live.test.tsx` so the profile list reload returns the new profile after build:

```ts
  it('dispatches build-default, selects the built profile, and opens the profile library', async () => {
    let built = false;
    const builtProfile = {
      id: 99,
      name: 'Default',
      valuation_date: '2026-05-13',
      source_type: 'default_underlying',
      status: 'completed',
      summary: { row_count: 1 },
      rows: [],
      created_at: '',
      updated_at: '',
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/pricing-parameter-profiles' && (!init || init.method === 'GET' || init.method === undefined)) {
        return response(built ? [builtProfile] : []);
      }
      if (url === '/api/underlying-pricing-defaults') return response([{
        underlying: '000300.SH', rate: 0.025, dividend_yield: 0.02, volatility: 0.185,
        notes: null, is_complete: true, latest_akshare_close: null, created_at: '', updated_at: '',
      }]);
      if (url === '/api/pricing-parameter-profiles/build-default' && init?.method === 'POST') {
        built = true;
        return response(builtProfile);
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<PricingParametersLive accountingDate="2026-05-13" />);
    await waitFor(() => expect(screen.getByRole('button', { name: /build default profile/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /build default profile/i }));
    await waitFor(() => expect(screen.getByText('Default')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Default/i })).toHaveAttribute('aria-current', 'true');
  });
```

- [ ] **Step 2: Run live tests to verify failure**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.live.test.tsx
```

Expected: FAIL because `PricingParametersLive` does not accept `accountingDate`, `onBuildDefault` does not return the profile, and route does not switch to library after build.

- [ ] **Step 3: Wire accounting date from `main.tsx`**

In `frontend/src/main.tsx`, change:

```tsx
        {route === 'pricing-parameters' && (
          <PricingParametersLive
            onPageContextChange={handlePageContextChange}
          />
        )}
```

to:

```tsx
        {route === 'pricing-parameters' && (
          <PricingParametersLive
            accountingDate={accountingDate}
            onPageContextChange={handlePageContextChange}
          />
        )}
```

- [ ] **Step 4: Update live props and build return**

In `frontend/src/routes/PricingParameters.live.tsx`, change props:

```ts
type Props = {
  accountingDate?: string;
  onPageContextChange?: PageContextReporter;
};
```

Change function signature:

```ts
export function PricingParametersLive({ accountingDate, onPageContextChange }: Props) {
```

Change `onBuildDefault` return behavior:

```ts
  const onBuildDefault = async (): Promise<PricingParameterProfile | null> => {
    setDefaultsBuilding(true);
    setDefaultsFeedback(null);
    try {
      const profile = await api<PricingParameterProfile>(
        '/api/pricing-parameter-profiles/build-default',
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({}),
        },
      );
      setSelectedId(profile.id);
      await loadProfiles();
      setDefaultsFeedback({ tone: 'success', message: `Built profile ${profile.name}.` });
      return profile;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      try {
        const parsed = JSON.parse(message);
        if (parsed?.detail?.unfilled_underlyings) {
          setDefaultsFeedback({
            tone: 'error',
            message: 'underlying defaults missing inputs',
            unfilled: parsed.detail.unfilled_underlyings,
          });
        } else if (parsed?.detail?.failed_akshare_underlyings) {
          setDefaultsFeedback({
            tone: 'error',
            message: 'AKShare spot fetch failed for one or more underlyings',
            failedAkshare: parsed.detail.failed_akshare_underlyings,
          });
        } else {
          setDefaultsFeedback({ tone: 'error', message });
        }
      } catch {
        setDefaultsFeedback({ tone: 'error', message });
      }
      return null;
    } finally {
      setDefaultsBuilding(false);
    }
  };
```

Pass `accountingDate` to `PricingParameters`:

```tsx
      accountingDate={accountingDate}
```

- [ ] **Step 5: Update route build handler**

In `frontend/src/routes/PricingParameters.tsx`, change the `onBuildDefault` prop type:

```ts
  onBuildDefault: () => Promise<PricingParameterProfile | null> | PricingParameterProfile | null | void;
```

Change `handleBuildDefault`:

```ts
  const handleBuildDefault = async () => {
    const profile = await onBuildDefault();
    if (profile && typeof profile === 'object') {
      setActiveNode('snapshot');
      setActiveSection('library');
      onSelectProfile(profile.id);
    }
  };
```

Add failure routing effect:

```ts
  useEffect(() => {
    if (defaultsFeedback?.tone !== 'error') return;
    if (defaultsFeedback.unfilled?.length) {
      setActiveNode('manual');
      setBuildFilter({ kind: 'manual' });
      setActiveSection('inputs');
    } else if (defaultsFeedback.failedAkshare?.length) {
      setActiveNode('akshare');
      setBuildFilter({ kind: 'akshare' });
      setActiveSection('inputs');
    }
  }, [defaultsFeedback]);
```

- [ ] **Step 6: Run live tests**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.live.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Run all pricing route tests**

Run:

```bash
cd frontend
npm test -- src/routes/pricingBuildReadiness.test.ts src/routes/PricingParameters.test.tsx src/routes/PricingParameters.live.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/main.tsx frontend/src/routes/PricingParameters.live.tsx frontend/src/routes/PricingParameters.tsx frontend/src/routes/PricingParameters.live.test.tsx
git commit -m "feat(pricing): bind build companion to live route"
```

## Task 6: Page Context And Visual Polish

**Files:**
- Modify: `frontend/src/routes/PricingParameters.tsx`
- Modify: `frontend/src/routes/PricingParameters.css`
- Modify: `frontend/src/routes/PricingParameters.test.tsx`

- [ ] **Step 1: Add failing page context assertion**

Extend the existing `reports underlying defaults in page context for pet agent` test in `frontend/src/routes/PricingParameters.test.tsx` with:

```ts
  expect(context.snapshot.build_companion).toMatchObject({
    active_node: 'manual',
    active_section: 'inputs',
    filter: { kind: 'manual' },
    readiness: {
      overall_state: 'blocked',
      manual_missing_count: 1,
    },
  });
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: FAIL because `build_companion` is not yet included in page context.

- [ ] **Step 3: Add build companion to page context**

In `frontend/src/routes/PricingParameters.tsx`, extend the `pageContext.snapshot` object:

```ts
        build_companion: {
          active_node: activeNode,
          active_section: activeSection,
          filter: buildFilter,
          accounting_date: accountingDate,
          readiness: {
            overall_state: buildReadiness.overallState,
            positions_count: buildReadiness.positionsCount,
            manual_missing_count: buildReadiness.manualMissing.length,
            spot_missing_count: buildReadiness.spotMissing.length,
            spot_stale_count: buildReadiness.spotStale.length,
            latest_default_profile_id: buildReadiness.latestDefaultProfile?.id ?? null,
          },
        },
```

Add dependencies to the `useMemo` dependency list:

```ts
    accountingDate,
    activeNode,
    activeSection,
    buildFilter,
    buildReadiness,
```

- [ ] **Step 4: Tighten mobile and density CSS**

Review the rendered page after Tasks 3-5. If buttons wrap poorly, add:

```css
.wl-pricing-build__actions .wl-button {
  min-height: 36px;
}

.wl-build-map__node {
  min-height: 112px;
}

.wl-pricing-params__tiles {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

@media (max-width: 1100px) {
  .wl-pricing-params__tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd frontend
npm test -- src/routes/PricingParameters.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/PricingParameters.tsx frontend/src/routes/PricingParameters.css frontend/src/routes/PricingParameters.test.tsx
git commit -m "feat(pricing): expose build companion context"
```

## Task 7: Browser Verification

**Files:**
- No required code files.
- Optional screenshots under ignored local output only; do not commit screenshots.

- [ ] **Step 1: Run frontend test suite for touched route**

Run:

```bash
cd frontend
npm test -- src/routes/pricingBuildReadiness.test.ts src/routes/PricingParameters.test.tsx src/routes/PricingParameters.live.test.tsx
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend
npm run build
```

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 3: Start or reuse local app**

Check ports:

```bash
lsof -nP -iTCP:5173 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

If no frontend server is running:

```bash
cd frontend
npm run dev
```

Expected: Vite serves `http://localhost:5173`.

- [ ] **Step 4: Browser smoke at desktop width**

Use Browser / Playwright:

1. Open `http://localhost:5173`.
2. Navigate to `Pricing Parameters`.
3. Confirm the first viewport shows `Daily build console`, the four build-map nodes, and `Build Default Profile`.
4. Click `Manual`; confirm lower table shows manual blockers.
5. Click `AKShare`; confirm lower table shows spot attention rows.
6. Click `Snapshot`; confirm profile library opens.

- [ ] **Step 5: Browser smoke at narrow width**

Resize to a narrow viewport such as `390x844`.

Confirm:

- Build map stacks without text overlap.
- Action buttons wrap but stay readable.
- The build action remains visible above the long table.
- Build input table scrolls horizontally instead of clipping text.

- [ ] **Step 6: Final commit only if verification caused code changes**

If CSS or code changed during browser verification:

```bash
git add frontend/src/routes/PricingParameters.css frontend/src/routes/PricingParameters.tsx
git commit -m "fix(pricing): polish build companion layout"
```

If no changes were needed, do not create an empty commit.

## Self-Review Checklist

- Spec coverage:
  - Build-first console: Tasks 3 and 5.
  - Interactive map: Task 3.
  - Blocker chips: Tasks 2 and 3.
  - Build Inputs / Profile Library segmentation: Tasks 2, 4, and 5.
  - Accounting-date freshness: Tasks 1 and 5.
  - Inline error behavior: Tasks 2 and 5.
  - Profile date prefix and origin badge cleanup: Tasks 1 and 4.
  - Browser verification: Task 7.
- No backend endpoints are introduced.
- Positions and Risk consumers are not modified except for shared types already in use.
- No deferred implementation steps remain.

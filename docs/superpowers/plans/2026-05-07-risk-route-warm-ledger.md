# Risk Route Migration (Plan 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the **Risk** route off PlaceholderRoute and onto the Warm Ledger design system. Build the dashboard grid layout (2-column top + full-width bottom) with three panels: GreeksSummary, PnlAttribution, ScenarioGrid. Wire to existing `POST /api/risk/runs` and `POST /api/reports/jobs`.

**Architecture:** Three independent panel components compose into a dashboard-grid route. The presentational layer takes risk metrics + portfolio list as props (testable in isolation); a `.live.tsx` container fetches portfolios, runs risk, and handles "promote to Report" wiring. Each panel exposes an `onPromoteToReport` callback that the live container fulfills via `POST /api/reports/jobs` with `report_type='risk'`.

**Tech Stack:** React 19 · Vite · vanilla CSS + custom properties · existing primitives from foundation: Button, Tile, Panel, Badge, Empty, PageHeader. No new external deps.

**Spec:** `docs/superpowers/specs/2026-05-07-ui-ux-redesign-design.md` (commit 141641a) — section "Per-route layouts · 4. Risk".

**Foundation:** commits f0ca7fc..8883c34. **Plan 2:** commits 6e31538..29b1a86 (RFQ Approval + Client RFQ shipped).

**Branch:** continues on `main` (user's standing consent).

## Backend reality (drives every scope decision below)

`POST /api/risk/runs` (body: `{portfolio_id, method='summary', market_snapshot_id?}`) returns:

```ts
{
  portfolio_id: number;
  method: string;
  metrics: {
    totals: {
      market_value: number;
      delta_proxy: number;
      gross_notional: number;
      pnl: number;
      one_day_var_proxy: number;
    };
    positions: Array<{
      position_id: number;
      underlying: string;
      product_type: string;
      quantity: number;
      price: number;
      market_value: number;
      gross_notional: number;
      pnl: number;
      delta_proxy: number;
      pricing_ok: boolean;
      pricing_error: string | null;
    }>;
  };
}
```

What this means for the spec:

- **Δ exists** as `delta_proxy` (aggregate + per-position). Labeled "Δ proxy" to be honest about what it is.
- **Γ, V, θ, ρ DO NOT EXIST** in the backend. The GreeksSummary panel renders them as deferred placeholders with a "pending backend Greeks extension" badge.
- **P&L attribution by source (spot/vol/theta) DOES NOT EXIST.** Per-position `pnl` IS available, so the PnlAttribution panel pivots to **"P&L by Position"** — bars per position from the positions array. This is a real signal, just a different cut than the spec's source-attribution.
- **Scenario grid (shift × vol matrix) HAS NO BACKEND DATA.** ScenarioGrid renders the spec's grid layout but with empty cells and a clear "Scenario engine deferred" overlay. No fake numbers.
- **"Promote to Report"** wires for real to `POST /api/reports/jobs` with `{report_type: 'risk', portfolio_id, title: 'Risk Run · <portfolio name>'}`. Reports route migration is Plan 4 — until then, success feedback is a transient status line on the Risk page (no global Toast yet).

Endpoints used:
- `GET /api/portfolios` — list portfolios for the picker
- `POST /api/risk/runs` — `{portfolio_id, method: 'summary'}` → metrics
- `POST /api/reports/jobs` — `{report_type: 'risk', portfolio_id, title}` → ReportJobOut

---

## File structure

**New files (components):**
- `frontend/src/components/GreeksSummary.tsx` (+ .css + .test.tsx) — aggregate risk metrics
- `frontend/src/components/PnlAttribution.tsx` (+ .css + .test.tsx) — P&L by position bars
- `frontend/src/components/ScenarioGrid.tsx` (+ .css + .test.tsx) — deferred-state grid

**New files (route):**
- `frontend/src/routes/Risk.tsx` (+ .css) — dashboard grid composition
- `frontend/src/routes/Risk.live.tsx` — API container

**Modified files:**
- `frontend/src/main.tsx` — replace `<PlaceholderRoute title="Risk" />` with `<RiskLive />`
- `README.md` — mark Plan 3 done

**Shared types (declared inline in Risk.tsx; not extracted to types.ts):**

```ts
export type RiskTotals = {
  market_value: number;
  delta_proxy: number;
  gross_notional: number;
  pnl: number;
  one_day_var_proxy: number;
};

export type RiskPosition = {
  position_id: number;
  underlying: string;
  product_type: string;
  quantity: number;
  price: number;
  market_value: number;
  gross_notional: number;
  pnl: number;
  delta_proxy: number;
  pricing_ok: boolean;
  pricing_error: string | null;
};

export type RiskMetrics = {
  totals: RiskTotals;
  positions: RiskPosition[];
};
```

These types are component-local (used only by Risk components). If a future plan needs them across routes, extract to `frontend/src/types.ts` then.

---

# Phase A · Panel Components

## Task 1: Build GreeksSummary

**Files:**
- Create: `frontend/src/components/GreeksSummary.tsx`
- Create: `frontend/src/components/GreeksSummary.css`
- Create: `frontend/src/components/GreeksSummary.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/GreeksSummary.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GreeksSummary } from './GreeksSummary';

const totals = {
  market_value: 38_200_000,
  delta_proxy: -0.07,
  gross_notional: 50_000_000,
  pnl: 700_000,
  one_day_var_proxy: 1_250_000,
};

describe('GreeksSummary', () => {
  it('renders aggregate metrics from totals', () => {
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/Δ proxy/i)).toBeInTheDocument();
    expect(screen.getByText('-0.07')).toBeInTheDocument();
    expect(screen.getByText(/1D VaR/i)).toBeInTheDocument();
    expect(screen.getByText(/PnL/i)).toBeInTheDocument();
  });

  it('renders deferred badge for missing Greeks', () => {
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/Γ.*V.*θ.*ρ.*pending/i)).toBeInTheDocument();
  });

  it('shows skeleton when totals is null', () => {
    const { container } = render(<GreeksSummary totals={null} onPromoteToReport={() => {}} />);
    expect(container.querySelectorAll('.wl-skeleton').length).toBeGreaterThan(0);
  });

  it('calls onPromoteToReport when ↗ button clicked', async () => {
    const onPromoteToReport = vi.fn();
    render(<GreeksSummary totals={totals} onPromoteToReport={onPromoteToReport} />);
    await userEvent.click(screen.getByRole('button', { name: /promote/i }));
    expect(onPromoteToReport).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run from `/Users/fuxinyao/open-otc-trading/frontend`: `npm test GreeksSummary -- --run`
Expected: FAIL with "Cannot find module './GreeksSummary'".

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/GreeksSummary.tsx`:

```tsx
import { Tile } from './Tile';
import { Skeleton } from './Skeleton';
import './GreeksSummary.css';

export type GreeksTotals = {
  market_value: number;
  delta_proxy: number;
  gross_notional: number;
  pnl: number;
  one_day_var_proxy: number;
};

type Props = {
  totals: GreeksTotals | null;
  onPromoteToReport: () => void;
};

function fmtSigned(n: number, digits = 2): string {
  return (n >= 0 ? '+' : '') + n.toFixed(digits);
}

function fmtMillions(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toFixed(0);
}

export function GreeksSummary({ totals, onPromoteToReport }: Props) {
  return (
    <section className="wl-greeks">
      <header className="wl-greeks__head">
        <span className="wl-greeks__title">GREEKS · PORTFOLIO</span>
        <button
          type="button"
          className="wl-greeks__promote"
          aria-label="Promote to Report"
          onClick={onPromoteToReport}
        >
          ↗
        </button>
      </header>
      <div className="wl-greeks__body">
        {totals ? (
          <>
            <div className="wl-greeks__tiles">
              <Tile label="Δ proxy" value={fmtSigned(totals.delta_proxy)} variant={totals.delta_proxy >= 0 ? 'pos' : 'neg'} />
              <Tile label="PnL" value={fmtMillions(totals.pnl)} variant={totals.pnl >= 0 ? 'pos' : 'neg'} />
              <Tile label="1D VaR proxy" value={fmtMillions(totals.one_day_var_proxy)} />
              <Tile label="Notional" value={fmtMillions(totals.gross_notional)} />
            </div>
            <div className="wl-greeks__deferred">
              Γ · V · θ · ρ — pending backend Greeks extension (deferred to Plan 5)
            </div>
          </>
        ) : (
          <div className="wl-greeks__skeletons">
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
          </div>
        )}
      </div>
    </section>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/GreeksSummary.css`:

```css
.wl-greeks { border: 1px solid var(--ink); background: var(--paper); display: flex; flex-direction: column; }
.wl-greeks__head {
  background: var(--ink);
  color: var(--paper);
  padding: 6px var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-greeks__title {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-greeks__promote {
  background: none;
  border: 0;
  color: var(--paper);
  font-family: var(--font-numeric);
  font-size: 16px;
  cursor: pointer;
  padding: 0 4px;
}
.wl-greeks__promote:hover { color: var(--paper-2); }
.wl-greeks__body { padding: var(--panel-padding); display: flex; flex-direction: column; gap: var(--gap-3); }
.wl-greeks__tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--gap-2);
}
.wl-greeks__deferred {
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  padding: var(--gap-2);
  border: 1px dashed var(--hairline-2);
  text-align: center;
}
.wl-greeks__skeletons { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--gap-2); }
```

- [ ] **Step 4: Run tests, expect 4 PASS**

Run: `npm test GreeksSummary -- --run`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/GreeksSummary.tsx frontend/src/components/GreeksSummary.css frontend/src/components/GreeksSummary.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add GreeksSummary panel with deferred Γ/V/θ/ρ"
```

## Task 2: Build PnlAttribution

**Files:**
- Create: `frontend/src/components/PnlAttribution.tsx`
- Create: `frontend/src/components/PnlAttribution.css`
- Create: `frontend/src/components/PnlAttribution.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/PnlAttribution.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PnlAttribution } from './PnlAttribution';

const positions = [
  { position_id: 1, underlying: 'CSI500', product_type: 'EuropeanVanillaOption', quantity: 1, price: 10, market_value: 10, gross_notional: 100, pnl: 50_000, delta_proxy: 1, pricing_ok: true, pricing_error: null },
  { position_id: 2, underlying: 'HSCEI',  product_type: 'BarrierOption',         quantity: 1, price: 8,  market_value: 8,  gross_notional: 80,  pnl: -20_000, delta_proxy: 1, pricing_ok: true, pricing_error: null },
  { position_id: 3, underlying: 'CSI300', product_type: 'EuropeanVanillaOption', quantity: 1, price: 5,  market_value: 5,  gross_notional: 50,  pnl: 10_000,  delta_proxy: 1, pricing_ok: true, pricing_error: null },
];

describe('PnlAttribution', () => {
  it('renders one row per position', () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    expect(screen.getByText('CSI500')).toBeInTheDocument();
    expect(screen.getByText('HSCEI')).toBeInTheDocument();
    expect(screen.getByText('CSI300')).toBeInTheDocument();
  });

  it('uses pos variant for positive pnl', () => {
    const { container } = render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    const posBars = container.querySelectorAll('.wl-attr__bar--pos');
    expect(posBars.length).toBe(2);
  });

  it('uses neg variant for negative pnl', () => {
    const { container } = render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    const negBars = container.querySelectorAll('.wl-attr__bar--neg');
    expect(negBars.length).toBe(1);
  });

  it('shows empty state when positions list is empty', () => {
    render(<PnlAttribution positions={[]} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/no priced positions/i)).toBeInTheDocument();
  });

  it('calls onPromoteToReport when ↗ clicked', async () => {
    const onPromoteToReport = vi.fn();
    render(<PnlAttribution positions={positions} onPromoteToReport={onPromoteToReport} />);
    await userEvent.click(screen.getByRole('button', { name: /promote/i }));
    expect(onPromoteToReport).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test PnlAttribution -- --run`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/PnlAttribution.tsx`:

```tsx
import { Empty } from './Empty';
import './PnlAttribution.css';

export type AttributionPosition = {
  position_id: number;
  underlying: string;
  product_type: string;
  quantity: number;
  price: number;
  market_value: number;
  gross_notional: number;
  pnl: number;
  delta_proxy: number;
  pricing_ok: boolean;
  pricing_error: string | null;
};

type Props = {
  positions: AttributionPosition[];
  onPromoteToReport: () => void;
};

function fmtSigned(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${n >= 0 ? '+' : ''}${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `${n >= 0 ? '+' : ''}${(n / 1_000).toFixed(1)}K`;
  return `${n >= 0 ? '+' : ''}${n.toFixed(0)}`;
}

export function PnlAttribution({ positions, onPromoteToReport }: Props) {
  const maxAbs = positions.reduce((acc, p) => Math.max(acc, Math.abs(p.pnl)), 0) || 1;
  return (
    <section className="wl-attr">
      <header className="wl-attr__head">
        <span className="wl-attr__title">P&amp;L · BY POSITION</span>
        <button
          type="button"
          className="wl-attr__promote"
          aria-label="Promote to Report"
          onClick={onPromoteToReport}
        >
          ↗
        </button>
      </header>
      <div className="wl-attr__body">
        {positions.length === 0 ? (
          <Empty message="No priced positions in this run" symbol="◌" />
        ) : (
          <ul className="wl-attr__list">
            {positions.map((p) => {
              const pct = (Math.abs(p.pnl) / maxAbs) * 100;
              const variant = p.pnl >= 0 ? 'pos' : 'neg';
              return (
                <li key={p.position_id} className="wl-attr__row">
                  <div className="wl-attr__label">
                    <span className="wl-attr__under">{p.underlying}</span>
                    <span className="wl-attr__product">{p.product_type}</span>
                  </div>
                  <div className="wl-attr__track">
                    <span className={`wl-attr__bar wl-attr__bar--${variant}`} style={{ width: `${pct}%` }} />
                  </div>
                  <div className={`wl-attr__value wl-attr__value--${variant}`}>{fmtSigned(p.pnl)}</div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/PnlAttribution.css`:

```css
.wl-attr { border: 1px solid var(--ink); background: var(--paper); display: flex; flex-direction: column; }
.wl-attr__head {
  background: var(--ink);
  color: var(--paper);
  padding: 6px var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-attr__title {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-attr__promote {
  background: none;
  border: 0;
  color: var(--paper);
  font-family: var(--font-numeric);
  font-size: 16px;
  cursor: pointer;
  padding: 0 4px;
}
.wl-attr__promote:hover { color: var(--paper-2); }
.wl-attr__body { padding: var(--panel-padding); }
.wl-attr__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--gap-2); }
.wl-attr__row {
  display: grid;
  grid-template-columns: 30% 1fr auto;
  gap: var(--gap-3);
  align-items: center;
}
.wl-attr__label { display: flex; flex-direction: column; min-width: 0; }
.wl-attr__under {
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  font-weight: 700;
  color: var(--ink);
}
.wl-attr__product {
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
}
.wl-attr__track {
  background: var(--paper-3);
  height: 12px;
  border: 1px solid var(--hairline);
  position: relative;
}
.wl-attr__bar {
  display: block;
  height: 100%;
}
.wl-attr__bar--pos { background: var(--pos); }
.wl-attr__bar--neg { background: var(--neg); }
.wl-attr__value {
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  font-weight: 700;
  text-align: right;
  min-width: 80px;
}
.wl-attr__value--pos { color: var(--pos); }
.wl-attr__value--neg { color: var(--neg); }
```

- [ ] **Step 4: Run tests, expect 5 PASS**

Run: `npm test PnlAttribution -- --run`
Expected: PASS — 5 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/PnlAttribution.tsx frontend/src/components/PnlAttribution.css frontend/src/components/PnlAttribution.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add PnlAttribution by-position bars"
```

## Task 3: Build ScenarioGrid

**Files:**
- Create: `frontend/src/components/ScenarioGrid.tsx`
- Create: `frontend/src/components/ScenarioGrid.css`
- Create: `frontend/src/components/ScenarioGrid.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ScenarioGrid.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScenarioGrid } from './ScenarioGrid';

describe('ScenarioGrid', () => {
  it('renders the spec grid layout (shift columns + vol rows)', () => {
    render(<ScenarioGrid onPromoteToReport={() => {}} />);
    // Shift column headers
    expect(screen.getByText('-3%')).toBeInTheDocument();
    expect(screen.getByText('+2%')).toBeInTheDocument();
    // Vol row labels
    expect(screen.getByText('-2v')).toBeInTheDocument();
    expect(screen.getByText('+2v')).toBeInTheDocument();
  });

  it('renders deferred-state overlay', () => {
    render(<ScenarioGrid onPromoteToReport={() => {}} />);
    expect(screen.getByText(/scenario engine deferred/i)).toBeInTheDocument();
  });

  it('disables promote button while deferred', () => {
    render(<ScenarioGrid onPromoteToReport={() => {}} />);
    expect(screen.getByRole('button', { name: /promote/i })).toBeDisabled();
  });

  it('does not call onPromoteToReport when disabled button clicked', async () => {
    const onPromoteToReport = vi.fn();
    render(<ScenarioGrid onPromoteToReport={onPromoteToReport} />);
    await userEvent.click(screen.getByRole('button', { name: /promote/i }));
    expect(onPromoteToReport).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `npm test ScenarioGrid -- --run`
Expected: FAIL.

- [ ] **Step 3: Write implementation**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ScenarioGrid.tsx`:

```tsx
import './ScenarioGrid.css';

const SHIFT_COLS = ['-3%', '-2%', '-1%', '0', '+1%', '+2%'];
const VOL_ROWS = ['-2v', '0v', '+2v'];

type Props = {
  onPromoteToReport: () => void;
};

export function ScenarioGrid({ onPromoteToReport }: Props) {
  return (
    <section className="wl-scenario">
      <header className="wl-scenario__head">
        <span className="wl-scenario__title">SCENARIO GRID · SHIFT × VOL</span>
        <button
          type="button"
          className="wl-scenario__promote"
          aria-label="Promote to Report"
          onClick={onPromoteToReport}
          disabled
        >
          ↗
        </button>
      </header>
      <div className="wl-scenario__body">
        <div className="wl-scenario__matrix" aria-hidden="true">
          <div className="wl-scenario__cell wl-scenario__cell--corner" />
          {SHIFT_COLS.map((shift) => (
            <div key={shift} className="wl-scenario__cell wl-scenario__cell--header">{shift}</div>
          ))}
          {VOL_ROWS.map((vol) => (
            <div key={vol} className="wl-scenario__row" style={{ display: 'contents' }}>
              <div className="wl-scenario__cell wl-scenario__cell--header">{vol}</div>
              {SHIFT_COLS.map((shift) => (
                <div key={shift} className="wl-scenario__cell wl-scenario__cell--empty">—</div>
              ))}
            </div>
          ))}
        </div>
        <div className="wl-scenario__overlay">
          <div className="wl-scenario__overlay-text">Scenario engine deferred</div>
          <div className="wl-scenario__overlay-detail">Backend shift × vol matrix lands in Plan 5.</div>
        </div>
      </div>
    </section>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/components/ScenarioGrid.css`:

```css
.wl-scenario { border: 1px solid var(--ink); background: var(--paper); display: flex; flex-direction: column; }
.wl-scenario__head {
  background: var(--ink);
  color: var(--paper);
  padding: 6px var(--gap-3);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.wl-scenario__title {
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.wl-scenario__promote {
  background: none;
  border: 0;
  color: var(--paper);
  font-family: var(--font-numeric);
  font-size: 16px;
  cursor: not-allowed;
  padding: 0 4px;
  opacity: 0.45;
}
.wl-scenario__body { position: relative; padding: var(--panel-padding); }
.wl-scenario__matrix {
  display: grid;
  grid-template-columns: 80px repeat(6, 1fr);
  gap: 1px;
  background: var(--paper-3);
  filter: blur(0.5px);
  opacity: 0.55;
}
.wl-scenario__cell {
  background: var(--paper);
  padding: var(--gap-2);
  font-family: var(--font-numeric);
  font-size: var(--type-num-m-size);
  text-align: right;
  color: var(--ink-2);
}
.wl-scenario__cell--header {
  font-family: var(--font-ui);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  font-weight: var(--type-caps-weight);
}
.wl-scenario__cell--corner { background: var(--paper-2); }
.wl-scenario__cell--empty { color: var(--hairline-2); }
.wl-scenario__overlay {
  position: absolute;
  inset: 0;
  background: color-mix(in oklab, var(--paper) 80%, transparent);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--gap-2);
  pointer-events: none;
}
.wl-scenario__overlay-text {
  font-family: var(--font-ui);
  font-size: var(--type-h3-size);
  font-weight: var(--type-h3-weight);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink);
  border: 1px solid var(--ink);
  padding: var(--gap-2) var(--gap-3);
  background: var(--paper);
}
.wl-scenario__overlay-detail {
  font-size: var(--type-small-size);
  color: var(--ink-2);
}
```

- [ ] **Step 4: Run tests, expect 4 PASS**

Run: `npm test ScenarioGrid -- --run`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/components/ScenarioGrid.tsx frontend/src/components/ScenarioGrid.css frontend/src/components/ScenarioGrid.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add ScenarioGrid in deferred state"
```

---

# Phase B · Risk Route + Live Container

## Task 4: Build Risk presentational route

**Files:**
- Create: `frontend/src/routes/Risk.tsx`
- Create: `frontend/src/routes/Risk.css`

(No test for this task — pure composition of already-tested panels. Typecheck is the gate.)

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Risk.tsx`:

```tsx
import { useId } from 'react';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { GreeksSummary, type GreeksTotals } from '../components/GreeksSummary';
import { PnlAttribution, type AttributionPosition } from '../components/PnlAttribution';
import { ScenarioGrid } from '../components/ScenarioGrid';
import './Risk.css';

export type RiskMetrics = {
  totals: GreeksTotals;
  positions: AttributionPosition[];
};

export type RiskPortfolioOption = {
  id: number;
  name: string;
};

type Props = {
  portfolios: RiskPortfolioOption[];
  selectedPortfolioId: number | null;
  metrics: RiskMetrics | null;
  running: boolean;
  feedback: string | null;
  onSelectPortfolio: (id: number) => void;
  onRunRisk: () => void;
  onPromoteGreeks: () => void;
  onPromotePnl: () => void;
  onPromoteScenario: () => void;
};

export function Risk({
  portfolios,
  selectedPortfolioId,
  metrics,
  running,
  feedback,
  onSelectPortfolio,
  onRunRisk,
  onPromoteGreeks,
  onPromotePnl,
  onPromoteScenario,
}: Props) {
  const pickerId = useId();
  const hasPortfolios = portfolios.length > 0;
  const chips: string[] = [];
  if (selectedPortfolioId != null) {
    const name = portfolios.find((p) => p.id === selectedPortfolioId)?.name ?? '';
    if (name) chips.push(name);
  }
  if (metrics) chips.push(`${metrics.positions.length} priced`);

  return (
    <>
      <PageHeader
        title="RISK"
        chips={chips}
        action={
          hasPortfolios ? (
            <div className="wl-risk__actions">
              <label htmlFor={pickerId} className="wl-risk__picker-label">Portfolio</label>
              <select
                id={pickerId}
                className="wl-risk__picker"
                value={selectedPortfolioId ?? ''}
                onChange={(e) => onSelectPortfolio(Number(e.target.value))}
              >
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <Button variant="primary" onClick={onRunRisk} disabled={running || selectedPortfolioId == null}>
                {running ? 'Running…' : 'Run Risk ⌘R'}
              </Button>
            </div>
          ) : null
        }
      />

      {feedback && (
        <div className="wl-risk__feedback" role="status" aria-live="polite">
          {feedback}
        </div>
      )}

      {!hasPortfolios ? (
        <Empty message="No portfolios available — create one in Positions to run risk." symbol="◌" />
      ) : (
        <div className="wl-risk__grid">
          <GreeksSummary
            totals={metrics?.totals ?? null}
            onPromoteToReport={onPromoteGreeks}
          />
          <PnlAttribution
            positions={metrics?.positions ?? []}
            onPromoteToReport={onPromotePnl}
          />
          <div className="wl-risk__span2">
            <ScenarioGrid onPromoteToReport={onPromoteScenario} />
          </div>
        </div>
      )}
    </>
  );
}
```

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Risk.css`:

```css
.wl-risk__actions {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
}
.wl-risk__picker-label {
  font-size: var(--type-caps-size);
  font-weight: var(--type-caps-weight);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-2);
}
.wl-risk__picker {
  border: 1px solid var(--hairline-2);
  background: var(--paper);
  color: var(--ink);
  padding: var(--input-padding-y) var(--input-padding-x);
  font-family: var(--font-ui);
  font-size: var(--type-body-size);
  border-radius: 0;
}
.wl-risk__feedback {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--pos);
  border: 1px solid var(--pos);
  padding: var(--gap-2) var(--gap-3);
  margin-bottom: var(--gap-3);
}
.wl-risk__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--gap-3);
  align-items: flex-start;
}
.wl-risk__span2 { grid-column: 1 / -1; }
@media (max-width: 1100px) {
  .wl-risk__grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 2: Typecheck**

Run from `/Users/fuxinyao/open-otc-trading/frontend`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/Risk.tsx frontend/src/routes/Risk.css
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): add Risk dashboard-grid route"
```

## Task 5: Build Risk live container

**Files:**
- Create: `frontend/src/routes/Risk.live.tsx`

- [ ] **Step 1: Create the file**

Create `/Users/fuxinyao/open-otc-trading/frontend/src/routes/Risk.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Portfolio } from '../types';
import { Risk, type RiskMetrics, type RiskPortfolioOption } from './Risk';
import { Skeleton } from '../components/Skeleton';
import { Empty } from '../components/Empty';

type RiskRunResponse = {
  portfolio_id: number;
  method: string;
  metrics: RiskMetrics;
};

type ReportJobOut = {
  id: number;
  report_type: string;
  status: string;
};

export function RiskLive() {
  const [portfolios, setPortfolios] = useState<RiskPortfolioOption[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<RiskMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api<Portfolio[]>('/api/portfolios');
        if (cancelled) return;
        const options: RiskPortfolioOption[] = list.map((p) => ({ id: p.id, name: p.name }));
        setPortfolios(options);
        if (options.length > 0) setSelectedId(options[0].id);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleRunRisk = async () => {
    if (selectedId == null) return;
    setRunning(true);
    setFeedback(null);
    try {
      const res = await api<RiskRunResponse>('/api/risk/runs', {
        method: 'POST',
        body: JSON.stringify({ portfolio_id: selectedId, method: 'summary' }),
      });
      setMetrics(res.metrics);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const promoteToReport = async () => {
    if (selectedId == null) return;
    const portfolio = portfolios.find((p) => p.id === selectedId);
    if (!portfolio) return;
    try {
      const job = await api<ReportJobOut>('/api/reports/jobs', {
        method: 'POST',
        body: JSON.stringify({
          report_type: 'risk',
          portfolio_id: selectedId,
          title: `Risk Run · ${portfolio.name}`,
        }),
      });
      setFeedback(`Risk report #${job.id} created — view in Reports (Plan 4).`);
      window.setTimeout(() => setFeedback(null), 6000);
    } catch (e) {
      setFeedback(`Could not create report: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={300} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load portfolios: ${error}`} />;
  }

  return (
    <Risk
      portfolios={portfolios}
      selectedPortfolioId={selectedId}
      metrics={metrics}
      running={running}
      feedback={feedback}
      onSelectPortfolio={setSelectedId}
      onRunRisk={handleRunRisk}
      onPromoteGreeks={promoteToReport}
      onPromotePnl={promoteToReport}
      onPromoteScenario={promoteToReport}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

Run from `/Users/fuxinyao/open-otc-trading/frontend`: `npx tsc -b --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/Risk.live.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): wire Risk route to live API + promote-to-report"
```

## Task 6: Wire Risk into main.tsx

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Add the import**

Open `/Users/fuxinyao/open-otc-trading/frontend/src/main.tsx`. After the line `import { ClientRfqLive } from './routes/ClientRfq.live';`, add:

```ts
import { RiskLive } from './routes/Risk.live';
```

- [ ] **Step 2: Replace placeholder render**

Find this exact line:

```tsx
        {route === 'risk'      && <PlaceholderRoute title="Risk" />}
```

Replace with:

```tsx
        {route === 'risk'      && <RiskLive />}
```

(Preserve the existing whitespace alignment.)

- [ ] **Step 3: Verify**

Run from `/Users/fuxinyao/open-otc-trading/frontend`:

```bash
npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
timeout 6 npm run dev 2>&1 | head -10
```
Expected: VITE ready, no errors.

```bash
npm test 2>&1 | tail -5
```
Expected: all tests pass (should be ~106 now: prior 93 + 13 new from this plan).

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/main.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(frontend): replace placeholder with RiskLive"
```

---

# Phase C · Smoke + Documentation

## Task 7: Final automated smoke + README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run all automated checks**

Run from `/Users/fuxinyao/open-otc-trading/frontend`:

```bash
npm test 2>&1 | tail -10
```
Expected: all tests pass.

```bash
npx tsc -b --noEmit; echo "tsc=$?"
```
Expected: `tsc=0`.

```bash
timeout 6 npm run dev 2>&1 | head -15
```
Expected: VITE ready.

```bash
npm run build 2>&1 | tail -10
```
Expected: build succeeds.

- [ ] **Step 2: Update README**

Open `/Users/fuxinyao/open-otc-trading/README.md`. Find the "Follow-up plans" section. Update the bullet list:

Replace:

```markdown
Follow-up plans migrate the remaining routes:

- ✅ Plan 2 — Client RFQ + RFQ Approval (`docs/superpowers/plans/2026-05-07-rfq-routes-warm-ledger.md`)
- Plan 3 — Risk
- Plan 4 — Reports + Agent Desk
- Plan 5 — accessibility audit + prefers-reduced-motion verification
```

With:

```markdown
Follow-up plans migrate the remaining routes:

- ✅ Plan 2 — Client RFQ + RFQ Approval (`docs/superpowers/plans/2026-05-07-rfq-routes-warm-ledger.md`)
- ✅ Plan 3 — Risk (`docs/superpowers/plans/2026-05-07-risk-route-warm-ledger.md`)
- Plan 4 — Reports + Agent Desk
- Plan 5 — accessibility audit + prefers-reduced-motion verification + backend Greeks/scenario extension
```

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add README.md
git -C /Users/fuxinyao/open-otc-trading commit -m "docs: note Plan 3 Risk route migration shipped"
```

- [ ] **Step 4: Hand off browser smoke checklist**

For the user to verify in a browser at `http://localhost:5173`:

- Risk tab opens; if no portfolios exist, an Empty state asks the user to create one in Positions.
- With portfolios available: portfolio picker + Run Risk button render in the page header.
- Click Run Risk → backend call succeeds, GreeksSummary panel populates with `Δ proxy / PnL / 1D VaR proxy / Notional` tiles, PnlAttribution shows one bar per priced position, ScenarioGrid stays in deferred-state with the overlay.
- Pos/neg pnl values render in green/red appropriately.
- Click `↗` on Greeks or PnL panel → `feedback` line shows "Risk report #N created — view in Reports (Plan 4)." and auto-clears after ~6s.
- Click `↗` on Scenario panel → button is disabled (deferred state); nothing happens.
- Switching the portfolio dropdown selects a different portfolio; Run Risk on the new selection refreshes the panels.
- Theme toggle and density toggle still work; numerics in the panels respect density.

---

# Self-Review Notes

(Performed after writing the plan; fixes applied inline.)

**Spec coverage (section "Per-route layouts · 4. Risk"):**
- "Top-left: Greeks summary panel (Δ, Γ, V, θ, ρ)" → Task 1 (GreeksSummary). Δ implemented as `delta_proxy` from backend; Γ/V/θ/ρ rendered as deferred line. Backend extension noted in Plan 5.
- "Top-right: P&L attribution panel (horizontal bars per attribution source: spot, vol, theta)" → Task 2 (PnlAttribution). **Pivoted to "P&L by Position"** because backend provides per-position pnl but no source attribution. Documented in Backend Reality section.
- "Bottom (spans both columns): scenario grid — shift × vol matrix, color-coded pos/neg per cell" → Task 3 (ScenarioGrid). Layout per spec, deferred state because backend has no scenario engine.
- "Every panel header has `↗` 'promote to Report' affordance that creates a Reports entry from the panel's current data" → Tasks 1, 2, 3 each implement an `onPromoteToReport` callback button; Task 5 wires them to `POST /api/reports/jobs`. Scenario's button is disabled in deferred state.

**Type consistency:**
- `GreeksTotals` exported from GreeksSummary (Task 1), imported by Risk.tsx (Task 4) ✓
- `AttributionPosition` exported from PnlAttribution (Task 2), imported by Risk.tsx ✓
- `RiskMetrics` and `RiskPortfolioOption` defined in Risk.tsx (Task 4), imported by Risk.live.tsx (Task 5) ✓
- `RiskRunResponse.metrics` shape matches `RiskMetrics` ✓

**Placeholder scan:** No "TBD", "TODO", "implement later", "similar to Task N" tokens. Every code-bearing step has full code.

**Backend reality check:**
- `POST /api/risk/runs` body: `{portfolio_id, method, market_snapshot_id?}` — Task 5 sends `{portfolio_id, method: 'summary'}` ✓
- `POST /api/reports/jobs` body: `{report_type, portfolio_id, rfq_id?, title}` — Task 5 sends `{report_type: 'risk', portfolio_id, title}` ✓
- `GET /api/portfolios` returns `Portfolio[]` with `{id, name, base_currency, positions}` — Task 5 maps to `{id, name}` ✓

**Out of scope (explicitly deferred):**
- Backend Greeks (Γ, V, θ, ρ) — needs quant-ark integration. Plan 5.
- Backend scenario engine (shift × vol matrix) — needs new endpoint. Plan 5.
- True P&L attribution by source (spot/vol/theta) — needs decomposition logic in `calculate_portfolio_risk`. Plan 5.
- Reports route to view created risk reports — Plan 4.
- Global Toast component for promote-to-report success — using inline feedback line for now; global Toast can land with any later plan that needs it.

# Follow-up plans (after this lands)

- **Plan 4:** Migrate Reports + Agent Desk routes (timeline view of risk/portfolio/RFQ reports; chat with assets pane).
- **Plan 5:** Backend Greeks/scenario extension + accessibility audit + reduced-motion verification + Berkeley Mono procurement.

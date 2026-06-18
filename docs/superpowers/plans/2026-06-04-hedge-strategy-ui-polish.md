# Hedge Strategy Tab UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Hedge Strategy tab around `Panel`/`Tile` components (KPI strip, legs panel) and add a booking-result banner with a per-leg receipt and view-in-Positions navigation.

**Architecture:** Frontend-only. The presentational `HedgeStrategy.tsx` gains a `BookingBanner` and `KpiTiles` section and wraps the legs table in the existing `Panel`; `HedgeStrategy.live.tsx` captures the `/api/hedging/book` response (or error) into a `bookingResult` state; navigation threads `onNavigate` from `main.tsx` down to a narrow `onViewPositions` callback. No backend changes — the book endpoint already returns `position_ids`.

**Tech Stack:** React 18 + TypeScript, vitest + @testing-library/react, plain CSS with design tokens.

**Spec:** `docs/superpowers/specs/2026-06-04-hedge-strategy-ui-polish-design.md`

---

## ⚠️ Execution environment

**Work in the primary checkout `/Users/fuxinyao/open-otc-trading` — do NOT create a worktree.** This plan layers on uncommitted working-tree changes (pricing-profile select, hard-band diagnostics, loosen-band/book-anyway); a worktree from HEAD would not contain them. Task 0 commits that in-flight work first so later commits stay clean. A concurrent agent sometimes shares this repo — committing early pins the WIP against interleaving.

All frontend commands run from `/Users/fuxinyao/open-otc-trading/frontend`; pytest runs from the repo root.

---

### Task 0: Pin the in-flight working-tree changes as their own commit

The working tree already contains a complete, separate feature (verify, then commit it so this plan's commits don't sweep it in). **Confirm with the user before committing if anything looks unfinished or tests fail.**

**Files:**
- Commit (pre-existing modifications, no edits): `backend/app/services/domains/hedging_strategy.py`, `backend/app/services/hedging_legs.py`, `frontend/src/routes/HedgeStrategy.css`, `frontend/src/routes/HedgeStrategy.live.test.tsx`, `frontend/src/routes/HedgeStrategy.live.tsx`, `frontend/src/routes/HedgeStrategy.test.tsx`, `frontend/src/routes/HedgeStrategy.tsx`, `frontend/src/types.ts`, `tests/test_hedging_book.py`, `tests/test_hedging_legs.py`, `tests/test_hedging_strategy_api.py`

- [ ] **Step 1: Verify backend tests pass**

Run (repo root): `python -m pytest tests/test_hedging_legs.py tests/test_hedging_strategy_api.py tests/test_hedging_book.py -q`
Expected: all pass.

- [ ] **Step 2: Verify frontend tests pass**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx src/routes/HedgeStrategy.live.test.tsx`
Expected: all pass.

- [ ] **Step 3: Commit the in-flight work**

```bash
git add backend/app/services/domains/hedging_strategy.py backend/app/services/hedging_legs.py \
  frontend/src/routes/HedgeStrategy.css frontend/src/routes/HedgeStrategy.live.test.tsx \
  frontend/src/routes/HedgeStrategy.live.tsx frontend/src/routes/HedgeStrategy.test.tsx \
  frontend/src/routes/HedgeStrategy.tsx frontend/src/types.ts \
  tests/test_hedging_book.py tests/test_hedging_legs.py tests/test_hedging_strategy_api.py
git commit -m "feat(hedging): hard-band diagnostics with loosen-band/book-anyway; pricing-profile risk runs"
```

Expected: `git status` shows a clean tree (except untracked files).

---

### Task 1: Booking banner (types + presentational component + CSS)

**Files:**
- Modify: `frontend/src/types.ts` (after the `HedgeGreeks` type, ~line 817)
- Modify: `frontend/src/routes/HedgeStrategy.tsx`
- Modify: `frontend/src/routes/HedgeStrategy.css`
- Test: `frontend/src/routes/HedgeStrategy.test.tsx`

- [ ] **Step 1: Add the result types to `types.ts`**

Insert after the `export type HedgeGreeks = …` line:

```ts
/** Response of POST /api/hedging/book — position_ids align positionally with
 *  the request's non-zero-quantity legs (backend skips qty 0 in order). */
export interface HedgeBookResponse {
  status: 'booked'
  portfolio_id: number
  underlying: string
  risk_run_id: number
  position_ids: number[]
}

/** Frontend-shaped outcome of the last Book attempt, for the banner. */
export type HedgeBookingResult =
  | {
      kind: 'success'
      portfolioName: string
      riskRunDate: string | null
      legs: { contractCode: string; quantity: number; role: string; positionId: number }[]
    }
  | { kind: 'error'; message: string }
```

- [ ] **Step 2: Write failing presentational tests**

In `HedgeStrategy.test.tsx`: add `HedgeBookingResult` to the type import, add the two new required props to `baseProps`, and append the banner tests.

```tsx
// import line becomes:
import type { HedgeableSummary, HedgeBookingResult, HedgeCandidate, HedgeProposal } from '../types'

// add inside baseProps:
  bookingResult: null,
  onDismissBookingResult: () => {},
```

```tsx
const bookedResult: HedgeBookingResult = {
  kind: 'success', portfolioName: 'Desk Book', riskRunDate: '2026-06-01',
  legs: [{ contractCode: 'IC2406', quantity: -1, role: 'delta', positionId: 214 }],
}

it('renders a booking receipt with per-leg position ids', () => {
  render(<HedgeStrategy {...baseProps} bookingResult={bookedResult} />)
  expect(screen.getByText(/Hedge booked to Desk Book/)).toBeInTheDocument()
  expect(screen.getByText(/risk run 2026-06-01/)).toBeInTheDocument()
  expect(screen.getByText(/IC2406 × -1 \(delta\) → position #214/)).toBeInTheDocument()
})

it('renders the booking failure with the API error detail', () => {
  render(<HedgeStrategy {...baseProps} bookingResult={{ kind: 'error', message: 'boom from API' }} />)
  expect(screen.getByText(/Booking failed — nothing was booked/)).toBeInTheDocument()
  expect(screen.getByText('boom from API')).toBeInTheDocument()
})

it('dismisses the booking banner', () => {
  const onDismissBookingResult = vi.fn()
  render(
    <HedgeStrategy {...baseProps} bookingResult={bookedResult}
      onDismissBookingResult={onDismissBookingResult} />,
  )
  fireEvent.click(screen.getByRole('button', { name: /dismiss booking result/i }))
  expect(onDismissBookingResult).toHaveBeenCalled()
})

it('shows the view-in-Positions link only when wired', () => {
  const onViewPositions = vi.fn()
  const { rerender } = render(
    <HedgeStrategy {...baseProps} bookingResult={bookedResult} onViewPositions={onViewPositions} />,
  )
  fireEvent.click(screen.getByRole('button', { name: /view in positions/i }))
  expect(onViewPositions).toHaveBeenCalled()
  rerender(<HedgeStrategy {...baseProps} bookingResult={bookedResult} />)
  expect(screen.queryByRole('button', { name: /view in positions/i })).not.toBeInTheDocument()
})
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx`
Expected: the 4 new tests FAIL (unknown props / missing banner); pre-existing tests still pass (extra unknown props are ignored until typed — TS errors appear at the vitest type level only if checked; failures will be runtime "unable to find element").

- [ ] **Step 4: Implement the banner in `HedgeStrategy.tsx`**

Add to the type import: `HedgeBookingResult`. Add the new component above `export function HedgeStrategy`:

```tsx
/** Outcome of the last Book attempt: success receipt or atomic-failure note. */
function BookingBanner(props: {
  result: HedgeBookingResult
  onDismiss: () => void
  onViewPositions?: () => void
}) {
  const { result, onDismiss, onViewPositions } = props
  if (result.kind === 'error') {
    return (
      <div className="hedge-strategy__banner hedge-strategy__banner--err" role="alert">
        <div className="hedge-strategy__banner-body">
          <p><b>Booking failed — nothing was booked.</b></p>
          <p className="hedge-strategy__banner-detail">{result.message}</p>
        </div>
        <Button variant="ghost" iconOnly aria-label="dismiss booking result" onClick={onDismiss}>✕</Button>
      </div>
    )
  }
  return (
    <div className="hedge-strategy__banner hedge-strategy__banner--ok" role="status">
      <div className="hedge-strategy__banner-body">
        <p>
          <b>Hedge booked to {result.portfolioName}</b>
          {result.riskRunDate && <> · risk run {result.riskRunDate}</>}
          {onViewPositions && (
            <> · <Button variant="ghost" onClick={onViewPositions}>view in Positions</Button></>
          )}
        </p>
        <ul className="hedge-strategy__banner-legs">
          {result.legs.map((l) => (
            <li key={l.positionId}>
              {l.contractCode} × {l.quantity} ({l.role}) → position #{l.positionId}
            </li>
          ))}
        </ul>
      </div>
      <Button variant="ghost" iconOnly aria-label="dismiss booking result" onClick={onDismiss}>✕</Button>
    </div>
  )
}
```

Add to the `HedgeStrategy` props type (and destructuring):

```tsx
  bookingResult: HedgeBookingResult | null
  onDismissBookingResult: () => void
  onViewPositions?: () => void
```

Render it directly after the closing `</div>` of `hedge-strategy__bar` (before the `no_risk_run` block):

```tsx
      {bookingResult && (
        <BookingBanner result={bookingResult} onDismiss={onDismissBookingResult}
          onViewPositions={onViewPositions} />
      )}
```

- [ ] **Step 5: Add banner CSS to `HedgeStrategy.css`** (in the "messages + actions" section)

```css
/* ── booking result banner ── */

.hedge-strategy__banner {
  display: flex;
  align-items: flex-start;
  gap: var(--gap-2);
  border: 1px solid var(--hairline);
  padding: var(--gap-2) var(--gap-3);
  font-size: var(--type-small-size);
}

.hedge-strategy__banner--ok { border-left: 4px solid var(--pos); color: var(--ink); }
.hedge-strategy__banner--err { border-left: 4px solid var(--neg); color: var(--neg); }

.hedge-strategy__banner-body { flex: 1; display: grid; gap: var(--gap-1); }
.hedge-strategy__banner-body p { margin: 0; }
.hedge-strategy__banner--ok b { color: var(--pos); }

.hedge-strategy__banner-legs {
  margin: 0;
  padding: 0;
  list-style: none;
  font-family: var(--font-numeric);
}

.hedge-strategy__banner-detail { font-family: var(--font-numeric); }
```

- [ ] **Step 6: Run tests to verify they pass**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx`
Expected: PASS (all, including the 4 new).

- [ ] **Step 7: Fix the live component's compile error**

`HedgeStrategy.live.tsx` now fails TS (missing required props). Add a temporary literal pass-through so the tree compiles (Task 4 replaces it with real state):

```tsx
        bookingResult={null} onDismissBookingResult={() => {}}
```

(place next to the other props in the `<HedgeStrategy …/>` render)

Run (frontend/): `npx tsc -b`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/HedgeStrategy.tsx \
  frontend/src/routes/HedgeStrategy.css frontend/src/routes/HedgeStrategy.test.tsx \
  frontend/src/routes/HedgeStrategy.live.tsx
git commit -m "feat(hedging-ui): booking result banner with per-leg receipt"
```

---

### Task 2: KPI tiles (exposure → residual)

**Files:**
- Modify: `frontend/src/routes/HedgeStrategy.tsx`
- Modify: `frontend/src/routes/HedgeStrategy.css`
- Test: `frontend/src/routes/HedgeStrategy.test.tsx`

- [ ] **Step 1: Write failing tests**

In `HedgeStrategy.test.tsx`, **replace** the test `shows the per-underlying greek readout` (it asserts the removed `Exposure` row) with:

```tsx
it('shows exposure KPI tiles with bands before a solve', () => {
  render(<HedgeStrategy {...baseProps} proposal={null} />)
  expect(screen.getByText('Δ CASH')).toBeInTheDocument()
  expect(screen.getByText('1,120,000')).toBeInTheDocument()
  expect(screen.getByText('band ±500,000')).toBeInTheDocument()
  expect(screen.queryByText('Δ RESIDUAL')).not.toBeInTheDocument()
})

it('switches KPI tiles to in-band residuals after a solve', () => {
  const { container } = render(<HedgeStrategy {...baseProps} />)
  expect(screen.getByText('Δ RESIDUAL')).toBeInTheDocument()
  expect(screen.getByText('0 ✓')).toBeInTheDocument() // delta residual, in_band true
  expect(screen.getByText('from 1,120,000 · band ±500,000')).toBeInTheDocument()
  expect(container.querySelector('.wl-tile--pos')).toBeTruthy()
})

it('marks out-of-band residual tiles red', () => {
  const outOfBand: HedgeProposal = {
    ...proposal,
    targets: { delta: -6035022, gamma: 0, vega: 0 },
    residual: { delta: -108452, gamma: 0, vega: 0 },
    in_band: { delta: false },
  }
  const { container } = render(
    <HedgeStrategy {...baseProps} targets={{ delta: -6035022, gamma: 0, vega: 0 }} proposal={outOfBand} />,
  )
  expect(screen.getByText('-108,452 ✗')).toBeInTheDocument()
  expect(container.querySelector('.wl-tile--neg')).toBeTruthy()
})

it('keeps tiles in exposure mode when the proposal is infeasible', () => {
  const infeasible = { ...proposal, status: 'infeasible' as const }
  render(<HedgeStrategy {...baseProps} proposal={infeasible} />)
  expect(screen.getByText('Δ CASH')).toBeInTheDocument()
  expect(screen.queryByText('Δ RESIDUAL')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx`
Expected: the 4 new tests FAIL ("unable to find … Δ CASH"); the replaced `Exposure` test is gone.

- [ ] **Step 3: Implement `KpiTiles` in `HedgeStrategy.tsx`**

Add imports:

```tsx
import { Tile, type TileVariant } from '../components/Tile'
```

Add above `BookingBanner`:

```tsx
const KPI_GLYPH: Record<keyof HedgeGreeks, string> = { delta: 'Δ', gamma: 'Γ', vega: 'ν' }

/** Exposure tiles that flip to would-be residuals once a solve produced one. */
function KpiTiles(props: {
  targets: HedgeGreeks
  proposal: HedgeProposal | null
  bands: HedgeGreeks | null
}) {
  const { targets, proposal, bands } = props
  const residual =
    proposal && proposal.status !== 'infeasible' && proposal.residual ? proposal.residual : null
  return (
    <div className="hedge-strategy__kpis">
      {GREEKS.map((g) => {
        const band = bands ? `band ±${fmt(bands[g])}` : null
        if (!residual) {
          return (
            <Tile key={g} label={`${KPI_GLYPH[g]} CASH`} value={fmt(targets[g])}
              delta={band ?? undefined} />
          )
        }
        const inBand = proposal?.in_band?.[g]
        const variant: TileVariant = inBand === true ? 'pos' : inBand === false ? 'neg' : 'default'
        const mark = inBand === true ? ' ✓' : inBand === false ? ' ✗' : ''
        return (
          <Tile key={g} label={`${KPI_GLYPH[g]} RESIDUAL`} value={`${fmt(residual[g])}${mark}`}
            variant={variant}
            delta={[`from ${fmt(targets[g])}`, band].filter(Boolean).join(' · ')} />
        )
      })}
    </div>
  )
}
```

In the `HedgeStrategy` return, **replace** the whole `{targets && (<div className="hedge-strategy__targets">…)}` block with:

```tsx
      {targets && <KpiTiles targets={targets} proposal={proposal} bands={bands} />}
```

and **delete** the whole `{proposal?.residual && proposal.status !== 'infeasible' && (<div className="hedge-strategy__residual">…)}` block (tiles now carry the residual + in-band coloring).

- [ ] **Step 4: Add KPI CSS to `HedgeStrategy.css`** (replace the "exposure readout" and "residual" sections)

Delete the `.hedge-strategy__targets`, `.hedge-strategy__target`, `.hedge-strategy__target b`, `.hedge-strategy__residual`, and `.hedge-strategy__residual span.*` rules. Add:

```css
/* ── KPI strip: exposure → residual tiles ── */

.hedge-strategy__kpis {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--gap-2);
}

/* Greek glyphs are pre-cased; uppercase transform would corrupt ν → Ν. */
.hedge-strategy__kpis .wl-tile__label { text-transform: none; }

@media (max-width: 640px) {
  .hedge-strategy__kpis { grid-template-columns: 1fr; }
}
```

- [ ] **Step 5: Verify no other consumers of the removed classes**

Run: `grep -rn "hedge-strategy__targets\|hedge-strategy__target\b\|hedge-strategy__residual" frontend/src`
Expected: no matches.

- [ ] **Step 6: Run tests to verify they pass**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/HedgeStrategy.tsx frontend/src/routes/HedgeStrategy.css \
  frontend/src/routes/HedgeStrategy.test.tsx
git commit -m "feat(hedging-ui): exposure/residual KPI tiles replace text rows"
```

---

### Task 3: Legs panel restructure

**Files:**
- Modify: `frontend/src/routes/HedgeStrategy.tsx`
- Modify: `frontend/src/routes/HedgeStrategy.css`
- Test: `frontend/src/routes/HedgeStrategy.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
it('frames the legs in a panel with strategy + leg-count meta', () => {
  render(<HedgeStrategy {...baseProps} />)
  expect(screen.getByText('Hedge legs')).toBeInTheDocument()
  expect(screen.getByText('Δ-neutral · 1 leg')).toBeInTheDocument()
})
```

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx`
Expected: new test FAILS.

- [ ] **Step 2: Implement the panel**

Add import:

```tsx
import { Panel } from '../components/Panel'
```

Inside `HedgeStrategy`, add after the `addable` const:

```tsx
  const strategyLabel = STRATEGIES.find((s) => s.value === strategy)?.label ?? strategy
  const panelMeta = legs.length > 0
    ? `${strategyLabel} · ${legs.length} leg${legs.length === 1 ? '' : 's'}`
    : strategyLabel
```

Then **replace** these four sibling blocks of the return —
`{bands && <BandEditor …/>}`, the `hedge-strategy__solvebar` div, the `{legs.length > 0 && (<table …>)}` table, and the `{addable.length > 0 && (<div className="hedge-strategy__addleg">…)}` block — with one `Panel` (table markup unchanged, copied verbatim):

```tsx
      <Panel title="Hedge legs" meta={panelMeta} className="hedge-strategy__panel">
        {bands && <BandEditor bands={bands} onSave={onSaveBands} />}

        {legs.length > 0 && (
          <table className="hedge-strategy__legs">
            <thead>
              <tr>
                <th>Contract</th><th>Role</th>
                <th className="num">δcash/lot</th><th className="num">Qty</th>
                <th>Swap</th><th></th>
              </tr>
            </thead>
            <tbody>
              {legs.map((leg) => (
                <tr key={leg.key}>
                  <td>{leg.contract_code}</td>
                  <td>{leg.role}</td>
                  <td className="num">{fmt(leg.delta)}</td>
                  <td className="num">{leg.quantity}</td>
                  <td>
                    <select className="hedge-strategy__input" aria-label={`swap ${leg.contract_code}`} value=""
                      onChange={(e) => e.target.value && onSwapLeg(leg.key, Number(e.target.value))}>
                      <option value="">swap…</option>
                      {addable.map((c) => (
                        <option key={c.instrument_id} value={c.instrument_id}>{c.contract_code}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <Button variant="ghost" iconOnly aria-label={`remove ${leg.contract_code}`}
                      onClick={() => onRemoveLeg(leg.key)}>✕</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="hedge-strategy__panel-foot">
          {addable.length > 0 && (
            <select className="hedge-strategy__input" aria-label="add leg" value=""
              onChange={(e) => e.target.value && onAddLeg(Number(e.target.value))}>
              <option value="">+ add leg…</option>
              {addable.map((c) => (
                <option key={c.instrument_id} value={c.instrument_id}>{c.contract_code}</option>
              ))}
            </select>
          )}
          <Button variant="default" onClick={onSolve} disabled={loading || portfolioId == null}>Solve</Button>
        </div>
      </Panel>
```

- [ ] **Step 3: Update CSS**

In `HedgeStrategy.css`: replace the combined rule

```css
.hedge-strategy__solvebar,
.hedge-strategy__bookbar { display: flex; }
```

with the new panel + bookbar rules:

```css
/* ── legs panel ── */

.hedge-strategy__panel .wl-panel__body {
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
}

.hedge-strategy__panel-foot {
  display: flex;
  align-items: center;
  gap: var(--gap-2);
}

.hedge-strategy__legs { border: 0; } /* Panel already draws the frame */

.hedge-strategy__bookbar { display: flex; justify-content: flex-end; }
```

- [ ] **Step 4: Verify no stale class consumers**

Run: `grep -rn "hedge-strategy__solvebar\|hedge-strategy__addleg" frontend/src`
Expected: no matches.

- [ ] **Step 5: Run the whole presentational file**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.test.tsx`
Expected: PASS — the existing swap/remove/add/bands/solve tests query by label/role, which survived the move.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/HedgeStrategy.tsx frontend/src/routes/HedgeStrategy.css \
  frontend/src/routes/HedgeStrategy.test.tsx
git commit -m "feat(hedging-ui): wrap legs/bands/solve in a Panel with strategy meta"
```

---

### Task 4: Live plumbing — capture the booking outcome

**Files:**
- Modify: `frontend/src/routes/HedgeStrategy.live.tsx`
- Test: `frontend/src/routes/HedgeStrategy.live.test.tsx`

- [ ] **Step 1: Extend `mockApi` to support HTTP failures**

In `HedgeStrategy.live.test.tsx`, replace the `vi.spyOn(globalThis, 'fetch')…` body inside `mockApi` with:

```ts
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    spy(url, init)
    const key = Object.keys(responders).find((k) => url.includes(k))
    const body = key ? responders[key]() : {}
    if (body && typeof body === 'object' && '__httpError' in (body as Record<string, unknown>)) {
      const detail = String((body as Record<string, unknown>).__httpError)
      return { ok: false, status: 500, text: async () => detail } as Response
    }
    return { ok: true, status: 200, json: async () => body } as Response
  })
```

- [ ] **Step 2: Write failing live tests**

```tsx
it('shows a booking receipt banner after a successful book', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  expect(await screen.findByText(/Hedge booked to Desk Book/)).toBeInTheDocument()
  expect(screen.getByText(/IC2406 × -1 \(delta\) → position #214/)).toBeInTheDocument()
})

it('shows a persistent error banner when booking fails', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({ __httpError: 'Cannot synthesize option hedge leg' }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  expect(await screen.findByText(/Booking failed — nothing was booked/)).toBeInTheDocument()
  expect(screen.getByText(/Cannot synthesize option hedge leg/)).toBeInTheDocument()
})

it('clears the booking banner on a new solve', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  await screen.findByText(/Hedge booked to Desk Book/)
  fireEvent.click(screen.getByRole('button', { name: /^solve$/i }))
  await waitFor(() =>
    expect(screen.queryByText(/Hedge booked to Desk Book/)).not.toBeInTheDocument())
})

it('clears the booking banner when the underlying changes', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  const { rerender } = render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  await screen.findByText(/Hedge booked to Desk Book/)
  rerender(<HedgeStrategyLive {...baseProps} underlying="000852.SH" underlyingId={2} />)
  await waitFor(() =>
    expect(screen.queryByText(/Hedge booked to Desk Book/)).not.toBeInTheDocument())
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.live.test.tsx`
Expected: the 4 new tests FAIL ("unable to find … Hedge booked"); pre-existing tests pass.

- [ ] **Step 4: Implement in `HedgeStrategy.live.tsx`**

Add `HedgeBookResponse, HedgeBookingResult` to the type import. Add state below `runningRisk`:

```tsx
  const [bookingResult, setBookingResult] = useState<HedgeBookingResult | null>(null)

  // A booking receipt describes one portfolio+underlying — drop it when either changes.
  useEffect(() => { setBookingResult(null) }, [portfolioId, underlying])
```

In `solve`, clear the previous outcome — first line inside the `try`:

```tsx
      setBookingResult(null)
```

Replace `bookProposal` with:

```tsx
  const bookProposal = async (allowInfeasible: boolean) => {
    if (!proposal || !proposal.legs) return
    if (!allowInfeasible && proposal.status !== 'feasible') return
    setLoading(true)
    try {
      const res = await api<HedgeBookResponse>('/api/hedging/book', {
        method: 'POST',
        body: JSON.stringify({
          portfolio_id: portfolioId, underlying, risk_run_id: proposal.risk_run_id,
          strategy, spot: proposal.spot, legs: proposal.legs,
        }),
      })
      if (cancelled.current) return
      // Backend books non-zero legs in proposal order; position_ids align with that.
      const bookedLegs = proposal.legs.filter((l) => l.quantity !== 0)
      setBookingResult({
        kind: 'success',
        portfolioName:
          portfolios.find((p) => p.id === portfolioId)?.name ?? `portfolio ${portfolioId}`,
        riskRunDate: summary?.created_at ? summary.created_at.slice(0, 10) : null,
        legs: bookedLegs.map((l, i) => ({
          contractCode: l.contract_code, quantity: l.quantity, role: l.role,
          positionId: res.position_ids[i],
        })),
      })
    } catch (err) {
      if (!cancelled.current) {
        setBookingResult({
          kind: 'error',
          message: err instanceof Error ? err.message : String(err),
        })
      }
    } finally {
      if (!cancelled.current) setLoading(false)
    }
  }
```

Replace the Task-1 placeholder props on `<HedgeStrategy …/>` with:

```tsx
        bookingResult={bookingResult}
        onDismissBookingResult={() => setBookingResult(null)}
```

- [ ] **Step 5: Run tests to verify they pass**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.live.test.tsx`
Expected: PASS (all, including pre-existing).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/HedgeStrategy.live.tsx frontend/src/routes/HedgeStrategy.live.test.tsx
git commit -m "feat(hedging-ui): surface booking success/failure from the book call"
```

---

### Task 5: Thread "view in Positions" navigation

**Files:**
- Modify: `frontend/src/main.tsx` (the `<HedgingLive …/>` mount, ~line 207)
- Modify: `frontend/src/routes/Hedging.live.tsx`
- Modify: `frontend/src/routes/Hedging.tsx`
- Modify: `frontend/src/routes/HedgeStrategy.live.tsx`
- Test: `frontend/src/routes/HedgeStrategy.live.test.tsx`

- [ ] **Step 1: Write the failing live test**

```tsx
it('navigates to Positions from the booking banner', async () => {
  const onViewPositions = vi.fn()
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  render(<HedgeStrategyLive {...baseProps} onViewPositions={onViewPositions} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  fireEvent.click(await screen.findByRole('button', { name: /view in positions/i }))
  expect(onViewPositions).toHaveBeenCalled()
})
```

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.live.test.tsx`
Expected: new test FAILS (prop not accepted / link absent).

- [ ] **Step 2: Implement the four-hop threading**

`HedgeStrategy.live.tsx` — add to props type and destructuring, then forward:

```tsx
  onViewPositions?: () => void
```

```tsx
        onViewPositions={onViewPositions}
```

(on the `<HedgeStrategy …/>` render, next to `onDismissBookingResult`)

`Hedging.tsx` — import the route type, accept the page-level callback, derive the narrow one:

```tsx
import type { Route } from '../types';   // merge into the existing ../types import
```

Add to `type Props`:

```tsx
  onNavigate?: (route: Route) => void;
```

Destructure `onNavigate` alongside the other props, and extend the `<HedgeStrategyLive …/>` render:

```tsx
                  onViewPositions={onNavigate ? () => onNavigate('positions') : undefined}
```

`Hedging.live.tsx` — accept and pass through:

```tsx
import type { Route } from '../types';   // merge into the existing ../types import

type Props = {
  onPageContextChange?: PageContextReporter;
  onNavigate?: (route: Route) => void;
};
```

Destructure `onNavigate` in `HedgingLive({ onPageContextChange, onNavigate }: Props)` and add `onNavigate={onNavigate}` to the `<Hedging …/>` render.

`main.tsx` — wire the App's `setRoute`:

```tsx
        {route === 'hedging'   && <HedgingLive onPageContextChange={handlePageContextChange} onNavigate={setRoute} />}
```

- [ ] **Step 3: Run tests + typecheck**

Run (frontend/): `npx vitest run src/routes/HedgeStrategy.live.test.tsx && npx tsc -b`
Expected: PASS, no TS errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/main.tsx frontend/src/routes/Hedging.live.tsx frontend/src/routes/Hedging.tsx \
  frontend/src/routes/HedgeStrategy.live.tsx frontend/src/routes/HedgeStrategy.live.test.tsx
git commit -m "feat(hedging-ui): view-in-Positions navigation from the booking banner"
```

---

### Task 6: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Whole frontend suite**

Run (frontend/): `npx vitest run`
Expected: all green. If unrelated suites assert on Hedging-page DOM, fix queries (label/role-based queries should survive).

- [ ] **Step 2: Typecheck**

Run (frontend/): `npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Backend sanity (untouched, but the tab depends on it)**

Run (repo root): `python -m pytest tests/test_hedging_strategy_api.py tests/test_hedging_book.py -q`
Expected: pass.

- [ ] **Step 4: Visual smoke test**

Start the app (backend + `npm run dev` in frontend/), open the Hedging page → Hedge Strategy tab, and verify against the spec: KPI tiles flip on Solve, panel frames legs/bands/solve, Book shows the receipt banner, ✕ dismisses, link jumps to Positions.

- [ ] **Step 5: Commit any straggler fixes**

```bash
git status   # should be clean; commit fixes individually if Step 1 required any
```

# Market Data "Reload from positions" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One button on the Market Data page that registers every open position's underlying and fetches an AKShare snapshot per resolvable symbol, so market data and pricing parameters can build from AKShare for the whole book.

**Architecture:** Frontend-only. `MarketDataLive` gains an `onReloadFromPositions(useProxy)` handler that POSTs the existing `/api/underlyings/sync-from-positions`, then loops the returned underlyings POSTing the existing single-symbol `/api/market-data/profiles/akshare` (trailing one-year window), with per-symbol progress/skip/failure reporting, then reloads the page. `MarketData` renders the button in the Watchlist header next to Refresh All.

**Tech Stack:** React + vitest + testing-library; backend endpoints reused unchanged.

**Spec:** `docs/superpowers/specs/2026-06-04-market-data-reload-from-positions-design.md`

**Commands** (frontend tests run from `frontend/`): `npx vitest run src/routes/MarketData.live.test.tsx src/routes/MarketData.test.tsx` · typecheck `npx tsc --noEmit`

---

### Task 1: `onReloadFromPositions` handler in `MarketDataLive`

**Files:**
- Modify: `frontend/src/routes/MarketData.live.tsx`
- Test: `frontend/src/routes/MarketData.live.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append inside `describe('MarketDataLive', ...)` in
`frontend/src/routes/MarketData.live.test.tsx` (the file already defines
`profile` and `response`):

```tsx
  it('reloads underlyings from positions: syncs, fetches resolvable symbols, reports skips', async () => {
    const syncResult = {
      created: 1,
      existing: 1,
      underlyings: [
        { id: 1, symbol: '000905.SH', akshare_symbol: 'sh000905', akshare_asset_class: 'index' },
        { id: 2, symbol: 'CSI500', akshare_symbol: null, akshare_asset_class: null },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/underlyings/sync-from-positions' && init?.method === 'POST') {
        return response(syncResult);
      }
      if (url === '/api/market-data/profiles/akshare' && init?.method === 'POST') {
        return response(profile);
      }
      if (url === '/api/market-data/fx-rates') return response([]);
      return response([profile]);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<MarketDataLive />);
    await waitFor(() => expect(screen.getByText('MARKET DATA')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /reload underlyings from positions/i }));

    await waitFor(() => {
      const snapshotPosts = fetchMock.mock.calls.filter(([u, init]) => (
        String(u) === '/api/market-data/profiles/akshare' && (init as RequestInit | undefined)?.method === 'POST'
      ));
      expect(snapshotPosts).toHaveLength(1);
      const body = JSON.parse(String(snapshotPosts[0][1]?.body ?? '{}'));
      expect(body.symbol).toBe('000905.SH');
      expect(body.asset_class).toBe('index');
      expect(body.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(body.end_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
    await waitFor(() => {
      expect(screen.getByText(/skipped 1 unresolvable: CSI500/i)).toBeInTheDocument();
      expect(screen.getByText(/fetched 1 snapshot/i)).toBeInTheDocument();
    });
  });

  it('reload-from-positions reports per-symbol fetch failures without aborting', async () => {
    const syncResult = {
      created: 0,
      existing: 2,
      underlyings: [
        { id: 1, symbol: '000905.SH', akshare_symbol: 'sh000905', akshare_asset_class: 'index' },
        { id: 2, symbol: 'AU9999.SGE', akshare_symbol: 'au9999', akshare_asset_class: 'sge_spot' },
      ],
    };
    let snapshotCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/underlyings/sync-from-positions' && init?.method === 'POST') {
        return response(syncResult);
      }
      if (url === '/api/market-data/profiles/akshare' && init?.method === 'POST') {
        snapshotCalls += 1;
        if (snapshotCalls === 1) return response({ detail: 'akshare down' }, 502);
        return response(profile);
      }
      if (url === '/api/market-data/fx-rates') return response([]);
      return response([profile]);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<MarketDataLive />);
    await waitFor(() => expect(screen.getByText('MARKET DATA')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /reload underlyings from positions/i }));

    await waitFor(() => {
      expect(screen.getByText(/1 failed: 000905\.SH/i)).toBeInTheDocument();
      expect(screen.getByText(/fetched 1 snapshot/i)).toBeInTheDocument();
    });
    expect(snapshotCalls).toBe(2);
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/MarketData.live.test.tsx`
Expected: both new tests FAIL — `getByRole('button', { name: /reload underlyings from positions/i })` finds nothing.

- [ ] **Step 3: Implement the handler and pass it down**

In `frontend/src/routes/MarketData.live.tsx`:

a) Add the handler after `onRefreshAll` (before the `return`). It uses the
file's existing `api`, `setFetching`, `setFeedback`, `load`:

```tsx
  const onReloadFromPositions = async (useProxy: boolean) => {
    setFetching(true);
    setFeedback(null);
    const endDate = new Date().toISOString().slice(0, 10);
    const startDate = new Date(Date.now() - 365 * 86_400_000).toISOString().slice(0, 10);
    try {
      const sync = await api<{
        created: number;
        existing: number;
        underlyings: Array<{
          symbol: string;
          akshare_symbol: string | null;
          akshare_asset_class: string | null;
        }>;
      }>('/api/underlyings/sync-from-positions', { method: 'POST' });
      const rows = sync.underlyings ?? [];
      const resolvable = rows.filter((u) => u.akshare_symbol && u.akshare_asset_class);
      const skipped = rows
        .filter((u) => !u.akshare_symbol || !u.akshare_asset_class)
        .map((u) => u.symbol);
      let ok = 0;
      const failed: string[] = [];
      for (const u of resolvable) {
        setFeedback({
          tone: 'success',
          message: `Fetching ${ok + failed.length + 1} / ${resolvable.length}: ${u.symbol}…`,
        });
        try {
          await api('/api/market-data/profiles/akshare', {
            method: 'POST',
            body: JSON.stringify({
              symbol: u.symbol,
              asset_class: u.akshare_asset_class,
              start_date: startDate,
              end_date: endDate,
              adjust: 'qfq',
              name: `${u.symbol} AKShare snapshot`,
              use_proxy: useProxy,
            }),
          });
          ok++;
        } catch {
          failed.push(u.symbol);
        }
      }
      const parts = [
        `Synced ${sync.created} new underlying${sync.created === 1 ? '' : 's'} (${sync.existing} existing)`,
        `fetched ${ok} snapshot${ok === 1 ? '' : 's'}`,
      ];
      if (skipped.length) parts.push(`skipped ${skipped.length} unresolvable: ${skipped.join(', ')}`);
      if (failed.length) parts.push(`${failed.length} failed: ${failed.join(', ')}`);
      setFeedback({ tone: failed.length === 0 ? 'success' : 'error', message: parts.join(' · ') });
      await load();
    } catch (err) {
      setFeedback({
        tone: 'error',
        message: `Could not sync underlyings: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setFetching(false);
    }
  };
```

b) Pass it down in the render:

```tsx
      onRefreshAll={onRefreshAll}
      onReloadFromPositions={onReloadFromPositions}
```

c) In `frontend/src/routes/MarketData.tsx` (needed for the tests to pass, kept
minimal here; Task 2 adds its own tests):

- Props type, after `onRefreshAll?: (useProxy: boolean) => void;`:

```tsx
  onReloadFromPositions?: (useProxy: boolean) => void;
```

- Destructure it in the component parameters after `onRefreshAll,`:

```tsx
  onReloadFromPositions,
```

- Button in the Watchlist header, immediately after the `{onRefreshAll && (...)}`
  block (uses the file's existing `Button`; add `ListPlus` to the existing
  `lucide-react` import):

```tsx
              {onReloadFromPositions && (
                <Button
                  type="button"
                  iconOnly
                  variant="ghost"
                  onClick={() => onReloadFromPositions(useProxy)}
                  disabled={fetching}
                  aria-label="Reload underlyings from positions"
                  title="Reload underlyings from positions and fetch AKShare snapshots"
                >
                  <ListPlus size={14} aria-hidden="true" />
                </Button>
              )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/routes/MarketData.live.test.tsx`
Expected: all PASS (existing tests included).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/MarketData.live.tsx frontend/src/routes/MarketData.tsx frontend/src/routes/MarketData.live.test.tsx
git commit -m "feat(market-data): reload underlyings from positions + AKShare fetch loop"
```

---

### Task 2: Presentational button behavior tests

**Files:**
- Test: `frontend/src/routes/MarketData.test.tsx`

(The button itself was added in Task 1 step 3c; this task pins its contract
from the presentational side.)

- [ ] **Step 1: Write the failing-or-passing tests and verify**

Append inside `describe('MarketData', ...)` in
`frontend/src/routes/MarketData.test.tsx` (uses the file's existing
`defaultProps`):

```tsx
  it('calls onReloadFromPositions with the proxy toggle state', async () => {
    const onReloadFromPositions = vi.fn();
    render(<MarketData {...defaultProps} onReloadFromPositions={onReloadFromPositions} />);
    await userEvent.click(
      screen.getByRole('button', { name: /reload underlyings from positions/i }),
    );
    expect(onReloadFromPositions).toHaveBeenCalledTimes(1);
    expect(typeof onReloadFromPositions.mock.calls[0][0]).toBe('boolean');
  });

  it('disables the reload-from-positions button while fetching', () => {
    render(
      <MarketData {...defaultProps} fetching onReloadFromPositions={vi.fn()} />,
    );
    expect(
      screen.getByRole('button', { name: /reload underlyings from positions/i }),
    ).toBeDisabled();
  });

  it('hides the reload-from-positions button when the handler is absent', () => {
    render(<MarketData {...defaultProps} />);
    expect(
      screen.queryByRole('button', { name: /reload underlyings from positions/i }),
    ).not.toBeInTheDocument();
  });
```

Run (from `frontend/`): `npx vitest run src/routes/MarketData.test.tsx`
Expected: all PASS (the implementation exists from Task 1). If any fail, fix
the implementation — not the test — and re-run.

Note: `localStorage['market-data-use-proxy']` seeds the proxy toggle; the
boolean-type assertion keeps the test independent of jsdom localStorage state.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/routes/MarketData.test.tsx
git commit -m "test(market-data): pin reload-from-positions button contract"
```

---

### Task 3: Full verification + live smoke

- [ ] **Step 1: Frontend suite + typecheck**

Run (from `frontend/`): `npx vitest run && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 2: Live smoke (vite hot-reloads; backend already running)**

In the browser: Market Data page → Watchlist header shows the new button →
click it → progress feedback cycles through book symbols → summary names
created/existing counts and any unresolvable symbols (expect e.g. `CSI500`
from portfolio X's hedge leg) → profile list now contains book underlyings.
Note: real AKShare fetches run; failures for genuinely unfetchable symbols
belong in the summary, not as a crash.

- [ ] **Step 3: Confirm clean tree**

```bash
git status --short   # only expected files; commit anything intentional left over
```

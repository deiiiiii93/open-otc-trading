# Frontend Explicit Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SPA's tab-state routing with explicit, bookmarkable URL routes for every internal page, without adding a router library.

**Architecture:** A pure bidirectional `Route ↔ path` map and helpers in `frontend/src/lib/routing.ts`, a `useRoute()` hook in `frontend/src/hooks/useRoute.ts` that owns route state and syncs it with the History API (pushState on user nav, popstate listener, mount canonicalization that preserves the query string), and a rewire of `frontend/src/main.tsx` to consume them. `?portfolio=<id>` is supported on `/risk` and `/hedging` only. The big render switch and all per-page prop threading stay unchanged.

**Tech Stack:** React 18 + TypeScript, Vite, Vitest (jsdom) + @testing-library/react. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-14-frontend-explicit-routing-design.md` (Codex-reviewed; 3 MUST-FIX findings folded in).

---

## File Structure

- **Create** `frontend/src/lib/routing.ts` — `NavRoute` type, `ROUTE_PATHS` map, `DEFAULT_ROUTE`, `routeToPath`, `pathToRoute`, `parsePortfolioParam`. Pure, no React.
- **Create** `frontend/src/lib/routing.test.ts` — unit tests for the above.
- **Create** `frontend/src/hooks/useRoute.ts` — route state + History API sync.
- **Create** `frontend/src/hooks/useRoute.test.tsx` — hook tests (jsdom history).
- **Modify** `frontend/src/main.tsx` — consume `useRoute`, replace nav `setRoute`→`navigate`, remove `initialRoute()` + `/client/rfq` effect, seed + sync `?portfolio=`.

All run from the `frontend/` directory. Test command: `npm test` (alias for `vitest run`); a single file: `npx vitest run src/lib/routing.test.ts`.

---

## Task 1: Pure routing map & helpers

**Files:**
- Create: `frontend/src/lib/routing.ts`
- Test: `frontend/src/lib/routing.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/routing.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  ROUTE_PATHS,
  DEFAULT_ROUTE,
  routeToPath,
  pathToRoute,
  parsePortfolioParam,
  type NavRoute,
} from './routing';

describe('ROUTE_PATHS', () => {
  it('has a unique path for every nav route', () => {
    const paths = Object.values(ROUTE_PATHS);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('covers exactly the 19 navigable routes', () => {
    expect(Object.keys(ROUTE_PATHS).length).toBe(19);
  });
});

describe('routeToPath', () => {
  it('maps a nav route to its path', () => {
    expect(routeToPath('risk')).toBe('/risk');
    expect(routeToPath('chat')).toBe('/agent-desk');
    expect(routeToPath('rfq')).toBe('/rfqs/approval');
    expect(routeToPath('client')).toBe('/client/rfq');
  });

  it('falls back to the default route path for a non-nav identifier', () => {
    // 'client-rfq' is a PageContext id, not a nav target.
    expect(routeToPath('client-rfq')).toBe(ROUTE_PATHS[DEFAULT_ROUTE]);
  });
});

describe('pathToRoute', () => {
  it('round-trips every nav route', () => {
    (Object.keys(ROUTE_PATHS) as NavRoute[]).forEach((route) => {
      expect(pathToRoute(routeToPath(route))).toBe(route);
    });
  });

  it('resolves / and unknown paths to the default route', () => {
    expect(pathToRoute('/')).toBe(DEFAULT_ROUTE);
    expect(pathToRoute('/nope')).toBe(DEFAULT_ROUTE);
  });

  it('matches /client/rfq to client (longest-prefix, trailing slash tolerant)', () => {
    expect(pathToRoute('/client/rfq')).toBe('client');
    expect(pathToRoute('/client/rfq/')).toBe('client');
  });

  it('ignores nested subpaths under a route', () => {
    expect(pathToRoute('/risk/anything')).toBe('risk');
  });
});

describe('parsePortfolioParam', () => {
  it('parses a numeric portfolio param', () => {
    expect(parsePortfolioParam('?portfolio=7')).toBe(7);
  });

  it('returns null for absent or non-numeric values', () => {
    expect(parsePortfolioParam('')).toBeNull();
    expect(parsePortfolioParam('?portfolio=')).toBeNull();
    expect(parsePortfolioParam('?portfolio=7x')).toBeNull();
    expect(parsePortfolioParam('?other=3')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/routing.test.ts`
Expected: FAIL — cannot resolve `./routing` (module does not exist).

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/routing.ts`:

```ts
import type { Route } from '../types';

// Navigable routes only. 'client-rfq' is a PageContext identifier, never a nav
// target, so it is intentionally excluded from the path map.
export type NavRoute = Exclude<Route, 'client-rfq'>;

// User-facing page concepts — NOT backend API endpoint names.
export const ROUTE_PATHS: Record<NavRoute, string> = {
  positions: '/positions',
  booking: '/booking',
  'pricing-parameters': '/pricing-parameters',
  'engine-configs': '/engine-configs',
  portfolios: '/portfolios',
  instruments: '/instruments',
  hedging: '/hedging',
  risk: '/risk',
  'greeks-landscape': '/greeks-landscape',
  'scenario-test': '/scenario-test',
  backtest: '/backtest',
  tasks: '/tasks',
  reports: '/reports',
  skills: '/skills',
  tracing: '/tracing',
  chat: '/agent-desk',
  rfq: '/rfqs/approval',
  'try-solve': '/try-solve',
  client: '/client/rfq',
};

export const DEFAULT_ROUTE: NavRoute = 'positions';

// Accepts the FULL Route union so callers typed against the existing
// `(route: Route) => void` props type-check. Non-nav ids fall back to default.
export function routeToPath(route: Route): string {
  return ROUTE_PATHS[route as NavRoute] ?? ROUTE_PATHS[DEFAULT_ROUTE];
}

// Longest path first so '/client/rfq' wins over any shorter prefix.
const PATH_ENTRIES: ReadonlyArray<readonly [string, NavRoute]> = (
  Object.entries(ROUTE_PATHS) as Array<[NavRoute, string]>
)
  .map(([route, path]) => [path, route] as const)
  .sort((a, b) => b[0].length - a[0].length);

export function pathToRoute(pathname: string): NavRoute {
  const clean = pathname.replace(/\/+$/, '') || '/';
  for (const [path, route] of PATH_ENTRIES) {
    if (clean === path || clean.startsWith(path + '/')) return route;
  }
  return DEFAULT_ROUTE;
}

// Strict integer parse: rejects "7x", empty, and absent. Query-string only.
export function parsePortfolioParam(search: string): number | null {
  const value = new URLSearchParams(search).get('portfolio');
  if (value == null || value === '') return null;
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && String(n) === value ? n : null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/routing.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/routing.ts frontend/src/lib/routing.test.ts
git commit -m "feat(routing): pure Route<->path map and query helpers"
```

---

## Task 2: `useRoute` hook (History API sync)

**Files:**
- Create: `frontend/src/hooks/useRoute.ts`
- Test: `frontend/src/hooks/useRoute.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/useRoute.test.tsx`:

```tsx
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRoute } from './useRoute';

function setUrl(url: string) {
  window.history.replaceState(null, '', url);
}

describe('useRoute', () => {
  beforeEach(() => {
    setUrl('/positions');
  });

  it('derives the route from the initial pathname', () => {
    setUrl('/risk');
    const { result } = renderHook(() => useRoute());
    expect(result.current.route).toBe('risk');
  });

  it('canonicalizes / to /positions on mount (replace, no extra history entry)', () => {
    setUrl('/');
    const before = window.history.length;
    const { result } = renderHook(() => useRoute());
    expect(result.current.route).toBe('positions');
    expect(window.location.pathname).toBe('/positions');
    expect(window.history.length).toBe(before); // replaceState, not push
  });

  it('canonicalizes an unknown path to /positions on mount', () => {
    setUrl('/nope');
    renderHook(() => useRoute());
    expect(window.location.pathname).toBe('/positions');
  });

  it('preserves the query string during mount canonicalization', () => {
    // pathname is already canonical for /risk; query must survive untouched.
    setUrl('/risk?portfolio=7');
    const { result } = renderHook(() => useRoute());
    expect(result.current.route).toBe('risk');
    expect(window.location.search).toBe('?portfolio=7');
  });

  it('navigate() pushes the path and updates the route', () => {
    const { result } = renderHook(() => useRoute());
    act(() => result.current.navigate('chat'));
    expect(window.location.pathname).toBe('/agent-desk');
    expect(result.current.route).toBe('chat');
  });

  it('updates the route on popstate', () => {
    const { result } = renderHook(() => useRoute());
    act(() => {
      window.history.pushState(null, '', '/hedging');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.route).toBe('hedging');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/useRoute.test.tsx`
Expected: FAIL — cannot resolve `./useRoute`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/hooks/useRoute.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';
import type { Route } from '../types';
import { pathToRoute, routeToPath, type NavRoute } from '../lib/routing';

type UseRoute = {
  route: NavRoute;
  navigate: (route: Route) => void;
};

export function useRoute(): UseRoute {
  const [route, setRoute] = useState<NavRoute>(() => pathToRoute(window.location.pathname));

  // Mount canonicalization. Preserve location.search so a deep-linked
  // ?portfolio= is not wiped before main.tsx reads it.
  useEffect(() => {
    const canonical = routeToPath(route);
    if (window.location.pathname !== canonical) {
      window.history.replaceState(null, '', canonical + window.location.search);
    }
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onPop = () => setRoute(pathToRoute(window.location.pathname));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((next: Route) => {
    const path = routeToPath(next);
    // Keep the query only when re-navigating to the current path; moving to a
    // different page drops it (non-risk/hedging routes are query-less).
    const target = window.location.pathname === path ? path + window.location.search : path;
    if (window.location.pathname + window.location.search !== target) {
      window.history.pushState(null, '', target);
    }
    setRoute(pathToRoute(path));
  }, []);

  return { route, navigate };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/hooks/useRoute.test.tsx`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useRoute.ts frontend/src/hooks/useRoute.test.tsx
git commit -m "feat(routing): useRoute hook syncing route state with History API"
```

---

## Task 3: Wire `main.tsx` to explicit routing

This task has no new unit test of its own (routing logic is covered by Tasks 1–2; `main.tsx` is the untested composition root). It is verified by `tsc -b` (the MUST-FIX #1 assignability guarantee) and the full existing suite staying green. Make the edits, then run the full verification in Step 7.

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Add imports**

At the top of `frontend/src/main.tsx`, alongside the existing imports, add:

```ts
import { useRoute } from './hooks/useRoute';
import { routeToPath, pathToRoute, parsePortfolioParam } from './lib/routing';
```

- [ ] **Step 2: Replace `initialRoute()` usage and route state**

Delete the `initialRoute` function:

```ts
function initialRoute(): Route {
  return location.pathname.includes('/client/rfq') ? 'client' : 'positions';
}
```

Replace the route state declaration:

```ts
const [route, setRoute] = useState<Route>(() => initialRoute());
```

with:

```ts
const { route, navigate } = useRoute();
```

Replace the two remaining `initialRoute()` calls in the `pageContext` initializer:

```ts
const [pageContext, setPageContext] = useState<PageContext>(() => routeContext(initialRoute()));
```

with:

```ts
const [pageContext, setPageContext] = useState<PageContext>(
  () => routeContext(pathToRoute(window.location.pathname)),
);
```

- [ ] **Step 3: Remove the old `/client/rfq` URL-sync effect**

Delete this entire effect (it is superseded by `useRoute`):

```ts
useEffect(() => {
  const path = route === 'client' ? '/client/rfq' : '/';
  if (location.pathname !== path) history.replaceState(null, '', path);
}, [route]);
```

- [ ] **Step 4: Seed `sharedPortfolioId` from the query and keep it in sync**

Replace the declaration:

```ts
const [sharedPortfolioId, setSharedPortfolioId] = useState<number | null>(null);
```

with a query-seeded initializer:

```ts
const [sharedPortfolioId, setSharedPortfolioId] = useState<number | null>(
  () => parsePortfolioParam(window.location.search),
);
```

Add two effects next to the other route effects:

```ts
// Reflect the shared portfolio in the URL while on Risk/Hedging (replace, never
// push — a filter change must not create Back-history entries).
useEffect(() => {
  if (route !== 'risk' && route !== 'hedging') return;
  const base = routeToPath(route);
  const target = sharedPortfolioId != null ? `${base}?portfolio=${sharedPortfolioId}` : base;
  if (window.location.pathname + window.location.search !== target) {
    window.history.replaceState(null, '', target);
  }
}, [route, sharedPortfolioId]);

// Back/Forward must restore the portfolio selection the popped URL carried.
useEffect(() => {
  const onPop = () => setSharedPortfolioId(parsePortfolioParam(window.location.search));
  window.addEventListener('popstate', onPop);
  return () => window.removeEventListener('popstate', onPop);
}, []);
```

- [ ] **Step 5: Replace every navigation `setRoute(...)` with `navigate(...)`**

There are exactly these navigation call sites — change each:

1. `onSelectCommand`, portfolios-create branch:
   `setRoute('portfolios');` → `navigate('portfolios');`
2. `onSelectCommand`, jump branch:
   `setRoute(target);` → `navigate(target);`
3. `handleOpenTrace`:
   `setRoute('tracing');` → `navigate('tracing');`
4. `AppShell` prop:
   `onNavigate={setRoute}` → `onNavigate={navigate}`
5. `FloatingAgentMiniChat` `onOpenDesk`:
   `onOpenDesk={() => { setAgentOpen(false); setRoute('chat'); }}`
   → `onOpenDesk={() => { setAgentOpen(false); navigate('chat'); }}`
6. `TasksLive` prop:
   `onNavigate={setRoute}` → `onNavigate={navigate}`
7. `HedgingLive` prop:
   `onNavigate={setRoute}` → `onNavigate={navigate}`

After this step there must be **zero** remaining `setRoute` references in `main.tsx`
(verify in Step 6). `navigate` accepts the full `Route` union, so `target` (typed `Route`
in `onSelectCommand`) and the `onNavigate: (route: Route) => void` props all type-check
unchanged — this is the MUST-FIX #1 resolution.

- [ ] **Step 6: Verify no stale references remain**

Run: `cd frontend && grep -n "setRoute\|initialRoute" src/main.tsx`
Expected: no output (empty). If anything prints, convert it per Step 5 / remove it.

- [ ] **Step 7: Typecheck and run the full suite**

Run: `cd frontend && npx tsc -b && npm test`
Expected: `tsc` exits 0 (MUST-FIX #1 assignability holds); Vitest reports all files passing,
including the new `routing.test.ts` / `useRoute.test.tsx` and the unchanged
`ClientRfq.test.tsx` (which still asserts `context.route === 'client-rfq'` — page-context
identifiers were untouched).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat(routing): wire main.tsx to explicit URL routes; drop /client/rfq special case"
```

---

## Task 4: Manual smoke verification

**Files:** none (verification only).

- [ ] **Step 1: Build to confirm production bundle is clean**

Run: `cd frontend && npm run build`
Expected: `tsc -b && vite build` completes with no type or build errors.

- [ ] **Step 2: Manual checklist (dev server)**

Run: `cd frontend && npm run dev`, open `http://localhost:5173`, and confirm:
- `/` redirects to `/positions` in the address bar.
- Clicking each sidebar item changes the URL to its mapped path (e.g. RFQ Approval → `/rfqs/approval`, Agent Desk → `/agent-desk`, Client RFQ → `/client/rfq`).
- Refresh on any page (e.g. `/hedging`) re-renders that page, not Positions.
- Browser Back/Forward moves between visited pages.
- On Risk, selecting a portfolio adds `?portfolio=<id>`; refreshing keeps the selection; Back/Forward across portfolio changes restores the selection.
- The floating agent is hidden on `/client/rfq` and visible elsewhere.

- [ ] **Step 3: No commit** (verification only). Record results in the PR description.

---

## Self-Review

- **Spec coverage:** Goals 1–7 map to tasks — explicit paths (Task 1 `ROUTE_PATHS`), `/`→`/positions` (Task 2 canonicalization), History-based nav (Task 2 `navigate` + Task 3 Step 5), Back/Forward (Task 2 popstate), `?portfolio=` (Task 3 Step 4), `/client/rfq` special case removed (Task 3 Step 3), refresh/deep-link (Task 2 initial state + Task 4 smoke). MUST-FIX #1/#2/#3 covered in Tasks 1–3. SPA hosting (spec §5) is a no-op for Vite — confirmed by Task 4 Step 1 build.
- **Placeholder scan:** none — every code step shows complete code.
- **Type consistency:** `NavRoute`, `ROUTE_PATHS`, `routeToPath(route: Route)`, `pathToRoute`, `parsePortfolioParam(search)` names are identical across Tasks 1–3; `useRoute` returns `{ route: NavRoute; navigate: (route: Route) => void }` consistently.

# Frontend Explicit Routing — Design

**Date:** 2026-06-14
**Status:** Revised after Codex review (Codex stood in for the user as reviewer; see
"Codex review incorporated" below). Three MUST-FIX correctness findings folded in.
**Scope:** `frontend/` only. No backend changes.

## Problem

Routing in the SPA is implemented as tab state, not URLs:

- `frontend/src/main.tsx` holds `const [route, setRoute] = useState<Route>()`. Each page
  renders via a conditional switch (`{route === 'positions' && <PositionsLive/>}`).
- The URL stays `/` for every page **except** one hand-wired special case:
  `initialRoute()` string-matches `/client/rfq`, and a `useEffect` does
  `history.replaceState` mapping `client` → `/client/rfq` and **every other page → `/`**.
- Navigation is buttons calling `setRoute`; browser Back/Forward does nothing.

Consequences: pages cannot be bookmarked/shared; refresh always dumps you back to
Positions; automated browser tests and support can't deep-link; agent
responses/notifications can't link to a page; Client RFQ is a maintenance snowflake.

## Goals

1. Every internal page has its own explicit URL path — **page concepts, not backend API
   endpoint names**.
2. `/` resolves to `/positions` (canonical URL shown in the bar).
3. Sidebar and command-palette ("Jump To") actions navigate via History API so the URL
   changes and a history entry is pushed.
4. Browser Back/Forward updates the rendered page (`popstate`).
5. One genuinely-useful shareable query param: `?portfolio=<id>` on `/risk` and `/hedging`.
6. The `/client/rfq` special-case logic disappears, replaced by the same general mechanism.
7. Refresh / direct navigation to any route path renders that page.

## Non-Goals (YAGNI)

- **No routing library.** React Router is deferred until nested routes, route params, or
  guarded pages actually appear. See "Approach" for why hand-rolled wins here.
- **No auth boundary / separate bundle for the client route.** The app has no auth today
  (single-operator internal tool). The client page keeps its current behavior (agent hidden)
  tied to its route. A real client/internal split is explicitly out of scope.
- **No query params beyond `?portfolio=`.** Other per-page filter state stays in component
  state until a concrete sharing need appears.
- **No change to `PageContext.route` identifiers.** Those feed the agent and are pinned by
  tests (e.g. `ClientRfq` reports `'client-rfq'`). The migration touches *navigation*
  routing, not page-context reporting.

## Approach: hand-rolled path↔route map (not React Router)

The app renders through a **single conditional switch** in `main.tsx`, with substantial
per-page prop threading (`onPageContextChange`, `portfolioId`/`onPortfolioIdChange`,
`onNavigate`, `controller`, `accountingDate`, `onOpenTrace`, …). React Router's `<Routes>`
would fragment that switch and force this shared state into context or per-route wrappers —
a large, risky refactor for no current benefit.

A small **bidirectional `Route ↔ path` map** plus a `useRoute()` hook preserves the entire
existing render switch and prop threading. Blast radius is confined to `main.tsx` plus one
new module. This matches the user's stated lean ("a small route-path mapping to begin with")
and YAGNI. The spec records React Router as the future upgrade path once nesting/params/guards
arrive.

Considered and rejected:
- **React Router DOM now** — correct long-term once nesting appears, but a disproportionate
  refactor of the prop-threaded switch today. Deferred, not dismissed.
- **Keep root-only + add more `/client/rfq`-style special cases** — explicitly the design
  we are migrating *away* from. Rejected.

## Design

### 1. New module: `frontend/src/lib/routing.ts`

Single source of truth for the path mapping and conversions. Pure functions, no React —
independently unit-testable.

```ts
import type { Route } from '../types';

// Navigable routes only (the 19 entries in navItems). Page-context-only identifiers
// like 'client-rfq' are NOT navigation routes and are intentionally absent.
export const ROUTE_PATHS: Record<NavRoute, string> = {
  positions:           '/positions',
  booking:             '/booking',
  'pricing-parameters':'/pricing-parameters',
  'engine-configs':    '/engine-configs',
  portfolios:          '/portfolios',
  instruments:         '/instruments',
  hedging:             '/hedging',
  risk:                '/risk',
  'greeks-landscape':  '/greeks-landscape',
  'scenario-test':     '/scenario-test',
  backtest:            '/backtest',
  tasks:               '/tasks',
  reports:             '/reports',
  skills:              '/skills',
  tracing:             '/tracing',
  chat:                '/agent-desk',
  rfq:                 '/rfqs/approval',
  'try-solve':         '/try-solve',
  client:              '/client/rfq',
};

export const DEFAULT_ROUTE: NavRoute = 'positions';

// 'client-rfq' is a PageContext identifier, never a nav target — exclude it.
export type NavRoute = Exclude<Route, 'client-rfq'>;

// Accepts the FULL Route union (not just NavRoute) so callers typed against the
// existing `(route: Route) => void` props type-check (see MUST-FIX #1). A non-nav
// identifier like 'client-rfq' has no path; fall back to the default route's path.
export function routeToPath(route: Route): string {
  return ROUTE_PATHS[route as NavRoute] ?? ROUTE_PATHS[DEFAULT_ROUTE];
}

// Longest-prefix match so '/client/rfq' wins over a hypothetical '/client'.
// Unknown path -> DEFAULT_ROUTE. Ignores query string and hash.
export function pathToRoute(pathname: string): NavRoute { /* reverse lookup */ }
```

Type safety: `ROUTE_PATHS` is keyed by `NavRoute`, so adding a new nav route without a path
(or vice versa) is a compile error. This is the structural guard the user asked for —
the map is central and exhaustive.

### 2. New hook: `frontend/src/hooks/useRoute.ts`

Owns the route state and its synchronization with the URL.

```ts
export function useRoute(): {
  route: NavRoute;
  // MUST-FIX #1: typed against the FULL Route union so it is assignable to the
  // existing `onNavigate: (route: Route) => void` props on AppShell/Sidebar/Tasks/
  // Hedging WITHOUT changing those component signatures. A non-nav route is
  // coerced to DEFAULT_ROUTE by routeToPath (never actually passed in practice).
  navigate: (route: Route) => void;          // user-initiated: pushState + setRoute
} {
  const [route, setRoute] = useState<NavRoute>(() => pathToRoute(location.pathname));

  // Canonicalize on mount: '/' or unknown -> replaceState to the resolved path.
  // MUST-FIX #2: PRESERVE location.search. A naive replaceState to pathname-only
  // would wipe an incoming deep link like /risk?portfolio=7 before the query is
  // ever read. Compare pathname only; keep the existing search untouched here and
  // let the ?portfolio= effect (section 4) own the query thereafter.
  useEffect(() => {
    const canonical = routeToPath(route);
    if (location.pathname !== canonical) {
      history.replaceState(null, '', canonical + location.search);
    }
  }, []); // mount only

  // Back/Forward. MUST-FIX #3: popstate must restore BOTH the page and any
  // ?portfolio= the popped entry carried. This hook restores the page; the
  // ?portfolio= effect in main.tsx re-seeds sharedPortfolioId from location.search
  // on the same popstate (it also listens) so Back/Forward between
  // /risk?portfolio=7 and /risk?portfolio=9 updates the selection.
  useEffect(() => {
    const onPop = () => setRoute(pathToRoute(location.pathname));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((next: Route) => {
    const path = routeToPath(next);
    // Preserve search only when staying on the same path (e.g. re-nav to current);
    // navigating to a different page drops the query (other routes are query-less).
    const target = location.pathname === path ? path + location.search : path;
    if (location.pathname + location.search !== target) {
      history.pushState(null, '', target);
    }
    setRoute(pathToRoute(path));
  }, []);

  return { route, navigate };
}
```

**History semantics (decision):**
- User navigation (sidebar, palette, programmatic `navigate('chat')` etc.) → `pushState`
  (Back/Forward works — a core goal).
- Mount canonicalization (`/` → `/positions`, unknown → `/positions`) → `replaceState`
  (no spurious history entry).
- `?portfolio=` changes → `replaceState` (filter tweaks must not spam Back history).

### 3. `main.tsx` wiring

- Replace `useState<Route>` + `initialRoute()` + the `/client/rfq` `useEffect` with
  `const { route, navigate } = useRoute()`.
- Replace **navigation** `setRoute(...)` calls with `navigate(...)`:
  - `<AppShell onNavigate={navigate} />` (sidebar)
  - `onSelectCommand` (palette "Jump To" + portfolios-create)
  - `handleOpenTrace` → `navigate('tracing')`
  - `FloatingAgent onOpenDesk` → `navigate('chat')`
  - `Tasks onNavigate={navigate}`, `Hedging onNavigate={navigate}`
- The big render switch is **unchanged** (still `{route === 'x' && <X/>}`).
- `routeContext()` / `handlePageContextChange` keep reporting `location.pathname` — now it
  carries the real path, a free improvement for the agent. Page-context route identifiers
  (incl. `'client-rfq'`) are untouched.
- `showAgent = route !== 'client'` is unchanged (client page still hides the agent).

### 4. `?portfolio=<id>` on `/risk` and `/hedging`

`sharedPortfolioId` already lives in `main.tsx` and is threaded into Risk and Hedging. This
promotes it to a shareable URL param, scoped to those two routes.

- **Seed at mount (synchronously)** — `sharedPortfolioId`'s `useState` initializer reads
  `?portfolio` from `location.search` directly: `useState<number|null>(() =>
  parsePortfolioParam(location.search))`. Reading in the initializer (not an effect)
  guarantees the deep-linked value is captured *before* `useRoute`'s mount canonicalization
  runs — and that canonicalization now preserves `location.search` anyway (MUST-FIX #2), so
  the value survives either way. Parse as int; ignore non-numeric/absent → null.
- **When** `sharedPortfolioId` changes while on `/risk` or `/hedging`: `replaceState` the
  query string (`?portfolio=7`, or strip it when null). Never `pushState` (filter change).
- **Leaving** those routes: drop the query param (the canonical path for other routes has
  no query string). Handled by `navigate` (different path → query dropped) plus the query
  effect which only writes a query while on risk/hedging.
- **On popstate** (MUST-FIX #3): re-seed `sharedPortfolioId` from the popped URL's
  `location.search` so Back/Forward across `/risk?portfolio=7` ↔ `/risk?portfolio=9` (and
  risk↔other-page) updates the selection. A `popstate` listener in `main.tsx` calls
  `setSharedPortfolioId(parsePortfolioParam(location.search))`.
- Implemented in `main.tsx`: the synchronous seed initializer, one effect keyed on
  `(route, sharedPortfolioId)` that `replaceState`s the query suffix, and one `popstate`
  listener. The `useRoute` mount canonicalization preserves `location.search`, so it never
  fights the query effect. `parsePortfolioParam` lives in `lib/routing.ts` (pure, tested).

### 5. Production / hosting

The frontend is standalone Vite. `npm run dev` (dev server) and `vite preview` both serve
`index.html` for unknown paths by default (Vite SPA history fallback). No backend static
mount exists. **No hosting change required**; this is called out so a future non-Vite host
(nginx/S3) knows it needs an `index.html` fallback. Documented in the spec; nothing to build.

## Data flow

```
URL (address bar)
  └─ load/refresh ─► useRoute(pathToRoute) ─► route state ─► render switch ─► page
  └─ Back/Forward ─► popstate ─► setRoute(pathToRoute) ─► render switch
sidebar / palette / programmatic ─► navigate(route) ─► pushState + setRoute ─► render + URL
Risk/Hedging portfolio pick ─► sharedPortfolioId ─► replaceState(?portfolio=) ─► URL only
```

## Testing strategy (TDD)

1. **`routing.test.ts`** (pure map): round-trip `routeToPath(pathToRoute(p)) === canonical`
   for every nav route; unknown path → `/positions`; `/` → `positions`; longest-prefix
   (`/client/rfq` → `client`, not a `/client` mismatch); `ROUTE_PATHS` covers exactly the
   nav routes (exhaustiveness).
2. **`useRoute.test.tsx`** (jsdom history API): mount on `/risk` yields `route==='risk'`;
   `navigate('chat')` pushes `/agent-desk` and updates route; `popstate` after a manual
   `history.back()`-style state change updates route; mount on `/` replaceState→`/positions`
   with no extra history entry; unknown path canonicalizes to `/positions`.
3. **`?portfolio=` behaviour**: `parsePortfolioParam('?portfolio=7')===7`, ignores
   non-numeric/absent → null; entering `/risk?portfolio=7` seeds `sharedPortfolioId=7` AND
   the value survives mount canonicalization (deep-link not wiped — MUST-FIX #2); changing it
   replaceStates the query; leaving strips it; popstate back to `/risk?portfolio=9` re-seeds
   to 9 (MUST-FIX #3). Tested at the hook/helper level plus a focused `main` integration check.
4. **navigate assignability** (MUST-FIX #1): a compile-level guarantee — `tsc -b` must pass
   with `navigate` passed to every `onNavigate`/`onOpenDesk` consumer. The build is the test.
5. **Regression**: full `npm test` + `tsc -b` (typecheck) must stay green. Existing
   `ClientRfq.test.tsx` assertion `context.route === 'client-rfq'` must remain valid
   (page-context identifiers untouched).

## Risks & mitigations

- **History push/replace interplay with the query effect** — the subtlest area. Mitigated by:
  pathname-only canonicalization, query effect uses `replaceState` only, explicit tests for
  the entering/changing/leaving transitions.
- **A route added later without a path** — compile error via the `Record<NavRoute, string>`
  exhaustiveness, plus the exhaustiveness unit test.
- **jsdom popstate**: jsdom supports `history.pushState`/`replaceState` and dispatching
  `popstate`; tests dispatch the event manually rather than relying on real Back/Forward.

## Codex review incorporated

Codex reviewed this spec standing in for the user. Its run captured the analysis but the
final write/notify step hung (tooling flake in the background runner), so the three captured
MUST-FIX findings were folded in directly:

1. **Prop-type variance** — `navigate` typed `(route: NavRoute) => void` is **not** assignable
   to the existing `onNavigate: (route: Route) => void` props (`AppShell`, `Sidebar`, `Tasks`,
   `Hedging`). Fix: type `navigate` and `routeToPath` against the full `Route` union, coercing
   non-nav identifiers to `DEFAULT_ROUTE`. Zero change to component prop signatures.
2. **Mount canonicalization wiped the query** — replacing to a pathname-only canonical URL
   would erase an incoming `/risk?portfolio=7` deep link before it was read. Fix: preserve
   `location.search` in the mount `replaceState`, and seed `sharedPortfolioId` synchronously
   in its `useState` initializer.
3. **popstate dropped `?portfolio=`** — Back/Forward restored the page but not the portfolio
   selection. Fix: a `popstate` listener in `main.tsx` re-seeds `sharedPortfolioId` from
   `location.search`.

SHOULD-CONSIDER ("is no-router defensible?"): yes — confirmed by the analysis; the single
prop-threaded switch makes React Router a disproportionate refactor with no current nesting/
guard need. Deferred, documented. The plan and final implementation get fresh Codex review
gates with durable capture.

## Files

- **New:** `frontend/src/lib/routing.ts`, `frontend/src/lib/routing.test.ts`,
  `frontend/src/hooks/useRoute.ts`, `frontend/src/hooks/useRoute.test.tsx`.
  `lib/routing.ts` also exports `parsePortfolioParam(search: string): number | null`.
- **Modified:** `frontend/src/main.tsx` (wire `useRoute`, replace nav `setRoute`→`navigate`,
  remove `initialRoute`/`/client/rfq` effect, seed `sharedPortfolioId` from query in its
  `useState` initializer, add the `?portfolio=` `replaceState` effect + a `popstate`
  re-seed listener).
- **Unchanged:** all 19 page components, `AppShell`, `Sidebar`, `types.ts` `Route` union,
  every `PageContext` reporter.

import type { Route } from '../types';

// Navigable routes only. 'client-rfq' is a PageContext identifier, never a nav
// target, so it is intentionally excluded from the path map.
export type NavRoute = Exclude<Route, 'client-rfq'>;

// User-facing page concepts — NOT backend API endpoint names.
export const ROUTE_PATHS: Record<NavRoute, string> = {
  memory: '/memory',
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
  arena: '/arena',
  workflows: '/workflows',
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

// `?portfolio=` is meaningful ONLY on Risk and Hedging — the only pages that
// consume a shared portfolio selection.
function usesPortfolioParam(route: NavRoute): boolean {
  return route === 'risk' || route === 'hedging';
}

// Canonical URL for a route + shared portfolio. The query is attached only on
// portfolio-aware routes, so navigating elsewhere drops it (no cross-route leak).
export function routeUrl(route: NavRoute, portfolioId: number | null): string {
  const base = ROUTE_PATHS[route];
  return usesPortfolioParam(route) && portfolioId != null
    ? `${base}?portfolio=${portfolioId}`
    : base;
}

// The shared portfolio implied by a location — null on routes that don't use it,
// so a deep link like /positions?portfolio=7 never seeds the shared selection.
export function portfolioFromLocation(pathname: string, search: string): number | null {
  return usesPortfolioParam(pathToRoute(pathname)) ? parsePortfolioParam(search) : null;
}

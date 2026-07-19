import type { Route } from '../types';

// Navigable routes only. 'client-rfq' is a PageContext identifier, never a nav
// target, so it is intentionally excluded from the path map.
export type NavRoute = Exclude<Route, 'client-rfq'>;

// User-facing page concepts — NOT backend API endpoint names.
export const ROUTE_PATHS: Record<NavRoute, string> = {
  memory: '/memory',
  'model-maintenance': '/model-maintenance',
  audit: '/audit',
  positions: '/positions',
  booking: '/booking',
  'pricing-parameters': '/pricing-parameters',
  'engine-configs': '/engine-configs',
  portfolios: '/portfolios',
  instruments: '/instruments',
  hedging: '/hedging',
  risk: '/risk',
  limits: '/limits',
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

// `?portfolio=` is meaningful only on pages that consume the session-shared
// portfolio selection.
function usesPortfolioParam(route: NavRoute): boolean {
  return route === 'risk' || route === 'hedging' || route === 'limits';
}

export const LIMIT_DEEP_LINK_PARAMS = [
  'tab',
  'run',
  'evaluation',
  'incident',
  'limit',
  'schedule',
] as const;

// Canonical URL for a route + shared portfolio. Limits and Audit additionally
// retain only their explicit deep-link allowlists; all other route-scoped
// query data is dropped so navigation cannot leak stale filters between pages.
export function routeUrl(
  route: NavRoute,
  portfolioId: number | null,
  currentSearch = '',
): string {
  const base = ROUTE_PATHS[route];
  const search = new URLSearchParams();
  if (usesPortfolioParam(route) && portfolioId != null) {
    search.set('portfolio', String(portfolioId));
  }
  if (route === 'limits') {
    const current = new URLSearchParams(currentSearch);
    for (const key of LIMIT_DEEP_LINK_PARAMS) {
      const value = current.get(key);
      if (value != null && value !== '') search.set(key, value);
    }
  }
  if (route === 'audit') {
    const auditRef = new URLSearchParams(currentSearch).get('audit_ref');
    if (auditRef != null && auditRef !== '') search.set('audit_ref', auditRef);
  }
  const query = search.toString();
  return query ? `${base}?${query}` : base;
}

// The shared portfolio implied by a location — null on routes that don't use it,
// so a deep link like /positions?portfolio=7 never seeds the shared selection.
export function portfolioFromLocation(pathname: string, search: string): number | null {
  return usesPortfolioParam(pathToRoute(pathname)) ? parsePortfolioParam(search) : null;
}

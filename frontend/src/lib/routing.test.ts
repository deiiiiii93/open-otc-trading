import { describe, expect, it } from 'vitest';
import {
  ROUTE_PATHS,
  DEFAULT_ROUTE,
  routeToPath,
  pathToRoute,
  parsePortfolioParam,
  routeUrl,
  portfolioFromLocation,
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

describe('routeUrl', () => {
  it('appends ?portfolio= only on risk/hedging', () => {
    expect(routeUrl('risk', 7)).toBe('/risk?portfolio=7');
    expect(routeUrl('hedging', 7)).toBe('/hedging?portfolio=7');
  });

  it('omits the query when portfolio is null', () => {
    expect(routeUrl('risk', null)).toBe('/risk');
  });

  it('never attaches a query to non-portfolio routes', () => {
    expect(routeUrl('positions', 7)).toBe('/positions');
    expect(routeUrl('chat', 7)).toBe('/agent-desk');
  });
});

describe('portfolioFromLocation', () => {
  it('reads the portfolio only on risk/hedging routes', () => {
    expect(portfolioFromLocation('/risk', '?portfolio=7')).toBe(7);
    expect(portfolioFromLocation('/hedging', '?portfolio=9')).toBe(9);
  });

  it('ignores the portfolio param on other routes (no cross-route leak)', () => {
    expect(portfolioFromLocation('/positions', '?portfolio=7')).toBeNull();
    expect(portfolioFromLocation('/', '?portfolio=7')).toBeNull();
  });
});

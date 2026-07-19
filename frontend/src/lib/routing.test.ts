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

  it('covers exactly the 25 navigable routes', () => {
    expect(Object.keys(ROUTE_PATHS).length).toBe(25);
  });

  it('includes the memory route', () => {
    expect(ROUTE_PATHS.memory).toBe('/memory');
  });

  it('maps the model-maintenance route round-trip', () => {
    expect(routeToPath('model-maintenance')).toBe('/model-maintenance');
    expect(pathToRoute('/model-maintenance')).toBe('model-maintenance');
  });

  it('maps the limits route round-trip', () => {
    expect(routeToPath('limits')).toBe('/limits');
    expect(pathToRoute('/limits')).toBe('limits');
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
  it('appends ?portfolio= only on portfolio-aware routes', () => {
    expect(routeUrl('risk', 7)).toBe('/risk?portfolio=7');
    expect(routeUrl('hedging', 7)).toBe('/hedging?portfolio=7');
    expect(routeUrl('limits', 7)).toBe('/limits?portfolio=7');
  });

  it('omits the query when portfolio is null', () => {
    expect(routeUrl('risk', null)).toBe('/risk');
  });

  it('never attaches a query to non-portfolio routes', () => {
    expect(routeUrl('positions', 7)).toBe('/positions');
    expect(routeUrl('chat', 7)).toBe('/agent-desk');
  });

  it('preserves only allowlisted Limits deep-link params', () => {
    expect(routeUrl(
      'limits',
      7,
      '?tab=breaches&run=11&evaluation=15&incident=12&limit=13&schedule=14'
      + '&portfolio=999&unknown=drop-me',
    )).toBe(
      '/limits?portfolio=7&tab=breaches&run=11&evaluation=15'
      + '&incident=12&limit=13&schedule=14',
    );
  });

  it('drops empty Limits deep links and all route-specific params elsewhere', () => {
    expect(routeUrl('limits', null, '?tab=&run=&unknown=x')).toBe('/limits');
    expect(routeUrl('risk', 7, '?tab=breaches&incident=12')).toBe(
      '/risk?portfolio=7',
    );
    expect(routeUrl('positions', 7, '?tab=breaches')).toBe('/positions');
  });

  it('preserves audit_ref only on Audit', () => {
    expect(routeUrl(
      'audit',
      7,
      '?audit_ref=limit%3Aincident%3A81%3Awaived&portfolio=7&unknown=drop-me',
    )).toBe('/audit?audit_ref=limit%3Aincident%3A81%3Awaived');
    expect(routeUrl('audit', null, '?audit_ref=&unknown=x')).toBe('/audit');
    expect(routeUrl('positions', null, '?audit_ref=limit%3A81')).toBe('/positions');
  });
});

describe('portfolioFromLocation', () => {
  it('reads the portfolio only on shared-portfolio routes', () => {
    expect(portfolioFromLocation('/risk', '?portfolio=7')).toBe(7);
    expect(portfolioFromLocation('/hedging', '?portfolio=9')).toBe(9);
    expect(portfolioFromLocation('/limits', '?portfolio=11')).toBe(11);
  });

  it('ignores the portfolio param on other routes (no cross-route leak)', () => {
    expect(portfolioFromLocation('/positions', '?portfolio=7')).toBeNull();
    expect(portfolioFromLocation('/', '?portfolio=7')).toBeNull();
  });
});

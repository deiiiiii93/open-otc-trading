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
    const onPop = () => {
      const next = pathToRoute(window.location.pathname);
      // Canonicalize a stale/unknown path popped from history so the URL and the
      // rendered page never disagree (e.g. Back to /nope renders Positions).
      const canonical = routeToPath(next);
      if (window.location.pathname !== canonical) {
        window.history.replaceState(null, '', canonical + window.location.search);
      }
      setRoute(next);
    };
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

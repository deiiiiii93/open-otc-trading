import { useCallback, useEffect, useState } from 'react';

export type Density = 'comfortable' | 'compact';

const STORAGE_KEY = 'otc:density';

function readStoredDensity(): Density {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === 'compact' ? 'compact' : 'comfortable';
}

function applyDensity(density: Density) {
  if (density === 'comfortable') {
    document.documentElement.removeAttribute('data-density');
  } else {
    document.documentElement.dataset.density = density;
  }
}

export function useDensity() {
  const [density, setDensityState] = useState<Density>(() => readStoredDensity());

  useEffect(() => {
    applyDensity(density);
  }, [density]);

  const setDensity = useCallback((next: Density) => {
    localStorage.setItem(STORAGE_KEY, next);
    setDensityState(next);
  }, []);

  const toggle = useCallback(() => {
    setDensityState((current) => {
      const next = current === 'comfortable' ? 'compact' : 'comfortable';
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { density, setDensity, toggle };
}

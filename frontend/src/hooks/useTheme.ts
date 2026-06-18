import { useCallback, useEffect, useState } from 'react';

export type Theme = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'otc:theme';

function readStoredTheme(): Theme {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === 'light' || raw === 'dark' || raw === 'system') return raw;
  return 'system';
}

function applyTheme(theme: Theme) {
  if (theme === 'system') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.dataset.theme = theme;
  }
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    localStorage.setItem(STORAGE_KEY, next);
    setThemeState(next);
  }, []);

  return { theme, setTheme };
}

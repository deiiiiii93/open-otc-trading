import { useCallback, useEffect, useState } from 'react';

export type ViewMode = 'compact' | 'detailed';

export const VIEW_MODE_STORAGE_KEY = 'wl.agent.viewMode';

function readMode(): ViewMode {
  if (typeof window === 'undefined') return 'compact';
  const raw = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY);
  return raw === 'detailed' ? 'detailed' : 'compact';
}

export function useViewMode(): [ViewMode, (mode: ViewMode) => void] {
  const [mode, setModeState] = useState<ViewMode>(() => readMode());

  useEffect(() => {
    setModeState(readMode());
  }, []);

  const setMode = useCallback((next: ViewMode) => {
    setModeState(next);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, next);
    }
  }, []);

  return [mode, setMode];
}

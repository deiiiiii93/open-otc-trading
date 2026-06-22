import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';

const STORAGE_KEY = 'otc:thousand-separator';

type ThousandSeparatorContextValue = {
  thousandSeparator: boolean;
  setThousandSeparator: (next: boolean) => void;
  toggleThousandSeparator: () => void;
};

const ThousandSeparatorContext = createContext<ThousandSeparatorContextValue | null>(null);

function readStoredPreference(): boolean {
  return localStorage.getItem(STORAGE_KEY) !== 'off';
}

export function ThousandSeparatorProvider({ children }: { children: React.ReactNode }) {
  const [thousandSeparator, setThousandSeparatorState] = useState(() => readStoredPreference());

  const setThousandSeparator = useCallback((next: boolean) => {
    localStorage.setItem(STORAGE_KEY, next ? 'on' : 'off');
    setThousandSeparatorState(next);
  }, []);

  const toggleThousandSeparator = useCallback(() => {
    setThousandSeparatorState((current) => {
      const next = !current;
      localStorage.setItem(STORAGE_KEY, next ? 'on' : 'off');
      return next;
    });
  }, []);

  const value = useMemo(() => ({
    thousandSeparator,
    setThousandSeparator,
    toggleThousandSeparator,
  }), [thousandSeparator, setThousandSeparator, toggleThousandSeparator]);

  return (
    <ThousandSeparatorContext.Provider value={value}>
      {children}
    </ThousandSeparatorContext.Provider>
  );
}

export function useThousandSeparator() {
  return useContext(ThousandSeparatorContext) ?? {
    thousandSeparator: true,
    setThousandSeparator: () => undefined,
    toggleThousandSeparator: () => undefined,
  };
}

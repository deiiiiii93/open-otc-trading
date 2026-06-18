import { useEffect, useMemo, useRef } from 'react';
import type { PageContext, PageContextReporter } from '../types';

export function usePageContextReporter(
  context: PageContext | null,
  reporter?: PageContextReporter,
) {
  const latestContextRef = useRef<PageContext | null>(context);
  latestContextRef.current = context;
  const signature = useMemo(() => JSON.stringify(context), [context]);

  useEffect(() => {
    if (!latestContextRef.current || !reporter) return;
    reporter(latestContextRef.current);
  }, [reporter, signature]);
}

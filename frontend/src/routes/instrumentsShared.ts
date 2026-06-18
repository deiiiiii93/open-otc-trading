/**
 * Shared helpers for the Instruments page tabs.
 * Lifted here so multiple tabs (AllowedHedges, MarketData) can import without a circular dep.
 */

/**
 * Classify a quote age into freshness bucket.
 * 0-1d: 'ok', 2-5d: 'stale', >5d: 'warn'
 */
export function quoteAgeBucket(ageDays: number): 'ok' | 'stale' | 'warn' {
  if (ageDays <= 1) return 'ok';
  if (ageDays <= 5) return 'stale';
  return 'warn';
}

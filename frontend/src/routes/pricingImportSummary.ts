/**
 * Pure summary-strip composition for the trade-keyed Pricing Params import.
 *
 * The T8 importer stamps `profile.summary` with `rows_applied`,
 * `rows_dormant`, `quotes_emitted`, `dormant_trade_ids`, and `spot_conflicts`.
 * This builds the one-line strip; zero-valued segments are omitted, and the
 * dormant trade ids are shown inline when there are five or fewer.
 */
export type PricingImportSummary = {
  rows_applied?: number | null;
  rows_dormant?: number | null;
  quotes_emitted?: number | null;
  dormant_trade_ids?: string[] | null;
  spot_conflicts?: { symbol: string; count: number; resolution: string }[] | null;
};

const DORMANT_INLINE_LIMIT = 5;

export function composeImportSummary(summary: PricingImportSummary | null | undefined): string {
  if (!summary) return '';
  const segments: string[] = [];

  const applied = summary.rows_applied ?? 0;
  if (applied > 0) segments.push(`${applied} applied`);

  const dormant = summary.rows_dormant ?? 0;
  if (dormant > 0) {
    const ids = summary.dormant_trade_ids ?? [];
    if (ids.length > 0 && ids.length <= DORMANT_INLINE_LIMIT) {
      segments.push(`${dormant} dormant (${ids.join(', ')})`);
    } else {
      segments.push(`${dormant} dormant`);
    }
  }

  const quotes = summary.quotes_emitted ?? 0;
  if (quotes > 0) segments.push(`${quotes} quotes emitted`);

  const conflicts = summary.spot_conflicts ?? [];
  if (conflicts.length > 0) {
    segments.push(
      `${conflicts.length} spot ${conflicts.length === 1 ? 'conflict' : 'conflicts'} (last row wins)`,
    );
  }

  return segments.join(' · ');
}

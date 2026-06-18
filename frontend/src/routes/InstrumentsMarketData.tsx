/**
 * InstrumentsMarketData — Market Data tab for the Instruments page.
 *
 * Three sub-sections via a small sub-tab strip:
 *   Quotes | Fetch events | FX rates
 *
 * Quotes: latest quotes table with age-bucket badges, server-side refresh,
 *   manual entry form, and history panel on row click.
 * Fetch events: read-only list of market data profiles (name, symbol,
 *   valuation_date, source). History charts are out of scope here; the old
 *   MarketData page will handle those until Task 16 removes it.
 * FX rates: ported from MarketData.tsx / MarketData.live.tsx as-is.
 */

import React, { useMemo, useState } from 'react';
import { Plus, RefreshCw, Search, X } from 'lucide-react';
import { Button } from '../components/Button';
import { DatePicker } from '../components/DatePicker';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { Select } from '../components/Select';
import type { FxRate, MarketDataProfile } from '../types';
import { quoteAgeBucket } from './instrumentsShared';
import { InstrumentsPager, usePagination } from './InstrumentsPager';
import './InstrumentsMarketData.css';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type MarketQuote = {
  id: number;
  instrument_id: number;
  symbol: string;
  kind: string;
  price: number;
  price_type: string;
  as_of: string;
  source: string;
  age_days: number;
  market_data_profile_id: number | null;
};

export type RefreshResult = {
  synced_created: number;
  synced_existing: number;
  fetched: number;
  skipped: string[];
  failed: { symbol: string; error: string }[];
};

export type ManualQuotePayload = {
  instrument_id: number;
  price: number;
  as_of: string;
  price_type: string;
};

// ---------------------------------------------------------------------------
// Pure helper — exported for unit testing
// ---------------------------------------------------------------------------

/**
 * Compose the human-readable summary string from a RefreshResult.
 * Empty segments are omitted; symbol lists are capped at SUMMARY_SYMBOL_CAP
 * entries with a "+N more" tail so a 50-symbol book can't flood the banner.
 *
 * Example: "Synced 2 new (5 existing) · fetched 3 · skipped 1: A · 1 failed: B"
 */
const SUMMARY_SYMBOL_CAP = 5;

function capSymbols(symbols: string[]): string {
  if (symbols.length <= SUMMARY_SYMBOL_CAP) return symbols.join(', ');
  const shown = symbols.slice(0, SUMMARY_SYMBOL_CAP).join(', ');
  return `${shown} +${symbols.length - SUMMARY_SYMBOL_CAP} more`;
}

export function composeRefreshSummary(result: RefreshResult): string {
  const segments: string[] = [];

  const syncedPart = `Synced ${result.synced_created} new (${result.synced_existing} existing)`;
  segments.push(syncedPart);

  if (result.fetched > 0) {
    segments.push(`fetched ${result.fetched}`);
  }

  if (result.skipped.length > 0) {
    segments.push(`skipped ${result.skipped.length}: ${capSymbols(result.skipped)}`);
  }

  if (result.failed.length > 0) {
    const symbols = capSymbols(result.failed.map((f) => f.symbol));
    segments.push(`${result.failed.length} failed: ${symbols}`);
  }

  return segments.join(' · ');
}

// ---------------------------------------------------------------------------
// Pure filter helpers — exported for unit testing
// ---------------------------------------------------------------------------

export type QuoteFilters = {
  kind: string;
  source: string;
  search: string;
};

export const EMPTY_QUOTE_FILTERS: QuoteFilters = { kind: '', source: '', search: '' };

/** Filter latest quotes by exact kind/source and case-insensitive symbol search. */
export function filterQuotes(quotes: MarketQuote[], f: QuoteFilters): MarketQuote[] {
  const q = f.search.trim().toLowerCase();
  return quotes.filter(
    (r) =>
      (!f.kind || r.kind === f.kind) &&
      (!f.source || r.source === f.source) &&
      (!q || r.symbol.toLowerCase().includes(q)),
  );
}

/** Filter fetch-event profiles by exact source and name/symbol search. */
export function filterProfiles(
  profiles: MarketDataProfile[],
  f: { source: string; search: string },
): MarketDataProfile[] {
  const q = f.search.trim().toLowerCase();
  return profiles.filter(
    (p) =>
      (!f.source || p.source === f.source) &&
      (!q || p.name.toLowerCase().includes(q) || p.symbol.toLowerCase().includes(q)),
  );
}

/** Filter FX rates by pair ("USD/CNY") or source search. */
export function filterFxRates(rates: FxRate[], search: string): FxRate[] {
  const q = search.trim().toLowerCase();
  if (!q) return rates;
  return rates.filter(
    (r) =>
      `${r.base_currency}/${r.quote_currency}`.toLowerCase().includes(q) ||
      r.source.toLowerCase().includes(q),
  );
}

function formatQuotePrice(price: number): string {
  return price.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

type CandlePoint = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  source: string;
  records: number;
};

type CandleChartStats = {
  min: number;
  max: number;
  ticks: number[];
  last: CandlePoint;
  previous: CandlePoint | null;
  change: number;
  changePct: number;
};

export function quoteHistoryToCandles(history: MarketQuote[]): CandlePoint[] {
  const byDate = new Map<string, MarketQuote[]>();

  for (const quote of history) {
    const date = quote.as_of?.slice(0, 10);
    if (!date) continue;
    const rows = byDate.get(date) ?? [];
    rows.push(quote);
    byDate.set(date, rows);
  }

  return [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, rows]) => {
      const prices = rows.map((row) => row.price);
      const open = rows.find((row) => row.price_type === 'open')?.price ?? rows[rows.length - 1].price;
      const close = rows.find((row) => row.price_type === 'close')?.price ?? rows[0].price;
      const high = rows.find((row) => row.price_type === 'high')?.price ?? Math.max(...prices, open, close);
      const low = rows.find((row) => row.price_type === 'low')?.price ?? Math.min(...prices, open, close);

      return {
        date,
        open,
        high,
        low,
        close,
        source: [...new Set(rows.map((row) => row.source))].join(', '),
        records: rows.length,
      };
    });
}

function valueY(value: number, min: number, max: number): number {
  if (max === min) return 50;
  return ((max - value) / (max - min)) * 100;
}

function buildCandleChartStats(candles: CandlePoint[]): CandleChartStats | null {
  if (candles.length === 0) return null;
  const rawMin = Math.min(...candles.map((point) => point.low));
  const rawMax = Math.max(...candles.map((point) => point.high));
  const range = rawMax - rawMin || Math.max(Math.abs(rawMax), 1) * 0.01;
  const min = rawMin - range * 0.14;
  const max = rawMax + range * 0.14;
  const tickCount = 4;
  const ticks = Array.from({ length: tickCount }, (_, index) => max - ((max - min) * index) / (tickCount - 1));
  const last = candles[candles.length - 1];
  const previous = candles.length > 1 ? candles[candles.length - 2] : null;
  const change = previous ? last.close - previous.close : 0;
  const changePct = previous && previous.close !== 0 ? change / previous.close : 0;
  return { min, max, ticks, last, previous, change, changePct };
}

function formatSignedPrice(value: number): string {
  const prefix = value > 0 ? '+' : '';
  return `${prefix}${formatQuotePrice(value)}`;
}

function formatSignedPct(value: number): string {
  const prefix = value > 0 ? '+' : '';
  return `${prefix}${(value * 100).toFixed(2)}%`;
}

// ---------------------------------------------------------------------------
// Sub-tab types
// ---------------------------------------------------------------------------

export type MarketDataSubTab = 'quotes' | 'fetch-events' | 'fx-rates';
type HistoryView = 'table' | 'chart';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type Instrument = { id: number; symbol: string };

type Props = {
  quotes: MarketQuote[];
  quotesLoading: boolean;
  quoteHistory: MarketQuote[];
  quoteHistoryInstrumentId: number | null;
  quoteHistoryLoading: boolean;
  refreshing: boolean;
  refreshFeedback: string | null;
  profiles: MarketDataProfile[];
  profilesLoading: boolean;
  fxRates: FxRate[];
  fxRatesLoading: boolean;
  fxFeedback: string | null;
  fxFetching: boolean;
  instruments: Instrument[];
  subTab?: MarketDataSubTab;
  quoteFilters?: QuoteFilters;
  fetchEventFilters?: { source: string; search: string };
  fxSearch?: string;
  manualQuoteOpen?: boolean;
  onManualQuoteOpenChange?: (open: boolean) => void;
  fxCreateOpen?: boolean;
  onFxCreateOpenChange?: (open: boolean) => void;
  onRefreshQuotes: () => Promise<void>;
  onManualQuote: (payload: ManualQuotePayload) => Promise<void>;
  onSelectQuoteHistory: (instrumentId: number) => void;
  onCloseHistory: () => void;
  onCreateFxRate: (payload: Omit<FxRate, 'id'>) => Promise<void>;
  onFetchFxRateAkshare: (base: string, quote: string) => Promise<void>;
  onDeleteFxRate: (id: number) => Promise<void>;
};

// ---------------------------------------------------------------------------
// Quotes sub-section
// ---------------------------------------------------------------------------

function QuotesSection({
  quotes,
  loading,
  refreshFeedback,
  quoteHistory,
  quoteHistoryInstrumentId,
  quoteHistoryLoading,
  instruments,
  filters,
  manualOpen,
  onManualOpenChange,
  onManualQuote,
  onSelectHistory,
  onCloseHistory,
}: {
  quotes: MarketQuote[];
  loading: boolean;
  refreshFeedback: string | null;
  quoteHistory: MarketQuote[];
  quoteHistoryInstrumentId: number | null;
  quoteHistoryLoading: boolean;
  instruments: Instrument[];
  filters: QuoteFilters;
  manualOpen: boolean;
  onManualOpenChange: (open: boolean) => void;
  onManualQuote: (payload: ManualQuotePayload) => Promise<void>;
  onSelectHistory: (id: number) => void;
  onCloseHistory: () => void;
}) {
  const [historyView, setHistoryView] = useState<HistoryView>('table');
  const [manualInstrumentId, setManualInstrumentId] = useState('');
  const [manualPrice, setManualPrice] = useState('');
  const [manualAsOf, setManualAsOf] = useState('');
  const [manualPriceType, setManualPriceType] = useState('close');
  const [manualSubmitting, setManualSubmitting] = useState(false);

  const filteredQuotes = useMemo(() => filterQuotes(quotes, filters), [quotes, filters]);
  const historySymbol = quotes.find((q) => q.instrument_id === quoteHistoryInstrumentId)?.symbol;
  const historyCandles = useMemo(() => quoteHistoryToCandles(quoteHistory), [quoteHistory]);
  const historyChartStats = useMemo(() => buildCandleChartStats(historyCandles), [historyCandles]);
  const pagination = usePagination(
    filteredQuotes,
    `${filters.kind}|${filters.source}|${filters.search}`,
  );

  const resetManualForm = () => {
    setManualInstrumentId('');
    setManualPrice('');
    setManualAsOf('');
    setManualPriceType('close');
  };

  const submitManual = async (e: React.FormEvent) => {
    e.preventDefault();
    const id = Number(manualInstrumentId);
    const price = Number(manualPrice);
    if (!id || !price || !manualAsOf) return;
    setManualSubmitting(true);
    try {
      await onManualQuote({ instrument_id: id, price, as_of: manualAsOf, price_type: manualPriceType });
      onManualOpenChange(false);
      resetManualForm();
    } finally {
      setManualSubmitting(false);
    }
  };

  return (
    <div className="wl-imd__section">
      {/* Refresh feedback */}
      {refreshFeedback && (
        <p className="wl-imd__feedback" role="status" aria-live="polite">
          {refreshFeedback}
        </p>
      )}

      {/* Manual quote inline form */}
      {manualOpen && (
        <form
          className="wl-imd__manual-form"
          onSubmit={submitManual}
          aria-label="Manual quote form"
        >
          <Select
            label="Instrument"
            value={manualInstrumentId}
            onChange={(v) => setManualInstrumentId(v)}
            placeholder="— select —"
            options={[
              { value: '', label: '— select —' },
              ...instruments.map((inst) => ({ value: String(inst.id), label: inst.symbol })),
            ]}
          />
          <label>
            Price
            <input
              type="number"
              step="any"
              value={manualPrice}
              onChange={(e) => setManualPrice(e.target.value)}
              aria-label="Price"
              required
              placeholder="0.00"
            />
          </label>
          <DatePicker
            label="As of"
            value={manualAsOf}
            onChange={(v) => setManualAsOf(v)}
          />
          <Select
            label="Price type"
            value={manualPriceType}
            onChange={(v) => setManualPriceType(v)}
            options={[
              { value: 'close', label: 'close' },
              { value: 'open', label: 'open' },
              { value: 'mid', label: 'mid' },
              { value: 'spot', label: 'spot' },
              { value: 'settlement', label: 'settlement' },
            ]}
          />
          <div className="wl-imd__form-actions">
            <Button type="submit" variant="primary" disabled={manualSubmitting}>
              Submit
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => { onManualOpenChange(false); resetManualForm(); }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      {/* Quotes table */}
      {loading ? (
        <Empty message="Loading quotes…" />
      ) : quotes.length === 0 ? (
        <Empty message="No quotes yet. Run a refresh or enter manually." />
      ) : filteredQuotes.length === 0 ? (
        <Empty message="No quotes match the current filters." symbol="∅" />
      ) : (
        <div className="wl-imd__table-wrap">
          <table className="wl-imd__table wl-imd__table--clickable" aria-label="Latest quotes">
            <thead>
              <tr>
                <th>INSTRUMENT</th>
                <th>KIND</th>
                <th>PRICE</th>
                <th>TYPE</th>
                <th>AS OF</th>
                <th>SOURCE</th>
                <th>AGE</th>
              </tr>
            </thead>
            <tbody>
              {pagination.pagedRows.map((q) => {
                const bucket = quoteAgeBucket(q.age_days);
                const isHistoryActive = quoteHistoryInstrumentId === q.instrument_id;
                return (
                  <tr
                    key={q.id}
                    className={isHistoryActive ? 'is-selected' : ''}
                    onClick={() => onSelectHistory(q.instrument_id)}
                  >
                    <td>{q.symbol}</td>
                    <td>{q.kind}</td>
                    <td>{formatQuotePrice(q.price)}</td>
                    <td>{q.price_type}</td>
                    <td>{q.as_of?.slice(0, 10)}</td>
                    <td>{q.source}</td>
                    <td>
                      <span className={`wl-ah__age-badge is-${bucket}`}>
                        {Math.round(q.age_days)}d
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <InstrumentsPager pagination={pagination} label="quotes" />

      <Modal
        open={quoteHistoryInstrumentId !== null}
        onOpenChange={(open) => { if (!open) onCloseHistory(); }}
        title={`Quote History${historySymbol ? ` · ${historySymbol}` : ''}`}
        contentClassName="wl-imd__history-dialog"
        layoutKey="quote-history"
        defaultWidth={960}
        defaultHeight={640}
        minWidth={520}
        minHeight={360}
      >
        <div className="wl-imd__history-modal">
          <div className="wl-imd__history-tabs" role="tablist" aria-label="Quote history views">
            {([
              ['table', 'Table'],
              ['chart', 'Candle chart'],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={historyView === id}
                className={`wl-imd__history-tab${historyView === id ? ' is-active' : ''}`}
                onClick={() => setHistoryView(id)}
              >
                {label}
              </button>
            ))}
          </div>

          {quoteHistoryLoading ? (
            <Empty message="Loading history…" />
          ) : quoteHistory.length === 0 ? (
            <Empty message="No history." />
          ) : historyView === 'table' ? (
            <div className="wl-imd__table-wrap wl-imd__history-table-wrap">
              <table className="wl-imd__table" aria-label="Quote history rows">
                <thead>
                  <tr>
                    <th>PRICE</th>
                    <th>TYPE</th>
                    <th>AS OF</th>
                    <th>SOURCE</th>
                  </tr>
                </thead>
                <tbody>
                  {quoteHistory.map((q) => (
                    <tr key={q.id}>
                      <td>{formatQuotePrice(q.price)}</td>
                      <td>{q.price_type}</td>
                      <td>{q.as_of?.slice(0, 10)}</td>
                      <td>{q.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : historyCandles.length === 0 || historyChartStats === null ? (
            <Empty message="No chartable history." />
          ) : (
            <div className="wl-imd__candle-chart" aria-label="Quote history candle chart">
              <div className="wl-imd__chart-header">
                <div>
                  <div className="wl-imd__chart-kicker">{historySymbol ?? 'Instrument'} · {historyCandles.length} sessions</div>
                  <div className="wl-imd__chart-price-row">
                    <span className="wl-imd__chart-last">{formatQuotePrice(historyChartStats.last.close)}</span>
                    <span className={`wl-imd__chart-change ${historyChartStats.change >= 0 ? 'is-up' : 'is-down'}`}>
                      {formatSignedPrice(historyChartStats.change)} ({formatSignedPct(historyChartStats.changePct)})
                    </span>
                  </div>
                </div>
                <div className="wl-imd__chart-meta">
                  <span>O {formatQuotePrice(historyChartStats.last.open)}</span>
                  <span>H {formatQuotePrice(historyChartStats.last.high)}</span>
                  <span>L {formatQuotePrice(historyChartStats.last.low)}</span>
                  <span>C {formatQuotePrice(historyChartStats.last.close)}</span>
                </div>
              </div>
              <div className="wl-imd__chart-card">
                <div className="wl-imd__price-scale" aria-hidden="true">
                  {historyChartStats.ticks.map((tick) => (
                    <span key={tick}>{formatQuotePrice(tick)}</span>
                  ))}
                </div>
                <div className="wl-imd__plot-area">
                  {historyChartStats.ticks.map((tick) => (
                    <span
                      key={tick}
                      className="wl-imd__grid-line"
                      style={{ top: `${valueY(tick, historyChartStats.min, historyChartStats.max)}%` }}
                    />
                  ))}
                  <span
                    className="wl-imd__last-line"
                    style={{ top: `${valueY(historyChartStats.last.close, historyChartStats.min, historyChartStats.max)}%` }}
                  />
                  <span
                    className="wl-imd__last-price-pill"
                    style={{ top: `${valueY(historyChartStats.last.close, historyChartStats.min, historyChartStats.max)}%` }}
                  >
                    {formatQuotePrice(historyChartStats.last.close)}
                  </span>
                  <div className="wl-imd__candle-series">
                    {historyCandles.map((point) => {
                      const openY = valueY(point.open, historyChartStats.min, historyChartStats.max);
                      const closeY = valueY(point.close, historyChartStats.min, historyChartStats.max);
                      const highY = valueY(point.high, historyChartStats.min, historyChartStats.max);
                      const lowY = valueY(point.low, historyChartStats.min, historyChartStats.max);
                      const bodyTop = Math.min(openY, closeY);
                      const bodyHeight = Math.max(Math.abs(openY - closeY), 0.8);
                      const direction = point.close >= point.open ? 'up' : 'down';
                      return (
                        <div
                          key={point.date}
                          className="wl-imd__candle-slot"
                          title={`${point.date}\nopen ${formatQuotePrice(point.open)}\nhigh ${formatQuotePrice(point.high)}\nlow ${formatQuotePrice(point.low)}\nclose ${formatQuotePrice(point.close)}\n${point.records} quote rows · ${point.source}`}
                        >
                          <span
                            className="wl-imd__candle-wick"
                            style={{ top: `${highY}%`, height: `${Math.max(lowY - highY, 1)}%` }}
                          />
                          <span
                            className={`wl-imd__candle-body is-${direction}`}
                            style={{ top: `${bodyTop}%`, height: `${bodyHeight}%` }}
                          />
                          <span className="wl-imd__candle-hover">
                            <strong>{point.date}</strong>
                            <span>O {formatQuotePrice(point.open)}</span>
                            <span>H {formatQuotePrice(point.high)}</span>
                            <span>L {formatQuotePrice(point.low)}</span>
                            <span>C {formatQuotePrice(point.close)}</span>
                          </span>
                          <span className="wl-imd__candle-date">{point.date.slice(5)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
              <div className="wl-imd__quote-strip" aria-label="Latest quote summary">
                {[
                  ['Open', historyChartStats.last.open],
                  ['High', historyChartStats.last.high],
                  ['Low', historyChartStats.last.low],
                  ['Close', historyChartStats.last.close],
                ].map(([label, value]) => (
                  <div key={label} className="wl-imd__quote-strip-item">
                    <span>{label}</span>
                    <strong>{formatQuotePrice(value as number)}</strong>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fetch events sub-section
// ---------------------------------------------------------------------------

function FetchEventsSection({
  profiles,
  loading,
  filters,
}: {
  profiles: MarketDataProfile[];
  loading: boolean;
  filters: { source: string; search: string };
}) {
  const filtered = useMemo(
    () => filterProfiles(profiles, filters),
    [profiles, filters],
  );
  const pagination = usePagination(filtered, `${filters.source}|${filters.search}`);

  if (loading) return <Empty message="Loading fetch events…" />;
  if (profiles.length === 0) return <Empty message="No fetch events yet." />;

  return (
    <div className="wl-imd__section">
      {/* History charts are out of scope here; the old MarketData page covers them until Task 16 removes it. */}
      {filtered.length === 0 ? (
        <Empty message="No fetch events match the current filters." symbol="∅" />
      ) : (
        <div className="wl-imd__table-wrap">
          <table className="wl-imd__table" aria-label="Fetch events">
            <thead>
              <tr>
                <th>NAME</th>
                <th>SYMBOL</th>
                <th>VALUATION DATE</th>
                <th>SOURCE</th>
              </tr>
            </thead>
            <tbody>
              {pagination.pagedRows.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>{p.symbol}</td>
                  <td>{p.valuation_date?.slice(0, 10)}</td>
                  <td>{p.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <InstrumentsPager pagination={pagination} label="fetch events" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// FX rates sub-section (ported from MarketData.tsx)
// ---------------------------------------------------------------------------

function FxRatesSection({
  fxRates,
  loading,
  feedback,
  fetching,
  search,
  createOpen,
  onCreateOpenChange,
  onCreateFxRate,
  onFetchAkshare,
  onDelete,
}: {
  fxRates: FxRate[];
  loading: boolean;
  feedback: string | null;
  fetching: boolean;
  search: string;
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  onCreateFxRate: (payload: Omit<FxRate, 'id'>) => Promise<void>;
  onFetchAkshare: (base: string, quote: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  // Manual create form state
  const [createBase, setCreateBase] = useState('USD');
  const [createQuote, setCreateQuote] = useState('CNY');
  const [createRate, setCreateRate] = useState('');
  const [createAsOf, setCreateAsOf] = useState('');
  const [createSource, setCreateSource] = useState('manual');
  const [createSubmitting, setCreateSubmitting] = useState(false);

  // AKShare fetch form state
  const [akBase, setAkBase] = useState('USD');
  const [akQuote, setAkQuote] = useState('CNY');
  const [akFetching, setAkFetching] = useState(false);

  // Table filter + pagination
  const filtered = useMemo(() => filterFxRates(fxRates, search), [fxRates, search]);
  const pagination = usePagination(filtered, search);

  const submitCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const rate = Number(createRate);
    if (!createBase || !createQuote || !rate || !createAsOf) return;
    setCreateSubmitting(true);
    try {
      await onCreateFxRate({
        base_currency: createBase,
        quote_currency: createQuote,
        rate,
        as_of_date: createAsOf,
        source: createSource,
      });
      onCreateOpenChange(false);
      setCreateBase('USD');
      setCreateQuote('CNY');
      setCreateRate('');
      setCreateAsOf('');
      setCreateSource('manual');
    } finally {
      setCreateSubmitting(false);
    }
  };

  const submitAkshare = async (e: React.FormEvent) => {
    e.preventDefault();
    setAkFetching(true);
    try {
      await onFetchAkshare(akBase, akQuote);
    } finally {
      setAkFetching(false);
    }
  };

  return (
    <div className="wl-imd__section">
      {/* AKShare fetch form */}
      <form className="wl-imd__fx-akshare-form" onSubmit={submitAkshare} aria-label="Fetch FX rate from AKShare">
        <label>
          Base
          <input
            value={akBase}
            onChange={(e) => setAkBase(e.target.value)}
            aria-label="Base currency"
            placeholder="USD"
          />
        </label>
        <label>
          Quote
          <input
            value={akQuote}
            onChange={(e) => setAkQuote(e.target.value)}
            aria-label="Quote currency"
            placeholder="CNY"
          />
        </label>
        <Button type="submit" disabled={fetching || akFetching || !akBase || !akQuote}>
          {akFetching ? 'Fetching…' : 'Fetch from AKShare'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={() => onCreateOpenChange(!createOpen)}
          aria-label="Add manually"
        >
          <Plus size={14} aria-hidden="true" />
          Add manually
        </Button>
      </form>

      {/* Manual create form */}
      {createOpen && (
        <form
          className="wl-imd__fx-create-form"
          onSubmit={submitCreate}
          aria-label="Add FX rate"
        >
          <label>
            Base currency
            <input
              value={createBase}
              onChange={(e) => setCreateBase(e.target.value)}
              aria-label="Base currency"
              required
            />
          </label>
          <label>
            Quote currency
            <input
              value={createQuote}
              onChange={(e) => setCreateQuote(e.target.value)}
              aria-label="Quote currency"
              required
            />
          </label>
          <label>
            Rate
            <input
              type="number"
              step="any"
              value={createRate}
              onChange={(e) => setCreateRate(e.target.value)}
              aria-label="Rate"
              required
              placeholder="7.25"
            />
          </label>
          <DatePicker
            label="As of date"
            value={createAsOf}
            onChange={(v) => setCreateAsOf(v)}
          />
          <label>
            Source
            <input
              value={createSource}
              onChange={(e) => setCreateSource(e.target.value)}
              aria-label="Source"
            />
          </label>
          <div className="wl-imd__form-actions">
            <Button type="submit" variant="primary" disabled={createSubmitting}>
              Save
            </Button>
            <Button type="button" variant="ghost" onClick={() => onCreateOpenChange(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {/* Feedback */}
      {feedback && (
        <p className="wl-imd__feedback" role="status" aria-live="polite">
          {feedback}
        </p>
      )}

      {/* FX rates table */}
      {loading ? (
        <Empty message="Loading FX rates…" />
      ) : fxRates.length === 0 ? (
        <Empty message="No FX rates yet." />
      ) : filtered.length === 0 ? (
        <Empty message="No FX rates match this search." symbol="∅" />
      ) : (
        <div className="wl-imd__table-wrap">
          <table className="wl-imd__table" aria-label="FX rates">
            <thead>
              <tr>
                <th>PAIR</th>
                <th>RATE</th>
                <th>AS OF</th>
                <th>SOURCE</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {pagination.pagedRows.map((r) => (
                <tr key={r.id}>
                  <td>
                    {r.base_currency}/{r.quote_currency}
                  </td>
                  <td>{r.rate.toLocaleString('en-US', { maximumFractionDigits: 6 })}</td>
                  <td>{r.as_of_date?.slice(0, 10)}</td>
                  <td>{r.source}</td>
                  <td>
                    <Button
                      variant="ghost"
                      onClick={() => onDelete(r.id)}
                      aria-label={`Delete ${r.base_currency}/${r.quote_currency}`}
                    >
                      <X size={13} aria-hidden="true" />
                      delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <InstrumentsPager pagination={pagination} label="FX rates" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export function InstrumentsMarketData({
  quotes,
  quotesLoading,
  quoteHistory,
  quoteHistoryInstrumentId,
  quoteHistoryLoading,
  refreshing,
  refreshFeedback,
  profiles,
  profilesLoading,
  fxRates,
  fxRatesLoading,
  fxFeedback,
  fxFetching,
  instruments,
  subTab,
  quoteFilters,
  fetchEventFilters,
  fxSearch,
  manualQuoteOpen,
  onManualQuoteOpenChange,
  fxCreateOpen,
  onFxCreateOpenChange,
  onRefreshQuotes,
  onManualQuote,
  onSelectQuoteHistory,
  onCloseHistory,
  onCreateFxRate,
  onFetchFxRateAkshare,
  onDeleteFxRate,
}: Props) {
  const controlled = subTab !== undefined;
  const [internalSubTab, setInternalSubTab] = useState<MarketDataSubTab>('quotes');
  const [internalQuoteFilters, setInternalQuoteFilters] = useState<QuoteFilters>(EMPTY_QUOTE_FILTERS);
  const [internalFetchEventFilters, setInternalFetchEventFilters] = useState({ source: '', search: '' });
  const [internalFxSearch, setInternalFxSearch] = useState('');
  const [internalManualQuoteOpen, setInternalManualQuoteOpen] = useState(false);
  const [internalFxCreateOpen, setInternalFxCreateOpen] = useState(false);
  const effectiveSubTab = subTab ?? internalSubTab;
  const effectiveQuoteFilters = quoteFilters ?? internalQuoteFilters;
  const effectiveFetchEventFilters = fetchEventFilters ?? internalFetchEventFilters;
  const effectiveFxSearch = fxSearch ?? internalFxSearch;
  const effectiveManualQuoteOpen = manualQuoteOpen ?? internalManualQuoteOpen;
  const effectiveFxCreateOpen = fxCreateOpen ?? internalFxCreateOpen;
  const setEffectiveManualQuoteOpen = onManualQuoteOpenChange ?? setInternalManualQuoteOpen;
  const setEffectiveFxCreateOpen = onFxCreateOpenChange ?? setInternalFxCreateOpen;
  const quoteKindOptions = useMemo(() => [...new Set(quotes.map((q) => q.kind))].sort(), [quotes]);
  const quoteSourceOptions = useMemo(() => [...new Set(quotes.map((q) => q.source))].sort(), [quotes]);
  const profileSourceOptions = useMemo(() => [...new Set(profiles.map((p) => p.source))].sort(), [profiles]);
  const SUB_TABS: { id: MarketDataSubTab; label: string }[] = [
    { id: 'quotes', label: 'Quotes' },
    { id: 'fetch-events', label: 'Fetch events' },
    { id: 'fx-rates', label: 'FX rates' },
  ];

  return (
    <div className="wl-imd">
      {!controlled && (
        <>
          <div className="wl-imd__subtabs" role="tablist" aria-label="Market data sub-tabs">
            {SUB_TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={effectiveSubTab === t.id}
                className={`wl-imd__subtab${effectiveSubTab === t.id ? ' is-active' : ''}`}
                onClick={() => setInternalSubTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          {effectiveSubTab === 'quotes' && (
            <div className="wl-imd__toolbar">
              <Button
                onClick={onRefreshQuotes}
                disabled={refreshing}
                aria-label="Refresh quotes"
                title="Refresh quotes (all resolvable)"
              >
                <RefreshCw size={14} aria-hidden="true" className={refreshing ? 'wl-imd-spin' : undefined} />
                {refreshing ? 'Refreshing…' : 'Refresh quotes (all resolvable)'}
              </Button>
              <Button
                variant="ghost"
                onClick={() => setEffectiveManualQuoteOpen(!effectiveManualQuoteOpen)}
                aria-label="Manual quote"
                title="Enter a quote manually"
              >
                <Plus size={14} aria-hidden="true" />
                Manual quote
              </Button>
              <div className="wl-imd__toolbar-spacer" />
              <Select
                variant="inline"
                label="Filter quotes by kind"
                value={effectiveQuoteFilters.kind}
                onChange={(v) => setInternalQuoteFilters({ ...effectiveQuoteFilters, kind: v })}
                options={[
                  { value: '', label: 'All' },
                  ...quoteKindOptions.map((k) => ({ value: k, label: k })),
                ]}
              />
              <Select
                variant="inline"
                label="SOURCE"
                value={effectiveQuoteFilters.source}
                onChange={(v) => setInternalQuoteFilters({ ...effectiveQuoteFilters, source: v })}
                options={[
                  { value: '', label: 'All' },
                  ...quoteSourceOptions.map((source) => ({ value: source, label: source })),
                ]}
              />
              <label className="wl-imd__search">
                <Search size={13} aria-hidden="true" />
                <input
                  type="search"
                  value={effectiveQuoteFilters.search}
                  onChange={(e) => setInternalQuoteFilters({ ...effectiveQuoteFilters, search: e.target.value })}
                  placeholder="Search symbol…"
                  aria-label="Search quotes"
                />
              </label>
            </div>
          )}
          {effectiveSubTab === 'fetch-events' && (
            <div className="wl-imd__toolbar">
              <Select
                variant="inline"
                label="SOURCE"
                value={effectiveFetchEventFilters.source}
                onChange={(v) =>
                  setInternalFetchEventFilters({ ...effectiveFetchEventFilters, source: v })
                }
                options={[
                  { value: '', label: 'All' },
                  ...profileSourceOptions.map((source) => ({ value: source, label: source })),
                ]}
              />
              <label className="wl-imd__search">
                <Search size={13} aria-hidden="true" />
                <input
                  type="search"
                  value={effectiveFetchEventFilters.search}
                  onChange={(e) =>
                    setInternalFetchEventFilters({ ...effectiveFetchEventFilters, search: e.target.value })
                  }
                  placeholder="Search name, symbol…"
                  aria-label="Search fetch events"
                />
              </label>
            </div>
          )}
          {effectiveSubTab === 'fx-rates' && (
            <div className="wl-imd__toolbar">
              <label className="wl-imd__search">
                <Search size={13} aria-hidden="true" />
                <input
                  type="search"
                  value={effectiveFxSearch}
                  onChange={(e) => setInternalFxSearch(e.target.value)}
                  placeholder="Search pair, source…"
                  aria-label="Search FX rates"
                />
              </label>
            </div>
          )}
        </>
      )}
      {/* Sub-tab content */}
      {effectiveSubTab === 'quotes' && (
        <QuotesSection
          quotes={quotes}
          loading={quotesLoading}
          refreshFeedback={refreshFeedback}
          quoteHistory={quoteHistory}
          quoteHistoryInstrumentId={quoteHistoryInstrumentId}
          quoteHistoryLoading={quoteHistoryLoading}
          instruments={instruments}
          filters={effectiveQuoteFilters}
          manualOpen={effectiveManualQuoteOpen}
          onManualOpenChange={setEffectiveManualQuoteOpen}
          onManualQuote={onManualQuote}
          onSelectHistory={onSelectQuoteHistory}
          onCloseHistory={onCloseHistory}
        />
      )}
      {effectiveSubTab === 'fetch-events' && (
        <FetchEventsSection
          profiles={profiles}
          loading={profilesLoading}
          filters={effectiveFetchEventFilters}
        />
      )}
      {effectiveSubTab === 'fx-rates' && (
        <FxRatesSection
          fxRates={fxRates}
          loading={fxRatesLoading}
          feedback={fxFeedback}
          fetching={fxFetching}
          search={effectiveFxSearch}
          createOpen={effectiveFxCreateOpen}
          onCreateOpenChange={setEffectiveFxCreateOpen}
          onCreateFxRate={onCreateFxRate}
          onFetchAkshare={onFetchFxRateAkshare}
          onDelete={onDeleteFxRate}
        />
      )}
    </div>
  );
}

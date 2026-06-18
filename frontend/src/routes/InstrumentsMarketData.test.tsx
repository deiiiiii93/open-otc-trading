/**
 * Tests for InstrumentsMarketData — Market Data tab on the Instruments page.
 *
 * FX cases ported from MarketData.test.tsx:
 *   - [fx-1] renders FX rates table with pair, rate, as_of, source
 *   - [fx-2] AKShare fetch form submits base+quote
 *   - [fx-3] manual create form submits base/quote/rate/as_of/source
 *   - [fx-4] delete button fires onDeleteFxRate with the rate id
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  InstrumentsMarketData,
  composeRefreshSummary,
  filterQuotes,
  filterProfiles,
  filterFxRates,
  quoteHistoryToCandles,
  EMPTY_QUOTE_FILTERS,
} from './InstrumentsMarketData';
import type { MarketQuote, RefreshResult } from './InstrumentsMarketData';
import type { FxRate, MarketDataProfile } from '../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function quote(overrides: Partial<MarketQuote> = {}): MarketQuote {
  return {
    id: 1,
    instrument_id: 10,
    symbol: 'IC2406.CFFEX',
    kind: 'futures',
    price: 5800,
    price_type: 'close',
    as_of: '2026-06-01',
    source: 'akshare',
    age_days: 0,
    market_data_profile_id: null,
    ...overrides,
  };
}

function fxRate(overrides: Partial<FxRate> = {}): FxRate {
  return {
    id: 1,
    base_currency: 'USD',
    quote_currency: 'CNY',
    rate: 7.25,
    as_of_date: '2026-06-01',
    source: 'akshare',
    ...overrides,
  };
}

function profile(overrides: Partial<MarketDataProfile> = {}): MarketDataProfile {
  return {
    id: 1,
    name: 'CSI300',
    source: 'akshare',
    symbol: '000300',
    asset_class: 'index',
    start_date: '2026-01-01',
    end_date: '2026-06-01',
    adjust: 'qfq',
    valuation_date: '2026-06-01',
    data: {},
    source_metadata: null,
    created_at: '2026-06-01T00:00:00',
    updated_at: '2026-06-01T00:00:00',
    ...overrides,
  };
}

const asyncNoop = vi.fn(async () => {});

const defaultProps = {
  quotes: [] as MarketQuote[],
  quotesLoading: false,
  quoteHistory: [] as MarketQuote[],
  quoteHistoryInstrumentId: null as number | null,
  quoteHistoryLoading: false,
  refreshing: false,
  refreshFeedback: null as string | null,
  profiles: [] as MarketDataProfile[],
  profilesLoading: false,
  fxRates: [] as FxRate[],
  fxRatesLoading: false,
  fxFeedback: null as string | null,
  fxFetching: false,
  instruments: [{ id: 10, symbol: 'IC2406.CFFEX' }],
  onRefreshQuotes: asyncNoop,
  onManualQuote: asyncNoop,
  onSelectQuoteHistory: vi.fn(),
  onCloseHistory: vi.fn(),
  onCreateFxRate: asyncNoop,
  onFetchFxRateAkshare: asyncNoop,
  onDeleteFxRate: asyncNoop,
};

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// composeRefreshSummary — pure unit tests
// ---------------------------------------------------------------------------

describe('composeRefreshSummary', () => {
  it('includes synced counts always', () => {
    const result: RefreshResult = { synced_created: 2, synced_existing: 5, fetched: 0, skipped: [], failed: [] };
    expect(composeRefreshSummary(result)).toBe('Synced 2 new (5 existing)');
  });

  it('includes fetched when > 0', () => {
    const result: RefreshResult = { synced_created: 1, synced_existing: 3, fetched: 4, skipped: [], failed: [] };
    expect(composeRefreshSummary(result)).toBe('Synced 1 new (3 existing) · fetched 4');
  });

  it('includes skipped list when non-empty', () => {
    const result: RefreshResult = { synced_created: 0, synced_existing: 0, fetched: 0, skipped: ['AAPL', 'GOOG'], failed: [] };
    expect(composeRefreshSummary(result)).toBe('Synced 0 new (0 existing) · skipped 2: AAPL, GOOG');
  });

  it('includes failed list when non-empty', () => {
    const result: RefreshResult = {
      synced_created: 1,
      synced_existing: 2,
      fetched: 3,
      skipped: [],
      failed: [{ symbol: 'BAD', error: 'timeout' }],
    };
    expect(composeRefreshSummary(result)).toBe('Synced 1 new (2 existing) · fetched 3 · 1 failed: BAD');
  });

  it('omits fetched=0 and empty skipped/failed segments (mixed result)', () => {
    const result: RefreshResult = {
      synced_created: 2,
      synced_existing: 5,
      fetched: 0,
      skipped: [],
      failed: [{ symbol: 'X', error: 'err' }],
    };
    const summary = composeRefreshSummary(result);
    expect(summary).toBe('Synced 2 new (5 existing) · 1 failed: X');
    // fetched segment absent
    expect(summary).not.toContain('fetched');
    // skipped segment absent
    expect(summary).not.toContain('skipped');
  });

  it('all segments present — full mixed result', () => {
    const result: RefreshResult = {
      synced_created: 3,
      synced_existing: 7,
      fetched: 10,
      skipped: ['S1'],
      failed: [{ symbol: 'F1', error: 'e' }, { symbol: 'F2', error: 'e' }],
    };
    expect(composeRefreshSummary(result)).toBe(
      'Synced 3 new (7 existing) · fetched 10 · skipped 1: S1 · 2 failed: F1, F2',
    );
  });
});

// ---------------------------------------------------------------------------
// Sub-tab switching
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData sub-tab switching', () => {
  it('renders three sub-tabs', () => {
    render(<InstrumentsMarketData {...defaultProps} />);
    expect(screen.getByRole('tab', { name: /quotes/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /fetch events/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /fx rates/i })).toBeInTheDocument();
  });

  it('starts on the Quotes sub-tab', () => {
    render(<InstrumentsMarketData {...defaultProps} />);
    expect(screen.getByRole('tab', { name: /quotes/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /refresh quotes/i })).toBeInTheDocument();
  });

  it('switches to Fetch events sub-tab', async () => {
    render(<InstrumentsMarketData {...defaultProps} />);
    await userEvent.click(screen.getByRole('tab', { name: /fetch events/i }));
    expect(screen.getByRole('tab', { name: /fetch events/i })).toHaveAttribute('aria-selected', 'true');
    // Quotes toolbar no longer visible
    expect(screen.queryByRole('button', { name: /refresh quotes/i })).not.toBeInTheDocument();
  });

  it('switches to FX rates sub-tab', async () => {
    render(<InstrumentsMarketData {...defaultProps} />);
    await userEvent.click(screen.getByRole('tab', { name: /fx rates/i }));
    expect(screen.getByRole('tab', { name: /fx rates/i })).toHaveAttribute('aria-selected', 'true');
    // AKShare fetch form is present
    expect(screen.getByRole('form', { name: /fetch fx rate from akshare/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Quotes table — age badge classes
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — quotes table', () => {
  it('renders the quotes table with symbol, kind, price, type, as_of, source', () => {
    const q = quote({ symbol: 'IC2406.CFFEX', kind: 'futures', price: 5800, price_type: 'close', as_of: '2026-06-01', source: 'akshare' });
    render(<InstrumentsMarketData {...defaultProps} quotes={[q]} />);
    const table = screen.getByRole('table', { name: /latest quotes/i });
    expect(within(table).getByText('IC2406.CFFEX')).toBeInTheDocument();
    expect(within(table).getByText('futures')).toBeInTheDocument();
    expect(within(table).getByText('close')).toBeInTheDocument();
    expect(within(table).getByText('2026-06-01')).toBeInTheDocument();
    expect(within(table).getByText('akshare')).toBeInTheDocument();
  });

  it('renders age-badge with class is-ok for age_days=0', () => {
    const q = quote({ age_days: 0 });
    render(<InstrumentsMarketData {...defaultProps} quotes={[q]} />);
    const badge = screen.getByText('0d');
    expect(badge).toHaveClass('is-ok');
  });

  it('renders age-badge with class is-stale for age_days=3', () => {
    const q = quote({ id: 2, age_days: 3 });
    render(<InstrumentsMarketData {...defaultProps} quotes={[q]} />);
    const badge = screen.getByText('3d');
    expect(badge).toHaveClass('is-stale');
  });

  it('renders age-badge with class is-warn for age_days=7', () => {
    const q = quote({ id: 3, age_days: 7 });
    render(<InstrumentsMarketData {...defaultProps} quotes={[q]} />);
    const badge = screen.getByText('7d');
    expect(badge).toHaveClass('is-warn');
  });

  it('shows empty state when no quotes', () => {
    render(<InstrumentsMarketData {...defaultProps} quotes={[]} />);
    expect(screen.getByText(/no quotes yet/i)).toBeInTheDocument();
  });

  it('shows loading state when quotesLoading=true', () => {
    render(<InstrumentsMarketData {...defaultProps} quotesLoading />);
    expect(screen.getByText(/loading quotes/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Refresh button — disabled while refreshing, fires callback
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — refresh button', () => {
  it('is enabled when not refreshing', () => {
    render(<InstrumentsMarketData {...defaultProps} refreshing={false} />);
    expect(screen.getByRole('button', { name: /refresh quotes/i })).not.toBeDisabled();
  });

  it('is disabled while refreshing', () => {
    render(<InstrumentsMarketData {...defaultProps} refreshing />);
    expect(screen.getByRole('button', { name: /refresh quotes/i })).toBeDisabled();
  });

  it('calls onRefreshQuotes when clicked', async () => {
    const onRefreshQuotes = vi.fn(async () => {});
    render(<InstrumentsMarketData {...defaultProps} onRefreshQuotes={onRefreshQuotes} />);
    await userEvent.click(screen.getByRole('button', { name: /refresh quotes/i }));
    expect(onRefreshQuotes).toHaveBeenCalledTimes(1);
  });

  it('shows refresh feedback string when set', () => {
    const feedback = 'Synced 2 new (5 existing) · fetched 3';
    render(<InstrumentsMarketData {...defaultProps} refreshFeedback={feedback} />);
    expect(screen.getByText(feedback)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Summary feedback string composition — integration: verify exact rendering
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — refresh feedback rendering (mixed result)', () => {
  it('renders the composed summary string from a mixed RefreshResult', () => {
    // This tests the full pipeline: composeRefreshSummary produces the string,
    // and the component renders it verbatim.
    const result: RefreshResult = {
      synced_created: 2,
      synced_existing: 4,
      fetched: 1,
      skipped: [],
      failed: [{ symbol: 'BAD', error: 'timeout' }],
    };
    const expected = composeRefreshSummary(result);
    render(<InstrumentsMarketData {...defaultProps} refreshFeedback={expected} />);
    expect(screen.getByText(expected)).toBeInTheDocument();
    // Verify the string is exactly the composed one (omits empty segments)
    expect(expected).not.toContain('skipped');
    expect(expected).toContain('fetched 1');
    expect(expected).toContain('1 failed: BAD');
  });
});

// ---------------------------------------------------------------------------
// Manual quote form
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — manual quote form', () => {
  it('shows manual quote form when button clicked', async () => {
    render(<InstrumentsMarketData {...defaultProps} />);
    await userEvent.click(screen.getByRole('button', { name: /manual quote/i }));
    expect(screen.getByRole('form', { name: /manual quote form/i })).toBeInTheDocument();
  });

  it('submits manual quote with instrument_id, price, as_of, price_type', async () => {
    const onManualQuote = vi.fn(async () => {});
    render(
      <InstrumentsMarketData
        {...defaultProps}
        onManualQuote={onManualQuote}
        instruments={[{ id: 10, symbol: 'IC2406.CFFEX' }]}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /manual quote/i }));

    await userEvent.selectOptions(screen.getByLabelText('Instrument'), '10');
    await userEvent.clear(screen.getByLabelText('Price'));
    await userEvent.type(screen.getByLabelText('Price'), '5800');
    await userEvent.type(screen.getByLabelText('As of'), '2026-06-01');
    await userEvent.selectOptions(screen.getByLabelText('Price type'), 'close');

    await userEvent.click(screen.getByRole('button', { name: /^submit$/i }));

    expect(onManualQuote).toHaveBeenCalledWith({
      instrument_id: 10,
      price: 5800,
      as_of: '2026-06-01',
      price_type: 'close',
    });
  });
});

// ---------------------------------------------------------------------------
// History panel
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — history panel', () => {
  it('shows history dialog rows when quoteHistoryInstrumentId is set', () => {
    const history: MarketQuote[] = [
      quote({ id: 100, price: 5700, price_type: 'close', as_of: '2026-05-30', source: 'akshare' }),
      quote({ id: 101, price: 5800, price_type: 'close', as_of: '2026-06-01', source: 'akshare' }),
    ];
    render(
      <InstrumentsMarketData
        {...defaultProps}
        quotes={[quote()]}
        quoteHistoryInstrumentId={10}
        quoteHistory={history}
      />,
    );
    const dialog = screen.getByRole('dialog', { name: /quote history/i });
    const histTable = within(dialog).getByRole('table', { name: /quote history rows/i });
    expect(within(histTable).getAllByRole('row')).toHaveLength(3); // 1 header + 2 data
  });

  it('switches quote history dialog to candle chart view', async () => {
    const history: MarketQuote[] = [
      quote({ id: 100, price: 5700, price_type: 'open', as_of: '2026-05-30', source: 'akshare' }),
      quote({ id: 101, price: 5800, price_type: 'close', as_of: '2026-05-30', source: 'akshare' }),
    ];
    render(
      <InstrumentsMarketData
        {...defaultProps}
        quotes={[quote()]}
        quoteHistoryInstrumentId={10}
        quoteHistory={history}
      />,
    );
    const dialog = screen.getByRole('dialog', { name: /quote history/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /candle chart/i }));
    expect(within(dialog).getByLabelText(/quote history candle chart/i)).toBeInTheDocument();
  });

  it('fires onSelectQuoteHistory when a quote row is clicked', async () => {
    const onSelectQuoteHistory = vi.fn();
    render(
      <InstrumentsMarketData
        {...defaultProps}
        quotes={[quote({ instrument_id: 10 })]}
        onSelectQuoteHistory={onSelectQuoteHistory}
      />,
    );
    const table = screen.getByRole('table', { name: /latest quotes/i });
    // Click the first data row
    const dataRow = within(table).getAllByRole('row')[1];
    await userEvent.click(dataRow);
    expect(onSelectQuoteHistory).toHaveBeenCalledWith(10);
  });

  it('fires onCloseHistory when close button clicked in history dialog', async () => {
    const onCloseHistory = vi.fn();
    render(
      <InstrumentsMarketData
        {...defaultProps}
        quotes={[quote()]}
        quoteHistoryInstrumentId={10}
        quoteHistory={[]}
        onCloseHistory={onCloseHistory}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onCloseHistory).toHaveBeenCalledTimes(1);
  });
});

describe('quoteHistoryToCandles', () => {
  it('rolls quote rows into daily candle points', () => {
    const candles = quoteHistoryToCandles([
      quote({ id: 1, price: 10, price_type: 'close', as_of: '2026-06-01', source: 'akshare' }),
      quote({ id: 2, price: 8, price_type: 'open', as_of: '2026-06-01', source: 'akshare' }),
      quote({ id: 3, price: 12, price_type: 'high', as_of: '2026-06-01', source: 'akshare' }),
      quote({ id: 4, price: 7, price_type: 'low', as_of: '2026-06-01', source: 'legacy' }),
    ]);

    expect(candles).toEqual([
      {
        date: '2026-06-01',
        open: 8,
        high: 12,
        low: 7,
        close: 10,
        source: 'akshare, legacy',
        records: 4,
      },
    ]);
  });
});

// ---------------------------------------------------------------------------
// Fetch events sub-section
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — fetch events', () => {
  it('renders fetch events table with name, symbol, valuation_date, source', async () => {
    const p = profile({ name: 'CSI300', symbol: '000300', valuation_date: '2026-06-01', source: 'akshare' });
    render(<InstrumentsMarketData {...defaultProps} profiles={[p]} />);
    await userEvent.click(screen.getByRole('tab', { name: /fetch events/i }));
    const table = screen.getByRole('table', { name: /fetch events/i });
    expect(within(table).getByText('CSI300')).toBeInTheDocument();
    expect(within(table).getByText('000300')).toBeInTheDocument();
    expect(within(table).getByText('2026-06-01')).toBeInTheDocument();
    expect(within(table).getByText('akshare')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// FX rates sub-section
// Ported from MarketData.test.tsx:
//   [fx-1] renders FX rates table
//   [fx-2] AKShare fetch form submits base+quote
//   [fx-3] manual create form submits base/quote/rate/as_of/source
//   [fx-4] delete button fires onDeleteFxRate with the rate id
// ---------------------------------------------------------------------------

describe('InstrumentsMarketData — FX rates [fx-1]', () => {
  it('renders FX rates table with pair, rate, as_of, source', async () => {
    const rates = [
      fxRate({ id: 1, base_currency: 'USD', quote_currency: 'CNY', rate: 7.25, as_of_date: '2026-06-01', source: 'akshare' }),
    ];
    render(<InstrumentsMarketData {...defaultProps} fxRates={rates} />);
    await userEvent.click(screen.getByRole('tab', { name: /fx rates/i }));
    const table = screen.getByRole('table', { name: /fx rates/i });
    expect(within(table).getByText('USD/CNY')).toBeInTheDocument();
    expect(within(table).getByText(/7\.25/)).toBeInTheDocument();
    expect(within(table).getByText('2026-06-01')).toBeInTheDocument();
    expect(within(table).getByText('akshare')).toBeInTheDocument();
  });
});

describe('InstrumentsMarketData — FX rates [fx-2] AKShare fetch', () => {
  it('submits base+quote to onFetchFxRateAkshare', async () => {
    const onFetchFxRateAkshare = vi.fn(async () => {});
    render(<InstrumentsMarketData {...defaultProps} onFetchFxRateAkshare={onFetchFxRateAkshare} />);
    await userEvent.click(screen.getByRole('tab', { name: /fx rates/i }));

    await userEvent.clear(screen.getByLabelText('Base currency'));
    await userEvent.type(screen.getByLabelText('Base currency'), 'EUR');
    await userEvent.clear(screen.getByLabelText('Quote currency'));
    await userEvent.type(screen.getByLabelText('Quote currency'), 'USD');
    await userEvent.click(screen.getByRole('button', { name: /fetch from akshare/i }));

    expect(onFetchFxRateAkshare).toHaveBeenCalledWith('EUR', 'USD');
  });
});

describe('InstrumentsMarketData — FX rates [fx-3] manual create', () => {
  it('submits manual FX rate with base/quote/rate/as_of/source', async () => {
    const onCreateFxRate = vi.fn(async () => {});
    render(<InstrumentsMarketData {...defaultProps} onCreateFxRate={onCreateFxRate} />);
    await userEvent.click(screen.getByRole('tab', { name: /fx rates/i }));
    await userEvent.click(screen.getByRole('button', { name: /add manually/i }));

    const form = screen.getByRole('form', { name: /add fx rate/i });
    await userEvent.clear(within(form).getByLabelText('Base currency'));
    await userEvent.type(within(form).getByLabelText('Base currency'), 'GBP');
    await userEvent.clear(within(form).getByLabelText('Quote currency'));
    await userEvent.type(within(form).getByLabelText('Quote currency'), 'CNY');
    await userEvent.clear(within(form).getByLabelText('Rate'));
    await userEvent.type(within(form).getByLabelText('Rate'), '9.1');
    await userEvent.type(within(form).getByLabelText('As of date'), '2026-06-01');
    await userEvent.clear(within(form).getByLabelText('Source'));
    await userEvent.type(within(form).getByLabelText('Source'), 'manual');

    await userEvent.click(within(form).getByRole('button', { name: /^save$/i }));

    expect(onCreateFxRate).toHaveBeenCalledWith(expect.objectContaining({
      base_currency: 'GBP',
      quote_currency: 'CNY',
      rate: 9.1,
      as_of_date: '2026-06-01',
      source: 'manual',
    }));
  });
});

describe('InstrumentsMarketData — FX rates [fx-4] delete', () => {
  it('fires onDeleteFxRate with the rate id', async () => {
    const onDeleteFxRate = vi.fn(async () => {});
    const rates = [fxRate({ id: 42 })];
    render(<InstrumentsMarketData {...defaultProps} fxRates={rates} onDeleteFxRate={onDeleteFxRate} />);
    await userEvent.click(screen.getByRole('tab', { name: /fx rates/i }));
    await userEvent.click(screen.getByRole('button', { name: /delete USD\/CNY/i }));
    expect(onDeleteFxRate).toHaveBeenCalledWith(42);
  });
});

describe('composeRefreshSummary symbol cap', () => {
  it('caps long symbol lists at 5 with a +N more tail', () => {
    const skipped = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'];
    const out = composeRefreshSummary({
      synced_created: 0, synced_existing: 7, fetched: 0,
      skipped, failed: [],
    });
    expect(out).toContain('skipped 7: S1, S2, S3, S4, S5 +2 more');
    expect(out).not.toContain('S6');
  });
});

// ---------------------------------------------------------------------------
// Pure filter helpers
// ---------------------------------------------------------------------------

describe('filterQuotes', () => {
  const rows = [
    quote({ id: 1, symbol: 'IC2406.CFFEX', kind: 'futures', source: 'akshare' }),
    quote({ id: 2, symbol: '000300.SH', kind: 'index', source: 'manual' }),
  ];

  it('empty filters return all rows', () => {
    expect(filterQuotes(rows, EMPTY_QUOTE_FILTERS)).toHaveLength(2);
  });

  it('kind narrows to exact matches', () => {
    const out = filterQuotes(rows, { ...EMPTY_QUOTE_FILTERS, kind: 'index' });
    expect(out.map((r) => r.id)).toEqual([2]);
  });

  it('source narrows to exact matches', () => {
    const out = filterQuotes(rows, { ...EMPTY_QUOTE_FILTERS, source: 'akshare' });
    expect(out.map((r) => r.id)).toEqual([1]);
  });

  it('search matches symbol case-insensitively', () => {
    const out = filterQuotes(rows, { ...EMPTY_QUOTE_FILTERS, search: 'ic24' });
    expect(out.map((r) => r.id)).toEqual([1]);
  });
});

describe('filterProfiles', () => {
  const rows = [
    profile({ id: 1, name: 'CSI300', symbol: '000300', source: 'akshare' }),
    profile({ id: 2, name: 'Gold spot', symbol: 'AU9999.SGE', source: 'manual' }),
  ];

  it('source narrows to exact matches', () => {
    const out = filterProfiles(rows, { source: 'manual', search: '' });
    expect(out.map((r) => r.id)).toEqual([2]);
  });

  it('search matches name or symbol case-insensitively', () => {
    expect(filterProfiles(rows, { source: '', search: 'gold' }).map((r) => r.id)).toEqual([2]);
    expect(filterProfiles(rows, { source: '', search: '000300' }).map((r) => r.id)).toEqual([1]);
  });
});

describe('filterFxRates', () => {
  const rows = [
    fxRate({ id: 1, base_currency: 'USD', quote_currency: 'CNY', source: 'akshare' }),
    fxRate({ id: 2, base_currency: 'GBP', quote_currency: 'CNY', source: 'manual' }),
  ];

  it('empty search returns all rows', () => {
    expect(filterFxRates(rows, '')).toHaveLength(2);
  });

  it('matches the rendered pair string', () => {
    expect(filterFxRates(rows, 'usd/cny').map((r) => r.id)).toEqual([1]);
  });

  it('matches source', () => {
    expect(filterFxRates(rows, 'manual').map((r) => r.id)).toEqual([2]);
  });
});

// ---------------------------------------------------------------------------
// Quotes filters + pagination integration
// ---------------------------------------------------------------------------

describe('quotes filters and pagination', () => {
  it('selecting a kind hides non-matching rows', async () => {
    const user = userEvent.setup();
    render(
      <InstrumentsMarketData
        {...defaultProps}
        quotes={[
          quote({ id: 1, instrument_id: 1, symbol: 'IC2406.CFFEX', kind: 'futures' }),
          quote({ id: 2, instrument_id: 2, symbol: '000300.SH', kind: 'index' }),
        ]}
      />,
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: /filter quotes by kind/i }),
      'index',
    );
    expect(screen.getByText('000300.SH')).toBeInTheDocument();
    expect(screen.queryByText('IC2406.CFFEX')).not.toBeInTheDocument();
  });

  it('paginates quotes beyond 25 rows', () => {
    const quotes = Array.from({ length: 30 }, (_, i) =>
      quote({ id: i + 1, instrument_id: i + 1, symbol: `SYM${i + 1}` }),
    );
    render(<InstrumentsMarketData {...defaultProps} quotes={quotes} />);
    const table = screen.getByRole('table', { name: /latest quotes/i });
    expect(within(table).getAllByRole('row')).toHaveLength(26); // header + 25
    expect(screen.getByText('1-25 of 30')).toBeInTheDocument();
  });
});

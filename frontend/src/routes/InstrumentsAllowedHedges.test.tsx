// frontend/src/routes/InstrumentsAllowedHedges.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  InstrumentsAllowedHedges,
  quoteAgeBucket,
  EMPTY_CANDIDATE_FILTERS,
} from './InstrumentsAllowedHedges';
import type { HedgeMapGroup, HedgeCandidate, QuoteInfo } from './InstrumentsAllowedHedges';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function group(overrides: Partial<HedgeMapGroup> = {}): HedgeMapGroup {
  return {
    underlying_id: 10,
    underlying_symbol: '000905.SH',
    entries: [
      {
        id: 1,
        instrument_id: 201,
        exchange: 'CFFEX',
        contract_code: 'IC2406',
        family: 'index_future',
        series_root: 'IC',
        instrument_type: 'future',
        option_type: null,
        strike: null,
        expiry: '2026-06-21',
        reconcile_status: 'active',
      },
    ],
    open_position_count: 3,
    ...overrides,
  };
}

function candidate(overrides: Partial<HedgeCandidate> = {}): HedgeCandidate {
  return {
    id: 101,
    underlying_id: 10,
    family: 'index_future',
    series_root: 'IC',
    exchange: 'CFFEX',
    contract_code: 'IC2409',
    instrument_type: 'future',
    option_type: null,
    strike: null,
    expiry: '2026-09-19',
    multiplier: 200,
    last_price: null,
    status: 'active',
    allowed: false,
    ...overrides,
  };
}

const noopAsync = vi.fn(async () => {});

const defaultProps = {
  groups: [group()],
  selectedUnderlyingId: 10,
  onSelectUnderlying: vi.fn(),
  candidates: [candidate()],
  candidateFilters: EMPTY_CANDIDATE_FILTERS,
  onCandidateFiltersChange: vi.fn(),
  quotesByInstrumentId: {} as Record<number, QuoteInfo>,
  onMark: noopAsync,
  onUnmark: noopAsync,
  onPurgeStale: noopAsync,
};

// ---------------------------------------------------------------------------
// quoteAgeBucket pure unit tests
// ---------------------------------------------------------------------------

describe('quoteAgeBucket', () => {
  it('0 → ok', () => expect(quoteAgeBucket(0)).toBe('ok'));
  it('1 → ok', () => expect(quoteAgeBucket(1)).toBe('ok'));
  it('2 → stale', () => expect(quoteAgeBucket(2)).toBe('stale'));
  it('3 → stale', () => expect(quoteAgeBucket(3)).toBe('stale'));
  it('5 → stale', () => expect(quoteAgeBucket(5)).toBe('stale'));
  it('6 → warn', () => expect(quoteAgeBucket(6)).toBe('warn'));
  it('7 → warn', () => expect(quoteAgeBucket(7)).toBe('warn'));
});

// ---------------------------------------------------------------------------
// Rail rendering
// ---------------------------------------------------------------------------

describe('InstrumentsAllowedHedges — rail', () => {
  it('renders rail items with pos count and allowed count', () => {
    render(<InstrumentsAllowedHedges {...defaultProps} />);
    // "3 pos · 1 allowed"
    expect(screen.getByText(/3 pos/)).toBeInTheDocument();
    expect(screen.getByText(/1 allowed/)).toBeInTheDocument();
  });

  it('shows underlying_symbol as the rail label', () => {
    render(<InstrumentsAllowedHedges {...defaultProps} />);
    // underlying_symbol="000905.SH" from the group fixture
    expect(screen.getByText('000905.SH')).toBeInTheDocument();
  });

  it('falls back to underlying_id when underlying_symbol is absent', () => {
    const g = group({ underlying_symbol: undefined });
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        groups={[g]}
        selectedUnderlyingId={10}
      />,
    );
    // Falls back to numeric id
    expect(screen.getByText('10')).toBeInTheDocument();
  });

  it('adds warn class for 0-allowed group with positions', () => {
    const warnGroup = group({ entries: [], open_position_count: 2 });
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        groups={[warnGroup]}
        selectedUnderlyingId={null}
      />,
    );
    const btn = screen.getByRole('button', { pressed: false });
    expect(btn.className).toMatch(/warn/);
  });

  it('does NOT add warn class when allowed > 0', () => {
    render(<InstrumentsAllowedHedges {...defaultProps} />);
    const btn = screen.getByRole('button', { pressed: true });
    expect(btn.className).not.toMatch(/warn/);
  });

  it('selecting a rail item fires onSelectUnderlying', async () => {
    const user = userEvent.setup();
    const onSelectUnderlying = vi.fn();
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        onSelectUnderlying={onSelectUnderlying}
      />,
    );
    await user.click(screen.getByRole('button', { pressed: true }));
    expect(onSelectUnderlying).toHaveBeenCalledWith(10);
  });
});

// ---------------------------------------------------------------------------
// Map table
// ---------------------------------------------------------------------------

describe('InstrumentsAllowedHedges — map table', () => {
  it('renders map entry row with contract code', () => {
    render(<InstrumentsAllowedHedges {...defaultProps} />);
    expect(screen.getByText('IC2406')).toBeInTheDocument();
  });

  it('shows reconcile_status badge', () => {
    render(<InstrumentsAllowedHedges {...defaultProps} />);
    // "active" appears as the reconcile_status badge in the map row
    expect(screen.getAllByText('active').length).toBeGreaterThan(0);
  });

  it('renders quote with age badge when quotesByInstrumentId has data', () => {
    // Map entry has instrument_id=201; quote must be keyed by instrument_id
    const quotes: Record<number, QuoteInfo> = { 201: { price: 5600, age_days: 0.5 } };
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        quotesByInstrumentId={quotes}
      />,
    );
    // toLocaleString may add commas — match the price digit
    expect(screen.getByText(/5[,\s]?600/)).toBeInTheDocument();
    // age badge for 0.5d → "ok"
    const ageBadge = document.querySelector('.wl-ah__age-badge.is-ok');
    expect(ageBadge).not.toBeNull();
  });

  it('renders stale reconcile_status for stale entry', () => {
    const staleGroup = group({
      entries: [
        {
          id: 2,
          exchange: 'CFFEX',
          contract_code: 'IC2403',
          family: 'index_future',
          series_root: 'IC',
          instrument_type: 'future',
          option_type: null,
          strike: null,
          expiry: '2026-03-21',
          reconcile_status: 'stale',
        },
      ],
    });
    render(
      <InstrumentsAllowedHedges {...defaultProps} groups={[staleGroup]} />,
    );
    expect(screen.getByText('stale')).toBeInTheDocument();
    // Purge stale button should appear (1 stale entry)
    expect(screen.getByRole('button', { name: /purge stale/i })).toBeInTheDocument();
  });

  it('unmark fires onUnmark with entry id', async () => {
    const user = userEvent.setup();
    const onUnmark = vi.fn(async () => {});
    render(<InstrumentsAllowedHedges {...defaultProps} onUnmark={onUnmark} />);
    await user.click(screen.getByRole('button', { name: /unmark IC2406/i }));
    expect(onUnmark).toHaveBeenCalledWith([1]);
  });

  it('purge fires onPurgeStale with underlying_id', async () => {
    const user = userEvent.setup();
    const staleGroup = group({
      entries: [
        {
          id: 2,
          exchange: 'CFFEX',
          contract_code: 'IC2403',
          family: 'index_future',
          series_root: 'IC',
          instrument_type: 'future',
          option_type: null,
          strike: null,
          expiry: '2026-03-21',
          reconcile_status: 'stale',
        },
      ],
    });
    const onPurgeStale = vi.fn(async () => {});
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        groups={[staleGroup]}
        onPurgeStale={onPurgeStale}
      />,
    );
    await user.click(screen.getByRole('button', { name: /purge stale/i }));
    expect(onPurgeStale).toHaveBeenCalledWith(10);
  });
});

// ---------------------------------------------------------------------------
// Candidates table
// ---------------------------------------------------------------------------

describe('InstrumentsAllowedHedges — candidates', () => {
  it('renders candidate row', () => {
    render(<InstrumentsAllowedHedges {...defaultProps} />);
    expect(screen.getByText('IC2409')).toBeInTheDocument();
  });

  it('mark fires onMark with candidate id', async () => {
    const user = userEvent.setup();
    const onMark = vi.fn(async () => {});
    render(<InstrumentsAllowedHedges {...defaultProps} onMark={onMark} />);
    await user.click(screen.getByRole('button', { name: /mark IC2409/i }));
    expect(onMark).toHaveBeenCalledWith([101]);
  });

  it('already-allowed candidate shows "marked" label, no mark button', () => {
    const markedCandidate = candidate({ allowed: true });
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        candidates={[markedCandidate]}
      />,
    );
    expect(screen.getByText('marked')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mark IC2409/i })).toBeNull();
  });

  it('family filter change fires onCandidateFiltersChange', async () => {
    const user = userEvent.setup();
    const onCandidateFiltersChange = vi.fn();
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        onCandidateFiltersChange={onCandidateFiltersChange}
      />,
    );
    const familySelect = screen.getByRole('combobox', { name: /filter by family/i });
    await user.selectOptions(familySelect, 'index_future');
    expect(onCandidateFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ family: 'index_future' }),
    );
  });

  it('option type filter change fires onCandidateFiltersChange', async () => {
    const user = userEvent.setup();
    const onCandidateFiltersChange = vi.fn();
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        onCandidateFiltersChange={onCandidateFiltersChange}
      />,
    );
    const cpSelect = screen.getByRole('combobox', { name: /filter by option type/i });
    await user.selectOptions(cpSelect, 'C');
    expect(onCandidateFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ optionType: 'C' }),
    );
  });

  it('search input change fires onCandidateFiltersChange', async () => {
    const user = userEvent.setup();
    const onCandidateFiltersChange = vi.fn();
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        onCandidateFiltersChange={onCandidateFiltersChange}
      />,
    );
    const searchInput = screen.getByRole('searchbox', { name: /search candidates/i });
    await user.type(searchInput, 'IC');
    // Should have fired on each character
    expect(onCandidateFiltersChange).toHaveBeenCalled();
  });

  it('candidate quote with age 3d shows stale badge', () => {
    const quotes: Record<number, QuoteInfo> = { 101: { price: 5800, age_days: 3.2 } };
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        quotesByInstrumentId={quotes}
      />,
    );
    // age badge: 3d → stale
    const ageBadge = document.querySelector('.wl-ah__age-badge.is-stale');
    expect(ageBadge).not.toBeNull();
  });

  it('candidate quote with age 7d shows warn badge', () => {
    const quotes: Record<number, QuoteInfo> = { 101: { price: 5800, age_days: 7 } };
    render(
      <InstrumentsAllowedHedges
        {...defaultProps}
        quotesByInstrumentId={quotes}
      />,
    );
    const ageBadge = document.querySelector('.wl-ah__age-badge.is-warn');
    expect(ageBadge).not.toBeNull();
  });
});

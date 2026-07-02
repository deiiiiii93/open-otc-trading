import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Instruments, mappingMismatch } from './Instruments';
import type { Instrument } from './Instruments';
import { EMPTY_CANDIDATE_FILTERS } from './InstrumentsAllowedHedges';
import type { FxRate, MarketDataProfile, UnderlyingPricingDefault } from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function instrument(overrides: Partial<Instrument> = {}): Instrument {
  return {
    id: 1,
    symbol: 'IC2406.CFFEX',
    display_name: 'IC 2406',
    kind: 'futures',
    exchange: 'CFFEX',
    currency: 'CNY',
    status: 'active',
    source: 'hedge_loader',
    akshare_symbol: 'IC2406',
    akshare_asset_class: 'futures',
    contract_code: 'IC2406',
    series_root: 'IC',
    expiry: '2026-06-21',
    multiplier: 200,
    strike: null,
    option_type: null,
    parent_id: null,
    loaded_at: null,
    rate: null,
    dividend_yield: null,
    volatility: null,
    notes: null,
    tags: [],
    created_at: '2026-06-01T00:00:00',
    updated_at: '2026-06-01T00:00:00',
    ...overrides,
  };
}

const defaultProps = {
  rows: [instrument()],
  loading: false,
  error: null,
  feedback: null,
  syncing: false,
  loadInProgress: false,
  loadTaskChip: null,
  kindFilter: '',
  statusFilter: '',
  search: '',
  onKindFilterChange: vi.fn(),
  onStatusFilterChange: vi.fn(),
  onSearchChange: vi.fn(),
  onSync: vi.fn(async () => {}),
  onLoad: vi.fn(async () => {}),
  onSaveInstrument: vi.fn(async () => {}),
  onSetInstrumentTags: vi.fn(async () => {}),
  onCreateInstrument: vi.fn(async () => {}),
  activeTab: 'registry' as const,
  onTabChange: vi.fn(),
  rolesByInstrumentId: {},
  hedgeGroups: [],
  selectedHedgeUnderlyingId: null,
  onSelectHedgeUnderlying: vi.fn(),
  hedgeCandidates: [],
  hedgeCandidateFilters: EMPTY_CANDIDATE_FILTERS,
  onHedgeCandidateFiltersChange: vi.fn(),
  quotesByInstrumentId: {},
  onHedgeMark: vi.fn(async () => {}),
  onHedgeUnmark: vi.fn(async () => {}),
  onHedgePurgeStale: vi.fn(async () => {}),
  // Market Data tab props
  marketQuotes: [],
  marketQuotesLoading: false,
  marketQuoteHistory: [],
  marketQuoteHistoryInstrumentId: null,
  marketQuoteHistoryLoading: false,
  marketRefreshing: false,
  marketRefreshFeedback: null,
  marketProfiles: [] as MarketDataProfile[],
  marketProfilesLoading: false,
  fxRates: [] as FxRate[],
  fxRatesLoading: false,
  fxFeedback: null,
  fxFetching: false,
  onRefreshQuotes: vi.fn(async () => {}),
  onManualQuote: vi.fn(async () => {}),
  onSelectQuoteHistory: vi.fn(),
  onCloseQuoteHistory: vi.fn(),
  onCreateFxRate: vi.fn(async () => {}),
  onFetchFxRateAkshare: vi.fn(async () => {}),
  onDeleteFxRate: vi.fn(async () => {}),
  // Assumptions tab props
  assumptionDefaults: [] as UnderlyingPricingDefault[],
  assumptionUnderlyingRoleSymbols: [] as string[],
  assumptionSets: [],
  assumptionSelectedSetId: null,
  assumptionBuilding: false,
  assumptionRefreshing: false,
  assumptionBuildFeedback: null,
  assumptionBuildUnfilled: null,
  onAssumptionBuild: vi.fn(),
  onAssumptionSelectSet: vi.fn(),
  onAssumptionRefreshFromPositions: vi.fn(),
  onAssumptionUpsert: vi.fn(),
};

// ---------------------------------------------------------------------------
// mappingMismatch pure helper
// ---------------------------------------------------------------------------

describe('mappingMismatch', () => {
  it('returns false when both are futures', () => {
    expect(mappingMismatch('futures', 'futures')).toBe(false);
  });

  it('returns true when kind=futures but akshare=index', () => {
    expect(mappingMismatch('futures', 'index')).toBe(true);
  });

  it('returns false for listed_option regardless of akshare_asset_class', () => {
    expect(mappingMismatch('listed_option', null)).toBe(false);
    expect(mappingMismatch('listed_option', 'futures')).toBe(false);
  });

  it('returns false when akshare_asset_class is null for trackable kinds', () => {
    // No akshare mapping means we can't say there is a mismatch
    expect(mappingMismatch('futures', null)).toBe(false);
  });

  it('returns true for index kind vs futures akshare', () => {
    expect(mappingMismatch('index', 'futures')).toBe(true);
  });

  it('returns true for etf kind vs index akshare', () => {
    expect(mappingMismatch('etf', 'index')).toBe(true);
  });

  it('returns false for stock when akshare matches stock', () => {
    expect(mappingMismatch('stock', 'stock')).toBe(false);
  });

  it('returns false for sge_spot — no akshare mapping expected', () => {
    expect(mappingMismatch('sge_spot', 'futures')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Instruments component
// ---------------------------------------------------------------------------

describe('Instruments', () => {
  it('renders the page header and 4 tabs', () => {
    render(<Instruments {...defaultProps} />);
    expect(screen.getByText('Instruments')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /registry/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /allowed hedges/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /market data/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /assumptions/i })).toBeInTheDocument();
  });

  it('clicking a tab fires onTabChange', async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    render(<Instruments {...defaultProps} onTabChange={onTabChange} />);
    await user.click(screen.getByRole('tab', { name: /allowed hedges/i }));
    expect(onTabChange).toHaveBeenCalledWith('allowed-hedges');
  });

  it('market-data tab renders the InstrumentsMarketData component (not the placeholder)', () => {
    render(<Instruments {...defaultProps} activeTab="market-data" />);
    // The placeholder text is gone; the real component's sub-tab strip is present.
    expect(screen.queryByText(/coming in this branch/i)).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /quotes/i })).toBeInTheDocument();
  });

  it('allowed-hedges tab renders the AllowedHedges panel (not the placeholder)', () => {
    render(<Instruments {...defaultProps} activeTab="allowed-hedges" />);
    expect(screen.queryByText(/coming in this branch/i)).not.toBeInTheDocument();
  });

  it('assumptions tab renders the InstrumentsAssumptions component (not the placeholder)', () => {
    render(<Instruments {...defaultProps} activeTab="assumptions" />);
    expect(screen.queryByText(/coming in this branch/i)).not.toBeInTheDocument();
    // The build button is a key landmark of the assumptions tab
    expect(screen.getByRole('button', { name: /build assumptions/i })).toBeInTheDocument();
  });

  it('renders instrument rows with symbol, kind, and status', () => {
    render(<Instruments {...defaultProps} />);
    expect(screen.getByText('IC2406.CFFEX')).toBeInTheDocument();
    // kind column (use getAllByText since 'futures' appears in select option too)
    expect(screen.getAllByText('futures').length).toBeGreaterThan(0);
    // status column
    expect(screen.getAllByText('active').length).toBeGreaterThan(0);
  });

  it('renders TERMS cell for a contract with expiry + multiplier', () => {
    render(<Instruments {...defaultProps} />);
    // Should contain something like "exp 2026-06-21 ×200"
    expect(screen.getByText(/exp.*2026-06-21/i)).toBeInTheDocument();
  });

  it('renders TERMS cell as em-dash for a non-contract (no expiry)', () => {
    const noExpiry = instrument({ expiry: null, multiplier: null, contract_code: null });
    render(<Instruments {...defaultProps} rows={[noExpiry]} />);
    expect(screen.getByTestId('terms-cell-1')).toHaveTextContent('—');
  });

  it('renders AKSHARE cell warning badge for mismatched row', () => {
    const mismatched = instrument({ kind: 'futures', akshare_asset_class: 'index' });
    render(<Instruments {...defaultProps} rows={[mismatched]} />);
    expect(screen.getByText(/mapping\?/i)).toBeInTheDocument();
  });

  it('does not render AKSHARE warning badge when kinds match', () => {
    render(<Instruments {...defaultProps} />);
    // futures + futures = no warning
    expect(screen.queryByText(/mapping\?/i)).not.toBeInTheDocument();
  });

  it('draft row gets the draft modifier class', () => {
    const draftRow = instrument({ status: 'draft', id: 42 });
    render(<Instruments {...defaultProps} rows={[draftRow]} />);
    const row = screen.getByText('IC2406.CFFEX').closest('tr')!;
    expect(row.className).toMatch(/draft/);
  });

  it('sync button fires onSync callback', async () => {
    const user = userEvent.setup();
    const onSync = vi.fn(async () => {});
    render(<Instruments {...defaultProps} onSync={onSync} />);
    await user.click(screen.getByRole('button', { name: /sync/i }));
    expect(onSync).toHaveBeenCalled();
  });

  it('load contracts button fires onLoad callback', async () => {
    const user = userEvent.setup();
    const onLoad = vi.fn(async () => {});
    render(<Instruments {...defaultProps} onLoad={onLoad} />);
    await user.click(screen.getByRole('button', { name: /load contracts/i }));
    expect(onLoad).toHaveBeenCalled();
  });

  it('shows load task chip when loadTaskChip is set', () => {
    render(<Instruments {...defaultProps} loadTaskChip="task 7 · 3/10 · importing" />);
    expect(screen.getByText(/task 7/i)).toBeInTheDocument();
  });

  it('edit: submit passes ONLY the changed field', async () => {
    const user = userEvent.setup();
    const onSaveInstrument = vi.fn(async () => {});
    render(<Instruments {...defaultProps} onSaveInstrument={onSaveInstrument} />);

    // Open edit
    await user.click(screen.getByRole('button', { name: /edit IC2406.CFFEX/i }));

    // Change only notes
    const notesInput = screen.getByLabelText(/IC2406.CFFEX notes/i);
    await user.clear(notesInput);
    await user.type(notesInput, 'test note');

    // Submit
    await user.click(screen.getByRole('button', { name: /save IC2406.CFFEX/i }));

    await waitFor(() => {
      expect(onSaveInstrument).toHaveBeenCalled();
      const call = onSaveInstrument.mock.calls[0] as unknown as [number, Record<string, unknown>];
      const [, fields] = call;
      // Only the changed field should be present
      expect(Object.keys(fields)).toEqual(['notes']);
      expect(fields.notes).toBe('test note');
    });
  });

  it('renders ROLES badges from rolesByInstrumentId', () => {
    const roles = { 1: { underlying: true, hedge: false } };
    render(<Instruments {...defaultProps} rolesByInstrumentId={roles} />);
    expect(screen.getByText('underlying')).toBeInTheDocument();
  });

  it('renders hedge badge when rolesByInstrumentId marks hedge=true', () => {
    const roles = { 1: { underlying: false, hedge: true } };
    render(<Instruments {...defaultProps} rolesByInstrumentId={roles} />);
    expect(screen.getByText('hedge')).toBeInTheDocument();
  });

  it('renders a TAGS column with an editable tag list per row', async () => {
    const onSetInstrumentTags = vi.fn().mockResolvedValue(undefined);
    render(
      <Instruments
        {...defaultProps}
        rows={[instrument({ tags: ['desk-priority'] })]}
        onSetInstrumentTags={onSetInstrumentTags}
      />,
    );
    expect(screen.getByText('desk-priority')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText('Add tag...'), 'watchlist{enter}');
    expect(onSetInstrumentTags).toHaveBeenCalledWith(1, ['desk-priority', 'watchlist']);
  });

  it('new instrument button opens the create dialog', async () => {
    const user = userEvent.setup();
    render(<Instruments {...defaultProps} />);
    await user.click(screen.getByRole('button', { name: /new instrument/i }));
    expect(screen.getByRole('heading', { name: /new instrument/i })).toBeInTheDocument();
  });

  it('create dialog submits new instrument data', async () => {
    const user = userEvent.setup();
    const onCreateInstrument = vi.fn<(fields: Record<string, unknown>) => Promise<void>>(async () => {});
    render(<Instruments {...defaultProps} onCreateInstrument={onCreateInstrument} />);

    await user.click(screen.getByRole('button', { name: /new instrument/i }));
    await user.type(screen.getByLabelText(/^Symbol \*/i), '000300.SH');

    await user.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() => {
      expect(onCreateInstrument).toHaveBeenCalled();
      const payload = onCreateInstrument.mock.calls[0][0];
      expect(payload.symbol).toBe('000300.SH');
      expect(payload.kind).toBe('index');
    });
  });
});

/**
 * Tests for InstrumentsAssumptions — Assumptions tab on the Instruments page.
 *
 * Test coverage:
 *   [no-import]      No import button/control present anywhere in the tab
 *   [build-fires]    Build button fires onBuild callback
 *   [build-disabled] Build button disabled while building=true
 *   [unfilled-chip]  Unfilled count chip shown when rows have missing fields
 *   [row-class]      Rows with missing fields get wl-assumptions__row--unfilled class
 *   [build-400]      buildUnfilled renders as an inline alert list
 *   [set-selector]   Set selector renders options + fires onSelectSet
 *   [set-view]       Selected set view shows symbol/rate/q/vol/provenance rows
 *   [prov-default]   formatFieldProvenance returns 'default' for instrument_default
 *   [prov-inherited] formatFieldProvenance returns 'inherited · <id>' for inherited
 *   [prov-absent]    formatFieldProvenance returns '—' when no source
 *   [defaults-edit]  Edit mode: submits expected {rate,dividend_yield,volatility} body
 *   [defaults-rowstate-complete] defaultsRowState returns 'complete' when all filled
 *   [defaults-rowstate-unfilled] defaultsRowState returns 'unfilled' when any null
 *
 * Ported from UnderlyingDefaultsPanel.test.tsx (still-relevant behaviors):
 *   [udp-edit]       invokes onUpsert with edited values  (edit flow)
 *   [udp-refresh]    Refresh from positions button fires onRefreshFromPositions
 *   [udp-build-feedback] Build 400 unfilled list renders (unfilled alert)
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  InstrumentsAssumptions,
  defaultsRowState,
  fieldProvenance,
  formatFieldProvenance,
  filterDefaults,
  filterDefaultsByUnderlyingRole,
  filterSetRows,
  filterSetRowsByUnderlyingRole,
  EMPTY_DEFAULTS_FILTERS,
} from './InstrumentsAssumptions';
import type {
  InstrumentsAssumptionsProps,
  AssumptionSet,
  AssumptionRow,
} from './InstrumentsAssumptions';
import type { UnderlyingPricingDefault } from '../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeDefault(overrides: Partial<UnderlyingPricingDefault> = {}): UnderlyingPricingDefault {
  return {
    underlying: '000300.SH',
    rate: 0.025,
    dividend_yield: 0.02,
    volatility: 0.185,
    notes: null,
    is_complete: true,
    has_open_position: false,
    latest_akshare_close: null,
    created_at: '2026-06-01T00:00:00',
    updated_at: '2026-06-01T00:00:00',
    ...overrides,
  };
}

function makeAssumptionRow(overrides: Partial<AssumptionRow> = {}): AssumptionRow {
  return {
    id: 1,
    instrument_id: 10,
    symbol: '000300.SH',
    rate: 0.025,
    dividend_yield: 0.02,
    volatility: 0.185,
    source_payload: {
      source: 'instrument_default',
      instrument_id: 10,
      manual_input_sources: {
        rate: 'instrument_default',
        dividend_yield: 'instrument_default',
        volatility: 'instrument_default',
      },
    },
    ...overrides,
  };
}

function makeSet(overrides: Partial<AssumptionSet> = {}): AssumptionSet {
  return {
    id: 1,
    name: 'Assumptions 2026-06-01 10:00',
    valuation_date: '2026-06-01T10:00:00',
    status: 'completed',
    summary: { row_count: 1 },
    created_at: '2026-06-01T10:00:00',
    rows: [makeAssumptionRow()],
    ...overrides,
  };
}

const completeDefault = makeDefault();
const unfilledDefault = makeDefault({
  underlying: '000852.SH',
  rate: null,
  dividend_yield: null,
  volatility: null,
  is_complete: false,
});

const defaultBaseProps: InstrumentsAssumptionsProps = {
  defaults: [completeDefault],
  underlyingRoleSymbols: ['000300.SH', '000852.SH'],
  sets: [],
  selectedSetId: null,
  building: false,
  refreshing: false,
  buildFeedback: null,
  buildUnfilled: null,
  onBuild: vi.fn(),
  onSelectSet: vi.fn(),
  onRefreshFromPositions: vi.fn(),
  onUpsert: vi.fn(),
  onCurveUpsert: vi.fn(),
  onGenerateFromCurves: vi.fn(),
};

// ---------------------------------------------------------------------------
// defaultsRowState pure helper
// ---------------------------------------------------------------------------

describe('defaultsRowState', () => {
  it('[defaults-rowstate-complete] returns complete when all fields non-null', () => {
    expect(defaultsRowState({ rate: 0.025, dividend_yield: 0.02, volatility: 0.185 })).toBe('complete');
  });

  it('[defaults-rowstate-unfilled] returns unfilled when rate is null', () => {
    expect(defaultsRowState({ rate: null, dividend_yield: 0.02, volatility: 0.185 })).toBe('unfilled');
  });

  it('[defaults-rowstate-unfilled] returns unfilled when dividend_yield is null', () => {
    expect(defaultsRowState({ rate: 0.025, dividend_yield: null, volatility: 0.185 })).toBe('unfilled');
  });

  it('[defaults-rowstate-unfilled] returns unfilled when volatility is null', () => {
    expect(defaultsRowState({ rate: 0.025, dividend_yield: 0.02, volatility: null })).toBe('unfilled');
  });

  it('[defaults-rowstate-unfilled] returns unfilled when all fields null', () => {
    expect(defaultsRowState({ rate: null, dividend_yield: null, volatility: null })).toBe('unfilled');
  });
});

// ---------------------------------------------------------------------------
// fieldProvenance pure helper
// ---------------------------------------------------------------------------

describe('fieldProvenance', () => {
  it('returns default when value is non-null', () => {
    expect(fieldProvenance(0.025)).toBe('default');
    expect(fieldProvenance(0)).toBe('default');
  });

  it('returns missing when value is null', () => {
    expect(fieldProvenance(null)).toBe('missing');
  });
});

// ---------------------------------------------------------------------------
// formatFieldProvenance pure helper
// ---------------------------------------------------------------------------

describe('formatFieldProvenance', () => {
  it('[prov-default] returns "default" for instrument_default source', () => {
    const row = makeAssumptionRow();
    expect(formatFieldProvenance('rate', row)).toBe('default');
  });

  it('[prov-inherited] returns "inherited · <trade_id>" when source is inherited_pricing_parameter_row and trade_id present', () => {
    const row = makeAssumptionRow({
      source_payload: {
        manual_input_sources: { rate: 'inherited_pricing_parameter_row' },
        inherited_source_trade_id: 'TRD-001',
      },
    });
    expect(formatFieldProvenance('rate', row)).toBe('inherited · TRD-001');
  });

  it('[prov-inherited] returns "inherited" when source is inherited_pricing_parameter_row but no trade_id', () => {
    const row = makeAssumptionRow({
      source_payload: {
        manual_input_sources: { rate: 'inherited_pricing_parameter_row' },
        inherited_source_trade_id: null,
      },
    });
    expect(formatFieldProvenance('rate', row)).toBe('inherited');
  });

  it('[prov-absent] returns "—" when source_payload is null', () => {
    const row = makeAssumptionRow({ source_payload: null });
    expect(formatFieldProvenance('rate', row)).toBe('—');
  });

  it('[prov-absent] returns "—" when field not in manual_input_sources', () => {
    const row = makeAssumptionRow({
      source_payload: { manual_input_sources: {} },
    });
    expect(formatFieldProvenance('rate', row)).toBe('—');
  });
});

// ---------------------------------------------------------------------------
// InstrumentsAssumptions component
// ---------------------------------------------------------------------------

describe('InstrumentsAssumptions', () => {
  // [no-import] ─────────────────────────────────────────────────────────────

  it('[no-import] has no import button or import text anywhere in the tab', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} />);
    // No button or any element with text matching "import" (case-insensitive)
    expect(screen.queryByRole('button', { name: /import/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/import/i)).not.toBeInTheDocument();
  });

  // [build-fires] ────────────────────────────────────────────────────────────

  it('[build-fires] Build assumptions button fires onBuild', async () => {
    const onBuild = vi.fn();
    const user = userEvent.setup();
    render(<InstrumentsAssumptions {...defaultBaseProps} onBuild={onBuild} />);
    await user.click(screen.getByRole('button', { name: /build assumptions/i }));
    expect(onBuild).toHaveBeenCalledOnce();
  });

  // [build-disabled] ─────────────────────────────────────────────────────────

  it('[build-disabled] Build button is disabled while building=true', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} building={true} />);
    const btn = screen.getByRole('button', { name: /build assumptions/i });
    expect(btn).toBeDisabled();
  });

  // [unfilled-chip] ──────────────────────────────────────────────────────────

  it('[unfilled-chip] shows unfilled chip count when defaults have missing fields', () => {
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault, unfilledDefault]}
      />,
    );
    // The chip shows "1 unfilled" (only unfilledDefault is unfilled)
    expect(screen.getByText(/1 unfilled/i)).toBeInTheDocument();
  });

  it('[unfilled-chip] ignores unfilled defaults without the UNDERLYING role', () => {
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault, unfilledDefault]}
        underlyingRoleSymbols={['000300.SH']}
      />,
    );
    expect(screen.queryByText(/\d+ unfilled/i)).not.toBeInTheDocument();
  });

  it('[unfilled-chip] does NOT show unfilled chip when all defaults are complete', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} defaults={[completeDefault]} />);
    // Match the chip's "N unfilled" text specifically — the STATE filter select
    // legitimately contains an "unfilled" option.
    expect(screen.queryByText(/\d+ unfilled/i)).not.toBeInTheDocument();
  });

  // [row-class] ──────────────────────────────────────────────────────────────

  it('[row-class] unfilled row gets wl-assumptions__row--unfilled class', () => {
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[unfilledDefault]}
      />,
    );
    const row = screen.getByText('000852.SH').closest('tr')!;
    expect(row.className).toMatch(/wl-assumptions__row--unfilled/);
  });

  it('[row-class] complete row does NOT get the unfilled class', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} defaults={[completeDefault]} />);
    const row = screen.getByText('000300.SH').closest('tr')!;
    expect(row.className).not.toMatch(/wl-assumptions__row--unfilled/);
  });

  // [build-400] ──────────────────────────────────────────────────────────────

  it('[build-400] renders buildUnfilled list as an inline alert', () => {
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        buildUnfilled={['000852.SH', '000905.SH']}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(within(alert).getByText('000852.SH')).toBeInTheDocument();
    expect(within(alert).getByText('000905.SH')).toBeInTheDocument();
  });

  it('[build-400] renders no alert when buildUnfilled is null', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} buildUnfilled={null} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // [set-selector] ───────────────────────────────────────────────────────────

  it('[set-selector] renders set options when sets are present', () => {
    const set1 = makeSet({ id: 1, name: 'Assumptions 2026-06-01 10:00' });
    const set2 = makeSet({ id: 2, name: 'Assumptions 2026-06-02 11:00', valuation_date: '2026-06-02T11:00:00' });
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        sets={[set1, set2]}
        selectedSetId={null}
      />,
    );
    const select = screen.getByRole('combobox', { name: /select assumption set/i });
    expect(select).toBeInTheDocument();
    expect(within(select).getByText(/Assumptions 2026-06-01/i)).toBeInTheDocument();
    expect(within(select).getByText(/Assumptions 2026-06-02/i)).toBeInTheDocument();
  });

  it('[set-selector] fires onSelectSet with the selected id', async () => {
    const onSelectSet = vi.fn();
    const user = userEvent.setup();
    const set1 = makeSet({ id: 1 });
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        sets={[set1]}
        selectedSetId={null}
        onSelectSet={onSelectSet}
      />,
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: /select assumption set/i }),
      '1',
    );
    expect(onSelectSet).toHaveBeenCalledWith(1);
  });

  it('[set-selector] no set selector rendered when sets is empty', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} sets={[]} />);
    expect(screen.queryByRole('combobox', { name: /select assumption set/i })).not.toBeInTheDocument();
  });

  // [set-view] ───────────────────────────────────────────────────────────────

  it('[set-view] selected set view shows symbol, rate, q, vol and provenance', () => {
    const set = makeSet({ id: 1 });
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        sets={[set]}
        selectedSetId={1}
      />,
    );
    // The set view section specifically (it has the wl-assumptions__set-view class)
    const setView = document.querySelector('.wl-assumptions__set-view')!;
    expect(setView).toBeInTheDocument();
    // symbol in set-view table
    expect(within(setView as HTMLElement).getByText('000300.SH')).toBeInTheDocument();
    // rate value shown (0.025 → '0.0250')
    expect(within(setView as HTMLElement).getByText('0.0250')).toBeInTheDocument();
    // provenance string visible
    expect(within(setView as HTMLElement).getByText(/r: default/i)).toBeInTheDocument();
  });

  it('[set-view] inherited provenance shows "inherited · {trade_id}"', () => {
    const row = makeAssumptionRow({
      source_payload: {
        manual_input_sources: {
          rate: 'inherited_pricing_parameter_row',
          dividend_yield: 'instrument_default',
          volatility: 'instrument_default',
        },
        inherited_source_trade_id: 'TRD-999',
      },
    });
    const set = makeSet({ rows: [row] });
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        sets={[set]}
        selectedSetId={set.id}
      />,
    );
    expect(screen.getByText(/inherited · TRD-999/i)).toBeInTheDocument();
  });

  it('[set-view] hides selected set rows without the UNDERLYING role', () => {
    const set = makeSet({
      rows: [
        makeAssumptionRow({ id: 1, symbol: '000300.SH' }),
        makeAssumptionRow({ id: 2, symbol: 'AU9999.SGE' }),
      ],
    });
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        sets={[set]}
        selectedSetId={set.id}
        underlyingRoleSymbols={['AU9999.SGE']}
      />,
    );
    expect(screen.getByText('AU9999.SGE')).toBeInTheDocument();
    expect(screen.queryByText('000300.SH')).not.toBeInTheDocument();
  });

  it('[set-view] no set view shown when selectedSetId is null', () => {
    const set = makeSet({ id: 1 });
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        sets={[set]}
        selectedSetId={null}
      />,
    );
    // The set-view section should not render
    expect(screen.queryByText('Assumptions 2026-06-01 10:00')).not.toBeInTheDocument();
  });

  // [defaults-edit] ──────────────────────────────────────────────────────────

  it('[defaults-edit] edit defaults row submits expected {rate, dividend_yield, volatility}', () => {
    const onUpsert = vi.fn();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault]}
        onUpsert={onUpsert}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const rateInput = screen.getByLabelText(/^rate$/i) as HTMLInputElement;
    fireEvent.change(rateInput, { target: { value: '0.03' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    expect(onUpsert).toHaveBeenCalledWith(
      '000300.SH',
      expect.objectContaining({ rate: 0.03 }),
    );
    // Body must include all three fields
    const [, fields] = onUpsert.mock.calls[0] as [string, { rate: number | null; dividend_yield: number | null; volatility: number | null }];
    expect(Object.keys(fields).sort()).toEqual(['dividend_yield', 'rate', 'volatility'].sort());
  });

  it('[defaults-edit] empty rate input sends null for rate', () => {
    const onUpsert = vi.fn();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault]}
        onUpsert={onUpsert}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const rateInput = screen.getByLabelText(/^rate$/i) as HTMLInputElement;
    fireEvent.change(rateInput, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    const [, fields] = onUpsert.mock.calls[0] as [string, { rate: number | null }];
    expect(fields.rate).toBeNull();
  });

  // [curve-edit] ───────────────────────────────────────────────────────────────

  it('[curve-edit] adding a rate curve point submits {rate_curve}', () => {
    const onCurveUpsert = vi.fn();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault]}
        onCurveUpsert={onCurveUpsert}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /curves for/i }));
    fireEvent.change(screen.getByLabelText(/rate curve tenor/i), { target: { value: '3M' } });
    fireEvent.change(screen.getByLabelText(/rate curve value/i), { target: { value: '0.02' } });
    fireEvent.click(screen.getByRole('button', { name: /add rate point/i }));
    fireEvent.click(screen.getByRole('button', { name: /save curves/i }));
    expect(onCurveUpsert).toHaveBeenCalledWith(
      '000300.SH',
      expect.objectContaining({ rate_curve: [{ tenor: '3M', value: 0.02 }] }),
    );
  });

  it('[curve-generate] the generate button fires onGenerateFromCurves', () => {
    const onGenerateFromCurves = vi.fn();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault]}
        onGenerateFromCurves={onGenerateFromCurves}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: /generate pricing parameters from curves/i }),
    );
    expect(onGenerateFromCurves).toHaveBeenCalled();
  });

  // [udp-refresh] ────────────────────────────────────────────────────────────
  // Ported from UnderlyingDefaultsPanel: refresh button fires callback.

  it('[udp-refresh] Refresh from positions button fires onRefreshFromPositions', async () => {
    const onRefreshFromPositions = vi.fn();
    const user = userEvent.setup();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        onRefreshFromPositions={onRefreshFromPositions}
      />,
    );
    await user.click(screen.getByRole('button', { name: /refresh from positions/i }));
    expect(onRefreshFromPositions).toHaveBeenCalledOnce();
  });

  // [udp-build-feedback] ─────────────────────────────────────────────────────
  // Ported from UnderlyingDefaultsPanel: build success feedback shows.

  it('[udp-build-feedback] buildFeedback message renders with status role', () => {
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        buildFeedback="Assumptions set #3 built · 5 rows"
      />,
    );
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent(/Assumptions set #3 built/i);
  });

  // [udp-edit] ───────────────────────────────────────────────────────────────
  // Ported from UnderlyingDefaultsPanel: cancel edit restores read-only view.

  it('[udp-edit] cancel edit restores read-only view without calling onUpsert', () => {
    const onUpsert = vi.fn();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault]}
        onUpsert={onUpsert}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    // Now in edit mode — Cancel should restore
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onUpsert).not.toHaveBeenCalled();
    // Edit button should be back
    expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument();
  });

  // provenance hints in defaults grid
  it('defaults grid shows "default" provenance hint for non-null field', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} defaults={[completeDefault]} />);
    // Should have at least one "default" text in the grid (rate, q, vol all have values)
    const defaultHints = screen.getAllByText('default');
    expect(defaultHints.length).toBeGreaterThanOrEqual(1);
  });

  it('defaults grid shows "missing" provenance hint for null field', () => {
    render(<InstrumentsAssumptions {...defaultBaseProps} defaults={[unfilledDefault]} />);
    const missingHints = screen.getAllByText('missing');
    expect(missingHints.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Pure filter helpers
// ---------------------------------------------------------------------------

describe('filterDefaults', () => {
  const rows = [completeDefault, unfilledDefault];

  it('empty filters return all rows', () => {
    expect(filterDefaults(rows, EMPTY_DEFAULTS_FILTERS)).toHaveLength(2);
  });

  it("state 'unfilled' keeps only unfilled rows", () => {
    const out = filterDefaults(rows, { ...EMPTY_DEFAULTS_FILTERS, state: 'unfilled' });
    expect(out.map((r) => r.underlying)).toEqual(['000852.SH']);
  });

  it("state 'complete' keeps only complete rows", () => {
    const out = filterDefaults(rows, { ...EMPTY_DEFAULTS_FILTERS, state: 'complete' });
    expect(out.map((r) => r.underlying)).toEqual(['000300.SH']);
  });

  it('search matches underlying case-insensitively', () => {
    const out = filterDefaults(rows, { ...EMPTY_DEFAULTS_FILTERS, search: '000852' });
    expect(out.map((r) => r.underlying)).toEqual(['000852.SH']);
  });

  it('does not apply underlying-role scope directly', () => {
    expect(filterDefaults(rows, EMPTY_DEFAULTS_FILTERS).map((r) => r.underlying)).toEqual([
      '000300.SH',
      '000852.SH',
    ]);
  });
});

describe('filterDefaultsByUnderlyingRole', () => {
  it('keeps only defaults whose symbol has the UNDERLYING role', () => {
    const scoped = [
      makeDefault({ underlying: 'OPEN.SH', has_open_position: true }),
      makeDefault({ underlying: 'IDLE.SH', has_open_position: false }),
    ];
    const out = filterDefaultsByUnderlyingRole(scoped, ['OPEN.SH']);
    expect(out.map((r) => r.underlying)).toEqual(['OPEN.SH']);
  });
});

describe('filterSetRows', () => {
  const rows = [
    makeAssumptionRow({ id: 1, symbol: '000300.SH' }),
    makeAssumptionRow({ id: 2, symbol: 'AU9999.SGE' }),
  ];

  it('empty search returns all rows', () => {
    expect(filterSetRows(rows, '')).toHaveLength(2);
  });

  it('search matches symbol case-insensitively', () => {
    expect(filterSetRows(rows, 'au99').map((r) => r.id)).toEqual([2]);
  });
});

describe('filterSetRowsByUnderlyingRole', () => {
  it('keeps only assumption rows whose symbol has the UNDERLYING role', () => {
    const rows = [
      makeAssumptionRow({ id: 1, symbol: '000300.SH' }),
      makeAssumptionRow({ id: 2, symbol: 'AU9999.SGE' }),
    ];
    expect(filterSetRowsByUnderlyingRole(rows, ['AU9999.SGE']).map((r) => r.id)).toEqual([2]);
  });
});

// ---------------------------------------------------------------------------
// Defaults grid filter integration
// ---------------------------------------------------------------------------

describe('defaults grid filters', () => {
  it('STATE=unfilled hides complete rows', async () => {
    const user = userEvent.setup();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault, unfilledDefault]}
      />,
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: /filter defaults by state/i }),
      'unfilled',
    );
    expect(screen.getByText('000852.SH')).toBeInTheDocument();
    expect(screen.queryByText('000300.SH')).not.toBeInTheDocument();
  });

  it('search narrows the grid to matching underlyings', async () => {
    const user = userEvent.setup();
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[completeDefault, unfilledDefault]}
      />,
    );
    await user.type(screen.getByRole('searchbox', { name: /search defaults/i }), '000300');
    expect(screen.getByText('000300.SH')).toBeInTheDocument();
    expect(screen.queryByText('000852.SH')).not.toBeInTheDocument();
  });

  it('hides defaults without the UNDERLYING role', () => {
    render(
      <InstrumentsAssumptions
        {...defaultBaseProps}
        defaults={[
          makeDefault({ underlying: 'OPEN.SH', has_open_position: true }),
          makeDefault({ underlying: 'IDLE.SH', has_open_position: false }),
        ]}
        underlyingRoleSymbols={['OPEN.SH']}
      />,
    );
    expect(screen.getByText('OPEN.SH')).toBeInTheDocument();
    expect(screen.queryByText('IDLE.SH')).not.toBeInTheDocument();
  });
});

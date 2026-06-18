import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Positions, type PositionRow } from './Positions';

const baseProps = {
  portfolios: [
    { id: 1, name: 'Desk-Q2', kind: 'container' as const },
    { id: 2, name: 'Snowballs', kind: 'view' as const },
  ],
  containerPortfolios: [
    { id: 1, name: 'Desk-Q2', kind: 'container' as const },
  ],
  selectedPortfolioId: 1,
  importPortfolioId: 1,
  portfolioName: 'Desk-Q2',
  portfolioKind: 'container' as const,
  nav: '1.25M',
  pnl: '+12.50K',
  pnlVariant: 'pos' as const,
  delta: '—',
  deltaVariant: 'default' as const,
  vega: '—',
  valuationDate: '2026-04-30',
  onSelectPortfolio: vi.fn(),
  onSelectImportPortfolio: vi.fn(),
  onRunPricing: vi.fn(),
  onPricePosition: vi.fn(),
  onImportPositions: vi.fn(),
  importingPositions: false,
  pricingPositionId: null,
  importFeedback: null,
  editingPositionId: null,
  addingLifecycleEvent: false,
};

const positionRows: PositionRow[] = [
  {
    id: 42,
    trade_id: 'T-VANILLA',
    product_id: 88,
    product: {
      id: 88,
      asset_class: 'equity',
      product_family: 'option',
      quantark_class: 'EuropeanVanillaOption',
      underlying: '000852.SH',
      currency: 'USD',
      terms: { strike: 100, option_type: 'CALL' },
    },
    underlying: '000852.SH',
    product_type: 'EuropeanVanillaOption',
    quantity: -1,
    entry_price: 0,
    currency: 'CNY',
    status: 'open',
    position_kind: 'otc',
    mapping_status: 'supported',
    price: null,
    market_value: null,
    pnl: null,
    delta: null,
    gamma: null,
    vega: null,
    theta: null,
    rho: null,
    rho_q: null,
    pricing_error: null,
    product_kwargs: {
      strike: 100,
      option_type: 'CALL',
      barrier_config: {
        ko_barrier: [100, 99],
        ko_rate: [0.08, 0.08],
        ko_observation_schedule: {
          records: [
            { observation_date: '2026-06-01', barrier: 100, return_rate: 0.08 },
            { observation_date: '2026-07-01', barrier: 99, return_rate: 0.08 },
          ],
        },
      },
      payoff_config: { rebate_rate: 0.08 },
      accrual_config: { coupon_pay_type: 'INSTANT', is_annualized: true },
      external_note: 'legacy import',
    },
    market_inputs: { spot: 101.5, volatility: 0.22 },
    engine_name: 'BlackScholesEngine',
  },
];

const futuresHedgeRow: PositionRow = {
  ...positionRows[0],
  id: 113,
  trade_id: 'HEDGE:33:1',
  underlying: 'IF2606.CFFEX',
  product_type: 'Futures',
  position_kind: 'listed',
  source_payload: {
    hedge: {
      is_hedge: true,
      hedged_underlying: '000300.SH',
      contract_code: 'IF2606',
    },
  },
  product: {
    ...positionRows[0].product!,
    product_family: 'futures',
    quantark_class: 'Futures',
    underlying: 'IF2606.CFFEX',
    terms: { underlying: 'IF2606.CFFEX', maturity: 0.04, multiplier: 300 },
  },
  product_kwargs: { underlying: 'IF2606.CFFEX', maturity: 0.04, multiplier: 300 },
  engine_name: 'DeltaOneEngine',
};

function sectionByHeading(container: HTMLElement, heading: string) {
  const headingNode = within(container).getByRole('heading', { name: heading });
  const section = headingNode.closest('section');
  expect(section).not.toBeNull();
  return section as HTMLElement;
}

function makeRow(id: number): PositionRow {
  return {
    ...positionRows[0],
    id,
    trade_id: `T-${id}`,
    underlying: id === 30 ? 'SEARCH-ME' : '000852.SH',
  };
}

function pricingProfileWithRows(rows: Array<Record<string, unknown>>) {
  return {
    id: 7,
    name: 'Profile',
    valuation_date: '2026-04-30T00:00:00',
    rows: rows.map((row, index) => ({
      id: 700 + index,
      profile_id: 7,
      source_trade_id: String(row.source_trade_id),
      symbol: String(row.symbol),
      spot: row.spot as number | null,
      rate: row.rate as number | null,
      dividend_yield: row.dividend_yield as number | null,
      volatility: row.volatility as number | null,
      source_row: (row.source_row as number | null | undefined) ?? null,
      source_payload: {},
      created_at: '2026-05-11T00:00:00',
      updated_at: '2026-05-11T00:00:00',
    })),
  };
}

async function openPricingTab() {
  await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
  const dialog = screen.getByRole('dialog', { name: /position detail/i });
  await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
  return dialog;
}

describe('Positions', () => {
  it('renders the CCY column for each row', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);
    expect(screen.getByText('CCY')).toBeInTheDocument();
    expect(screen.getAllByText('CNY').length).toBeGreaterThan(0);
  });

  it('renders the OTC/listed position kind tag for each row', async () => {
    render(
      <Positions
        {...baseProps}
        rows={[
          positionRows[0],
          { ...positionRows[0], id: 43, trade_id: 'T-LISTED', position_kind: 'listed' },
        ]}
      />,
    );
    expect(screen.getByText('KIND')).toBeInTheDocument();
    expect(screen.getByText('OTC')).toBeInTheDocument();
    expect(screen.getByText('LISTED')).toBeInTheDocument();
  });

  it('shows the position currency in the Contract Snapshot', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);
    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const snapshot = sectionByHeading(dialog, 'Contract Snapshot');
    expect(within(snapshot).getByText('Currency')).toBeInTheDocument();
    expect(within(snapshot).getByText('CNY')).toBeInTheDocument();
  });

  it('shows the hedged underlying in the Contract Snapshot', async () => {
    render(<Positions {...baseProps} rows={[futuresHedgeRow]} />);

    await userEvent.click(screen.getAllByText('HEDGE:33:1')[0]);

    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const snapshot = sectionByHeading(dialog, 'Contract Snapshot');
    expect(within(snapshot).getByText('Hedged Underlying')).toBeInTheDocument();
    expect(within(snapshot).getByText('000300.SH')).toBeInTheDocument();
  });

  it('renders portfolio positions before pricing results exist and opens row detail dialog', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);

    expect(screen.queryByText('BlackScholesEngine')).not.toBeInTheDocument();
    const tradeCells = screen.getAllByText('T-VANILLA');
    expect(tradeCells.length).toBeGreaterThan(0);
    await userEvent.click(tradeCells[0]);

    expect(screen.getByRole('dialog', { name: /position detail/i })).toBeInTheDocument();
    expect(screen.getAllByText('BlackScholesEngine').length).toBeGreaterThan(0);
  });

  it('extracts pricing parameters from a unique complete underlying profile row', async () => {
    render(
      <Positions
        {...baseProps}
        rows={positionRows}
        pricingProfiles={[pricingProfileWithRows([{
          source_trade_id: 'UNDERLYING-ONLY',
          symbol: '000852.SH',
          spot: 222.5,
          rate: 0.03,
          dividend_yield: 0.01,
          volatility: 0.44,
          source_row: 18,
        }])]}
        selectedPricingProfileId={7}
        resolvedParams={{
          spot: { value: 11965, source: 'market_quote', quote_source: 'akshare', age_days: 2 },
          rate: { value: 0.03, source: 'pricing_parameter_profile', profile_id: 7 },
          dividend_yield: { value: 0.01, source: 'pricing_parameter_profile', profile_id: 7 },
          volatility: { value: 0.44, source: 'pricing_parameter_profile', profile_id: 7 },
        }}
      />,
    );

    const dialog = await openPricingTab();

    expect(within(dialog).queryByText(/cannot extract pricing parameters/i)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/Underlying row 18/i)).toBeInTheDocument();
    // Ticket fields prefill from the server-resolved params: spot from the
    // quote store, r/q/vol from the profile resolution — never from the last
    // run's market_inputs snapshot.
    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(11965);
    expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(0.03);
    expect(within(dialog).getByLabelText('Pricing dividend yield')).toHaveValue(0.01);
    expect(within(dialog).getByLabelText('Pricing volatility')).toHaveValue(0.44);
  });

  it('prefills the pricing ticket from resolved params, ignoring stale last-run market inputs', async () => {
    render(
      <Positions
        {...baseProps}
        rows={[{ ...positionRows[0], market_inputs: { spot: 6048.224, rate: 0.025, dividend_yield: 0.01, volatility: 0.25 } }]}
        resolvedParams={{
          spot: { value: 11965, source: 'market_quote', quote_source: 'akshare', age_days: 2 },
          rate: { value: 0.014, source: 'assumption_set', assumption_set_id: 3 },
          dividend_yield: { value: 0.016, source: 'assumption_set', assumption_set_id: 3 },
          volatility: { value: 0.234, source: 'assumption_set', assumption_set_id: 3 },
        }}
      />,
    );

    const dialog = await openPricingTab();

    // The stale spot recorded by the last valuation run (an override echo)
    // must not leak into the ticket.
    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(11965);
    expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(0.014);
    expect(within(dialog).getByLabelText('Pricing dividend yield')).toHaveValue(0.016);
    expect(within(dialog).getByLabelText('Pricing volatility')).toHaveValue(0.234);
  });

  it('leaves ticket market fields blank when no resolved params are available', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);

    const dialog = await openPricingTab();

    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(null);
    expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(null);
    expect(within(dialog).getByLabelText('Pricing dividend yield')).toHaveValue(null);
    expect(within(dialog).getByLabelText('Pricing volatility')).toHaveValue(null);
  });

  it('shows the Pricing Params provenance block in the Pricing tab, not Details', async () => {
    render(
      <Positions
        {...baseProps}
        rows={positionRows}
        resolvedParams={{
          spot: { value: 11965, source: 'market_quote', quote_source: 'akshare', age_days: 2 },
          rate: { value: 0.014, source: 'assumption_set', assumption_set_id: 3 },
          dividend_yield: { value: 0.016, source: 'assumption_set', assumption_set_id: 3 },
          volatility: { value: 0.234, source: 'assumption_set', assumption_set_id: 3 },
        }}
      />,
    );

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    expect(within(dialog).queryByRole('heading', { name: 'Pricing Params' })).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
    const paramsSection = sectionByHeading(dialog, 'Pricing Params');
    expect(within(paramsSection).getByText(/quote · 2d · akshare/)).toBeInTheDocument();
  });

  it('shows a pricing extraction banner for ambiguous same-underlying profile rows', async () => {
    render(
      <Positions
        {...baseProps}
        rows={positionRows}
        pricingProfiles={[pricingProfileWithRows([
          {
            source_trade_id: 'ROW-1',
            symbol: '000852.SH',
            spot: 222.5,
            rate: 0.03,
            dividend_yield: 0.01,
            volatility: 0.44,
          },
          {
            source_trade_id: 'ROW-2',
            symbol: '000852.SH',
            spot: 223.5,
            rate: 0.03,
            dividend_yield: 0.01,
            volatility: 0.44,
          },
        ])]}
        selectedPricingProfileId={7}
      />,
    );

    const dialog = await openPricingTab();

    expect(within(dialog).getByRole('alert')).toHaveTextContent(/cannot extract pricing parameters/i);
    expect(within(dialog).getByText(/Ambiguous underlying rows/i)).toBeInTheDocument();
    // Without resolved params the field stays blank — never the last-run echo.
    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(null);
  });

  it('shows a pricing extraction banner for incomplete exact profile rows', async () => {
    render(
      <Positions
        {...baseProps}
        rows={positionRows}
        pricingProfiles={[pricingProfileWithRows([{
          source_trade_id: 'T-VANILLA',
          symbol: '000852.SH',
          spot: 222.5,
          rate: null,
          dividend_yield: 0.01,
          volatility: 0.44,
        }])]}
        selectedPricingProfileId={7}
      />,
    );

    const dialog = await openPricingTab();

    expect(within(dialog).getByRole('alert')).toHaveTextContent(/cannot extract pricing parameters/i);
    expect(within(dialog).getByText(/Incomplete pricing row/i)).toBeInTheDocument();
    // Without resolved params the field stays blank — never the last-run echo.
    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(null);
  });

  it('does not require pricing profile fields for DeltaOneEngine positions', async () => {
    render(
      <Positions
        {...baseProps}
        rows={[futuresHedgeRow]}
        pricingProfiles={[pricingProfileWithRows([{
          source_trade_id: 'HEDGE:33:1',
          symbol: 'IF2606.CFFEX',
          spot: 4913.8,
          rate: null,
          dividend_yield: null,
          volatility: null,
        }])]}
        selectedPricingProfileId={7}
        resolvedParams={{
          spot: { value: 4913.8, source: 'market_quote', quote_source: 'hedge_load', age_days: 5 },
          rate: { value: null, source: 'missing' },
          dividend_yield: { value: null, source: 'missing' },
          volatility: { value: null, source: 'missing' },
        }}
      />,
    );

    await userEvent.click(screen.getAllByText('HEDGE:33:1')[0]);
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));

    expect(within(dialog).queryByText(/Loaded profile/i)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/cannot extract pricing parameters/i)).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(4913.8);
    expect(within(dialog).queryByLabelText('Pricing rate')).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Pricing dividend yield')).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Pricing volatility')).not.toBeInTheDocument();
  });

  it('accepts product-backed rows and renders product identity in the detail area', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
    const dialog = screen.getByRole('dialog', { name: /position detail/i });

    expect(within(dialog).getByText('Product ID')).toBeInTheDocument();
    expect(within(dialog).getByText('88')).toBeInTheDocument();
    expect(within(dialog).getByText('Product Family')).toBeInTheDocument();
    expect(within(dialog).getByText('option')).toBeInTheDocument();
    expect(within(dialog).getByText('QuantArk Class')).toBeInTheDocument();
    expect(within(dialog).getAllByText('EuropeanVanillaOption').length).toBeGreaterThan(0);
  });

  it('reports the active position detail dialog as page context', async () => {
    const onPageContextChange = vi.fn();
    render(
      <Positions
        {...baseProps}
        rows={positionRows}
        onPageContextChange={onPageContextChange}
      />,
    );

    await waitFor(() => {
      expect(onPageContextChange).toHaveBeenCalledWith(expect.objectContaining({
        route: 'positions',
        entity_ids: expect.objectContaining({ portfolio_id: 1 }),
      }));
    });

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);

    await waitFor(() => {
      expect(onPageContextChange).toHaveBeenLastCalledWith(expect.objectContaining({
        title: 'Position Detail dialog',
        entity_ids: expect.objectContaining({
          portfolio_id: 1,
          position_id: 42,
          source_trade_id: 'T-VANILLA',
        }),
      }));
    });
  });

  it('renders product terms and market inputs as read-only form fields', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);

    expect(screen.getByText('Contract Terms')).toBeInTheDocument();
    expect(screen.getByText('Product Terms')).toBeInTheDocument();
    expect(screen.getByLabelText(/strike/i)).toHaveValue('100');
    expect(screen.getByLabelText(/option type/i)).toHaveValue('CALL');
    const productTerms = screen.getByRole('group', { name: /product terms/i });
    const extraFields = screen.getByRole('group', { name: /extra fields/i });
    expect(within(productTerms).queryByText(/extra fields/i)).not.toBeInTheDocument();
    expect(within(extraFields).getByLabelText(/external note/i)).toHaveValue('legacy import');

    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));

    expect(within(dialog).getByRole('group', { name: /market inputs/i })).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Spot', { selector: 'input[readonly]' })).toHaveValue('101.5');
    expect(within(dialog).getByLabelText('Volatility', { selector: 'input[readonly]' })).toHaveValue('0.22');
  });

  it('shows position Greeks as cards with the other position metrics', async () => {
    render(
      <Positions
        {...baseProps}
        rows={[{
          ...positionRows[0],
          market_value: 16486.4861,
          delta: -19.9914,
          gamma: 0.0023,
          vega: -7832.0893,
          theta: 74.3071,
          rho: 6313.0152,
          rho_q: -7458.2281,
        }]}
      />,
    );

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const metricCards = dialog.querySelector('.wl-positions__detail-kpis');
    expect(metricCards).not.toBeNull();
    expect(metricCards).toHaveClass('wl-positions__detail-kpis');

    expect(metricCards!.querySelectorAll('.wl-tile')).toHaveLength(13);
    expect(metricCards!.querySelectorAll('.wl-positions__greek-card')).toHaveLength(6);
    expect(within(dialog).getByText('Market Value')).toBeInTheDocument();
    expect(within(dialog).getByText('16486.49')).toBeInTheDocument();
    expect(within(dialog).getByText('-19.9914')).toBeInTheDocument();
    expect(within(dialog).getByText('-7458.2281')).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));

    expect(within(dialog).queryByText('Greeks')).not.toBeInTheDocument();
  });

  it('renders complex product configs as folded nested form groups', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);

    expect(screen.getByText('Barrier Config')).toBeInTheDocument();
    expect(screen.getByText('Payoff Config')).toBeInTheDocument();
    expect(screen.getByText('Accrual Config')).toBeInTheDocument();

    await userEvent.click(screen.getByText('Payoff Config'));
    expect(screen.getByLabelText(/rebate rate/i)).toHaveValue('0.08');

    await userEvent.click(screen.getByText('Accrual Config'));
    expect(screen.getByLabelText(/coupon pay type/i)).toHaveValue('INSTANT');
    expect(screen.getByLabelText(/is annualized/i)).toHaveValue('true');

    await userEvent.click(screen.getByText('Barrier Config'));
    const barrierConfig = screen.getByText('Barrier Config').closest('details');
    expect(barrierConfig).not.toBeNull();
    expect(within(barrierConfig as HTMLElement).queryByLabelText(/ko barrier values/i)).not.toBeInTheDocument();
    expect(within(barrierConfig as HTMLElement).queryByLabelText(/ko rate values/i)).not.toBeInTheDocument();

    const koSchedule = within(barrierConfig as HTMLElement).getByText('Ko Observation Schedule').closest('.wl-positions__schedule-field');
    expect(koSchedule).not.toBeNull();
    expect(within(koSchedule as HTMLElement).getByRole('columnheader', { name: /observation date/i })).toBeInTheDocument();
    expect(within(koSchedule as HTMLElement).getByRole('columnheader', { name: /barrier/i })).toBeInTheDocument();
    expect(within(koSchedule as HTMLElement).getByRole('columnheader', { name: /return rate/i })).toBeInTheDocument();
    expect(within(koSchedule as HTMLElement).getByText('2026-06-01')).toBeInTheDocument();
    expect(within(koSchedule as HTMLElement).getByText('100')).toBeInTheDocument();
    expect(within(koSchedule as HTMLElement).getAllByText('0.08')).toHaveLength(2);
  });

  it('opens detail for rows that arrive asynchronously', async () => {
    const { rerender } = render(<Positions {...baseProps} rows={[]} />);

    rerender(<Positions {...baseProps} rows={positionRows} />);

    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
    expect(screen.getAllByText('BlackScholesEngine').length).toBeGreaterThan(0);
  });

  it('filters positions by search text', async () => {
    render(<Positions {...baseProps} rows={[makeRow(1), makeRow(30)]} />);

    await userEvent.type(screen.getByPlaceholderText(/search positions/i), 'search-me');

    expect(screen.getByText('T-30')).toBeInTheDocument();
    expect(screen.queryByText('T-1')).not.toBeInTheDocument();
  });

  it('paginates position rows', async () => {
    const rows = Array.from({ length: 30 }, (_, index) => makeRow(index + 1));
    render(<Positions {...baseProps} rows={rows} />);

    expect(screen.getByText('T-1')).toBeInTheDocument();
    expect(screen.queryByText('T-30')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /next page/i }));

    expect(screen.getByText('T-30')).toBeInTheDocument();
    expect(screen.queryByText('T-1')).not.toBeInTheDocument();
  });

  it('passes the uploaded xlsx file to the import handler', async () => {
    const onImportPositions = vi.fn();
    render(<Positions {...baseProps} rows={positionRows} onImportPositions={onImportPositions} />);

    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    expect(screen.getByRole('dialog', { name: /import positions/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Import target portfolio')).toHaveValue('1');
    await userEvent.click(screen.getByRole('button', { name: /choose xlsx/i }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /import positions/i })).not.toBeInTheDocument();
    });

    const file = new File(['xlsx'], 'positions.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(screen.getByLabelText(/import positions xlsx/i), file);

    expect(onImportPositions).toHaveBeenCalledWith(file);
  });

  it('keeps import target selection in the import dialog and limits it to containers', async () => {
    const onSelectPortfolio = vi.fn();
    const onSelectImportPortfolio = vi.fn();
    render(
      <Positions
        {...baseProps}
        rows={positionRows}
        selectedPortfolioId={2}
        portfolioName="Snowballs"
        portfolioKind="view"
        onSelectPortfolio={onSelectPortfolio}
        onSelectImportPortfolio={onSelectImportPortfolio}
      />,
    );

    expect(screen.getByLabelText('Display portfolio')).toHaveValue('2');
    expect(screen.queryByLabelText('Import target portfolio')).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Display portfolio'), '1');
    expect(onSelectPortfolio).toHaveBeenCalledWith(1);

    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    expect(screen.getByLabelText('Import target portfolio')).toHaveValue('1');
    const importOptions = Array.from(
      (screen.getByLabelText('Import target portfolio') as HTMLSelectElement).options,
    ).map((option) => option.textContent);
    expect(importOptions).toEqual(['Desk-Q2']);
  });
});

describe('inline editing', () => {
  const containerProps = {
    ...baseProps,
    portfolioKind: 'container' as const,
    onEditPosition: vi.fn(),
  };

  const viewProps = {
    ...baseProps,
    portfolioKind: 'view' as const,
    onEditPosition: vi.fn(),
  };

  const editableRow: PositionRow = {
    id: 42,
    trade_id: 'T-SNOWBALL',
    underlying: 'CSI500',
    product_type: 'SnowballOption',
    quantity: -1,
    entry_price: 100,
    currency: 'CNY',
    status: 'open',
    position_kind: 'otc',
    mapping_status: 'supported',
    price: null,
    market_value: null,
    pnl: null,
    delta: null,
    gamma: null,
    vega: null,
    theta: null,
    rho: null,
    rho_q: null,
  };

  it('shows editable cells for container portfolio', () => {
    render(<Positions {...containerProps} rows={[editableRow]} />);
    const qtyCell = screen.getByText('-1');
    expect(qtyCell.className).toContain('wl-positions__editable-cell');
  });

  it('does not show editable cells for view portfolio', () => {
    render(<Positions {...viewProps} rows={[editableRow]} />);
    const qtyCell = screen.getByText('-1');
    expect(qtyCell.className).not.toContain('wl-positions__editable-cell');
  });

  it('calls onEditPosition when quantity is edited', async () => {
    const user = userEvent.setup();
    render(<Positions {...containerProps} rows={[editableRow]} />);
    const qtyCell = screen.getByText('-1');
    await user.click(qtyCell);
    const input = screen.getByDisplayValue('-1');
    await user.clear(input);
    await user.type(input, '2');
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(containerProps.onEditPosition).toHaveBeenCalled();
    });
  });

  it('does not show or submit engine kwargs from the detail edit form', async () => {
    const user = userEvent.setup();
    const onEditPosition = vi.fn();
    render(
      <Positions
        {...containerProps}
        onEditPosition={onEditPosition}
        rows={[{ ...editableRow, engine_kwargs: { params_type: 'quad_params' } }]}
      />,
    );

    await user.click(screen.getByText('T-SNOWBALL'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });

    expect(within(dialog).queryByText(/engine kwargs/i)).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(onEditPosition).toHaveBeenCalled());
    expect(onEditPosition.mock.calls[0][1]).not.toHaveProperty('engine_kwargs');
  });

  it('shows extra fields as a separate form in the detail edit form', async () => {
    const user = userEvent.setup();
    render(
      <Positions
        {...containerProps}
        rows={[{
          ...editableRow,
          product_kwargs: {
            strike: 100,
            initial_price: 100,
            external_note: 'legacy import',
          },
        }]}
      />,
    );

    await user.click(screen.getByText('T-SNOWBALL'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const editSection = sectionByHeading(dialog, 'Edit Position');
    const productTerms = within(editSection).getByRole('group', { name: /product terms/i });
    const extraFields = within(editSection).getByRole('group', { name: /extra fields/i });

    expect(within(productTerms).queryByText(/extra fields/i)).not.toBeInTheDocument();
    expect(within(extraFields).getByLabelText(/external note/i)).toHaveValue('legacy import');
  });

  it('does not duplicate readonly contract terms while editing container positions', async () => {
    const user = userEvent.setup();
    render(
      <Positions
        {...containerProps}
        rows={[{
          ...editableRow,
          product_kwargs: {
            strike: 100,
            initial_price: 100,
            external_note: 'legacy import',
          },
        }]}
      />,
    );

    await user.click(screen.getByText('T-SNOWBALL'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const editSection = sectionByHeading(dialog, 'Edit Position');

    expect(within(dialog).getByText('Edit Position')).toBeInTheDocument();
    expect(within(dialog).queryByText('Contract Terms')).not.toBeInTheDocument();
    expect(within(editSection).getByRole('group', { name: /product terms/i })).toBeInTheDocument();
    expect(within(editSection).getByRole('group', { name: /extra fields/i })).toBeInTheDocument();
  });

  it('renders product term booleans with the standard field layout', async () => {
    const user = userEvent.setup();
    render(
      <Positions
        {...containerProps}
        rows={[{
          ...editableRow,
          product_kwargs: {
            strike: 100,
            initial_price: 100,
            is_reverse: false,
            _otc_lifecycle_knocked_in: true,
          },
        }]}
      />,
    );

    await user.click(screen.getByText('T-SNOWBALL'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const editSection = sectionByHeading(dialog, 'Edit Position');
    const isReverse = within(editSection).getByLabelText(/is reverse/i);
    const knockedIn = within(editSection).getByLabelText(/lifecycle knocked in/i);

    expect(isReverse.closest('.wl-positions__term-field')).toBeInTheDocument();
    expect(knockedIn.closest('.wl-positions__term-field')).toBeInTheDocument();
    expect(isReverse.parentElement).toHaveClass('wl-positions__term-boolean-control');
    expect(knockedIn.parentElement).toHaveClass('wl-positions__term-boolean-control');
  });
});

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DEFAULT_TRY_SOLVE_CATALOG, DEFAULT_TRY_SOLVE_ROWS, TrySolve } from './TrySolve';
import type { TrySolveRowOut } from '../types';

describe('TrySolve', () => {
  it('renders top-right header actions for importing Excel rows and exporting', () => {
    render(<TrySolve />);

    expect(screen.getByRole('heading', { name: 'TRY TO SOLVE' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /import excel row/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument();
  });

  it('fallback product library includes all workbook product labels', () => {
    render(<TrySolve />);

    expect(DEFAULT_TRY_SOLVE_CATALOG.products).toHaveLength(19);
    expect(screen.getByRole('option', { name: 'Autocall · autocall' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Phoenix · phoenix' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Knock-Out Autocall · knock_out_autocall' })).toBeInTheDocument();
  });

  it('shows mixed-product queue rows and notifies row selection', async () => {
    const onSelectRow = vi.fn();
    render(<TrySolve onSelectRow={onSelectRow} />);

    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).getByRole('button', { name: /xl-12 autocall/i })).toBeInTheDocument();
    expect(within(queue).getByRole('button', { name: /xl-18 vanilla/i })).toBeInTheDocument();
    expect(within(queue).getByRole('button', { name: /xl-31 digital/i })).toBeInTheDocument();

    await userEvent.click(within(queue).getByRole('button', { name: /xl-18 vanilla/i }));

    expect(onSelectRow).toHaveBeenCalledWith('XL-18');
    expect(within(queue).getByRole('button', { name: /xl-18 vanilla/i })).toHaveAttribute('aria-pressed', 'true');
  });

  it('filters request queue rows by search text', async () => {
    render(<TrySolve />);

    await userEvent.type(screen.getByLabelText(/search request queue/i), 'vanilla');

    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).getByRole('button', { name: /xl-18 vanilla/i })).toBeInTheDocument();
    expect(within(queue).queryByRole('button', { name: /xl-12 autocall/i })).toBeNull();
  });

  it('exposes a draggable workspace panel resizer', () => {
    const { container } = render(<TrySolve />);
    const resizer = screen.getByRole('button', { name: /resize terms and quote panels/i });

    fireEvent.pointerDown(resizer, { clientX: 620 });
    fireEvent.pointerMove(window, { clientX: 720 });
    fireEvent.pointerUp(window);

    const workspace = container.querySelector('.wl-try-solve__workspace') as HTMLElement;
    expect(workspace.getAttribute('style')).toContain('--try-solve-editor-panel');
  });

  it('changes product-specific fields and quote fields when selecting different rows', async () => {
    render(<TrySolve />);

    expect(screen.getByRole('form', { name: /autocall field editor/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Knock-Out Barrier')).toHaveValue(1.03);
    expect(screen.getByLabelText('Quote Field')).toHaveDisplayValue('Annualized Coupon');

    await userEvent.click(screen.getByRole('button', { name: /xl-18 vanilla/i }));

    expect(screen.getByRole('form', { name: /vanilla field editor/i })).toBeInTheDocument();
    expect(screen.getByLabelText('Option Type')).toHaveValue('call');
    expect(screen.getByLabelText('Strike')).toHaveValue(1.02);
    expect(screen.getByLabelText('Quote Field')).toHaveDisplayValue('Strike');
  });

  it('marks the terms field selected as the quote field', async () => {
    render(<TrySolve />);

    await userEvent.click(screen.getByRole('button', { name: /xl-18 vanilla/i }));

    expect(screen.getByLabelText('Strike').closest('.wl-try-solve__field')).toHaveAttribute('data-quote-field', 'true');
    expect(screen.getByLabelText('Option Type').closest('.wl-try-solve__field')).not.toHaveAttribute('data-quote-field');
  });

  it('updates the marked terms field when the quote field changes', () => {
    const onQuoteRequestChange = vi.fn();
    const rows: TrySolveRowOut[] = [
      {
        ...DEFAULT_TRY_SOLVE_ROWS[0],
        quote_request: {
          ...DEFAULT_TRY_SOLVE_ROWS[0].quote_request,
          quote_field_key: 'ko_barrier',
        },
      },
    ];
    render(<TrySolve rows={rows} onQuoteRequestChange={onQuoteRequestChange} />);

    expect(screen.getByLabelText('Knock-Out Barrier').closest('.wl-try-solve__field')).toHaveAttribute('data-quote-field', 'true');
    expect(screen.getByLabelText('Knock-In Barrier').closest('.wl-try-solve__field')).not.toHaveAttribute('data-quote-field');
  });

  it('renders schema-captured and solver-pending status honestly', async () => {
    const onPageContextChange = vi.fn();
    render(<TrySolve onPageContextChange={onPageContextChange} />);

    await userEvent.click(screen.getByRole('button', { name: /xl-31 digital/i }));

    expect(screen.getAllByText(/schema captured/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/solver pending/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/solver mapping is pending/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /solve selected/i })).toBeDisabled();
    await waitFor(() => {
      expect(onPageContextChange).toHaveBeenLastCalledWith(expect.objectContaining({
        route: 'try-solve',
        entity_ids: expect.objectContaining({ row_id: 'XL-31' }),
      }));
    });
  });

  it('fires live-route callbacks for field edits, quote edits, export, and solve actions', async () => {
    const onFieldChange = vi.fn();
    const onMarketChange = vi.fn();
    const onQuoteRequestChange = vi.fn();
    const onExport = vi.fn();
    const onSolveSelected = vi.fn();
    const onSolveAll = vi.fn();
    render(
      <TrySolve
        onFieldChange={onFieldChange}
        onMarketChange={onMarketChange}
        onQuoteRequestChange={onQuoteRequestChange}
        onExport={onExport}
        onSolveSelected={onSolveSelected}
        onSolveAll={onSolveAll}
      />,
    );

    fireEvent.change(screen.getByLabelText('Knock-Out Barrier'), { target: { value: '1.08' } });
    expect(onFieldChange).toHaveBeenCalledWith('XL-12', 'ko_barrier', 1.08);

    fireEvent.change(screen.getByLabelText('Dividend Yield'), { target: { value: '0.012' } });
    expect(onMarketChange).toHaveBeenCalledWith('XL-12', { dividend_yield: 0.012 });

    fireEvent.change(screen.getByLabelText('Quote Field'), { target: { value: 'ko_barrier' } });
    expect(onQuoteRequestChange).toHaveBeenCalledWith(
      'XL-12',
      expect.objectContaining({ quote_field_key: 'ko_barrier' }),
    );

    fireEvent.change(screen.getByLabelText('Target Label'), { target: { value: 'premium %' } });
    expect(onQuoteRequestChange).toHaveBeenCalledWith('XL-12', { target_label: 'premium %' });

    fireEvent.change(screen.getByLabelText('Target Value'), { target: { value: '0.25' } });
    expect(onQuoteRequestChange).toHaveBeenCalledWith('XL-12', { target_value: 0.25 });

    await userEvent.click(screen.getByRole('button', { name: /^export$/i }));
    expect(onExport).toHaveBeenCalledWith('selected', ['XL-12']);

    await userEvent.click(screen.getByRole('button', { name: /^solve selected$/i }));
    expect(onSolveSelected).toHaveBeenCalledWith('XL-12');

    await userEvent.click(screen.getByRole('button', { name: /^solve all$/i }));
    expect(onSolveAll).toHaveBeenCalledWith(['XL-12', 'XL-18', 'XL-31']);
  });

  it('defaults annualized coupon search range to practical decimal-rate bounds', () => {
    const onQuoteRequestChange = vi.fn();
    const rows: TrySolveRowOut[] = [
      {
        ...DEFAULT_TRY_SOLVE_ROWS[0],
        quote_request: {
          ...DEFAULT_TRY_SOLVE_ROWS[0].quote_request,
          quote_field_key: 'ko_barrier',
        },
      },
    ];
    render(<TrySolve rows={rows} onQuoteRequestChange={onQuoteRequestChange} />);

    fireEvent.change(screen.getByLabelText('Quote Field'), { target: { value: 'annualized_coupon' } });

    expect(onQuoteRequestChange).toHaveBeenCalledWith('XL-12', {
      quote_field_key: 'annualized_coupon',
      lower_bound: 0.001,
      upper_bound: 0.5,
      initial_guess: 0.1,
    });
  });

  it('defaults strike search range from the row spot when strike is quoted', async () => {
    const onQuoteRequestChange = vi.fn();
    const rows: TrySolveRowOut[] = [
      {
        ...DEFAULT_TRY_SOLVE_ROWS[1],
        quote_request: {
          ...DEFAULT_TRY_SOLVE_ROWS[1].quote_request,
          quote_field_key: 'premium_rate',
        },
      },
    ];
    render(<TrySolve rows={rows} selectedRowId="XL-18" onQuoteRequestChange={onQuoteRequestChange} />);

    fireEvent.change(screen.getByLabelText('Quote Field'), { target: { value: 'strike' } });

    expect(onQuoteRequestChange).toHaveBeenCalledWith('XL-18', {
      quote_field_key: 'strike',
      lower_bound: 0.101,
      upper_bound: 2.02,
      initial_guess: 1.01,
    });
  });

  it('displays target value with percent sign when target label is premium %', () => {
    const rows: TrySolveRowOut[] = [
      {
        ...DEFAULT_TRY_SOLVE_ROWS[0],
        row_id: 'XL-12',
        quote_request: {
          ...DEFAULT_TRY_SOLVE_ROWS[0].quote_request,
          target_label: 'premium %',
          target_value: 0.25,
        },
      },
    ];
    render(<TrySolve rows={rows} />);

    const targetMetric = screen.getByText('Target');
    expect(targetMetric.closest('.wl-try-solve__metric')).not.toBeNull();
    expect(targetMetric.closest('.wl-try-solve__metric')).toHaveTextContent('0.25000%');

    const targetValueField = screen.getByLabelText('Target Value');
    expect(targetValueField.closest('.wl-try-solve__field')).toHaveTextContent('%');
  });

  it('fires import callback with the selected Excel file', async () => {
    const onImportExcel = vi.fn();
    render(<TrySolve onImportExcel={onImportExcel} />);

    const file = new File(['xlsx'], 'try-solve.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), file);

    expect(onImportExcel).toHaveBeenCalledWith(file);
  });

  it('renders profile selectors and emits selected market profile ids', () => {
    const onMarketChange = vi.fn();
    render(
      <TrySolve
        pricingProfiles={[
          {
            id: 11,
            name: 'Desk close',
            valuation_date: '2026-05-13T00:00:00',
            source_type: 'xlsx',
            status: 'completed',
            summary: { row_count: 2 },
            created_at: '2026-05-13T00:00:00',
            updated_at: '2026-05-13T00:00:00',
            rows: [],
          },
        ]}
        marketDataProfiles={[
          {
            id: 22,
            name: 'CSI300 daily',
            source: 'akshare',
            symbol: '000300.SH',
            asset_class: 'index',
            start_date: '2026-05-01',
            end_date: '2026-05-13',
            adjust: 'qfq',
            valuation_date: '2026-05-13T00:00:00',
            data: { spot: 108.5 },
            source_metadata: {},
            created_at: '2026-05-13T00:00:00',
            updated_at: '2026-05-13T00:00:00',
          },
        ]}
        onMarketChange={onMarketChange}
      />,
    );

    expect(screen.getByRole('option', { name: /desk close/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /no profiles for 000852\.sh/i })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Pricing Parameter Profile'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Market Data Profile'), { target: { value: '22' } });

    expect(onMarketChange).toHaveBeenCalledWith('XL-12', { pricing_parameter_profile_id: 11 });
    expect(onMarketChange).toHaveBeenCalledWith('XL-12', { market_data_profile_id: null });
  });

  it('exposes snowball schedule-override columns (frequency select + lockup)', () => {
    render(<TrySolve catalog={DEFAULT_TRY_SOLVE_CATALOG} rows={DEFAULT_TRY_SOLVE_ROWS} />);
    // the first row (xl-12 autocall) is selected by default
    expect(screen.getByRole('form', { name: /autocall field editor/i })).toBeInTheDocument();

    expect(screen.getByLabelText('End Date')).toHaveValue('2027-05-14');

    const freq = screen.getByLabelText('Observation Frequency');
    expect(freq.tagName).toBe('SELECT');
    expect(within(freq).getByRole('option', { name: 'MONTHLY' })).toBeInTheDocument();
    expect(within(freq).getByRole('option', { name: 'QUARTERLY' })).toBeInTheDocument();
    expect(within(freq).getByRole('option', { name: 'SEMI_ANNUAL' })).toBeInTheDocument();

    expect(screen.getByLabelText('Lockup Months')).toBeInTheDocument();
  });

  it('shows derived price levels for moneyness barrier inputs', () => {
    const rows: TrySolveRowOut[] = [
      {
        ...DEFAULT_TRY_SOLVE_ROWS[0],
        fields: {
          ...DEFAULT_TRY_SOLVE_ROWS[0].fields,
          initial_price: 8637.743,
          ko_barrier: 1.03,
          ki_barrier: 0.75,
        },
        market: {
          ...DEFAULT_TRY_SOLVE_ROWS[0].market,
          spot: 8637.743,
        },
      },
    ];

    render(<TrySolve rows={rows} />);

    expect(screen.getByText(/Level 8,896\.88 \(103% of 8,637\.74\)/)).toBeInTheDocument();
    expect(screen.getByText(/Level 6,478\.31 \(75% of 8,637\.74\)/)).toBeInTheDocument();
  });
});

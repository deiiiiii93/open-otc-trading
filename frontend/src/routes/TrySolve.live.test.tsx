import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TrySolveLive } from './TrySolve.live';
import type { Instrument, MarketDataProfile, PricingParameterProfile, TrySolveCatalog, TrySolveRowOut } from '../types';

const catalog: TrySolveCatalog = {
  products: [
    {
      product_key: 'autocall',
      label: 'Autocall',
      excel_sheet: 'autocall',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'SnowballOption',
      default_engine_name: 'SnowballQuadEngine',
      fields: [
        field('underlying', 'Underlying', 'text', true),
        field('initial_price', 'Initial Price', 'number'),
        field('ko_barrier', 'Knock-Out Barrier', 'number'),
      ],
      quote_fields: [
        quoteField('annualized_coupon', 'Annualized Coupon', 'barrier_config.ko_rate'),
        quoteField('ko_barrier', 'Knock-Out Barrier', 'barrier_config.ko_barrier'),
      ],
    },
    {
      product_key: 'vanilla',
      label: 'Vanilla',
      excel_sheet: 'vanilla',
      initial_solver_state: 'solver_ready',
      quantark_product_type: 'EuropeanVanillaOption',
      default_engine_name: 'BlackScholesEngine',
      fields: [
        field('underlying', 'Underlying', 'text', true),
        field('initial_price', 'Initial Price', 'number'),
        field('strike', 'Strike', 'number'),
      ],
      quote_fields: [
        quoteField('premium_rate', 'Premium Rate', 'premium_rate', false),
        quoteField('strike', 'Strike', 'strike'),
      ],
    },
  ],
  status_options: ['solver_ready', 'solved', 'solve_failed'],
};

const importedRows: TrySolveRowOut[] = [
  row({
    row_id: 'IM-1',
    product_key: 'autocall',
    product_label: 'Autocall',
    source_sheet: 'autocall',
    source_row: 2,
    fields: { underlying: '000852.SH', ko_barrier: 1.03 },
    quote_request: {
      quote_field_key: 'annualized_coupon',
      target_label: 'price',
      target_value: 0,
      lower_bound: -1,
      upper_bound: 2,
      initial_guess: 0.1,
    },
  }),
  row({
    row_id: 'IM-2',
    product_key: 'vanilla',
    product_label: 'Vanilla',
    source_sheet: 'vanilla',
    source_row: 4,
    fields: { underlying: '510050.SH', strike: 1.02 },
    quote_request: {
      quote_field_key: 'strike',
      target_label: 'price',
      target_value: 0,
      lower_bound: 0.01,
      upper_bound: 10,
      initial_guess: 1,
    },
  }),
];

const pricingProfiles: PricingParameterProfile[] = [
  {
    id: 11,
    name: 'Desk close',
    valuation_date: '2026-05-13T00:00:00',
    source_type: 'xlsx',
    source_path: null,
    status: 'completed',
    summary: { row_count: 2 },
    created_at: '2026-05-13T00:00:00',
    updated_at: '2026-05-13T00:00:00',
    rows: [],
  },
];

const marketDataProfiles: MarketDataProfile[] = [
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
  {
    id: 23,
    name: 'CSI1000 daily',
    source: 'akshare',
    symbol: '000852.SH',
    asset_class: 'index',
    start_date: '2026-05-01',
    end_date: '2026-05-13',
    adjust: 'qfq',
    valuation_date: '2026-05-13T00:00:00',
    data: { spot: 99.5 },
    source_metadata: {},
    created_at: '2026-05-13T00:00:00',
    updated_at: '2026-05-13T00:00:00',
  },
];

const underlyings: Instrument[] = [
  underlying({ symbol: '000852.SH', display_name: 'CSI 1000', status: 'active' }),
  underlying({ symbol: '000300.SH', display_name: 'CSI 300', status: 'active' }),
  underlying({ symbol: '510050.SH', display_name: 'SSE 50 ETF', status: 'inactive' }),
];

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('TrySolveLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the catalog and renders the route', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<TrySolveLive />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/rfq/try-solve/catalog', expect.any(Object));
    });
    expect(screen.getByRole('heading', { name: 'TRY TO SOLVE' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /import excel row/i })).toBeInTheDocument();
  });

  it('submits imported Excel as multipart form data and renders mixed-product rows', async () => {
    let importInit: RequestInit | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        importInit = init;
        return response({ batch_id: 'batch-1', rows: importedRows, summary: { total_rows: 2, solved: 0 } });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    const file = new File(['xlsx'], 'try-solve.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), file);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/rfq/try-solve/import', expect.objectContaining({ method: 'POST' }));
    });
    expect(importInit?.body).toBeInstanceOf(FormData);
    expect((importInit?.body as FormData).get('file')).toBeInstanceOf(File);
    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).getByRole('button', { name: /im-1 autocall/i })).toBeInTheDocument();
    expect(within(queue).getByRole('button', { name: /im-2 vanilla/i })).toBeInTheDocument();
  });

  it('solves the selected row with edited fields, market, and quote request', async () => {
    let solveBody: { row: TrySolveRowOut } | undefined;
    const solvedRow = { ...importedRows[0], status: 'solved', solved_value: 0.123, model_price: 0, residual: 0 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        return response({ batch_id: 'batch-1', rows: importedRows, summary: { total_rows: 2, solved: 0 } });
      }
      if (url === '/api/rfq/try-solve/solve' && init?.method === 'POST') {
        solveBody = JSON.parse(String(init.body));
        return response(solvedRow);
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), new File(['xlsx'], 'try-solve.xlsx'));
    await screen.findByRole('button', { name: /im-1 autocall/i });
    fireEvent.change(screen.getByLabelText('Pricing Parameter Profile'), { target: { value: '11' } });
    fireEvent.change(screen.getByLabelText('Market Data Profile'), { target: { value: '23' } });
    fireEvent.change(screen.getByLabelText('Knock-Out Barrier'), { target: { value: '1.08' } });
    fireEvent.change(screen.getByLabelText('Spot'), { target: { value: '1.23' } });
    fireEvent.change(screen.getByLabelText('Dividend Yield'), { target: { value: '0.012' } });
    fireEvent.change(screen.getByLabelText('Quote Field'), { target: { value: 'ko_barrier' } });
    fireEvent.change(screen.getByLabelText('Quote Initial Guess'), { target: { value: '1.04' } });
    await userEvent.click(screen.getByRole('button', { name: /^solve selected$/i }));

    await waitFor(() => expect(solveBody?.row.row_id).toBe('IM-1'));
    expect(solveBody?.row.fields.ko_barrier).toBe(1.08);
    expect(solveBody?.row.market.pricing_parameter_profile_id).toBe(11);
    expect(solveBody?.row.market.market_data_profile_id).toBe(23);
    expect(solveBody?.row.market.spot).toBe(1.23);
    expect(solveBody?.row.market.dividend_yield).toBe(0.012);
    expect(solveBody?.row.quote_request.quote_field_key).toBe('ko_barrier');
    expect(solveBody?.row.quote_request.initial_guess).toBe(1.04);
    expect(await screen.findByText('0.12300')).toBeInTheDocument();
  });

  it('shows failed solve status instead of a stale solved banner', async () => {
    const failedRow = {
      ...importedRows[1],
      status: 'solve_failed',
      diagnostics: [
        'Target price 0.01 is not between model prices at quote Strike bounds [8,000, 9,000]: 8,000 -> 784.984, 9,000 -> 270.01. Widen Quote Lower/Upper Bound or adjust Target Value.',
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        return response({ batch_id: 'batch-1', rows: importedRows, summary: { total_rows: 2, solved: 0 } });
      }
      if (url === '/api/rfq/try-solve/solve' && init?.method === 'POST') {
        return response(failedRow);
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), new File(['xlsx'], 'try-solve.xlsx'));
    await userEvent.click(await screen.findByRole('button', { name: /im-2 vanilla/i }));
    await userEvent.click(screen.getByRole('button', { name: /^solve selected$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Solve failed for IM-2. See diagnostics.');
    expect(screen.queryByText(/Solved IM-2/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Target price 0.01 is not between model prices at quote Strike bounds/i)).toBeInTheDocument();
    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).getByRole('button', { name: /im-2 vanilla solve failed/i })).toHaveAttribute('aria-pressed', 'true');
  });

  it('preserves semantic quote-field initial guess instead of forcing midpoint', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        return response({ batch_id: 'batch-1', rows: importedRows, summary: { total_rows: 2, solved: 0 } });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), new File(['xlsx'], 'try-solve.xlsx'));
    await userEvent.click(await screen.findByRole('button', { name: /im-2 vanilla/i }));
    fireEvent.change(screen.getByLabelText('Spot'), { target: { value: '1.01' } });
    fireEvent.change(screen.getByLabelText('Quote Field'), { target: { value: 'premium_rate' } });
    fireEvent.change(screen.getByLabelText('Quote Field'), { target: { value: 'strike' } });

    expect(screen.getByLabelText('Quote Lower Bound')).toHaveValue(0.101);
    expect(screen.getByLabelText('Quote Upper Bound')).toHaveValue(2.02);
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue(1.01);
  });

  it('adds a manual request row from the product library', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    fireEvent.change(screen.getByLabelText('Product Type'), { target: { value: 'vanilla' } });
    await userEvent.click(screen.getByRole('button', { name: /^new$/i }));

    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).getByRole('button', { name: /man-1 vanilla/i })).toBeInTheDocument();
    expect(screen.getByRole('form', { name: /vanilla field editor/i })).toBeInTheDocument();
    expect(screen.getByText('Missing required terms: Underlying.')).toBeInTheDocument();
  });

  it('clears manual required-term diagnostics after required fields are entered', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/instruments?status=active&tag=underlying') return response(underlyings.filter((u) => u.status === 'active'));
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    fireEvent.change(screen.getByLabelText('Product Type'), { target: { value: 'vanilla' } });
    await userEvent.click(screen.getByRole('button', { name: /^new$/i }));
    expect(screen.getByText('Missing required terms: Underlying.')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');

    expect(screen.queryByText('Missing required terms: Underlying.')).not.toBeInTheDocument();
    expect(screen.getByText('No missing terms detected.')).toBeInTheDocument();
    expect(screen.getByLabelText('Initial Price')).toHaveValue(99.5);
    expect(screen.getByLabelText('Quote Field')).toHaveValue('strike');
    await waitFor(() => expect(screen.getByLabelText('Quote Lower Bound')).toHaveValue(9.95));
    expect(screen.getByLabelText('Quote Upper Bound')).toHaveValue(199);
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue(99.5);
    fireEvent.change(screen.getByLabelText('Spot'), { target: { value: '4931.386' } });
    expect(screen.getByLabelText('Quote Lower Bound')).toHaveValue(493.1386);
    expect(screen.getByLabelText('Quote Upper Bound')).toHaveValue('9,862.772');
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue('4,931.386');
    fireEvent.change(screen.getByLabelText('Quote Lower Bound'), { target: { value: '8000' } });
    fireEvent.change(screen.getByLabelText('Quote Upper Bound'), { target: { value: '10000' } });
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue('9,000');
    fireEvent.change(screen.getByLabelText('Quote Initial Guess'), { target: { value: '8500' } });
    fireEvent.change(screen.getByLabelText('Quote Lower Bound'), { target: { value: '7000' } });
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue('8,500');
    fireEvent.change(screen.getByLabelText('Quote Initial Guess'), { target: { value: '12000' } });
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue('10,000');
    fireEvent.change(screen.getByLabelText('Quote Initial Guess'), { target: { value: '8500' } });
    fireEvent.change(screen.getByLabelText('Quote Upper Bound'), { target: { value: '5000' } });
    expect(screen.getByLabelText('Quote Initial Guess')).toHaveValue('5,000');
    expect(screen.getByRole('button', { name: /^solve selected$/i })).toBeEnabled();
    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).getByRole('button', { name: /man-1 vanilla solver ready/i })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders underlying as a picker limited to active underlyings', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/instruments?status=active&tag=underlying') return response(underlyings.filter((u) => u.status === 'active'));
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });

    const underlyingSelect = screen.getByLabelText('Underlying');
    expect(underlyingSelect.tagName).toBe('SELECT');
    expect(underlyingSelect).toHaveValue('000852.SH');
    expect(within(underlyingSelect).getByRole('option', { name: /000852\.SH/i })).toBeInTheDocument();
    expect(within(underlyingSelect).getByRole('option', { name: /000300\.SH/i })).toBeInTheDocument();
    expect(within(underlyingSelect).queryByRole('option', { name: /510050\.SH/i })).not.toBeInTheDocument();
  });

  it('hydrates initial price and spot from latest market data on setup load', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/instruments?status=active&tag=underlying') return response(underlyings.filter((u) => u.status === 'active'));
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });

    await waitFor(() => {
      expect(screen.getByLabelText('Initial Price')).toHaveValue(99.5);
      expect(screen.getByLabelText('Spot')).toHaveValue(99.5);
    });
  });

  it('refreshes initial price and spot from latest market data when underlying changes', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/instruments?status=active&tag=underlying') return response(underlyings.filter((u) => u.status === 'active'));
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000300.SH');

    expect(screen.getByLabelText('Initial Price')).toHaveValue(108.5);
    expect(screen.getByLabelText('Spot')).toHaveValue(108.5);
  });

  it('deletes request rows from the queue and moves selection to the next row', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.click(screen.getByRole('button', { name: /delete xl-12 request/i }));

    const queue = screen.getByRole('list', { name: /request rows/i });
    expect(within(queue).queryByRole('button', { name: /xl-12 autocall/i })).not.toBeInTheDocument();
    expect(within(queue).getByRole('button', { name: /xl-18 vanilla/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('form', { name: /vanilla field editor/i })).toBeInTheDocument();
  });

  it('filters market data profiles to the selected row underlying', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        return response({ batch_id: 'batch-1', rows: importedRows, summary: { total_rows: 2, solved: 0 } });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), new File(['xlsx'], 'try-solve.xlsx'));
    await screen.findByRole('button', { name: /im-1 autocall/i });

    const marketSelect = screen.getByLabelText('Market Data Profile');
    expect(within(marketSelect).getByRole('option', { name: /csi1000 daily/i })).toBeInTheDocument();
    expect(within(marketSelect).getByRole('option', { name: /saved/i })).toBeInTheDocument();
    expect(within(marketSelect).queryByRole('option', { name: /csi300 daily/i })).not.toBeInTheDocument();
  });

  it('posts all rows when solving the full queue', async () => {
    let solveBatchBody: { rows: TrySolveRowOut[] } | undefined;
    const batchRows = importedRows.map((item) => ({ ...item, status: 'solved', solved_value: 1 }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        return response({ batch_id: 'batch-1', rows: importedRows, summary: { total_rows: 2, solved: 0 } });
      }
      if (url === '/api/rfq/try-solve/solve-batch' && init?.method === 'POST') {
        solveBatchBody = JSON.parse(String(init.body));
        return response({ batch_id: 'batch-2', rows: batchRows, summary: { total_rows: 2, solved: 2 } });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), new File(['xlsx'], 'try-solve.xlsx'));
    await screen.findByRole('button', { name: /im-2 vanilla/i });
    await userEvent.click(screen.getByRole('button', { name: /^solve all$/i }));

    await waitFor(() => expect(solveBatchBody?.rows.map((item) => item.row_id)).toEqual(['IM-1', 'IM-2']));
    expect(await screen.findByRole('status')).toHaveTextContent('Solved 2 rows. 2 solved.');
  });

  it('exports rows and navigates to the returned artifact URL', async () => {
    let exportBody: { rows: TrySolveRowOut[]; scope: string; selected_row_ids: string[] } | undefined;
    const navigate = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/export' && init?.method === 'POST') {
        exportBody = JSON.parse(String(init.body));
        return response({
          filename: 'try-solve-results.xlsx',
          url: '/artifacts/try-solve/try-solve-results.xlsx',
          row_count: 3,
          scope: 'selected',
        });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive navigate={navigate} />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.click(screen.getByRole('button', { name: /^export$/i }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/artifacts/try-solve/try-solve-results.xlsx'));
    expect(exportBody?.scope).toBe('selected');
    expect(exportBody?.selected_row_ids).toEqual(['XL-12']);
    expect(exportBody?.rows).toHaveLength(3);
  });

  it('surfaces import failures as an alert', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/rfq/try-solve/catalog') return response(catalog);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/market-data/profiles') return response(marketDataProfiles);
      if (url === '/api/rfq/try-solve/import' && init?.method === 'POST') {
        return response({ detail: 'bad workbook' }, 400);
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TrySolveLive />);

    await screen.findByRole('heading', { name: 'TRY TO SOLVE' });
    await userEvent.upload(screen.getByLabelText(/import excel row file/i), new File(['xlsx'], 'bad.xlsx'));

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not import workbook: bad workbook');
  });
});

function field(key: string, label: string, fieldType: 'text' | 'number', required = false) {
  return {
    key,
    label,
    field_type: fieldType,
    excel_aliases: [],
    required,
    options: [],
  };
}

function quoteField(key: string, label: string, canonicalPath: string, solverReady = true) {
  return {
    key,
    label,
    excel_header: label,
    canonical_path: canonicalPath,
    lower_bound: -1,
    upper_bound: 2,
    initial_guess: 0,
    solver_ready: solverReady,
  };
}

function row(overrides: Partial<TrySolveRowOut> & Pick<TrySolveRowOut, 'row_id' | 'product_key' | 'product_label'>): TrySolveRowOut {
  return {
    source: 'excel',
    source_sheet: null,
    source_row: null,
    status: 'solver_ready',
    diagnostics: [],
    quantark_product_type: null,
    engine_name: null,
    fields: {},
    raw_values: {},
    market: { valuation_date: '2026-05-13', spot: 1, volatility: 0.2, rate: 0.02 },
    quote_request: {
      quote_field_key: 'premium_rate',
      target_label: 'price',
      target_value: 0,
      lower_bound: -1,
      upper_bound: 2,
      initial_guess: 0,
    },
    ...overrides,
  };
}

function underlying(overrides: Pick<Instrument, 'symbol' | 'display_name' | 'status'>): Instrument {
  return {
    id: Number(overrides.symbol.slice(0, 6)),
    symbol: overrides.symbol,
    display_name: overrides.display_name,
    kind: 'index',
    exchange: null,
    currency: 'CNY',
    akshare_symbol: overrides.symbol,
    akshare_asset_class: 'index',
    status: overrides.status,
    source: 'managed',
    contract_code: null,
    series_root: null,
    expiry: null,
    multiplier: null,
    strike: null,
    option_type: null,
    parent_id: null,
    loaded_at: null,
    rate: 0.02,
    dividend_yield: 0.01,
    volatility: 0.2,
    notes: null,
    tags: ['underlying'],
    created_at: '2026-05-13T00:00:00',
    updated_at: '2026-05-13T00:00:00',
  };
}

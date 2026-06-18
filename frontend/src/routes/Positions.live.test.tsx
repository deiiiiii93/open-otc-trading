import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PositionsLive } from './Positions.live';

const portfolio = {
  id: 1,
  name: 'Desk-Q2',
  kind: 'container',
  base_currency: 'USD',
  created_at: '2026-05-07T00:00:00',
  updated_at: '2026-05-07T00:00:00',
  positions: [
    {
      id: 42,
      portfolio_id: 1,
      underlying: '000852.SH',
      product_type: 'EuropeanVanillaOption',
      product_kwargs: { strike: 100, option_type: 'CALL' },
      engine_name: 'BlackScholesEngine',
      engine_kwargs: {},
      quantity: -1,
      entry_price: 0,
      status: 'open',
      position_kind: 'otc',
      source_trade_id: 'T-VANILLA',
      source_row: 12,
      mapping_status: 'supported',
      mapping_error: null,
      source_payload: {},
    },
  ],
};

const viewPortfolio = {
  ...portfolio,
  id: 2,
  name: 'Latest View',
  kind: 'view',
  updated_at: '2026-05-09T00:00:00',
  positions: [
    {
      ...portfolio.positions[0],
      id: 44,
      source_trade_id: 'T-VIEW',
      product_type: 'SnowballOption',
    },
  ],
};

const twoPositionPortfolio = {
  ...portfolio,
  positions: [
    portfolio.positions[0],
    {
      ...portfolio.positions[0],
      id: 43,
      source_trade_id: 'T-SECOND',
      underlying: '000300.SH',
      product_kwargs: { strike: 95, option_type: 'PUT' },
    },
  ],
};

const pricingProfiles = [
  {
    id: 7,
    name: '2026-04-30 Close',
    valuation_date: '2026-04-30T00:00:00',
    source_type: 'xlsx',
    source_path: null,
    status: 'completed',
    summary: {},
    created_at: '2026-05-11T00:00:00',
    updated_at: '2026-05-11T00:00:00',
    rows: [
      {
        id: 70,
        profile_id: 7,
        source_trade_id: 'T-VANILLA',
        symbol: '000852.SH',
        spot: 111.25,
        rate: 0.025,
        dividend_yield: 0.01,
        volatility: 0.33,
        source_row: 12,
        source_payload: {},
        created_at: '2026-05-11T00:00:00',
        updated_at: '2026-05-11T00:00:00',
      },
    ],
  },
];

const portfoliosPath = '/api/portfolios';

const fullRun = {
  id: 7,
  portfolio_id: 1,
  status: 'completed',
  valuation_date: '2026-05-07T00:00:00',
  overrides: {},
  summary: { market_value: 1234, pnl: 55 },
  results: [
    {
      id: 701,
      valuation_run_id: 7,
      position_id: 42,
      source_trade_id: 'T-VANILLA',
      ok: true,
      price: 1,
      market_value: -1,
      pnl: 1,
      market_inputs: { valuation_date: '2026-05-07T00:00:00', spot: 101.5, rate: 0.014, dividend_yield: 0, volatility: 0.22 },
      result_payload: {},
      error: null,
      created_at: '2026-05-07T00:00:00',
    },
    {
      id: 702,
      valuation_run_id: 7,
      position_id: 43,
      source_trade_id: 'T-SECOND',
      ok: true,
      price: 7,
      market_value: 7,
      pnl: 2,
      market_inputs: { valuation_date: '2026-05-07T00:00:00', spot: 99, rate: 0.014, dividend_yield: 0, volatility: 0.22 },
      result_payload: {},
      error: null,
      created_at: '2026-05-07T00:00:00',
    },
  ],
};

const singleRun = {
  id: 8,
  portfolio_id: 1,
  status: 'completed',
  valuation_date: '2026-05-08T00:00:00',
  overrides: { position_ids: [42], spot: 103.5 },
  summary: { market_value: 200, pnl: 10 },
  results: [
    {
      ...fullRun.results[0],
      id: 801,
      valuation_run_id: 8,
      price: 2,
      market_value: -2,
      market_inputs: { spot: 103.5, rate: 0.014, dividend_yield: 0, volatility: 0.22 },
    },
  ],
};

const queuedPricingTask = {
  id: 99,
  kind: 'position_pricing',
  status: 'queued',
  portfolio_id: 1,
  risk_run_id: null,
  report_job_id: null,
  progress_current: 0,
  progress_total: 0,
  message: 'Pricing run queued',
  error: null,
  created_at: '2026-05-11T00:00:00',
  started_at: null,
  finished_at: null,
};

const completedPricingTask = {
  ...queuedPricingTask,
  status: 'completed',
  progress_current: 1,
  progress_total: 1,
  message: 'Pricing run #7 completed',
  started_at: '2026-05-11T00:00:01',
  finished_at: '2026-05-11T00:00:02',
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

function unexpectedRequest(url: string): never {
  throw new Error(`Unexpected request: ${url}`);
}

describe('PositionsLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the most recently updated portfolio, including views', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio, viewPortfolio]);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/portfolios/2/runs') return response([]);
      return unexpectedRequest(url);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VIEW')).toBeInTheDocument());
    expect(fetchMock.mock.calls.map(([input]) => requestUrl(input))).toContain(portfoliosPath);
    expect(screen.getByLabelText('Display portfolio')).toHaveValue('2');
    expect(screen.queryByLabelText('Import target portfolio')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    expect(screen.getByLabelText('Import target portfolio')).toHaveValue('1');
  });

  it('leaves pricing inputs empty when there is no run result (PositionMarketInput store is gone)', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/portfolios/1/runs') return response([]);
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.click(screen.getByText('T-VANILLA'));

    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));

    expect(screen.getByRole('dialog', { name: /position detail/i })).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(null);
    expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(null);
    expect(within(dialog).getByLabelText('Pricing volatility')).toHaveValue(null);
  });

  it('sends edited market and engine parameters for a single-position pricing run', async () => {
    const postBodies: unknown[] = [];
    let runs: unknown[] = [fullRun];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/portfolios/1/runs') return response(runs);
      if (url.startsWith('/api/portfolios/1/positions/42/pricing-params')) {
        return response({
          spot: { value: 101.5, source: 'market_quote', age_days: 1, quote_source: 'akshare' },
          rate: { value: 0.014, source: 'assumption_set', assumption_set_id: 4 },
          dividend_yield: { value: 0, source: 'assumption_set', assumption_set_id: 4 },
          volatility: { value: 0.22, source: 'assumption_set', assumption_set_id: 4 },
        });
      }
      if (url === '/api/portfolios/1/positions/price' && init?.method === 'POST') {
        postBodies.push(JSON.parse(String(init.body)));
        runs = [singleRun];
        return response(singleRun);
      }
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.click(screen.getByText('T-VANILLA'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
    // The ticket prefills once the resolved params land.
    await waitFor(() => expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(0.014));
    await userEvent.selectOptions(within(dialog).getByLabelText('Pricing engine'), 'EuropeanMCEngine');

    const spot = within(dialog).getByLabelText('Pricing spot');
    await userEvent.clear(spot);
    await userEvent.type(spot, '103.5');
    const paths = within(dialog).getByLabelText('Path Nums');
    await userEvent.clear(paths);
    await userEvent.type(paths, '12345');
    const seed = within(dialog).getByLabelText('Seed');
    await userEvent.clear(seed);
    await userEvent.type(seed, '7');
    await userEvent.click(within(dialog).getByRole('button', { name: /price position/i }));

    await waitFor(() => expect(postBodies).toHaveLength(1));
    expect(postBodies[0]).toMatchObject({
      position_ids: [42],
      valuation_date: '2026-05-07T00:00:00',
      spot: 103.5,
      rate: 0.014,
      dividend_yield: 0,
      volatility: 0.22,
      engine_name: 'EuropeanMCEngine',
      engine_kwargs: {
        params_type: 'mc_params',
        params_kwargs: {
          num_paths: 12345,
          time_steps: 100,
          seed: 7,
        },
      },
    });
  });

  it('sends the selected pricing parameter profile for a portfolio pricing run', async () => {
    const postBodies: unknown[] = [];
    let runs: unknown[] = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/runs') return response(runs);
      if (url === '/api/batch-pricing/runs' && init?.method === 'POST') {
        postBodies.push(JSON.parse(String(init.body)));
        runs = [fullRun];
        return response({ id: 8, portfolio_id: 1, status: 'queued', task_id: 99, metrics: {}, created_at: '2026-06-06T00:00:00Z' });
      }
      if (url === '/api/tasks/99') return response(completedPricingTask);
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Pricing profile'), '7');
    await userEvent.click(screen.getByRole('button', { name: /run batch pricing/i }));

    await waitFor(() => expect(postBodies).toHaveLength(1));
    expect(postBodies[0]).toMatchObject({
      pricing_parameter_profile_id: 7,
    });
    await waitFor(() => expect(screen.getByText('Market Value (PV)')).toBeInTheDocument());
  });

  it('uses an initial pricing parameter profile loaded from the management page', async () => {
    const postBodies: unknown[] = [];
    let runs: unknown[] = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/runs') return response(runs);
      if (url === '/api/batch-pricing/runs' && init?.method === 'POST') {
        postBodies.push(JSON.parse(String(init.body)));
        runs = [fullRun];
        return response({ id: 8, portfolio_id: 1, status: 'queued', task_id: 99, metrics: {}, created_at: '2026-06-06T00:00:00Z' });
      }
      if (url === '/api/tasks/99') return response(completedPricingTask);
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive initialPricingProfileId={7} />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    expect(screen.getByLabelText('Pricing profile')).toHaveValue('7');
    await userEvent.click(screen.getByRole('button', { name: /run batch pricing/i }));

    await waitFor(() => expect(postBodies).toHaveLength(1));
    expect(postBodies[0]).toMatchObject({
      pricing_parameter_profile_id: 7,
    });
  });

  it('restores the pricing parameter profile from the latest saved risk run on load', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/runs') {
        return response([{ ...fullRun, pricing_parameter_profile_id: null }]);
      }
      if (url === '/api/portfolios/1/risk-runs/latest') {
        return response({
          id: 7,
          portfolio_id: 1,
          pricing_parameter_profile_id: 7,
          status: 'completed',
          metrics: { totals: { pnl: 10 } },
        });
      }
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    expect(screen.getByLabelText('Pricing profile')).toHaveValue('7');
  });

  it('labels the no-profile fallback as live market plus assumptions', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/runs') return response([]);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(null);
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'Live market + assumptions' })).toHaveValue('');
  });

  it('prefills the position pricing ticket from the server-resolved pricing params', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/runs') return response([]);
      if (url === '/api/portfolios/1/positions/42/pricing-params?pricing_parameter_profile_id=7') {
        return response({
          spot: { value: 11965, source: 'market_quote', age_days: 2, quote_source: 'akshare' },
          rate: { value: 0.025, source: 'pricing_parameter_profile', profile_id: 7, source_trade_id: 'T-VANILLA' },
          dividend_yield: { value: 0.01, source: 'pricing_parameter_profile', profile_id: 7, source_trade_id: 'T-VANILLA' },
          volatility: { value: 0.33, source: 'pricing_parameter_profile', profile_id: 7, source_trade_id: 'T-VANILLA' },
        });
      }
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive initialPricingProfileId={7} />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.click(screen.getByText('T-VANILLA'));

    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
    expect(within(dialog).getByText(/Loaded profile/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/2026-04-30 Close/i)).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Pricing valuation date')).toHaveValue('2026-04-30');
    // All four market fields prefill from the server resolution: spot from the
    // quote store, r/q/vol from the trade-keyed profile row.
    await waitFor(() => expect(within(dialog).getByLabelText('Pricing spot')).toHaveValue(11965));
    expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(0.025);
    expect(within(dialog).getByLabelText('Pricing dividend yield')).toHaveValue(0.01);
    expect(within(dialog).getByLabelText('Pricing volatility')).toHaveValue(0.33);
  });

  it('keeps the position detail dialog open after single-position pricing completes', async () => {
    let runs: unknown[] = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/portfolios/1/runs') return response(runs);
      if (url === '/api/portfolios/1/positions/price' && init?.method === 'POST') {
        runs = [singleRun];
        return response(singleRun);
      }
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.click(screen.getByText('T-VANILLA'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
    await userEvent.click(within(dialog).getByRole('button', { name: /price position/i }));

    await waitFor(() => expect(screen.getByText(/Priced T-VANILLA\. Took \d+\.\d seconds\./)).toBeInTheDocument());
    const detailDialog = screen.getByRole('dialog', { name: /position detail/i });
    expect(detailDialog).toBeInTheDocument();
    expect(within(detailDialog).getByText('2.000')).toBeInTheDocument();
  });

  it('uses latest row results without replacing the latest full-run summary', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([twoPositionPortfolio]);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/portfolios/1/runs') return response([singleRun, fullRun]);
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    expect(screen.getByText('1.23K')).toBeInTheDocument();
    expect(screen.getByText('2.000')).toBeInTheDocument();
    expect(screen.getByText('7.000')).toBeInTheDocument();
  });

  it('fetches and renders resolved pricing params when a detail row opens', async () => {
    const resolved = {
      spot: { value: 6412.55, source: 'market_quote', age_days: 2, quote_source: 'xlsx_import' },
      rate: { value: 0.023, source: 'pricing_parameter_profile', profile_id: 7, source_trade_id: 'T-VANILLA' },
      dividend_yield: { value: 0.018, source: 'assumption_set', assumption_set_id: 4, assumption_row_id: 9 },
      volatility: { value: null, source: 'missing' },
    };
    let pricingParamsUrl: string | null = null;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/runs') return response([]);
      if (url.startsWith('/api/portfolios/1/positions/42/pricing-params')) {
        pricingParamsUrl = url;
        return response(resolved);
      }
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive initialPricingProfileId={7} />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.click(screen.getByText('T-VANILLA'));

    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    // The provenance block lives in the Pricing tab, above the ticket it feeds.
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
    await waitFor(() => expect(within(dialog).getByText(/quote · 2d · xlsx_import/)).toBeInTheDocument());
    expect(within(dialog).getByText(/profile #7 · T-VANILLA/)).toBeInTheDocument();
    expect(within(dialog).getByText(/assumptions #4/)).toBeInTheDocument();
    // The selected profile id rides on the query string.
    expect(pricingParamsUrl).toBe('/api/portfolios/1/positions/42/pricing-params?pricing_parameter_profile_id=7');
  });

  it('closes the detail dialog when the profile changes, then refetches with the new profile on reopen', async () => {
    const secondProfile = { ...pricingProfiles[0], id: 8, name: '2026-05-30 Close', rows: [] };
    const pricingParamsUrls: string[] = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === portfoliosPath) return response([portfolio]);
      if (url === '/api/pricing-parameter-profiles') return response([...pricingProfiles, secondProfile]);
      if (url === '/api/portfolios/1/runs') return response([]);
      if (url.startsWith('/api/portfolios/1/positions/42/pricing-params')) {
        pricingParamsUrls.push(url);
        const fromSecondProfile = url.endsWith('=8');
        return response({
          spot: { value: 11965, source: 'market_quote', age_days: 2, quote_source: 'akshare' },
          rate: { value: fromSecondProfile ? 0.031 : 0.025, source: 'pricing_parameter_profile', profile_id: fromSecondProfile ? 8 : 7, source_trade_id: 'T-VANILLA' },
          dividend_yield: { value: 0.01, source: 'pricing_parameter_profile', profile_id: fromSecondProfile ? 8 : 7, source_trade_id: 'T-VANILLA' },
          volatility: { value: 0.33, source: 'pricing_parameter_profile', profile_id: fromSecondProfile ? 8 : 7, source_trade_id: 'T-VANILLA' },
        });
      }
      return unexpectedRequest(url);
    }) as unknown as typeof fetch;

    render(<PositionsLive initialPricingProfileId={7} />);

    await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
    await userEvent.click(screen.getByText('T-VANILLA'));
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(dialog).getByRole('tab', { name: /pricing/i }));
    await waitFor(() => expect(within(dialog).getByLabelText('Pricing rate')).toHaveValue(0.025));

    // The profile selector lives in the toolbar, behind the modal. Interacting
    // with it counts as "interact outside" and dismisses the dialog — so there
    // is no stuck-open dialog holding stale resolved params from profile 7.
    await userEvent.selectOptions(screen.getByLabelText('Pricing profile'), '8');
    expect(screen.getByLabelText('Pricing profile')).toHaveValue('8');
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /position detail/i })).not.toBeInTheDocument(),
    );

    // Reopening fetches with the now-current profile id — the ticket prefills
    // from profile 8's resolution, never the stale profile-7 values.
    await userEvent.click(screen.getByText('T-VANILLA'));
    const reopened = screen.getByRole('dialog', { name: /position detail/i });
    await userEvent.click(within(reopened).getByRole('tab', { name: /pricing/i }));
    await waitFor(() => expect(within(reopened).getByLabelText('Pricing rate')).toHaveValue(0.031));
    expect(pricingParamsUrls).toEqual([
      '/api/portfolios/1/positions/42/pricing-params?pricing_parameter_profile_id=7',
      '/api/portfolios/1/positions/42/pricing-params?pricing_parameter_profile_id=8',
    ]);
  });
});

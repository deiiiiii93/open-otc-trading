import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RiskLive } from './Risk.live';

const portfolios = [{
  id: 1,
  name: 'Desk-Q2',
  base_currency: 'USD',
  positions: [{
    id: 42,
    portfolio_id: 1,
    underlying: 'CSI500',
    product_type: 'EuropeanVanillaOption',
    product_kwargs: {},
    engine_name: 'BlackScholesEngine',
    engine_kwargs: {},
    quantity: 5,
    entry_price: 8,
    status: 'open',
    source_trade_id: 'T-VANILLA',
    mapping_status: 'imported',
  }],
}];

const latestRun = {
  id: 7,
  portfolio_id: 1,
  pricing_parameter_profile_id: null,
  market_snapshot_id: null,
  method: 'summary',
  status: 'completed',
  metrics: {
    totals: {
      market_value: 1234,
      delta_proxy: 0.5,
      gross_notional: 1_000_000,
      pnl: 1234,
      one_day_var_proxy: 56,
    },
    positions: [
      {
        position_id: 42,
        underlying: 'CSI500',
        product_type: 'EuropeanVanillaOption',
        quantity: 5,
        price: 10,
        market_value: 50,
        gross_notional: 50_000,
        pnl: 10,
        delta_proxy: 5,
        pricing_ok: true,
        pricing_error: null,
      },
    ],
  },
  scenario_cells: null,
  created_at: '2026-05-08T08:00:00Z',
};

const manualRun = {
  ...latestRun,
  id: 8,
  status: 'completed',
  metrics: {
    ...latestRun.metrics,
    totals: { ...latestRun.metrics.totals, pnl: 4321 },
  },
};

const queuedManualRun = {
  ...manualRun,
  status: 'queued',
  metrics: {},
  scenario_cells: null,
  task_id: 99,
};

const pricingProfiles = [{
  id: 3,
  name: 'Q1 Pricing',
  valuation_date: '2026-05-12',
  source_type: 'xlsx',
  source_path: null,
  status: 'completed',
  summary: {},
  created_at: '2026-05-12T00:00:00Z',
  updated_at: '2026-05-12T00:00:00Z',
  rows: [],
}];

const completedTask = {
  id: 99,
  kind: 'risk_run',
  status: 'completed',
  portfolio_id: 1,
  risk_run_id: 8,
  report_job_id: null,
  progress_current: 1,
  progress_total: 1,
  message: 'Completed 1 positions',
  error: null,
  created_at: '2026-05-08T08:00:00Z',
  started_at: '2026-05-08T08:00:01Z',
  finished_at: '2026-05-08T08:00:02Z',
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

const twoPortfolios = [
  ...portfolios,
  {
    id: 2,
    name: 'Hedge-X',
    base_currency: 'CNY',
    positions: [],
  },
];

describe('RiskLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('initially selects the shared portfolio preference when it exists', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(twoPortfolios);
      if (url === '/api/portfolios/2/risk-runs/latest') return response(null);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive portfolioId={2} />);

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (
      requestUrl(url as RequestInfo | URL) === '/api/portfolios/2/risk-runs/latest'
    ))).toBe(true));
    expect(fetchMock.mock.calls.some(([url]) => (
      requestUrl(url as RequestInfo | URL) === '/api/portfolios/1/risk-runs/latest'
    ))).toBe(false);
  });

  it('reselects when the shared portfolio prop changes after mount (Back/Forward)', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(twoPortfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(null);
      if (url === '/api/portfolios/2/risk-runs/latest') return response(null);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const { rerender } = render(<RiskLive portfolioId={2} />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (
      requestUrl(url as RequestInfo | URL) === '/api/portfolios/2/risk-runs/latest'
    ))).toBe(true));

    // Simulate Back/Forward changing the shared portfolio while mounted.
    rerender(<RiskLive portfolioId={1} />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (
      requestUrl(url as RequestInfo | URL) === '/api/portfolios/1/risk-runs/latest'
    ))).toBe(true));
  });

  it('honors a prop change that arrives before the portfolio list loads', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(twoPortfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(null);
      if (url === '/api/portfolios/2/risk-runs/latest') return response(null);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    // Rerender to portfolio 1 BEFORE the /api/portfolios fetch resolves: the
    // reactivity effect must re-evaluate once options arrive (it depends on the
    // portfolio list), overriding the loader's stale mount-time selection of 2.
    const { rerender } = render(<RiskLive portfolioId={2} />);
    rerender(<RiskLive portfolioId={1} />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (
      requestUrl(url as RequestInfo | URL) === '/api/portfolios/1/risk-runs/latest'
    ))).toBe(true));
  });

  it('falls back to the first portfolio without notifying when the preference is unknown', async () => {
    const onPortfolioIdChange = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(twoPortfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive portfolioId={99} onPortfolioIdChange={onPortfolioIdChange} />);

    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    expect(onPortfolioIdChange).not.toHaveBeenCalled();
  });

  it('notifies onPortfolioIdChange when the user explicitly picks a portfolio', async () => {
    const onPortfolioIdChange = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(twoPortfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/portfolios/2/risk-runs/latest') return response(null);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive onPortfolioIdChange={onPortfolioIdChange} />);

    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Portfolio'), ['2']);
    expect(onPortfolioIdChange).toHaveBeenCalledWith(2);
  });

  it('loads the latest saved run on navigation without creating a new run', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);

    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    expect(screen.getByText('CSI500')).toBeInTheDocument();
    expect(screen.getByText('TRADE T-VANILLA')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url, init]) => (
      requestUrl(url as RequestInfo | URL) === '/api/batch-pricing/runs' && (init as RequestInit | undefined)?.method === 'POST'
    ))).toBe(false);
  });

  it('only creates a risk run after the manual Run Batch Pricing action', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/batch-pricing/runs' && init?.method === 'POST') return response(queuedManualRun);
      if (url === '/api/tasks/99') return response(completedTask);
      if (url === '/api/risk/runs/8') return response(manualRun);
      if (url === '/api/risk/scenarios' && init?.method === 'POST') {
        return response({
          portfolio_id: 1,
          base_pnl: 0,
          cells: [[{ spot_shift_pct: 0, vol_shift_abs: 0, pnl: 0 }]],
        });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);
    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /run batch pricing/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url, init]) => (
        requestUrl(url as RequestInfo | URL) === '/api/batch-pricing/runs' && (init as RequestInit | undefined)?.method === 'POST'
      ));
      const body = JSON.parse(String(posted?.[1]?.body ?? '{}')) as { portfolio_id?: number; method?: string; pricing_parameter_profile_id?: number };
      expect(body.portfolio_id).toBe(1);
      expect(body.method).toBeUndefined();
      expect(body.pricing_parameter_profile_id).toBeUndefined();
      expect(posted).not.toBeUndefined();
    });
    await waitFor(() => expect(screen.getByText(/Task #99 completed/)).toBeInTheDocument());
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (
      requestUrl(url as RequestInfo | URL) === '/api/risk/runs/8'
    ))).toBe(true));
  });

  it('labels the no-profile fallback as live market plus assumptions', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);

    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    expect(screen.getByRole('option', { name: 'Live market + assumptions' })).toHaveValue('');
  });

  it('displays a completed mixed-currency run (null totals + by_currency) instead of the empty state', async () => {
    // Mirrors the wire shape risk_currency.build_currency_aware_totals emits
    // for a mixed-currency portfolio: totals is null, money greeks live per ccy.
    const mixedRun = {
      ...latestRun,
      id: 20,
      metrics: {
        totals: null,
        by_currency: {
          CNY: {
            market_value: -9_821_617.04, gross_notional: 9_895_698, pnl: -9_821_617.04,
            one_day_var_proxy: 124_446.29, delta_cash: -6_035_022.31, gamma_cash: 60_350.23,
            vega: 33_437.48, theta: -3_057.01, rho: -2_809.62, rho_q: 84_525.93,
            position_count: 2,
          },
          USD: {
            market_value: 3_663.83, gross_notional: 50_000, pnl: 3_663.83,
            one_day_var_proxy: 88.12, delta_cash: 1_234.56, gamma_cash: 85.5,
            vega: 210.25, theta: -12.75, rho: 4.2, rho_q: 6.6,
            position_count: 1,
          },
        },
        shared: { delta_proxy: -1880, delta: -1167.27, gamma: 0.24 },
        mixed_currency: true,
        currencies: ['CNY', 'USD'],
        positions: latestRun.metrics.positions,
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(mixedRun);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);

    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    expect(screen.getByText('GREEKS · CNY')).toBeInTheDocument();
    expect(screen.getByText('GREEKS · USD')).toBeInTheDocument();
    expect(screen.getByText('CSI500')).toBeInTheDocument();
    expect(screen.queryByText(/No saved risk run/)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url, init]) => (
      requestUrl(url as RequestInfo | URL) === '/api/batch-pricing/runs' && (init as RequestInit | undefined)?.method === 'POST'
    ))).toBe(false);
  });

  it('shows an empty state when no saved run exists and does not auto-run', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(null);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);

    await waitFor(() => (
      expect(screen.getByText('No saved risk run for this portfolio. Run manually to calculate risk.')).toBeInTheDocument()
    ));
    expect(fetchMock.mock.calls.some(([url, init]) => (
      requestUrl(url as RequestInfo | URL) === '/api/batch-pricing/runs' && (init as RequestInit | undefined)?.method === 'POST'
    ))).toBe(false);
  });

  it('includes selected pricing profile when manually running risk', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/batch-pricing/runs' && init?.method === 'POST') return response(queuedManualRun);
      if (url === '/api/tasks/99') return response(completedTask);
      if (url === '/api/risk/runs/8') return response(manualRun);
      if (url === '/api/risk/scenarios' && init?.method === 'POST') {
        return response({
          portfolio_id: 1,
          base_pnl: 0,
          cells: [[{ spot_shift_pct: 0, vol_shift_abs: 0, pnl: 0 }]],
        });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);
    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    await userEvent.selectOptions(
      screen.getByLabelText('Pricing parameter profile'),
      ['3'],
    );
    await userEvent.click(screen.getByRole('button', { name: /run batch pricing/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url, init]) => (
        requestUrl(url as RequestInfo | URL) === '/api/batch-pricing/runs' && (init as RequestInit | undefined)?.method === 'POST'
      ));
      const body = JSON.parse(String(posted?.[1]?.body ?? '{}')) as { pricing_parameter_profile_id?: number };
      expect(body.pricing_parameter_profile_id).toBe(3);
      expect(posted).not.toBeUndefined();
    });
  });

  it('includes selected pricing profile when promoting risk to report', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios/1/risk-runs/latest') return response(latestRun);
      if (url === '/api/reports/jobs' && init?.method === 'POST') {
        return response({
          id: 30,
          report_type: 'risk',
          status: 'queued',
          task_id: 44,
        });
      }
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<RiskLive />);
    await waitFor(() => expect(screen.getByText('1 priced')).toBeInTheDocument());
    await userEvent.selectOptions(
      screen.getByLabelText('Pricing parameter profile'),
      ['3'],
    );
    await userEvent.click(screen.getAllByRole('button', { name: /promote/i })[0]);

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url, init]) => (
        requestUrl(url as RequestInfo | URL) === '/api/reports/jobs' && (init as RequestInit | undefined)?.method === 'POST'
      ));
      const body = JSON.parse(String(posted?.[1]?.body ?? '{}')) as { pricing_parameter_profile_id?: number };
      expect(body.pricing_parameter_profile_id).toBe(3);
      expect(posted).not.toBeUndefined();
    });
  });
});

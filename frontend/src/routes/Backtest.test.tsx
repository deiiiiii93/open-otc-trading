import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BacktestLive } from './Backtest';

const portfolios = [
  {
    id: 1,
    name: 'Desk Book',
    kind: 'container' as const,
    base_currency: 'CNY',
    positions: [],
  },
];

const engineConfigs = [
  {
    id: 7,
    name: 'System Default',
    description: null,
    status: 'active',
    is_default: true,
    rules: { rules: [] },
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
  {
    id: 9,
    name: 'MC Everywhere',
    description: null,
    status: 'active',
    is_default: false,
    rules: { rules: [] },
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
  },
];

const completedRun = {
  id: 3,
  portfolio_id: 1,
  status: 'completed',
  spec: {
    start: '2025-01-01',
    end: '2025-06-30',
    engine: 'quad',
    vol_source: 'realized',
  },
  config: null,
  results: {
    window: { start: '2025-01-01', end: '2025-06-30' },
    engine: 'quad',
    portfolio: {
      total_pnl: 123456.78,
      hedge_pnl: 45000,
      product_pnl: 78456.78,
      num_trades: 12,
      sharpe: 1.42,
      max_drawdown: -0.05,
      var_95: -8000,
      cvar_95: -12000,
      pnl_series: [
        { date: '2025-01-03', total_pnl: 1000, hedge_pnl: 400, product_pnl: 600 },
        { date: '2025-01-10', total_pnl: 5000, hedge_pnl: 2000, product_pnl: 3000 },
      ],
    },
    by_underlying: [
      {
        underlying: 'AAPL',
        hedge_instrument: 'AAPL-FUT',
        num_products: 2,
        total_pnl: 60000,
        hedge_pnl: 22000,
        num_trades: 6,
        lifecycle_events: [
          { type: 'coupon', date: '2025-03-15', cashflow: 5000 },
        ],
        event_summary: {},
        pnl_series: [],
        greeks_series: [],
      },
    ],
    excluded_positions: [],
    notes: [],
  },
  artifacts: {
    dashboards: { AAPL: 'backtest_run_3_AAPL.html' },
  },
  created_at: '2025-07-01T10:00:00Z',
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

describe('BacktestLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the configurator with "Run backtest" button and empty runs', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/backtest/runs')) return response([]);
      return response({});
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /run backtest/i })).toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/portfolio/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/start date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/end date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/engine config/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/vol source/i)).toBeInTheDocument();
  });

  it('the Run backtest button is disabled when start or end date is empty', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/backtest/runs')) return response([]);
      return response({});
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /run backtest/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /run backtest/i })).toBeDisabled();
  });

  it('shows run history when portfolio is auto-selected', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/backtest/runs?portfolio_id=1') return response([completedRun]);
      return response([]);
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() =>
      expect(screen.getByText(/run #3/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/completed/i)).toBeInTheDocument();
  });

  it('renders Total P&L KPI when a completed run is selected', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/backtest/runs?portfolio_id=1') return response([completedRun]);
      return response([]);
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() => expect(screen.getByText(/run #3/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #3/i));

    await waitFor(() =>
      expect(screen.getByText(/total p&l/i)).toBeInTheDocument(),
    );
    // 123,456.78 formatted
    expect(screen.getByText(/123[,.]?456/)).toBeInTheDocument();
  });

  it('shows "Running…" status message for a running run', async () => {
    const runningRun = {
      ...completedRun,
      id: 5,
      status: 'running',
      results: {},
    };

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/backtest/runs?portfolio_id=1') return response([runningRun]);
      if (url === '/api/backtest/runs/5') return response(runningRun);
      return response([]);
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() => expect(screen.getByText(/run #5/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #5/i));

    await waitFor(() =>
      expect(screen.getByText(/running…/i)).toBeInTheDocument(),
    );
  });

  it('shows failure message for a failed run', async () => {
    const failedRun = {
      ...completedRun,
      id: 6,
      status: 'failed',
      results: { error: 'pricing engine crashed' },
    };

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/backtest/runs?portfolio_id=1') return response([failedRun]);
      return response([]);
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() => expect(screen.getByText(/run #6/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #6/i));

    await waitFor(() =>
      expect(screen.getByText(/run failed/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/pricing engine crashed/i)).toBeInTheDocument();
  });

  it('renders object-shaped hedge instrument details', async () => {
    const objectHedgeRun = {
      ...completedRun,
      id: 7,
      results: {
        ...completedRun.results,
        by_underlying: [
          {
            ...completedRun.results.by_underlying[0],
            hedge_instrument: { kind: 'futures', multiplier: 200 },
          },
        ],
      },
    };
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/backtest/runs?portfolio_id=1') return response([objectHedgeRun]);
      return response([]);
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() => expect(screen.getByText(/run #7/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #7/i));
    await userEvent.click(screen.getAllByText(/AAPL/)[0]);

    expect(screen.getByText(/hedge instrument: futures x200/i)).toBeInTheDocument();
  });

  it('posts a run with the correct payload on submit', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/engine-configs') return response(engineConfigs);
      if (url === '/api/backtest/runs' && init?.method === 'POST') {
        return response(completedRun);
      }
      if (url.startsWith('/api/backtest/runs')) return response([completedRun]);
      return response([]);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /run backtest/i })).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole('option', { name: /system default \(default\)/i })).toBeInTheDocument(),
    );

    // Fill dates to enable the button
    await userEvent.type(screen.getByLabelText(/start date/i), '2025-01-01');
    await userEvent.type(screen.getByLabelText(/end date/i), '2025-06-30');

    await userEvent.click(screen.getByRole('button', { name: /run backtest/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url, init]) =>
        requestUrl(url as RequestInfo | URL) === '/api/backtest/runs' &&
        (init as RequestInit | undefined)?.method === 'POST',
      );
      expect(posted).not.toBeUndefined();
      const body = JSON.parse(String(posted?.[1]?.body ?? '{}')) as {
        portfolio_id?: number;
        engine_config_id?: number | null;
        spec?: { engine?: string; vol_source?: string };
      };
      expect(body.portfolio_id).toBe(1);
      // default engine config is preselected
      expect(body.engine_config_id).toBe(7);
      expect(body.spec?.engine).toBe('quad');
      expect(body.spec?.vol_source).toBe('realized');
    });
  });

  it('posts the selected engine config id', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/engine-configs') return response(engineConfigs);
      if (url === '/api/backtest/runs' && init?.method === 'POST') {
        return response(completedRun);
      }
      if (url.startsWith('/api/backtest/runs')) return response([completedRun]);
      return response([]);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /run backtest/i })).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole('option', { name: /mc everywhere/i })).toBeInTheDocument(),
    );

    await userEvent.selectOptions(screen.getByLabelText(/engine config/i), '9');
    await userEvent.type(screen.getByLabelText(/start date/i), '2025-01-01');
    await userEvent.type(screen.getByLabelText(/end date/i), '2025-06-30');
    await userEvent.click(screen.getByRole('button', { name: /run backtest/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url, init]) =>
        requestUrl(url as RequestInfo | URL) === '/api/backtest/runs' &&
        (init as RequestInit | undefined)?.method === 'POST',
      );
      const body = JSON.parse(String(posted?.[1]?.body ?? '{}')) as {
        engine_config_id?: number | null;
      };
      expect(body.engine_config_id).toBe(9);
    });
  });

  it('renders dashboard artifact links for a completed run', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/backtest/runs?portfolio_id=1') return response([completedRun]);
      return response([]);
    }) as unknown as typeof fetch;

    render(<BacktestLive />);

    await waitFor(() => expect(screen.getByText(/run #3/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #3/i));

    await waitFor(() =>
      expect(screen.getByRole('link', { name: /quant-ark dashboard.*aapl/i })).toBeInTheDocument(),
    );
    expect(screen.getByTitle(/quant-ark dashboard aapl/i)).toHaveAttribute(
      'src',
      '/api/backtest/runs/3/artifacts/backtest_run_3_AAPL.html',
    );
    const standaloneLink = screen.getByRole('link', { name: /open standalone tab/i });
    expect(standaloneLink).toHaveAttribute(
      'href',
      '/api/backtest/runs/3/artifacts/backtest_run_3_AAPL.html',
    );
    expect(standaloneLink).toHaveAttribute('target', '_blank');
    const link = screen.getByRole('link', { name: /quant-ark dashboard.*aapl/i });
    expect(link).toHaveAttribute(
      'href',
      '/api/backtest/runs/3/artifacts/backtest_run_3_AAPL.html',
    );
    const downloadLink = screen.getByRole('link', { name: /download dashboard html.*aapl/i });
    expect(downloadLink).toHaveAttribute('download');
    expect(downloadLink).toHaveAttribute(
      'href',
      '/api/backtest/runs/3/artifacts/backtest_run_3_AAPL.html?download=true',
    );
  });
});

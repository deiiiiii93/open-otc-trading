import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GreeksLandscapeLive, GreeksLandscapeView, type GreekLandscapeRun } from './GreeksLandscape';

const run: GreekLandscapeRun = {
  id: 4,
  portfolio_id: 1,
  status: 'completed',
  config: { spot_min_pct: -10, spot_max_pct: 10, spot_nodes: 3 },
  results: {
    spot_shifts_pct: [-10, 0, 10],
    portfolio: {
      raw: [
        { spot_shift_pct: -10, delta: 1, gamma: 0.1 },
        { spot_shift_pct: 0, delta: 2, gamma: 0.2 },
        { spot_shift_pct: 10, delta: 3, gamma: 0.3 },
      ],
      cash_by_currency: {},
    },
    by_underlying: {
      AAPL: {
        raw: [
          { spot_shift_pct: -10, delta: 1, gamma: 0.1 },
          { spot_shift_pct: 0, delta: 2, gamma: 0.2 },
          { spot_shift_pct: 10, delta: 3, gamma: 0.3 },
        ],
        cash_by_currency: {},
      },
    },
    positions: [],
  },
  excluded_positions: [],
  resolved_position_ids: [1],
  created_at: '2026-06-12T00:00:00Z',
};

function renderView(props: Partial<Parameters<typeof GreeksLandscapeView>[0]> = {}) {
  return render(
    <GreeksLandscapeView
      portfolios={[{ id: 1, name: 'Book' }]}
      selectedPortfolioId={1}
      runs={[]}
      selectedRunId={null}
      run={null}
      running={false}
      error={null}
      onSelectPortfolio={() => {}}
      onSelectRun={() => {}}
      onRun={vi.fn()}
      {...props}
    />,
  );
}

describe('GreeksLandscapeView', () => {
  it('renders synchronized Delta and Gamma charts and switches grouping', async () => {
    renderView({ run });

    expect(screen.getByText('DELTA LANDSCAPE')).toBeInTheDocument();
    expect(screen.getByText('GAMMA LANDSCAPE')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('View'), 'underlying');
    expect(screen.getByText('AAPL')).toBeInTheDocument();
  });

  it('submits editable grid values', async () => {
    const onRun = vi.fn();
    renderView({ run: null, onRun });
    await userEvent.clear(screen.getByLabelText('Minimum spot change'));
    await userEvent.type(screen.getByLabelText('Minimum spot change'), '-20');
    await userEvent.click(screen.getByRole('button', { name: 'Run Landscape' }));
    expect(onRun).toHaveBeenCalledWith({ spot_min_pct: -20, spot_max_pct: 30, spot_nodes: 61 });
  });

  it('reports selected scope, latest run, and agent actions as page context', async () => {
    const onPageContextChange = vi.fn();
    renderView({
      run,
      runs: [run],
      selectedRunId: run.id,
      portfolios: [{ id: 1, name: 'Book', positions: [{ id: 1, underlying: 'AAPL', source_trade_id: 'T-AAPL' }] }],
      selectedPricingProfileId: 7,
      selectedEngineConfigId: 3,
      onPageContextChange,
    });

    await waitFor(() => expect(onPageContextChange).toHaveBeenCalled());
    expect(onPageContextChange.mock.calls.at(-1)?.[0]).toMatchObject({
      route: 'greeks-landscape',
      entity_ids: {
        portfolio_id: 1,
        pricing_profile_id: 7,
        engine_config_id: 3,
        greeks_landscape_run_id: 4,
      },
      snapshot: {
        selected_portfolio: { id: 1, name: 'Book' },
        greeks_landscape: {
          run_id: 4,
          status: 'completed',
          config: { spot_nodes: 3 },
        },
      },
      actions: expect.arrayContaining([
        expect.objectContaining({ name: 'run_greeks_landscape' }),
        expect.objectContaining({ name: 'list_greeks_landscape_runs' }),
      ]),
    });
  });
});

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

const portfolios = [{
  id: 1,
  name: 'Book',
  base_currency: 'USD',
  positions: [{
    id: 1,
    portfolio_id: 1,
    underlying: 'AAPL',
    product_type: 'EuropeanVanillaOption',
    product_kwargs: {},
    engine_name: 'BlackScholesEngine',
    engine_kwargs: {},
    quantity: 1,
    entry_price: 8,
    status: 'open',
    source_trade_id: 'T-AAPL',
    mapping_status: 'imported',
  }],
}];

describe('GreeksLandscapeLive', () => {
  afterEach(() => vi.restoreAllMocks());

  it('loads run history without creating a run', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/pricing-parameter-profiles' || url === '/api/engine-configs') return response([]);
      if (url === '/api/greeks-landscape/runs?portfolio_id=1') return response([run]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<GreeksLandscapeLive />);

    await waitFor(() => expect(screen.getByRole('button', { name: /run #4/i })).toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([url, init]) => (
      requestUrl(url as RequestInfo | URL) === '/api/greeks-landscape/runs'
      && (init as RequestInit | undefined)?.method === 'POST'
    ))).toBe(false);
  });

  it('polls a queued landscape task and loads the completed run', async () => {
    const queuedRun = { ...run, id: 5, status: 'queued', task_id: 9, results: {} };
    const completedRun = { ...run, id: 5 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/pricing-parameter-profiles' || url === '/api/engine-configs') return response([]);
      if (url === '/api/greeks-landscape/runs?portfolio_id=1') return response([]);
      if (url === '/api/greeks-landscape/runs' && init?.method === 'POST') return response(queuedRun);
      if (url === '/api/tasks/9') return response({ id: 9, kind: 'greeks_landscape', status: 'completed' });
      if (url === '/api/greeks-landscape/runs/5') return response(completedRun);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<GreeksLandscapeLive />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run Landscape' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Run Landscape' }));

    await waitFor(() => expect(screen.getByRole('button', { name: /run #5/i })).toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([url]) => requestUrl(url as RequestInfo | URL) === '/api/tasks/9')).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => requestUrl(url as RequestInfo | URL) === '/api/greeks-landscape/runs/5')).toBe(true);
  });
});

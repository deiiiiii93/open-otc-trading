import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScenarioTestLive } from './ScenarioTest';
import type { ScenarioSetDetail } from '../types';

const library = {
  predefined: [
    {
      key: 'market_crash',
      name: 'Market Crash',
      description: 'Severe market downturn stress scenario',
      num_stresses: 3,
      stresses: [{ param: 'spot', stress_type: 'PERCENTAGE', value: -20, level: 'portfolio', target: null }],
    },
    {
      key: 'rate_shock',
      name: 'Rate Shock',
      description: 'Sudden interest rate increase',
      num_stresses: 2,
      stresses: [{ param: 'rate', stress_type: 'ABSOLUTE', value: 2, level: 'portfolio', target: null }],
    },
  ],
  saved_sets: [],
};

const pricingProfiles = [
  {
    id: 5,
    name: 'Q2 Pricing',
    valuation_date: '2026-06-01',
    source_type: 'xlsx',
    source_path: null,
    status: 'completed',
    summary: {},
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    rows: [],
  },
];

const portfolios = [
  {
    id: 1,
    name: 'Desk Book',
    kind: 'container' as const,
    base_currency: 'CNY',
    positions: [],
  },
];

const completedRun = {
  id: 7,
  portfolio_id: 1,
  pricing_parameter_profile_id: null,
  status: 'completed',
  results: {
    baseline_value: 9876543.21,
    baseline_greeks: null,
    scenarios: [
      {
        name: 'market_crash',
        portfolio_value: 8500000,
        pnl: -1376543.21,
        pnl_pct: -13.94,
        greeks: null,
        underlying_results: {},
        position_results: [],
        execution_time: 0.42,
      },
      {
        name: 'rate_shock',
        portfolio_value: 9200000,
        pnl: -676543.21,
        pnl_pct: -6.85,
        greeks: null,
        underlying_results: {},
        position_results: [],
        execution_time: 0.31,
      },
    ],
    worst_scenario: 'market_crash',
    best_scenario: 'rate_shock',
    risk_summary: {},
    var_cvar: { var: 1000000, cvar: 1300000, confidence: 0.95 },
    num_scenarios: 2,
    execution_time: 1.1,
  },
  excluded_positions: null,
  artifacts: {
    report_html_path: '/srv/data/scenario_reports/run_7_report.html',
    export_paths: ['/srv/data/scenario_exports/run_7_results.csv'],
    notes: ['2 positions priced successfully.'],
  },
  created_at: '2026-06-08T09:00:00Z',
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

describe('ScenarioTestLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the "Scenario Test" heading and predefined scenario labels', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/scenario-test/runs')) return response([]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /scenario test/i })).toBeInTheDocument();
    expect(screen.getByText('Rate Shock')).toBeInTheDocument();
  });

  it('filters predefined scenarios by name and description', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/scenario-test/runs')) return response([]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    const search = screen.getByRole('searchbox', { name: /filter scenarios/i });
    await userEvent.type(search, 'rate');
    await waitFor(() => {
      expect(screen.queryByText('Rate Shock')).toBeInTheDocument();
      expect(screen.queryByText('Market Crash')).not.toBeInTheDocument();
    });
  });

  it('loads run history when a portfolio is selected', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/scenario-test/runs?portfolio_id=1') return response([completedRun]);
      return response([]);
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    // Portfolio auto-selects to first; history lives on the Runs tab
    await userEvent.click(screen.getByRole('tab', { name: /runs/i }));
    await waitFor(() =>
      expect(screen.getByText(/run #7/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/completed/i)).toBeInTheDocument();
  });

  it('the Run button is disabled until a portfolio is selected', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/portfolios') return response([]);
      if (url.startsWith('/api/scenario-test/runs')) return response([]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /run scenario test/i })).toBeDisabled();
  });

  it('posts a run with the selected scenario key and portfolio', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/scenario-test/runs' && init?.method === 'POST') {
        return response(completedRun);
      }
      if (url.startsWith('/api/scenario-test/runs')) return response([completedRun]);
      return response({});
    });

    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());

    // Check the Market Crash checkbox
    await userEvent.click(screen.getByRole('checkbox', { name: /market crash/i }));
    // Click Run
    await userEvent.click(screen.getByRole('button', { name: /run scenario test/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url, init]) => (
        requestUrl(url as RequestInfo | URL) === '/api/scenario-test/runs' &&
        (init as RequestInit | undefined)?.method === 'POST'
      ));
      expect(posted).not.toBeUndefined();
      const body = JSON.parse(String(posted?.[1]?.body ?? '{}')) as {
        portfolio_id?: number;
        predefined?: string[];
      };
      expect(body.portfolio_id).toBe(1);
      expect(body.predefined).toContain('market_crash');
    });
  });

  it('shows worst_scenario from run results', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/scenario-test/runs')) return response([completedRun]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /runs/i }));
    await waitFor(() =>
      expect(screen.getByText(/market_crash/i)).toBeInTheDocument(),
    );
  });

  it('RunDetail shows baseline_value, scenario rows, and artifact download links', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/scenario-test/runs')) return response([completedRun]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);

    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    // Run history lives on the Runs tab
    await userEvent.click(screen.getByRole('tab', { name: /runs/i }));
    // Wait for run list to appear then click run #7 to open RunDetail
    await waitFor(() => expect(screen.getByText(/run #7/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #7/i));

    // Baseline value (NOT baseline_pnl)
    await waitFor(() =>
      expect(screen.getByText(/baseline value/i)).toBeInTheDocument(),
    );
    // The formatted baseline value 9,876,543.21 should be rendered
    expect(screen.getByText(/9[,.]?876[,.]?543/)).toBeInTheDocument();

    // Worst scenario name shown in the detail panel
    expect(screen.getAllByText(/market_crash/).length).toBeGreaterThan(0);

    // Scenario table row for market_crash with its pnl
    // pnl=-1376543.21 formatted as a negative number
    expect(screen.getByText(/-1[,.]?376[,.]?543/)).toBeInTheDocument();

    // pnl_pct is ALREADY a percentage from the engine (-13.94), so it must render
    // as "-13.94%", NOT "-1394.00%" (regression guard for the 100x scaling bug).
    expect(screen.getByText('-13.94%')).toBeInTheDocument();
    expect(screen.queryByText('-1394.00%')).not.toBeInTheDocument();

    // HTML report preview iframe (src includes cache-bust timestamp)
    const reportFrame = screen.getByTitle(/scenario test report for run 7/i);
    expect(reportFrame).toHaveAttribute(
      'src',
      expect.stringMatching(/^\/api\/scenario-test\/runs\/7\/artifacts\/run_7_report\.html\?t=\d+$/),
    );

    // Open standalone tab link
    const standaloneLink = screen.getByRole('link', { name: /open standalone tab/i });
    expect(standaloneLink).toHaveAttribute(
      'href',
      '/api/scenario-test/runs/7/artifacts/run_7_report.html',
    );
    expect(standaloneLink).toHaveAttribute('target', '_blank');

    // Download HTML report link
    const reportLink = screen.getByRole('link', { name: /download html report/i });
    expect(reportLink).toHaveAttribute(
      'href',
      '/api/scenario-test/runs/7/artifacts/run_7_report.html?download=true',
    );
    expect(reportLink).toHaveAttribute('download');

    // Export path download link (basename) includes download flag
    const exportLink = screen.getByRole('link', { name: /run_7_results\.csv/i });
    expect(exportLink).toHaveAttribute(
      'href',
      '/api/scenario-test/runs/7/artifacts/run_7_results.csv?download=true',
    );
    expect(exportLink).toHaveAttribute('download');
  });

  it('RunDetail shows pricing_warnings when present', async () => {
    const runWithWarnings = {
      ...completedRun,
      results: {
        ...(completedRun.results as Record<string, unknown>),
        pricing_warnings: [{ position_id: 5, reason: 'no profile row' }],
      },
    };
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/scenario-test/runs')) return response([runWithWarnings]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);
    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /runs/i }));
    await waitFor(() => expect(screen.getByText(/run #7/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #7/i));

    await waitFor(() =>
      expect(screen.getByText(/pricing warnings/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/no profile row/i)).toBeInTheDocument();
  });

  it('opens a detail dialog when a predefined scenario name is clicked', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/scenario-test/sets') return response([]);
      if (url.startsWith('/api/scenario-test/runs')) return response([]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);
    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /details for market crash/i }));
    expect(await screen.findByRole('dialog', { name: /market crash/i })).toBeInTheDocument();
    expect(screen.getByText('Spot')).toBeInTheDocument();
  });

  it('RunDetail does not crash when var_cvar is an error payload', async () => {
    const erroredRun = {
      ...completedRun,
      results: {
        ...(completedRun.results as Record<string, unknown>),
        // backend persists {error: ...} when VaR/CVaR computation failed
        var_cvar: { error: 'cvar failed' },
      },
    };
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url.startsWith('/api/scenario-test/runs')) return response([erroredRun]);
      return response({});
    }) as unknown as typeof fetch;

    render(<ScenarioTestLive />);
    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /runs/i }));
    await waitFor(() => expect(screen.getByText(/run #7/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/run #7/i));

    // Baseline still renders (panel didn't crash) and the VaR row is omitted.
    await waitFor(() =>
      expect(screen.getByText(/baseline value/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/VaR \/ CVaR/i)).not.toBeInTheDocument();
  });

  it('creates a custom scenario and includes it in a run', async () => {
    const customAfter: ScenarioSetDetail[] = [
      { name: 'My_Shock', description: '', stresses: [
        { param: 'spot', stress_type: 'PERCENTAGE', value: -10, level: 'portfolio', target: null }] },
    ];
    let setsCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/scenario-test/sets' && init?.method === 'POST') return response({ name: 'My_Shock', path: '/x.yaml' });
      if (url === '/api/scenario-test/sets') { setsCalls += 1; return response(setsCalls === 1 ? [] : customAfter); }
      if (url === '/api/scenario-test/runs' && init?.method === 'POST') return response(completedRun);
      if (url.startsWith('/api/scenario-test/runs')) return response([completedRun]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ScenarioTestLive />);
    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /new custom scenario/i }));
    await userEvent.type(screen.getByLabelText(/scenario name/i), 'My Shock');
    await userEvent.type(screen.getByLabelText(/^value 0$/i), '-10');
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(screen.getByText('My_Shock')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('checkbox', { name: /my_shock/i }));
    await userEvent.click(screen.getByRole('button', { name: /run scenario test/i }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([u, i]) =>
        requestUrl(u as RequestInfo | URL) === '/api/scenario-test/runs' &&
        (i as RequestInit | undefined)?.method === 'POST');
      const body = JSON.parse(String((posted?.[1] as RequestInit)?.body ?? '{}'));
      expect(body.scenario_sets).toContain('My_Shock');
    });
  });

  it('lists scenario sets with a count badge and runs the selected set', async () => {
    const sets = [
      { name: 'spot_vol', num_scenarios: 9, combine_mode: 'cross_product',
        axes_summary: 'spot × vol', has_grid: true,
        axes: [{ param: 'spot', start: -0.2, stop: 0.2, step: 0.1, stress_type: 'PERCENTAGE', level: 'portfolio', target: null }] },
    ];
    let postedBody: Record<string, unknown> | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/scenario-test/library') return response(library);
      if (url === '/api/pricing-parameter-profiles') return response(pricingProfiles);
      if (url === '/api/portfolios') return response(portfolios);
      if (url === '/api/scenario-test/sets/full') return response(sets);
      if (url === '/api/scenario-test/sets') return response([]);
      if (url === '/api/scenario-test/runs' && init?.method === 'POST') {
        postedBody = JSON.parse(String(init?.body)); return response({ ...completedRun, id: 11, status: 'queued' });
      }
      if (url.startsWith('/api/scenario-test/runs')) return response([]);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ScenarioTestLive />);
    await waitFor(() => expect(screen.getByText('Market Crash')).toBeInTheDocument());

    // Set row + badge
    await waitFor(() => expect(screen.getByText('spot_vol')).toBeInTheDocument());
    expect(screen.getByText(/spot × vol · 9/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('checkbox', { name: 'spot_vol' }));
    await userEvent.click(screen.getByRole('button', { name: /run scenario test/i }));

    await waitFor(() => expect((postedBody?.scenario_sets as string[] | undefined)).toContain('spot_vol'));
  });
});

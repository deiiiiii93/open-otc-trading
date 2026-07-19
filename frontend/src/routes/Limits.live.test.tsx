import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import { LimitsLive } from './Limits.live';
import { parseServerDateTime } from './limitsDateTime';

const AS_OF = '2026-07-18T09:00:00';

const PORTFOLIOS = [
  {
    id: 1,
    name: 'Macro Book',
    kind: 'container',
    base_currency: 'USD',
    positions: [],
  },
  {
    id: 2,
    name: 'Options Book',
    kind: 'container',
    base_currency: 'USD',
    positions: [],
  },
];

const PRICING_PROFILES = [
  {
    id: 11,
    name: 'Morning marks',
    valuation_date: '2026-07-18',
    source_type: 'xlsx',
    source_path: null,
    status: 'completed',
    summary: {},
    created_at: AS_OF,
    updated_at: AS_OF,
    rows: [],
  },
];

const ENGINE_CONFIGS = [
  {
    id: 21,
    name: 'Desk default',
    description: null,
    status: 'active',
    is_default: true,
    rules: {},
    business_days_in_year: 252,
    created_at: AS_OF,
    updated_at: AS_OF,
  },
];

const MARKET_SNAPSHOTS = [
  {
    id: 31,
    name: 'Morning close',
    source: 'manual',
    symbol: 'SPX',
    asset_class: 'index',
    valuation_date: AS_OF,
    data: { spot: 6000 },
    source_metadata: { evidence_id: 'manual:spx:20260718' },
    created_at: AS_OF,
  },
];

const ACTIVE_VERSION = {
  id: 101,
  risk_limit_id: 41,
  version: 1,
  state: 'active',
  metric_kind: 'delta',
  source_kind: 'risk_run',
  methodology: {},
  scope_type: 'portfolio',
  scope_config: { portfolio_ids: [1, 2] },
  aggregation: 'net',
  transform: 'absolute',
  comparator: 'upper',
  warning_lower: null,
  warning_upper: 80,
  hard_lower: null,
  hard_upper: 100,
  unit: 'underlying_units',
  currency: null,
  bump_convention: null,
  freshness_policy: { max_age_seconds: 300 },
  effective_from: '2026-07-18T08:00:00',
  effective_until: null,
  rationale: 'Desk delta envelope',
  created_at: '2026-07-17T08:00:00',
  activated_at: '2026-07-18T08:00:00',
};

const LIMIT = {
  id: 41,
  key: 'desk-delta',
  name: 'Desk Delta',
  description: 'Portfolio delta limit',
  category: 'greek',
  owner: 'market-risk',
  tags: ['intraday'],
  active_version_id: 101,
  row_version: 2,
  created_at: '2026-07-17T08:00:00',
  updated_at: '2026-07-18T08:00:00',
  versions: [ACTIVE_VERSION],
  active_version: ACTIVE_VERSION,
};

const SOURCE_REFERENCE = {
  id: 91,
  source_kind: 'risk_run',
  risk_run_id: 301,
  scenario_test_run_id: null,
  backtest_run_id: null,
  requested_parameters: { method: 'greeks' },
  source_status: 'completed',
  is_fresh: true,
  completeness_diagnostics: {
    reason: 'risk source complete',
    requested_positions: 4,
    covered_positions: 4,
  },
  source_valuation_at: AS_OF,
  source_created_at: AS_OF,
  created_at: AS_OF,
};

const MONITORING_RUN = {
  id: 51,
  trigger: 'manual',
  mode: 'interactive',
  portfolio_id: 1,
  pricing_parameter_profile_id: 11,
  engine_config_id: 21,
  market_snapshot_id: 31,
  effective_market_evidence_id: 'manual:spx:20260718',
  valuation_as_of: AS_OF,
  source_policy: 'refresh_if_stale',
  max_source_age_seconds: 300,
  status: 'completed',
  summary: { ok: 1, warning: 1, breach: 1, unknown: 1 },
  definition_snapshot_hash: `sha256:${'a'.repeat(64)}`,
  limit_version_ids: [101],
  task_id: 61,
  started_at: '2026-07-18T09:00:01',
  finished_at: '2026-07-18T09:00:04',
  created_at: AS_OF,
  source_references: [SOURCE_REFERENCE],
};

function evaluation(
  id: number,
  label: string,
  status: 'ok' | 'warning' | 'breach' | 'unknown',
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    monitoring_run_id: 51,
    limit_version_id: 101,
    scope_type: 'portfolio',
    scope_key: 'portfolio:1',
    scope_label: label,
    observed_value: status === 'unknown' ? null : 40,
    adverse_value: status === 'unknown' ? null : 40,
    warning_lower: null,
    warning_upper: 80,
    hard_lower: null,
    hard_upper: 100,
    utilization:
      status === 'breach' ? 1.1 : status === 'warning' ? 0.85 : status === 'ok' ? 0.4 : null,
    headroom:
      status === 'breach' ? -10 : status === 'warning' ? 15 : status === 'ok' ? 60 : null,
    governing_boundary: status === 'unknown' ? null : 'upper',
    status,
    reason_code: status === 'unknown' ? 'source_incomplete' : null,
    reason: status === 'unknown' ? 'Two positions have no fresh risk evidence' : null,
    coverage_count: status === 'unknown' ? 2 : 4,
    coverage_ratio: status === 'unknown' ? 0.5 : 1,
    evidence: {
      source_reference_id: 91,
      source_created_at: AS_OF,
      is_fresh: status !== 'unknown',
    },
    evaluated_at: AS_OF,
    ...overrides,
  };
}

const EVALUATIONS = [
  evaluation(71, 'Portfolio Delta', 'ok'),
  evaluation(72, 'Portfolio Rho', 'warning', { observed_value: 85, adverse_value: 85 }),
  evaluation(73, 'Portfolio RhoQ', 'breach', { observed_value: 110, adverse_value: 110 }),
  evaluation(74, 'Portfolio VaR', 'unknown', { limit_version_id: 102 }),
];

const OPEN_EVENT = {
  id: 401,
  incident_id: 81,
  event_type: 'opened',
  evaluation_id: 73,
  actor: 'limit_monitor',
  persona: null,
  mode: 'interactive',
  thread_id: null,
  audit_ref: 'audit:401',
  payload: {},
  created_at: AS_OF,
};

function incident(
  overrides: Record<string, unknown> = {},
) {
  return {
    id: 81,
    risk_limit_id: 41,
    portfolio_id: 1,
    scope_type: 'portfolio',
    scope_key: 'portfolio:1',
    scope_label: 'Macro Book',
    severity: 'breach',
    status: 'open',
    first_evaluation_id: 73,
    last_evaluation_id: 73,
    first_seen_at: AS_OF,
    last_seen_at: AS_OF,
    acknowledged_at: null,
    waived_at: null,
    resolved_at: null,
    owner: 'market-risk',
    assignee: null,
    waiver_expires_at: null,
    waiver_rationale: null,
    row_version: 1,
    created_at: AS_OF,
    updated_at: AS_OF,
    risk_limit: LIMIT,
    events: [OPEN_EVENT],
    ...overrides,
  };
}

const OPEN_INCIDENT = incident();

function dashboardFor(portfolioId = 1, overrides: Record<string, unknown> = {}) {
  return {
    summary: {
      breaches: 1,
      warnings: 1,
      unknowns: 1,
      ok: 1,
      highest_utilization: 1.1,
      active_incidents: 1,
    },
    current_evaluations: EVALUATIONS,
    evaluation_groups: [
      { category: 'greek', evaluations: EVALUATIONS.slice(0, 3) },
      { category: 'var', evaluations: EVALUATIONS.slice(3) },
    ],
    current_evidence_run: { ...MONITORING_RUN, portfolio_id: portfolioId },
    latest_run: { ...MONITORING_RUN, portfolio_id: portfolioId },
    active_incidents: [{ ...OPEN_INCIDENT, portfolio_id: portfolioId }],
    trends: [
      {
        run_id: 51,
        created_at: AS_OF,
        status: 'completed',
        summary: MONITORING_RUN.summary,
      },
    ],
    ...overrides,
  };
}

const EMPTY_DASHBOARD = dashboardFor(1, {
  summary: {
    breaches: 0,
    warnings: 0,
    unknowns: 0,
    ok: 0,
    highest_utilization: null,
    active_incidents: 0,
  },
  current_evaluations: [],
  evaluation_groups: [],
  current_evidence_run: null,
  latest_run: null,
  active_incidents: [],
  trends: [],
});

const QUEUED_RUN = {
  ...MONITORING_RUN,
  status: 'queued',
  summary: {},
  started_at: null,
  finished_at: null,
};

const UNKNOWN_RUN = {
  ...MONITORING_RUN,
  status: 'completed_with_unknowns',
  summary: { ok: 1, warning: 1, breach: 1, unknown: 1 },
};

const COMPLETED_TASK = {
  id: 61,
  kind: 'limit_monitoring',
  status: 'completed',
  portfolio_id: 1,
  risk_run_id: null,
  greeks_landscape_run_id: null,
  report_job_id: null,
  limit_monitoring_run_id: 51,
  progress_current: 4,
  progress_total: 4,
  message: 'Limit monitoring completed with unknown evaluations',
  error: null,
  result_payload: {},
  created_at: AS_OF,
  started_at: '2026-07-18T09:00:01',
  finished_at: '2026-07-18T09:00:04',
};

type ApiRequest = {
  url: URL;
  method: string;
  init: RequestInit;
  body: unknown;
};

type ApiOverride = (
  request: ApiRequest,
) => Response | Promise<Response> | undefined;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function toRequest(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
): ApiRequest {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof Request
        ? input.url
        : input.toString();
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
  const bodyText = init?.body == null ? null : String(init.body);
  return {
    url: new URL(raw, 'http://limits.test'),
    method,
    init: init ?? {},
    body: bodyText ? JSON.parse(bodyText) : null,
  };
}

function defaultResponse(request: ApiRequest): Response {
  const { pathname } = request.url;
  const portfolioId = Number(request.url.searchParams.get('portfolio_id') ?? 1);

  if (request.method === 'GET' && pathname === '/api/portfolios') return json(PORTFOLIOS);
  if (request.method === 'GET' && pathname === '/api/pricing-parameter-profiles') {
    return json(PRICING_PROFILES);
  }
  if (request.method === 'GET' && pathname === '/api/engine-configs') return json(ENGINE_CONFIGS);
  if (request.method === 'GET' && pathname === '/api/market-data/snapshots') {
    return json(MARKET_SNAPSHOTS);
  }
  if (request.method === 'GET' && pathname === '/api/limit-monitoring/dashboard') {
    return json(dashboardFor(portfolioId));
  }
  if (request.method === 'GET' && pathname === '/api/limit-monitoring/summary') {
    return json({
      ...dashboardFor(portfolioId).summary,
      latest_run: { ...MONITORING_RUN, portfolio_id: portfolioId },
      latest_incident_event_id: 401,
    });
  }
  if (request.method === 'GET' && pathname === '/api/limit-monitoring/runs') {
    return json({ items: [{ ...MONITORING_RUN, portfolio_id: portfolioId }], total: 1 });
  }
  if (request.method === 'POST' && pathname === '/api/limit-monitoring/runs') {
    return json({ ...QUEUED_RUN, portfolio_id: portfolioId }, 202);
  }
  if (request.method === 'GET' && pathname === '/api/limit-monitoring/runs/51') {
    return json({ ...UNKNOWN_RUN, portfolio_id: portfolioId });
  }
  if (
    request.method === 'GET'
    && pathname === '/api/limit-monitoring/runs/51/evaluations'
  ) {
    return json({ items: EVALUATIONS, total: EVALUATIONS.length });
  }
  if (request.method === 'GET' && pathname === '/api/tasks/61') {
    return json({ ...COMPLETED_TASK, portfolio_id: portfolioId });
  }
  if (request.method === 'GET' && pathname === '/api/limits') {
    return json({ items: [LIMIT], total: 1 });
  }
  if (request.method === 'GET' && pathname === '/api/limits/41') return json(LIMIT);
  if (request.method === 'GET' && pathname === '/api/limits/41/versions') {
    return json(LIMIT.versions);
  }
  if (
    request.method === 'GET'
    && pathname.startsWith('/api/limit-evaluations/')
  ) {
    const evaluationId = Number(pathname.split('/').at(-1));
    const found = EVALUATIONS.find((item) => item.id === evaluationId);
    return found ? json(found) : json({ detail: 'not found' }, 404);
  }
  if (request.method === 'GET' && pathname === '/api/limit-incidents') {
    return json({
      items: [{ ...OPEN_INCIDENT, portfolio_id: portfolioId }],
      total: 1,
    });
  }
  if (request.method === 'GET' && pathname === '/api/limit-incidents/81') {
    return json({ ...OPEN_INCIDENT, portfolio_id: portfolioId });
  }

  throw new Error(`Unexpected Limits API request: ${request.method} ${request.url}`);
}

function installApi(override?: ApiOverride) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const request = toRequest(input, init);
      const overridden = override?.(request);
      if (overridden !== undefined) return overridden;
      return defaultResponse(request);
    },
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function requests(
  fetchMock: ReturnType<typeof vi.fn>,
  pathname: string,
  method?: string,
): ApiRequest[] {
  return fetchMock.mock.calls
    .map(([input, init]) => toRequest(input as RequestInfo | URL, init as RequestInit | undefined))
    .filter((request) => (
      request.url.pathname === pathname
      && (method == null || request.method === method)
    ));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  window.history.replaceState(null, '', '/limits?tab=monitor');
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/positions');
});

describe('LimitsLive monitor', () => {
  it('renders a stable loading state before the dashboard resolves', async () => {
    const gate = deferred<Response>();
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
      ) {
        return gate.promise;
      }
      return undefined;
    });

    render(<LimitsLive />);

    expect(screen.getByText(/loading limits/i)).toBeInTheDocument();
    gate.resolve(json(EMPTY_DASHBOARD));
    expect(await screen.findByText(/no monitoring data/i)).toBeInTheDocument();
  });

  it('separates an empty dashboard from a load error', async () => {
    installApi((request) => {
      if (request.url.pathname === '/api/limit-monitoring/dashboard') {
        return json(EMPTY_DASHBOARD);
      }
      return undefined;
    });
    const empty = render(<LimitsLive />);
    expect(await screen.findByText(/no monitoring data/i)).toBeInTheDocument();
    empty.unmount();

    installApi((request) => {
      if (request.url.pathname === '/api/limit-monitoring/dashboard') {
        return json({ detail: 'dashboard exploded' }, 500);
      }
      return undefined;
    });
    render(<LimitsLive />);
    expect(await screen.findByText(/dashboard exploded/i)).toBeInTheDocument();
  });

  it('shows summary counts, grouped status semantics, freshness and evidence', async () => {
    installApi();
    render(<LimitsLive />);

    const delta = await screen.findByText('Portfolio Delta');
    for (const [label, expected] of [
      ['Breaches', '1'],
      ['Warnings', '1'],
      ['Unknowns', '1'],
      ['OK', '1'],
    ]) {
      const tile = screen.getByText(label).closest('.wl-tile');
      expect(tile).not.toBeNull();
      expect(within(tile as HTMLElement).getByText(expected)).toBeInTheDocument();
    }

    const deltaRow = delta.closest('[role="row"]');
    const rhoRow = screen.getByText('Portfolio Rho').closest('[role="row"]');
    const rhoQRow = screen.getByText('Portfolio RhoQ').closest('[role="row"]');
    const varRow = screen.getByText('Portfolio VaR').closest('[role="row"]');
    expect(within(deltaRow as HTMLElement).getByText(/^ok$/i)).toBeInTheDocument();
    expect(within(rhoRow as HTMLElement).getByText(/^warning$/i)).toBeInTheDocument();
    expect(within(rhoQRow as HTMLElement).getByText(/^breach$/i)).toBeInTheDocument();
    expect(within(varRow as HTMLElement).getByText(/^unknown$/i)).toBeInTheDocument();
    expect(within(varRow as HTMLElement).getByText(/50%/i)).toBeInTheDocument();
    expect(within(varRow as HTMLElement).getByText(/two positions/i)).toBeInTheDocument();
    expect(screen.getByText(/greek limits/i)).toBeInTheDocument();
    expect(screen.getByText(/var limits/i)).toBeInTheDocument();

    await userEvent.click(
      within(deltaRow as HTMLElement).getByRole('button', { name: /evidence/i }),
    );
    const evidence = await screen.findByRole('dialog', { name: /limit evidence/i });
    expect(within(evidence).getByText(MONITORING_RUN.definition_snapshot_hash)).toBeInTheDocument();
    expect(within(evidence).getByText('manual:spx:20260718')).toBeInTheDocument();
    expect(within(evidence).getByText(/risk source complete/i)).toBeInTheDocument();
    expect(within(evidence).getByText(/fresh/i)).toBeInTheDocument();
  });

  it('binds dashboard evaluations to their own immutable run, not a newer queued run', async () => {
    const newerQueued = {
      ...QUEUED_RUN,
      id: 52,
      task_id: 62,
      definition_snapshot_hash: `sha256:${'b'.repeat(64)}`,
      source_references: [],
    };
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
      ) {
        return json(dashboardFor(1, { latest_run: newerQueued }));
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/51'
      ) {
        return json(MONITORING_RUN);
      }
      return undefined;
    });
    render(<LimitsLive />);

    const delta = await screen.findByText('Portfolio Delta');
    expect(screen.getByText(/run #51/i)).toBeInTheDocument();
    await userEvent.click(
      within(delta.closest('[role="row"]') as HTMLElement)
        .getByRole('button', { name: /evidence/i }),
    );
    const evidence = await screen.findByRole('dialog', { name: /limit evidence/i });
    expect(
      within(evidence).getByText(MONITORING_RUN.definition_snapshot_hash),
    ).toBeInTheDocument();
    expect(
      within(evidence).queryByText(newerQueued.definition_snapshot_hash),
    ).not.toBeInTheDocument();
  });

  it('keeps zero-evaluation dashboard evidence bound to its terminal run', async () => {
    const newerQueued = {
      ...QUEUED_RUN,
      id: 52,
      task_id: 62,
      definition_snapshot_hash: `sha256:${'b'.repeat(64)}`,
      source_references: [],
    };
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
      ) {
        return json(dashboardFor(1, {
          summary: {
            breaches: 0,
            warnings: 0,
            unknowns: 0,
            ok: 0,
            highest_utilization: null,
            active_incidents: 0,
          },
          current_evaluations: [],
          evaluation_groups: [],
          current_evidence_run: MONITORING_RUN,
          latest_run: newerQueued,
        }));
      }
      return undefined;
    });
    render(<LimitsLive />);

    expect(await screen.findByText(/run #51/i)).toBeInTheDocument();
    expect(screen.queryByText(/run #52/i)).not.toBeInTheDocument();
    expect(screen.getByText(/current evidence/i)).toBeInTheDocument();
    expect(screen.getByText(/^completed$/i)).toBeInTheDocument();
  });

  it('passes typed scenario source inputs through the frozen monitoring envelope', async () => {
    const fetchMock = installApi();
    render(<LimitsLive portfolioId={1} />);

    await screen.findByText('Portfolio Delta');
    fireEvent.change(screen.getByLabelText('Source inputs'), {
      target: {
        value: JSON.stringify({
          scenario_test: {
            scenario_request: { scenario_set_id: 44 },
            config: { workers: 2 },
          },
        }),
      },
    });
    await userEvent.click(screen.getByRole('button', { name: /^run now$/i }));

    await waitFor(() => {
      expect(
        requests(fetchMock, '/api/limit-monitoring/runs', 'POST'),
      ).toHaveLength(1);
    });
    expect(
      requests(fetchMock, '/api/limit-monitoring/runs', 'POST')[0].body,
    ).toMatchObject({
      source_inputs: {
        scenario_test: {
          scenario_request: { scenario_set_id: 44 },
          config: { workers: 2 },
        },
      },
    });
  });

  it('honors a valid preferred portfolio without briefly loading the fallback', async () => {
    const fetchMock = installApi();
    render(<LimitsLive portfolioId={2} />);

    expect(await screen.findByLabelText('Portfolio')).toHaveValue('2');
    await waitFor(() => {
      const dashboardRequests = requests(
        fetchMock,
        '/api/limit-monitoring/dashboard',
        'GET',
      );
      expect(dashboardRequests.length).toBeGreaterThan(0);
      expect(
        dashboardRequests.every(
          (request) => request.url.searchParams.get('portfolio_id') === '2',
        ),
      ).toBe(true);
    });
  });

  it('falls back silently, but reports an explicit portfolio change', async () => {
    const onPortfolioIdChange = vi.fn();
    installApi();
    render(
      <LimitsLive
        portfolioId={999}
        onPortfolioIdChange={onPortfolioIdChange}
      />,
    );

    const picker = await screen.findByLabelText('Portfolio');
    expect(picker).toHaveValue('1');
    expect(onPortfolioIdChange).not.toHaveBeenCalled();

    await userEvent.selectOptions(picker, '2');
    expect(onPortfolioIdChange).toHaveBeenCalledWith(2);
    await waitFor(() => expect(picker).toHaveValue('2'));
  });

  it('clears prior-portfolio evidence while the new portfolio is loading', async () => {
    const nextDashboard = deferred<Response>();
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
        && request.url.searchParams.get('portfolio_id') === '2'
      ) {
        return nextDashboard.promise;
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await userEvent.selectOptions(screen.getByLabelText('Portfolio'), '2');

    await waitFor(() => {
      expect(screen.queryByText('Portfolio Delta')).not.toBeInTheDocument();
    });
    expect(screen.getByText(/loading limits/i)).toBeInTheDocument();
    nextDashboard.resolve(json({ ...EMPTY_DASHBOARD, latest_run: null }));
    expect(await screen.findByText(/no monitoring data/i)).toBeInTheDocument();
  });

  it('posts explicit evidence inputs, polls TaskRun, then treats completed_with_unknowns as success', async () => {
    let submitted = false;
    const fetchMock = installApi((request) => {
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-monitoring/runs'
      ) {
        submitted = true;
        return json(QUEUED_RUN, 202);
      }
      if (
        submitted
        && request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
      ) {
        return json(
          dashboardFor(1, {
            latest_run: UNKNOWN_RUN,
          }),
        );
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await userEvent.selectOptions(screen.getByLabelText('Pricing profile'), '11');
    await userEvent.selectOptions(screen.getByLabelText('Engine config'), '21');
    await userEvent.selectOptions(screen.getByLabelText('Market snapshot'), '31');
    await userEvent.selectOptions(screen.getByLabelText('Source policy'), 'force_refresh');
    fireEvent.change(screen.getByLabelText('Valuation as of'), {
      target: { value: '2026-07-18T10:15' },
    });
    await userEvent.clear(screen.getByLabelText(/max source age/i));
    await userEvent.type(screen.getByLabelText(/max source age/i), '600');
    await userEvent.click(screen.getByRole('button', { name: /^run now$/i }));

    await waitFor(() => {
      expect(
        requests(fetchMock, '/api/limit-monitoring/runs', 'POST'),
      ).toHaveLength(1);
    });
    const posted = requests(fetchMock, '/api/limit-monitoring/runs', 'POST')[0].body;
    expect(posted).toEqual({
      portfolio_id: 1,
      pricing_parameter_profile_id: 11,
      engine_config_id: 21,
      market_snapshot_id: 31,
      valuation_as_of: new Date('2026-07-18T10:15').toISOString(),
      source_policy: 'force_refresh',
      max_source_age_seconds: 600,
      source_inputs: {},
    });

    await waitFor(() => {
      expect(requests(fetchMock, '/api/tasks/61', 'GET')).toHaveLength(1);
      expect(
        requests(fetchMock, '/api/limit-monitoring/runs/51', 'GET').length,
      ).toBeGreaterThan(0);
    });
    expect(await screen.findByText(/completed with unknowns/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^run now$/i })).toBeEnabled();

    await waitFor(() => {
      expect(
        requests(fetchMock, '/api/limit-monitoring/dashboard', 'GET').length,
      ).toBeGreaterThan(1);
      expect(
        requests(
          fetchMock,
          '/api/limit-monitoring/runs/51/evaluations',
          'GET',
        ).length,
      ).toBeGreaterThan(0);
      expect(
        requests(fetchMock, '/api/limit-incidents', 'GET').length,
      ).toBeGreaterThan(0);
    });
  });

  it('keeps the authoritative terminal run and task error when auxiliary refreshes fail', async () => {
    let submitted = false;
    let allowRecovery = false;
    const failedRun = {
      ...MONITORING_RUN,
      status: 'failed',
      summary: {},
      finished_at: '2026-07-18T09:00:04',
    };
    const failedTask = {
      ...COMPLETED_TASK,
      status: 'failed',
      message: 'Limit monitoring failed',
      error: 'Risk engine unavailable',
    };
    installApi((request) => {
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-monitoring/runs'
      ) {
        submitted = true;
        return json(QUEUED_RUN, 202);
      }
      if (
        submitted
        && request.method === 'GET'
        && request.url.pathname === '/api/tasks/61'
      ) {
        return json(failedTask);
      }
      if (
        submitted
        && !allowRecovery
        && request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/51'
      ) {
        return json(failedRun);
      }
      if (
        submitted
        && !allowRecovery
        && request.method === 'GET'
        && (
          request.url.pathname === '/api/limit-monitoring/dashboard'
          || request.url.pathname === '/api/limit-monitoring/runs/51/evaluations'
          || request.url.pathname === '/api/limit-incidents'
        )
      ) {
        return json({ detail: 'Auxiliary refresh unavailable' }, 503);
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await userEvent.click(screen.getByRole('button', { name: /^run now$/i }));

    expect(await screen.findByText(/^failed$/i)).toBeInTheDocument();
    expect(await screen.findByText(/risk engine unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/run #51/i)).toBeInTheDocument();
    expect(screen.queryByText(/^auxiliary refresh unavailable$/i))
      .not.toBeInTheDocument();

    allowRecovery = true;
    await userEvent.click(
      screen.getByRole('tab', { name: /^definitions$/i }),
    );
    await screen.findByText('Desk Delta');
    await userEvent.click(screen.getByRole('tab', { name: /^monitor$/i }));

    await waitFor(() => {
      expect(screen.queryByText(/risk engine unavailable/i))
        .not.toBeInTheDocument();
      expect(screen.getByText(/run #51/i)).toBeInTheDocument();
    });
  });

  it('does not let active polling overwrite historical evaluation navigation', async () => {
    const taskGate = deferred<Response>();
    const queuedRun = {
      ...QUEUED_RUN,
      id: 52,
      task_id: 62,
    };
    const completedRun = {
      ...MONITORING_RUN,
      id: 52,
      task_id: 62,
    };
    const fetchMock = installApi((request) => {
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-monitoring/runs'
      ) {
        return json(queuedRun, 202);
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/tasks/62'
      ) {
        return taskGate.promise;
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52'
      ) {
        return json(completedRun);
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52/evaluations'
      ) {
        return json({ items: [], total: 0 });
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await userEvent.click(screen.getByRole('button', { name: /^run now$/i }));
    await waitFor(() => {
      expect(requests(fetchMock, '/api/tasks/62', 'GET')).toHaveLength(1);
    });

    await userEvent.click(screen.getByRole('tab', { name: /breaches/i }));
    await userEvent.click(
      await screen.findByRole('button', { name: /evaluation #73/i }),
    );
    expect(
      await screen.findByRole('dialog', { name: /limit evidence/i }),
    ).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get('run')).toBe('51');
    expect(screen.getByRole('button', { name: /^running…$/i })).toBeDisabled();

    taskGate.resolve(json({
      ...COMPLETED_TASK,
      id: 62,
      limit_monitoring_run_id: 52,
    }));
    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get('run')).toBe('51');
      expect(screen.getByText(/run #51/i)).toBeInTheDocument();
      expect(screen.getByText(/^run #52 completed\.$/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^run now$/i })).toBeEnabled();
    });
  });

  it('resumes tracking an active dashboard task and blocks overlapping runs', async () => {
    const taskGate = deferred<Response>();
    let completed = false;
    const queuedRun = {
      ...QUEUED_RUN,
      id: 52,
      task_id: 62,
    };
    const completedRun = {
      ...MONITORING_RUN,
      id: 52,
      task_id: 62,
    };
    const fetchMock = installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
      ) {
        return completed
          ? json({ detail: 'Dashboard refresh unavailable' }, 503)
          : json(dashboardFor(1, { latest_run: queuedRun }));
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/tasks/62'
      ) {
        return taskGate.promise;
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52'
      ) {
        return json(completedRun);
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52/evaluations'
      ) {
        return json({ items: [], total: 0 });
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await waitFor(() => {
      expect(requests(fetchMock, '/api/tasks/62', 'GET')).toHaveLength(1);
    });
    expect(screen.getByRole('button', { name: /^running…$/i })).toBeDisabled();
    expect(
      requests(fetchMock, '/api/limit-monitoring/runs', 'POST'),
    ).toHaveLength(0);

    completed = true;
    taskGate.resolve(json({
      ...COMPLETED_TASK,
      id: 62,
      limit_monitoring_run_id: 52,
    }));

    expect(
      await screen.findByText(
        /run #52 completed.*dashboard refresh unavailable/i,
      ),
    )
      .toBeInTheDocument();
    expect(screen.getByText(/^run #51$/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^run now$/i })).toBeEnabled();
    });
    expect(requests(fetchMock, '/api/tasks/62', 'GET')).toHaveLength(1);
  });

  it('restores cached evidence and ignores a stale implicit load when refresh fails', async () => {
    const staleDashboardGate = deferred<Response>();
    const taskGate = deferred<Response>();
    let dashboardRequests = 0;
    const queuedRun = {
      ...QUEUED_RUN,
      id: 52,
      task_id: 62,
    };
    const completedRun = {
      ...MONITORING_RUN,
      id: 52,
      task_id: 62,
    };
    const fetchMock = installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/dashboard'
      ) {
        dashboardRequests += 1;
        if (dashboardRequests === 1) return json(dashboardFor(1));
        if (dashboardRequests === 2) return staleDashboardGate.promise;
        return json({ detail: 'Dashboard refresh unavailable' }, 503);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-monitoring/runs'
      ) {
        return json(queuedRun, 202);
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/tasks/62'
      ) {
        return taskGate.promise;
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52'
      ) {
        return json(completedRun);
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52/evaluations'
      ) {
        return json({ items: [], total: 0 });
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await userEvent.click(screen.getByRole('button', { name: /^run now$/i }));
    await waitFor(() => {
      expect(requests(fetchMock, '/api/tasks/62', 'GET')).toHaveLength(1);
    });

    window.history.pushState(null, '', '/limits?tab=monitor');
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));
    await waitFor(() => expect(dashboardRequests).toBe(2));

    taskGate.resolve(json({
      ...COMPLETED_TASK,
      id: 62,
      limit_monitoring_run_id: 52,
    }));
    expect(
      await screen.findByText(
        /run #52 completed.*dashboard refresh unavailable/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/^run #51$/i)).toBeInTheDocument();

    await act(async () => {
      staleDashboardGate.resolve(
        json(dashboardFor(1, { latest_run: queuedRun })),
      );
      await staleDashboardGate.promise;
    });
    await waitFor(() => {
      expect(screen.getByText(/^run #51$/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^run now$/i })).toBeEnabled();
      expect(requests(fetchMock, '/api/tasks/62', 'GET')).toHaveLength(1);
    });
  });

  it('retries transient task polling failures without permitting overlap', async () => {
    let submitted = false;
    let attempts = 0;
    installApi((request) => {
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-monitoring/runs'
      ) {
        submitted = true;
        return json(QUEUED_RUN, 202);
      }
      if (
        submitted
        && request.method === 'GET'
        && request.url.pathname === '/api/tasks/61'
      ) {
        attempts += 1;
        return attempts === 1
          ? json({ detail: 'Task service unavailable' }, 503)
          : json(COMPLETED_TASK);
      }
      return undefined;
    });
    render(<LimitsLive />);

    await screen.findByText('Portfolio Delta');
    await userEvent.click(screen.getByRole('button', { name: /^run now$/i }));

    expect(await screen.findByText(/status unavailable; retrying/i))
      .toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^running…$/i })).toBeDisabled();
    expect(
      await screen.findByText(/completed with unknowns/i, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(attempts).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole('button', { name: /^run now$/i })).toBeEnabled();
  });

  it('loads a URL-selected run and responds to same-route popstate', async () => {
    window.history.replaceState(
      null,
      '',
      '/limits?portfolio=1&tab=monitor&run=51',
    );
    const fetchMock = installApi();
    render(<LimitsLive portfolioId={1} />);

    expect(await screen.findByText(/run #51/i)).toBeInTheDocument();
    expect(requests(fetchMock, '/api/limit-monitoring/runs/51', 'GET')).toHaveLength(1);
    expect(
      requests(
        fetchMock,
        '/api/limit-monitoring/runs/51/evaluations',
        'GET',
      ).length,
    ).toBeGreaterThan(0);

    window.history.pushState(
      null,
      '',
      '/limits?portfolio=1&tab=monitor',
    );
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));
    await waitFor(() => {
      expect(screen.queryByText(/selected run #51/i)).not.toBeInTheDocument();
    });
  });

  it('closes deep-linked evaluation evidence when Back removes the evaluation', async () => {
    window.history.replaceState(
      null,
      '',
      '/limits?portfolio=1&tab=monitor&run=51&evaluation=73',
    );
    installApi();
    render(<LimitsLive portfolioId={1} />);

    expect(
      await screen.findByRole('dialog', { name: /limit evidence/i }),
    ).toBeInTheDocument();

    window.history.pushState(
      null,
      '',
      '/limits?portfolio=1&tab=monitor&run=51',
    );
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));

    await waitFor(() => {
      expect(
        screen.queryByRole('dialog', { name: /limit evidence/i }),
      ).not.toBeInTheDocument();
    });
  });

  it('closes manual evidence on a run change and reports its focus to agents', async () => {
    const nextRun = { ...MONITORING_RUN, id: 52 };
    const onPageContextChange = vi.fn();
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52'
      ) {
        return json(nextRun);
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/52/evaluations'
      ) {
        return json({ items: [], total: 0 });
      }
      return undefined;
    });
    render(
      <LimitsLive
        portfolioId={1}
        onPageContextChange={onPageContextChange}
      />,
    );

    const delta = await screen.findByText('Portfolio Delta');
    await userEvent.click(
      within(delta.closest('[role="row"]') as HTMLElement)
        .getByRole('button', { name: /evidence/i }),
    );
    expect(
      await screen.findByRole('dialog', { name: /limit evidence/i }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(onPageContextChange.mock.calls.at(-1)?.[0].entity_ids.evaluation_id)
        .toBe(71);
    });

    window.history.pushState(
      null,
      '',
      '/limits?portfolio=1&tab=monitor&run=52',
    );
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));

    await waitFor(() => {
      expect(
        screen.queryByRole('dialog', { name: /limit evidence/i }),
      ).not.toBeInTheDocument();
      expect(onPageContextChange.mock.calls.at(-1)?.[0].entity_ids.evaluation_id)
        .toBeNull();
    });
    expect(await screen.findByText(/run #52/i)).toBeInTheDocument();
  });

  it('loads every page of an explicit run evaluation ledger', async () => {
    window.history.replaceState(
      null,
      '',
      '/limits?portfolio=1&tab=monitor&run=51',
    );
    const allEvaluations = Array.from({ length: 201 }, (_, index) => (
      evaluation(1000 + index, `Scope ${index + 1}`, 'ok')
    ));
    const fetchMock = installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-monitoring/runs/51/evaluations'
      ) {
        const offset = Number(request.url.searchParams.get('offset') ?? 0);
        return json({
          items: allEvaluations.slice(offset, offset + 200),
          total: allEvaluations.length,
        });
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    expect(await screen.findByText('Scope 201')).toBeInTheDocument();
    const pages = requests(
      fetchMock,
      '/api/limit-monitoring/runs/51/evaluations',
      'GET',
    );
    expect(pages.map((request) => request.url.searchParams.get('offset')))
      .toEqual(['0', '200']);
  });
});

describe('LimitsLive definitions', () => {
  const DRAFT_VERSION = {
    ...ACTIVE_VERSION,
    id: 102,
    version: 2,
    state: 'draft',
    metric_kind: 'rho_q',
    bump_convention: 'per +1 percentage point',
    effective_from: null,
    activated_at: null,
  };
  const WITH_DRAFT = {
    ...LIMIT,
    row_version: 3,
    versions: [ACTIVE_VERSION, DRAFT_VERSION],
  };
  const ACTIVATED_VERSION = {
    ...DRAFT_VERSION,
    state: 'active',
    effective_from: '2026-07-18T11:00:00',
    activated_at: '2026-07-18T11:00:00',
  };
  const ACTIVATED_LIMIT = {
    ...WITH_DRAFT,
    row_version: 4,
    active_version_id: 102,
    versions: [
      { ...ACTIVE_VERSION, state: 'superseded' },
      ACTIVATED_VERSION,
    ],
    active_version: ACTIVATED_VERSION,
  };

  beforeEach(() => {
    window.history.replaceState(
      null,
      '',
      '/limits?portfolio=1&tab=definitions&limit=41',
    );
  });

  it('seeds a new portfolio limit with the selected portfolio scope', async () => {
    const created = {
      ...LIMIT,
      id: 42,
      key: 'desk-gamma',
      name: 'Desk Gamma',
      active_version_id: null,
      versions: [{ ...ACTIVE_VERSION, id: 103, risk_limit_id: 42, state: 'draft' }],
      active_version: null,
    };
    const fetchMock = installApi((request) => {
      if (request.method === 'POST' && request.url.pathname === '/api/limits') {
        return json(created, 201);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await screen.findByText('Desk Delta');
    await userEvent.click(screen.getByRole('button', { name: /^new limit$/i }));
    await userEvent.type(screen.getByLabelText('Key'), 'desk-gamma');
    await userEvent.type(screen.getByLabelText('Name'), 'Desk Gamma');
    await userEvent.type(screen.getByLabelText('Owner'), 'market-risk');
    await userEvent.click(
      screen.getByRole('button', { name: /create draft limit/i }),
    );

    await waitFor(() => {
      expect(requests(fetchMock, '/api/limits', 'POST')).toHaveLength(1);
    });
    expect(
      requests(fetchMock, '/api/limits', 'POST')[0].body,
    ).toMatchObject({
      key: 'desk-gamma',
      category: 'greek',
      initial_version: {
        scope_type: 'portfolio',
        scope_config: { portfolio_ids: [1] },
      },
    });
  });

  it('creates a RhoQ draft with the current row version, then activates it separately', async () => {
    let currentLimit: typeof LIMIT | typeof WITH_DRAFT | typeof ACTIVATED_LIMIT = LIMIT;
    const fetchMock = installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limits') {
        return json({ items: [currentLimit], total: 1 });
      }
      if (request.method === 'GET' && request.url.pathname === '/api/limits/41') {
        return json(currentLimit);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limits/41/versions'
      ) {
        currentLimit = WITH_DRAFT;
        return json(WITH_DRAFT, 201);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limits/41/versions/102/activate'
      ) {
        currentLimit = ACTIVATED_LIMIT;
        return json(ACTIVATED_LIMIT);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    expect(await screen.findByText('Desk Delta')).toBeInTheDocument();
    expect(screen.getByText(/active version 1/i)).toBeInTheDocument();
    const activeMetric = screen.queryByLabelText('Metric');
    if (activeMetric) expect(activeMetric).toBeDisabled();

    await userEvent.click(
      screen.getByRole('button', { name: /create next draft/i }),
    );
    await userEvent.selectOptions(screen.getByLabelText('Metric'), 'rho_q');
    expect(screen.getByRole('option', { name: 'RhoQ' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /save draft/i }));

    await waitFor(() => {
      expect(
        requests(fetchMock, '/api/limits/41/versions', 'POST'),
      ).toHaveLength(1);
    });
    const draftBody = requests(
      fetchMock,
      '/api/limits/41/versions',
      'POST',
    )[0].body as {
      expected_row_version: number;
      version: Record<string, unknown>;
    };
    expect(draftBody.expected_row_version).toBe(2);
    expect(draftBody.version.metric_kind).toBe('rho_q');
    expect(draftBody.version.scope_config).toEqual({ portfolio_ids: [1, 2] });
    expect(draftBody.version.currency).toBe('USD');
    expect(draftBody.version.bump_convention).toBe('per +1 percentage point');
    expect(draftBody.version).not.toHaveProperty('effective_from');
    expect(draftBody.version).not.toHaveProperty('activated_at');

    await userEvent.click(
      await screen.findByRole('button', { name: /activate version 2/i }),
    );
    await waitFor(() => {
      expect(
        requests(
          fetchMock,
          '/api/limits/41/versions/102/activate',
          'POST',
        ),
      ).toHaveLength(1);
    });
    expect(
      requests(
        fetchMock,
        '/api/limits/41/versions/102/activate',
        'POST',
      )[0].body,
    ).toEqual({ expected_row_version: 3 });
    expect(await screen.findByText(/active version 2/i)).toBeInTheDocument();
    expect(screen.getByText(/rhoq/i)).toBeInTheDocument();
  });

  it('treats timezone-naive server timestamps as UTC for display and draft round trips', async () => {
    const effectiveFromUtc = new Date('2026-07-18T08:00:00Z');
    const effectiveUntilUtc = new Date('2026-07-25T04:00:00Z');
    const localOffset = effectiveUntilUtc.getTimezoneOffset() * 60_000;
    const expectedEffectiveUntilLocal = new Date(
      effectiveUntilUtc.getTime() - localOffset,
    ).toISOString().slice(0, 16);
    const expectedEffectiveFromDisplay = new Intl.DateTimeFormat('en-GB', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(effectiveFromUtc);
    expect(
      parseServerDateTime('2026-07-18T08:00:00').toISOString(),
    ).toBe('2026-07-18T08:00:00.000Z');

    const expiringVersion = {
      ...ACTIVE_VERSION,
      effective_until: '2026-07-25T04:00:00',
    };
    const expiringLimit = {
      ...LIMIT,
      versions: [expiringVersion],
      active_version: expiringVersion,
    };
    const fetchMock = installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limits') {
        return json({ items: [expiringLimit], total: 1 });
      }
      if (request.method === 'GET' && request.url.pathname === '/api/limits/41') {
        return json(expiringLimit);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limits/41/versions'
      ) {
        return json({ ...expiringLimit, row_version: 3 }, 201);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    expect(
      (await screen.findAllByText(expectedEffectiveFromDisplay)).length,
    ).toBeGreaterThan(0);
    await userEvent.click(
      screen.getByRole('button', { name: /create next draft/i }),
    );
    expect(screen.getByLabelText('Effective until')).toHaveValue(
      expectedEffectiveUntilLocal,
    );

    await userEvent.click(screen.getByRole('button', { name: /save draft/i }));
    await waitFor(() => {
      expect(
        requests(fetchMock, '/api/limits/41/versions', 'POST'),
      ).toHaveLength(1);
    });
    expect(
      requests(fetchMock, '/api/limits/41/versions', 'POST')[0].body,
    ).toMatchObject({
      version: {
        effective_until: '2026-07-25T04:00:00.000Z',
      },
    });
  });

  it('closes stale metadata after a same-definition conflict refresh', async () => {
    let conflictReturned = false;
    const freshLimit = {
      ...LIMIT,
      row_version: 7,
      name: 'Desk Delta (server)',
      owner: 'server-risk',
    };
    const fetchMock = installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limits') {
        return json({
          items: [conflictReturned ? freshLimit : LIMIT],
          total: 1,
        });
      }
      if (request.method === 'GET' && request.url.pathname === '/api/limits/41') {
        return json(conflictReturned ? freshLimit : LIMIT);
      }
      if (request.method === 'PATCH' && request.url.pathname === '/api/limits/41') {
        conflictReturned = true;
        return json(
          { detail: 'stale risk limit row version: expected 2, found 7' },
          409,
        );
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await screen.findByText('Desk Delta');
    await userEvent.click(screen.getByRole('button', { name: /edit metadata/i }));
    await userEvent.clear(screen.getByLabelText('Name'));
    await userEvent.type(screen.getByLabelText('Name'), 'Desk Delta (local)');
    await userEvent.click(screen.getByRole('button', { name: /save metadata/i }));

    expect(
      await screen.findByText(/stale risk limit row version/i),
    ).toBeInTheDocument();
    expect(await screen.findByText('Desk Delta (server)')).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole('dialog', { name: /edit limit metadata/i }),
      ).not.toBeInTheDocument();
    });
    expect(requests(fetchMock, '/api/limits/41', 'PATCH')[0].body).toMatchObject({
      expected_row_version: 2,
      name: 'Desk Delta (local)',
    });

    await userEvent.click(screen.getByRole('button', { name: /edit metadata/i }));
    expect(screen.getByLabelText('Name')).toHaveValue('Desk Delta (server)');
    expect(screen.getByLabelText('Owner')).toHaveValue('server-risk');
  });

  it('shows a 409 conflict and refreshes instead of claiming a stale draft succeeded', async () => {
    let conflictReturned = false;
    let readsAfterConflict = 0;
    const freshLimit = {
      ...LIMIT,
      row_version: 7,
      name: 'Desk Delta (server)',
    };
    const fetchMock = installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limits') {
        if (conflictReturned) readsAfterConflict += 1;
        return json({
          items: [conflictReturned ? freshLimit : LIMIT],
          total: 1,
        });
      }
      if (request.method === 'GET' && request.url.pathname === '/api/limits/41') {
        if (conflictReturned) readsAfterConflict += 1;
        return json(conflictReturned ? freshLimit : LIMIT);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limits/41/versions'
      ) {
        conflictReturned = true;
        return json(
          { detail: 'stale risk limit row version: expected 2, found 7' },
          409,
        );
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await screen.findByText('Desk Delta');
    await userEvent.click(
      screen.getByRole('button', { name: /create next draft/i }),
    );
    await userEvent.selectOptions(screen.getByLabelText('Metric'), 'rho');
    await userEvent.click(screen.getByRole('button', { name: /save draft/i }));

    expect(
      await screen.findByText(/stale risk limit row version/i),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(readsAfterConflict).toBeGreaterThan(0);
    });
    expect(await screen.findByText('Desk Delta (server)')).toBeInTheDocument();
    expect(
      requests(fetchMock, '/api/limits/41/versions', 'POST')[0].body,
    ).toMatchObject({ expected_row_version: 2 });
    expect(
      screen.queryByRole('dialog', { name: /create next draft/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/active version 2/i)).not.toBeInTheDocument();
  });
});

describe('LimitsLive breaches', () => {
  beforeEach(() => {
    window.history.replaceState(
      null,
      '',
      '/limits?portfolio=1&tab=breaches&incident=81',
    );
  });

  it('opens incident evidence with run-consistent, tab-scoped agent context', async () => {
    const fetchMock = installApi();
    const onPageContextChange = vi.fn();
    render(
      <LimitsLive
        portfolioId={1}
        onPageContextChange={onPageContextChange}
      />,
    );

    await userEvent.click(
      await screen.findByRole('button', { name: /evaluation #73/i }),
    );

    expect(await screen.findByRole('dialog', { name: /limit evidence/i }))
      .toBeInTheDocument();
    expect(window.location.pathname).toBe('/limits');
    const search = new URLSearchParams(window.location.search);
    expect(search.get('tab')).toBe('monitor');
    expect(search.get('run')).toBe('51');
    expect(search.get('evaluation')).toBe('73');
    expect(search.get('incident')).toBeNull();
    expect(
      requests(fetchMock, '/api/limit-evaluations/73', 'GET').length,
    ).toBeGreaterThan(0);
    await waitFor(() => {
      const context = onPageContextChange.mock.calls.at(-1)?.[0];
      expect(context.entity_ids).toMatchObject({
        portfolio_id: 1,
        monitoring_run_id: 51,
        evaluation_id: 73,
        incident_id: null,
      });
      expect(context.snapshot.dashboard_summary).toEqual(UNKNOWN_RUN.summary);
      expect(context.snapshot.selected_incident).toBeNull();
    });
  });

  it('does not let a delayed evaluation lookup override newer tab navigation', async () => {
    const evaluationGate = deferred<Response>();
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-evaluations/73'
      ) {
        return evaluationGate.promise;
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await userEvent.click(
      await screen.findByRole('button', { name: /evaluation #73/i }),
    );
    await userEvent.click(
      screen.getByRole('tab', { name: /^definitions$/i }),
    );
    expect(await screen.findByText('Desk Delta')).toBeInTheDocument();

    evaluationGate.resolve(json(EVALUATIONS[2]));
    await act(async () => {
      await Promise.resolve();
    });

    expect(
      screen.getByRole('tab', { name: /^definitions$/i }),
    ).toHaveAttribute('aria-selected', 'true');
    expect(new URLSearchParams(window.location.search).get('evaluation')).toBeNull();
  });

  it('hands an incident audit reference to Audit navigation', async () => {
    const onOpenAudit = vi.fn();
    installApi();
    render(<LimitsLive portfolioId={1} onOpenAudit={onOpenAudit} />);

    await userEvent.click(
      await screen.findByRole('button', { name: 'audit:401' }),
    );

    expect(onOpenAudit).toHaveBeenCalledWith('audit:401');
  });

  it('offers only comment and reopen for a recovered terminal incident', async () => {
    const recovered = incident({ status: 'recovered', row_version: 4 });
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents'
      ) {
        return json({ items: [recovered], total: 1 });
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents/81'
      ) {
        return json(recovered);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    expect(await screen.findByText(/^recovered$/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^comment$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^reopen$/i })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /^assign$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^waive$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^resolve$/i })).toBeNull();
  });

  it('discards a stale incident detail response after Back navigation', async () => {
    const second = incident({
      id: 82,
      scope_key: 'portfolio:2',
      scope_label: 'Options Book',
      row_version: 3,
      events: [{ ...OPEN_EVENT, id: 501, incident_id: 82 }],
    });
    const secondDetail = deferred<Response>();
    const fetchMock = installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents'
      ) {
        return json({ items: [OPEN_INCIDENT, second], total: 2 });
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents/82'
      ) {
        return secondDetail.promise;
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await screen.findByText('Incident #81');
    fireEvent.click(screen.getByText('#82').closest('[role="row"]') as HTMLElement);
    await waitFor(() => {
      expect(
        requests(fetchMock, '/api/limit-incidents/82', 'GET').length,
      ).toBeGreaterThan(0);
    });

    window.history.pushState(
      null,
      '',
      '/limits?portfolio=1&tab=breaches&incident=81',
    );
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));
    await screen.findByText('Incident #81');
    secondDetail.resolve(json(second));

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText('Incident #82')).not.toBeInTheDocument();
    expect(screen.getAllByText('Macro Book').length).toBeGreaterThan(0);
  });

  it('does not let an incident mutation overwrite a newer selection', async () => {
    const second = incident({
      id: 82,
      scope_key: 'portfolio:2',
      scope_label: 'Options Book',
      row_version: 3,
      events: [{ ...OPEN_EVENT, id: 501, incident_id: 82 }],
    });
    const acknowledgeGate = deferred<Response>();
    installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents'
      ) {
        return json({ items: [OPEN_INCIDENT, second], total: 2 });
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents/82'
      ) {
        return json(second);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-incidents/81/acknowledge'
      ) {
        return acknowledgeGate.promise;
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await userEvent.click(
      await screen.findByRole('button', { name: /^acknowledge$/i }),
    );
    fireEvent.click(
      screen.getByText('#82').closest('[role="row"]') as HTMLElement,
    );
    expect(await screen.findByText('Incident #82')).toBeInTheDocument();

    acknowledgeGate.resolve(json(incident({
      status: 'acknowledged',
      row_version: 7,
      acknowledged_at: '2026-07-18T09:05:00',
    })));
    await waitFor(() => {
      expect(screen.getByText('Incident #82')).toBeInTheDocument();
      expect(screen.getByText(/row version 3/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Incident #81')).not.toBeInTheDocument();
  });

  it('does not acknowledge optimistically and adopts the server row version', async () => {
    const gate = deferred<Response>();
    let acknowledged:
      | ReturnType<typeof incident>
      | null = null;
    const fetchMock = installApi((request) => {
      if (
        acknowledged
        && request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents'
      ) {
        return json({ items: [acknowledged], total: 1 });
      }
      if (
        acknowledged
        && request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents/81'
      ) {
        return json(acknowledged);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-incidents/81/acknowledge'
      ) {
        return gate.promise;
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    const acknowledge = await screen.findByRole('button', {
      name: /^acknowledge$/i,
    });
    expect(screen.getByText(/^open$/i)).toBeInTheDocument();
    expect(screen.getByText(/row version 1/i)).toBeInTheDocument();

    await userEvent.click(acknowledge);
    expect(screen.getByText(/^open$/i)).toBeInTheDocument();
    expect(screen.queryByText(/^acknowledged$/i)).not.toBeInTheDocument();
    expect(acknowledge).toBeDisabled();

    acknowledged = incident({
      status: 'acknowledged',
      row_version: 7,
      acknowledged_at: '2026-07-18T09:05:00',
      events: [
        OPEN_EVENT,
        {
          ...OPEN_EVENT,
          id: 402,
          event_type: 'acknowledged',
          actor: 'desk_user',
        },
      ],
    });
    gate.resolve(json(acknowledged));

    expect(await screen.findByText(/^acknowledged$/i)).toBeInTheDocument();
    expect(screen.getByText(/row version 7/i)).toBeInTheDocument();
    const request = requests(
      fetchMock,
      '/api/limit-incidents/81/acknowledge',
      'POST',
    )[0];
    expect(request.url.searchParams.get('portfolio_id')).toBe('1');
    expect(request.body).toEqual({ expected_row_version: 1 });
  });

  it('closes a stale incident action after a same-incident conflict refresh', async () => {
    let conflictReturned = false;
    const freshIncident = incident({
      status: 'assigned',
      assignee: 'bob',
      row_version: 7,
    });
    const fetchMock = installApi((request) => {
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents/81'
      ) {
        return json(conflictReturned ? freshIncident : OPEN_INCIDENT);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-incidents/81/assign'
      ) {
        conflictReturned = true;
        return json(
          { detail: 'stale limit incident row version: expected 1, found 7' },
          409,
        );
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await userEvent.click(
      await screen.findByRole('button', { name: /^assign$/i }),
    );
    await userEvent.type(screen.getByLabelText('Assignee'), 'alice');
    await userEvent.click(
      screen.getByRole('button', { name: /save assignment/i }),
    );

    expect(
      await screen.findByText(/stale limit incident row version/i),
    ).toBeInTheDocument();
    expect(await screen.findByText(/row version 7/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole('dialog', { name: /assign incident/i }),
      ).not.toBeInTheDocument();
    });
    expect(
      requests(fetchMock, '/api/limit-incidents/81/assign', 'POST')[0].body,
    ).toEqual({ expected_row_version: 1, assignee: 'alice' });

    await userEvent.click(screen.getByRole('button', { name: /^assign$/i }));
    expect(screen.getByLabelText('Assignee')).toHaveValue('bob');
  });

  it.each([
    {
      name: 'assign',
      open: /assign$/i,
      field: /assignee/i,
      value: 'alice',
      submit: /save assignment/i,
      path: '/api/limit-incidents/81/assign',
      body: { expected_row_version: 1, assignee: 'alice' },
      returned: incident({ status: 'assigned', assignee: 'alice', row_version: 5 }),
      result: /assigned/i,
    },
    {
      name: 'comment',
      open: /comment$/i,
      field: /^comment$/i,
      value: 'Hedge is in progress',
      submit: /add comment/i,
      path: '/api/limit-incidents/81/comments',
      body: { expected_row_version: 1, comment: 'Hedge is in progress' },
      returned: incident({
        row_version: 5,
        events: [
          OPEN_EVENT,
          {
            ...OPEN_EVENT,
            id: 402,
            event_type: 'commented',
            payload: { comment: 'Hedge is in progress' },
          },
        ],
      }),
      result: /hedge is in progress/i,
    },
    {
      name: 'waive',
      open: /waive$/i,
      field: /waiver rationale/i,
      value: 'Temporary board approval',
      submit: /confirm waiver/i,
      path: '/api/limit-incidents/81/waive',
      body: {
        expected_row_version: 1,
        rationale: 'Temporary board approval',
        expires_at: new Date('2026-07-20T12:00').toISOString(),
      },
      returned: incident({
        status: 'waived',
        row_version: 5,
        waiver_rationale: 'Temporary board approval',
        waiver_expires_at: '2026-07-20T12:00:00',
      }),
      result: /waived/i,
      expiresAt: '2026-07-20T12:00',
    },
  ])(
    'sends the current concurrency token for $name and replaces it from the response',
    async ({
      open,
      field,
      value,
      submit,
      path,
      body,
      returned,
      result,
      expiresAt,
    }) => {
      let mutated = false;
      const fetchMock = installApi((request) => {
        if (
          mutated
          && request.method === 'GET'
          && request.url.pathname === '/api/limit-incidents'
        ) {
          return json({ items: [returned], total: 1 });
        }
        if (
          mutated
          && request.method === 'GET'
          && request.url.pathname === '/api/limit-incidents/81'
        ) {
          return json(returned);
        }
        if (request.method === 'POST' && request.url.pathname === path) {
          mutated = true;
          return json(returned);
        }
        return undefined;
      });
      render(<LimitsLive portfolioId={1} />);

      await userEvent.click(
        await screen.findByRole('button', { name: open }),
      );
      await userEvent.type(screen.getByLabelText(field), value);
      if (expiresAt) {
        fireEvent.change(screen.getByLabelText(/waiver expires at/i), {
          target: { value: expiresAt },
        });
      }
      await userEvent.click(screen.getByRole('button', { name: submit }));

      await waitFor(() => {
        expect(requests(fetchMock, path, 'POST')).toHaveLength(1);
      });
      expect(requests(fetchMock, path, 'POST')[0].body).toEqual(body);
      expect(await screen.findByText(result)).toBeInTheDocument();
      expect(screen.getByText(/row version 5/i)).toBeInTheDocument();
    },
  );

  it('resolves and reopens with the row version returned by each server action', async () => {
    let current = OPEN_INCIDENT;
    const fetchMock = installApi((request) => {
      if (request.method === 'GET' && request.url.pathname === '/api/limit-incidents') {
        return json({ items: [current], total: 1 });
      }
      if (
        request.method === 'GET'
        && request.url.pathname === '/api/limit-incidents/81'
      ) {
        return json(current);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-incidents/81/resolve'
      ) {
        current = incident({
          status: 'resolved',
          row_version: 9,
          resolved_at: '2026-07-18T09:10:00',
        });
        return json(current);
      }
      if (
        request.method === 'POST'
        && request.url.pathname === '/api/limit-incidents/81/reopen'
      ) {
        current = incident({ status: 'open', row_version: 12 });
        return json(current);
      }
      return undefined;
    });
    render(<LimitsLive portfolioId={1} />);

    await userEvent.click(
      await screen.findByRole('button', { name: /^resolve$/i }),
    );
    expect(await screen.findByText(/^resolved$/i)).toBeInTheDocument();
    expect(
      requests(fetchMock, '/api/limit-incidents/81/resolve', 'POST')[0].body,
    ).toEqual({ expected_row_version: 1 });

    await userEvent.click(
      await screen.findByRole('button', { name: /^reopen$/i }),
    );
    expect(await screen.findByText(/^open$/i)).toBeInTheDocument();
    expect(
      requests(fetchMock, '/api/limit-incidents/81/reopen', 'POST')[0].body,
    ).toEqual({ expected_row_version: 9 });
    expect(screen.getByText(/row version 12/i)).toBeInTheDocument();
  });
});

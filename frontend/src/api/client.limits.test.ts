import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  acknowledgeLimitIncident,
  activateRiskLimitVersion,
  assignLimitIncident,
  commentLimitIncident,
  createLimitMonitoringRun,
  createRiskLimit,
  createRiskLimitVersion,
  deactivateRiskLimit,
  getLimitIncident,
  getLimitEvaluation,
  getLimitMonitoringDashboard,
  getLimitMonitoringRun,
  getLimitMonitoringSummary,
  getRiskLimit,
  listLimitEvaluations,
  listLimitIncidents,
  listLimitMonitoringRuns,
  listMarketSnapshots,
  listRiskLimits,
  listRiskLimitVersions,
  reopenLimitIncident,
  resolveLimitIncident,
  retireRiskLimit,
  updateRiskLimitMetadata,
  waiveLimitIncident,
} from './client';
import type {
  LimitCreateInput,
  LimitMonitoringRunCreateInput,
  ReportJob,
  TaskRun,
} from '../types';

afterEach(() => vi.restoreAllMocks());

function mockJson(body: unknown = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => (
    new Response(JSON.stringify(body), { status: 200 })
  ));
}

const version = {
  metric_kind: 'delta',
  source_kind: 'risk_run',
  methodology: {},
  scope_type: 'portfolio',
  scope_config: { portfolio_ids: [7] },
  aggregation: 'net',
  transform: 'absolute',
  comparator: 'upper',
  warning_upper: 80,
  hard_upper: 100,
  unit: 'underlying_units',
  freshness_policy: { max_age_seconds: 300 },
} as const;

describe('risk limit definition client', () => {
  it('encodes list filters without leaking absent values', async () => {
    const fetchMock = mockJson({ items: [], total: 0 });

    await listRiskLimits({
      category: 'greek',
      owner: 'market-risk',
      state: 'active',
      scope_type: 'portfolio',
      tag: 'intraday',
      portfolio_id: 7,
      limit: 25,
      offset: 50,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/limits?category=greek&owner=market-risk&state=active'
      + '&scope_type=portfolio&tag=intraday&portfolio_id=7&limit=25&offset=50',
    );
  });

  it('uses the exact definition and row-version mutation bodies', async () => {
    const fetchMock = mockJson();
    const createBody: LimitCreateInput = {
      key: 'desk-delta',
      name: 'Desk delta',
      category: 'greek',
      owner: 'market-risk',
      initial_version: version,
    };

    await createRiskLimit(createBody);
    await getRiskLimit(4);
    await updateRiskLimitMetadata(4, {
      expected_row_version: 2,
      name: 'Desk delta v2',
    });
    await createRiskLimitVersion(4, {
      expected_row_version: 3,
      version,
    });
    await listRiskLimitVersions(4);
    await activateRiskLimitVersion(4, 9, { expected_row_version: 4 });
    await deactivateRiskLimit(4, { expected_row_version: 5 });
    await retireRiskLimit(4, { expected_row_version: 6 });

    expect(fetchMock.mock.calls[0]).toEqual([
      '/api/limits',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(createBody),
      }),
    ]);
    expect(fetchMock.mock.calls[1][0]).toBe('/api/limits/4');
    expect(fetchMock.mock.calls[2]).toEqual([
      '/api/limits/4',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          expected_row_version: 2,
          name: 'Desk delta v2',
        }),
      }),
    ]);
    expect(fetchMock.mock.calls[3]).toEqual([
      '/api/limits/4/versions',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          expected_row_version: 3,
          version,
        }),
      }),
    ]);
    expect(fetchMock.mock.calls[4][0]).toBe('/api/limits/4/versions');
    expect(fetchMock.mock.calls[5][0]).toBe('/api/limits/4/versions/9/activate');
    expect(JSON.parse((fetchMock.mock.calls[5][1] as RequestInit).body as string))
      .toEqual({ expected_row_version: 4 });
    expect(fetchMock.mock.calls[6][0]).toBe('/api/limits/4/deactivate');
    expect(fetchMock.mock.calls[7][0]).toBe('/api/limits/4/retire');
  });
});

describe('limit monitoring client', () => {
  it('posts the explicit monitoring evidence contract and scopes all reads', async () => {
    const fetchMock = mockJson({ items: [], total: 0 });
    const body: LimitMonitoringRunCreateInput = {
      portfolio_id: 7,
      pricing_parameter_profile_id: 2,
      engine_config_id: 3,
      market_snapshot_id: 5,
      effective_market_evidence_id: 'manual:spx:20260718',
      valuation_as_of: '2026-07-18T09:00:00Z',
      source_policy: 'refresh_if_stale',
      max_source_age_seconds: 300,
      source_inputs: { risk_run: { reuse: true } },
    };

    await createLimitMonitoringRun(body);
    await listLimitMonitoringRuns({
      portfolio_id: 7,
      status: 'completed',
      limit: 10,
      offset: 20,
    });
    await getLimitMonitoringRun(12, 7);
    await listLimitEvaluations(12, {
      portfolio_id: 7,
      status: 'warning',
      limit: 40,
      offset: 2,
    });
    await getLimitMonitoringDashboard(7, 15);
    await getLimitMonitoringSummary(7);

    expect(fetchMock.mock.calls[0]).toEqual([
      '/api/limit-monitoring/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(body),
      }),
    ]);
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/api/limit-monitoring/runs?portfolio_id=7&status=completed&limit=10&offset=20',
    );
    expect(fetchMock.mock.calls[2][0]).toBe(
      '/api/limit-monitoring/runs/12?portfolio_id=7',
    );
    expect(fetchMock.mock.calls[3][0]).toBe(
      '/api/limit-monitoring/runs/12/evaluations'
      + '?portfolio_id=7&status=warning&limit=40&offset=2',
    );
    expect(fetchMock.mock.calls[4][0]).toBe(
      '/api/limit-monitoring/dashboard?portfolio_id=7&trend_limit=15',
    );
    expect(fetchMock.mock.calls[5][0]).toBe(
      '/api/limit-monitoring/summary?portfolio_id=7',
    );
  });

  it('scopes an evaluation deep link to its portfolio', async () => {
    const fetchMock = mockJson();

    await getLimitEvaluation(73, 7);

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/limit-evaluations/73?portfolio_id=7',
    );
  });
});

describe('limit incident client', () => {
  it('keeps portfolio visibility in the query and concurrency data in the body', async () => {
    const fetchMock = mockJson({ items: [], total: 0 });

    await listLimitIncidents({
      portfolio_id: 7,
      status: 'open',
      severity: 'breach',
      limit: 10,
      offset: 5,
    });
    await getLimitIncident(11, 7);
    await acknowledgeLimitIncident(11, 7, { expected_row_version: 1 });
    await assignLimitIncident(11, 7, {
      expected_row_version: 2,
      assignee: 'alice',
    });
    await commentLimitIncident(11, 7, {
      expected_row_version: 3,
      comment: 'Hedge in progress',
    });
    await waiveLimitIncident(11, 7, {
      expected_row_version: 4,
      rationale: 'Approved temporary excess',
      expires_at: '2026-07-18T10:00:00Z',
    });
    await resolveLimitIncident(11, 7, { expected_row_version: 5 });
    await reopenLimitIncident(11, 7, { expected_row_version: 6 });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/limit-incidents?portfolio_id=7&status=open'
      + '&severity=breach&limit=10&offset=5',
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/api/limit-incidents/11?portfolio_id=7',
    );
    for (const call of fetchMock.mock.calls.slice(2)) {
      expect(call[0]).toContain('portfolio_id=7');
      expect((call[1] as RequestInit).method).toBe('POST');
    }
    expect(fetchMock.mock.calls[4][0]).toBe(
      '/api/limit-incidents/11/comments?portfolio_id=7',
    );
    expect(JSON.parse((fetchMock.mock.calls[5][1] as RequestInit).body as string))
      .toEqual({
        expected_row_version: 4,
        rationale: 'Approved temporary excess',
        expires_at: '2026-07-18T10:00:00Z',
      });
  });
});

describe('market snapshot and linked task contracts', () => {
  it('lists bounded snapshots with encoded filters', async () => {
    const fetchMock = mockJson([]);

    await listMarketSnapshots({
      source: 'manual close',
      as_of: '2026-07-18T09:00:00+08:00',
      limit: 20,
      offset: 3,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/market-data/snapshots?source=manual+close'
      + '&as_of=2026-07-18T09%3A00%3A00%2B08%3A00&limit=20&offset=3',
    );
  });

  it('types task and report links without adding a task domain status', () => {
    const task = {
      id: 1,
      kind: 'limit_monitoring',
      status: 'completed',
      limit_monitoring_run_id: 12,
      progress_current: 1,
      progress_total: 1,
      created_at: '2026-07-18T09:00:00Z',
    } satisfies TaskRun;
    const report = {
      id: 2,
      report_type: 'limit_analysis',
      status: 'completed',
      request_payload: {},
      result_payload: {},
      artifact_paths: {},
      limit_monitoring_run_id: 12,
      created_at: '2026-07-18T09:05:00Z',
    } satisfies ReportJob;

    expect(task.limit_monitoring_run_id).toBe(12);
    expect(report.limit_monitoring_run_id).toBe(12);
  });
});

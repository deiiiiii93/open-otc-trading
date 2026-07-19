import type {
  BacktestRun,
  BacktestRunRequest,
  DeskWorkflow,
  DeskWorkflowSummary,
  EngineConfigVariant,
  EngineConfigVariantInput,
  FxRate,
  Instrument,
  PricingParameterProfile,
  ScenarioGridRequest,
  ScenarioLibrary,
  ScenarioSetDetail,
  ScenarioSetSummary,
  ScenarioSpec,
  ScenarioTestRun,
  ScenarioTestRunRequest,
  SkillCatalog,
  SkillDeleteResult,
  SkillFile,
  SkillFrontmatter,
  SkillReloadResult,
  SkillSaveResult,
  SkillTier,
  SkillValidateResult,
  TraceRunDetail,
  TraceRunNode,
  TraceSummary,
  TracingConfig,
  MemoryFact,
  MemoryStatus,
  AuditAction,
  AuditActionDetail,
  AuditSummary,
  AgentRegistry,
  ChannelWrite,
  ModelWrite,
  LimitActionInput,
  LimitCreateInput,
  LimitDashboard,
  LimitEvaluation,
  LimitIncident,
  LimitIncidentAssignInput,
  LimitIncidentCommentInput,
  LimitIncidentWaiveInput,
  LimitMetadataPatchInput,
  LimitMonitoringRun,
  LimitMonitoringRunCreateInput,
  LimitMonitoringSummary,
  LimitVersion,
  LimitVersionCreateInput,
  MarketSnapshot,
  RiskLimit,
} from '../types';

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has('content-type')) {
    headers.set('content-type', 'application/json');
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return undefined as T;
  return response.json();
}

export async function uploadForm<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(path, { method: 'POST', body });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export function fetchTracingConfig(): Promise<TracingConfig> {
  return api<TracingConfig>('/api/tracing/config');
}

export function fetchRecentTraces(
  limit = 50,
): Promise<{ traces: TraceSummary[] }> {
  return api(`/api/tracing/recent?limit=${limit}`);
}

export function fetchThreadTraces(
  threadId: number,
): Promise<{ thread_id: number; traces: TraceSummary[] }> {
  return api(`/api/tracing/threads/${threadId}/traces`);
}

export function fetchTraceTree(
  traceId: string,
): Promise<{ trace_id: string; runs: TraceRunNode[] }> {
  return api(`/api/tracing/traces/${traceId}`);
}

export function fetchTraceRun(runId: string): Promise<TraceRunDetail> {
  return api(`/api/tracing/runs/${runId}`);
}

export interface AuditListParams {
  audit_ref?: string;
  status?: string;
  kind?: string;
  tool_name?: string;
  tool_class?: string;
  mode?: string;
  limit?: number;
  offset?: number;
}

export function listAuditActions(
  params: AuditListParams = {},
): Promise<{ items: AuditAction[]; total: number }> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const qs = search.toString();
  return api(`/api/audit/actions${qs ? `?${qs}` : ''}`);
}

export function getAuditAction(id: number): Promise<AuditActionDetail> {
  return api(`/api/audit/actions/${id}`);
}

export function fetchAuditSummary(): Promise<AuditSummary> {
  return api('/api/audit/summary');
}

export const listFxRates = () => api<FxRate[]>('/api/market-data/fx-rates');
export const createFxRate = (body: Omit<FxRate, 'id'>) =>
  api<FxRate>('/api/market-data/fx-rates', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  });
export const fetchFxRateAkshare = (base: string, quote: string) =>
  api<FxRate>('/api/market-data/fx-rates/akshare', {
    method: 'POST',
    body: JSON.stringify({ base_currency: base, quote_currency: quote }),
    headers: { 'Content-Type': 'application/json' },
  });
export const deleteFxRate = (id: number) =>
  api<{ ok: boolean }>(`/api/market-data/fx-rates/${id}`, { method: 'DELETE' });

export type PricingParameterRowCreateInput = {
  source_trade_id?: string | null;
  symbol: string;
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
};

export type PricingParameterProfileCreateInput = {
  name?: string | null;
  valuation_date?: string | null;
  rows: PricingParameterRowCreateInput[];
};

export const createPricingParameterProfile = (body: PricingParameterProfileCreateInput) =>
  api<PricingParameterProfile>('/api/pricing-parameter-profiles', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export type InstrumentCreateInput = Omit<
  Instrument,
  'id' | 'created_at' | 'updated_at' | 'source' | 'loaded_at' | 'contract_code' | 'tags'
>;

export const createInstrument = (body: InstrumentCreateInput) =>
  api<Instrument>('/api/instruments', {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  });

export const listEngineConfigs = () => api<EngineConfigVariant[]>('/api/engine-configs');
export const createEngineConfig = (body: EngineConfigVariantInput) =>
  api<EngineConfigVariant>('/api/engine-configs', { method: 'POST', body: JSON.stringify(body) });
export const updateEngineConfig = (id: number, body: EngineConfigVariantInput) =>
  api<EngineConfigVariant>(`/api/engine-configs/${id}`, { method: 'PUT', body: JSON.stringify(body) });
export const deleteEngineConfig = (id: number) =>
  api<{ ok: boolean }>(`/api/engine-configs/${id}`, { method: 'DELETE' });
export const setDefaultEngineConfig = (id: number) =>
  api<EngineConfigVariant>(`/api/engine-configs/${id}/default`, { method: 'POST' });

// --- Risk Limits ------------------------------------------------------------

type LimitQueryValue = string | number | null | undefined;

function withLimitQuery(
  path: string,
  params: Record<string, LimitQueryValue>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== '') search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export type RiskLimitListParams = {
  category?: string;
  owner?: string;
  state?: string;
  scope_type?: string;
  tag?: string;
  portfolio_id?: number;
  limit?: number;
  offset?: number;
};

export const listRiskLimits = (params: RiskLimitListParams = {}) =>
  api<{ items: RiskLimit[]; total: number }>(withLimitQuery('/api/limits', params));

export const createRiskLimit = (body: LimitCreateInput) =>
  api<RiskLimit>('/api/limits', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const getRiskLimit = (limitId: number) =>
  api<RiskLimit>(`/api/limits/${limitId}`);

export const updateRiskLimitMetadata = (
  limitId: number,
  body: LimitMetadataPatchInput,
) =>
  api<RiskLimit>(`/api/limits/${limitId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });

export const createRiskLimitVersion = (
  limitId: number,
  body: LimitVersionCreateInput,
) =>
  api<RiskLimit>(`/api/limits/${limitId}/versions`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const listRiskLimitVersions = (limitId: number) =>
  api<LimitVersion[]>(`/api/limits/${limitId}/versions`);

export const activateRiskLimitVersion = (
  limitId: number,
  versionId: number,
  body: LimitActionInput,
) =>
  api<RiskLimit>(`/api/limits/${limitId}/versions/${versionId}/activate`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const deactivateRiskLimit = (limitId: number, body: LimitActionInput) =>
  api<RiskLimit>(`/api/limits/${limitId}/deactivate`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const retireRiskLimit = (limitId: number, body: LimitActionInput) =>
  api<RiskLimit>(`/api/limits/${limitId}/retire`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const createLimitMonitoringRun = (body: LimitMonitoringRunCreateInput) =>
  api<LimitMonitoringRun>('/api/limit-monitoring/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export type LimitMonitoringRunListParams = {
  portfolio_id: number;
  status?: string;
  limit?: number;
  offset?: number;
};

export const listLimitMonitoringRuns = (params: LimitMonitoringRunListParams) =>
  api<{ items: LimitMonitoringRun[]; total: number }>(
    withLimitQuery('/api/limit-monitoring/runs', params),
  );

export const getLimitMonitoringRun = (runId: number, portfolioId: number) =>
  api<LimitMonitoringRun>(
    withLimitQuery(`/api/limit-monitoring/runs/${runId}`, {
      portfolio_id: portfolioId,
    }),
  );

export type LimitEvaluationListParams = {
  portfolio_id: number;
  status?: string;
  limit?: number;
  offset?: number;
};

export const listLimitEvaluations = (
  runId: number,
  params: LimitEvaluationListParams,
) =>
  api<{ items: LimitEvaluation[]; total: number }>(
    withLimitQuery(
      `/api/limit-monitoring/runs/${runId}/evaluations`,
      params,
    ),
  );

export const getLimitEvaluation = (
  evaluationId: number,
  portfolioId: number,
) =>
  api<LimitEvaluation>(
    withLimitQuery(`/api/limit-evaluations/${evaluationId}`, {
      portfolio_id: portfolioId,
    }),
  );

export type LimitIncidentListParams = {
  portfolio_id: number;
  status?: string;
  severity?: string;
  limit?: number;
  offset?: number;
};

export const listLimitIncidents = (params: LimitIncidentListParams) =>
  api<{ items: LimitIncident[]; total: number }>(
    withLimitQuery('/api/limit-incidents', params),
  );

export const getLimitIncident = (incidentId: number, portfolioId: number) =>
  api<LimitIncident>(
    withLimitQuery(`/api/limit-incidents/${incidentId}`, {
      portfolio_id: portfolioId,
    }),
  );

function mutateLimitIncident<T extends LimitActionInput>(
  incidentId: number,
  portfolioId: number,
  action: string,
  body: T,
): Promise<LimitIncident> {
  return api<LimitIncident>(
    withLimitQuery(`/api/limit-incidents/${incidentId}/${action}`, {
      portfolio_id: portfolioId,
    }),
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  );
}

export const acknowledgeLimitIncident = (
  incidentId: number,
  portfolioId: number,
  body: LimitActionInput,
) => mutateLimitIncident(incidentId, portfolioId, 'acknowledge', body);

export const assignLimitIncident = (
  incidentId: number,
  portfolioId: number,
  body: LimitIncidentAssignInput,
) => mutateLimitIncident(incidentId, portfolioId, 'assign', body);

export const commentLimitIncident = (
  incidentId: number,
  portfolioId: number,
  body: LimitIncidentCommentInput,
) => mutateLimitIncident(incidentId, portfolioId, 'comments', body);

export const waiveLimitIncident = (
  incidentId: number,
  portfolioId: number,
  body: LimitIncidentWaiveInput,
) => mutateLimitIncident(incidentId, portfolioId, 'waive', body);

export const resolveLimitIncident = (
  incidentId: number,
  portfolioId: number,
  body: LimitActionInput,
) => mutateLimitIncident(incidentId, portfolioId, 'resolve', body);

export const reopenLimitIncident = (
  incidentId: number,
  portfolioId: number,
  body: LimitActionInput,
) => mutateLimitIncident(incidentId, portfolioId, 'reopen', body);

export const getLimitMonitoringDashboard = (
  portfolioId: number,
  trendLimit?: number,
) =>
  api<LimitDashboard>(
    withLimitQuery('/api/limit-monitoring/dashboard', {
      portfolio_id: portfolioId,
      trend_limit: trendLimit,
    }),
  );

export const getLimitMonitoringSummary = (portfolioId: number) =>
  api<LimitMonitoringSummary>(
    withLimitQuery('/api/limit-monitoring/summary', {
      portfolio_id: portfolioId,
    }),
  );

export type MarketSnapshotListParams = {
  source?: string;
  as_of?: string;
  limit?: number;
  offset?: number;
};

export const listMarketSnapshots = (params: MarketSnapshotListParams = {}) =>
  api<MarketSnapshot[]>(
    withLimitQuery('/api/market-data/snapshots', params),
  );

// --- Skills management -------------------------------------------------

const encodeSkillPath = (path: string) =>
  path.split('/').map(encodeURIComponent).join('/');

export const listSkillsCatalog = () => api<SkillCatalog>('/api/skills/catalog');

export const getSkillFile = (tier: SkillTier, path: string) =>
  api<SkillFile>(`/api/skills/${tier}/${encodeSkillPath(path)}`);

export const saveWorkflowSkill = (
  path: string,
  frontmatter: SkillFrontmatter,
  body: string,
) =>
  api<SkillSaveResult>(`/api/skills/workflows/${encodeSkillPath(path)}`, {
    method: 'PUT',
    body: JSON.stringify({ frontmatter, body }),
  });

export const saveRawSkillFile = (tier: SkillTier, path: string, content: string) =>
  api<SkillSaveResult>(`/api/skills/${tier}/${encodeSkillPath(path)}`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  });

export const createWorkflowSkill = (
  domain: string,
  name: string,
  frontmatter: SkillFrontmatter,
  body: string,
) =>
  api<SkillSaveResult>('/api/skills/workflows', {
    method: 'POST',
    body: JSON.stringify({ domain, name, frontmatter, body }),
  });

export const deleteWorkflowSkill = (domain: string, name: string) =>
  api<SkillDeleteResult>(`/api/skills/workflows/${domain}/${name}`, {
    method: 'DELETE',
  });

export const validateWorkflowSkill = (
  frontmatter: SkillFrontmatter,
  body: string,
) =>
  api<SkillValidateResult>('/api/skills/validate', {
    method: 'POST',
    body: JSON.stringify({ tier: 'workflows', frontmatter, body }),
  });

export const validateRawSkillFile = (tier: SkillTier, content: string) =>
  api<SkillValidateResult>('/api/skills/validate', {
    method: 'POST',
    body: JSON.stringify({ tier, content }),
  });

export const reloadSkills = () =>
  api<SkillReloadResult>('/api/skills/reload', { method: 'POST' });

// --- Scenario Test -------------------------------------------------

export const fetchScenarioLibrary = () =>
  api<ScenarioLibrary>('/api/scenario-test/library');

export const createScenarioTestRun = (body: ScenarioTestRunRequest) =>
  api<ScenarioTestRun>('/api/scenario-test/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const listScenarioTestRuns = (portfolioId: number) =>
  api<ScenarioTestRun[]>(`/api/scenario-test/runs?portfolio_id=${portfolioId}`);

export const getScenarioTestRun = (id: number) =>
  api<ScenarioTestRun>(`/api/scenario-test/runs/${id}`);

export const scenarioTestArtifactUrl = (
  runId: number,
  name: string,
  options?: { download?: boolean },
) => {
  const basename = name.split('/').pop() ?? name;
  const query = options?.download ? '?download=true' : '';
  return `/api/scenario-test/runs/${runId}/artifacts/${encodeURIComponent(basename)}${query}`;
};

export const fetchScenarioSets = () =>
  api<ScenarioSetDetail[]>('/api/scenario-test/sets');

export const getScenarioSet = (name: string) =>
  api<ScenarioSetDetail>(`/api/scenario-test/sets/${encodeURIComponent(name)}`);

export const saveScenarioSet = (name: string, custom: ScenarioSpec[]) =>
  api<{ name: string; path: string }>('/api/scenario-test/sets', {
    method: 'POST',
    body: JSON.stringify({ name, custom }),
  });

export const deleteScenarioSet = (name: string) =>
  api<{ ok: boolean; name: string }>(`/api/scenario-test/sets/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });

export const fetchScenarioSetsFull = () =>
  api<ScenarioSetSummary[]>('/api/scenario-test/sets/full');

export const getScenarioSetScenarios = (name: string) =>
  api<ScenarioSpec[]>(`/api/scenario-test/sets/${encodeURIComponent(name)}/scenarios`);

export const generateScenarioSet = (body: ScenarioGridRequest) =>
  api<{ name: string; num_scenarios: number; path: string }>(
    '/api/scenario-test/sets/generate',
    { method: 'POST', body: JSON.stringify(body) },
  );

// --- Backtest -------------------------------------------------

export const createBacktestRun = (body: BacktestRunRequest) =>
  api<BacktestRun>('/api/backtest/runs', { method: 'POST', body: JSON.stringify(body) });

export const listBacktestRuns = (portfolioId: number) =>
  api<BacktestRun[]>(`/api/backtest/runs?portfolio_id=${portfolioId}`);

export const getBacktestRun = (runId: number) =>
  api<BacktestRun>(`/api/backtest/runs/${runId}`);

export const backtestArtifactUrl = (runId: number, name: string, options?: { download?: boolean }) => {
  const basename = name.split('/').pop() ?? name;
  const query = options?.download ? '?download=true' : '';
  return `/api/backtest/runs/${runId}/artifacts/${encodeURIComponent(basename)}${query}`;
};

// --- desk workflows ---
export const listPortfolios = () =>
  api<Array<{ name: string }>>('/api/portfolios').then((rows) => rows.map((r) => r.name));
export const listWorkflows = () => api<DeskWorkflowSummary[]>('/api/workflows');
export const getWorkflow = (slug: string) => api<DeskWorkflow>(`/api/workflows/${slug}`);
export const createWorkflow = (script: string) =>
  api<DeskWorkflow>('/api/workflows', { method: 'POST', body: JSON.stringify({ script }) });
export const updateWorkflow = (slug: string, script: string) =>
  api<DeskWorkflow>(`/api/workflows/${slug}`, { method: 'PUT', body: JSON.stringify({ script }) });
export const deleteWorkflow = (slug: string) =>
  api<{ ok: boolean }>(`/api/workflows/${slug}`, { method: 'DELETE' });
export const validateWorkflow = (script: string) =>
  api<{ ok: boolean; error: string | null }>('/api/workflows/validate', {
    method: 'POST',
    body: JSON.stringify({ script }),
  });

// --- Memory Console ---------------------------------------------------------

export function errorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === 'string') return parsed.detail;
  } catch {
    /* not JSON */
  }
  return raw || 'request failed';
}

export const listMemoryFacts = (params: {
  scope_type?: string;
  scope_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}) => {
  const q = new URLSearchParams();
  if (params.scope_type) q.set('scope_type', params.scope_type);
  if (params.scope_id) q.set('scope_id', params.scope_id);
  if (params.status) q.set('status', params.status);
  q.set('limit', String(params.limit ?? 100));
  q.set('offset', String(params.offset ?? 0));
  return api<{ items: MemoryFact[]; total: number }>(`/api/memory/facts?${q.toString()}`);
};

export const createMemoryFact = (body: {
  scope_type: string;
  scope_id?: string;
  content: string;
  confidence: number;
  category?: string | null;
}) => api<MemoryFact>('/api/memory/facts', { method: 'POST', body: JSON.stringify(body) });

export const patchMemoryFact = (
  id: number,
  body: { content?: string; confidence?: number; category?: string | null },
) => api<MemoryFact>(`/api/memory/facts/${id}`, { method: 'PATCH', body: JSON.stringify(body) });

export const approveMemoryFact = (id: number) =>
  api<MemoryFact>(`/api/memory/facts/${id}/approve`, { method: 'POST' });

export const setMemoryFactPinned = (id: number, pinned: boolean) =>
  api<MemoryFact>(`/api/memory/facts/${id}/pin`, { method: 'PATCH', body: JSON.stringify({ pinned }) });

export const deleteMemoryFact = (id: number) =>
  api<void>(`/api/memory/facts/${id}`, { method: 'DELETE' });

export const getMemoryStatus = () => api<MemoryStatus>('/api/memory/status');

export const listPortfoliosWithIds = () =>
  api<Array<{ id: number; name: string }>>('/api/portfolios').then((rows) =>
    rows.map((r) => ({ id: r.id, name: r.name })),
  );

// --- Model maintenance (agent channel/model registry) ----------------------
// Channel names are URL-encoded; model ids are sent RAW because they contain
// slashes (e.g. anthropic/claude-sonnet-4.6) and the backend route uses the
// {model_id:path} converter to match the whole remainder.

export const getAgentRegistry = () => api<AgentRegistry>('/api/agent/registry');

export const createChannel = (c: ChannelWrite) =>
  api<AgentRegistry>('/api/agent/channels', { method: 'POST', body: JSON.stringify(c) });

export const updateChannel = (name: string, c: ChannelWrite) =>
  api<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(c),
  });

export const deleteChannel = (name: string) =>
  api<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(name)}`, { method: 'DELETE' });

export const createModel = (channel: string, m: ModelWrite) =>
  api<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(channel)}/models`, {
    method: 'POST',
    body: JSON.stringify(m),
  });

export const updateModel = (channel: string, id: string, m: ModelWrite) =>
  api<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(channel)}/models/${id}`, {
    method: 'PUT',
    body: JSON.stringify(m),
  });

export const deleteModel = (channel: string, id: string) =>
  api<AgentRegistry>(`/api/agent/channels/${encodeURIComponent(channel)}/models/${id}`, {
    method: 'DELETE',
  });

export const setDefaultModel = (channel: string, model: string) =>
  api<AgentRegistry>('/api/agent/registry/default', {
    method: 'PUT',
    body: JSON.stringify({ channel, model }),
  });

export const validateDraft = (kind: string, payload: unknown) =>
  api<{ ok: boolean; errors: string[] }>('/api/agent/channels/validate', {
    method: 'POST',
    body: JSON.stringify({ kind, payload }),
  });

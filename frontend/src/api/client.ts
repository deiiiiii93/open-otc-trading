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
  'id' | 'created_at' | 'updated_at' | 'source' | 'loaded_at' | 'contract_code'
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

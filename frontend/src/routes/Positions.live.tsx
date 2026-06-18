import { useEffect, useRef, useState } from 'react';
import { api, listEngineConfigs, uploadForm } from '../api/client';
import type {
  EngineConfigVariant,
  PageContextReporter,
  Portfolio,
  PositionImportBatch,
  Position,
  PositionValuationRun,
  PricingParameterProfile,
  ResolvedPricingParams,
  TaskRun,
  PositionLifecycleEvent,
} from '../types';
import { Positions } from './Positions';
import { Empty } from '../components/Empty';
import { Skeleton } from '../components/Skeleton';
import type { PositionPricingRequest, PositionRow } from './Positions';
import type { PositionsRiskSummary } from './Positions';

const POSITIONS_PORTFOLIO_PATH = '/api/portfolios';
const ACTIVE_TASK_STATUSES = new Set(['queued', 'running']);
const TERMINAL_TASK_STATUSES = new Set(['completed', 'completed_with_errors', 'failed']);
const POLL_INTERVAL_MS = 1000;

const formatElapsedSeconds = (startedAtMs: number, endedAtMs: number = performance.now()) => (
  ((endedAtMs - startedAtMs) / 1000).toFixed(1)
);

type Props = {
  initialPricingProfileId?: number | null;
  onPricingProfileChange?: (profileId: number | null) => void;
  onPageContextChange?: PageContextReporter;
};

type RiskRunResponse = {
  id: number;
  portfolio_id: number;
  pricing_parameter_profile_id?: number | null;
  status: string;
  metrics?: {
    totals?: PositionsRiskSummary | null;
    by_currency?: Record<string, PositionsRiskSummary> | null;
  };
};

export function PositionsLive({
  initialPricingProfileId = null,
  onPricingProfileChange,
  onPageContextChange,
}: Props) {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(null);
  const [importPortfolioId, setImportPortfolioId] = useState<number | null>(null);
  const [runs, setRuns] = useState<PositionValuationRun[]>([]);
  const [riskSummary, setRiskSummary] = useState<PositionsRiskSummary | null>(null);
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([]);
  const [engineConfigs, setEngineConfigs] = useState<EngineConfigVariant[]>([]);
  const [selectedPricingProfileId, setSelectedPricingProfileId] = useState<number | null>(initialPricingProfileId);
  const [selectedEngineConfigId, setSelectedEngineConfigId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [pricingPositionId, setPricingPositionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [editingPositionId, setEditingPositionId] = useState<number | null>(null);
  const [lifecycleEvents, setLifecycleEvents] = useState<PositionLifecycleEvent[]>([]);
  const [addingLifecycleEvent, setAddingLifecycleEvent] = useState(false);
  const [resolvedParams, setResolvedParams] = useState<ResolvedPricingParams | null>(null);
  const [resolvedParamsLoading, setResolvedParamsLoading] = useState(false);
  const selectedPortfolioIdRef = useRef<number | null>(selectedPortfolioId);
  const latestPricingTaskRequestIdRef = useRef(0);
  const resolvedParamsRequestRef = useRef(0);

  useEffect(() => {
    selectedPortfolioIdRef.current = selectedPortfolioId;
  }, [selectedPortfolioId]);

  const load = async (
    showLoading = true,
    preferredPortfolioId = selectedPortfolioId,
    preferredImportPortfolioId = importPortfolioId,
  ) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const [portfolios, pricingProfiles, loadedEngineConfigs] = await Promise.all([
        api<Portfolio[]>(POSITIONS_PORTFOLIO_PATH),
        api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
        // Non-fatal: the page must still load when engine configs can't be
        // fetched (picker falls back to "Position engines only").
        listEngineConfigs().catch(() => [] as EngineConfigVariant[]),
      ]);
      setPortfolios(portfolios);
      setPricingProfiles(Array.isArray(pricingProfiles) ? pricingProfiles : []);
      const engineRows = Array.isArray(loadedEngineConfigs) ? loadedEngineConfigs : [];
      setEngineConfigs(engineRows);
      setSelectedEngineConfigId((current) => current ?? engineRows.find((row) => row.is_default)?.id ?? engineRows[0]?.id ?? null);
      const desk = chooseSelectedPortfolio(portfolios, preferredPortfolioId);
      if (!desk) {
        setPortfolio(null);
        setSelectedPortfolioId(null);
        setImportPortfolioId(null);
        setRuns([]);
        setRiskSummary(null);
        return;
      }
      const nextImportPortfolioId = chooseImportPortfolioId(portfolios, preferredImportPortfolioId, desk);
      setPortfolio(desk);
      setSelectedPortfolioId(desk.id);
      setImportPortfolioId(nextImportPortfolioId);
      const [runs, latestRiskRun] = await Promise.all([
        api<PositionValuationRun[]>(`/api/portfolios/${desk.id}/runs`),
        fetchLatestRiskRun(desk.id),
      ]);
      setRuns(runs);
      if (
        initialPricingProfileId === null &&
        (selectedPortfolioId == null || preferredPortfolioId !== selectedPortfolioId)
      ) {
        setSelectedPricingProfileId(
          latestRiskRun
            ? latestRiskRun.pricing_parameter_profile_id ?? null
            : runs[0]?.pricing_parameter_profile_id ?? null,
        );
      }
      setRiskSummary(summaryFromRiskRun(latestRiskRun));

      const lifecycleEventsList = await fetchLifecycleEvents(desk.id);
      setLifecycleEvents(lifecycleEventsList);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    setSelectedPricingProfileId(initialPricingProfileId);
  }, [initialPricingProfileId]);

  const handleSelectPortfolio = (portfolioId: number) => {
    setFeedback(null);
    setSelectedPortfolioId(portfolioId);
    void load(true, portfolioId, importPortfolioId);
  };

  const handleSelectImportPortfolio = (portfolioId: number) => {
    setImportPortfolioId(portfolioId);
  };

  const handleSelectPricingProfile = (profileId: number | null) => {
    setSelectedPricingProfileId(profileId);
    onPricingProfileChange?.(profileId);
  };

  const handleDetailOpen = async (row: PositionRow) => {
    if (!portfolio) return;
    const requestId = ++resolvedParamsRequestRef.current;
    setResolvedParams(null);
    setResolvedParamsLoading(true);
    const query = selectedPricingProfileId == null
      ? ''
      : `?pricing_parameter_profile_id=${selectedPricingProfileId}`;
    try {
      const params = await api<ResolvedPricingParams>(
        `/api/portfolios/${portfolio.id}/positions/${row.id}/pricing-params${query}`,
      );
      if (resolvedParamsRequestRef.current !== requestId) return;
      setResolvedParams(params);
    } catch {
      if (resolvedParamsRequestRef.current !== requestId) return;
      setResolvedParams(null);
    } finally {
      if (resolvedParamsRequestRef.current === requestId) {
        setResolvedParamsLoading(false);
      }
    }
  };

  const handleRunPricing = async () => {
    if (!portfolio) return;
    const portfolioId = portfolio.id;
    const requestId = ++latestPricingTaskRequestIdRef.current;
    try {
      const run = await api<{ id: number; status: string; task_id: number | null }>(
        '/api/batch-pricing/runs',
        {
          method: 'POST',
          body: JSON.stringify({
            portfolio_id: portfolioId,
            ...pricingProfileRequestBody(selectedPricingProfileId),
            ...(selectedEngineConfigId == null ? {} : { engine_config_id: selectedEngineConfigId }),
          }),
        },
      );
      if (run.task_id != null && ACTIVE_TASK_STATUSES.has(run.status)) {
        setFeedback(`Task #${run.task_id} queued: batch pricing started`);
        void pollPricingTask(run.task_id, portfolioId, importPortfolioId, requestId);
        return;
      }
      setFeedback(`Batch pricing run #${run.id} ${run.status.replaceAll('_', ' ')}`);
      await load(true, portfolioId, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not run batch pricing: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const fetchTask = async (taskId: number): Promise<TaskRun> => api<TaskRun>(`/api/tasks/${taskId}`);

  const taskFeedback = (task: TaskRun, fallback: string): string => {
    const total = task.progress_total || 0;
    const progress = total > 0 ? ` (${task.progress_current}/${total})` : '';
    return `Task #${task.id} ${task.status.replaceAll('_', ' ')}${progress}: ${task.message ?? fallback}`.trim();
  };

  const pollPricingTask = async (
    taskId: number,
    portfolioId: number,
    importTargetId: number | null,
    requestId: number,
  ) => {
    try {
      while (
        selectedPortfolioIdRef.current === portfolioId
        && latestPricingTaskRequestIdRef.current === requestId
      ) {
        const task = await fetchTask(taskId);
        if (
          selectedPortfolioIdRef.current !== portfolioId
          || latestPricingTaskRequestIdRef.current !== requestId
        ) return;
        setFeedback(taskFeedback(task, 'Pricing run in progress'));
        if (TERMINAL_TASK_STATUSES.has(task.status)) {
          if (task.status === 'failed') {
            setFeedback(`Could not run pricing: ${task.error || task.message || 'Task failed'}`);
            return;
          }
          await load(true, portfolioId, importTargetId);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
      }
    } catch (e) {
      if (
        selectedPortfolioIdRef.current === portfolioId
        && latestPricingTaskRequestIdRef.current === requestId
      ) {
        setFeedback(`Could not monitor pricing task: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  };

  const handlePricePosition = async (row: PositionRow, request: PositionPricingRequest) => {
    if (!portfolio) return;
    setPricingPositionId(row.id);
    const pricingStartedAt = performance.now();
    try {
      await api<PositionValuationRun>(`/api/portfolios/${portfolio.id}/positions/price`, {
        method: 'POST',
        body: JSON.stringify({
          position_ids: [row.id],
          ...pricingProfileRequestBody(selectedPricingProfileId),
          ...(selectedEngineConfigId == null ? {} : { engine_config_id: selectedEngineConfigId }),
          ...request,
        }),
      });
      setFeedback(`Priced ${row.trade_id}. Took ${formatElapsedSeconds(pricingStartedAt)} seconds.`);
      await load(false, portfolio.id, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not price ${row.trade_id}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPricingPositionId(null);
    }
  };

  const handleImportPositions = async (file: File) => {
    if (importPortfolioId == null) return;
    const form = new FormData();
    form.append('file', file);
    form.append('sheet_name', 'Positions');
    setImporting(true);
    try {
      const batch = await uploadForm<PositionImportBatch>(`/api/portfolios/${importPortfolioId}/positions/import`, form);
      setFeedback(
        `Imported ${batch.imported_count} positions · ${batch.supported_count} supported · ${batch.error_count} errors`,
      );
      await load(true, importPortfolioId, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not import positions: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setImporting(false);
    }
  };

  const handleEditPosition = async (row: import('./Positions').PositionRow, updates: Partial<import('./Positions').PositionRow>) => {
    if (!portfolio) return;
    setEditingPositionId(row.id);
    try {
      const patchBody: Record<string, unknown> = {};
      if (updates.underlying !== undefined) patchBody.underlying = updates.underlying;
      if (updates.product_type !== undefined) patchBody.product_type = updates.product_type;
      if (updates.quantity !== undefined) patchBody.quantity = updates.quantity;
      if (updates.entry_price !== undefined) patchBody.entry_price = updates.entry_price;
      if (updates.currency !== undefined) patchBody.currency = updates.currency;
      if (updates.status !== undefined) patchBody.status = updates.status;
      if (updates.position_kind !== undefined) patchBody.position_kind = updates.position_kind;
      if (updates.trade_id !== undefined) patchBody.source_trade_id = updates.trade_id;
      if (updates.engine_name !== undefined) patchBody.engine_name = updates.engine_name;
      if (updates.product_kwargs !== undefined) patchBody.product_kwargs = updates.product_kwargs;
      if (updates.product !== undefined) patchBody.product = updates.product;
      if (updates.engine_kwargs !== undefined) patchBody.engine_kwargs = updates.engine_kwargs;

      await api(`/api/portfolios/${portfolio.id}/positions/${row.id}`, {
        method: 'PATCH',
        body: JSON.stringify(patchBody),
      });
      setFeedback(`Updated ${row.trade_id}`);
      await load(false, portfolio.id, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not update ${row.trade_id}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setEditingPositionId(null);
    }
  };

  const handleAddLifecycleEvent = async (row: import('./Positions').PositionRow, eventType: string, eventData: Record<string, unknown>) => {
    if (!portfolio) return;
    setAddingLifecycleEvent(true);
    try {
      await api(`/api/portfolios/${portfolio.id}/positions/${row.id}/lifecycle-events`, {
        method: 'POST',
        body: JSON.stringify({ event_type: eventType, event_data: eventData }),
      });
      setFeedback(`Added ${eventType} event to ${row.trade_id}`);
      await load(false, portfolio.id, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not add lifecycle event: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAddingLifecycleEvent(false);
    }
  };

  const handleCancelLifecycleEvent = async (
    row: import('./Positions').PositionRow,
    event: PositionLifecycleEvent,
    reason: string | null,
  ) => {
    if (!portfolio) return;
    setAddingLifecycleEvent(true);
    try {
      await api(`/api/portfolios/${portfolio.id}/positions/${row.id}/lifecycle-events/${event.id}/cancel`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      });
      setFeedback(`Cancelled ${event.event_type} event for ${row.trade_id}`);
      await load(false, portfolio.id, importPortfolioId);
    } catch (e) {
      setFeedback(`Could not cancel lifecycle event: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setAddingLifecycleEvent(false);
    }
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={80} />
        <div style={{ height: 12 }} />
        <Skeleton height={240} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load positions: ${error}`} />;
  }

  if (!portfolio) {
    return <Empty message="No portfolios available." />;
  }

  const latestResults = new Map<number, PositionValuationRun['results'][number]>();
  for (const valuationRun of runs) {
    for (const result of valuationRun.results ?? []) {
      if (!latestResults.has(result.position_id)) {
        latestResults.set(result.position_id, result);
      }
    }
  }
  const summaryRun = runs.find(isFullPortfolioRun) ?? runs[0] ?? null;
  const rows: PositionRow[] = (portfolio.positions ?? []).map((position) => {
    const result = latestResults.get(position.id);
    const resultMarketInputs = result?.market_inputs;
    return {
      id: position.id,
      trade_id: String(position.source_trade_id ?? `#${position.id}`),
      product_id: position.product_id,
      product: position.product,
      underlying: position.underlying,
      product_type: position.product_type,
      quantity: Number(position.quantity ?? 0),
      entry_price: Number(position.entry_price ?? 0),
      currency: position.currency,
      status: position.status,
      position_kind: position.position_kind ?? 'otc',
      mapping_status: position.mapping_status,
      source_row: position.source_row,
      mapping_error: position.mapping_error,
      product_kwargs: position.product_kwargs,
      source_payload: position.source_payload,
      engine_name: position.engine_name,
      engine_kwargs: position.engine_kwargs,
      price: numberOrNull(result?.price),
      market_value: numberOrNull(result?.market_value),
      pnl: numberOrNull(result?.pnl),
      delta: numberOrNull(result?.result_payload?.delta),
      gamma: numberOrNull(result?.result_payload?.gamma),
      vega: numberOrNull(result?.result_payload?.vega),
      theta: numberOrNull(result?.result_payload?.theta),
      rho: numberOrNull(result?.result_payload?.rho),
      rho_q: numberOrNull(result?.result_payload?.rho_q),
      // PositionMarketInput store is gone (instrument-unification): the only
      // market-inputs source is the latest valuation result's resolved payload.
      market_inputs: hasObjectValues(resultMarketInputs) ? resultMarketInputs : undefined,
      result_payload: result?.result_payload,
      pricing_error: result?.error ?? null,
    };
  });

  const summary = summaryRun?.summary ?? {};
  const navValue = (summary as Record<string, unknown>).nav ?? (summary as Record<string, unknown>).market_value;
  const pnlValue = (summary as Record<string, unknown>).pnl;
  const deltaValue = (summary as Record<string, unknown>).delta;
  const vegaValue = (summary as Record<string, unknown>).vega;
  const nav = formatSummaryValue(navValue);
  const pnl = formatSummaryValue(pnlValue);
  const delta = formatSummaryValue(deltaValue);
  const vega = formatSummaryValue(vegaValue);

  return (
    <Positions
      rows={rows}
      portfolios={portfolios.map(toPortfolioOption)}
      containerPortfolios={portfolios.filter((item) => item.kind === 'container').map(toPortfolioOption)}
      pricingProfiles={pricingProfiles.map(toPricingProfileOption)}
      engineConfigs={engineConfigs}
      selectedPortfolioId={portfolio.id}
      importPortfolioId={importPortfolioId}
      selectedPricingProfileId={selectedPricingProfileId}
      selectedEngineConfigId={selectedEngineConfigId}
      portfolioName={portfolio.name}
      portfolioKind={portfolio.kind}
      nav={nav}
      pnl={pnl}
      pnlVariant={isNegative(pnlValue) ? 'neg' : pnl === '—' ? 'default' : 'pos'}
      delta={delta}
      deltaVariant={isNegative(deltaValue) ? 'neg' : delta === '—' ? 'default' : 'pos'}
      vega={vega}
      riskSummary={riskSummary ?? summaryFromValuationRun(summaryRun)}
      valuationDate={summaryRun?.valuation_date ?? '—'}
      onSelectPortfolio={handleSelectPortfolio}
      onSelectImportPortfolio={handleSelectImportPortfolio}
      onSelectPricingProfile={handleSelectPricingProfile}
      onSelectEngineConfig={setSelectedEngineConfigId}
      onRunPricing={handleRunPricing}
      onPricePosition={handlePricePosition}
      onImportPositions={handleImportPositions}
      importingPositions={importing}
      pricingPositionId={pricingPositionId}
      importFeedback={feedback}
      onPageContextChange={onPageContextChange}
      onEditPosition={handleEditPosition}
      editingPositionId={editingPositionId}
      lifecycleEvents={lifecycleEvents}
      onAddLifecycleEvent={handleAddLifecycleEvent}
      onCancelLifecycleEvent={handleCancelLifecycleEvent}
      addingLifecycleEvent={addingLifecycleEvent}
      resolvedParams={resolvedParams}
      resolvedParamsLoading={resolvedParamsLoading}
      onDetailOpen={handleDetailOpen}
    />
  );
}

function chooseSelectedPortfolio(portfolios: Portfolio[], preferredPortfolioId: number | null): Portfolio | null {
  const preferred = preferredPortfolioId == null
    ? null
    : portfolios.find((candidate) => candidate.id === preferredPortfolioId);
  return preferred ?? latestUpdatedPortfolio(portfolios);
}

function chooseImportPortfolioId(
  portfolios: Portfolio[],
  preferredPortfolioId: number | null,
  selectedPortfolio: Portfolio,
): number | null {
  const containers = portfolios.filter((candidate) => candidate.kind === 'container');
  const preferred = preferredPortfolioId == null
    ? null
    : containers.find((candidate) => candidate.id === preferredPortfolioId);
  if (preferred) return preferred.id;
  if (selectedPortfolio.kind === 'container') return selectedPortfolio.id;
  return latestUpdatedPortfolio(containers)?.id ?? null;
}

function latestUpdatedPortfolio(portfolios: Portfolio[]): Portfolio | null {
  return [...portfolios].sort((left, right) => timestamp(right.updated_at) - timestamp(left.updated_at))[0] ?? null;
}

function timestamp(value: string | undefined): number {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : 0;
}

function toPortfolioOption(portfolio: Portfolio) {
  return {
    id: portfolio.id,
    name: portfolio.name,
    kind: portfolio.kind,
  };
}

function toPricingProfileOption(profile: PricingParameterProfile) {
  return {
    id: profile.id,
    name: profile.name,
    valuation_date: profile.valuation_date,
    rows: profile.rows,
  };
}

function pricingProfileRequestBody(profileId: number | null): { pricing_parameter_profile_id?: number } {
  return profileId == null ? {} : { pricing_parameter_profile_id: profileId };
}

function numberOrNull(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatSummaryValue(value: unknown): string {
  if (value == null || value === '') return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  const sign = parsed < 0 ? '-' : '';
  const abs = Math.abs(parsed);
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(2)}K`;
  return `${sign}${abs.toFixed(2)}`;
}

function isNegative(value: unknown): boolean {
  return Number(value) < 0;
}

async function fetchLatestRiskRun(portfolioId: number): Promise<RiskRunResponse | null> {
  try {
    return await api<RiskRunResponse | null>(`/api/portfolios/${portfolioId}/risk-runs/latest`);
  } catch {
    return null;
  }
}

async function fetchLifecycleEvents(portfolioId: number): Promise<PositionLifecycleEvent[]> {
  try {
    return await api<PositionLifecycleEvent[]>(`/api/portfolios/${portfolioId}/lifecycle-events`);
  } catch {
    return [];
  }
}

function summaryFromRiskRun(run: RiskRunResponse | null): PositionsRiskSummary | null {
  const totals = run?.metrics?.totals;
  if (totals && Object.keys(totals).length > 0) return totals;
  const byCurrency = run?.metrics?.by_currency;
  if (!byCurrency || Object.keys(byCurrency).length === 0) return null;
  return Object.values(byCurrency).reduce<PositionsRiskSummary>((acc, bucket) => mergeSummary(acc, bucket), {});
}

function summaryFromValuationRun(run: PositionValuationRun | null): PositionsRiskSummary | null {
  if (!run?.summary) return null;
  return run.summary as PositionsRiskSummary;
}

function mergeSummary(left: PositionsRiskSummary, right: PositionsRiskSummary): PositionsRiskSummary {
  const merged: PositionsRiskSummary = { ...left };
  for (const key of Object.keys(right) as Array<keyof PositionsRiskSummary>) {
    const value = right[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      merged[key] = (merged[key] ?? 0) + value;
    }
  }
  return merged;
}

function hasObjectValues(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value).length > 0;
}

function isFullPortfolioRun(run: PositionValuationRun): boolean {
  const positionIds = run.overrides?.position_ids;
  return !Array.isArray(positionIds) || positionIds.length === 0;
}

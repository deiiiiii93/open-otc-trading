import { useEffect, useRef, useState } from 'react';
import { api, listEngineConfigs } from '../api/client';
import type {
  EngineConfigVariant,
  PricingParameterProfile,
  PageContextReporter,
  Portfolio,
  TaskRun,
} from '../types';
import { Risk, type RiskMetrics, type RiskPortfolioOption } from './Risk';
import type { CurrencyGreeks, GreeksTotals } from '../components/GreeksSummary';
import { Skeleton } from '../components/Skeleton';
import { Empty } from '../components/Empty';


type RiskRunResponse = {
  id: number;
  portfolio_id: number;
  pricing_parameter_profile_id: number | null;
  market_snapshot_id: number | null;
  method: string;
  status: string;
  metrics: {
    totals?: GreeksTotals | null;
    by_currency?: Record<string, CurrencyGreeks> | null;
    positions?: RiskMetrics['positions'];
  };
  task_id?: number | null;
  created_at: string;
};

type ReportJobOut = {
  id: number;
  report_type: string;
  status: string;
  task_id?: number | null;
};

type Props = {
  onPageContextChange?: PageContextReporter;
  /** Session-shared portfolio preference; honored only if it exists in this
   * page's portfolio list (fallbacks never write back). */
  portfolioId?: number | null;
  onPortfolioIdChange?: (id: number) => void;
};

const ACTIVE_TASK_STATUSES = new Set(['queued', 'running']);
const TERMINAL_TASK_STATUSES = new Set(['completed', 'completed_with_errors', 'failed']);
const POLL_INTERVAL_MS = 1000;

export function RiskLive({ onPageContextChange, portfolioId, onPortfolioIdChange }: Props) {
  const [portfolios, setPortfolios] = useState<RiskPortfolioOption[]>([]);
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([]);
  const [engineConfigs, setEngineConfigs] = useState<EngineConfigVariant[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedPricingProfileId, setSelectedPricingProfileId] = useState<number | null>(null);
  const [selectedEngineConfigId, setSelectedEngineConfigId] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<RiskMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const selectedIdRef = useRef<number | null>(null);
  const latestRequestIdRef = useRef(0);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  const enrichPositions = (
    portfolioId: number,
    positions: RiskMetrics['positions'],
  ): RiskMetrics['positions'] => {
    const portfolio = portfolios.find((p) => p.id === portfolioId);
    if (!portfolio?.positions?.length) return positions;
    const tradeIds = new Map(portfolio.positions.map((p) => [p.id, p.source_trade_id ?? null]));
    return positions.map((position) => ({
      ...position,
      source_trade_id: position.source_trade_id ?? tradeIds.get(position.position_id) ?? null,
    }));
  };

  // Mixed-currency runs have totals: null with per-currency money greeks in
  // by_currency — either shape counts as displayable metrics.
  const hasRiskMetrics = (run: RiskRunResponse): boolean =>
    Array.isArray(run.metrics.positions) &&
    (Boolean(run.metrics.totals) || Object.keys(run.metrics.by_currency ?? {}).length > 0);

  const toRiskMetrics = (run: RiskRunResponse): RiskMetrics => ({
    totals: run.metrics.totals ?? null,
    byCurrency: run.metrics.by_currency ?? null,
    positions: enrichPositions(run.portfolio_id, run.metrics.positions ?? []),
  });

  const fetchLatestRiskRun = async (portfolioId: number): Promise<RiskRunResponse | null> => {
    const response = await fetch(`/api/portfolios/${portfolioId}/risk-runs/latest`);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  };

  const fetchRiskRun = async (riskRunId: number): Promise<RiskRunResponse> => {
    const response = await fetch(`/api/risk/runs/${riskRunId}`);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  };

  const fetchTask = async (taskId: number): Promise<TaskRun> => {
    const response = await fetch(`/api/tasks/${taskId}`);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  };

  const taskFeedback = (task: TaskRun): string => {
    const total = task.progress_total || 0;
    const progress = total > 0 ? ` (${task.progress_current}/${total})` : '';
    return `Task #${task.id} ${task.status.replaceAll('_', ' ')}${progress}: ${task.message ?? ''}`.trim();
  };

  const pollTask = async (
    taskId: number,
    portfolioId: number,
    riskRunId: number,
    cancelledRef: { current: boolean },
    requestId: number,
  ) => {
    try {
      while (!cancelledRef.current && selectedIdRef.current === portfolioId && latestRequestIdRef.current === requestId) {
        const task = await fetchTask(taskId);
        if (cancelledRef.current || selectedIdRef.current !== portfolioId || latestRequestIdRef.current !== requestId) return;
        setFeedback(taskFeedback(task));
        setRunning(ACTIVE_TASK_STATUSES.has(task.status));
        if (TERMINAL_TASK_STATUSES.has(task.status)) {
          if (task.status === 'failed') {
            setError(task.error || 'Risk run failed');
            setRunning(false);
            return;
          }
          const finalRun = await fetchRiskRun(riskRunId);
          if (cancelledRef.current || selectedIdRef.current !== portfolioId || latestRequestIdRef.current !== requestId) return;
          if (hasRiskMetrics(finalRun)) {
            setMetrics(toRiskMetrics(finalRun));
          }
          setRunning(false);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
      }
    } catch (e) {
      if (!cancelledRef.current && selectedIdRef.current === portfolioId && latestRequestIdRef.current === requestId) {
        setError(e instanceof Error ? e.message : String(e));
        setRunning(false);
      }
    }
  };

  const loadLatestRiskRun = async (portfolioId: number, cancelledRef: { current: boolean }) => {
    const requestId = ++latestRequestIdRef.current;
    setMetricsLoading(true);
    setError(null);
    try {
      const latest = await fetchLatestRiskRun(portfolioId);
      if (cancelledRef.current || selectedIdRef.current !== portfolioId || latestRequestIdRef.current !== requestId) return;
      if (!latest) {
        setMetrics(null);
        setFeedback(null);
        return;
      }
      setSelectedPricingProfileId(latest.pricing_parameter_profile_id ?? null);
      if (ACTIVE_TASK_STATUSES.has(latest.status) && latest.task_id != null) {
        setMetrics(hasRiskMetrics(latest) ? toRiskMetrics(latest) : null);
        setRunning(true);
        setFeedback(`Task #${latest.task_id} ${latest.status}: ${latest.method} risk run`);
        void pollTask(latest.task_id, portfolioId, latest.id, cancelledRef, requestId);
        return;
      }
      setMetrics(hasRiskMetrics(latest) ? toRiskMetrics(latest) : null);
    } catch (e) {
      if (!cancelledRef.current && selectedIdRef.current === portfolioId && latestRequestIdRef.current === requestId) {
        setError(e instanceof Error ? e.message : String(e));
        setMetrics(null);
      }
    } finally {
      if (!cancelledRef.current && selectedIdRef.current === portfolioId && latestRequestIdRef.current === requestId) {
        setMetricsLoading(false);
      }
    }
  };

  const runRiskForPortfolio = async (portfolioId: number, cancelledRef: { current: boolean }) => {
    const requestId = latestRequestIdRef.current + 1;
    latestRequestIdRef.current = requestId;
    setMetricsLoading(false);
    setRunning(true);
    setFeedback(null);
    setError(null);
    const requestBody: { portfolio_id: number; pricing_parameter_profile_id?: number; engine_config_id?: number } = {
      portfolio_id: portfolioId,
    };
    if (selectedPricingProfileId != null) {
      requestBody.pricing_parameter_profile_id = selectedPricingProfileId;
    }
    if (selectedEngineConfigId != null) {
      requestBody.engine_config_id = selectedEngineConfigId;
    }
    try {
      const res = await api<RiskRunResponse>('/api/batch-pricing/runs', {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });
      if (cancelledRef.current || selectedIdRef.current !== portfolioId) return;
      if (hasRiskMetrics(res)) {
        setMetrics(toRiskMetrics(res));
      } else {
        setMetrics(null);
      }
      if (res.task_id != null && ACTIVE_TASK_STATUSES.has(res.status)) {
        setFeedback(`Task #${res.task_id} queued: risk run started`);
        void pollTask(res.task_id, portfolioId, res.id, cancelledRef, requestId);
        return;
      }
      setRunning(false);
    } catch (e) {
      if (!cancelledRef.current) {
        setError(e instanceof Error ? e.message : String(e));
        setRunning(false);
      }
    }
  };

  useEffect(() => {
    const cancelledRef = { current: false };
    (async () => {
      try {
        const [portfolioResult, profilesResult, engineConfigResult] = await Promise.allSettled([
          api<Portfolio[]>('/api/portfolios'),
          api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
          listEngineConfigs(),
        ]);
        if (cancelledRef.current) return;
        if (portfolioResult.status === 'rejected') {
          throw portfolioResult.reason;
        }
        const list = portfolioResult.value;
        if (profilesResult.status === 'fulfilled') {
          setPricingProfiles(Array.isArray(profilesResult.value) ? profilesResult.value : []);
        } else {
          setPricingProfiles([]);
        }
        if (engineConfigResult.status === 'fulfilled') {
          const rows = Array.isArray(engineConfigResult.value) ? engineConfigResult.value : [];
          setEngineConfigs(rows);
          setSelectedEngineConfigId(rows.find((row) => row.is_default)?.id ?? rows[0]?.id ?? null);
        }
        const options: RiskPortfolioOption[] = list.map((p) => ({
          id: p.id,
          name: p.name,
          positions: p.positions.map((position) => ({
            id: position.id,
            source_trade_id: position.source_trade_id ?? null,
          })),
        }));
        setPortfolios(options);
        if (options.length > 0) {
          const preferredId =
            portfolioId != null && options.some((option) => option.id === portfolioId)
              ? portfolioId
              : options[0].id;
          setSelectedId(preferredId);
        }
      } catch (e) {
        if (!cancelledRef.current) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    })();
    return () => { cancelledRef.current = true; };
    // Mount-time seed of the selection from the shared preference + portfolio
    // list. Post-mount prop changes are handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React to the shared portfolio changing AFTER mount — e.g. browser
  // Back/Forward over /risk?portfolio=N updates the prop while this page stays
  // mounted. Depends on `portfolios` too so that a prop change arriving BEFORE
  // the portfolio list loads is still honored once the options resolve (this
  // effect runs after the loader's batched setPortfolios+setSelectedId, so it
  // overrides the loader's stale mount-time selection). The `!== selectedId`
  // guard keeps the page's own picks from looping.
  useEffect(() => {
    if (
      portfolioId != null
      && portfolioId !== selectedId
      && portfolios.some((option) => option.id === portfolioId)
    ) {
      setSelectedId(portfolioId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioId, portfolios]);

  useEffect(() => {
    if (selectedId == null) {
      setMetrics(null);
      return;
    }
    const cancelledRef = { current: false };
    setMetrics(null);
    void loadLatestRiskRun(selectedId, cancelledRef);
    return () => { cancelledRef.current = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const handleRunRisk = async () => {
    if (selectedId == null) return;
    const cancelledRef = { current: false };
    await runRiskForPortfolio(selectedId, cancelledRef);
  };

  const promoteToReport = async () => {
    if (selectedId == null) return;
    const portfolio = portfolios.find((p) => p.id === selectedId);
    if (!portfolio) return;
    try {
      const requestBody: {
        report_type: 'risk';
        portfolio_id: number;
        title: string;
        pricing_parameter_profile_id?: number;
      } = {
        report_type: 'risk',
        portfolio_id: selectedId,
        title: `Risk Run · ${portfolio.name}`,
      };
      if (selectedPricingProfileId != null) {
        requestBody.pricing_parameter_profile_id = selectedPricingProfileId;
      }
      const job = await api<ReportJobOut>('/api/reports/jobs', {
        method: 'POST',
        body: JSON.stringify(requestBody),
      });
      setFeedback(`Risk report #${job.id} queued${job.task_id ? ` as task #${job.task_id}` : ''} — view in Tasks.`);
      window.setTimeout(() => setFeedback(null), 6000);
    } catch (e) {
      setFeedback(`Could not create report: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={300} />
      </div>
    );
  }

  if (error && !metrics) {
    return <Empty message={`Could not load portfolios: ${error}`} />;
  }

  return (
        <Risk
          portfolios={portfolios}
          selectedPortfolioId={selectedId}
          pricingProfiles={pricingProfiles}
          engineConfigs={engineConfigs}
          selectedPricingProfileId={selectedPricingProfileId}
          selectedEngineConfigId={selectedEngineConfigId}
          metrics={metrics}
          metricsLoading={metricsLoading}
          running={running}
          feedback={feedback}
          onSelectPortfolio={(id) => { setSelectedId(id); onPortfolioIdChange?.(id); }}
          onSelectPricingProfile={setSelectedPricingProfileId}
          onSelectEngineConfig={setSelectedEngineConfigId}
          onRunRisk={handleRunRisk}
          onPromoteGreeks={promoteToReport}
          onPromotePnl={promoteToReport}
        onPageContextChange={onPageContextChange}
    />
  );
}

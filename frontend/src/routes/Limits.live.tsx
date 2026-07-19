import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  acknowledgeLimitIncident,
  activateRiskLimitVersion,
  api,
  assignLimitIncident,
  commentLimitIncident,
  createLimitMonitoringRun,
  createRiskLimit,
  createRiskLimitVersion,
  deactivateRiskLimit,
  errorMessage,
  getLimitEvaluation,
  getLimitIncident,
  getLimitMonitoringDashboard,
  getLimitMonitoringRun,
  getRiskLimit,
  listEngineConfigs,
  listLimitEvaluations,
  listLimitIncidents,
  listMarketSnapshots,
  listRiskLimits,
  reopenLimitIncident,
  resolveLimitIncident,
  retireRiskLimit,
  updateRiskLimitMetadata,
  waiveLimitIncident,
} from '../api/client';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type {
  EngineConfigVariant,
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
  LimitSourceInputs,
  LimitSourcePolicy,
  LimitVersionCreateInput,
  MarketSnapshot,
  PageContext,
  PageContextReporter,
  Portfolio,
  PricingParameterProfile,
  RiskLimit,
  TaskRun,
} from '../types';
import { Limits, type LimitsTab } from './Limits';

type Props = {
  onPageContextChange?: PageContextReporter;
  portfolioId?: number | null;
  onPortfolioIdChange?: (id: number) => void;
  onAskLimitManager?: () => void;
  onOpenAudit?: (auditRef: string) => void;
};

type LocationSelection = {
  tab: LimitsTab;
  runId: number | null;
  definitionId: number | null;
  incidentId: number | null;
  evaluationId: number | null;
};

const ACTIVE_TASK_STATUSES = new Set(['queued', 'running']);
const TERMINAL_TASK_STATUSES = new Set([
  'completed',
  'completed_with_errors',
  'failed',
]);
const VALID_TABS = new Set<LimitsTab>([
  'monitor',
  'definitions',
  'breaches',
  'schedules',
  'reports',
]);
const POLL_INTERVAL_MS = 1000;
const DEFAULT_MAX_SOURCE_AGE_SECONDS = 300;

export function LimitsLive({
  onPageContextChange,
  portfolioId,
  onPortfolioIdChange,
  onAskLimitManager,
  onOpenAudit,
}: Props) {
  const initialLocation = useMemo(readLocationSelection, []);
  const [activeTab, setActiveTab] = useState<LimitsTab>(initialLocation.tab);
  const [explicitRunId, setExplicitRunId] = useState<number | null>(
    initialLocation.runId,
  );
  const [selectedDefinitionId, setSelectedDefinitionId] = useState<number | null>(
    initialLocation.definitionId,
  );
  const [selectedIncidentId, setSelectedIncidentId] = useState<number | null>(
    initialLocation.incidentId,
  );
  const [selectedEvaluationId, setSelectedEvaluationId] = useState<number | null>(
    initialLocation.evaluationId,
  );
  const [focusedEvaluationId, setFocusedEvaluationId] = useState<number | null>(
    null,
  );

  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([]);
  const [engineConfigs, setEngineConfigs] = useState<EngineConfigVariant[]>([]);
  const [marketSnapshots, setMarketSnapshots] = useState<MarketSnapshot[]>([]);
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(null);
  const [selectedPricingProfileId, setSelectedPricingProfileId] = useState<number | null>(null);
  const [selectedEngineConfigId, setSelectedEngineConfigId] = useState<number | null>(null);
  const [selectedMarketSnapshotId, setSelectedMarketSnapshotId] = useState<number | null>(null);
  const [effectiveMarketEvidenceId, setEffectiveMarketEvidenceId] = useState('');
  const [valuationAsOf, setValuationAsOf] = useState(defaultValuationAsOf);
  const [sourcePolicy, setSourcePolicy] = useState<LimitSourcePolicy>('refresh_if_stale');
  const [maxSourceAgeSeconds, setMaxSourceAgeSeconds] = useState<number | null>(
    DEFAULT_MAX_SOURCE_AGE_SECONDS,
  );
  const [sourceInputsText, setSourceInputsText] = useState('{}');

  const [dashboard, setDashboard] = useState<LimitDashboard | null>(null);
  const [selectedRun, setSelectedRun] = useState<LimitMonitoringRun | null>(null);
  const [evaluations, setEvaluations] = useState<LimitEvaluation[]>([]);
  const [definitions, setDefinitions] = useState<RiskLimit[]>([]);
  const [incidents, setIncidents] = useState<LimitIncident[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<LimitIncident | null>(null);

  const [choicesLoading, setChoicesLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [mutationPending, setMutationPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mutationFeedback, setMutationFeedback] = useState<string | null>(null);
  const monitoringInFlight =
    running
    || (
      dashboard?.latest_run != null
      && ACTIVE_TASK_STATUSES.has(dashboard.latest_run.status)
    );

  const selectedPortfolioIdRef = useRef<number | null>(null);
  const selectedIncidentIdRef = useRef<number | null>(
    initialLocation.incidentId,
  );
  const selectedEvaluationIdRef = useRef<number | null>(
    initialLocation.evaluationId,
  );
  const monitorRequestIdRef = useRef(0);
  const runRequestIdRef = useRef(0);
  const breachRequestIdRef = useRef(0);
  const incidentRequestIdRef = useRef(0);
  const evaluationRequestIdRef = useRef(0);
  const workflowManagedRunIdRef = useRef<number | null>(null);
  const activeWorkflowRunIdRef = useRef<number | null>(null);
  const dashboardRef = useRef<LimitDashboard | null>(dashboard);

  useEffect(() => {
    dashboardRef.current = dashboard;
  }, [dashboard]);

  useEffect(() => {
    selectedIncidentIdRef.current = selectedIncidentId;
  }, [selectedIncidentId]);

  useEffect(() => {
    selectedEvaluationIdRef.current = selectedEvaluationId;
  }, [selectedEvaluationId]);

  useEffect(() => {
    const previousPortfolio = selectedPortfolioIdRef.current;
    if (
      previousPortfolio != null
      && selectedPortfolioId != null
      && previousPortfolio !== selectedPortfolioId
    ) {
      monitorRequestIdRef.current += 1;
      runRequestIdRef.current += 1;
      breachRequestIdRef.current += 1;
      incidentRequestIdRef.current += 1;
      evaluationRequestIdRef.current += 1;
      activeWorkflowRunIdRef.current = null;
      setRunning(false);
      setDashboard(null);
      setSelectedRun(null);
      setEvaluations([]);
      setFocusedEvaluationId(null);
      setIncidents([]);
      setSelectedIncident(null);
      setError(null);
    }
    selectedPortfolioIdRef.current = selectedPortfolioId;
  }, [selectedPortfolioId]);

  useEffect(() => () => {
    selectedPortfolioIdRef.current = null;
    selectedIncidentIdRef.current = null;
    selectedEvaluationIdRef.current = null;
    activeWorkflowRunIdRef.current = null;
    monitorRequestIdRef.current += 1;
    runRequestIdRef.current += 1;
    breachRequestIdRef.current += 1;
    incidentRequestIdRef.current += 1;
    evaluationRequestIdRef.current += 1;
  }, []);

  useEffect(() => {
    const cancelled = { current: false };
    void (async () => {
      setChoicesLoading(true);
      try {
        const [portfolioResult, profileResult, engineResult, snapshotResult] =
          await Promise.allSettled([
            api<Portfolio[]>('/api/portfolios'),
            api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
            listEngineConfigs(),
            listMarketSnapshots({ limit: 200 }),
          ]);
        if (cancelled.current) return;
        if (portfolioResult.status === 'rejected') {
          throw portfolioResult.reason;
        }
        const nextPortfolios = Array.isArray(portfolioResult.value)
          ? portfolioResult.value
          : [];
        const nextProfiles =
          profileResult.status === 'fulfilled' && Array.isArray(profileResult.value)
            ? profileResult.value
            : [];
        const nextEngines =
          engineResult.status === 'fulfilled' && Array.isArray(engineResult.value)
            ? engineResult.value
            : [];
        const nextSnapshots =
          snapshotResult.status === 'fulfilled' && Array.isArray(snapshotResult.value)
            ? snapshotResult.value
            : [];

        setPortfolios(nextPortfolios);
        setPricingProfiles(nextProfiles);
        setEngineConfigs(nextEngines);
        setMarketSnapshots(nextSnapshots);
        setSelectedEngineConfigId(
          nextEngines.find((item) => item.is_default)?.id
            ?? nextEngines[0]?.id
            ?? null,
        );
        setSelectedMarketSnapshotId(nextSnapshots[0]?.id ?? null);

        const preferred =
          portfolioId != null
          && nextPortfolios.some((item) => item.id === portfolioId)
            ? portfolioId
            : nextPortfolios[0]?.id ?? null;
        setSelectedPortfolioId(preferred);
      } catch (reason) {
        if (!cancelled.current) setError(errorMessage(reason));
      } finally {
        if (!cancelled.current) setChoicesLoading(false);
      }
    })();
    return () => {
      cancelled.current = true;
    };
    // Mount-time preference selection follows RiskLive. Later prop changes are
    // reconciled by the dedicated effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (
      portfolioId != null
      && portfolioId !== selectedPortfolioId
      && portfolios.some((item) => item.id === portfolioId)
    ) {
      setSelectedPortfolioId(portfolioId);
    }
  }, [portfolioId, portfolios, selectedPortfolioId]);

  useEffect(() => {
    const onPopState = () => {
      const selection = readLocationSelection();
      monitorRequestIdRef.current += 1;
      incidentRequestIdRef.current += 1;
      evaluationRequestIdRef.current += 1;
      workflowManagedRunIdRef.current = null;
      setActiveTab(selection.tab);
      setExplicitRunId(selection.runId);
      setSelectedRun(null);
      setEvaluations([]);
      setFocusedEvaluationId(null);
      setSelectedDefinitionId(selection.definitionId);
      selectedIncidentIdRef.current = selection.incidentId;
      setSelectedIncidentId(selection.incidentId);
      setSelectedIncident(null);
      selectedEvaluationIdRef.current = selection.evaluationId;
      setSelectedEvaluationId(selection.evaluationId);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const replaceDefinition = useCallback((definition: RiskLimit) => {
    setDefinitions((current) => {
      const found = current.some((item) => item.id === definition.id);
      return found
        ? current.map((item) => item.id === definition.id ? definition : item)
        : [definition, ...current];
    });
  }, []);

  const replaceIncident = useCallback((incident: LimitIncident) => {
    setIncidents((current) => {
      const found = current.some((item) => item.id === incident.id);
      return found
        ? current.map((item) => item.id === incident.id ? incident : item)
        : [incident, ...current];
    });
  }, []);

  const loadMonitor = useCallback(async (
    portfolio: number,
    requestedRunId: number | null,
    options: { preserve?: boolean } = {},
  ) => {
    const requestId = ++monitorRequestIdRef.current;
    if (!options.preserve) setContentLoading(true);
    setError(null);
    try {
      const [nextDashboard, incidentPage] = await Promise.all([
        getLimitMonitoringDashboard(portfolio, 20),
        listLimitIncidents({ portfolio_id: portfolio, limit: 200 }),
      ]);
      if (
        requestId !== monitorRequestIdRef.current
        || selectedPortfolioIdRef.current !== portfolio
      ) return;

      let nextRun: LimitMonitoringRun | null;
      let nextEvaluations: LimitEvaluation[];
      if (requestedRunId != null) {
        const [run, evaluationPage] = await Promise.all([
          getLimitMonitoringRun(requestedRunId, portfolio),
          listAllLimitEvaluations(requestedRunId, portfolio),
        ]);
        if (
          requestId !== monitorRequestIdRef.current
          || selectedPortfolioIdRef.current !== portfolio
        ) return;
        nextRun = run;
        nextEvaluations = evaluationPage;
      } else {
        const currentEvaluations = nextDashboard.current_evaluations;
        const currentRunId = currentEvaluations[0]?.monitoring_run_id ?? null;
        if (
          currentRunId != null
          && currentEvaluations.some(
            (evaluation) => evaluation.monitoring_run_id !== currentRunId,
          )
        ) {
          throw new Error('Dashboard returned evaluations from multiple monitoring runs.');
        }
        if (
          currentRunId != null
          && nextDashboard.current_evidence_run?.id !== currentRunId
        ) {
          throw new Error(
            'Dashboard evidence run does not match its current evaluations.',
          );
        }
        nextRun = nextDashboard.current_evidence_run;
        if (
          requestId !== monitorRequestIdRef.current
          || selectedPortfolioIdRef.current !== portfolio
        ) return;
        nextEvaluations = currentEvaluations;
      }
      setDashboard(nextDashboard);
      setIncidents(incidentPage.items);
      setSelectedRun(nextRun);
      setEvaluations(nextEvaluations);
    } catch (reason) {
      if (
        requestId === monitorRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setError(errorMessage(reason));
      }
    } finally {
      if (
        requestId === monitorRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setContentLoading(false);
      }
    }
  }, []);

  const loadDefinitions = useCallback(async () => {
    setContentLoading(true);
    setError(null);
    try {
      const page = await listRiskLimits({ limit: 200 });
      setDefinitions(page.items);
      const requestedId = readPositiveInteger(
        new URLSearchParams(window.location.search).get('limit'),
      );
      const nextId =
        requestedId != null && page.items.some((item) => item.id === requestedId)
          ? requestedId
          : page.items[0]?.id ?? null;
      setSelectedDefinitionId(nextId);
      if (nextId != null) {
        const detail = await getRiskLimit(nextId);
        replaceDefinition(detail);
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setContentLoading(false);
    }
  }, [replaceDefinition]);

  const loadBreaches = useCallback(async (
    portfolio: number,
    requestedIncidentId: number | null,
  ) => {
    const requestId = ++breachRequestIdRef.current;
    setContentLoading(true);
    setError(null);
    try {
      const page = await listLimitIncidents({
        portfolio_id: portfolio,
        limit: 200,
      });
      if (
        requestId !== breachRequestIdRef.current
        || selectedPortfolioIdRef.current !== portfolio
      ) return;
      setIncidents(page.items);
      const nextId =
        requestedIncidentId != null
          ? requestedIncidentId
          : page.items[0]?.id ?? null;
      selectedIncidentIdRef.current = nextId;
      setSelectedIncidentId(nextId);
      if (nextId == null) {
        setSelectedIncident(null);
      }
    } catch (reason) {
      if (
        requestId === breachRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setError(errorMessage(reason));
      }
    } finally {
      if (
        requestId === breachRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setContentLoading(false);
      }
    }
  }, []);

  const loadIncidentDetail = useCallback(async (
    portfolio: number,
    incidentId: number,
  ) => {
    const requestId = ++incidentRequestIdRef.current;
    setSelectedIncident(null);
    try {
      const detail = await getLimitIncident(incidentId, portfolio);
      if (
        requestId !== incidentRequestIdRef.current
        || selectedPortfolioIdRef.current !== portfolio
        || selectedIncidentIdRef.current !== incidentId
        || detail.id !== incidentId
      ) return;
      replaceIncident(detail);
      setSelectedIncident(detail);
    } catch (reason) {
      if (
        requestId === incidentRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setError(errorMessage(reason));
      }
    }
  }, [replaceIncident]);

  useEffect(() => {
    if (selectedPortfolioId == null || choicesLoading) {
      if (!choicesLoading) setContentLoading(false);
      return;
    }
    if (activeTab === 'monitor') {
      if (
        explicitRunId != null
        && workflowManagedRunIdRef.current === explicitRunId
      ) {
        workflowManagedRunIdRef.current = null;
        return;
      }
      void loadMonitor(selectedPortfolioId, explicitRunId);
      return;
    }
    if (activeTab === 'definitions') {
      void loadDefinitions();
      return;
    }
    if (activeTab === 'breaches') {
      void loadBreaches(selectedPortfolioId, selectedIncidentId);
      return;
    }
    setContentLoading(false);
  }, [
    activeTab,
    choicesLoading,
    explicitRunId,
    loadBreaches,
    loadDefinitions,
    loadMonitor,
    selectedIncidentId,
    selectedPortfolioId,
  ]);

  useEffect(() => {
    if (
      activeTab !== 'breaches'
      || selectedPortfolioId == null
      || selectedIncidentId == null
    ) {
      if (selectedIncidentId == null) setSelectedIncident(null);
      return;
    }
    void loadIncidentDetail(selectedPortfolioId, selectedIncidentId);
  }, [
    activeTab,
    loadIncidentDetail,
    selectedIncidentId,
    selectedPortfolioId,
  ]);

  useEffect(() => {
    if (
      selectedEvaluationId == null
      || selectedPortfolioId == null
      || activeTab !== 'monitor'
    ) {
      evaluationRequestIdRef.current += 1;
      return;
    }
    const requestId = ++evaluationRequestIdRef.current;
    const portfolio = selectedPortfolioId;
    const evaluationId = selectedEvaluationId;
    void (async () => {
      try {
        const evaluation = await getLimitEvaluation(
          evaluationId,
          portfolio,
        );
        if (
          requestId !== evaluationRequestIdRef.current
          || selectedPortfolioIdRef.current !== portfolio
          || selectedEvaluationIdRef.current !== evaluationId
        ) return;
        setExplicitRunId(evaluation.monitoring_run_id);
        if (readLocationSelection().runId !== evaluation.monitoring_run_id) {
          updateLocation({
            tab: 'monitor',
            run: evaluation.monitoring_run_id,
            evaluation: evaluation.id,
          }, false);
        }
      } catch (reason) {
        if (
          requestId === evaluationRequestIdRef.current
          && selectedPortfolioIdRef.current === portfolio
        ) {
          setError(errorMessage(reason));
        }
      }
    })();
  }, [
    activeTab,
    selectedEvaluationId,
    selectedPortfolioId,
  ]);

  const handleTabChange = (tab: LimitsTab) => {
    evaluationRequestIdRef.current += 1;
    if (tab !== activeTab) {
      workflowManagedRunIdRef.current = null;
      setFocusedEvaluationId(null);
      setMutationFeedback(null);
    }
    setActiveTab(tab);
    updateLocation({ tab }, true);
  };

  const handlePortfolioChange = (id: number) => {
    runRequestIdRef.current += 1;
    monitorRequestIdRef.current += 1;
    breachRequestIdRef.current += 1;
    incidentRequestIdRef.current += 1;
    evaluationRequestIdRef.current += 1;
    setRunning(false);
    setDashboard(null);
    setSelectedRun(null);
    setEvaluations([]);
    setFocusedEvaluationId(null);
    setIncidents([]);
    setSelectedPortfolioId(id);
    setExplicitRunId(null);
    workflowManagedRunIdRef.current = null;
    activeWorkflowRunIdRef.current = null;
    selectedEvaluationIdRef.current = null;
    setSelectedEvaluationId(null);
    selectedIncidentIdRef.current = null;
    setSelectedIncidentId(null);
    setSelectedIncident(null);
    updateLocation({
      portfolio: id,
      run: null,
      evaluation: null,
      incident: null,
    }, false);
    onPortfolioIdChange?.(id);
  };

  const handleSelectDefinition = async (definitionId: number) => {
    evaluationRequestIdRef.current += 1;
    setSelectedDefinitionId(definitionId);
    updateLocation({ limit: definitionId }, true);
    try {
      const detail = await getRiskLimit(definitionId);
      replaceDefinition(detail);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const handleSelectIncident = async (incidentId: number) => {
    evaluationRequestIdRef.current += 1;
    incidentRequestIdRef.current += 1;
    selectedIncidentIdRef.current = incidentId;
    setSelectedIncidentId(incidentId);
    setSelectedIncident(null);
    updateLocation({ incident: incidentId }, true);
  };

  const refreshCompletedRun = async (
    finalRun: LimitMonitoringRun,
    portfolio: number,
    requestId: number,
  ): Promise<string | null> => {
    if (
      requestId !== runRequestIdRef.current
      || selectedPortfolioIdRef.current !== portfolio
    ) return null;
    setDashboard((current) => (
      current?.latest_run?.id === finalRun.id
        ? { ...current, latest_run: finalRun }
        : current
    ));
    const exactRunView = isExactMonitoringRunView(finalRun.id);
    const implicitView = isImplicitMonitoringView();
    if (exactRunView || implicitView) {
      monitorRequestIdRef.current += 1;
    }
    if (exactRunView) {
      setSelectedRun(finalRun);
      setEvaluations([]);
      setContentLoading(false);
    } else if (implicitView) {
      setSelectedRun(dashboardRef.current?.current_evidence_run ?? null);
      setEvaluations(dashboardRef.current?.current_evaluations ?? []);
      setContentLoading(false);
    }

    const [dashboardResult, evaluationsResult, incidentsResult] =
      await Promise.allSettled([
        getLimitMonitoringDashboard(portfolio, 20),
        listAllLimitEvaluations(finalRun.id, portfolio),
        listLimitIncidents({ portfolio_id: portfolio, limit: 200 }),
      ]);
    if (
      requestId !== runRequestIdRef.current
      || selectedPortfolioIdRef.current !== portfolio
    ) return null;
    if (dashboardResult.status === 'fulfilled') {
      setDashboard(dashboardResult.value);
      if (isImplicitMonitoringView()) {
        setSelectedRun(dashboardResult.value.current_evidence_run);
        setEvaluations(dashboardResult.value.current_evaluations);
        setContentLoading(false);
      }
    }
    if (
      evaluationsResult.status === 'fulfilled'
      && isExactMonitoringRunView(finalRun.id)
    ) {
      setSelectedRun(finalRun);
      setEvaluations(evaluationsResult.value);
    }
    if (incidentsResult.status === 'fulfilled') {
      setIncidents(incidentsResult.value.items);
    }
    const failures = [
      dashboardResult,
      evaluationsResult,
      incidentsResult,
    ].flatMap((result) => (
      result.status === 'rejected' ? [errorMessage(result.reason)] : []
    ));
    return failures.length
      ? `Supporting evidence refresh was partial: ${failures.join('; ')}`
      : null;
  };

  const handleRunNow = async () => {
    if (
      monitoringInFlight
      || selectedPortfolioId == null
      || (selectedMarketSnapshotId == null && !effectiveMarketEvidenceId.trim())
    ) return;
    const requestId = ++runRequestIdRef.current;
    const portfolio = selectedPortfolioId;
    let workflowRunId: number | null = null;
    setRunning(true);
    setError(null);
    setMutationFeedback(null);

    try {
      const sourceInputs = parseSourceInputs(sourceInputsText);
      if (
        sourceInputs.backtest
        && !effectiveMarketEvidenceId.trim()
      ) {
        throw new Error(
          'Backtest monitoring requires an effective market evidence id.',
        );
      }
      const body: LimitMonitoringRunCreateInput = {
        portfolio_id: portfolio,
        valuation_as_of: localDateTimeToIso(valuationAsOf),
        source_policy: sourcePolicy,
        max_source_age_seconds: maxSourceAgeSeconds,
        source_inputs: sourceInputs,
      };
      if (selectedPricingProfileId != null) {
        body.pricing_parameter_profile_id = selectedPricingProfileId;
      }
      if (selectedEngineConfigId != null) {
        body.engine_config_id = selectedEngineConfigId;
      }
      if (selectedMarketSnapshotId != null) {
        body.market_snapshot_id = selectedMarketSnapshotId;
      }
      if (effectiveMarketEvidenceId.trim()) {
        body.effective_market_evidence_id = effectiveMarketEvidenceId.trim();
      }

      const queued = await createLimitMonitoringRun(body);
      if (
        requestId !== runRequestIdRef.current
        || selectedPortfolioIdRef.current !== portfolio
      ) return;
      setSelectedRun(queued);
      setExplicitRunId(queued.id);
      workflowManagedRunIdRef.current = queued.id;
      activeWorkflowRunIdRef.current = queued.id;
      workflowRunId = queued.id;
      setEvaluations([]);
      setFocusedEvaluationId(null);
      selectedEvaluationIdRef.current = null;
      setSelectedEvaluationId(null);
      updateLocation({ run: queued.id, evaluation: null }, false);
      setMutationFeedback(
        queued.task_id == null
          ? `Run #${queued.id} accepted.`
          : `Run #${queued.id} queued as task #${queued.task_id}.`,
      );
      if (queued.task_id == null || !ACTIVE_TASK_STATUSES.has(queued.status)) {
        const finalRun = await getLimitMonitoringRun(queued.id, portfolio);
        const refreshWarning = await refreshCompletedRun(
          finalRun,
          portfolio,
          requestId,
        );
        if (
          refreshWarning
          && requestId === runRequestIdRef.current
          && selectedPortfolioIdRef.current === portfolio
        ) {
          setMutationFeedback(refreshWarning);
        }
        return;
      }
      await pollMonitoringTask(
        queued.task_id,
        queued.id,
        portfolio,
        requestId,
      );
    } catch (reason) {
      if (
        requestId === runRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setError(errorMessage(reason));
      }
    } finally {
      if (
        requestId === runRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setRunning(false);
        if (activeWorkflowRunIdRef.current === workflowRunId) {
          activeWorkflowRunIdRef.current = null;
        }
      }
    }
  };

  const pollMonitoringTask = async (
    taskId: number,
    runId: number,
    portfolio: number,
    requestId: number,
  ) => {
    while (
      requestId === runRequestIdRef.current
      && selectedPortfolioIdRef.current === portfolio
    ) {
      try {
        const task = await api<TaskRun>(`/api/tasks/${taskId}`);
        if (
          requestId !== runRequestIdRef.current
          || selectedPortfolioIdRef.current !== portfolio
        ) return;
        setMutationFeedback(taskMessage(task));
        if (TERMINAL_TASK_STATUSES.has(task.status)) {
          const finalRun = await getLimitMonitoringRun(runId, portfolio);
          if (
            requestId !== runRequestIdRef.current
            || selectedPortfolioIdRef.current !== portfolio
          ) return;
          const refreshWarning = await refreshCompletedRun(
            finalRun,
            portfolio,
            requestId,
          );
          if (
            requestId !== runRequestIdRef.current
            || selectedPortfolioIdRef.current !== portfolio
          ) return;
          if (task.status === 'failed') {
            setError(task.error || 'Limit monitoring failed.');
            if (refreshWarning) setMutationFeedback(refreshWarning);
            return;
          }
          const completedMessage =
            finalRun.status === 'completed_with_unknowns'
              ? `Run #${runId} completed with unknowns — review incomplete evidence.`
              : `Run #${runId} ${humanize(finalRun.status)}.`;
          setMutationFeedback(
            refreshWarning
              ? `${completedMessage} ${refreshWarning}`
              : completedMessage,
          );
          return;
        }
      } catch (reason) {
        if (
          requestId !== runRequestIdRef.current
          || selectedPortfolioIdRef.current !== portfolio
        ) return;
        setMutationFeedback(
          `Task #${taskId} status unavailable; retrying: ${errorMessage(reason)}`,
        );
      }
      await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
    }
  };

  useEffect(() => {
    const latestRun = dashboard?.latest_run;
    const portfolio = selectedPortfolioId;
    const taskId = latestRun?.task_id;
    if (
      latestRun == null
      || portfolio == null
      || taskId == null
      || !ACTIVE_TASK_STATUSES.has(latestRun.status)
      || running
      || activeWorkflowRunIdRef.current != null
    ) return;

    const requestId = ++runRequestIdRef.current;
    activeWorkflowRunIdRef.current = latestRun.id;
    setRunning(true);
    void (async () => {
      try {
        await pollMonitoringTask(
          taskId,
          latestRun.id,
          portfolio,
          requestId,
        );
      } catch (reason) {
        if (
          requestId === runRequestIdRef.current
          && selectedPortfolioIdRef.current === portfolio
        ) {
          setError(errorMessage(reason));
        }
      } finally {
        if (
          requestId === runRequestIdRef.current
          && selectedPortfolioIdRef.current === portfolio
        ) {
          setRunning(false);
          if (activeWorkflowRunIdRef.current === latestRun.id) {
            activeWorkflowRunIdRef.current = null;
          }
        }
      }
    })();
    // Poll resumption is keyed by immutable task identity, not callback identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    dashboard?.latest_run?.id,
    dashboard?.latest_run?.status,
    dashboard?.latest_run?.task_id,
    running,
    selectedPortfolioId,
  ]);

  const refreshDefinitionAfterConflict = async (
    definitionId: number,
    reason: unknown,
  ) => {
    const message = errorMessage(reason);
    setMutationFeedback(message);
    if (!isConcurrencyConflict(message)) return;
    try {
      replaceDefinition(await getRiskLimit(definitionId));
    } catch (refreshReason) {
      setMutationFeedback(
        `${message} Refresh failed: ${errorMessage(refreshReason)}`,
      );
    }
  };

  const runDefinitionMutation = async (
    definitionId: number,
    operation: () => Promise<RiskLimit>,
    success: string,
  ) => {
    setMutationPending(true);
    setMutationFeedback(null);
    try {
      const result = await operation();
      replaceDefinition(result);
      setSelectedDefinitionId((current) => (
        current === definitionId ? result.id : current
      ));
      setMutationFeedback(success);
      return true;
    } catch (reason) {
      await refreshDefinitionAfterConflict(definitionId, reason);
      return false;
    } finally {
      setMutationPending(false);
    }
  };

  const handleCreateDefinition = async (body: LimitCreateInput) => {
    setMutationPending(true);
    setMutationFeedback(null);
    try {
      const result = await createRiskLimit(body);
      replaceDefinition(result);
      setSelectedDefinitionId(result.id);
      updateLocation({ limit: result.id }, false);
      setMutationFeedback(`Created draft limit ${result.name}.`);
      return true;
    } catch (reason) {
      setMutationFeedback(errorMessage(reason));
      return false;
    } finally {
      setMutationPending(false);
    }
  };

  const handleUpdateDefinition = (
    definitionId: number,
    body: LimitMetadataPatchInput,
  ) => runDefinitionMutation(
    definitionId,
    () => updateRiskLimitMetadata(definitionId, body),
    'Limit metadata updated.',
  );

  const handleCreateVersion = (
    definitionId: number,
    body: LimitVersionCreateInput,
  ) => runDefinitionMutation(
    definitionId,
    () => createRiskLimitVersion(definitionId, body),
    'Draft version saved. Activation remains a separate action.',
  );

  const handleActivateVersion = (
    definitionId: number,
    versionId: number,
    body: LimitActionInput,
  ) => runDefinitionMutation(
    definitionId,
    () => activateRiskLimitVersion(definitionId, versionId, body),
    'Limit version activated.',
  );

  const handleDeactivateDefinition = (
    definitionId: number,
    body: LimitActionInput,
  ) => runDefinitionMutation(
    definitionId,
    () => deactivateRiskLimit(definitionId, body),
    'Limit deactivated.',
  );

  const handleRetireDefinition = (
    definitionId: number,
    body: LimitActionInput,
  ) => runDefinitionMutation(
    definitionId,
    () => retireRiskLimit(definitionId, body),
    'Limit retired.',
  );

  const refreshIncidentAfterConflict = async (
    incidentId: number,
    portfolio: number,
    reason: unknown,
  ) => {
    const message = errorMessage(reason);
    setMutationFeedback(message);
    if (
      !isConcurrencyConflict(message)
      || selectedPortfolioIdRef.current !== portfolio
    ) return;
    try {
      const fresh = await getLimitIncident(incidentId, portfolio);
      if (selectedPortfolioIdRef.current !== portfolio) return;
      replaceIncident(fresh);
      if (selectedIncidentIdRef.current === incidentId) {
        setSelectedIncident(fresh);
      }
    } catch (refreshReason) {
      setMutationFeedback(
        `${message} Refresh failed: ${errorMessage(refreshReason)}`,
      );
    }
  };

  const runIncidentMutation = async (
    incidentId: number,
    portfolio: number,
    operation: () => Promise<LimitIncident>,
    success: string,
  ) => {
    setMutationPending(true);
    setMutationFeedback(null);
    try {
      const result = await operation();
      if (selectedPortfolioIdRef.current !== portfolio) return true;
      replaceIncident(result);
      if (selectedIncidentIdRef.current === incidentId) {
        setSelectedIncident(result);
      }
      setMutationFeedback(success);
      return true;
    } catch (reason) {
      await refreshIncidentAfterConflict(incidentId, portfolio, reason);
      return false;
    } finally {
      setMutationPending(false);
    }
  };

  const requireIncidentPortfolio = () => {
    if (selectedPortfolioId == null) {
      throw new Error('Select a portfolio before changing an incident.');
    }
    return selectedPortfolioId;
  };

  const handleAcknowledgeIncident = (
    incidentId: number,
    body: LimitActionInput,
  ) => {
    const portfolio = requireIncidentPortfolio();
    return runIncidentMutation(
      incidentId,
      portfolio,
      () => acknowledgeLimitIncident(incidentId, portfolio, body),
      'Acknowledgement recorded.',
    );
  };

  const handleAssignIncident = (
    incidentId: number,
    body: LimitIncidentAssignInput,
  ) => {
    const portfolio = requireIncidentPortfolio();
    return runIncidentMutation(
      incidentId,
      portfolio,
      () => assignLimitIncident(incidentId, portfolio, body),
      'Ownership updated.',
    );
  };

  const handleCommentIncident = (
    incidentId: number,
    body: LimitIncidentCommentInput,
  ) => {
    const portfolio = requireIncidentPortfolio();
    return runIncidentMutation(
      incidentId,
      portfolio,
      () => commentLimitIncident(incidentId, portfolio, body),
      'Comment added.',
    );
  };

  const handleWaiveIncident = (
    incidentId: number,
    body: LimitIncidentWaiveInput,
  ) => {
    const portfolio = requireIncidentPortfolio();
    return runIncidentMutation(
      incidentId,
      portfolio,
      () => waiveLimitIncident(incidentId, portfolio, body),
      'Temporary exception recorded.',
    );
  };

  const handleResolveIncident = (
    incidentId: number,
    body: LimitActionInput,
  ) => {
    const portfolio = requireIncidentPortfolio();
    return runIncidentMutation(
      incidentId,
      portfolio,
      () => resolveLimitIncident(incidentId, portfolio, body),
      'Lifecycle updated.',
    );
  };

  const handleReopenIncident = (
    incidentId: number,
    body: LimitActionInput,
  ) => {
    const portfolio = requireIncidentPortfolio();
    return runIncidentMutation(
      incidentId,
      portfolio,
      () => reopenLimitIncident(incidentId, portfolio, body),
      'Lifecycle updated.',
    );
  };

  const handleOpenEvaluation = async (evaluationId: number) => {
    if (selectedPortfolioId == null) return;
    workflowManagedRunIdRef.current = null;
    const requestId = ++evaluationRequestIdRef.current;
    const portfolio = selectedPortfolioId;
    setError(null);
    setMutationFeedback(null);
    try {
      const evaluation = await getLimitEvaluation(evaluationId, portfolio);
      if (
        requestId !== evaluationRequestIdRef.current
        || selectedPortfolioIdRef.current !== portfolio
      ) return;
      setActiveTab('monitor');
      selectedIncidentIdRef.current = null;
      setSelectedIncidentId(null);
      setSelectedIncident(null);
      setSelectedRun(null);
      setEvaluations([]);
      setFocusedEvaluationId(null);
      setExplicitRunId(evaluation.monitoring_run_id);
      workflowManagedRunIdRef.current = null;
      selectedEvaluationIdRef.current = evaluation.id;
      setSelectedEvaluationId(evaluation.id);
      updateLocation({
        tab: 'monitor',
        run: evaluation.monitoring_run_id,
        evaluation: evaluation.id,
        incident: null,
      }, true);
    } catch (reason) {
      if (
        requestId === evaluationRequestIdRef.current
        && selectedPortfolioIdRef.current === portfolio
      ) {
        setError(errorMessage(reason));
      }
    }
  };

  const handleCloseEvaluation = () => {
    evaluationRequestIdRef.current += 1;
    selectedEvaluationIdRef.current = null;
    setFocusedEvaluationId(null);
    setSelectedEvaluationId(null);
    updateLocation({ evaluation: null }, false);
  };

  const selectedDefinition =
    definitions.find((item) => item.id === selectedDefinitionId) ?? null;
  const selectedPortfolio =
    portfolios.find((item) => item.id === selectedPortfolioId) ?? null;
  const chips = [
    selectedPortfolio?.name,
    activeTab === 'monitor' && selectedRun ? `Run #${selectedRun.id}` : null,
    activeTab === 'definitions' ? selectedDefinition?.name : null,
    activeTab === 'breaches' && selectedIncident
      ? `Incident #${selectedIncident.id}`
      : null,
  ].filter((value): value is string => Boolean(value));
  const pageContext = useMemo((): PageContext => ({
    route: 'limits',
    title: 'Limits',
    path: '/limits',
    entity_ids: {
      portfolio_id: selectedPortfolioId,
      limit_id: activeTab === 'definitions' ? selectedDefinitionId : null,
      monitoring_run_id:
        activeTab === 'monitor' ? selectedRun?.id ?? null : null,
      evaluation_id:
        activeTab === 'monitor'
          ? focusedEvaluationId ?? selectedEvaluationId
          : null,
      incident_id: activeTab === 'breaches' ? selectedIncidentId : null,
    },
    snapshot: {
      active_tab: activeTab,
      portfolio: selectedPortfolio
        ? { id: selectedPortfolio.id, name: selectedPortfolio.name }
        : null,
      dashboard_summary:
        activeTab !== 'monitor'
          ? null
          : explicitRunId != null
            ? selectedRun?.summary ?? null
            : dashboard?.summary ?? null,
      selected_run: activeTab === 'monitor' && selectedRun
        ? {
            id: selectedRun.id,
            status: selectedRun.status,
            valuation_as_of: selectedRun.valuation_as_of,
            mode: selectedRun.mode,
            source_policy: selectedRun.source_policy,
            source_references: selectedRun.source_references.slice(0, 12),
          }
        : null,
      evaluations: activeTab === 'monitor'
        ? evaluations.slice(0, 20).map((item) => ({
            id: item.id,
            limit_version_id: item.limit_version_id,
            scope_label: item.scope_label,
            status: item.status,
            observed_value: item.observed_value,
            utilization: item.utilization,
            headroom: item.headroom,
            reason_code: item.reason_code,
          }))
        : [],
      selected_definition: activeTab === 'definitions' && selectedDefinition
        ? {
            id: selectedDefinition.id,
            key: selectedDefinition.key,
            name: selectedDefinition.name,
            row_version: selectedDefinition.row_version,
            active_version_id: selectedDefinition.active_version_id,
          }
        : null,
      selected_incident: activeTab === 'breaches' && selectedIncident
        ? {
            id: selectedIncident.id,
            severity: selectedIncident.severity,
            status: selectedIncident.status,
            scope_label: selectedIncident.scope_label,
            assignee: selectedIncident.assignee,
            row_version: selectedIncident.row_version,
          }
        : null,
    },
    loaded_context: {
      completeness: contentLoading ? 'partial' : 'complete',
      visible_count:
        activeTab === 'monitor'
          ? evaluations.length
          : activeTab === 'definitions'
            ? definitions.length
            : activeTab === 'breaches'
              ? incidents.length
              : 0,
      total_count:
        activeTab === 'monitor'
          ? evaluations.length
          : activeTab === 'definitions'
            ? definitions.length
            : activeTab === 'breaches'
              ? incidents.length
              : 0,
    },
    actions: declareActions([
      {
        name: 'run_limit_monitoring',
        required_ids: ['portfolio_id'],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/limit-monitoring/runs',
      },
      {
        name: 'read_limit_monitoring_dashboard',
        required_ids: ['portfolio_id'],
        confirmation: 'implicit',
        backend_endpoint: 'GET /api/limit-monitoring/dashboard',
      },
      {
        name: 'manage_limit_definition',
        required_ids: ['limit_id'],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/limits/{limit_id}/versions',
      },
      {
        name: 'manage_limit_incident',
        required_ids: ['incident_id', 'portfolio_id'],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/limit-incidents/{incident_id}/{action}',
      },
    ]),
    chips,
  }), [
    activeTab,
    chips,
    contentLoading,
    dashboard,
    definitions.length,
    evaluations,
    explicitRunId,
    focusedEvaluationId,
    incidents.length,
    selectedDefinition,
    selectedDefinitionId,
    selectedIncident,
    selectedIncidentId,
    selectedEvaluationId,
    selectedPortfolio,
    selectedPortfolioId,
    selectedRun,
  ]);
  usePageContextReporter(pageContext, onPageContextChange);

  return (
    <Limits
      activeTab={activeTab}
      onTabChange={handleTabChange}
      portfolios={portfolios}
      pricingProfiles={pricingProfiles}
      engineConfigs={engineConfigs}
      marketSnapshots={marketSnapshots}
      selectedPortfolioId={selectedPortfolioId}
      selectedPricingProfileId={selectedPricingProfileId}
      selectedEngineConfigId={selectedEngineConfigId}
      selectedMarketSnapshotId={selectedMarketSnapshotId}
      effectiveMarketEvidenceId={effectiveMarketEvidenceId}
      valuationAsOf={valuationAsOf}
      sourcePolicy={sourcePolicy}
      maxSourceAgeSeconds={maxSourceAgeSeconds}
      sourceInputsText={sourceInputsText}
      onSelectPortfolio={handlePortfolioChange}
      onSelectPricingProfile={setSelectedPricingProfileId}
      onSelectEngineConfig={setSelectedEngineConfigId}
      onSelectMarketSnapshot={setSelectedMarketSnapshotId}
      onEffectiveMarketEvidenceChange={setEffectiveMarketEvidenceId}
      onValuationAsOfChange={setValuationAsOf}
      onSourcePolicyChange={setSourcePolicy}
      onMaxSourceAgeSecondsChange={setMaxSourceAgeSeconds}
      onSourceInputsTextChange={setSourceInputsText}
      dashboard={dashboard}
      selectedRun={selectedRun}
      selectedRunExplicit={explicitRunId != null}
      selectedEvaluationId={selectedEvaluationId}
      onCloseEvaluation={handleCloseEvaluation}
      onEvidenceFocus={setFocusedEvaluationId}
      evaluations={evaluations}
      definitions={definitions}
      selectedDefinitionId={selectedDefinitionId}
      onSelectDefinition={handleSelectDefinition}
      incidents={incidents}
      selectedIncidentId={selectedIncidentId}
      selectedIncident={selectedIncident}
      onSelectIncident={handleSelectIncident}
      loading={choicesLoading || contentLoading}
      running={monitoringInFlight}
      mutationPending={mutationPending}
      error={error}
      mutationFeedback={mutationFeedback}
      onRunNow={handleRunNow}
      onAskLimitManager={onAskLimitManager}
      onCreateDefinition={handleCreateDefinition}
      onUpdateDefinition={handleUpdateDefinition}
      onCreateDefinitionVersion={handleCreateVersion}
      onActivateDefinitionVersion={handleActivateVersion}
      onDeactivateDefinition={handleDeactivateDefinition}
      onRetireDefinition={handleRetireDefinition}
      onAcknowledgeIncident={handleAcknowledgeIncident}
      onAssignIncident={handleAssignIncident}
      onCommentIncident={handleCommentIncident}
      onWaiveIncident={handleWaiveIncident}
      onResolveIncident={handleResolveIncident}
      onReopenIncident={handleReopenIncident}
      onOpenEvaluation={handleOpenEvaluation}
      onOpenAudit={onOpenAudit}
    />
  );
}

function readLocationSelection(): LocationSelection {
  const search = new URLSearchParams(window.location.search);
  const requestedTab = search.get('tab') as LimitsTab | null;
  return {
    tab:
      requestedTab != null && VALID_TABS.has(requestedTab)
        ? requestedTab
        : 'monitor',
    runId: readPositiveInteger(search.get('run')),
    definitionId: readPositiveInteger(search.get('limit')),
    incidentId: readPositiveInteger(search.get('incident')),
    evaluationId: readPositiveInteger(search.get('evaluation')),
  };
}

function isExactMonitoringRunView(runId: number): boolean {
  const selection = readLocationSelection();
  return (
    selection.tab === 'monitor'
    && selection.runId === runId
  );
}

function isImplicitMonitoringView(): boolean {
  const selection = readLocationSelection();
  return selection.tab === 'monitor' && selection.runId == null;
}

function readPositiveInteger(value: string | null): number | null {
  if (value == null || !/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function updateLocation(
  patch: {
    tab?: LimitsTab;
    portfolio?: number | null;
    run?: number | null;
    incident?: number | null;
    limit?: number | null;
    evaluation?: number | null;
  },
  push: boolean,
) {
  const search = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(patch)) {
    if (value == null) search.delete(key);
    else search.set(key, String(value));
  }
  const query = search.toString();
  const target = `/limits${query ? `?${query}` : ''}`;
  if (window.location.pathname + window.location.search === target) return;
  if (push) window.history.pushState(null, '', target);
  else window.history.replaceState(null, '', target);
}

function defaultValuationAsOf(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 16);
}

async function listAllLimitEvaluations(
  runId: number,
  portfolioId: number,
): Promise<LimitEvaluation[]> {
  const pageSize = 200;
  const items: LimitEvaluation[] = [];
  let total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const page = await listLimitEvaluations(runId, {
      portfolio_id: portfolioId,
      limit: pageSize,
      offset: items.length,
    });
    total = page.total;
    items.push(...page.items);
    if (!page.items.length) break;
  }
  return items;
}

function parseSourceInputs(value: string): LimitSourceInputs {
  const parsed: unknown = JSON.parse(value);
  if (parsed == null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Source inputs must be a JSON object.');
  }
  const sourceInputs = parsed as LimitSourceInputs;
  for (const [source, input] of Object.entries(sourceInputs)) {
    if (input == null || Array.isArray(input) || typeof input !== 'object') {
      throw new Error(`Source input ${source} must be a JSON object.`);
    }
  }
  if (
    Object.hasOwn(sourceInputs, 'scenario_test')
    && (
      typeof sourceInputs.scenario_test !== 'object'
      || sourceInputs.scenario_test == null
      || sourceInputs.scenario_test.scenario_request == null
      || Array.isArray(sourceInputs.scenario_test.scenario_request)
      || typeof sourceInputs.scenario_test.scenario_request !== 'object'
    )
  ) {
    throw new Error(
      'Scenario monitoring requires source_inputs.scenario_test.scenario_request.',
    );
  }
  if (
    Object.hasOwn(sourceInputs, 'backtest')
    && (
      typeof sourceInputs.backtest !== 'object'
      || sourceInputs.backtest == null
      || sourceInputs.backtest.spec == null
      || Array.isArray(sourceInputs.backtest.spec)
      || typeof sourceInputs.backtest.spec !== 'object'
    )
  ) {
    throw new Error('Backtest monitoring requires source_inputs.backtest.spec.');
  }
  return sourceInputs;
}

function localDateTimeToIso(value: string): string {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    throw new Error('Valuation as of must be a valid local date and time.');
  }
  return parsed.toISOString();
}

function taskMessage(task: TaskRun): string {
  const progress =
    task.progress_total > 0
      ? ` ${task.progress_current}/${task.progress_total}`
      : '';
  return `Task #${task.id} ${humanize(task.status)}${progress}${
    task.message ? ` — ${task.message}` : ''
  }`;
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ');
}

function isConcurrencyConflict(message: string): boolean {
  return /stale|conflict|row[ _-]?version/i.test(message);
}

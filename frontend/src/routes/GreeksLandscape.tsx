import { useEffect, useId, useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api, listEngineConfigs } from '../api/client';
import { RunWorkbenchPage } from '../components/templates';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { HeaderControls } from '../components/HeaderControls';
import { Select } from '../components/Select';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type { EngineConfigVariant, PageContext, PageContextReporter, Portfolio, PricingParameterProfile, TaskRun } from '../types';
import './GreeksLandscape.css';

type RawPoint = { spot_shift_pct: number; spot?: number; delta: number; gamma: number };
type CashPoint = { spot_shift_pct: number; spot?: number; delta_cash: number; gamma_cash: number };
type GroupCurves = { raw: RawPoint[]; cash_by_currency: Record<string, CashPoint[]> };
type PositionCurves = {
  position_id: number;
  source_trade_id?: string | null;
  underlying: string;
  currency: string;
  engine_name: string;
  calculation_mode: string;
  curves: { raw: RawPoint[]; cash: CashPoint[] };
};

export type GreekLandscapeRun = {
  id: number;
  portfolio_id: number;
  pricing_parameter_profile_id?: number | null;
  engine_config_id?: number | null;
  status: string;
  config: { spot_min_pct: number; spot_max_pct: number; spot_nodes: number };
  results: {
    spot_shifts_pct?: number[];
    portfolio?: GroupCurves;
    by_underlying?: Record<string, GroupCurves>;
    positions?: PositionCurves[];
    valuation_as_of?: string;
  };
  excluded_positions: Array<{ position_id: number; underlying: string; reason: string }> | null;
  resolved_position_ids: number[] | null;
  task_id?: number | null;
  created_at: string;
};

type GridConfig = { spot_min_pct: number; spot_max_pct: number; spot_nodes: number; position_ids?: number[] };
type GridDraft = { spot_min_pct: string; spot_max_pct: string; spot_nodes: string };
type PortfolioOption = { id: number; name: string; positions?: Array<{ id: number; underlying: string; source_trade_id?: string | null }> };

type ViewProps = {
  portfolios: PortfolioOption[];
  selectedPortfolioId: number | null;
  runs: GreekLandscapeRun[];
  selectedRunId: number | null;
  run: GreekLandscapeRun | null;
  running: boolean;
  error: string | null;
  pricingProfiles?: PricingParameterProfile[];
  engineConfigs?: EngineConfigVariant[];
  selectedPricingProfileId?: number | null;
  selectedEngineConfigId?: number | null;
  onSelectPortfolio: (id: number) => void;
  onSelectRun: (id: number | null) => void;
  onSelectPricingProfile?: (id: number | null) => void;
  onSelectEngineConfig?: (id: number | null) => void;
  onRun: (config: GridConfig) => void;
  onPageContextChange?: PageContextReporter;
};

const COLORS = ['#1d6f5f', '#b66a2b', '#536fa8', '#9a4d6c', '#6e7f35', '#7759a6'];

function formatDate(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ');
}

function RunRow({
  run,
  selected,
  onClick,
}: {
  run: GreekLandscapeRun;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <li
      className={`wl-workbench__run-row${selected ? ' wl-workbench__run-row--selected' : ''}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
    >
      <span className="wl-workbench__run-id">Run #{run.id}</span>
      <span className={`wl-workbench__run-status wl-workbench__run-status--${run.status}`}>
        {run.status}
      </span>
      <span className="wl-workbench__run-date">{formatDate(run.created_at)}</span>
      <span className="wl-landscape__run-nodes">
        {run.config.spot_min_pct}% / {run.config.spot_max_pct}% · {run.config.spot_nodes} nodes
      </span>
    </li>
  );
}

export function GreeksLandscapeView(props: ViewProps) {
  const [view, setView] = useState<'portfolio' | 'underlying' | 'position'>('portfolio');
  const [units, setUnits] = useState<'raw' | 'cash'>('raw');
  const [positionId, setPositionId] = useState<number | null>(null);
  const [scope, setScope] = useState<'portfolio' | 'position'>('portfolio');
  const runSearchId = useId();
  const [runSearch, setRunSearch] = useState('');
  const selectedPortfolio = props.portfolios.find((portfolio) => portfolio.id === props.selectedPortfolioId);
  const scopePositions = selectedPortfolio?.positions ?? [];
  const [scopePositionId, setScopePositionId] = useState<number | null>(null);
  const [grid, setGrid] = useState<GridDraft>({ spot_min_pct: '-30', spot_max_pct: '30', spot_nodes: '61' });
  const positions = props.run?.results.positions ?? [];
  const series = useMemo(
    () => landscapeSeries(props.run, view, units, positionId),
    [positionId, props.run, units, view],
  );
  const pageContext = useMemo((): PageContext => ({
    route: 'greeks-landscape',
    title: 'Greeks Landscape',
    path: '/',
    entity_ids: {
      portfolio_id: props.selectedPortfolioId,
      pricing_profile_id: props.selectedPricingProfileId,
      engine_config_id: props.selectedEngineConfigId,
      greeks_landscape_run_id: props.run?.id,
    },
    snapshot: {
      selected_portfolio: selectedPortfolio
        ? {
            id: selectedPortfolio.id,
            name: selectedPortfolio.name,
            position_count: selectedPortfolio.positions?.length ?? null,
          }
        : null,
      calculation_scope: scope,
      scope_position_id: scope === 'position' ? scopePositionId : null,
      view,
      units,
      running: props.running,
      greeks_landscape: props.run
        ? {
            run_id: props.run.id,
            status: props.run.status,
            config: props.run.config,
            valuation_as_of: props.run.results.valuation_as_of,
            resolved_position_ids: props.run.resolved_position_ids,
            excluded_positions: props.run.excluded_positions,
            displayed_series: {
              names: series.names,
              data: series.data.slice(0, 101),
              total_points: series.data.length,
            },
          }
        : null,
    },
    loaded_context: {
      completeness: series.data.length > 101 ? 'partial' : 'complete',
      visible_count: Math.min(series.data.length, 101),
      total_count: series.data.length,
    },
    actions: declareActions([
      {
        name: 'run_greeks_landscape',
        required_ids: ['portfolio_id'],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/greeks-landscape/runs',
      },
      {
        name: 'get_greeks_landscape_run',
        required_ids: ['greeks_landscape_run_id'],
        confirmation: 'implicit',
        backend_endpoint: 'GET /api/greeks-landscape/runs/{run_id}',
      },
      {
        name: 'list_greeks_landscape_runs',
        required_ids: ['portfolio_id'],
        confirmation: 'implicit',
        backend_endpoint: 'GET /api/greeks-landscape/runs?portfolio_id={portfolio_id}',
      },
    ]),
    chips: [
      ...(selectedPortfolio ? [selectedPortfolio.name] : []),
      ...(props.run ? [`Run #${props.run.id}`, props.run.status] : []),
    ],
  }), [
    props.run,
    props.running,
    props.selectedEngineConfigId,
    props.selectedPortfolioId,
    props.selectedPricingProfileId,
    scope,
    scopePositionId,
    selectedPortfolio,
    series,
    units,
    view,
  ]);
  usePageContextReporter(pageContext, props.onPageContextChange);

  const normalizedRunSearch = runSearch.trim().toLowerCase();
  const visibleRuns = normalizedRunSearch
    ? props.runs.filter((run) => [
      `run #${run.id}`,
      String(run.id),
      run.status,
      run.created_at,
      `${run.config.spot_min_pct}%`,
      `${run.config.spot_max_pct}%`,
      `${run.config.spot_nodes} nodes`,
    ].join(' ').toLowerCase().includes(normalizedRunSearch))
    : props.runs;

  useEffect(() => {
    if (props.run?.config) setGrid({
      spot_min_pct: String(props.run.config.spot_min_pct),
      spot_max_pct: String(props.run.config.spot_max_pct),
      spot_nodes: String(props.run.config.spot_nodes),
    });
  }, [props.run?.id]);

  const runConfig = (
    <HeaderControls className="wl-landscape__run-config">
      <div className="wl-landscape__config-row wl-landscape__config-row--context">
        <Select
          className="wl-landscape__config-portfolio"
          variant="inline"
          label="Portfolio"
          value={String(props.selectedPortfolioId ?? '')}
          onChange={(v) => props.onSelectPortfolio(Number(v))}
          options={props.portfolios.map((portfolio) => ({ value: String(portfolio.id), label: portfolio.name }))}
        />
        {props.onSelectPricingProfile && <Select
          className="wl-landscape__config-profile"
          variant="inline"
          label="Pricing profile"
          value={String(props.selectedPricingProfileId ?? '')}
          onChange={(v) => props.onSelectPricingProfile?.(v ? Number(v) : null)}
          options={[
            { value: '', label: 'Live market + assumptions' },
            ...(props.pricingProfiles ?? []).map((profile) => ({ value: String(profile.id), label: profile.name })),
          ]}
        />}
        {props.onSelectEngineConfig && <Select
          className="wl-landscape__config-engine"
          variant="inline"
          label="Engine config"
          value={String(props.selectedEngineConfigId ?? '')}
          onChange={(v) => props.onSelectEngineConfig?.(v ? Number(v) : null)}
          options={[
            { value: '', label: 'Position engines' },
            ...(props.engineConfigs ?? []).map((config) => ({ value: String(config.id), label: config.name })),
          ]}
        />}
      </div>
      <div className="wl-landscape__config-row wl-landscape__config-row--execution">
        <fieldset className="wl-landscape__spot-grid">
          <legend>Spot grid</legend>
          <label className="wl-landscape__grid-field wl-landscape__grid-field--min"><span>Min %</span>
            <input aria-label="Minimum spot change" type="number" value={grid.spot_min_pct} onChange={(e) => setGrid({ ...grid, spot_min_pct: e.target.value })} />
          </label>
          <label className="wl-landscape__grid-field wl-landscape__grid-field--max"><span>Max %</span>
            <input aria-label="Maximum spot change" type="number" value={grid.spot_max_pct} onChange={(e) => setGrid({ ...grid, spot_max_pct: e.target.value })} />
          </label>
          <label className="wl-landscape__grid-field wl-landscape__grid-field--nodes"><span>Nodes</span>
            <input aria-label="Spot nodes" type="number" min={3} max={501} value={grid.spot_nodes} onChange={(e) => setGrid({ ...grid, spot_nodes: e.target.value })} />
          </label>
        </fieldset>
        <Select
          className="wl-landscape__config-scope"
          variant="inline"
          label="Calculation scope"
          value={scope}
          onChange={(v) => setScope(v as typeof scope)}
          options={[
            { value: 'portfolio', label: 'Full portfolio' },
            { value: 'position', label: 'Single position' },
          ]}
        />
        {scope === 'position' && <Select
          className="wl-landscape__config-position"
          variant="inline"
          label="Scope position"
          value={String(scopePositionId ?? scopePositions[0]?.id ?? '')}
          onChange={(v) => setScopePositionId(Number(v))}
          options={scopePositions.map((position) => ({ value: String(position.id), label: `${position.source_trade_id ?? `#${position.id}`} · ${position.underlying}` }))}
        />}
        <Button className="wl-landscape__config-action" variant="primary" disabled={props.running || props.selectedPortfolioId == null} onClick={() => props.onRun({
          spot_min_pct: Number(grid.spot_min_pct),
          spot_max_pct: Number(grid.spot_max_pct),
          spot_nodes: Number(grid.spot_nodes),
          ...(scope === 'position' && (scopePositionId ?? scopePositions[0]?.id) != null
            ? { position_ids: [scopePositionId ?? scopePositions[0].id] }
            : {}),
        })}>
          {props.running ? 'Running…' : 'Run Landscape'}
        </Button>
      </div>
    </HeaderControls>
  );

  const runHistory = (
    <div className="wl-workbench__panel">
      <section className="wl-workbench__section">
        <h2 className="wl-workbench__section-title">Run History</h2>
        {props.runs.length === 0 ? (
          <Empty
            message={
              props.selectedPortfolioId == null
                ? 'Select a portfolio to view run history.'
                : 'No landscape runs for this portfolio.'
            }
            symbol="◌"
          />
        ) : (
          <>
            <label className="wl-workbench__run-search" htmlFor={runSearchId}>
              <span>Search runs</span>
              <input
                id={runSearchId}
                type="search"
                value={runSearch}
                onChange={(event) => setRunSearch(event.target.value)}
                placeholder="Run, status, grid…"
              />
            </label>
            {visibleRuns.length === 0 ? (
              <Empty message="No matching runs." symbol="◌" />
            ) : (
              <ul className="wl-workbench__run-list" role="list" aria-label="Greeks landscape runs">
                {visibleRuns.map((run) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    selected={run.id === props.selectedRunId}
                    onClick={() => props.onSelectRun(run.id === props.selectedRunId ? null : run.id)}
                  />
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </div>
  );

  const runDetail = (
    <div className="wl-workbench__panel wl-workbench__section">
      {props.error && <div className="wl-landscape__error" role="alert">{props.error}</div>}
      {!props.run ? (
        <Empty message={props.running ? 'Landscape calculation is running.' : 'No saved landscape run. Configure the grid and run the calculation.'} symbol="◌" />
      ) : (
        <>
          <div className="wl-landscape__view-controls">
            <Select
              variant="inline"
              label="View"
              value={view}
              onChange={(v) => setView(v as typeof view)}
              options={[
                { value: 'portfolio', label: 'Portfolio' },
                { value: 'underlying', label: 'By underlying' },
                { value: 'position', label: 'Single position' },
              ]}
            />
            <Select
              variant="inline"
              label="Units"
              value={units}
              onChange={(v) => setUnits(v as typeof units)}
              options={[
                { value: 'raw', label: 'Raw Greeks' },
                { value: 'cash', label: 'Cash Greeks' },
              ]}
            />
            {view === 'position' && <Select
              variant="inline"
              label="Position"
              value={String(positionId ?? positions[0]?.position_id ?? '')}
              onChange={(v) => setPositionId(Number(v))}
              options={positions.map((position) => ({ value: String(position.position_id), label: `${position.source_trade_id ?? `#${position.position_id}`} · ${position.underlying}` }))}
            />}
          </div>
          <div className="wl-landscape__series" aria-label="Displayed series">
            {series.names.map((name, index) => <span key={name} style={{ borderColor: COLORS[index % COLORS.length] }}>{name}</span>)}
          </div>
          <div className="wl-landscape__charts">
            <LandscapeChart title="DELTA LANDSCAPE" metric={units === 'raw' ? 'delta' : 'delta_cash'} series={series} />
            <LandscapeChart title="GAMMA LANDSCAPE" metric={units === 'raw' ? 'gamma' : 'gamma_cash'} series={series} />
          </div>
          {props.run.excluded_positions && props.run.excluded_positions.length > 0 && (
            <div className="wl-landscape__error" role="status">
              {props.run.excluded_positions.length} position(s) excluded: {props.run.excluded_positions.map((row) => `${row.underlying}: ${row.reason}`).join('; ')}
            </div>
          )}
        </>
      )}
    </div>
  );

  return (
    <RunWorkbenchPage
      title="GREEKS LANDSCAPE"
      chips={props.run ? [`Run #${props.run.id}`, props.run.status] : []}
      runConfig={runConfig}
      runHistory={runHistory}
      runDetail={runDetail}
    />
  );
}

function LandscapeChart({ title, metric, series }: { title: string; metric: string; series: ChartSeries }) {
  return (
    <section className="wl-landscape__chart">
      <h3>{title}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={series.data} syncId="greeks-landscape" margin={{ top: 10, right: 18, bottom: 10, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
          <XAxis dataKey="spot_shift_pct" unit="%" />
          <YAxis width={80} />
          <Tooltip />
          <Legend />
          <ReferenceLine x={0} stroke="var(--ink-2)" strokeDasharray="4 4" />
          <ReferenceLine y={0} stroke="var(--ink-2)" />
          {series.names.map((name, index) => (
            <Line key={name} type="monotone" dataKey={`${name}:${metric}`} name={name} stroke={COLORS[index % COLORS.length]} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </section>
  );
}

type ChartSeries = { names: string[]; data: Array<Record<string, number>> };

function landscapeSeries(run: GreekLandscapeRun | null, view: 'portfolio' | 'underlying' | 'position', units: 'raw' | 'cash', selectedPositionId: number | null): ChartSeries {
  if (!run) return { names: [], data: [] };
  const groups: Array<{ name: string; raw?: RawPoint[]; cash?: CashPoint[] }> = [];
  if (view === 'portfolio') {
    if (units === 'raw') groups.push({ name: 'Portfolio', raw: run.results.portfolio?.raw ?? [] });
    else Object.entries(run.results.portfolio?.cash_by_currency ?? {}).forEach(([currency, cash]) => groups.push({ name: `Portfolio ${currency}`, cash }));
  } else if (view === 'underlying') {
    Object.entries(run.results.by_underlying ?? {}).forEach(([underlying, group]) => {
      if (units === 'raw') groups.push({ name: underlying, raw: group.raw });
      else Object.entries(group.cash_by_currency).forEach(([currency, cash]) => groups.push({ name: `${underlying} ${currency}`, cash }));
    });
  } else {
    const positions = run.results.positions ?? [];
    const position = positions.find((row) => row.position_id === selectedPositionId) ?? positions[0];
    if (position) groups.push({ name: position.source_trade_id ?? `#${position.position_id}`, ...(units === 'raw' ? { raw: position.curves.raw } : { cash: position.curves.cash }) });
  }
  const shifts = run.results.spot_shifts_pct ?? [];
  return {
    names: groups.map((group) => group.name),
    data: shifts.map((shift, index) => {
      const row: Record<string, number> = { spot_shift_pct: shift };
      groups.forEach((group) => {
        const point = units === 'raw' ? group.raw?.[index] : group.cash?.[index];
        if (!point) return;
        Object.entries(point).forEach(([key, value]) => {
          if (typeof value === 'number') row[`${group.name}:${key}`] = value;
        });
      });
      return row;
    }),
  };
}

const ACTIVE = new Set(['queued', 'running']);

export function GreeksLandscapeLive({ onPageContextChange }: { onPageContextChange?: PageContextReporter } = {}) {
  const [portfolios, setPortfolios] = useState<PortfolioOption[]>([]);
  const [profiles, setProfiles] = useState<PricingParameterProfile[]>([]);
  const [engineConfigs, setEngineConfigs] = useState<EngineConfigVariant[]>([]);
  const [portfolioId, setPortfolioId] = useState<number | null>(null);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [engineConfigId, setEngineConfigId] = useState<number | null>(null);
  const [runs, setRuns] = useState<GreekLandscapeRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [run, setRun] = useState<GreekLandscapeRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<Portfolio[]>('/api/portfolios'),
      api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
      listEngineConfigs(),
    ]).then(([portfolioRows, profileRows, configs]) => {
      setPortfolios(portfolioRows.map(({ id, name, positions }) => ({
        id,
        name,
        positions: positions.map((position) => ({
          id: position.id,
          underlying: position.underlying,
          source_trade_id: position.source_trade_id,
        })),
      })));
      setProfiles(profileRows);
      setEngineConfigs(configs);
      setPortfolioId((current) => current ?? portfolioRows[0]?.id ?? null);
      setEngineConfigId(configs.find((config) => config.is_default)?.id ?? null);
    }).catch((err) => setError(String(err)));
  }, []);

  useEffect(() => {
    if (portfolioId == null) {
      setRuns([]);
      setSelectedRunId(null);
      setRun(null);
      return;
    }
    setError(null);
    api<GreekLandscapeRun[]>(`/api/greeks-landscape/runs?portfolio_id=${portfolioId}`)
      .then((list) => {
        const sorted = [...list].sort((a, b) => b.id - a.id);
        setRuns(sorted);
        const latest = sorted[0] ?? null;
        setSelectedRunId(latest?.id ?? null);
        setRun(latest);
        setProfileId(latest?.pricing_parameter_profile_id ?? null);
        setEngineConfigId(latest?.engine_config_id ?? null);
        if (latest?.task_id && ACTIVE.has(latest.status)) void poll(latest.task_id, latest.id);
      })
      .catch((err) => setError(String(err)));
  }, [portfolioId]);

  const poll = async (taskId: number, runId: number) => {
    setRunning(true);
    while (true) {
      const task = await api<TaskRun>(`/api/tasks/${taskId}`);
      if (!ACTIVE.has(task.status)) {
        const updated = await api<GreekLandscapeRun>(`/api/greeks-landscape/runs/${runId}`);
        setRun(updated);
        setRuns((prev) => {
          const next = prev.map((r) => (r.id === updated.id ? updated : r));
          return next;
        });
        setRunning(false);
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
  };

  const start = async (config: GridConfig) => {
    if (portfolioId == null) return;
    setError(null);
    try {
      const created = await api<GreekLandscapeRun>('/api/greeks-landscape/runs', {
        method: 'POST',
        body: JSON.stringify({
          portfolio_id: portfolioId,
          pricing_parameter_profile_id: profileId,
          engine_config_id: engineConfigId,
          ...config,
        }),
      });
      setRuns((prev) => [created, ...prev.filter((r) => r.id !== created.id)].sort((a, b) => b.id - a.id));
      setSelectedRunId(created.id);
      setRun(created);
      if (created.task_id) void poll(created.task_id, created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRunning(false);
    }
  };

  const handleSelectRun = (id: number | null) => {
    setSelectedRunId(id);
    setRun(id == null ? null : runs.find((r) => r.id === id) ?? null);
  };

  return <GreeksLandscapeView
    portfolios={portfolios}
    selectedPortfolioId={portfolioId}
    runs={runs}
    selectedRunId={selectedRunId}
    run={run}
    running={running}
    error={error}
    pricingProfiles={profiles}
    engineConfigs={engineConfigs}
    selectedPricingProfileId={profileId}
    selectedEngineConfigId={engineConfigId}
    onSelectPortfolio={setPortfolioId}
    onSelectRun={handleSelectRun}
    onSelectPricingProfile={setProfileId}
    onSelectEngineConfig={setEngineConfigId}
    onRun={start}
    onPageContextChange={onPageContextChange}
  />;
}

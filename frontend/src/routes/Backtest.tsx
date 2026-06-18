import { useEffect, useId, useRef, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { RunWorkbenchPage } from '../components/templates';
import { Button } from '../components/Button';
import { HeaderControls } from '../components/HeaderControls';
import { DatePicker } from '../components/DatePicker';
import { Empty } from '../components/Empty';
import { Select } from '../components/Select';
import {
  api,
  backtestArtifactUrl,
  createBacktestRun,
  getBacktestRun,
  listEngineConfigs,
  listBacktestRuns,
} from '../api/client';
import type {
  BacktestRun,
  BacktestRunRequest,
  BacktestSpec,
  BacktestUnderlying,
  EngineConfigVariant,
  Portfolio,
} from '../types';
import './Backtest.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'empty']);
function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

function formatDate(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ');
}

function fmtNumber(value: number | null | undefined): string {
  if (value == null) return '—';
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtPct(value: number | null | undefined): string {
  if (value == null) return '—';
  return `${value.toFixed(2)}%`;
}

function pnlClass(value: number | null | undefined): string {
  if (value == null) return '';
  if (value > 0) return 'wl-backtest__kpi-value--pos';
  if (value < 0) return 'wl-backtest__kpi-value--neg';
  return '';
}

function hedgeInstrumentLabel(value: BacktestUnderlying['hedge_instrument']): string | null {
  if (value == null) return null;
  if (typeof value === 'string') return value;
  const parts = [value.kind, value.multiplier != null ? `x${value.multiplier}` : null]
    .filter((item): item is string => Boolean(item));
  return parts.length > 0 ? parts.join(' ') : JSON.stringify(value);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RunRow({
  run,
  selected,
  onClick,
}: {
  run: BacktestRun;
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
      <span className="wl-backtest__run-engine">
        {run.spec?.engine ?? '—'}
      </span>
    </li>
  );
}

function UnderlyingRow({ u }: { u: BacktestUnderlying }) {
  const events = u.lifecycle_events ?? [];
  const hedgeLabel = hedgeInstrumentLabel(u.hedge_instrument);
  const pnlCls = u.total_pnl > 0
    ? 'wl-backtest__underlying-stat--pos'
    : u.total_pnl < 0
      ? 'wl-backtest__underlying-stat--neg'
      : '';

  return (
    <details className="wl-backtest__underlying-item">
      <summary>
        <span className="wl-backtest__underlying-name">{u.underlying}</span>
        <span className={`wl-backtest__underlying-stat ${pnlCls}`}>
          P&L: {fmtNumber(u.total_pnl)}
        </span>
        <span className="wl-backtest__underlying-stat">
          Hedge: {fmtNumber(u.hedge_pnl)}
        </span>
        <span className="wl-backtest__underlying-stat">
          {u.num_products} product{u.num_products !== 1 ? 's' : ''}
        </span>
      </summary>
      <div className="wl-backtest__underlying-body">
        {hedgeLabel && (
          <p className="wl-backtest__underlying-note">
            Hedge instrument: {hedgeLabel}
          </p>
        )}
        {events.length > 0 && (
          <>
            <h4 className="wl-backtest__lifecycle-title">Lifecycle Events</h4>
            <table className="wl-backtest__lifecycle-table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Date</th>
                  <th>Cashflow</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev, i) => (
                  <tr key={i}>
                    <td>{ev.type}</td>
                    <td>{ev.date}</td>
                    <td>{fmtNumber(ev.cashflow)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </details>
  );
}

function RunReport({ run }: { run: BacktestRun }) {
  const results = run.results ?? {};
  const portfolio = results.portfolio;
  const byUnderlying: BacktestUnderlying[] = results.by_underlying ?? [];
  const dashboards = run.artifacts?.dashboards ?? {};
  const dashboardEntries = Object.entries(dashboards);

  if (run.status === 'running' || run.status === 'queued') {
    return (
      <div className="wl-backtest__status-running" role="status" aria-live="polite">
        Running… Polling for results.
      </div>
    );
  }

  if (run.status === 'failed') {
    const errMsg = results.error ?? 'Unknown error';
    return (
      <div className="wl-backtest__status-failed" role="alert">
        Run failed: {errMsg}
      </div>
    );
  }

  if (run.status === 'empty') {
    return (
      <div className="wl-backtest__status-failed" role="alert">
        Run completed with no results (empty).
      </div>
    );
  }

  // completed
  return (
    <div className="wl-backtest__report">
      <h3 className="wl-backtest__report-title">
        Run #{run.id} · {run.spec?.engine} · {results.window?.start ?? '?'} – {results.window?.end ?? '?'}
      </h3>

      {/* KPI cards */}
      {portfolio && (
        <div className="wl-backtest__kpis">
          <div className="wl-backtest__kpi">
            <span className="wl-backtest__kpi-label">Total P&L</span>
            <span className={`wl-backtest__kpi-value ${pnlClass(portfolio.total_pnl)}`}>
              {fmtNumber(portfolio.total_pnl)}
            </span>
          </div>
          <div className="wl-backtest__kpi">
            <span className="wl-backtest__kpi-label">Hedge P&L</span>
            <span className={`wl-backtest__kpi-value ${pnlClass(portfolio.hedge_pnl)}`}>
              {fmtNumber(portfolio.hedge_pnl)}
            </span>
          </div>
          <div className="wl-backtest__kpi">
            <span className="wl-backtest__kpi-label">Trades</span>
            <span className="wl-backtest__kpi-value">{portfolio.num_trades}</span>
          </div>
          <div className="wl-backtest__kpi">
            <span className="wl-backtest__kpi-label">Max DD</span>
            <span className={`wl-backtest__kpi-value ${pnlClass(portfolio.max_drawdown)}`}>
              {fmtPct(portfolio.max_drawdown)}
            </span>
          </div>
          <div className="wl-backtest__kpi">
            <span className="wl-backtest__kpi-label">Sharpe</span>
            <span className="wl-backtest__kpi-value">
              {portfolio.sharpe != null ? portfolio.sharpe.toFixed(2) : '—'}
            </span>
          </div>
          <div className="wl-backtest__kpi">
            <span className="wl-backtest__kpi-label">VaR 95</span>
            <span className={`wl-backtest__kpi-value ${pnlClass(portfolio.var_95)}`}>
              {fmtNumber(portfolio.var_95)}
            </span>
          </div>
        </div>
      )}

      {/* Cumulative P&L chart */}
      {portfolio && portfolio.pnl_series && portfolio.pnl_series.length > 0 && (
        <div className="wl-backtest__chart-section">
          <h4 className="wl-backtest__chart-title">Cumulative P&L</h4>
          <div className="wl-backtest__chart-wrap">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={portfolio.pnl_series} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--ink-2)' }}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--hairline)' }}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: 'var(--ink-2)' }}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--hairline)' }}
                  width={60}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--paper)',
                    border: '1px solid var(--hairline-2)',
                    fontSize: 'var(--type-small-size)',
                    color: 'var(--ink)',
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 'var(--type-small-size)', color: 'var(--ink-2)' }} />
                <Line
                  type="monotone"
                  dataKey="total_pnl"
                  name="Total P&L"
                  stroke="var(--info)"
                  dot={false}
                  strokeWidth={2}
                />
                <Line
                  type="monotone"
                  dataKey="hedge_pnl"
                  name="Hedge P&L"
                  stroke="var(--pos)"
                  dot={false}
                  strokeWidth={1.5}
                />
                <Line
                  type="monotone"
                  dataKey="product_pnl"
                  name="Product P&L"
                  stroke="var(--warn)"
                  dot={false}
                  strokeWidth={1.5}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* By-underlying accordion */}
      {byUnderlying.length > 0 && (
        <div className="wl-backtest__underlying-section">
          <h4 className="wl-backtest__underlying-title">By Underlying</h4>
          <div className="wl-backtest__underlying-list">
            {byUnderlying.map((u) => (
              <UnderlyingRow key={u.underlying} u={u} />
            ))}
          </div>
        </div>
      )}

      {/* Dashboard artifact links */}
      {dashboardEntries.length > 0 && (
        <div className="wl-backtest__dashboard-section">
          <h4 className="wl-backtest__underlying-title">Quant-Ark Dashboards</h4>
          {dashboardEntries.map(([underlying, path]) => (
            <div key={underlying} className="wl-backtest__dashboard-card">
              <div className="wl-backtest__dashboard-card-head">
                <span>{underlying}</span>
                <div className="wl-backtest__dashboard-head-actions">
                  <span>Deep-dive HTML</span>
                  <a
                    href={backtestArtifactUrl(run.id, path)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="wl-backtest__dashboard-link wl-backtest__dashboard-link--compact"
                  >
                    Open standalone tab
                  </a>
                </div>
              </div>
              <iframe
                title={`Quant-Ark dashboard ${underlying}`}
                src={backtestArtifactUrl(run.id, path)}
                className="wl-backtest__dashboard-frame"
              />
              <div className="wl-backtest__dashboard-actions">
                <a
                  href={backtestArtifactUrl(run.id, path)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="wl-backtest__dashboard-link"
                >
                  Open full quant-ark dashboard — {underlying}
                </a>
                <a
                  href={backtestArtifactUrl(run.id, path, { download: true })}
                  download
                  className="wl-backtest__dashboard-link"
                >
                  Download dashboard HTML — {underlying}
                </a>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Excluded positions note */}
      {run.excluded_positions && run.excluded_positions.length > 0 && (
        <p style={{ margin: 0, fontSize: 'var(--type-small-size)', color: 'var(--ink-2)' }}>
          {run.excluded_positions.length} position(s) excluded.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export function Backtest() {
  const portfolioPickerId = useId();
  const startId = useId();
  const endId = useId();
  const engineConfigId = useId();
  const volSourceId = useId();
  const runSearchId = useId();

  // Data
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [engineConfigs, setEngineConfigs] = useState<EngineConfigVariant[]>([]);

  // Config form state
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(null);
  const [specStart, setSpecStart] = useState('');
  const [specEnd, setSpecEnd] = useState('');
  const [volSource, setVolSource] = useState<BacktestSpec['vol_source']>('realized');
  const [selectedEngineConfigId, setSelectedEngineConfigId] = useState<number | null>(null);

  // Selections
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [runSearch, setRunSearch] = useState('');

  // UI state
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  // Initial load: portfolios
  useEffect(() => {
    const cancelled = { current: false };
    api<Portfolio[]>('/api/portfolios')
      .then((list) => {
        if (cancelled.current) return;
        const sorted = list ?? [];
        setPortfolios(sorted);
        if (sorted.length > 0) {
          setSelectedPortfolioId(sorted[0].id);
        }
      })
      .catch(() => {
        if (!cancelled.current) setPortfolios([]);
      })
      .finally(() => {
        if (!cancelled.current) setLoading(false);
      });
    return () => { cancelled.current = true; };
  }, []);

  useEffect(() => {
    listEngineConfigs()
      .then((rows) => {
        const list = Array.isArray(rows) ? rows : [];
        setEngineConfigs(list);
        setSelectedEngineConfigId(list.find((row) => row.is_default)?.id ?? list[0]?.id ?? null);
      })
      .catch(() => setEngineConfigs([]));
  }, []);

  // Fetch run history when portfolio changes
  useEffect(() => {
    if (selectedPortfolioId == null) {
      setRuns([]);
      return;
    }
    const cancelled = { current: false };
    listBacktestRuns(selectedPortfolioId)
      .then((data) => {
        if (!cancelled.current) {
          setRuns([...data].sort((a, b) => b.id - a.id));
          setSelectedRunId(null);
        }
      })
      .catch(() => {
        if (!cancelled.current) setRuns([]);
      });
    return () => { cancelled.current = true; };
  }, [selectedPortfolioId]);

  const selectedPortfolio = portfolios.find((p) => p.id === selectedPortfolioId);
  // Poll while selected run is non-terminal
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;
    if (selectedRun == null || isTerminal(selectedRun.status)) {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
      return;
    }
    let stopped = false;
    const timer = setInterval(async () => {
      if (stopped) return;
      try {
        const updated = await getBacktestRun(selectedRun.id);
        if (!stopped) {
          setRuns((prev) => {
            const next = prev.map((r) => (r.id === updated.id ? updated : r));
            return next;
          });
          if (isTerminal(updated.status)) {
            // Refresh full list once terminal
            if (selectedPortfolioId != null) {
              listBacktestRuns(selectedPortfolioId)
                .then((data) => {
                  if (!stopped) setRuns([...data].sort((a, b) => b.id - a.id));
                })
                .catch(() => { /* ignore */ });
            }
          }
        }
      } catch {
        // transient errors shouldn't kill the poll loop
      }
    }, 2000);
    pollingRef.current = timer;
    return () => {
      stopped = true;
      clearInterval(timer);
      pollingRef.current = null;
    };
  }, [runs, selectedRunId, selectedPortfolioId]);

  const handleRun = async () => {
    if (selectedPortfolioId == null) return;
    setSubmitting(true);
    setFeedback(null);
    setError(null);
    try {
      const body: BacktestRunRequest = {
        portfolio_id: selectedPortfolioId,
        engine_config_id: selectedEngineConfigId,
        spec: {
          start: specStart,
          end: specEnd,
          engine: 'quad',
          vol_source: volSource,
        },
      };
      const run = await createBacktestRun(body);
      setFeedback(`Run #${run.id} queued (${run.status}).`);
      const updated = await listBacktestRuns(selectedPortfolioId);
      setRuns([...updated].sort((a, b) => b.id - a.id));
      setSelectedRunId(run.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const normalizedRunSearch = runSearch.trim().toLowerCase();
  const visibleRuns = normalizedRunSearch
    ? runs.filter((run) => [
      `run #${run.id}`,
      String(run.id),
      run.status,
      run.created_at,
      run.spec?.engine,
      run.spec?.vol_source,
    ].filter(Boolean).join(' ').toLowerCase().includes(normalizedRunSearch))
    : runs;

  const chips: string[] = [];
  if (selectedPortfolio) chips.push(selectedPortfolio.name);
  if (runs.length > 0) chips.push(`${runs.length} run${runs.length === 1 ? '' : 's'}`);

  if (loading) {
    return (
      <RunWorkbenchPage
        title="BACKTEST"
        chips={[]}
        runConfig={null}
        runHistory={<Empty variant="loading" message="Loading…" />}
        runDetail={<Empty variant="loading" message="Loading…" />}
      />
    );
  }

  const runConfig = (
    <HeaderControls className="wl-backtest__run-config">
      <Select
        className="wl-backtest__run-config-portfolio"
        variant="inline"
        label="Portfolio"
        id={portfolioPickerId}
        value={String(selectedPortfolioId ?? '')}
        onChange={(v) => setSelectedPortfolioId(v ? Number(v) : null)}
        options={[
          ...(portfolios.length === 0 ? [{ value: '', label: '—' }] : []),
          ...portfolios.map((p) => ({ value: String(p.id), label: p.name })),
        ]}
      />
      <DatePicker
        className="wl-backtest__run-config-date"
        label="Start date"
        id={startId}
        value={specStart}
        onChange={(v) => setSpecStart(v)}
      />
      <DatePicker
        className="wl-backtest__run-config-date"
        label="End date"
        id={endId}
        value={specEnd}
        onChange={(v) => setSpecEnd(v)}
      />
      <Select
        className="wl-backtest__run-config-engine"
        variant="inline"
        label="Engine config"
        id={engineConfigId}
        value={String(selectedEngineConfigId ?? '')}
        onChange={(v) => setSelectedEngineConfigId(v ? Number(v) : null)}
        options={[
          { value: '', label: 'Position engines' },
          ...engineConfigs.map((config) => ({
            value: String(config.id),
            label: `${config.name}${config.is_default ? ' (default)' : ''}`,
          })),
        ]}
      />
      <Select
        className="wl-backtest__run-config-vol"
        variant="inline"
        label="Vol source"
        id={volSourceId}
        value={volSource}
        onChange={(v) => setVolSource(v as BacktestSpec['vol_source'])}
        options={[
          { value: 'realized', label: 'Realized' },
          { value: 'flat', label: 'Flat' },
        ]}
      />
      <Button
        className="wl-backtest__run-config-action"
        variant="primary"
        onClick={handleRun}
        disabled={submitting || selectedPortfolioId == null || !specStart || !specEnd}
      >
        {submitting ? 'Starting…' : 'Run backtest'}
      </Button>
    </HeaderControls>
  );

  const feedbackNode = (feedback || error) ? (
    <>
      {feedback && (
        <div className="wl-backtest__feedback" role="status" aria-live="polite">
          {feedback}
        </div>
      )}
      {error && (
        <div className="wl-backtest__error" role="alert">
          {error}
        </div>
      )}
    </>
  ) : undefined;

  const runHistory = (
    <div className="wl-workbench__panel">
      <section className="wl-workbench__section">
        <h2 className="wl-workbench__section-title">Run History</h2>
        {runs.length === 0 ? (
          <Empty
            message={
              selectedPortfolioId == null
                ? 'Select a portfolio to view run history.'
                : 'No backtest runs for this portfolio.'
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
                placeholder="Run, status, engine…"
              />
            </label>
            {visibleRuns.length === 0 ? (
              <Empty message="No matching runs." symbol="◌" />
            ) : (
              <ul className="wl-workbench__run-list" role="list" aria-label="Backtest runs">
                {visibleRuns.map((run) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    selected={run.id === selectedRunId}
                    onClick={() => setSelectedRunId(run.id === selectedRunId ? null : run.id)}
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
    <section className="wl-workbench__panel wl-workbench__section">
      {selectedRun ? (
        <RunReport run={selectedRun} />
      ) : (
        <Empty
          message="Select a run to view its backtest report."
          symbol="◌"
        />
      )}
    </section>
  );

  return (
    <RunWorkbenchPage
      title="BACKTEST"
      chips={chips}
      runConfig={runConfig}
      feedback={feedbackNode}
      runHistory={runHistory}
      runDetail={runDetail}
    />
  );
}

export function BacktestLive() {
  return <Backtest />;
}

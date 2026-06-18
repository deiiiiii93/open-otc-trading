import { useEffect, useId, useMemo, useState } from 'react';
import { PageScaffold } from '../components/templates/PageScaffold';
import { SplitLayout } from '../components/SplitLayout';
import { Tabs, TabsList, TabsTrigger } from '../components/Tabs';
import { PageToolbar, PageToolbarSpacer, PageToolbarSearch } from '../components/PageToolbar';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Select } from '../components/Select';
import {
  api,
  fetchScenarioLibrary,
  createScenarioTestRun,
  listEngineConfigs,
  listScenarioTestRuns,
  scenarioTestArtifactUrl,
  fetchScenarioSets,
  fetchScenarioSetsFull,
  getScenarioSetScenarios,
  generateScenarioSet,
  saveScenarioSet,
  deleteScenarioSet,
} from '../api/client';
import type {
  EngineConfigVariant,
  PricingParameterProfile,
  Portfolio,
  ScenarioLibrary,
  ScenarioTestRun,
  ScenarioTestRunRequest,
  ScenarioSetDetail,
  ScenarioSetSummary,
  ScenarioGridRequest,
  ScenarioStress,
  ScenarioSpec,
} from '../types';
import { ScenarioDetailDialog } from '../components/ScenarioDetailDialog';
import { ScenarioBuilderDialog } from '../components/ScenarioBuilderDialog';
import { ScenarioGridDialog } from '../components/ScenarioGridDialog';
import '../components/templates/WorkbenchPage.css';
import './ScenarioTest.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ScenarioTab = 'scenarios' | 'runs';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatProfileDate(value: string): string {
  if (!value) return '—';
  const datePrefix = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  if (datePrefix) return datePrefix;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Date(parsed).toISOString().slice(0, 10);
}

function formatDate(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ');
}

function foldScenarioText(value: string): string {
  return value.toLowerCase().replace(/[\s_-]+/g, '');
}

function scenarioMatches(
  item: { name: string; description?: string | null },
  filter: string,
): boolean {
  const needle = foldScenarioText(filter);
  if (!needle) return true;
  return (
    foldScenarioText(item.name).includes(needle) ||
    (!!item.description && foldScenarioText(item.description).includes(needle))
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RunRow({
  run,
  selected,
  onClick,
}: {
  run: ScenarioTestRun;
  selected: boolean;
  onClick: () => void;
}) {
  const worstScenario =
    run.results && typeof run.results === 'object' && 'worst_scenario' in run.results
      ? String(run.results.worst_scenario)
      : null;

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
      {worstScenario && (
        <span className="wl-scenario-test__run-worst" title="Worst scenario">
          {worstScenario}
        </span>
      )}
    </li>
  );
}

function fmtNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtPct(value: number): string {
  // `value` is ALREADY a percentage from the engine (pnl / baseline * 100),
  // forwarded unchanged by shape_results — do not multiply by 100 again.
  return `${value.toFixed(2)}%`;
}

function ReportPreview({ run, reportBasename }: { run: ScenarioTestRun; reportBasename: string }) {
  // Browsers cache iframe content aggressively; append a per-selection cache-bust
  // timestamp so the iframe always fetches the latest report when the run is loaded.
  const cacheBust = useMemo(() => Date.now(), [run.id]);
  const reportUrl = `${scenarioTestArtifactUrl(run.id, reportBasename)}?t=${cacheBust}`;

  return (
    <div className="wl-scenario-test__report-section">
      <div className="wl-scenario-test__report-card">
        <div className="wl-scenario-test__report-card-head">
          <span>HTML Report</span>
          <div className="wl-scenario-test__report-head-actions">
            <a
              href={scenarioTestArtifactUrl(run.id, reportBasename)}
              target="_blank"
              rel="noopener noreferrer"
              className="wl-scenario-test__artifact-link wl-scenario-test__artifact-link--compact"
            >
              Open standalone tab
            </a>
          </div>
        </div>
        <iframe
          title={`Scenario test report for run ${run.id}`}
          src={reportUrl}
          className="wl-scenario-test__report-frame"
        />
        <div className="wl-scenario-test__report-actions">
          <a
            href={scenarioTestArtifactUrl(run.id, reportBasename)}
            target="_blank"
            rel="noopener noreferrer"
            className="wl-scenario-test__artifact-link"
          >
            Open full HTML report
          </a>
          <a
            href={scenarioTestArtifactUrl(run.id, reportBasename, { download: true })}
            download
            className="wl-scenario-test__artifact-link"
          >
            Download HTML report
          </a>
        </div>
      </div>
    </div>
  );
}

function RunDetail({ run }: { run: ScenarioTestRun }) {
  if (run.status === 'running' || run.status === 'queued') {
    return (
      <div className="wl-scenario-test__status-running" role="status" aria-live="polite">
        Running… Polling for results.
      </div>
    );
  }

  if (run.status === 'failed') {
    const results =
      run.results && typeof run.results === 'object'
        ? (run.results as Record<string, unknown>)
        : null;
    const errMsg = results != null && 'error' in results ? String(results.error) : 'Unknown error';
    return (
      <div className="wl-scenario-test__status-failed" role="alert">
        Run failed: {errMsg}
      </div>
    );
  }

  if (run.status === 'empty') {
    return (
      <div className="wl-scenario-test__status-failed" role="alert">
        Run completed with no results (empty).
      </div>
    );
  }

  const results =
    run.results && typeof run.results === 'object' ? (run.results as Record<string, unknown>) : null;

  const baselineValue =
    results != null && 'baseline_value' in results && results.baseline_value != null
      ? Number(results.baseline_value)
      : null;
  const worstScenario =
    results != null && results.worst_scenario != null ? String(results.worst_scenario) : null;
  const bestScenario =
    results != null && results.best_scenario != null ? String(results.best_scenario) : null;

  const scenarios: Array<{ name: string; pnl: number; pnl_pct: number }> =
    results != null && Array.isArray(results.scenarios)
      ? (results.scenarios as Array<{ name: string; pnl: number; pnl_pct: number }>)
      : [];

  const pricingWarnings: Array<{ position_id: number | string; reason: string }> =
    results != null && Array.isArray(results.pricing_warnings)
      ? (results.pricing_warnings as Array<{ position_id: number | string; reason: string }>)
      : [];

  // var_cvar may be an {error: ...} payload when the backend caught a VaR/CVaR
  // failure — only render it when var & cvar are real numbers, else skip the row.
  const rawVarCvar =
    results != null && results.var_cvar != null && typeof results.var_cvar === 'object'
      ? (results.var_cvar as Record<string, unknown>)
      : null;
  const varCvar =
    rawVarCvar != null &&
    typeof rawVarCvar.var === 'number' &&
    typeof rawVarCvar.cvar === 'number'
      ? {
          var: rawVarCvar.var as number,
          cvar: rawVarCvar.cvar as number,
          confidence:
            typeof rawVarCvar.confidence === 'number' ? rawVarCvar.confidence : 0.95,
        }
      : null;

  // Artifact helpers
  const reportHtmlPath = run.artifacts?.report_html_path ?? null;
  const reportBasename = reportHtmlPath?.split('/').pop() ?? null;
  const exportPaths: string[] = run.artifacts?.export_paths ?? [];

  return (
    <div className="wl-scenario-test__run-detail">
      <h3 className="wl-scenario-test__detail-title">
        Run #{run.id} · {run.status}
      </h3>

      {baselineValue != null && (
        <div className="wl-scenario-test__detail-row">
          <span className="wl-scenario-test__detail-label">Baseline value</span>
          <span className="wl-scenario-test__detail-value">{fmtNumber(baselineValue)}</span>
        </div>
      )}

      {worstScenario && (
        <div className="wl-scenario-test__detail-row">
          <span className="wl-scenario-test__detail-label">Worst scenario</span>
          <span className="wl-scenario-test__detail-value">{worstScenario}</span>
        </div>
      )}

      {bestScenario && (
        <div className="wl-scenario-test__detail-row">
          <span className="wl-scenario-test__detail-label">Best scenario</span>
          <span className="wl-scenario-test__detail-value">{bestScenario}</span>
        </div>
      )}

      {varCvar != null && (
        <div className="wl-scenario-test__detail-row">
          <span className="wl-scenario-test__detail-label">
            VaR / CVaR ({(varCvar.confidence * 100).toFixed(0)}%)
          </span>
          <span className="wl-scenario-test__detail-value">
            {fmtNumber(varCvar.var)} / {fmtNumber(varCvar.cvar)}
          </span>
        </div>
      )}

      {scenarios.length > 0 && (
        <div className="wl-scenario-test__scenarios-section">
          <h4 className="wl-scenario-test__scenarios-title">Scenarios</h4>
          <table className="wl-scenario-test__scenarios-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>P&amp;L</th>
                <th>P&amp;L %</th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td>
                  <td>{fmtNumber(s.pnl)}</td>
                  <td>{fmtPct(s.pnl_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {run.excluded_positions && run.excluded_positions.length > 0 && (
        <p className="wl-scenario-test__detail-note">
          {run.excluded_positions.length} position(s) excluded.
        </p>
      )}

      {pricingWarnings.length > 0 && (
        <div className="wl-scenario-test__pricing-warnings" role="alert">
          <p className="wl-scenario-test__pricing-warnings-title">
            Pricing warnings: some positions were priced off fallback assumptions.
          </p>
          <ul className="wl-scenario-test__pricing-warnings-list">
            {pricingWarnings.map((w, i) => (
              <li key={i}>
                Position {w.position_id}: {w.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {reportBasename && (
        <ReportPreview run={run} reportBasename={reportBasename} />
      )}

      {exportPaths.length > 0 && (
        <div className="wl-scenario-test__artifacts">
          <h4 className="wl-scenario-test__artifacts-title">Export files</h4>
          {exportPaths.map((p) => {
            const basename = p.split('/').pop() ?? p;
            return (
              <a
                key={p}
                href={scenarioTestArtifactUrl(run.id, basename, { download: true })}
                download
                className="wl-scenario-test__artifact-link"
              >
                {basename}
              </a>
            );
          })}
        </div>
      )}

      {run.artifacts?.notes && run.artifacts.notes.length > 0 && (
        <ul className="wl-scenario-test__notes">
          {run.artifacts.notes.map((note, i) => <li key={i}>{note}</li>)}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export function ScenarioTestLive() {
  const portfolioPickerId = useId();
  const profilePickerId = useId();
  const engineConfigPickerId = useId();
  const runSearchId = useId();

  // Data
  const [library, setLibrary] = useState<ScenarioLibrary | null>(null);
  const [portfolios, setPortfolios] = useState<Pick<Portfolio, 'id' | 'name'>[]>([]);
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([]);
  const [engineConfigs, setEngineConfigs] = useState<EngineConfigVariant[]>([]);
  const [runs, setRuns] = useState<ScenarioTestRun[]>([]);

  // Selections
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null);
  const [selectedEngineConfigId, setSelectedEngineConfigId] = useState<number | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [customSets, setCustomSets] = useState<ScenarioSetDetail[]>([]);
  const [selectedCustomNames, setSelectedCustomNames] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<{ name: string; description: string; stresses: ScenarioStress[] } | null>(null);
  const [builder, setBuilder] = useState<{ initial: ScenarioSetDetail | null } | null>(null);
  const [sets, setSets] = useState<ScenarioSetSummary[]>([]);
  const [selectedSetNames, setSelectedSetNames] = useState<Set<string>>(new Set());
  const [grid, setGrid] = useState<{ initial: ScenarioSetSummary | null } | null>(null);
  const [expandedSet, setExpandedSet] = useState<string | null>(null);
  const [setMembers, setSetMembers] = useState<Record<string, ScenarioSpec[]>>({});

  // UI state
  const [activeTab, setActiveTab] = useState<ScenarioTab>('scenarios');
  const [scenarioSearch, setScenarioSearch] = useState('');
  const [runSearch, setRunSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  // Initial load: library + portfolios + pricing profiles
  useEffect(() => {
    const cancelled = { current: false };
    Promise.allSettled([
      fetchScenarioLibrary(),
      api<Portfolio[]>('/api/portfolios'),
      api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
      listEngineConfigs(),
      fetchScenarioSets(),
      fetchScenarioSetsFull(),
    ]).then(([libResult, portResult, profileResult, engineConfigResult, setsResult, setsFullResult]) => {
      if (cancelled.current) return;

      if (libResult.status === 'fulfilled') {
        setLibrary(libResult.value);
      }
      if (portResult.status === 'fulfilled') {
        const list = portResult.value ?? [];
        setPortfolios(list);
        if (list.length > 0) {
          setSelectedPortfolioId(list[0].id);
        }
      }
      if (profileResult.status === 'fulfilled') {
        setPricingProfiles(Array.isArray(profileResult.value) ? profileResult.value : []);
      }
      if (engineConfigResult.status === 'fulfilled') {
        const rows = Array.isArray(engineConfigResult.value) ? engineConfigResult.value : [];
        setEngineConfigs(rows);
        setSelectedEngineConfigId(rows.find((row) => row.is_default)?.id ?? rows[0]?.id ?? null);
      }
      if (setsResult.status === 'fulfilled') {
        setCustomSets(Array.isArray(setsResult.value) ? setsResult.value : []);
      }
      if (setsFullResult.status === 'fulfilled') {
        setSets(Array.isArray(setsFullResult.value) ? setsFullResult.value : []);
      }
    }).catch((e) => {
      if (!cancelled.current) setError(String(e));
    }).finally(() => {
      if (!cancelled.current) setLoading(false);
    });
    return () => { cancelled.current = true; };
  }, []);

  // Fetch run history when portfolio changes
  useEffect(() => {
    if (selectedPortfolioId == null) {
      setRuns([]);
      return;
    }
    const cancelled = { current: false };
    listScenarioTestRuns(selectedPortfolioId)
      .then((data) => {
        if (!cancelled.current) {
          const sorted = [...data].sort((a, b) => b.id - a.id);
          setRuns(sorted);
          setSelectedRunId(null);
        }
      })
      .catch(() => {
        if (!cancelled.current) setRuns([]);
      });
    return () => { cancelled.current = true; };
  }, [selectedPortfolioId]);

  // Poll while any run is still queued/running so async results + artifacts
  // surface without a manual reload or portfolio switch.
  useEffect(() => {
    if (selectedPortfolioId == null) return;
    const ACTIVE = new Set(['queued', 'running']);
    if (!runs.some((r) => ACTIVE.has(r.status))) return;
    let stopped = false;
    const timer = setInterval(async () => {
      if (stopped) return;
      try {
        const data = await listScenarioTestRuns(selectedPortfolioId);
        if (!stopped) setRuns([...data].sort((a, b) => b.id - a.id));
      } catch {
        // transient errors shouldn't kill the poll loop
      }
    }, 4000);
    return () => { stopped = true; clearInterval(timer); };
  }, [runs, selectedPortfolioId]);

  const toggleKey = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const reloadCustomSets = async () => {
    try { setCustomSets(await fetchScenarioSets()); } catch { /* keep prior */ }
  };

  const handleSaveCustom = async (name: string, description: string, stresses: ScenarioStress[]) => {
    await saveScenarioSet(name, [{ name, description, stresses }]);
    setBuilder(null);
    await reloadCustomSets();
  };

  const handleDeleteCustom = async (name: string) => {
    if (!window.confirm(`Delete custom scenario "${name}"?`)) return;
    try {
      await deleteScenarioSet(name);
      setSelectedCustomNames((prev) => { const n = new Set(prev); n.delete(name); return n; });
      await reloadCustomSets();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleCustom = (name: string) =>
    setSelectedCustomNames((prev) => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name); else n.add(name);
      return n;
    });

  const reloadSets = async () => {
    try { setSets(await fetchScenarioSetsFull()); } catch { /* keep prior */ }
  };

  const toggleSet = (name: string) =>
    setSelectedSetNames((prev) => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name); else n.add(name);
      return n;
    });

  const handleGenerate = async (body: ScenarioGridRequest) => {
    await generateScenarioSet(body);
    setGrid(null);
    await reloadSets();
  };

  const handleDeleteSet = async (name: string) => {
    if (!window.confirm(`Delete scenario set "${name}"?`)) return;
    try {
      await deleteScenarioSet(name);
      setSelectedSetNames((prev) => { const n = new Set(prev); n.delete(name); return n; });
      await reloadSets();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const toggleExpandSet = async (name: string) => {
    if (expandedSet === name) { setExpandedSet(null); return; }
    setExpandedSet(name);
    if (!setMembers[name]) {
      try {
        const members = await getScenarioSetScenarios(name);
        setSetMembers((prev) => ({ ...prev, [name]: members }));
      } catch { /* show nothing on error */ }
    }
  };

  const handleRun = async () => {
    if (selectedPortfolioId == null) return;
    setSubmitting(true);
    setFeedback(null);
    setError(null);
    try {
      const body: ScenarioTestRunRequest = {
        portfolio_id: selectedPortfolioId,
        pricing_parameter_profile_id: selectedProfileId,
        engine_config_id: selectedEngineConfigId,
        predefined: Array.from(selectedKeys),
        scenario_sets: [...selectedCustomNames, ...selectedSetNames],
        config: {
          calculate_greeks: true,
          greeks_method: 'numerical',
          export_formats: ['json', 'csv'],
        },
      };
      const run = await createScenarioTestRun(body);
      setFeedback(`Run #${run.id} queued (${run.status}).`);
      // Refresh run list
      const updated = await listScenarioTestRuns(selectedPortfolioId);
      setRuns([...updated].sort((a, b) => b.id - a.id));
      // Switch to the Runs tab so the queued run is visible.
      setActiveTab('runs');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  // Chips for the page header
  const selectedPortfolio = portfolios.find((p) => p.id === selectedPortfolioId);
  const chips: string[] = [];
  if (selectedPortfolio) chips.push(selectedPortfolio.name);
  if (runs.length > 0) chips.push(`${runs.length} run${runs.length === 1 ? '' : 's'}`);

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;

  const filteredPredefined = library?.predefined.filter((s) => scenarioMatches(s, scenarioSearch)) ?? [];
  const filteredCustom = customSets.filter((s) => scenarioMatches(s, scenarioSearch));
  const filteredSets = sets.filter((s) =>
    scenarioMatches({ name: s.name, description: s.axes_summary }, scenarioSearch),
  );

  const scenariosPanel = (
    <div className="wl-workbench__panel wl-workbench__panel--scenarios">
      {/* Scenario picker */}
      <section className="wl-workbench__section">
        <h2 className="wl-workbench__section-title">Predefined Scenarios</h2>
        {library == null || library.predefined.length === 0 ? (
          <Empty message="No predefined scenarios available." symbol="◌" />
        ) : filteredPredefined.length === 0 ? (
          <Empty message="No predefined scenarios match this filter." symbol="◌" />
        ) : (
          <ul className="wl-scenario-test__scenario-list" role="list" aria-label="Predefined scenarios">
            {filteredPredefined.map((s) => (
              <li key={s.key} className="wl-scenario-test__scenario-item">
                <label className="wl-scenario-test__scenario-label">
                  <input
                    type="checkbox"
                    aria-label={s.name}
                    checked={selectedKeys.has(s.key)}
                    onChange={() => toggleKey(s.key)}
                  />
                  <div className="wl-scenario-test__scenario-meta">
                    <button
                      type="button"
                      className="wl-scenario-test__scenario-name-btn"
                      aria-label={`Details for ${s.name}`}
                      onClick={(e) => {
                        e.preventDefault();
                        setDetail({ name: s.name, description: s.description, stresses: s.stresses ?? [] });
                      }}
                    >
                      {s.name}
                    </button>
                    <span className="wl-scenario-test__scenario-desc">{s.description}</span>
                  </div>
                  <span className="wl-scenario-test__scenario-count">
                    {s.num_stresses} stress{s.num_stresses === 1 ? '' : 'es'}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Custom Scenarios */}
      <section className="wl-workbench__section">
        <div className="wl-workbench__section-head">
          <h2 className="wl-workbench__section-title">Custom Scenarios</h2>
          <Button variant="ghost" onClick={() => setBuilder({ initial: null })}>+ New custom scenario</Button>
        </div>
        {customSets.length === 0 ? (
          <Empty message="No custom scenarios yet." symbol="◌" />
        ) : filteredCustom.length === 0 ? (
          <Empty message="No custom scenarios match this filter." symbol="◌" />
        ) : (
          <ul className="wl-scenario-test__scenario-list" role="list" aria-label="Custom scenarios">
            {filteredCustom.map((s) => (
              <li
                key={s.name}
                className="wl-scenario-test__scenario-item wl-scenario-test__scenario-item--custom"
              >
                <label className="wl-scenario-test__scenario-label">
                  <input
                    type="checkbox"
                    aria-label={s.name}
                    checked={selectedCustomNames.has(s.name)}
                    onChange={() => toggleCustom(s.name)}
                  />
                  <div className="wl-scenario-test__scenario-meta">
                    <button
                      type="button"
                      className="wl-scenario-test__scenario-name-btn"
                      aria-label={`Details for ${s.name}`}
                      onClick={(e) => {
                        e.preventDefault();
                        setDetail({ name: s.name, description: s.description, stresses: s.stresses ?? [] });
                      }}
                    >
                      {s.name}
                    </button>
                    {s.description && <span className="wl-scenario-test__scenario-desc">{s.description}</span>}
                  </div>
                  <span className="wl-scenario-test__scenario-count">
                    {s.stresses.length} stress{s.stresses.length === 1 ? '' : 'es'}
                  </span>
                </label>
                <div className="wl-scenario-test__scenario-row-actions">
                  <Button variant="ghost" onClick={() => setBuilder({ initial: s })}>Edit</Button>
                  <Button variant="ghost" onClick={() => handleDeleteCustom(s.name)}>Delete</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Scenario Sets (multi-scenario, generated grids) */}
      <section className="wl-workbench__section">
        <div className="wl-workbench__section-head">
          <h2 className="wl-workbench__section-title">Scenario Sets</h2>
          <Button variant="ghost" onClick={() => setGrid({ initial: null })}>+ Generate set</Button>
        </div>
        {sets.length === 0 ? (
          <Empty message="No scenario sets yet." symbol="◌" />
        ) : filteredSets.length === 0 ? (
          <Empty message="No scenario sets match this filter." symbol="◌" />
        ) : (
          <ul className="wl-scenario-test__scenario-list" role="list" aria-label="Scenario sets">
            {filteredSets.map((s) => (
              <li key={s.name} className="wl-scenario-test__scenario-item wl-scenario-test__scenario-item--custom">
                <label className="wl-scenario-test__scenario-label">
                  <input
                    type="checkbox"
                    aria-label={s.name}
                    checked={selectedSetNames.has(s.name)}
                    onChange={() => toggleSet(s.name)}
                  />
                  <div className="wl-scenario-test__scenario-meta">
                    <button
                      type="button"
                      className="wl-scenario-test__scenario-name-btn"
                      aria-label={`View ${s.name}`}
                      onClick={(e) => { e.preventDefault(); toggleExpandSet(s.name); }}
                    >
                      {s.name}
                    </button>
                    <span className="wl-scenario-test__scenario-desc">
                      {(s.axes_summary || 'set')} · {s.num_scenarios}
                    </span>
                  </div>
                  <span className="wl-scenario-test__scenario-count">{s.num_scenarios} scenarios</span>
                </label>
                <div className="wl-scenario-test__scenario-row-actions">
                  {s.has_grid && <Button variant="ghost" onClick={() => setGrid({ initial: s })}>Edit</Button>}
                  <Button variant="ghost" onClick={() => handleDeleteSet(s.name)}>Delete</Button>
                </div>
                {expandedSet === s.name && (
                  <ul className="wl-scenario-test__set-members" aria-label={`${s.name} members`}>
                    {(setMembers[s.name] ?? []).map((m, i) => (
                      <li key={i} className="wl-scenario-test__set-member">
                        {m.name} — {m.stresses.length} stress{m.stresses.length === 1 ? '' : 'es'}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );

  const normalizedRunSearch = runSearch.trim().toLowerCase();
  const visibleRuns = normalizedRunSearch
    ? runs.filter((run) => [
      `run #${run.id}`,
      String(run.id),
      run.status,
      run.created_at,
    ].filter(Boolean).join(' ').toLowerCase().includes(normalizedRunSearch))
    : runs;

  const runHistory = (
    <div className="wl-workbench__panel">
      <section className="wl-workbench__section">
        <h2 className="wl-workbench__section-title">Run History</h2>
        {runs.length === 0 ? (
          <Empty
            message={
              selectedPortfolioId == null
                ? 'Select a portfolio to view run history.'
                : 'No scenario test runs for this portfolio.'
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
                placeholder="Run, status…"
              />
            </label>
            {visibleRuns.length === 0 ? (
              <Empty message="No matching runs." symbol="◌" />
            ) : (
              <ul className="wl-workbench__run-list" role="list" aria-label="Scenario test runs">
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
        <RunDetail run={selectedRun} />
      ) : (
        <Empty
          message="Select a run to view its scenario test report."
          symbol="◌"
        />
      )}
    </section>
  );

  const runsPanel = (
    <SplitLayout rail={runHistory} railWidth="var(--rail-width)" railLabel="Scenario test run history">
      {runDetail}
    </SplitLayout>
  );

  const feedbackNode = (feedback || error) ? (
    <>
      {feedback && (
        <div className="wl-scenario-test__feedback" role="status" aria-live="polite">
          {feedback}
        </div>
      )}
      {error && (
        <div className="wl-scenario-test__error" role="alert">
          {error}
        </div>
      )}
    </>
  ) : undefined;

  const toolbar = (
    <PageToolbar>
      <Select
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
      <Select
        variant="inline"
        label="Pricing parameter profile"
        id={profilePickerId}
        value={String(selectedProfileId ?? '')}
        onChange={(v) => setSelectedProfileId(v ? Number(v) : null)}
        options={[
          { value: '', label: 'None' },
          ...pricingProfiles.map((profile) => ({
            value: String(profile.id),
            label: `${profile.name} · ${formatProfileDate(profile.valuation_date)}`,
          })),
        ]}
      />
      <Select
        variant="inline"
        label="Engine config"
        id={engineConfigPickerId}
        value={String(selectedEngineConfigId ?? '')}
        onChange={(v) => setSelectedEngineConfigId(v ? Number(v) : null)}
        options={[
          { value: '', label: 'Position engines only' },
          ...engineConfigs.map((config) => ({
            value: String(config.id),
            label: `${config.name}${config.is_default ? ' (default)' : ''}`,
          })),
        ]}
      />
      {activeTab === 'scenarios' && (
        <PageToolbarSearch
          value={scenarioSearch}
          onChange={setScenarioSearch}
          placeholder="Filter scenarios…"
          aria-label="Filter scenarios"
        />
      )}
      <PageToolbarSpacer />
      <Button
        variant="primary"
        onClick={handleRun}
        disabled={submitting || selectedPortfolioId == null
          || (selectedKeys.size === 0 && selectedCustomNames.size === 0 && selectedSetNames.size === 0)}
      >
        {submitting ? 'Running…' : 'Run scenario test'}
      </Button>
    </PageToolbar>
  );

  if (loading) {
    return (
      <PageScaffold title="Scenario Test" chips={[]}>
        <Empty variant="loading" message="Loading…" />
      </PageScaffold>
    );
  }

  return (
    <PageScaffold
      title="Scenario Test"
      chips={chips}
      feedback={feedbackNode}
    >
      <Tabs value={activeTab} onValueChange={(v: string) => setActiveTab(v as ScenarioTab)}>
        <TabsList aria-label="Scenario test tabs">
          <TabsTrigger value="scenarios">Scenarios</TabsTrigger>
          <TabsTrigger value="runs">
            Runs <span className="wl-scenario-test__tab-count">{runs.length}</span>
          </TabsTrigger>
        </TabsList>
      </Tabs>
      {toolbar}
      {activeTab === 'scenarios' ? scenariosPanel : runsPanel}

      {detail && (
        <ScenarioDetailDialog
          open
          name={detail.name}
          description={detail.description}
          stresses={detail.stresses}
          onClose={() => setDetail(null)}
        />
      )}
      {builder && (
        <ScenarioBuilderDialog
          open
          initial={builder.initial}
          existingNames={customSets.map((s) => s.name)}
          onSave={handleSaveCustom}
          onClose={() => setBuilder(null)}
        />
      )}
      {grid && (
        <ScenarioGridDialog
          open
          initial={grid.initial}
          existingNames={[...customSets.map((s) => s.name), ...sets.map((s) => s.name)]}
          onGenerate={handleGenerate}
          onClose={() => setGrid(null)}
        />
      )}
    </PageScaffold>
  );
}

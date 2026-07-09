import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { PageScaffold } from '../components/templates/PageScaffold';
import { Table, type Column } from '../components/Table';
import {
  deleteArenaRuns,
  getArenaLeaderboard,
  getArenaRun,
  getMatchTranscript,
  listArenaModels,
  listArenaRuns,
  mergeArenaRuns,
  type ArenaLeaderboardRow,
  type ArenaMatchSummary,
  type ArenaModel,
  type ArenaRunDetail,
  type ArenaRunSummary,
  type ArenaScoreBreakdown,
  type ArenaCheck,
  type ArenaObjectiveStep,
} from '../lib/arenaApi';
import './Arena.css';

// Dimension order + card-stat labels, so the drilldown groups and the ability
// card speak the same language. A stat is round(99 × passed/total) — the same
// kernel the backend card uses — computed here from the axis tally so the header
// stays truthful even for rows without a stored card.
const AXIS_ORDER = ['grounding', 'adherence', 'synthesis', 'procedural'] as const;
const AXIS_STAT: Record<string, string> = {
  grounding: 'GRD',
  adherence: 'ADH',
  synthesis: 'SYN',
  procedural: 'PRC',
};
const axisStat = (passed: number, total: number): number =>
  total > 0 ? Math.round((99 * passed) / total) : 0;

function CheckRow({ check, showAxis = false }: { check: ArenaCheck; showAxis?: boolean }) {
  return (
    <li className={`wl-arena__check wl-arena__check--${check.passed ? 'pass' : 'fail'}`}>
      <span className="wl-arena__check-mark" aria-hidden="true">
        {check.passed ? '✓' : '✗'}
      </span>
      <span className="wl-arena__check-label">{check.label}</span>
      {showAxis && check.axis && AXIS_STAT[check.axis] && (
        <span className="wl-arena__check-axis" title={check.axis}>
          {AXIS_STAT[check.axis]}
        </span>
      )}
      {!check.passed && check.detail && (
        <span className="wl-arena__check-detail">{check.detail}</span>
      )}
    </li>
  );
}

// "By dimension" view: flatten every scored check (per-step + success criteria)
// and bucket by axis, so a user reads exactly which checks fed GRD/ADH/SYN/PRC
// and where points were lost. Source order is preserved within a dimension.
// Unknown-axis checks (older rows) fall into a trailing "other" group.
function DimensionGroups({
  steps,
  success,
}: {
  steps: ArenaObjectiveStep[];
  success: ArenaCheck[];
}) {
  const groups = new Map<string, ArenaCheck[]>();
  for (const c of [...steps.flatMap((s) => s.checks), ...success]) {
    const key = c.axis && AXIS_STAT[c.axis] ? c.axis : 'other';
    const arr = groups.get(key);
    if (arr) arr.push(c);
    else groups.set(key, [c]);
  }
  const ordered = [...AXIS_ORDER, 'other'].filter((k) => groups.has(k));
  return (
    <>
      {ordered.map((axis) => {
        const checks = groups.get(axis)!;
        const passed = checks.filter((c) => c.passed).length;
        const total = checks.length;
        const stat = AXIS_STAT[axis];
        return (
          <div key={axis} className="wl-arena__dim-group">
            <div className="wl-arena__dim-head">
              <span className="wl-arena__dim-name">{axis}</span>
              <span className="wl-arena__dim-tally">
                {passed}/{total}
              </span>
              {stat && (
                <span
                  className="wl-arena__dim-stat"
                  title={`${stat} = round(99 × ${passed}/${total})`}
                >
                  {stat} {axisStat(passed, total)}
                </span>
              )}
            </div>
            <ul className="wl-arena__check-list">
              {checks.map((c, i) => (
                <CheckRow key={i} check={c} />
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );
}

// The six ability-card axes: the five OVR stats + Consistency (CON). CON is
// muted (greyed) only when null — a single-match run has no dispersion to measure.
// When present it is a first-class stat folded into the OVR (weight 0.18), so it
// is NOT advisory. Numbers-first order matches the OVR weighting.
function cardAxes(
  card: NonNullable<ArenaScoreBreakdown['card']>,
): { key: string; value: number | null; advisory?: boolean }[] {
  const con = card.con ?? null;
  return [
    { key: 'GRD', value: card.stats.GRD },
    { key: 'ADH', value: card.stats.ADH },
    { key: 'SYN', value: card.stats.SYN },
    { key: 'EFF', value: card.stats.EFF },
    { key: 'PRC', value: card.stats.PRC },
    { key: 'CON', value: con, advisory: con == null },
  ];
}

function AbilityCardView({ card }: { card: NonNullable<ArenaScoreBreakdown['card']> }) {
  const stats = cardAxes(card);
  return (
    <div className="wl-arena__card">
      <div className="wl-arena__card-ovr">
        <span className="wl-arena__card-ovr-value">{card.ovr}</span>
        <span className="wl-arena__card-ovr-label">OVR</span>
      </div>
      <span className="wl-arena__card-position">{card.position}</span>
      <div className="wl-arena__card-stats">
        {stats.map((s) => (
          <div
            key={s.key}
            className={
              'wl-arena__stat' + (s.advisory ? ' wl-arena__stat--muted' : '')
            }
          >
            <span className="wl-arena__stat-value">
              {s.value != null ? Math.round(s.value) : '—'}
            </span>
            <span className="wl-arena__stat-name">{s.key}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Radar/hexagon of the six ability-card axes (five OVR stats + Consistency), for
// at-a-glance profile comparison across a run's match cards. Drawn in a 100×100
// viewBox sized 1:1 to px so the label font can use the caps type token directly.
// Axis order matches AbilityCardView (numbers-first, OVR weighting). CON plots
// like any stat when measured; when null (a single-match run — no dispersion) it
// plots at the centre with a muted label, so the shape reads honestly.
function AbilityHexagon({ card }: { card: NonNullable<ArenaScoreBreakdown['card']> }) {
  const axes = cardAxes(card);
  const C = 50; // centre of the 100×100 viewBox
  const R = 30; // radius to the outer ring (leaves room for vertex labels)
  const MAX = 99; // a stat's ceiling
  const at = (i: number, frac: number): [number, number] => {
    const a = (Math.PI * 2 * i) / axes.length - Math.PI / 2; // vertex 0 at top
    return [C + Math.cos(a) * R * frac, C + Math.sin(a) * R * frac];
  };
  const ring = (frac: number) =>
    axes.map((_, i) => at(i, frac).map((n) => n.toFixed(1)).join(',')).join(' ');
  const data = axes
    .map((ax, i) =>
      at(i, Math.max(0, Math.min(MAX, ax.value ?? 0)) / MAX)
        .map((n) => n.toFixed(1))
        .join(','),
    )
    .join(' ');
  // Anchor labels away from the shape so they don't overlap the vertices.
  const anchor = (x: number) => (x > C + 3 ? 'start' : x < C - 3 ? 'end' : 'middle');
  return (
    <svg
      className="wl-arena__hex"
      viewBox="0 0 100 100"
      width={100}
      height={100}
      role="img"
      aria-label={`Ability radar, OVR ${card.ovr}`}
    >
      <polygon className="wl-arena__hex-ring" points={ring(1)} />
      <polygon className="wl-arena__hex-ring" points={ring(0.5)} />
      {axes.map((_, i) => {
        const [x, y] = at(i, 1);
        return (
          <line
            key={i}
            className="wl-arena__hex-spoke"
            x1={C}
            y1={C}
            x2={x.toFixed(1)}
            y2={y.toFixed(1)}
          />
        );
      })}
      <polygon className="wl-arena__hex-data" points={data} />
      {axes.map((ax, i) => {
        const [x, y] = at(i, 1.28);
        return (
          <text
            key={ax.key}
            className={
              'wl-arena__hex-label' + (ax.advisory ? ' wl-arena__hex-label--muted' : '')
            }
            x={x.toFixed(1)}
            y={y.toFixed(1)}
            textAnchor={anchor(x)}
            dominantBaseline="middle"
          >
            {ax.key}
          </text>
        );
      })}
    </svg>
  );
}

// Compact per-trial roster + the aggregate card, shown on the drilldown's
// "Average" tab. The misleading single-trial per-check detail that used to render
// here (aggregate rows carry one representative trial's steps but a MEAN headline)
// is replaced by an honest summary; the real per-check detail lives in each Trial tab.
function AggregateSummary({ breakdown }: { breakdown: ArenaScoreBreakdown }) {
  const trials = breakdown.aggregate ?? [];
  return (
    <div className="wl-arena__breakdown">
      <div className="wl-arena__breakdown-head">
        <span className="wl-arena__transcript-title">
          Average of {breakdown.n_trials ?? trials.length} trials
        </span>
        <span className="wl-arena__breakdown-tally">
          {breakdown.objective_score != null
            ? `Objective ${breakdown.objective_score.toFixed(1)}`
            : 'Objective n/a'}
          {breakdown.objective_stdev != null
            ? ` ± ${breakdown.objective_stdev.toFixed(1)}`
            : ''}
        </span>
      </div>
      {breakdown.card && <AbilityCardView card={breakdown.card} />}
      <ul className="wl-arena__check-list">
        {trials.map((t, i) => (
          <li key={i} className="wl-arena__check-row">
            <span className="wl-arena__check-detail">
              Trial {i + 1}: OVR {t.card?.ovr ?? '—'} · objective{' '}
              {t.objective?.passed ?? '?'}/{t.objective?.total ?? '?'}
              {t.judge?.judged_score != null
                ? ` · judge ${t.judge.judged_score.toFixed(1)}`
                : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ScoreBreakdownView({ breakdown }: { breakdown: ArenaScoreBreakdown }) {
  const obj = breakdown.objective;
  const judge = breakdown.judge;
  const diagnosis = breakdown.diagnosis;
  // Drilldown grouping: "step" (chronological, the default) vs "dimension"
  // (checks regrouped under GRD/ADH/SYN/PRC so a user can read exactly where a
  // stat's points came from and where they were lost). Hook declared before the
  // early-return guard to satisfy the rules-of-hooks.
  const [checkView, setCheckView] = useState<'step' | 'dimension'>('step');
  // Multi-trial drilldown: 'avg' (the aggregate summary, default) or a trial index
  // (that trial's full breakdown). Hook before any early return (rules-of-hooks).
  const [trialTab, setTrialTab] = useState<'avg' | number>('avg');

  // A multi-trial aggregate with a validated aggregate card: show a tab per trial.
  // Each trial is itself a full breakdown with its own derived card, so a selected
  // trial recurses through this same renderer to get the complete card + per-check
  // view; 'avg' shows the summary.
  const trials = breakdown.aggregate;
  // Coverage guard (mirrors the backend): partial trial COVERAGE — a row declaring
  // more trials than were scored (retry / partial write) — or a MALFORMED trial
  // (null/primitive placeholder) must NOT render trial tabs (the recursive renderer
  // would crash on a non-object trial). Keyed on the backend's card_reason FIRST,
  // independent of the aggregate shape, since a partial write can also leave the
  // aggregate missing / empty / non-list.
  if (
    breakdown.card_reason === 'partial_trial_coverage' ||
    breakdown.card_reason === 'invalid_trial_shape'
  ) {
    const scored = Array.isArray(trials) ? trials.length : 0;
    return (
      <div className="wl-arena__breakdown">
        <div className="wl-arena__breakdown-head">
          <span className="wl-arena__transcript-title">Incomplete multi-trial run</span>
          <span className="wl-arena__breakdown-tally">
            {breakdown.n_trials != null
              ? `${scored}/${breakdown.n_trials} trials scored`
              : `${scored} trials`}
          </span>
        </div>
        <p className="wl-arena__diagnosis-analysis">
          Per-trial detail is withheld until this match is fully scored.
        </p>
      </div>
    );
  }
  if (Array.isArray(trials) && trials.length > 1) {
    // Clamp: a numeric tab left over from a longer aggregate (if state ever
    // outlives a match switch) falls back to the average rather than indexing
    // out of range. The drilldown also keys this component per match (below), so
    // this is defence-in-depth, not the primary reset.
    const activeTrial =
      typeof trialTab === 'number' && trialTab < trials.length ? trialTab : null;
    return (
      <div className="wl-arena__breakdown">
        <div className="wl-arena__trial-tabs" role="tablist" aria-label="Trials">
          <button
            type="button"
            role="tab"
            aria-selected={activeTrial === null}
            className={`wl-arena__trial-tab wl-arena__trial-tab--${
              activeTrial === null ? 'active' : 'idle'
            }`}
            onClick={() => setTrialTab('avg')}
          >
            Average
          </button>
          {trials.map((_, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={activeTrial === i}
              className={`wl-arena__trial-tab wl-arena__trial-tab--${
                activeTrial === i ? 'active' : 'idle'
              }`}
              onClick={() => setTrialTab(i)}
            >
              Trial {i + 1}
            </button>
          ))}
        </div>
        {activeTrial === null ? (
          <AggregateSummary breakdown={breakdown} />
        ) : (
          <ScoreBreakdownView breakdown={trials[activeTrial]} />
        )}
      </div>
    );
  }

  // Objective drives the detailed view (it is the sole ranking axis). Require the
  // FULL per-check shape (steps + success arrays), not just presence of `objective`:
  // aggregate/legacy/minimal rows may carry only headline+axes and would crash the
  // detailed renderer. A real jury-off row has full objective detail (no `judge`) and
  // renders in full with the subjective/jury sections simply omitted; a minimal row
  // degrades to the compact summary.
  if (!obj || !Array.isArray(obj.steps) || !Array.isArray(obj.success)) {
    return (
      <div className="wl-arena__breakdown">
        <div className="wl-arena__breakdown-head">
          <span className="wl-arena__transcript-title">Score breakdown</span>
          <span className="wl-arena__breakdown-tally">
            {breakdown.objective_score != null
              ? `Objective ${breakdown.objective_score.toFixed(1)}`
              : 'Objective n/a'}
            {breakdown.total_score != null
              ? ` · Total ${breakdown.total_score.toFixed(1)}`
              : ''}
            {breakdown.n_trials != null ? ` · ${breakdown.n_trials} trials` : ''}
          </span>
        </div>
        {breakdown.aggregate && breakdown.aggregate.length > 0 && (
          <div className="wl-arena__breakdown-step">
            <div className="wl-arena__breakdown-step-head">
              <span className="wl-arena__breakdown-step-title">Per-trial</span>
            </div>
            <ul className="wl-arena__check-list">
              {breakdown.aggregate.map((t, i) => (
                <li key={i} className="wl-arena__check-row">
                  <span className="wl-arena__check-detail">
                    Trial {i + 1}: objective {t.objective?.passed ?? '?'}/
                    {t.objective?.total ?? '?'}
                    {t.judge?.judged_score != null
                      ? ` · judge ${t.judge.judged_score.toFixed(1)}`
                      : ' · judge n/a'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="wl-arena__breakdown">
      <div className="wl-arena__breakdown-head">
        <span className="wl-arena__transcript-title">Score breakdown</span>
        <span className="wl-arena__breakdown-tally">
          Objective {obj.passed}/{obj.total}
          {judge &&
            (judge.judged_score != null && !judge.judge_missing
              ? ` · Subjective ${judge.judged_score.toFixed(1)}${
                  judge.judged_stdev != null ? ` ± ${judge.judged_stdev.toFixed(1)}` : ''
                } (adv.)`
              : ' · Subjective n/a')}
          {judge && breakdown.subjective_mode === 'self_consistency' && (
            <span className="wl-arena__degraded-chip" title="Single-judge fallback">degraded</span>
          )}
        </span>
      </div>

      {breakdown.card && <AbilityCardView card={breakdown.card} />}

      {obj.axes && (
        <div className="wl-arena__axes">
          {(['procedural', 'adherence', 'grounding', 'synthesis'] as const)
            .filter((k) => obj.axes && obj.axes[k])
            .map((k) => (
              <div key={k} className="wl-arena__axis-cell">
                <span className="wl-arena__axis-name">{k}</span>
                <span className="wl-arena__axis-tally">
                  {obj.axes![k].passed}/{obj.axes![k].total}
                </span>
              </div>
            ))}
        </div>
      )}

      {diagnosis && (diagnosis.counts || diagnosis.analysis) && (
        <div className="wl-arena__diagnosis">
          <span className="wl-arena__diagnosis-title">Diagnosis</span>
          {diagnosis.counts && (
            <div className="wl-arena__diagnosis-counts">{diagnosis.counts}</div>
          )}
          {diagnosis.analysis && (
            <p className="wl-arena__diagnosis-analysis">{diagnosis.analysis}</p>
          )}
        </div>
      )}

      <div className="wl-arena__view-toggle" role="group" aria-label="Group checks by">
        <button
          type="button"
          className={`wl-arena__view-btn wl-arena__view-btn--${
            checkView === 'step' ? 'active' : 'idle'
          }`}
          aria-pressed={checkView === 'step'}
          onClick={() => setCheckView('step')}
        >
          By step
        </button>
        <button
          type="button"
          className={`wl-arena__view-btn wl-arena__view-btn--${
            checkView === 'dimension' ? 'active' : 'idle'
          }`}
          aria-pressed={checkView === 'dimension'}
          onClick={() => setCheckView('dimension')}
        >
          By dimension
        </button>
      </div>

      {checkView === 'dimension' ? (
        <DimensionGroups steps={obj.steps} success={obj.success} />
      ) : (
        <>
          {obj.steps.map((step) => {
            const passed = step.checks.filter((c) => c.passed).length;
            return (
              <div key={step.index} className="wl-arena__breakdown-step">
                <div className="wl-arena__breakdown-step-head">
                  <span className="wl-arena__breakdown-step-title">
                    Step {step.index + 1}
                  </span>
                  <span className="wl-arena__breakdown-step-tally">
                    {passed}/{step.checks.length}
                  </span>
                </div>
                <div className="wl-arena__breakdown-step-user">{step.user}</div>
                <ul className="wl-arena__check-list">
                  {step.checks.map((c, i) => (
                    <CheckRow key={i} check={c} showAxis />
                  ))}
                </ul>
              </div>
            );
          })}

          {obj.success.length > 0 && (
            <div className="wl-arena__breakdown-step">
              <div className="wl-arena__breakdown-step-head">
                <span className="wl-arena__breakdown-step-title">Success criteria</span>
                <span className="wl-arena__breakdown-step-tally">
                  {obj.success.filter((c) => c.passed).length}/{obj.success.length}
                </span>
              </div>
              <ul className="wl-arena__check-list">
                {obj.success.map((c, i) => (
                  <CheckRow key={i} check={c} showAxis />
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {judge && judge.rubric_scores && judge.rubric_scores.length > 0 && (
        <div className="wl-arena__breakdown-step">
          <div className="wl-arena__breakdown-step-head">
            <span className="wl-arena__breakdown-step-title">Judge rubric</span>
          </div>
          <ul className="wl-arena__check-list">
            {judge.rubric_scores.map((r, i) => (
              <li key={i} className="wl-arena__check wl-arena__check--judge">
                <span className="wl-arena__check-score">{r.score}</span>
                <span className="wl-arena__check-label">{r.point}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {judge && judge.per_judge && judge.per_judge.length > 0 && (
        <div className="wl-arena__breakdown-step">
          <div className="wl-arena__breakdown-step-head">
            <span className="wl-arena__breakdown-step-title">Per-judge (jury)</span>
          </div>
          <ul className="wl-arena__check-list">
            {judge.per_judge.map((j, i) => (
              <li key={i} className="wl-arena__check wl-arena__check--judge">
                <span className="wl-arena__check-score">{j.judged_score.toFixed(1)}</span>
                <span className="wl-arena__check-label">{j.model}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function statusClass(status: string): string {
  if (status === 'completed') return 'wl-arena__status--completed';
  if (status === 'failed') return 'wl-arena__status--failed';
  if (status === 'running') return 'wl-arena__status--running';
  if (status === 'invalid') return 'wl-arena__status--invalid';
  return '';
}

function fmtScore(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(3);
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function modelDisplayName(modelId: string, models: ArenaModel[]): string {
  return models.find((m) => m.slug === modelId)?.display_name ?? modelId;
}

export function ArenaLive() {
  const [leaderboard, setLeaderboard] = useState<ArenaLeaderboardRow[]>([]);
  const [runs, setRuns] = useState<ArenaRunSummary[]>([]);
  const [models, setModels] = useState<ArenaModel[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [runDetail, setRunDetail] = useState<ArenaRunDetail | null>(null);
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);
  const [transcript, setTranscript] = useState<unknown | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [loadingTranscript, setLoadingTranscript] = useState(false);
  const [copiedTranscript, setCopiedTranscript] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<number>>(new Set());
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const copyTranscript = useCallback(async () => {
    if (transcript == null) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(transcript, null, 2));
      setCopiedTranscript(true);
      window.setTimeout(() => setCopiedTranscript(false), 1500);
    } catch {
      // ignore
    }
  }, [transcript]);

  const refresh = useCallback(() => {
    setError(null);
    Promise.all([
      listArenaRuns(),
      getArenaLeaderboard(selectedRunId ?? undefined),
      listArenaModels(),
    ])
      .then(([runsResp, lbResp, modelsResp]) => {
        setRuns(runsResp.runs);
        setLeaderboard(lbResp.rows);
        setModels(modelsResp.models);
      })
      .catch((e: unknown) => setError(String(e)));
  }, [selectedRunId]);

  useEffect(() => { refresh(); }, [refresh]);

  const selectRun = useCallback((runId: number) => {
    setSelectedRunId(runId);
    setRunDetail(null);
    setSelectedMatchId(null);
    setTranscript(null);
    setTranscriptError(null);
    getArenaRun(runId)
      .then(setRunDetail)
      .catch((e: unknown) => setError(String(e)));
  }, []);

  const selectMatch = useCallback((match: ArenaMatchSummary) => {
    setSelectedMatchId(match.id);
    setTranscript(null);
    setTranscriptError(null);
    // Aggregate (multi-trial) and older rows persist no single transcript —
    // skip the fetch that would 404 and show a plain note instead.
    if (match.transcript_path == null) {
      setLoadingTranscript(false);
      setTranscriptError('No transcript stored for this match (aggregated multi-trial or older run).');
      return;
    }
    setLoadingTranscript(true);
    getMatchTranscript(match.id)
      .then((t) => { setTranscript(t); })
      .catch((e: unknown) => { setTranscriptError(String(e)); })
      .finally(() => setLoadingTranscript(false));
  }, []);

  const toggleRunSelection = useCallback((runId: number) => {
    setSelectedRunIds((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  }, []);

  const clearRunSelection = useCallback(() => setSelectedRunIds(new Set()), []);

  const handleMergeRuns = useCallback(() => {
    const ids = Array.from(selectedRunIds);
    if (ids.length < 2) return;
    mergeArenaRuns(ids)
      .then((res) => {
        setSelectedRunIds(new Set());
        refresh();
        selectRun(res.run_id);
      })
      .catch((e: unknown) => setError(String(e)));
  }, [selectedRunIds, refresh, selectRun]);

  const handleDeleteRunsConfirmed = useCallback(() => {
    const ids = Array.from(selectedRunIds);
    deleteArenaRuns(ids)
      .then(() => {
        setDeleteConfirmOpen(false);
        setSelectedRunIds(new Set());
        if (selectedRunId != null && ids.includes(selectedRunId)) {
          setSelectedRunId(null);
          setRunDetail(null);
          setSelectedMatchId(null);
        }
        refresh();
      })
      .catch((e: unknown) => {
        setError(String(e));
        setDeleteConfirmOpen(false);
      });
  }, [selectedRunIds, selectedRunId, refresh]);

  const chips = [
    `${runs.length} run${runs.length === 1 ? '' : 's'}`,
    `${leaderboard.length} model${leaderboard.length === 1 ? '' : 's'}`,
  ];

  // Board-level predicate: the jury was intended for at least one displayed row
  // (a jury-on run, or a legacy row). A pure objective-only board (every row
  // "disabled") hides the Subjective column entirely; a mixed board keeps it so a
  // failed opt-in jury ("missing") never silently vanishes (spec D7).
  const juryIntended = useMemo(
    () =>
      leaderboard.some(
        (r) => r.subjective_mean != null || r.subjective_mode !== 'disabled',
      ),
    [leaderboard],
  );

  const leaderboardColumns: Column<ArenaLeaderboardRow>[] = useMemo(() => {
    const cols: Column<ArenaLeaderboardRow>[] = [
      {
        key: 'rank',
        header: 'Rank',
        numeric: true,
        width: 'max-content',
        render: (row) => <span className="wl-arena__rank">#{row.rank}</span>,
      },
      {
        key: 'model',
        header: 'Model',
        width: 'minmax(0, 2fr)',
        render: (row) => modelDisplayName(row.model_id, models),
      },
      {
        // Headline ranking axis — the numbers-first ability card OVR (spec B5).
        key: 'ovr',
        header: 'OVR',
        numeric: true,
        width: 'minmax(0, 1fr)',
        render: (row) =>
          row.ovr != null ? (
            <span className="wl-arena__ovr">{row.ovr}</span>
          ) : (
            <span className="wl-arena__subjective-na" title="Uncarded — no stored axes">—</span>
          ),
      },
      {
        // Objective mean, retained as a secondary column (no longer the sort key).
        key: 'avg_objective',
        header: 'Objective',
        numeric: true,
        width: 'minmax(0, 1fr)',
        render: (row) => fmtScore(row.avg_objective),
      },
    ];
    if (juryIntended) {
      cols.push({
        // Advisory only — jury mean ± stdev; never affects rank. Shown only on
        // boards where the jury was intended.
        key: 'subjective',
        header: 'Subjective (adv.)',
        numeric: true,
        width: 'minmax(0, 1.2fr)',
        render: (row) => {
          // A mean is shown whenever one exists — even if the aggregated mode is
          // "missing" (a partial outage: some matches scored, others lost the jury).
          // Never let a partial outage suppress a real advisory number; flag it with
          // a marker alongside instead. A row with no mean shows the outage marker
          // ("missing") or a blank cell (deliberately "disabled").
          return (
            <span className="wl-arena__subjective">
              {row.subjective_mean != null ? (
                <>
                  {row.subjective_mean.toFixed(1)}
                  {row.subjective_stdev != null && (
                    <span className="wl-arena__subjective-sd"> ± {row.subjective_stdev.toFixed(1)}</span>
                  )}
                </>
              ) : row.subjective_mode === 'missing' ? (
                <span className="wl-arena__subjective-na" title="Jury failed — all judges unavailable">—</span>
              ) : null}
              {row.subjective_mode === 'self_consistency' && (
                <span className="wl-arena__degraded-chip" title="Single-judge fallback — panel unavailable">degraded</span>
              )}
              {row.subjective_mean != null && row.subjective_mode === 'missing' && (
                <span className="wl-arena__degraded-chip" title="Some matches lost the jury (all judges failed)">partial</span>
              )}
            </span>
          );
        },
      });
    }
    cols.push({
      key: 'matches',
      header: 'Matches',
      numeric: true,
      width: 'minmax(0, 1fr)',
      render: (row) => (
        <span className="wl-arena__match-count">
          {row.matches}
          {(row.invalid ?? 0) > 0 && (
            <span className="wl-arena__invalid-chip">{row.invalid} infra</span>
          )}
        </span>
      ),
    });
    return cols;
  }, [models, juryIntended]);

  return (
    <PageScaffold
      title="ARENA"
      chips={chips}
      actions={<Button variant="ghost" onClick={refresh}>Refresh</Button>}
      feedback={error && (
        <div role="alert" style={{ color: 'var(--neg)' }}>
          {error}
        </div>
      )}
    >
      <div className="wl-arena__workspace">
        {/* Leaderboard */}
        <div className="wl-arena__panel wl-arena__panel--leaderboard">
          <div className="wl-arena__section-head">
            <span className="wl-arena__eyebrow">
              Leaderboard{selectedRunId != null ? ` — run ${String(selectedRunId).slice(0, 8)}` : ' — all runs'}
            </span>
          </div>
          {leaderboard.length === 0 ? (
            <Empty message="No leaderboard data yet — run an arena evaluation to populate scores." />
          ) : (
            <Table columns={leaderboardColumns} rows={leaderboard} rowKey={(r) => r.model_id} />
          )}
        </div>

        {/* Run picker + detail */}
        <div className="wl-arena__two-col">
          <div className="wl-arena__panel">
            <div className="wl-arena__section-head">
              <span className="wl-arena__eyebrow">Runs</span>
            </div>
            {selectedRunIds.size > 0 && (
              <div className="wl-arena__run-actions">
                <span className="wl-arena__run-actions-count">
                  {selectedRunIds.size} selected
                </span>
                <Button
                  variant="default"
                  disabled={selectedRunIds.size < 2}
                  onClick={handleMergeRuns}
                >
                  Merge ({selectedRunIds.size})
                </Button>
                <Button variant="danger" onClick={() => setDeleteConfirmOpen(true)}>
                  Delete ({selectedRunIds.size})
                </Button>
                <Button variant="ghost" onClick={clearRunSelection}>
                  Clear
                </Button>
              </div>
            )}
            {runs.length === 0 ? (
              <Empty message="No arena runs yet." />
            ) : (
              <div className="wl-arena__run-list">
                {runs.map((run) => (
                  <div
                    key={run.id}
                    className={`wl-arena__run-item${run.id === selectedRunId ? ' is-active' : ''}`}
                  >
                    <input
                      type="checkbox"
                      className="wl-arena__run-checkbox"
                      aria-label={`Select run ${run.id}`}
                      checked={selectedRunIds.has(run.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => toggleRunSelection(run.id)}
                    />
                    <button
                      type="button"
                      className="wl-arena__run-item-body"
                      onClick={() => selectRun(run.id)}
                    >
                      <span className="wl-arena__run-id">{String(run.id).slice(0, 8)}</span>
                      <span className={`wl-arena__status ${statusClass(run.status)}`}>
                        {run.status}
                      </span>
                      <span className="wl-arena__run-meta">{fmtDate(run.created_at)}</span>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Run detail: match grid */}
          <div>
            {runDetail ? (
              <div className="wl-arena__panel">
                <div className="wl-arena__section-head">
                  <span className="wl-arena__eyebrow">
                    Matches — run {selectedRunId != null ? String(selectedRunId).slice(0, 8) : ''}
                  </span>
                  <span className={`wl-arena__status ${statusClass(runDetail.run.status)}`}>
                    {runDetail.run.status}
                  </span>
                </div>
                {runDetail.matches.length === 0 ? (
                  <Empty message="No matches in this run." />
                ) : (
                  <div
                    className="wl-arena__match-grid"
                    style={{
                      gridTemplateColumns: `repeat(auto-fill, minmax(200px, 1fr))`,
                    }}
                  >
                    {runDetail.matches.map((match) => (
                      <button
                        key={match.id}
                        type="button"
                        className={`wl-arena__match-cell${match.id === selectedMatchId ? ' is-active' : ''}`}
                        onClick={() => selectMatch(match)}
                      >
                        <span className="wl-arena__match-title">
                          {modelDisplayName(match.model_id, models)}
                        </span>
                        <span className="wl-arena__match-title" style={{ fontWeight: 'normal', color: 'var(--ink-2)' }}>
                          {match.workflow_id}
                        </span>
                        <span className={`wl-arena__status ${statusClass(match.status)}`}>
                          {match.status}
                        </span>
                        {match.status === 'invalid' && match.error && (
                          <span className="wl-arena__match-invalid-reason">
                            {match.error}
                          </span>
                        )}
                        {match.score_breakdown?.card ? (
                          <div className="wl-arena__match-ability">
                            <div className="wl-arena__card-ovr">
                              <span className="wl-arena__card-ovr-value">
                                {match.score_breakdown.card.ovr}
                              </span>
                              <span className="wl-arena__card-ovr-label">OVR</span>
                            </div>
                            <AbilityHexagon card={match.score_breakdown.card} />
                          </div>
                        ) : (
                          // Uncarded rows (legacy runs, no ability card) keep the
                          // objective score so the cell still reports a number.
                          match.objective_score != null && (
                            <span className="wl-arena__match-score">
                              Obj: {fmtScore(match.objective_score)}
                            </span>
                          )
                        )}
                        {match.score_breakdown?.diagnosis?.analysis && (
                          <span className="wl-arena__match-diagnosis">
                            {match.score_breakdown.diagnosis.analysis}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : selectedRunId ? (
              <div className="wl-arena__panel" style={{ padding: 'var(--gap-3)' }}>
                <span style={{ color: 'var(--ink-2)', fontSize: 'var(--type-small-size)' }}>
                  Loading run detail…
                </span>
              </div>
            ) : null}

            {/* Match drill-down: score breakdown + transcript */}
            {selectedMatchId && (
              <div className="wl-arena__transcript" style={{ marginTop: 'var(--gap-3)' }}>
                <div className="wl-arena__transcript-head">
                  <span className="wl-arena__transcript-title">Match detail</span>
                  <Button variant="ghost" onClick={() => { setSelectedMatchId(null); setTranscript(null); }}>
                    Close
                  </Button>
                </div>
                {(() => {
                  const selectedMatch = runDetail?.matches.find((m) => m.id === selectedMatchId);
                  return selectedMatch?.score_breakdown ? (
                    // key per match → trial-tab / check-view state resets on switch,
                    // never carrying a stale trial index into a different match.
                    <ScoreBreakdownView key={selectedMatch.id} breakdown={selectedMatch.score_breakdown} />
                  ) : selectedMatch ? (
                    <span style={{ color: 'var(--ink-2)', fontSize: 'var(--type-small-size)' }}>
                      No score breakdown for this match (older run or failed match).
                    </span>
                  ) : null;
                })()}
                <div className="wl-arena__transcript-head" style={{ marginTop: 'var(--gap-3)' }}>
                  <span className="wl-arena__transcript-title">Transcript</span>
                  {transcript != null && !loadingTranscript && (
                    <Button
                      variant="ghost"
                      iconOnly
                      className="wl-arena__transcript-copy"
                      onClick={copyTranscript}
                      aria-label={copiedTranscript ? 'Copied' : 'Copy transcript'}
                      title={copiedTranscript ? 'Copied' : 'Copy transcript'}
                    >
                      {copiedTranscript ? <Check size={16} /> : <Copy size={16} />}
                    </Button>
                  )}
                </div>
                {loadingTranscript && (
                  <span style={{ color: 'var(--ink-2)', fontSize: 'var(--type-small-size)' }}>
                    Loading transcript…
                  </span>
                )}
                {transcriptError && (
                  <div role="alert" style={{ color: 'var(--neg)', fontSize: 'var(--type-small-size)' }}>
                    {transcriptError}
                  </div>
                )}
                {transcript != null && !loadingTranscript && (
                  <pre className="wl-arena__transcript-body">
                    {JSON.stringify(transcript, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <Modal
        open={deleteConfirmOpen}
        onOpenChange={(open) => { if (!open) setDeleteConfirmOpen(false); }}
        title="DELETE ARENA RUNS"
        layoutKey="arena-run-delete"
      >
        <div className="wl-arena__delete-confirm">
          <p className="wl-arena__delete-confirm-body">
            Delete run{selectedRunIds.size === 1 ? '' : 's'}{' '}
            <strong>{Array.from(selectedRunIds).join(', ')}</strong>? This removes matches and
            transcript files for the selected runs. This action cannot be undone.
          </p>
          <div className="wl-arena__delete-confirm-actions">
            <Button variant="ghost" onClick={() => setDeleteConfirmOpen(false)}>Cancel</Button>
            <Button variant="danger" onClick={handleDeleteRunsConfirmed}>Delete</Button>
          </div>
        </div>
      </Modal>
    </PageScaffold>
  );
}

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { PageScaffold } from '../components/templates/PageScaffold';
import { Table, type Column } from '../components/Table';
import {
  getArenaLeaderboard,
  getArenaRun,
  getMatchTranscript,
  listArenaModels,
  listArenaRuns,
  type ArenaLeaderboardRow,
  type ArenaMatchSummary,
  type ArenaModel,
  type ArenaRunDetail,
  type ArenaRunSummary,
  type ArenaScoreBreakdown,
  type ArenaCheck,
} from '../lib/arenaApi';
import './Arena.css';

function CheckRow({ check }: { check: ArenaCheck }) {
  return (
    <li className={`wl-arena__check wl-arena__check--${check.passed ? 'pass' : 'fail'}`}>
      <span className="wl-arena__check-mark" aria-hidden="true">
        {check.passed ? '✓' : '✗'}
      </span>
      <span className="wl-arena__check-label">{check.label}</span>
      {!check.passed && check.detail && (
        <span className="wl-arena__check-detail">{check.detail}</span>
      )}
    </li>
  );
}

function ScoreBreakdownView({ breakdown }: { breakdown: ArenaScoreBreakdown }) {
  const obj = breakdown.objective;
  const judge = breakdown.judge;
  const diagnosis = breakdown.diagnosis;
  return (
    <div className="wl-arena__breakdown">
      <div className="wl-arena__breakdown-head">
        <span className="wl-arena__transcript-title">Score breakdown</span>
        <span className="wl-arena__breakdown-tally">
          Objective {obj.passed}/{obj.total}
          {judge.judged_score != null && !judge.judge_missing
            ? ` · Judge ${judge.judged_score.toFixed(1)}`
            : ' · Judge n/a'}
        </span>
      </div>

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
                <CheckRow key={i} check={c} />
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
              <CheckRow key={i} check={c} />
            ))}
          </ul>
        </div>
      )}

      {judge.rubric_scores.length > 0 && (
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
  const [error, setError] = useState<string | null>(null);

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
    setLoadingTranscript(true);
    getMatchTranscript(match.id)
      .then((t) => { setTranscript(t); })
      .catch((e: unknown) => { setTranscriptError(String(e)); })
      .finally(() => setLoadingTranscript(false));
  }, []);

  const chips = [
    `${runs.length} run${runs.length === 1 ? '' : 's'}`,
    `${leaderboard.length} model${leaderboard.length === 1 ? '' : 's'}`,
  ];

  const leaderboardColumns: Column<ArenaLeaderboardRow>[] = useMemo(
    () => [
      {
        key: 'model',
        header: 'Model',
        width: 'minmax(0, 2fr)',
        render: (row) => modelDisplayName(row.model_id, models),
      },
      {
        key: 'avg_total',
        header: 'Avg Total',
        numeric: true,
        width: 'minmax(0, 1fr)',
        render: (row) => fmtScore(row.avg_total),
      },
      {
        key: 'avg_objective',
        header: 'Avg Objective',
        numeric: true,
        width: 'minmax(0, 1fr)',
        render: (row) => fmtScore(row.avg_objective),
      },
      {
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
      },
    ],
    [models],
  );

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
            {runs.length === 0 ? (
              <Empty message="No arena runs yet." />
            ) : (
              <div className="wl-arena__run-list">
                {runs.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    className={`wl-arena__run-item${run.id === selectedRunId ? ' is-active' : ''}`}
                    onClick={() => selectRun(run.id)}
                  >
                    <span className="wl-arena__run-id">{String(run.id).slice(0, 8)}</span>
                    <span className={`wl-arena__status ${statusClass(run.status)}`}>
                      {run.status}
                    </span>
                    <span className="wl-arena__run-meta">{fmtDate(run.created_at)}</span>
                  </button>
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
                        <span className="wl-arena__match-score">
                          Total: {fmtScore(match.total_score)}
                          {' · '}
                          Obj: {fmtScore(match.objective_score)}
                        </span>
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
                    <ScoreBreakdownView breakdown={selectedMatch.score_breakdown} />
                  ) : selectedMatch ? (
                    <span style={{ color: 'var(--ink-2)', fontSize: 'var(--type-small-size)' }}>
                      No score breakdown for this match (older run or failed match).
                    </span>
                  ) : null;
                })()}
                <div className="wl-arena__transcript-head" style={{ marginTop: 'var(--gap-3)' }}>
                  <span className="wl-arena__transcript-title">Transcript</span>
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
    </PageScaffold>
  );
}

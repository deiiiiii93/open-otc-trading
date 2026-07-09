import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ArenaLive } from './Arena.live';

// Mock the arenaApi module
vi.mock('../lib/arenaApi', () => ({
  listArenaRuns: vi.fn(),
  getArenaRun: vi.fn(),
  getArenaLeaderboard: vi.fn(),
  getMatchTranscript: vi.fn(),
  listArenaModels: vi.fn(),
  createArenaRun: vi.fn(),
  deleteArenaRuns: vi.fn(),
  mergeArenaRuns: vi.fn(),
}));

import * as arenaApi from '../lib/arenaApi';

const mockModels = [
  { slug: 'claude-sonnet', zenmux_name: 'claude-sonnet', display_name: 'Claude Sonnet' },
  { slug: 'gpt-4o', zenmux_name: 'gpt-4o', display_name: 'GPT-4o' },
];

const mockRuns = [
  {
    id: 1,
    status: 'completed',
    created_at: '2026-06-24T10:00:00Z',
    workflow_ids: ['workflow-a'],
    model_ids: ['claude-sonnet'],
  },
];

const mockLeaderboard = [
  { model_id: 'claude-sonnet', rank: 1, ovr: 82,
    card_mean: { ovr: 82, GRD: 90, ADH: 80, SYN: 88, EFF: 75, PRC: 70 },
    avg_objective: 0.9, subjective_mean: 0.6,
    subjective_stdev: 0.2, subjective_mode: 'panel', matches: 3, invalid: 1 },
  { model_id: 'gpt-4o', rank: 2, ovr: 71,
    card_mean: { ovr: 71, GRD: 70, ADH: 72, SYN: 68, EFF: 74, PRC: 66 },
    avg_objective: 0.75, subjective_mean: 0.8,
    subjective_stdev: 0.3, subjective_mode: 'self_consistency', matches: 3, invalid: 0 },
];

const mockMatches = [
  {
    id: 101,
    workflow_id: 'workflow-a',
    model_id: 'claude-sonnet',
    status: 'completed',
    objective_score: 0.9,
    judged_score: 0.8,
    total_score: 0.85,
    judge_missing: false,
    transcript_path: 'artifacts/arena/run-1/claude-sonnet/workflow-a/transcript.json',
    score_breakdown: {
      objective: {
        passed: 1,
        total: 2,
        steps: [
          {
            index: 0,
            user: 'Check the latest risk',
            checks: [
              { kind: 'skill', label: 'skill: read-risk-result', passed: true, detail: '', axis: 'procedural' },
              { kind: 'tool', label: 'tool: get_latest_risk_run', passed: false, detail: 'not called', axis: 'procedural' },
            ],
          },
        ],
        success: [],
        axes: {
          procedural: { passed: 20, total: 22 },
          adherence: { passed: 6, total: 8 },
          grounding: { passed: 4, total: 5 },
          synthesis: { passed: 4, total: 4 },
        },
      },
      judge: { rubric_scores: [{ point: 'Synthesis coherence', score: 80 }], judged_score: 80, judge_missing: false,
        judged_stdev: 6.5, per_judge: [
          { model: 'deepseek-v4-pro', judged_score: 74 },
          { model: 'qwen/qwen3.7-max', judged_score: 86 }] },
      subjective_mode: 'panel',
      diagnosis: {
        counts: '1/7 expected skills · 0 tool calls · 1/2 checks',
        analysis: 'Over-caution — asked for the portfolio instead of resolving the named profile.',
        counts_detail: { skills_hit: 1, tool_calls: 0, checks_passed: 1, checks_total: 2 },
      },
      weights: { obj: 0.5, judge: 0.5 },
      objective_score: 50,
      total_score: 65,
      // Single-match model → CON unmeasurable (null → greyed), OVR at its base.
      card: { ovr: 82, base_ovr: 82, stats: { GRD: 90, ADH: 80, SYN: 88, PRC: 70, EFF: 75 },
        jdg: null, con: null, position: 'Sniper' },
    },
  },
  {
    id: 102,
    workflow_id: 'workflow-b',
    model_id: 'gpt-4o',
    status: 'invalid',
    objective_score: null,
    judged_score: null,
    total_score: null,
    judge_missing: true,
    transcript_path: null,
    score_breakdown: null,
    error: 'infra_blank',
  },
];

const mockTranscript = { messages: [{ role: 'user', content: 'Run workflow' }] };

function setupMocks() {
  vi.mocked(arenaApi.listArenaRuns).mockResolvedValue({ runs: mockRuns, total: 1 });
  vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({ rows: mockLeaderboard });
  vi.mocked(arenaApi.listArenaModels).mockResolvedValue({ models: mockModels });
  vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
    run: mockRuns[0],
    matches: mockMatches,
  });
  vi.mocked(arenaApi.getMatchTranscript).mockResolvedValue(mockTranscript);
}

afterEach(() => { vi.clearAllMocks(); });

describe('ArenaLive', () => {
  it('renders leaderboard rows with display names and scores from mocked data', async () => {
    setupMocks();
    render(<ArenaLive />);

    // Leaderboard table should appear with display names
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument();
    expect(await screen.findByText('GPT-4o')).toBeInTheDocument();

    // Objective (the ranked axis) is formatted; the blended total is gone
    expect(screen.getByText('0.900')).toBeInTheDocument();
    expect(screen.getByText('0.750')).toBeInTheDocument();
    // gpt-4o's subjective came from a single-judge fallback → degraded badge
    expect(screen.getByText('degraded')).toBeInTheDocument();

    // Initial load requests the global leaderboard
    expect(arenaApi.getArenaLeaderboard).toHaveBeenCalledWith(undefined);
  });

  it('renders the runs list', async () => {
    setupMocks();
    render(<ArenaLive />);

    // Run ID rendered as String(1).slice(0,8) === '1'
    expect(await screen.findByText('1')).toBeInTheDocument();
  });

  it('clicking a run loads run detail and shows match grid', async () => {
    setupMocks();
    render(<ArenaLive />);

    const runButton = await screen.findByText('1');
    await userEvent.click(runButton);

    // Match cell should appear with workflow and model info
    expect(await screen.findByText('workflow-a')).toBeInTheDocument();
    // Carded matches lead with the OVR headline (Total/Obj text retired).
    expect((await screen.findAllByText('OVR')).length).toBeGreaterThan(0);
    expect(arenaApi.getArenaRun).toHaveBeenCalledWith(1);
    expect(arenaApi.getArenaLeaderboard).toHaveBeenCalledWith(1);
  });

  it('shows OVR and the ability radar on a carded match cell (and neither on an uncarded one)', async () => {
    setupMocks();
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));

    // The carded match (#101, OVR 82) surfaces the OVR headline...
    const abilityRow = (await screen.findByText('workflow-a'))
      .closest('.wl-arena__match-cell')!
      .querySelector('.wl-arena__match-ability') as HTMLElement;
    expect(abilityRow).toBeTruthy();
    expect(within(abilityRow).getByText('OVR')).toBeInTheDocument();
    expect(within(abilityRow).getByText('82')).toBeInTheDocument();
    // ...and a radar drawn as an SVG with the six axis labels (CON replaces JDG).
    const radar = abilityRow.querySelector('.wl-arena__hex') as SVGElement;
    expect(radar).toBeTruthy();
    for (const stat of ['GRD', 'ADH', 'SYN', 'EFF', 'PRC', 'CON']) {
      expect(within(abilityRow).getByText(stat)).toBeInTheDocument();
    }

    // The invalid match (#102, score_breakdown null) shows no card.
    const invalidCell = (await screen.findByText('workflow-b'))
      .closest('.wl-arena__match-cell') as HTMLElement;
    expect(invalidCell.querySelector('.wl-arena__match-ability')).toBeNull();
  });

  it('clicking a match fetches the transcript and renders transcript content', async () => {
    setupMocks();
    render(<ArenaLive />);

    // First, click the run
    const runButton = await screen.findByText('1');
    await userEvent.click(runButton);

    // Wait for match cell
    const matchButton = await screen.findByText('workflow-a');
    await userEvent.click(matchButton);

    // Transcript should be loaded and shown
    await waitFor(() => {
      expect(arenaApi.getMatchTranscript).toHaveBeenCalledWith(101);
    });

    // The transcript JSON content should appear
    expect(await screen.findByText(/Run workflow/)).toBeInTheDocument();
  });

  it('clicking a match shows the per-check score breakdown', async () => {
    setupMocks();
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));

    // Breakdown header + aggregate tally
    expect(await screen.findByText('Score breakdown')).toBeInTheDocument();
    expect(screen.getByText(/Objective 1\/2/)).toBeInTheDocument();
    // A passed check and a failed check with its detail
    expect(screen.getByText('skill: read-risk-result')).toBeInTheDocument();
    expect(screen.getByText('tool: get_latest_risk_run')).toBeInTheDocument();
    expect(screen.getByText('not called')).toBeInTheDocument();
    // Judge rubric point surfaces
    expect(screen.getByText('Synthesis coherence')).toBeInTheDocument();
    // Subjective is advisory with dispersion, and per-judge jury detail renders
    expect(screen.getByText(/Subjective 80\.0 ± 6\.5 \(adv\.\)/)).toBeInTheDocument();
    expect(screen.getByText('Per-judge (jury)')).toBeInTheDocument();
    expect(screen.getByText('deepseek-v4-pro')).toBeInTheDocument();
    expect(screen.getByText('qwen/qwen3.7-max')).toBeInTheDocument();
  });

  it('toggles the score breakdown between By step and By dimension', async () => {
    setupMocks();
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));
    await screen.findByText('Score breakdown');

    // Default = By step: the per-step header shows.
    expect(screen.getByText('Step 1')).toBeInTheDocument();

    // Switch to By dimension: checks regroup under their axis, with the derived
    // card stat (procedural 1 pass + 1 fail → PRC = round(99 × 1/2) = 50).
    await userEvent.click(screen.getByRole('button', { name: 'By dimension' }));
    expect(screen.getByText(/PRC\s+50/)).toBeInTheDocument();
    // The failing check and its reason still surface, now under the dimension.
    expect(screen.getByText('tool: get_latest_risk_run')).toBeInTheDocument();
    expect(screen.getByText('not called')).toBeInTheDocument();
    // Step grouping is replaced (no per-step header in the dimension view).
    expect(screen.queryByText('Step 1')).not.toBeInTheDocument();

    // Toggling back restores the step view.
    await userEvent.click(screen.getByRole('button', { name: 'By step' }));
    expect(screen.getByText('Step 1')).toBeInTheDocument();
  });

  it('renders the OVR headline on the leaderboard', async () => {
    setupMocks();
    render(<ArenaLive />);
    // OVR is the headline ranking column (spec B5).
    expect(await screen.findByText('OVR')).toBeInTheDocument();
    expect(screen.getByText('82')).toBeInTheDocument();
    expect(screen.getByText('71')).toBeInTheDocument();
  });

  it('renders the ability card (OVR + six stats, CON greyed when single-match)', async () => {
    setupMocks();
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));
    await screen.findByText('Score breakdown');

    // Card position badge + all six stat labels present. Scope to the card:
    // the same stat codes (e.g. PRC) now also appear as per-check axis chips in
    // the By-step check list, so an unscoped getByText would be ambiguous.
    expect(screen.getByText('Sniper')).toBeInTheDocument();
    const card = screen.getByText('Sniper').closest('.wl-arena__card') as HTMLElement;
    for (const stat of ['GRD', 'ADH', 'SYN', 'PRC', 'EFF', 'CON']) {
      expect(within(card).getByText(stat)).toBeInTheDocument();
    }
    // CON is null (single-match run) → muted, renders an em dash under CON.
    const conName = within(card).getByText('CON');
    const conCell = conName.closest('.wl-arena__stat');
    expect(conCell?.className).toContain('wl-arena__stat--muted');
    expect(conCell?.querySelector('.wl-arena__stat-value')?.textContent).toBe('—');
  });

  it('renders a measured CON as a first-class (non-muted) stat', async () => {
    setupMocks();
    // Override the run with a card whose CON is measured (a multi-match model).
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
      run: mockRuns[0],
      matches: [{
        ...mockMatches[0],
        score_breakdown: {
          ...mockMatches[0].score_breakdown!,
          card: { ...mockMatches[0].score_breakdown!.card!, con: 60, base_ovr: 87 },
        },
      }],
    });
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));
    await screen.findByText('Score breakdown');

    const card = screen.getByText('Sniper').closest('.wl-arena__card') as HTMLElement;
    const conCell = within(card).getByText('CON').closest('.wl-arena__stat');
    expect(conCell?.className).not.toContain('wl-arena__stat--muted');
    expect(conCell?.querySelector('.wl-arena__stat-value')?.textContent).toBe('60');
  });

  it('renders per-trial tabs for a multi-trial aggregate match', async () => {
    setupMocks();
    const base = mockMatches[0].score_breakdown!;
    const trialA = { ...base, objective_score: 84.6,
      card: { ovr: 82, base_ovr: 82, stats: { GRD: 90, ADH: 80, SYN: 88, PRC: 70, EFF: 75 },
        jdg: null, con: null, position: 'Sniper' } };
    const trialB = { ...base, objective_score: 48.7,
      card: { ovr: 40, base_ovr: 40, stats: { GRD: 40, ADH: 60, SYN: 0, PRC: 54, EFF: 16 },
        jdg: null, con: null, position: 'Anchor' } };
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
      run: mockRuns[0],
      matches: [{
        ...mockMatches[0],
        score_breakdown: {
          objective_score: 66.7, objective_stdev: 18.0, n_trials: 2,
          // Aggregate card: averaged stats + trial-dispersion CON (20).
          card: { ovr: 50, base_ovr: 61, stats: { GRD: 65, ADH: 70, SYN: 44, PRC: 62, EFF: 46 },
            jdg: null, con: 20, position: 'All-rounder' },
          aggregate: [trialA, trialB],
        },
      }],
    });
    render(<ArenaLive />);
    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));

    // Tab bar: Average + one tab per trial.
    expect(await screen.findByRole('tab', { name: 'Average' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Trial 1' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Trial 2' })).toBeInTheDocument();
    // Average tab (default): honest summary + per-trial roster with each trial's OVR.
    expect(screen.getByText(/Average of 2 trials/)).toBeInTheDocument();
    expect(screen.getByText(/Trial 1: OVR 82/)).toBeInTheDocument();
    expect(screen.getByText(/Trial 2: OVR 40/)).toBeInTheDocument();

    // Switch to Trial 2 → its own full breakdown (the aggregate summary is gone).
    await userEvent.click(screen.getByRole('tab', { name: 'Trial 2' }));
    expect(screen.queryByText(/Average of 2 trials/)).not.toBeInTheDocument();
    // Trial 2's own card (position Anchor) renders in the recursive detailed view.
    expect(screen.getByText('Anchor')).toBeInTheDocument();
  });

  it.each([
    ['short 2/3', [{}, {}], '2/3 trials scored'],
    ['missing aggregate', undefined, '0/3 trials scored'],
    ['empty aggregate', [], '0/3 trials scored'],
  ])('shows an incomplete state (no trial tabs) for a partial aggregate — %s',
    async (_label, aggregate, tally) => {
    setupMocks();
    const base = mockMatches[0].score_breakdown!;
    // Backend refused the aggregate card (partial coverage) and withheld per-trial
    // cards; the drilldown must not render scored trial tabs for the remnant — even
    // when the aggregate is missing/empty, keyed on card_reason not the array shape.
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
      run: mockRuns[0],
      matches: [{
        ...mockMatches[0],
        score_breakdown: {
          n_trials: 3,
          card: null,
          card_reason: 'partial_trial_coverage',
          ...(aggregate ? { aggregate: aggregate.map(() => ({ ...base })) } : {}),
        },
      }],
    });
    render(<ArenaLive />);
    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));

    expect(await screen.findByText('Incomplete multi-trial run')).toBeInTheDocument();
    expect(screen.getByText(tally)).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Trial 1' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Average' })).not.toBeInTheDocument();
  });

  it('resets the trial tab when switching to a shorter aggregate match', async () => {
    setupMocks();
    const base = mockMatches[0].score_breakdown!;
    const mkCard = (ovr: number) => ({ ovr, base_ovr: ovr,
      stats: { GRD: ovr, ADH: ovr, SYN: ovr, PRC: ovr, EFF: ovr },
      jdg: null, con: null, position: 'Sniper' });
    const mkAgg = (n: number) => ({
      objective_score: 60, n_trials: n, con: 30,
      card: { ...mkCard(60), con: 30 },
      aggregate: Array.from({ length: n }, (_, i) => ({ ...base, card: mkCard(50 + i) })),
    });
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
      run: mockRuns[0],
      matches: [
        { ...mockMatches[0], id: 301, workflow_id: 'wf-long', score_breakdown: mkAgg(3) },
        { ...mockMatches[0], id: 302, workflow_id: 'wf-short', score_breakdown: mkAgg(2) },
      ],
    });
    render(<ArenaLive />);
    await userEvent.click(await screen.findByText('1'));

    // Open the 3-trial match and jump to Trial 3.
    await userEvent.click(await screen.findByText('wf-long'));
    await userEvent.click(await screen.findByRole('tab', { name: 'Trial 3' }));

    // Switch to the 2-trial match: must not crash, must reset to Average, and must
    // NOT expose a Trial 3 tab (the stale index is gone).
    await userEvent.click(screen.getByText('wf-short'));
    const avg = await screen.findByRole('tab', { name: 'Average' });
    expect(avg).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: 'Trial 3' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Trial 2' })).toBeInTheDocument();
  });

  it('clicking a match shows the diagnosis (counts + analysis)', async () => {
    setupMocks();
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));

    expect(await screen.findByText('Diagnosis')).toBeInTheDocument();
    expect(
      screen.getByText('1/7 expected skills · 0 tool calls · 1/2 checks'),
    ).toBeInTheDocument();
    // The analysis narrative appears (match cell + drilldown both render it)
    expect(
      screen.getAllByText(/Over-caution — asked for the portfolio/).length,
    ).toBeGreaterThan(0);
  });

  it('keeps the subjective mean visible on a partial outage (mean + missing marker)', async () => {
    setupMocks();
    vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({
      rows: [
        { model_id: 'claude-sonnet', rank: 1, avg_objective: 0.9, subjective_mean: 0.6,
          subjective_stdev: 0.2, subjective_mode: 'missing', matches: 2, invalid: 0 },
      ],
    });
    render(<ArenaLive />);
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument();
    // The real advisory mean must NOT be suppressed by the partial-outage mode…
    expect(screen.getByText('0.6')).toBeInTheDocument();
    // …and the outage is still flagged alongside it.
    expect(screen.getByText('partial')).toBeInTheDocument();
  });

  it('hides the Subjective column when every leaderboard row is jury-disabled', async () => {
    setupMocks();
    vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({
      rows: [
        { model_id: 'claude-sonnet', rank: 1, avg_objective: 0.9, subjective_mean: null,
          subjective_mode: 'disabled', matches: 3, invalid: 0 },
        { model_id: 'gpt-4o', rank: 2, avg_objective: 0.75, subjective_mean: null,
          subjective_mode: 'disabled', matches: 3, invalid: 0 },
      ],
    });
    render(<ArenaLive />);
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument();
    // Objective-only board → no Subjective column header.
    expect(screen.queryByText(/Subjective/)).not.toBeInTheDocument();
  });

  it('shows the Subjective column on a mixed board (jury-on + disabled rows)', async () => {
    setupMocks();
    vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({
      rows: [
        { model_id: 'claude-sonnet', rank: 1, avg_objective: 0.9, subjective_mean: 0.55,
          subjective_stdev: 0.1, subjective_mode: 'panel', matches: 3, invalid: 0 },
        { model_id: 'gpt-4o', rank: 2, avg_objective: 0.75, subjective_mean: null,
          subjective_mode: 'disabled', matches: 3, invalid: 0 },
      ],
    });
    render(<ArenaLive />);
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument();
    // Any jury-intended row keeps the column visible so it never silently vanishes.
    expect(screen.getByText(/Subjective/)).toBeInTheDocument();
  });

  it('degrades to the compact breakdown for a minimal objective (no steps/success) without crashing', async () => {
    setupMocks();
    // A legacy/aggregate objective-only row carrying only headline + axes (the shape
    // the store emits for jury-off aggregate means) must NOT enter the detailed
    // renderer (which maps obj.steps / reads obj.success.length) and crash.
    const minimalMatch = {
      ...mockMatches[0], id: 202, judged_score: null, judge_missing: false,
      score_breakdown: { objective: { axes: {} }, subjective_mode: 'disabled',
        objective_score: 71.8, total_score: 71.8, n_trials: 3 },
    };
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({ run: mockRuns[0], matches: [minimalMatch] });
    render(<ArenaLive />);
    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));
    // Compact summary renders (headline) rather than throwing.
    expect(await screen.findByText('Score breakdown')).toBeInTheDocument();
    expect(screen.getByText(/Objective 71\.8/)).toBeInTheDocument();
    expect(screen.queryByText('Per-judge (jury)')).not.toBeInTheDocument();
  });

  it('renders objective drilldown for a jury-off match (no judge block, no per-judge)', async () => {
    setupMocks();
    const objBreakdown = { ...mockMatches[0].score_breakdown, subjective_mode: 'disabled' };
    delete (objBreakdown as { judge?: unknown }).judge;
    const objOnlyMatch = {
      ...mockMatches[0], id: 201, judged_score: null, judge_missing: true,
      score_breakdown: objBreakdown,
    };
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({ run: mockRuns[0], matches: [objOnlyMatch] });
    render(<ArenaLive />);
    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));
    // Detailed objective view (NOT the compact fallback): tally + step checks render.
    expect(await screen.findByText('Score breakdown')).toBeInTheDocument();
    expect(screen.getByText(/Objective 1\/2/)).toBeInTheDocument();
    expect(screen.getByText('skill: read-risk-result')).toBeInTheDocument();
    expect(screen.getByText('tool: get_latest_risk_run')).toBeInTheDocument();
    // No subjective/jury sections when the jury did not run.
    expect(screen.queryByText('Per-judge (jury)')).not.toBeInTheDocument();
    expect(screen.queryByText(/Subjective 80/)).not.toBeInTheDocument();
  });

  it('renders aggregate-shaped breakdown (no per-check objective) without crashing', async () => {
    // Regression: multi-trial averaged rows (run #10) carry only headline
    // scores + `aggregate`, with NO top-level `objective`/`judge`. The drilldown
    // must degrade to a summary, not throw on `objective.passed`.
    setupMocks();
    const aggregateMatch = {
      ...mockMatches[0],
      id: 201,
      transcript_path: null,
      score_breakdown: {
        weights: { obj: 0.5, judge: 0.5 },
        objective_score: 74.4,
        total_score: 67.3,
        n_trials: 2,
        aggregate: [
          { objective: { passed: 29, total: 39, steps: [], success: [] },
            judge: { rubric_scores: [], judged_score: 62.5 } },
          { objective: { passed: 29, total: 39, steps: [], success: [] },
            judge: { rubric_scores: [], judged_score: 58.3 } },
        ],
      },
    };
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({
      run: mockRuns[0],
      matches: [aggregateMatch],
    });
    render(<ArenaLive />);

    await userEvent.click(await screen.findByText('1'));
    await userEvent.click(await screen.findByText('workflow-a'));

    // Tabbed drilldown renders (trials carry empty steps + no card) without throwing.
    expect(await screen.findByText(/Average of 2 trials/)).toBeInTheDocument();
    expect(screen.getByText(/Objective 74\.4/)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Trial 2' })).toBeInTheDocument();
    // Per-trial roster: no card → OVR em-dash, but objective + judge still render
    // (both trials are 29/39, so there are two such lines).
    expect(screen.getAllByText(/objective 29\/39/)).toHaveLength(2);
    expect(screen.getByText(/judge 58\.3/)).toBeInTheDocument();
  });

  it('shows empty state when no leaderboard data', async () => {
    vi.mocked(arenaApi.listArenaRuns).mockResolvedValue({ runs: [], total: 0 });
    vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({ rows: [] });
    vi.mocked(arenaApi.listArenaModels).mockResolvedValue({ models: [] });

    render(<ArenaLive />);

    expect(
      await screen.findByText(/No leaderboard data yet/),
    ).toBeInTheDocument();
    expect(screen.getByText(/No arena runs yet/)).toBeInTheDocument();
  });

  it('shows error when API fails', async () => {
    vi.mocked(arenaApi.listArenaRuns).mockRejectedValue(new Error('network error'));
    vi.mocked(arenaApi.getArenaLeaderboard).mockRejectedValue(new Error('network error'));
    vi.mocked(arenaApi.listArenaModels).mockRejectedValue(new Error('network error'));

    render(<ArenaLive />);

    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});


describe('flagship v2: invalid matches + axis strip', () => {
  it('renders invalid count chip on leaderboard rows', async () => {
    setupMocks();
    render(<ArenaLive />);
    expect(await screen.findByText('Claude Sonnet')).toBeInTheDocument();
    expect(screen.getByText(/1 infra/)).toBeInTheDocument();
  });

  it('renders invalid match status badge with its reason', async () => {
    setupMocks();
    render(<ArenaLive />);
    const runButton = await screen.findByText('1');
    await userEvent.click(runButton);
    expect(await screen.findByText('invalid')).toBeInTheDocument();
    expect(screen.getByText(/infra_blank/)).toBeInTheDocument();
  });

  it('renders axis strip when breakdown carries axes', async () => {
    setupMocks();
    render(<ArenaLive />);
    const runButton = await screen.findByText('1');
    await userEvent.click(runButton);
    const matchCell = await screen.findByText('Claude Sonnet', { selector: '.wl-arena__match-title' });
    await userEvent.click(matchCell);
    expect(await screen.findByText('procedural')).toBeInTheDocument();
    expect(screen.getByText('20/22')).toBeInTheDocument();
    expect(screen.getByText('grounding')).toBeInTheDocument();
    expect(screen.getByText('4/5')).toBeInTheDocument();
  });
});

describe('arena runs: multi-select + merge/delete action bar', () => {
  const twoRuns = [
    { id: 1, status: 'completed', created_at: '2026-06-24T10:00:00Z', workflow_ids: ['workflow-a'], model_ids: ['claude-sonnet'] },
    { id: 2, status: 'completed', created_at: '2026-06-25T10:00:00Z', workflow_ids: ['workflow-a'], model_ids: ['gpt-4o'] },
  ];

  function setupTwoRunMocks() {
    vi.mocked(arenaApi.listArenaRuns).mockResolvedValue({ runs: twoRuns, total: 2 });
    vi.mocked(arenaApi.getArenaLeaderboard).mockResolvedValue({ rows: mockLeaderboard });
    vi.mocked(arenaApi.listArenaModels).mockResolvedValue({ models: mockModels });
    vi.mocked(arenaApi.getArenaRun).mockResolvedValue({ run: twoRuns[0], matches: mockMatches });
    vi.mocked(arenaApi.getMatchTranscript).mockResolvedValue(mockTranscript);
  }

  it('shows merge/delete action bar when runs are checked', async () => {
    setupTwoRunMocks();
    render(<ArenaLive />);
    await screen.findByText('1');

    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes).toHaveLength(2);

    fireEvent.click(checkboxes[0]);
    expect(screen.getByText(/Merge \(1\)/)).toBeDisabled();
    expect(screen.getByText(/Delete \(1\)/)).toBeEnabled();

    fireEvent.click(checkboxes[1]);
    expect(screen.getByText(/Merge \(2\)/)).toBeEnabled();
    expect(screen.getByText(/Delete \(2\)/)).toBeEnabled();
  });

  it('checking a run checkbox does not also select the run (stopPropagation)', async () => {
    setupTwoRunMocks();
    render(<ArenaLive />);
    await screen.findByText('1');

    fireEvent.click(screen.getAllByRole('checkbox')[0]);
    expect(arenaApi.getArenaRun).not.toHaveBeenCalled();
  });

  it('Clear deselects all runs and hides the action bar', async () => {
    setupTwoRunMocks();
    render(<ArenaLive />);
    await screen.findByText('1');

    fireEvent.click(screen.getAllByRole('checkbox')[0]);
    expect(screen.getByText(/Delete \(1\)/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.queryByText(/Delete \(/)).not.toBeInTheDocument();
  });

  it('confirms and calls deleteArenaRuns, then refreshes and clears selection', async () => {
    setupTwoRunMocks();
    vi.mocked(arenaApi.deleteArenaRuns).mockResolvedValue({
      deleted_run_ids: [1, 2],
      match_count: 4,
      files_removed: 4,
    });
    render(<ArenaLive />);
    await screen.findByText('1');

    fireEvent.click(screen.getAllByRole('checkbox')[0]);
    fireEvent.click(screen.getAllByRole('checkbox')[1]);

    await userEvent.click(screen.getByText(/Delete \(2\)/));
    // Confirm modal shows the "cannot be undone" warning copy.
    expect(await screen.findByText(/cannot be undone/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(arenaApi.deleteArenaRuns).toHaveBeenCalledWith([1, 2]);
    });
    // Selection clears (action bar disappears) once the delete resolves.
    await waitFor(() => {
      expect(screen.queryByText(/Delete \(/)).not.toBeInTheDocument();
    });
  });

  it('merges the selected runs and selects the new aggregate run', async () => {
    setupTwoRunMocks();
    vi.mocked(arenaApi.mergeArenaRuns).mockResolvedValue({ run_id: 99 });
    render(<ArenaLive />);
    await screen.findByText('1');

    fireEvent.click(screen.getAllByRole('checkbox')[0]);
    fireEvent.click(screen.getAllByRole('checkbox')[1]);

    await userEvent.click(screen.getByText(/Merge \(2\)/));

    await waitFor(() => {
      expect(arenaApi.mergeArenaRuns).toHaveBeenCalledWith([1, 2]);
    });
    await waitFor(() => {
      expect(arenaApi.getArenaRun).toHaveBeenCalledWith(99);
    });
  });
});

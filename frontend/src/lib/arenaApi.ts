// Arena API client — typed fetch wrappers for /api/arena/* endpoints.

export type ArenaRunStatus = 'pending' | 'running' | 'completed' | 'failed' | string;

export type ArenaRunSummary = {
  id: number;
  status: ArenaRunStatus;
  created_at: string;
  workflow_ids: string[];
  model_ids: string[];
  /** null = unpinned (vendor default) — true of every run before this existed. */
  reasoning_effort?: string | null;
};

export type ArenaCheck = {
  kind: string;
  label: string;
  passed: boolean;
  detail: string;
  // Which scoring dimension this check feeds (grounding/adherence/synthesis/
  // procedural). Present on full v2 rows; drives the "By dimension" drilldown
  // and the per-check axis chip. Optional for older/minimal rows.
  axis?: string;
};

export type ArenaObjectiveStep = {
  index: number;
  user: string;
  checks: ArenaCheck[];
};

export type ArenaAxisTally = { passed: number; total: number };

export type ArenaScoreBreakdown = {
  // Optional because multi-trial *aggregate* rows and pre-v2 rows may omit the
  // per-check detail, carrying only the averaged headline scores + `aggregate`.
  // The drilldown must degrade gracefully rather than assume these are present.
  objective?: {
    // Per-check detail is present only on FULL v2 rows. Aggregate/legacy/minimal
    // rows may carry only headline + axes, so these are optional and the drilldown
    // must check for the full shape (Array.isArray(steps/success)) before the
    // detailed render, else degrade to the compact summary.
    passed?: number;
    total?: number;
    steps?: ArenaObjectiveStep[];
    success?: ArenaCheck[];
    // Per-axis subtotals (procedural/adherence/grounding/synthesis) — absent
    // on breakdowns recorded before the flagship v2 scoring.
    axes?: Record<string, ArenaAxisTally>;
  };
  judge?: {
    rubric_scores: { point: string; score: number }[];
    judged_score: number | null;
    judge_missing?: boolean;
    // Jury detail: each judge's mean + dispersion across the panel.
    per_judge?: { model: string; judged_score: number }[];
    judged_stdev?: number | null;
  };
  // How the subjective score was produced: "disabled" (jury opt-out — no judge
  // attempted) | "panel" | "self_consistency" (DEGRADED single-model fallback) |
  // "missing" (jury on, all judges failed).
  subjective_mode?: string;
  // Ability card (spec B) — derived from the objective axes + tool-call count.
  // `null` (with a sibling card_reason) for rows that can't be carded: legacy
  // rows without stored axes, missing tool counts, or an unloadable workflow.
  card?: {
    ovr: number;
    stats: { GRD: number; ADH: number; SYN: number; PRC: number; EFF: number };
    jdg: number | null;
    // Consistency (0–99): reliability across the model's matches in this run.
    // `null` when the run has a single match for the model (no dispersion to
    // measure → greyed in the radar). Server-derived at run-read time; folded into
    // `ovr` at weight 0.18. `base_ovr` is the pre-CON OVR (present once folded).
    con?: number | null;
    base_ovr?: number;
    position: string;
  } | null;
  card_reason?: string;
  diagnosis?: {
    counts: string;
    analysis: string;
    counts_detail?: Record<string, number | string>;
  };
  weights?: { obj: number; judge: number };
  objective_score?: number;
  objective_stdev?: number;
  total_score?: number;
  // Multi-trial aggregate rows: per-trial detail lives here — each element is a
  // full breakdown (own objective steps + a derived `card`), so the drilldown can
  // render one tab per trial. `card` at this level is the trial-averaged aggregate.
  n_trials?: number;
  aggregate?: ArenaScoreBreakdown[];
};

export type ArenaMatchSummary = {
  id: number;
  workflow_id: string;
  model_id: string;
  status: string;
  objective_score: number | null;
  judged_score: number | null;
  total_score: number | null;
  judge_missing: boolean;
  transcript_path: string | null;
  score_breakdown: ArenaScoreBreakdown | null;
  // Corroborating failure reason (e.g. "infra_blank" for invalid matches).
  error?: string | null;
};

export type ArenaRunDetail = {
  run: ArenaRunSummary;
  matches: ArenaMatchSummary[];
};

export type ArenaLeaderboardRow = {
  model_id: string;
  // Ranking is by the numbers-first ability card OVR (spec B5); `rank` is SHARED
  // across models tied on OVR. Uncarded rows fall back to objective ranking.
  rank: number;
  // Headline OVR (0–99) + the per-stat means for the radar. Null for uncarded rows.
  ovr?: number | null;
  card_mean?: { ovr: number; base_ovr?: number; con?: number | null; GRD: number; ADH: number; SYN: number; EFF: number; PRC: number } | null;
  // How many of this model's scored matches are carded. ovr/card_mean are null
  // unless carded_count === matches (a full, non-partial sample).
  carded_count?: number;
  avg_objective: number | null;
  // Advisory subjective jury score (mean ± stdev) + how it was produced
  // ("panel" | "self_consistency" (degraded) | "missing"). Never affects rank.
  subjective_mean?: number | null;
  subjective_stdev?: number | null;
  subjective_mode?: string;
  matches: number;
  // Infra-invalid match count — excluded from the averages, surfaced so
  // degraded routes stay visible.
  invalid?: number;
};

export type ArenaLeaderboard = {
  rows: ArenaLeaderboardRow[];
};

export type ArenaModel = {
  slug: string;
  zenmux_name: string;
  display_name: string;
  /** This model's declared effort ladder; absent/[] handled by sharedReasoningEfforts. */
  reasoning_efforts?: string[];
};

export type ArenaModelsResponse = {
  models: ArenaModel[];
};

export type ArenaRunsResponse = {
  runs: ArenaRunSummary[];
  total: number;
};

/**
 * Effort levels a run can be pinned to, weakest → strongest; omit/null leaves the
 * vendor default. The union of what ZenMux names — support varies per model and
 * is not published, so a level a given contestant lacks is normalised upstream.
 */
export const ARENA_REASONING_EFFORTS = [
  'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max',
] as const;

/**
 * The levels one model offers, in canonical weakest→strongest order.
 *
 * Effort is chosen PER MODEL, not once per run: the ladders genuinely differ
 * (GLM-5.2 is high/max only, most models are low/medium/high, Qwen3.7 /
 * MiniMax M3 have no levels at all), so no single value can pin a mixed field —
 * an intersection across a large field is usually empty.
 *
 * A model with no `reasoning_efforts` array is unknown to the models.dev snapshot
 * and treated as unconstrained, matching the server's permissive fallback.
 */
export function reasoningEffortsFor(model: ArenaModel): string[] {
  const declared = model.reasoning_efforts;
  if (!declared) return [...ARENA_REASONING_EFFORTS];
  return ARENA_REASONING_EFFORTS.filter((e) => declared.includes(e));
}
export type ArenaReasoningEffort = (typeof ARENA_REASONING_EFFORTS)[number];

export type ArenaCreateRunRequest = {
  workflow_ids: string[];
  model_ids: string[];
  trials: number;
  weights?: { obj: number; judge: number };
  /**
   * Per-model effort {model_slug: effort}. A model absent runs at its own vendor
   * default, as every contestant did on boards #8–#104.
   */
  reasoning_efforts?: Record<string, ArenaReasoningEffort>;
};

export type ArenaWorkflowSummary = { id: string; title: string; tags: string[]; step_count: number };

export type ArenaCreateRunResponse = {
  run_id: number;
  status: ArenaRunStatus;
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export function listArenaRuns(limit = 20, offset = 0): Promise<ArenaRunsResponse> {
  return apiFetch(`/api/arena/runs?limit=${limit}&offset=${offset}`);
}

export function getArenaRun(runId: number): Promise<ArenaRunDetail> {
  return apiFetch(`/api/arena/runs/${runId}`);
}

export function getArenaLeaderboard(runId?: number, tag?: string): Promise<ArenaLeaderboard> {
  const params = new URLSearchParams();
  if (runId != null) params.set('run_id', String(runId));
  if (tag) params.set('tag', tag);
  const qs = params.toString();
  return apiFetch(`/api/arena/leaderboard${qs ? `?${qs}` : ''}`);
}

export function getMatchTranscript(matchId: number): Promise<unknown> {
  return apiFetch(`/api/arena/matches/${matchId}/transcript`);
}

export function listArenaModels(): Promise<ArenaModelsResponse> {
  return apiFetch('/api/arena/models');
}

export function createArenaRun(body: ArenaCreateRunRequest): Promise<ArenaCreateRunResponse> {
  return apiFetch('/api/arena/runs', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function listArenaWorkflows(): Promise<{ workflows: ArenaWorkflowSummary[] }> {
  return apiFetch('/api/arena/workflows');
}

export function deleteArenaRuns(runIds: number[]): Promise<{ deleted_run_ids: number[]; match_count: number; files_removed: number }> {
  return apiFetch('/api/arena/runs/delete', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  });
}

export function mergeArenaRuns(sourceRunIds: number[]): Promise<{ run_id: number }> {
  return apiFetch('/api/arena/runs/merge', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ source_run_ids: sourceRunIds }),
  });
}

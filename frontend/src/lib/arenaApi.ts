// Arena API client — typed fetch wrappers for /api/arena/* endpoints.

export type ArenaRunStatus = 'pending' | 'running' | 'completed' | 'failed' | string;

export type ArenaRunSummary = {
  id: number;
  status: ArenaRunStatus;
  created_at: string;
  workflow_ids: string[];
  model_ids: string[];
};

export type ArenaCheck = {
  kind: string;
  label: string;
  passed: boolean;
  detail: string;
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
  diagnosis?: {
    counts: string;
    analysis: string;
    counts_detail?: Record<string, number | string>;
  };
  weights?: { obj: number; judge: number };
  objective_score?: number;
  total_score?: number;
  // Multi-trial aggregate rows (averaged board): per-trial detail lives here.
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
  // Ranking is by the deterministic objective axis (spec D5 — no blend);
  // `rank` is SHARED across models tied on objective.
  rank: number;
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
};

export type ArenaModelsResponse = {
  models: ArenaModel[];
};

export type ArenaRunsResponse = {
  runs: ArenaRunSummary[];
  total: number;
};

export type ArenaCreateRunRequest = {
  workflow_ids: string[];
  model_ids: string[];
  weights?: { obj: number; judge: number };
};

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

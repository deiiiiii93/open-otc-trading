// Arena API client — typed fetch wrappers for /api/arena/* endpoints.

export type ArenaRunStatus = 'pending' | 'running' | 'completed' | 'failed' | string;

export type ArenaRunSummary = {
  id: string;
  status: ArenaRunStatus;
  created_at: string;
  workflow_ids: string[];
  model_ids: string[];
};

export type ArenaMatchSummary = {
  id: string;
  workflow_id: string;
  model_id: string;
  status: string;
  objective_score: number | null;
  judged_score: number | null;
  total_score: number | null;
  judge_missing: boolean;
  transcript_path: string | null;
};

export type ArenaRunDetail = {
  run: ArenaRunSummary;
  matches: ArenaMatchSummary[];
};

export type ArenaLeaderboardRow = {
  model_id: string;
  avg_total: number | null;
  avg_objective: number | null;
  matches: number;
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
  weights?: { objective: number; judge: number };
};

export type ArenaCreateRunResponse = {
  run_id: string;
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

export function getArenaRun(runId: string): Promise<ArenaRunDetail> {
  return apiFetch(`/api/arena/runs/${runId}`);
}

export function getArenaLeaderboard(runId?: string, tag?: string): Promise<ArenaLeaderboard> {
  const params = new URLSearchParams();
  if (runId) params.set('run_id', runId);
  if (tag) params.set('tag', tag);
  const qs = params.toString();
  return apiFetch(`/api/arena/leaderboard${qs ? `?${qs}` : ''}`);
}

export function getMatchTranscript(matchId: string): Promise<unknown> {
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

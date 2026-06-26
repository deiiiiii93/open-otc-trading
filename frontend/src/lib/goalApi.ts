// Goal-mode API client — typed fetch wrappers for /api/chat/threads/{id}/goal/*.
//
// Goal mode (spec §G) turns a natural-language `/goal <description>` into a structured
// acceptance contract the user ratifies once; the desk run is then graded against the
// frozen criteria. These wrappers drive that lifecycle from the composer.

export type GoalMode = 'interactive' | 'auto' | 'yolo';

export type GoalRunStatus =
  | 'awaiting_ratification'
  | 'running'
  | 'stuck_needs_human'
  | 'satisfied'
  | 'cancelled';

export type GoalFieldPredicate = {
  path: string;
  op: string;
  value?: unknown;
};

export type GoalCheck = {
  type: 'artifact_exists' | 'ledger_predicate' | 'measurable';
  [key: string]: unknown;
};

export type GoalCriterion = {
  id: string;
  text: string;
  required: boolean;
  check: GoalCheck;
};

export type GoalContract = {
  schema_version: string;
  goal_text: string;
  summary: string;
  domain_write_policy: 'forbidden' | 'allowed_by_mode';
  criteria: GoalCriterion[];
};

export type GoalFailingCriterion = {
  id: string;
  status: string;
  reason: string;
};

export type GoalRunState = {
  schema_version: string;
  goal_run_id: string;
  status: GoalRunStatus;
  mode: GoalMode;
  contract_hash: string | null;
  terminal_reason?: string | null;
  last_verdict?: string | null;
  failing_criteria?: GoalFailingCriterion[];
};

export type GoalClarification = {
  type: 'needs_clarification';
  summary: string;
  questions: string[];
};

export type StartGoalResponse = GoalRunState | GoalClarification;

/** Parse a `/goal <description>` composer command. Returns null for anything else,
 * including a bare `/goal`, a description-less `/goal   `, or `/goalkeeper ...`. */
export function parseGoalCommand(raw: string): { goalText: string } | null {
  const trimmed = raw.trim();
  if (trimmed !== '/goal' && !trimmed.startsWith('/goal ')) return null;
  const goalText = trimmed.slice('/goal'.length).trim();
  return goalText ? { goalText } : null;
}

export function isClarification(r: StartGoalResponse): r is GoalClarification {
  return (r as GoalClarification).type === 'needs_clarification';
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const base = (threadId: number) => `/api/chat/threads/${threadId}/goal`;

export function startGoal(threadId: number, goalText: string, mode: GoalMode): Promise<StartGoalResponse> {
  return post(base(threadId), { goal_text: goalText, mode });
}

export function ratifyGoal(threadId: number): Promise<GoalRunState> {
  return post(`${base(threadId)}/ratify`);
}

export function resumeGoal(threadId: number): Promise<GoalRunState> {
  return post(`${base(threadId)}/resume`);
}

export function cancelGoal(threadId: number): Promise<GoalRunState> {
  return post(`${base(threadId)}/cancel`);
}

export function getGoal(threadId: number): Promise<GoalRunState | null> {
  return apiFetch(base(threadId));
}

export function getGoalContract(threadId: number): Promise<GoalContract | null> {
  return apiFetch(`${base(threadId)}/contract`);
}

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  parseGoalCommand,
  startGoal,
  ratifyGoal,
  cancelGoal,
  getGoalContract,
  isClarification,
} from './goalApi';

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetchOnce(body: unknown, ok = true) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('parseGoalCommand', () => {
  it('extracts the goal text after /goal', () => {
    expect(parseGoalCommand('/goal get latest risk onto Control')).toEqual({
      goalText: 'get latest risk onto Control',
    });
  });
  it('trims surrounding whitespace', () => {
    expect(parseGoalCommand('  /goal   refresh risk  ')).toEqual({ goalText: 'refresh risk' });
  });
  it('returns null for a normal message', () => {
    expect(parseGoalCommand('what is my delta?')).toBeNull();
  });
  it('returns null for /goal with no description', () => {
    expect(parseGoalCommand('/goal')).toBeNull();
    expect(parseGoalCommand('/goal    ')).toBeNull();
  });
  it('does not match a word that merely starts with goal', () => {
    expect(parseGoalCommand('/goalkeeper save')).toBeNull();
  });
});

describe('goal API client', () => {
  it('startGoal posts goal_text and mode', async () => {
    const fetchMock = mockFetchOnce({ schema_version: 'goal_run_state.v1', status: 'awaiting_ratification' });
    await startGoal(7, 'refresh risk', 'auto');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/threads/7/goal',
      expect.objectContaining({ method: 'POST' }),
    );
    const init = fetchMock.mock.calls[0][1];
    expect(JSON.parse(init.body)).toEqual({ goal_text: 'refresh risk', mode: 'auto' });
  });

  it('isClarification narrows the start response', () => {
    expect(isClarification({ type: 'needs_clarification', summary: 's', questions: ['q'] })).toBe(true);
    expect(isClarification({ schema_version: 'goal_run_state.v1', status: 'running' } as never)).toBe(false);
  });

  it('ratifyGoal posts to the ratify endpoint', async () => {
    const fetchMock = mockFetchOnce({ status: 'running' });
    await ratifyGoal(7);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/threads/7/goal/ratify',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('cancelGoal posts to the cancel endpoint', async () => {
    const fetchMock = mockFetchOnce({ status: 'cancelled' });
    await cancelGoal(7);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/chat/threads/7/goal/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('getGoalContract reads the contract endpoint', async () => {
    mockFetchOnce({ goal_text: 'g', criteria: [] });
    const contract = await getGoalContract(7);
    expect(contract?.goal_text).toBe('g');
  });

  it('throws on a non-ok response', async () => {
    mockFetchOnce({ detail: 'bad contract' }, false);
    await expect(startGoal(7, 'x', 'auto')).rejects.toThrow();
  });
});

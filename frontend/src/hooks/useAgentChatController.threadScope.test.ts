import { describe, it, expect } from 'vitest';
import {
  hasMoreThreads,
  mergeThreadLists,
  threadListScopes,
  withActiveThreadPreserved,
} from './useAgentChatController';
import type { Thread } from '../types';

const thread = (id: number, source: string, updated_at?: string): Thread => ({
  id,
  title: `t${id}`,
  character: 'trader',
  source,
  updated_at,
  messages: [],
});

describe('threadListScopes', () => {
  it('requests only the controller source when arena is hidden', () => {
    expect(threadListScopes('desk', false)).toEqual(['desk']);
  });

  it('adds the arena scope when the desk toggle is on', () => {
    expect(threadListScopes('desk', true)).toEqual(['desk', 'arena']);
  });

  it('never requests the arena scope twice', () => {
    expect(threadListScopes('arena', true)).toEqual(['arena']);
  });

  it('keeps the builder surface free of arena threads', () => {
    expect(threadListScopes('workflow_builder', false)).toEqual(['workflow_builder']);
  });
});

describe('mergeThreadLists', () => {
  it('re-sorts across lists so the newest thread leads', () => {
    // Each scoped response is updated_at-desc on its own; concatenating them
    // is not, and the caller resumes "most recent" by taking the first match.
    const desk = [thread(1, 'desk', '2026-08-10T00:00:00'), thread(2, 'desk', '2026-08-01T00:00:00')];
    const arena = [thread(3, 'arena', '2026-08-15T00:00:00')];

    expect(mergeThreadLists([desk, arena]).map((t) => t.id)).toEqual([3, 1, 2]);
  });

  it('sorts threads with no updated_at last', () => {
    const rows = [thread(1, 'desk', undefined), thread(2, 'desk', '2026-08-01T00:00:00')];

    expect(mergeThreadLists([rows]).map((t) => t.id)).toEqual([2, 1]);
  });

  it('returns a single list unchanged in order', () => {
    const rows = [thread(1, 'desk', '2026-08-10T00:00:00'), thread(2, 'desk', '2026-08-01T00:00:00')];

    expect(mergeThreadLists([rows]).map((t) => t.id)).toEqual([1, 2]);
  });

  it('trims the merged list back to the page size', () => {
    // Each scope is fetched with the same limit, so the union can hold twice
    // the page — but the true newest N of a union is always inside the union
    // of each list's newest N, so trimming after the merge is correct.
    const desk = [thread(1, 'desk', '2026-08-10T00:00:00'), thread(2, 'desk', '2026-08-09T00:00:00')];
    const arena = [thread(3, 'arena', '2026-08-15T00:00:00'), thread(4, 'arena', '2026-08-14T00:00:00')];

    expect(mergeThreadLists([desk, arena], 2).map((t) => t.id)).toEqual([3, 4]);
  });
});

describe('hasMoreThreads', () => {
  it('reports more when a scope filled its page', () => {
    expect(hasMoreThreads([[thread(1, 'desk'), thread(2, 'desk')]], 2)).toBe(true);
  });

  it('reports no more when every scope came back short', () => {
    expect(hasMoreThreads([[thread(1, 'desk')]], 2)).toBe(false);
  });

  it('reports more when only the second scope filled its page', () => {
    const short = [thread(1, 'desk')];
    const full = [thread(2, 'arena'), thread(3, 'arena')];

    expect(hasMoreThreads([short, full], 2)).toBe(true);
  });
});

describe('withActiveThreadPreserved', () => {
  it('keeps the open thread when a search result set drops it', () => {
    // Otherwise searching blanks the message pane of the thread you are reading.
    const previous = [thread(7, 'desk', '2026-08-01T00:00:00')];
    const searchResults = [thread(9, 'desk', '2026-08-05T00:00:00')];

    const kept = withActiveThreadPreserved(searchResults, previous, 7);

    expect(kept.map((t) => t.id)).toContain(7);
    expect(kept.map((t) => t.id)).toContain(9);
  });

  it('leaves the list untouched when the open thread is already in it', () => {
    const previous = [thread(7, 'desk', '2026-08-01T00:00:00')];
    const next = [thread(7, 'desk', '2026-08-02T00:00:00')];

    expect(withActiveThreadPreserved(next, previous, 7)).toBe(next);
  });

  it('leaves the list untouched when no thread is open', () => {
    const next = [thread(9, 'desk')];

    expect(withActiveThreadPreserved(next, [], null)).toBe(next);
  });

  it('does not invent a thread it never had', () => {
    const next = [thread(9, 'desk')];

    expect(withActiveThreadPreserved(next, [], 7)).toBe(next);
  });
});

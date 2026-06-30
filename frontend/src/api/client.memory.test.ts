import { describe, it, expect, vi, beforeEach } from 'vitest';
import { errorMessage, listMemoryFacts, createMemoryFact, getMemoryStatus } from './client';

describe('errorMessage', () => {
  it('extracts FastAPI detail', () => {
    expect(errorMessage(new Error('{"detail":"duplicate"}'))).toBe('duplicate');
  });
  it('falls back to raw text', () => {
    expect(errorMessage(new Error('plain failure'))).toBe('plain failure');
  });
  it('handles non-Error', () => {
    expect(errorMessage('boom')).toBe('boom');
  });
});

describe('memory client fns (mocked fetch)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listMemoryFacts builds the query string', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }));
    await listMemoryFacts({ scope_type: 'book', scope_id: '7', status: 'proposed', limit: 100, offset: 100 });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('scope_type=book');
    expect(url).toContain('scope_id=7');
    expect(url).toContain('status=proposed');
    expect(url).toContain('limit=100');
    expect(url).toContain('offset=100');
  });

  it('createMemoryFact POSTs the body', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ id: 1 }), { status: 201 }));
    await createMemoryFact({ scope_type: 'book', scope_id: '7', content: 'x', confidence: 0.9 });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toMatchObject({ scope_type: 'book', scope_id: '7', content: 'x' });
  });

  it('api<T> throws Error whose message is the raw response body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"detail":"duplicate"}', { status: 409 }));
    await expect(getMemoryStatus()).rejects.toThrow('{"detail":"duplicate"}');
  });
});

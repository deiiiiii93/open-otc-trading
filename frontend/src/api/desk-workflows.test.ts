import { describe, it, expect, vi, afterEach } from 'vitest';
import { listWorkflows, createWorkflow } from './client';

afterEach(() => vi.restoreAllMocks());

describe('desk-workflow api', () => {
  it('lists workflows', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            slug: 'a', title: 'A', persona: 'trader', description: '',
            scope: 'local', default_mode: 'auto', source: 'user',
          },
        ]),
        { status: 200 },
      ),
    );
    const rows = await listWorkflows();
    expect(rows[0].slug).toBe('a');
  });

  it('creates via POST', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ slug: 'a', script: 'meta = {}' }), { status: 200 }),
    );
    await createWorkflow('meta = {}');
    expect(spy).toHaveBeenCalledWith('/api/workflows', expect.objectContaining({ method: 'POST' }));
  });
});

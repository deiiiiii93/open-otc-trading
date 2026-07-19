import { afterEach, describe, expect, it, vi } from 'vitest';
import { listAuditActions } from './client';

afterEach(() => vi.restoreAllMocks());

describe('audit client', () => {
  it('encodes an exact audit_ref list filter', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }),
    );

    await listAuditActions({
      audit_ref: 'limit:incident:81:waived',
      limit: 1,
      offset: 0,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/audit/actions?audit_ref=limit%3Aincident%3A81%3Awaived&limit=1&offset=0',
    );
  });
});

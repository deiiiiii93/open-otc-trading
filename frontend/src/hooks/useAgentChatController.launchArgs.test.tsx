import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useAgentChatController } from './useAgentChatController';

describe('launchWorkflow args', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.restoreAllMocks();
    fetchMock = vi.fn((url: string, init?: { method?: string; body?: string }) => {
      const u = String(url);
      if (u.endsWith('/api/agent/models')) {
        return Promise.resolve({ ok: true, json: async () => ({ active: null, channels: [] }) });
      }
      if (u.includes('/workflows/') && u.includes('/run')) {
        return Promise.resolve({ ok: true, body: null });
      }
      if (u.includes('/api/chat/threads') && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ id: 7, title: 't', character: 'trader', source: 'desk', messages: [] }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => [] }); // GET threads list, polling, etc.
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  it('posts args in the run body', async () => {
    const { result } = renderHook(() => useAgentChatController());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled()); // let mount settle
    await act(async () => {
      await result.current.launchWorkflow('need-portfolio', 'yolo', { portfolio: 'Default' });
    });
    const runCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes('/workflows/need-portfolio/run'));
    expect(runCall).toBeTruthy();
    expect(JSON.parse((runCall![1] as { body: string }).body)).toMatchObject({
      mode: 'yolo', args: { portfolio: 'Default' },
    });
  });
});

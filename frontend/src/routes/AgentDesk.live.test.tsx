import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AgentDeskLive } from './AgentDesk.live';

const enc = new TextEncoder();

const baseThread = {
  id: 1,
  title: 'Test',
  character: 'trader',
  messages: [] as unknown[],
};

const modelCatalog = {
  enabled: false,
  active: { channel: 'zenmux', provider: 'anthropic', model: 'anthropic/claude-sonnet-4-6' },
  channels: [
    {
      name: 'zenmux',
      label: 'Zenmux',
      type: 'zenmux',
      healthy: false,
      models: [
        {
          channel: 'zenmux',
          provider: 'anthropic',
          model: 'anthropic/claude-sonnet-4-6',
          label: 'Claude Sonnet 4.6',
        },
      ],
    },
  ],
};

function makeSseStream(lines: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const line of lines) controller.enqueue(enc.encode(line));
      controller.close();
    },
  });
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

beforeEach(() => {
  globalThis.fetch = vi.fn();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('AgentDeskLive SSE parsing', () => {
  it('parses tool_start, token, tool_end, and done events into a streaming bubble', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let threadsRequestCount = 0;
    const pageContext = {
      route: 'positions' as const,
      title: 'Positions - Book A',
      path: '/',
      entity_ids: { portfolio_id: 7 },
      snapshot: { portfolio: { id: 7, name: 'Book A' } },
      chips: ['Book A', '4 trades'],
    };
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const getStreamController = () => {
      if (streamController == null) throw new Error('stream controller not ready');
      return streamController;
    };

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        threadsRequestCount += 1;
        if (threadsRequestCount === 1) {
          return response([baseThread]);
        }
        return response([
          {
            ...baseThread,
            messages: [
              { id: 10, role: 'user', content: 'hi', meta: {} },
              {
                id: 99,
                role: 'assistant',
                character: 'trader',
                content: 'Hello world',
                meta: {
                  process_events: [
                    { id: 'r1', name: 'get_positions', status: 'done', duration_ms: 80 },
                  ],
                },
              },
            ],
          },
        ]);
      }
      if (url.includes('/messages/stream')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller;
            controller.enqueue(enc.encode(
              'event: tool_start\ndata: {"id":"r1","name":"get_positions","args":{"portfolio_id":1}}\n\n'
              + 'event: todo_update\ndata: {"todos":[{"content":"Load positions","status":"in_progress"},{"content":"Summarize matches","status":"pending"}]}\n\n'
              + 'event: token\ndata: {"text":"Hello "}\n\n',
            ));
          },
        });
        return new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return response({});
    });

    render(<AgentDeskLive pageContext={pageContext} />);
    await waitFor(() => screen.getByLabelText(/ask anything/i));
    await userEvent.click(screen.getByRole('checkbox', { name: /yolo/i }));
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText('Load positions')).toBeInTheDocument();
    });
    expect(screen.getByText('in progress')).toBeInTheDocument();
    expect(screen.getByText('get_positions')).toBeInTheDocument();

    const controller = getStreamController();
    controller.enqueue(enc.encode(
      'event: token\ndata: {"text":"world"}\n\n'
      + 'event: tool_end\ndata: {"id":"r1","duration_ms":80,"output":{"count":3}}\n\n'
      + 'event: done\ndata: {"message_id":99}\n\n',
    ));
    controller.close();

    await waitFor(() => {
      expect(screen.getByText(/Hello world/)).toBeInTheDocument();
    });
    expect(screen.getByText('get_positions')).toBeInTheDocument();
    const streamCall = fetchMock.mock.calls.find(([input]) => requestUrl(input).includes('/messages/stream'));
    const body = JSON.parse(String(streamCall?.[1]?.body ?? '{}'));
    expect(body.page_context).toMatchObject({
      route: 'positions',
      title: 'Positions - Book A',
      entity_ids: { portfolio_id: 7 },
    });
    expect(body.yolo_mode).toBe(true);
  });

  it('resets the streaming bubble on envelope_transitioned so the first-pass refusal does not leak', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let threadsRequestCount = 0;
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const getStreamController = () => {
      if (streamController == null) throw new Error('stream controller not ready');
      return streamController;
    };

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        threadsRequestCount += 1;
        return response([baseThread]);
      }
      if (url.includes('/messages/stream')) {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller;
            // First pass under pet_page: the model refuses before the runtime widens.
            controller.enqueue(enc.encode(
              'event: token\ndata: {"text":"Blocked at this access level."}\n\n',
            ));
          },
        });
        return new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return response({});
    });

    render(<AgentDeskLive />);
    await waitFor(() => screen.getByLabelText(/ask anything/i));
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Blocked at this access level/)).toBeInTheDocument();
    });

    // Escalation widens pet_page -> pet_diagnostic and re-streams the real answer.
    // The first-pass refusal must be dropped from the live bubble, not appended to.
    const controller = getStreamController();
    controller.enqueue(enc.encode(
      'event: envelope_transitioned\ndata: {"previous_envelope":"pet_page","new_envelope":"pet_diagnostic","reason":"diagnostic_followup","denied_tool":"query_snowball_ko_from_spot"}\n\n'
      + 'event: token\ndata: {"text":"Here is the KO proximity table."}\n\n',
    ));

    await waitFor(() => {
      expect(screen.getByText(/Here is the KO proximity table/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Blocked at this access level/)).not.toBeInTheDocument();
    controller.close();
  });

  it('ignores heartbeat events', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let threadsRequestCount = 0;

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        threadsRequestCount += 1;
        return response(
          threadsRequestCount === 1
            ? [baseThread]
            : [
                {
                  ...baseThread,
                  messages: [
                    { id: 10, role: 'user', content: 'hi', meta: {} },
                    { id: 99, role: 'assistant', character: 'trader', content: 'Hello', meta: {} },
                  ],
                },
              ],
        );
      }
      if (url.includes('/messages/stream')) {
        const lines = [
          'event: token\ndata: {"text":"Hel"}\n\n',
          'event: heartbeat\ndata: {}\n\n',
          'event: token\ndata: {"text":"lo"}\n\n',
          'event: done\ndata: {"message_id":99}\n\n',
        ];
        return new Response(makeSseStream(lines), {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return response({});
    });

    render(<AgentDeskLive />);
    await waitFor(() => screen.getByLabelText(/ask anything/i));
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(screen.getByText('Hello')).toBeInTheDocument());
    expect(screen.queryByText('{}')).not.toBeInTheDocument();
  });

  it('aborts the active stream when Stop is clicked', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let streamSignal: AbortSignal | null = null;

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        return response([baseThread]);
      }
      if (url.includes('/messages/stream')) {
        streamSignal = (init?.signal as AbortSignal | undefined) ?? null;
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(enc.encode('event: token\ndata: {"text":"Working"}\n\n'));
            streamSignal?.addEventListener('abort', () => {
              controller.error(new DOMException('Aborted', 'AbortError'));
            });
          },
        });
        return new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return response({});
    });

    render(<AgentDeskLive />);
    await waitFor(() => screen.getByLabelText(/ask anything/i));
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /stop/i }));

    await waitFor(() => expect(streamSignal?.aborted).toBe(true));
    await waitFor(() => expect(screen.queryByRole('button', { name: /stop/i })).not.toBeInTheDocument());
  });

  it('keeps the desk usable when the stream emits an error event', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let threadsRequestCount = 0;

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        threadsRequestCount += 1;
        return response(
          threadsRequestCount === 1
            ? [baseThread]
            : [
                {
                  ...baseThread,
                  messages: [
                    { id: 10, role: 'user', content: 'hi', meta: {} },
                    {
                      id: 99,
                      role: 'assistant',
                      character: 'trader',
                      content: 'quota exceeded',
                      meta: { agent_phase: 'error' },
                    },
                  ],
                },
              ],
        );
      }
      if (url.includes('/messages/stream')) {
        const lines = [
          'event: error\ndata: {"message":"quota exceeded","retryable":false}\n\n',
          'event: done\ndata: {"message_id":99}\n\n',
        ];
        return new Response(makeSseStream(lines), {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return response({});
    });

    render(<AgentDeskLive />);
    await waitFor(() => screen.getByLabelText(/ask anything/i));
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(screen.getByText('quota exceeded')).toBeInTheDocument());
    expect(screen.queryByText(/Could not load Agent Desk/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument();
  });

  it('refreshes the active thread when an async agent posts a HITL approval', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let threadsRequestCount = 0;
    const hitlMessage = {
      id: 133,
      role: 'assistant',
      character: 'async_agent',
      content: 'Background task wants approval for create_report.',
      meta: {
        agent_graph: 'async_agent',
        agent_phase: 'awaiting_confirmation',
        async_task_id: 13,
        pending_actions: [
          {
            id: 'intr-1:0',
            tool_name: 'create_report',
            label: 'Create report artifacts',
            summary: 'Create report',
            status: 'pending',
            async_task_id: 13,
          },
        ],
      },
    };

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        threadsRequestCount += 1;
        return response([
          {
            ...baseThread,
            messages: threadsRequestCount >= 2 ? [hitlMessage] : [],
          },
        ]);
      }
      if (url.endsWith('/api/chat/threads/1/async_agents')) {
        return response([
          {
            task_id: 13,
            description: 'approval',
            status: 'running',
            awaiting_approval: true,
            started_at: '2026-05-17T14:07:57.266353',
            finished_at: null,
            last_message_preview: null,
          },
        ]);
      }
      return response({});
    });

    render(<AgentDeskLive />);

    await waitFor(() => expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByText('Background task wants approval for create_report.')).toBeInTheDocument(),
    );
    expect(screen.getByText('Create report artifacts')).toBeInTheDocument();
  });

  it('stops polling async agents after an empty status response', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    let asyncAgentRequests = 0;
    const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
    vi.spyOn(window, 'setInterval').mockImplementation(() => 123);

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        return response([baseThread]);
      }
      if (url.endsWith('/api/chat/threads/1/async_agents')) {
        asyncAgentRequests += 1;
        return response([]);
      }
      return response({});
    });

    render(<AgentDeskLive />);

    await waitFor(() => expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument());
    await waitFor(() => expect(asyncAgentRequests).toBe(1));
    expect(clearIntervalSpy).toHaveBeenCalledWith(123);
  });
});

describe('AgentDeskLive dismiss flow', () => {
  it('shows progress immediately after confirming an action', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);
    const pauseMessage = {
      id: 10,
      role: 'assistant',
      character: 'risk_manager',
      content: 'Shall I run risk analysis?',
      meta: {
        pending_actions: [
          {
            id: 'act-1',
            tool_name: 'run_batch_pricing',
            label: 'Run batch pricing (valuations + risk)',
            summary: 'Portfolio #7',
            status: 'pending',
          },
        ],
      },
    };
    let resolveConfirm!: (value: Response) => void;
    const confirmResponse = new Promise<Response>((resolve) => {
      resolveConfirm = resolve;
    });

    fetchMock.mockImplementation(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url.endsWith('/api/chat/threads') && (!init?.method || init.method === 'GET')) {
        return response([{ ...baseThread, messages: [pauseMessage] }]);
      }
      if (url.endsWith('/api/chat/threads/1/async_agents')) return response([]);
      if (url.endsWith('/api/chat/threads/1/messages/10/actions/act-1/confirm')) {
        return confirmResponse;
      }
      return response({});
    });

    render(<AgentDeskLive />);

    await waitFor(() => expect(screen.getByText('Run batch pricing (valuations + risk)')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /confirm action/i }));

    expect(screen.getByText('Confirmed')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Running run_batch_pricing');
    expect(screen.getByRole('progressbar')).toBeInTheDocument();

    resolveConfirm(response({
      id: 11,
      role: 'assistant',
      character: 'risk_manager',
      content: 'Queued.',
      meta: {},
    }));
  });

  it('POSTs to dismiss endpoint and appends the response message', async () => {
    const pauseMessage = {
      id: 10,
      role: 'assistant',
      character: 'trader',
      content: 'Shall I run risk analysis?',
      meta: {
        pending_actions: [
          {
            id: 'act-1',
            tool_name: 'run_batch_pricing',
            label: 'Run batch pricing (valuations + risk)',
            summary: 'Portfolio #7',
            status: 'pending',
          },
        ],
      },
    };

    const dismissResponseMessage = {
      id: 11,
      role: 'assistant',
      character: 'trader',
      content: 'Understood, skipping risk analysis.',
      meta: {},
    };

    const postCalls: string[] = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url === '/api/chat/threads') {
        return response([{ ...baseThread, messages: [pauseMessage] }]);
      }
      const dismissUrl = '/api/chat/threads/1/messages/10/actions/act-1/dismiss';
      if (url === dismissUrl && init?.method === 'POST') {
        postCalls.push(url);
        return response(dismissResponseMessage);
      }
      return response({});
    }) as unknown as typeof fetch;

    render(<AgentDeskLive />);

    await waitFor(() => expect(screen.getByText('Shall I run risk analysis?')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('Run batch pricing (valuations + risk)')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));

    await waitFor(() =>
      expect(postCalls).toContain('/api/chat/threads/1/messages/10/actions/act-1/dismiss'),
    );
    await waitFor(() =>
      expect(screen.getByText('Understood, skipping risk analysis.')).toBeInTheDocument(),
    );
  });

  it('marks action as dismissed optimistically before the network response', async () => {
    let dismissResolve!: (v: Response) => void;
    const dismissPromise = new Promise<Response>((res) => {
      dismissResolve = res;
    });

    const pauseMessage = {
      id: 20,
      role: 'assistant',
      character: 'trader',
      content: 'Confirm before proceeding?',
      meta: {
        pending_actions: [
          {
            id: 'act-2',
            tool_name: 'approve_rfq',
            label: 'Approve RFQ',
            summary: 'RFQ #42',
            status: 'pending',
          },
        ],
      },
    };

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url.endsWith('/api/agent/models')) return response(modelCatalog);
      if (url === '/api/chat/threads') {
        return response([{ ...baseThread, messages: [pauseMessage] }]);
      }
      if (url.includes('/dismiss') && init?.method === 'POST') {
        return dismissPromise;
      }
      return response({});
    }) as unknown as typeof fetch;

    render(<AgentDeskLive />);

    await waitFor(() => expect(screen.getByText('Approve RFQ')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));

    await waitFor(() => expect(screen.getByRole('button', { name: /dismiss/i })).toBeDisabled());

    dismissResolve(
      response({ id: 21, role: 'assistant', character: 'trader', content: 'Done.', meta: {} }),
    );
  });
});

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FloatingAgent } from './FloatingAgent';
import { FloatingAgentMiniChat } from './FloatingAgentMiniChat';
import type { AgentChatController } from '../hooks/useAgentChatController';
import type { PageContext, Thread } from '../types';

const pageContext: PageContext = {
  route: 'positions',
  title: 'Position Detail dialog',
  path: '/',
  entity_ids: { portfolio_id: 2, position_id: 9 },
  snapshot: { position: { id: 9, trade_id: 'SNB-001' } },
  chips: ['dialog', 'SNB-001'],
};

function controller(overrides: Partial<AgentChatController> = {}): AgentChatController {
  const activeThread: Thread = {
    id: 1,
    title: 'Thread',
    character: 'trader',
    messages: [],
  };

  return {
    threads: [activeThread],
    activeThreadId: activeThread.id,
    activeThread,
    loading: false,
    sending: false,
    streaming: false,
    streamingItem: null,
    error: null,
    viewMode: 'compact',
    channels: [],
    selectedModel: null,
    executionMode: 'auto',
    confirmingActionIds: new Set(),
    taskRunsById: {},
    setSelectedModel: vi.fn(),
    setExecutionMode: vi.fn(),
    setViewMode: vi.fn(),
    selectThread: vi.fn(),
    createThread: vi.fn(),
    renameThread: vi.fn(),
    exportThread: vi.fn(),
    deleteThread: vi.fn(),
    forkThread: vi.fn(),
    sendMessage: vi.fn(),
    launchWorkflow: vi.fn(),
    confirmCostPreview: vi.fn(),
    stopStreaming: vi.fn(),
    confirmAction: vi.fn(),
    dismissAction: vi.fn(),
    refreshModels: vi.fn(),
    goalContract: null,
    goalState: null,
    goalClarification: null,
    goalBusy: false,
    ratifyActiveGoal: vi.fn(),
    cancelActiveGoal: vi.fn(),
    ...overrides,
  };
}

describe('FloatingAgentMiniChat', () => {
  it('renders a mini chat surface inside the floating agent panel', () => {
    render(
      <FloatingAgent open onOpenChange={() => {}} chips={pageContext.chips} hasUnread={false}>
        <FloatingAgentMiniChat
          controller={controller()}
          pageContext={pageContext}
          onOpenDesk={() => {}}
        />
      </FloatingAgent>,
    );

    expect(screen.getByText('Loaded context')).toBeInTheDocument();
    expect(screen.getByText('Position Detail dialog')).toBeInTheDocument();
    expect(screen.getByText(/tok est\./i)).toBeInTheDocument();
    expect(screen.getByText(/Recent 0 \/ full desk/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^new$/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument();
    expect(screen.queryByText(/full chat surface/i)).not.toBeInTheDocument();
  });

  it('creates a new pet session and keeps the active page context loaded', async () => {
    const createThread = vi.fn().mockResolvedValue(undefined);
    const sendMessage = vi.fn();
    render(
      <FloatingAgentMiniChat
        controller={controller({ createThread, sendMessage })}
        pageContext={pageContext}
        onOpenDesk={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /^new$/i }));

    expect(createThread).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Position Detail dialog')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/ask anything/i), 'start fresh from here');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0][1]).toEqual(pageContext);
    expect(sendMessage.mock.calls[0][3]).toMatchObject({
      chip_count: 2,
      snapshot_key_count: 1,
      warning_level: 'none',
    });
  });

  it('sends messages with the current page context and usage metadata', async () => {
    const sendMessage = vi.fn();
    render(
      <FloatingAgentMiniChat
        controller={controller({ sendMessage })}
        pageContext={pageContext}
        onOpenDesk={() => {}}
      />,
    );

    await userEvent.type(screen.getByLabelText(/ask anything/i), 'what am I looking at?');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(sendMessage).toHaveBeenCalledTimes(1);
    const call = sendMessage.mock.calls[0];
    expect(call[0]).toBe('what am I looking at?');
    expect(call[1]).toEqual(pageContext);
    expect(call[3]).toMatchObject({
      chip_count: 2,
      snapshot_key_count: 1,
      entity_id_count: 2,
      warning_level: 'none',
    });
    expect(call[3].bytes).toBeGreaterThan(0);
    expect(call[3].estimated_tokens).toBeGreaterThan(0);
  });

  it('renders the shared execution-mode control in the pet composer', async () => {
    const setExecutionMode = vi.fn();
    render(
      <FloatingAgentMiniChat
        controller={controller({ executionMode: 'yolo', setExecutionMode })}
        pageContext={pageContext}
        onOpenDesk={() => {}}
      />,
    );

    const yolo = screen.getByRole('button', { name: /yolo/i });
    expect(yolo).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(screen.getByRole('button', { name: /interactive/i }));

    expect(setExecutionMode).toHaveBeenCalledWith('interactive');
  });

  it('opens the full context drawer lazily', async () => {
    render(
      <FloatingAgentMiniChat
        controller={controller()}
        pageContext={pageContext}
        onOpenDesk={() => {}}
      />,
    );

    expect(screen.queryByText(/SNB-001/)).toBeInTheDocument();
    expect(screen.queryByText(/"position"/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^context$/i }));

    expect(screen.getByText(/entity_ids 2/i)).toBeInTheDocument();
    expect(screen.getByText(/snapshot 1/i)).toBeInTheDocument();
    expect(screen.getByText(/"position"/)).toBeInTheDocument();
  });

  it('warns without blocking large contexts', async () => {
    const sendMessage = vi.fn();
    const largeContext: PageContext = {
      ...pageContext,
      snapshot: { rows: 'x'.repeat(55_000) },
    };
    render(
      <FloatingAgentMiniChat
        controller={controller({ sendMessage })}
        pageContext={largeContext}
        onOpenDesk={() => {}}
      />,
    );

    expect(screen.getByText(/Full context will still be sent/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'use all context');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(sendMessage.mock.calls[0][1]).toEqual(largeContext);
    expect(sendMessage.mock.calls[0][3].warning_level).toBe('large');
  });

  it('can confirm action proposals from recent thread messages', async () => {
    const confirmAction = vi.fn();
    const activeThread: Thread = {
      id: 1,
      title: 'Thread',
      character: 'trader',
      messages: [
        {
          id: 10,
          role: 'assistant',
          character: 'trader',
          content: 'Ready to run pricing?',
          meta: {
            pending_actions: [
              {
                id: 'act-1',
                tool_name: 'run_pricing',
                label: 'Run pricing',
                summary: 'Portfolio #2',
                status: 'pending',
              },
            ],
          },
        },
      ],
    };

    render(
      <FloatingAgentMiniChat
        controller={controller({ activeThread, threads: [activeThread], confirmAction })}
        pageContext={pageContext}
        onOpenDesk={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /confirm action/i }));

    expect(confirmAction).toHaveBeenCalledWith(10, 'act-1');
  });

  it('sends selected reply options with the current page context', async () => {
    const sendMessage = vi.fn();
    const activeThread: Thread = {
      id: 1,
      title: 'Thread',
      character: 'trader',
      messages: [
        {
          id: 11,
          role: 'assistant',
          character: 'trader',
          content: [
            'Do you want to proceed?',
            '- **Yes**: Continue',
            '- **No**: Stop here',
          ].join('\n'),
          meta: {},
        },
      ],
    };

    render(
      <FloatingAgentMiniChat
        controller={controller({ activeThread, threads: [activeThread], sendMessage })}
        pageContext={pageContext}
        accountingDate="2026-05-17"
        onOpenDesk={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /No.*Stop here/i }));

    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0][0]).toBe('No');
    expect(sendMessage.mock.calls[0][1]).toEqual(pageContext);
    expect(sendMessage.mock.calls[0][2]).toBe('2026-05-17');
    expect(sendMessage.mock.calls[0][3]).toMatchObject({
      chip_count: 2,
      entity_id_count: 2,
      warning_level: 'none',
    });
  });
});

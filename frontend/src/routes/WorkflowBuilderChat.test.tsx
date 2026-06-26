import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkflowBuilderChat } from './WorkflowBuilderChat';
import type { AgentChatController } from '../hooks/useAgentChatController';
import type { Thread } from '../types';

function controller(overrides: Partial<AgentChatController> = {}): AgentChatController {
  const activeThread: Thread = {
    id: 1,
    title: 'Build',
    character: 'risk_manager',
    source: 'workflow_builder',
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
    ...overrides,
  };
}

describe('WorkflowBuilderChat', () => {
  it('renders a clean panel: header, New build, empty state — no thread rail', () => {
    render(<WorkflowBuilderChat controller={controller()} onNewBuild={vi.fn()} />);

    expect(screen.getByRole('heading', { name: /build a workflow/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /new build/i })).toBeInTheDocument();
    expect(screen.getByText(/describe the workflow you want to build/i)).toBeInTheDocument();
    // The Agent Desk thread rail must not be embedded here.
    expect(screen.queryByLabelText(/search threads/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/show arena threads/i)).not.toBeInTheDocument();
  });

  it('shows the transcript instead of the empty state once there are messages', () => {
    const c = controller({
      activeThread: {
        id: 1,
        title: 'Build',
        character: 'risk_manager',
        source: 'workflow_builder',
        messages: [{ id: 5, role: 'user', content: 'build a vega check', meta: {} }],
      },
    });
    render(<WorkflowBuilderChat controller={c} onNewBuild={vi.fn()} />);

    expect(screen.getByText('build a vega check')).toBeInTheDocument();
    expect(
      screen.queryByText(/describe the workflow you want to build/i),
    ).not.toBeInTheDocument();
  });

  it('fires onNewBuild when New build is clicked', () => {
    const onNewBuild = vi.fn();
    render(<WorkflowBuilderChat controller={controller()} onNewBuild={onNewBuild} />);
    fireEvent.click(screen.getByRole('button', { name: /new build/i }));
    expect(onNewBuild).toHaveBeenCalledTimes(1);
  });
});

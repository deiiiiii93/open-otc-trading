import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AgentDesk } from './AgentDesk';
import type { Thread } from '../types';

function thread(id: number, title: string, source?: string): Thread {
  return { id, title, character: 'trader', source, messages: [] };
}

const baseProps = {
  activeThreadId: null,
  sending: false,
  viewMode: 'detailed' as const,
  onChangeViewMode: vi.fn(),
  onSelectThread: vi.fn(),
  onNewThread: vi.fn(),
  onRenameThread: vi.fn(),
  onExportThread: vi.fn(),
  onDeleteThread: vi.fn(),
  onForkThread: vi.fn(),
  onSend: vi.fn(),
  onConfirmAction: vi.fn(),
  onDismissAction: vi.fn(),
};

describe('AgentDesk arena thread toggle', () => {
  it('hides arena threads by default', () => {
    const threads = [thread(1, 'Desk one'), thread(2, 'Arena run', 'arena')];
    render(<AgentDesk {...baseProps} threads={threads} />);

    expect(screen.getByText('Desk one')).toBeInTheDocument();
    expect(screen.queryByText('Arena run')).not.toBeInTheDocument();
  });

  it('shows arena threads when the controller has them switched on', () => {
    const threads = [thread(1, 'Desk one'), thread(2, 'Arena run', 'arena')];
    render(<AgentDesk {...baseProps} threads={threads} showArena />);

    expect(screen.getByText('Arena run')).toBeInTheDocument();
  });

  it('asks the controller to switch arena threads on, rather than filtering locally', () => {
    // Load-bearing: arena threads are no longer in the payload unless the
    // controller re-fetches with ?source=arena, so a purely local toggle would
    // reveal nothing. The checkbox must report upward.
    const onShowArenaChange = vi.fn();
    render(
      <AgentDesk
        {...baseProps}
        threads={[thread(1, 'Desk one')]}
        showArena={false}
        onShowArenaChange={onShowArenaChange}
      />,
    );

    fireEvent.click(screen.getByLabelText(/show arena threads/i));

    expect(onShowArenaChange).toHaveBeenCalledWith(true);
  });

  it('never shows internal workflow or hedge-evidence threads', () => {
    const threads = [
      thread(1, 'Desk one'),
      thread(2, 'Build chat', 'workflow_builder'),
      thread(3, 'Hedge proposal evidence', 'hedge_evidence'),
    ];
    render(<AgentDesk {...baseProps} threads={threads} showArena />);

    expect(screen.getByText('Desk one')).toBeInTheDocument();
    expect(screen.queryByText('Build chat')).not.toBeInTheDocument();
    expect(screen.queryByText('Hedge proposal evidence')).not.toBeInTheDocument();
  });
});

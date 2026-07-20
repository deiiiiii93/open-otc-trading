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
  it('hides arena threads by default and reveals them when toggled', () => {
    const threads = [thread(1, 'Desk one'), thread(2, 'Arena run', 'arena')];
    render(<AgentDesk {...baseProps} threads={threads} />);

    expect(screen.getByText('Desk one')).toBeInTheDocument();
    expect(screen.queryByText('Arena run')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/show arena threads/i));
    expect(screen.getByText('Arena run')).toBeInTheDocument();
  });

  it('never shows internal workflow or hedge-evidence threads', () => {
    const threads = [
      thread(1, 'Desk one'),
      thread(2, 'Build chat', 'workflow_builder'),
      thread(3, 'Hedge proposal evidence', 'hedge_evidence'),
    ];
    render(<AgentDesk {...baseProps} threads={threads} />);

    expect(screen.getByText('Desk one')).toBeInTheDocument();
    expect(screen.queryByText('Build chat')).not.toBeInTheDocument();
    expect(screen.queryByText('Hedge proposal evidence')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/show arena threads/i));
    expect(screen.queryByText('Build chat')).not.toBeInTheDocument();
    expect(screen.queryByText('Hedge proposal evidence')).not.toBeInTheDocument();
  });
});

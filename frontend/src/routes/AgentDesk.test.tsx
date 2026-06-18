import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { AgentDesk } from './AgentDesk';
import type { Thread } from '../types';

const thread: Thread = {
  id: 1,
  title: 'Morning desk',
  character: 'trader',
  messages: [
    { id: 1, role: 'user', character: null, content: 'Price this.', meta: {} },
    { id: 2, role: 'assistant', character: 'trader', content: 'Done.', meta: {} },
  ],
};

function renderDesk(overrides: Partial<ComponentProps<typeof AgentDesk>> = {}) {
  return render(
    <AgentDesk
      threads={[thread]}
      activeThreadId={1}
      sending={false}
      streaming={false}
      streamingItem={null}
      viewMode="compact"
      onChangeViewMode={() => {}}
      onSelectThread={() => {}}
      onNewThread={() => {}}
      onRenameThread={() => {}}
      onExportThread={() => {}}
      onDeleteThread={() => {}}
      onForkThread={() => {}}
      onSend={() => {}}
      onConfirmAction={() => {}}
      onDismissAction={() => {}}
      {...overrides}
    />,
  );
}

describe('AgentDesk', () => {
  it('renders active thread messages through MessageList', () => {
    renderDesk();
    expect(screen.getByText('Price this.')).toBeInTheDocument();
    expect(screen.getByText('Done.')).toBeInTheDocument();
  });

  it('renders the compact/detailed view mode toggle', () => {
    renderDesk({ viewMode: 'detailed' });
    expect(screen.getByRole('button', { name: 'Compact' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByRole('button', { name: 'Detailed' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('calls onChangeViewMode from the header toggle', async () => {
    const onChangeViewMode = vi.fn();
    renderDesk({ onChangeViewMode });
    await userEvent.click(screen.getByRole('button', { name: 'Detailed' }));
    expect(onChangeViewMode).toHaveBeenCalledWith('detailed');
  });

  it('passes streaming item into the message list', () => {
    renderDesk({
      streaming: true,
      streamingItem: {
        id: -1,
        role: 'assistant',
        character: 'trader',
        content: 'Streaming reply',
        meta: {},
      },
    });
    expect(screen.getByText('Streaming reply')).toBeInTheDocument();
  });

  it('sends the selected reply option through the composer send path', async () => {
    const onSend = vi.fn();
    renderDesk({
      onSend,
      threads: [{
        ...thread,
        messages: [
          {
            id: 20,
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
      }],
    });

    await userEvent.click(screen.getByRole('button', { name: /No.*Stop here/i }));
    expect(onSend).toHaveBeenCalledWith('No');
  });

  it('renames a thread from the thread rail', async () => {
    const onRenameThread = vi.fn();
    renderDesk({ onRenameThread });

    await userEvent.click(screen.getByRole('button', { name: 'Rename Morning desk' }));
    await userEvent.clear(screen.getByLabelText('Rename Morning desk'));
    await userEvent.type(screen.getByLabelText('Rename Morning desk'), 'Afternoon desk');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(onRenameThread).toHaveBeenCalledWith(1, 'Afternoon desk');
  });

  it('filters threads by title and message content', async () => {
    renderDesk({
      threads: [
        thread,
        {
          id: 2,
          title: 'Risk review',
          character: 'risk_manager',
          messages: [
            { id: 3, role: 'user', character: null, content: 'Check vega exposure.', meta: {} },
          ],
        },
      ],
    });

    await userEvent.type(screen.getByRole('searchbox', { name: /search threads/i }), 'vega');

    expect(screen.getByText('Risk review')).toBeInTheDocument();
    expect(screen.queryByText('Morning desk')).not.toBeInTheDocument();
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
  });

  it('renders a trace button per thread when onOpenTrace is provided', async () => {
    const onOpenTrace = vi.fn();
    renderDesk({ onOpenTrace });

    await userEvent.click(
      screen.getByRole('button', { name: 'View trace for Morning desk' }),
    );
    expect(onOpenTrace).toHaveBeenCalledWith(1);
  });

  it('renders no trace button when onOpenTrace is absent', () => {
    renderDesk();
    expect(
      screen.queryByRole('button', { name: /View trace/ }),
    ).not.toBeInTheDocument();
  });

  it('surfaces export, fork, and delete thread actions', async () => {
    const onExportThread = vi.fn();
    const onForkThread = vi.fn();
    const onDeleteThread = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderDesk({ onExportThread, onForkThread, onDeleteThread });

    await userEvent.click(screen.getByRole('button', { name: 'Export Morning desk' }));
    await userEvent.click(screen.getByRole('button', { name: 'Fork Morning desk' }));
    await userEvent.click(screen.getByRole('button', { name: 'Delete Morning desk' }));

    expect(onExportThread).toHaveBeenCalledWith(1);
    expect(onForkThread).toHaveBeenCalledWith(1);
    expect(onDeleteThread).toHaveBeenCalledWith(1);
  });
});

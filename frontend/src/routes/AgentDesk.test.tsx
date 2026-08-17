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

  it('reports the search term upward instead of filtering locally', async () => {
    // The list is one page, so a local filter would only ever search what was
    // fetched. The term goes to the controller, which queries the server across
    // every thread in the database.
    const onThreadSearchChange = vi.fn();
    renderDesk({ onThreadSearchChange });

    await userEvent.type(
      screen.getByRole('searchbox', { name: /search threads/i }),
      'v',
    );

    expect(onThreadSearchChange).toHaveBeenCalledWith('v');
  });

  it('renders whatever the server returned for a search, without re-filtering', async () => {
    renderDesk({
      threadSearch: 'vega',
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

    // 'Morning desk' does not contain "vega"; the server decided it matched, so
    // the rail must show it rather than second-guess the query.
    expect(screen.getByText('Risk review')).toBeInTheDocument();
    expect(screen.getByText('Morning desk')).toBeInTheDocument();
    expect(screen.getByText('2 matching')).toBeInTheDocument();
  });

  it('offers Load more only when another page may exist', async () => {
    const onLoadMoreThreads = vi.fn();
    const { rerender } = renderDesk();
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument();

    rerender(
      <AgentDesk
        threads={[thread]}
        activeThreadId={1}
        sending={false}
        streaming={false}
        streamingItem={null}
        viewMode="compact"
        moreThreadsAvailable
        onLoadMoreThreads={onLoadMoreThreads}
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
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /load more/i }));
    expect(onLoadMoreThreads).toHaveBeenCalled();
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

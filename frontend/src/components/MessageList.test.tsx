import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MessageList } from './MessageList';
import type { ChatMessage as ChatMessageType } from '../types';

function makeMsg(id: number, role: 'user' | 'assistant', content: string): ChatMessageType {
  return { id, role, character: role === 'assistant' ? 'trader' : null, content, meta: {} };
}

function setScrollGeometry(
  node: HTMLElement,
  opts: { scrollTop: number; scrollHeight: number; clientHeight: number },
) {
  Object.defineProperty(node, 'scrollTop', {
    configurable: true,
    get: () => opts.scrollTop,
    set: () => {},
  });
  Object.defineProperty(node, 'scrollHeight', {
    configurable: true,
    get: () => opts.scrollHeight,
  });
  Object.defineProperty(node, 'clientHeight', {
    configurable: true,
    get: () => opts.clientHeight,
  });
}

describe('MessageList', () => {
  it('renders one ChatBubble per message', () => {
    const items = [makeMsg(1, 'user', 'hi'), makeMsg(2, 'assistant', 'hello')];
    render(
      <MessageList
        items={items}
        streaming={false}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    expect(screen.getByText('hi')).toBeInTheDocument();
    expect(screen.getByText('hello')).toBeInTheDocument();
  });

  it('does not render the pill when pinned at bottom', () => {
    const items = [makeMsg(1, 'assistant', 'hello')];
    const { container } = render(
      <MessageList
        items={items}
        streaming={false}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    const list = container.querySelector('.wl-message-list__scroll') as HTMLElement;
    setScrollGeometry(list, { scrollTop: 880, scrollHeight: 1000, clientHeight: 120 });
    act(() => {
      list.dispatchEvent(new Event('scroll'));
    });
    expect(container.querySelector('.wl-new-messages-pill')).toBeFalsy();
  });

  it('renders the pill when scrolled up and streaming', () => {
    const items = [makeMsg(1, 'assistant', 'hello')];
    const { container } = render(
      <MessageList
        items={items}
        streaming
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    const list = container.querySelector('.wl-message-list__scroll') as HTMLElement;
    setScrollGeometry(list, { scrollTop: 100, scrollHeight: 1000, clientHeight: 120 });
    act(() => {
      list.dispatchEvent(new Event('scroll'));
    });
    expect(container.querySelector('.wl-new-messages-pill')).toBeTruthy();
  });

  it('shows count pill when scrolled up and a new message arrives (not streaming)', () => {
    const items = [makeMsg(1, 'user', 'hi')];
    const { container, rerender } = render(
      <MessageList
        items={items}
        streaming={false}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    const list = container.querySelector('.wl-message-list__scroll') as HTMLElement;
    setScrollGeometry(list, { scrollTop: 100, scrollHeight: 1000, clientHeight: 120 });
    act(() => {
      list.dispatchEvent(new Event('scroll'));
    });

    rerender(
      <MessageList
        items={[...items, makeMsg(2, 'assistant', 'hello')]}
        streaming={false}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />,
    );
    const pill = container.querySelector('.wl-new-messages-pill');
    expect(pill).toBeTruthy();
    expect(pill?.textContent).toMatch(/1/);
  });

  it('enables reply options only on the latest completed assistant message', async () => {
    const onSelectReplyOption = vi.fn();
    const first = makeMsg(1, 'assistant', [
      'Do you want the first choice?',
      '- **Yes**: First yes',
      '- **No**: First no',
    ].join('\n'));
    const second = makeMsg(2, 'assistant', [
      'Do you want the second choice?',
      '- **Yes**: Second yes',
      '- **No**: Second no',
    ].join('\n'));

    render(
      <MessageList
        items={[first, second]}
        streaming={false}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
      />,
    );

    expect(screen.getByText(/First yes/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /First yes/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Yes.*Second yes/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /No.*Second no/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(2, 'No');
  });

  it('does not enable reply options while streaming', () => {
    render(
      <MessageList
        items={[makeMsg(1, 'assistant', [
          'Do you want to proceed?',
          '- **Yes**: Run it',
          '- **No**: Stop here',
        ].join('\n'))]}
        streaming
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
      />,
    );

    expect(screen.queryByRole('group', { name: /suggested replies/i })).not.toBeInTheDocument();
  });
});

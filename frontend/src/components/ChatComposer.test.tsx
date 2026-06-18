import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatComposer } from './ChatComposer';

describe('ChatComposer', () => {
  it('renders textarea and send button', () => {
    render(<ChatComposer onSend={() => {}} sending={false} />);
    expect(screen.getByLabelText(/ask anything/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument();
  });

  it('calls onSend with current text', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    await userEvent.type(screen.getByLabelText(/ask anything/i), 'price snowball');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(onSend).toHaveBeenCalledWith('price snowball');
  });

  it('clears input after send', async () => {
    render(<ChatComposer onSend={() => {}} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i) as HTMLTextAreaElement;
    await userEvent.type(textarea, 'hello');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(textarea.value).toBe('');
  });

  it('sends on Enter', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i);
    await userEvent.type(textarea, 'price snowball');
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('price snowball');
  });

  it('inserts a newline instead of sending on Shift+Enter', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i);
    await userEvent.type(textarea, 'line one');
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('does not send on Enter while an IME composition is active', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    const textarea = screen.getByLabelText(/ask anything/i);
    await userEvent.type(textarea, '雪球');
    // While composing CJK input, Enter confirms a candidate and must not send.
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('does not send on Enter when sending is already in flight', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending />);
    const textarea = screen.getByLabelText(/ask anything/i);
    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('disables send when sending=true', () => {
    render(<ChatComposer onSend={() => {}} sending />);
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
  });

  it('does not call onSend when text is empty', async () => {
    const onSend = vi.fn();
    render(<ChatComposer onSend={onSend} sending={false} />);
    await userEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it('shows Streaming… when streaming prop is true', () => {
    render(<ChatComposer onSend={() => {}} sending streaming />);
    expect(screen.getByRole('button', { name: /streaming/i })).toBeInTheDocument();
  });

  it('shows a stop button while streaming when stop handler is provided', async () => {
    const onStopStreaming = vi.fn();
    render(<ChatComposer onSend={() => {}} sending streaming onStopStreaming={onStopStreaming} />);

    await userEvent.click(screen.getByRole('button', { name: /stop/i }));

    expect(onStopStreaming).toHaveBeenCalledTimes(1);
  });

  it('toggles YOLO mode when provided', async () => {
    const onChangeYoloMode = vi.fn();
    render(
      <ChatComposer
        onSend={() => {}}
        sending={false}
        yoloMode={false}
        onChangeYoloMode={onChangeYoloMode}
      />,
    );

    await userEvent.click(screen.getByRole('checkbox', { name: /yolo/i }));

    expect(onChangeYoloMode).toHaveBeenCalledWith(true);
  });

  it('disables YOLO toggle while streaming', () => {
    render(
      <ChatComposer
        onSend={() => {}}
        sending
        streaming
        yoloMode
        onChangeYoloMode={() => {}}
      />,
    );

    expect(screen.getByRole('checkbox', { name: /yolo/i })).toBeDisabled();
  });
});

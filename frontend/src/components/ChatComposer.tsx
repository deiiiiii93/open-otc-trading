import { Send, Square } from 'lucide-react';
import { useId, useState, type KeyboardEvent } from 'react';
import type { AgentChannel, AgentExecutionMode, AgentModelSelection } from '../types';
import { Button } from './Button';
import { ModelPicker } from './ModelPicker';
import './ChatComposer.css';

type Props = {
  onSend: (message: string) => void;
  sending: boolean;
  streaming?: boolean;
  channels?: AgentChannel[];
  selectedModel?: AgentModelSelection | null;
  executionMode?: AgentExecutionMode;
  onChangeModel?: (s: AgentModelSelection) => void;
  onChangeMode?: (mode: AgentExecutionMode) => void;
  onStopStreaming?: () => void;
  onRefreshModels?: () => void | Promise<void>;
  compactModelPicker?: boolean;
};

const MODE_OPTIONS: ReadonlyArray<{
  value: AgentExecutionMode;
  label: string;
  title: string;
}> = [
  {
    value: 'interactive',
    label: 'Interactive',
    title: 'Confirmation prompts surface to you before write actions run.',
  },
  {
    value: 'auto',
    label: 'AUTO',
    title: 'Auto-clears confirmation prompts; the agent may still ask via reply-option cards.',
  },
  {
    value: 'yolo',
    label: 'YOLO',
    title: 'Headless — auto-executes, never prompts. Money-adjacent actions run without confirmation.',
  },
];

export function ChatComposer({
  onSend, sending, streaming,
  channels, selectedModel, executionMode = 'auto',
  onChangeModel, onChangeMode, onStopStreaming, onRefreshModels, compactModelPicker = false,
}: Props) {
  const [text, setText] = useState('');
  const id = useId();

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    onSend(trimmed);
    setText('');
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter') return;
    // Shift+Enter inserts a newline; let the textarea handle it.
    if (event.shiftKey) return;
    // Mid-IME composition, Enter confirms a CJK candidate — never a send.
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    handleSend();
  };

  return (
    <div className="wl-composer">
      <label htmlFor={id} className="wl-composer__label">Ask anything</label>
      <textarea
        id={id}
        className="wl-composer__textarea"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={3}
        placeholder="Quote a snowball, run risk, generate a report…"
      />
      <div className="wl-composer__actions">
        {channels && onChangeModel && (
          <ModelPicker
            channels={channels}
            selected={selectedModel ?? null}
            onChange={onChangeModel}
            onRefresh={onRefreshModels}
            compact={compactModelPicker}
          />
        )}
        {onChangeMode && (
          <div className="wl-composer__mode" role="group" aria-label="Execution mode">
            {MODE_OPTIONS.map((option) => {
              const active = executionMode === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  className={`wl-composer__mode-btn${active ? ' is-active' : ''}`}
                  title={option.title}
                  aria-pressed={active}
                  disabled={sending || !!streaming}
                  onClick={() => onChangeMode(option.value)}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        )}
        {streaming && onStopStreaming ? (
          <Button type="button" variant="danger" onClick={onStopStreaming}>
            <Square size={16} aria-hidden="true" />
            Stop
          </Button>
        ) : (
          <Button variant="primary" onClick={handleSend} disabled={sending || text.trim().length === 0}>
            <Send size={16} aria-hidden="true" />
            {streaming ? 'Streaming...' : sending ? 'Sending...' : 'Send'}
          </Button>
        )}
      </div>
    </div>
  );
}

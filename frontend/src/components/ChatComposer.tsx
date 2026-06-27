import { Send, Square } from 'lucide-react';
import { useId, useState, type KeyboardEvent } from 'react';
import type {
  AgentChannel,
  AgentExecutionMode,
  AgentModelSelection,
  DeskWorkflowSummary,
} from '../types';
import { Button } from './Button';
import { ModelPicker } from './ModelPicker';
import { RESERVED_COMPOSER_COMMANDS } from '../lib/reservedCommands';
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
  workflows?: DeskWorkflowSummary[];
  onLaunchWorkflow?: (slug: string, mode: 'auto' | 'yolo') => void;
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
  workflows, onLaunchWorkflow,
}: Props) {
  const [text, setText] = useState('');
  const id = useId();

  const firstToken = text.startsWith('/') ? text.slice(1).split(/\s+/)[0] ?? '' : null;
  const showPicker =
    firstToken !== null &&
    !text.includes(' ') &&
    !RESERVED_COMPOSER_COMMANDS.has(firstToken) &&
    !!onLaunchWorkflow &&
    (workflows?.length ?? 0) > 0;
  const matches = showPicker
    ? (workflows ?? []).filter(
        (w) =>
          w.slug.includes(firstToken ?? '') ||
          w.title.toLowerCase().includes((firstToken ?? '').toLowerCase()),
      )
    : [];

  const launch = (w: DeskWorkflowSummary) => {
    onLaunchWorkflow?.(w.slug, w.default_mode);
    setText('');
  };

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    // A bare "/slug" that matches a workflow launches it instead of sending chat.
    if (showPicker && matches.length > 0) {
      launch(matches[0]);
      return;
    }
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
      {showPicker && matches.length > 0 && (
        <ul className="wl-composer__slash" role="listbox" aria-label="Workflows">
          {matches.map((w) => (
            <li key={w.slug}>
              <button
                type="button"
                className="wl-composer__slash-item"
                role="option"
                aria-selected={false}
                onClick={() => launch(w)}
              >
                <strong>/{w.slug}</strong>
                <span>{w.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
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

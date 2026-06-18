import { Send, Square } from 'lucide-react';
import { useId, useState, type KeyboardEvent } from 'react';
import type { AgentChannel, AgentModelSelection } from '../types';
import { Button } from './Button';
import { ModelPicker } from './ModelPicker';
import './ChatComposer.css';

type Props = {
  onSend: (message: string) => void;
  sending: boolean;
  streaming?: boolean;
  channels?: AgentChannel[];
  selectedModel?: AgentModelSelection | null;
  yoloMode?: boolean;
  onChangeModel?: (s: AgentModelSelection) => void;
  onChangeYoloMode?: (enabled: boolean) => void;
  onStopStreaming?: () => void;
  onRefreshModels?: () => void | Promise<void>;
  compactModelPicker?: boolean;
};

export function ChatComposer({
  onSend, sending, streaming,
  channels, selectedModel, yoloMode = false,
  onChangeModel, onChangeYoloMode, onStopStreaming, onRefreshModels, compactModelPicker = false,
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
        {onChangeYoloMode && (
          <label
            className={`wl-composer__yolo${yoloMode ? ' is-active' : ''}`}
            title="YOLO uses LangChain auto-approval for ordinary write actions. Irreversible actions still pause for confirmation."
          >
            <input
              type="checkbox"
              checked={yoloMode}
              disabled={sending || !!streaming}
              onChange={(event) => onChangeYoloMode(event.currentTarget.checked)}
            />
            <span>YOLO</span>
          </label>
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

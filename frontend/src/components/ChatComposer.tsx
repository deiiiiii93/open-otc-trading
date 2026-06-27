import { Send, Square } from 'lucide-react';
import { useId, useRef, useState, type KeyboardEvent } from 'react';
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

// Built-in composer slash-commands (not workflows) — surfaced in the slash menu so they
// are discoverable. `/goal <description>` is intercepted by the chat controller, which
// frames an acceptance contract; see useAgentChatController + reservedCommands.
const BUILTIN_COMMANDS: ReadonlyArray<{ name: string; title: string }> = [
  { name: 'goal', title: 'Define a goal with acceptance criteria for the agent to pursue' },
];

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
  // Index of the highlighted slash-menu item (keyboard/hover cursor). Reset to 0 whenever
  // the text — and therefore the match list — changes; clamped at use sites for safety.
  const [activeIndex, setActiveIndex] = useState(0);
  const id = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  // The "/token" being typed before any space (lower-cased); null when not composing a
  // command. Built-in commands (e.g. /goal) match by name prefix and need no workflows;
  // workflow matches are gated on a launcher and exclude reserved built-in names.
  const slashToken =
    text.startsWith('/') && !text.includes(' ') ? text.slice(1).toLowerCase() : null;
  const builtinMatches =
    slashToken === null ? [] : BUILTIN_COMMANDS.filter((c) => c.name.startsWith(slashToken));
  const workflowMatches =
    slashToken !== null && !RESERVED_COMPOSER_COMMANDS.has(slashToken) && onLaunchWorkflow
      ? (workflows ?? []).filter(
          (w) => w.slug.includes(slashToken) || w.title.toLowerCase().includes(slashToken),
        )
      : [];

  const launch = (w: DeskWorkflowSummary) => {
    onLaunchWorkflow?.(w.slug, w.default_mode);
    setText('');
  };

  // A built-in command needs an argument, so selecting it fills "/name " and keeps focus
  // for the user to type the rest — it does NOT submit.
  const fillCommand = (name: string) => {
    setText(`/${name} `);
    textareaRef.current?.focus();
  };

  // Built-ins and workflows flattened into one indexable list so a single keyboard cursor
  // (activeIndex) can walk both. `select` is the Enter/click action; `autofill` is what
  // Tab completes the token to without launching/sending.
  type MenuItem = { key: string; token: string; title: string; autofill: string; select: () => void };
  const menuItems: MenuItem[] = [
    ...builtinMatches.map((c) => ({
      key: `builtin-${c.name}`,
      token: c.name,
      title: c.title,
      autofill: `/${c.name} `,
      select: () => fillCommand(c.name),
    })),
    ...workflowMatches.map((w) => ({
      key: w.slug,
      token: w.slug,
      title: w.title,
      autofill: `/${w.slug}`,
      select: () => launch(w),
    })),
  ];
  const showMenu = menuItems.length > 0;
  const activeIdx = showMenu ? Math.min(activeIndex, menuItems.length - 1) : 0;

  // A native <textarea> can't colour part of its own text, so a mirror backdrop renders the
  // same text with the leading command token wrapped in a tinted span. Only colour a token
  // we actually recognise (a built-in name or a known workflow slug) — never arbitrary text.
  const leadingToken =
    text.startsWith('/') ? text.slice(1).split(/\s/, 1)[0].toLowerCase() : '';
  const isRecognizedCommand =
    leadingToken.length > 0 &&
    (BUILTIN_COMMANDS.some((c) => c.name === leadingToken) ||
      (workflows ?? []).some((w) => w.slug === leadingToken));
  // Slice on the original text to preserve the user's casing in the rendered token.
  const highlightNodes = isRecognizedCommand
    ? [
        <span key="cmd" className="wl-composer__cmd-token">
          {text.slice(0, leadingToken.length + 1)}
        </span>,
        text.slice(leadingToken.length + 1),
      ]
    : text;

  // Keep the backdrop scrolled in lock-step with the textarea so the overlay never drifts
  // once the input overflows its visible height.
  const syncScroll = (event: { currentTarget: HTMLTextAreaElement }) => {
    const el = backdropRef.current;
    if (!el) return;
    el.scrollTop = event.currentTarget.scrollTop;
    el.scrollLeft = event.currentTarget.scrollLeft;
  };

  const setTextAndReset = (value: string) => {
    setText(value);
    setActiveIndex(0);
  };

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    // Bare built-in command (e.g. "/goal" with no description): prompt for its argument.
    if (slashToken !== null && BUILTIN_COMMANDS.some((c) => c.name === slashToken)) {
      fillCommand(slashToken);
      return;
    }
    // A bare "/slug" that matches a workflow launches it instead of sending chat.
    if (workflowMatches.length > 0) {
      launch(workflowMatches[0]);
      return;
    }
    onSend(trimmed);
    setText('');
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // While the slash menu is open, arrows move the highlight, Tab autocompletes the
    // highlighted slug, and Enter selects it (instead of sending).
    if (showMenu) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActiveIndex((i) => (Math.min(i, menuItems.length - 1) + 1) % menuItems.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActiveIndex(
          (i) => (Math.min(i, menuItems.length - 1) - 1 + menuItems.length) % menuItems.length,
        );
        return;
      }
      if (event.key === 'Tab' && !event.shiftKey) {
        event.preventDefault();
        setText(menuItems[activeIdx].autofill);
        textareaRef.current?.focus();
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault();
        menuItems[activeIdx].select();
        return;
      }
    }
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
      {showMenu && (
        <ul className="wl-composer__slash" role="listbox" aria-label="Commands">
          {menuItems.map((item, idx) => (
            <li key={item.key}>
              <button
                type="button"
                className={`wl-composer__slash-item${idx === activeIdx ? ' is-active' : ''}`}
                role="option"
                aria-selected={idx === activeIdx}
                onClick={item.select}
                onMouseEnter={() => setActiveIndex(idx)}
              >
                <strong className="wl-composer__slash-slug">/{item.token}</strong>
                <span>{item.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="wl-composer__input">
        <div className="wl-composer__highlights" aria-hidden="true" ref={backdropRef}>
          {highlightNodes}
        </div>
        <textarea
          id={id}
          ref={textareaRef}
          className="wl-composer__textarea"
          value={text}
          onChange={(e) => setTextAndReset(e.target.value)}
          onKeyDown={handleKeyDown}
          onScroll={syncScroll}
          rows={3}
          placeholder="Quote a snowball, run risk, generate a report…"
        />
      </div>
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

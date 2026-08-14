import { Paperclip, Send, Square } from 'lucide-react';
import { useId, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react';
import type {
  AgentChannel,
  AgentExecutionMode,
  AgentModelOption,
  AgentModelSelection,
  AgentReasoningEffortChoice,
  DeskWorkflowSummary,
} from '../types';
import type { ViewMode } from '../hooks/useViewMode';
import { Button } from './Button';
import { Chip } from './Chip';
import { ModelPicker } from './ModelPicker';
import { Select } from './Select';
import { RESERVED_COMPOSER_COMMANDS } from '../lib/reservedCommands';
import './ChatComposer.css';

// The confirmation pipeline only parses PDF/DOCX (services/confirmations/extract.py
// raises on anything else), so the picker filters in code as well as via `accept` —
// `accept` is a hint the OS dialog can bypass (drag-drop, "All files"), and an
// unparseable upload would only fail later, inside the agent turn.
export const ATTACHMENT_EXTENSIONS = ['.pdf', '.docx'] as const;

function isAttachable(file: File): boolean {
  const name = file.name.toLowerCase();
  return ATTACHMENT_EXTENSIONS.some((ext) => name.endsWith(ext));
}

type Props = {
  onSend: (message: string, attachments?: File[]) => void;
  sending: boolean;
  streaming?: boolean;
  channels?: AgentChannel[];
  selectedModel?: AgentModelSelection | null;
  executionMode?: AgentExecutionMode;
  reasoningEffort?: AgentReasoningEffortChoice;
  onChangeModel?: (s: AgentModelSelection) => void;
  onChangeMode?: (mode: AgentExecutionMode) => void;
  onChangeReasoningEffort?: (effort: AgentReasoningEffortChoice) => void;
  onStopStreaming?: () => void;
  onRefreshModels?: () => void | Promise<void>;
  compactModelPicker?: boolean;
  viewMode?: ViewMode;
  onChangeViewMode?: (mode: ViewMode) => void;
  workflows?: DeskWorkflowSummary[];
  onLaunchWorkflow?: (slug: string, mode: 'auto' | 'yolo') => void;
  onRequestParams?: (workflow: DeskWorkflowSummary) => void;
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

// Reasoning effort, weakest → strongest. "Default" is not a level — it sends no
// `reasoning_effort` at all, so the provider's own default applies (what every
// turn did before this control existed). Higher effort measurably lowers
// tool-call count on this desk (~22% fewer between low and high on a paired
// arena A/B), so it is a real operating choice, not a cosmetic one.
//
// The menu is FILTERED PER MODEL from `AgentModelOption.reasoning_efforts` (a
// vendored models.dev snapshot), because there is no universal ladder: most
// models take low/medium/high, the GPT-5.6 family takes none…max, GLM-5.2 takes
// only high/max, and Qwen3.7 / MiniMax M3 have no ladder at all. Offering a level
// the server would reject is worse than not offering it — the request fails at
// send time instead of at pick time.
const EFFORT_LABELS: Record<string, string> = {
  none: 'None',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'X-High',
  max: 'Max',
};
// Display order; anything the model declares that is not listed here still shows,
// appended, so an upstream addition degrades to "visible but unsorted" rather
// than "silently hidden".
const EFFORT_ORDER = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

export function effortOptionsFor(
  model: AgentModelOption | null | undefined,
): ReadonlyArray<{ value: AgentReasoningEffortChoice; label: string }> {
  const declared = model?.reasoning_efforts;
  // `undefined` = the caller supplied no model info at all (e.g. a bare composer
  // render) → offer the full ladder. `[]` = the model genuinely accepts none →
  // offer only "Default".
  const levels = declared ?? EFFORT_ORDER;
  const ordered = [
    ...EFFORT_ORDER.filter((e) => levels.includes(e)),
    ...levels.filter((e) => !EFFORT_ORDER.includes(e)),
  ];
  return [
    { value: 'default' as AgentReasoningEffortChoice, label: 'Default' },
    ...ordered.map((e) => ({
      value: e as AgentReasoningEffortChoice,
      label: EFFORT_LABELS[e] ?? e,
    })),
  ];
}

export function ChatComposer({
  onSend, sending, streaming,
  channels, selectedModel, executionMode = 'auto', reasoningEffort = 'default',
  onChangeModel, onChangeMode, onChangeReasoningEffort,
  onStopStreaming, onRefreshModels, compactModelPicker = false,
  viewMode, onChangeViewMode,
  workflows, onLaunchWorkflow, onRequestParams,
}: Props) {
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState<File[]>([]);
  const [attachmentNotice, setAttachmentNotice] = useState<string | null>(null);
  // Index of the highlighted slash-menu item (keyboard/hover cursor). Reset to 0 whenever
  // the text — and therefore the match list — changes; clamped at use sites for safety.
  const [activeIndex, setActiveIndex] = useState(0);
  const id = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // The selected model's own effort ladder. Resolved from `channels` (the server's
  // /api/agent/models payload) rather than trusted from `selectedModel`, which is
  // only a {channel, provider, model} triple and carries no capability data.
  const selectedOption: AgentModelOption | undefined = selectedModel
    ? (channels ?? [])
        .find((c) => c.name === selectedModel.channel)
        ?.models.find(
          (m) => m.model === selectedModel.model && m.provider === selectedModel.provider,
        )
    : undefined;
  const effortOptions = effortOptionsFor(selectedOption);

  const addFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files ?? []);
    const accepted = picked.filter(isAttachable);
    const rejected = picked.filter((f) => !isAttachable(f));
    if (accepted.length > 0) {
      setAttachments((prev) => [
        ...prev,
        // Same name+size twice is a re-pick of one file, not two trades — dropping the
        // duplicate keeps the agent from parsing (and offering to book) the same
        // confirmation twice.
        ...accepted.filter((f) => !prev.some((p) => p.name === f.name && p.size === f.size)),
      ]);
    }
    setAttachmentNotice(
      rejected.length > 0
        ? `Only ${ATTACHMENT_EXTENSIONS.join(' / ')} files can be attached — skipped ${rejected
            .map((f) => f.name)
            .join(', ')}.`
        : null,
    );
    // Reset the input so re-picking the same file after removing it still fires onChange.
    event.target.value = '';
  };

  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
    setAttachmentNotice(null);
  };

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
    if ((w.params?.length ?? 0) > 0 && onRequestParams) {
      onRequestParams(w);
      setText('');
      return;
    }
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
    if (sending) return;
    // Attachments alone are a legitimate turn ("here's the confirmation") — the agent
    // receives them through the manifest the backend appends to the run content.
    if (!trimmed && attachments.length === 0) return;
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
    onSend(trimmed, attachments.length > 0 ? attachments : undefined);
    setText('');
    setAttachments([]);
    setAttachmentNotice(null);
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
      {(attachments.length > 0 || attachmentNotice) && (
        <div className="wl-composer__attachments">
          {attachments.map((file, idx) => (
            <Chip
              key={`${file.name}-${file.size}`}
              onRemove={() => removeAttachment(idx)}
            >
              {file.name}
            </Chip>
          ))}
          {attachmentNotice && (
            <span className="wl-composer__attachment-notice" role="status">
              {attachmentNotice}
            </span>
          )}
        </div>
      )}
      <div className="wl-composer__actions">
        <input
          ref={fileInputRef}
          type="file"
          className="wl-composer__file-input"
          accept={ATTACHMENT_EXTENSIONS.join(',')}
          multiple
          onChange={addFiles}
          data-testid="composer-file-input"
        />
        <Button
          type="button"
          variant="ghost"
          iconOnly
          onClick={() => fileInputRef.current?.click()}
          disabled={sending}
          aria-label="Attach confirmation files"
          title="Attach trade confirmation files (PDF / DOCX)"
        >
          <Paperclip size={16} aria-hidden="true" />
        </Button>
        {channels && onChangeModel && (
          <ModelPicker
            channels={channels}
            selected={selectedModel ?? null}
            onChange={onChangeModel}
            onRefresh={onRefreshModels}
            compact={compactModelPicker}
          />
        )}
        {onChangeReasoningEffort && (
          <Select
            variant="inline"
            label="Effort"
            // Switching to a model that lacks the current level must not leave a
            // stale pick that the server will reject; show Default instead.
            value={effortOptions.some((o) => o.value === reasoningEffort)
              ? reasoningEffort
              : 'default'}
            options={effortOptions.map((o) => ({ value: o.value, label: o.label }))}
            onChange={(v) => onChangeReasoningEffort(v as AgentReasoningEffortChoice)}
            disabled={sending || !!streaming || effortOptions.length === 1}
          />
        )}
        {onChangeMode && (
          <Select
            variant="inline"
            label="Mode"
            value={executionMode}
            options={MODE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            onChange={(v) => onChangeMode(v as AgentExecutionMode)}
            disabled={sending || !!streaming}
          />
        )}
        {viewMode && onChangeViewMode && (
          <Select
            variant="inline"
            label="Detail"
            value={viewMode}
            options={[
              { value: 'detailed', label: 'Detailed' },
              { value: 'compact', label: 'Compact' },
            ]}
            onChange={(v) => onChangeViewMode(v as ViewMode)}
          />
        )}
        {streaming && onStopStreaming ? (
          <Button type="button" variant="danger" onClick={onStopStreaming}>
            <Square size={16} aria-hidden="true" />
            Stop
          </Button>
        ) : (
          <Button
            variant="primary"
            onClick={handleSend}
            disabled={sending || (text.trim().length === 0 && attachments.length === 0)}
          >
            <Send size={16} aria-hidden="true" />
            {streaming ? 'Streaming...' : sending ? 'Sending...' : 'Send'}
          </Button>
        )}
      </div>
    </div>
  );
}

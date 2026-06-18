import {
  Children,
  isValidElement,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  personaDisplayLabel,
  proposalToolName,
  type AgentChannel,
  type AgentActionProposal,
  type AgentAsset,
  type AgentTodoItem,
  type ChatMessage as ChatMessageType,
  type ReplyOptionMeta,
  type TaskRun,
  type TermFormMeta,
  type ToolEvent,
} from '../types';
import { ActionProposal } from './ActionProposal';
import { ToolTimeline } from './ToolTimeline';
import type { ViewMode } from '../hooks/useViewMode';
import { colorForProvider } from './providerColors';
import { extractReplyOptions, type ReplyOption } from './replyOptions';
import { TermForm } from './TermForm';
import './ChatBubble.css';

type Props = {
  message: ChatMessageType;
  viewMode: ViewMode;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
  onSelectReplyOption?: (messageId: number, option: string) => void;
  onConfirmCostPreview?: () => void;
  replyOptionsEnabled?: boolean;
  isStreaming?: boolean;
  channels?: AgentChannel[];
  confirmingActionIds?: ReadonlySet<string>;
  taskRunsById?: Record<number, TaskRun>;
};

type CodeElementProps = {
  className?: string;
  children?: ReactNode;
};

const remarkPlugins = [remarkGfm];

export function ChatBubble({
  message,
  viewMode,
  onConfirmAction,
  onDismissAction,
  onSelectReplyOption,
  onConfirmCostPreview,
  replyOptionsEnabled = false,
  isStreaming,
  channels = [],
  confirmingActionIds,
  taskRunsById,
}: Props) {
  const variant = message.role === 'user' ? 'user' : 'assistant';
  const meta = message.meta ?? {};
  const pendingActions: AgentActionProposal[] = !isStreaming && Array.isArray(meta.pending_actions)
    ? (meta.pending_actions as AgentActionProposal[])
    : [];
  const processEvents = (meta.process_events ?? []) as ToolEvent[] | string[];
  const todos = normalizeTodos(meta.todos);
  const modelChip = meta.model_selection
    ? resolveModelChip(channels, meta.model_selection)
    : null;
  const assets = Array.isArray(meta.assets) ? (meta.assets as AgentAsset[]) : [];
  const renderStructuredReplyOptions = canRenderStructuredReplyOptions(meta);
  const structuredOptions: ReplyOptionMeta[] =
    variant === 'assistant'
    && !isStreaming
    && pendingActions.length === 0
    && renderStructuredReplyOptions
    && Array.isArray(meta.reply_options)
      ? (meta.reply_options as ReplyOptionMeta[]).filter(
          (o) => o && typeof o.label === 'string' && o.label.trim().length > 0,
        )
      : [];
  const heuristicExtraction = structuredOptions.length === 0
    && variant === 'assistant'
    && !isStreaming
    && pendingActions.length === 0
    ? extractReplyOptions(message.content)
    : null;
  const termForm: TermFormMeta | null =
    variant === 'assistant'
    && !isStreaming
    && pendingActions.length === 0
    && meta.term_form
    && Array.isArray((meta.term_form as TermFormMeta).fields)
    && (meta.term_form as TermFormMeta).fields.length > 0
      ? (meta.term_form as TermFormMeta)
      : null;
  const showReplyOptions = !!(
    replyOptionsEnabled
    && onSelectReplyOption
    && (
      structuredOptions.length > 0
      || (heuristicExtraction && heuristicExtraction.options.length > 0)
    )
  );
  const visibleContent = showReplyOptions && structuredOptions.length === 0 && heuristicExtraction
    ? heuristicExtraction.contentWithoutOptions
    : message.content;
  const optionsToRender: ReplyOption[] = structuredOptions.length > 0
    ? structuredOptions
    : (heuristicExtraction?.options ?? []);
  const linkedContent = linkifyAssetPaths(visibleContent, assets);
  const presentation = resolveAssistantContentPresentation({
    content: linkedContent,
    isAssistant: variant === 'assistant',
    isStreaming: !!isStreaming,
    processEvents,
  });
  const { bodyContent, reasoningContent } = presentation;
  const markdownComponents = markdownComponentsForAssets(assets);
  const toolTimelineDefaultOpen = !!(
    isStreaming
    || hasRunningTool(processEvents)
    || hasErroredTool(processEvents)
  );

  return (
    <article className={`wl-chat-bubble wl-chat-bubble--${variant}`}>
      <div className="wl-chat-bubble__shell">
        {message.character && variant === 'assistant' && (
          <header className="wl-chat-bubble__head">
            <span className="wl-chat-bubble__character">{personaDisplayLabel(message.character)}</span>
          </header>
        )}
        {variant === 'assistant' && processEvents && (processEvents as unknown[]).length > 0 && (
          <ToolTimeline
            events={processEvents}
            mode={viewMode}
            defaultOpen={toolTimelineDefaultOpen}
          />
        )}
        {variant === 'assistant' && todos.length > 0 && (
          <TodoList todos={todos} />
        )}
        {(bodyContent.trim() || isStreaming) && (
          <div className="wl-chat-bubble__body">
            <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
              {bodyContent}
            </ReactMarkdown>
            {isStreaming && <span className="wl-chat-bubble__cursor" aria-hidden="true" />}
          </div>
        )}
        {reasoningContent && (
          <ReasoningBlock
            content={reasoningContent}
            defaultOpen={!!isStreaming}
            markdownComponents={markdownComponents}
          />
        )}
        {showReplyOptions && optionsToRender.length > 0 && (
          <ReplyOptionButtons
            options={optionsToRender}
            onSelect={(option, detail) => onSelectReplyOption!(
              message.id,
              detail
                ? replyOptionValueWithDetail(option, detail)
                : option.value ?? option.label,
            )}
          />
        )}
        {termForm && onSelectReplyOption && (
          <TermForm
            form={termForm}
            onSubmit={(composed) => onSelectReplyOption(message.id, composed)}
          />
        )}
        {variant === 'assistant' && meta.model_selection && modelChip && (
          <div className="wl-chat-bubble__model-chip" data-testid="chat-bubble-model-chip">
            <span
              className="wl-chat-bubble__model-dot"
              style={{ background: colorForProvider(meta.model_selection.provider) }}
              aria-hidden="true"
            />
            <span className="wl-chat-bubble__model-label">
              {modelChip.label}
            </span>
            {modelChip.missing && <span className="wl-chat-bubble__model-missing">?</span>}
            {meta.model_selection_fallback && (
              <span className="wl-chat-bubble__model-fallback">
                (fell back to default)
              </span>
            )}
          </div>
        )}
        {pendingActions.map((action) => (
          <div key={action.id} className="wl-chat-bubble__action">
            <ActionProposal
              proposal={action}
              executing={confirmingActionIds?.has(actionExecutionKey(message.id, action.id))}
              task={taskForAction(action, taskRunsById)}
              onConfirm={(p) => onConfirmAction(message.id, p.id)}
              onDismiss={(p) => onDismissAction(message.id, p.id)}
            />
          </div>
        ))}
        {meta.cost_preview && onConfirmCostPreview && (
          <div className="wl-chat-bubble__cost-preview">
            <div className="wl-chat-bubble__cost-preview-text">
              <strong>{meta.cost_preview.tool_name}</strong>
              {' is estimated at ~'}
              <strong>{meta.cost_preview.estimated_seconds.toFixed(0)}s</strong>
              . Confirm to run.
            </div>
            <button
              type="button"
              className="wl-chat-bubble__cost-preview-confirm"
              onClick={onConfirmCostPreview}
            >
              Confirm and run
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function actionExecutionKey(messageId: number, actionId: string): string {
  return `${messageId}:${actionId}`;
}

function taskForAction(
  action: AgentActionProposal,
  taskRunsById?: Record<number, TaskRun>,
): TaskRun | undefined {
  const taskId = action.task_id ?? action.async_task_id;
  if (typeof taskId === 'number' && taskRunsById?.[taskId]) {
    return taskRunsById[taskId];
  }
  if (typeof action.task_status === 'string') {
    return {
      id: -1,
      kind: action.task_kind ?? proposalToolName(action),
      status: action.task_status,
      progress_current: action.task_progress_current ?? 0,
      progress_total: action.task_progress_total ?? 0,
      message: action.task_message ?? null,
      created_at: '',
    };
  }
  return undefined;
}

function ReasoningBlock({
  content,
  defaultOpen,
  markdownComponents,
}: {
  content: string;
  defaultOpen: boolean;
  markdownComponents: Components;
}) {
  return (
    <details className="wl-chat-bubble__reasoning" open={defaultOpen}>
      <summary className="wl-chat-bubble__reasoning-summary">
        <span className="wl-chat-bubble__reasoning-title">Reasoning</span>
        {' '}
        <span className="wl-chat-bubble__reasoning-meta">
          {countWords(content)} words
        </span>
      </summary>
      <div className="wl-chat-bubble__body wl-chat-bubble__reasoning-body">
        <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
          {content}
        </ReactMarkdown>
      </div>
    </details>
  );
}

function TodoList({ todos }: { todos: AgentTodoItem[] }) {
  return (
    <ol className="wl-chat-bubble__todos" aria-label="Agent todo list">
      {todos.map((todo, index) => (
        <li key={`${index}-${todo.content}`} data-status={todo.status}>
          <span className="wl-chat-bubble__todo-marker" aria-hidden="true" />
          <span className="wl-chat-bubble__todo-content">{todo.content}</span>
          <span className="wl-chat-bubble__todo-status">
            {todo.status.replace('_', ' ')}
          </span>
        </li>
      ))}
    </ol>
  );
}

function normalizeTodos(value: unknown): AgentTodoItem[] {
  if (!Array.isArray(value)) return [];
  const todos: AgentTodoItem[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    const content = typeof record.content === 'string' ? record.content.trim() : '';
    const status = record.status;
    if (!content) continue;
    if (status !== 'pending' && status !== 'in_progress' && status !== 'completed') {
      continue;
    }
    todos.push({ content, status });
  }
  return todos;
}

function canRenderStructuredReplyOptions(meta: Record<string, unknown>): boolean {
  if (
    meta.agent_graph !== 'deepagents'
    || meta.agent_phase !== 'completed'
    || Array.isArray(meta.process_events)
  ) {
    return true;
  }
  return false;
}

type ContentPresentation = {
  bodyContent: string;
  reasoningContent: string | null;
};

function resolveAssistantContentPresentation({
  content,
  isAssistant,
  isStreaming,
  processEvents,
}: {
  content: string;
  isAssistant: boolean;
  isStreaming: boolean;
  processEvents: ToolEvent[] | string[];
}): ContentPresentation {
  if (!isAssistant) return { bodyContent: content, reasoningContent: null };

  const publicBoundary = findPublicContentBoundary(content);
  if (
    publicBoundary
    && shouldFoldReasoningContent({
      content: publicBoundary.preamble,
      isAssistant,
      isStreaming,
      processEvents,
    })
  ) {
    return {
      bodyContent: publicBoundary.body,
      reasoningContent: publicBoundary.preamble.trim(),
    };
  }

  if (shouldFoldReasoningContent({ content, isAssistant, isStreaming, processEvents })) {
    return { bodyContent: '', reasoningContent: content };
  }

  return { bodyContent: content, reasoningContent: null };
}

function findPublicContentBoundary(content: string): { preamble: string; body: string } | null {
  const preferredMarker = /here\s+is\s+the\s+full\s+structured\s+output\s*:/i.exec(content);
  if (preferredMarker) {
    const bodyStart = preferredMarker.index + preferredMarker[0].length;
    const body = content.slice(bodyStart).trimStart();
    if (startsWithPublicMarkdown(body)) {
      return {
        preamble: content.slice(0, bodyStart),
        body,
      };
    }
  }

  const ruleMatch = /^-{3,}\s*\n+\s*#{1,3}\s+\S.*$/m.exec(content);
  if (ruleMatch && ruleMatch.index > 0) {
    return {
      preamble: content.slice(0, ruleMatch.index),
      body: content.slice(ruleMatch.index).trimStart(),
    };
  }

  const headingPattern = /^#{1,3}\s+.*\b(?:portfolio|report|summary|risk|hedg\w*|snapshot|recommendation)\b.*$/gim;
  let headingMatch = headingPattern.exec(content);
  while (headingMatch) {
    if (headingMatch.index > 0) {
      return {
        preamble: content.slice(0, headingMatch.index),
        body: content.slice(headingMatch.index).trimStart(),
      };
    }
    headingMatch = headingPattern.exec(content);
  }

  return null;
}

function startsWithPublicMarkdown(content: string): boolean {
  return /^(?:-{3,}\s*\n+)?\s*#{1,3}\s+\S/.test(content.trimStart());
}

function shouldFoldReasoningContent({
  content,
  isAssistant,
  isStreaming,
  processEvents,
}: {
  content: string;
  isAssistant: boolean;
  isStreaming: boolean;
  processEvents: ToolEvent[] | string[];
}): boolean {
  if (!isAssistant) return false;
  const text = content.trim();
  if (!text) return false;
  const eventCount = Array.isArray(processEvents) ? processEvents.length : 0;
  if (eventCount === 0) return false;

  const lowered = text.toLowerCase();
  const markerCount = REASONING_MARKERS.reduce(
    (count, marker) => count + countOccurrences(lowered, marker),
    0,
  );
  const toolNameMentions = toolNamesFromEvents(processEvents).filter(
    (name) => name && lowered.includes(name.toLowerCase()),
  ).length;

  if (isStreaming) {
    return text.length > 1200 && markerCount >= 3;
  }
  return (
    (text.length > 700 && markerCount >= 3)
    || (eventCount >= 5 && text.length > 500 && (markerCount >= 2 || toolNameMentions >= 2))
  );
}

const REASONING_MARKERS = [
  'let me ',
  'i need to ',
  'now i ',
  'i have ',
  'i see the issue',
  'the skill confirms',
  'fallback pattern',
  'the sandbox',
  'pass it ',
  'read the ',
  'fetch the ',
  'run the ',
];

function countOccurrences(haystack: string, needle: string): number {
  let count = 0;
  let index = haystack.indexOf(needle);
  while (index !== -1) {
    count += 1;
    index = haystack.indexOf(needle, index + needle.length);
  }
  return count;
}

function toolNamesFromEvents(events: ToolEvent[] | string[]): string[] {
  if (!Array.isArray(events) || typeof events[0] === 'string') return [];
  return (events as ToolEvent[]).map((event) => event.name);
}

function hasRunningTool(events: ToolEvent[] | string[]): boolean {
  return Array.isArray(events)
    && typeof events[0] !== 'string'
    && (events as ToolEvent[]).some((event) => event.status === 'running');
}

function hasErroredTool(events: ToolEvent[] | string[]): boolean {
  return Array.isArray(events)
    && typeof events[0] !== 'string'
    && (events as ToolEvent[]).some((event) => event.status === 'error');
}

function countWords(text: string): number {
  const words = text.trim().match(/\S+/g);
  return words ? words.length : 0;
}

function ReplyOptionButtons({
  options,
  onSelect,
}: {
  options: ReplyOption[];
  onSelect: (option: ReplyOption, detail?: string) => void;
}) {
  const [activeOptionKey, setActiveOptionKey] = useState<string | null>(null);
  const [detail, setDetail] = useState('');

  const submitDetail = (event: FormEvent, option: ReplyOption) => {
    event.preventDefault();
    const trimmed = detail.trim();
    if (!trimmed) return;
    onSelect(option, trimmed);
    setDetail('');
    setActiveOptionKey(null);
  };

  return (
    <div className="wl-chat-bubble__reply-options" role="group" aria-label="Suggested replies">
      {options.map((option, index) => {
        const optionKey = `${option.label}:${index}`;
        const requiresDetail = replyOptionNeedsDetail(option);
        const isActive = activeOptionKey === optionKey;
        return (
          <div key={optionKey} className="wl-chat-bubble__reply-choice">
            <button
              type="button"
              className={`wl-chat-bubble__reply-option${isActive ? ' is-active' : ''}`}
              aria-expanded={requiresDetail ? isActive : undefined}
              onClick={() => {
                if (!requiresDetail) {
                  onSelect(option);
                  return;
                }
                setActiveOptionKey(isActive ? null : optionKey);
                setDetail('');
              }}
            >
              <span className="wl-chat-bubble__reply-label">{option.label}</span>
              {option.description && (
                <span className="wl-chat-bubble__reply-desc">{option.description}</span>
              )}
            </button>
            {requiresDetail && isActive && (
              <form
                className="wl-chat-bubble__reply-detail"
                onSubmit={(event) => submitDetail(event, option)}
              >
                <input
                  className="wl-chat-bubble__reply-input"
                  value={detail}
                  onChange={(event) => setDetail(event.target.value)}
                  placeholder={replyOptionDetailPlaceholder(option)}
                  autoFocus
                />
                <button
                  type="submit"
                  className="wl-chat-bubble__reply-submit"
                  disabled={!detail.trim()}
                >
                  Send
                </button>
              </form>
            )}
          </div>
        );
      })}
    </div>
  );
}

function replyOptionNeedsDetail(option: ReplyOption): boolean {
  const text = `${option.label} ${option.description ?? ''} ${option.value ?? ''}`.toLowerCase();
  return /\b(specify|provide|enter|type|name|custom|other)\b/.test(text)
    || /\bspecific\s+(portfolio|position|trade|date|book|id)\b/.test(text)
    || /\bportfolio\s+(id|name)\b/.test(text)
    || /\bposition\s+(id|name)\b/.test(text);
}

function replyOptionDetailPlaceholder(option: ReplyOption): string {
  const text = `${option.label} ${option.description ?? ''} ${option.value ?? ''}`.toLowerCase();
  if (text.includes('portfolio')) return 'Portfolio name or ID';
  if (text.includes('position') || text.includes('trade')) return 'Position or trade ID';
  if (text.includes('date')) return 'Date';
  return 'Details';
}

function replyOptionValueWithDetail(option: ReplyOption, detail: string): string {
  const template = option.value ?? option.label;
  if (template.includes('{{input}}')) return template.replaceAll('{{input}}', detail);
  if (template.includes('{input}')) return template.replaceAll('{input}', detail);
  return `${option.label}: ${detail}`;
}

function markdownComponentsForAssets(assets: AgentAsset[]): Components {
  return {
    a({ node: _node, href, children, ...props }) {
      const resolvedHref = resolveAssetHref(href, assets);
      return (
        <a {...props} href={resolvedHref} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
    pre({ node: _node, children, ...props }) {
      const jsonText = extractJsonCodeBlock(children);
      if (jsonText) {
        const parsed = parseJson(jsonText);
        if (parsed.ok) return <JsonTable data={parsed.value} />;
      }
      return <pre {...props}>{children}</pre>;
    },
    table({ node: _node, ...props }) {
      return (
        <div className="wl-chat-bubble__table-wrap">
          <table className="wl-chat-bubble__table" {...props} />
        </div>
      );
    },
    td({ node: _node, children, ...props }) {
      const text = textFromNode(children).trim();
      return (
        <td
          className={isNumericTableCell(text) ? 'wl-chat-bubble__table-cell--numeric' : undefined}
          {...props}
        >
          {children}
        </td>
      );
    },
  };
}

function linkifyAssetPaths(content: string, assets: AgentAsset[]): string {
  if (!assets.length || !content) return content;
  return content.replace(
    /(^|[\s:])((?:\/trading_desk\/)[^\s)\]]+\.(?:html?|csv|json|md|markdown|txt|xlsx?))/gi,
    (match, prefix, path) => {
      const href = resolveAssetHref(path, assets);
      if (!href || href === path) return match;
      return `${prefix}[${path}](${href})`;
    },
  );
}

function resolveAssetHref(href: string | undefined, assets: AgentAsset[]): string | undefined {
  if (!href) return href;
  const asset = assets.find((item) => item.path === href || item.metadata?.virtual_path === href);
  return asset?.url ?? href;
}

function resolveModelChip(
  channels: AgentChannel[],
  selection: { channel: string; provider: string; model: string },
): { label: string; missing: boolean } {
  const channel = channels.find((ch) => ch.name === selection.channel);
  const model = channel?.models.find((md) => (
    md.provider === selection.provider && md.model === selection.model
  ));
  if (channel && model) {
    return { label: `${model.label} · ${channel.label}`, missing: false };
  }
  const fallbackLabel = formatModelId(selection.model);
  const channelLabel = channel?.label ?? formatChannelName(selection.channel);
  return {
    label: channelLabel ? `${fallbackLabel} · ${channelLabel}` : fallbackLabel,
    missing: !channel,
  };
}

function formatModelId(model: string): string {
  const rawModel = model.includes('/') ? model.split('/').slice(1).join('/') : model;
  const raw = rawModel.replace(/(\d)-(?=\d)/g, '$1.');
  return raw
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => {
      if (/^gpt$/i.test(part)) return 'GPT';
      if (/^\d+(?:\.\d+)*$/.test(part)) return part;
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(' ');
}

function formatChannelName(channel: string): string {
  return channel
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function extractJsonCodeBlock(children: ReactNode): string | null {
  const child = Children.toArray(children)[0];
  if (!isValidElement<CodeElementProps>(child)) return null;
  const className = child.props.className ?? '';
  if (!/\blanguage-json\b/.test(className)) return null;
  return textFromNode(child.props.children).trim();
}

function textFromNode(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => (typeof child === 'string' || typeof child === 'number' ? String(child) : ''))
    .join('');
}

function parseJson(value: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(value) };
  } catch {
    return { ok: false };
  }
}

function JsonTable({ data, nested = false }: { data: unknown; nested?: boolean }) {
  if (data === null || typeof data !== 'object') {
    return <span className="wl-chat-bubble__json-value">{formatScalar(data)}</span>;
  }

  const entries = Array.isArray(data)
    ? data.map((value, index) => [String(index), value] as const)
    : Object.entries(data as Record<string, unknown>);

  return (
    <table
      className={
        nested
          ? 'wl-chat-bubble__json-table wl-chat-bubble__json-table--nested'
          : 'wl-chat-bubble__json-table'
      }
    >
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th scope="row">{key}</th>
            <td>
              {value !== null && typeof value === 'object' ? (
                <JsonTable data={value} nested />
              ) : (
                <span className="wl-chat-bubble__json-value">{formatScalar(value)}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatScalar(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function isNumericTableCell(value: string): boolean {
  return /^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value.replace(/,/g, ''));
}

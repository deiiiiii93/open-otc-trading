import { Fragment, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, CircleDashed } from 'lucide-react';
import type { TraceRunDetail, TraceRunNode, TraceSummary } from '../types';
import { MasterDetailPage } from '../components/templates';
import { RailList } from '../components/RailList';
import { RailItem } from '../components/RailItem';
import { Empty } from '../components/Empty';
import { NumberInput } from '../components/NumberInput';
import './Tracing.css';

const remarkPlugins = [remarkGfm];

type Props = {
  threadId: number | null;
  traces: TraceSummary[];
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  runs: TraceRunNode[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  runDetail: TraceRunDetail | null;
  loading: boolean;
  onThreadChange?: (threadId: number | null) => void;
};

function statusIcon(status: TraceSummary['status']) {
  if (status === 'error') return <AlertCircle size={14} aria-label="error" />;
  if (status === 'running') return <CircleDashed size={14} aria-label="running" />;
  return <CheckCircle2 size={14} aria-label="success" />;
}

function durationMs(start: string, end: string | null): string {
  if (!end) return '—';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function depthOf(run: TraceRunNode): number {
  return run.dotted_order.split('.').length - 1;
}

function tryParseJson(raw: string | null): { parsed: unknown; isJson: boolean } {
  if (!raw) return { parsed: null, isJson: false };
  try {
    return { parsed: JSON.parse(raw), isJson: true };
  } catch {
    return { parsed: raw, isJson: false };
  }
}

type PayloadViewMode = 'raw' | 'rendered';

const LONG_MESSAGE_LIMIT = 20_000;

/* --- LangChain run-shape detection ----------------------------------------
 * Trace spans persist LangChain Run.inputs / Run.outputs as JSON. The readable
 * content (the assistant's reply, tool calls, token usage) is nested deep
 * inside a verbose envelope. Rendered mode unwraps that envelope per run_type
 * so the user sees the human-meaningful payload instead of raw LangChain JSON.
 * Raw mode always shows the full pretty-printed JSON. */

function asObject(v: unknown): Record<string, unknown> | null {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
    ? v as Record<string, unknown>
    : null;
}

function firstGeneration(outputs: unknown): Record<string, unknown> | null {
  const obj = asObject(outputs);
  if (!obj) return null;
  const gens = obj.generations;
  if (!Array.isArray(gens) || !Array.isArray(gens[0]) || !gens[0][0]) return null;
  return asObject(gens[0][0]) ?? null;
}

function genMessage(gen: Record<string, unknown>): Record<string, unknown> | null {
  const msg = asObject(gen.message);
  if (!msg) return null;
  const kwargs = asObject(msg.kwargs);
  return kwargs;
}

// LLM outputs keep the assistant text in either `gen.text` or, when empty, in
// the AIMessage content blocks: message.kwargs.content = [{type:"text",text}, ...].
function genText(gen: Record<string, unknown>): string {
  const text = typeof gen.text === 'string' ? gen.text : '';
  if (text.trim()) return text;
  const kwargs = genMessage(gen);
  if (!kwargs) return '';
  const content = kwargs.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => {
        const block = asObject(b);
        if (!block) return '';
        return block.type === 'text' && typeof block.text === 'string' ? block.text : '';
      })
      .join('');
  }
  return '';
}

type ToolCall = { name: string; args: unknown; id?: string };

const MESSAGE_REPR_KEYS = ['content', 'additional_kwargs', 'response_metadata', 'type', 'name', 'tool_call_id'];

function parseMaybeJson(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed || !['{', '['].includes(trimmed[0])) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function normalizeToolCalls(value: unknown): ToolCall[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((call): ToolCall | null => {
      const obj = asObject(call);
      if (!obj) return null;
      const fn = asObject(obj.function);
      const name = typeof obj.name === 'string'
        ? obj.name
        : typeof fn?.name === 'string'
          ? fn.name
          : typeof obj.type === 'string'
            ? obj.type
            : 'tool';
      const rawArgs = 'args' in obj
        ? obj.args
        : 'arguments' in obj
          ? obj.arguments
          : 'input' in obj
            ? obj.input
            : fn?.arguments;
      const args = typeof rawArgs === 'string' ? parseMaybeJson(rawArgs) : rawArgs;
      const id = typeof obj.id === 'string' ? obj.id : undefined;
      return { name, args, id };
    })
    .filter((call): call is ToolCall => call !== null);
}

function genToolCalls(gen: Record<string, unknown>): ToolCall[] {
  const kwargs = genMessage(gen);
  const additional = kwargs && asObject(kwargs.additional_kwargs);
  const fromKwargs = kwargs && Array.isArray(kwargs.tool_calls) ? kwargs.tool_calls : null;
  const fromAdditional = additional && Array.isArray(additional.tool_calls) ? additional.tool_calls : null;
  const calls = fromKwargs
    ?? fromAdditional
    ?? (Array.isArray(kwargs?.content)
      ? (kwargs!.content as unknown[]).filter((b): b is Record<string, unknown> =>
        ['tool_call', 'tool_use'].includes(String(asObject(b)?.type)))
      : []);
  return normalizeToolCalls(calls);
}

function genUsage(gen: Record<string, unknown>):
  { input?: number; output?: number; total?: number } | null {
  const kwargs = genMessage(gen);
  const usage = kwargs && asObject(kwargs.usage_metadata);
  if (!usage) return null;
  return {
    input: typeof usage.input_tokens === 'number' ? usage.input_tokens : undefined,
    output: typeof usage.output_tokens === 'number' ? usage.output_tokens : undefined,
    total: typeof usage.total_tokens === 'number' ? usage.total_tokens : undefined,
  };
}

function contentText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return content == null ? '' : JSON.stringify(content, null, 2);
  return content
    .map((block) => {
      const obj = asObject(block);
      if (!obj) return typeof block === 'string' ? block : JSON.stringify(block, null, 2);
      if (obj.type === 'tool_call' || obj.type === 'tool_use') return '';
      if (typeof obj.text === 'string') return obj.text;
      if (typeof obj.content === 'string') return obj.content;
      return JSON.stringify(obj, null, 2);
    })
    .filter(Boolean)
    .join('\n\n');
}

function roleLabel(role: string | undefined): string {
  if (role === 'human' || role === 'user') return 'User';
  if (role === 'ai' || role === 'assistant') return 'Assistant';
  if (role === 'system') return 'System';
  if (role === 'tool' || role === 'function') return 'Tool';
  return role ? role.replace(/^./, (c) => c.toUpperCase()) : 'Message';
}

function parseLangChainMessageRepr(value: string): Record<string, unknown> | null {
  if (!MESSAGE_REPR_KEYS.some((key) => value.includes(`${key}=`))) return null;
  const result: Record<string, unknown> = {};
  let i = 0;
  while (i < value.length) {
    while (value[i] === ' ') i += 1;
    const keyMatch = value.slice(i).match(/^([A-Za-z_][A-Za-z0-9_]*)=/);
    if (!keyMatch) break;
    const key = keyMatch[1];
    i += key.length + 1;
    const quote = value[i];
    if (quote === "'" || quote === '"') {
      i += 1;
      let raw = '';
      while (i < value.length) {
        const ch = value[i];
        if (ch === '\\' && i + 1 < value.length) {
          raw += ch + value[i + 1];
          i += 2;
          continue;
        }
        if (ch === quote) {
          i += 1;
          break;
        }
        raw += ch;
        i += 1;
      }
      result[key] = parsePythonReprString(raw);
    } else {
      const next = value.slice(i).search(/\s+[A-Za-z_][A-Za-z0-9_]*=/);
      const raw = next === -1 ? value.slice(i) : value.slice(i, i + next);
      result[key] = parseMaybeJsonishPython(raw.trim());
      i = next === -1 ? value.length : i + next + 1;
    }
  }
  return 'content' in result || 'additional_kwargs' in result || 'response_metadata' in result ? result : null;
}

function parseMaybeJsonishPython(value: string): unknown {
  if (value === '{}') return {};
  if (value === '[]') return [];
  if (value === 'None') return null;
  if (value === 'True') return true;
  if (value === 'False') return false;
  return parseMaybeJson(value);
}

function roleFromMessage(envelope: Record<string, unknown>, body: Record<string, unknown>): string | undefined {
  if (typeof body.type === 'string') return body.type;
  if (typeof body.role === 'string') return body.role;
  const id = Array.isArray(envelope.id) ? envelope.id[envelope.id.length - 1] : undefined;
  if (id === 'HumanMessage') return 'human';
  if (id === 'AIMessage') return 'ai';
  if (id === 'SystemMessage') return 'system';
  if (id === 'ToolMessage') return 'tool';
  return undefined;
}

function omitKeys(obj: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const omitted = new Set(keys);
  return Object.fromEntries(Object.entries(obj).filter(([, value]) => value !== undefined).filter(([key]) => !omitted.has(key)));
}

function messageExtras(body: Record<string, unknown>): Record<string, unknown> {
  return omitKeys(body, [
    'content', 'tool_calls', 'type', 'role', 'name',
    'additional_kwargs', 'response_metadata', 'usage_metadata',
    'invalid_tool_calls',
  ]);
}

function additionalToolCalls(body: Record<string, unknown>): ToolCall[] {
  const additional = asObject(body.additional_kwargs);
  const fromAdditional = additional && Array.isArray(additional.tool_calls)
    ? normalizeToolCalls(additional.tool_calls)
    : [];
  const fromContent = Array.isArray(body.content)
    ? normalizeToolCalls((body.content as unknown[]).filter((block) => {
      const obj = asObject(block);
      return obj && ['tool_call', 'tool_use'].includes(String(obj.type));
    }))
    : [];
  return [...normalizeToolCalls(body.tool_calls), ...fromAdditional, ...fromContent];
}

type ChatMessage = {
  role: string;
  label: string;
  name?: string;
  content: string;
  toolCalls: ToolCall[];
  usage: { input?: number; output?: number; total?: number } | null;
  extras: Record<string, unknown>;
};

function chatMessages(val: unknown): ChatMessage[] | null {
  const obj = asObject(val);
  const messages = obj && Array.isArray(obj.messages) ? obj.messages : null;
  if (!messages) return null;

  return messages.map((msg): ChatMessage => {
    const parsedRepr = typeof msg === 'string' ? parseLangChainMessageRepr(msg) : null;
    const envelope = asObject(msg) ?? {};
    const body = parsedRepr ?? asObject(envelope.kwargs) ?? envelope;
    const role = roleFromMessage(envelope, body);
    const name = typeof body.name === 'string' ? body.name : undefined;
    const usage = asObject(body.usage_metadata);
    return {
      role: role ?? 'message',
      label: roleLabel(role),
      name,
      content: contentText(body.content),
      toolCalls: additionalToolCalls(body),
      usage: usage
        ? {
          input: typeof usage.input_tokens === 'number' ? usage.input_tokens : undefined,
          output: typeof usage.output_tokens === 'number' ? usage.output_tokens : undefined,
          total: typeof usage.total_tokens === 'number' ? usage.total_tokens : undefined,
        }
        : null,
      extras: messageExtras(body),
    };
  });
}

function isEmptyObject(value: Record<string, unknown>): boolean {
  return Object.keys(value).length === 0;
}

function displayTitle(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function isRenderableText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function scalarText(value: unknown): string | null {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

function flattenCell(value: unknown): string {
  const scalar = scalarText(value);
  if (scalar !== null) return scalar;
  return JSON.stringify(value, null, 2);
}

function tableModel(val: unknown): { columns: string[]; rows: string[][] } | null {
  if (Array.isArray(val)) {
    const objects = val.map(asObject);
    if (objects.length === 0 || objects.some((obj) => obj === null)) return null;
    const rowsAsObjects = objects as Array<Record<string, unknown>>;
    const columns = Array.from(new Set(rowsAsObjects.flatMap((row) => Object.keys(row))));
    if (columns.length === 0) return null;
    return {
      columns,
      rows: rowsAsObjects.map((row) => columns.map((col) => flattenCell(row[col]))),
    };
  }

  const obj = asObject(val);
  if (!obj) return null;
  const entries = Object.entries(obj);
  if (entries.length === 0) return null;
  const scalarEntries = entries.filter(([, value]) => scalarText(value) !== null);
  if (scalarEntries.length < 2) return null;
  return {
    columns: ['Field', 'Value'],
    rows: scalarEntries.map(([key, value]) => [displayTitle(key), flattenCell(value)]),
  };
}

function primaryEntries(val: unknown, direction: 'inputs' | 'outputs'): Array<{ key: string; value: unknown; kind: 'markdown' | 'json' }> {
  if (Array.isArray(val)) return val.length > 0 ? [{ key: direction, value: val, kind: 'json' }] : [];
  const obj = asObject(val);
  if (!obj) return isRenderableText(val) ? [{ key: direction, value: val, kind: 'markdown' }] : [];
  const preferred = direction === 'outputs'
    ? ['output', 'result', 'response', 'answer', 'final', 'return', 'messages']
    : ['input', 'prompt', 'question', 'query', 'instructions', 'messages'];

  const entries: Array<{ key: string; value: unknown; kind: 'markdown' | 'json' }> = [];
  for (const key of preferred) {
    if (!(key in obj)) continue;
    const value = obj[key];
    if (key === 'messages') continue;
    if (isRenderableText(value)) entries.push({ key, value, kind: 'markdown' });
    else if (value != null) entries.push({ key, value, kind: 'json' });
  }
  if (entries.length) return entries;

  return Object.entries(obj)
    .filter(([, value]) => value !== null && value !== undefined && !(Array.isArray(value) && value.length === 0))
    .slice(0, 8)
    .map(([key, value]) => ({ key, value, kind: isRenderableText(value) ? 'markdown' : 'json' }));
}

function GenericPayloadView({ val, direction }: { val: unknown; direction: 'inputs' | 'outputs' }) {
  const entries = primaryEntries(val, direction);
  if (entries.length === 0) return <div className="wl-tracing__empty-note">No structured payload</div>;
  return (
    <div className="wl-tracing__readable">
      {entries.map((entry) => (
        <section key={entry.key} className="wl-tracing__section-card">
          <div className="wl-tracing__eyebrow">{displayTitle(entry.key)}</div>
          {entry.kind === 'markdown'
            ? <MessageContent content={String(entry.value)} />
            : <StructuredValue val={entry.value} />}
        </section>
      ))}
    </div>
  );
}

function StructuredValue({ val }: { val: unknown }) {
  const table = tableModel(val);
  if (!table) return <ColoredJson val={val} />;
  return <DataTable columns={table.columns} rows={table.rows} />;
}

function DataTable({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="wl-tracing__table-wrap">
      <table className="wl-tracing__data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{displayTitle(column)}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => <td key={j}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function parsePythonReprString(value: string): string {
  return value
    .replace(/\\n/g, '\n')
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\');
}

function parseToolMessageRepr(value: string): { content: string; name?: string; toolCallId?: string } | null {
  const contentMatch = value.match(/^content=(['"])([\s\S]*?)\1(?:\s|$)/);
  if (!contentMatch) return null;
  const rest = value.slice(contentMatch[0].length);
  const nameMatch = rest.match(/\bname=(['"])(.*?)\1/);
  const idMatch = rest.match(/\btool_call_id=(['"])(.*?)\1/);
  return {
    content: parsePythonReprString(contentMatch[2]),
    name: nameMatch?.[2],
    toolCallId: idMatch?.[2],
  };
}

function Markdown({ children }: { children: string }) {
  return (
    <div className="wl-tracing__markdown">
      <ReactMarkdown remarkPlugins={remarkPlugins}>{children}</ReactMarkdown>
    </div>
  );
}

function MessageContent({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const truncated = content.length > LONG_MESSAGE_LIMIT;
  const visible = truncated && !expanded ? content.slice(0, LONG_MESSAGE_LIMIT) : content;
  return (
    <>
      <Markdown>{visible}</Markdown>
      {truncated && (
        <button
          type="button"
          className="wl-tracing__message-expand"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Show less' : `Only showing first ${LONG_MESSAGE_LIMIT.toLocaleString()} of ${content.length.toLocaleString()} characters. Click to see all`}
        </button>
      )}
    </>
  );
}

function ChatMessagesView({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) return <div className="wl-tracing__empty-note">No messages</div>;
  return (
    <div className="wl-tracing__messages">
      {messages.map((message, i) => (
        <section key={i} className={`wl-tracing__message wl-tracing__message--${message.role}`}>
          <div className="wl-tracing__message-head">
            <span className="wl-tracing__message-role">{message.label}</span>
            {message.name && <code className="wl-tracing__message-name">{message.name}</code>}
          </div>
          {message.content.trim()
            ? <MessageContent content={message.content} />
            : message.toolCalls.length > 0 || message.usage || !isEmptyObject(message.extras)
              ? null
              : <div className="wl-tracing__empty-note">No content</div>}
          {message.toolCalls.length > 0 && <ToolCallList calls={message.toolCalls} />}
          {message.usage && <UsageChips usage={message.usage} />}
          {!isEmptyObject(message.extras) && <StructuredValue val={message.extras} />}
        </section>
      ))}
    </div>
  );
}

function ToolCallList({ calls }: { calls: ToolCall[] }) {
  return (
    <div className="wl-tracing__toolcalls">
      <div className="wl-tracing__eyebrow">Tool calls</div>
      {calls.map((call, i) => (
        <div key={call.id ?? i} className="wl-tracing__toolcall">
          <code className="wl-tracing__toolcall-name">{call.name}</code>
          {call.args !== undefined && (
            <pre className="wl-tracing__toolcall-args">
              {typeof call.args === 'string' ? call.args : JSON.stringify(call.args, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

function UsageChips({ usage }: { usage: { input?: number; output?: number; total?: number } }) {
  return (
    <div className="wl-tracing__usage">
      {usage.input != null && <span className="wl-tracing__usage-chip">in {usage.input}</span>}
      {usage.output != null && <span className="wl-tracing__usage-chip">out {usage.output}</span>}
      {usage.total != null && <span className="wl-tracing__usage-chip">total {usage.total}</span>}
    </div>
  );
}

// Rendered view for an LLM Run.outputs envelope ({generations, llm_output, ...}).
function LlmOutputView({ val }: { val: unknown }) {
  const gen = firstGeneration(val);
  if (!gen) return <ColoredJson val={val} />;
  const text = genText(gen);
  const calls = genToolCalls(gen);
  const usage = genUsage(gen);
  return (
    <div className="wl-tracing__readable">
      {text.trim() && <Markdown>{text}</Markdown>}
      {calls.length > 0 && <ToolCallList calls={calls} />}
      {usage && <UsageChips usage={usage} />}
      {!text.trim() && calls.length === 0 && !usage && <StructuredValue val={val} />}
    </div>
  );
}

// Rendered view for an LLM Run.inputs envelope ({prompts: ["System: ... Human: ..."]}).
function LlmInputView({ val }: { val: unknown }) {
  const obj = asObject(val);
  const prompts = obj && Array.isArray(obj.prompts) ? obj.prompts : null;
  const prompt = prompts && typeof prompts[0] === 'string' ? prompts[0] : null;
  if (!prompt) return <ColoredJson val={val} />;
  return (
    <div className="wl-tracing__readable">
      <Markdown>{prompt}</Markdown>
    </div>
  );
}

// Rendered view for a tool Run.outputs envelope ({output: "Command(...)" | dict | string}).
function ToolOutputView({ val }: { val: unknown }) {
  const obj = asObject(val);
  if (!obj) return <GenericPayloadView val={val} direction="outputs" />;
  const out = 'output' in obj
    ? obj.output
    : 'result' in obj
      ? obj.result
      : 'content' in obj
        ? obj.content
        : undefined;
  if (out === undefined) return <GenericPayloadView val={val} direction="outputs" />;
  // Structured dict output — show as colored JSON.
  if (asObject(out) || Array.isArray(out)) return <GenericPayloadView val={out} direction="outputs" />;
  if (typeof out !== 'string') return <GenericPayloadView val={val} direction="outputs" />;
  // The common shape is a stringified ToolMessage repr:
  //   content='...' name='...' tool_call_id='...'
  // Pull out the content payload; if it is itself JSON, render that; else pre.
  const parsedRepr = parseToolMessageRepr(out);
  if (parsedRepr) {
    const parsed = tryParseJson(parsedRepr.content);
    return (
      <div className="wl-tracing__readable">
        {parsedRepr.name && <div className="wl-tracing__eyebrow">{parsedRepr.name}</div>}
        {parsed.isJson
          ? <StructuredValue val={parsed.parsed} />
          : <MessageContent content={parsedRepr.content} />}
        {parsedRepr.toolCallId && <code className="wl-tracing__message-name">{parsedRepr.toolCallId}</code>}
      </div>
    );
  }
  // Free-form string output — render as preformatted.
  return (
    <div className="wl-tracing__readable">
      <MessageContent content={out} />
    </div>
  );
}

// Dispatch a parsed payload to the right rendered view, falling back to the
// colored JSON tree for anything we don't recognise (chain runs, ad-hoc dicts).
function ReadablePayload({ val, runType, direction }: {
  val: unknown;
  runType: TraceRunDetail['run_type'];
  direction: 'inputs' | 'outputs';
}) {
  const messages = chatMessages(val);
  if (messages) return <ChatMessagesView messages={messages} />;
  if (runType === 'llm' && direction === 'outputs') return <LlmOutputView val={val} />;
  if (runType === 'llm' && direction === 'inputs') return <LlmInputView val={val} />;
  if (runType === 'tool' && direction === 'outputs') return <ToolOutputView val={val} />;
  return <GenericPayloadView val={val} direction={direction} />;
}

function ColoredJson({ val }: { val: unknown }) {
  return <JsonValue val={val} depth={0} />;
}

function JsonValue({ val, depth }: { val: unknown; depth: number }) {
  if (val === null) return <span className="wl-json-null">null</span>;
  if (typeof val === 'boolean') return <span className="wl-json-bool">{String(val)}</span>;
  if (typeof val === 'number') return <span className="wl-json-number">{String(val)}</span>;
  if (typeof val === 'string') return <span className="wl-json-string">"{val}"</span>;

  if (Array.isArray(val)) {
    if (val.length === 0) return <span className="wl-json-punct">[]</span>;
    return (
      <>
        <span className="wl-json-punct">[</span>
        <span className="wl-json-indent">
          {val.map((item, i) => (
            <Fragment key={i}>
              <JsonValue val={item} depth={depth + 1} />
              {i < val.length - 1 && <span className="wl-json-punct">,</span>}
              {'\n'}{' '.repeat((depth + 1) * 2)}
            </Fragment>
          ))}
        </span>
        <span className="wl-json-punct">]</span>
      </>
    );
  }

  if (typeof val === 'object') {
    const entries = Object.entries(val as Record<string, unknown>);
    if (entries.length === 0) return <span className="wl-json-punct">{'{}'}</span>;
    return (
      <>
        <span className="wl-json-punct">{'{'}</span>
        <span className="wl-json-indent">
          {entries.map(([k, v], i) => (
            <Fragment key={k}>
              <span className="wl-json-key">"{k}"</span>
              <span className="wl-json-punct">: </span>
              <JsonValue val={v} depth={depth + 1} />
              {i < entries.length - 1 && <span className="wl-json-punct">,</span>}
              {'\n'}{' '.repeat((depth + 1) * 2)}
            </Fragment>
          ))}
        </span>
        <span className="wl-json-punct">{'}'}</span>
      </>
    );
  }

  return <>{String(val)}</>;
}

function JsonBlock({
  raw, parsed, isJson, label, viewMode, runType, direction,
}: {
  raw: string | null;
  parsed: unknown;
  isJson: boolean;
  label: string;
  viewMode: PayloadViewMode;
  runType: TraceRunDetail['run_type'];
  direction: 'inputs' | 'outputs';
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (!raw) return null;

  const showRendered = isJson && viewMode === 'rendered';

  return (
    <div className="wl-tracing__payload">
      <button
        type="button"
        className="wl-tracing__payload-head"
        onClick={() => setCollapsed((v) => !v)}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <span className="wl-tracing__eyebrow">{label}</span>
        {isJson && <span className="wl-tracing__payload-badge">JSON</span>}
      </button>
      {!collapsed && (
        showRendered ? (
          <div className="wl-tracing__payload-body wl-tracing__payload-body--rendered">
            <ReadablePayload val={parsed} runType={runType} direction={direction} />
          </div>
        ) : (
          <pre className="wl-tracing__payload-body">
            {isJson ? JSON.stringify(parsed, null, 2) : String(parsed)}
          </pre>
        )
      )}
    </div>
  );
}

function SpanDetail({ runDetail }: { runDetail: TraceRunDetail }) {
  const [viewMode, setViewMode] = useState<PayloadViewMode>('rendered');
  const inputsParsed = useMemo(() => tryParseJson(runDetail.inputs), [runDetail.inputs]);
  const outputsParsed = useMemo(() => tryParseJson(runDetail.outputs), [runDetail.outputs]);
  const hasJsonPayload = inputsParsed.isJson || outputsParsed.isJson;

  return (
    <>
      <div className="wl-tracing__detail-head">
        <span className="wl-tracing__detail-name">{runDetail.name}</span>
        <span className={`wl-tracing__detail-status wl-tracing__detail-status--${runDetail.status}`}>
          {runDetail.status}
        </span>
        {hasJsonPayload && (
          <fieldset className="wl-tracing__view-mode" aria-label="Payload view">
            <button
              type="button"
              aria-pressed={viewMode === 'rendered'}
              className={`wl-tracing__view-mode-btn${viewMode === 'rendered' ? ' is-active' : ''}`}
              onClick={() => setViewMode('rendered')}
            >
              Rendered
            </button>
            <button
              type="button"
              aria-pressed={viewMode === 'raw'}
              className={`wl-tracing__view-mode-btn${viewMode === 'raw' ? ' is-active' : ''}`}
              onClick={() => setViewMode('raw')}
            >
              Raw
            </button>
          </fieldset>
        )}
      </div>
      <div className="wl-tracing__detail-meta">
        <span>type: <b>{runDetail.run_type}</b></span>
        <span>id: <code>{runDetail.id.slice(0, 24)}…</code></span>
        {runDetail.parent_run_id && (
          <span>parent: <code>{runDetail.parent_run_id.slice(0, 24)}…</code></span>
        )}
        <span>duration: <b>{durationMs(runDetail.start_time, runDetail.end_time)}</b></span>
        {runDetail.total_tokens != null && (
          <span>tokens: <b>{runDetail.total_tokens}</b> ({runDetail.prompt_tokens ?? '?'} → {runDetail.completion_tokens ?? '?'})</span>
        )}
        <span>start: <code>{new Date(runDetail.start_time).toLocaleTimeString()}</code></span>
      </div>
      {runDetail.error && (
        <div className="wl-tracing__error" role="alert">
          <div className="wl-tracing__error-label">Error</div>
          <pre className="wl-tracing__error-body">{runDetail.error}</pre>
        </div>
      )}
      <JsonBlock
        raw={runDetail.inputs}
        parsed={inputsParsed.parsed}
        isJson={inputsParsed.isJson}
        label="Inputs"
        viewMode={viewMode}
        runType={runDetail.run_type}
        direction="inputs"
      />
      <JsonBlock
        raw={runDetail.outputs}
        parsed={outputsParsed.parsed}
        isJson={outputsParsed.isJson}
        label="Outputs"
        viewMode={viewMode}
        runType={runDetail.run_type}
        direction="outputs"
      />
    </>
  );
}

export function Tracing({
  threadId, traces, selectedTraceId, onSelectTrace,
  runs, selectedRunId, onSelectRun, runDetail, loading,
  onThreadChange,
}: Props) {
  const orderedRuns = useMemo(
    () => [...runs].sort((a, b) => a.dotted_order.localeCompare(b.dotted_order)),
    [runs],
  );

  const rail = (
    <RailList scroll className="wl-tracing__list">
        <div className="wl-tracing__head">
          <div className="wl-tracing__eyebrow">Traces</div>
          {threadId != null ? (
            <span className="wl-tracing__filter-chip">
              Thread #{threadId}
              {onThreadChange && (
                <button
                  className="wl-tracing__clear-thread"
                  onClick={() => onThreadChange(null)}
                  aria-label="Clear thread filter"
                >
                  ×
                </button>
              )}
            </span>
          ) : onThreadChange && (
            <NumberInput
              className="wl-tracing__thread-input"
              type="number"
              placeholder="Thread ID…"
              aria-label="Filter by thread ID"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const val = parseInt(e.currentTarget.value, 10);
                  if (!isNaN(val)) onThreadChange(val);
                }
              }}
            />
          )}
        </div>
        {traces.length === 0 ? (
          <Empty message="No traces recorded yet — run an agent turn." />
        ) : (
          traces.map((trace) => (
            <RailItem
              key={trace.id}
              layout="row"
              className="wl-tracing__trace-card"
              active={trace.id === selectedTraceId}
              onClick={() => onSelectTrace(trace.trace_id)}
            >
              <span className="wl-tracing__status">{statusIcon(trace.status)}</span>
              <span className="wl-tracing__trace-name wl-rail__title">{trace.name}</span>
              <span className="wl-tracing__meta wl-rail__meta">
                {new Date(trace.start_time).toLocaleString()} · {durationMs(trace.start_time, trace.end_time)}
                {trace.total_tokens != null ? ` · ${trace.total_tokens} tok` : ''}
              </span>
            </RailItem>
          ))
        )}
    </RailList>
  );

  return (
    <MasterDetailPage title="TRACING" rail={rail} railLabel="Traces">
      <div className="wl-tracing__workspace">
      <div className="wl-tracing__tree" aria-label="Span tree">
        {loading ? (
          <Empty variant="loading" message="Loading…" />
        ) : orderedRuns.length === 0 ? (
          <Empty message="Select a trace to inspect its spans." />
        ) : (
          orderedRuns.map((run) => (
            <button
              key={run.id}
              type="button"
              className={`wl-tracing__span${run.id === selectedRunId ? ' is-active' : ''}`}
              style={{ ['--wl-span-depth' as string]: depthOf(run) }}
              onClick={() => onSelectRun(run.id)}
            >
              <span className="wl-tracing__status">{statusIcon(run.status)}</span>
              <span className={`wl-tracing__span-type wl-tracing__span-type--${run.run_type}`}>
                {run.run_type}
              </span>
              <span className="wl-tracing__span-name">{run.name}</span>
              <span className="wl-tracing__meta">
                {durationMs(run.start_time, run.end_time)}
                {run.total_tokens != null ? ` · ${run.total_tokens} tok` : ''}
              </span>
            </button>
          ))
        )}
      </div>

      <aside className="wl-tracing__detail" aria-label="Span detail">
        {runDetail === null ? (
          <Empty message="Select a span to see its payloads." />
        ) : (
          <SpanDetail runDetail={runDetail} />
        )}
      </aside>
      </div>
    </MasterDetailPage>
  );
}

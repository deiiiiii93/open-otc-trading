import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Tracing } from './Tracing';
import type { TraceRunDetail, TraceRunNode, TraceSummary } from '../types';

const summary: TraceSummary = {
  id: 'root', trace_id: 'root', name: 'orchestrator', run_type: 'chain',
  status: 'success', start_time: '2026-06-11T09:00:00',
  end_time: '2026-06-11T09:00:03', total_tokens: 40,
  thread_id: 7, task_id: null, workflow_id: null,
};

const runs: TraceRunNode[] = [
  {
    ...summary, parent_run_id: null, dotted_order: 'a', error: null,
    prompt_tokens: null, completion_tokens: null,
    inputs_preview: '{"q":"price it"}', inputs_truncated: false,
    outputs_preview: '{"a":"done"}', outputs_truncated: false,
  },
  {
    ...summary, id: 'tool1', name: 'price_position', run_type: 'tool',
    parent_run_id: 'root', dotted_order: 'a.b', error: null,
    prompt_tokens: null, completion_tokens: null, total_tokens: null,
    inputs_preview: '{"sym":"AAPL"}', inputs_truncated: false,
    outputs_preview: '{"pv":1.23}', outputs_truncated: false,
  },
];

function renderPage(overrides: Partial<Parameters<typeof Tracing>[0]> = {}) {
  const props = {
    threadId: 7,
    traces: [summary],
    selectedTraceId: 'root',
    onSelectTrace: vi.fn(),
    runs,
    selectedRunId: 'root',
    onSelectRun: vi.fn(),
    runDetail: null,
    loading: false,
    ...overrides,
  };
  render(<Tracing {...props} />);
  return props;
}

// A realistic LangChain LLM Run.outputs envelope: an AIMessage with markdown
// text + a tool_call, plus usage_metadata — the exact shape persisted by the
// LocalTracer (see backend/app/services/tracing/tracer.py).
function llmOutput(opts: { text?: string; toolCalls?: Array<{ name: string; args: unknown }> }) {
  const content: unknown[] = [];
  if (opts.text) content.push({ type: 'text', text: opts.text, index: 0 });
  for (const tc of opts.toolCalls ?? []) {
    content.push({ type: 'tool_call', id: 'call_1', name: tc.name, args: tc.args });
  }
  const toolCalls = (opts.toolCalls ?? []).map((tc) => ({
    name: tc.name, args: tc.args, id: 'call_1', type: 'tool_call',
  }));
  return {
    generations: [[{
      text: opts.text ?? '',
      generation_info: null,
      type: 'ChatGeneration',
      message: {
        lc: 1, type: 'constructor',
        id: ['langchain', 'schema', 'messages', 'AIMessage'],
        kwargs: {
          content,
          response_metadata: { model_provider: 'anthropic', stop_reason: 'tool_use' },
          type: 'ai',
          id: 'lc_run--x',
          tool_calls: toolCalls,
          usage_metadata: { input_tokens: 14669, output_tokens: 167, total_tokens: 14836 },
          invalid_tool_calls: [],
        },
      },
    }]],
    llm_output: null,
    run: null,
    type: 'LLMResult',
  };
}

describe('Tracing', () => {
  it('renders trace list, span tree, and thread filter chip', () => {
    renderPage();
    expect(screen.getByText('Thread #7')).toBeInTheDocument();
    expect(screen.getAllByText('orchestrator').length).toBeGreaterThan(0);
    expect(screen.getByText('price_position')).toBeInTheDocument();
    expect(screen.getByText('tool')).toBeInTheDocument(); // run-type badge
  });

  it('selecting a span calls onSelectRun', () => {
    const props = renderPage();
    fireEvent.click(screen.getByRole('button', { name: /price_position/ }));
    expect(props.onSelectRun).toHaveBeenCalledWith('tool1');
  });

  it('shows the span detail payloads', () => {
    renderPage({
      runDetail: {
        ...summary, run_type: 'chain', parent_run_id: null, dotted_order: 'a', error: null,
        prompt_tokens: 11, completion_tokens: 4,
        inputs: '{"q":"price it"}', outputs: '{"a":"done"}', extra: '{}',
      },
    });
    expect(screen.getByText(/price it/)).toBeInTheDocument();
    expect(screen.getByText(/Inputs/)).toBeInTheDocument();
  });

  it('hides the raw/rendered toggle when no JSON payload is present', () => {
    renderPage({
      runDetail: {
        ...summary, parent_run_id: null, dotted_order: 'a', error: null,
        prompt_tokens: null, completion_tokens: null,
        inputs: 'not json', outputs: null, extra: '{}',
      },
    });
    expect(screen.queryByRole('button', { name: 'Raw' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rendered' })).not.toBeInTheDocument();
  });

  it('renders an LLM output as markdown + tool calls + usage in rendered mode', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'llm', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: 14669, completion_tokens: 167, total_tokens: 14836,
          inputs: JSON.stringify({ prompts: ['System: hi\nHuman: go'] }),
          outputs: JSON.stringify(llmOutput({
            text: '### Portfolio Match\n\n| Field | Value |\n|---|---|\n| **id** | `1` |',
            toolCalls: [{ name: 'list_portfolios', args: { name: 'Default' } }],
          })),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    // Default: rendered mode — the assistant text is a real heading + table.
    expect(container.querySelector('.wl-tracing__markdown h3')).not.toBeNull();
    expect(container.querySelector('.wl-tracing__markdown table')).not.toBeNull();
    // Tool call rendered by name with its args.
    expect(container.querySelector('.wl-tracing__toolcall-name')?.textContent).toBe('list_portfolios');
    expect(container.querySelector('.wl-tracing__toolcall-args')?.textContent).toContain('Default');
    // Usage chips.
    expect(container.querySelector('.wl-tracing__usage-chip')?.textContent).toMatch(/in 14669/);

    // Switch to raw: the LangChain envelope shows as plain JSON, no heading.
    fireEvent.click(screen.getByRole('button', { name: 'Raw' }));
    expect(container.querySelector('.wl-tracing__markdown h3')).toBeNull();
    expect(container.querySelector('.wl-tracing__toolcall-name')).toBeNull();
    expect(container.querySelector('.wl-tracing__usage-chip')).toBeNull();
  });

  it('renders a tool output envelope by unwrapping the ToolMessage repr', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'tool', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({ todos: [{ content: 'do thing', status: 'in_progress' }] }),
          outputs: JSON.stringify({
            output: "content='{\"ok\":true}' name='write_todos' tool_call_id='call_1'",
          }),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    // Rendered: the inner JSON content is unwrapped and shown as colored JSON,
    // and the tool name appears as the section eyebrow. (Inputs also renders
    // colored JSON, so check the "ok" key exists among all rendered keys.)
    const keys = Array.from(container.querySelectorAll('.wl-json-key')).map((el) => el.textContent);
    expect(keys).toContain('"ok"');
    expect(screen.getByText('write_todos')).toBeInTheDocument();

    // Raw: the full envelope pretty-printed.
    fireEvent.click(screen.getByRole('button', { name: 'Raw' }));
    expect(container.textContent).toContain('tool_call_id');
  });

  it('renders LangChain chat inputs as readable message cards', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'chain', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({
            messages: [
              {
                lc: 1,
                type: 'constructor',
                id: ['langchain', 'schema', 'messages', 'SystemMessage'],
                kwargs: { content: '## Rules\n\nUse tools carefully.', type: 'system' },
              },
              {
                lc: 1,
                type: 'constructor',
                id: ['langchain', 'schema', 'messages', 'HumanMessage'],
                kwargs: { content: 'What is the latest risk?', type: 'human' },
              },
            ],
          }),
          outputs: null,
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(screen.getByText('System')).toBeInTheDocument();
    expect(screen.getByText('User')).toBeInTheDocument();
    expect(container.querySelectorAll('.wl-tracing__message')).toHaveLength(2);
    expect(container.querySelector('.wl-tracing__markdown h2')).not.toBeNull();
    expect(screen.queryByText(/"messages"/)).not.toBeInTheDocument();
  });

  it('renders legacy stringified LangChain messages without losing content', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'chain', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({
            messages: ["content='## Context\\n\\nUser asks for risk.' additional_kwargs={} response_metadata={}"],
            files: {},
          }),
          outputs: JSON.stringify({ skills_metadata: [] }),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(container.querySelector('.wl-tracing__markdown h2')).not.toBeNull();
    expect(screen.getByText(/User asks for risk/)).toBeInTheDocument();
    expect(screen.queryByText('No content')).not.toBeInTheDocument();
  });

  it('renders chain payloads as readable sections instead of raw JSON', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'chain', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({ input: '### Request\n\nFind latest risk.' }),
          outputs: JSON.stringify({ output: '### Result\n\n| Field | Value |\n|---|---|\n| Status | OK |' }),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(container.querySelector('.wl-tracing__section-card')).not.toBeNull();
    expect(screen.getByText('Input')).toBeInTheDocument();
    expect(screen.getByText('Output')).toBeInTheDocument();
    expect(container.querySelector('.wl-tracing__markdown table')).not.toBeNull();
    expect(screen.queryByText(/"output"/)).not.toBeInTheDocument();
  });

  it('does not show No content for assistant tool-call-only messages', () => {
    render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'chain', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({
            messages: [{
              lc: 1,
              type: 'constructor',
              id: ['langchain', 'schema', 'messages', 'AIMessage'],
              kwargs: {
                content: '',
                type: 'ai',
                tool_calls: [{ name: 'run_python', args: { code: '1 + 1' }, id: 'call_1' }],
              },
            }],
          }),
          outputs: null,
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(screen.getByText('Assistant')).toBeInTheDocument();
    expect(screen.getByText('run_python')).toBeInTheDocument();
    expect(screen.queryByText('No content')).not.toBeInTheDocument();
  });

  it('renders tool outputs from repr strings as markdown content', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="tool1"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, id: 'tool1', name: 'run_python', run_type: 'tool', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({ code: 'print(1)' }),
          outputs: JSON.stringify({ output: "content='### Chart\\n\\nTool result' name='run_python' tool_call_id='call_1'" }),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(screen.getAllByText('run_python').length).toBeGreaterThan(1);
    expect(container.querySelector('.wl-tracing__markdown h3')).not.toBeNull();
    expect(screen.getByText('call_1')).toBeInTheDocument();
  });

  it('renders structured object payloads as key-value tables', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="root"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, run_type: 'chain', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({ input: 'go' }),
          outputs: JSON.stringify({ result: { portfolio_id: 2, status: 'completed', value: 0.5 } }),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(container.querySelector('.wl-tracing__data-table')).not.toBeNull();
    expect(screen.getByText('Portfolio Id')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.queryByText(/"portfolio_id"/)).not.toBeInTheDocument();
  });

  it('renders arrays of objects as data tables', () => {
    const { container } = render(
      <Tracing
        threadId={7}
        traces={[summary]}
        selectedTraceId="root"
        onSelectTrace={vi.fn()}
        runs={runs}
        selectedRunId="tool1"
        onSelectRun={vi.fn()}
        runDetail={{
          ...summary, id: 'tool1', run_type: 'tool', parent_run_id: null, dotted_order: 'a', error: null,
          prompt_tokens: null, completion_tokens: null, total_tokens: null,
          inputs: JSON.stringify({ query: 'portfolios' }),
          outputs: JSON.stringify({ output: [{ id: 1, name: 'Control', status: 'active' }, { id: 2, name: 'Desk', status: 'paused' }] }),
          extra: '{}',
        }}
        loading={false}
      />,
    );

    expect(container.querySelector('.wl-tracing__data-table')).not.toBeNull();
    expect(screen.getByText('Control')).toBeInTheDocument();
    expect(screen.getByText('paused')).toBeInTheDocument();
  });

  it('renders empty state without traces', () => {
    renderPage({ traces: [], runs: [], selectedTraceId: null, runDetail: null });
    expect(screen.getByText(/No traces/)).toBeInTheDocument();
  });
});

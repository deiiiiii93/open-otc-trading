import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Tracing } from './Tracing';
import type { TraceRunNode, TraceSummary } from '../types';

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
        ...summary, parent_run_id: null, dotted_order: 'a', error: null,
        prompt_tokens: 11, completion_tokens: 4,
        inputs: '{"q":"price it"}', outputs: '{"a":"done"}', extra: '{}',
      },
    });
    expect(screen.getByText(/price it/)).toBeInTheDocument();
    expect(screen.getByText(/Inputs/)).toBeInTheDocument();
  });

  it('renders empty state without traces', () => {
    renderPage({ traces: [], runs: [], selectedTraceId: null, runDetail: null });
    expect(screen.getByText(/No traces/)).toBeInTheDocument();
  });
});

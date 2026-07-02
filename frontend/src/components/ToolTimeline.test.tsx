import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ToolTimeline } from './ToolTimeline';
import type { ToolEvent } from '../types';

const events: ToolEvent[] = [
  {
    id: 'r1',
    name: 'get_positions',
    status: 'done',
    duration_ms: 120,
    args: { portfolio_id: 1 },
    output: { count: 3 },
  },
  { id: 'r2', name: 'price_product', status: 'running', args: { underlying: 'SPX' } },
];

describe('ToolTimeline', () => {
  it('renders a list item per tool event', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('get_positions');
    expect(items[1]).toHaveTextContent('price_product');
  });

  it('shows duration_ms for completed events', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    expect(screen.getAllByText(/120/).length).toBeGreaterThan(0);
  });

  it('shows running indicator for running events', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    const running = screen.getByText('price_product').closest('li');
    expect(running).toHaveAttribute('data-status', 'running');
  });

  it('hides args in compact mode', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    expect(screen.queryByText(/portfolio_id/)).not.toBeInTheDocument();
    expect(screen.queryByText(/underlying/)).not.toBeInTheDocument();
  });

  it('shows args inside <details> in detailed mode', () => {
    render(<ToolTimeline events={events} mode="detailed" />);
    const details = document.querySelectorAll('details');
    expect(details.length).toBe(3);
    expect(screen.getByText(/portfolio_id/)).toBeInTheDocument();
    expect(screen.getByText(/underlying/)).toBeInTheDocument();
  });

  it('can render the whole timeline collapsed by default', () => {
    render(<ToolTimeline events={events} mode="detailed" defaultOpen={false} />);
    const outer = document.querySelector('.wl-tool-timeline-box');
    expect(outer).not.toHaveAttribute('open');
    expect(screen.getByText('Tool use')).toBeInTheDocument();
    expect(screen.getByText(/2 calls/)).toBeInTheDocument();
  });

  it('groups consecutive events with the same name into one row', () => {
    const dup: ToolEvent[] = [
      { id: 'r1', name: 'price_product', status: 'done', duration_ms: 50 },
      { id: 'r2', name: 'price_product', status: 'done', duration_ms: 80 },
    ];
    render(<ToolTimeline events={dup} mode="compact" />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent(/price_product/);
    expect(items[0]).toHaveTextContent(/×2/);
    expect(items[0]).toHaveTextContent(/130ms/);
  });

  it('keeps non-consecutive same-name events as separate rows', () => {
    const mixed: ToolEvent[] = [
      { id: 'r1', name: 'read_file', status: 'done', duration_ms: 10 },
      { id: 'r2', name: 'price_product', status: 'done', duration_ms: 50 },
      { id: 'r3', name: 'read_file', status: 'done', duration_ms: 20 },
    ];
    render(<ToolTimeline events={mixed} mode="compact" />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent(/read_file/);
    expect(items[1]).toHaveTextContent(/price_product/);
    expect(items[2]).toHaveTextContent(/read_file/);
  });

  it('expands grouped events individually in detailed mode', () => {
    const dup: ToolEvent[] = [
      { id: 'r1', name: 'price_product', status: 'done', duration_ms: 50, args: { a: 1 } },
      { id: 'r2', name: 'price_product', status: 'done', duration_ms: 80, args: { a: 2 } },
    ];
    render(<ToolTimeline events={dup} mode="detailed" />);
    expect(screen.getByText(/×2/)).toBeInTheDocument();
    expect(screen.getByText(/"a": 1/)).toBeInTheDocument();
    expect(screen.getByText(/"a": 2/)).toBeInTheDocument();
  });

  it('shows error status on a group if any event errored', () => {
    const groupWithError: ToolEvent[] = [
      { id: 'r1', name: 'read_file', status: 'done', duration_ms: 10 },
      { id: 'r2', name: 'read_file', status: 'error', duration_ms: 5, error: 'boom' },
    ];
    render(<ToolTimeline events={groupWithError} mode="compact" />);
    const item = screen.getByText('read_file').closest('li');
    expect(item).toHaveAttribute('data-status', 'error');
  });

  it('renders nothing when events is empty', () => {
    const { container } = render(<ToolTimeline events={[]} mode="compact" />);
    expect(container.firstChild).toBeNull();
  });

  it('falls back to compact rendering if events is a string array (legacy meta)', () => {
    render(
      <ToolTimeline
        events={['get_positions starting', 'get_positions done'] as unknown as ToolEvent[]}
        mode="detailed"
      />,
    );
    expect(screen.getByText(/get_positions starting/)).toBeInTheDocument();
    expect(screen.getByText(/get_positions done/)).toBeInTheDocument();
  });
});

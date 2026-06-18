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

  it('keeps two events with the same name distinct via id', () => {
    const dup: ToolEvent[] = [
      { id: 'r1', name: 'price_product', status: 'done', duration_ms: 50 },
      { id: 'r2', name: 'price_product', status: 'done', duration_ms: 80 },
    ];
    render(<ToolTimeline events={dup} mode="compact" />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(/50/);
    expect(items[1]).toHaveTextContent(/80/);
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

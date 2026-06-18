import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqInbox } from './RfqInbox';
import type { RFQ } from '../types';

const rfqs: RFQ[] = [
  { id: 1042, client_name: 'Lakeshore Cap', channel: 'chat', status: 'pending_approval', request_payload: {}, quote_payload: {} },
  { id: 1041, client_name: 'Meridian',     channel: 'form', status: 'approved',          request_payload: {}, quote_payload: {} },
];

describe('RfqInbox', () => {
  it('renders one row per RFQ', () => {
    render(<RfqInbox rfqs={rfqs} selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText(/#1042/)).toBeInTheDocument();
    expect(screen.getByText(/Lakeshore Cap/)).toBeInTheDocument();
    expect(screen.getByText(/#1041/)).toBeInTheDocument();
  });

  it('marks selected row', () => {
    const { container } = render(<RfqInbox rfqs={rfqs} selectedId={1042} onSelect={() => {}} />);
    const selected = container.querySelectorAll('.wl-rfq-inbox__row--selected');
    expect(selected.length).toBe(1);
  });

  it('calls onSelect when row clicked', async () => {
    const onSelect = vi.fn();
    render(<RfqInbox rfqs={rfqs} selectedId={null} onSelect={onSelect} />);
    await userEvent.click(screen.getByText(/#1041/));
    expect(onSelect).toHaveBeenCalledWith(1041);
  });

  it('shows empty state when no rfqs', () => {
    render(<RfqInbox rfqs={[]} selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText(/No RFQs/i)).toBeInTheDocument();
  });
});

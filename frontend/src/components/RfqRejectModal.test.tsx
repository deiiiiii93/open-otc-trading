import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqRejectModal } from './RfqRejectModal';

describe('RfqRejectModal', () => {
  it('renders title with rfq id when open', () => {
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={() => {}} />);
    expect(screen.getByText(/Reject RFQ #1042/i)).toBeInTheDocument();
  });

  it('disables confirm when reason is empty', () => {
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={() => {}} />);
    expect(screen.getByRole('button', { name: /confirm reject/i })).toBeDisabled();
  });

  it('enables confirm when reason has text', async () => {
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={() => {}} />);
    await userEvent.type(screen.getByLabelText(/reason/i), 'price too aggressive');
    expect(screen.getByRole('button', { name: /confirm reject/i })).toBeEnabled();
  });

  it('calls onConfirm with id and reason', async () => {
    const onConfirm = vi.fn();
    render(<RfqRejectModal open rfqId={1042} onConfirm={onConfirm} onOpenChange={() => {}} />);
    await userEvent.type(screen.getByLabelText(/reason/i), 'price too aggressive');
    await userEvent.click(screen.getByRole('button', { name: /confirm reject/i }));
    expect(onConfirm).toHaveBeenCalledWith(1042, 'price too aggressive');
  });

  it('calls onOpenChange(false) on cancel', async () => {
    const onOpenChange = vi.fn();
    render(<RfqRejectModal open rfqId={1042} onConfirm={() => {}} onOpenChange={onOpenChange} />);
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

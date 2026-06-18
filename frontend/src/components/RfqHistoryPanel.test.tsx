import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RfqHistoryPanel } from './RfqHistoryPanel';
import type { RFQ } from '../types';

const rfqs: RFQ[] = [
  {
    id: 42,
    client_name: 'Demo Client',
    channel: 'form',
    status: 'pending_approval',
    request_payload: { product_type: 'SnowballOption' },
    quote_payload: {},
    created_at: '2026-06-06T08:00:00Z',
  },
  {
    id: 41,
    client_name: 'Demo Client',
    channel: 'chat',
    status: 'approved',
    request_payload: { product_type: 'EuropeanVanillaOption' },
    quote_payload: {},
    created_at: '2026-06-05T08:00:00Z',
  },
];

const labelFor = (productType: string) =>
  productType === 'SnowballOption' ? 'Snowball' : productType === 'EuropeanVanillaOption' ? 'Vanilla' : productType;

describe('RfqHistoryPanel', () => {
  it('renders rows with product label and status, and selects on click', async () => {
    const onSelect = vi.fn();
    render(
      <RfqHistoryPanel rfqs={rfqs} selectedRfqId={42} productLabelFor={labelFor} onSelect={onSelect} />,
    );

    const list = screen.getByRole('list', { name: /my rfqs/i });
    const row42 = within(list).getByRole('button', { name: /#42 snowball/i });
    expect(row42).toHaveAttribute('aria-pressed', 'true');
    expect(within(list).getByText('Pending approval')).toBeInTheDocument();

    await userEvent.click(within(list).getByRole('button', { name: /#41 vanilla/i }));
    expect(onSelect).toHaveBeenCalledWith(41);
  });

  it('offers Clone only on the selected row', async () => {
    const onClone = vi.fn();
    render(
      <RfqHistoryPanel rfqs={rfqs} selectedRfqId={41} productLabelFor={labelFor} onClone={onClone} />,
    );

    expect(screen.queryByRole('button', { name: /clone rfq 42/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /clone rfq 41/i }));
    expect(onClone).toHaveBeenCalledWith(rfqs[1]);
  });

  it('shows an empty state without rfqs', () => {
    render(<RfqHistoryPanel rfqs={[]} selectedRfqId={null} productLabelFor={labelFor} />);
    expect(screen.getByText(/no rfqs submitted yet/i)).toBeInTheDocument();
  });
});

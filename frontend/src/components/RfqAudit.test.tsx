import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RfqAudit } from './RfqAudit';
import type { RFQ } from '../types';

const pending: RFQ = {
  id: 1, client_name: 'C', channel: 'form', status: 'pending_approval',
  request_payload: {}, quote_payload: {}, approved_response: null,
};

const approved: RFQ = {
  ...pending, status: 'approved', approved_response: 'sent',
};

describe('RfqAudit', () => {
  it('shows created event', () => {
    render(<RfqAudit rfq={pending} />);
    expect(screen.getByText(/created/i)).toBeInTheDocument();
  });

  it('shows pending status when pending_approval', () => {
    render(<RfqAudit rfq={pending} />);
    expect(screen.getByText(/pending desk approval/i)).toBeInTheDocument();
  });

  it('shows released event when approved', () => {
    render(<RfqAudit rfq={approved} />);
    expect(screen.getByText(/released to client/i)).toBeInTheDocument();
  });

  it('notes audit limitation', () => {
    render(<RfqAudit rfq={pending} />);
    expect(screen.getByText(/derived from rfq fields/i)).toBeInTheDocument();
  });
});

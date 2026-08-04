import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ConfirmationsLive } from './Confirmations.live';
import * as client from '../api/client';
import type { ConfirmationBatch, ExtractedTrade } from '../types';

function makeTrade(o: Partial<ExtractedTrade> = {}): ExtractedTrade {
  return {
    id: 10, document_id: 1, seq: 1, family: 'SnowballOption',
    extracted_terms: {}, terms: {},
    underlying: '600519.SH', quantity: 100, entry_price: 10,
    currency: 'CNY', counterparty: null, trade_date: null,
    external_trade_id: null, confidence: 0.9,
    evidence: {}, validation_status: 'valid', validation_errors: [],
    status: 'extracted', booked_position_id: null, reject_reason: null,
    ...o,
  };
}

function makeBatch(o: Partial<ConfirmationBatch> = {}): ConfirmationBatch {
  return {
    id: 1, source: 'web', default_portfolio_id: 7, task_id: null,
    created_at: '2026-01-01T00:00:00Z',
    documents: [{
      id: 1, filename: 'confirm.pdf', sha256: 'abc', byte_len: 10, mime: 'application/pdf',
      page_count: 1, extract_mode: 'text', status: 'parsed', error: null,
      model_provenance: null, parsed_at: '2026-01-01T00:00:00Z', trades: [makeTrade()],
    }],
    ...o,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(client, 'listConfirmationBatches').mockResolvedValue([makeBatch()]);
  vi.spyOn(client, 'listPortfoliosWithIds').mockResolvedValue([{ id: 7, name: 'Macro' }]);
});

describe('ConfirmationsLive — Save validation', () => {
  it('blocks Save with an inline error and never calls the API when quantity is not a number', async () => {
    vi.spyOn(client, 'updateExtractedTrade').mockResolvedValue(makeTrade());
    render(<ConfirmationsLive />);
    await waitFor(() => screen.getByLabelText('Quantity'));

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: 'abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save trade 10' }));

    await waitFor(() => expect(screen.getByText('Quantity must be a number.')).toBeInTheDocument());
    expect(client.updateExtractedTrade).not.toHaveBeenCalled();
  });

  it('blocks Save with an inline error and never calls the API when underlying is cleared', async () => {
    vi.spyOn(client, 'updateExtractedTrade').mockResolvedValue(makeTrade());
    render(<ConfirmationsLive />);
    await waitFor(() => screen.getByLabelText('Underlying'));

    fireEvent.change(screen.getByLabelText('Underlying'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save trade 10' }));

    await waitFor(() => expect(screen.getByText('Underlying is required.')).toBeInTheDocument());
    expect(client.updateExtractedTrade).not.toHaveBeenCalled();
  });

  it('re-editing a blocked field clears the inline error without re-attempting the API call', async () => {
    vi.spyOn(client, 'updateExtractedTrade').mockResolvedValue(makeTrade());
    render(<ConfirmationsLive />);
    await waitFor(() => screen.getByLabelText('Quantity'));

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: 'abc' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save trade 10' }));
    await waitFor(() => expect(screen.getByText('Quantity must be a number.')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '200' } });
    expect(screen.queryByText('Quantity must be a number.')).not.toBeInTheDocument();
    expect(client.updateExtractedTrade).not.toHaveBeenCalled();
  });

  it('saves valid edits and honestly omits an untouched-blank optional numeric field', async () => {
    vi.spyOn(client, 'updateExtractedTrade').mockResolvedValue(makeTrade());
    vi.spyOn(client, 'getConfirmationBatch').mockResolvedValue(makeBatch());
    render(<ConfirmationsLive />);
    await waitFor(() => screen.getByLabelText('Entry price'));

    // Blank an optional field before saving — this must be sent as "leave
    // unchanged" (omitted), never as an explicit null the server would drop.
    fireEvent.change(screen.getByLabelText('Entry price'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save trade 10' }));

    await waitFor(() => expect(client.updateExtractedTrade).toHaveBeenCalledTimes(1));
    const [tradeId, body] = vi.mocked(client.updateExtractedTrade).mock.calls[0];
    expect(tradeId).toBe(10);
    expect(body).toMatchObject({ underlying: '600519.SH', currency: 'CNY' });
    expect(body).not.toHaveProperty('entry_price');

    await waitFor(() => expect(screen.getByText('Trade saved.')).toBeInTheDocument());
  });
});

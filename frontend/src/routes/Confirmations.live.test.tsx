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

describe('ConfirmationsLive — parse polling', () => {
  const parsingDoc = (id: number) => ({
    id, filename: `parsing-${id}.pdf`, sha256: 'abc', byte_len: 10, mime: 'application/pdf',
    page_count: 1, extract_mode: null, status: 'parsing' as const, error: null,
    model_provenance: null, parsed_at: null, trades: [],
  });

  // The selected batch is #1; the batch still parsing is #2. Before the fix
  // the poll refreshed only the selected batch, so #2 froze mid-parse and its
  // rail badge never caught up.
  it('keeps polling for a batch that is parsing but NOT selected', async () => {
    vi.useFakeTimers();
    try {
      const stale = [makeBatch({ id: 1 }), makeBatch({ id: 2, documents: [parsingDoc(2)] })];
      const settled = [makeBatch({ id: 1 }), makeBatch({ id: 2 })];
      const list = vi.spyOn(client, 'listConfirmationBatches').mockResolvedValue(stale);

      render(<ConfirmationsLive />);
      // Wait for the response to reach STATE, not merely for the call to have
      // been made — the poll effect only registers once `batches` updates.
      // Batch #1 is the auto-selected parsed one, so the only 'parsing' text
      // on screen is batch #2's rail badge.
      await vi.waitFor(() => expect(screen.getByText('parsing')).toBeInTheDocument());

      list.mockResolvedValue(settled);
      await vi.advanceTimersByTimeAsync(2000);
      await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(2));

      // Every document is terminal now, so the loop must stop on its own.
      await vi.waitFor(() => expect(screen.queryByText('parsing')).not.toBeInTheDocument());
      await vi.advanceTimersByTimeAsync(6000);
      expect(list).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('never starts a poll when every batch is already terminal', async () => {
    vi.useFakeTimers();
    try {
      const list = vi.spyOn(client, 'listConfirmationBatches').mockResolvedValue([makeBatch()]);
      render(<ConfirmationsLive />);
      await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(1));
      await vi.advanceTimersByTimeAsync(6000);
      expect(list).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
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

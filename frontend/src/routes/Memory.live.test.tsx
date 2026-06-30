import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryLive } from './Memory.live';
import * as client from '../api/client';
import type { MemoryFact } from '../types';

const fact = (over: Partial<MemoryFact> = {}): MemoryFact => ({
  id: 1, scope_type: 'domain', scope_id: 'global', content: 'vol skew steepens',
  confidence: 0.88, status: 'proposed', category: null, source_error: false,
  pinned: false, created_by: 'extractor', extractor_model: 'deepseek/deepseek-v4-flash',
  source_session_id: 318, created_at: '2026-06-30T00:00:00', updated_at: '2026-06-30T00:00:00', ...over,
});
const status = {
  enabled: true,
  config: { confidence_floor: 0.7, max_facts_per_scope: 100, max_correction_facts: 20, injection_token_budget: 2000, correction_token_budget: 1000 },
  counts: { domain: { proposed: 1 } },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(client, 'getMemoryStatus').mockResolvedValue(status as never);
  vi.spyOn(client, 'listPortfoliosWithIds').mockResolvedValue([{ id: 7, name: 'Macro' }]);
  vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact()], total: 1 } as never);
});

describe('MemoryLive', () => {
  it('lands on All/Current and shows a proposed domain fact', async () => {
    render(<MemoryLive />);
    await waitFor(() => expect(screen.getByText('vol skew steepens')).toBeInTheDocument());
    expect(client.listMemoryFacts).toHaveBeenCalledWith(
      expect.not.objectContaining({ status: expect.anything(), scope_type: expect.anything() }),
    );
  });

  it('switching to Domain tab refetches with scope_type=domain', async () => {
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    await userEvent.click(screen.getByRole('tab', { name: /domain/i }));
    await waitFor(() =>
      expect(client.listMemoryFacts).toHaveBeenLastCalledWith(expect.objectContaining({ scope_type: 'domain' })),
    );
  });

  it('approve calls approveMemoryFact then refetches', async () => {
    vi.spyOn(client, 'approveMemoryFact').mockResolvedValue({} as never);
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(client.approveMemoryFact).toHaveBeenCalledWith(1));
  });

  it('pin toggle calls setMemoryFactPinned with the negated flag', async () => {
    vi.spyOn(client, 'setMemoryFactPinned').mockResolvedValue({} as never);
    vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact({ status: 'active', pinned: false })], total: 1 } as never);
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    fireEvent.click(screen.getByRole('button', { name: /^pin$/i }));
    await waitFor(() => expect(client.setMemoryFactPinned).toHaveBeenCalledWith(1, true));
  });

  it('edit submits patchMemoryFact with changed fields incl. category clear', async () => {
    vi.spyOn(client, 'patchMemoryFact').mockResolvedValue({} as never);
    vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact({ status: 'active', category: 'pref' })], total: 1 } as never);
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(screen.getByLabelText(/content/i), { target: { value: 'updated' } });
    fireEvent.change(screen.getByLabelText(/category/i), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() =>
      expect(client.patchMemoryFact).toHaveBeenCalledWith(1, expect.objectContaining({ content: 'updated', category: '' })),
    );
  });

  it('create from All tab with book scope sends scope_id=String(id)', async () => {
    vi.spyOn(client, 'createMemoryFact').mockResolvedValue({} as never);
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    fireEvent.click(screen.getByRole('button', { name: /new/i }));
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: 'book' } });
    fireEvent.change(screen.getByLabelText(/portfolio/i), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText(/content/i), { target: { value: 'book fact' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() =>
      expect(client.createMemoryFact).toHaveBeenCalledWith(
        expect.objectContaining({ scope_type: 'book', scope_id: '7', content: 'book fact' }),
      ),
    );
  });

  it('delete requires confirm', async () => {
    vi.spyOn(client, 'deleteMemoryFact').mockResolvedValue(undefined as never);
    vi.spyOn(client, 'listMemoryFacts').mockResolvedValue({ items: [fact({ status: 'active' })], total: 1 } as never);
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    fireEvent.click(screen.getByRole('button', { name: /delete/i }));
    expect(client.deleteMemoryFact).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /archive this fact/i }));
    await waitFor(() => expect(client.deleteMemoryFact).toHaveBeenCalledWith(1));
  });

  it('status fetch failure keeps the table', async () => {
    vi.spyOn(client, 'getMemoryStatus').mockRejectedValue(new Error('boom'));
    render(<MemoryLive />);
    await waitFor(() => expect(screen.getByText('vol skew steepens')).toBeInTheDocument());
  });

  it('load more appends the next page', async () => {
    vi.spyOn(client, 'listMemoryFacts')
      .mockResolvedValueOnce({ items: [fact({ id: 1 })], total: 2 } as never)
      .mockResolvedValueOnce({ items: [fact({ id: 2, content: 'second fact' })], total: 2 } as never);
    render(<MemoryLive />);
    await waitFor(() => screen.getByText('vol skew steepens'));
    fireEvent.click(screen.getByRole('button', { name: /load more/i }));
    await waitFor(() => expect(screen.getByText('second fact')).toBeInTheDocument());
    expect(client.listMemoryFacts).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 }));
  });
});

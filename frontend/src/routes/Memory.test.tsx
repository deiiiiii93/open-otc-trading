import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Memory, type MemoryProps } from './Memory';
import type { MemoryFact } from '../types';

const f = (o: Partial<MemoryFact> = {}): MemoryFact => ({
  id: 1, scope_type: 'domain', scope_id: 'global', content: 'c', confidence: 0.8,
  status: 'proposed', category: null, source_error: false, pinned: false,
  created_by: 'extractor', extractor_model: 'm', source_session_id: 9,
  created_at: '', updated_at: '', ...o,
});

const base: MemoryProps = {
  facts: [], total: 0,
  status: {
    enabled: true,
    config: { confidence_floor: 0.7, max_facts_per_scope: 100, max_correction_facts: 20, injection_token_budget: 2000, correction_token_budget: 1000 },
    counts: { domain: { proposed: 2 }, user: { active: 5 } },
  },
  loading: false, error: null, feedback: null,
  activeScope: 'all', statusFilter: 'current', search: '',
  portfolios: [], portfoliosError: null, selectedPortfolio: null,
  rowBusy: new Set<number>(), confidenceFloor: 0.7,
  modal: null, modalSaving: false, modalError: null,
  onScope: vi.fn(), onStatusFilter: vi.fn(), onSearch: vi.fn(), onSelectPortfolio: vi.fn(),
  onRefresh: vi.fn(), onLoadMore: vi.fn(), onApprove: vi.fn(), onPin: vi.fn(),
  onEdit: vi.fn(), onDelete: vi.fn(), onNew: vi.fn(),
  onModalChange: vi.fn(), onModalSave: vi.fn(), onModalCancel: vi.fn(), onConfirmDelete: vi.fn(),
};

describe('Memory presentational', () => {
  it('shows the proposed attention chip from counts', () => {
    render(<Memory {...base} facts={[f()]} />);
    expect(screen.getByText(/proposed 2/i)).toBeInTheDocument();
  });
  it('disabled banner when status.enabled is false', () => {
    render(<Memory {...base} status={{ ...base.status!, enabled: false }} facts={[f()]} />);
    expect(screen.getByText(/capture is off/i)).toBeInTheDocument();
  });
  it('Approve shown only for proposed; archived has no actions', () => {
    render(<Memory {...base} facts={[f({ id: 1, status: 'proposed' }), f({ id: 2, status: 'archived' })]} />);
    expect(screen.getAllByRole('button', { name: /approve/i }).length).toBe(1);
  });
  it('Load more visible when total > facts.length', () => {
    render(<Memory {...base} facts={[f()]} total={50} />);
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
  });
  it('book row in the All tab shows the portfolio name', () => {
    render(<Memory {...base} activeScope="all" portfolios={[{ id: 7, name: 'Macro' }]}
      facts={[f({ id: 3, scope_type: 'book', scope_id: '7', content: 'book fact' })]} />);
    expect(screen.getByText('Macro')).toBeInTheDocument();
  });
});

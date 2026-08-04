import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Confirmations, type ConfirmationsProps } from './Confirmations';
import type { ConfirmationBatch, ConfirmationDocument, ExtractedTrade } from '../types';

function makeTrade(o: Partial<ExtractedTrade> = {}): ExtractedTrade {
  return {
    id: 1, document_id: 1, seq: 1, family: 'SnowballOption',
    extracted_terms: {}, terms: {},
    underlying: '600519.SH', quantity: 100, entry_price: 10,
    currency: 'CNY', counterparty: null, trade_date: null,
    external_trade_id: null, confidence: 0.9,
    evidence: { underlying: { quote: '600519.SH', page: 1 } },
    validation_status: 'valid', validation_errors: [],
    status: 'extracted', booked_position_id: null, reject_reason: null,
    ...o,
  };
}

function makeDocument(o: Partial<ConfirmationDocument> = {}): ConfirmationDocument {
  return {
    id: 1, filename: 'confirm.pdf', sha256: 'abc123', byte_len: 1024, mime: 'application/pdf',
    page_count: 1, extract_mode: 'text', status: 'parsed', error: null,
    model_provenance: null, parsed_at: '2026-01-01T00:00:00Z', trades: [],
    ...o,
  };
}

function makeBatch(o: Partial<ConfirmationBatch> = {}): ConfirmationBatch {
  return {
    id: 1, source: 'web', default_portfolio_id: null, task_id: null,
    created_at: '2026-01-01T00:00:00Z', documents: [], ...o,
  };
}

const basePortfolios = [{ id: 7, name: 'Macro' }];

const baseProps: ConfirmationsProps = {
  batches: [],
  selectedBatch: null,
  loading: false,
  error: null,
  feedback: null,
  portfolios: basePortfolios,
  portfoliosError: null,
  selectedFileNames: [],
  uploadPortfolioId: null,
  uploading: false,
  drafts: {},
  tradePortfolio: {},
  rejectReason: {},
  rowBusy: new Set<number>(),
  onFilesSelected: vi.fn(),
  onUploadPortfolioChange: vi.fn(),
  onUpload: vi.fn(),
  onSelectBatch: vi.fn(),
  onRefresh: vi.fn(),
  onDraftChange: vi.fn(),
  onSaveTrade: vi.fn(),
  onTradePortfolioChange: vi.fn(),
  onRejectReasonChange: vi.fn(),
  onBookTrade: vi.fn(),
  onRejectTrade: vi.fn(),
};

describe('Confirmations presentational', () => {
  it('shows a status badge for a document still parsing', () => {
    const batch = makeBatch({ documents: [makeDocument({ id: 2, status: 'parsing', trades: [] })] });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.getByText('parsing')).toBeInTheDocument();
  });

  it('shows the error text for a failed document', () => {
    const batch = makeBatch({
      documents: [makeDocument({ id: 3, status: 'failed', error: 'extraction blew up', trades: [] })],
    });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.getByText('extraction blew up')).toBeInTheDocument();
  });

  it('enables Book for a valid trade once a target portfolio is resolved', () => {
    const trade = makeTrade({ id: 10, validation_status: 'valid' });
    const batch = makeBatch({ documents: [makeDocument({ id: 4, trades: [trade] })] });
    render(
      <Confirmations
        {...baseProps}
        batches={[batch]}
        selectedBatch={batch}
        tradePortfolio={{ 10: 7 }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Book trade 10' })).toBeEnabled();
  });

  it('keeps Book disabled for a valid trade with no portfolio resolved', () => {
    const trade = makeTrade({ id: 13, validation_status: 'valid' });
    const batch = makeBatch({ documents: [makeDocument({ id: 7, trades: [trade] })] });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.getByRole('button', { name: 'Book trade 13' })).toBeDisabled();
  });

  it('disables Book and lists validation errors for an invalid trade', () => {
    const trade = makeTrade({
      id: 11, validation_status: 'invalid', validation_errors: ['missing underlying'],
    });
    const batch = makeBatch({ documents: [makeDocument({ id: 5, trades: [trade] })] });
    render(
      <Confirmations
        {...baseProps}
        batches={[batch]}
        selectedBatch={batch}
        tradePortfolio={{ 11: 7 }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Book trade 11' })).toBeDisabled();
    expect(screen.getByText('missing underlying')).toBeInTheDocument();
  });

  it('links a booked trade to its position', () => {
    const trade = makeTrade({ id: 12, status: 'booked', booked_position_id: 555 });
    const batch = makeBatch({ documents: [makeDocument({ id: 6, trades: [trade] })] });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    const link = screen.getByRole('link', { name: '#555' });
    expect(link).toHaveAttribute('href', expect.stringContaining('/positions'));
  });

  it('renders evidence quotes with their page number', () => {
    const trade = makeTrade({
      id: 14,
      evidence: { underlying: { quote: '600519.SH', page: 2 } },
    });
    const batch = makeBatch({ documents: [makeDocument({ id: 8, trades: [trade] })] });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.getByText(/underlying/)).toBeInTheDocument();
    expect(screen.getByText(/600519\.SH/)).toBeInTheDocument();
    expect(screen.getByText(/p\.2/)).toBeInTheDocument();
  });
});

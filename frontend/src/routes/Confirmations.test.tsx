import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import {
  Confirmations, batchStatusMark, describeParseProgress, parseProgressLabel,
  type ConfirmationsProps, type ParseProgress,
} from './Confirmations';
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
    family_check: null,
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
  it('shows the System One family check on the trade row', () => {
    const disagree = makeTrade({
      id: 20,
      family_check: {
        status: 'disagree', reason: null, jev_family: 'PhoenixOption', confidence: 0.82,
        top: [['PhoenixOption', 0.82]], model: 'typesafe/jev-1.13',
      },
    });
    const agree = makeTrade({
      id: 21,
      family_check: {
        status: 'agree', reason: null, jev_family: 'SnowballOption', confidence: 0.9,
        top: null, model: 'typesafe/jev-1.13',
      },
    });
    const unscored = makeTrade({
      id: 22,
      family_check: {
        status: 'unscored', reason: 'no_key', jev_family: null, confidence: null, top: null,
        model: 'typesafe/jev-1.13',
      },
    });
    const never = makeTrade({ id: 23 });
    const batch = makeBatch({
      documents: [makeDocument({ id: 9, trades: [disagree, agree, unscored, never] })],
    });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.getByText('family? PhoenixOption')).toBeInTheDocument();
    expect(screen.getByText('family ✓')).toBeInTheDocument();
    expect(screen.getByTitle('family check: no_key')).toBeInTheDocument();
    const neverRow = screen.getByTestId('confirmation-trade-23');
    expect(within(neverRow).queryByText(/family/)).not.toBeInTheDocument();
  });

  // Scoped to the document card: the batch rail now carries its own headline
  // 'parsing' badge, so an unscoped query matches both.
  it('shows a status badge for a document still parsing', () => {
    const batch = makeBatch({ documents: [makeDocument({ id: 2, status: 'parsing', trades: [] })] });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    const card = within(screen.getByTestId('confirmation-document-2'));
    expect(card.getByText('parsing')).toBeInTheDocument();
  });

  it('shows a live parsing banner with shimmer placeholders while a document parses', () => {
    const batch = makeBatch({ documents: [makeDocument({ id: 2, status: 'parsing', trades: [] })] });
    const { container } = render(
      <Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />,
    );
    expect(screen.getByTestId('confirmations-parsing-banner')).toBeInTheDocument();
    expect(screen.getByText('Reading the document and extracting trades…')).toBeInTheDocument();
    expect(container.querySelectorAll('.wl-skeleton').length).toBeGreaterThan(0);
    expect(screen.getByTestId('confirmation-document-2')).toHaveAttribute('aria-busy', 'true');
  });

  // Documents parse sequentially server-side, so a queued file is a distinct
  // state from an actively-parsing one and must not claim work is happening.
  it('marks a pending document as queued, without shimmer placeholders', () => {
    const batch = makeBatch({ documents: [makeDocument({ id: 4, status: 'pending', trades: [] })] });
    const { container } = render(
      <Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />,
    );
    expect(screen.getByText('Queued — documents are parsed one at a time.')).toBeInTheDocument();
    expect(container.querySelectorAll('.wl-skeleton')).toHaveLength(0);
  });

  it('drops the parsing banner once every document is terminal', () => {
    const batch = makeBatch({
      documents: [
        makeDocument({ id: 5, status: 'parsed', trades: [] }),
        makeDocument({ id: 6, status: 'failed', error: 'nope', trades: [] }),
      ],
    });
    render(<Confirmations {...baseProps} batches={[batch]} selectedBatch={batch} />);
    expect(screen.queryByTestId('confirmations-parsing-banner')).not.toBeInTheDocument();
  });

  it('describeParseProgress folds document statuses into progress counts', () => {
    const batch = makeBatch({
      documents: [
        makeDocument({ id: 1, status: 'parsed' }),
        makeDocument({ id: 2, status: 'failed', error: 'nope' }),
        makeDocument({ id: 3, status: 'parsing', filename: 'live.pdf' }),
        makeDocument({ id: 4, status: 'pending' }),
      ],
    });
    const p = describeParseProgress(batch);
    expect(p).toMatchObject({
      done: 2, parsed: 1, failed: 1, queued: 1, total: 4, inFlight: true,
    });
    expect(p.active?.filename).toBe('live.pdf');
  });

  describe('parseProgressLabel', () => {
    const progress = (o: Partial<ParseProgress> = {}): ParseProgress => ({
      done: 0, parsed: 0, failed: 0, queued: 0, active: null, total: 0, inFlight: false, ...o,
    });

    it('reports the active document position, not the queue length', () => {
      expect(parseProgressLabel(progress({
        done: 2, parsed: 2, queued: 2, active: makeDocument({ status: 'parsing' }),
        total: 5, inFlight: true,
      }))).toBe('Parsing 3 of 5…');
    });

    // Between dispatch and the first status="parsing" transition nothing is
    // actually being worked on — claiming "parsing" there would be a lie.
    it('reports queued while no document has started', () => {
      expect(parseProgressLabel(progress({
        queued: 3, active: null, total: 3, inFlight: true,
      }))).toBe('Queued — 3 documents');
    });

    it('never folds failures into the done count', () => {
      expect(parseProgressLabel(progress({
        done: 2, parsed: 1, failed: 1, active: makeDocument({ status: 'parsing' }),
        total: 4, inFlight: true,
      }))).toBe('Parsing 3 of 4… · 1 failed');
    });

    it('vanishes on a clean finish but keeps reporting failures', () => {
      expect(parseProgressLabel(progress({
        done: 3, parsed: 3, total: 3, inFlight: false,
      }))).toBeNull();
      expect(parseProgressLabel(progress({
        done: 3, parsed: 2, failed: 1, total: 3, inFlight: false,
      }))).toBe('2 parsed · 1 failed');
    });

    it('says nothing for an empty batch', () => {
      expect(parseProgressLabel(progress({}))).toBeNull();
    });
  });

  describe('batchStatusMark', () => {
    const progress = (o: Partial<ParseProgress> = {}): ParseProgress => ({
      done: 0, parsed: 0, failed: 0, queued: 0, active: null, total: 0, inFlight: false, ...o,
    });

    it('lets in-flight work outrank a failure already recorded', () => {
      expect(batchStatusMark(progress({
        failed: 1, done: 1, active: makeDocument({ status: 'parsing' }), total: 3, inFlight: true,
      }))).toEqual({ text: 'parsing', variant: 'warn', live: true });
    });

    it('distinguishes queued from parsing', () => {
      expect(batchStatusMark(progress({ queued: 2, total: 2, inFlight: true })))
        .toEqual({ text: 'queued', variant: 'warn', live: true });
    });

    it('surfaces partial failure from the rail, not just the open pane', () => {
      expect(batchStatusMark(progress({ done: 3, parsed: 2, failed: 1, total: 3 })))
        .toEqual({ text: '1 failed', variant: 'neg', live: false });
      expect(batchStatusMark(progress({ done: 2, failed: 2, total: 2 })))
        .toEqual({ text: 'failed', variant: 'neg', live: false });
    });

    it('reports a clean finish', () => {
      expect(batchStatusMark(progress({ done: 2, parsed: 2, total: 2 })))
        .toEqual({ text: 'parsed', variant: 'pos', live: false });
    });
  });

  // The rail is ~280-380px wide; fixed grid tracks summing past that used to
  // push the Docs and Status columns off the panel edge entirely.
  it('keeps the batch row to one headline badge and a small fixed-track budget', () => {
    const batch = makeBatch({
      documents: [
        makeDocument({ id: 1, status: 'parsed' }),
        makeDocument({ id: 2, status: 'parsing', trades: [] }),
      ],
    });
    const { container } = render(
      <Confirmations {...baseProps} batches={[batch]} selectedBatch={null} />,
    );
    const row = container.querySelector('.wl-table__row:not(.wl-table__row--head)') as HTMLElement;
    expect(row.querySelectorAll('.wl-confirmations__status-cell .wl-badge')).toHaveLength(1);

    const fixedRem = (row.style.gridTemplateColumns.match(/([\d.]+)rem/g) ?? [])
      .reduce((sum, t) => sum + parseFloat(t), 0);
    expect(fixedRem).toBeLessThan(8);
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

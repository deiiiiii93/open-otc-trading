import { useMemo } from 'react';
import type { ConfirmationBatch, ConfirmationDocument, ExtractedTrade } from '../types';
import { PageScaffold } from '../components/templates/PageScaffold';
import { Button } from '../components/Button';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Chip } from '../components/Chip';
import { Table, type Column } from '../components/Table';
import { Select } from '../components/Select';
import { Input } from '../components/Input';
import { Empty } from '../components/Empty';
import { routeUrl } from '../lib/routing';
import './Confirmations.css';

export type TradeDraft = {
  underlying: string;
  quantity: string;
  entry_price: string;
  currency: string;
  termsText: string;
  termsError: string | null;
  /** Set when Save is blocked by client-side field validation (required-field
   * or non-numeric-number checks) — kept separate from termsError so a JSON
   * parse failure and a field-validation failure never overwrite each other. */
  fieldsError: string | null;
};

export function tradeDraftDefaults(trade: ExtractedTrade): TradeDraft {
  return {
    underlying: trade.underlying ?? '',
    quantity: trade.quantity != null ? String(trade.quantity) : '',
    entry_price: trade.entry_price != null ? String(trade.entry_price) : '',
    currency: trade.currency ?? '',
    termsText: JSON.stringify(trade.terms ?? {}, null, 2),
    termsError: null,
    fieldsError: null,
  };
}

export interface ConfirmationsProps {
  batches: ConfirmationBatch[];
  selectedBatch: ConfirmationBatch | null;
  loading: boolean;
  error: string | null;
  feedback: string | null;
  portfolios: Array<{ id: number; name: string }>;
  portfoliosError: string | null;
  selectedFileNames: string[];
  uploadPortfolioId: number | null;
  uploading: boolean;
  drafts: Record<number, TradeDraft>;
  tradePortfolio: Record<number, number | null>;
  rejectReason: Record<number, string>;
  rowBusy: Set<number>;
  onFilesSelected: (files: FileList | null) => void;
  onUploadPortfolioChange: (id: number | null) => void;
  onUpload: () => void;
  onSelectBatch: (id: number) => void;
  onRefresh: () => void;
  onDraftChange: (tradeId: number, next: TradeDraft) => void;
  onSaveTrade: (tradeId: number) => void;
  onTradePortfolioChange: (tradeId: number, id: number | null) => void;
  onRejectReasonChange: (tradeId: number, value: string) => void;
  onBookTrade: (tradeId: number) => void;
  onRejectTrade: (tradeId: number) => void;
}

const DOC_STATUS_VARIANT: Record<ConfirmationDocument['status'], BadgeVariant> = {
  pending: 'ink',
  parsing: 'warn',
  parsed: 'pos',
  failed: 'neg',
};

const VALIDATION_VARIANT: Record<ExtractedTrade['validation_status'], BadgeVariant> = {
  valid: 'pos',
  invalid: 'neg',
  unsupported: 'warn',
};

function formatTime(iso: string): string {
  const stamped = /Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(stamped);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function docStatusCounts(batch: ConfirmationBatch): Array<[ConfirmationDocument['status'], number]> {
  const counts = new Map<ConfirmationDocument['status'], number>();
  for (const doc of batch.documents) {
    counts.set(doc.status, (counts.get(doc.status) ?? 0) + 1);
  }
  return Array.from(counts.entries());
}

function portfolioOptions(portfolios: Array<{ id: number; name: string }>, placeholder: string) {
  return [{ value: '', label: placeholder }, ...portfolios.map((p) => ({ value: String(p.id), label: p.name }))];
}

export function Confirmations(props: ConfirmationsProps) {
  const {
    batches, selectedBatch, loading, error, feedback,
    portfolios, portfoliosError, selectedFileNames, uploadPortfolioId, uploading,
    drafts, tradePortfolio, rejectReason, rowBusy,
    onFilesSelected, onUploadPortfolioChange, onUpload, onSelectBatch, onRefresh,
    onDraftChange, onSaveTrade, onTradePortfolioChange, onRejectReasonChange,
    onBookTrade, onRejectTrade,
  } = props;

  const columns = useMemo<Column<ConfirmationBatch>[]>(() => [
    { key: 'id', header: 'ID', width: '4rem', render: (b) => `#${b.id}` },
    { key: 'created', header: 'Created', width: '11rem', render: (b) => formatTime(b.created_at) },
    { key: 'source', header: 'Source', width: '6rem', render: (b) => b.source },
    { key: 'docs', header: 'Docs', numeric: true, width: '4rem', render: (b) => b.documents.length },
    {
      key: 'status',
      header: 'Status',
      width: 'minmax(0, 1.4fr)',
      render: (b) => (
        <div className="wl-confirmations__status-cell">
          {docStatusCounts(b).map(([status, n]) => (
            <Badge key={status} variant={DOC_STATUS_VARIANT[status]}>{status} {n}</Badge>
          ))}
        </div>
      ),
    },
  ], []);

  return (
    <PageScaffold title="Confirmations" chips={[`${batches.length} batches`]} feedback={error ?? feedback}>
      <div className="wl-confirmations">
        <div className="wl-confirmations__left">
          <div className="wl-confirmations__upload">
            <label className="wl-confirmations__dropzone" htmlFor="confirmations-file-input">
              <input
                id="confirmations-file-input"
                data-testid="confirmations-file-input"
                className="wl-confirmations__file-input"
                type="file"
                multiple
                accept=".pdf,.docx"
                onChange={(e) => onFilesSelected(e.target.files)}
              />
              <span className="wl-confirmations__dropzone-text">
                {selectedFileNames.length > 0
                  ? `${selectedFileNames.length} file(s) selected`
                  : 'Choose confirmation files (PDF, DOCX) or drop them here'}
              </span>
            </label>
            {selectedFileNames.length > 0 && (
              <ul className="wl-confirmations__file-list">
                {selectedFileNames.map((name) => <li key={name}>{name}</li>)}
              </ul>
            )}
            <label className="wl-field">
              <span className="wl-field__label">Default portfolio</span>
              <Select
                value={uploadPortfolioId == null ? '' : String(uploadPortfolioId)}
                onChange={(v) => onUploadPortfolioChange(v === '' ? null : Number(v))}
                options={portfolioOptions(portfolios, 'No default')}
              />
            </label>
            {portfoliosError && <span className="wl-confirmations__error">{portfoliosError}</span>}
            <Button
              variant="primary"
              disabled={uploading || selectedFileNames.length === 0}
              onClick={onUpload}
            >
              {uploading ? 'Uploading…' : 'Upload'}
            </Button>
          </div>

          {loading ? (
            <Empty message="Loading batches…" variant="loading" />
          ) : batches.length === 0 ? (
            <Empty message="No confirmation batches yet" hint="Upload a trade confirmation to get started." />
          ) : (
            <Table
              columns={columns}
              rows={batches}
              rowKey={(b) => b.id}
              selectedKey={selectedBatch?.id ?? null}
              onRowClick={(b) => onSelectBatch(b.id)}
            />
          )}
          <Button variant="default" onClick={onRefresh}>Refresh</Button>
        </div>

        <div className="wl-confirmations__right">
          {!selectedBatch ? (
            <Empty message="Select a batch to review its documents" />
          ) : selectedBatch.documents.length === 0 ? (
            <Empty message="This batch has no documents" />
          ) : (
            selectedBatch.documents.map((document) => (
              <DocumentCard
                key={document.id}
                document={document}
                batchDefaultPortfolioId={selectedBatch.default_portfolio_id}
                portfolios={portfolios}
                drafts={drafts}
                tradePortfolio={tradePortfolio}
                rejectReason={rejectReason}
                rowBusy={rowBusy}
                onDraftChange={onDraftChange}
                onSaveTrade={onSaveTrade}
                onTradePortfolioChange={onTradePortfolioChange}
                onRejectReasonChange={onRejectReasonChange}
                onBookTrade={onBookTrade}
                onRejectTrade={onRejectTrade}
              />
            ))
          )}
        </div>
      </div>
    </PageScaffold>
  );
}

function DocumentCard({
  document, batchDefaultPortfolioId, portfolios, drafts, tradePortfolio, rejectReason, rowBusy,
  onDraftChange, onSaveTrade, onTradePortfolioChange, onRejectReasonChange, onBookTrade, onRejectTrade,
}: {
  document: ConfirmationDocument;
  batchDefaultPortfolioId: number | null;
  portfolios: Array<{ id: number; name: string }>;
  drafts: Record<number, TradeDraft>;
  tradePortfolio: Record<number, number | null>;
  rejectReason: Record<number, string>;
  rowBusy: Set<number>;
  onDraftChange: (tradeId: number, next: TradeDraft) => void;
  onSaveTrade: (tradeId: number) => void;
  onTradePortfolioChange: (tradeId: number, id: number | null) => void;
  onRejectReasonChange: (tradeId: number, value: string) => void;
  onBookTrade: (tradeId: number) => void;
  onRejectTrade: (tradeId: number) => void;
}) {
  return (
    <div className="wl-confirmations__document" data-testid={`confirmation-document-${document.id}`}>
      <div className="wl-confirmations__document-head">
        <span className="wl-confirmations__document-name">{document.filename}</span>
        <Badge variant={DOC_STATUS_VARIANT[document.status]}>{document.status}</Badge>
        {document.extract_mode && <Chip>{document.extract_mode}</Chip>}
      </div>
      {document.status === 'failed' && document.error && (
        <p className="wl-confirmations__error" role="alert">{document.error}</p>
      )}
      {document.trades.map((trade) => (
        <TradeCard
          key={trade.id}
          trade={trade}
          batchDefaultPortfolioId={batchDefaultPortfolioId}
          portfolios={portfolios}
          draft={drafts[trade.id]}
          tradePortfolioId={tradePortfolio[trade.id] ?? null}
          rejectReasonValue={rejectReason[trade.id] ?? ''}
          busy={rowBusy.has(trade.id)}
          onDraftChange={onDraftChange}
          onSaveTrade={onSaveTrade}
          onTradePortfolioChange={onTradePortfolioChange}
          onRejectReasonChange={onRejectReasonChange}
          onBookTrade={onBookTrade}
          onRejectTrade={onRejectTrade}
        />
      ))}
    </div>
  );
}

function TradeCard({
  trade, batchDefaultPortfolioId, portfolios, draft, tradePortfolioId, rejectReasonValue, busy,
  onDraftChange, onSaveTrade, onTradePortfolioChange, onRejectReasonChange, onBookTrade, onRejectTrade,
}: {
  trade: ExtractedTrade;
  batchDefaultPortfolioId: number | null;
  portfolios: Array<{ id: number; name: string }>;
  draft: TradeDraft | undefined;
  tradePortfolioId: number | null;
  rejectReasonValue: string;
  busy: boolean;
  onDraftChange: (tradeId: number, next: TradeDraft) => void;
  onSaveTrade: (tradeId: number) => void;
  onTradePortfolioChange: (tradeId: number, id: number | null) => void;
  onRejectReasonChange: (tradeId: number, value: string) => void;
  onBookTrade: (tradeId: number) => void;
  onRejectTrade: (tradeId: number) => void;
}) {
  const d = draft ?? tradeDraftDefaults(trade);
  const resolvedPortfolioId = tradePortfolioId ?? batchDefaultPortfolioId ?? null;
  const editable = trade.status === 'extracted';
  const canBook = editable && trade.validation_status === 'valid' && resolvedPortfolioId != null;

  return (
    <div className="wl-confirmations__trade" data-testid={`confirmation-trade-${trade.id}`}>
      <div className="wl-confirmations__trade-head">
        <span className="wl-confirmations__trade-family">{trade.family}</span>
        <Badge variant={VALIDATION_VARIANT[trade.validation_status]}>{trade.validation_status}</Badge>
        {trade.status === 'booked' && <Badge variant="pos" solid>booked</Badge>}
        {trade.status === 'rejected' && <Badge variant="ink">rejected</Badge>}
      </div>

      {trade.status === 'booked' && trade.booked_position_id != null && (
        <p className="wl-confirmations__booked-link">
          Booked as{' '}
          <a className="wl-confirmations__link" href={routeUrl('positions', null)}>
            #{trade.booked_position_id}
          </a>
        </p>
      )}
      {trade.status === 'rejected' && (
        <p className="wl-confirmations__reject-note">
          Rejected{trade.reject_reason ? `: ${trade.reject_reason}` : ''}
        </p>
      )}

      {editable && (
        <>
          <div className="wl-confirmations__trade-fields">
            <Input
              label="Underlying"
              value={d.underlying}
              onChange={(e) => onDraftChange(trade.id, { ...d, underlying: e.target.value, fieldsError: null })}
            />
            <Input
              label="Quantity"
              type="text"
              inputMode="decimal"
              value={d.quantity}
              onChange={(e) => onDraftChange(trade.id, { ...d, quantity: e.target.value, fieldsError: null })}
            />
            <Input
              label="Entry price"
              type="text"
              inputMode="decimal"
              value={d.entry_price}
              onChange={(e) => onDraftChange(trade.id, { ...d, entry_price: e.target.value, fieldsError: null })}
            />
            <Input
              label="Currency"
              value={d.currency}
              onChange={(e) => onDraftChange(trade.id, { ...d, currency: e.target.value, fieldsError: null })}
            />
          </div>
          <label className="wl-field">
            <span className="wl-field__label">Terms (JSON)</span>
            <textarea
              className="wl-confirmations__terms-input"
              data-testid={`confirmation-terms-${trade.id}`}
              rows={6}
              value={d.termsText}
              onChange={(e) => onDraftChange(trade.id, { ...d, termsText: e.target.value, termsError: null })}
            />
          </label>
          {d.termsError && <span className="wl-confirmations__error">{d.termsError}</span>}
          {d.fieldsError && <span className="wl-confirmations__error">{d.fieldsError}</span>}
          <Button
            variant="default"
            disabled={busy}
            aria-label={`Save trade ${trade.id}`}
            onClick={() => onSaveTrade(trade.id)}
          >
            Save
          </Button>
        </>
      )}

      {Object.keys(trade.evidence ?? {}).length > 0 && (
        <ul className="wl-confirmations__evidence">
          {Object.entries(trade.evidence).map(([field, ev]) => (
            <li key={field}>{field} — &quot;{ev.quote}&quot; (p.{ev.page})</li>
          ))}
        </ul>
      )}

      {trade.validation_errors.length > 0 && (
        <ul className="wl-confirmations__validation-errors" role="alert">
          {trade.validation_errors.map((err, i) => <li key={i}>{String(err)}</li>)}
        </ul>
      )}

      {editable && (
        <div className="wl-confirmations__trade-actions">
          <label className="wl-field wl-confirmations__trade-portfolio">
            <span className="wl-field__label">Book to portfolio</span>
            <Select
              value={resolvedPortfolioId == null ? '' : String(resolvedPortfolioId)}
              onChange={(v) => onTradePortfolioChange(trade.id, v === '' ? null : Number(v))}
              options={portfolioOptions(portfolios, 'Select portfolio…')}
            />
          </label>
          <Button
            variant="primary"
            disabled={!canBook || busy}
            aria-label={`Book trade ${trade.id}`}
            onClick={() => onBookTrade(trade.id)}
          >
            Book
          </Button>
          <Input
            label="Reject reason (optional)"
            value={rejectReasonValue}
            onChange={(e) => onRejectReasonChange(trade.id, e.target.value)}
          />
          <Button
            variant="danger"
            disabled={busy}
            aria-label={`Reject trade ${trade.id}`}
            onClick={() => onRejectTrade(trade.id)}
          >
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}

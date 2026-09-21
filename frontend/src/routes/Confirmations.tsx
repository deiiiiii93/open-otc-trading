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
import { Skeleton } from '../components/Skeleton';
import { routeUrl } from '../lib/routing';
import './Confirmations.css';

/**
 * Document statuses that mean the server is still working on this file.
 *
 * Single source of truth: `Confirmations.live.tsx` imports this to decide
 * whether to keep polling, so the poll loop and the on-screen hint can never
 * disagree about what "still parsing" means.
 */
export const IN_FLIGHT_DOC_STATUSES: ReadonlySet<ConfirmationDocument['status']> =
  new Set<ConfirmationDocument['status']>(['pending', 'parsing']);

export type ParseProgress = {
  /** Documents the server has finished with — either outcome. */
  done: number;
  parsed: number;
  failed: number;
  /** Accepted but not yet started. Documents are parsed one at a time. */
  queued: number;
  /** The document being worked on right now, if any. */
  active: ConfirmationDocument | null;
  total: number;
  /** True while any document is still `pending` or `parsing`. */
  inFlight: boolean;
};

/** Fold a batch's documents into the counts the parsing hint is built from. */
export function describeParseProgress(batch: ConfirmationBatch): ParseProgress {
  const docs = batch.documents;
  const parsed = docs.filter((d) => d.status === 'parsed').length;
  const failed = docs.filter((d) => d.status === 'failed').length;
  return {
    done: parsed + failed,
    parsed,
    failed,
    queued: docs.filter((d) => d.status === 'pending').length,
    active: docs.find((d) => d.status === 'parsing') ?? null,
    total: docs.length,
    inFlight: docs.some((d) => IN_FLIGHT_DOC_STATUSES.has(d.status)),
  };
}

/**
 * Turns {@link ParseProgress} into the one line shown in the page header chip
 * and (while work is in flight) the banner above the document cards. Returns
 * `null` to show no line at all.
 *
 * Three deliberate choices:
 *
 * - **Failures are counted separately, never folded into "done".** `done` is
 *   `parsed + failed`, so a bare "3 of 5" can quietly mean two blew up. The
 *   failure count therefore rides along as its own clause whenever it is
 *   non-zero.
 * - **A clean finish vanishes; a failure does not.** Once every document is
 *   terminal there is no progress left to report, so the happy path returns
 *   `null` rather than parking a permanent chip. But a batch that lost a
 *   document keeps saying so, so a desk that looked away at the wrong moment
 *   still finds out.
 * - **"Parsing N of M" is only claimed when a document is genuinely being
 *   worked on.** Documents are parsed sequentially server-side, so between
 *   `dispatch_parse` and the first `status="parsing"` transition nothing is
 *   happening yet — that window reports as queued, not as parsing.
 */
export function parseProgressLabel(p: ParseProgress): string | null {
  if (p.total === 0) return null;
  const failures = p.failed > 0 ? ` · ${p.failed} failed` : '';
  if (!p.inFlight) {
    return p.failed > 0 ? `${p.parsed} parsed${failures}` : null;
  }
  if (p.active == null) {
    return `Queued — ${p.total} document${p.total === 1 ? '' : 's'}${failures}`;
  }
  return `Parsing ${p.done + 1} of ${p.total}…${failures}`;
}

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

/** null = never checked (feature off / arena / older row): render nothing. */
function familyCheckBadge(check: ExtractedTrade['family_check']) {
  if (!check) return null;
  if (check.status === 'agree') return <Badge variant="pos">family ✓</Badge>;
  if (check.status === 'disagree') {
    const conf = check.confidence != null ? ` at ${check.confidence.toFixed(2)}` : '';
    return (
      <span title={`System One reads ${check.jev_family}${conf}. Review before booking.`}>
        <Badge variant="warn">family? {check.jev_family}</Badge>
      </span>
    );
  }
  return (
    <span title={`family check: ${check.reason ?? 'unscored'}`}>
      <Badge variant="ink">family unchecked</Badge>
    </span>
  );
}

function parseTime(iso: string): Date {
  return new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function formatTime(iso: string): string {
  const d = parseTime(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Rail-width timestamp — the full one rides along in the cell's `title`. */
function formatTimeCompact(iso: string): string {
  const d = parseTime(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export type BatchStatusMark = { text: string; variant: BadgeVariant; live: boolean };

/**
 * The single headline badge shown per batch row.
 *
 * The batch list lives in a ~280–380px rail, which cannot fit one badge per
 * document status — the old five-column layout summed 25rem of *fixed* grid
 * track, so Docs and Status were pushed off the panel edge and never seen. The
 * row therefore reports the batch's headline state and leaves the per-status
 * breakdown to the review pane, which has the room for it.
 *
 * Precedence deliberately mirrors {@link parseProgressLabel}: work still in
 * flight outranks everything, then failures, then a clean finish. A batch that
 * lost a document keeps saying so from the rail, not just from the open pane.
 */
export function batchStatusMark(p: ParseProgress): BatchStatusMark {
  if (p.total === 0) return { text: 'empty', variant: 'ink', live: false };
  if (p.inFlight) return { text: p.active ? 'parsing' : 'queued', variant: 'warn', live: true };
  if (p.failed > 0) {
    return { text: p.parsed > 0 ? `${p.failed} failed` : 'failed', variant: 'neg', live: false };
  }
  return { text: 'parsed', variant: 'pos', live: false };
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

  // Fixed track total must stay well under the rail's 280px floor — see
  // batchStatusMark. Everything else is `fr` so the row survives compact
  // density and a narrow window.
  const columns = useMemo<Column<ConfirmationBatch>[]>(() => [
    { key: 'id', header: 'ID', width: '2.75rem', render: (b) => `#${b.id}` },
    {
      key: 'created',
      header: 'Created',
      width: 'minmax(0, 1fr)',
      render: (b) => (
        <span title={`${formatTime(b.created_at)} · source: ${b.source}`}>
          {formatTimeCompact(b.created_at)}
        </span>
      ),
    },
    { key: 'docs', header: 'Docs', numeric: true, width: '2.5rem', render: (b) => b.documents.length },
    {
      key: 'status',
      header: 'Status',
      width: 'minmax(0, 1.1fr)',
      render: (b) => {
        const mark = batchStatusMark(describeParseProgress(b));
        return (
          <div className="wl-confirmations__status-cell">
            <Badge variant={mark.variant}>
              {mark.live && <PulseDot />}
              {mark.text}
            </Badge>
          </div>
        );
      },
    },
  ], []);

  const progress = selectedBatch ? describeParseProgress(selectedBatch) : null;
  const progressLabel = progress ? parseProgressLabel(progress) : null;
  const chips = [`${batches.length} batches`, ...(progressLabel ? [progressLabel] : [])];

  return (
    <PageScaffold title="Confirmations" chips={chips} feedback={error ?? feedback}>
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
            <>
            {progress?.inFlight && (
              <div
                className="wl-confirmations__parsing-banner"
                data-testid="confirmations-parsing-banner"
                role="status"
                aria-live="polite"
              >
                <div className="wl-confirmations__parsing-head">
                  <PulseDot />
                  <span className="wl-confirmations__parsing-label">
                    {progressLabel ?? 'Parsing…'}
                  </span>
                  {progress.active && (
                    <span className="wl-confirmations__parsing-file">{progress.active.filename}</span>
                  )}
                </div>
                <div
                  className="wl-confirmations__progress-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={progress.total}
                  aria-valuenow={progress.done}
                  aria-label="Documents parsed"
                >
                  <div
                    className="wl-confirmations__progress-fill"
                    style={{ width: `${(progress.done / Math.max(progress.total, 1)) * 100}%` }}
                  />
                </div>
              </div>
            )}
            {selectedBatch.documents.map((document) => (
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
            ))}
            </>
          )}
        </div>
      </div>
    </PageScaffold>
  );
}

/** Small animated marker meaning "the server is working on this right now". */
function PulseDot() {
  return <span className="wl-confirmations__pulse-dot" aria-hidden="true" />;
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
  const inFlight = IN_FLIGHT_DOC_STATUSES.has(document.status);

  return (
    <div
      className="wl-confirmations__document"
      data-testid={`confirmation-document-${document.id}`}
      aria-busy={inFlight || undefined}
    >
      <div className="wl-confirmations__document-head">
        <span className="wl-confirmations__document-name">{document.filename}</span>
        <Badge variant={DOC_STATUS_VARIANT[document.status]}>
          {inFlight && <PulseDot />}
          {document.status}
        </Badge>
        {document.extract_mode && <Chip>{document.extract_mode}</Chip>}
      </div>
      {inFlight && (
        <div className="wl-confirmations__doc-parsing" role="status" aria-live="polite">
          <span className="wl-confirmations__doc-parsing-text">
            {document.status === 'parsing'
              ? 'Reading the document and extracting trades…'
              : 'Queued — documents are parsed one at a time.'}
          </span>
          {document.status === 'parsing' && (
            <div className="wl-confirmations__doc-skeletons" aria-hidden="true">
              <Skeleton height={12} width="40%" />
              <Skeleton height={12} width="85%" />
              <Skeleton height={12} width="65%" />
            </div>
          )}
        </div>
      )}
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
        {familyCheckBadge(trade.family_check)}
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

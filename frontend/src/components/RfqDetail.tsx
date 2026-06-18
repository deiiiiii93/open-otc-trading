import type { Portfolio, RFQ } from '../types';
import { Button } from './Button';
import { Select } from './Select';
import { Badge, type BadgeVariant } from './Badge';
import { RfqQuoteForm, type RfqQuoteOverrides } from './RfqQuoteForm';
import './RfqDetail.css';

type Props = {
  rfq: RFQ;
  portfolios?: Portfolio[];
  selectedPortfolioId?: number | null;
  onPortfolioChange?: (id: number) => void;
  onQuote?: (id: number, overrides: RfqQuoteOverrides) => void;
  onApprove?: (id: number) => void;
  onRelease?: (id: number) => void;
  onAccept?: (id: number) => void;
  onBook?: (id: number) => void;
  onRejectClick?: (id: number) => void;
};

const statusVariant: Record<string, BadgeVariant> = {
  pending_approval: 'warn',
  submitted: 'warn',
  pricing_failed: 'neg',
  approved: 'pos',
  released: 'pos',
  client_accepted: 'pos',
  booked: 'pos',
  rejected: 'neg',
  expired: 'neg',
  cancelled: 'neg',
  draft: 'ink',
};

function payloadError(rfq: RFQ): string | null {
  const direct = rfq.quote_payload.quantark_error ?? rfq.quote_payload.error;
  if (direct) return String(direct);
  const validation = rfq.quote_payload.validation as { missing_fields?: string[]; errors?: string[] } | undefined;
  const missing = validation?.missing_fields ?? [];
  const errors = validation?.errors ?? [];
  const parts = [...missing, ...errors].filter(Boolean);
  return parts.length > 0 ? parts.join(', ') : null;
}

function latestQuoteVersion(rfq: RFQ) {
  return [...(rfq.quote_versions ?? [])].sort((a, b) => b.version - a.version)[0] ?? null;
}

function jsonBlock(value: unknown): string {
  if (value == null) return '{}';
  return JSON.stringify(value, null, 2);
}

function readCurrency(rfq: RFQ): string {
  const request = rfq.request_payload ?? {};
  const market = request.market;
  const product = request.product;
  const productKwargs = request.product_kwargs;
  const value =
    (market && typeof market === 'object' && !Array.isArray(market) ? (market as Record<string, unknown>).currency : null) ??
    (product && typeof product === 'object' && !Array.isArray(product) ? (product as Record<string, unknown>).currency : null) ??
    (productKwargs && typeof productKwargs === 'object' && !Array.isArray(productKwargs) ? (productKwargs as Record<string, unknown>).currency : null) ??
    rfq.quote_payload.quote_amount_currency;
  return value == null || String(value).trim() === '' ? 'N/A' : String(value).toUpperCase();
}

export function RfqDetail({
  rfq,
  portfolios = [],
  selectedPortfolioId = null,
  onPortfolioChange,
  onQuote = () => {},
  onApprove = () => {},
  onRelease = () => {},
  onAccept = () => {},
  onBook = () => {},
  onRejectClick = () => {},
}: Props) {
  const isPending = rfq.status === 'pending_approval';
  const quoteVersions = rfq.quote_versions ?? [];
  const latest = latestQuoteVersion(rfq);
  const requestedTerms = latest?.request_payload?.terms ?? rfq.request_payload;
  const executableTerms = latest?.request_payload?.executable_terms ?? null;
  const error = payloadError(rfq);
  const canRelease = rfq.status === 'approved';
  const canAccept = rfq.status === 'released';
  const canBook = rfq.status === 'client_accepted';
  const currency = readCurrency(rfq);

  return (
    <div className="wl-rfq-detail">
      <header className="wl-rfq-detail__head">
        <div>
          <div className="wl-rfq-detail__id">RFQ #{rfq.id}</div>
          <div className="wl-rfq-detail__client">{rfq.client_name}</div>
        </div>
        <Badge variant={statusVariant[rfq.status] ?? 'ink'}>{rfq.status}</Badge>
      </header>

      <dl className="wl-rfq-detail__facts">
        <div>
          <dt>Underlying</dt>
          <dd>{String(rfq.request_payload?.underlying ?? 'N/A')}</dd>
        </div>
        <div>
          <dt>Currency</dt>
          <dd>{currency}</dd>
        </div>
        <div>
          <dt>Notional</dt>
          <dd>{String(rfq.request_payload?.quantity ?? 'N/A')}</dd>
        </div>
      </dl>

      <RfqQuoteForm rfq={rfq} onQuote={onQuote} />

      {error && (
        <div className="wl-rfq-detail__error">
          <div className="wl-rfq-detail__response-label">Pricing or validation error</div>
          <p>{error}</p>
        </div>
      )}

      {rfq.approved_response && (
        <div className="wl-rfq-detail__response">
          <div className="wl-rfq-detail__response-label">Released response</div>
          <p>{rfq.approved_response}</p>
        </div>
      )}

      <div className="wl-rfq-detail__terms">
        <section>
          <div className="wl-rfq-detail__response-label">Requested terms</div>
          <pre>{jsonBlock(requestedTerms)}</pre>
        </section>
        <section>
          <div className="wl-rfq-detail__response-label">Executable quoted terms</div>
          <pre>{executableTerms ? jsonBlock(executableTerms) : 'No executable quote terms yet.'}</pre>
        </section>
      </div>

      <div className="wl-rfq-detail__versions">
        <div className="wl-rfq-detail__response-label">Quote versions</div>
        {quoteVersions.length === 0 ? (
          <p>No quote versions yet.</p>
        ) : (
          <ol>
            {quoteVersions.map((version) => (
              <li key={version.id}>
                <span>v{version.version}</span>
                <Badge variant={statusVariant[version.status] ?? 'ink'}>{version.status}</Badge>
                <span>{version.quote_mode}</span>
                {version.error && <span>{version.error}</span>}
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="wl-rfq-detail__actions">
        {isPending && (
          <>
            <Button variant="primary" onClick={() => onApprove(rfq.id)}>Approve &amp; Send</Button>
            <Button variant="danger" onClick={() => onRejectClick(rfq.id)}>Reject...</Button>
          </>
        )}
        {canRelease && <Button variant="primary" onClick={() => onRelease(rfq.id)}>Release</Button>}
        {canAccept && <Button variant="primary" onClick={() => onAccept(rfq.id)}>Mark Accepted</Button>}
        {canBook && (
          <>
            <Select
              label="Portfolio"
              value={selectedPortfolioId != null ? String(selectedPortfolioId) : ''}
              onChange={(v) => onPortfolioChange?.(Number(v))}
              placeholder="Select portfolio"
              options={portfolios.map((portfolio) => ({ value: String(portfolio.id), label: portfolio.name }))}
            />
            <Button variant="primary" onClick={() => onBook(rfq.id)} disabled={!selectedPortfolioId}>
              Book
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

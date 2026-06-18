import { CopyPlus } from 'lucide-react';
import { Badge } from './Badge';
import { Button } from './Button';
import { rfqStatusBadge, formatRfqStatus } from '../lib/rfqStatus';
import type { RFQ } from '../types';
import './RfqHistoryPanel.css';

type Props = {
  rfqs: RFQ[];
  selectedRfqId: number | null;
  productLabelFor: (productType: string) => string;
  onSelect?: (rfqId: number) => void;
  onClone?: (rfq: RFQ) => void;
};

export function RfqHistoryPanel({ rfqs, selectedRfqId, productLabelFor, onSelect, onClone }: Props) {
  if (!rfqs.length) {
    return <div className="wl-rfq-history__empty">No RFQs submitted yet.</div>;
  }
  return (
    <div className="wl-rfq-history" role="list" aria-label="My RFQs">
      {rfqs.map((rfq) => {
        const selected = rfq.id === selectedRfqId;
        const productType = String(rfq.request_payload?.product_type ?? '');
        return (
          <div
            key={rfq.id}
            className={selected ? 'wl-rfq-history__row wl-rfq-history__row--active' : 'wl-rfq-history__row'}
          >
            <button
              type="button"
              className="wl-rfq-history__select"
              aria-pressed={selected}
              aria-label={`#${rfq.id} ${productLabelFor(productType)} ${formatRfqStatus(rfq.status)}`}
              onClick={() => onSelect?.(rfq.id)}
            >
              <span className="wl-rfq-history__main">
                <span className="wl-rfq-history__id">#{rfq.id}</span>
                <span className="wl-rfq-history__product">{productLabelFor(productType)}</span>
              </span>
              <Badge variant={rfqStatusBadge(rfq.status)}>{formatRfqStatus(rfq.status)}</Badge>
              <span className="wl-rfq-history__meta">
                {formatCreatedAt(rfq.created_at)} · {rfq.channel}
              </span>
            </button>
            {selected && onClone ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => onClone(rfq)}
                aria-label={`Clone RFQ ${rfq.id} into editor`}
              >
                <CopyPlus size={14} aria-hidden="true" />
                Clone
              </Button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function formatCreatedAt(value: string | undefined): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return (
    parsed.toLocaleDateString(undefined, { month: '2-digit', day: '2-digit' }) +
    ' ' +
    parsed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
  );
}

import type { RFQ } from '../types';
import { Badge, type BadgeVariant } from './Badge';
import { Empty } from './Empty';
import './RfqInbox.css';

type Props = {
  rfqs: RFQ[];
  selectedId: number | null;
  onSelect: (id: number) => void;
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

export function RfqInbox({ rfqs, selectedId, onSelect }: Props) {
  if (rfqs.length === 0) {
    return <Empty message="No RFQs in inbox" symbol="∅" />;
  }
  return (
    <div className="wl-rfq-inbox">
      {rfqs.map((rfq) => {
        const isSelected = rfq.id === selectedId;
        return (
          <button
            key={rfq.id}
            type="button"
            className={`wl-rfq-inbox__row ${isSelected ? 'wl-rfq-inbox__row--selected' : ''}`.trim()}
            onClick={() => onSelect(rfq.id)}
          >
            <div className="wl-rfq-inbox__id">#{rfq.id}</div>
            <div className="wl-rfq-inbox__client">{rfq.client_name}</div>
            <Badge variant={statusVariant[rfq.status] ?? 'ink'}>{rfq.status}</Badge>
          </button>
        );
      })}
    </div>
  );
}

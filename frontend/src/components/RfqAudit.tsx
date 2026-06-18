import type { AuditEvent, RFQ } from '../types';
import './RfqAudit.css';

type Props = {
  rfq: RFQ;
  events?: AuditEvent[];
};

type Event = { label: string; detail?: string };

function deriveEvents(rfq: RFQ): Event[] {
  const events: Event[] = [{ label: 'Created' }];
  if (rfq.status === 'pending_approval') {
    events.push({ label: 'Pending desk approval' });
  }
  if (rfq.status === 'approved') {
    events.push({ label: 'Approved' });
    if (rfq.approved_response) {
      events.push({ label: 'Released to client' });
    }
  }
  if (rfq.status === 'released') events.push({ label: 'Released to client' });
  if (rfq.status === 'client_accepted') events.push({ label: 'Client accepted' });
  if (rfq.status === 'booked') events.push({ label: 'Booked to portfolio' });
  if (rfq.status === 'rejected') {
    events.push({ label: 'Rejected' });
  }
  return events;
}

function eventLabel(event: AuditEvent): string {
  return event.event_type.replace(/^rfq\./, '').replaceAll('_', ' ');
}

export function RfqAudit({ rfq, events: auditEvents }: Props) {
  const events = auditEvents?.length
    ? auditEvents.map((event) => ({
        label: eventLabel(event),
        detail: `${event.actor} · ${new Date(event.created_at).toLocaleString()}`,
      }))
    : deriveEvents(rfq);
  return (
    <div className="wl-rfq-audit">
      <ol className="wl-rfq-audit__list">
        {events.map((event, index) => (
          <li key={index} className="wl-rfq-audit__row">
            <span className="wl-rfq-audit__bullet" aria-hidden />
            <span className="wl-rfq-audit__label">{event.label}</span>
            {event.detail && <span className="wl-rfq-audit__detail">{event.detail}</span>}
          </li>
        ))}
      </ol>
      {!auditEvents?.length && (
        <p className="wl-rfq-audit__note">Derived from RFQ fields when audit-event rows are unavailable.</p>
      )}
    </div>
  );
}

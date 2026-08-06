import { AlertTriangle, CheckCircle2, FileText, XCircle } from 'lucide-react';
import type { BookingResultMeta } from '../types';
import './BookingResultCard.css';

type Props = {
  booking: BookingResultMeta;
};

const STATUS_LABEL: Record<BookingResultMeta['status'], string> = {
  booked: 'Position booked',
  already_booked: 'Already booked',
  failed: 'Booking refused',
};

const STATUS_ICON: Record<BookingResultMeta['status'], typeof CheckCircle2> = {
  booked: CheckCircle2,
  already_booked: AlertTriangle,
  failed: XCircle,
};

/** Terms whose label reads better than the raw snake_case key. Anything not
 * listed falls back to sentence-casing the key, so a new product family's terms
 * still render sensibly without a code change. */
const TERM_LABEL: Record<string, string> = {
  option_type: 'Option type',
  barrier_type: 'Barrier type',
  initial_price: 'Initial price',
  exercise_date: 'Exercise date',
  maturity_years: 'Maturity (y)',
  maturity_date: 'Maturity',
  contract_multiplier: 'Multiplier',
  settlement_date: 'Settlement',
};

function termLabel(key: string): string {
  if (TERM_LABEL[key]) return TERM_LABEL[key];
  const spaced = key.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Render values verbatim. Deliberately NO locale/rounding pass: these are the
 * economics that were actually persisted, and a card that quietly rounds a
 * strike is worse than one that looks unpolished. */
function termValue(value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

function humanizeError(error: string): string {
  return error.replace(/_/g, ' ');
}

function detailLines(detail: unknown): string[] {
  if (typeof detail === 'string') return detail.trim() ? [detail] : [];
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === 'string' ? d : JSON.stringify(d)));
  }
  if (detail && typeof detail === 'object') return [JSON.stringify(detail)];
  return [];
}

/**
 * Terminal result of a booking write, rendered from the server's structured
 * record rather than the assistant's prose.
 *
 * The prose path is not reliable for this: a booking turn runs many tools, and
 * `ChatBubble`'s reasoning-fold heuristic folds long tool-heavy replies into a
 * collapsed "Reasoning" block — which once hid a completed booking entirely.
 * An irreversible write must not be visible only by chance.
 */
export function BookingResultCard({ booking }: Props) {
  const Icon = STATUS_ICON[booking.status] ?? CheckCircle2;
  const terms = Object.entries(booking.terms ?? {});
  const details = detailLines(booking.detail);
  const size = [
    booking.quantity != null ? String(booking.quantity) : null,
    booking.entry_price != null ? `@ ${booking.entry_price}` : null,
    booking.currency,
  ].filter(Boolean);

  return (
    <section
      className={`wl-bookingcard wl-bookingcard--${booking.status}`}
      data-testid="booking-result-card"
      aria-label={STATUS_LABEL[booking.status]}
    >
      <header className="wl-bookingcard__head">
        <span className="wl-bookingcard__status">
          <Icon size={14} aria-hidden="true" />
          {STATUS_LABEL[booking.status]}
        </span>
        {booking.position_id != null && (
          <span className="wl-bookingcard__position">#{booking.position_id}</span>
        )}
      </header>

      <div className="wl-bookingcard__identity">
        {booking.underlying && (
          <span className="wl-bookingcard__underlying">{booking.underlying}</span>
        )}
        {booking.family && (
          <span className="wl-bookingcard__family">{booking.family}</span>
        )}
      </div>

      {(size.length > 0 || booking.portfolio) && (
        <div className="wl-bookingcard__size">
          {size.map((part) => (
            <span key={part} className="wl-bookingcard__size-part">{part}</span>
          ))}
          {booking.portfolio && (
            <span className="wl-bookingcard__portfolio">
              into {booking.portfolio.name ?? 'portfolio'} (id {booking.portfolio.id})
            </span>
          )}
        </div>
      )}

      {terms.length > 0 && (
        <dl className="wl-bookingcard__terms" data-testid="booking-result-terms">
          {terms.map(([key, value]) => (
            <div key={key} className="wl-bookingcard__term">
              <dt className="wl-bookingcard__term-label">{termLabel(key)}</dt>
              <dd className="wl-bookingcard__term-value">{termValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}

      {booking.error && (
        <div className="wl-bookingcard__error" role="status">
          <span className="wl-bookingcard__error-code">{humanizeError(booking.error)}</span>
          {details.length > 0 && (
            <ul className="wl-bookingcard__error-detail">
              {details.map((line, idx) => (
                <li key={`${line}-${idx}`}>{line}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {(booking.source_document || booking.external_trade_id || booking.counterparty) && (
        <footer className="wl-bookingcard__provenance">
          {booking.source_document && (
            <span className="wl-bookingcard__source">
              <FileText size={12} aria-hidden="true" />
              {booking.source_document}
            </span>
          )}
          {booking.counterparty && (
            <span className="wl-bookingcard__cpty">{booking.counterparty}</span>
          )}
          {booking.external_trade_id && (
            <span className="wl-bookingcard__ref">
              Ref {booking.external_trade_id}
              {booking.trade_date ? ` · ${booking.trade_date}` : ''}
            </span>
          )}
        </footer>
      )}
    </section>
  );
}

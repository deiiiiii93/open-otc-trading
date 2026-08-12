import { useMemo, useState } from 'react';
import { Check, ChevronDown, Plus, XCircle } from 'lucide-react';
import type { LifecycleFieldSpec, LifecycleVocabulary, PositionLifecycleEvent } from '../types';
import type { PositionRow } from '../routes/Positions';
import { Button } from './Button';
import { DatePicker } from './DatePicker';
import { NumberInput } from './NumberInput';

type Props = {
  row: PositionRow;
  events: PositionLifecycleEvent[];
  onAddEvent: (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => void | Promise<void>;
  onCancelEvent?: (row: PositionRow, event: PositionLifecycleEvent, reason: string | null) => void | Promise<void>;
  adding: boolean;
  /** Server-owned; null while loading. This component keeps no fallback table
   *  on purpose — the hardcoded copy it replaced had already drifted from the
   *  backend and silently lost `settle` and `fixing`. */
  vocabulary: LifecycleVocabulary | null;
};

export function PositionLifecycleTimeline({ row, events, onAddEvent, onCancelEvent, adding, vocabulary }: Props) {
  const [showForm, setShowForm] = useState(false);
  const [eventType, setEventType] = useState('');
  const [eventData, setEventData] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [eventPickerOpen, setEventPickerOpen] = useState(false);

  const availableEvents = useMemo(
    () => vocabulary?.by_family[row.product_type] ?? [],
    [vocabulary, row.product_type],
  );
  const fields: LifecycleFieldSpec[] = useMemo(
    () => vocabulary?.event_fields[eventType] ?? [],
    [vocabulary, eventType],
  );

  const updateField = (key: string, value: string) => {
    setEventData((current) => ({ ...current, [key]: value }));
  };

  const handleSubmit = async () => {
    setFormError(null);
    if (!eventType) {
      setFormError('Please select an event type');
      return;
    }

    const payload: Record<string, unknown> = {};
    for (const field of fields) {
      const value = eventData[field.key]?.trim();
      if (!value) continue;
      if (field.type === 'bool') {
        // Checkboxes are stored as 'true'/'' in the string-keyed form state;
        // the API must receive a real boolean (e.g. `exercise.early`).
        payload[field.key] = value === 'true';
      } else {
        payload[field.key] = field.type === 'number' ? Number(value) : value;
      }
    }

    await onAddEvent(row, eventType, payload);
    setShowForm(false);
    setEventPickerOpen(false);
    setEventType('');
    setEventData({});
  };

  const handleCancelEvent = async (event: PositionLifecycleEvent) => {
    if (!onCancelEvent || event.cancelled_at) return;
    const confirmed = window.confirm(`Cancel lifecycle event #${event.id} (${event.event_type})?`);
    if (!confirmed) return;
    const reason = window.prompt('Cancellation reason (optional)')?.trim() || null;
    await onCancelEvent(row, event, reason);
  };

  const hideForm = () => {
    setShowForm(false);
    setEventPickerOpen(false);
  };

  return (
    <div className="wl-positions__lifecycle">
      <div className="wl-positions__lifecycle-header">
        <h4>Lifecycle Events</h4>
        <div className="wl-positions__lifecycle-actions">
          {!showForm && (
            <Button
              type="button"
              className="wl-positions__lifecycle-toggle"
              onClick={() => setShowForm(true)}
              disabled={adding || !vocabulary}
            >
              <Plus size={14} />
              New
            </Button>
          )}
          {showForm && (
            <>
              <Button type="button" onClick={hideForm} disabled={adding}>
                Cancel
              </Button>
              <Button type="button" variant="primary" onClick={handleSubmit} disabled={adding}>
                {adding ? 'Adding...' : 'Add Event'}
              </Button>
            </>
          )}
        </div>
      </div>

      {showForm && (
        <div className="wl-positions__lifecycle-form">
          <div className="wl-positions__lifecycle-picker">
            <span className="wl-positions__lifecycle-picker-label">Event Type</span>
            <button
              type="button"
              className="wl-positions__lifecycle-picker-button"
              aria-haspopup="listbox"
              aria-expanded={eventPickerOpen}
              onClick={() => setEventPickerOpen((open) => !open)}
            >
              <span>{eventType || 'Select event type'}</span>
              <ChevronDown size={14} aria-hidden="true" />
            </button>
            {eventPickerOpen && (
              <div className="wl-positions__lifecycle-picker-drawer" role="listbox" aria-label="Event type">
                {availableEvents.map((et) => (
                  <button
                    key={et}
                    type="button"
                    role="option"
                    aria-selected={eventType === et}
                    className="wl-positions__lifecycle-picker-option"
                    onClick={() => {
                      setEventType(et);
                      setEventData({});
                      setEventPickerOpen(false);
                    }}
                  >
                    <span>{et}</span>
                    {eventType === et && <Check size={14} aria-hidden="true" />}
                  </button>
                ))}
              </div>
            )}
          </div>

          {fields.length > 0 && (
            <div className="wl-positions__lifecycle-fields">
              {fields.map((field) =>
                field.type === 'date' ? (
                  <div key={field.key} className="wl-positions__term-field">
                    <DatePicker
                      label={field.label}
                      value={eventData[field.key] ?? ''}
                      onChange={(v) => updateField(field.key, v)}
                    />
                  </div>
                ) : field.type === 'bool' ? (
                  <div key={field.key} className="wl-positions__term-field">
                    <span>{field.label}</span>
                    <div className="wl-positions__term-boolean-control">
                      <input
                        type="checkbox"
                        aria-label={field.label}
                        checked={eventData[field.key] === 'true'}
                        onChange={(e) => updateField(field.key, e.target.checked ? 'true' : '')}
                      />
                    </div>
                  </div>
                ) : (
                  <label key={field.key} className="wl-positions__term-field">
                    <span>{field.label}</span>
                    <NumberInput
                      type={field.type === 'number' ? 'number' : 'text'}
                      step={field.type === 'number' ? 'any' : undefined}
                      value={eventData[field.key] ?? ''}
                      onChange={(e) => updateField(field.key, e.target.value)}
                    />
                  </label>
                ),
              )}
            </div>
          )}

          {formError && <div className="wl-positions__ticket-error">{formError}</div>}
        </div>
      )}

      {events.length === 0 ? (
        <div className="wl-positions__lifecycle-empty">No lifecycle events recorded.</div>
      ) : (
        <ul className="wl-positions__lifecycle-list">
          {events.map((event) => (
            <li
              key={event.id}
              className={[
                'wl-positions__lifecycle-item',
                event.cancelled_at ? 'wl-positions__lifecycle-item--cancelled' : '',
              ].filter(Boolean).join(' ')}
            >
              <div className="wl-positions__lifecycle-meta">
                <span className={`wl-positions__lifecycle-badge wl-positions__lifecycle-badge--${event.event_type}`}>
                  {event.event_type}
                </span>
                {event.cancelled_at && (
                  <span className="wl-positions__lifecycle-cancelled">cancelled</span>
                )}
                {event.old_status != null && event.new_status != null && (
                  <span className="wl-positions__lifecycle-transition">
                    {event.old_status} → {event.new_status}
                  </span>
                )}
                <time>{new Date(event.created_at).toLocaleString()}</time>
                {!event.cancelled_at && onCancelEvent && (
                  <Button
                    type="button"
                    variant="ghost"
                    className="wl-positions__lifecycle-cancel"
                    onClick={() => { void handleCancelEvent(event); }}
                    disabled={adding}
                    title="Cancel lifecycle event"
                    aria-label={`Cancel lifecycle event ${event.id}`}
                  >
                    <XCircle size={14} />
                  </Button>
                )}
              </div>
              {event.cancelled_at && (
                <div className="wl-positions__lifecycle-cancel-meta">
                  Cancelled by {event.cancelled_by ?? 'unknown'} at {new Date(event.cancelled_at).toLocaleString()}
                  {event.cancellation_reason ? ` · ${event.cancellation_reason}` : ''}
                </div>
              )}
              {Object.keys(event.event_data).length > 0 && (
                <pre className="wl-positions__lifecycle-data">
                  {JSON.stringify(event.event_data, null, 2)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

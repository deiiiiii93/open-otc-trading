import { useEffect, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Modal';
import { Select } from './Select';
import { DatePicker } from './DatePicker';
import type { InstrumentCreateInput } from '../api/client';
import './InstrumentCreateDialog.css';

const KIND_OPTIONS = ['futures', 'index', 'etf', 'stock', 'listed_option', 'sge_spot'];
const STATUS_OPTIONS = ['draft', 'active', 'inactive', 'expired'];

export type InstrumentCreateFormData = InstrumentCreateInput;

type Props = {
  open: boolean;
  onCancel: () => void;
  onCreate: (data: InstrumentCreateFormData) => Promise<void>;
};

function emptyForm(): InstrumentCreateFormData {
  return {
    symbol: '',
    display_name: null,
    kind: 'index',
    exchange: null,
    currency: 'CNY',
    status: 'draft',
    akshare_symbol: null,
    akshare_asset_class: null,
    series_root: null,
    expiry: null,
    multiplier: null,
    strike: null,
    option_type: null,
    parent_id: null,
    notes: null,
    rate: null,
    dividend_yield: null,
    volatility: null,
  };
}

function parseOptionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const num = Number(trimmed);
  return Number.isNaN(num) ? null : num;
}

function parseOptionalInt(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const num = Number(trimmed);
  return Number.isNaN(num) ? null : num;
}

export function InstrumentCreateDialog({ open, onCancel, onCreate }: Props) {
  const [form, setForm] = useState<InstrumentCreateFormData>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setForm(emptyForm());
      setError(null);
      setSaving(false);
    }
  }, [open]);

  const update = <K extends keyof InstrumentCreateFormData>(field: K, value: InstrumentCreateFormData[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const canSubmit = form.symbol.trim() && form.kind && !saving;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      await onCreate({
        ...form,
        symbol: form.symbol.trim(),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={(o) => { if (!o) onCancel(); }}
      title="NEW INSTRUMENT"
      layoutKey="instrument-create"
      defaultWidth={640}
      defaultHeight={540}
    >
      <form className="wl-instrument-create" onSubmit={submit}>
        <div className="wl-instrument-create__grid">
          <label className="wl-field">
            <span className="wl-field__label">Symbol *</span>
            <input
              className="wl-input"
              value={form.symbol}
              onChange={(e) => update('symbol', e.target.value)}
              placeholder="e.g. 000300.SH"
              autoFocus
              required
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">Display name</span>
            <input
              className="wl-input"
              value={form.display_name ?? ''}
              onChange={(e) => update('display_name', e.target.value || null)}
              placeholder="e.g. CSI 300"
            />
          </label>
          <div className="wl-field">
            <Select
              label="Kind *"
              value={form.kind}
              onChange={(v) => update('kind', v)}
              options={KIND_OPTIONS.map((k) => ({ value: k, label: k }))}
            />
          </div>
          <div className="wl-field">
            <Select
              label="Status"
              value={form.status}
              onChange={(v) => update('status', v)}
              options={STATUS_OPTIONS.map((s) => ({ value: s, label: s }))}
            />
          </div>
          <label className="wl-field">
            <span className="wl-field__label">Currency</span>
            <input
              className="wl-input"
              value={form.currency}
              onChange={(e) => update('currency', e.target.value)}
              placeholder="CNY"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">Exchange</span>
            <input
              className="wl-input"
              value={form.exchange ?? ''}
              onChange={(e) => update('exchange', e.target.value || null)}
              placeholder="e.g. SH"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">Series root</span>
            <input
              className="wl-input"
              value={form.series_root ?? ''}
              onChange={(e) => update('series_root', e.target.value || null)}
              placeholder="e.g. IC"
            />
          </label>
          <div className="wl-field">
            <DatePicker
              label="Expiry"
              value={form.expiry ?? ''}
              onChange={(v) => update('expiry', v || null)}
              placeholder="YYYY-MM-DD"
            />
          </div>
          <label className="wl-field">
            <span className="wl-field__label">Multiplier</span>
            <input
              className="wl-input"
              inputMode="decimal"
              value={form.multiplier ?? ''}
              onChange={(e) => update('multiplier', parseOptionalNumber(e.target.value))}
              placeholder="e.g. 1"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">Strike</span>
            <input
              className="wl-input"
              inputMode="decimal"
              value={form.strike ?? ''}
              onChange={(e) => update('strike', parseOptionalNumber(e.target.value))}
              placeholder="For listed options"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">Option type</span>
            <input
              className="wl-input"
              value={form.option_type ?? ''}
              onChange={(e) => update('option_type', e.target.value || null)}
              placeholder="C / P"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">Parent ID</span>
            <input
              className="wl-input"
              inputMode="numeric"
              value={form.parent_id ?? ''}
              onChange={(e) => update('parent_id', parseOptionalInt(e.target.value))}
              placeholder="Existing instrument id"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">AKShare symbol</span>
            <input
              className="wl-input"
              value={form.akshare_symbol ?? ''}
              onChange={(e) => update('akshare_symbol', e.target.value || null)}
              placeholder="e.g. 000300"
            />
          </label>
          <label className="wl-field">
            <span className="wl-field__label">AKShare asset class</span>
            <input
              className="wl-input"
              value={form.akshare_asset_class ?? ''}
              onChange={(e) => update('akshare_asset_class', e.target.value || null)}
              placeholder="e.g. index"
            />
          </label>
          <label className="wl-field wl-instrument-create__full">
            <span className="wl-field__label">Notes</span>
            <textarea
              className="wl-input"
              value={form.notes ?? ''}
              onChange={(e) => update('notes', e.target.value || null)}
              rows={2}
            />
          </label>
        </div>
        {error && <div className="wl-field__hint wl-field__hint--error" role="alert">{error}</div>}
        <div className="wl-instrument-create__actions">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!canSubmit}>
            {saving ? 'Creating…' : 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

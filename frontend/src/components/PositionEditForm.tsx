import { useState, type FormEvent } from 'react';
import type { PositionRow } from '../routes/Positions';
import { Button } from './Button';
import { NumberInput } from './NumberInput';
import { Select } from './Select';
import { ProductTermsForm } from './ProductTermsForm';

const PRODUCT_TYPES = [
  'EuropeanVanillaOption',
  'AmericanOption',
  'CashOrNothingDigitalOption',
  'BarrierOption',
  'OneTouchOption',
  'DoubleOneTouchOption',
  'SingleSharkfinOption',
  'DoubleSharkfinOption',
  'SnowballOption',
  'KnockOutResetSnowballOption',
  'PhoenixOption',
  'AsianOption',
  'RangeAccrualOption',
  'Futures',
  'SpotInstrument',
];

const ENGINE_OPTIONS: Record<string, string[]> = {
  EuropeanVanillaOption: ['BlackScholesEngine', 'EuropeanMCEngine', 'EuropeanQuadEngine', 'PDEEngine'],
  BarrierOption: ['BarrierAnalyticalEngine', 'BarrierOptionMCEngine', 'BarrierQuadEngine', 'PDEEngine'],
  SnowballOption: ['SnowballQuadEngine', 'SnowballMCEngine', 'PDEEngine', 'KOResetSnowballQuadEngine'],
  PhoenixOption: ['PhoenixQuadEngine', 'PhoenixMCEngine', 'PDEEngine'],
  CashOrNothingDigitalOption: ['DigitalOptionAnalyticalEngine', 'DigitalOptionMCEngine'],
  AsianOption: ['AsianOptionAnalyticalEngine', 'AsianOptionMCEngine'],
  Futures: ['DeltaOneEngine'],
  SpotInstrument: ['DeltaOneEngine'],
};

export function inferProductFamily(productType: string, productTerms: Record<string, unknown> = {}): string {
  if (Array.isArray(productTerms.components) && productTerms.components.length > 0) {
    return 'package';
  }

  const normalized = productType.toLowerCase();
  if (normalized.includes('snowball') || normalized.includes('phoenix') || normalized.includes('autocallable')) return 'autocallable';
  if (normalized.includes('barrier')) return 'barrier';
  if (normalized.includes('touch')) return 'touch';
  if (normalized.includes('asian')) return 'asian';
  if (normalized.includes('rangeaccrual') || normalized.includes('range_accrual') || normalized.includes('range accrual')) {
    return 'range_accrual';
  }
  if (normalized.includes('sharkfin')) return 'sharkfin';
  if (['stock', 'fund', 'etf', 'spot', 'spotinstrument'].includes(normalized)) return 'spot';
  if (normalized.includes('futures') || normalized.includes('future') || normalized.includes('forward')) return 'futures';
  return 'option';
}

type Props = {
  row: PositionRow;
  onSave: (row: PositionRow, updates: Partial<PositionRow>) => void | Promise<void>;
  saving: boolean;
};

export function PositionEditForm({ row, onSave, saving }: Props) {
  const [form, setForm] = useState({
    underlying: row.underlying,
    product_type: row.product_type,
    quantity: String(row.quantity),
    entry_price: String(row.entry_price ?? 0),
    currency: row.currency,
    status: row.status,
    source_trade_id: row.trade_id ?? '',
    engine_name: row.engine_name ?? '',
    product_terms: JSON.stringify(row.product?.terms ?? row.product_kwargs ?? {}, null, 2),
  });
  const [error, setError] = useState<string | null>(null);

  const engineOptions = ENGINE_OPTIONS[form.product_type] ?? [];

  const update = (key: keyof typeof form, value: string) => {
    setForm((f) => ({ ...f, [key]: value }));
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    let productTerms: Record<string, unknown>;
    try {
      productTerms = JSON.parse(form.product_terms);
    } catch {
      setError('Product terms JSON is invalid');
      return;
    }

    const quantity = Number(form.quantity);
    const entry_price = Number(form.entry_price);
    if (!Number.isFinite(quantity)) {
      setError('Quantity must be a valid number');
      return;
    }
    if (!Number.isFinite(entry_price)) {
      setError('Entry price must be a valid number');
      return;
    }
    if (!/^[A-Z]{3}$/.test(form.currency)) {
      setError('Currency must be a 3-letter ISO code');
      return;
    }

    const productComponents = Array.isArray(productTerms.components)
      ? productTerms.components.filter((component): component is Record<string, unknown> => (
        component !== null && typeof component === 'object' && !Array.isArray(component)
      ))
      : [];

    await onSave(row, {
      underlying: form.underlying,
      product_type: form.product_type,
      quantity,
      entry_price,
      currency: form.currency,
      status: form.status,
      trade_id: form.source_trade_id || undefined,
      engine_name: form.engine_name || undefined,
      product_kwargs: productTerms,
      product: {
        asset_class: 'equity',
        product_family: inferProductFamily(form.product_type, productTerms),
        quantark_class: form.product_type,
        underlying: form.underlying,
        currency: form.currency,
        terms: productTerms,
        components: productComponents,
      } as PositionRow['product'],
    });
  };

  return (
    <form className="wl-positions__edit-form" onSubmit={handleSubmit}>
      <div className="wl-positions__edit-grid">
        <label className="wl-positions__term-field">
          <span>Underlying</span>
          <input value={form.underlying} onChange={(e) => update('underlying', e.target.value)} />
        </label>
        <div className="wl-positions__term-field">
          <Select
            label="Product Type"
            value={form.product_type}
            onChange={(v) => update('product_type', v)}
            options={PRODUCT_TYPES.map((pt) => ({ value: pt, label: pt }))}
          />
        </div>
        <label className="wl-positions__term-field">
          <span>Quantity</span>
          <NumberInput type="number" step="any" value={form.quantity} onChange={(e) => update('quantity', e.target.value)} />
        </label>
        <label className="wl-positions__term-field">
          <span>Entry Price</span>
          <NumberInput type="number" step="any" value={form.entry_price} onChange={(e) => update('entry_price', e.target.value)} />
        </label>
        <label className="wl-positions__term-field">
          <span>Currency</span>
          <input
            value={form.currency}
            maxLength={3}
            onChange={(e) => update('currency', e.target.value.toUpperCase())}
          />
        </label>
        <div className="wl-positions__term-field">
          <Select
            label="Status"
            value={form.status}
            onChange={(v) => update('status', v)}
            options={[
              { value: 'open', label: 'open' },
              { value: 'knocked_in', label: 'knocked_in' },
              { value: 'closed', label: 'closed' },
            ]}
          />
        </div>
        <label className="wl-positions__term-field">
          <span>Trade ID</span>
          <input value={form.source_trade_id} onChange={(e) => update('source_trade_id', e.target.value)} />
        </label>
        <div className="wl-positions__term-field">
          <Select
            label="Engine"
            value={form.engine_name}
            onChange={(v) => update('engine_name', v)}
            placeholder="—"
            options={[
              { value: '', label: '—' },
              ...engineOptions.map((eng) => ({ value: eng, label: eng })),
            ]}
          />
        </div>
      </div>
      {form.currency && row.product?.currency && form.currency !== row.product.currency && (
        <div className="wl-positions__ticket-warning" role="status">
          Currency {form.currency} differs from booked trade currency ({row.product.currency})
          — risk will re-bucket under the new currency.
        </div>
      )}
      <ProductTermsForm
        productType={form.product_type}
        productKwargs={(() => {
          try { return JSON.parse(form.product_terms); } catch { return {}; }
        })()}
        onChange={(productTerms) => update('product_terms', JSON.stringify(productTerms, null, 2))}
      />
      {error && <div className="wl-positions__ticket-error" role="alert">{error}</div>}
      <div className="wl-positions__edit-actions">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? 'Saving...' : 'Save Changes'}
        </Button>
      </div>
    </form>
  );
}

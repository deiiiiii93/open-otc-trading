import { useEffect, useMemo, useState, useCallback, type FormEvent } from 'react';
import type { RFQ } from '../types';
import { Button } from './Button';
import { Input } from './Input';
import { Select } from './Select';
import { DatePicker } from './DatePicker';
import { Badge } from './Badge';
import { getProductFields, getProductTypeLabel } from '../lib/rfqProductFields';
import { groupProductFields, type FieldType } from '../lib/productFieldGroups';
import './RfqQuoteForm.css';

export type RfqQuoteOverrides = {
  market?: Record<string, unknown>;
  engine_spec?: Record<string, unknown>;
  product_kwargs?: Record<string, unknown>;
  unknown?: Record<string, unknown>;
  target?: Record<string, unknown>;
};

type Props = {
  rfq: RFQ;
  onQuote: (id: number, overrides: RfqQuoteOverrides) => void;
};

const SIDE_OPTIONS = [
  { value: 'buy', label: 'Buy' },
  { value: 'sell', label: 'Sell' },
];

const QUOTE_MODE_OPTIONS = [
  { value: 'solve', label: 'Solve Unknown' },
  { value: 'price', label: 'Price Fixed Terms' },
];

const NUMERIC_KEYS = new Set([
  'accrual_factor', 'barrier', 'barrier_direction', 'barrier_level',
  'barrier_type', 'cash_payoff', 'component_product_id', 'contract_multiplier',
  'coupon_barrier_pct', 'coupon_rate', 'coupon_yield',
  'initial_price', 'ki_barrier', 'ki_barrier_pct',
  'knock_out_rebate', 'ko_barrier', 'ko_barrier_pct', 'ko_rate',
  'lockup_months', 'lower_barrier', 'maturity', 'maturity_years',
  'multiplier', 'no_hit_rebate', 'notional', 'num_observations',
  'participation_rate', 'payout', 'post_ko_barrier_pct', 'post_ko_rate',
  'quantity', 'rebate', 'return_rate', 'strike', 'upper_barrier',
]);

const SNOWBALL_FAMILIES = new Set([
  'SnowballOption',
  'KnockOutResetSnowballOption',
  'PhoenixOption',
]);

const MISSING_FIELD_ALIASES: Record<string, string[]> = {
  maturity_years: ['maturity'],
  trade_start_date: ['initial_date'],
  ko_barrier_pct: ['barrier_config.ko_barrier'],
  ki_barrier_pct: ['barrier_config.ki_barrier'],
  ko_rate: ['barrier_config.ko_rate'],
  lockup_months: ['barrier_config.lockup_months'],
  post_ko_barrier_pct: ['post_barrier_config.ko_barrier'],
  post_ko_rate: ['post_barrier_config.ko_rate'],
  coupon_barrier_pct: ['coupon_config.coupon_barrier'],
  coupon_rate: ['coupon_config.coupon_rate'],
};

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}

function parseMissingFields(quotePayload: Record<string, unknown>): Set<string> {
  const validation = quotePayload.validation as { missing_fields?: string[] } | undefined;
  return new Set(validation?.missing_fields ?? []);
}

function parseErrors(quotePayload: Record<string, unknown>): string[] {
  const validation = quotePayload.validation as { errors?: string[] } | undefined;
  return validation?.errors ?? [];
}

function parseQuantarkError(quotePayload: Record<string, unknown>): string | null {
  const err = quotePayload.quantark_error ?? quotePayload.error;
  if (!err) return null;
  return String(err);
}

function hasValue(v: unknown): boolean {
  return v !== undefined && v !== null && v !== '';
}

function getPathValue(source: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.').filter(Boolean);
  let current: unknown = source;
  for (const part of parts) {
    if (!isPlainObject(current)) return undefined;
    current = current[part];
  }
  return current;
}

function clonePlainValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(clonePlainValue);
  if (!isPlainObject(value)) return value;
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, clonePlainValue(entry)]));
}

function clonePlainRecord(value: Record<string, unknown>): Record<string, unknown> {
  return clonePlainValue(value) as Record<string, unknown>;
}

function setPathValue(target: Record<string, unknown>, path: string, value: unknown) {
  const parts = path.split('.').filter(Boolean);
  if (parts.length === 0) return;
  let current = target;
  for (const part of parts.slice(0, -1)) {
    const existing = current[part];
    if (isPlainObject(existing)) {
      current = existing;
    } else {
      const next: Record<string, unknown> = {};
      current[part] = next;
      current = next;
    }
  }
  current[parts[parts.length - 1]] = value;
}

function firstValue(source: Record<string, unknown>, paths: string[]): unknown {
  for (const path of paths) {
    const value = getPathValue(source, path);
    if (hasValue(value)) return value;
  }
  return undefined;
}

function percentFromBarrier(productKwargs: Record<string, unknown>, path: string): number | undefined {
  const initial = Number(getPathValue(productKwargs, 'initial_price'));
  const barrier = Number(getPathValue(productKwargs, path));
  if (!Number.isFinite(initial) || initial === 0 || !Number.isFinite(barrier)) return undefined;
  return Number(((barrier / initial) * 100).toFixed(6));
}

function dateOnly(value: unknown): string | undefined {
  if (typeof value !== 'string' || value.length < 10) return undefined;
  return value.slice(0, 10);
}

function productFieldValue(
  productType: string,
  productKwargs: Record<string, unknown>,
  payload: Record<string, unknown>,
  key: string,
): unknown {
  const direct = getPathValue(productKwargs, key);
  if (hasValue(direct)) return direct;
  if (!SNOWBALL_FAMILIES.has(productType)) return undefined;

  switch (key) {
    case 'maturity_years':
      return firstValue(productKwargs, ['maturity']);
    case 'trade_start_date':
      return (
        dateOnly(firstValue(productKwargs, ['trade_start_date', 'initial_date']))
        ?? dateOnly(getPathValue(payload, 'market.valuation_date'))
      );
    case 'ko_barrier_pct':
      return percentFromBarrier(productKwargs, 'barrier_config.ko_barrier');
    case 'ki_barrier_pct':
      return percentFromBarrier(productKwargs, 'barrier_config.ki_barrier');
    case 'ko_rate':
      return getPathValue(productKwargs, 'barrier_config.ko_rate');
    case 'lockup_months':
      return getPathValue(productKwargs, 'barrier_config.lockup_months');
    case 'observation_frequency':
      return getPathValue(productKwargs, 'barrier_config.ko_observation_schedule.frequency');
    case 'post_ko_barrier_pct':
      return percentFromBarrier(productKwargs, 'post_barrier_config.ko_barrier');
    case 'post_ko_rate':
      return getPathValue(productKwargs, 'post_barrier_config.ko_rate');
    case 'coupon_barrier_pct':
      return percentFromBarrier(productKwargs, 'coupon_config.coupon_barrier');
    case 'coupon_rate':
      return getPathValue(productKwargs, 'coupon_config.coupon_rate');
    default:
      return undefined;
  }
}

function isFieldMissing(fieldKey: string, missingSet: Set<string>): boolean {
  return missingSet.has(fieldKey) || (MISSING_FIELD_ALIASES[fieldKey] ?? []).some((alias) => missingSet.has(alias));
}

function coerceValue(raw: string, kind: 'string' | 'number' | 'boolean' | 'null' | 'array'): unknown {
  if (kind === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (kind === 'boolean') {
    if (raw === 'true') return true;
    if (raw === 'false') return false;
    return raw;
  }
  return raw;
}

function buildProductKwargsOverrides(
  formValues: Record<string, string>,
  fields: { key: string; type: FieldType }[],
  productKwargs: Record<string, unknown>,
): Record<string, unknown> {
  const overrides = clonePlainRecord(productKwargs);
  for (const field of fields) {
    const key = field.key;
    const raw = formValues[key];
    if (raw === undefined) continue;
    const current = getPathValue(productKwargs, key);
    if (raw === '' && current == null) continue;
    if (raw === '') continue;
    const kind = field.type === 'number'
      ? 'number'
      : field.type === 'boolean'
        ? 'boolean'
        : typeof current === 'number'
          ? 'number'
          : 'string';
    const coerced = coerceValue(raw, kind);
    setPathValue(overrides, key, coerced);
  }
  return overrides;
}

function parseOptionalNumber(raw: string | undefined): number | undefined {
  if (raw == null || raw.trim() === '') return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function firstNumber(source: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = readNumber(source[key]);
    if (value != null) return value;
  }
  return null;
}

function formatNumber(value: number, maximumFractionDigits = 6): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits,
  }).format(value);
}

function quoteAmountFromPayload(
  requestPayload: Record<string, unknown>,
  quotePayload: Record<string, unknown>,
): number | null {
  const directAmount = readNumber(quotePayload.quote_amount);
  if (directAmount != null) return directAmount;

  const unitPrice = firstNumber(quotePayload, ['unit_price', 'achieved_price', 'price', 'target_value']);
  const quantity = readNumber(requestPayload.quantity);
  if (unitPrice == null || quantity == null) return null;

  const productKwargs = isPlainObject(requestPayload.product_kwargs) ? requestPayload.product_kwargs : {};
  const scale = readNumber(quotePayload.quote_price_scale) ?? readNumber(productKwargs.initial_price) ?? 1;
  if (scale <= 0) return null;
  return (unitPrice * quantity) / scale;
}

function parseFieldValue(value: unknown, fieldType: FieldType): string {
  if (value == null || value === '') return '';
  if (fieldType === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function readUnknownPayload(payload: Record<string, unknown>) {
  const unknown = isPlainObject(payload.unknown) ? payload.unknown : {};
  return {
    field_path: String(unknown.field_path ?? ''),
    lower_bound: typeof unknown.lower_bound === 'number' ? String(unknown.lower_bound) : '',
    upper_bound: typeof unknown.upper_bound === 'number' ? String(unknown.upper_bound) : '',
    initial_guess: typeof unknown.initial_guess === 'number' ? String(unknown.initial_guess) : '',
  };
}

function readTargetPayload(payload: Record<string, unknown>) {
  const target = isPlainObject(payload.target) ? payload.target : {};
  return {
    label: String(target.label ?? 'price'),
    value: typeof target.value === 'number' ? String(target.value) : '',
  };
}

function readMarketPayload(payload: Record<string, unknown>) {
  const market = isPlainObject(payload.market) ? payload.market : {};
  return {
    spot: typeof market.spot === 'number' ? String(market.spot) : '',
    volatility: typeof market.volatility === 'number' ? String(market.volatility) : '',
    rate: typeof market.rate === 'number' ? String(market.rate) : '',
    dividend_yield: typeof market.dividend_yield === 'number' ? String(market.dividend_yield) : '',
  };
}

function readEngineSpecPayload(payload: Record<string, unknown>) {
  const engineSpec = isPlainObject(payload.engine_spec) ? payload.engine_spec : {};
  return {
    engine_name: String(engineSpec.engine_name ?? ''),
    params_type: String(engineSpec.params_type ?? ''),
  };
}

export function RfqQuoteForm({ rfq, onQuote }: Props) {
  const payload = useMemo(
    () => (rfq.request_payload ?? {}) as Record<string, unknown>,
    [rfq.request_payload],
  );
  const productType = String(payload.product_type ?? '');
  const productKwargs = isPlainObject(payload.product_kwargs) ? payload.product_kwargs : {};
  const productLabel = getProductTypeLabel(productType);
  const side = String(payload.side ?? 'buy');
  const underlying = String(payload.underlying ?? '');
  const quantity = typeof payload.quantity === 'number' ? String(payload.quantity) : '';

  const productFields = useMemo(
    () => getProductFields(productType, productKwargs),
    [productType, productKwargs],
  );

  const groupedFields = useMemo(() => groupProductFields(productFields), [productFields]);

  const missingSet = useMemo(
    () => parseMissingFields(rfq.quote_payload ?? {}),
    [rfq.quote_payload],
  );
  const errors = useMemo(
    () => parseErrors(rfq.quote_payload ?? {}),
    [rfq.quote_payload],
  );
  const quantarkError = useMemo(
    () => parseQuantarkError(rfq.quote_payload ?? {}),
    [rfq.quote_payload],
  );

  const initialFormValues = useMemo(() => {
    const map: Record<string, string> = {};
    for (const field of productFields) {
      const value = productFieldValue(productType, productKwargs, payload, field.key);
      map[field.key] = parseFieldValue(value, field.type);
    }
    map.side = side;
    map.underlying = underlying;
    map.quantity = quantity;
    const unknown = readUnknownPayload(payload);
    map.unknown_field_path = unknown.field_path;
    map.unknown_lower = unknown.lower_bound;
    map.unknown_upper = unknown.upper_bound;
    map.unknown_guess = unknown.initial_guess;
    const target = readTargetPayload(payload);
    map.target_label = target.label;
    map.target_value = target.value;
    const market = readMarketPayload(payload);
    map.market_spot = market.spot;
    map.market_vol = market.volatility;
    map.market_rate = market.rate;
    map.market_div = market.dividend_yield;
    const engineSpec = readEngineSpecPayload(payload);
    map.engine_name = engineSpec.engine_name;
    map.engine_params_type = engineSpec.params_type;
    return map;
  }, [payload, productFields, productKwargs, productType, side, underlying, quantity]);

  const [formValues, setFormValues] = useState<Record<string, string>>(initialFormValues);

  useEffect(() => {
    setFormValues(initialFormValues);
  }, [initialFormValues]);

  const setFieldValue = useCallback(
    (path: string, value: string) => {
      setFormValues((prev) => ({ ...prev, [path]: value }));
    },
    [],
  );

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();

    const productKwargsOverrides = buildProductKwargsOverrides(formValues, productFields, productKwargs);

    const overrides: RfqQuoteOverrides = {};

    if (Object.keys(productKwargsOverrides).length > 0) {
      overrides.product_kwargs = productKwargsOverrides;
    }

    if (formValues.unknown_field_path) {
      overrides.unknown = {
        field_path: formValues.unknown_field_path,
        lower_bound: Number(formValues.unknown_lower) || 0,
        upper_bound: Number(formValues.unknown_upper) || 0,
        initial_guess: formValues.unknown_guess ? Number(formValues.unknown_guess) : null,
      };
    }

    if (formValues.target_label || formValues.target_value) {
      overrides.target = {
        label: formValues.target_label || 'price',
        value: Number(formValues.target_value) || 0,
      };
    }

    const marketSpot = parseOptionalNumber(formValues.market_spot);
    const marketVol = parseOptionalNumber(formValues.market_vol);
    const marketRate = parseOptionalNumber(formValues.market_rate);
    const marketDiv = parseOptionalNumber(formValues.market_div);
    if (marketSpot !== undefined || marketVol !== undefined || marketRate !== undefined || marketDiv !== undefined) {
      overrides.market = {};
      if (marketSpot !== undefined) overrides.market.spot = marketSpot;
      if (marketVol !== undefined) overrides.market.volatility = marketVol;
      if (marketRate !== undefined) overrides.market.rate = marketRate;
      if (marketDiv !== undefined) overrides.market.dividend_yield = marketDiv;
    }

    if (formValues.engine_name) {
      overrides.engine_spec = { engine_name: formValues.engine_name };
      if (formValues.engine_params_type) {
        overrides.engine_spec.params_type = formValues.engine_params_type;
      }
    }

    onQuote(rfq.id, overrides);
  };

  const solved = useMemo(() => {
    return readNumber(rfq.quote_payload.solved_value);
  }, [rfq.quote_payload.solved_value]);

  const unitPrice = useMemo(
    () => firstNumber(rfq.quote_payload, ['unit_price', 'achieved_price', 'price', 'target_value']),
    [rfq.quote_payload],
  );

  const quoteAmount = useMemo(
    () => quoteAmountFromPayload(payload, rfq.quote_payload),
    [payload, rfq.quote_payload],
  );

  const quoteAmountCurrency = useMemo(() => {
    const direct = rfq.quote_payload.quote_amount_currency;
    if (typeof direct === 'string' && direct.trim()) return direct.trim();
    const marketPayload = isPlainObject(payload.market) ? payload.market : {};
    const currency = marketPayload.currency;
    return typeof currency === 'string' && currency.trim() ? currency.trim() : '';
  }, [payload, rfq.quote_payload.quote_amount_currency]);

  const quoteMode = String(payload.quote_mode ?? 'solve');
  const unknown = readUnknownPayload(payload);
  const target = readTargetPayload(payload);
  const market = readMarketPayload(payload);
  const engineSpec = readEngineSpecPayload(payload);

  return (
    <form className="wl-rfq-quote-form" onSubmit={handleSubmit}>
      {(errors.length > 0 || quantarkError) && (
        <div className="wl-rfq-quote-form__errors">
          {quantarkError && (
            <div className="wl-rfq-quote-form__error-item">{quantarkError}</div>
          )}
          {errors.map((err, i) => (
            <div key={i} className="wl-rfq-quote-form__error-item">{err}</div>
          ))}
        </div>
      )}

      <div className="wl-rfq-quote-form__headline">
        <Badge variant="ink">{productLabel}</Badge>
        {underlying && <span className="wl-rfq-quote-form__underlying">{underlying}</span>}
        <span className="wl-rfq-quote-form__mode">{quoteMode === 'price' ? 'Price' : 'Solve'}</span>
      </div>

      <div className="wl-rfq-quote-form__counterparty">
        <Select
          label="Side"
          value={formValues.side ?? 'buy'}
          onChange={(v) => setFieldValue('side', v)}
          options={SIDE_OPTIONS}
        />
        <Input
          label="Underlying"
          value={formValues.underlying ?? ''}
          onChange={(e) => setFieldValue('underlying', e.target.value)}
        />
        <Input
          type="number"
          step="any"
          label="Quantity"
          value={formValues.quantity ?? ''}
          onChange={(e) => setFieldValue('quantity', e.target.value)}
        />
      </div>

      {groupedFields.map((group) => (
        <fieldset key={group.key} className="wl-rfq-quote-form__section">
          <legend className="wl-rfq-quote-form__legend">{group.label}</legend>
          <div className="wl-rfq-quote-form__grid">
            {group.fields.map((field) => (
              <RfqFormField
                key={field.key}
                field={field}
                value={formValues[field.key] ?? ''}
                onChange={(v) => setFieldValue(field.key, v)}
                isMissing={isFieldMissing(field.key, missingSet)}
              />
            ))}
          </div>
        </fieldset>
      ))}

      {quoteMode === 'solve' && (
        <fieldset className="wl-rfq-quote-form__section">
          <legend className="wl-rfq-quote-form__legend">Solver Parameters</legend>
          <div className="wl-rfq-quote-form__grid">
            <Input
              label="Unknown Field"
              value={formValues.unknown_field_path ?? unknown.field_path}
              onChange={(e) => setFieldValue('unknown_field_path', e.target.value)}
            />
            <Input
              type="number"
              step="any"
              label="Lower Bound"
              value={formValues.unknown_lower ?? unknown.lower_bound}
              onChange={(e) => setFieldValue('unknown_lower', e.target.value)}
            />
            <Input
              type="number"
              step="any"
              label="Upper Bound"
              value={formValues.unknown_upper ?? unknown.upper_bound}
              onChange={(e) => setFieldValue('unknown_upper', e.target.value)}
            />
            <Input
              type="number"
              step="any"
              label="Initial Guess"
              value={formValues.unknown_guess ?? unknown.initial_guess}
              onChange={(e) => setFieldValue('unknown_guess', e.target.value)}
            />
          </div>
          <div className="wl-rfq-quote-form__grid" style={{ marginTop: 'var(--gap-3)' }}>
            <Select
              label="Target Label"
              value={formValues.target_label ?? target.label}
              onChange={(v) => setFieldValue('target_label', v)}
              options={[
                { value: 'price', label: 'price' },
                { value: 'premium', label: 'premium' },
                { value: 'reoffer', label: 'reoffer' },
              ]}
            />
            <Input
              type="number"
              step="any"
              label="Target Value"
              value={formValues.target_value ?? target.value}
              onChange={(e) => setFieldValue('target_value', e.target.value)}
            />
          </div>
        </fieldset>
      )}

      <fieldset className="wl-rfq-quote-form__section">
        <legend className="wl-rfq-quote-form__legend">Market Data</legend>
        <div className="wl-rfq-quote-form__grid">
          <Input
            type="number"
            step="any"
            label="Spot"
            value={formValues.market_spot ?? market.spot}
            onChange={(e) => setFieldValue('market_spot', e.target.value)}
          />
          <Input
            type="number"
            step="any"
            label="Volatility"
            value={formValues.market_vol ?? market.volatility}
            onChange={(e) => setFieldValue('market_vol', e.target.value)}
          />
          <Input
            type="number"
            step="any"
            label="Rate"
            value={formValues.market_rate ?? market.rate}
            onChange={(e) => setFieldValue('market_rate', e.target.value)}
          />
          <Input
            type="number"
            step="any"
            label="Dividend Yield"
            value={formValues.market_div ?? market.dividend_yield}
            onChange={(e) => setFieldValue('market_div', e.target.value)}
          />
        </div>
      </fieldset>

      <fieldset className="wl-rfq-quote-form__section">
        <legend className="wl-rfq-quote-form__legend">Engine</legend>
        <div className="wl-rfq-quote-form__grid">
          <Input
            label="Engine Name"
            value={formValues.engine_name ?? engineSpec.engine_name}
            onChange={(e) => setFieldValue('engine_name', e.target.value)}
          />
          <Input
            label="Params Type"
            value={formValues.engine_params_type ?? engineSpec.params_type}
            onChange={(e) => setFieldValue('engine_params_type', e.target.value)}
          />
        </div>
      </fieldset>

      {(solved != null || quoteAmount != null || unitPrice != null) && (
        <div className="wl-rfq-quote-form__results">
          {solved != null && (
            <div className="wl-rfq-quote-form__metric">
              <span>Solved</span>
              <strong>{solved.toFixed(6)}</strong>
            </div>
          )}
          {quoteAmount != null && (
            <div className="wl-rfq-quote-form__metric">
              <span>Quote Amount</span>
              <strong>{quoteAmountCurrency ? `${quoteAmountCurrency} ` : ''}{formatNumber(quoteAmount, 2)}</strong>
            </div>
          )}
          {unitPrice != null && (
            <div className="wl-rfq-quote-form__metric">
              <span>{quoteAmount != null ? 'Unit Price' : 'Price'}</span>
              <strong>{unitPrice.toFixed(6)}</strong>
            </div>
          )}
        </div>
      )}

      <div className="wl-rfq-quote-form__actions">
        <Button type="submit" variant="primary">Quote</Button>
      </div>
    </form>
  );
}

function RfqFormField({
  field,
  value,
  onChange,
  isMissing,
}: {
  field: { key: string; label: string; type: FieldType; options?: string[] };
  value: string;
  onChange: (v: string) => void;
  isMissing: boolean;
}) {
  const label = `${field.label}${isMissing ? ' *' : ''}`;
  const error = isMissing ? 'Required' : undefined;

  if (field.type === 'boolean') {
    return (
      <Select
        label={label}
        value={value}
        onChange={onChange}
        options={[
          { value: 'true', label: 'True' },
          { value: 'false', label: 'False' },
        ]}
        error={error}
      />
    );
  }

  if (field.type === 'select') {
    return (
      <Select
        label={label}
        value={value}
        onChange={onChange}
        options={[
          { value: '', label: '-' },
          ...(field.options?.map((opt) => ({ value: opt, label: opt })) ?? []),
        ]}
        error={error}
      />
    );
  }

  if (field.type === 'date') {
    return (
      <DatePicker
        label={label}
        value={value}
        onChange={onChange}
      />
    );
  }

  return (
    <Input
      type={field.type === 'number' ? 'number' : 'text'}
      step={field.type === 'number' ? 'any' : undefined}
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      error={error}
    />
  );
}

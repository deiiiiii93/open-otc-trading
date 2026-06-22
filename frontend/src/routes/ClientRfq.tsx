import { useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { Send } from 'lucide-react';
import { WizardPage } from '../components/templates';
import { Panel } from '../components/Panel';
import { Empty } from '../components/Empty';
import { Button } from '../components/Button';
import { Badge } from '../components/Badge';
import { NumberInput } from '../components/NumberInput';
import { Select } from '../components/Select';
import { Tabs, TabsList, TabsTrigger } from '../components/Tabs';
import { ProductTermsForm } from '../components/ProductTermsForm';
import { RfqHistoryPanel } from '../components/RfqHistoryPanel';
import { rfqStatusBadge, formatRfqStatus } from '../lib/rfqStatus';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type {
  PageContext,
  PageContextReporter,
  RFQ,
  RfqCatalog,
  RfqTemplate,
  RfqUnknownFieldSpec,
  Underlying,
} from '../types';
import './ClientRfq.css';

export type ClientRfqForm = {
  underlying: string;
  currency: string;
  side: 'buy' | 'sell';
  notional: number | null;
  quoteMode: 'solve' | 'price';
  product: string;
  productTerms: Record<string, unknown>;
  engineSpec: Record<string, unknown>;
  unknownField: string;
  lowerBound: number | null;
  upperBound: number | null;
  initialGuess: number | null;
  targetLabel: 'price' | 'premium' | 'reoffer';
  targetValue: number | null;
};

type Props = {
  catalog?: RfqCatalog | null;
  underlyings?: Underlying[];
  rfqs?: RFQ[];
  selectedRfqId?: number | null;
  clientName?: string;
  defaultMessage?: string;
  loading?: boolean;
  submitting?: boolean;
  error?: string | null;
  feedback?: string | null;
  onClientNameChange?: (name: string) => void;
  onSelectRfq?: (rfqId: number) => void;
  onSubmitNL?: (message: string) => Promise<void> | void;
  onSubmitStructured?: (form: ClientRfqForm) => Promise<void> | void;
  onPageContextChange?: PageContextReporter;
};

const FALLBACK_TEMPLATE: RfqTemplate = {
  key: 'vanilla',
  label: 'Vanilla',
  product_type: 'EuropeanVanillaOption',
  engine_spec: { engine_name: 'BlackScholesEngine' },
  unknown_fields: ['strike', 'volatility'],
  unknown_field_specs: [
    { field_path: 'strike', label: 'Strike', lower_bound: 50, upper_bound: 150, initial_guess: 100 },
    { field_path: 'volatility', label: 'Volatility', lower_bound: 0.01, upper_bound: 2, initial_guess: 0.2 },
  ],
  product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
};

export function ClientRfq({
  catalog,
  underlyings = [],
  rfqs = [],
  selectedRfqId,
  clientName = 'Demo Client',
  defaultMessage = '',
  loading = false,
  submitting = false,
  error = null,
  feedback = null,
  onClientNameChange,
  onSelectRfq,
  onSubmitNL,
  onSubmitStructured,
  onPageContextChange,
}: Props) {
  const templates = catalog?.templates?.length ? catalog.templates : [FALLBACK_TEMPLATE];
  const [mode, setMode] = useState<'nl' | 'structured'>('structured');
  const [message, setMessage] = useState(defaultMessage);
  const [form, setForm] = useState<ClientRfqForm>(() => formFromTemplate(templates[0]));
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null);
  const workbenchRef = useRef<HTMLDivElement | null>(null);
  const [histPct, setHistPct] = useState(28);
  const [termsPct, setTermsPct] = useState(42);

  const effectiveSelectedId = selectedRfqId !== undefined ? selectedRfqId : internalSelectedId;
  const selectedRfq = rfqs.find((rfq) => rfq.id === effectiveSelectedId) ?? rfqs[0] ?? null;

  const template = templates.find((item) => item.product_type === form.product) ?? FALLBACK_TEMPLATE;
  const specs = templateSpecs(template);

  const productLabelFor = (productType: string): string =>
    templates.find((item) => item.product_type === productType)?.label ?? (productType || 'Unknown');

  const handleSelect = (rfqId: number) => {
    if (selectedRfqId === undefined) setInternalSelectedId(rfqId);
    onSelectRfq?.(rfqId);
  };

  const handleResizeHistPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!workbenchRef.current) return;
    event.preventDefault();
    const rect = workbenchRef.current.getBoundingClientRect();
    const handlePointerMove = (moveEvent: PointerEvent) => {
      const next = ((moveEvent.clientX - rect.left) / rect.width) * 100;
      setHistPct(Math.min(50, Math.max(15, next)));
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };

  const handleResizeTermsPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!workbenchRef.current) return;
    event.preventDefault();
    const rect = workbenchRef.current.getBoundingClientRect();
    const handlePointerMove = (moveEvent: PointerEvent) => {
      const next = ((moveEvent.clientX - rect.left) / rect.width) * 100;
      setTermsPct(Math.min(85, Math.max(histPct + 20, next)));
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };

  const switchProduct = (productType: string) => {
    const nextTemplate = templates.find((item) => item.product_type === productType) ?? FALLBACK_TEMPLATE;
    const next = formFromTemplate(nextTemplate);
    setForm({
      ...next,
      underlying: form.underlying,
      currency: form.currency,
      productTerms: syncTermsUnderlying(next.productTerms, form.underlying),
      side: form.side,
      notional: form.notional,
      quoteMode: form.quoteMode,
      targetLabel: form.targetLabel,
      targetValue: form.targetValue,
    });
  };

  const selectUnknownField = (fieldPath: string) => {
    const spec = specs.find((item) => item.field_path === fieldPath);
    setForm({
      ...form,
      unknownField: fieldPath,
      lowerBound: spec?.lower_bound ?? form.lowerBound,
      upperBound: spec?.upper_bound ?? form.upperBound,
      initialGuess: spec?.initial_guess ?? form.initialGuess,
    });
  };

  const cloneRfq = (rfq: RFQ) => {
    const payload = rfq.request_payload ?? {};
    const unknown = isRecord(payload.unknown) ? payload.unknown : {};
    const target = isRecord(payload.target) ? payload.target : {};
    const productType = String(payload.product_type ?? form.product);
    const sourceTemplate = templates.find((item) => item.product_type === productType) ?? null;
    setMode('structured');
    setForm({
      underlying: String(payload.underlying ?? ''),
      currency: currencyFromPayload(payload, 'CNY'),
      side: payload.side === 'sell' ? 'sell' : 'buy',
      notional: numberOrNull(payload.quantity) ?? 1,
      quoteMode: payload.quote_mode === 'price' ? 'price' : 'solve',
      product: productType,
      productTerms: isRecord(payload.product_kwargs)
        ? cloneRecord(payload.product_kwargs)
        : cloneRecord(sourceTemplate?.product_kwargs ?? {}),
      engineSpec: isRecord(payload.engine_spec)
        ? cloneRecord(payload.engine_spec)
        : cloneRecord(sourceTemplate?.engine_spec ?? {}),
      unknownField: String(unknown.field_path ?? ''),
      lowerBound: numberOrNull(unknown.lower_bound),
      upperBound: numberOrNull(unknown.upper_bound),
      initialGuess: numberOrNull(unknown.initial_guess),
      targetLabel: target.label === 'premium' || target.label === 'reoffer' ? target.label : 'price',
      targetValue: numberOrNull(target.value) ?? 0,
    });
  };

  const contractComplete = isContractComplete(form.product, form.productTerms, form.quoteMode, form.unknownField);
  const hasUnderlying = form.underlying.trim() !== '';
  const notionalValid = form.notional != null && form.notional > 0;
  const solveValid =
    form.quoteMode === 'price' ||
    (form.unknownField !== '' &&
      form.targetValue != null &&
      form.targetValue > 0 &&
      form.lowerBound != null &&
      form.upperBound != null &&
      form.initialGuess != null);
  const canSubmit = !submitting && hasUnderlying && notionalValid && contractComplete && solveValid;

  const chips = useMemo(() => {
    const pending = rfqs.filter(
      (rfq) => rfq.status === 'pending_approval' || rfq.status === 'submitted',
    ).length;
    return [`${rfqs.length} RFQs`, `${pending} pending`];
  }, [rfqs]);

  const pageContext = useMemo((): PageContext => ({
    route: 'client-rfq',
    title: 'Client RFQ',
    path: '/',
    entity_ids: { rfq_id: selectedRfq?.id ?? null },
    snapshot: {
      rfq_count: rfqs.length,
      selected_rfq: selectedRfq
        ? {
            id: selectedRfq.id,
            status: selectedRfq.status,
            product_type: String(selectedRfq.request_payload?.product_type ?? ''),
            client_name: selectedRfq.client_name,
          }
        : null,
      editor: { mode, product: form.product, quote_mode: form.quoteMode },
    },
    loaded_context: { completeness: 'complete' },
    actions: declareActions([
      {
        name: 'submit_structured_rfq',
        required_ids: [],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/client/rfq/form',
      },
      {
        name: 'submit_nl_rfq',
        required_ids: [],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/client/rfq/chat',
      },
    ]),
    chips,
  }), [chips, form.product, form.quoteMode, mode, rfqs.length, selectedRfq]);
  usePageContextReporter(pageContext, onPageContextChange);

  return (
    <WizardPage
      title="CLIENT RFQ"
      chips={chips}
      tabs={(
        <Tabs value={mode} onValueChange={(value) => setMode(value === 'nl' ? 'nl' : 'structured')}>
          <TabsList>
            <TabsTrigger value="nl">Natural Language</TabsTrigger>
            <TabsTrigger value="structured">Structured</TabsTrigger>
          </TabsList>
        </Tabs>
      )}
    >
      {error && (
        <div className="wl-client-rfq__message wl-client-rfq__message--error" role="alert">{error}</div>
      )}
      {feedback && <div className="wl-client-rfq__message" role="status">{feedback}</div>}

      <div
        ref={workbenchRef}
        className="wl-client-rfq__workbench"
        data-mode={mode}
        style={{
          '--rfq-hist-pct': mode === 'nl'
            ? `${Math.min(50, Math.max(15, histPct))}%`
            : `${histPct}%`,
          '--rfq-terms-pct': `${termsPct}%`,
        } as CSSProperties}
      >
        <Panel
          title="My RFQs"
          meta={loading ? 'loading' : `${rfqs.length} rfqs`}
          className="wl-client-rfq__history-panel"
        >
          <label className="wl-client-rfq__client-name">
            <span>Client Name</span>
            <input
              value={clientName}
              onChange={(event) => onClientNameChange?.(event.currentTarget.value)}
              aria-label="Client Name"
            />
          </label>
          <RfqHistoryPanel
            rfqs={rfqs}
            selectedRfqId={selectedRfq?.id ?? null}
            productLabelFor={productLabelFor}
            onSelect={handleSelect}
            onClone={cloneRfq}
          />
        </Panel>

        <button
          type="button"
          className="wl-client-rfq__panel-resizer"
          aria-label="Resize history panel"
          onPointerDown={handleResizeHistPointerDown}
        />

        {mode === 'nl' ? (
          <Panel title="Message" meta="chat intake" className="wl-client-rfq__nl-panel">
            <textarea
              className="wl-client-rfq__textarea"
              rows={6}
              value={message}
              onChange={(event) => setMessage(event.currentTarget.value)}
              aria-label="Message"
            />
            <div className="wl-client-rfq__actions">
              <Button
                variant="primary"
                disabled={submitting || !message.trim()}
                onClick={() => void onSubmitNL?.(message)}
              >
                <Send size={15} aria-hidden="true" />
                {submitting ? 'Submitting…' : 'Submit Natural Language'}
              </Button>
            </div>
          </Panel>
        ) : (
          <>
            <Panel
              title={`${productLabelFor(form.product)} Terms`}
              meta={form.product}
              className="wl-client-rfq__terms-panel"
            >
              <div className="wl-client-rfq__field-group">
                <div className="wl-client-rfq__field-group-title">Product Details</div>
                <Select
                  label="Product"
                  className="wl-client-rfq__field"
                  value={form.product}
                  onChange={(v) => switchProduct(v)}
                  options={templates.map((item) => ({ value: item.product_type, label: item.label }))}
                />
                <UnderlyingSelect
                  value={form.underlying}
                  underlyings={underlyings}
                  className="wl-client-rfq__field"
                  onChange={(underlying) =>
                    setForm({
                      ...form,
                      underlying,
                      productTerms: syncTermsUnderlying(form.productTerms, underlying),
                    })}
                />
                <Select
                  label="Currency"
                  className="wl-client-rfq__field"
                  value={form.currency}
                  onChange={(v) => setForm({ ...form, currency: normalizeCurrency(v) })}
                  options={CURRENCY_OPTIONS}
                />
                <Select
                  label="Side"
                  className="wl-client-rfq__field"
                  value={form.side}
                  onChange={(v) => setForm({ ...form, side: v === 'sell' ? 'sell' : 'buy' })}
                  options={[
                    { value: 'buy', label: 'buy' },
                    { value: 'sell', label: 'sell' },
                  ]}
                />
                <label className="wl-client-rfq__field">
                  <span>Notional</span>
                  <NumberInput
                    type="number"
                    min="0"
                    step="any"
                    value={form.notional ?? ''}
                    onChange={(event) =>
                      setForm({ ...form, notional: parseNumberInput(event.currentTarget.value) })}
                    aria-label="Notional"
                  />
                </label>
              </div>
              <ProductTermsForm
                productType={form.product}
                productKwargs={form.productTerms}
                onChange={(productTerms) => setForm({ ...form, productTerms })}
              />
            </Panel>

            <button
              type="button"
              className="wl-client-rfq__panel-resizer"
              aria-label="Resize terms panel"
              onPointerDown={handleResizeTermsPointerDown}
            />

            <Panel
              title="Quote & Submit"
              meta={form.quoteMode === 'solve' ? 'solve unknown' : 'price fixed terms'}
              className="wl-client-rfq__quote-panel"
            >
              <div className="wl-client-rfq__section-title">Quote Mode</div>
              <div className="wl-client-rfq__field-group">
                <Select
                  label="Quote Mode"
                  className="wl-client-rfq__field"
                  value={form.quoteMode}
                  onChange={(v) => setForm({ ...form, quoteMode: v === 'price' ? 'price' : 'solve' })}
                  options={[
                    { value: 'solve', label: 'Solve Unknown' },
                    { value: 'price', label: 'Price Fixed Terms' },
                  ]}
                />
              </div>

              {form.quoteMode === 'solve' && (
                <>
                  <div className="wl-client-rfq__section-title">Solver Parameters</div>
                  <div className="wl-client-rfq__section-grid">
                    <Select
                      label="Solve For"
                      className="wl-client-rfq__field"
                      value={form.unknownField}
                      onChange={(v) => selectUnknownField(v)}
                      options={specs.map((spec) => ({ value: spec.field_path, label: spec.label }))}
                    />
                    <Select
                      label="Target Label"
                      className="wl-client-rfq__field"
                      value={form.targetLabel}
                      onChange={(v) => setForm({
                        ...form,
                        targetLabel: v === 'premium' || v === 'reoffer' ? v : 'price',
                      })}
                      options={[
                        { value: 'price', label: 'price' },
                        { value: 'premium', label: 'premium' },
                        { value: 'reoffer', label: 'reoffer' },
                      ]}
                    />
                    <label className="wl-client-rfq__field">
                      <span>Lower Bound</span>
                      <NumberInput
                        type="number"
                        step="any"
                        value={form.lowerBound ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, lowerBound: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Lower Bound"
                      />
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Upper Bound</span>
                      <NumberInput
                        type="number"
                        step="any"
                        value={form.upperBound ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, upperBound: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Upper Bound"
                      />
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Initial Guess</span>
                      <NumberInput
                        type="number"
                        step="any"
                        value={form.initialGuess ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, initialGuess: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Initial Guess"
                      />
                    </label>
                    <label className="wl-client-rfq__field">
                      <span>Target Value</span>
                      <NumberInput
                        type="number"
                        step="any"
                        value={form.targetValue ?? ''}
                        onChange={(event) =>
                          setForm({ ...form, targetValue: parseNumberInput(event.currentTarget.value) })}
                        aria-label="Target Value"
                      />
                    </label>
                  </div>
                </>
              )}

              {!contractComplete && (
                <div className="wl-client-rfq__hint">
                  Fill all required contract terms to enable submission.
                </div>
              )}

              <div className="wl-client-rfq__actions">
                <Button
                  variant="primary"
                  disabled={!canSubmit}
                  onClick={() => void onSubmitStructured?.(form)}
                >
                  {submitting ? 'Submitting…' : 'Submit RFQ ▸'}
                </Button>
              </div>

              <div className="wl-client-rfq__status">
                <div className="wl-client-rfq__status-title">Status</div>
                {selectedRfq ? (
                  <StatusDetail rfq={selectedRfq} productLabelFor={productLabelFor} />
                ) : (
                  <Empty message="No RFQ selected." />
                )}
              </div>
            </Panel>
          </>
        )}
      </div>
    </WizardPage>
  );
}

function UnderlyingSelect({
  value,
  underlyings,
  onChange,
  className,
}: {
  value: string;
  underlyings: Underlying[];
  onChange: (value: string) => void;
  className?: string;
}) {
  const active = underlyings.filter((underlying) => underlying.status === 'active');
  const hasCurrentValue = active.some((underlying) => underlying.symbol === value);
  const options = [
    ...(!value
      ? [{ value: '', label: active.length ? 'Choose underlying' : 'No active underlyings', disabled: active.length > 0 }]
      : []),
    ...(value && !hasCurrentValue
      ? [{ value, label: `${value} (not active)`, disabled: true }]
      : []),
    ...active.map((underlying) => ({
      value: underlying.symbol,
      label: underlying.display_name && underlying.display_name !== underlying.symbol
        ? `${underlying.symbol} · ${underlying.display_name}`
        : underlying.symbol,
    })),
  ];
  return (
    <Select
      className={className}
      label="Underlying"
      value={value}
      onChange={(v) => onChange(v)}
      disabled={active.length === 0 && !value}
      options={options}
    />
  );
}

function StatusDetail({
  rfq,
  productLabelFor,
}: {
  rfq: RFQ;
  productLabelFor: (productType: string) => string;
}) {
  const fieldLabel = readString(rfq.quote_payload, 'field_label')
    ?? readString(rfq.quote_payload, 'field_path')
    ?? 'solved field';
  const solved = readNumber(rfq.quote_payload, 'solved_value');
  const price = readNumber(rfq.quote_payload, 'achieved_price');
  const response = rfq.approved_response ?? readString(rfq.quote_payload, 'client_response') ?? '';
  const quoteError = readString(rfq.quote_payload, 'quantark_error');
  const payload = rfq.request_payload ?? {};
  const currency = currencyFromPayload(payload, '');
  const summary = [
    String(payload.underlying ?? ''),
    currency,
    String(payload.side ?? ''),
    payload.quantity != null ? String(payload.quantity) : '',
    productLabelFor(String(payload.product_type ?? '')),
  ].filter(Boolean).join(' · ');

  return (
    <div className="wl-client-rfq__status-detail">
      <div className="wl-client-rfq__status-head">
        <span className="wl-client-rfq__status-id">RFQ #{rfq.id}</span>
        <Badge variant={rfqStatusBadge(rfq.status)}>{formatRfqStatus(rfq.status)}</Badge>
      </div>
      {summary && <p className="wl-client-rfq__status-summary">{summary}</p>}
      {response && <p className="wl-client-rfq__status-response">{response}</p>}
      {quoteError && <p className="wl-client-rfq__status-response wl-client-rfq__status-response--error">{quoteError}</p>}
      {(solved != null || price != null) && (
        <div className="wl-client-rfq__status-terms">
          {solved != null && (
            <div className="wl-client-rfq__status-term">
              <span>{fieldLabel}</span>
              <strong>{solved.toFixed(6)}</strong>
            </div>
          )}
          {price != null && (
            <div className="wl-client-rfq__status-term">
              <span>price</span>
              <strong>{price.toFixed(6)}</strong>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formFromTemplate(template: RfqTemplate): ClientRfqForm {
  const specs = templateSpecs(template);
  const first = specs[0] ?? null;
  return {
    underlying: '',
    currency: 'CNY',
    side: 'buy',
    notional: 1,
    quoteMode: 'solve',
    product: template.product_type,
    productTerms: cloneRecord(template.product_kwargs),
    engineSpec: cloneRecord(template.engine_spec),
    unknownField: first?.field_path ?? '',
    lowerBound: first?.lower_bound ?? null,
    upperBound: first?.upper_bound ?? null,
    initialGuess: first?.initial_guess ?? null,
    targetLabel: 'price',
    targetValue: 0,
  };
}

const CURRENCY_OPTIONS = ['CNY', 'USD', 'HKD', 'EUR', 'JPY', 'GBP'].map((code) => ({
  value: code,
  label: code,
}));

function normalizeCurrency(value: string): string {
  return value.trim().toUpperCase() || 'CNY';
}

function currencyFromPayload(payload: Record<string, unknown>, fallback: string): string {
  const market = isRecord(payload.market) ? payload.market : {};
  const product = isRecord(payload.product) ? payload.product : {};
  const productKwargs = isRecord(payload.product_kwargs) ? payload.product_kwargs : {};
  const value = market.currency ?? product.currency ?? productKwargs.currency;
  return value == null || String(value).trim() === '' ? fallback : normalizeCurrency(String(value));
}

function templateSpecs(template: RfqTemplate): RfqUnknownFieldSpec[] {
  if (template.unknown_field_specs?.length) return template.unknown_field_specs;
  return (template.unknown_fields ?? []).map((path) => ({
    field_path: path,
    label: titleFromPath(path),
    lower_bound: 0,
    upper_bound: 200,
    initial_guess: 100,
  }));
}

function titleFromPath(path: string): string {
  const tail = path.split('.').pop() ?? path;
  return tail.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const SOLVE_TARGET_FLAT_KEY: Record<string, string> = {
  'barrier_config.ko_rate': 'ko_rate',
  'coupon_config.coupon_rate': 'coupon_rate',
  'barrier_config.ki_barrier': 'ki_barrier_pct',
};

const SNOWBALL_REQUIRED_KEYS = [
  'initial_price',
  'maturity_years',
  'trade_start_date',
  'observation_frequency',
  'ko_barrier_pct',
  'ki_barrier_pct',
  'ko_rate',
  'lockup_months',
];
const REQUIRED_CONTRACT_KEYS: Record<string, string[]> = {
  SnowballOption: SNOWBALL_REQUIRED_KEYS,
  KnockOutResetSnowballOption: [...SNOWBALL_REQUIRED_KEYS, 'post_ko_barrier_pct', 'post_ko_rate'],
  PhoenixOption: [...SNOWBALL_REQUIRED_KEYS, 'coupon_barrier_pct', 'coupon_rate'],
};

function isFilled(value: unknown): boolean {
  return value !== undefined && value !== null && value !== '';
}

function isContractComplete(
  product: string,
  productTerms: Record<string, unknown>,
  quoteMode: 'solve' | 'price',
  unknownField: string,
): boolean {
  const required = REQUIRED_CONTRACT_KEYS[product];
  if (!required) return true;
  const solveKey = quoteMode === 'solve' ? SOLVE_TARGET_FLAT_KEY[unknownField] : undefined;
  return required.every((key) => key === solveKey || isFilled(productTerms[key]));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function syncTermsUnderlying(
  terms: Record<string, unknown>,
  underlying: string,
): Record<string, unknown> {
  if (!('underlying' in terms) || underlying.trim() === '') return terms;
  return { ...terms, underlying: underlying.trim() };
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

function numberOrNull(value: unknown): number | null {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseNumberInput(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  if (value == null || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return value == null ? null : String(value);
}

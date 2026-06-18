import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { SendHorizontal } from 'lucide-react';
import { api } from '../api/client';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { MetricRow } from '../components/MetricRow';
import { WizardPage } from '../components/templates';
import { ProductTermsForm } from '../components/ProductTermsForm';
import { Select } from '../components/Select';
import { Skeleton } from '../components/Skeleton';
import { BookingVisualCompanion } from '../components/BookingVisualCompanion';
import { inferProductFamily } from '../components/PositionEditForm';
import { declareActions } from '../lib/pageActions';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type { Instrument, MarketDataProfile, PageContext, PageContextReporter, Portfolio, ProductRoot } from '../types';
import './Positions.css';
import './Booking.css';

const PRODUCT_TYPES = [
  'EuropeanVanillaOption',
  'AmericanOption',
  'CashOrNothingDigitalOption',
  'BarrierOption',
  'SingleSharkfinOption',
  'DoubleSharkfinOption',
  'SnowballOption',
  'PhoenixOption',
  'AsianOption',
  'Stock',
  'Fund',
  'ETF',
  'Futures',
];

const barrierProductTypes = new Set(['SnowballOption', 'PhoenixOption']);

const CURRENCY_CODES = ['USD', 'CNY', 'EUR'] as const;

const ENGINE_OPTIONS_BY_PRODUCT: Record<string, string[]> = {
  EuropeanVanillaOption: ['BlackScholesEngine', 'EuropeanMCEngine', 'EuropeanQuadEngine', 'PDEEngine'],
  AmericanOption: ['AmericanOptionAnalyticalEngine', 'PDEEngine'],
  CashOrNothingDigitalOption: ['DigitalOptionAnalyticalEngine', 'DigitalOptionMCEngine'],
  BarrierOption: ['BarrierAnalyticalEngine', 'BarrierOptionMCEngine', 'BarrierQuadEngine', 'PDEEngine'],
  SingleSharkfinOption: ['SingleSharkfinOptionAnalyticalEngine' ],
  DoubleSharkfinOption: ['DoubleSharkfinOptionAnalyticalEngine' ],
  SnowballOption: ['SnowballQuadEngine', 'SnowballMCEngine', 'PDEEngine'],
  PhoenixOption: ['PhoenixQuadEngine', 'PhoenixMCEngine', 'PDEEngine'],
  AsianOption: ['AsianOptionAnalyticalEngine', 'AsianOptionMCEngine'],
  Stock: ['DeltaOneEngine'],
  Fund: ['DeltaOneEngine'],
  ETF: ['DeltaOneEngine'],
  Futures: ['FuturesEngine'],
};

const DEFAULT_TERMS: Record<string, Record<string, unknown>> = {
  EuropeanVanillaOption: { strike: 100, option_type: 'CALL', maturity: 1, components: [] },
  AmericanOption: { strike: 100, option_type: 'CALL', maturity: 1 },
  CashOrNothingDigitalOption: { strike: 100, option_type: 'CALL', payout: 1, maturity: 1 },
  BarrierOption: { strike: 100, option_type: 'CALL', barrier: 90, barrier_type: 'DOWN_OUT', maturity: 1 },
  SingleSharkfinOption: { strike: 100, option_type: 'CALL', participation_rate: 1, barrier: 103, maturity: 1 },
  DoubleSharkfinOption: { strike: 100, option_type: 'CALL', participation_rate: 1, upper_barrier: 103, lower_barrier: 97, maturity: 1 },
  SnowballOption: {
    initial_price: 100,
    contract_multiplier: 1,
    strike: 100,
    _otc_ki_observation_convention: 'DAILY',
    barrier_config: {
      ki_barrier: 75,
      ko_barrier: 103,
      ko_rate: 0.12,
      ko_observation_schedule: { records: [] },
      ki_observation_schedule: { records: [] },
    },
    accrual_config: {
      coupon_pay_type: 'INSTANT',
      is_annualized: true,
      is_annualized_ko: true,
      is_annualized_ki: false,
      is_annualized_rebate: true,
    },
  },
  PhoenixOption: {
    initial_price: 100,
    contract_multiplier: 1,
    strike: 100,
    _otc_ki_observation_convention: 'DAILY',
    barrier_config: {
      ki_barrier: 75,
      ko_barrier: 103,
      ko_observation_schedule: { records: [] },
      ki_observation_schedule: { records: [] },
    },
    coupon_config: { coupon_barrier: 80, coupon_rate: 0.12 },
  },
  AsianOption: { strike: 100, option_type: 'CALL', maturity: 1 },
  Stock: { deltaone_type: 'STOCK', contract_multiplier: 1 },
  Fund: { deltaone_type: 'FUND', contract_multiplier: 1 },
  ETF: { deltaone_type: 'ETF', contract_multiplier: 1 },
  Futures: { contract_code: '', multiplier: 1, basis: 0 },
};

type BookingFormState = {
  portfolioId: string;
  underlying: string;
  productType: string;
  currency: string;
  quantity: string;
  notional: string;
  entryPrice: string;
  status: string;
  tradeId: string;
  company: string;
  engineName: string;
  terms: Record<string, unknown>;
  kiBarrierPercent: string;
  koBarrierPercent: string;
};

type Props = {
  onPageContextChange?: PageContextReporter;
};

export function BookingLive({ onPageContextChange }: Props) {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [marketDataProfiles, setMarketDataProfiles] = useState<MarketDataProfile[]>([]);
  const [underlyings, setUnderlyings] = useState<Instrument[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [lastBooked, setLastBooked] = useState<{ id: number; trade_id: string; product_id?: number | null } | null>(null);
  const [form, setForm] = useState<BookingFormState>(() => ({
    portfolioId: '',
    underlying: '000300.SH',
    productType: 'SnowballOption',
    currency: 'CNY',
    quantity: '-1',
    notional: '',
    entryPrice: '0',
    status: 'open',
    tradeId: '',
    company: '',
    engineName: 'SnowballQuadEngine',
    terms: DEFAULT_TERMS.SnowballOption,
    kiBarrierPercent: '75',
    koBarrierPercent: '103',
  }));

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([
      api<Portfolio[]>('/api/portfolios'),
      api<MarketDataProfile[]>('/api/market-data/profiles'),
      api<Instrument[]>('/api/instruments'),
    ])
      .then(([rows, profiles, underlyingRows]) => {
        if (!alive) return;
        setPortfolios(rows);
        setMarketDataProfiles(profiles);
        setUnderlyings(underlyingRows);
        const firstContainer = rows.find((portfolio) => portfolio.kind === 'container');
        const activeSymbols = activeUnderlyingSymbols(underlyingRows);
        setForm((current) => {
          const nextPortfolioId = current.portfolioId || (firstContainer ? String(firstContainer.id) : '');
          const nextUnderlying = activeSymbols.length > 0 && !activeSymbols.includes(current.underlying)
            ? activeSymbols[0]
            : current.underlying;
          return {
            ...current,
            portfolioId: nextPortfolioId,
            underlying: nextUnderlying,
          };
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, []);

  const containerPortfolios = useMemo(
    () => portfolios.filter((portfolio) => portfolio.kind === 'container'),
    [portfolios],
  );
  const activeUnderlyings = useMemo(
    () => underlyings.filter((underlying) => underlying.status === 'active'),
    [underlyings],
  );
  const selectedPortfolio = useMemo(
    () => containerPortfolios.find((portfolio) => String(portfolio.id) === form.portfolioId) ?? null,
    [containerPortfolios, form.portfolioId],
  );
  const productTerms = form.terms;
  const productFamily = inferProductFamily(form.productType, productTerms);
  const latestSpot = useMemo(
    () => latestSpotForUnderlying(marketDataProfiles, form.underlying),
    [marketDataProfiles, form.underlying],
  );

  useEffect(() => {
    if (latestSpot == null) return;
    setForm((current) => {
      const nextTerms = applySpotDefaults(current.productType, current.terms, latestSpot, null);
      const patchedTerms = applyBarrierPercentAdjustment(
        current.productType,
        nextTerms,
        current.kiBarrierPercent,
        current.koBarrierPercent,
      );
      const nextPercents = computeBarrierPercentState(current.productType, patchedTerms);
      const nextNotional = syncNotionalFromTerms(patchedTerms);
      return {
        ...current,
        terms: patchedTerms,
        ...nextPercents,
        ...(nextNotional !== undefined ? { notional: nextNotional } : {}),
      };
    });
  }, [latestSpot]);

  const pageContext: PageContext = useMemo(() => ({
    route: 'booking',
    title: 'Booking',
    path: location.pathname,
    entity_ids: {
      portfolio_id: selectedPortfolio?.id ?? null,
    },
    snapshot: {
      selected_portfolio: selectedPortfolio ? {
        id: selectedPortfolio.id,
        name: selectedPortfolio.name,
        kind: selectedPortfolio.kind,
      } : null,
      draft: {
        product_type: form.productType,
        product_family: productFamily,
        underlying: form.underlying,
        active_underlying_count: activeUnderlyings.length,
        quantity: form.quantity,
      },
      last_booked: lastBooked,
    },
    loaded_context: {
      completeness: loading ? 'partial' : 'complete',
      visible_count: containerPortfolios.length,
      total_count: containerPortfolios.length,
    },
    actions: declareActions([
      {
        name: 'book_position',
        required_ids: ['portfolio_id'],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/portfolios/{portfolio_id}/positions',
      },
    ]),
    chips: ['Booking', productFamily, selectedPortfolio?.name ?? 'No portfolio'],
  }), [activeUnderlyings.length, containerPortfolios.length, form.productType, form.quantity, form.underlying, lastBooked, loading, productFamily, selectedPortfolio]);
  usePageContextReporter(pageContext, onPageContextChange);

  const update = (key: keyof BookingFormState, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const updateUnderlying = (underlying: string) => {
    const nextSpot = latestSpotForUnderlying(marketDataProfiles, underlying);
    const marketSpots = marketDataProfiles.map(spotFromProfile).filter((spot): spot is number => spot != null);
    setForm((current) => ({
      ...current,
      underlying,
      ...(() => {
        const nextTerms = applySpotDefaults(current.productType, current.terms, nextSpot, latestSpot, marketSpots);
        const patchedTerms = applyBarrierPercentAdjustment(
          current.productType,
          nextTerms,
          current.kiBarrierPercent,
          current.koBarrierPercent,
        );
        const nextNotional = syncNotionalFromTerms(patchedTerms);
        return {
          terms: patchedTerms,
          ...computeBarrierPercentState(current.productType, patchedTerms),
          ...(nextNotional !== undefined ? { notional: nextNotional } : {}),
        };
      })(),
    }));
  };

  const updateProductType = (productType: string) => {
    const defaults = DEFAULT_TERMS[productType] ?? {};
    const spot = latestSpotForUnderlying(marketDataProfiles, form.underlying);
    const nextTerms = applySpotDefaults(productType, defaults, spot, null);
    const nextNotional = syncNotionalFromTerms(nextTerms);
    setForm((current) => ({
      ...current,
      productType,
      engineName: defaultEngine(productType),
      terms: nextTerms,
      ...computeBarrierPercentState(productType, nextTerms),
      ...(nextNotional !== undefined ? { notional: nextNotional } : {}),
    }));
  };

  const updateTerms = (terms: Record<string, unknown>) => {
    setForm((current) => {
      const patchedTerms = applyBarrierPercentAdjustment(
        current.productType,
        terms,
        current.kiBarrierPercent,
        current.koBarrierPercent,
      );
      const nextNotional = syncNotionalFromTerms(patchedTerms);
      return {
        ...current,
        terms: patchedTerms,
        ...computeBarrierPercentState(current.productType, patchedTerms),
        ...(nextNotional !== undefined ? { notional: nextNotional } : {}),
      };
    });
  };

  const updateBarrierPercent = (field: 'ki' | 'ko', value: string) => {
    setForm((current) => {
      const terms = applyPercentToBarrierTerms(current.productType, current.terms, field, value);
      const percentState = computeBarrierPercentState(current.productType, terms);
      return {
        ...current,
        terms,
        ...percentState,
        [field === 'ki' ? 'kiBarrierPercent' : 'koBarrierPercent']: value,
      };
    });
  };

  const updateNotional = (value: string) => {
    setForm((current) => {
      const terms = { ...current.terms };
      const notional = parseNumeric(value);
      const initialPrice = parseNumeric(terms.initial_price);
      if (isFiniteNumber(notional) && isFiniteNumber(initialPrice) && initialPrice !== 0) {
        terms.contract_multiplier = Number((notional / initialPrice).toFixed(8));
      }
      return { ...current, notional: value, terms };
    });
  };

  const showBarrierPercentFields = barrierProductTypes.has(form.productType);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setFeedback(null);

    if (!selectedPortfolio) {
      setError('Choose a container portfolio before booking.');
      return;
    }
    if (!activeUnderlyings.some((underlying) => underlying.symbol === form.underlying)) {
      setError('Choose an active underlying before booking.');
      return;
    }

    const quantity = Number(form.quantity);
    const entryPrice = Number(form.entryPrice);
    if (!Number.isFinite(quantity)) {
      setError('Quantity must be a valid number.');
      return;
    }
    if (!Number.isFinite(entryPrice)) {
      setError('Entry price must be a valid number.');
      return;
    }
    const product = buildProductRoot({
      productType: form.productType,
      productFamily,
      underlying: form.underlying,
      currency: form.currency,
      terms: productTerms,
    });

    const notional = Number(form.notional);
    const payloadProductKwargs = Number.isFinite(notional) && notional > 0
      ? { ...productTerms, notional }
      : { ...productTerms };

    const payload = {
      underlying: form.underlying,
      product_type: form.productType,
      product_kwargs: payloadProductKwargs,
      product,
      engine_name: form.engineName || defaultEngine(form.productType),
      engine_kwargs: {},
      quantity,
      entry_price: entryPrice,
      status: form.status,
      source_trade_id: form.tradeId || undefined,
      company: form.company || undefined,
    };

    setSaving(true);
    try {
      const response = await api<Portfolio>(`/api/portfolios/${selectedPortfolio.id}/positions`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const booked = response.positions.at(-1);
      setLastBooked(booked ? {
        id: booked.id,
        trade_id: booked.source_trade_id ?? `#${booked.id}`,
        product_id: booked.product_id,
      } : null);
      setFeedback(booked ? `Booked ${booked.source_trade_id ?? `#${booked.id}`}` : 'Position booked.');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={120} />
        <div style={{ height: 12 }} />
        <Skeleton height={300} />
      </div>
    );
  }

  if (containerPortfolios.length === 0) {
    return <Empty message="Create a container portfolio before booking positions." symbol="∅" />;
  }

  return (
    <WizardPage
      title="BOOKING"
      chips={[selectedPortfolio?.name ?? '—', productFamily, form.productType]}
      footer={
        <Button type="submit" form="booking-form" variant="primary" disabled={saving}>
          <SendHorizontal size={16} aria-hidden="true" />
          {saving ? 'Booking...' : 'Book Position'}
        </Button>
      }
    >
      <div className="wl-booking__shell">
        <div className="wl-booking__left">
          <MetricRow
            metrics={[
              { label: 'Target', value: selectedPortfolio?.name ?? '—' },
              { label: 'Family', value: productFamily },
              { label: 'Product', value: form.productType },
              { label: 'Quantity', value: form.quantity || '—' },
            ]}
          />
          {feedback && <div className="wl-positions__feedback" role="status">{feedback}</div>}
          {error && <div className="wl-positions__ticket-error" role="alert">{error}</div>}
          <form id="booking-form" className="wl-positions__detail" onSubmit={handleSubmit}>
            <section className="wl-positions__detail-section">
              <h4>Booking Ticket</h4>
              <div className="wl-booking__ticket-grid">
                <Select
                  label="Portfolio"
                  className="wl-positions__term-field"
                  value={form.portfolioId}
                  onChange={(v) => update('portfolioId', v)}
                  options={containerPortfolios.map((portfolio) => ({
                    value: String(portfolio.id),
                    label: portfolio.name,
                  }))}
                />
                <Select
                  label="Underlying"
                  className="wl-positions__term-field"
                  value={activeUnderlyings.some((underlying) => underlying.symbol === form.underlying) ? form.underlying : ''}
                  onChange={(v) => updateUnderlying(v)}
                  disabled={activeUnderlyings.length === 0}
                  options={activeUnderlyings.length === 0
                    ? [{ value: '', label: 'No active underlyings' }]
                    : activeUnderlyings.map((underlying) => ({
                        value: underlying.symbol,
                        label: underlying.symbol + (underlying.display_name && underlying.display_name !== underlying.symbol ? ` · ${underlying.display_name}` : ''),
                      }))
                  }
                />
                <Select
                  label="Product Type"
                  className="wl-positions__term-field"
                  value={form.productType}
                  onChange={(v) => updateProductType(v)}
                  options={PRODUCT_TYPES.map((productType) => ({
                    value: productType,
                    label: productType,
                  }))}
                />
                <Select
                  label="Currency"
                  className="wl-positions__term-field"
                  value={form.currency}
                  onChange={(v) => update('currency', v)}
                  options={CURRENCY_CODES.map((currency) => ({
                    value: currency,
                    label: currency,
                  }))}
                />
                <label className="wl-positions__term-field">
                  <span>Quantity</span>
                  <input type="number" step="any" value={form.quantity} onChange={(event) => update('quantity', event.target.value)} />
                </label>
                <label className="wl-positions__term-field">
                  <span>Entry Price</span>
                  <input type="number" step="any" value={form.entryPrice} onChange={(event) => update('entryPrice', event.target.value)} />
                </label>
                <label className="wl-positions__term-field">
                  <span>Notional</span>
                  <input type="number" step="any" value={form.notional} onChange={(event) => updateNotional(event.target.value)} />
                </label>
                <Select
                  label="Status"
                  className="wl-positions__term-field"
                  value={form.status}
                  onChange={(v) => update('status', v)}
                  options={[
                    { value: 'open', label: 'open' },
                    { value: 'knocked_in', label: 'knocked_in' },
                    { value: 'closed', label: 'closed' },
                  ]}
                />
                <label className="wl-positions__term-field">
                  <span>Trade ID</span>
                  <input value={form.tradeId} onChange={(event) => update('tradeId', event.target.value)} />
                </label>
                <label className="wl-positions__term-field">
                  <span>Company</span>
                  <input value={form.company} onChange={(event) => update('company', event.target.value)} />
                </label>
                <Select
                  label="Engine"
                  className="wl-positions__term-field"
                  value={form.engineName}
                  onChange={(v) => update('engineName', v)}
                  placeholder="—"
                  options={[
                    { value: '', label: '—' },
                    ...(ENGINE_OPTIONS_BY_PRODUCT[form.productType] ?? []).map((engine) => ({
                      value: engine,
                      label: engine,
                    })),
                  ]}
                />
                {showBarrierPercentFields ? (
                  <>
                    <label className="wl-positions__term-field">
                      <span>KI Barrier %</span>
                      <input
                        type="number"
                        step="any"
                        value={form.kiBarrierPercent}
                        onChange={(event) => updateBarrierPercent('ki', event.target.value)}
                      />
                    </label>
                    <label className="wl-positions__term-field">
                      <span>KO Barrier %</span>
                      <input
                        type="number"
                        step="any"
                        value={form.koBarrierPercent}
                        onChange={(event) => updateBarrierPercent('ko', event.target.value)}
                      />
                    </label>
                  </>
                ) : null}
              </div>
            </section>
            <section className="wl-positions__detail-section">
              <h4>Product Terms</h4>
              <ProductTermsForm
                productType={form.productType}
                productKwargs={productTerms}
                onChange={updateTerms}
              />
            </section>
          </form>
        </div>
        <div className="wl-booking__right">
          <BookingVisualCompanion
            productType={form.productType}
            productFamily={productFamily}
            terms={productTerms}
            quantity={form.quantity}
            entryPrice={form.entryPrice}
            underlying={form.underlying}
            currency={form.currency}
          />
        </div>
      </div>
    </WizardPage>
  );
}

function activeUnderlyingSymbols(rows: Instrument[]): string[] {
  return rows
    .filter((underlying) => underlying.status === 'active')
    .map((underlying) => underlying.symbol);
}

function buildProductRoot({
  productType,
  productFamily,
  underlying,
  currency,
  terms,
}: {
  productType: string;
  productFamily: string;
  underlying: string;
  currency: string;
  terms: Record<string, unknown>;
}): Omit<ProductRoot, 'id'> {
  const components = Array.isArray(terms.components)
    ? terms.components.filter((component): component is Record<string, unknown> => (
      component !== null && typeof component === 'object' && !Array.isArray(component)
    ))
    : [];
  return {
    asset_class: 'equity',
    product_family: productFamily,
    quantark_class: productType,
    underlying,
    currency,
    terms,
    components,
  };
}

function defaultEngine(productType: string): string {
  if (productType === 'SnowballOption') return 'SnowballQuadEngine';
  if (productType === 'PhoenixOption') return 'PhoenixQuadEngine';
  if (productType === 'BarrierOption') return 'BarrierAnalyticalEngine';
  if (productType === 'AsianOption') return 'AsianOptionAnalyticalEngine';
  if (['Stock', 'Fund', 'ETF'].includes(productType)) return 'DeltaOneEngine';
  if (productType === 'Futures') return 'FuturesEngine';
  return 'BlackScholesEngine';
}

function applySpotDefaults(
  productType: string,
  terms: Record<string, unknown>,
  spot: number | null,
  previousSpot: number | null,
  autoFilledSpots: number[] = [],
): Record<string, unknown> {
  if (spot == null || !shouldUseSpotDefaults(productType, terms)) return terms;
  const nextTerms = { ...terms };
  let changed = false;
  for (const key of ['initial_price', 'strike'] as const) {
    if (shouldReplaceSpotDefault(nextTerms[key], previousSpot, autoFilledSpots)) {
      nextTerms[key] = spot;
      changed = true;
    }
  }
  return changed ? nextTerms : terms;
}

function shouldUseSpotDefaults(productType: string, terms: Record<string, unknown>): boolean {
  return productType in DEFAULT_TERMS && ('initial_price' in terms || 'strike' in terms);
}

function computeBarrierPercentState(
  productType: string,
  terms: Record<string, unknown>,
): Pick<BookingFormState, 'kiBarrierPercent' | 'koBarrierPercent'> {
  if (!barrierProductTypes.has(productType)) {
    return { kiBarrierPercent: '', koBarrierPercent: '' };
  }
  const config = getBarrierConfig(terms);
  if (!config) {
    return { kiBarrierPercent: '', koBarrierPercent: '' };
  }
  const initialPrice = parseNumeric(terms.initial_price);
  return {
    kiBarrierPercent: percentFromBarrier(initialPrice, parseNumeric(config.ki_barrier)),
    koBarrierPercent: percentFromBarrier(initialPrice, parseNumeric(config.ko_barrier)),
  };
}

function applyBarrierPercentAdjustment(
  productType: string,
  terms: Record<string, unknown>,
  kiBarrierPercent: string,
  koBarrierPercent: string,
): Record<string, unknown> {
  if (!barrierProductTypes.has(productType)) return terms;
  const initialPrice = parseNumeric(terms.initial_price);
  if (!isFiniteNumber(initialPrice) || initialPrice === 0) return terms;

  const config = getBarrierConfig(terms);
  if (!config) return terms;

  const nextConfig = { ...config };
  let changed = false;
  const nextKiBarrier = applyBarrierPercent(initialPrice, parseNumeric(kiBarrierPercent));
  if (isFiniteNumber(nextKiBarrier)) {
    if (nextConfig.ki_barrier !== nextKiBarrier) {
      nextConfig.ki_barrier = nextKiBarrier;
      changed = true;
    }
  }
  const nextKoBarrier = applyBarrierPercent(initialPrice, parseNumeric(koBarrierPercent));
  if (isFiniteNumber(nextKoBarrier)) {
    if (nextConfig.ko_barrier !== nextKoBarrier) {
      nextConfig.ko_barrier = nextKoBarrier;
      changed = true;
    }
  }
  return changed ? { ...terms, barrier_config: nextConfig } : terms;
}

function applyPercentToBarrierTerms(
  productType: string,
  terms: Record<string, unknown>,
  field: 'ki' | 'ko',
  value: string,
): Record<string, unknown> {
  if (!barrierProductTypes.has(productType)) return terms;
  const config = getBarrierConfig(terms);
  if (!config) return terms;
  const parsedPercent = parseNumeric(value);
  if (!isFiniteNumber(parsedPercent)) {
    return terms;
  }
  const initialPrice = parseNumeric(terms.initial_price);
  if (!isFiniteNumber(initialPrice) || initialPrice === 0) return terms;
  const target = initialPrice * parsedPercent / 100;
  const nextConfig = {
    ...config,
    ...(field === 'ki' ? { ki_barrier: target } : { ko_barrier: target }),
  };
  return { ...terms, barrier_config: nextConfig };
}

function getBarrierConfig(terms: Record<string, unknown>): Record<string, unknown> | null {
  const config = terms.barrier_config;
  if (config == null || typeof config !== 'object' || Array.isArray(config)) return null;
  return config as Record<string, unknown>;
}

function percentFromBarrier(initialPrice: number | null, barrier: number | null): string {
  if (!isFiniteNumber(initialPrice) || !isFiniteNumber(barrier) || initialPrice === 0) return '';
  return formatPercent((barrier / initialPrice) * 100);
}

function applyBarrierPercent(initialPrice: number | null, percent: number | null): number | null {
  if (!isFiniteNumber(initialPrice) || !isFiniteNumber(percent)) return null;
  return initialPrice * percent / 100;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function parseNumeric(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatPercent(value: number): string {
  return String(Number(value.toFixed(8)));
}

function syncNotionalFromTerms(terms: Record<string, unknown>): string | undefined {
  const initialPrice = parseNumeric(terms.initial_price);
  const contractMultiplier = parseNumeric(terms.contract_multiplier);
  if (isFiniteNumber(initialPrice) && isFiniteNumber(contractMultiplier) && initialPrice !== 0) {
    return String(Number((initialPrice * contractMultiplier).toFixed(8)));
  }
  return undefined;
}

function shouldReplaceSpotDefault(value: unknown, previousSpot: number | null, autoFilledSpots: number[]): boolean {
  if (value === undefined || value === null || value === '' || value === 100) return true;
  const numberValue = Number(value);
  return (previousSpot != null && numberValue === previousSpot) || autoFilledSpots.includes(numberValue);
}

function latestSpotForUnderlying(profiles: MarketDataProfile[], underlying: string): number | null {
  const candidates = normalizedSymbolCandidates(underlying);
  for (const profile of profiles) {
    if (!candidates.has(normalizeSymbol(profile.symbol))) continue;
    const spot = spotFromProfile(profile);
    if (spot != null) return spot;
  }
  return null;
}

function normalizedSymbolCandidates(symbol: string): Set<string> {
  const normalized = normalizeSymbol(symbol);
  const withoutExchange = normalized.replace(/\.(SH|SZ)$/u, '');
  return new Set([normalized, withoutExchange]);
}

function normalizeSymbol(symbol: string): string {
  return symbol.trim().toUpperCase();
}

function spotFromProfile(profile: MarketDataProfile): number | null {
  const data = profile.data ?? {};
  const raw = data.spot ?? data.latest?.close;
  const spot = Number(raw);
  return Number.isFinite(spot) ? spot : null;
}

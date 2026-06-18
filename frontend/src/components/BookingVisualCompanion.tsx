import { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { TrendingUp, TrendingDown, ShieldAlert, Info } from 'lucide-react';
import './BookingVisualCompanion.css';

type PayoffPoint = {
  spot: number;
  spotLabel: string;
  [key: string]: number | string;
};

type PayoffResult = {
  series: PayoffPoint[];
  scenarios: string[];
  description: string;
  bullets: string[];
  references: { key: string; label: string; value: number; type: 'strike' | 'barrier' | 'entry' }[];
};

type Props = {
  productType: string;
  productFamily: string;
  terms: Record<string, unknown>;
  quantity: string;
  entryPrice: string;
  underlying: string;
  currency: string;
};

export function BookingVisualCompanion({
  productType,
  productFamily,
  terms,
  quantity,
  entryPrice,
  underlying,
  currency,
}: Props) {
  const payoff = useMemo(
    () => computePayoff(productType, terms, quantity, entryPrice, currency),
    [productType, terms, quantity, entryPrice, currency],
  );

  const scenarioColors = ['var(--ink)', 'var(--warn)', 'var(--info)'];
  const hasData = payoff.series.length > 0;

  return (
    <div className="wl-booking-companion">
      <header className="wl-booking-companion__head">
        <div className="wl-booking-companion__title">
          <TrendingUp size={16} aria-hidden="true" />
          <span>Visual Companion</span>
        </div>
        <div className="wl-booking-companion__meta">
          <span>{productType}</span>
          <span>·</span>
          <span>{productFamily}</span>
        </div>
      </header>

      <div className="wl-booking-companion__body">
        <div className="wl-booking-companion__narrative">
          <p className="wl-booking-companion__lead">{payoff.description}</p>
          {payoff.bullets.length > 0 && (
            <ul className="wl-booking-companion__bullets">
              {payoff.bullets.map((bullet, index) => (
                <li key={index}>
                  <Info size={12} aria-hidden="true" />
                  <span>{bullet}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {hasData ? (
          <div className="wl-booking-companion__chart-wrap">
            <div className="wl-booking-companion__chart-head">
              <span>Terminal Payoff</span>
              <span className="wl-booking-companion__unit">
                {currency} · qty {quantity || '0'}
              </span>
            </div>
            <div className="wl-booking-companion__canvas">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={payoff.series} margin={{ top: 24, right: 16, bottom: 8, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
                  <XAxis
                    dataKey="spotLabel"
                    tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }}
                    tickMargin={6}
                  />
                  <YAxis
                    tick={{ fill: 'var(--ink-2)', fontSize: 10, fontFamily: 'var(--font-numeric)' }}
                    tickFormatter={(value: number) => formatCompact(value)}
                  />
                  <Tooltip
                    content={(
                      <CustomTooltip
                        currency={currency}
                        scenarios={payoff.scenarios}
                        colors={scenarioColors}
                      />
                    )}
                  />
                  {payoff.references.map((ref) => (
                    <ReferenceLine
                      key={ref.key}
                      x={snapToClosestSpot(ref.value, payoff.series)}
                      stroke={referenceColor(ref.type)}
                      strokeDasharray={ref.type === 'strike' ? '4 4' : '2 2'}
                      label={{
                        value: ref.label,
                        position: 'top',
                        fill: 'var(--ink-2)',
                        fontSize: 10,
                        fontFamily: 'var(--font-numeric)',
                      }}
                    />
                  ))}
                  {payoff.scenarios.map((scenario, index) => (
                    <Line
                      key={scenario}
                      type="monotone"
                      dataKey={scenario}
                      name={scenario}
                      stroke={scenarioColors[index % scenarioColors.length]}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <div className="wl-booking-companion__empty">
            <ShieldAlert size={24} aria-hidden="true" />
            <span>No payoff preview available for this product configuration.</span>
          </div>
        )}

        <div className="wl-booking-companion__summary">
          <SummaryCard
            label="Underlying"
            value={underlying}
            tone="neutral"
          />
          <SummaryCard
            label="Quantity"
            value={quantity || '0'}
            tone={Number(quantity) < 0 ? 'neg' : 'pos'}
          />
          <SummaryCard
            label="Entry Price"
            value={entryPrice || '0'}
            tone="neutral"
          />
          <SummaryCard
            label="Reference"
            value={formatPrice(referencePrice(terms))}
            tone="neutral"
          />
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone: 'pos' | 'neg' | 'neutral';
}) {
  const Icon = tone === 'pos' ? TrendingUp : tone === 'neg' ? TrendingDown : Info;
  return (
    <div className={`wl-booking-companion__card wl-booking-companion__card--${tone}`}>
      <span className="wl-booking-companion__card-label">{label}</span>
      <span className="wl-booking-companion__card-value">
        <Icon size={12} aria-hidden="true" />
        {value}
      </span>
    </div>
  );
}

function CustomTooltip({
  active,
  payload,
  label,
  currency,
  scenarios,
  colors,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
  currency: string;
  scenarios: string[];
  colors: string[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="wl-booking-companion__tooltip">
      <div className="wl-booking-companion__tooltip-title">Spot {label}</div>
      {scenarios.map((scenario, index) => {
        const point = payload.find((p) => p.name === scenario);
        if (point == null) return null;
        return (
          <div key={scenario} className="wl-booking-companion__tooltip-row">
            <span
              className="wl-booking-companion__tooltip-dot"
              style={{ background: colors[index % colors.length] }}
            />
            <span className="wl-booking-companion__tooltip-scenario">{scenario}</span>
            <span className="wl-booking-companion__tooltip-value">
              {currency} {formatNumber(point.value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function computePayoff(
  productType: string,
  terms: Record<string, unknown>,
  quantityRaw: string,
  entryPriceRaw: string,
  currency: string,
): PayoffResult {
  const quantity = Number(quantityRaw);
  const qty = Number.isFinite(quantity) ? quantity : 0;
  const entryPrice = Number(entryPriceRaw);

  const initialPrice = numeric(terms.initial_price) ?? numeric(terms.strike) ?? 100;
  const strike = numeric(terms.strike) ?? initialPrice;
  const optionType = String(terms.option_type ?? 'CALL').toUpperCase();

  const spots = generateSpotRange(initialPrice || 100);
  const spotLabels = new Map(spots.map((s) => [s, formatSpot(s)]));

  const base: PayoffResult = {
    series: [],
    scenarios: ['Payoff'],
    description: `A ${productType} position on a reference spot of ${formatPrice(initialPrice)}.`,
    bullets: [],
    references: [],
  };

  switch (productType) {
    case 'EuropeanVanillaOption':
    case 'AmericanOption':
    case 'AsianOption': {
      const description =
        optionType === 'CALL'
          ? 'Long call option. Gains when the underlying rises above the strike, with payoff increasing one-for-one.'
          : 'Long put option. Gains when the underlying falls below the strike, with payoff increasing one-for-one.';
      return {
        ...base,
        description,
        bullets: [`Strike at ${formatPrice(strike)}.`, `Quantity ${qty > 0 ? '+' : ''}${qty}.`],
        references: [{ key: 'strike', label: 'K', value: strike, type: 'strike' }],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: vanillaPayoff(spot, strike, optionType, qty),
        })),
      };
    }

    case 'CashOrNothingDigitalOption': {
      const payout = numeric(terms.payout) ?? 1;
      const description =
        optionType === 'CALL'
          ? `Cash-or-nothing digital call. Pays a fixed ${formatPrice(payout)} if spot finishes above the strike, otherwise zero.`
          : `Cash-or-nothing digital put. Pays a fixed ${formatPrice(payout)} if spot finishes below the strike, otherwise zero.`;
      return {
        ...base,
        description,
        bullets: [`Fixed payout ${formatPrice(payout)} ${currency}.`, `Strike at ${formatPrice(strike)}.`],
        references: [{ key: 'strike', label: 'K', value: strike, type: 'strike' }],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: digitalPayoff(spot, strike, payout, optionType, qty),
        })),
      };
    }

    case 'BarrierOption': {
      const barrier = numeric(terms.barrier) ?? initialPrice * 1.03;
      const barrierType = String(terms.barrier_type ?? 'DOWN_OUT').toUpperCase();
      const rebate = numeric(terms.rebate) ?? 0;
      const description = `Barrier option (${barrierType.replace(/_/g, ' ').toLowerCase()}). Payoff determined by the terminal spot relative to the barrier at ${formatPrice(barrier)}.`;
      return {
        ...base,
        description,
        bullets: [
          `Barrier at ${formatPrice(barrier)}.`,
          `Rebate ${formatPrice(rebate)} if the barrier knocks the option out.`,
          `${optionType} strike at ${formatPrice(strike)}.`,
        ],
        references: [
          { key: 'strike', label: 'K', value: strike, type: 'strike' },
          { key: 'barrier', label: 'B', value: barrier, type: 'barrier' },
        ],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: barrierPayoff(spot, strike, barrier, barrierType, rebate, optionType, qty),
        })),
      };
    }

    case 'SingleSharkfinOption': {
      const barrier = numeric(terms.barrier) ?? initialPrice * 1.03;
      const participation = numeric(terms.participation_rate) ?? 1;
      const description = `Single sharkfin call. Participation rate of ${participation}x on the upside, capped at the barrier.`;
      return {
        ...base,
        description,
        bullets: [
          `Strike ${formatPrice(strike)}, barrier ${formatPrice(barrier)}.`,
          `Participation rate ${participation}x.`,
        ],
        references: [
          { key: 'strike', label: 'K', value: strike, type: 'strike' },
          { key: 'barrier', label: 'Cap', value: barrier, type: 'barrier' },
        ],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: singleSharkfinPayoff(spot, strike, barrier, participation, optionType, qty),
        })),
      };
    }

    case 'DoubleSharkfinOption': {
      const upperBarrier = numeric(terms.upper_barrier) ?? initialPrice * 1.03;
      const lowerBarrier = numeric(terms.lower_barrier) ?? initialPrice * 0.97;
      const participation = numeric(terms.participation_rate) ?? 1;
      const description = `Double sharkfin call. Captures moves in either direction up to the barriers, with ${participation}x participation.`;
      return {
        ...base,
        description,
        bullets: [
          `Strike ${formatPrice(strike)}, barriers ${formatPrice(lowerBarrier)} / ${formatPrice(upperBarrier)}.`,
          `Participation rate ${participation}x.`,
        ],
        references: [
          { key: 'strike', label: 'K', value: strike, type: 'strike' },
          { key: 'upper', label: 'U', value: upperBarrier, type: 'barrier' },
          { key: 'lower', label: 'L', value: lowerBarrier, type: 'barrier' },
        ],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: doubleSharkfinPayoff(spot, strike, upperBarrier, lowerBarrier, participation, optionType, qty),
        })),
      };
    }

    case 'SnowballOption': {
      const barrierConfig = record(terms.barrier_config);
      const koBarrier = numeric(barrierConfig?.ko_barrier) ?? initialPrice * 1.03;
      const kiBarrier = numeric(barrierConfig?.ki_barrier) ?? initialPrice * 0.75;
      const koRate = numeric(barrierConfig?.ko_rate) ?? 0.12;
      const notional = Math.abs(qty) || 1;
      const sideFactor = qty < 0 ? -1 : 1;
      const description = `Snowball autocall. If the underlying stays above the KO barrier, the position redeems early with coupon. At maturity, the payoff depends on whether the KI barrier was touched.`;
      return {
        ...base,
        description,
        bullets: [
          `KO barrier ${formatPrice(koBarrier)} · coupon ${(koRate * 100).toFixed(1)}%.`,
          `KI barrier ${formatPrice(kiBarrier)}.`,
          `Quantity ${qty > 0 ? '+' : ''}${qty}.`,
        ],
        references: [
          { key: 'ko', label: 'KO', value: koBarrier, type: 'barrier' },
          { key: 'ki', label: 'KI', value: kiBarrier, type: 'barrier' },
          { key: 'strike', label: 'K', value: strike, type: 'strike' },
        ],
        scenarios: ['No KI', 'KI hit'],
        series: spots.map((spot) => {
          const noKi = snowballMaturityPayoff(spot, strike, kiBarrier, koRate, false, notional, sideFactor);
          const kiHit = snowballMaturityPayoff(spot, strike, kiBarrier, koRate, true, notional, sideFactor);
          return {
            spot,
            spotLabel: spotLabels.get(spot)!,
            'No KI': noKi,
            'KI hit': kiHit,
          };
        }),
      };
    }

    case 'PhoenixOption': {
      const barrierConfig = record(terms.barrier_config);
      const couponConfig = record(terms.coupon_config);
      const koBarrier = numeric(barrierConfig?.ko_barrier) ?? initialPrice * 1.03;
      const kiBarrier = numeric(barrierConfig?.ki_barrier) ?? initialPrice * 0.75;
      const couponBarrier = numeric(couponConfig?.coupon_barrier) ?? initialPrice * 0.8;
      const couponRate = numeric(couponConfig?.coupon_rate) ?? 0.12;
      const notional = Math.abs(qty) || 1;
      const sideFactor = qty < 0 ? -1 : 1;
      const description = `Phoenix autocall. Pays a periodic coupon when the underlying closes above the coupon barrier. Redemption at maturity depends on KI and final spot.`;
      return {
        ...base,
        description,
        bullets: [
          `KO ${formatPrice(koBarrier)} · coupon barrier ${formatPrice(couponBarrier)} · coupon ${(couponRate * 100).toFixed(1)}%.`,
          `KI barrier ${formatPrice(kiBarrier)}.`,
        ],
        references: [
          { key: 'ko', label: 'KO', value: koBarrier, type: 'barrier' },
          { key: 'coupon', label: 'C', value: couponBarrier, type: 'barrier' },
          { key: 'ki', label: 'KI', value: kiBarrier, type: 'barrier' },
          { key: 'strike', label: 'K', value: strike, type: 'strike' },
        ],
        scenarios: ['No KI', 'KI hit'],
        series: spots.map((spot) => {
          const noKi = phoenixMaturityPayoff(spot, strike, kiBarrier, couponBarrier, couponRate, false, notional, sideFactor);
          const kiHit = phoenixMaturityPayoff(spot, strike, kiBarrier, couponBarrier, couponRate, true, notional, sideFactor);
          return {
            spot,
            spotLabel: spotLabels.get(spot)!,
            'No KI': noKi,
            'KI hit': kiHit,
          };
        }),
      };
    }

    case 'Stock':
    case 'Fund':
    case 'ETF': {
      const ref = entryPrice && entryPrice > 0 ? entryPrice : initialPrice;
      const description = `Delta-one ${productType.toLowerCase()} position. Payoff moves one-for-one with the underlying price change from the reference level.`;
      return {
        ...base,
        description,
        bullets: [
          `Reference price ${formatPrice(ref)}.`,
          `Quantity ${qty > 0 ? '+' : ''}${qty}.`,
        ],
        references: [{ key: 'entry', label: 'Ref', value: ref, type: 'entry' }],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: (spot - ref) * qty,
        })),
      };
    }

    case 'Futures': {
      const basis = numeric(terms.basis) ?? 0;
      const multiplier = numeric(terms.multiplier) ?? 1;
      const ref = entryPrice && entryPrice > 0 ? entryPrice : initialPrice;
      const description = `Futures position. Linear payoff driven by the underlying price change from the reference, scaled by multiplier ${multiplier}.`;
      return {
        ...base,
        description,
        bullets: [
          `Reference ${formatPrice(ref)}, basis ${formatPrice(basis)}, multiplier ${multiplier}.`,
          `Quantity ${qty > 0 ? '+' : ''}${qty}.`,
        ],
        references: [{ key: 'entry', label: 'Ref', value: ref, type: 'entry' }],
        series: spots.map((spot) => ({
          spot,
          spotLabel: spotLabels.get(spot)!,
          Payoff: (spot - ref - basis) * multiplier * qty,
        })),
      };
    }

    default:
      return {
        ...base,
        description: `${productType} is configured. Add the required terms to enable the payoff preview.`,
      };
  }
}

function vanillaPayoff(spot: number, strike: number, optionType: string, qty: number): number {
  const intrinsic = optionType === 'CALL' ? Math.max(0, spot - strike) : Math.max(0, strike - spot);
  return intrinsic * qty;
}

function digitalPayoff(
  spot: number,
  strike: number,
  payout: number,
  optionType: string,
  qty: number,
): number {
  const itm = optionType === 'CALL' ? spot > strike : spot < strike;
  return (itm ? payout : 0) * qty;
}

function barrierPayoff(
  spot: number,
  strike: number,
  barrier: number,
  barrierType: string,
  rebate: number,
  optionType: string,
  qty: number,
): number {
  const knocked = barrierType.startsWith('UP')
    ? spot >= barrier
    : spot <= barrier;
  const isOut = barrierType.endsWith('_OUT');
  if (isOut && knocked) return rebate * qty;
  if (!isOut && !knocked) return rebate * qty;
  return vanillaPayoff(spot, strike, optionType, qty);
}

function singleSharkfinPayoff(
  spot: number,
  strike: number,
  barrier: number,
  participation: number,
  optionType: string,
  qty: number,
): number {
  if (optionType !== 'CALL') return 0;
  const gain = Math.max(0, Math.min(spot - strike, barrier - strike));
  return gain * participation * qty;
}

function doubleSharkfinPayoff(
  spot: number,
  strike: number,
  upper: number,
  lower: number,
  participation: number,
  optionType: string,
  qty: number,
): number {
  if (optionType !== 'CALL') return 0;
  const upGain = Math.max(0, Math.min(spot - strike, upper - strike));
  const downGain = Math.max(0, Math.min(strike - spot, strike - lower));
  return (upGain + downGain) * participation * qty;
}

function snowballMaturityPayoff(
  spot: number,
  strike: number,
  kiBarrier: number,
  koRate: number,
  kiHit: boolean,
  notional: number,
  sideFactor: number,
): number {
  const coupon = koRate * notional;
  if (spot >= strike) {
    return sideFactor * coupon;
  }
  if (!kiHit || spot >= kiBarrier) {
    return 0;
  }
  const loss = (spot / strike - 1) * notional;
  return sideFactor * (loss + coupon);
}

function phoenixMaturityPayoff(
  spot: number,
  strike: number,
  kiBarrier: number,
  couponBarrier: number,
  couponRate: number,
  kiHit: boolean,
  notional: number,
  sideFactor: number,
): number {
  const coupon = couponRate * notional;
  if (spot >= couponBarrier) {
    return sideFactor * coupon;
  }
  if (!kiHit || spot >= kiBarrier) {
    return 0;
  }
  const loss = (spot / strike - 1) * notional;
  return sideFactor * (loss + coupon);
}

function generateSpotRange(center: number): number[] {
  const ref = center && center > 0 ? center : 100;
  const steps = 41;
  const min = ref * 0.5;
  const max = ref * 1.5;
  const step = (max - min) / (steps - 1);
  return Array.from({ length: steps }, (_, i) => min + step * i);
}

function referencePrice(terms: Record<string, unknown>): number {
  return numeric(terms.initial_price) ?? numeric(terms.strike) ?? 100;
}

function referenceColor(type: 'strike' | 'barrier' | 'entry'): string {
  if (type === 'strike') return 'var(--info)';
  if (type === 'barrier') return 'var(--warn)';
  return 'var(--ink-2)';
}

function numeric(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function snapToClosestSpot(value: number, series: PayoffPoint[]): string {
  if (series.length === 0) return formatSpot(value);
  let closest = series[0];
  let minDiff = Math.abs(value - closest.spot);
  for (const point of series) {
    const diff = Math.abs(value - point.spot);
    if (diff < minDiff) {
      minDiff = diff;
      closest = point;
    }
  }
  return closest.spotLabel;
}

function formatSpot(value: number): string {
  if (value === 0) return '0';
  if (Math.abs(value) >= 10000) return `${(value / 1000).toFixed(1)}k`;
  if (Math.abs(value) >= 1) return value.toFixed(1);
  return value.toFixed(3);
}

function formatCompact(value: number): string {
  if (!Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)}k`;
  if (Math.abs(value) >= 1) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value.toFixed(4);
}

function formatPrice(value: number): string {
  if (!Number.isFinite(value)) return '—';
  const rounded = Number(value.toFixed(4));
  return rounded.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

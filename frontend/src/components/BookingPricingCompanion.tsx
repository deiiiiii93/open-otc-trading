import { useEffect, useState } from 'react';
import { Calculator } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { NumberInput } from './NumberInput';
import { Tile } from './Tile';
import type { PricingPreviewOut } from '../types';
import './BookingPricingCompanion.css';

type Props = {
  productType: string;
  productFamily: string;
  terms: Record<string, unknown>;
  engineName: string;
  underlying: string;
  currency: string;
  latestSpot: number | null;
  quantity: string;
};

// The preview endpoint prices ONE LONG UNIT of the position (QuantArk has no concept
// of buy/sell); direction and size live in the signed booking quantity. Scale the
// per-unit PV and every greek by it: Price of Position = quantity × price of one unit
// (a sell, quantity < 0, flips the sign of PV and all greeks alike). A blank/non-numeric
// quantity field falls back to factor 1 so we show the honest per-unit value rather than 0/NaN.
function scaleByQuantity(result: PricingPreviewOut, quantity: string): PricingPreviewOut {
  const parsed = Number(quantity);
  const factor = quantity.trim() !== '' && Number.isFinite(parsed) ? parsed : 1;
  if (factor === 1) return result;
  const g = result.greeks;
  return {
    ...result,
    price: result.price * factor,
    greeks: g
      ? {
        delta: g.delta * factor,
        gamma: g.gamma * factor,
        vega: g.vega * factor,
        theta: g.theta * factor,
        rho: g.rho * factor,
        rho_q: g.rho_q * factor,
      }
      : g,
  };
}

type MarketInputs = {
  spot: string;
  rate: string;
  volatility: string;
  dividendYield: string;
  valuationDate: string;
};

type UnderlyingPricingDefaultRow = {
  underlying: string;
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
};

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function str(value: number | null | undefined, fallback: string): string {
  return value == null || !Number.isFinite(value) ? fallback : String(value);
}

export function BookingPricingCompanion({
  productType, terms, engineName, underlying, currency, latestSpot, quantity,
}: Props) {
  const [inputs, setInputs] = useState<MarketInputs>({
    spot: str(latestSpot, '100'),
    rate: '0.03',
    volatility: '0.2',
    dividendYield: '0',
    valuationDate: today(),
  });
  const [pricing, setPricing] = useState(false);
  const [result, setResult] = useState<PricingPreviewOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-pre-fill only when the underlying itself changes (preserve user edits
  // across term tweaks). Spot follows latestSpot for the selected underlying.
  useEffect(() => {
    let alive = true;
    setInputs((cur) => ({ ...cur, spot: str(latestSpot, cur.spot) }));
    api<UnderlyingPricingDefaultRow[]>('/api/underlying-pricing-defaults')
      .then((rows) => {
        if (!alive) return;
        const d = rows.find((r) => r.underlying === underlying);
        if (!d) return;
        setInputs((cur) => ({
          ...cur,
          rate: str(d.rate, cur.rate),
          volatility: str(d.volatility, cur.volatility),
          dividendYield: str(d.dividend_yield, cur.dividendYield),
        }));
      })
      .catch(() => { /* keep defaults if no profile exists */ });
    return () => { alive = false; };
  }, [underlying, latestSpot]);

  const update = (key: keyof MarketInputs, value: string) =>
    setInputs((cur) => ({ ...cur, [key]: value }));

  const handlePrice = async () => {
    setResult(null);
    setPricing(true);
    setError(null);
    try {
      const body = {
        product_type: productType,
        product_kwargs: terms,
        engine_name: engineName,
        engine_kwargs: {},
        market: {
          spot: Number(inputs.spot),
          rate: Number(inputs.rate),
          volatility: Number(inputs.volatility),
          dividend_yield: Number(inputs.dividendYield),
          valuation_date: inputs.valuationDate,
          currency,
        },
        compute_greeks: true,
      };
      const res = await api<PricingPreviewOut>('/api/pricing/preview', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setResult(null);
        setError(res.error || 'Pricing failed.');
      } else {
        setResult(res);
      }
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPricing(false);
    }
  };

  const fields: { key: keyof MarketInputs; label: string; type: string }[] = [
    { key: 'spot', label: 'Spot', type: 'number' },
    { key: 'rate', label: 'Rate', type: 'number' },
    { key: 'volatility', label: 'Volatility', type: 'number' },
    { key: 'dividendYield', label: 'Dividend Yield', type: 'number' },
    { key: 'valuationDate', label: 'Valuation Date', type: 'date' },
  ];

  const scaled = result ? scaleByQuantity(result, quantity) : null;
  const greeks = scaled?.greeks ?? null;

  return (
    <section className="wl-pricing-companion">
      <header className="wl-pricing-companion__head">
        <Calculator size={16} aria-hidden="true" />
        <span>Pricing Companion</span>
      </header>
      <div className="wl-pricing-companion__inputs">
        {fields.map((f) => (
          <label key={f.key} className="wl-pricing-companion__field">
            <span>{f.label}</span>
            <NumberInput
              type={f.type}
              step="any"
              aria-label={f.label}
              value={inputs[f.key]}
              onChange={(e) => update(f.key, e.target.value)}
            />
          </label>
        ))}
      </div>
      <Button type="button" variant="primary" onClick={handlePrice} disabled={pricing}>
        <Calculator size={16} aria-hidden="true" />
        {pricing ? 'Pricing…' : 'Price'}
      </Button>

      {error && <div className="wl-pricing-companion__error" role="alert">{error}</div>}

      {scaled && (
        <div className="wl-pricing-companion__results">
          <div className="wl-pricing-companion__engine">Engine · {scaled.engine}</div>
          <div className="wl-pricing-companion__tiles">
            <Tile label="Price (PV)" value={scaled.price.toFixed(4)} variant={scaled.price >= 0 ? 'pos' : 'neg'} />
            {greeks ? (
              <>
                <Tile label="Delta" value={greeks.delta.toFixed(4)} variant={greeks.delta >= 0 ? 'pos' : 'neg'} />
                <Tile label="Gamma" value={greeks.gamma.toFixed(4)} variant={greeks.gamma >= 0 ? 'pos' : 'neg'} />
                <Tile label="Vega" value={greeks.vega.toFixed(4)} variant={greeks.vega >= 0 ? 'pos' : 'neg'} />
                <Tile label="Theta" value={greeks.theta.toFixed(4)} variant={greeks.theta >= 0 ? 'pos' : 'neg'} />
                <Tile label="Rho" value={greeks.rho.toFixed(4)} variant={greeks.rho >= 0 ? 'pos' : 'neg'} />
                <Tile label="RhoQ" value={greeks.rho_q.toFixed(4)} variant={greeks.rho_q >= 0 ? 'pos' : 'neg'} />
              </>
            ) : null}
          </div>
          {!greeks && (
            <div className="wl-pricing-companion__note">
              Greeks unavailable{scaled.greeks_error ? ` · ${scaled.greeks_error}` : ''}.
            </div>
          )}
        </div>
      )}
    </section>
  );
}

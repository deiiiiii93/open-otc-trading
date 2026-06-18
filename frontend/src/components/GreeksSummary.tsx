import { Fragment } from 'react';
import { Tile } from './Tile';
import { Skeleton } from './Skeleton';
import { formatSignedNumber } from './numberFormat';
import './GreeksSummary.css';

export type GreeksTotals = {
  market_value: number;
  delta_proxy: number;
  gross_notional: number;
  pnl: number;
  one_day_var_proxy: number;
  delta?: number;
  gamma?: number;
  delta_cash?: number;
  gamma_cash?: number;
  vega?: number;
  theta?: number;
  rho?: number;
  rho_q?: number;
};

// Mixed-currency runs carry only money greeks per currency (delta/gamma/
// delta_proxy are currency-invariant and live in the shared block).
export type CurrencyGreeks = Partial<GreeksTotals> & { position_count?: number };

type Props = {
  totals: GreeksTotals | null;
  byCurrency?: Record<string, CurrencyGreeks> | null;
  onPromoteToReport?: () => void;
};

function GreekTiles({ greeks }: { greeks: CurrencyGreeks }) {
  const marketValue = greeks.market_value ?? 0;
  const pnl = greeks.pnl ?? 0;
  const deltaCash = greeks.delta_cash ?? greeks.delta ?? greeks.delta_proxy ?? 0;
  const gammaCash = greeks.gamma_cash ?? greeks.gamma ?? 0;
  const vega = greeks.vega ?? 0;
  const theta = greeks.theta ?? 0;
  const rho = greeks.rho ?? 0;
  const rhoQ = greeks.rho_q ?? 0;

  return (
    <div className="wl-greeks__tiles">
      <Tile label="Market Value (PV)" value={formatSignedNumber(marketValue)} variant={marketValue >= 0 ? 'pos' : 'neg'} />
      <Tile label="PnL" value={formatSignedNumber(pnl)} variant={pnl >= 0 ? 'pos' : 'neg'} />
      <Tile label="Delta Cash" value={formatSignedNumber(deltaCash)} variant={deltaCash >= 0 ? 'pos' : 'neg'} />
      <Tile label="Gamma Cash" value={formatSignedNumber(gammaCash)} variant={gammaCash >= 0 ? 'pos' : 'neg'} />
      <Tile label="Vega" value={formatSignedNumber(vega)} variant={vega >= 0 ? 'pos' : 'neg'} />
      <Tile label="Theta" value={formatSignedNumber(theta)} variant={theta >= 0 ? 'pos' : 'neg'} />
      <Tile label="Rho" value={formatSignedNumber(rho)} variant={rho >= 0 ? 'pos' : 'neg'} />
      <Tile label="RhoQ" value={formatSignedNumber(rhoQ)} variant={rhoQ >= 0 ? 'pos' : 'neg'} />
    </div>
  );
}

export function GreeksSummary({ totals, byCurrency, onPromoteToReport }: Props) {
  // Mixed-currency shape: totals is null, money greeks grouped per currency.
  const groups = !totals && byCurrency && Object.keys(byCurrency).length > 0
    ? Object.entries(byCurrency).sort(([a], [b]) => a.localeCompare(b))
    : null;

  return (
    <section className="wl-greeks">
      <header className="wl-greeks__head">
        <span className="wl-greeks__title">
          {groups ? `GREEKS · ${groups[0][0]}` : 'GREEKS · PORTFOLIO'}
        </span>
        {onPromoteToReport && (
          <button
            type="button"
            className="wl-greeks__promote"
            aria-label="Promote to Report"
            onClick={onPromoteToReport}
          >
            ↗
          </button>
        )}
      </header>
      <div className="wl-greeks__body">
        {groups ? (
          <GreekTiles greeks={groups[0][1]} />
        ) : totals ? (
          <GreekTiles greeks={totals} />
        ) : (
          <div className="wl-greeks__skeletons">
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
            <Skeleton height={56} />
          </div>
        )}
      </div>
      {groups?.slice(1).map(([currency, bucket]) => (
        <Fragment key={currency}>
          <header className="wl-greeks__head">
            <span className="wl-greeks__title">{`GREEKS · ${currency}`}</span>
          </header>
          <div className="wl-greeks__body">
            <GreekTiles greeks={bucket} />
          </div>
        </Fragment>
      ))}
    </section>
  );
}

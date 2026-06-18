// frontend/src/routes/Hedging.tsx
import './Hedging.css';
import { useMemo } from 'react';
import { MasterDetailPage } from '../components/templates';
import { RailList } from '../components/RailList';
import { RailItem } from '../components/RailItem';
import { Empty } from '../components/Empty';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type { HedgeUnderlying, PageContext, PageContextReporter, Route } from '../types';
import { HedgeStrategyLive } from './HedgeStrategy.live';

type Props = {
  underlyings: HedgeUnderlying[];
  selectedUnderlyingId: number | null;
  onSelectUnderlying: (id: number) => void;
  onPageContextChange?: PageContextReporter;
  portfolios: { id: number; name: string }[];
  portfolioId: number | null;
  onPortfolioIdChange: (id: number | null) => void;
  onNavigate?: (route: Route) => void;
};

export function Hedging(props: Props) {
  const {
    underlyings, selectedUnderlyingId,
    onSelectUnderlying, onPageContextChange,
    portfolios, portfolioId, onPortfolioIdChange, onNavigate,
  } = props;

  const current = underlyings.find((u) => u.underlying_id === selectedUnderlyingId) ?? null;

  const pageContext = useMemo((): PageContext => ({
    route: 'hedging',
    title: 'Hedging',
    path: '/',
    entity_ids: {
      underlying_id: selectedUnderlyingId,
    },
    snapshot: {
      underlying_symbol: current?.symbol ?? null,
      underlyings_count: underlyings.length,
    },
    chips: ['Hedging', ...(current ? [current.symbol] : [])],
  }), [selectedUnderlyingId, current, underlyings.length]);

  usePageContextReporter(pageContext, onPageContextChange);

  const rail = (
    <RailList>
      {underlyings.map((u) => {
        const displayName = u.display_name?.trim();
        const showDisplayName = displayName && displayName !== u.symbol;
        return (
          <RailItem
            key={u.underlying_id}
            active={u.underlying_id === selectedUnderlyingId}
            onClick={() => onSelectUnderlying(u.underlying_id)}
          >
            <span className="hedging-underlying-card__top">
          <span className="hedging-underlying-card__symbol wl-rail__title">{u.symbol}</span>
          <span className="hedging-underlying-card__asset">{u.asset_class}</span>
        </span>
        {showDisplayName && (
          <span className="hedging-underlying-card__name wl-rail__meta">{displayName}</span>
        )}
        <span className="hedging-underlying-card__tags">
          {u.families.map((f) => (
            <span key={f.family} className="hedging-underlying-tag">
              <span>{f.family}</span>
              <strong>{f.allowed}/{f.total}</strong>
            </span>
          ))}
              {u.unresolvable && (
                <span className="hedging-underlying-tag hedging-underlying-tag--warn">
                  ⚠ unresolvable
                </span>
              )}
            </span>
          </RailItem>
        );
      })}
    </RailList>
  );

  return (
    <MasterDetailPage
      title="HEDGING"
      rail={rail}
      railLabel="Underlyings"
    >
      {current != null
        ? <HedgeStrategyLive
            portfolios={portfolios}
            portfolioId={portfolioId}
            onPortfolioChange={onPortfolioIdChange}
            underlying={current.symbol}
            underlyingId={current.underlying_id}
            onViewPositions={onNavigate ? () => onNavigate('positions') : undefined}
          />
        : (
          <Empty message="Select an underlying from the left to hedge." />
        )
      }
    </MasterDetailPage>
  );
}

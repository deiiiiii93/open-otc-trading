// frontend/src/routes/Hedging.live.tsx
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { HedgeUnderlying, PageContextReporter, Route } from '../types';
import { Hedging } from './Hedging';

type Props = {
  onPageContextChange?: PageContextReporter;
  onNavigate?: (route: Route) => void;
  /** Session-shared portfolio preference; honored only if it is one of this
   * page's container portfolios (fallbacks never write back). */
  portfolioId?: number | null;
  onPortfolioIdChange?: (id: number | null) => void;
};

export function HedgingLive({
  onPageContextChange, onNavigate,
  portfolioId: portfolioIdProp, onPortfolioIdChange,
}: Props) {
  const [underlyings, setUnderlyings] = useState<HedgeUnderlying[]>([]);
  const [selectedUnderlyingId, setSelectedUnderlyingId] = useState<number | null>(null);
  const [portfolios, setPortfolios] = useState<{ id: number; name: string }[]>([]);
  // Controlled (App-shared) or local portfolio *preference*. The effective
  // selection honors the preference only when it is one of this page's
  // container portfolios — a Risk-page view selection falls back to the first
  // container here WITHOUT writing the fallback back to the shared state.
  const controlled = portfolioIdProp !== undefined;
  const [localPreference, setLocalPreference] = useState<number | null>(null);
  const preference = controlled ? portfolioIdProp : localPreference;
  const portfolioId =
    preference != null && portfolios.some((p) => p.id === preference)
      ? preference
      : portfolios[0]?.id ?? null;
  const handlePortfolioIdChange = (id: number | null) => {
    if (!controlled) setLocalPreference(id);
    onPortfolioIdChange?.(id);
  };

  const cancelledRef = useRef(false);
  // Reset on setup so StrictMode's setup→cleanup→setup remount doesn't leave the
  // flag stuck `true` (which would discard every async fetch result).
  useEffect(() => {
    cancelledRef.current = false;
    return () => { cancelledRef.current = true; };
  }, []);

  useEffect(() => {
    void api<HedgeUnderlying[]>('/api/hedging/underlyings')
      .then((rows) => {
        if (cancelledRef.current) return;
        setUnderlyings(rows);
        if (rows.length && selectedUnderlyingId === null) {
          setSelectedUnderlyingId(rows[0].underlying_id);
        }
      })
      .catch((err) => console.error('Failed to load hedging underlyings', err));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Container portfolios feed the strategy picker (the effective selection
  // is derived above: preference if it's a container, else the first one).
  useEffect(() => {
    void api<{ id: number; name: string }[]>('/api/portfolios?kind=container')
      .then((rows) => {
        if (cancelledRef.current) return;
        setPortfolios(rows.map((p) => ({ id: p.id, name: p.name })));
      })
      .catch((err) => console.error('Failed to load portfolios', err));
  }, []);

  const onSelectUnderlying = (id: number) => {
    setSelectedUnderlyingId(id);
  };

  return (
    <Hedging
      underlyings={underlyings}
      selectedUnderlyingId={selectedUnderlyingId}
      onSelectUnderlying={onSelectUnderlying}
      onPageContextChange={onPageContextChange}
      portfolios={portfolios}
      portfolioId={portfolioId}
      onPortfolioIdChange={handlePortfolioIdChange}
      onNavigate={onNavigate}
    />
  );
}

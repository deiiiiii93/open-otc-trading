import { useId, useMemo } from 'react';
import { AnalyticsDashboard } from '../components/templates';
import { Button } from '../components/Button';
import { HeaderControls } from '../components/HeaderControls';
import { Empty } from '../components/Empty';
import { Select } from '../components/Select';
import { GreeksSummary, type CurrencyGreeks, type GreeksTotals } from '../components/GreeksSummary';
import { PnlAttribution, type AttributionPosition } from '../components/PnlAttribution';

import { formatCount } from '../components/numberFormat';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type { EngineConfigVariant, PageContext, PageContextReporter, PricingParameterProfile } from '../types';
import './Risk.css';

export type RiskMetrics = {
  // Mixed-currency runs have totals: null with money greeks per currency.
  totals: GreeksTotals | null;
  byCurrency: Record<string, CurrencyGreeks> | null;
  positions: AttributionPosition[];

};

export type RiskPortfolioOption = {
  id: number;
  name: string;
  positions?: Array<{
    id: number;
    source_trade_id?: string | null;
  }>;
};

type Props = {
  portfolios: RiskPortfolioOption[];
  pricingProfiles: PricingParameterProfile[];
  engineConfigs: EngineConfigVariant[];
  selectedPortfolioId: number | null;
  selectedPricingProfileId: number | null;
  selectedEngineConfigId: number | null;
  metrics: RiskMetrics | null;
  metricsLoading: boolean;
  running: boolean;
  feedback: string | null;
  onSelectPortfolio: (id: number) => void;
  onSelectPricingProfile: (profileId: number | null) => void;
  onSelectEngineConfig: (configId: number | null) => void;
  onRunRisk: () => void;
  onPromoteGreeks: () => void;
  onPromotePnl: () => void;
  onPageContextChange?: PageContextReporter;
};

export function Risk({
  portfolios,
  selectedPortfolioId,
  metrics,
  metricsLoading,
  running,
  feedback,
  pricingProfiles,
  engineConfigs,
  selectedPricingProfileId,
  selectedEngineConfigId,
  onSelectPortfolio,
  onSelectPricingProfile,
  onSelectEngineConfig,
  onRunRisk,
  onPromoteGreeks,
  onPromotePnl,
  onPageContextChange,
}: Props) {
  const pickerId = useId();
  const profilePickerId = useId();
  const engineConfigPickerId = useId();
  const hasPortfolios = portfolios.length > 0;
  const selectedPortfolio = portfolios.find((p) => p.id === selectedPortfolioId) ?? null;
  const selectedPricingProfile = pricingProfiles.find((profile) => profile.id === selectedPricingProfileId) ?? null;
  const chips: string[] = [];
  if (selectedPortfolioId != null) {
    const name = selectedPortfolio?.name ?? '';
    if (name) chips.push(name);
  }
  if (metrics) chips.push(`${formatCount(metrics.positions.length)} priced`);
  const pageContext = useMemo((): PageContext => ({
    route: 'risk',
    title: 'Risk',
    path: '/',
    entity_ids: {
      portfolio_id: selectedPortfolioId,
      pricing_profile_id: selectedPricingProfileId,
    },
    snapshot: {
      selected_portfolio: selectedPortfolio
        ? {
            id: selectedPortfolio.id,
            name: selectedPortfolio.name,
            position_count: selectedPortfolio.positions?.length ?? null,
          }
        : null,
      selected_pricing_profile: selectedPricingProfile
        ? {
            id: selectedPricingProfile.id,
            name: selectedPricingProfile.name,
            valuation_date: selectedPricingProfile.valuation_date,
          }
        : null,
      metrics_loading: metricsLoading,
      running,
      risk: metrics
        ? {
            totals: metrics.totals,
            by_currency: metrics.byCurrency,
            positions: metrics.positions.slice(0, 12),
          }
        : null,
    },
    loaded_context: {
      completeness: 'complete',
      visible_count: metrics?.positions.length ?? 0,
      total_count: metrics?.positions.length ?? 0,
    },
    actions: declareActions([
      {
        name: 'run_batch_pricing',
        required_ids: ['portfolio_id', 'pricing_profile_id'],
        confirmation: 'explicit',
        backend_endpoint: 'POST /api/batch-pricing/runs',
      },
      {
        name: 'read_risk_result',
        required_ids: ['portfolio_id'],
        confirmation: 'implicit',
        backend_endpoint: 'GET /api/risk/runs/latest',
      },
      {
        name: 'get_task_status',
        required_ids: [],
        confirmation: 'implicit',
        backend_endpoint: 'GET /api/tasks/{task_id}',
      },
    ]),
    chips,
  }), [
    chips,
    metrics,
    metricsLoading,
    running,
    selectedPricingProfile,
    selectedPortfolio,
    selectedPortfolioId,
    selectedPricingProfileId,
  ]);
  usePageContextReporter(pageContext, onPageContextChange);

  const actions = hasPortfolios ? (
    <HeaderControls>
      <Select
        variant="inline"
        label="Portfolio"
        id={pickerId}
        value={String(selectedPortfolioId ?? '')}
        onChange={(v) => onSelectPortfolio(Number(v))}
        options={portfolios.map((p) => ({ value: String(p.id), label: p.name }))}
      />
      <Select
        variant="inline"
        label="Pricing parameter profile"
        id={profilePickerId}
        value={String(selectedPricingProfileId ?? '')}
        onChange={(v) => onSelectPricingProfile(v ? Number(v) : null)}
        options={[
          { value: '', label: 'Live market + assumptions' },
          ...pricingProfiles.map((profile) => ({
            value: String(profile.id),
            label: `${profile.name} · ${formatProfileDate(profile.valuation_date)}`,
          })),
        ]}
      />
      <Select
        variant="inline"
        label="Engine config"
        id={engineConfigPickerId}
        value={String(selectedEngineConfigId ?? '')}
        onChange={(v) => onSelectEngineConfig(v ? Number(v) : null)}
        options={[
          { value: '', label: 'Position engines only' },
          ...engineConfigs.map((config) => ({
            value: String(config.id),
            label: `${config.name}${config.is_default ? ' (default)' : ''}`,
          })),
        ]}
      />
      <Button variant="primary" onClick={onRunRisk} disabled={running || selectedPortfolioId == null}>
        {running ? 'Batch Running' : 'Run Batch Pricing ⌘R'}
      </Button>
    </HeaderControls>
  ) : null;

  const emptyState = !hasPortfolios ? (
    <Empty message="No portfolios available — create one in Positions to run risk." symbol="◌" />
  ) : !metrics && running ? (
    <Empty message="Risk run is active. Results will appear here when the task completes." symbol="◌" />
  ) : !metrics && !metricsLoading ? (
    <Empty message="No saved risk run for this portfolio. Run manually to calculate risk." symbol="◌" />
  ) : null;

  return (
    <AnalyticsDashboard
      title="RISK"
      chips={chips}
      actions={actions}
      feedback={feedback ? <span className="wl-risk__feedback">{feedback}</span> : undefined}
      columns={1}
      state={emptyState ?? undefined}
      panels={
        <>
          <GreeksSummary
            totals={metrics?.totals ?? null}
            byCurrency={metrics?.byCurrency ?? null}
            onPromoteToReport={onPromoteGreeks}
          />
          <PnlAttribution
            positions={metrics?.positions ?? []}
            onPromoteToReport={onPromotePnl}
          />
        </>
      }
    />
  );
}

function formatProfileDate(value: string): string {
  if (!value) return '—';
  const datePrefix = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  if (datePrefix) return datePrefix;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Date(parsed).toISOString().slice(0, 10);
}

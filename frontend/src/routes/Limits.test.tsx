import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Limits, type LimitsProps, type LimitsTab } from './Limits';

function props(overrides: Partial<LimitsProps> = {}): LimitsProps {
  return {
    activeTab: 'monitor',
    onTabChange: vi.fn(),
    portfolios: [],
    pricingProfiles: [],
    engineConfigs: [],
    marketSnapshots: [],
    selectedPortfolioId: null,
    selectedPricingProfileId: null,
    selectedEngineConfigId: null,
    selectedMarketSnapshotId: null,
    effectiveMarketEvidenceId: '',
    valuationAsOf: '2026-07-18T09:00',
    sourcePolicy: 'refresh_if_stale',
    maxSourceAgeSeconds: 300,
    sourceInputsText: '{}',
    onSelectPortfolio: vi.fn(),
    onSelectPricingProfile: vi.fn(),
    onSelectEngineConfig: vi.fn(),
    onSelectMarketSnapshot: vi.fn(),
    onEffectiveMarketEvidenceChange: vi.fn(),
    onValuationAsOfChange: vi.fn(),
    onSourcePolicyChange: vi.fn(),
    onMaxSourceAgeSecondsChange: vi.fn(),
    onSourceInputsTextChange: vi.fn(),
    dashboard: null,
    selectedRun: null,
    evaluations: [],
    definitions: [],
    selectedDefinitionId: null,
    onSelectDefinition: vi.fn(),
    incidents: [],
    selectedIncidentId: null,
    selectedIncident: null,
    onSelectIncident: vi.fn(),
    loading: false,
    running: false,
    mutationPending: false,
    error: null,
    mutationFeedback: null,
    onRunNow: vi.fn(),
    onCreateDefinition: vi.fn(),
    onUpdateDefinition: vi.fn(),
    onCreateDefinitionVersion: vi.fn(),
    onActivateDefinitionVersion: vi.fn(),
    onDeactivateDefinition: vi.fn(),
    onRetireDefinition: vi.fn(),
    onAcknowledgeIncident: vi.fn(),
    onAssignIncident: vi.fn(),
    onCommentIncident: vi.fn(),
    onWaiveIncident: vi.fn(),
    onResolveIncident: vi.fn(),
    onReopenIncident: vi.fn(),
    ...overrides,
  };
}

function Harness() {
  const [tab, setTab] = useState<LimitsTab>('monitor');
  return <Limits {...props({ activeTab: tab, onTabChange: setTab })} />;
}

describe('Limits shell', () => {
  it('owns exactly one page scaffold and exposes all five tabs', () => {
    const { container } = render(<Limits {...props()} />);

    expect(container.querySelectorAll('.wl-scaffold')).toHaveLength(1);
    for (const name of ['Monitor', 'Definitions', 'Breaches', 'Schedules', 'Reports']) {
      expect(screen.getByRole('tab', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Generate analysis' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Ask Limit Manager' })).toBeDisabled();
  });

  it('switches controlled tab bodies without nesting a page template', async () => {
    const { container } = render(<Harness />);

    await userEvent.click(screen.getByRole('tab', { name: 'Schedules' }));

    expect(screen.getByText('Schedule control is staged')).toBeInTheDocument();
    expect(container.querySelectorAll('.wl-scaffold')).toHaveLength(1);
  });

  it('keeps Run now disabled until a portfolio and evidence selector exist', () => {
    const { rerender } = render(<Limits {...props()} />);
    expect(screen.getAllByRole('button', { name: 'Run now' })[0]).toBeDisabled();

    rerender(
      <Limits
        {...props({
          portfolios: [{
            id: 1,
            name: 'Macro',
            kind: 'container',
            base_currency: 'USD',
            positions: [],
          }],
          selectedPortfolioId: 1,
          marketSnapshots: [{
            id: 2,
            name: 'Close',
            source: 'manual',
            symbol: 'SPX',
            asset_class: 'index',
            valuation_date: '2026-07-18T09:00:00',
            data: {},
            source_metadata: {},
            created_at: '2026-07-18T09:00:00',
          }],
          selectedMarketSnapshotId: 2,
        })}
      />,
    );
    expect(screen.getAllByRole('button', { name: 'Run now' })[0]).toBeEnabled();
  });
});

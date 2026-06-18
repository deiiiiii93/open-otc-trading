import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { test, expect, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { PricingParameters } from './PricingParameters';
import type { PricingParameterProfile, PricingParameterRow } from '../types';

function baseRow(overrides: Partial<PricingParameterRow> = {}): PricingParameterRow {
  return {
    id: 1,
    profile_id: 1,
    source_trade_id: 'T-0001',
    symbol: '000905.SH',
    instrument_id: 11,
    rate: 0.023,
    dividend_yield: 0.018,
    volatility: 0.21,
    source_row: 2,
    source_payload: {},
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

function baseProfile(overrides: Partial<PricingParameterProfile> = {}): PricingParameterProfile {
  return {
    id: 1,
    name: 'Imported 2026-05-13',
    valuation_date: '2026-05-13',
    source_type: 'xlsx',
    status: 'completed',
    summary: { row_count: 1 },
    rows: [baseRow()],
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

function renderPricingParameters(overrides: Partial<ComponentProps<typeof PricingParameters>> = {}) {
  return render(
    <PricingParameters
      profiles={[]}
      selectedId={null}
      loading={false}
      error={null}
      importing={false}
      feedback={null}
      onSelectProfile={() => {}}
      onImport={() => {}}
      {...overrides}
    />,
  );
}

test('renders source-type filter chips with counts', () => {
  const profiles = [
    baseProfile({ id: 1, source_type: 'default_underlying', summary: { row_count: 5 }, rows: [] }),
    baseProfile({ id: 2, name: 'XLSX A', source_type: 'xlsx', summary: { row_count: 3 }, rows: [] }),
    baseProfile({ id: 3, name: 'Spot A', source_type: 'market_data_spot', summary: { row_count: 2 }, rows: [] }),
  ];
  renderPricingParameters({ profiles, selectedId: 1 });
  expect(screen.getByText(/ALL · 3/)).toBeInTheDocument();
  expect(screen.getByText(/DEFAULT · 1/)).toBeInTheDocument();
  expect(screen.getByText(/XLSX · 1/)).toBeInTheDocument();
  expect(screen.getByText(/SPOT · 1/)).toBeInTheDocument();
});

test('does not select a fallback profile when selectedId is null', () => {
  renderPricingParameters({
    profiles: [baseProfile({ id: 7, name: 'Unselected', rows: [] })],
    selectedId: null,
  });
  const profileButton = within(screen.getByLabelText('Pricing parameter profiles')).getByRole('button', { name: /Unselected/ });
  expect(profileButton).not.toHaveAttribute('aria-current');
  expect(screen.getByText('No pricing parameter profiles yet.')).toBeInTheDocument();
});

test('renders the import-summary strip with applied/dormant/quotes/conflict segments', () => {
  const profile = baseProfile({
    id: 4,
    summary: {
      row_count: 3,
      rows_applied: 2,
      rows_dormant: 1,
      quotes_emitted: 3,
      dormant_trade_ids: ['T-DORM'],
      spot_conflicts: [{ symbol: '000905.SH', count: 2, resolution: 'last row wins' }],
    },
    rows: [
      baseRow({ id: 41, source_trade_id: 'T-APP1' }),
      baseRow({ id: 42, source_trade_id: 'T-APP2' }),
      baseRow({ id: 43, source_trade_id: 'T-DORM' }),
    ],
  });
  renderPricingParameters({ profiles: [profile], selectedId: 4 });
  expect(screen.getByText(
    '2 applied · 1 dormant (T-DORM) · 3 quotes emitted · 1 spot conflict (last row wins)',
  )).toBeInTheDocument();
});

test('marks dormant rows with a DORMANT status badge and applied rows with APPLIED', () => {
  const profile = baseProfile({
    id: 5,
    summary: { row_count: 2, dormant_trade_ids: ['T-DORM'] },
    rows: [
      baseRow({ id: 51, source_trade_id: 'T-LIVE' }),
      baseRow({ id: 52, source_trade_id: 'T-DORM' }),
    ],
  });
  renderPricingParameters({ profiles: [profile], selectedId: 5 });
  const table = screen.getByRole('table');
  expect(within(table).getByText('DORMANT')).toBeInTheDocument();
  expect(within(table).getByText('APPLIED')).toBeInTheDocument();
});

test('the trade-keyed rows table has no SPOT column', () => {
  renderPricingParameters({ profiles: [baseProfile()], selectedId: 1 });
  const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
  expect(headers).toEqual(['TRADE ID', 'POSITION', 'INSTRUMENT', 'RATE', 'DIV YIELD', 'VOL', 'STATUS']);
  expect(headers).not.toContain('SPOT');
});

test('reports the selected profile import summary in page context for the agent', () => {
  const onPageContextChange = vi.fn();
  const profile = baseProfile({
    id: 9,
    summary: {
      row_count: 2,
      rows_applied: 1,
      rows_dormant: 1,
      quotes_emitted: 2,
      dormant_trade_ids: ['T-DORM'],
      spot_conflicts: [],
    },
    rows: [
      baseRow({ id: 91, source_trade_id: 'T-LIVE' }),
      baseRow({ id: 92, source_trade_id: 'T-DORM' }),
    ],
  });
  renderPricingParameters({ profiles: [profile], selectedId: 9, onPageContextChange });

  const context = onPageContextChange.mock.calls.at(-1)?.[0];
  expect(context.snapshot.selected_profile).toMatchObject({
    id: 9,
    rows_applied: 1,
    rows_dormant: 1,
    quotes_emitted: 2,
    import_summary: '1 applied · 1 dormant (T-DORM) · 2 quotes emitted',
  });
  const visible = context.snapshot.visible_rows;
  expect(visible.find((r: { source_trade_id: string }) => r.source_trade_id === 'T-DORM').status).toBe('dormant');
  expect(visible.find((r: { source_trade_id: string }) => r.source_trade_id === 'T-LIVE').status).toBe('applied');
  expect(JSON.stringify(visible)).not.toContain('spot');
});

test('import modal submits name, valuation date, sheet, and file', async () => {
  const user = userEvent.setup();
  const onImport = vi.fn();
  renderPricingParameters({ onImport });

  await user.click(screen.getByRole('button', { name: /import xlsx/i }));
  await user.type(screen.getByLabelText('Profile name'), '2026-04-30 Close');
  await user.type(screen.getByLabelText('Valuation date'), '2026-04-30');
  const file = new File(['xlsx'], 'market.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  await user.upload(screen.getByLabelText('Pricing parameters xlsx'), file);
  await user.click(screen.getByRole('button', { name: /^import$/i }));

  expect(onImport).toHaveBeenCalledWith(expect.objectContaining({
    name: '2026-04-30 Close',
    valuationDate: '2026-04-30',
    file,
  }));
});

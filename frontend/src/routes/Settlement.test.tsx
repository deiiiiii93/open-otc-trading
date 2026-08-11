import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Settlement, type SettlementCashflowRow } from './Settlement';

const row = (over: Partial<SettlementCashflowRow> = {}): SettlementCashflowRow => ({
  id: 1,
  position_id: 7,
  underlying: 'AAPL',
  product_type: 'SnowballOption',
  event_type: 'settle',
  leg_key: 'settlement',
  direction: 'pay',
  amount: 1234.56,
  currency: 'USD',
  value_date: '2026-08-20',
  counterparty: 'Acme Capital',
  status: 'pending',
  stale: false,
  stale_reason: null,
  derived_amount: 1234.56,
  row_version: 1,
  ...over,
});

describe('Settlement', () => {
  it('renders a cashflow row with its amount and counterparty', () => {
    render(<Settlement rows={[row()]} total={1} summary={null} />);
    expect(screen.getByText(/Acme Capital/)).toBeInTheDocument();
    expect(screen.getByText(/1,234\.56/)).toBeInTheDocument();
  });

  it('marks a needs_amount row distinctly from a zero amount', () => {
    render(
      <Settlement
        rows={[row({ amount: null, status: 'needs_amount' })]}
        total={1}
        summary={null}
      />,
    );
    expect(screen.getAllByText(/needs amount/i).length).toBeGreaterThan(0);
  });

  it('does not render a null amount as 0', () => {
    render(
      <Settlement
        rows={[row({ amount: null, status: 'needs_amount' })]}
        total={1}
        summary={null}
      />,
    );
    expect(screen.queryByText(/^0\.00$/)).not.toBeInTheDocument();
  });

  it('flags a stale row', () => {
    render(
      <Settlement
        rows={[row({ stale: true, stale_reason: { kind: 'derived_values_changed' } })]}
        total={1}
        summary={null}
      />,
    );
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
  });

  it('shows an empty state rather than a bare table', () => {
    render(<Settlement rows={[]} total={0} summary={null} />);
    expect(screen.getByText(/no settlement cashflows/i)).toBeInTheDocument();
  });

  it('summarises status counts in the header chips', () => {
    render(
      <Settlement
        rows={[row()]}
        total={6}
        summary={{
          by_status: { pending: 4, needs_amount: 1, released: 1 },
          totals_by_currency: { USD: 900 },
          stale_count: 2,
        }}
      />,
    );
    expect(screen.getByText(/6 cashflows/)).toBeInTheDocument();
    expect(screen.getByText(/2 stale/)).toBeInTheDocument();
  });
});

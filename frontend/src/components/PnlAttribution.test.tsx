import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PnlAttribution } from './PnlAttribution';

const positions = [
  {
    position_id: 1,
    source_trade_id: 'T-VANILLA',
    underlying: 'CSI500',
    product_type: 'EuropeanVanillaOption',
    quantity: 1,
    price: 10,
    market_value: 10,
    gross_notional: 100,
    pnl: 50_000,
    delta_proxy: 1,
    delta: 1234.5,
    gamma: -20_000,
    delta_cash: 12_345,
    gamma_cash: -200_000,
    vega: 2500,
    theta: -17.25,
    rho: 0,
    rho_q: 11.11,
    pricing_ok: true,
    pricing_error: null,
  },
  {
    position_id: 2,
    source_trade_id: 'T-BARRIER',
    underlying: 'HSCEI',
    product_type: 'BarrierOption',
    quantity: 1,
    price: 8,
    market_value: 8,
    gross_notional: 80,
    pnl: -20_000,
    delta_proxy: 1,
    delta: -25,
    gamma: 5,
    delta_cash: -250,
    gamma_cash: 50,
    vega: -1500,
    theta: 3,
    rho: 0,
    rho_q: -2.22,
    pricing_ok: true,
    pricing_error: null,
  },
  {
    position_id: 3,
    source_trade_id: 'T-CSI300',
    underlying: 'CSI300',
    product_type: 'EuropeanVanillaOption',
    quantity: 1,
    price: 5,
    market_value: 5,
    gross_notional: 50,
    pnl: 10_000,
    delta_proxy: 1,
    delta: 10,
    gamma: 3,
    delta_cash: 100,
    gamma_cash: 30,
    vega: 50,
    theta: -1,
    rho: 0,
    rho_q: 4.56,
    pricing_ok: true,
    pricing_error: null,
  },
  {
    position_id: 4,
    source_trade_id: 'T-SECOND',
    underlying: 'CSI500',
    product_type: 'BarrierOption',
    quantity: 1,
    price: 11,
    market_value: 11,
    gross_notional: 110,
    pnl: 15_000,
    delta_proxy: 1,
    delta: 765.5,
    gamma: 1000,
    delta_cash: 7655,
    gamma_cash: 10_000,
    vega: -500,
    theta: 17.25,
    rho: 4,
    rho_q: 4.44,
    pricing_ok: true,
    pricing_error: null,
  },
];

describe('PnlAttribution', () => {
  it('renders Greek rows with position and trade IDs', () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    expect(screen.getByText('GREEKS · BY POSITION')).toBeInTheDocument();
    expect(screen.queryByText('P&L · BY POSITION')).not.toBeInTheDocument();
    expect(screen.getAllByText('CSI500').length).toBeGreaterThan(0);
    expect(screen.getByText('HSCEI')).toBeInTheDocument();
    expect(screen.getByText('CSI300')).toBeInTheDocument();
    expect(screen.getByText('POS 1')).toBeInTheDocument();
    expect(screen.getByText('TRADE T-VANILLA')).toBeInTheDocument();
    expect(screen.getAllByText('Delta Cash')).toHaveLength(4);
    expect(screen.getAllByText('Gamma Cash')).toHaveLength(4);
    expect(screen.getAllByText('RhoQ')).toHaveLength(4);
  });

  it('uses pos variant for positive Greek values', () => {
    const { container } = render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    const positiveValues = container.querySelectorAll('.wl-attr__value--pos');
    expect(positiveValues.length).toBeGreaterThan(0);
  });

  it('uses neg variant for negative Greek values', () => {
    const { container } = render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    const negativeValues = container.querySelectorAll('.wl-attr__value--neg');
    expect(negativeValues.length).toBeGreaterThan(0);
  });

  it('formats Greek values with thousands separators', () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    expect(screen.getByText('+12,345.0000')).toBeInTheDocument();
    expect(screen.getByText('-200,000.0000')).toBeInTheDocument();
    expect(screen.getByText('+11.1100')).toBeInTheDocument();
  });

  it('shows empty state when positions list is empty', () => {
    render(<PnlAttribution positions={[]} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/no priced positions/i)).toBeInTheDocument();
  });

  it('calls onPromoteToReport when ↗ clicked', async () => {
    const onPromoteToReport = vi.fn();
    render(<PnlAttribution positions={positions} onPromoteToReport={onPromoteToReport} />);
    await userEvent.click(screen.getByRole('button', { name: /promote/i }));
    expect(onPromoteToReport).toHaveBeenCalledOnce();
  });

  it('filters rows by position ID search', async () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    const searchInput = screen.getByPlaceholderText(/search position \/ trade id/i);
    await userEvent.type(searchInput, '2');
    expect(screen.queryByText('CSI500')).not.toBeInTheDocument();
    expect(screen.getByText('HSCEI')).toBeInTheDocument();
    expect(screen.queryByText('CSI300')).not.toBeInTheDocument();
  });

  it('filters rows by trade ID search', async () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);
    const searchInput = screen.getByPlaceholderText(/search position \/ trade id/i);
    await userEvent.type(searchInput, 't-vanilla');
    expect(screen.getByText('CSI500')).toBeInTheDocument();
    expect(screen.queryByText('HSCEI')).not.toBeInTheDocument();
    expect(screen.queryByText('CSI300')).not.toBeInTheDocument();
  });

  it('toggles to underlying grouping and aggregates Greeks', async () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);

    await userEvent.click(screen.getByRole('button', { name: 'Underlying' }));

    expect(screen.getByText('GREEKS · BY UNDERLYING')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search underlying/i)).toBeInTheDocument();
    expect(screen.getAllByText('CSI500')).toHaveLength(1);
    expect(screen.getByText('2 positions')).toBeInTheDocument();
    expect(screen.getByText('+20,000.0000')).toBeInTheDocument();
    expect(screen.getByText('-190,000.0000')).toBeInTheDocument();
    expect(screen.getByText('+2,000.0000')).toBeInTheDocument();
    expect(screen.getByText('+15.5500')).toBeInTheDocument();
  });

  it('filters underlying rows by underlying search', async () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);

    await userEvent.click(screen.getByRole('button', { name: 'Underlying' }));
    const searchInput = screen.getByPlaceholderText(/search underlying/i);
    await userEvent.type(searchInput, 'hscei');

    expect(screen.getByText('HSCEI')).toBeInTheDocument();
    expect(screen.queryByText('CSI500')).not.toBeInTheDocument();
    expect(screen.getByText('1–1 of 1')).toBeInTheDocument();
  });

  it('drills from an underlying row into filtered position rows', async () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);

    await userEvent.click(screen.getByRole('button', { name: 'Underlying' }));
    await userEvent.click(screen.getByRole('button', { name: /drill down to CSI500 positions/i }));

    expect(screen.getByText('GREEKS · BY POSITION')).toBeInTheDocument();
    expect(screen.getByDisplayValue('CSI500')).toBeInTheDocument();
    expect(screen.getAllByText('CSI500')).toHaveLength(2);
    expect(screen.getByText('TRADE T-VANILLA')).toBeInTheDocument();
    expect(screen.getByText('TRADE T-SECOND')).toBeInTheDocument();
    expect(screen.queryByText('HSCEI')).not.toBeInTheDocument();
  });

  it('supports keyboard drilldown from underlying rows', async () => {
    render(<PnlAttribution positions={positions} onPromoteToReport={() => {}} />);

    await userEvent.click(screen.getByRole('button', { name: 'Underlying' }));
    screen.getByRole('button', { name: /drill down to HSCEI positions/i }).focus();
    await userEvent.keyboard('{Enter}');

    expect(screen.getByText('GREEKS · BY POSITION')).toBeInTheDocument();
    expect(screen.getByDisplayValue('HSCEI')).toBeInTheDocument();
    expect(screen.getByText('TRADE T-BARRIER')).toBeInTheDocument();
    expect(screen.queryByText('CSI500')).not.toBeInTheDocument();
  });

  it('paginates large position lists', () => {
    const manyPositions = Array.from({ length: 30 }, (_, i) => ({
      position_id: i + 1,
      underlying: `SYM${i + 1}`,
      product_type: 'EuropeanVanillaOption',
      quantity: 1,
      price: 10,
      market_value: 10,
      gross_notional: 100,
      pnl: (i + 1) * 1000,
      delta_proxy: 1,
      delta: i + 1,
      gamma: 0,
      vega: 0,
      theta: 0,
      rho: 0,
      pricing_ok: true,
      pricing_error: null,
    }));
    render(<PnlAttribution positions={manyPositions} onPromoteToReport={() => {}} />);
    // Default page size is 25; page 1 shows IDs 1–25
    expect(screen.getByText('SYM1')).toBeInTheDocument();
    expect(screen.queryByText('SYM26')).not.toBeInTheDocument();
    expect(screen.getByText(/1–25 of 30/)).toBeInTheDocument();
  });
});

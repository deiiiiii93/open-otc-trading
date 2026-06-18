import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GreeksSummary, type GreeksTotals } from './GreeksSummary';

const totals = {
  market_value: 38_200_000,
  delta_proxy: -0.07,
  gross_notional: 50_000_000,
  pnl: 700_000,
  one_day_var_proxy: 1_250_000,
};

describe('GreeksSummary', () => {
  it('renders the primary Greek tiles from totals', () => {
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText(/Delta Cash/i)).toBeInTheDocument();
    expect(screen.getByText(/Gamma Cash/i)).toBeInTheDocument();
    expect(screen.getByText(/Vega/i)).toBeInTheDocument();
    expect(screen.getByText(/Theta/i)).toBeInTheDocument();
    expect(screen.getByText(/^Rho$/)).toBeInTheDocument();
    expect(screen.getByText(/RhoQ/i)).toBeInTheDocument();
    expect(screen.getByText('-0.0700')).toBeInTheDocument();
    expect(screen.queryByText(/1D VaR/i)).not.toBeInTheDocument();
    // PnL tile is now rendered
    expect(screen.getByText(/PnL/i)).toBeInTheDocument();
  });

  it('shows skeleton when totals is null', () => {
    const { container } = render(<GreeksSummary totals={null} onPromoteToReport={() => {}} />);
    expect(container.querySelectorAll('.wl-skeleton').length).toBeGreaterThan(0);
  });

  it('calls onPromoteToReport when ↗ button clicked', async () => {
    const onPromoteToReport = vi.fn();
    render(<GreeksSummary totals={totals} onPromoteToReport={onPromoteToReport} />);
    await userEvent.click(screen.getByRole('button', { name: /promote/i }));
    expect(onPromoteToReport).toHaveBeenCalledOnce();
  });

  it('renders live Delta Cash Gamma Cash Vega Theta values when totals include them', () => {
    const totals: GreeksTotals = {
      market_value: 100, delta_proxy: 5, gross_notional: 1000, pnl: 12,
      one_day_var_proxy: 8,
      delta: 0.42,
      gamma: 0.018,
      delta_cash: 42,
      gamma_cash: 1.8,
      vega: 33.5,
      theta: -1.2,
      rho: 8.4,
      rho_q: 9.9,
    };
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText('Delta Cash')).toBeInTheDocument();
    expect(screen.getByText('Gamma Cash')).toBeInTheDocument();
    expect(screen.getByText('Vega')).toBeInTheDocument();
    expect(screen.getByText('Theta')).toBeInTheDocument();
    expect(screen.getByText('Rho')).toBeInTheDocument();
    expect(screen.getByText('RhoQ')).toBeInTheDocument();
    expect(screen.getByText('+42.0000')).toBeInTheDocument();
    expect(screen.getByText('+1.8000')).toBeInTheDocument();
    expect(screen.getByText('+33.5000')).toBeInTheDocument();
    expect(screen.getByText('-1.2000')).toBeInTheDocument();
    expect(screen.getByText('+8.4000')).toBeInTheDocument();
    expect(screen.getByText('+9.9000')).toBeInTheDocument();
    expect(screen.queryByText(/deferred/i)).not.toBeInTheDocument();
  });

  it('formats large Greek values with thousands separators', () => {
    const totals: GreeksTotals = {
      market_value: 100, delta_proxy: 5, gross_notional: 1000, pnl: 12,
      one_day_var_proxy: 8,
      delta: -780364.6577,
      gamma: -6103083.3307,
      delta_cash: -78036465.77,
      gamma_cash: -610308333.07,
      vega: 4363600,
      theta: -1265322.7579,
      rho: 8.4,
      rho_q: 1.2345,
    };
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText('-78,036,465.7700')).toBeInTheDocument();
    expect(screen.getByText('-610,308,333.0700')).toBeInTheDocument();
    expect(screen.getByText('+4,363,600.0000')).toBeInTheDocument();
    expect(screen.getByText('-1,265,322.7579')).toBeInTheDocument();
    expect(screen.getByText('+8.4000')).toBeInTheDocument();
    expect(screen.getByText('+1.2345')).toBeInTheDocument();
  });

  it('renders one labeled section per currency when totals is null and byCurrency is present', () => {
    const byCurrency = {
      CNY: {
        market_value: -9_821_617.04, gross_notional: 9_895_698, pnl: -9_821_617.04,
        one_day_var_proxy: 124_446.29, delta_cash: -6_035_022.31, gamma_cash: 60_350.23,
        vega: 33_437.48, theta: -3_057.01, rho: -2_809.62, rho_q: 84_525.93,
        position_count: 2,
      },
      USD: {
        market_value: 3_663.83, gross_notional: 50_000, pnl: 3_663.83,
        one_day_var_proxy: 88.12, delta_cash: 1_234.56, gamma_cash: 85.5,
        vega: 210.25, theta: -12.75, rho: 4.2, rho_q: 6.6,
        position_count: 1,
      },
    };
    const { container } = render(
      <GreeksSummary totals={null} byCurrency={byCurrency} onPromoteToReport={() => {}} />,
    );
    expect(screen.getByText('GREEKS · CNY')).toBeInTheDocument();
    expect(screen.getByText('GREEKS · USD')).toBeInTheDocument();
    expect(screen.getByText('-6,035,022.3100')).toBeInTheDocument();
    expect(screen.getByText('+1,234.5600')).toBeInTheDocument();
    expect(container.querySelectorAll('.wl-skeleton').length).toBe(0);
  });

  it('falls back to delta proxy and zeroes when only legacy totals are present', () => {
    const totals: GreeksTotals = {
      market_value: 100, delta_proxy: 5, gross_notional: 1000, pnl: 12,
      one_day_var_proxy: 8,
    };
    render(<GreeksSummary totals={totals} onPromoteToReport={() => {}} />);
    expect(screen.getByText('+5.0000')).toBeInTheDocument();
    expect(screen.getAllByText('+0.0000').length).toBe(5);
    expect(screen.queryByText(/deferred/i)).not.toBeInTheDocument();
  });

  it('renders Market Value (PV) and PnL tiles from totals', () => {
    render(
      <GreeksSummary
        totals={{
          market_value: 1234.56,
          pnl: -78.9,
          delta_proxy: 0,
          gross_notional: 0,
          one_day_var_proxy: 0,
        }}
        onPromoteToReport={vi.fn()}
      />,
    );
    expect(screen.getByText('Market Value (PV)')).toBeInTheDocument();
    expect(screen.getByText('PnL')).toBeInTheDocument();
    // formatSignedNumber uses 4 decimal places with thousands separators
    expect(screen.getByText('+1,234.5600')).toBeInTheDocument();
    expect(screen.getByText('-78.9000')).toBeInTheDocument();
  });

  it('renders PV and PnL inside each currency bucket', () => {
    render(
      <GreeksSummary
        totals={null}
        byCurrency={{
          CNY: { market_value: 11.5, pnl: 2.25, vega: 1 },
          USD: { market_value: -3.75, pnl: 0.5, vega: 2 },
        }}
        onPromoteToReport={vi.fn()}
      />,
    );
    expect(screen.getAllByText('Market Value (PV)')).toHaveLength(2);
    expect(screen.getAllByText('PnL')).toHaveLength(2);
  });

  it('hides the promote button when onPromoteToReport is omitted', () => {
    render(
      <GreeksSummary
        totals={{
          market_value: 1,
          pnl: 1,
          delta_proxy: 0,
          gross_notional: 0,
          one_day_var_proxy: 0,
        }}
      />,
    );
    expect(screen.queryByLabelText('Promote to Report')).not.toBeInTheDocument();
  });
});

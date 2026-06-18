import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RiskReportDialog } from './RiskReportDialog';

const run = {
  id: 7,
  portfolio_id: 3,
  method: 'summary',
  status: 'completed',
  created_at: '2026-06-06T08:00:00Z',
  metrics: {
    totals: {
      market_value: 1500.5,
      pnl: 250.25,
      delta_proxy: 0,
      gross_notional: 0,
      one_day_var_proxy: 0,
      vega: 12,
    },
    by_currency: null,
    positions: [
      {
        position_id: 11,
        source_trade_id: 'T-77',
        underlying: 'AAPL',
        product_type: 'EuropeanVanillaOption',
        quantity: 5,
        price: 10.5,
        market_value: 52.5,
        gross_notional: 500,
        pnl: 12.5,
        delta_proxy: 0.5,
        pricing_ok: true,
        pricing_error: null,
      },
    ],
  },
};

function mockFetch(payload: unknown, ok = true) {
  const fetchMock = vi.fn(async (_input?: RequestInfo | URL, _init?: RequestInit) => ({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
    json: async () => payload,
  }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('RiskReportDialog', () => {
  it('fetches the risk run and shows PV, PnL, and attribution', async () => {
    const fetchMock = mockFetch(run);
    render(<RiskReportDialog riskRunId={7} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('Market Value (PV)')).toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/risk/runs/7'))).toBe(true);
    expect(screen.getByText('PnL')).toBeInTheDocument();
    expect(screen.getByText(/RISK REPORT · RUN #7/)).toBeInTheDocument();
    expect(screen.getByText(/Portfolio #3/)).toBeInTheDocument();
    expect(screen.getByText('AAPL')).toBeInTheDocument();
  });

  it('does not fetch when closed', () => {
    const fetchMock = mockFetch(run);
    render(<RiskReportDialog riskRunId={7} open={false} onClose={() => {}} />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows an error message when the fetch fails', async () => {
    mockFetch({ detail: 'boom' }, false);
    render(<RiskReportDialog riskRunId={7} open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Could not load risk run/)).toBeInTheDocument());
  });
});

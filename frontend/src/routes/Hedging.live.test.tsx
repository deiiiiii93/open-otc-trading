// frontend/src/routes/Hedging.live.test.tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { HedgingLive } from './Hedging.live';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const underlyings = [{
  underlying_id: 1, symbol: '000905.SH', display_name: 'CSI 500', asset_class: 'index',
  unresolvable: false, last_loaded_at: '2026-06-02T09:12:00', stale_count: 0,
  families: [{ family: 'index_future', total: 2, allowed: 1 }],
}];

describe('HedgingLive', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('loads underlyings and renders the underlying rail', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/hedging/underlyings')) return response(underlyings);
      return response([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<HedgingLive />);
    await waitFor(() => expect(screen.getByText('000905.SH')).toBeTruthy());
  });

  it('strategy tab uses the shared portfolio preference when it is a container', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/hedging/underlyings')) return response(underlyings);
      if (url.includes('/api/portfolios?kind=container')) {
        return response([{ id: 4, name: 'X' }, { id: 5, name: 'Dup' }]);
      }
      if (url.includes('/api/hedging/hedgeable?')) {
        return response({ status: 'no_risk_run', underlyings: [] });
      }
      return response([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<HedgingLive portfolioId={5} onPortfolioIdChange={() => {}} />);
    await waitFor(() => expect(screen.getByText('000905.SH')).toBeTruthy());

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) =>
        String(u).includes('/api/hedging/hedgeable?portfolio_id=5'))).toBe(true),
    );
  });

  it('falls back to the first container without write-back when the preference is unknown', async () => {
    const onPortfolioIdChange = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/hedging/underlyings')) return response(underlyings);
      if (url.includes('/api/portfolios?kind=container')) {
        return response([{ id: 4, name: 'X' }, { id: 5, name: 'Dup' }]);
      }
      if (url.includes('/api/hedging/hedgeable?')) {
        return response({ status: 'no_risk_run', underlyings: [] });
      }
      return response([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    // 99 is e.g. a view portfolio picked on the Risk page — not a container.
    render(<HedgingLive portfolioId={99} onPortfolioIdChange={onPortfolioIdChange} />);
    await waitFor(() => expect(screen.getByText('000905.SH')).toBeTruthy());

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) =>
        String(u).includes('/api/hedging/hedgeable?portfolio_id=4'))).toBe(true),
    );
    expect(onPortfolioIdChange).not.toHaveBeenCalled();
  });

  it('notifies onPortfolioIdChange when the user picks a portfolio on the strategy tab', async () => {
    const onPortfolioIdChange = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/hedging/underlyings')) return response(underlyings);
      if (url.includes('/api/portfolios?kind=container')) {
        return response([{ id: 4, name: 'X' }, { id: 5, name: 'Dup' }]);
      }
      if (url.includes('/api/hedging/hedgeable?')) {
        return response({ status: 'no_risk_run', underlyings: [] });
      }
      return response([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<HedgingLive portfolioId={4} onPortfolioIdChange={onPortfolioIdChange} />);
    await waitFor(() => expect(screen.getByText('000905.SH')).toBeTruthy());

    const picker = await screen.findByLabelText('portfolio');
    await act(async () => {
      fireEvent.change(picker, { target: { value: '5' } });
    });
    expect(onPortfolioIdChange).toHaveBeenCalledWith(5);
  });
});

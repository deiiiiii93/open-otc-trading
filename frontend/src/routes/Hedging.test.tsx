// frontend/src/routes/Hedging.test.tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Hedging } from './Hedging';
import type { HedgeUnderlying } from '../types';

function silentFetch() {
  vi.stubGlobal('fetch', vi.fn(() => new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })));
}

const underlying: HedgeUnderlying = {
  underlying_id: 1, symbol: '000905.SH', display_name: 'CSI 500', asset_class: 'index',
  unresolvable: false, last_loaded_at: '2026-06-02T09:12:00', stale_count: 0,
  families: [{ family: 'index_future', total: 3, allowed: 1 }],
};

function setup(overrides: Record<string, unknown> = {}) {
  silentFetch();
  const props = {
    underlyings: [underlying],
    selectedUnderlyingId: 1,
    onSelectUnderlying: vi.fn(),
    portfolios: [],
    portfolioId: null,
    onPortfolioIdChange: vi.fn(),
    ...overrides,
  };
  render(<Hedging {...props} />);
  return { props };
}

describe('Hedging', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('renders the underlying rail with allowed/total counts', () => {
    setup();
    expect(screen.getByText('000905.SH')).toBeTruthy();
    expect(screen.getByText(/1\/3/)).toBeTruthy();
  });

  it('calls onSelectUnderlying when a card is clicked', () => {
    const second: HedgeUnderlying = {
      underlying_id: 2, symbol: '000300.SH', display_name: 'CSI 300', asset_class: 'index',
      unresolvable: false, last_loaded_at: null, stale_count: 0,
      families: [],
    };
    const { props } = setup({ underlyings: [underlying, second], selectedUnderlyingId: 1 });
    fireEvent.click(screen.getByText('000300.SH'));
    expect(props.onSelectUnderlying).toHaveBeenCalledWith(2);
  });

  it('shows the strategy setup prompt when no underlying is selected', () => {
    // No fetch mock needed: no underlying selected means HedgeStrategyLive is not rendered
    vi.stubGlobal('fetch', vi.fn());
    setup({ selectedUnderlyingId: null });
    expect(screen.getByText(/Select an underlying from the left to hedge/i)).toBeTruthy();
  });
});

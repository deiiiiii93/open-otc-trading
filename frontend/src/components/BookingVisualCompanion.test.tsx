import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BookingVisualCompanion } from './BookingVisualCompanion';

describe('BookingVisualCompanion', () => {
  it('renders a vanilla call payoff description and chart', () => {
    render(
      <BookingVisualCompanion
        productType="EuropeanVanillaOption"
        productFamily="vanilla"
        terms={{ strike: 100, option_type: 'CALL' }}
        quantity="10"
        entryPrice="0"
        underlying="000300.SH"
        currency="CNY"
      />,
    );

    expect(screen.getByText('Visual Companion')).toBeInTheDocument();
    expect(screen.getByText(/Long call option/i)).toBeInTheDocument();
    expect(screen.getByText('Terminal Payoff')).toBeInTheDocument();
    expect(screen.getByText('000300.SH')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
  });

  it('renders a snowball payoff with KI and no-KI scenarios', () => {
    render(
      <BookingVisualCompanion
        productType="SnowballOption"
        productFamily="autocallable"
        terms={{
          initial_price: 100,
          strike: 100,
          barrier_config: { ki_barrier: 75, ko_barrier: 103, ko_rate: 0.12 },
        }}
        quantity="-1"
        entryPrice="0"
        underlying="000300.SH"
        currency="CNY"
      />,
    );

    expect(screen.getByText(/Snowball autocall/i)).toBeInTheDocument();
    expect(screen.getByText(/KO barrier 103 · coupon 12.0%/i)).toBeInTheDocument();
  });

  it('renders an empty state for unsupported configurations', () => {
    render(
      <BookingVisualCompanion
        productType="UnknownProduct"
        productFamily="unknown"
        terms={{}}
        quantity="1"
        entryPrice="0"
        underlying="000300.SH"
        currency="CNY"
      />,
    );

    expect(screen.getByText(/No payoff preview available/i)).toBeInTheDocument();
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BookingPricingCompanion } from './BookingPricingCompanion';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}
function url(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

const baseProps = {
  productType: 'EuropeanVanillaOption',
  productFamily: 'vanilla',
  terms: { strike: 100, option_type: 'CALL' },
  engineName: 'BlackScholesEngine',
  underlying: '000300.SH',
  currency: 'CNY',
  latestSpot: 3888.12,
};

describe('BookingPricingCompanion', () => {
  afterEach(() => vi.restoreAllMocks());

  it('pre-fills rate/vol/q from underlying defaults and spot from latestSpot', async () => {
    globalThis.fetch = vi.fn(async (input) => {
      if (url(input) === '/api/underlying-pricing-defaults') {
        return response([{ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 }]);
      }
      throw new Error(`unexpected ${url(input)}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);

    await waitFor(() => {
      expect(screen.getByLabelText('Spot')).toHaveValue(3888.12);
      expect(screen.getByLabelText('Rate')).toHaveValue(0.025);
      expect(screen.getByLabelText('Volatility')).toHaveValue(0.2);
      expect(screen.getByLabelText('Dividend Yield')).toHaveValue(0.01);
    });
  });

  it('prices on demand and renders PV + greek tiles', async () => {
    globalThis.fetch = vi.fn(async (input, init) => {
      const u = url(input);
      if (u === '/api/underlying-pricing-defaults') {
        return response([{ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 }]);
      }
      if (u === '/api/pricing/preview' && init?.method === 'POST') {
        return response({
          ok: true, price: 12.34, engine: 'BlackScholesEngine', product_type: 'EuropeanVanillaOption',
          greeks: { delta: 0.5, gamma: 0.01, vega: 0.2, theta: -0.03, rho: 0.1, rho_q: -0.05 },
          greeks_error: null, error: null,
        });
      }
      throw new Error(`unexpected ${u}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);
    await screen.findByLabelText('Spot');
    await userEvent.click(screen.getByRole('button', { name: /price/i }));

    await waitFor(() => {
      expect(screen.getByText('Price (PV)')).toBeInTheDocument();
      expect(screen.getByText('12.3400')).toBeInTheDocument();
      expect(screen.getByText('Delta')).toBeInTheDocument();
      expect(screen.getByText('0.5000')).toBeInTheDocument();
    });
  });

  it('shows the error when pricing fails', async () => {
    globalThis.fetch = vi.fn(async (input, init) => {
      const u = url(input);
      if (u === '/api/underlying-pricing-defaults') {
        return response([{ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 }]);
      }
      if (u === '/api/pricing/preview' && init?.method === 'POST') {
        return response({ ok: false, price: 0, engine: 'BlackScholesEngine',
          product_type: 'EuropeanVanillaOption', greeks: null, greeks_error: null, error: 'bad terms' });
      }
      throw new Error(`unexpected ${u}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);
    await screen.findByLabelText('Spot');
    await userEvent.click(screen.getByRole('button', { name: /price/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('bad terms');
    });
    expect(screen.queryByText('Price (PV)')).not.toBeInTheDocument();
  });

  it('shows greeks unavailable note when greeks are null', async () => {
    globalThis.fetch = vi.fn(async (input, init) => {
      const u = url(input);
      if (u === '/api/underlying-pricing-defaults') {
        return response([{ underlying: '000300.SH', rate: 0.025, dividend_yield: 0.01, volatility: 0.2 }]);
      }
      if (u === '/api/pricing/preview' && init?.method === 'POST') {
        return response({
          ok: true, price: 9.99, engine: 'BlackScholesEngine', product_type: 'EuropeanVanillaOption',
          greeks: null, greeks_error: 'greek calc failed', error: null,
        });
      }
      throw new Error(`unexpected ${u}`);
    }) as unknown as typeof fetch;

    render(<BookingPricingCompanion {...baseProps} />);
    await screen.findByLabelText('Spot');
    await userEvent.click(screen.getByRole('button', { name: /price/i }));

    await waitFor(() => {
      expect(screen.getByText('Price (PV)')).toBeInTheDocument();
      expect(screen.getByText('9.9900')).toBeInTheDocument();
      expect(screen.getByText(/greeks unavailable/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Delta')).not.toBeInTheDocument();
  });
});

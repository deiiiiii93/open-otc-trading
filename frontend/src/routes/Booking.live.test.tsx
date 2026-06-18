import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BookingLive } from './Booking.live';

const portfolio = {
  id: 1,
  name: 'Desk-Q2',
  kind: 'container',
  base_currency: 'USD',
  created_at: '2026-05-07T00:00:00',
  updated_at: '2026-05-07T00:00:00',
  positions: [],
};

const viewPortfolio = {
  ...portfolio,
  id: 2,
  name: 'View Only',
  kind: 'view',
};

const marketDataProfile = {
  id: 9,
  name: 'CSI300 latest',
  source: 'akshare',
  symbol: '000300',
  asset_class: 'index',
  start_date: '2026-05-01',
  end_date: '2026-05-28',
  adjust: 'qfq',
  valuation_date: '2026-05-28T00:00:00',
  data: { spot: 3888.12, latest: { date: '2026-05-28', close: 3888.12 } },
  source_metadata: {},
  created_at: '2026-05-28T00:00:00',
  updated_at: '2026-05-28T00:00:00',
};

const secondMarketDataProfile = {
  ...marketDataProfile,
  id: 10,
  name: 'CSI500 latest',
  symbol: '000905',
  data: { spot: 6123.45, latest: { date: '2026-05-28', close: 6123.45 } },
};

const activeUnderlying = {
  id: 101,
  symbol: '000300.SH',
  display_name: 'CSI 300',
  kind: 'index',
  exchange: 'SH',
  currency: 'CNY',
  akshare_symbol: '000300',
  akshare_asset_class: 'index',
  status: 'active',
  source: 'manual',
  contract_code: null,
  series_root: null,
  expiry: null,
  multiplier: null,
  strike: null,
  option_type: null,
  parent_id: null,
  loaded_at: null,
  rate: 0.025,
  dividend_yield: 0.01,
  volatility: 0.2,
  notes: null,
  created_at: '2026-05-28T00:00:00',
  updated_at: '2026-05-28T00:00:00',
};

const secondActiveUnderlying = {
  ...activeUnderlying,
  id: 102,
  symbol: '000905.SH',
  display_name: 'CSI 500',
  akshare_symbol: '000905',
};

const inactiveUnderlying = {
  ...activeUnderlying,
  id: 103,
  symbol: '000852.SH',
  display_name: 'CSI 1000',
  akshare_symbol: '000852',
  status: 'inactive',
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

describe('BookingLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('books a product-backed position into a container portfolio', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio, viewPortfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      if (url === '/api/portfolios/1/positions' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        expect(body).toEqual(expect.objectContaining({
          underlying: '000300.SH',
          product_type: 'SnowballOption',
          quantity: -1,
          company: 'ACME Corp',
          product_kwargs: expect.objectContaining({
            initial_price: 100,
            barrier_config: expect.objectContaining({ ko_rate: 0.12 }),
            accrual_config: expect.not.objectContaining({ coupon_rate: expect.anything() }),
          }),
          product: expect.objectContaining({
            asset_class: 'equity',
            product_family: 'autocallable',
            quantark_class: 'SnowballOption',
            underlying: '000300.SH',
            terms: expect.objectContaining({ initial_price: 100 }),
          }),
        }));
        return response({
          ...portfolio,
          positions: [{
            id: 42,
            portfolio_id: 1,
            product_id: 88,
            source_trade_id: 'BOOK-1',
            underlying: body.underlying,
            product_type: body.product_type,
            product_kwargs: body.product_kwargs,
            engine_name: body.engine_name,
            engine_kwargs: {},
            quantity: body.quantity,
            entry_price: body.entry_price,
            status: body.status,
            mapping_status: 'manual',
          }],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByText('BOOKING')).toBeInTheDocument());
    expect(screen.getByLabelText('Currency')).toHaveValue('CNY');
    await userEvent.type(screen.getByLabelText('Trade ID'), 'BOOK-1');
    await userEvent.type(screen.getByLabelText('Company'), 'ACME Corp');
    await userEvent.click(screen.getByRole('button', { name: /book position/i }));

    await waitFor(() => expect(screen.getByText('Booked BOOK-1')).toBeInTheDocument());
  });

  it('defaults currency to CNY and constrains supported choices', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    const currency = await screen.findByLabelText('Currency');
    expect(currency).toHaveValue('CNY');
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(
      expect.arrayContaining(['USD', 'CNY', 'EUR']),
    );

    await userEvent.selectOptions(currency, 'EUR');
    expect(currency).toHaveValue('EUR');
  });

  it('uses a picker limited to active underlyings', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([]);
      if (url === '/api/instruments' && !init?.method) return response([inactiveUnderlying, secondActiveUnderlying]);
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    const picker = await screen.findByLabelText('Underlying');
    expect(picker.tagName).toBe('SELECT');
    expect(picker).toHaveValue('000905.SH');
    expect(screen.getByRole('option', { name: /000905.SH/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /000852.SH/ })).not.toBeInTheDocument();
  });

  it('defaults Snowball initial and strike prices from latest market data spot', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByLabelText('Initial Price')).toHaveValue(3888.12));
    expect(screen.getByLabelText('Strike')).toHaveValue(3888.12);
  });

  it('adds KI/KO barrier% fields and keeps barrier levels adaptive to initial price', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile, secondMarketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying, secondActiveUnderlying]);
      if (url === '/api/portfolios/1/positions' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        const barrierConfig = (body.product_kwargs.barrier_config as Record<string, unknown>) || {};
        expect(barrierConfig).toMatchObject({
          ki_barrier: expect.any(Number),
          ko_barrier: expect.any(Number),
        });
        expect(body.product_kwargs).toMatchObject({
          initial_price: 6123.45,
          strike: 6123.45,
        });
        expect(barrierConfig.ki_barrier).toBeCloseTo(4898.76, 6);
        expect(barrierConfig.ko_barrier).toBeCloseTo(6307.1535, 6);
        return response({
          ...portfolio,
          positions: [{
            id: 42,
            portfolio_id: 1,
            product_id: 88,
            source_trade_id: 'BOOK-1',
            underlying: body.underlying,
            product_type: body.product_type,
            product_kwargs: body.product_kwargs,
            engine_name: body.engine_name,
            engine_kwargs: {},
            quantity: body.quantity,
            entry_price: body.entry_price,
            status: body.status,
            mapping_status: 'manual',
          }],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByLabelText('KI Barrier %')).toHaveValue(75));
    expect(screen.getByLabelText('KO Barrier %')).toHaveValue(103);
    await userEvent.clear(screen.getByLabelText('KI Barrier %'));
    await userEvent.type(screen.getByLabelText('KI Barrier %'), '80');
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000905.SH');

    await userEvent.click(screen.getByRole('button', { name: /book position/i }));

    await waitFor(() => expect(screen.getByText('Booked BOOK-1')).toBeInTheDocument());
  });

  it('defaults European vanilla strike from latest market data spot', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByText('BOOKING')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Product Type'), 'EuropeanVanillaOption');

    await waitFor(() => expect(screen.getByLabelText('Strike')).toHaveValue(3888.12));
  });

  it('omits stale maturity when booking European vanilla with explicit dates', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      if (url === '/api/portfolios/1/positions' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        expect(body.product_type).toBe('EuropeanVanillaOption');
        expect(body.product.terms).toEqual(expect.objectContaining({
          strike: 3888.12,
          option_type: 'CALL',
          exercise_date: '2026-09-18',
          settlement_date: '2026-09-18',
        }));
        expect(body.product.terms).not.toHaveProperty('maturity');
        return response({
          ...portfolio,
          positions: [{
            id: 43,
            portfolio_id: 1,
            product_id: 89,
            source_trade_id: 'BOOK-EV',
            underlying: body.underlying,
            product_type: body.product_type,
            product_kwargs: body.product_kwargs,
            engine_name: body.engine_name,
            engine_kwargs: {},
            quantity: body.quantity,
            entry_price: body.entry_price,
            status: body.status,
            mapping_status: 'manual',
          }],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByText('BOOKING')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Product Type'), 'EuropeanVanillaOption');
    await waitFor(() => expect(screen.getByLabelText('Strike')).toHaveValue(3888.12));
    fireEvent.change(screen.getByLabelText('Exercise Date'), { target: { value: '2026-09-18' } });
    fireEvent.change(screen.getByLabelText('Settlement Date'), { target: { value: '2026-09-18' } });

    await userEvent.click(screen.getByRole('button', { name: /book position/i }));

    await waitFor(() => expect(screen.getByText('Booked BOOK-EV')).toBeInTheDocument());
  });

  it('defaults strike from latest market data spot for every strike-bearing product', async () => {
    const strikeProducts = [
      'EuropeanVanillaOption',
      'AmericanOption',
      'CashOrNothingDigitalOption',
      'BarrierOption',
      'SingleSharkfinOption',
      'DoubleSharkfinOption',
      'AsianOption',
      'SnowballOption',
      'PhoenixOption',
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByText('BOOKING')).toBeInTheDocument());
    for (const productType of strikeProducts) {
      await userEvent.selectOptions(screen.getByLabelText('Product Type'), productType);
      await waitFor(() => expect(screen.getByLabelText('Strike')).toHaveValue(3888.12));
    }
  });

  it('refreshes auto-filled initial and strike prices when underlying changes', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([marketDataProfile, secondMarketDataProfile]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying, secondActiveUnderlying]);
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByLabelText('Initial Price')).toHaveValue(3888.12));
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000905.SH');

    await waitFor(() => expect(screen.getByLabelText('Initial Price')).toHaveValue(6123.45));
    expect(screen.getByLabelText('Strike')).toHaveValue(6123.45);
  });

  it('sends package components as top-level product components', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/portfolios' && !init?.method) return response([portfolio]);
      if (url === '/api/market-data/profiles' && !init?.method) return response([]);
      if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
      if (url === '/api/portfolios/1/positions' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        expect(body.product.product_family).toBe('package');
        expect(body.product.components).toEqual([{ component_product_id: 7, quantity: 1 }]);
        return response({ ...portfolio, positions: [{ id: 43, product_id: 89, source_trade_id: 'PKG-1' }] });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<BookingLive />);

    await waitFor(() => expect(screen.getByText('BOOKING')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText('Product Type'), 'EuropeanVanillaOption');
    await userEvent.click(screen.getByRole('button', { name: /add row/i }));
    fireEvent.change(screen.getByLabelText(/components 1 component product id/i), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText(/components 1 quantity/i), { target: { value: '1' } });
    await userEvent.click(screen.getByRole('button', { name: /book position/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/portfolios/1/positions', expect.any(Object)));
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { InstrumentsLive } from './Instruments.live';

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

const staleGroup = {
  underlying_id: 1,
  underlying_symbol: 'IC2406.CFFEX',
  open_position_count: 0,
  entries: [
    {
      id: 5, instrument_id: 2, exchange: 'CFFEX', contract_code: 'IC2406',
      family: 'index_future', series_root: 'IC', instrument_type: 'future',
      option_type: null, strike: null, expiry: null, reconcile_status: 'stale',
    },
  ],
};

describe('InstrumentsLive — Allowed Hedges mutations refresh the Registry TAGS cell', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('re-fetches /api/instruments after purging stale hedge entries', async () => {
    let instrumentsFetchCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/instruments' && !init?.method) {
        instrumentsFetchCount += 1;
        return response([]);
      }
      if (url === '/api/hedging/map' && !init?.method) return response([staleGroup]);
      if (url === '/api/market-data/quotes?latest=1' && !init?.method) return response([]);
      if (url.startsWith('/api/hedging/instruments?') && !init?.method) return response([]);
      if (url === '/api/hedging/map/purge-stale?underlying_id=1' && init?.method === 'POST') {
        return response({ purged: 1 });
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? 'GET'}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<InstrumentsLive />);

    await waitFor(() => expect(instrumentsFetchCount).toBe(1));
    await userEvent.click(await screen.findByRole('tab', { name: /allowed hedges/i }));
    await screen.findByLabelText('Purge stale entries');

    await userEvent.click(screen.getByLabelText('Purge stale entries'));

    await waitFor(() => expect(instrumentsFetchCount).toBe(2));
  });
});

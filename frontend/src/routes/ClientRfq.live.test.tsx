import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ClientRfqLive } from './ClientRfq.live';
import type { RFQ } from '../types';

const catalog = {
  product_types: [],
  engine_options: [],
  unknown_fields: { EuropeanVanillaOption: ['strike'] },
  templates: [
    {
      key: 'vanilla',
      label: 'Vanilla',
      product_type: 'EuropeanVanillaOption',
      engine_spec: { engine_name: 'BlackScholesEngine' },
      unknown_fields: ['strike'],
      unknown_field_specs: [
        { field_path: 'strike', label: 'Strike', lower_bound: 50, upper_bound: 150, initial_guess: 100 },
      ],
      product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1, contract_multiplier: 1 },
    },
    {
      key: 'futures',
      label: 'Futures',
      product_type: 'Futures',
      engine_spec: { engine_name: 'DeltaOneEngine' },
      unknown_fields: ['basis'],
      unknown_field_specs: [
        { field_path: 'basis', label: 'Basis', lower_bound: -10, upper_bound: 10, initial_guess: 0 },
      ],
      // Delta-one seeds carry the underlying inside the terms (QuantArk
      // builds Futures from it); the live wrapper must keep it in sync
      // with the user's selection.
      product_kwargs: { maturity: 1, multiplier: 1, underlying: 'CSI500' },
    },
  ],
  advanced: {},
};

const instruments = [
  { id: 1, symbol: '000852.SH', display_name: 'CSI 1000', status: 'active', currency: 'CNY' },
];

const listedRfq: RFQ = {
  id: 7,
  client_name: 'Demo Client',
  channel: 'form',
  status: 'pending_approval',
  request_payload: { product_type: 'EuropeanVanillaOption', underlying: '000852.SH', side: 'buy', quantity: 1 },
  quote_payload: {},
  created_at: '2026-06-06T08:00:00Z',
};

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockFetch(onForm?: (init: RequestInit | undefined) => void) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith('/api/rfq/catalog')) return jsonResponse(catalog);
    if (url.startsWith('/api/instruments')) return jsonResponse(instruments);
    if (url.startsWith('/api/client/rfqs')) return jsonResponse([listedRfq]);
    if (url.startsWith('/api/client/rfq/form')) {
      onForm?.(init);
      return jsonResponse({ ...listedRfq, id: 8 });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('ClientRfqLive', () => {
  it('fetches catalog, instruments, and the client rfq list on mount', async () => {
    const fetchMock = mockFetch();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /#7 vanilla/i })).toBeInTheDocument();
    });
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain('/api/rfq/catalog');
    expect(urls).toContain('/api/instruments');
    expect(urls.some((url) => url.startsWith('/api/client/rfqs?client_name=Demo%20Client'))).toBe(true);
  });

  it('submits the structured form without a tenor overwrite and refreshes the list', async () => {
    let formBody: Record<string, unknown> | null = null;
    const fetchMock = mockFetch((init) => {
      formBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Underlying')).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '12');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/rfq #8 submitted/i);
    });
    expect(formBody).not.toBeNull();
    const body = formBody as unknown as Record<string, any>;
    expect(body.client_name).toBe('Demo Client');
    expect(body.quote_mode).toBe('solve');
    expect(body.product.quantark_class).toBe('EuropeanVanillaOption');
    expect(body.product.underlying).toBe('000852.SH');
    expect(body.product.currency).toBe('CNY');
    expect(body.market).toEqual({ currency: 'CNY' });
    expect(body.product.terms.currency).toBeUndefined();
    expect(body.product.terms.maturity).toBe(1);
    expect(body.unknown.field_path).toBe('strike');
    // Non-default target value so this pins the typed input, not the seed.
    expect(body.target).toEqual({ label: 'price', value: 12 });
    expect(body.tenor).toBeUndefined();
  });

  it('submits the selected currency through product and market', async () => {
    let formBody: Record<string, unknown> | null = null;
    const fetchMock = mockFetch((init) => {
      formBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Currency')).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.selectOptions(screen.getByLabelText('Currency'), 'USD');
    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '12');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/rfq #8 submitted/i);
    });
    const body = formBody as unknown as Record<string, any>;
    expect(body.product.currency).toBe('USD');
    expect(body.market.currency).toBe('USD');
  });

  it('clones RFQ currency into the structured editor', async () => {
    const existing = {
      ...listedRfq,
      request_payload: {
        product_type: 'EuropeanVanillaOption', underlying: '000852.SH', side: 'buy',
        quantity: 1, market: { currency: 'HKD' }, product_kwargs: { strike: 100, option_type: 'CALL', maturity: 1 },
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith('/api/rfq/catalog')) return jsonResponse(catalog);
      if (url.startsWith('/api/instruments')) return jsonResponse(instruments);
      if (url.startsWith('/api/client/rfqs')) return jsonResponse([existing]);
      if (url.startsWith('/api/client/rfq/form')) return jsonResponse({ ...existing, id: 8 });
      throw new Error(`Unexpected fetch: ${url} ${String(init?.body ?? '')}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /#7 vanilla/i })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('button', { name: /clone rfq 7/i }));

    expect(screen.getByLabelText('Currency')).toHaveValue('HKD');
  });

  it('keeps delta-one terms underlying in sync with the selected underlying', async () => {
    let formBody: Record<string, unknown> | null = null;
    const fetchMock = mockFetch((init) => {
      formBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Underlying')).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText('Product'), 'Futures');
    // Futures also renders the terms `underlying` as an Extra Field, so
    // disambiguate the canonical select by role.
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Underlying' }),
      '000852.SH',
    );
    // The visible terms input tracks the selection…
    expect(screen.getByRole('textbox', { name: 'Underlying' })).toHaveValue('000852.SH');

    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '5');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/rfq #8 submitted/i);
    });
    const body = formBody as unknown as Record<string, any>;
    expect(body.product.underlying).toBe('000852.SH');
    // …and the template seed (CSI500) must not leak into the priced terms.
    expect(body.product.terms.underlying).toBe('000852.SH');
  });

  it('trims the client name on submission and rejects a blank one', async () => {
    let formBody: Record<string, unknown> | null = null;
    const fetchMock = mockFetch((init) => {
      formBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Underlying')).toBeInTheDocument();
    });

    const nameInput = screen.getByLabelText('Client Name');
    await userEvent.clear(nameInput);

    // Blank name: submission is rejected before any request is made.
    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '12');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));
    expect(screen.getByRole('alert')).toHaveTextContent(/enter a client name/i);
    expect(formBody).toBeNull();

    // Whitespace-padded name: the submitted payload carries the trimmed
    // value the history query uses, so the new RFQ stays visible.
    await userEvent.type(nameInput, '  Demo Client  ');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/rfq #8 submitted/i);
    });
    expect((formBody as unknown as Record<string, any>).client_name).toBe('Demo Client');
  });

  it('shows no RFQs and skips fetching when the client name is blank', async () => {
    const fetchMock = mockFetch();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /#7 vanilla/i })).toBeInTheDocument();
    });

    await userEvent.clear(screen.getByLabelText('Client Name'));
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /#7 vanilla/i })).not.toBeInTheDocument();
    });
    // A blank client_name must never reach the API: the backend would skip
    // its filter and return every client's RFQs.
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes('client_name=&'))).toBe(false);
  });

  it('surfaces backend error detail and persists the client name', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/rfq/catalog')) return jsonResponse(catalog);
      if (url.startsWith('/api/instruments')) return jsonResponse(instruments);
      if (url.startsWith('/api/client/rfqs')) return jsonResponse([]);
      if (url.startsWith('/api/client/rfq/form')) {
        return {
          ok: false,
          status: 400,
          json: async () => ({ detail: 'QuantArk build failed' }),
          text: async () => JSON.stringify({ detail: 'QuantArk build failed' }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<ClientRfqLive />);
    await waitFor(() => {
      expect(screen.getByLabelText('Underlying')).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText('Underlying'), '000852.SH');
    await userEvent.clear(screen.getByLabelText('Target Value'));
    await userEvent.type(screen.getByLabelText('Target Value'), '12');
    await userEvent.click(screen.getByRole('button', { name: /submit rfq/i }));
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('QuantArk build failed');
    });

    const nameInput = screen.getByLabelText('Client Name');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Acme');
    await waitFor(() => {
      expect(localStorage.getItem('openOtc.clientRfqName')).toBe('Acme');
    });
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.startsWith('/api/client/rfqs?client_name=Acme'))).toBe(true);
    });
  });
});

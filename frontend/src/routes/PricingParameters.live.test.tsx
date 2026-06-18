import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PricingParametersLive } from './PricingParameters.live';

const profile = {
  id: 7,
  name: '2026-04-30 Close',
  valuation_date: '2026-04-30T00:00:00',
  source_type: 'xlsx',
  source_path: '/tmp/market.xlsx',
  status: 'completed',
  summary: {
    row_count: 2,
    duplicate_trade_ids: [],
    rows_applied: 1,
    rows_dormant: 1,
    quotes_emitted: 2,
    dormant_trade_ids: ['T-DORM'],
    spot_conflicts: [],
  },
  created_at: '2026-05-11T00:00:00',
  updated_at: '2026-05-11T00:00:00',
  rows: [
    {
      id: 70,
      profile_id: 7,
      source_trade_id: 'T-VANILLA',
      symbol: '000852.SH',
      instrument_id: 8,
      rate: 0.02,
      dividend_yield: 0.03,
      volatility: 0.22,
      source_row: 2,
      source_payload: {},
      created_at: '2026-05-11T00:00:00',
      updated_at: '2026-05-11T00:00:00',
    },
    {
      id: 71,
      profile_id: 7,
      source_trade_id: 'T-DORM',
      symbol: '000905.SH',
      instrument_id: 9,
      rate: 0.02,
      dividend_yield: 0.03,
      volatility: 0.22,
      source_row: 3,
      source_payload: {},
      created_at: '2026-05-11T00:00:00',
      updated_at: '2026-05-11T00:00:00',
    },
  ],
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function deferredResponse(): { promise: Promise<Response>; resolve: (value: Response) => void } {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('PricingParametersLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders profiles, the trade-keyed row table, and the import summary strip', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/pricing-parameter-profiles') return response([profile]);
      return response({});
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<PricingParametersLive />);

    await waitFor(() => expect(screen.getByText('PRICING PARAMS')).toBeInTheDocument());
    expect(screen.getByText('2026-04-30 Close')).toBeInTheDocument();
    // Applied trade ids render in both the TRADE ID and POSITION columns.
    expect(screen.getAllByText('T-VANILLA').length).toBeGreaterThan(0);
    expect(screen.getByText('000852.SH')).toBeInTheDocument();
    expect(screen.getByText('1 applied · 1 dormant (T-DORM) · 2 quotes emitted')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /2026-04-30 close/i })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: /^new$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /import xlsx/i })).toBeInTheDocument();
    // No spot column anywhere.
    const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
    expect(headers).not.toContain('SPOT');
  });

  it('submits the import modal as multipart form data', async () => {
    let importInit: RequestInit | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/pricing-parameter-profiles/import' && init?.method === 'POST') {
        importInit = init;
        return response(profile);
      }
      return response({});
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<PricingParametersLive />);

    await waitFor(() => expect(screen.getByText('PRICING PARAMS')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    await userEvent.type(screen.getByLabelText('Profile name'), '2026-04-30 Close');
    await userEvent.type(screen.getByLabelText('Valuation date'), '2026-04-30');
    const file = new File(['xlsx'], 'market.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(screen.getByLabelText('Pricing parameters xlsx'), file);
    await userEvent.click(screen.getByRole('button', { name: /^import$/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pricing-parameter-profiles/import',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(importInit?.body).toBeInstanceOf(FormData);
    const body = importInit?.body as FormData;
    const submittedFile = body.get('file');
    expect(submittedFile).toBeInstanceOf(File);
    expect((submittedFile as File).name).toBe('market.xlsx');
    expect(body.get('name')).toBe('2026-04-30 Close');
    expect(body.get('valuation_date')).toBe('2026-04-30');
    expect(await screen.findByRole('status')).toHaveTextContent('Imported 2 rows.');
  });

  it('submits manual pricing parameters as json and selects the new profile', async () => {
    const manualProfile = {
      ...profile,
      id: 88,
      name: 'Manual close',
      source_type: 'agent',
      summary: { row_count: 1, created_by: 'desk_user' },
      rows: [
        {
          ...profile.rows[0],
          id: 880,
          profile_id: 88,
          source_trade_id: 'T-MANUAL',
          symbol: '000905.SH',
          rate: 0.023,
          dividend_yield: 0.011,
          volatility: 0.21,
        },
      ],
    };
    let createBody: Record<string, unknown> | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pricing-parameter-profiles' && (!init || init.method === 'GET' || init.method === undefined)) {
        return response([manualProfile]);
      }
      if (url === '/api/pricing-parameter-profiles' && init?.method === 'POST') {
        createBody = JSON.parse(String(init.body));
        return response(manualProfile, 201);
      }
      return response({});
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<PricingParametersLive />);

    await waitFor(() => expect(screen.getByText('PRICING PARAMS')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^new$/i }));
    await userEvent.type(screen.getByLabelText('Manual profile name'), 'Manual close');
    await userEvent.type(screen.getByLabelText('Valuation date'), '2026-06-18');
    await userEvent.type(screen.getByLabelText('Trade ID'), 'T-MANUAL');
    await userEvent.type(screen.getByLabelText('Symbol'), '000905.SH');
    await userEvent.type(screen.getByLabelText('Rate'), '0.023');
    await userEvent.type(screen.getByLabelText('Dividend yield'), '0.011');
    await userEvent.type(screen.getByLabelText('Volatility'), '0.21');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/pricing-parameter-profiles',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(createBody).toEqual({
      name: 'Manual close',
      valuation_date: '2026-06-18',
      rows: [
        {
          source_trade_id: 'T-MANUAL',
          symbol: '000905.SH',
          rate: 0.023,
          dividend_yield: 0.011,
          volatility: 0.21,
        },
      ],
    });
    expect(await screen.findByRole('status')).toHaveTextContent('Created 1 row profile.');
    expect(
      within(screen.getByLabelText('Pricing parameter profiles')).getByRole('button', { name: /manual close/i }),
    ).toHaveAttribute('aria-current', 'true');
  });

  it('announces import failures as alerts', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/pricing-parameter-profiles/import' && init?.method === 'POST') {
        return response({ detail: 'bad workbook' }, 400);
      }
      return response({});
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<PricingParametersLive />);

    await waitFor(() => expect(screen.getByText('PRICING PARAMS')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    const file = new File(['xlsx'], 'market.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(screen.getByLabelText('Pricing parameters xlsx'), file);
    await userEvent.click(screen.getByRole('button', { name: /^import$/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not import: bad workbook');
    expect(alert).not.toHaveTextContent('{"detail"');
  });

  it('ignores stale initial profile loads after an import refresh selects a new profile', async () => {
    const initialProfiles = deferredResponse();
    const importedProfile = {
      ...profile,
      id: 44,
      name: 'Imported Fresh',
      valuation_date: '2026-05-17',
      summary: { row_count: 1, dormant_trade_ids: [] },
      rows: [
        {
          ...profile.rows[0],
          id: 440,
          profile_id: 44,
          source_trade_id: 'T-FRESH',
        },
      ],
    };
    let profileGetCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pricing-parameter-profiles' && (!init || init.method === 'GET' || init.method === undefined)) {
        profileGetCount += 1;
        return profileGetCount === 1 ? initialProfiles.promise : response([importedProfile]);
      }
      if (url === '/api/pricing-parameter-profiles/import' && init?.method === 'POST') {
        return response(importedProfile);
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<PricingParametersLive />);
    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    const file = new File(['xlsx'], 'fresh.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await userEvent.upload(screen.getByLabelText('Pricing parameters xlsx'), file);
    await userEvent.click(screen.getByRole('button', { name: /^import$/i }));

    expect(await screen.findByRole('status')).toHaveTextContent('Imported 1 rows.');
    expect(
      within(screen.getByLabelText('Pricing parameter profiles')).getByRole('button', { name: /imported fresh/i }),
    ).toHaveAttribute('aria-current', 'true');

    await act(async () => {
      initialProfiles.resolve(response([profile]));
      await initialProfiles.promise;
    });

    expect(screen.queryByText('2026-04-30 Close')).not.toBeInTheDocument();
    expect(screen.getAllByText('T-FRESH').length).toBeGreaterThan(0);
  });
});

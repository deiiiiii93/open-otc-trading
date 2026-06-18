import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { PortfoliosLive } from './Portfolios.live';

const fetchMock = vi.fn();

beforeEach(() => {
  globalThis.fetch = fetchMock as any;
  fetchMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

function jsonResponse(body: any, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function makePortfolio(overrides: Partial<any> = {}) {
  return {
    id: 1,
    name: 'Snow',
    kind: 'view',
    base_currency: 'USD',
    description: null,
    tags: [],
    filter_rule: null,
    manual_include_ids: [],
    manual_exclude_ids: [],
    source_portfolio_ids: [],
    resolved_position_count: 0,
    created_at: 't',
    updated_at: 't',
    positions: [],
    ...overrides,
  };
}

test('lists portfolios on mount', async () => {
  fetchMock.mockImplementation((url: string) => {
    if (url.endsWith('/api/portfolios')) return jsonResponse([makePortfolio()]);
    if (url.includes('/api/portfolios/1')) return jsonResponse(makePortfolio());
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /snow · view/i })).toBeInTheDocument(),
  );
});

test('opens create dialog and POSTs the new portfolio', async () => {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/api/portfolios') && init?.method === 'POST') {
      return jsonResponse({ ...makePortfolio({ id: 2, name: 'NewView' }) });
    }
    if (url.endsWith('/api/portfolios')) return jsonResponse([makePortfolio()]);
    if (url.includes('/api/portfolios/1')) return jsonResponse(makePortfolio());
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() => expect(screen.getByRole('button', { name: /^new$/i })).toBeInTheDocument());

  await userEvent.click(screen.getByRole('button', { name: /^new$/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /new view portfolio/i }));
  await userEvent.type(screen.getByLabelText(/name/i), 'NewView');
  await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

  await waitFor(() => {
    const postCall = fetchMock.mock.calls.find(([url, init]: any[]) =>
      url === '/api/portfolios' && init?.method === 'POST',
    );
    expect(postCall).toBeTruthy();
    expect(JSON.parse(postCall![1].body)).toEqual({ name: 'NewView', kind: 'view' });
  });
});

test('deduplicates resolved view preview rows across container and view details', async () => {
  const position = {
    id: 10,
    source_trade_id: 'TRADE-10',
    underlying: 'AAPL',
    product_type: 'SnowballOption',
    quantity: -1,
    entry_price: 0,
    status: 'open',
  };
  const view = makePortfolio({
    id: 1,
    name: 'Snow',
    kind: 'view',
    resolved_position_count: 1,
    positions: [position],
  });
  const container = makePortfolio({
    id: 2,
    name: 'Book',
    kind: 'container',
    resolved_position_count: 1,
    positions: [position],
  });

  fetchMock.mockImplementation((url: string) => {
    if (url === '/api/portfolios') return jsonResponse([view, container]);
    if (url === '/api/portfolios/1/membership') {
      return jsonResponse({ portfolio_id: 1, position_ids: [10] });
    }
    if (url === '/api/portfolios/1') return jsonResponse(view);
    if (url === '/api/portfolios/2') return jsonResponse(container);
    return jsonResponse([]);
  });

  render(<PortfoliosLive />);
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /snow · view/i })).toBeInTheDocument(),
  );

  await userEvent.selectOptions(screen.getByLabelText(/select portfolio/i), '1');
  await waitFor(() =>
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('PORTFOLIOS · Snow'),
  );
  await new Promise((resolve) => setTimeout(resolve, 350));
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/portfolios/1/membership')).toBe(true),
  );

  const positionsTile = screen.getByText('POSITIONS').closest('.wl-tile');
  expect(positionsTile).toBeTruthy();
  expect(within(positionsTile as HTMLElement).getByText('1')).toBeInTheDocument();
  expect(screen.getByText('1-1 of 1')).toBeInTheDocument();
});

test('opens delete dialog and DELETEs the portfolio', async () => {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith('/api/portfolios')) return jsonResponse([makePortfolio()]);
    if (url.includes('/api/portfolios/1') && init?.method === 'DELETE') {
      return jsonResponse({}, true);
    }
    if (url.includes('/api/portfolios/1')) return jsonResponse(makePortfolio());
    return jsonResponse([]);
  });
  render(<PortfoliosLive />);
  await waitFor(() => expect(screen.getByRole('combobox', { name: /select portfolio/i })).toBeInTheDocument());

  await userEvent.selectOptions(screen.getByLabelText(/select portfolio/i), '1');
  await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('PORTFOLIOS · Snow'));

  await userEvent.click(screen.getByRole('button', { name: /more actions/i }));
  await userEvent.click(screen.getByRole('menuitem', { name: /delete portfolio/i }));
  await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));

  await waitFor(() => {
    const deleteCall = fetchMock.mock.calls.find(([url, init]: any[]) =>
      url === '/api/portfolios/1' && init?.method === 'DELETE',
    );
    expect(deleteCall).toBeTruthy();
  });
});

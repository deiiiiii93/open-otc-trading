import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { it, expect, vi, beforeEach } from 'vitest'
import { HedgeStrategyLive } from './HedgeStrategy.live'

const proposal = {
  status: 'feasible', portfolio_id: 1, underlying: '000905.SH',
  strategy: 'delta_neutral', risk_run_id: 7, spot: 5600,
  source_artifact_id: 901,
  artifact_generated_at: '2026-06-03T08:00:05Z',
  valuation_as_of: '2026-06-03T08:00:00Z',
  risk_generated_at: '2026-06-03T08:00:01Z',
  expires_at: '2026-06-03T08:05:05Z',
  targets: { delta: 1120000, gamma: 0, vega: 0 },
  bands: { delta: 500000, gamma: 50000, vega: 10000 },
  legs: [{ contract_code: 'IC2406', exchange: 'CFFEX', instrument_type: 'future',
           role: 'delta', multiplier: 200, quantity: -1, key: 'CFFEX:IC2406', instrument_id: 11,
           delta: 1120000, gamma: 0, vega: 0, priced_ok: true, price_error: null,
           option_type: null, strike: null, family: 'index_future' }],
  residual: { delta: 0, gamma: 0, vega: 0 }, in_band: { delta: true },
  binding: [], warnings: [],
}

const infeasibleProposal = {
  ...proposal,
  status: 'infeasible',
  targets: { delta: -6035022, gamma: 0, vega: 0 },
  bands: { delta: 10000, gamma: 50000, vega: 10000 },
  legs: [{ ...proposal.legs[0], contract_code: 'IF2606', quantity: 4, delta: 1481643 }],
  residual: { delta: -108452, gamma: 0, vega: 0 },
  in_band: { delta: false },
  binding: [{ greek: 'delta', shortfall: 98452 }],
  diagnostics: [{
    kind: 'hard_band_residual',
    greek: 'delta',
    target: -6035022,
    band: 10000,
    residual: -108452,
    shortfall: 98452,
    suggested_band: 108452,
    terms: [{ contract_code: 'IF2606', quantity: 4, per_lot: 1481643, contribution: 5926572 }],
  }],
  warnings: [],
}

function mockApi(spy = vi.fn(), overrides: Record<string, () => unknown> = {}) {
  const responders: Record<string, () => unknown> = {
    '/api/hedging/hedgeable': () => ({
      status: 'ok', portfolio_id: 1, risk_run_id: 7, created_at: '2026-06-03T08:00:00',
      stale: false, underlyings: [{ underlying: '000905.SH', targets: { delta: 1120000, gamma: 0, vega: 0 }, spot: 5600 }],
    }),
    '/api/hedging/instruments': () => ([
      { id: 11, underlying_id: 1, family: 'index_future', series_root: 'IC', exchange: 'CFFEX',
        contract_code: 'IC2406', instrument_type: 'future', option_type: null, strike: null,
        expiry: null, multiplier: 200, last_price: 5600, status: 'live', allowed: true },
      { id: 12, underlying_id: 1, family: 'index_future', series_root: 'IC', exchange: 'CFFEX',
        contract_code: 'IC2409', instrument_type: 'future', option_type: null, strike: null,
        expiry: null, multiplier: 200, last_price: 5610, status: 'live', allowed: true },
    ]),
    '/api/hedging/bands': () => ({ delta: 500000, gamma: 50000, vega: 10000 }),
    '/api/hedging/solve': () => proposal,
    '/api/hedging/book': () => ({ status: 'booked', position_ids: [1] }),
    '/api/pricing-parameter-profiles': () => ([
      { id: 3, name: 'EOD_20260603', valuation_date: '2026-06-03T00:00:00',
        source_type: 'default_underlying', source_path: null, status: 'active',
        summary: { row_count: 2 }, created_at: '2026-06-03T00:00:00',
        updated_at: '2026-06-03T00:00:00', rows: [] },
    ]),
    '/api/batch-pricing/runs': () => ({ id: 8, status: 'completed' }),
    ...overrides,
  }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    spy(url, init)
    const key = Object.keys(responders).find((k) => url.includes(k))
    const body = key ? responders[key]() : {}
    if (body && typeof body === 'object' && '__httpError' in (body as Record<string, unknown>)) {
      const detail = String((body as Record<string, unknown>).__httpError)
      return { ok: false, status: 500, text: async () => detail } as Response
    }
    return { ok: true, status: 200, json: async () => body } as Response
  })
}

const baseProps = {
  portfolios: [{ id: 1, name: 'Desk Book' }],
  portfolioId: 1, onPortfolioChange: () => {},
  underlying: '000905.SH', underlyingId: 1,
}

beforeEach(() => { vi.restoreAllMocks() })

it('auto-solves on mount and renders quantities from the API', async () => {
  mockApi()
  render(<HedgeStrategyLive {...baseProps} />)
  // '-1' (a sized quantity) only ever renders in a leg row — unambiguous,
  // unlike the contract code which also appears as an add-leg dropdown option.
  await screen.findByText('-1')
  expect(screen.getByLabelText('swap IC2406')).toBeInTheDocument()
})

it('re-solves with an explicit leg set when a leg is swapped', async () => {
  const spy = vi.fn()
  mockApi(spy)
  render(<HedgeStrategyLive {...baseProps} />)
  const swap = await screen.findByLabelText('swap IC2406')
  fireEvent.change(swap, { target: { value: '12' } })
  await waitFor(() => {
    const solveCall = [...spy.mock.calls].reverse()
      .find(([u]) => String(u).includes('/api/hedging/solve'))
    expect(solveCall).toBeTruthy()
    expect(JSON.parse(solveCall![1].body)).toMatchObject({
      legs: [{ instrument_id: 12, role: 'delta' }],
    })
  })
})

it('books the feasible proposal', async () => {
  const spy = vi.fn()
  mockApi(spy)
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  await waitFor(() => {
    const bookCall = spy.mock.calls.find(([u, init]) =>
      String(u).includes('/api/hedging/book') && init?.method === 'POST')
    expect(bookCall).toBeTruthy()
    const body = JSON.parse(String(bookCall![1].body))
    expect(body).toMatchObject({
      source_artifact_id: 901,
      artifact_generated_at: '2026-06-03T08:00:05Z',
      valuation_as_of: '2026-06-03T08:00:00Z',
      risk_generated_at: '2026-06-03T08:00:01Z',
      expires_at: '2026-06-03T08:05:05Z',
    })
    expect(body).not.toHaveProperty('workflow_id')
  })
})

it('refuses to book a proposal without the immutable evidence tuple', async () => {
  const spy = vi.fn()
  mockApi(spy, {
    '/api/hedging/solve': () => ({
      ...proposal,
      source_artifact_id: undefined,
      artifact_generated_at: undefined,
      valuation_as_of: undefined,
      risk_generated_at: undefined,
      expires_at: undefined,
    }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  fireEvent.click(screen.getByRole('button', { name: /book hedge/i }))

  expect(await screen.findByText(/missing immutable booking evidence/i)).toBeInTheDocument()
  expect(spy.mock.calls.some(([u]) => String(u).includes('/api/hedging/book'))).toBe(false)
})

it('loosens the binding band and re-solves an infeasible proposal', async () => {
  const spy = vi.fn()
  mockApi(spy, {
    '/api/hedging/solve': () => infeasibleProposal,
    '/api/hedging/bands': () => ({ delta: 10000, gamma: 50000, vega: 10000 }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  const loosen = await screen.findByRole('button', { name: /loosen band/i })
  fireEvent.click(loosen)

  await waitFor(() => {
    const putCall = spy.mock.calls.find(([u, init]) =>
      String(u).includes('/api/hedging/bands/1') && init?.method === 'PUT')
    expect(putCall).toBeTruthy()
    expect(JSON.parse(String(putCall![1].body))).toMatchObject({
      delta: 108452,
      gamma: 50000,
      vega: 10000,
    })
  })
  await waitFor(() => {
    const solveCalls = spy.mock.calls.filter(([u]) => String(u).includes('/api/hedging/solve'))
    expect(solveCalls.length).toBeGreaterThanOrEqual(2)
  })
})

it('confirms before booking an infeasible proposal anyway', async () => {
  const spy = vi.fn()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  mockApi(spy, {
    '/api/hedging/solve': () => infeasibleProposal,
    '/api/hedging/bands': () => ({ delta: 10000, gamma: 50000, vega: 10000 }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  const bookAnyway = await screen.findByRole('button', { name: /book anyway/i })
  fireEvent.click(bookAnyway)

  await waitFor(() => expect(confirm).toHaveBeenCalled())
  await waitFor(() => {
    const bookCall = spy.mock.calls.find(([u, init]) =>
      String(u).includes('/api/hedging/book') && init?.method === 'POST')
    expect(bookCall).toBeTruthy()
    expect(JSON.parse(String(bookCall![1].body))).toMatchObject({
      portfolio_id: 1,
      underlying: '000905.SH',
      legs: infeasibleProposal.legs,
      source_artifact_id: 901,
      artifact_generated_at: '2026-06-03T08:00:05Z',
      valuation_as_of: '2026-06-03T08:00:00Z',
      risk_generated_at: '2026-06-03T08:00:01Z',
      expires_at: '2026-06-03T08:05:05Z',
    })
    expect(JSON.parse(String(bookCall![1].body))).not.toHaveProperty('workflow_id')
  })
})

it('runs risk with the selected pricing parameter profile', async () => {
  const spy = vi.fn()
  mockApi(spy)
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByLabelText('pricing profile')
  fireEvent.click(screen.getByRole('button', { name: /run risk/i }))
  await waitFor(() => {
    const runCall = spy.mock.calls.find(([u, init]) =>
      String(u) === '/api/batch-pricing/runs' && init?.method === 'POST')
    expect(runCall).toBeTruthy()
    expect(JSON.parse(String(runCall![1].body))).toMatchObject({
      portfolio_id: 1,
      pricing_parameter_profile_id: 3,
    })
  })
})

it('surfaces risk-run pricing errors before the no-exposure solve message', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    const response = (body: unknown) =>
      ({ ok: true, status: 200, json: async () => body }) as Response
    if (url.includes('/api/pricing-parameter-profiles')) {
      return response([
        { id: 3, name: 'EOD_20260603', valuation_date: '2026-06-03T00:00:00',
          source_type: 'default_underlying', source_path: null, status: 'active',
          summary: {}, created_at: '2026-06-03T00:00:00',
          updated_at: '2026-06-03T00:00:00', rows: [] },
      ])
    }
    if (url.includes('/api/hedging/hedgeable')) {
      return response({
        status: 'ok', portfolio_id: 1, risk_run_id: 15, created_at: '2026-06-03T08:00:00',
        stale: false, underlyings: [],
      })
    }
    if (url.includes('/api/hedging/instruments')) return response([])
    if (url.includes('/api/hedging/bands')) return response({ delta: 500000, gamma: 50000, vega: 10000 })
    if (url.includes('/api/hedging/solve')) {
      return response({
        status: 'no_exposure',
        message: 'No greek exposure to 000905.SH in risk run 15.',
      })
    }
    if (url === '/api/batch-pricing/runs' && init?.method === 'POST') {
      return response({ id: 15, status: 'queued' })
    }
    if (url.includes('/api/risk/runs/15')) {
      return response({
        id: 15,
        status: 'completed_with_errors',
        metrics: {
          positions: [
            {
              underlying: '000905.SH',
              pricing_ok: false,
              greeks_ok: false,
              pricing_error: 'Selected pricing profile cannot extract pricing parameters for position #110',
            },
          ],
        },
      })
    }
    return response({})
  })

  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByLabelText('pricing profile')
  fireEvent.click(screen.getByRole('button', { name: /run risk/i }))
  expect(await screen.findByText(/cannot extract pricing parameters for position #110/i)).toBeInTheDocument()
  expect(screen.queryByText(/No greek exposure/i)).not.toBeInTheDocument()
})

it('shows a booking receipt banner after a successful book', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  expect(await screen.findByText(/Hedge booked to Desk Book/)).toBeInTheDocument()
  expect(screen.getByText(/IC2406 × -1 \(delta\) → position #214/)).toBeInTheDocument()
})

it('shows a persistent error banner when booking fails', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({ __httpError: 'Cannot synthesize option hedge leg' }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  expect(await screen.findByText(/Booking failed — nothing was booked/)).toBeInTheDocument()
  expect(screen.getByText(/Cannot synthesize option hedge leg/)).toBeInTheDocument()
})

it('clears the booking banner on a new solve', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  await screen.findByText(/Hedge booked to Desk Book/)
  fireEvent.click(screen.getByRole('button', { name: /^solve$/i }))
  await waitFor(() =>
    expect(screen.queryByText(/Hedge booked to Desk Book/)).not.toBeInTheDocument())
})

it('navigates to Positions from the booking banner', async () => {
  const onViewPositions = vi.fn()
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  render(<HedgeStrategyLive {...baseProps} onViewPositions={onViewPositions} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  fireEvent.click(await screen.findByRole('button', { name: /view in positions/i }))
  expect(onViewPositions).toHaveBeenCalled()
})

it('clears the booking banner when the underlying changes', async () => {
  mockApi(vi.fn(), {
    '/api/hedging/book': () => ({
      status: 'booked', portfolio_id: 1, underlying: '000905.SH',
      risk_run_id: 7, position_ids: [214],
    }),
  })
  const { rerender } = render(<HedgeStrategyLive {...baseProps} />)
  await screen.findByText('-1')
  const bookBtn = screen.getByRole('button', { name: /book hedge/i })
  await waitFor(() => expect(bookBtn).toBeEnabled())
  fireEvent.click(bookBtn)
  await screen.findByText(/Hedge booked to Desk Book/)
  rerender(<HedgeStrategyLive {...baseProps} underlying="000852.SH" underlyingId={2} />)
  await waitFor(() =>
    expect(screen.queryByText(/Hedge booked to Desk Book/)).not.toBeInTheDocument())
})

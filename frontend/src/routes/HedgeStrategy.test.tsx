import { render, screen, fireEvent } from '@testing-library/react'
import { it, expect, vi } from 'vitest'
import { HedgeStrategy } from './HedgeStrategy'
import type { HedgeableSummary, HedgeBookingResult, HedgeCandidate, HedgeProposal } from '../types'

const leg = {
  key: 'CFFEX:IC2406', instrument_id: 11, contract_code: 'IC2406', exchange: 'CFFEX',
  instrument_type: 'future', role: 'delta', multiplier: 200, quantity: -1,
  delta: 1120000, gamma: 0, vega: 0, priced_ok: true, price_error: null,
  option_type: null, strike: null, family: 'index_future',
}

const proposal: HedgeProposal = {
  status: 'feasible', portfolio_id: 1, underlying: '000905.SH',
  strategy: 'delta_neutral', risk_run_id: 7, spot: 5600,
  targets: { delta: 1120000, gamma: 0, vega: 0 },
  bands: { delta: 500000, gamma: 50000, vega: 10000 },
  legs: [leg],
  residual: { delta: 0, gamma: 0, vega: 0 },
  in_band: { delta: true }, binding: [], warnings: [],
}

const summary: HedgeableSummary = {
  status: 'ok', portfolio_id: 1, risk_run_id: 7,
  created_at: '2026-06-01T08:00:00', stale: true,
  underlyings: [{ underlying: '000905.SH', targets: { delta: 1120000, gamma: 0, vega: 0 }, spot: 5600 }],
}

const candidates: HedgeCandidate[] = [
  { instrument_id: 11, contract_code: 'IC2406', instrument_type: 'future', family: 'index_future' },
  { instrument_id: 12, contract_code: 'IC2409', instrument_type: 'future', family: 'index_future' },
]

const baseProps = {
  portfolios: [{ id: 1, name: 'Desk Book' }, { id: 2, name: 'Prop' }],
  portfolioId: 1,
  onPortfolioChange: () => {},
  pricingProfiles: [],
  pricingProfileId: null,
  onPricingProfileChange: () => {},
  summary, onRunRisk: () => {}, runningRisk: false,
  targets: { delta: 1120000, gamma: 0, vega: 0 },
  strategy: 'delta_neutral' as const,
  onStrategyChange: () => {},
  proposal, onSolve: () => {}, onBook: () => {}, onBookAnyway: () => {}, loading: false,
  candidates, onSwapLeg: () => {}, onRemoveLeg: () => {}, onAddLeg: () => {},
  bands: { delta: 500000, gamma: 50000, vega: 10000 },
  onSaveBands: () => {}, onLoosenBand: () => {},
  bookingResult: null,
  onDismissBookingResult: () => {},
}

it('renders sized legs and an enabled Book button when feasible', () => {
  render(<HedgeStrategy {...baseProps} />)
  expect(screen.getByText('IC2406')).toBeInTheDocument()
  expect(screen.getByText('-1')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /book/i })).toBeEnabled()
})

it('renders the portfolio picker and reports a change', () => {
  const onPortfolioChange = vi.fn()
  render(<HedgeStrategy {...baseProps} onPortfolioChange={onPortfolioChange} />)
  fireEvent.change(screen.getByLabelText('portfolio'), { target: { value: '2' } })
  expect(onPortfolioChange).toHaveBeenCalledWith(2)
})

it('shows the risk-run date with a stale badge and a Run risk button', () => {
  const onRunRisk = vi.fn()
  render(<HedgeStrategy {...baseProps} onRunRisk={onRunRisk} />)
  expect(screen.getByText(/2026-06-01/)).toBeInTheDocument()
  expect(screen.getByText(/stale/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /run risk/i }))
  expect(onRunRisk).toHaveBeenCalled()
})

it('shows exposure KPI tiles with bands before a solve', () => {
  render(<HedgeStrategy {...baseProps} proposal={null} />)
  expect(screen.getByText('Δ CASH')).toBeInTheDocument()
  expect(screen.getByText('1,120,000')).toBeInTheDocument()
  expect(screen.getByText('band ±500,000')).toBeInTheDocument()
  expect(screen.queryByText('Δ RESIDUAL')).not.toBeInTheDocument()
})

it('switches KPI tiles to in-band residuals after a solve', () => {
  const { container } = render(<HedgeStrategy {...baseProps} />)
  expect(screen.getByText('Δ RESIDUAL')).toBeInTheDocument()
  expect(screen.getByText('0 ✓')).toBeInTheDocument() // delta residual, in_band true
  expect(screen.getByText('from 1,120,000 · band ±500,000')).toBeInTheDocument()
  expect(container.querySelector('.wl-tile--pos')).toBeTruthy()
})

it('marks out-of-band residual tiles red', () => {
  const outOfBand: HedgeProposal = {
    ...proposal,
    targets: { delta: -6035022, gamma: 0, vega: 0 },
    residual: { delta: -108452, gamma: 0, vega: 0 },
    in_band: { delta: false },
  }
  const { container } = render(
    <HedgeStrategy {...baseProps} targets={{ delta: -6035022, gamma: 0, vega: 0 }} proposal={outOfBand} />,
  )
  expect(screen.getByText('-108,452 ✗')).toBeInTheDocument()
  expect(container.querySelector('.wl-tile--neg')).toBeTruthy()
})

it('keeps tiles in exposure mode when the proposal is infeasible', () => {
  const infeasible = { ...proposal, status: 'infeasible' as const }
  render(<HedgeStrategy {...baseProps} proposal={infeasible} />)
  expect(screen.getByText('Δ CASH')).toBeInTheDocument()
  expect(screen.queryByText('Δ RESIDUAL')).not.toBeInTheDocument()
})

it('swaps a leg to another candidate', () => {
  const onSwapLeg = vi.fn()
  render(<HedgeStrategy {...baseProps} onSwapLeg={onSwapLeg} />)
  fireEvent.change(screen.getByLabelText('swap IC2406'), { target: { value: '12' } })
  expect(onSwapLeg).toHaveBeenCalledWith('CFFEX:IC2406', 12)
})

it('removes a leg', () => {
  const onRemoveLeg = vi.fn()
  render(<HedgeStrategy {...baseProps} onRemoveLeg={onRemoveLeg} />)
  fireEvent.click(screen.getByRole('button', { name: /remove IC2406/i }))
  expect(onRemoveLeg).toHaveBeenCalledWith('CFFEX:IC2406')
})

it('adds a leg from the addable candidates', () => {
  const onAddLeg = vi.fn()
  render(<HedgeStrategy {...baseProps} onAddLeg={onAddLeg} />)
  fireEvent.change(screen.getByLabelText('add leg'), { target: { value: '12' } })
  expect(onAddLeg).toHaveBeenCalledWith(12)
})

it('edits and saves bands', () => {
  const onSaveBands = vi.fn()
  render(<HedgeStrategy {...baseProps} onSaveBands={onSaveBands} />)
  fireEvent.click(screen.getByRole('button', { name: /^edit$/i }))
  fireEvent.change(screen.getByLabelText('delta band'), { target: { value: '600000' } })
  fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
  expect(onSaveBands).toHaveBeenCalledWith({ delta: 600000, gamma: 50000, vega: 10000 })
})

it('shows the per-lot delta cash in the legs table', () => {
  // Exposure distinct from the per-lot value so the assertion is unambiguous.
  const big = { delta: 5000000, gamma: 0, vega: 0 }
  render(<HedgeStrategy {...baseProps} targets={big} proposal={{ ...proposal, targets: big }} />)
  expect(screen.getByText('1,120,000')).toBeInTheDocument() // leg.delta, the δcash/lot cell
})

it('explains when Δ exposure is too small to trigger a delta hedge (qty 0)', () => {
  const small = { delta: 644000, gamma: 0, vega: 0 }
  const p: HedgeProposal = {
    ...proposal, status: 'feasible', legs: [{ ...leg, quantity: 0 }], targets: small,
  }
  render(<HedgeStrategy {...baseProps} targets={small} proposal={p} />)
  expect(screen.getByText(/too small to trigger a delta hedge/i)).toBeInTheDocument()
})

it('disables Book and shows the binding greek when infeasible', () => {
  const infeasible = { ...proposal, status: 'infeasible' as const,
    in_band: { delta: true, gamma: false }, binding: [{ greek: 'gamma', shortfall: 38000 }] }
  render(<HedgeStrategy {...baseProps} strategy="delta_gamma_neutral" proposal={infeasible} />)
  expect(screen.getByText(/gamma/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /book/i })).toBeDisabled()
})

it('explains hard-band infeasibility and exposes explicit actions', () => {
  const onLoosenBand = vi.fn()
  const onBookAnyway = vi.fn()
  const infeasible: HedgeProposal = {
    ...proposal,
    status: 'infeasible',
    targets: { delta: -6035022, gamma: 0, vega: 0 },
    bands: { delta: 10000, gamma: 50000, vega: 10000 },
    legs: [{ ...leg, contract_code: 'IF2606', quantity: 4, delta: 1481643 }],
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
    warnings: [{ contract_code: 'IO2606-C-4900', error: 'Invalid option type: C' }],
  }

  render(
    <HedgeStrategy
      {...baseProps}
      proposal={infeasible}
      onLoosenBand={onLoosenBand}
      onBookAnyway={onBookAnyway}
    />,
  )

  expect(screen.getByText(/Delta target -6,035,022 \+ 4 x IF2606 1,481,643 = residual -108,452/i))
    .toBeInTheDocument()
  expect(screen.getByText(/IO2606-C-4900: Invalid option type: C/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /loosen band/i }))
  expect(onLoosenBand).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /book anyway/i }))
  expect(onBookAnyway).toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /^book hedge$/i })).toBeDisabled()
})

it('calls onSolve when Solve is clicked', () => {
  const onSolve = vi.fn()
  render(<HedgeStrategy {...baseProps} proposal={null} onSolve={onSolve} />)
  fireEvent.click(screen.getByRole('button', { name: /solve/i }))
  expect(onSolve).toHaveBeenCalled()
})

it('frames the legs in a panel with strategy + leg-count meta', () => {
  render(<HedgeStrategy {...baseProps} />)
  expect(screen.getByText('Hedge legs')).toBeInTheDocument()
  expect(screen.getByText('Δ-neutral · 1 leg')).toBeInTheDocument()
})

const bookedResult: HedgeBookingResult = {
  kind: 'success', portfolioName: 'Desk Book', riskRunDate: '2026-06-01',
  legs: [{ contractCode: 'IC2406', quantity: -1, role: 'delta', positionId: 214 }],
}

it('renders a booking receipt with per-leg position ids', () => {
  render(<HedgeStrategy {...baseProps} bookingResult={bookedResult} />)
  expect(screen.getByText(/Hedge booked to Desk Book/)).toBeInTheDocument()
  expect(screen.getByText(/risk run 2026-06-01/)).toBeInTheDocument()
  expect(screen.getByText(/IC2406 × -1 \(delta\) → position #214/)).toBeInTheDocument()
})

it('renders the booking failure with the API error detail', () => {
  render(<HedgeStrategy {...baseProps} bookingResult={{ kind: 'error', message: 'boom from API' }} />)
  expect(screen.getByText(/Booking failed — nothing was booked/)).toBeInTheDocument()
  expect(screen.getByText('boom from API')).toBeInTheDocument()
})

it('dismisses the booking banner', () => {
  const onDismissBookingResult = vi.fn()
  render(
    <HedgeStrategy {...baseProps} bookingResult={bookedResult}
      onDismissBookingResult={onDismissBookingResult} />,
  )
  fireEvent.click(screen.getByRole('button', { name: /dismiss booking result/i }))
  expect(onDismissBookingResult).toHaveBeenCalled()
})

it('shows the view-in-Positions link only when wired', () => {
  const onViewPositions = vi.fn()
  const { rerender } = render(
    <HedgeStrategy {...baseProps} bookingResult={bookedResult} onViewPositions={onViewPositions} />,
  )
  fireEvent.click(screen.getByRole('button', { name: /view in positions/i }))
  expect(onViewPositions).toHaveBeenCalled()
  rerender(<HedgeStrategy {...baseProps} bookingResult={bookedResult} />)
  expect(screen.queryByRole('button', { name: /view in positions/i })).not.toBeInTheDocument()
})

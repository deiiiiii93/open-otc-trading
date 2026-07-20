import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { HedgeStrategy } from './HedgeStrategy'
import type {
  HedgeableSummary, HedgeBookResponse, HedgeBookingResult, HedgeCandidate, HedgeGreeks,
  HedgeInstrument, HedgeLeg, HedgeProposal, HedgeStrategyName, PricingParameterProfile,
} from '../types'

type MinimalLeg = { instrument_id: number; role: string }

type RiskRunResponse = {
  id: number
  status: string
  metrics?: {
    positions?: Array<{
      underlying?: string | null
      pricing_ok?: boolean | null
      greeks_ok?: boolean | null
      pricing_error?: string | null
      greeks_error?: string | null
    }>
  }
}

const minimal = (l: HedgeLeg): MinimalLeg => ({ instrument_id: l.instrument_id, role: l.role })

export function HedgeStrategyLive(props: {
  portfolios: { id: number; name: string }[]
  portfolioId: number | null
  onPortfolioChange: (id: number | null) => void
  underlying: string
  underlyingId: number
  onViewPositions?: () => void
}) {
  const { portfolios, portfolioId, onPortfolioChange, underlying, underlyingId, onViewPositions } = props
  const [strategy, setStrategy] = useState<HedgeStrategyName>('delta_neutral')
  const [proposal, setProposal] = useState<HedgeProposal | null>(null)
  const [summary, setSummary] = useState<HedgeableSummary | null>(null)
  const [candidates, setCandidates] = useState<HedgeCandidate[]>([])
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([])
  const [pricingProfileId, setPricingProfileId] = useState<number | null>(null)
  const [bands, setBands] = useState<HedgeGreeks | null>(null)
  const [riskError, setRiskError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [runningRisk, setRunningRisk] = useState(false)
  const [bookingResult, setBookingResult] = useState<HedgeBookingResult | null>(null)

  // A booking receipt describes one portfolio+underlying — drop it when either changes.
  useEffect(() => { setBookingResult(null) }, [portfolioId, underlying])

  const cancelled = useRef(false)
  // Reset on setup so StrictMode's remount doesn't leave the flag stuck `true`.
  useEffect(() => {
    cancelled.current = false
    return () => { cancelled.current = true }
  }, [])

  useEffect(() => {
    void api<PricingParameterProfile[]>('/api/pricing-parameter-profiles').then((profiles) => {
      if (cancelled.current) return
      const list = Array.isArray(profiles) ? profiles : []
      setPricingProfiles(list)
      setPricingProfileId((current) => current ?? list[0]?.id ?? null)
    }).catch(() => {
      if (!cancelled.current) setPricingProfiles([])
    })
  }, [])

  const solve = useCallback(async (legsOverride?: MinimalLeg[]) => {
    if (portfolioId == null) return
    setLoading(true)
    try {
      setBookingResult(null)
      const body: Record<string, unknown> = { portfolio_id: portfolioId, underlying, strategy }
      if (legsOverride) body.legs = legsOverride
      const res = await api<HedgeProposal>('/api/hedging/solve', {
        method: 'POST', body: JSON.stringify(body),
      })
      if (!cancelled.current) setProposal(res)
    } finally {
      if (!cancelled.current) setLoading(false)
    }
  }, [portfolioId, underlying, strategy])

  const loadSummary = useCallback(async () => {
    if (portfolioId == null) { setSummary(null); return }
    const s = await api<HedgeableSummary>(`/api/hedging/hedgeable?portfolio_id=${portfolioId}`)
    if (cancelled.current) return
    setSummary(s)
    const hasUnderlying = s.underlyings?.some((row) => row.underlying === underlying)
    if (s.status === 'ok' && s.risk_run_id != null && !hasUnderlying) {
      const run = await api<RiskRunResponse>(`/api/risk/runs/${s.risk_run_id}`)
      if (!cancelled.current) setRiskError(riskRunErrorForUnderlying(run, underlying))
    } else {
      setRiskError(null)
    }
  }, [portfolioId, underlying])

  // Risk-run summary (banner + greek readout) follows the selected portfolio.
  useEffect(() => { setProposal(null); void loadSummary() }, [loadSummary])

  // Swap/add candidates + resolved bands follow the selected underlying.
  useEffect(() => {
    void api<HedgeInstrument[]>(
      `/api/hedging/instruments?underlying_id=${underlyingId}&allowed_only=true&status=live`,
    ).then((rows) => {
      if (cancelled.current) return
      setCandidates(rows.map((i) => ({
        instrument_id: i.id, contract_code: i.contract_code,
        instrument_type: i.instrument_type, family: i.family,
      })))
    })
    void api<HedgeGreeks>(`/api/hedging/bands?underlying_id=${underlyingId}`).then((b) => {
      if (!cancelled.current) setBands(b)
    })
  }, [underlyingId])

  // Auto-solve (and re-solve on strategy change) once a fresh-enough run exists.
  useEffect(() => {
    if (summary?.status === 'ok' && portfolioId != null) void solve()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioId, underlying, strategy, summary?.status, summary?.risk_run_id])

  const roleFor = (instrumentId: number) => {
    const c = candidates.find((x) => x.instrument_id === instrumentId)
    return c && c.instrument_type === 'option' ? 'gamma_vega' : 'delta'
  }

  const swapLeg = (legKey: string, instrumentId: number) => {
    if (!proposal?.legs) return
    void solve(proposal.legs.map((l) =>
      l.key === legKey ? { instrument_id: instrumentId, role: roleFor(instrumentId) } : minimal(l)))
  }
  const removeLeg = (legKey: string) => {
    if (!proposal?.legs) return
    void solve(proposal.legs.filter((l) => l.key !== legKey).map(minimal))
  }
  const addLeg = (instrumentId: number) => {
    const existing = proposal?.legs?.map(minimal) ?? []
    void solve([...existing, { instrument_id: instrumentId, role: roleFor(instrumentId) }])
  }

  const saveBands = async (b: HedgeGreeks) => {
    await api(`/api/hedging/bands/${underlyingId}`, { method: 'PUT', body: JSON.stringify(b) })
    if (cancelled.current) return
    setBands(b)
    void solve()
  }

  const loosenBand = async () => {
    const baseBands = bands ?? proposal?.bands
    if (!proposal?.diagnostics?.length || !baseBands) return
    const next = { ...baseBands }
    for (const diagnostic of proposal.diagnostics) {
      if (diagnostic.kind !== 'hard_band_residual') continue
      next[diagnostic.greek] = Math.max(next[diagnostic.greek], Math.ceil(diagnostic.suggested_band))
    }
    await saveBands(next)
  }

  const runRisk = async () => {
    if (portfolioId == null) return
    setRunningRisk(true)
    setRiskError(null)
    try {
      const run = await api<RiskRunResponse>('/api/batch-pricing/runs', {
        method: 'POST',
        body: JSON.stringify({
          portfolio_id: portfolioId,
          ...(pricingProfileId != null ? { pricing_parameter_profile_id: pricingProfileId } : {}),
        }),
      })
      let finalRun = run
      for (let i = 0; i < 60 && !cancelled.current; i++) {
        const r = await api<RiskRunResponse>(`/api/risk/runs/${run.id}`)
        finalRun = r
        if (r.status !== 'queued' && r.status !== 'running') break
        await new Promise((res) => setTimeout(res, 1000))
      }
      if (cancelled.current) return
      const message = riskRunErrorForUnderlying(finalRun, underlying)
      if (message) {
        setRiskError(message)
        setProposal(null)
        await loadSummary()
        return
      }
      await loadSummary()
    } finally {
      if (!cancelled.current) setRunningRisk(false)
    }
  }

  const bookProposal = async (allowInfeasible: boolean) => {
    if (!proposal || !proposal.legs) return
    if (!allowInfeasible && proposal.status !== 'feasible') return
    if (
      proposal.source_artifact_id == null ||
      !proposal.artifact_generated_at ||
      !proposal.valuation_as_of ||
      !proposal.risk_generated_at ||
      !proposal.expires_at
    ) {
      setBookingResult({
        kind: 'error',
        message: 'Hedge proposal is missing immutable booking evidence. Solve again before booking.',
      })
      return
    }
    setLoading(true)
    try {
      const res = await api<HedgeBookResponse>('/api/hedging/book', {
        method: 'POST',
        body: JSON.stringify({
          portfolio_id: portfolioId, underlying, risk_run_id: proposal.risk_run_id,
          source_artifact_id: proposal.source_artifact_id,
          artifact_generated_at: proposal.artifact_generated_at,
          valuation_as_of: proposal.valuation_as_of,
          risk_generated_at: proposal.risk_generated_at,
          expires_at: proposal.expires_at,
          strategy, spot: proposal.spot, legs: proposal.legs,
        }),
      })
      if (cancelled.current) return
      // Backend books non-zero legs in proposal order; position_ids align with that.
      const bookedLegs = proposal.legs.filter((l) => l.quantity !== 0)
      setBookingResult({
        kind: 'success',
        portfolioName:
          portfolios.find((p) => p.id === portfolioId)?.name ?? `portfolio ${portfolioId}`,
        riskRunDate: summary?.created_at ? summary.created_at.slice(0, 10) : null,
        legs: bookedLegs.map((l, i) => ({
          contractCode: l.contract_code, quantity: l.quantity, role: l.role,
          positionId: res.position_ids[i],
        })),
      })
    } catch (err) {
      if (!cancelled.current) {
        setBookingResult({
          kind: 'error',
          message: err instanceof Error ? err.message : String(err),
        })
      }
    } finally {
      if (!cancelled.current) setLoading(false)
    }
  }

  const book = async () => {
    await bookProposal(false)
  }

  const bookAnyway = async () => {
    if (!proposal?.diagnostics?.some((d) => d.kind === 'hard_band_residual')) return
    const ok = window.confirm(
      'Book this hedge even though the residual is outside the configured band?',
    )
    if (!ok) return
    await bookProposal(true)
  }

  const targets =
    summary?.underlyings?.find((u) => u.underlying === underlying)?.targets ?? null
  const otherMessage =
    proposal && proposal.status !== 'feasible' && proposal.status !== 'infeasible' &&
    proposal.status !== 'no_risk_run' ? proposal.message : null
  const warnMessage = riskError ?? otherMessage

  return (
    <div className="hedge-strategy-live">
      <HedgeStrategy
        portfolios={portfolios} portfolioId={portfolioId} onPortfolioChange={onPortfolioChange}
        pricingProfiles={pricingProfiles} pricingProfileId={pricingProfileId}
        onPricingProfileChange={setPricingProfileId}
        summary={summary} onRunRisk={runRisk} runningRisk={runningRisk}
        targets={targets}
        strategy={strategy} onStrategyChange={setStrategy}
        proposal={proposal} onSolve={() => solve()} onBook={book} onBookAnyway={bookAnyway}
        loading={loading}
        candidates={candidates} onSwapLeg={swapLeg} onRemoveLeg={removeLeg} onAddLeg={addLeg}
        bands={bands} onSaveBands={saveBands} onLoosenBand={loosenBand}
        bookingResult={bookingResult}
        onDismissBookingResult={() => setBookingResult(null)}
        onViewPositions={onViewPositions}
      />
      {warnMessage && <p className="hedge-strategy__note">{warnMessage}</p>}
    </div>
  )
}

function riskRunErrorForUnderlying(run: RiskRunResponse, underlying: string): string | null {
  if (run.status !== 'failed' && run.status !== 'completed_with_errors') return null
  const rows = run.metrics?.positions ?? []
  const matching = rows.filter((row) => row.underlying === underlying)
  const failed = matching.filter((row) => row.pricing_ok === false || row.greeks_ok === false)
  if (failed.length === 0) return null
  const reasons = failed
    .map((row) => row.pricing_error || row.greeks_error)
    .filter((value): value is string => Boolean(value))
  return reasons[0] ?? `Risk run ${run.id} failed to produce greeks for ${underlying}.`
}

import './HedgeStrategy.css'
import { useEffect, useState } from 'react'
import { Button } from '../components/Button'
import { Badge } from '../components/Badge'
import { NumberInput } from '../components/NumberInput'
import { Select } from '../components/Select'
import { type TileVariant } from '../components/Tile'
import { MetricRow, type Metric } from '../components/MetricRow'
import { PanelGrid } from '../components/PanelGrid'
import { Panel } from '../components/Panel'
import type {
  HedgeableSummary, HedgeBookingResult, HedgeCandidate, HedgeDiagnostic, HedgeGreeks,
  HedgeProposal, HedgeStrategyName, PricingParameterProfile,
} from '../types'

const STRATEGIES: { value: HedgeStrategyName; label: string }[] = [
  { value: 'delta_neutral',          label: 'Δ-neutral' },
  { value: 'delta_neutral_enhanced', label: 'Δ-neutral (enhanced)' },
  { value: 'delta_gamma_neutral',    label: 'Δ+Γ-neutral' },
  { value: 'full_neutral',           label: 'Full neutral' },
]

const GREEK_SYMBOLS: Record<keyof HedgeGreeks, string> = {
  delta: 'δ', gamma: 'γ', vega: 'ν',
}

const GREEKS: (keyof HedgeGreeks)[] = ['delta', 'gamma', 'vega']

function fmt(n: number): string {
  return Math.round(n).toLocaleString()
}

function greekLabel(greek: keyof HedgeGreeks): string {
  if (greek === 'delta') return 'Delta'
  if (greek === 'gamma') return 'Gamma'
  return 'Vega'
}

function diagnosticText(diagnostic: HedgeDiagnostic): string {
  const terms = diagnostic.terms
    .filter((term) => term.quantity !== 0)
    .map((term) => `${term.quantity >= 0 ? '+' : '-'} ${fmt(Math.abs(term.quantity))} x ${term.contract_code} ${fmt(term.per_lot)}`)
  const expression = [fmt(diagnostic.target), ...terms].join(' ')
  return `${greekLabel(diagnostic.greek)} target ${expression} = residual ${fmt(diagnostic.residual)}; band is +/-${fmt(diagnostic.band)}, shortfall ${fmt(diagnostic.shortfall)}.`
}

const KPI_GLYPH: Record<keyof HedgeGreeks, string> = { delta: 'Δ', gamma: 'Γ', vega: 'ν' }

/** Exposure tiles that flip to would-be residuals once a solve produced one. */
function KpiTiles(props: {
  targets: HedgeGreeks
  proposal: HedgeProposal | null
  bands: HedgeGreeks | null
}) {
  const { targets, proposal, bands } = props
  const residual =
    proposal && proposal.status !== 'infeasible' && proposal.residual ? proposal.residual : null
  const metrics: Metric[] = GREEKS.map((g) => {
    const band = bands ? `band ±${fmt(bands[g])}` : null
    if (!residual) {
      return {
        label: `${KPI_GLYPH[g]} CASH`,
        value: fmt(targets[g]),
        delta: band ?? undefined,
      }
    }
    const inBand = proposal?.in_band?.[g]
    const variant: TileVariant = inBand === true ? 'pos' : inBand === false ? 'neg' : 'default'
    const mark = inBand === true ? ' ✓' : inBand === false ? ' ✗' : ''
    return {
      label: `${KPI_GLYPH[g]} RESIDUAL`,
      value: `${fmt(residual[g])}${mark}`,
      variant,
      delta: [`from ${fmt(targets[g])}`, band].filter(Boolean).join(' · '),
    }
  })
  return <MetricRow metrics={metrics} className="hedge-strategy__kpis" />
}

/** Outcome of the last Book attempt: success receipt or atomic-failure note. */
function BookingBanner(props: {
  result: HedgeBookingResult
  onDismiss: () => void
  onViewPositions?: () => void
}) {
  const { result, onDismiss, onViewPositions } = props
  if (result.kind === 'error') {
    return (
      <div className="hedge-strategy__banner hedge-strategy__banner--err" role="alert">
        <div className="hedge-strategy__banner-body">
          <p><b>Booking failed — nothing was booked.</b></p>
          <p className="hedge-strategy__banner-detail">{result.message}</p>
        </div>
        <Button variant="ghost" iconOnly aria-label="dismiss booking result" onClick={onDismiss}>✕</Button>
      </div>
    )
  }
  return (
    <div className="hedge-strategy__banner hedge-strategy__banner--ok" role="status">
      <div className="hedge-strategy__banner-body">
        <p>
          <b>Hedge booked to {result.portfolioName}</b>
          {result.riskRunDate && <> · risk run {result.riskRunDate}</>}
          {onViewPositions && (
            <> · <Button variant="ghost" onClick={onViewPositions}>view in Positions</Button></>
          )}
        </p>
        <ul className="hedge-strategy__banner-legs">
          {result.legs.map((l) => (
            <li key={l.positionId}>
              {l.contractCode} × {l.quantity} ({l.role}) → position #{l.positionId}
            </li>
          ))}
        </ul>
      </div>
      <Button variant="ghost" iconOnly aria-label="dismiss booking result" onClick={onDismiss}>✕</Button>
    </div>
  )
}

/** Inline band editor: shows the resolved widths, toggles to editable inputs. */
function BandEditor(props: { bands: HedgeGreeks; onSave: (b: HedgeGreeks) => void }) {
  const { bands, onSave } = props
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<HedgeGreeks>(bands)
  // Keep the draft in sync if the resolved bands change while not editing.
  useEffect(() => { if (!editing) setDraft(bands) }, [bands, editing])

  if (!editing) {
    return (
      <div className="hedge-strategy__bands">
        <span className="hedge-strategy__label">Bands</span>
        {GREEKS.map((g) => (
          <span key={g} className="hedge-strategy__band">
            {GREEK_SYMBOLS[g]} ±{fmt(bands[g])}
          </span>
        ))}
        <Button variant="ghost" onClick={() => { setDraft(bands); setEditing(true) }}>edit</Button>
      </div>
    )
  }
  return (
    <div className="hedge-strategy__bands hedge-strategy__bands--editing">
      <span className="hedge-strategy__label">Bands</span>
      {GREEKS.map((g) => (
        <label key={g} className="hedge-strategy__band">
          {GREEK_SYMBOLS[g]} ±
          <NumberInput
            className="hedge-strategy__input" type="number" value={draft[g]} aria-label={`${g} band`}
            onChange={(e) => setDraft({ ...draft, [g]: Number(e.target.value) })}
          />
        </label>
      ))}
      <Button variant="default" onClick={() => { onSave(draft); setEditing(false) }}>Save</Button>
      <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
    </div>
  )
}

export function HedgeStrategy(props: {
  portfolios: { id: number; name: string }[]
  portfolioId: number | null
  onPortfolioChange: (id: number | null) => void
  pricingProfiles: PricingParameterProfile[]
  pricingProfileId: number | null
  onPricingProfileChange: (id: number | null) => void
  summary: HedgeableSummary | null
  onRunRisk: () => void
  runningRisk: boolean
  targets: HedgeGreeks | null
  strategy: HedgeStrategyName
  onStrategyChange: (s: HedgeStrategyName) => void
  proposal: HedgeProposal | null
  onSolve: () => void
  onBook: () => void
  onBookAnyway: () => void
  loading: boolean
  candidates: HedgeCandidate[]
  onSwapLeg: (legKey: string, instrumentId: number) => void
  onRemoveLeg: (legKey: string) => void
  onAddLeg: (instrumentId: number) => void
  bands: HedgeGreeks | null
  onSaveBands: (b: HedgeGreeks) => void
  onLoosenBand: () => void
  bookingResult: HedgeBookingResult | null
  onDismissBookingResult: () => void
  onViewPositions?: () => void
}) {
  const {
    portfolios, portfolioId, onPortfolioChange,
    pricingProfiles, pricingProfileId, onPricingProfileChange,
    summary, onRunRisk, runningRisk, targets,
    strategy, onStrategyChange, proposal, onSolve, onBook, onBookAnyway, loading,
    candidates, onSwapLeg, onRemoveLeg, onAddLeg, bands, onSaveBands,
    onLoosenBand,
    bookingResult, onDismissBookingResult, onViewPositions,
  } = props
  const feasible = proposal?.status === 'feasible'
  const legs = proposal?.legs ?? []
  const usedIds = new Set(legs.map((l) => l.instrument_id))
  const addable = candidates.filter((c) => !usedIds.has(c.instrument_id))
  const strategyLabel = STRATEGIES.find((s) => s.value === strategy)?.label ?? strategy
  const panelMeta = legs.length > 0
    ? `${strategyLabel} · ${legs.length} leg${legs.length === 1 ? '' : 's'}`
    : strategyLabel
  const runDate = summary?.created_at ? summary.created_at.slice(0, 10) : null
  const hardBandDiagnostics = (proposal?.diagnostics ?? [])
    .filter((d) => d.kind === 'hard_band_residual')
  const canBookAnyway = hardBandDiagnostics.length > 0 && legs.some((l) => l.quantity !== 0)

  // The delta hedge solving to 0 lots means the current Δ exposure is smaller
  // than one whole lot can correct (within band, or one lot would overshoot).
  const deltaLeg = legs.find((l) => l.role === 'delta') ?? legs[0]
  const deltaUnhedged = !!deltaLeg && deltaLeg.quantity === 0
  // Suppress the generic "binding: delta" line when the note below already
  // explains the zero-lot delta case in plain language.
  const otherBinding = (proposal?.binding ?? [])
    .filter((b) => !(deltaUnhedged && b.greek === 'delta'))

  return (
    <div className="hedge-strategy">
      <div className="hedge-strategy__bar">
        <Select
          variant="inline"
          label="portfolio"
          value={portfolioId != null ? String(portfolioId) : ''}
          onChange={(v) => onPortfolioChange(v ? Number(v) : null)}
          options={[
            { value: '', label: '—' },
            ...portfolios.map((p) => ({ value: String(p.id), label: p.name })),
          ]}
        />
        <Select
          variant="inline"
          label="pricing profile"
          value={pricingProfileId != null ? String(pricingProfileId) : ''}
          onChange={(v) => onPricingProfileChange(v ? Number(v) : null)}
          options={[
            { value: '', label: 'None' },
            ...pricingProfiles.map((profile) => ({
              value: String(profile.id),
              label: `${profile.name} · ${profile.valuation_date.slice(0, 10)}`,
            })),
          ]}
        />
        <Select
          variant="inline"
          label="Strategy"
          value={strategy}
          onChange={(v) => onStrategyChange(v as HedgeStrategyName)}
          options={STRATEGIES.map((s) => ({ value: s.value, label: s.label }))}
        />
        {summary?.status === 'ok' && runDate && (
          <span className="hedge-strategy__run">
            <span className="hedge-strategy__label">RiskRun</span>
            <span className="hedge-strategy__run-date">{runDate}</span>
            {summary.stale && <Badge variant="warn">stale</Badge>}
            <Button variant="ghost" onClick={onRunRisk} disabled={runningRisk}>
              {runningRisk ? 'Running…' : 'Run risk'}
            </Button>
          </span>
        )}
      </div>

      {bookingResult && (
        <BookingBanner result={bookingResult} onDismiss={onDismissBookingResult}
          onViewPositions={onViewPositions} />
      )}

      {summary?.status === 'no_risk_run' && (
        <p className="hedge-strategy__warn">
          No completed risk run — run risk first.{' '}
          <Button variant="default" onClick={onRunRisk} disabled={runningRisk || portfolioId == null}>
            {runningRisk ? 'Running…' : 'Run risk'}
          </Button>
        </p>
      )}

      {targets && <KpiTiles targets={targets} proposal={proposal} bands={bands} />}

      <PanelGrid columns={1}>
      <Panel title="Hedge legs" meta={panelMeta} className="hedge-strategy__panel">
        {bands && <BandEditor bands={bands} onSave={onSaveBands} />}

        {legs.length > 0 && (
          <table className="hedge-strategy__legs">
            <thead>
              <tr>
                <th>Contract</th><th>Role</th>
                <th className="num">δcash/lot</th><th className="num">Qty</th>
                <th>Swap</th><th></th>
              </tr>
            </thead>
            <tbody>
              {legs.map((leg) => (
                <tr key={leg.key}>
                  <td>{leg.contract_code}</td>
                  <td>{leg.role}</td>
                  <td className="num">{fmt(leg.delta)}</td>
                  <td className="num">{leg.quantity}</td>
                  <td>
                    <Select
                      label={`swap ${leg.contract_code}`}
                      value=""
                      onChange={(v) => v && onSwapLeg(leg.key, Number(v))}
                      placeholder="swap…"
                      options={[
                        { value: '', label: 'swap…' },
                        ...addable.map((c) => ({ value: String(c.instrument_id), label: c.contract_code })),
                      ]}
                    />
                  </td>
                  <td>
                    <Button variant="ghost" iconOnly aria-label={`remove ${leg.contract_code}`}
                      onClick={() => onRemoveLeg(leg.key)}>✕</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="hedge-strategy__panel-foot">
          {addable.length > 0 && (
            <Select
              label="add leg"
              value=""
              onChange={(v) => v && onAddLeg(Number(v))}
              placeholder="+ add leg…"
              options={[
                { value: '', label: '+ add leg…' },
                ...addable.map((c) => ({ value: String(c.instrument_id), label: c.contract_code })),
              ]}
            />
          )}
          <Button variant="default" onClick={onSolve} disabled={loading || portfolioId == null}>Solve</Button>
        </div>
      </Panel>
      </PanelGrid>

      {proposal && deltaUnhedged && deltaLeg && (
        <p className="hedge-strategy__note">
          Current Δ exposure {fmt(proposal.targets?.delta ?? deltaLeg.delta * deltaLeg.quantity)} is
          too small to trigger a delta hedge — one lot of {deltaLeg.contract_code} is
          δcash {fmt(deltaLeg.delta)}.
        </p>
      )}

      {proposal?.status === 'infeasible' && otherBinding.length > 0 && (
        <p className="hedge-strategy__infeasible">
          Infeasible — binding: {otherBinding.map((b) => b.greek).join(', ')}
        </p>
      )}

      {hardBandDiagnostics.length > 0 && (
        <div className="hedge-strategy__diagnostics">
          {hardBandDiagnostics.map((diagnostic) => (
            <p key={diagnostic.greek}>{diagnosticText(diagnostic)}</p>
          ))}
          <div className="hedge-strategy__diagnostic-actions">
            <Button variant="default" onClick={onLoosenBand} disabled={loading}>
              Loosen band
            </Button>
            {canBookAnyway && (
              <Button variant="danger" onClick={onBookAnyway} disabled={loading}>
                Book anyway
              </Button>
            )}
          </div>
        </div>
      )}

      {proposal?.warnings && proposal.warnings.length > 0 && (
        <div className="hedge-strategy__warnings">
          {proposal.warnings.map((warning) => (
            <p key={`${warning.contract_code}:${warning.error}`}>
              {warning.contract_code}: {warning.error}
            </p>
          ))}
        </div>
      )}

      <div className="hedge-strategy__bookbar">
        <Button variant="primary" onClick={onBook} disabled={!feasible || loading}>Book hedge</Button>
      </div>
    </div>
  )
}

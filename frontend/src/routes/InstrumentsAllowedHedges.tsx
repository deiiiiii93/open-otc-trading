// frontend/src/routes/InstrumentsAllowedHedges.tsx
import { useId } from 'react';
import { Search, X } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { NumberInput } from '../components/NumberInput';
import { Select } from '../components/Select';
import { quoteAgeBucket as _quoteAgeBucket } from './instrumentsShared';
import { InstrumentsPager, usePagination } from './InstrumentsPager';
import './InstrumentsAllowedHedges.css';

// Re-export so existing callers and tests keep working without importing from instrumentsShared.
export { quoteAgeBucket } from './instrumentsShared';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type HedgeMapEntry = {
  id: number;
  instrument_id?: number | null;
  exchange: string;
  contract_code: string;
  family: string;
  series_root: string;
  instrument_type: string;
  option_type: string | null;
  strike: number | null;
  expiry: string | null;
  reconcile_status: string;
};

export type HedgeMapGroup = {
  underlying_id: number;
  underlying_symbol?: string;
  entries: HedgeMapEntry[];
  open_position_count: number;
};

/** Catalog instrument returned by /api/hedging/instruments */
export type HedgeCandidate = {
  id: number;
  underlying_id: number;
  family: string;
  series_root: string;
  exchange: string;
  contract_code: string;
  instrument_type: string;
  option_type: string | null;
  strike: number | null;
  expiry: string | null;
  multiplier: number | null;
  last_price: number | null;
  status: string;
  allowed: boolean;
};

export type CandidateFilters = {
  family: string;
  optionType: '' | 'C' | 'P';
  strikeMin: string;
  strikeMax: string;
  search: string;
};

export const EMPTY_CANDIDATE_FILTERS: CandidateFilters = {
  family: '',
  optionType: '',
  strikeMin: '',
  strikeMax: '',
  search: '',
};

export type QuoteInfo = {
  price: number;
  age_days: number;
};

type Feedback = {
  tone: 'success' | 'error';
  message: string;
};

type Props = {
  groups: HedgeMapGroup[];
  selectedUnderlyingId: number | null;
  onSelectUnderlying: (id: number) => void;
  candidates: HedgeCandidate[];
  candidateFilters: CandidateFilters;
  onCandidateFiltersChange?: (f: CandidateFilters) => void;
  quotesByInstrumentId: Record<number, QuoteInfo>;
  onMark: (instrumentIds: number[]) => Promise<void>;
  onUnmark: (instrumentIds: number[]) => Promise<void>;
  onPurgeStale?: (underlyingId: number) => Promise<void>;
  feedback?: Feedback | null;
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function QuoteCell({ quote }: { quote: QuoteInfo | undefined }) {
  if (!quote) return <span className="wl-ah__quote-empty">—</span>;
  const bucket = _quoteAgeBucket(quote.age_days);
  const ageDays = Math.round(quote.age_days);
  return (
    <span className="wl-ah__quote">
      {quote.price.toLocaleString('en-US', { maximumFractionDigits: 4 })}
      {' '}
      <span className={`wl-ah__age-badge is-${bucket}`}>{ageDays}d</span>
    </span>
  );
}

function ReconcileStatusBadge({ status }: { status: string }) {
  return <span className={`wl-ah__reconcile is-${status}`}>{status}</span>;
}

function TermsText({
  expiry, strike, optionType, multiplier,
}: {
  expiry: string | null;
  strike: number | null;
  optionType: string | null;
  multiplier?: number | null;
}) {
  const parts: string[] = [];
  if (expiry) parts.push(`exp ${expiry}`);
  if (multiplier != null) parts.push(`×${multiplier}`);
  if (optionType) parts.push(optionType);
  if (strike != null) parts.push(`@ ${strike}`);
  return <span>{parts.join(' ') || '—'}</span>;
}

// ---------------------------------------------------------------------------
// Left rail
// ---------------------------------------------------------------------------

function UnderlyingRail({
  groups,
  selectedUnderlyingId,
  onSelectUnderlying,
}: Pick<Props, 'groups' | 'selectedUnderlyingId' | 'onSelectUnderlying'>) {
  if (groups.length === 0) {
    return (
      <div className="wl-ah__rail">
        <p className="wl-ah__rail-empty">No underlyings with exposure.</p>
      </div>
    );
  }

  return (
    <div className="wl-ah__rail">
      {groups.map((g) => {
        const allowedCount = g.entries.length;
        const isWarn = allowedCount === 0 && g.open_position_count > 0;
        const isActive = g.underlying_id === selectedUnderlyingId;
        return (
          <button
            key={g.underlying_id}
            className={[
              'wl-ah__rail-item',
              isActive ? 'is-active' : '',
              isWarn ? 'wl-ah__rail-item--warn' : '',
            ]
              .filter(Boolean)
              .join(' ')}
            onClick={() => onSelectUnderlying(g.underlying_id)}
            aria-pressed={isActive}
          >
            <span className="wl-ah__rail-symbol">{g.underlying_symbol || g.underlying_id}</span>
            <span className="wl-ah__rail-meta">
              {g.open_position_count} pos · {allowedCount} allowed
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right top — current map table
// ---------------------------------------------------------------------------

function MapTable({
  group,
  quotesByInstrumentId,
  onUnmark,
  onPurgeStale,
}: {
  group: HedgeMapGroup | null;
  quotesByInstrumentId: Record<number, QuoteInfo>;
  onUnmark: (ids: number[]) => Promise<void>;
  onPurgeStale?: (underlyingId: number) => Promise<void>;
}) {
  // Hook order: pagination must run before the early return below.
  const pagination = usePagination(group?.entries ?? [], group?.underlying_id ?? null);

  if (!group) {
    return (
      <Empty message="Select an underlying to see its hedge map." />
    );
  }

  const staleCount = group.entries.filter((e) => e.reconcile_status === 'stale').length;

  return (
    <div className="wl-ah__map-section">
      <div className="wl-ah__map-header">
        <h3 className="wl-ah__section-title">
          Allowed Hedges
          <span className="wl-ah__entry-count">{group.entries.length}</span>
        </h3>
        {onPurgeStale && staleCount > 0 && (
          <Button
            variant="ghost"
            onClick={() => onPurgeStale(group.underlying_id)}
            aria-label="Purge stale entries"
          >
            Purge stale ({staleCount})
          </Button>
        )}
      </div>

      {group.entries.length === 0 ? (
        <Empty message="No allowed hedges — mark candidates below." />
      ) : (
        <div className="wl-ah__table-wrap">
          <table className="wl-ah__table">
            <thead>
              <tr>
                <th>CONTRACT</th>
                <th>FAMILY</th>
                <th>TERMS</th>
                <th>QUOTE</th>
                <th>STATUS</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {pagination.pagedRows.map((e) => {
                // Quote lookup uses the durable instrument_id link so the key
                // matches quotesByInstrumentId (keyed by catalog instrument id).
                // Entries without an instrument_id link (legacy) resolve to -1
                // which will never match, returning undefined (shows "—").
                const quote = quotesByInstrumentId[e.instrument_id ?? -1];
                return (
                  <tr key={e.id} data-testid={`map-row-${e.id}`}>
                    <td>
                      <span className="wl-ah__code">{e.contract_code}</span>
                      <small>{e.exchange}</small>
                    </td>
                    <td>{e.family}</td>
                    <td>
                      <TermsText
                        expiry={e.expiry}
                        strike={e.strike}
                        optionType={e.option_type}
                      />
                    </td>
                    <td>
                      <QuoteCell quote={quote} />
                    </td>
                    <td>
                      <ReconcileStatusBadge status={e.reconcile_status} />
                    </td>
                    <td>
                      <Button
                        variant="ghost"
                        onClick={() => onUnmark([e.id])}
                        aria-label={`Unmark ${e.contract_code}`}
                      >
                        <X size={13} aria-hidden="true" />
                        unmark
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <InstrumentsPager pagination={pagination} label="allowed hedges" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right bottom — candidates table
// ---------------------------------------------------------------------------

function CandidatesTable({
  candidates,
  filters,
  onFiltersChange,
  quotesByInstrumentId,
  onMark,
}: {
  candidates: HedgeCandidate[];
  filters: CandidateFilters;
  onFiltersChange?: (f: CandidateFilters) => void;
  quotesByInstrumentId: Record<number, QuoteInfo>;
  onMark: (ids: number[]) => Promise<void>;
}) {
  const searchId = useId();
  const pagination = usePagination(
    candidates,
    `${filters.family}|${filters.optionType}|${filters.strikeMin}|${filters.strikeMax}|${filters.search}`,
  );

  return (
    <div className="wl-ah__candidates-section">
      <div className="wl-ah__map-header">
        <h3 className="wl-ah__section-title">
          Candidates
          <span className="wl-ah__entry-count">{candidates.length}</span>
        </h3>
      </div>

      {onFiltersChange && (
        <div className="wl-ah__candidate-filters">
          <Select
            variant="inline"
            label="Filter by family"
            value={filters.family}
            onChange={(v) => onFiltersChange({ ...filters, family: v })}
            options={[
              { value: '', label: 'All' },
              { value: 'index_future', label: 'index_future' },
              { value: 'index_option', label: 'index_option' },
              { value: 'commodity_future', label: 'commodity_future' },
              { value: 'commodity_option', label: 'commodity_option' },
            ]}
          />

          <Select
            variant="inline"
            label="Filter by option type"
            value={filters.optionType}
            onChange={(v) =>
              onFiltersChange({
                ...filters,
                optionType: v as CandidateFilters['optionType'],
              })
            }
            options={[
              { value: '', label: 'All' },
              { value: 'C', label: 'C' },
              { value: 'P', label: 'P' },
            ]}
          />

          <NumberInput
            className="wl-ah__strike-input"
            type="number"
            value={filters.strikeMin}
            onChange={(e) => onFiltersChange({ ...filters, strikeMin: e.target.value })}
            placeholder="Strike min"
            aria-label="Strike min"
          />
          <NumberInput
            className="wl-ah__strike-input"
            type="number"
            value={filters.strikeMax}
            onChange={(e) => onFiltersChange({ ...filters, strikeMax: e.target.value })}
            placeholder="Strike max"
            aria-label="Strike max"
          />

          <label className="wl-ah__search-wrap" htmlFor={searchId}>
            <Search size={13} aria-hidden="true" />
            <input
              id={searchId}
              type="search"
              value={filters.search}
              onChange={(e) => onFiltersChange({ ...filters, search: e.target.value })}
              placeholder="Search contract…"
              aria-label="Search candidates"
            />
          </label>
        </div>
      )}

      {candidates.length === 0 ? (
        <Empty message="No candidates match the current filters." />
      ) : (
        <div className="wl-ah__table-wrap">
          <table className="wl-ah__table">
            <thead>
              <tr>
                <th>CONTRACT</th>
                <th>FAMILY</th>
                <th>TERMS</th>
                <th>QUOTE</th>
                <th>STATUS</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {pagination.pagedRows.map((c) => {
                const quote = quotesByInstrumentId[c.id];
                return (
                  <tr key={c.id} data-testid={`candidate-row-${c.id}`}>
                    <td>
                      <span className="wl-ah__code">{c.contract_code}</span>
                      <small>{c.exchange}</small>
                    </td>
                    <td>{c.family}</td>
                    <td>
                      <TermsText
                        expiry={c.expiry}
                        strike={c.strike}
                        optionType={c.option_type}
                        multiplier={c.multiplier}
                      />
                    </td>
                    <td>
                      <QuoteCell quote={quote} />
                    </td>
                    <td>
                      <span className={`wl-ah__status is-${c.status}`}>{c.status}</span>
                    </td>
                    <td>
                      {c.allowed ? (
                        <span className="wl-ah__already-marked">marked</span>
                      ) : (
                        <Button
                          variant="ghost"
                          onClick={() => onMark([c.id])}
                          aria-label={`Mark ${c.contract_code}`}
                        >
                          mark
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <InstrumentsPager pagination={pagination} label="candidates" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export function InstrumentsAllowedHedges({
  groups,
  selectedUnderlyingId,
  onSelectUnderlying,
  candidates,
  candidateFilters,
  onCandidateFiltersChange,
  quotesByInstrumentId,
  onMark,
  onUnmark,
  onPurgeStale,
  feedback,
}: Props) {
  const selectedGroup = groups.find((g) => g.underlying_id === selectedUnderlyingId) ?? null;

  return (
    <div className="wl-ah">
      {feedback && (
        <div
          className={`wl-ah__feedback is-${feedback.tone}`}
          role={feedback.tone === 'error' ? 'alert' : 'status'}
        >
          {feedback.message}
        </div>
      )}

      <div className="wl-ah__layout">
        {/* Left rail */}
        <UnderlyingRail
          groups={groups}
          selectedUnderlyingId={selectedUnderlyingId}
          onSelectUnderlying={onSelectUnderlying}
        />

        {/* Right pane */}
        <div className="wl-ah__right">
          <MapTable
            group={selectedGroup}
            quotesByInstrumentId={quotesByInstrumentId}
            onUnmark={onUnmark}
            onPurgeStale={onPurgeStale}
          />
          <CandidatesTable
            candidates={candidates}
            filters={candidateFilters}
            onFiltersChange={onCandidateFiltersChange}
            quotesByInstrumentId={quotesByInstrumentId}
            onMark={onMark}
          />
        </div>
      </div>
    </div>
  );
}

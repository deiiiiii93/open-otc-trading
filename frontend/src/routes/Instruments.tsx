import { useId, useState, type Dispatch, type SetStateAction } from 'react';
import { Check, Download, Plus, RefreshCw, X, Pencil } from 'lucide-react';
import { PageScaffold } from '../components/templates/PageScaffold';
import { PageToolbar, PageToolbarSpacer, PageToolbarSearch } from '../components/PageToolbar';
import { Tabs, TabsList, TabsTrigger } from '../components/Tabs';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { NumberInput } from '../components/NumberInput';
import { Select } from '../components/Select';
import { InstrumentCreateDialog } from '../components/InstrumentCreateDialog';
import type { InstrumentCreateFormData } from '../components/InstrumentCreateDialog';
import { InstrumentsAllowedHedges } from './InstrumentsAllowedHedges';
import type {
  HedgeMapGroup,
  HedgeCandidate,
  CandidateFilters,
  QuoteInfo,
} from './InstrumentsAllowedHedges';
import { InstrumentsMarketData } from './InstrumentsMarketData';
import {
  EMPTY_QUOTE_FILTERS,
  type MarketDataSubTab,
  type MarketQuote,
  type ManualQuotePayload,
  type QuoteFilters,
} from './InstrumentsMarketData';
import {
  EMPTY_DEFAULTS_FILTERS,
  InstrumentsAssumptions,
  defaultsRowState,
} from './InstrumentsAssumptions';
import type { AssumptionSet, DefaultsFilters } from './InstrumentsAssumptions';
import { InstrumentsPager, usePagination } from './InstrumentsPager';
import type { FxRate, MarketDataProfile, UnderlyingPricingDefault } from '../types';
import { TagEditor } from '../components/TagEditor';
import './Instruments.css';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type Instrument = {
  id: number;
  symbol: string;
  display_name: string | null;
  kind: string;
  exchange: string | null;
  currency: string;
  status: string;
  source: string;
  akshare_symbol: string | null;
  akshare_asset_class: string | null;
  contract_code: string | null;
  series_root: string | null;
  expiry: string | null;
  multiplier: number | null;
  strike: number | null;
  option_type: string | null;
  parent_id: number | null;
  loaded_at: string | null;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  notes: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type Tab = 'registry' | 'allowed-hedges' | 'market-data' | 'assumptions';

type Feedback = {
  tone: 'success' | 'error';
  message: string;
};

type Props = {
  rows: Instrument[];
  loading: boolean;
  error: string | null;
  feedback: Feedback | null;
  syncing: boolean;
  loadInProgress: boolean;
  /** Chip text shown while a load task is running, e.g. "task 7 · 3/10 · importing" */
  loadTaskChip: string | null;
  kindFilter: string;
  statusFilter: string;
  search: string;
  onKindFilterChange: (v: string) => void;
  onStatusFilterChange: (v: string) => void;
  onSearchChange: (v: string) => void;
  onSync: () => Promise<void>;
  onLoad: () => Promise<void>;
  onSaveInstrument: (id: number, fields: Partial<Instrument>) => Promise<void>;
  onSetInstrumentTags: (id: number, tags: string[]) => Promise<void>;
  onCreateInstrument: (fields: InstrumentCreateFormData) => Promise<void>;
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
  // Allowed Hedges tab props
  hedgeGroups: HedgeMapGroup[];
  selectedHedgeUnderlyingId: number | null;
  onSelectHedgeUnderlying: (id: number) => void;
  hedgeCandidates: HedgeCandidate[];
  hedgeCandidateFilters: CandidateFilters;
  onHedgeCandidateFiltersChange: (f: CandidateFilters) => void;
  quotesByInstrumentId: Record<number, QuoteInfo>;
  onHedgeMark: (ids: number[]) => Promise<void>;
  onHedgeUnmark: (ids: number[]) => Promise<void>;
  onHedgePurgeStale: (underlyingId: number) => Promise<void>;
  // Market Data tab props
  marketQuotes: MarketQuote[];
  marketQuotesLoading: boolean;
  marketQuoteHistory: MarketQuote[];
  marketQuoteHistoryInstrumentId: number | null;
  marketQuoteHistoryLoading: boolean;
  marketRefreshing: boolean;
  marketRefreshFeedback: string | null;
  marketProfiles: MarketDataProfile[];
  marketProfilesLoading: boolean;
  fxRates: FxRate[];
  fxRatesLoading: boolean;
  fxFeedback: string | null;
  fxFetching: boolean;
  onRefreshQuotes: () => Promise<void>;
  onManualQuote: (payload: ManualQuotePayload) => Promise<void>;
  onSelectQuoteHistory: (instrumentId: number) => void;
  onCloseQuoteHistory: () => void;
  onCreateFxRate: (payload: Omit<FxRate, 'id'>) => Promise<void>;
  onFetchFxRateAkshare: (base: string, quote: string) => Promise<void>;
  onDeleteFxRate: (id: number) => Promise<void>;
  // Assumptions tab props
  assumptionDefaults: UnderlyingPricingDefault[];
  assumptionUnderlyingRoleSymbols: string[];
  assumptionSets: AssumptionSet[];
  assumptionSelectedSetId: number | null;
  assumptionBuilding: boolean;
  assumptionRefreshing: boolean;
  assumptionBuildFeedback: string | null;
  assumptionBuildUnfilled: string[] | null;
  onAssumptionBuild: () => void;
  onAssumptionSelectSet: (id: number | null) => void;
  onAssumptionRefreshFromPositions: () => void;
  onAssumptionUpsert: (
    underlying: string,
    fields: { rate: number | null; dividend_yield: number | null; volatility: number | null },
  ) => void;
};

// ---------------------------------------------------------------------------
// Exported pure helper — unit-testable
// ---------------------------------------------------------------------------

/**
 * Returns true when the instrument kind implies an AKShare mapping but the
 * reported akshare_asset_class disagrees.
 *
 * Kinds that are NOT expected to have an AKShare fetch (listed_option, sge_spot)
 * always return false. When akshare_asset_class is null we assume the mapping
 * hasn't been filled yet and return false (no warning until data is present).
 */
export function mappingMismatch(kind: string, akshareAssetClass: string | null): boolean {
  // These kinds don't have AKShare coverage — suppress warnings entirely.
  if (kind === 'listed_option' || kind === 'sge_spot') return false;
  if (akshareAssetClass === null) return false;
  return kind !== akshareAssetClass;
}

// ---------------------------------------------------------------------------
// Draft type for in-row editing
// ---------------------------------------------------------------------------

type Draft = {
  status: string;
  display_name: string;
  currency: string;
  akshare_symbol: string;
  akshare_asset_class: string;
  kind: string;
  series_root: string;
  expiry: string;
  multiplier: string;
  strike: string;
  option_type: string;
  parent_id: string;
  notes: string;
};

function toDraft(row: Instrument): Draft {
  return {
    status: row.status ?? 'draft',
    display_name: row.display_name ?? '',
    currency: row.currency ?? '',
    akshare_symbol: row.akshare_symbol ?? '',
    akshare_asset_class: row.akshare_asset_class ?? '',
    kind: row.kind ?? '',
    series_root: row.series_root ?? '',
    expiry: row.expiry ?? '',
    multiplier: row.multiplier == null ? '' : String(row.multiplier),
    strike: row.strike == null ? '' : String(row.strike),
    option_type: row.option_type ?? '',
    parent_id: row.parent_id == null ? '' : String(row.parent_id),
    notes: row.notes ?? '',
  };
}

function setDraftValue(
  field: keyof Draft,
  value: string,
  setDraft: Dispatch<SetStateAction<Draft | null>>,
) {
  setDraft((current) => (current ? { ...current, [field]: value } : current));
}

/** Build the PATCH body: only fields whose draft value differs from the original row. */
function changedFields(original: Instrument, draft: Draft): Partial<Instrument> {
  const changed: Partial<Instrument> = {};

  if (draft.status !== (original.status ?? 'draft')) changed.status = draft.status;
  if (draft.display_name !== (original.display_name ?? '')) changed.display_name = draft.display_name || null;
  if (draft.currency !== (original.currency ?? '')) changed.currency = draft.currency;
  if (draft.akshare_symbol !== (original.akshare_symbol ?? '')) changed.akshare_symbol = draft.akshare_symbol || null;
  if (draft.akshare_asset_class !== (original.akshare_asset_class ?? '')) changed.akshare_asset_class = draft.akshare_asset_class || null;
  if (draft.kind !== (original.kind ?? '')) changed.kind = draft.kind;
  if (draft.series_root !== (original.series_root ?? '')) changed.series_root = draft.series_root || null;
  if (draft.expiry !== (original.expiry ?? '')) changed.expiry = draft.expiry || null;

  const multiplierNum = draft.multiplier.trim() ? Number(draft.multiplier) : null;
  if (multiplierNum !== (original.multiplier ?? null)) changed.multiplier = multiplierNum;

  const strikeNum = draft.strike.trim() ? Number(draft.strike) : null;
  if (strikeNum !== (original.strike ?? null)) changed.strike = strikeNum;

  if (draft.option_type !== (original.option_type ?? '')) changed.option_type = draft.option_type || null;

  const parentIdNum = draft.parent_id.trim() ? Number(draft.parent_id) : null;
  if (parentIdNum !== (original.parent_id ?? null)) changed.parent_id = parentIdNum;

  if (draft.notes !== (original.notes ?? '')) changed.notes = draft.notes || null;

  return changed;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusBadge({ value }: { value: string }) {
  return <span className={`wl-instruments__status is-${value}`}>{value}</span>;
}

function TermsCell({ row }: { row: Instrument }) {
  if (!row.expiry && !row.multiplier) {
    return <span>—</span>;
  }
  const parts: string[] = [];
  if (row.expiry) parts.push(`exp ${row.expiry}`);
  if (row.multiplier != null) parts.push(`×${row.multiplier}`);
  if (row.option_type) parts.push(row.option_type);
  if (row.strike != null) parts.push(`@ ${row.strike}`);
  return <span>{parts.join(' ')}</span>;
}

function AkshareCell({ row }: { row: Instrument }) {
  const hasMismatch = mappingMismatch(row.kind, row.akshare_asset_class);
  return (
    <span>
      {row.akshare_symbol || '—'}
      {row.akshare_asset_class && <small> {row.akshare_asset_class}</small>}
      {hasMismatch && (
        <span className="wl-instruments__warn-badge" aria-label="AKShare mapping mismatch">
          ⚠ mapping?
        </span>
      )}
    </span>
  );
}

const REGISTRY_KIND_OPTIONS = ['futures', 'index', 'etf', 'stock', 'listed_option', 'sge_spot'];

// ---------------------------------------------------------------------------
// Registry tab content
// ---------------------------------------------------------------------------

function RegistryTab({
  rows,
  pagedRows,
  loading,
  onSaveInstrument,
  onSetInstrumentTags,
}: Pick<Props, 'rows' | 'loading' | 'onSaveInstrument' | 'onSetInstrumentTags'> & {
  pagedRows: Instrument[];
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const startEdit = (row: Instrument) => {
    setEditing(row.id);
    setDraft(toDraft(row));
    setSaveError(null);
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft(null);
    setSaveError(null);
  };

  const saveEdit = async (row: Instrument) => {
    if (!draft) return;
    const fields = changedFields(row, draft);
    setSaving(true);
    setSaveError(null);
    try {
      await onSaveInstrument(row.id, fields);
      cancelEdit();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="wl-instruments__registry">
      {loading ? (
        <Empty message="Loading instruments…" symbol="..." />
      ) : rows.length === 0 ? (
        <Empty message="No instruments match this view." symbol="∅" />
      ) : (
        <div className="wl-instruments__table-wrap">
          <table className="wl-instruments__table">
            <thead>
              <tr>
                <th>SYMBOL</th>
                <th>KIND</th>
                <th>PARENT</th>
                <th>STATUS</th>
                <th>TAGS</th>
                <th>TERMS</th>
                <th>AKSHARE</th>
                <th>NOTES</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {pagedRows.map((row) => {
                const isEditing = editing === row.id;
                const draftRow = isEditing && draft ? draft : null;
                const isDraft = row.status === 'draft';

                return (
                  <tr
                    key={row.id}
                    className={`is-${row.status}${isDraft ? ' wl-instruments__row--draft' : ''}`}
                  >
                    {/* SYMBOL */}
                    <td>
                      <span className="wl-instruments__symbol">{row.symbol}</span>
                      {draftRow ? (
                        <div className="wl-instruments__stack">
                          <input
                            value={draftRow.display_name}
                            onChange={(e) => setDraftValue('display_name', e.target.value, setDraft)}
                            aria-label={`${row.symbol} display name`}
                            placeholder="display name"
                          />
                          <input
                            value={draftRow.currency}
                            onChange={(e) => setDraftValue('currency', e.target.value, setDraft)}
                            aria-label={`${row.symbol} currency`}
                            placeholder="currency"
                          />
                        </div>
                      ) : (
                        row.display_name && (
                          <span className="wl-instruments__name">{row.display_name}</span>
                        )
                      )}
                    </td>

                    {/* KIND */}
                    <td>
                      {draftRow ? (
                        <Select
                          label={`${row.symbol} kind`}
                          value={draftRow.kind}
                          onChange={(v) => setDraftValue('kind', v, setDraft)}
                          options={REGISTRY_KIND_OPTIONS.map((k) => ({ value: k, label: k }))}
                        />
                      ) : (
                        row.kind
                      )}
                    </td>

                    {/* PARENT */}
                    <td>
                      {draftRow ? (
                        <input
                          value={draftRow.parent_id}
                          onChange={(e) => setDraftValue('parent_id', e.target.value, setDraft)}
                          aria-label={`${row.symbol} parent id`}
                          placeholder="parent id"
                          inputMode="numeric"
                        />
                      ) : (
                        row.parent_id ?? '—'
                      )}
                    </td>

                    {/* STATUS */}
                    <td>
                      {draftRow ? (
                        <Select
                          label={`${row.symbol} status`}
                          value={draftRow.status}
                          onChange={(v) => setDraftValue('status', v, setDraft)}
                          options={[
                            { value: 'draft', label: 'draft' },
                            { value: 'active', label: 'active' },
                            { value: 'inactive', label: 'inactive' },
                            { value: 'expired', label: 'expired' },
                          ]}
                        />
                      ) : (
                        <StatusBadge value={row.status} />
                      )}
                    </td>

                    {/* TAGS */}
                    <td>
                      {row.tags.includes('hedge') && (
                        <span
                          className="wl-tageditor__chip wl-tageditor__chip--readonly"
                          title="Auto-managed from Allowed Hedges"
                        >
                          hedge
                        </span>
                      )}
                      <TagEditor
                        tags={row.tags.filter((t) => t !== 'hedge')}
                        onChange={(next) => {
                          void onSetInstrumentTags(row.id, row.tags.includes('hedge') ? [...next, 'hedge'] : next);
                        }}
                      />
                    </td>

                    {/* TERMS */}
                    <td data-testid={`terms-cell-${row.id}`}>
                      {draftRow ? (
                        <div className="wl-instruments__stack">
                          <input
                            value={draftRow.series_root}
                            onChange={(e) => setDraftValue('series_root', e.target.value, setDraft)}
                            aria-label={`${row.symbol} series root`}
                            placeholder="series root"
                          />
                          <input
                            value={draftRow.expiry}
                            onChange={(e) => setDraftValue('expiry', e.target.value, setDraft)}
                            aria-label={`${row.symbol} expiry`}
                            placeholder="expiry (YYYY-MM-DD)"
                          />
                          <input
                            value={draftRow.multiplier}
                            onChange={(e) => setDraftValue('multiplier', e.target.value, setDraft)}
                            aria-label={`${row.symbol} multiplier`}
                            placeholder="multiplier"
                            inputMode="decimal"
                          />
                          <input
                            value={draftRow.strike}
                            onChange={(e) => setDraftValue('strike', e.target.value, setDraft)}
                            aria-label={`${row.symbol} strike`}
                            placeholder="strike"
                            inputMode="decimal"
                          />
                          <input
                            value={draftRow.option_type}
                            onChange={(e) => setDraftValue('option_type', e.target.value, setDraft)}
                            aria-label={`${row.symbol} option type`}
                            placeholder="C / P"
                          />
                        </div>
                      ) : (
                        <TermsCell row={row} />
                      )}
                    </td>

                    {/* AKSHARE */}
                    <td>
                      {draftRow ? (
                        <div className="wl-instruments__stack">
                          <input
                            value={draftRow.akshare_symbol}
                            onChange={(e) => setDraftValue('akshare_symbol', e.target.value, setDraft)}
                            aria-label={`${row.symbol} akshare symbol`}
                            placeholder="akshare symbol"
                          />
                          <input
                            value={draftRow.akshare_asset_class}
                            onChange={(e) => setDraftValue('akshare_asset_class', e.target.value, setDraft)}
                            aria-label={`${row.symbol} akshare asset class`}
                            placeholder="akshare asset class"
                          />
                        </div>
                      ) : (
                        <AkshareCell row={row} />
                      )}
                    </td>

                    {/* NOTES */}
                    <td>
                      {draftRow ? (
                        <textarea
                          value={draftRow.notes}
                          onChange={(e) => setDraftValue('notes', e.target.value, setDraft)}
                          aria-label={`${row.symbol} notes`}
                          rows={2}
                        />
                      ) : (
                        row.notes || '—'
                      )}
                    </td>

                    {/* ACTIONS */}
                    <td>
                      <div className="wl-instruments__actions">
                        {draftRow ? (
                          <>
                            <Button
                              variant="ghost"
                              onClick={() => saveEdit(row)}
                              disabled={saving}
                              aria-label={`Save ${row.symbol}`}
                            >
                              <Check size={14} aria-hidden="true" />
                            </Button>
                            <Button
                              variant="ghost"
                              onClick={cancelEdit}
                              aria-label={`Cancel ${row.symbol}`}
                            >
                              <X size={14} aria-hidden="true" />
                            </Button>
                          </>
                        ) : (
                          <Button
                            variant="ghost"
                            onClick={() => startEdit(row)}
                            aria-label={`Edit ${row.symbol}`}
                          >
                            <Pencil size={14} aria-hidden="true" />
                          </Button>
                        )}
                      </div>
                      {isEditing && saveError && (
                        <div className="wl-instruments__row-error" role="alert">
                          {saveError}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab strip
// ---------------------------------------------------------------------------

const TABS: { id: Tab; label: string }[] = [
  { id: 'registry', label: 'Registry' },
  { id: 'allowed-hedges', label: 'Allowed Hedges' },
  { id: 'market-data', label: 'Market Data' },
  { id: 'assumptions', label: 'Assumptions' },
];

// ---------------------------------------------------------------------------
// Main presentational component
// ---------------------------------------------------------------------------

export function Instruments({
  rows,
  loading,
  error,
  feedback,
  syncing,
  loadInProgress,
  loadTaskChip,
  kindFilter,
  statusFilter,
  search,
  onKindFilterChange,
  onStatusFilterChange,
  onSearchChange,
  onSync,
  onLoad,
  onSaveInstrument,
  onSetInstrumentTags,
  onCreateInstrument,
  activeTab,
  onTabChange,
  hedgeGroups,
  selectedHedgeUnderlyingId,
  onSelectHedgeUnderlying,
  hedgeCandidates,
  hedgeCandidateFilters,
  onHedgeCandidateFiltersChange,
  quotesByInstrumentId,
  onHedgeMark,
  onHedgeUnmark,
  onHedgePurgeStale,
  marketQuotes,
  marketQuotesLoading,
  marketQuoteHistory,
  marketQuoteHistoryInstrumentId,
  marketQuoteHistoryLoading,
  marketRefreshing,
  marketRefreshFeedback,
  marketProfiles,
  marketProfilesLoading,
  fxRates,
  fxRatesLoading,
  fxFeedback,
  fxFetching,
  onRefreshQuotes,
  onManualQuote,
  onSelectQuoteHistory,
  onCloseQuoteHistory,
  onCreateFxRate,
  onFetchFxRateAkshare,
  onDeleteFxRate,
  assumptionDefaults,
  assumptionUnderlyingRoleSymbols,
  assumptionSets,
  assumptionSelectedSetId,
  assumptionBuilding,
  assumptionRefreshing,
  assumptionBuildFeedback,
  assumptionBuildUnfilled,
  onAssumptionBuild,
  onAssumptionSelectSet,
  onAssumptionRefreshFromPositions,
  onAssumptionUpsert,
}: Props) {
  const registrySearchId = useId();
  const marketSearchId = useId();
  const hedgeSearchId = useId();
  const assumptionSearchId = useId();
  const [marketSubTab, setMarketSubTab] = useState<MarketDataSubTab>('quotes');
  const [quoteFilters, setQuoteFilters] = useState<QuoteFilters>(EMPTY_QUOTE_FILTERS);
  const [fetchEventFilters, setFetchEventFilters] = useState({ source: '', search: '' });
  const [fxSearch, setFxSearch] = useState('');
  const [manualQuoteOpen, setManualQuoteOpen] = useState(false);
  const [fxCreateOpen, setFxCreateOpen] = useState(false);
  const [assumptionDefaultsFilters, setAssumptionDefaultsFilters] =
    useState<DefaultsFilters>(EMPTY_DEFAULTS_FILTERS);
  const [assumptionSetSearch, setAssumptionSetSearch] = useState('');
  const [createInstrumentOpen, setCreateInstrumentOpen] = useState(false);
  const registryPagination = usePagination(rows, `${kindFilter}|${statusFilter}|${search}`);
  const selectedHedgeGroup =
    hedgeGroups.find((g) => g.underlying_id === selectedHedgeUnderlyingId) ?? null;
  const staleHedgeCount = selectedHedgeGroup?.entries.filter((e) => e.reconcile_status === 'stale').length ?? 0;
  const quoteKindOptions = [...new Set(marketQuotes.map((q) => q.kind))].sort();
  const quoteSourceOptions = [...new Set(marketQuotes.map((q) => q.source))].sort();
  const profileSourceOptions = [...new Set(marketProfiles.map((p) => p.source))].sort();
  const assumptionRoleSymbols = new Set(
    assumptionUnderlyingRoleSymbols.map((symbol) => symbol.trim().toLowerCase()).filter(Boolean),
  );
  const scopedAssumptionDefaults = assumptionDefaults.filter((row) =>
    assumptionRoleSymbols.has(row.underlying.trim().toLowerCase()),
  );
  const assumptionUnfilledCount = scopedAssumptionDefaults.filter((row) =>
    defaultsRowState(row) === 'unfilled',
  ).length;
  const activeAssumptionSearch = assumptionSelectedSetId == null
    ? assumptionDefaultsFilters.search
    : assumptionSetSearch;
  const onActiveAssumptionSearchChange = (value: string) => {
    if (assumptionSelectedSetId == null) {
      setAssumptionDefaultsFilters({ ...assumptionDefaultsFilters, search: value });
    } else {
      setAssumptionSetSearch(value);
    }
  };
  const marketTabs: { id: MarketDataSubTab; label: string }[] = [
    { id: 'quotes', label: 'Quotes' },
    { id: 'fetch-events', label: 'Fetch Events' },
    { id: 'fx-rates', label: 'FX Rates' },
  ];

  const renderRegistryControls = () => (
    <>
      <Button onClick={onSync} disabled={syncing} title="Sync instruments from current positions">
        <RefreshCw size={15} aria-hidden="true" />
        {syncing ? 'Syncing…' : 'Sync from Positions'}
      </Button>
      <Button onClick={onLoad} disabled={loadInProgress} title="Load contracts from AKShare">
        <Download size={15} aria-hidden="true" />
        {loadInProgress ? 'Loading…' : 'Load Contracts'}
      </Button>
      <Button
        variant="ghost"
        onClick={() => setCreateInstrumentOpen(true)}
        aria-label="New instrument"
        title="Add an instrument manually"
      >
        <Plus size={15} aria-hidden="true" />
        New
      </Button>
      {loadTaskChip && (
        <span className="wl-instruments__load-chip" aria-live="polite">
          {loadTaskChip}
        </span>
      )}
      <PageToolbarSpacer />
      <Select
        variant="inline"
        label="KIND"
        value={kindFilter}
        onChange={(v) => onKindFilterChange(v)}
        options={[
          { value: '', label: 'All' },
          ...REGISTRY_KIND_OPTIONS.map((k) => ({ value: k, label: k })),
        ]}
      />
      <Select
        variant="inline"
        label="STATUS"
        value={statusFilter}
        onChange={(v) => onStatusFilterChange(v)}
        options={[
          { value: '', label: 'All' },
          { value: 'active', label: 'active' },
          { value: 'draft', label: 'draft' },
          { value: 'inactive', label: 'inactive' },
          { value: 'expired', label: 'expired' },
        ]}
      />
      <PageToolbarSearch
        id={registrySearchId}
        value={search}
        onChange={onSearchChange}
        placeholder="Search symbol, exchange…"
        aria-label="Search instruments"
      />
      <InstrumentsPager pagination={registryPagination} label="instruments" />
    </>
  );

  const renderAllowedHedgesControls = () => (
    <>
      {selectedHedgeGroup && staleHedgeCount > 0 && (
        <Button
          variant="ghost"
          onClick={() => onHedgePurgeStale(selectedHedgeGroup.underlying_id)}
          aria-label="Purge stale entries"
        >
          Purge stale ({staleHedgeCount})
        </Button>
      )}
      <Select
        variant="inline"
        label="FAMILY"
        value={hedgeCandidateFilters.family}
        onChange={(v) => onHedgeCandidateFiltersChange({ ...hedgeCandidateFilters, family: v })}
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
        label="C/P"
        value={hedgeCandidateFilters.optionType}
        onChange={(v) =>
          onHedgeCandidateFiltersChange({
            ...hedgeCandidateFilters,
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
        className="wl-instruments__inline-input"
        type="number"
        value={hedgeCandidateFilters.strikeMin}
        onChange={(e) => onHedgeCandidateFiltersChange({ ...hedgeCandidateFilters, strikeMin: e.target.value })}
        placeholder="Strike min"
        aria-label="Strike min"
      />
      <NumberInput
        className="wl-instruments__inline-input"
        type="number"
        value={hedgeCandidateFilters.strikeMax}
        onChange={(e) => onHedgeCandidateFiltersChange({ ...hedgeCandidateFilters, strikeMax: e.target.value })}
        placeholder="Strike max"
        aria-label="Strike max"
      />
      <PageToolbarSpacer />
      <PageToolbarSearch
        id={hedgeSearchId}
        value={hedgeCandidateFilters.search}
        onChange={(v) => onHedgeCandidateFiltersChange({ ...hedgeCandidateFilters, search: v })}
        placeholder="Search contract…"
        aria-label="Search candidates"
      />
    </>
  );

  const renderMarketDataControls = () => (
    <>
      <div className="wl-instruments__subtabs" role="tablist" aria-label="Market data sub-tabs">
        {marketTabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={marketSubTab === tab.id}
            className={`wl-instruments__subtab${marketSubTab === tab.id ? ' is-active' : ''}`}
            onClick={() => setMarketSubTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {marketSubTab === 'quotes' ? (
        <>
          <Button
            onClick={onRefreshQuotes}
            disabled={marketRefreshing}
            aria-label="Refresh quotes"
            title="Refresh quotes (all resolvable)"
          >
            <RefreshCw size={14} aria-hidden="true" className={marketRefreshing ? 'wl-imd-spin' : undefined} />
            {marketRefreshing ? 'Refreshing…' : 'Refresh Quotes'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => setManualQuoteOpen(!manualQuoteOpen)}
            aria-label="Manual quote"
            title="Enter a quote manually"
          >
            <Plus size={14} aria-hidden="true" />
            Manual Quote
          </Button>
          <PageToolbarSpacer />
          <Select
            variant="inline"
            label="KIND"
            value={quoteFilters.kind}
            onChange={(v) => setQuoteFilters({ ...quoteFilters, kind: v })}
            options={[
              { value: '', label: 'All' },
              ...quoteKindOptions.map((k) => ({ value: k, label: k })),
            ]}
          />
          <Select
            variant="inline"
            label="SOURCE"
            value={quoteFilters.source}
            onChange={(v) => setQuoteFilters({ ...quoteFilters, source: v })}
            options={[
              { value: '', label: 'All' },
              ...quoteSourceOptions.map((source) => ({ value: source, label: source })),
            ]}
          />
          <PageToolbarSearch
            id={marketSearchId}
            value={quoteFilters.search}
            onChange={(v) => setQuoteFilters({ ...quoteFilters, search: v })}
            placeholder="Search symbol…"
            aria-label="Search quotes"
          />
        </>
      ) : marketSubTab === 'fetch-events' ? (
        <>
          <PageToolbarSpacer />
          <Select
            variant="inline"
            label="SOURCE"
            value={fetchEventFilters.source}
            onChange={(v) => setFetchEventFilters({ ...fetchEventFilters, source: v })}
            options={[
              { value: '', label: 'All' },
              ...profileSourceOptions.map((source) => ({ value: source, label: source })),
            ]}
          />
          <PageToolbarSearch
            id={marketSearchId}
            value={fetchEventFilters.search}
            onChange={(v) => setFetchEventFilters({ ...fetchEventFilters, search: v })}
            placeholder="Search name, symbol…"
            aria-label="Search fetch events"
          />
        </>
      ) : (
        <>
          <Button
            variant="ghost"
            onClick={() => setFxCreateOpen(!fxCreateOpen)}
            aria-label="Add manually"
          >
            <Plus size={14} aria-hidden="true" />
            Add Manually
          </Button>
          <PageToolbarSpacer />
          <PageToolbarSearch
            id={marketSearchId}
            value={fxSearch}
            onChange={setFxSearch}
            placeholder="Search pair, source…"
            aria-label="Search FX rates"
          />
        </>
      )}
    </>
  );

  const renderAssumptionsControls = () => (
    <>
      <Button
        type="button"
        disabled={assumptionBuilding}
        onClick={onAssumptionBuild}
        aria-label={assumptionUnfilledCount > 0 ? `Build assumptions (${assumptionUnfilledCount} unfilled)` : 'Build assumptions'}
      >
        Build Assumptions
        {assumptionUnfilledCount > 0 && (
          <span className="wl-assumptions__unfilled-chip" aria-label={`${assumptionUnfilledCount} unfilled`}>
            {assumptionUnfilledCount} unfilled
          </span>
        )}
      </Button>
      {assumptionSets.length > 0 && (
        <Select
          variant="inline"
          label="SET"
          value={assumptionSelectedSetId != null ? String(assumptionSelectedSetId) : ''}
          onChange={(v) => onAssumptionSelectSet(v ? Number(v) : null)}
          options={[
            { value: '', label: 'none' },
            ...assumptionSets.map((set) => ({
              value: String(set.id),
              label: `${set.name} (${set.valuation_date.slice(0, 10)})`,
            })),
          ]}
        />
      )}
      <Button
        type="button"
        variant="ghost"
        disabled={assumptionRefreshing}
        onClick={onAssumptionRefreshFromPositions}
      >
        Refresh from Positions
      </Button>
      <PageToolbarSpacer />
      {assumptionSelectedSetId == null && (
        <Select
          variant="inline"
          label="STATE"
          value={assumptionDefaultsFilters.state}
          onChange={(v) =>
            setAssumptionDefaultsFilters({
              ...assumptionDefaultsFilters,
              state: v as DefaultsFilters['state'],
            })
          }
          options={[
            { value: '', label: 'All' },
            { value: 'complete', label: 'complete' },
            { value: 'unfilled', label: 'unfilled' },
          ]}
        />
      )}
      <PageToolbarSearch
        id={assumptionSearchId}
        value={activeAssumptionSearch}
        onChange={onActiveAssumptionSearchChange}
        placeholder={assumptionSelectedSetId == null ? 'Search underlying…' : 'Search symbol…'}
        aria-label={assumptionSelectedSetId == null ? 'Search defaults' : 'Search set rows'}
      />
    </>
  );

  const renderActiveControls = () => {
    if (activeTab === 'registry') return renderRegistryControls();
    if (activeTab === 'allowed-hedges') return renderAllowedHedgesControls();
    if (activeTab === 'market-data') return renderMarketDataControls();
    return renderAssumptionsControls();
  };

  const tabCount = (id: Tab) =>
    id === 'registry'
      ? rows.length
      : id === 'allowed-hedges'
        ? hedgeCandidates.length
        : id === 'market-data'
          ? marketQuotes.length
          : scopedAssumptionDefaults.length;

  const feedbackNode = (feedback || error) ? (
    <>
      {feedback && (
        <div
          className={`wl-instruments__feedback is-${feedback.tone}`}
          role={feedback.tone === 'error' ? 'alert' : 'status'}
        >
          {feedback.message}
        </div>
      )}
      {error && (
        <div className="wl-instruments__feedback is-error" role="alert">
          {error}
        </div>
      )}
    </>
  ) : undefined;

  return (
    <PageScaffold
      title="Instruments"
      chips={['Master Data', `${rows.length} total`]}
      feedback={feedbackNode}
    >
      <Tabs value={activeTab} onValueChange={(v: string) => onTabChange(v as Tab)}>
        <TabsList aria-label="Instruments tabs">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label}
              <span className="wl-instruments__tab-count">{tabCount(tab.id)}</span>
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <PageToolbar>
        {renderActiveControls()}
      </PageToolbar>

      {/* Tab content */}
      {activeTab === 'registry' ? (
        <RegistryTab
          rows={rows}
          pagedRows={registryPagination.pagedRows}
          loading={loading}
          onSaveInstrument={onSaveInstrument}
          onSetInstrumentTags={onSetInstrumentTags}
        />
      ) : activeTab === 'allowed-hedges' ? (
        <InstrumentsAllowedHedges
          groups={hedgeGroups}
          selectedUnderlyingId={selectedHedgeUnderlyingId}
          onSelectUnderlying={onSelectHedgeUnderlying}
          candidates={hedgeCandidates}
          candidateFilters={hedgeCandidateFilters}
          quotesByInstrumentId={quotesByInstrumentId}
          onMark={onHedgeMark}
          onUnmark={onHedgeUnmark}
          feedback={feedback}
        />
      ) : activeTab === 'market-data' ? (
        <InstrumentsMarketData
          quotes={marketQuotes}
          quotesLoading={marketQuotesLoading}
          quoteHistory={marketQuoteHistory}
          quoteHistoryInstrumentId={marketQuoteHistoryInstrumentId}
          quoteHistoryLoading={marketQuoteHistoryLoading}
          refreshing={marketRefreshing}
          refreshFeedback={marketRefreshFeedback}
          profiles={marketProfiles}
          profilesLoading={marketProfilesLoading}
          fxRates={fxRates}
          fxRatesLoading={fxRatesLoading}
          fxFeedback={fxFeedback}
          fxFetching={fxFetching}
          instruments={rows.map((r) => ({ id: r.id, symbol: r.symbol }))}
          subTab={marketSubTab}
          quoteFilters={quoteFilters}
          fetchEventFilters={fetchEventFilters}
          fxSearch={fxSearch}
          manualQuoteOpen={manualQuoteOpen}
          onManualQuoteOpenChange={setManualQuoteOpen}
          fxCreateOpen={fxCreateOpen}
          onFxCreateOpenChange={setFxCreateOpen}
          onRefreshQuotes={onRefreshQuotes}
          onManualQuote={onManualQuote}
          onSelectQuoteHistory={onSelectQuoteHistory}
          onCloseHistory={onCloseQuoteHistory}
          onCreateFxRate={onCreateFxRate}
          onFetchFxRateAkshare={onFetchFxRateAkshare}
          onDeleteFxRate={onDeleteFxRate}
        />
      ) : (
        <InstrumentsAssumptions
          defaults={assumptionDefaults}
          underlyingRoleSymbols={assumptionUnderlyingRoleSymbols}
          sets={assumptionSets}
          selectedSetId={assumptionSelectedSetId}
          building={assumptionBuilding}
          refreshing={assumptionRefreshing}
          buildFeedback={assumptionBuildFeedback}
          buildUnfilled={assumptionBuildUnfilled}
          defaultsFilters={assumptionDefaultsFilters}
          onDefaultsFiltersChange={setAssumptionDefaultsFilters}
          setSearch={assumptionSetSearch}
          onSetSearchChange={setAssumptionSetSearch}
          onBuild={onAssumptionBuild}
          onSelectSet={onAssumptionSelectSet}
          onRefreshFromPositions={onAssumptionRefreshFromPositions}
          onUpsert={onAssumptionUpsert}
        />
      )}
      <InstrumentCreateDialog
        open={createInstrumentOpen}
        onCancel={() => setCreateInstrumentOpen(false)}
        onCreate={async (data) => {
          await onCreateInstrument(data);
          setCreateInstrumentOpen(false);
        }}
      />
    </PageScaffold>
  );
}

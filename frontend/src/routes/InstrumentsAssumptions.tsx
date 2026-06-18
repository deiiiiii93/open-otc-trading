/**
 * InstrumentsAssumptions — Assumptions tab for the Instruments page.
 *
 * Surfaces instrument-level r/q/vol defaults (editable grid) and assumption
 * sets built from those defaults.  NO import button anywhere on this tab —
 * trade-keyed imports live on the Pricing Params page (Task 15).
 *
 * Layout
 * ─────────────────────────────────────────────────────────────────────────────
 * Toolbar:  [Build assumptions <N unfilled>]  [Set selector ▼]  [Refresh from positions]
 * ─────────────────────────────────────────────────────────────────────────────
 * Defaults grid  (editable; per-field provenance hint)
 * ─────────────────────────────────────────────────────────────────────────────
 * Selected set view  (read-only; provenance from source_payload)
 */

import { useMemo, useState } from 'react';
import { Check, Pencil, Search, X } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Select } from '../components/Select';
import type { UnderlyingPricingDefault } from '../types';
import { InstrumentsPager, usePagination } from './InstrumentsPager';
import './InstrumentsAssumptions.css';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type AssumptionRow = {
  id: number;
  instrument_id: number;
  symbol: string;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  source_payload: {
    manual_input_sources?: Record<string, string>;
    inherited_source_trade_id?: string | null;
    [key: string]: unknown;
  } | null;
};

export type AssumptionSet = {
  id: number;
  name: string;
  valuation_date: string;
  status: string;
  summary: Record<string, unknown>;
  created_at: string;
  rows: AssumptionRow[];
};

export type InstrumentsAssumptionsProps = {
  /** Defaults rows from GET /api/underlying-pricing-defaults */
  defaults: UnderlyingPricingDefault[];
  /** Symbols that currently carry the Registry UNDERLYING role. */
  underlyingRoleSymbols: string[];
  /** All assumption sets (newest first) from GET /api/assumptions/sets */
  sets: AssumptionSet[];
  /** Selected set id (or null) */
  selectedSetId: number | null;
  /** Whether a build is currently in-flight */
  building: boolean;
  /** Whether refresh-from-positions is in flight */
  refreshing: boolean;
  /** Build success feedback message */
  buildFeedback: string | null;
  /** Unfilled underlyings list from a 400 build response */
  buildUnfilled: string[] | null;
  defaultsFilters?: DefaultsFilters;
  onDefaultsFiltersChange?: (filters: DefaultsFilters) => void;
  setSearch?: string;
  onSetSearchChange?: (search: string) => void;
  /** Fire build → POST /api/assumptions/build */
  onBuild: () => void;
  /** Select a set from the selector */
  onSelectSet: (id: number | null) => void;
  /** Refresh defaults from positions */
  onRefreshFromPositions: () => void;
  /** Save edits to a defaults row → PUT /api/underlying-pricing-defaults/{underlying} */
  onUpsert: (
    underlying: string,
    fields: { rate: number | null; dividend_yield: number | null; volatility: number | null },
  ) => void;
};

// ---------------------------------------------------------------------------
// Pure helper — exported for unit testing
// ---------------------------------------------------------------------------

/**
 * Determine row-level state from the three resolved fields.
 *
 * Resolution order applied by the backend already:
 *   instrument_default → inherited_pricing_parameter_row → null (missing)
 *
 * Since the Out schema returns the RESOLVED values, we can only distinguish:
 *   'complete'   — all three fields non-null
 *   'inherited'  — at least one field is null (would need inheritance that wasn't
 *                  available; conceptually "partially filled from inheritance")
 *                  In practice: any-non-null-but-not-all → flag row as needing
 *                  attention. We use 'unfilled' for any null field.
 *
 * Per-field provenance:
 *   field != null  → 'complete' (has a value — either explicitly set or inherited)
 *   field == null  → 'unfilled' (no value, not resolvable)
 *
 * Row-level state:
 *   all fields complete → 'complete'
 *   any null           → 'unfilled'
 *
 * The 'inherited' state is reserved for when exactly some fields are provided
 * by the inherited defaults. Since we cannot distinguish from the Out schema
 * alone, we emit 'inherited' only when is_complete is true but no explicit
 * value is set (i.e., it came from inheritance — inferred by is_complete=true
 * while some field has a value via resolution).
 *
 * Simplified contract used here:
 *   - 'complete'  — all three non-null
 *   - 'unfilled'  — any null (missing after full resolution)
 */
export function defaultsRowState(
  row: Pick<UnderlyingPricingDefault, 'rate' | 'dividend_yield' | 'volatility'>,
): 'complete' | 'unfilled' {
  if (row.rate != null && row.dividend_yield != null && row.volatility != null) {
    return 'complete';
  }
  return 'unfilled';
}

/**
 * Per-field provenance label displayed under each value in the defaults grid.
 *
 * Since UnderlyingPricingDefaultOut.rate is the resolved value (instrument
 * default OR inherited), we label:
 *   value non-null → 'default'  (value is present — either explicit or inherited)
 *   value null     → 'missing'
 *
 * When a set is available, the set's source_payload.manual_input_sources gives
 * the true instrument_default vs inherited_pricing_parameter_row distinction.
 * That's surfaced in the set view, not the defaults grid.
 */
export function fieldProvenance(value: number | null): 'default' | 'missing' {
  return value != null ? 'default' : 'missing';
}

// ---------------------------------------------------------------------------
// Pure filter helpers — exported for unit testing
// ---------------------------------------------------------------------------

export type DefaultsFilters = {
  state: '' | 'complete' | 'unfilled';
  search: string;
};

export const EMPTY_DEFAULTS_FILTERS: DefaultsFilters = { state: '', search: '' };

function symbolSet(symbols: string[]): Set<string> {
  return new Set(symbols.map((s) => s.trim().toLowerCase()).filter(Boolean));
}

function hasUnderlyingRole(symbol: string, roleSymbols: Set<string>): boolean {
  return roleSymbols.has(symbol.trim().toLowerCase());
}

export function filterDefaultsByUnderlyingRole(
  rows: UnderlyingPricingDefault[],
  underlyingRoleSymbols: string[],
): UnderlyingPricingDefault[] {
  const roleSymbols = symbolSet(underlyingRoleSymbols);
  return rows.filter((r) => hasUnderlyingRole(r.underlying, roleSymbols));
}

/** Filter defaults rows by open-position scope, completeness state, and underlying search. */
export function filterDefaults(
  rows: UnderlyingPricingDefault[],
  f: DefaultsFilters,
): UnderlyingPricingDefault[] {
  const q = f.search.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (!f.state || defaultsRowState(r) === f.state) &&
      (!q || r.underlying.toLowerCase().includes(q)),
  );
}

export function filterSetRowsByUnderlyingRole(
  rows: AssumptionRow[],
  underlyingRoleSymbols: string[],
): AssumptionRow[] {
  const roleSymbols = symbolSet(underlyingRoleSymbols);
  return rows.filter((r) => hasUnderlyingRole(r.symbol, roleSymbols));
}

/** Filter assumption-set rows by symbol search. */
export function filterSetRows(rows: AssumptionRow[], search: string): AssumptionRow[] {
  const q = search.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) => r.symbol.toLowerCase().includes(q));
}

// ---------------------------------------------------------------------------
// Inline edit state for the defaults grid
// ---------------------------------------------------------------------------

type DraftFields = { rate: string; dividend_yield: string; volatility: string };

function toDraft(row: UnderlyingPricingDefault): DraftFields {
  return {
    rate: row.rate == null ? '' : String(row.rate),
    dividend_yield: row.dividend_yield == null ? '' : String(row.dividend_yield),
    volatility: row.volatility == null ? '' : String(row.volatility),
  };
}

function parseNum(s: string): number | null {
  const v = s.trim();
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------------------
// Set-view provenance string helpers
// ---------------------------------------------------------------------------

/**
 * Human-readable provenance for one field from source_payload.manual_input_sources.
 * 'instrument_default'            → 'default'
 * 'inherited_pricing_parameter_row' → 'inherited · {trade_id}' or 'inherited'
 * anything else / absent          → raw value or '—'
 */
export function formatFieldProvenance(
  field: string,
  row: AssumptionRow,
): string {
  const sources = row.source_payload?.manual_input_sources;
  const src = sources?.[field];
  if (!src) return '—';
  if (src === 'instrument_default') return 'default';
  if (src === 'inherited_pricing_parameter_row') {
    const tradeId = row.source_payload?.inherited_source_trade_id;
    return tradeId ? `inherited · ${tradeId}` : 'inherited';
  }
  return src;
}

// ---------------------------------------------------------------------------
// Defaults grid sub-component
// ---------------------------------------------------------------------------

type DefaultsGridProps = {
  rows: UnderlyingPricingDefault[];
  filters: DefaultsFilters;
  onUpsert: InstrumentsAssumptionsProps['onUpsert'];
  emptyMessage: string;
};

function DefaultsGrid({ rows, filters, onUpsert, emptyMessage }: DefaultsGridProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftFields | null>(null);
  const filtered = useMemo(() => filterDefaults(rows, filters), [rows, filters]);
  const pagination = usePagination(
    filtered,
    `${filters.state}|${filters.search}`,
  );

  const startEdit = (row: UnderlyingPricingDefault) => {
    setEditing(row.underlying);
    setDraft(toDraft(row));
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft(null);
  };

  const saveEdit = (underlying: string) => {
    if (!draft) return;
    onUpsert(underlying, {
      rate: parseNum(draft.rate),
      dividend_yield: parseNum(draft.dividend_yield),
      volatility: parseNum(draft.volatility),
    });
    cancelEdit();
  };

  if (rows.length === 0) {
    return <Empty message={emptyMessage} />;
  }

  return (
    <>
      {filtered.length === 0 ? (
        <Empty message="No defaults match the current filters." />
      ) : (
        <table className="wl-assumptions__defaults-table">
          <thead>
            <tr>
              <th>UNDERLYING</th>
              <th className="num">RATE</th>
              <th className="num">DIV YIELD</th>
              <th className="num">VOL</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pagination.pagedRows.map((row) => {
              const state = defaultsRowState(row);
              const isEditing = editing === row.underlying;
              const d = isEditing && draft ? draft : null;
              return (
                <tr
                  key={row.underlying}
                  className={state === 'unfilled' ? 'wl-assumptions__row--unfilled' : ''}
                >
                  <td>{row.underlying}</td>

                  {/* RATE */}
                  <td className="num">
                    {isEditing ? (
                      <input
                        aria-label="rate"
                        value={d?.rate ?? ''}
                        onChange={(e) =>
                          setDraft((prev) => (prev ? { ...prev, rate: e.target.value } : prev))
                        }
                      />
                    ) : (
                      <>
                        <span>{row.rate != null ? row.rate.toFixed(4) : '—'}</span>
                        <span className="wl-assumptions__provenance">
                          {fieldProvenance(row.rate)}
                        </span>
                      </>
                    )}
                  </td>

                  {/* DIV YIELD */}
                  <td className="num">
                    {isEditing ? (
                      <input
                        aria-label="dividend yield"
                        value={d?.dividend_yield ?? ''}
                        onChange={(e) =>
                          setDraft((prev) =>
                            prev ? { ...prev, dividend_yield: e.target.value } : prev,
                          )
                        }
                      />
                    ) : (
                      <>
                        <span>{row.dividend_yield != null ? row.dividend_yield.toFixed(4) : '—'}</span>
                        <span className="wl-assumptions__provenance">
                          {fieldProvenance(row.dividend_yield)}
                        </span>
                      </>
                    )}
                  </td>

                  {/* VOL */}
                  <td className="num">
                    {isEditing ? (
                      <input
                        aria-label="volatility"
                        value={d?.volatility ?? ''}
                        onChange={(e) =>
                          setDraft((prev) =>
                            prev ? { ...prev, volatility: e.target.value } : prev,
                          )
                        }
                      />
                    ) : (
                      <>
                        <span>{row.volatility != null ? row.volatility.toFixed(4) : '—'}</span>
                        <span className="wl-assumptions__provenance">
                          {fieldProvenance(row.volatility)}
                        </span>
                      </>
                    )}
                  </td>

                  {/* ACTIONS */}
                  <td>
                    <div className="wl-assumptions__actions">
                      {isEditing ? (
                        <>
                          <Button
                            variant="ghost"
                            onClick={() => saveEdit(row.underlying)}
                            aria-label={`Save ${row.underlying}`}
                          >
                            <Check size={14} aria-hidden="true" />
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={cancelEdit}
                            aria-label={`Cancel ${row.underlying}`}
                          >
                            <X size={14} aria-hidden="true" />
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          onClick={() => startEdit(row)}
                          aria-label={`Edit ${row.underlying}`}
                        >
                          <Pencil size={14} aria-hidden="true" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <InstrumentsPager pagination={pagination} label="defaults" />
    </>
  );
}

// ---------------------------------------------------------------------------
// Selected set view sub-component
// ---------------------------------------------------------------------------

type SetViewProps = {
  set: AssumptionSet;
  search: string;
};

function SetView({ set, search }: SetViewProps) {
  const filtered = useMemo(() => filterSetRows(set.rows, search), [set.rows, search]);
  const pagination = usePagination(filtered, `${set.id}|${search}`);

  return (
    <section className="wl-assumptions__set-view">
      <header className="wl-assumptions__set-header">
        <span className="wl-assumptions__set-name">{set.name}</span>
        <span className="wl-assumptions__set-date">
          {set.valuation_date.slice(0, 10)}
        </span>
        <span className="wl-assumptions__set-status">{set.status}</span>
        <span className="wl-assumptions__set-count">
          {set.rows.length} rows
        </span>
      </header>
      <table className="wl-assumptions__set-table">
        <thead>
          <tr>
            <th>SYMBOL</th>
            <th className="num">RATE</th>
            <th className="num">DIV YIELD</th>
            <th className="num">VOL</th>
            <th>PROVENANCE</th>
          </tr>
        </thead>
        <tbody>
          {set.rows.length === 0 ? (
            <tr>
              <td colSpan={5} className="wl-assumptions__empty">No rows in this set.</td>
            </tr>
          ) : filtered.length === 0 ? (
            <tr>
              <td colSpan={5} className="wl-assumptions__empty">No rows match this search.</td>
            </tr>
          ) : (
            pagination.pagedRows.map((row) => (
              <tr key={row.id}>
                <td>{row.symbol}</td>
                <td className="num">{row.rate != null ? row.rate.toFixed(4) : '—'}</td>
                <td className="num">
                  {row.dividend_yield != null ? row.dividend_yield.toFixed(4) : '—'}
                </td>
                <td className="num">
                  {row.volatility != null ? row.volatility.toFixed(4) : '—'}
                </td>
                <td>
                  <span className="wl-assumptions__prov-cell">
                    r: {formatFieldProvenance('rate', row)} ·{' '}
                    q: {formatFieldProvenance('dividend_yield', row)} ·{' '}
                    σ: {formatFieldProvenance('volatility', row)}
                  </span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      <InstrumentsPager pagination={pagination} label="set rows" />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Main presentational component
// ---------------------------------------------------------------------------

export function InstrumentsAssumptions({
  defaults,
  underlyingRoleSymbols,
  sets,
  selectedSetId,
  building,
  refreshing,
  buildFeedback,
  buildUnfilled,
  defaultsFilters,
  onDefaultsFiltersChange,
  setSearch,
  onSetSearchChange,
  onBuild,
  onSelectSet,
  onRefreshFromPositions,
  onUpsert,
}: InstrumentsAssumptionsProps) {
  const standaloneControls = defaultsFilters === undefined;
  const [internalDefaultsFilters, setInternalDefaultsFilters] =
    useState<DefaultsFilters>(EMPTY_DEFAULTS_FILTERS);
  const [internalSetSearch, setInternalSetSearch] = useState('');
  const effectiveDefaultsFilters = defaultsFilters ?? internalDefaultsFilters;
  const effectiveSetSearch = setSearch ?? internalSetSearch;
  const setEffectiveDefaultsFilters = onDefaultsFiltersChange ?? setInternalDefaultsFilters;
  const setEffectiveSetSearch = onSetSearchChange ?? setInternalSetSearch;
  const scopedDefaults = useMemo(
    () => filterDefaultsByUnderlyingRole(defaults, underlyingRoleSymbols),
    [defaults, underlyingRoleSymbols],
  );
  const selectedSet = sets.find((s) => s.id === selectedSetId) ?? null;
  const scopedSelectedSet = useMemo(
    () =>
      selectedSet
        ? {
            ...selectedSet,
            rows: filterSetRowsByUnderlyingRole(selectedSet.rows, underlyingRoleSymbols),
          }
        : null,
    [selectedSet, underlyingRoleSymbols],
  );
  const unfilledCount = scopedDefaults.filter((r) => defaultsRowState(r) === 'unfilled').length;
  const emptyDefaultsMessage =
    defaults.length === 0
      ? 'No underlying defaults yet. Click Refresh from positions to populate.'
      : 'No defaults match instruments with the UNDERLYING role.';

  return (
    <div className="wl-assumptions">
      {standaloneControls && (
        <div className="wl-assumptions__toolbar">
          <Button
            type="button"
            disabled={building}
            onClick={onBuild}
            aria-label={unfilledCount > 0 ? `Build assumptions (${unfilledCount} unfilled)` : 'Build assumptions'}
          >
            Build assumptions
            {unfilledCount > 0 && (
              <span className="wl-assumptions__unfilled-chip" aria-label={`${unfilledCount} unfilled`}>
                {unfilledCount} unfilled
              </span>
            )}
          </Button>

          {sets.length > 0 && (
            <Select
              variant="inline"
              label="Select assumption set"
              value={selectedSetId != null ? String(selectedSetId) : ''}
              onChange={(v) => onSelectSet(v ? Number(v) : null)}
              options={[
                { value: '', label: '— none —' },
                ...sets.map((s) => ({ value: String(s.id), label: `${s.name} (${s.valuation_date.slice(0, 10)})` })),
              ]}
            />
          )}

          <Button
            type="button"
            variant="ghost"
            disabled={refreshing}
            onClick={onRefreshFromPositions}
          >
            Refresh from positions
          </Button>
        </div>
      )}

      {/* ── Build feedback ── */}
      {buildFeedback && (
        <p className="wl-assumptions__feedback is-success" role="status" aria-live="polite">
          {buildFeedback}
        </p>
      )}

      {/* ── Build error: unfilled underlyings ── */}
      {buildUnfilled && buildUnfilled.length > 0 && (
        <div className="wl-assumptions__feedback is-error" role="alert">
          <span>Build failed — missing rate / div / vol for:</span>
          <ul className="wl-assumptions__unfilled-list">
            {buildUnfilled.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Defaults grid ── */}
      <section className="wl-assumptions__defaults">
        <h3 className="wl-assumptions__section-title">Instrument defaults</h3>
        {standaloneControls && (
          <div className="wl-assumptions__filters">
            <Select
              variant="inline"
              label="Filter defaults by state"
              value={effectiveDefaultsFilters.state}
              onChange={(v) =>
                setEffectiveDefaultsFilters({
                  ...effectiveDefaultsFilters,
                  state: v as DefaultsFilters['state'],
                })
              }
              options={[
                { value: '', label: 'All' },
                { value: 'complete', label: 'complete' },
                { value: 'unfilled', label: 'unfilled' },
              ]}
            />

            <label className="wl-assumptions__search">
              <Search size={13} aria-hidden="true" />
              <input
                type="search"
                value={effectiveDefaultsFilters.search}
                onChange={(e) =>
                  setEffectiveDefaultsFilters({ ...effectiveDefaultsFilters, search: e.target.value })
                }
                placeholder="Search underlying…"
                aria-label="Search defaults"
              />
            </label>
          </div>
        )}
        <DefaultsGrid
          rows={scopedDefaults}
          filters={effectiveDefaultsFilters}
          onUpsert={onUpsert}
          emptyMessage={emptyDefaultsMessage}
        />
      </section>

      {/* ── Selected set view ── */}
      {scopedSelectedSet && (
        <>
          {standaloneControls && scopedSelectedSet.rows.length > 0 && (
            <div className="wl-assumptions__filters">
              <label className="wl-assumptions__search">
                <Search size={13} aria-hidden="true" />
                <input
                  type="search"
                  value={effectiveSetSearch}
                  onChange={(e) => setEffectiveSetSearch(e.target.value)}
                  placeholder="Search symbol…"
                  aria-label="Search set rows"
                />
              </label>
            </div>
          )}
          <SetView set={scopedSelectedSet} search={effectiveSetSearch} />
        </>
      )}
    </div>
  );
}

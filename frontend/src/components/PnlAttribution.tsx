import { useState, useMemo } from 'react';
import { Empty } from './Empty';
import { Select } from './Select';
import { formatCount, formatSignedNumber } from './numberFormat';
import './PnlAttribution.css';

export type AttributionPosition = {
  position_id: number;
  source_trade_id?: string | null;
  underlying: string;
  product_type: string;
  quantity: number;
  price: number;
  market_value: number;
  gross_notional: number;
  pnl: number;
  delta_proxy: number;
  delta?: number;
  gamma?: number;
  delta_cash?: number;
  gamma_cash?: number;
  vega?: number;
  theta?: number;
  rho?: number;
  rho_q?: number;
  pricing_ok: boolean;
  pricing_error: string | null;
};

type Props = {
  positions: AttributionPosition[];
  onPromoteToReport?: () => void;
};

type GroupingMode = 'position' | 'underlying';
type GreekRow = {
  key: string;
  underlying: string;
  productSummary: string;
  ids: string[];
  positionCount: number;
  positionId?: number;
  delta_cash: number;
  gamma_cash: number;
  vega: number;
  theta: number;
  rho: number;
  rho_q: number;
};

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];
const GREEK_COLUMNS = [
  { key: 'delta_cash', label: 'Delta Cash' },
  { key: 'gamma_cash', label: 'Gamma Cash' },
  { key: 'vega', label: 'Vega' },
  { key: 'theta', label: 'Theta' },
  { key: 'rho', label: 'Rho' },
  { key: 'rho_q', label: 'RhoQ' },
] as const;

type GreekKey = typeof GREEK_COLUMNS[number]['key'];

function greekValue(position: AttributionPosition, key: GreekKey): number {
  const value = position[key];
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (key === 'delta_cash' && typeof position.delta === 'number' && Number.isFinite(position.delta)) {
    return position.delta;
  }
  if (key === 'delta_cash' && Number.isFinite(position.delta_proxy)) return position.delta_proxy;
  if (key === 'gamma_cash' && typeof position.gamma === 'number' && Number.isFinite(position.gamma)) {
    return position.gamma;
  }
  return 0;
}

function positionRow(position: AttributionPosition): GreekRow {
  return {
    key: `position-${position.position_id}`,
    underlying: position.underlying,
    productSummary: position.product_type,
    ids: [`POS ${formatCount(position.position_id)}`, `TRADE ${position.source_trade_id || '—'}`],
    positionCount: 1,
    positionId: position.position_id,
    delta_cash: greekValue(position, 'delta_cash'),
    gamma_cash: greekValue(position, 'gamma_cash'),
    vega: greekValue(position, 'vega'),
    theta: greekValue(position, 'theta'),
    rho: greekValue(position, 'rho'),
    rho_q: greekValue(position, 'rho_q'),
  };
}

function underlyingRows(positions: AttributionPosition[]): GreekRow[] {
  const groups = new Map<string, { positions: AttributionPosition[]; productTypes: Set<string> }>();
  positions.forEach((position) => {
    const key = position.underlying || 'Unknown';
    const group = groups.get(key) ?? { positions: [], productTypes: new Set<string>() };
    group.positions.push(position);
    if (position.product_type) group.productTypes.add(position.product_type);
    groups.set(key, group);
  });

  return Array.from(groups.entries())
    .map(([underlying, group]) => {
      const productTypes = Array.from(group.productTypes).sort();
      const productSummary = productTypes.length === 1
        ? productTypes[0]
        : `${formatCount(productTypes.length)} product types`;
      return {
        key: `underlying-${underlying}`,
        underlying,
        productSummary,
        ids: [`${formatCount(group.positions.length)} positions`],
        positionCount: group.positions.length,
        delta_cash: group.positions.reduce((acc, position) => acc + greekValue(position, 'delta_cash'), 0),
        gamma_cash: group.positions.reduce((acc, position) => acc + greekValue(position, 'gamma_cash'), 0),
        vega: group.positions.reduce((acc, position) => acc + greekValue(position, 'vega'), 0),
        theta: group.positions.reduce((acc, position) => acc + greekValue(position, 'theta'), 0),
        rho: group.positions.reduce((acc, position) => acc + greekValue(position, 'rho'), 0),
        rho_q: group.positions.reduce((acc, position) => acc + greekValue(position, 'rho_q'), 0),
      };
    })
    .sort((a, b) => a.underlying.localeCompare(b.underlying));
}

export function PnlAttribution({ positions, onPromoteToReport }: Props) {
  const [groupingMode, setGroupingMode] = useState<GroupingMode>('position');
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const rows = useMemo(() => (
    groupingMode === 'position' ? positions.map(positionRow) : underlyingRows(positions)
  ), [positions, groupingMode]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) => (
      row.underlying.toLowerCase().includes(q)
      || row.productSummary.toLowerCase().includes(q)
      || row.ids.some((id) => id.toLowerCase().includes(q))
      || String(row.positionId ?? '').includes(q)
    ));
  }, [rows, searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const startIdx = safePage * pageSize + 1;
  const endIdx = Math.min((safePage + 1) * pageSize, filtered.length);

  const drillDownToUnderlying = (underlying: string) => {
    setGroupingMode('position');
    setSearchQuery(underlying);
    setPage(0);
  };

  const renderRowContent = (row: GreekRow) => (
    <>
      <div className="wl-attr__label">
        <span className="wl-attr__under">{row.underlying}</span>
        <span className="wl-attr__ids">
          {row.ids.map((id) => <span key={id}>{id}</span>)}
        </span>
        <span className="wl-attr__product">{row.productSummary}</span>
      </div>
      <div
        className="wl-attr__greeks"
        aria-label={groupingMode === 'position'
          ? `Greeks for position ${row.positionId}`
          : `Greeks for underlying ${row.underlying}`}
      >
        {GREEK_COLUMNS.map((column) => {
          const value = row[column.key];
          const variant = value >= 0 ? 'pos' : 'neg';
          return (
            <div key={column.key} className="wl-attr__metric">
              <span className="wl-attr__metric-label">{column.label}</span>
              <span className={`wl-attr__value wl-attr__value--${variant}`}>
                {formatSignedNumber(value)}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );

  return (
    <section className="wl-attr">
      <header className="wl-attr__head">
        <span className="wl-attr__title">GREEKS · BY {groupingMode.toUpperCase()}</span>
        <div className="wl-attr__head-actions">
          <div className="wl-attr__toggle" role="group" aria-label="Greek grouping">
            {(['position', 'underlying'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                className={`wl-attr__toggle-btn ${groupingMode === mode ? 'wl-attr__toggle-btn--active' : ''}`.trim()}
                aria-pressed={groupingMode === mode}
                onClick={() => {
                  setGroupingMode(mode);
                  setSearchQuery('');
                  setPage(0);
                }}
              >
                {mode === 'position' ? 'Position' : 'Underlying'}
              </button>
            ))}
          </div>
          {onPromoteToReport && (
            <button
              type="button"
              className="wl-attr__promote"
              aria-label={`Promote Greeks by ${groupingMode} to Report`}
              onClick={onPromoteToReport}
            >
              ↗
            </button>
          )}
        </div>
      </header>
      <div className="wl-attr__body">
        {positions.length === 0 ? (
          <Empty message="No priced positions in this run" symbol="◌" />
        ) : (
          <>
            <div className="wl-attr__toolbar">
              <input
                type="text"
                className="wl-attr__search"
                placeholder={groupingMode === 'position' ? 'Search position / trade ID…' : 'Search underlying…'}
                value={searchQuery}
                onChange={(e) => { setSearchQuery(e.target.value); setPage(0); }}
              />
              <Select
                variant="inline"
                label="Rows per page"
                value={String(pageSize)}
                onChange={(v) => { setPageSize(Number(v)); setPage(0); }}
                options={PAGE_SIZE_OPTIONS.map((s) => ({ value: String(s), label: `${s} / page` }))}
              />
            </div>
            <ul className="wl-attr__list">
              {paged.map((row) => {
                const isUnderlyingRow = groupingMode === 'underlying';
                return (
                  <li key={row.key} className="wl-attr__item">
                    {isUnderlyingRow ? (
                      <button
                        type="button"
                        className="wl-attr__row wl-attr__row--drillable"
                        aria-label={`Drill down to ${row.underlying} positions`}
                        onClick={() => drillDownToUnderlying(row.underlying)}
                      >
                        {renderRowContent(row)}
                      </button>
                    ) : (
                      <div className="wl-attr__row">
                        {renderRowContent(row)}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="wl-attr__pagination">
              <span className="wl-attr__pageinfo">
                {filtered.length > 0
                  ? `${formatCount(startIdx)}–${formatCount(endIdx)} of ${formatCount(filtered.length)}`
                  : '0 results'}
              </span>
              <div className="wl-attr__pagebuttons">
                <button
                  type="button"
                  className="wl-attr__pagebtn"
                  disabled={safePage === 0}
                  onClick={() => setPage(safePage - 1)}
                >
                  ← Prev
                </button>
                <button
                  type="button"
                  className="wl-attr__pagebtn"
                  disabled={safePage >= totalPages - 1}
                  onClick={() => setPage(safePage + 1)}
                >
                  Next →
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

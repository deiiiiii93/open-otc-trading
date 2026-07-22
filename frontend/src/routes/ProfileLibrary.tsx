import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '../components/Button';
import { RailList } from '../components/RailList';
import { RailItem } from '../components/RailItem';
import { Empty } from '../components/Empty';
import { Select } from '../components/Select';
import { Table, type Column } from '../components/Table';
import { Tile } from '../components/Tile';
import type { PricingParameterProfile, PricingParameterRow } from '../types';
import { shortProfileDate } from './pricingBuildReadiness';
import { composeImportSummary } from './pricingImportSummary';

type SourceTypeKey = 'default_underlying' | 'xlsx' | 'market_data_spot';

const SOURCE_TYPE_LABELS: Record<SourceTypeKey, string> = {
  default_underlying: 'DEFAULT',
  xlsx: 'XLSX',
  market_data_spot: 'SPOT',
};

const SOURCE_TYPE_ORDER: SourceTypeKey[] = ['default_underlying', 'xlsx', 'market_data_spot'];

// The RailItem left accent bar doubles as the always-on source-type stripe.
const SOURCE_TYPE_ACCENT: Record<SourceTypeKey, string> = {
  default_underlying: '--info',
  xlsx: '--warn',
  market_data_spot: '--pos',
};

type Props = {
  profiles: PricingParameterProfile[];
  selected: PricingParameterProfile | null;
  loading: boolean;
  filterMode: 'all' | 'live';
  onFilterModeChange: (mode: 'all' | 'live') => void;
  onSelectProfile: (id: number) => void;
};

export function useProfileLibrary({
  profiles,
  selected,
  loading,
  filterMode,
  onFilterModeChange,
  onSelectProfile,
}: Props): { rail: ReactNode; detail: ReactNode } {
  const [sourceFilter, setSourceFilter] = useState<SourceTypeKey | 'all'>('all');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const sourceCounts = useMemo(() => {
    const counts: Record<SourceTypeKey | 'all', number> = {
      all: profiles.length,
      default_underlying: 0,
      xlsx: 0,
      market_data_spot: 0,
    };
    for (const profile of profiles) {
      const key = profile.source_type as SourceTypeKey;
      if (key in counts) counts[key] += 1;
    }
    return counts;
  }, [profiles]);

  const visibleProfiles = useMemo(() => {
    if (sourceFilter === 'all') return profiles;
    return profiles.filter((profile) => profile.source_type === sourceFilter);
  }, [profiles, sourceFilter]);

  const allRows = selected?.rows ?? [];
  const dormantTradeIds = useMemo(
    () => new Set<string>((selected?.summary?.dormant_trade_ids as string[] | undefined) ?? []),
    [selected?.summary?.dormant_trade_ids],
  );
  const filteredRows = useMemo(() => {
    if (filterMode === 'all') return allRows;
    return allRows.filter((row) => (
      row.volatility != null && row.rate != null && row.dividend_yield != null
    ));
  }, [allRows, filterMode]);

  useEffect(() => {
    setPage(0);
  }, [filterMode, pageSize, selected?.id]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pagedRows = useMemo(
    () => filteredRows.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [filteredRows, pageSize, safePage],
  );
  const visibleStart = filteredRows.length === 0 ? 0 : safePage * pageSize + 1;
  const visibleEnd = Math.min(safePage * pageSize + pageSize, filteredRows.length);
  const selectedSourceLabel = selected ? sourceTypeLabel(selected.source_type) : '';
  const importSummary = selected ? composeImportSummary(selected.summary) : '';

  const columns: Column<PricingParameterRow>[] = useMemo(() => {
    const isDormant = (row: PricingParameterRow) => dormantTradeIds.has(row.source_trade_id);
    return [
      { key: 'source_trade_id', header: 'TRADE ID', width: '1.6fr' },
      {
        key: 'position',
        header: 'POSITION',
        width: '1.4fr',
        render: (row) =>
          row.position_id != null
            ? `#${row.position_id}`
            : isDormant(row)
              ? '-'
              : row.source_trade_id,
      },
      {
        key: 'symbol',
        header: 'INSTRUMENT',
        width: '1.2fr',
        render: (row) => row.symbol,
      },
      { key: 'rate', header: 'RATE', numeric: true, render: (row) => fmt(row.rate) },
      { key: 'dividend_yield', header: 'DIV YIELD', numeric: true, render: (row) => fmt(row.dividend_yield) },
      { key: 'volatility', header: 'VOL', numeric: true, render: (row) => fmt(row.volatility) },
      {
        key: 'status',
        header: 'STATUS',
        width: '0.9fr',
        render: (row) => (
          isDormant(row)
            ? <span className="wl-origin is-dormant">DORMANT</span>
            : <span className="wl-origin is-applied">APPLIED</span>
        ),
      },
    ];
  }, [dormantTradeIds]);

  const rail = (
    <RailList>
        {visibleProfiles.map((profile) => (
          <RailItem
            key={profile.id}
            className="wl-pricing-params__profile"
            accent={SOURCE_TYPE_ACCENT[profile.source_type as SourceTypeKey]}
            active={selected?.id === profile.id}
            onClick={() => onSelectProfile(profile.id)}
          >
            <strong className="wl-pricing-params__profile-name wl-rail__title">{profile.name}</strong>
            <span className="wl-pricing-params__profile-meta wl-rail__meta">{shortProfileDate(profile.valuation_date)} · {profile.summary?.row_count ?? profile.rows.length} rows</span>
            <span className={`wl-pricing-params__source-tag is-${profile.source_type}`}>
              {sourceTypeLabel(profile.source_type)}
            </span>
          </RailItem>
        ))}
    </RailList>
  );

  const detail = (
    <section className="wl-pricing-params__detail">
        <div className="wl-pricing-params__source-filter">
          <button
            type="button"
            className={`wl-pricing-params__source-pill ${sourceFilter === 'all' ? 'is-active' : ''}`}
            onClick={() => setSourceFilter('all')}
          >
            ALL · {sourceCounts.all}
          </button>
          {SOURCE_TYPE_ORDER.map((key) => (
            <button
              key={key}
              type="button"
              className={`wl-pricing-params__source-pill ${sourceFilter === key ? 'is-active' : ''}`}
              onClick={() => setSourceFilter(key)}
            >
              {SOURCE_TYPE_LABELS[key]} · {sourceCounts[key]}
            </button>
          ))}
        </div>
        {selected ? (
          <>
            {importSummary && (
              <p className="wl-pricing-params__import-summary" role="status">
                {importSummary}
              </p>
            )}
            <div className="wl-pricing-params__tiles">
              <Tile label="Rows" value={String(filteredRows.length)} />
              <Tile label="Duplicates" value={String((selected.summary?.duplicate_trade_ids ?? []).length)} />
              <Tile label="Status" value={selected.status} />
              <Tile label="Source type" value={selectedSourceLabel} />
            </div>
            <div className="wl-pricing-params__toolbar">
              <div className="wl-pricing-params__filter">
                <button
                  type="button"
                  className={`wl-pricing-params__filter-btn ${filterMode === 'all' ? 'is-active' : ''}`}
                  onClick={() => onFilterModeChange('all')}
                >
                  ALL
                </button>
                <button
                  type="button"
                  className={`wl-pricing-params__filter-btn ${filterMode === 'live' ? 'is-active' : ''}`}
                  onClick={() => onFilterModeChange('live')}
                >
                  LIVE
                </button>
              </div>
              <div className="wl-pricing-params__pager">
                <span className="wl-pricing-params__pageinfo">
                  {visibleStart}-{visibleEnd} of {filteredRows.length}
                </span>
                <Select
                  variant="inline"
                  label="Rows per page"
                  value={String(pageSize)}
                  onChange={(v) => setPageSize(Number(v))}
                  options={[10, 25, 50, 100].map((size) => ({ value: String(size), label: `${size} / page` }))}
                />
                <Button
                  type="button"
                  variant="ghost"
                  iconOnly
                  aria-label="Previous page"
                  disabled={safePage === 0}
                  onClick={() => setPage((current) => Math.max(0, current - 1))}
                >
                  <ChevronLeft size={16} aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  iconOnly
                  aria-label="Next page"
                  disabled={safePage >= totalPages - 1}
                  onClick={() => setPage((current) => Math.min(totalPages - 1, current + 1))}
                >
                  <ChevronRight size={16} aria-hidden="true" />
                </Button>
              </div>
            </div>
            <Table columns={columns} rows={pagedRows} rowKey={(row) => row.id} />
            {filteredRows.length === 0 && (
              <Empty message={filterMode === 'live' ? 'No rows with complete pricing parameters.' : 'No rows in this profile.'} symbol="◌" />
            )}
          </>
        ) : (
          <Empty message={loading ? 'Loading pricing parameter profiles...' : 'No pricing parameter profiles yet.'} />
        )}
    </section>
  );

  return { rail, detail };
}

function sourceTypeLabel(sourceType: string): string {
  return SOURCE_TYPE_LABELS[sourceType as SourceTypeKey] ?? sourceType.toUpperCase();
}

function fmt(value: number | null | undefined): string {
  return value == null ? '-' : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

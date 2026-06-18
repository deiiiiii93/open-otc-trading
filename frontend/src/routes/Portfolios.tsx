import { useEffect, useMemo, useState } from 'react';
import { Calculator, MoreHorizontal, Plus } from 'lucide-react';
import { Button } from '../components/Button';
import { Chip } from '../components/Chip';
import { Empty } from '../components/Empty';
import { KindChip } from '../components/KindChip';
import { Panel } from '../components/Panel';
import { PositionPicker } from '../components/PositionPicker';
import { RuleEditor } from '../components/RuleEditor';
import { FIELDS, leavesToRule, ruleToLeaves } from '../components/RuleBuilder';
import { Select } from '../components/Select';
import { SourcePicker } from '../components/SourcePicker';
import { Table, type Column } from '../components/Table';
import { TagEditor } from '../components/TagEditor';
import { DataTablePage } from '../components/templates';
import { type Metric } from '../components/MetricRow';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type {
  FilterRule,
  PageContext,
  PageContextReporter,
  PortfolioDetail,
  PortfolioKind,
  PortfolioSummary,
} from '../types';
import './Portfolios.css';

type SaveState =
  | { kind: 'idle' }
  | { kind: 'editing' }
  | { kind: 'saving' }
  | { kind: 'saved'; at: number }
  | { kind: 'error'; message: string };

type PreviewPosition = {
  id: number;
  source_trade_id?: string | null;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  status: string;
};

type PickerPosition = {
  id: number;
  source_trade_id?: string | null;
  underlying: string;
  product_type: string;
};

export type PortfoliosProps = {
  portfolios: PortfolioSummary[];
  allPortfolios: PortfolioSummary[];
  allPositions: PickerPosition[];
  selected: PortfolioDetail | null;
  selectedPortfolioId: number | null;
  pendingMembershipPreview: PreviewPosition[] | null;
  saveState: SaveState;
  onSelectPortfolio: (id: number) => void;
  onOpenCreate: (kind: PortfolioKind) => void;
  onOpenDelete: () => void;
  onSaveRule: (rule: FilterRule | null) => Promise<void>;
  onAddInclude: (positionIds: number[]) => Promise<void>;
  onRemoveInclude: (positionId: number) => Promise<void>;
  onAddExclude: (positionIds: number[]) => Promise<void>;
  onRemoveExclude: (positionId: number) => Promise<void>;
  onAddSource: (portfolioIds: number[]) => Promise<void>;
  onRemoveSource: (portfolioId: number) => Promise<void>;
  onSetTags: (tags: string[]) => Promise<void>;
  onRunPricing: () => void;
  onRunRisk: () => void;
  activeDialog?: { kind: 'create'; portfolioKind: PortfolioKind } | { kind: 'delete' } | null;
  onPageContextChange?: PageContextReporter;
};

export function Portfolios(props: PortfoliosProps) {
  const portfolio = props.selected;
  const isView = portfolio?.kind === 'view';

  const resolvedRows: PreviewPosition[] = isView
    ? props.pendingMembershipPreview ?? ((portfolio?.positions ?? []) as PreviewPosition[])
    : (portfolio?.positions ?? []) as PreviewPosition[];

  const tileValues = useMemo(() => computeTiles(resolvedRows), [resolvedRows]);

  const [includePickerOpen, setIncludePickerOpen] = useState(false);
  const [excludePickerOpen, setExcludePickerOpen] = useState(false);
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);

  const filteredRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return resolvedRows;
    return resolvedRows.filter((row) => {
      const haystack = [
        String(row.id),
        row.source_trade_id,
        row.underlying,
        row.product_type,
        row.status,
      ].filter((v) => v != null).join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [resolvedRows, searchQuery]);

  useEffect(() => {
    setPage(0);
  }, [searchQuery, pageSize]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pagedRows = useMemo(
    () => filteredRows.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [filteredRows, pageSize, safePage],
  );
  const pageContext = useMemo(() => {
    const base: PageContext = {
      route: 'portfolios',
      title: `Portfolios - ${portfolio?.name ?? 'none'}`,
      path: '/',
      entity_ids: { portfolio_id: portfolio?.id ?? props.selectedPortfolioId ?? null },
      snapshot: {
        selected_portfolio: portfolio
          ? {
              id: portfolio.id,
              name: portfolio.name,
              kind: portfolio.kind,
              base_currency: portfolio.base_currency,
              description: portfolio.description,
              tags: portfolio.tags,
              resolved_position_count: portfolio.resolved_position_count,
              manual_include_ids: portfolio.manual_include_ids,
              manual_exclude_ids: portfolio.manual_exclude_ids,
              source_portfolio_ids: portfolio.source_portfolio_ids,
              filter_rule: portfolio.filter_rule,
            }
          : null,
        save_state: props.saveState,
        resolved_rows: filteredRows.slice(0, 12),
      },
      chips: portfolioChips(portfolio),
    };

    if (props.activeDialog?.kind === 'create') {
      return {
        ...base,
        title: 'Create Portfolio dialog',
        snapshot: {
          parent_context: base,
          create_kind: props.activeDialog.portfolioKind,
        },
        chips: ['dialog', 'Create Portfolio', props.activeDialog.portfolioKind],
      };
    }
    if (props.activeDialog?.kind === 'delete') {
      return {
        ...base,
        title: 'Delete Portfolio dialog',
        snapshot: {
          parent_context: base,
          deleting_portfolio: portfolio
            ? { id: portfolio.id, name: portfolio.name, kind: portfolio.kind }
            : null,
        },
        chips: ['dialog', 'Delete Portfolio', portfolio?.name ?? 'none'],
      };
    }
    if (includePickerOpen || excludePickerOpen || sourcePickerOpen) {
      const kind = sourcePickerOpen
        ? 'Source Portfolio picker'
        : includePickerOpen
          ? 'Manual Include picker'
          : 'Manual Exclude picker';
      return {
        ...base,
        title: kind,
        snapshot: {
          parent_context: base,
          picker: kind,
          available_positions: props.allPositions.slice(0, 20),
          available_portfolios: props.allPortfolios.slice(0, 20),
        },
        chips: ['dialog', kind, portfolio?.name ?? 'portfolio'],
      };
    }
    return base;
  }, [
    excludePickerOpen,
    filteredRows,
    includePickerOpen,
    portfolio,
    props.activeDialog,
    props.allPortfolios,
    props.allPositions,
    props.saveState,
    props.selectedPortfolioId,
    sourcePickerOpen,
  ]);
  usePageContextReporter(pageContext, props.onPageContextChange);

  const tileMetrics: Metric[] = [
    { label: 'POSITIONS', value: tileValues.positions },
    { label: 'UNDERLYINGS', value: tileValues.underlyings },
    { label: 'NET QTY', value: tileValues.netQty, variant: tileValues.netQtyVariant },
    { label: 'STATUS', value: tileValues.status },
  ];

  return (
    <DataTablePage<PreviewPosition>
      title={`PORTFOLIOS · ${portfolio?.name ?? '—'}`}
      chips={portfolioChips(portfolio)}
      actions={
        <div className="wl-portfolios__actions">
          <Select
            variant="inline"
            label="Select portfolio"
            value={props.selectedPortfolioId != null ? String(props.selectedPortfolioId) : ''}
            onChange={(v) => props.onSelectPortfolio(Number(v))}
            placeholder="Choose…"
            options={props.portfolios.map((option) => ({
              value: String(option.id),
              label: `${option.name} · ${option.kind}`,
            }))}
          />
          <NewPortfolioMenu onSelect={props.onOpenCreate} />
          <Button variant="primary" onClick={props.onRunPricing} disabled={!portfolio}>
            <Calculator size={16} aria-hidden="true" />
            Run Pricing
          </Button>
          <OverflowMenu onDelete={props.onOpenDelete} onRunRisk={props.onRunRisk} disabled={!portfolio} />
        </div>
      }
      metrics={tileMetrics}
      toolbar={portfolio ? {
        search: { value: searchQuery, onChange: setSearchQuery, placeholder: 'Search resolved positions...' },
        pager: {
          page: safePage,
          pageSize,
          total: filteredRows.length,
          onPage: setPage,
          onPageSize: setPageSize,
        },
      } : undefined}
      body={
        !portfolio ? (
          <Empty message="Select a portfolio to begin." symbol="◌" />
        ) : (
          <div className="wl-portfolios__twopane">
            <Panel title="DEFINITION" meta={saveStateLabel(props.saveState)}>
              {isView ? (
                <>
                  <RuleFieldset rule={portfolio.filter_rule} onChange={props.onSaveRule} />
                  <SourcesFieldset
                    portfolio={portfolio}
                    allPortfolios={props.allPortfolios}
                    onOpen={() => setSourcePickerOpen(true)}
                    onRemove={props.onRemoveSource}
                  />
                  <ManualFieldset
                    legend="MANUAL INCLUDES"
                    ids={portfolio.manual_include_ids}
                    positions={props.allPositions}
                    onOpen={() => setIncludePickerOpen(true)}
                    onRemove={props.onRemoveInclude}
                  />
                  <ManualFieldset
                    legend="MANUAL EXCLUDES"
                    ids={portfolio.manual_exclude_ids}
                    positions={props.allPositions}
                    onOpen={() => setExcludePickerOpen(true)}
                    onRemove={props.onRemoveExclude}
                  />
                  <TagsFieldset tags={portfolio.tags} onChange={props.onSetTags} />
                </>
              ) : (
                <>
                  <p className="wl-portfolios__container-hint">
                    Container holds owned positions imported via XLSX. Use the Positions page to add positions.
                  </p>
                  <TagsFieldset tags={portfolio.tags} onChange={props.onSetTags} />
                </>
              )}
            </Panel>

            <Panel
              title={isView ? 'RESOLVED POSITIONS' : 'OWNED POSITIONS'}
              meta={`${filteredRows.length} rows · ${tileValues.underlyings} underlyings`}
              className="wl-portfolios__resolved-panel"
            >
              <ResolvedTable rows={pagedRows} />
            </Panel>
          </div>
        )
      }
      overlays={
        portfolio && isView ? (
          <>
            <PositionPicker
              open={includePickerOpen}
              positions={props.allPositions}
              excludeIds={[...portfolio.manual_include_ids, ...portfolio.manual_exclude_ids]}
              onCancel={() => setIncludePickerOpen(false)}
              onConfirm={async (ids) => { setIncludePickerOpen(false); await props.onAddInclude(ids); }}
              title="ADD MANUAL INCLUDES"
            />
            <PositionPicker
              open={excludePickerOpen}
              positions={props.allPositions}
              excludeIds={[...portfolio.manual_exclude_ids, ...portfolio.manual_include_ids]}
              onCancel={() => setExcludePickerOpen(false)}
              onConfirm={async (ids) => { setExcludePickerOpen(false); await props.onAddExclude(ids); }}
              title="ADD MANUAL EXCLUDES"
            />
            <SourcePicker
              open={sourcePickerOpen}
              portfolios={props.allPortfolios}
              currentPortfolioId={portfolio.id}
              excludeIds={portfolio.source_portfolio_ids}
              onCancel={() => setSourcePickerOpen(false)}
              onConfirm={async (ids) => { setSourcePickerOpen(false); await props.onAddSource(ids); }}
            />
          </>
        ) : undefined
      }
    />
  );
}

function portfolioChips(portfolio: PortfolioDetail | null): string[] {
  if (!portfolio) return ['—'];
  const updated = portfolio.updated_at ? `updated ${portfolio.updated_at.slice(0, 10)}` : '';
  const chips = [portfolio.kind.toUpperCase(), `${portfolio.resolved_position_count} positions`];
  if (updated) chips.push(updated);
  if (portfolio.tags.length) chips.push(portfolio.tags.slice(0, 3).join(' · '));
  return chips;
}

type TileValues = {
  positions: string;
  underlyings: string;
  netQty: string;
  netQtyVariant: 'default' | 'pos' | 'neg';
  status: string;
};

function computeTiles(rows: PreviewPosition[]): TileValues {
  if (rows.length === 0) {
    return {
      positions: '—', underlyings: '—', netQty: '—',
      netQtyVariant: 'default', status: '—',
    };
  }
  const underlyings = new Set(rows.map((row) => row.underlying)).size;
  const netQty = rows.reduce((acc, row) => acc + row.quantity, 0);
  const allOpen = rows.every((row) => row.status === 'open');
  return {
    positions: String(rows.length),
    underlyings: String(underlyings),
    netQty: signed(netQty),
    netQtyVariant: netQty > 0 ? 'pos' : netQty < 0 ? 'neg' : 'default',
    status: allOpen ? 'ALL OPEN' : 'MIXED',
  };
}

function signed(value: number): string {
  if (value === 0) return '0';
  return value > 0 ? `+${value}` : String(value);
}

function saveStateLabel(state: SaveState): string {
  if (state.kind === 'editing') return 'editing…';
  if (state.kind === 'saving') return 'saving…';
  if (state.kind === 'saved') return `auto-saved ${secondsAgo(state.at)}s ago`;
  if (state.kind === 'error') return `save failed — ${state.message}`;
  return '';
}

function secondsAgo(timestamp: number): number {
  return Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
}

function NewPortfolioMenu({ onSelect }: { onSelect: (kind: PortfolioKind) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="wl-portfolios__newmenu">
      <Button onClick={() => setOpen((current) => !current)}>
        <Plus size={16} aria-hidden="true" />
        New
      </Button>
      {open && (
        <ul className="wl-portfolios__newmenu-list" role="menu">
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onSelect('view'); }}>
              New view portfolio
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onSelect('container'); }}>
              New container portfolio
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

function OverflowMenu({
  onDelete, onRunRisk, disabled,
}: { onDelete: () => void; onRunRisk: () => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  if (disabled) {
    return (
      <Button variant="ghost" iconOnly disabled aria-label="More actions">
        <MoreHorizontal size={16} aria-hidden="true" />
      </Button>
    );
  }
  return (
    <div className="wl-portfolios__newmenu">
      <Button variant="ghost" iconOnly aria-label="More actions" onClick={() => setOpen((cur) => !cur)}>
        <MoreHorizontal size={16} aria-hidden="true" />
      </Button>
      {open && (
        <ul className="wl-portfolios__newmenu-list wl-portfolios__newmenu-list--right" role="menu">
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onRunRisk(); }}>
              Run Risk
            </button>
          </li>
          <li>
            <button type="button" role="menuitem" onClick={() => { setOpen(false); onDelete(); }}>
              Delete portfolio
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

function RuleFieldset({
  rule, onChange,
}: { rule: FilterRule | null; onChange: (rule: FilterRule | null) => Promise<void> }) {
  const leaves = ruleToLeaves(rule);
  const meta = leaves.length === 0
    ? 'No conditions'
    : `${leaves.length} condition${leaves.length === 1 ? '' : 's'}`;
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>RULE</legend>
      <div className="wl-portfolios__fieldset-head">
        <span className="wl-portfolios__fieldset-meta">{meta}</span>
        <Button className="wl-portfolios__add-button" onClick={() => { void onChange(leavesToRule([...leaves, { op: 'eq', field: FIELDS[0], value: '' }])); }}>+ condition</Button>
      </div>
      <RuleEditor rule={rule} onChange={(next) => { void onChange(next); }} />
    </fieldset>
  );
}

function SourcesFieldset({
  portfolio, allPortfolios, onOpen, onRemove,
}: {
  portfolio: PortfolioDetail;
  allPortfolios: PortfolioSummary[];
  onOpen: () => void;
  onRemove: (id: number) => Promise<void>;
}) {
  const sources = portfolio.source_portfolio_ids
    .map((id) => allPortfolios.find((item) => item.id === id))
    .filter(Boolean) as PortfolioSummary[];
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>SOURCES</legend>
      <div className="wl-portfolios__fieldset-head">
        <span className="wl-portfolios__fieldset-meta">
          {sources.length === 0
            ? 'No source portfolios — this view is defined by its own rule only.'
            : `${sources.length} selected · union`}
        </span>
        <Button className="wl-portfolios__add-button" onClick={onOpen}>+ source</Button>
      </div>
      {sources.length > 0 && (
        <div className="wl-portfolios__chiprow">
          {sources.map((source) => (
            <Chip key={source.id} onRemove={() => { void onRemove(source.id); }}>
              <KindChip kind={source.kind} /> {source.name}
            </Chip>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function ManualFieldset({
  legend, ids, positions, onOpen, onRemove,
}: {
  legend: string;
  ids: number[];
  positions: PickerPosition[];
  onOpen: () => void;
  onRemove: (id: number) => Promise<void>;
}) {
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>{legend}</legend>
      <div className="wl-portfolios__fieldset-head">
        <span className="wl-portfolios__fieldset-meta">{ids.length} selected</span>
        <Button className="wl-portfolios__add-button" onClick={onOpen}>+ pick position</Button>
      </div>
      {ids.length === 0 ? (
        <span className="wl-portfolios__empty-inline">None.</span>
      ) : (
        <div className="wl-portfolios__chiprow">
          {ids.map((id) => {
            const position = positions.find((item) => item.id === id);
            return (
              <Chip key={id} onRemove={() => { void onRemove(id); }}>
                #{id} {position?.underlying ?? ''}
              </Chip>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}

function TagsFieldset({
  tags, onChange,
}: { tags: string[]; onChange: (tags: string[]) => Promise<void> }) {
  return (
    <fieldset className="wl-portfolios__fieldset">
      <legend>TAGS</legend>
      <TagEditor tags={tags} onChange={(next) => { void onChange(next); }} />
    </fieldset>
  );
}

function ResolvedTable({ rows }: { rows: PreviewPosition[] }) {
  const columns: Column<PreviewPosition>[] = [
    { key: 'source_trade_id', header: 'TRADE', width: '1.6fr', render: (row) => row.source_trade_id ?? String(row.id) },
    { key: 'underlying', header: 'UNDER', width: '1fr' },
    { key: 'product_type', header: 'PRODUCT', width: '1.4fr' },
    { key: 'quantity', header: 'QTY', width: '0.6fr', numeric: true, render: (row) => signed(row.quantity) },
    { key: 'status', header: 'STATUS', width: '0.7fr' },
  ];
  if (rows.length === 0) {
    return <Empty message="No positions match this view." symbol="◌" />;
  }
  return <Table columns={columns} rows={rows} rowKey={(row) => row.id} />;
}

export type { SaveState };

import { type ReactNode, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { Calculator, Download, Upload } from 'lucide-react';
import { DataTablePage } from '../components/templates';
import { type Metric } from '../components/MetricRow';
import { Tile } from '../components/Tile';
import { type Column } from '../components/Table';
import { Button } from '../components/Button';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { PositionEditForm } from '../components/PositionEditForm';
import { PositionLifecycleTimeline } from '../components/PositionLifecycleTimeline';
import { Select } from '../components/Select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/Tabs';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import { declareActions } from '../lib/pageActions';
import type { EngineConfigVariant, PageContext, PageContextReporter, PortfolioKind, PricingParameterRow, PositionLifecycleEvent, ProductRoot, ResolvedParam, ResolvedPricingParams } from '../types';
import './Positions.css';

export type PositionsRiskSummary = {
  market_value?: number | null;
  gross_notional?: number | null;
  pnl?: number | null;
  delta?: number | null;
  gamma?: number | null;
  delta_cash?: number | null;
  gamma_cash?: number | null;
  vega?: number | null;
  theta?: number | null;
  rho?: number | null;
  rho_q?: number | null;
};

export type PositionRow = {
  id: number;
  trade_id: string;
  product_id?: number | null;
  product?: ProductRoot | null;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  currency: string;
  status: string;
  position_kind: 'otc' | 'listed';
  mapping_status: string;
  source_row?: number | null;
  mapping_error?: string | null;
  product_kwargs?: Record<string, unknown>;
  source_payload?: Record<string, unknown> | null;
  engine_name?: string | null;
  engine_kwargs?: Record<string, unknown>;
  market_inputs?: Record<string, unknown>;
  result_payload?: Record<string, unknown>;
  pricing_error?: string | null;
  price: number | null;
  market_value: number | null;
  pnl: number | null;
  delta: number | null;
  gamma: number | null;
  vega: number | null;
  theta: number | null;
  rho: number | null;
  rho_q: number | null;
};

export type PositionPricingRequest = {
  pricing_parameter_profile_id?: number;
  engine_config_id?: number | null;
  valuation_date?: string;
  spot?: number;
  rate?: number;
  dividend_yield?: number;
  volatility?: number;
  engine_name: string;
  engine_kwargs: Record<string, unknown>;
  compute_greeks?: boolean;
};

export type PositionPortfolioOption = {
  id: number;
  name: string;
  kind: PortfolioKind;
};

export type PricingProfileOption = {
  id: number;
  name: string;
  valuation_date: string;
  rows?: PricingParameterRow[];
};

type Props = {
  rows: PositionRow[];
  portfolios: PositionPortfolioOption[];
  containerPortfolios: PositionPortfolioOption[];
  pricingProfiles?: PricingProfileOption[];
  engineConfigs?: EngineConfigVariant[];
  selectedPortfolioId: number | null;
  importPortfolioId: number | null;
  selectedPricingProfileId?: number | null;
  selectedEngineConfigId?: number | null;
  portfolioName: string;
  portfolioKind: PortfolioKind | null;
  nav: string;
  pnl: string;
  pnlVariant: 'pos' | 'neg' | 'default';
  delta: string;
  deltaVariant: 'pos' | 'neg' | 'default';
  vega: string;
  riskSummary?: PositionsRiskSummary | null;
  valuationDate: string;
  onSelectPortfolio: (portfolioId: number) => void;
  onSelectImportPortfolio: (portfolioId: number) => void;
  onSelectPricingProfile?: (profileId: number | null) => void;
  onSelectEngineConfig?: (configId: number | null) => void;
  onRunPricing: () => void;
  onPricePosition: (row: PositionRow, request: PositionPricingRequest) => void | Promise<void>;
  onImportPositions: (file: File) => void;
  importingPositions: boolean;
  pricingPositionId: number | null;
  importFeedback: string | null;
  onPageContextChange?: PageContextReporter;
  onEditPosition?: (row: PositionRow, updates: Partial<PositionRow>) => void | Promise<void>;
  editingPositionId: number | null;
  lifecycleEvents?: PositionLifecycleEvent[];
  onAddLifecycleEvent?: (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => void | Promise<void>;
  onCancelLifecycleEvent?: (row: PositionRow, event: PositionLifecycleEvent, reason: string | null) => void | Promise<void>;
  addingLifecycleEvent: boolean;
  resolvedParams?: ResolvedPricingParams | null;
  resolvedParamsLoading?: boolean;
  onDetailOpen?: (row: PositionRow) => void;
};

export function Positions({
  rows,
  portfolios,
  containerPortfolios,
  pricingProfiles = [],
  engineConfigs = [],
  selectedPortfolioId,
  importPortfolioId,
  selectedPricingProfileId = null,
  selectedEngineConfigId = null,
  portfolioName,
  portfolioKind,
  nav,
  pnl,
  pnlVariant,
  delta,
  deltaVariant,
  vega,
  riskSummary,
  valuationDate,
  onSelectPortfolio,
  onSelectImportPortfolio,
  onSelectPricingProfile = () => {},
  onSelectEngineConfig = () => {},
  onRunPricing,
  onPricePosition,
  onImportPositions,
  importingPositions,
  pricingPositionId,
  importFeedback,
  onPageContextChange,
  onEditPosition,
  editingPositionId,
  lifecycleEvents,
  onAddLifecycleEvent,
  onCancelLifecycleEvent,
  addingLifecycleEvent,
  resolvedParams,
  resolvedParamsLoading,
  onDetailOpen,
}: Props) {
  const importInputId = useId();
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [showLiveOnly, setShowLiveOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [editingCell, setEditingCell] = useState<{ rowId: number; key: keyof PositionRow } | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const showMobileRows = useMediaQuery('(max-width: 640px)');

  const handleCellClick = (row: PositionRow, key: 'quantity' | 'entry_price' | 'status') => {
    if (!onEditPosition || portfolioKind !== 'container') return;
    setEditingCell({ rowId: row.id, key });
    setEditValue(String(row[key] ?? ''));
  };

  const handleCellBlur = async () => {
    if (!editingCell || !onEditPosition) return;
    const { rowId, key } = editingCell;
    const row = rows.find((r) => r.id === rowId);
    if (!row) return;

    const currentValue = row[key];
    let parsedValue: number | string = editValue;
    if (key === 'quantity' || key === 'entry_price') {
      parsedValue = Number(editValue);
      if (!Number.isFinite(parsedValue)) {
        setEditingCell(null);
        return;
      }
    }

    if (parsedValue !== currentValue) {
      await onEditPosition(row, { [key]: parsedValue });
    }
    setEditingCell(null);
  };

  const handleCellKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      void handleCellBlur();
    } else if (event.key === 'Escape') {
      setEditingCell(null);
    }
  };

  useEffect(() => {
    if (rows.length === 0) {
      setSelected(null);
      setDetailOpen(false);
      return;
    }
    if (selected != null && !rows.some((row) => row.id === selected)) {
      setSelected(null);
      setDetailOpen(false);
    }
  }, [rows, selected]);

  const filteredRows = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter((row) => {
      const haystack = [
        row.id,
        row.trade_id,
        row.underlying,
        row.product_type,
        row.status,
        row.mapping_status,
        row.source_row,
        row.engine_name,
      ].filter((value) => value != null).join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [rows, searchQuery]);

  const displayRows = useMemo(() => {
    if (!showLiveOnly) return filteredRows;
    return filteredRows.filter((row) => row.status === 'open');
  }, [filteredRows, showLiveOnly]);

  useEffect(() => {
    setPage(0);
  }, [searchQuery, pageSize, showLiveOnly]);

  const totalPages = Math.max(1, Math.ceil(displayRows.length / pageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pagedRows = useMemo(
    () => displayRows.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [displayRows, pageSize, safePage],
  );
  const selectedRow = useMemo(() => rows.find((r) => r.id === selected) ?? null, [rows, selected]);
  const selectedPricingProfile = useMemo(
    () => pricingProfiles.find((profile) => profile.id === selectedPricingProfileId) ?? null,
    [pricingProfiles, selectedPricingProfileId],
  );
  const positionSummary = useMemo(() => summarizePositions(rows), [rows]);
  const pageContext = useMemo(() => {
    const base: PageContext = {
      route: 'positions',
      title: `Positions - ${portfolioName}`,
      path: '/',
      entity_ids: {
        portfolio_id: selectedPortfolioId,
        pricing_profile_id: selectedPricingProfileId,
      },
      snapshot: {
        portfolio: {
          id: selectedPortfolioId,
          name: portfolioName,
          kind: portfolioKind,
          position_count: rows.length,
        },
        summary: { nav, pnl, delta, vega, risk_summary: riskSummary, position_summary: positionSummary, valuation_date: valuationDate },
        selected_pricing_profile: selectedPricingProfile
          ? {
              id: selectedPricingProfile.id,
              name: selectedPricingProfile.name,
              valuation_date: selectedPricingProfile.valuation_date,
            }
          : null,
        latest_price_run: {
          valuation_date: valuationDate,
          results: rows.slice(0, 12).map((row) => ({
            position_id: row.id,
            source_trade_id: row.trade_id,
            ok: !row.pricing_error,
            price: row.price,
            market_value: row.market_value,
            pnl: row.pnl,
            error: row.pricing_error,
          })),
        },
      },
      // Page loads the full position set client-side; the agent can answer
      // count/aggregate questions from snapshot.portfolio.position_count
      // without escalating.
      loaded_context: {
        completeness: 'complete',
        visible_count: rows.length,
        total_count: rows.length,
      },
      actions: declareActions([
        {
          name: 'count_positions',
          required_ids: ['portfolio_id'],
          confirmation: 'implicit',
          backend_endpoint: 'GET /api/positions',
        },
        {
          name: 'run_batch_pricing',
          required_ids: ['portfolio_id'],
          confirmation: 'explicit',
          backend_endpoint: 'POST /api/batch-pricing/runs',
        },
      ]),
      chips: [portfolioKind ?? 'portfolio', `val ${valuationDate}`, `${rows.length} trades`],
    };

    if (importDialogOpen) {
      return {
        ...base,
        title: 'Import Positions dialog',
        entity_ids: {
          ...base.entity_ids,
          import_portfolio_id: importPortfolioId,
        },
        snapshot: {
          parent_context: base,
          import_target: containerPortfolios.find((portfolio) => portfolio.id === importPortfolioId) ?? null,
          available_container_portfolios: containerPortfolios.map((portfolio) => ({
            id: portfolio.id,
            name: portfolio.name,
            kind: portfolio.kind,
          })),
        },
        chips: ['dialog', 'Import Positions', ...(base.chips ?? [])],
      };
    }

    if (detailOpen && selectedRow) {
      return {
        ...base,
        title: 'Position Detail dialog',
        entity_ids: {
          ...base.entity_ids,
          position_id: selectedRow.id,
          source_trade_id: selectedRow.trade_id,
        },
        snapshot: {
          parent_context: base,
          position: {
            id: selectedRow.id,
            trade_id: selectedRow.trade_id,
            portfolio_id: selectedPortfolioId,
            underlying: selectedRow.underlying,
            product_type: selectedRow.product_type,
            engine_name: selectedRow.engine_name,
            status: selectedRow.status,
            quantity: selectedRow.quantity,
            price: selectedRow.price,
            market_value: selectedRow.market_value,
            pnl: selectedRow.pnl,
            delta: selectedRow.delta,
            vega: selectedRow.vega,
            pricing_error: selectedRow.pricing_error,
          },
          pricing_profile: selectedPricingProfile
            ? {
                id: selectedPricingProfile.id,
                name: selectedPricingProfile.name,
                valuation_date: selectedPricingProfile.valuation_date,
              }
            : null,
        },
        chips: ['dialog', selectedRow.trade_id, selectedRow.product_type],
      };
    }

    return base;
  }, [
    containerPortfolios,
    delta,
    detailOpen,
    importDialogOpen,
    importPortfolioId,
    nav,
    pnl,
    positionSummary,
    portfolioKind,
    portfolioName,
    rows,
    selectedPortfolioId,
    selectedPricingProfile,
    selectedPricingProfileId,
    selectedRow,
    valuationDate,
    vega,
    riskSummary,
  ]);
  usePageContextReporter(pageContext, onPageContextChange);

  const columns: Column<PositionRow>[] = [
    { key: 'trade_id', header: 'TRADE', width: '1.6fr' },
    { key: 'underlying', header: 'UNDER', width: '1fr' },
    { key: 'product_type', header: 'TYPE', width: '1.3fr' },
    {
      key: 'position_kind',
      header: 'KIND',
      width: '0.7fr',
      render: (r) => <PositionKindBadge value={r.position_kind} />,
    },
    {
      key: 'quantity',
      header: 'QTY',
      width: '0.7fr',
      numeric: true,
      render: (r) => {
        if (editingCell?.rowId === r.id && editingCell.key === 'quantity') {
          return (
            <input
              type="number"
              step="any"
              value={editValue}
              autoFocus
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleCellBlur}
              onKeyDown={handleCellKeyDown}
              className="wl-positions__inline-input"
            />
          );
        }
        return (
          <span
            className={portfolioKind === 'container' ? 'wl-positions__editable-cell' : ''}
            onClick={() => handleCellClick(r, 'quantity')}
          >
            {formatSigned(r.quantity, 0)}
          </span>
        );
      },
    },
    {
      key: 'entry_price',
      header: 'ENTRY',
      width: '0.7fr',
      numeric: true,
      render: (r) => {
        if (editingCell?.rowId === r.id && editingCell.key === 'entry_price') {
          return (
            <input
              type="number"
              step="any"
              value={editValue}
              autoFocus
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleCellBlur}
              onKeyDown={handleCellKeyDown}
              className="wl-positions__inline-input"
            />
          );
        }
        return (
          <span
            className={portfolioKind === 'container' ? 'wl-positions__editable-cell' : ''}
            onClick={() => handleCellClick(r, 'entry_price')}
          >
            {formatNullableNumber(r.entry_price, 2)}
          </span>
        );
      },
    },
    { key: 'currency', header: 'CCY', width: '0.5fr' },
    { key: 'price',  header: 'PRICE', width: '0.8fr', numeric: true, render: (r) => formatNullableNumber(r.price, 3) },
    { key: 'pnl',  header: 'P&L', width: '0.8fr', numeric: true, render: (r) => formatNullableSigned(r.pnl, 2) },
    {
      key: 'status',
      header: 'STATUS',
      width: '0.7fr',
      render: (r) => {
        if (editingCell?.rowId === r.id && editingCell.key === 'status') {
          return (
            <select
              value={editValue}
              autoFocus
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleCellBlur}
              onKeyDown={handleCellKeyDown}
              className="wl-positions__inline-select"
            >
              <option value="open">open</option>
              <option value="knocked_in">knocked_in</option>
              <option value="closed">closed</option>
            </select>
          );
        }
        return (
          <span
            className={portfolioKind === 'container' ? 'wl-positions__editable-cell' : ''}
            onClick={() => handleCellClick(r, 'status')}
          >
            {r.status}
          </span>
        );
      },
    },
  ];

  const handleImportChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (file) {
      onImportPositions(file);
    }
    event.currentTarget.value = '';
  };

  const openDetail = (row: PositionRow) => {
    setSelected(row.id);
    setDetailOpen(true);
    // Resolved pricing params are fetched per (position, profile) on every
    // open. The profile selector lives in the toolbar (behind the modal), so
    // changing it dismisses the dialog — there is no "profile changed while
    // open" state to go stale; the next open refetches with the current
    // profile. Pinned by the Positions.live "closes then refetches" test.
    onDetailOpen?.(row);
  };

  const openImportDialog = () => {
    if (containerPortfolios.length === 0 || importingPositions) return;
    setImportDialogOpen(true);
  };

  const chooseImportFile = () => {
    setImportDialogOpen(false);
    window.setTimeout(() => importInputRef.current?.click(), 0);
  };

  const riskMetrics: Metric[] = [
    { label: 'Market Value (PV)', value: formatMetricValue(riskSummary?.market_value, nav), variant: variantForNumber(riskSummary?.market_value) },
    { label: 'PnL', value: formatMetricValue(riskSummary?.pnl, pnl), variant: variantForNumber(riskSummary?.pnl, pnlVariant) },
    { label: 'Delta Cash', value: formatMetricValue(firstNumber(riskSummary?.delta_cash, riskSummary?.delta), delta), variant: variantForNumber(firstNumber(riskSummary?.delta_cash, riskSummary?.delta), deltaVariant) },
    { label: 'Gamma Cash', value: formatMetricValue(firstNumber(riskSummary?.gamma_cash, riskSummary?.gamma)), variant: variantForNumber(firstNumber(riskSummary?.gamma_cash, riskSummary?.gamma)) },
    { label: 'Vega', value: formatMetricValue(riskSummary?.vega, vega), variant: variantForNumber(riskSummary?.vega) },
    { label: 'Theta', value: formatMetricValue(riskSummary?.theta), variant: variantForNumber(riskSummary?.theta) },
    { label: 'Rho', value: formatMetricValue(riskSummary?.rho), variant: variantForNumber(riskSummary?.rho) },
    { label: 'RhoQ', value: formatMetricValue(riskSummary?.rho_q), variant: variantForNumber(riskSummary?.rho_q) },
  ];
  const positionSummaryMetrics: Metric[] = [
    { label: 'Positions', value: `${positionSummary.live}/${positionSummary.total}` },
    { label: 'Notional', value: formatMetricValue(positionSummary.notional) },
    { label: 'Top Underlying', value: positionSummary.topUnderlying },
    { label: 'Top Type', value: positionSummary.topType },
  ];

  const positionFilters = (
    <div className="wl-positions__toggle" role="group" aria-label="Position filter">
      <button
        type="button"
        className={`wl-positions__toggle-btn${!showLiveOnly ? ' wl-positions__toggle-btn--active' : ''}`}
        aria-pressed={!showLiveOnly}
        onClick={() => setShowLiveOnly(false)}
      >
        All
      </button>
      <button
        type="button"
        className={`wl-positions__toggle-btn${showLiveOnly ? ' wl-positions__toggle-btn--active' : ''}`}
        aria-pressed={showLiveOnly}
        onClick={() => setShowLiveOnly(true)}
      >
        Live
      </button>
    </div>
  );

  return (
    <DataTablePage<PositionRow>
      title={`POSITIONS · ${portfolioName}`}
      chips={[portfolioKind ?? '—', `val ${valuationDate}`, `${rows.length} trades`]}
      feedback={
        importFeedback ? (
          <div className="wl-positions__feedback" role="status" aria-live="polite">
            {importFeedback}
          </div>
        ) : undefined
      }
      actions={
        <div className="wl-positions__actions">
          <div className="wl-positions__filter-group">
              <Select
                variant="inline"
                label="Display portfolio"
                id="positions-display-portfolio"
                value={String(selectedPortfolioId ?? '')}
                onChange={(v) => onSelectPortfolio(Number(v))}
                options={portfolios.map((portfolio) => ({
                  value: String(portfolio.id),
                  label: `${portfolio.name} · ${portfolio.kind}`,
                }))}
              />
              <Select
                variant="inline"
                label="Pricing profile"
                id="positions-pricing-profile"
                value={String(selectedPricingProfileId ?? '')}
                onChange={(v) => onSelectPricingProfile(v ? Number(v) : null)}
                options={[
                  { value: '', label: 'Live market + assumptions' },
                  ...pricingProfiles.map((profile) => ({
                    value: String(profile.id),
                    label: `${profile.name} · ${formatProfileDate(profile.valuation_date)}`,
                  })),
                ]}
              />
              <Select
                variant="inline"
                label="Engine config"
                id="positions-engine-config"
                value={String(selectedEngineConfigId ?? '')}
                onChange={(v) => onSelectEngineConfig(v ? Number(v) : null)}
                options={[
                  { value: '', label: 'Position engines only' },
                  ...engineConfigs.map((config) => ({
                    value: String(config.id),
                    label: `${config.name}${config.is_default ? ' (default)' : ''}`,
                  })),
                ]}
              />
            </div>
            <div className="wl-positions__button-group">
              <a
                className="wl-button wl-button--default"
                href="/api/positions/import-template"
                download="positions_import_template.xlsx"
              >
                <Download size={16} aria-hidden="true" />
                <span>Download Template</span>
              </a>
              <Button
                type="button"
                onClick={openImportDialog}
                disabled={importingPositions || importPortfolioId == null || containerPortfolios.length === 0}
              >
                <Upload size={16} aria-hidden="true" />
                <span>{importingPositions ? 'Importing' : 'Import XLSX'}</span>
              </Button>
              <input
                ref={importInputRef}
                id={importInputId}
                className="wl-positions__file"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                aria-label="Import positions xlsx"
                disabled={importingPositions}
                onChange={handleImportChange}
              />
              <Button variant="primary" onClick={onRunPricing}>
                <Calculator size={16} aria-hidden="true" />
                Run Batch Pricing
              </Button>
            </div>
        </div>
      }
      metrics={[riskMetrics, positionSummaryMetrics]}
      toolbar={{
        search: { value: searchQuery, onChange: setSearchQuery, placeholder: 'Search positions...' },
        filters: positionFilters,
        pager: {
          page: safePage,
          pageSize,
          total: displayRows.length,
          onPage: setPage,
          onPageSize: setPageSize,
        },
      }}
      table={{
        columns,
        rows: pagedRows,
        rowKey: (r) => r.id,
        selectedKey: selected,
        onRowClick: openDetail,
        className: 'wl-positions__table',
      }}
      mobileCards={
        showMobileRows && pagedRows.length > 0 ? (
          <div className="wl-positions__mobile-list" aria-label="Position cards">
            {pagedRows.map((row) => (
              <PositionMobileCard
                key={row.id}
                row={row}
                selected={selected === row.id}
                onOpen={openDetail}
              />
            ))}
          </div>
        ) : undefined
      }
      empty={
        <Empty message={showLiveOnly ? 'No live positions.' : 'No positions match this search.'} symbol="◌" />
      }
      overlays={
        <>
          <Modal
            open={importDialogOpen}
            onOpenChange={setImportDialogOpen}
            title="Import Positions"
            layoutKey="import-positions"
            contentClassName="wl-positions__import-modal"
          >
            <div className="wl-positions__import-form">
              <Select
                label="Import target portfolio"
                id="positions-import-portfolio"
                className="wl-positions__term-field"
                value={String(importPortfolioId ?? '')}
                disabled={importingPositions || containerPortfolios.length === 0}
                onChange={(v) => onSelectImportPortfolio(Number(v))}
                options={containerPortfolios.map((portfolio) => ({
                  value: String(portfolio.id),
                  label: portfolio.name,
                }))}
              />
              <div className="wl-positions__import-actions">
                <Button type="button" variant="ghost" onClick={() => setImportDialogOpen(false)}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  onClick={chooseImportFile}
                  disabled={importPortfolioId == null || importingPositions}
                >
                  <Upload size={16} aria-hidden="true" />
                  Choose XLSX
                </Button>
              </div>
            </div>
          </Modal>
          <Modal
            open={detailOpen && selectedRow != null}
            onOpenChange={setDetailOpen}
            title="Position Detail"
            layoutKey="position-detail"
            contentClassName="wl-positions__modal"
            description={selectedRow ? `${selectedRow.trade_id} · ${selectedRow.status.toUpperCase()}` : undefined}
          >
            {selectedRow ? (
              <PositionDetail
                row={selectedRow}
                onPricePosition={onPricePosition}
                pricing={pricingPositionId === selectedRow.id}
                selectedPricingProfile={selectedPricingProfile}
                resolvedParams={resolvedParams}
                resolvedParamsLoading={resolvedParamsLoading}
                onSave={portfolioKind === 'container' ? onEditPosition : undefined}
                saving={editingPositionId === selectedRow.id}
                lifecycleEvents={lifecycleEvents?.filter((e) => e.position_id === selectedRow.id)}
                onAddLifecycleEvent={onAddLifecycleEvent}
                onCancelLifecycleEvent={onCancelLifecycleEvent}
                addingLifecycleEvent={addingLifecycleEvent}
              />
            ) : null}
          </Modal>
        </>
      }
    />
  );
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia(query);
    const handleChange = () => setMatches(media.matches);

    handleChange();
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, [query]);

  return matches;
}

function PositionMobileCard({
  row,
  selected,
  onOpen,
}: {
  row: PositionRow;
  selected: boolean;
  onOpen: (row: PositionRow) => void;
}) {
  const pnlVariant = signedTileVariant(row.pnl);

  return (
    <button
      type="button"
      className={`wl-positions__mobile-card ${selected ? 'wl-positions__mobile-card--selected' : ''}`}
      onClick={() => onOpen(row)}
      aria-label={`Open ${row.trade_id} position details`}
    >
      <span className="wl-positions__mobile-card-head">
        <span className="wl-positions__mobile-title">
          <strong>{row.trade_id}</strong>
          <small>{row.underlying} · {row.product_type}</small>
        </span>
        <span className="wl-positions__mobile-badges">
          <PositionKindBadge value={row.position_kind} />
          <Badge variant={statusBadgeVariant(row.status)}>{row.status}</Badge>
        </span>
      </span>
      <span className="wl-positions__mobile-metrics">
        <span>
          <small>Qty</small>
          <strong>{formatSigned(row.quantity, 0)}</strong>
        </span>
        <span>
          <small>Entry</small>
          <strong>{formatNullableNumber(row.entry_price, 2)}</strong>
        </span>
        <span>
          <small>Price</small>
          <strong>{formatNullableNumber(row.price, 3)}</strong>
        </span>
        <span className={`wl-positions__mobile-pnl wl-positions__mobile-pnl--${pnlVariant}`}>
          <small>P&L</small>
          <strong>{formatNullableSigned(row.pnl, 2)}</strong>
        </span>
      </span>
      <span className="wl-positions__mobile-meta">
        <span>{row.currency}</span>
        <span>{row.mapping_status}</span>
        <span>{row.engine_name || 'No engine'}</span>
      </span>
    </button>
  );
}

function PositionDetail({
  row,
  onPricePosition,
  pricing,
  selectedPricingProfile,
  resolvedParams,
  resolvedParamsLoading,
  onSave,
  saving,
  lifecycleEvents,
  onAddLifecycleEvent,
  onCancelLifecycleEvent,
  addingLifecycleEvent,
}: {
  row: PositionRow;
  onPricePosition: (row: PositionRow, request: PositionPricingRequest) => void | Promise<void>;
  pricing: boolean;
  selectedPricingProfile: PricingProfileOption | null;
  resolvedParams?: ResolvedPricingParams | null;
  resolvedParamsLoading?: boolean;
  onSave?: (row: PositionRow, updates: Partial<PositionRow>) => void | Promise<void>;
  saving: boolean;
  lifecycleEvents?: PositionLifecycleEvent[];
  onAddLifecycleEvent?: (row: PositionRow, eventType: string, eventData: Record<string, unknown>) => void | Promise<void>;
  onCancelLifecycleEvent?: (row: PositionRow, event: PositionLifecycleEvent, reason: string | null) => void | Promise<void>;
  addingLifecycleEvent: boolean;
}) {
  const pricingParameterResolution = useMemo(
    () => pricingParameterResolutionForPosition(row, selectedPricingProfile),
    [row.trade_id, row.underlying, selectedPricingProfile],
  );
  const detailKpiClass = row.pnl == null ? 'default' : row.pnl >= 0 ? 'pos' : 'neg';
  const hedgePayload = isRecord(row.source_payload?.hedge) ? row.source_payload.hedge : null;
  const hedgedUnderlying = scalarString(hedgePayload?.hedged_underlying);

  return (
    <div className="wl-positions__detail-shell">
      <header className="wl-positions__detail-head">
        <div className="wl-positions__detail-meta">
          <h2 className="wl-positions__detail-trade">{row.trade_id}</h2>
          <p className="wl-positions__detail-subtitle">
            {row.underlying} · {row.product_type}
          </p>
        </div>
        <div className="wl-positions__detail-badges">
          <Badge variant={statusBadgeVariant(row.status)}>{row.status}</Badge>
          <Badge variant={mappingStatusBadgeVariant(row.mapping_status)}>{row.mapping_status}</Badge>
          <Badge variant="ink">{row.engine_name || 'No engine'}</Badge>
        </div>
      </header>
      <div className="wl-positions__detail-kpis">
        <Tile label="Position" value={`#${row.id}`} />
        <Tile label="Quantity" value={formatSigned(row.quantity, 0)} />
        <Tile label="Entry" value={formatNullableNumber(row.entry_price, 2)} />
        <Tile label="Price" value={formatNullableNumber(row.price, 3)} />
        <Tile label="Market Value" value={formatNullableNumber(row.market_value, 2)} />
        <Tile label="P&L" value={formatNullableSigned(row.pnl, 2)} variant={detailKpiClass} />
        <Tile label="Last Spot" value={formatFormValue(row.market_inputs?.spot)} />
        <PositionGreekMetricCards row={row} />
      </div>
      {(row.mapping_error || row.pricing_error) && (
        <div className="wl-positions__error">
          {row.mapping_error || row.pricing_error}
        </div>
      )}
      <Tabs defaultValue="details">
        <TabsList>
          <TabsTrigger value="details">Details</TabsTrigger>
          <TabsTrigger value="lifecycle">Lifecycle</TabsTrigger>
          <TabsTrigger value="pricing">Pricing</TabsTrigger>
        </TabsList>

        <TabsContent value="details">
          <div className="wl-positions__detail">
            <PositionDetailSection title="Contract Snapshot">
              <dl className="wl-positions__detail-grid">
                <DetailItem label="Trade" value={row.trade_id} />
                <DetailItem label="Position" value={`#${row.id}`} />
                <DetailItem label="Underlying" value={row.underlying} />
                {hedgedUnderlying && <DetailItem label="Hedged Underlying" value={hedgedUnderlying} />}
                <DetailItem label="Product" value={row.product_type} />
                <DetailItem label="Currency" value={row.currency} />
                <DetailItem label="Product ID" value={row.product_id != null ? String(row.product_id) : row.product?.id != null ? String(row.product.id) : '—'} />
                <DetailItem label="Product Family" value={row.product?.product_family ?? '—'} />
                <DetailItem label="QuantArk Class" value={row.product?.quantark_class ?? '—'} />
                <DetailItem label="Engine" value={row.engine_name ?? '—'} />
                <DetailItem label="Mapping" value={row.mapping_status} />
              </dl>
            </PositionDetailSection>
            {onSave && (
              <PositionDetailSection title="Edit Position">
                <PositionEditForm row={row} onSave={onSave} saving={saving} />
              </PositionDetailSection>
            )}
            {!onSave && (
              <PositionDetailSection title="Contract Terms">
                <ReadonlyProductTerms
                  productType={row.product_type}
                  value={row.product_kwargs ?? {}}
                  idPrefix={`product-${row.id}`}
                />
              </PositionDetailSection>
            )}
          </div>
        </TabsContent>

        <TabsContent value="lifecycle">
          <PositionLifecycleTimeline
            row={row}
            events={lifecycleEvents ?? []}
            onAddEvent={onAddLifecycleEvent ?? (() => {})}
            onCancelEvent={onCancelLifecycleEvent}
            adding={addingLifecycleEvent}
          />
        </TabsContent>

        <TabsContent value="pricing">
          <div className="wl-positions__detail">
            <PositionDetailSection title="Pricing Params">
              <ResolvedPricingParamsBlock
                resolvedParams={resolvedParams}
                loading={resolvedParamsLoading}
                deltaOne={isDeltaOneRow(row)}
              />
            </PositionDetailSection>
            <PositionDetailSection title="Pricing Ticket">
              <PricingTicket
                row={row}
                onPricePosition={onPricePosition}
                pricing={pricing}
                selectedPricingProfile={selectedPricingProfile}
                pricingParameterResolution={pricingParameterResolution}
                resolvedParams={resolvedParams}
              />
            </PositionDetailSection>
            <PositionDetailSection title="Market Inputs">
              <ReadonlyObjectForm title="Market Inputs" value={row.market_inputs ?? {}} idPrefix={`market-${row.id}`} />
            </PositionDetailSection>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function PositionGreekMetricCards({ row }: { row: PositionRow }) {
  const hasGreeks = row.delta != null || row.gamma != null || row.vega != null || row.theta != null || row.rho != null || row.rho_q != null;
  if (!hasGreeks) return null;
  const greeks = [
    { label: 'Delta', value: row.delta },
    { label: 'Gamma', value: row.gamma },
    { label: 'Vega', value: row.vega },
    { label: 'Theta', value: row.theta },
    { label: 'Rho', value: row.rho },
    { label: 'RhoQ', value: row.rho_q },
  ];
  return (
    <>
      {greeks.map((greek) => (
        <Tile
          key={greek.label}
          label={greek.label}
          value={formatNullableNumber(greek.value, 4)}
          variant={signedTileVariant(greek.value)}
          className="wl-positions__greek-card"
        />
      ))}
    </>
  );
}

function PositionDetailSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="wl-positions__detail-section">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function ReadonlyProductTerms({
  productType,
  value,
  idPrefix,
}: {
  productType: string;
  value: Record<string, unknown>;
  idPrefix: string;
}) {
  const entries = Object.entries(value);
  if (entries.length === 0) return <div className="wl-positions__term-empty">No values captured.</div>;

  const knownKeys = PRODUCT_TERM_KEYS_BY_PRODUCT[productType] ?? [];
  const knownKeySet = new Set(knownKeys);
  const knownEntries = entries.filter(([key]) => knownKeySet.has(key) && !PRODUCT_NESTED_CONFIG_KEYS.has(key));
  const nestedEntries = entries.filter(([key, fieldValue]) => PRODUCT_NESTED_CONFIG_KEYS.has(key) && isComplexFormValue(fieldValue));
  const extraEntries = entries.filter(([key]) => !knownKeySet.has(key) && !PRODUCT_NESTED_CONFIG_KEYS.has(key));
  const hasProductTerms = knownEntries.length > 0 || nestedEntries.length > 0;

  return (
    <>
      <form className="wl-positions__term-form" onSubmit={(event) => event.preventDefault()}>
        <fieldset>
          <legend>Product Terms</legend>
          {hasProductTerms ? (
            <>
              {knownEntries.length > 0 && <ReadonlyEntries entries={knownEntries} idPrefix={`${idPrefix}-known`} depth={0} />}
              {nestedEntries.length > 0 && (
                <div className="wl-positions__term-groups">
                  {nestedEntries.map(([fieldKey, fieldValue]) => (
                    <ReadonlyComplexField
                      key={fieldKey}
                      fieldKey={fieldKey}
                      value={fieldValue}
                      id={`${idPrefix}-${sanitizeId(fieldKey)}`}
                      depth={0}
                    />
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="wl-positions__term-empty">No values captured.</div>
          )}
        </fieldset>
      </form>
      {extraEntries.length > 0 && (
        <form className="wl-positions__term-form" onSubmit={(event) => event.preventDefault()}>
          <fieldset>
            <legend>Extra Fields</legend>
            <div className="wl-positions__term-extra-count">{extraEntries.length} fields</div>
            <div className="wl-positions__term-extra-body">
              <ReadonlyEntries entries={extraEntries} idPrefix={`${idPrefix}-extra`} depth={0} />
            </div>
          </fieldset>
        </form>
      )}
    </>
  );
}

type EngineFamily = 'quad' | 'mc' | 'pde' | 'analytical';

type ParamSpec = {
  key: string;
  label: string;
  type: 'number' | 'integer' | 'boolean' | 'select';
  defaultValue: string | boolean;
  min?: number;
  options?: string[];
};

type PricingTicketState = {
  valuation_date: string;
  spot: string;
  rate: string;
  dividend_yield: string;
  volatility: string;
  engine_name: string;
  params: Record<string, string | boolean>;
  advancedJson: string;
  compute_greeks: boolean;
};

type PricingParameterMatchType = 'trade_id' | 'underlying' | 'missing' | 'ambiguous' | 'incomplete';

type PricingParameterResolution = {
  row: PricingParameterRow | null;
  matchType: PricingParameterMatchType;
  missingPricingFields: string[];
  candidateCount: number;
  ok: boolean;
};

// Spot now resolves from the quote store, not the profile row — a row is
// complete on r/q/vol alone (parity with backend PRICING_PARAMETER_FIELDS).
const REQUIRED_PRICING_FIELDS = ['rate', 'dividend_yield', 'volatility'] as const;

const ENGINE_OPTIONS_BY_PRODUCT: Record<string, string[]> = {
  EuropeanVanillaOption: ['BlackScholesEngine', 'EuropeanMCEngine', 'EuropeanQuadEngine', 'PDEEngine'],
  BarrierOption: ['BarrierAnalyticalEngine', 'BarrierOptionMCEngine', 'BarrierQuadEngine', 'PDEEngine'],
  SnowballOption: ['SnowballQuadEngine', 'SnowballMCEngine', 'PDEEngine', 'KOResetSnowballQuadEngine'],
  PhoenixOption: ['PhoenixQuadEngine', 'PhoenixMCEngine', 'PDEEngine'],
  CashOrNothingDigitalOption: ['DigitalOptionAnalyticalEngine', 'DigitalOptionMCEngine'],
  AsianOption: ['AsianOptionAnalyticalEngine', 'AsianOptionMCEngine'],
};

const PARAM_SPECS_BY_FAMILY: Record<Exclude<EngineFamily, 'analytical'>, ParamSpec[]> = {
  quad: [
    { key: 'grid_points', label: 'Grid Points', type: 'integer', defaultValue: '1001', min: 100 },
    { key: 'num_std_devs', label: 'Std Devs', type: 'number', defaultValue: '10', min: 3 },
  ],
  mc: [
    { key: 'num_paths', label: 'Path Nums', type: 'integer', defaultValue: '10000', min: 1 },
    { key: 'time_steps', label: 'Time Steps', type: 'integer', defaultValue: '100', min: 1 },
    { key: 'seed', label: 'Seed', type: 'integer', defaultValue: '42', min: 0 },
  ],
  pde: [
    { key: 'grid_size', label: 'Grid Size', type: 'integer', defaultValue: '400', min: 1 },
    { key: 'time_steps', label: 'Time Steps', type: 'integer', defaultValue: '200', min: 1 },
    { key: 'max_grid_size', label: 'Max Grid Size', type: 'integer', defaultValue: '', min: 1 },
    { key: 'max_time_steps', label: 'Max Time Steps', type: 'integer', defaultValue: '', min: 1 },
    { key: 'auto_grid', label: 'Auto Grid', type: 'boolean', defaultValue: true },
    {
      key: 'time_grid_type',
      label: 'Time Grid Type',
      type: 'select',
      defaultValue: 'uniform',
      options: ['uniform', 'graded', 'event_clustered', 'event_aligned'],
    },
  ],
};

const PARAM_TYPE_BY_FAMILY: Record<Exclude<EngineFamily, 'analytical'>, string> = {
  quad: 'quad_params',
  mc: 'mc_params',
  pde: 'pde_params',
};

const PRODUCT_TERM_KEYS_BY_PRODUCT: Record<string, string[]> = {
  EuropeanVanillaOption: ['strike', 'option_type', 'exercise_date', 'settlement_date'],
  AmericanOption: ['strike', 'option_type', 'exercise_date', 'settlement_date'],
  CashOrNothingDigitalOption: ['strike', 'payout', 'option_type', 'exercise_date', 'settlement_date'],
  BarrierOption: ['strike', 'option_type', 'barrier', 'barrier_type', 'rebate', 'participation_rate', 'exercise_date', 'settlement_date'],
  SnowballOption: ['initial_price', 'strike', 'initial_date', 'exercise_date', 'settlement_date', 'contract_multiplier', 'is_reverse', '_otc_ki_observation_convention', '_otc_lifecycle_knocked_in', '_otc_lifecycle_state'],
  PhoenixOption: ['initial_price', 'strike', 'initial_date', 'exercise_date', 'settlement_date', 'contract_multiplier', 'is_reverse', '_otc_ki_observation_convention', '_otc_lifecycle_knocked_in', '_otc_lifecycle_state'],
  SingleSharkfinOption: ['strike', 'option_type', 'participation_rate', 'knock_out_rebate', 'no_hit_rebate', 'exercise_date', 'settlement_date'],
  DoubleSharkfinOption: ['strike', 'option_type', 'participation_rate', 'knock_out_rebate', 'no_hit_rebate', 'exercise_date', 'settlement_date'],
  AsianOption: ['strike', 'option_type', 'exercise_date', 'settlement_date'],
};

const PRODUCT_NESTED_CONFIG_KEYS = new Set([
  'barrier_config',
  'payoff_config',
  'coupon_config',
  'accrual_config',
]);

const KO_SCHEDULE_DETAIL_KEYS = new Set(['ko_barrier', 'ko_rate']);
const SCHEDULE_COLUMN_ORDER = [
  'observation_date',
  'barrier',
  'return_rate',
  'is_rate_annualized',
  'coupon_rate',
  'accrual_factor',
];

function PricingTicket({
  row,
  onPricePosition,
  pricing,
  selectedPricingProfile,
  pricingParameterResolution,
  resolvedParams,
}: {
  row: PositionRow;
  onPricePosition: (row: PositionRow, request: PositionPricingRequest) => void | Promise<void>;
  pricing: boolean;
  selectedPricingProfile: PricingProfileOption | null;
  pricingParameterResolution: PricingParameterResolution;
  resolvedParams?: ResolvedPricingParams | null;
}) {
  const deltaOne = isDeltaOneRow(row);
  const effectivePricingProfile = deltaOne ? null : selectedPricingProfile;
  const profileExtractionWarning = effectivePricingProfile && !pricingParameterResolution.ok
    ? 'Selected pricing profile cannot extract pricing parameters for this position. Pricing fields fall back to resolved market data and assumptions.'
    : null;
  const [ticket, setTicket] = useState<PricingTicketState>(() => initialTicketState(row, effectivePricingProfile, resolvedParams ?? null));
  const engineOptions = useMemo(() => engineOptionsForRow(row), [row]);
  const family = engineFamily(ticket.engine_name);
  const validation = useMemo(
    () => buildPricingRequest(ticket, effectivePricingProfile?.id ?? null, { deltaOne }),
    [ticket, effectivePricingProfile, deltaOne],
  );
  const paramSpecs = family === 'analytical' ? [] : PARAM_SPECS_BY_FAMILY[family];

  useEffect(() => {
    setTicket(initialTicketState(row, effectivePricingProfile, resolvedParams ?? null));
  }, [row.id, effectivePricingProfile?.id, resolvedParams]);

  const updateField = (key: keyof PricingTicketState, value: string) => {
    setTicket((current) => ({ ...current, [key]: value }));
  };

  const updateParam = (key: string, value: string | boolean) => {
    setTicket((current) => ({ ...current, params: { ...current.params, [key]: value } }));
  };

  const updateEngine = (engineName: string) => {
    setTicket((current) => {
      const currentFamily = engineFamily(current.engine_name);
      const nextFamily = engineFamily(engineName);
      return {
        ...current,
        engine_name: engineName,
        params: currentFamily === nextFamily ? current.params : defaultParamState(engineName, {}),
        advancedJson: currentFamily === nextFamily ? current.advancedJson : stripAdvancedJsonText(current.advancedJson),
      };
    });
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (validation.error) return;
    void onPricePosition(row, validation.request);
  };

  return (
    <form className="wl-positions__term-form wl-positions__ticket" onSubmit={handleSubmit}>
      <fieldset>
        <legend>Pricing Ticket</legend>
        {effectivePricingProfile && (
          <div className="wl-positions__ticket-source">
            <span>Loaded profile</span>
            <strong>{effectivePricingProfile.name} · {formatProfileDate(effectivePricingProfile.valuation_date)}</strong>
            <span>{profileSourceLabel(pricingParameterResolution)}</span>
          </div>
        )}
        {profileExtractionWarning && (
          <div className="wl-positions__ticket-warning" role="alert">
            {profileExtractionWarning}
          </div>
        )}
        <div className="wl-positions__ticket-grid">
          <label className="wl-positions__term-field" htmlFor={`pricing-${row.id}-valuation-date`}>
            <span>Valuation Date</span>
            <input
              id={`pricing-${row.id}-valuation-date`}
              aria-label="Pricing valuation date"
              value={ticket.valuation_date}
              placeholder="YYYY-MM-DD or ISO datetime"
              onChange={(event) => updateField('valuation_date', event.target.value)}
            />
          </label>
          <PricingNumberField
            id={`pricing-${row.id}-spot`}
            label="Spot"
            ariaLabel="Pricing spot"
            value={ticket.spot}
            onChange={(value) => updateField('spot', value)}
          />
          {!deltaOne && (
            <>
              <PricingNumberField
                id={`pricing-${row.id}-rate`}
                label="Rate"
                ariaLabel="Pricing rate"
                value={ticket.rate}
                onChange={(value) => updateField('rate', value)}
              />
              <PricingNumberField
                id={`pricing-${row.id}-dividend-yield`}
                label="Dividend Yield"
                ariaLabel="Pricing dividend yield"
                value={ticket.dividend_yield}
                onChange={(value) => updateField('dividend_yield', value)}
              />
              <PricingNumberField
                id={`pricing-${row.id}-volatility`}
                label="Volatility"
                ariaLabel="Pricing volatility"
                value={ticket.volatility}
                onChange={(value) => updateField('volatility', value)}
              />
            </>
          )}
          <Select
            label="Pricing engine"
            id={`pricing-${row.id}-engine`}
            className="wl-positions__term-field"
            value={ticket.engine_name}
            onChange={(v) => updateEngine(v)}
            options={engineOptions.map((engine) => ({
              value: engine,
              label: engine,
            }))}
          />
        </div>
        {paramSpecs.length > 0 && (
          <div className="wl-positions__ticket-params" aria-label="Engine parameters">
            {paramSpecs.map((spec) => (
              <GuidedParamField
                key={spec.key}
                rowId={row.id}
                spec={spec}
                value={ticket.params[spec.key] ?? spec.defaultValue}
                onChange={(value) => updateParam(spec.key, value)}
              />
            ))}
          </div>
        )}
        <label className="wl-positions__term-field wl-positions__term-field--wide" htmlFor={`pricing-${row.id}-json`}>
          <span>Engine Kwargs (JSON)</span>
          <textarea
            id={`pricing-${row.id}-json`}
            aria-label="Engine kwargs JSON"
            value={ticket.advancedJson}
            rows={5}
            onChange={(event) => updateField('advancedJson', event.target.value)}
          />
        </label>
        {validation.error && (
          <div className="wl-positions__ticket-error" role="alert">
            {validation.error}
          </div>
        )}
        <div className="wl-positions__ticket-actions">
          <label className="wl-positions__check-field" htmlFor={`pricing-${row.id}-compute-greeks`}>
            <input
              id={`pricing-${row.id}-compute-greeks`}
              type="checkbox"
              checked={ticket.compute_greeks}
              onChange={(event) => setTicket((current) => ({ ...current, compute_greeks: event.target.checked }))}
            />
            <span>Compute Greeks</span>
          </label>
          <Button type="submit" variant="primary" disabled={pricing || Boolean(validation.error)}>
            <Calculator size={16} aria-hidden="true" />
            <span>{pricing ? 'Pricing' : 'Price Position'}</span>
          </Button>
        </div>
      </fieldset>
    </form>
  );
}

function PricingNumberField({
  id,
  label,
  ariaLabel,
  value,
  onChange,
}: {
  id: string;
  label: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="wl-positions__term-field" htmlFor={id}>
      <span>{label}</span>
      <input
        id={id}
        aria-label={ariaLabel}
        type="number"
        step="any"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function GuidedParamField({
  rowId,
  spec,
  value,
  onChange,
}: {
  rowId: number;
  spec: ParamSpec;
  value: string | boolean;
  onChange: (value: string | boolean) => void;
}) {
  const id = `pricing-${rowId}-param-${sanitizeId(spec.key)}`;
  if (spec.type === 'boolean') {
    return (
      <label className="wl-positions__check-field" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>{spec.label}</span>
      </label>
    );
  }
  if (spec.type === 'select') {
    return (
      <Select
        label={spec.label}
        id={id}
        className="wl-positions__term-field"
        value={String(value)}
        onChange={(v) => onChange(v)}
        options={(spec.options ?? []).map((option) => ({
          value: option,
          label: option,
        }))}
      />
    );
  }
  return (
    <PricingNumberField
      id={id}
      label={spec.label}
      ariaLabel={spec.label}
      value={String(value)}
      onChange={onChange}
    />
  );
}

function initialTicketState(
  row: PositionRow,
  pricingProfile: PricingProfileOption | null = null,
  resolvedParams: ResolvedPricingParams | null = null,
): PricingTicketState {
  const engineName = row.engine_name || defaultEngineForProduct(row.product_type);
  const profileValuationDate = pricingProfile?.valuation_date ? formatProfileDate(pricingProfile.valuation_date) : '';
  // Market fields prefill from the server-resolved params (spot from the quote
  // store, r/q/vol from profile → assumptions) — never from the last run's
  // market_inputs snapshot, which can echo a stale override forever.
  return {
    valuation_date: profileValuationDate || scalarString(row.market_inputs?.valuation_date),
    spot: scalarString(resolvedParams?.spot?.value),
    rate: scalarString(resolvedParams?.rate?.value),
    dividend_yield: scalarString(resolvedParams?.dividend_yield?.value),
    volatility: scalarString(resolvedParams?.volatility?.value),
    engine_name: engineName,
    params: defaultParamState(engineName, row.engine_kwargs ?? {}),
    advancedJson: stringifyJsonObject(stripEngineParamKeys(row.engine_kwargs ?? {})),
    compute_greeks: false,
  };
}

function pricingParameterResolutionForPosition(
  row: PositionRow,
  profile: PricingProfileOption | null,
): PricingParameterResolution {
  if (!profile?.rows?.length) {
    return {
      row: null,
      matchType: 'missing',
      missingPricingFields: [],
      candidateCount: 0,
      ok: false,
    };
  }
  const tradeId = normalizeKey(row.trade_id);
  const underlying = normalizeKey(row.underlying);
  const exact = tradeId
    ? profile.rows.find((candidate) => normalizeKey(candidate.source_trade_id) === tradeId)
    : null;
  if (exact) {
    const missing = missingPricingFields(exact);
    if (missing.length > 0) {
      return {
        row: exact,
        matchType: 'incomplete',
        missingPricingFields: missing,
        candidateCount: 1,
        ok: false,
      };
    }
    return {
      row: exact,
      matchType: 'trade_id',
      missingPricingFields: [],
      candidateCount: 1,
      ok: true,
    };
  }

  const underlyingRows = profile.rows.filter((candidate) => normalizeKey(candidate.symbol) === underlying);
  if (underlyingRows.length === 0) {
    return {
      row: null,
      matchType: 'missing',
      missingPricingFields: [],
      candidateCount: 0,
      ok: false,
    };
  }
  const completeRows = underlyingRows.filter((candidate) => missingPricingFields(candidate).length === 0);
  if (completeRows.length === 1) {
    return {
      row: completeRows[0],
      matchType: 'underlying',
      missingPricingFields: [],
      candidateCount: underlyingRows.length,
      ok: true,
    };
  }
  if (completeRows.length > 1) {
    return {
      row: null,
      matchType: 'ambiguous',
      missingPricingFields: [],
      candidateCount: completeRows.length,
      ok: false,
    };
  }
  return {
    row: null,
    matchType: 'incomplete',
    missingPricingFields: Array.from(new Set(underlyingRows.flatMap(missingPricingFields))).sort(),
    candidateCount: underlyingRows.length,
    ok: false,
  };
}

function normalizeKey(value: string | null | undefined): string {
  return String(value ?? '').trim().toLowerCase();
}

function missingPricingFields(row: PricingParameterRow): string[] {
  return REQUIRED_PRICING_FIELDS.filter((field) => row[field] == null);
}

function profileSourceLabel(resolution: PricingParameterResolution): string {
  if (resolution.row && resolution.matchType === 'trade_id') {
    return `Trade row ${resolution.row.source_row ?? resolution.row.id}`;
  }
  if (resolution.row && resolution.matchType === 'underlying') {
    return `Underlying row ${resolution.row.source_row ?? resolution.row.id}`;
  }
  if (resolution.matchType === 'ambiguous') return 'Ambiguous underlying rows';
  if (resolution.matchType === 'incomplete') return 'Incomplete pricing row';
  return 'No matching pricing row';
}

function engineOptionsForRow(row: PositionRow): string[] {
  const options = ENGINE_OPTIONS_BY_PRODUCT[row.product_type] ?? [defaultEngineForProduct(row.product_type)];
  return uniqueStrings([...options, row.engine_name].filter((value): value is string => Boolean(value)));
}

function defaultEngineForProduct(productType: string): string {
  return ENGINE_OPTIONS_BY_PRODUCT[productType]?.[0] ?? 'BlackScholesEngine';
}

function engineFamily(engineName: string): EngineFamily {
  const normalized = engineName.toLowerCase();
  if (normalized.includes('quad')) return 'quad';
  if (normalized.includes('mc')) return 'mc';
  if (normalized.includes('pde')) return 'pde';
  return 'analytical';
}

function defaultParamState(engineName: string, engineKwargs: Record<string, unknown>): Record<string, string | boolean> {
  const family = engineFamily(engineName);
  if (family === 'analytical') return {};
  const specs = PARAM_SPECS_BY_FAMILY[family];
  const existingParams = isRecord(engineKwargs.params_kwargs) ? engineKwargs.params_kwargs : {};
  return Object.fromEntries(
    specs.map((spec) => {
      const existing = existingParams[spec.key];
      if (spec.type === 'boolean') {
        return [spec.key, typeof existing === 'boolean' ? existing : spec.defaultValue];
      }
      return [spec.key, existing == null ? spec.defaultValue : String(existing)];
    }),
  );
}

function buildPricingRequest(
  ticket: PricingTicketState,
  selectedPricingProfileId: number | null = null,
  options: { deltaOne?: boolean } = {},
): { request: PositionPricingRequest; error: string | null } {
  const parsedAdvanced = parseAdvancedJson(ticket.advancedJson);
  if (parsedAdvanced.error) {
    return { request: emptyPricingRequest(ticket.engine_name), error: parsedAdvanced.error };
  }

  const request: PositionPricingRequest = {
    engine_name: ticket.engine_name,
    engine_kwargs: composeEngineKwargs(ticket, parsedAdvanced.value),
    compute_greeks: ticket.compute_greeks,
  };
  if (selectedPricingProfileId != null && !options.deltaOne) {
    request.pricing_parameter_profile_id = selectedPricingProfileId;
  }
  if (ticket.valuation_date.trim()) {
    request.valuation_date = ticket.valuation_date.trim();
  }

  const marketKeys = options.deltaOne
    ? (['spot'] as const)
    : (['spot', 'rate', 'dividend_yield', 'volatility'] as const);
  for (const key of marketKeys) {
    const parsed = parseOptionalNumber(ticket[key], labelForKey(key));
    if (parsed.error) return { request, error: parsed.error };
    if (parsed.value != null) request[key] = parsed.value;
  }

  const params = parseGuidedParams(ticket);
  if (params.error) return { request, error: params.error };
  request.engine_kwargs = composeEngineKwargs(ticket, parsedAdvanced.value, params.value);
  return { request, error: null };
}

function isDeltaOneRow(row: PositionRow): boolean {
  return row.engine_name === 'DeltaOneEngine' || row.product_type === 'Futures' || row.product_type === 'SpotInstrument';
}

function emptyPricingRequest(engineName: string): PositionPricingRequest {
  return { engine_name: engineName, engine_kwargs: {} };
}

function composeEngineKwargs(
  ticket: PricingTicketState,
  advanced: Record<string, unknown>,
  guidedParams: Record<string, unknown> = {},
): Record<string, unknown> {
  const family = engineFamily(ticket.engine_name);
  if (family === 'analytical') return { ...advanced };
  const advancedParams = isRecord(advanced.params_kwargs) ? advanced.params_kwargs : {};
  return {
    ...advanced,
    params_type: PARAM_TYPE_BY_FAMILY[family],
    params_kwargs: { ...advancedParams, ...guidedParams },
  };
}

function parseGuidedParams(ticket: PricingTicketState): { value: Record<string, unknown>; error: string | null } {
  const family = engineFamily(ticket.engine_name);
  if (family === 'analytical') return { value: {}, error: null };
  const params: Record<string, unknown> = {};
  for (const spec of PARAM_SPECS_BY_FAMILY[family]) {
    const raw = ticket.params[spec.key] ?? spec.defaultValue;
    if (spec.type === 'boolean') {
      params[spec.key] = Boolean(raw);
      continue;
    }
    if (spec.type === 'select') {
      if (String(raw).trim()) params[spec.key] = String(raw).trim();
      continue;
    }
    const text = String(raw).trim();
    if (!text) continue;
    const value = Number(text);
    if (!Number.isFinite(value)) {
      return { value: params, error: `${spec.label} must be a valid number.` };
    }
    if (spec.type === 'integer' && !Number.isInteger(value)) {
      return { value: params, error: `${spec.label} must be an integer.` };
    }
    if (spec.min != null && value < spec.min) {
      return { value: params, error: `${spec.label} must be at least ${spec.min}.` };
    }
    params[spec.key] = value;
  }
  return { value: params, error: null };
}

function parseOptionalNumber(text: string, label: string): { value: number | null; error: string | null } {
  const trimmed = text.trim();
  if (!trimmed) return { value: null, error: null };
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    return { value: null, error: `${label} must be a valid number.` };
  }
  return { value, error: null };
}

function parseAdvancedJson(text: string): { value: Record<string, unknown>; error: string | null } {
  const trimmed = text.trim();
  if (!trimmed) return { value: {}, error: null };
  try {
    const parsed = JSON.parse(trimmed);
    if (!isRecord(parsed)) {
      return { value: {}, error: 'Advanced JSON must be an object.' };
    }
    return { value: parsed, error: null };
  } catch (error) {
    return {
      value: {},
      error: `Advanced JSON is invalid: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

function stripAdvancedJsonText(text: string): string {
  const parsed = parseAdvancedJson(text);
  if (parsed.error) return text;
  return stringifyJsonObject(stripEngineParamKeys(parsed.value));
}

function stripEngineParamKeys(value: Record<string, unknown>): Record<string, unknown> {
  const stripped = { ...value };
  delete stripped.params_type;
  delete stripped.params_kwargs;
  return stripped;
}

function stringifyJsonObject(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2);
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

function scalarString(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'string') return value;
  return '';
}

function statusBadgeVariant(status: string): BadgeVariant {
  const normalized = status.toLowerCase();
  if (normalized === 'open') return 'info';
  if (['closed', 'terminated', 'rejected', 'expired'].includes(normalized)) return 'neg';
  if (normalized === 'supported') return 'pos';
  return 'ink';
}

function mappingStatusBadgeVariant(mappingStatus: string): BadgeVariant {
  const normalized = mappingStatus.toLowerCase();
  if (normalized === 'supported') return 'pos';
  if (normalized === 'unsupported' || normalized === 'error') return 'neg';
  if (normalized === 'mapped_with_warnings') return 'warn';
  return 'info';
}

function PositionKindBadge({ value }: { value: PositionRow['position_kind'] }) {
  return (
    <Badge variant={value === 'listed' ? 'info' : 'ink'}>
      {value === 'listed' ? 'LISTED' : 'OTC'}
    </Badge>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** Provenance label for one resolved pricing-param field. */
export function resolvedParamProvenance(param: ResolvedParam | undefined): string {
  if (!param) return 'missing';
  switch (param.source) {
    case 'market_quote': {
      const age = param.age_days != null ? `${Math.round(param.age_days)}d` : '—';
      return `quote · ${age} · ${param.quote_source ?? 'unknown'}`;
    }
    case 'pricing_parameter_profile':
      return `profile #${param.profile_id ?? '—'} · ${param.source_trade_id ?? '—'}`;
    case 'assumption_set':
      return `assumptions #${param.assumption_set_id ?? '—'}`;
    default:
      return 'missing';
  }
}

function resolvedParamValue(param: ResolvedParam | undefined): string {
  if (!param || param.value == null) return '—';
  return Number(param.value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export function ResolvedPricingParamsBlock({
  resolvedParams,
  loading,
  deltaOne = false,
}: {
  resolvedParams?: ResolvedPricingParams | null;
  loading?: boolean;
  deltaOne?: boolean;
}) {
  if (loading && !resolvedParams) {
    return <p className="wl-positions__resolved-params-empty">Resolving pricing params…</p>;
  }
  if (!resolvedParams) {
    return <p className="wl-positions__resolved-params-empty">No resolved pricing params.</p>;
  }
  const fields: { label: string; param: ResolvedParam | undefined }[] = deltaOne
    ? [{ label: 'Spot', param: resolvedParams.spot }]
    : [
        { label: 'Spot', param: resolvedParams.spot },
        { label: 'Rate', param: resolvedParams.rate },
        { label: 'Div yield', param: resolvedParams.dividend_yield },
        { label: 'Volatility', param: resolvedParams.volatility },
      ];
  return (
    <dl className="wl-positions__detail-grid">
      {fields.map(({ label, param }) => (
        <DetailItem
          key={label}
          label={label}
          value={`${resolvedParamValue(param)} (${resolvedParamProvenance(param)})`}
        />
      ))}
    </dl>
  );
}

function ReadonlyObjectForm({
  title,
  value,
  idPrefix,
}: {
  title: string;
  value: Record<string, unknown>;
  idPrefix: string;
}) {
  const entries = Object.entries(value);
  return (
    <form className="wl-positions__term-form" onSubmit={(event) => event.preventDefault()}>
      <fieldset>
        <legend>{title}</legend>
        {entries.length > 0 ? (
          <ReadonlyEntries entries={entries} idPrefix={idPrefix} depth={0} />
        ) : (
          <div className="wl-positions__term-empty">No values captured.</div>
        )}
      </fieldset>
    </form>
  );
}

function ReadonlyEntries({
  entries,
  idPrefix,
  depth,
}: {
  entries: [string, unknown][];
  idPrefix: string;
  depth: number;
}) {
  const scalarEntries = entries.filter(([, value]) => !isComplexFormValue(value));
  const complexEntries = entries.filter(([, value]) => isComplexFormValue(value));

  return (
    <>
      {scalarEntries.length > 0 && (
        <div className="wl-positions__term-grid">
          {scalarEntries.map(([key, fieldValue]) => (
            <ReadonlyScalarField
              key={key}
              fieldKey={key}
              value={fieldValue}
              id={`${idPrefix}-${sanitizeId(key)}`}
            />
          ))}
        </div>
      )}
      {complexEntries.length > 0 && (
        <div className="wl-positions__term-groups">
          {complexEntries.map(([key, fieldValue]) => {
            const scheduleRecords = scheduleRecordsForValue(fieldValue);
            if (scheduleRecords) {
              return (
                <ReadonlyScheduleField
                  key={key}
                  fieldKey={key}
                  value={fieldValue as Record<string, unknown>}
                  records={scheduleRecords}
                />
              );
            }
            return (
              <ReadonlyComplexField
                key={key}
                fieldKey={key}
                value={fieldValue}
                id={`${idPrefix}-${sanitizeId(key)}`}
                depth={depth + 1}
              />
            );
          })}
        </div>
      )}
    </>
  );
}

function ReadonlyScalarField({ fieldKey, value, id }: { fieldKey: string; value: unknown; id: string }) {
  const label = labelForKey(fieldKey);
  return (
    <label className="wl-positions__term-field" htmlFor={id}>
      <span>{label}</span>
      <input id={id} value={formatFormValue(value)} readOnly />
    </label>
  );
}

function ReadonlyComplexField({
  fieldKey,
  value,
  id,
  depth,
}: {
  fieldKey: string;
  value: unknown;
  id: string;
  depth: number;
}) {
  const label = labelForKey(fieldKey);
  const records = Array.isArray(value) ? flatRecordArray(value) : null;
  const nestedEntries = isRecord(value)
    ? Object.entries(value).filter(([key]) => {
        if (fieldKey === 'barrier_config' && scheduleRecordsForValue(value.ko_observation_schedule) && KO_SCHEDULE_DETAIL_KEYS.has(key)) {
          return false;
        }
        return true;
      })
    : [];

  return (
    <details className="wl-positions__term-group">
      <summary>
        <span>{label}</span>
        <small>{complexSummary(value)}</small>
      </summary>
      <div className="wl-positions__term-group-body">
        {records ? (
          <ReadonlyRecordTable records={records} idPrefix={id} />
        ) : Array.isArray(value) ? (
          <ReadonlyArrayField label={`${label} Values`} value={value} id={`${id}-values`} />
        ) : isRecord(value) ? (
          <ReadonlyEntries entries={nestedEntries} idPrefix={id} depth={depth} />
        ) : (
          <ReadonlyArrayField label={`${label} Values`} value={[value]} id={`${id}-values`} />
        )}
      </div>
    </details>
  );
}

function ReadonlyScheduleField({
  fieldKey,
  value,
  records,
}: {
  fieldKey: string;
  value: Record<string, unknown>;
  records: Record<string, unknown>[];
}) {
  const metadata = Object.entries(value).filter(([key]) => key !== 'records');
  return (
    <div className="wl-positions__schedule-field">
      <div className="wl-positions__schedule-head">
        <span>{labelForKey(fieldKey)}</span>
        <small>{records.length} rows</small>
      </div>
      <ReadonlyRecordTable records={records} />
      {metadata.length > 0 && (
        <div className="wl-positions__term-table-meta">
          {metadata.map(([key, metadataValue]) => (
            <span key={key}>{labelForKey(key)}: {formatFormValue(metadataValue)}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function ReadonlyArrayField({ label, value, id }: { label: string; value: unknown[]; id: string }) {
  return (
    <label className="wl-positions__term-field wl-positions__term-field--wide" htmlFor={id}>
      <span>{label}</span>
      <textarea id={id} value={value.map(formatFormValue).join('\n')} readOnly rows={formatArrayRows(value)} />
    </label>
  );
}

function ReadonlyRecordTable({ records, idPrefix }: { records: Record<string, unknown>[]; idPrefix?: string }) {
  const columns = orderedRecordColumns(records);
  return (
    <div className="wl-positions__term-table-wrap">
      <table className="wl-positions__term-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{labelForKey(column)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((record, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td key={column}>
                  {idPrefix ? (
                    <input
                      aria-label={`${labelForKey(column)} row ${rowIndex + 1}`}
                      value={formatFormValue(record[column])}
                      readOnly
                    />
                  ) : (
                    formatFormValue(record[column])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {idPrefix && (
        <div className="wl-positions__term-table-meta" id={`${idPrefix}-rows`}>
          {records.length} rows
        </div>
      )}
    </div>
  );
}

function isComplexFormValue(value: unknown): boolean {
  return Array.isArray(value) || (typeof value === 'object' && value !== null);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function flatRecordArray(value: unknown[]): Record<string, unknown>[] | null {
  if (value.length === 0) return null;
  if (!value.every(isRecord)) return null;
  if (!value.every((record) => Object.values(record).every((fieldValue) => !isComplexFormValue(fieldValue)))) {
    return null;
  }
  return value as Record<string, unknown>[];
}

function scheduleRecordsForValue(value: unknown): Record<string, unknown>[] | null {
  if (!isRecord(value) || !Array.isArray(value.records)) return null;
  const records = value.records.filter(isRecord);
  return records.length > 0 ? records : null;
}

function orderedRecordColumns(records: Record<string, unknown>[]): string[] {
  const keys = new Set(records.flatMap((record) => Object.keys(record)));
  return [
    ...SCHEDULE_COLUMN_ORDER.filter((key) => keys.has(key)),
    ...Array.from(keys).filter((key) => !SCHEDULE_COLUMN_ORDER.includes(key)).sort(),
  ];
}

function formatFormValue(value: unknown): string {
  if (value == null) return '';
  if (value instanceof Date) return value.toISOString();
  if (isComplexFormValue(value)) return JSON.stringify(value, null, 2);
  return String(value);
}

function formatArrayRows(value: unknown[]): number {
  return Math.max(4, Math.min(12, value.length));
}

function complexSummary(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} items`;
  if (isRecord(value)) return `${Object.keys(value).length} fields`;
  return '1 value';
}

function labelForKey(key: string): string {
  return key
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatProfileDate(value: string): string {
  if (!value) return '—';
  const datePrefix = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  if (datePrefix) return datePrefix;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Date(parsed).toISOString().slice(0, 10);
}

function sanitizeId(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'field';
}

function formatSigned(n: number, decimals = 2): string {
  return (n >= 0 ? '+' : '') + n.toFixed(decimals);
}

function firstNumber(...values: Array<number | null | undefined>): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

function formatMetricValue(value: number | null | undefined, fallback = '—'): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(2)}K`;
  return `${sign}${abs.toFixed(4)}`;
}

function variantForNumber(value: number | null | undefined, fallback: 'default' | 'pos' | 'neg' = 'default') {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  if (value === 0) return 'default';
  return value > 0 ? 'pos' : 'neg';
}

function summarizePositions(rows: PositionRow[]) {
  const live = rows.filter((row) => row.status === 'open').length;
  const notional = rows.reduce((sum, row) => sum + Math.abs(row.quantity * (row.price ?? row.entry_price ?? 0)), 0);
  return {
    live,
    total: rows.length,
    notional,
    topUnderlying: dominantLabel(rows, (row) => row.underlying),
    topType: dominantLabel(rows, (row) => row.product_type),
  };
}

function dominantLabel(rows: PositionRow[], keyOf: (row: PositionRow) => string | null | undefined): string {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const key = keyOf(row) || 'Unknown';
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let bestKey = '—';
  let bestCount = 0;
  for (const [key, count] of counts) {
    if (count > bestCount || (count === bestCount && key.localeCompare(bestKey) < 0)) {
      bestKey = key;
      bestCount = count;
    }
  }
  return bestCount > 0 ? `${bestKey} · ${bestCount}` : '—';
}

function formatNullableNumber(n: number | null, decimals = 2): string {
  return n == null || !Number.isFinite(n) ? '—' : n.toFixed(decimals);
}

function signedTileVariant(n: number | null): 'default' | 'pos' | 'neg' {
  if (n == null || !Number.isFinite(n) || n === 0) return 'default';
  return n > 0 ? 'pos' : 'neg';
}

function formatNullableSigned(n: number | null, decimals = 2): string {
  return n == null || !Number.isFinite(n) ? '—' : formatSigned(n, decimals);
}

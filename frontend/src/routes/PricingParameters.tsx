import { useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { Download, Plus, Upload } from 'lucide-react';
import { Button } from '../components/Button';
import { DatePicker } from '../components/DatePicker';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { NumberInput } from '../components/NumberInput';
import { MasterDetailPage } from '../components/templates';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type { PageContext, PageContextReporter, PricingParameterProfile } from '../types';
import { useProfileLibrary } from './ProfileLibrary';
import { shortProfileDate } from './pricingBuildReadiness';
import { composeImportSummary } from './pricingImportSummary';
import './PricingParameters.css';

type ImportPayload = {
  file: File;
  name: string;
  valuationDate: string;
  sheetName: string;
};

export type ManualPricingParameterPayload = {
  name: string;
  valuationDate: string;
  sourceTradeId: string;
  symbol: string;
  rate: string;
  dividendYield: string;
  volatility: string;
};

export type PricingParameterFeedback = {
  tone: 'success' | 'error';
  message: string;
};

type Props = {
  profiles: PricingParameterProfile[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  importing: boolean;
  creating: boolean;
  feedback: PricingParameterFeedback | null;
  onSelectProfile: (id: number) => void;
  onCreateManual: (payload: ManualPricingParameterPayload) => void;
  onImport: (payload: ImportPayload) => void;
  onPageContextChange?: PageContextReporter;
};

export function PricingParameters({
  profiles,
  selectedId,
  loading,
  error,
  importing,
  creating,
  feedback,
  onSelectProfile,
  onCreateManual,
  onImport,
  onPageContextChange,
}: Props) {
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [manualModalOpen, setManualModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [valuationDate, setValuationDate] = useState('');
  const [sheetName, setSheetName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [manualName, setManualName] = useState('');
  const [manualValuationDate, setManualValuationDate] = useState('');
  const [manualSourceTradeId, setManualSourceTradeId] = useState('');
  const [manualSymbol, setManualSymbol] = useState('');
  const [manualRate, setManualRate] = useState('');
  const [manualDividendYield, setManualDividendYield] = useState('');
  const [manualVolatility, setManualVolatility] = useState('');
  const [filterMode, setFilterMode] = useState<'all' | 'live'>('all');
  const fileRef = useRef<HTMLInputElement | null>(null);

  const selected = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? null,
    [profiles, selectedId],
  );

  const contextRows = useMemo(() => {
    const rows = selected?.rows ?? [];
    if (filterMode === 'all') return rows;
    return rows.filter((row) => (
      row.volatility != null && row.rate != null && row.dividend_yield != null
    ));
  }, [filterMode, selected?.rows]);

  const dormantTradeIds = useMemo(
    () => new Set<string>((selected?.summary?.dormant_trade_ids as string[] | undefined) ?? []),
    [selected?.summary?.dormant_trade_ids],
  );

  const pageContext = useMemo(() => {
    const base: PageContext = {
      route: 'pricing-parameters',
      title: 'Pricing Params',
      path: '/',
      entity_ids: { pricing_profile_id: selected?.id ?? null },
      snapshot: {
        selected_profile: selected
          ? {
              id: selected.id,
              name: selected.name,
              valuation_date: selected.valuation_date,
              status: selected.status,
              row_count: selected.summary?.row_count ?? selected.rows.length,
              rows_applied: selected.summary?.rows_applied ?? null,
              rows_dormant: selected.summary?.rows_dormant ?? null,
              quotes_emitted: selected.summary?.quotes_emitted ?? null,
              spot_conflict_count: (selected.summary?.spot_conflicts ?? []).length,
              import_summary: composeImportSummary(selected.summary),
            }
          : null,
        filter_mode: filterMode,
        visible_rows: contextRows.slice(0, 12).map((row) => ({
          id: row.id,
          source_trade_id: row.source_trade_id,
          symbol: row.symbol,
          instrument_id: row.instrument_id ?? null,
          rate: row.rate,
          dividend_yield: row.dividend_yield,
          volatility: row.volatility,
          status: dormantTradeIds.has(row.source_trade_id) ? 'dormant' : 'applied',
        })),
      },
      chips: selected
        ? ['global', shortProfileDate(selected.valuation_date), `${contextRows.length} rows`]
        : ['global', loading ? 'loading' : 'empty'],
    };

    if (manualModalOpen) {
      return {
        ...base,
        title: 'New Pricing Params dialog',
        snapshot: {
          parent_context: base,
          draft: {
            name: manualName,
            valuation_date: manualValuationDate,
            source_trade_id: manualSourceTradeId,
            symbol: manualSymbol,
            rate: manualRate,
            dividend_yield: manualDividendYield,
            volatility: manualVolatility,
          },
        },
        chips: ['dialog', 'New Pricing Params'],
      };
    }

    if (!importModalOpen) return base;
    return {
      ...base,
      title: 'Import Pricing Params dialog',
      snapshot: {
        parent_context: base,
        draft: {
          name,
          valuation_date: valuationDate,
          sheet_name: sheetName,
          file_name: file?.name ?? null,
        },
      },
      chips: ['dialog', 'Import Pricing Params'],
    };
  }, [
    contextRows,
    dormantTradeIds,
    file?.name,
    filterMode,
    importModalOpen,
    loading,
    manualDividendYield,
    manualModalOpen,
    manualName,
    manualRate,
    manualSourceTradeId,
    manualSymbol,
    manualValuationDate,
    manualVolatility,
    name,
    selected,
    sheetName,
    valuationDate,
  ]);
  usePageContextReporter(pageContext, onPageContextChange);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    onImport({ file, name, valuationDate, sheetName });
    setImportModalOpen(false);
    setFile(null);
    setName('');
    setValuationDate('');
    setSheetName('');
    if (fileRef.current) fileRef.current.value = '';
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.currentTarget.files?.[0] ?? null);
  };

  const hasManualValue = [manualRate, manualDividendYield, manualVolatility]
    .some((value) => value.trim() !== '');
  const canSubmitManual = manualSymbol.trim() !== '' && hasManualValue && !creating;

  const resetManualForm = () => {
    setManualName('');
    setManualValuationDate('');
    setManualSourceTradeId('');
    setManualSymbol('');
    setManualRate('');
    setManualDividendYield('');
    setManualVolatility('');
  };

  const submitManual = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmitManual) return;
    onCreateManual({
      name: manualName,
      valuationDate: manualValuationDate,
      sourceTradeId: manualSourceTradeId,
      symbol: manualSymbol,
      rate: manualRate,
      dividendYield: manualDividendYield,
      volatility: manualVolatility,
    });
    setManualModalOpen(false);
    resetManualForm();
  };

  const { rail, detail } = useProfileLibrary({
    profiles,
    selected,
    loading,
    filterMode,
    onFilterModeChange: setFilterMode,
    onSelectProfile,
  });

  const actions = (
    <div className="wl-pricing-params__actions">
      <Button type="button" variant="primary" onClick={() => setManualModalOpen(true)} disabled={creating}>
        <Plus size={16} aria-hidden="true" />
        <span>New</span>
      </Button>
      <a
        className="wl-button wl-button--default"
        href="/api/pricing-parameter-profiles/import-template"
        download="pricing_parameters_import_template.xlsx"
      >
        <Download size={16} aria-hidden="true" />
        <span>Download Template</span>
      </a>
      <Button type="button" onClick={() => setImportModalOpen(true)} disabled={importing}>
        <Upload size={16} aria-hidden="true" />
        <span>Import XLSX</span>
      </Button>
    </div>
  );

  const feedbackNode = feedback ? (
    <p
      className={`wl-pricing-params__feedback wl-pricing-params__feedback--${feedback.tone}`}
      role={feedback.tone === 'error' ? 'alert' : undefined}
    >
      {feedback.message}
    </p>
  ) : undefined;

  return (
    <div className="wl-pricing-params-page">
      <MasterDetailPage
        title="PRICING PARAMS"
        chips={selected ? ['global', shortProfileDate(selected.valuation_date), `${contextRows.length} rows`] : ['global', loading ? 'loading' : 'empty']}
        actions={actions}
        feedback={feedbackNode}
        rail={error ? null : rail}
        railLabel="Pricing parameter profiles"
      >
        {error
          ? <Empty message={`Could not load pricing parameters: ${error}`} />
          : detail}
      </MasterDetailPage>
      <Modal
        open={manualModalOpen}
        onOpenChange={setManualModalOpen}
        title="New Pricing Params"
        layoutKey="new-pricing-parameters"
        defaultHeight={600}
        minHeight={520}
      >
        <form className="wl-pricing-params__form" onSubmit={submitManual}>
          <label>
            Profile name
            <input aria-label="Manual profile name" value={manualName} onChange={(event) => setManualName(event.target.value)} />
          </label>
          <DatePicker
            label="Valuation date"
            value={manualValuationDate}
            onChange={(v) => setManualValuationDate(v)}
          />
          <label>
            Trade ID
            <input aria-label="Trade ID" value={manualSourceTradeId} onChange={(event) => setManualSourceTradeId(event.target.value)} />
          </label>
          <label>
            Symbol
            <input aria-label="Symbol" value={manualSymbol} onChange={(event) => setManualSymbol(event.target.value)} required />
          </label>
          <div className="wl-pricing-params__row-grid">
            <label>
              Rate
              <NumberInput aria-label="Rate" type="number" step="any" value={manualRate} onChange={(event) => setManualRate(event.target.value)} />
            </label>
            <label>
              Div yield
              <NumberInput aria-label="Dividend yield" type="number" step="any" value={manualDividendYield} onChange={(event) => setManualDividendYield(event.target.value)} />
            </label>
            <label>
              Vol
              <NumberInput aria-label="Volatility" type="number" step="any" value={manualVolatility} onChange={(event) => setManualVolatility(event.target.value)} />
            </label>
          </div>
          <div className="wl-pricing-params__form-actions">
            <Button type="submit" variant="primary" disabled={!canSubmitManual}>Create</Button>
          </div>
        </form>
      </Modal>
      <Modal
        open={importModalOpen}
        onOpenChange={setImportModalOpen}
        title="Import Pricing Params"
        layoutKey="import-pricing-parameters"
      >
        <form className="wl-pricing-params__form" onSubmit={submit}>
          <label>
            Profile name
            <input aria-label="Profile name" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <DatePicker
            label="Valuation date"
            value={valuationDate}
            onChange={(v) => setValuationDate(v)}
          />
          <label>
            Sheet name
            <input aria-label="Sheet name" value={sheetName} onChange={(event) => setSheetName(event.target.value)} />
          </label>
          <input
            ref={fileRef}
            aria-label="Pricing parameters xlsx"
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={onFile}
          />
          <div className="wl-pricing-params__form-actions">
            <Button type="submit" variant="primary" disabled={!file || importing}>Import</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

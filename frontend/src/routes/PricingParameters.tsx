import { useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { Download, Upload } from 'lucide-react';
import { Button } from '../components/Button';
import { DatePicker } from '../components/DatePicker';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
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
  feedback: PricingParameterFeedback | null;
  onSelectProfile: (id: number) => void;
  onImport: (payload: ImportPayload) => void;
  onPageContextChange?: PageContextReporter;
};

export function PricingParameters({
  profiles,
  selectedId,
  loading,
  error,
  importing,
  feedback,
  onSelectProfile,
  onImport,
  onPageContextChange,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [valuationDate, setValuationDate] = useState('');
  const [sheetName, setSheetName] = useState('');
  const [file, setFile] = useState<File | null>(null);
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

    if (!modalOpen) return base;
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
    loading,
    modalOpen,
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
    setModalOpen(false);
    setFile(null);
    setName('');
    setValuationDate('');
    setSheetName('');
    if (fileRef.current) fileRef.current.value = '';
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.currentTarget.files?.[0] ?? null);
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
      <a
        className="wl-button wl-button--default"
        href="/api/pricing-parameter-profiles/import-template"
        download="pricing_parameters_import_template.xlsx"
      >
        <Download size={16} aria-hidden="true" />
        <span>Download Template</span>
      </a>
      <Button type="button" onClick={() => setModalOpen(true)} disabled={importing}>
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
        open={modalOpen}
        onOpenChange={setModalOpen}
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

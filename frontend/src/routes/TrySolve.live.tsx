import { useEffect, useMemo, useState } from 'react';
import { api, uploadForm } from '../api/client';
import type {
  Instrument,
  MarketDataProfile,
  PageContextReporter,
  PricingParameterProfile,
  TrySolveBatchOut,
  TrySolveCatalog,
  TrySolveExportOut,
  TrySolveExportRequest,
  TrySolveProduct,
  TrySolveQuoteField,
  TrySolveMarket,
  TrySolveQuoteRequest,
  TrySolveRowIn,
  TrySolveRowOut,
  Underlying,
} from '../types';
import { DEFAULT_TRY_SOLVE_CATALOG, DEFAULT_TRY_SOLVE_ROWS, TrySolve } from './TrySolve';

type Props = {
  onPageContextChange?: PageContextReporter;
  navigate?: (url: string) => void;
};

const TRY_SOLVE_API = '/api/rfq/try-solve';
const MANUAL_ROWS_STORAGE_KEY = 'otc:try-solve:manual-rows';

function loadPersistedManualRows(): TrySolveRowOut[] {
  try {
    const raw = localStorage.getItem(MANUAL_ROWS_STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as TrySolveRowOut[];
  } catch {
    return [];
  }
}

function persistManualRows(rows: TrySolveRowOut[]) {
  const manualRows = rows.filter((row) => row.source === 'manual');
  if (manualRows.length === 0) {
    localStorage.removeItem(MANUAL_ROWS_STORAGE_KEY);
  } else {
    localStorage.setItem(MANUAL_ROWS_STORAGE_KEY, JSON.stringify(manualRows));
  }
}

export function TrySolveLive({ onPageContextChange, navigate = (url) => window.location.assign(url) }: Props) {
  const [catalog, setCatalog,] = useState<TrySolveCatalog | null>(null);
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([]);
  const [marketDataProfiles, setMarketDataProfiles] = useState<MarketDataProfile[]>([]);
  const [underlyings, setUnderlyings] = useState<Instrument[]>([]);
  const [rows, setRows] = useState<TrySolveRowOut[]>(() => [
    ...cloneRows(DEFAULT_TRY_SOLVE_ROWS),
    ...loadPersistedManualRows(),
  ]);
  const [selectedRowId, setSelectedRowId] = useState<string | null>(() => {
    const persisted = loadPersistedManualRows();
    return persisted[0]?.row_id ?? DEFAULT_TRY_SOLVE_ROWS[0]?.row_id ?? null;
  });
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [solving, setSolving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [manualInitialGuessRows, setManualInitialGuessRows] = useState<Set<string>>(() => new Set());
  const normalizedRows = useMemo(
    () => normalizeRowsForSolve(rows, catalog, marketDataProfiles),
    [catalog, marketDataProfiles, rows],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      api<TrySolveCatalog>(`${TRY_SOLVE_API}/catalog`),
      api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
      api<MarketDataProfile[]>('/api/market-data/profiles'),
      api<Instrument[]>('/api/instruments'),
    ])
      .then(([nextCatalog, nextPricingProfiles, nextMarketDataProfiles, nextUnderlyings]) => {
        if (!cancelled) {
          setCatalog(nextCatalog);
          setPricingProfiles(nextPricingProfiles);
          setMarketDataProfiles(nextMarketDataProfiles);
          setUnderlyings(Array.isArray(nextUnderlyings) ? nextUnderlyings : []);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(`Could not load Try Solve setup: ${formatApiError(err)}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setRows((currentRows) => normalizeRowsForSolve(currentRows, catalog, marketDataProfiles));
  }, [catalog, marketDataProfiles, rows]);

  useEffect(() => {
    persistManualRows(rows);
  }, [rows]);

  const handleImportExcel = async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    setImporting(true);
    setError(null);
    setFeedback(null);
    try {
      const batch = await uploadForm<TrySolveBatchOut>(`${TRY_SOLVE_API}/import`, form);
      setRows((currentRows) => {
        const nextRows = shouldReplaceRows(currentRows) ? batch.rows : [...currentRows, ...batch.rows];
        setSelectedRowId((currentSelected) => preserveSelection(currentSelected, nextRows, batch.rows[0]?.row_id ?? null));
        return nextRows;
      });
      setFeedback(importFeedback(batch));
    } catch (err) {
      setError(`Could not import workbook: ${formatApiError(err)}`);
    } finally {
      setImporting(false);
    }
  };

  const handleSolveSelected = async (rowId: string) => {
    const row = normalizedRows.find((candidate) => candidate.row_id === rowId);
    if (!row) return;
    setSolving(true);
    setError(null);
    setFeedback(null);
    try {
      const solvedRow = await api<TrySolveRowOut>(`${TRY_SOLVE_API}/solve`, {
        method: 'POST',
        body: JSON.stringify({ row: toRowIn(row) }),
      });
      setRows((currentRows) => replaceRow(currentRows, solvedRow));
      setSelectedRowId(solvedRow.row_id);
      if (solvedRow.status === 'solved') {
        setFeedback(solveFeedback([solvedRow], `Solved ${solvedRow.row_id}.`));
      } else if (solvedRow.status === 'solve_failed') {
        setError(`Solve failed for ${solvedRow.row_id}. See diagnostics.`);
      } else {
        setFeedback(`Solve checked ${solvedRow.row_id}: ${formatStatusLabel(solvedRow.status)}.`);
      }
    } catch (err) {
      setError(`Could not solve ${row.row_id}: ${formatApiError(err)}`);
    } finally {
      setSolving(false);
    }
  };

  const handleSolveAll = async () => {
    setSolving(true);
    setError(null);
    setFeedback(null);
    try {
      const batch = await api<TrySolveBatchOut>(`${TRY_SOLVE_API}/solve-batch`, {
        method: 'POST',
        body: JSON.stringify({ rows: normalizedRows.map(toRowIn) }),
      });
      setRows(batch.rows);
      setSelectedRowId((currentSelected) => preserveSelection(currentSelected, batch.rows, batch.rows[0]?.row_id ?? null));
      const failedCount = batch.rows.filter((row) => row.status === 'solve_failed').length;
      if (failedCount > 0) {
        setError(`Solve all completed with ${failedCount} failed. ${batch.rows.filter((row) => row.status === 'solved').length} solved.`);
      } else {
        setFeedback(solveFeedback(batch.rows, `Solved ${batch.rows.length} rows.`));
      }
    } catch (err) {
      setError(`Could not solve all rows: ${formatApiError(err)}`);
    } finally {
      setSolving(false);
    }
  };

  const handleExport = async (
    scope: TrySolveExportRequest['scope'],
    selectedRowIds: string[],
  ) => {
    setExporting(true);
    setError(null);
    setFeedback(null);
    try {
      const exported = await api<TrySolveExportOut>(`${TRY_SOLVE_API}/export`, {
        method: 'POST',
        body: JSON.stringify({
          rows: normalizedRows,
          scope,
          selected_row_ids: selectedRowIds,
        }),
      });
      setFeedback(`Exported ${exported.row_count} rows to ${exported.filename}.`);
      navigate(exported.url);
    } catch (err) {
      setError(`Could not export rows: ${formatApiError(err)}`);
    } finally {
      setExporting(false);
    }
  };

  const handleAddManualRequest = (productKey: string) => {
    const product = (catalog?.products ?? DEFAULT_TRY_SOLVE_CATALOG.products).find((item) => item.product_key === productKey);
    if (!product) return;
    setError(null);
    setFeedback(null);
    setRows((currentRows) => {
      const manualRow = createManualRow(product, currentRows, marketDataProfiles);
      setSelectedRowId(manualRow.row_id);
      return [...currentRows, manualRow];
    });
  };

  const handleDeleteRequest = (rowId: string) => {
    setError(null);
    setFeedback(null);
    setRows((currentRows) => {
      const removedIndex = currentRows.findIndex((row) => row.row_id === rowId);
      if (removedIndex < 0) return currentRows;
      const nextRows = currentRows.filter((row) => row.row_id !== rowId);
      setSelectedRowId((currentSelected) => {
        if (currentSelected !== rowId) {
          return preserveSelection(currentSelected, nextRows, nextRows[0]?.row_id ?? null);
        }
        return nextRows[Math.min(removedIndex, nextRows.length - 1)]?.row_id ?? null;
      });
      return nextRows;
    });
  };

  const handleFieldChange = (rowId: string, fieldKey: string, value: unknown) => {
    setError(null);
    setFeedback(null);
    setRows((currentRows) => currentRows.map((row) => {
      if (row.row_id !== rowId) return row;
      const product = findProduct(row.product_key, catalog);
      const nextRow = refreshEditedRow({
        ...row,
        fields: { ...row.fields, [fieldKey]: value },
      }, product);
      const rowWithSpotDefaults = fieldKey === 'underlying'
        ? withLatestSpotDefaults(nextRow, product, marketDataProfiles, value, {
          forceInitialPrice: true,
          forceMarketSpot: true,
        })
        : nextRow;
      return refreshAutoQuoteRange(row, rowWithSpotDefaults, product);
    }));
  };

  const handleMarketChange = (rowId: string, patch: Partial<TrySolveMarket>) => {
    setError(null);
    setFeedback(null);
    setRows((currentRows) => currentRows.map((row) => (
      row.row_id === rowId
        ? refreshAutoQuoteRange(
          row,
          { ...row, market: { ...row.market, ...patch } },
          findProduct(row.product_key, catalog),
        )
        : row
    )));
  };

  const handleQuoteRequestChange = (rowId: string, patch: Partial<TrySolveQuoteRequest>) => {
    setError(null);
    setFeedback(null);
    setManualInitialGuessRows((currentRows) => {
      if (patch.quote_field_key != null) {
        const nextRows = new Set(currentRows);
        nextRows.delete(rowId);
        return nextRows;
      }
      if (hasOwn(patch, 'initial_guess')) {
        const nextRows = new Set(currentRows);
        nextRows.add(rowId);
        return nextRows;
      }
      return currentRows;
    });
    const isManualInitialGuess = manualInitialGuessRows.has(rowId);
    const shouldResetManualInitialGuess = patch.quote_field_key != null;
    const shouldSetManualInitialGuess = hasOwn(patch, 'initial_guess');
    setRows((currentRows) => currentRows.map((row) => (
      row.row_id === rowId
        ? {
          ...row,
          quote_request: autoInitialGuessQuoteRequest(
            row.quote_request,
            patch,
            shouldSetManualInitialGuess ? true : isManualInitialGuess,
            shouldResetManualInitialGuess,
          ),
        }
        : row
    )));
  };

  return (
    <TrySolve
      catalog={catalog ?? undefined}
      pricingProfiles={pricingProfiles}
      marketDataProfiles={marketDataProfiles}
      underlyings={underlyings as unknown as Underlying[]}
      rows={normalizedRows}
      selectedRowId={selectedRowId}
      loading={loading}
      importing={importing}
      exporting={exporting}
      solving={solving}
      error={error}
      feedback={feedback}
      onSelectRow={setSelectedRowId}
      onImportExcel={handleImportExcel}
      onExport={handleExport}
      onSolveSelected={handleSolveSelected}
      onSolveAll={handleSolveAll}
      onAddManualRequest={handleAddManualRequest}
      onDeleteRequest={handleDeleteRequest}
      onFieldChange={handleFieldChange}
      onMarketChange={handleMarketChange}
      onQuoteRequestChange={handleQuoteRequestChange}
      onPageContextChange={onPageContextChange}
    />
  );
}

function createManualRow(
  product: TrySolveProduct,
  rows: TrySolveRowOut[],
  marketDataProfiles: MarketDataProfile[],
): TrySolveRowOut {
  const fields = Object.fromEntries(product.fields.map((field) => [
    field.key,
    field.default ?? defaultValueForField(field.field_type),
  ]));
  const missingRequired = requiredMissingLabels(product, fields);
  const quoteField = defaultSolverQuoteField(product);
  const status = missingRequired.length ? 'missing_terms' : product.initial_solver_state;
  const row: TrySolveRowOut = {
    row_id: nextManualRowId(rows),
    source: 'manual',
    source_sheet: null,
    source_row: null,
    product_key: product.product_key,
    product_label: product.label,
    status,
    diagnostics: missingRequired.length ? [requiredTermsDiagnostic(missingRequired)] : [],
    quantark_product_type: product.quantark_product_type ?? null,
    engine_name: product.default_engine_name ?? null,
    fields,
    raw_values: {},
    market: {
      valuation_date: todayDate(),
      spot: null,
      volatility: null,
      rate: null,
      dividend_yield: null,
    },
    quote_request: {
      quote_field_key: quoteField?.key ?? 'premium_rate',
      target_label: 'price',
      target_value: 0,
      quote_value_mode: 'absolute',
      lower_bound: quoteField?.lower_bound ?? null,
      upper_bound: quoteField?.upper_bound ?? null,
      initial_guess: midpoint(quoteField?.lower_bound, quoteField?.upper_bound) ?? quoteField?.initial_guess ?? null,
    },
  };
  const rowWithSpotDefaults = withLatestSpotDefaults(row, product, marketDataProfiles, fields.underlying, {
    forceInitialPrice: false,
    forceMarketSpot: false,
  });
  return refreshAutoQuoteRange(row, rowWithSpotDefaults, product);
}

function normalizeRowsForSolve(
  rows: TrySolveRowOut[],
  catalog: TrySolveCatalog | null,
  marketDataProfiles: MarketDataProfile[] = [],
): TrySolveRowOut[] {
  let changed = false;
  const nextRows = rows.map((row) => {
    const product = findProduct(row.product_key, catalog);
    const rowWithRequiredTerms = refreshRequiredTerms(row, product);
    const nextRow = withLatestSpotDefaults(
      rowWithRequiredTerms,
      product,
      marketDataProfiles,
      rowWithRequiredTerms.fields.underlying,
      { forceInitialPrice: false, forceMarketSpot: false },
    );
    const nextRowWithQuoteRange = refreshAutoQuoteRange(rowWithRequiredTerms, nextRow, product);
    if (nextRowWithQuoteRange !== row) changed = true;
    return nextRowWithQuoteRange;
  });
  return changed ? nextRows : rows;
}

function refreshEditedRow(row: TrySolveRowOut, product: TrySolveProduct | null): TrySolveRowOut {
  return refreshRequiredTerms({
    ...row,
    solved_value: null,
    model_price: null,
    residual: null,
    executable_terms: null,
  }, product, { resetSolved: true });
}

function refreshRequiredTerms(
  row: TrySolveRowOut,
  product: TrySolveProduct | null,
  options: { resetSolved?: boolean } = {},
): TrySolveRowOut {
  if (!product) return row;
  const diagnostics = (row.diagnostics ?? []).filter((item) => !isRequiredTermsDiagnostic(item));
  const missingRequired = requiredMissingLabels(product, row.fields);
  if (missingRequired.length) {
    return updateRowStatusAndDiagnostics(row, 'missing_terms', [...diagnostics, requiredTermsDiagnostic(missingRequired)]);
  }
  const rowWithReadyQuote = ensureSolverReadyQuote(row, product);
  const status = rowWithReadyQuote.status === 'missing_terms' || (options.resetSolved && rowWithReadyQuote.status === 'solved')
    ? product.initial_solver_state
    : rowWithReadyQuote.status;
  return updateRowStatusAndDiagnostics(rowWithReadyQuote, status, diagnostics);
}

function findProduct(productKey: string, catalog: TrySolveCatalog | null): TrySolveProduct | null {
  return (catalog?.products ?? DEFAULT_TRY_SOLVE_CATALOG.products).find((product) => product.product_key === productKey) ?? null;
}

function withLatestSpotDefaults(
  row: TrySolveRowOut,
  product: TrySolveProduct | null,
  marketDataProfiles: MarketDataProfile[],
  underlying: unknown,
  options: { forceInitialPrice: boolean; forceMarketSpot: boolean },
): TrySolveRowOut {
  if (!product) return row;
  const spot = latestSpotForUnderlying(marketDataProfiles, String(underlying ?? ''));
  if (spot == null) return row;
  const shouldPatchInitialPrice = product.fields.some((field) => field.key === 'initial_price')
    && (options.forceInitialPrice || shouldReplaceSpotDefault(row.fields.initial_price));
  const shouldPatchMarketSpot = options.forceMarketSpot || shouldReplaceSpotDefault(row.market.spot);
  const nextFields = shouldPatchInitialPrice
    ? { ...row.fields, initial_price: spot }
    : row.fields;
  const nextMarket = shouldPatchMarketSpot
    ? { ...row.market, spot }
    : row.market;
  if (nextFields === row.fields && nextMarket === row.market) return row;
  return { ...row, fields: nextFields, market: nextMarket };
}

function shouldReplaceSpotDefault(value: unknown): boolean {
  if (value == null || value === '') return true;
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue === 1;
}

function latestSpotForUnderlying(profiles: MarketDataProfile[], underlying: string): number | null {
  const candidates = normalizedSymbolCandidates(underlying);
  for (const profile of profiles) {
    if (!candidates.has(normalizeSymbol(profile.symbol))) continue;
    const spot = spotFromProfile(profile);
    if (spot != null) return spot;
  }
  return null;
}

function normalizedSymbolCandidates(symbol: string): Set<string> {
  const normalized = normalizeSymbol(symbol);
  const withoutExchange = normalized.replace(/\.(SH|SZ)$/u, '');
  return new Set([normalized, withoutExchange]);
}

function normalizeSymbol(symbol: string): string {
  return symbol.trim().toUpperCase();
}

function spotFromProfile(profile: MarketDataProfile): number | null {
  const data = profile.data ?? {};
  const raw = data.spot ?? data.latest?.close;
  const spot = Number(raw);
  return Number.isFinite(spot) ? spot : null;
}

function ensureSolverReadyQuote(row: TrySolveRowOut, product: TrySolveProduct): TrySolveRowOut {
  const currentQuote = product.quote_fields.find((quote) => quote.key === row.quote_request.quote_field_key);
  if (currentQuote?.solver_ready) return row;
  const readyQuote = defaultSolverQuoteField(product);
  if (!readyQuote?.solver_ready) return row;
  const rangeDefaults = quoteRangeDefaults(row, readyQuote);
  return {
    ...row,
    quote_request: {
      ...row.quote_request,
      quote_field_key: readyQuote.key,
      lower_bound: rangeDefaults?.lower_bound ?? readyQuote.lower_bound,
      upper_bound: rangeDefaults?.upper_bound ?? readyQuote.upper_bound,
      initial_guess: rangeDefaults?.initial_guess ?? readyQuote.initial_guess,
    },
  };
}

function defaultSolverQuoteField(product: TrySolveProduct) {
  return product.quote_fields.find((quote) => quote.solver_ready) ?? product.quote_fields[0];
}

function autoInitialGuessQuoteRequest(
  currentQuoteRequest: TrySolveQuoteRequest,
  patch: Partial<TrySolveQuoteRequest>,
  isManualInitialGuess: boolean,
  forceAutoInitialGuess: boolean,
): TrySolveQuoteRequest {
  const nextQuoteRequest = normalizeQuoteBounds({ ...currentQuoteRequest, ...patch }, patch);
  const boundsChanged = hasOwn(patch, 'lower_bound') || hasOwn(patch, 'upper_bound') || hasOwn(patch, 'quote_field_key');
  if (hasOwn(patch, 'initial_guess')) return clampInitialGuessQuoteRequest(nextQuoteRequest);
  if (!boundsChanged || (isManualInitialGuess && !forceAutoInitialGuess)) return clampInitialGuessQuoteRequest(nextQuoteRequest);
  return clampInitialGuessQuoteRequest({
    ...nextQuoteRequest,
    initial_guess: midpoint(nextQuoteRequest.lower_bound, nextQuoteRequest.upper_bound),
  });
}

function midpoint(lower: unknown, upper: unknown): number | null {
  return typeof lower === 'number' && Number.isFinite(lower)
    && typeof upper === 'number' && Number.isFinite(upper)
    ? (lower + upper) / 2
    : null;
}

function normalizeQuoteBounds(
  quoteRequest: TrySolveQuoteRequest,
  patch: Partial<TrySolveQuoteRequest>,
): TrySolveQuoteRequest {
  const lowerBound = quoteRequest.lower_bound;
  const upperBound = quoteRequest.upper_bound;
  if (
    typeof lowerBound !== 'number'
    || !Number.isFinite(lowerBound)
    || typeof upperBound !== 'number'
    || !Number.isFinite(upperBound)
    || lowerBound <= upperBound
  ) {
    return quoteRequest;
  }
  if (hasOwn(patch, 'upper_bound') && !hasOwn(patch, 'lower_bound')) {
    return { ...quoteRequest, lower_bound: upperBound };
  }
  if (hasOwn(patch, 'lower_bound') && !hasOwn(patch, 'upper_bound')) {
    return { ...quoteRequest, upper_bound: lowerBound };
  }
  return { ...quoteRequest, lower_bound: upperBound, upper_bound: lowerBound };
}

function clampInitialGuessQuoteRequest(quoteRequest: TrySolveQuoteRequest): TrySolveQuoteRequest {
  const initialGuess = quoteRequest.initial_guess;
  const lowerBound = quoteRequest.lower_bound;
  const upperBound = quoteRequest.upper_bound;
  if (
    typeof initialGuess !== 'number'
    || !Number.isFinite(initialGuess)
    || typeof lowerBound !== 'number'
    || !Number.isFinite(lowerBound)
    || typeof upperBound !== 'number'
    || !Number.isFinite(upperBound)
  ) {
    return quoteRequest;
  }
  const lower = Math.min(lowerBound, upperBound);
  const upper = Math.max(lowerBound, upperBound);
  const clampedGuess = Math.min(upper, Math.max(lower, initialGuess));
  return clampedGuess === initialGuess
    ? quoteRequest
    : { ...quoteRequest, initial_guess: clampedGuess };
}

function refreshAutoQuoteRange(
  previousRow: TrySolveRowOut,
  nextRow: TrySolveRowOut,
  product: TrySolveProduct | null,
): TrySolveRowOut {
  if (!product) return nextRow;
  const quoteField = product.quote_fields.find((quote) => quote.key === nextRow.quote_request.quote_field_key);
  if (!quoteField) return nextRow;
  const nextDefaults = quoteRangeDefaults(nextRow, quoteField);
  if (!nextDefaults) return nextRow;
  const previousQuoteField = product.quote_fields.find((quote) => quote.key === previousRow.quote_request.quote_field_key);
  const previousDefaults = previousQuoteField ? quoteRangeDefaults(previousRow, previousQuoteField) : null;
  if (!shouldReplaceQuoteRange(previousRow.quote_request, quoteField, previousDefaults)) return nextRow;
  if (
    nextRow.quote_request.lower_bound === nextDefaults.lower_bound
    && nextRow.quote_request.upper_bound === nextDefaults.upper_bound
    && nextRow.quote_request.initial_guess === nextDefaults.initial_guess
  ) {
    return nextRow;
  }
  return {
    ...nextRow,
    quote_request: {
      ...nextRow.quote_request,
      ...nextDefaults,
    },
  };
}

function shouldReplaceQuoteRange(
  quoteRequest: TrySolveQuoteRequest,
  quoteField: TrySolveQuoteField,
  previousDefaults: Pick<TrySolveQuoteRequest, 'lower_bound' | 'upper_bound' | 'initial_guess'> | null,
): boolean {
  if (quoteRequest.lower_bound == null || quoteRequest.upper_bound == null) return true;
  const catalogDefaults = {
    lower_bound: quoteField.lower_bound,
    upper_bound: quoteField.upper_bound,
    initial_guess: quoteField.initial_guess ?? null,
  };
  return quoteRangeMatches(quoteRequest, catalogDefaults)
    || (previousDefaults != null && quoteRangeMatches(quoteRequest, previousDefaults));
}

function quoteRangeMatches(
  quoteRequest: TrySolveQuoteRequest,
  defaults: Pick<TrySolveQuoteRequest, 'lower_bound' | 'upper_bound' | 'initial_guess'>,
): boolean {
  const defaultMidpoint = midpoint(defaults.lower_bound, defaults.upper_bound);
  return quoteRequest.lower_bound === defaults.lower_bound
    && quoteRequest.upper_bound === defaults.upper_bound
    && (
      quoteRequest.initial_guess == null
      || defaults.initial_guess == null
      || quoteRequest.initial_guess === defaults.initial_guess
      || quoteRequest.initial_guess === defaultMidpoint
    );
}

function quoteRangeDefaults(
  row: TrySolveRowOut,
  quoteField: TrySolveQuoteField,
): Pick<TrySolveQuoteRequest, 'lower_bound' | 'upper_bound' | 'initial_guess'> | null {
  if (isReferencePriceQuoteField(quoteField)) {
    const referencePrice = quoteReferencePrice(row);
    if (referencePrice != null) {
      return {
        lower_bound: cleanRangeNumber(referencePrice * 0.1),
        upper_bound: cleanRangeNumber(referencePrice * 2),
        initial_guess: cleanRangeNumber(referencePrice),
      };
    }
  }
  if (isCouponRateQuoteField(quoteField)) {
    return {
      lower_bound: 0.001,
      upper_bound: 0.5,
      initial_guess: 0.1,
    };
  }
  return {
    lower_bound: quoteField.lower_bound,
    upper_bound: quoteField.upper_bound,
    initial_guess: quoteField.initial_guess ?? null,
  };
}

function isReferencePriceQuoteField(quoteField: TrySolveQuoteField): boolean {
  const key = quoteField.key.toLowerCase();
  const path = quoteField.canonical_path.toLowerCase();
  return key === 'strike' || path === 'strike';
}

function isCouponRateQuoteField(quoteField: TrySolveQuoteField): boolean {
  const key = quoteField.key.toLowerCase();
  const path = quoteField.canonical_path.toLowerCase();
  return key === 'annualized_coupon'
    || key === 'coupon_yield'
    || key === 'range_accrual_rate'
    || path.endsWith('.ko_rate')
    || path.endsWith('.coupon_rate')
    || path === 'range_config.accrual_rate';
}

function quoteReferencePrice(row: TrySolveRowOut): number | null {
  const spot = finitePositiveNumber(row.market.spot);
  if (spot != null) return spot;
  const initialPrice = finitePositiveNumber(row.fields.initial_price);
  if (initialPrice != null) return initialPrice;
  return null;
}

function finitePositiveNumber(value: unknown): number | null {
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function cleanRangeNumber(value: number): number {
  return Number(value.toPrecision(12));
}

function updateRowStatusAndDiagnostics(row: TrySolveRowOut, status: string, diagnostics: string[]): TrySolveRowOut {
  if (row.status === status && stringArraysEqual(row.diagnostics ?? [], diagnostics)) return row;
  return { ...row, status, diagnostics };
}

function stringArraysEqual(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function requiredMissingLabels(product: TrySolveProduct, fields: TrySolveRowOut['fields']): string[] {
  return product.fields
    .filter((field) => field.required)
    .filter((field) => isBlank(fields[field.key]))
    .map((field) => field.label);
}

function requiredTermsDiagnostic(missingRequired: string[]): string {
  return `Missing required terms: ${missingRequired.join(', ')}.`;
}

function isRequiredTermsDiagnostic(item: string): boolean {
  return item.startsWith('Missing required terms:');
}

function nextManualRowId(rows: TrySolveRowOut[]): string {
  const maxId = rows.reduce((max, row) => {
    const match = /^MAN-(\d+)$/.exec(row.row_id);
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);
  return `MAN-${maxId + 1}`;
}

function defaultValueForField(fieldType: TrySolveProduct['fields'][number]['field_type']): unknown {
  if (fieldType === 'number') return null;
  if (fieldType === 'boolean') return false;
  return '';
}

function isBlank(value: unknown): boolean {
  return value == null || String(value).trim() === '';
}

function hasOwn<T extends object>(value: T, key: PropertyKey): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function todayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function shouldReplaceRows(rows: TrySolveRowOut[]): boolean {
  if (rows.length === 0) return true;
  return rows.length === DEFAULT_TRY_SOLVE_ROWS.length
    && rows.every((row, index) => row.row_id === DEFAULT_TRY_SOLVE_ROWS[index]?.row_id);
}

function preserveSelection(
  currentSelected: string | null,
  rows: TrySolveRowOut[],
  fallback: string | null,
): string | null {
  if (currentSelected && rows.some((row) => row.row_id === currentSelected)) return currentSelected;
  if (fallback && rows.some((row) => row.row_id === fallback)) return fallback;
  return rows[0]?.row_id ?? null;
}

function replaceRow(rows: TrySolveRowOut[], nextRow: TrySolveRowOut): TrySolveRowOut[] {
  return rows.map((row) => (row.row_id === nextRow.row_id ? nextRow : row));
}

function toRowIn(row: TrySolveRowOut): TrySolveRowIn {
  return {
    row_id: row.row_id,
    source: row.source,
    product_key: row.product_key,
    source_sheet: row.source_sheet,
    source_row: row.source_row,
    fields: row.fields,
    raw_values: row.raw_values,
    market: row.market,
    quote_request: row.quote_request,
  };
}

function cloneRows(rows: TrySolveRowOut[]): TrySolveRowOut[] {
  return rows.map((row) => ({
    ...row,
    fields: { ...row.fields },
    raw_values: { ...row.raw_values },
    market: { ...row.market },
    quote_request: { ...row.quote_request },
    diagnostics: [...row.diagnostics],
    executable_terms: row.executable_terms ? { ...row.executable_terms } : row.executable_terms,
  }));
}

function importFeedback(batch: TrySolveBatchOut): string {
  const total = Number(batch.summary?.total_rows ?? batch.rows.length);
  const solved = Number(batch.summary?.solved ?? batch.rows.filter((row) => row.status === 'solved').length);
  return `Imported ${total} rows · ${solved} solved.`;
}

function solveFeedback(rows: TrySolveRowOut[], fallback: string): string {
  const solved = rows.filter((row) => row.status === 'solved').length;
  return solved > 0 ? `${fallback} ${solved} solved.` : fallback;
}

function formatStatusLabel(status: string): string {
  return status.replaceAll('_', ' ');
}

function formatApiError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(message) as { detail?: unknown };
    if (typeof parsed.detail === 'string') return parsed.detail;
  } catch {
    // Keep the original message when it is not a JSON error payload.
  }
  return message;
}

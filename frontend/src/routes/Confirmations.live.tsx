import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  bookExtractedTrade, errorMessage, getConfirmationBatch, listConfirmationBatches,
  listPortfoliosWithIds, rejectExtractedTrade, updateExtractedTrade, uploadConfirmations,
} from '../api/client';
import type { ConfirmationBatch, ExtractedTrade, PageContextReporter } from '../types';
import {
  Confirmations, IN_FLIGHT_DOC_STATUSES, tradeDraftDefaults, type TradeDraft,
} from './Confirmations';

type Props = { onPageContextChange?: PageContextReporter };

function findTradeInBatch(batch: ConfirmationBatch | null, tradeId: number): ExtractedTrade | null {
  if (!batch) return null;
  for (const document of batch.documents) {
    const match = document.trades.find((t) => t.id === tradeId);
    if (match) return match;
  }
  return null;
}

// Client-side gate for Save: the PUT endpoint silently drops any null-valued
// key (`if v is not None` server-side), so an emptied required field or a
// NaN-producing typo (Number('abc') -> NaN -> JSON.stringify -> null) would
// otherwise round-trip as a no-op while still showing "Trade saved." —
// required fields must error+block; optional numeric fields may be left
// blank (omitted from the PUT body, i.e. "unchanged") but if non-blank must
// parse to a finite number.
function validateDraftFields(draft: TradeDraft): string | null {
  if (draft.underlying.trim() === '') return 'Underlying is required.';
  if (draft.currency.trim() === '') return 'Currency is required.';
  if (draft.quantity.trim() !== '' && !Number.isFinite(Number(draft.quantity))) {
    return 'Quantity must be a number.';
  }
  if (draft.entry_price.trim() !== '' && !Number.isFinite(Number(draft.entry_price))) {
    return 'Entry price must be a number.';
  }
  return null;
}

export function ConfirmationsLive(_props: Props) {
  const [batches, setBatches] = useState<ConfirmationBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [portfolios, setPortfolios] = useState<Array<{ id: number; name: string }>>([]);
  const [portfoliosError, setPortfoliosError] = useState<string | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadPortfolioId, setUploadPortfolioId] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, TradeDraft>>({});
  const [tradePortfolio, setTradePortfolio] = useState<Record<number, number | null>>({});
  const [rejectReason, setRejectReason] = useState<Record<number, string>>({});
  const [rowBusy, setRowBusy] = useState<Set<number>>(new Set());

  // ONE stale-response guard shared by both loaders, bumped at dispatch, so
  // the newest dispatch always wins. This is safe only because every dispatch
  // happens strictly after whatever caused it has already committed
  // server-side (mutations await their PUT/POST before refreshing), so a later
  // request can never observe an older world than an earlier one.
  //
  // Two counters used to be required because `onRefresh` fired both loaders
  // together and a shared counter would permanently discard the list response.
  // That pairing is gone: the list endpoint returns documents *and* trades, so
  // loadBatches() is a strict superset of refreshBatch() and `onRefresh` just
  // calls the former.
  const batchSeqRef = useRef(0);

  const selectedBatch = useMemo(
    () => batches.find((b) => b.id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

  const loadBatches = useCallback(async () => {
    const token = ++batchSeqRef.current;
    try {
      const list = await listConfirmationBatches();
      if (token !== batchSeqRef.current) return;
      setBatches(list);
      setError(null);
    } catch (e) {
      if (token === batchSeqRef.current) setError(errorMessage(e));
    } finally {
      if (token === batchSeqRef.current) setLoading(false);
    }
  }, []);

  const refreshBatch = useCallback(async (id: number) => {
    const token = ++batchSeqRef.current;
    try {
      const updated = await getConfirmationBatch(id);
      if (token !== batchSeqRef.current) return;
      setBatches((prev) => prev.map((b) => (b.id === id ? updated : b)));
    } catch (e) {
      if (token === batchSeqRef.current) setFeedback(errorMessage(e));
    }
  }, []);

  useEffect(() => { void loadBatches(); }, [loadBatches]);

  useEffect(() => {
    listPortfoliosWithIds()
      .then(setPortfolios)
      .catch((e: unknown) => setPortfoliosError(errorMessage(e)));
  }, []);

  // Auto-select the most recent batch once the list has loaded.
  useEffect(() => {
    if (selectedBatchId == null && batches.length > 0) {
      setSelectedBatchId(batches[0].id);
    }
  }, [batches, selectedBatchId]);

  // Poll while ANY batch still has work in flight, not just the selected one:
  // upload a second batch, switch away from the first, and the first would
  // otherwise freeze mid-parse with a stale rail badge forever. Polling the
  // list (rather than one batch detail) refreshes every row at once, and
  // gating on a boolean means the interval is rebuilt only when that flips —
  // not on every tick's response.
  const anyParseInFlight = useMemo(
    () => batches.some((b) => b.documents.some((d) => IN_FLIGHT_DOC_STATUSES.has(d.status))),
    [batches],
  );

  useEffect(() => {
    if (!anyParseInFlight) return undefined;
    const timer = window.setInterval(() => { void loadBatches(); }, 2000);
    return () => window.clearInterval(timer);
  }, [anyParseInFlight, loadBatches]);

  const onFilesSelected = useCallback((files: FileList | null) => {
    setSelectedFiles(files ? Array.from(files) : []);
  }, []);

  const onUpload = useCallback(async () => {
    if (selectedFiles.length === 0) return;
    setUploading(true);
    try {
      const batch = await uploadConfirmations(selectedFiles, uploadPortfolioId);
      setBatches((prev) => [batch, ...prev]);
      setSelectedBatchId(batch.id);
      setSelectedFiles([]);
      setFeedback(`Uploaded batch #${batch.id} (${batch.documents.length} file(s)).`);
    } catch (e) {
      setFeedback(errorMessage(e));
    } finally {
      setUploading(false);
    }
  }, [selectedFiles, uploadPortfolioId]);

  const onDraftChange = useCallback((tradeId: number, next: TradeDraft) => {
    setDrafts((prev) => ({ ...prev, [tradeId]: next }));
  }, []);

  const onSaveTrade = useCallback(async (tradeId: number) => {
    const trade = findTradeInBatch(selectedBatch, tradeId);
    const draft = drafts[tradeId] ?? (trade ? tradeDraftDefaults(trade) : null);
    if (!draft) return;

    // Field validation runs BEFORE any API call: required fields must not be
    // empty and numeric fields, if non-blank, must parse — otherwise the PUT
    // silently no-ops the offending key server-side while still reporting
    // "Trade saved." (see the top-of-file comment on validateDraftFields).
    const fieldsError = validateDraftFields(draft);
    if (fieldsError) {
      setDrafts((prev) => ({ ...prev, [tradeId]: { ...draft, fieldsError } }));
      return;
    }

    let parsedTerms: Record<string, unknown>;
    try {
      parsedTerms = draft.termsText.trim() === '' ? {} : JSON.parse(draft.termsText);
    } catch (e) {
      setDrafts((prev) => ({
        ...prev,
        [tradeId]: {
          ...draft, fieldsError: null,
          termsError: `Invalid JSON: ${e instanceof Error ? e.message : String(e)}`,
        },
      }));
      return;
    }

    // Only present-and-valid keys go in the body — an untouched-empty
    // quantity/entry_price is honestly omitted (meaning "leave unchanged"),
    // never sent as an explicit null the server would drop anyway.
    const body: Partial<ExtractedTrade> = {
      underlying: draft.underlying.trim(),
      currency: draft.currency.trim(),
      terms: parsedTerms,
    };
    if (draft.quantity.trim() !== '') body.quantity = Number(draft.quantity);
    if (draft.entry_price.trim() !== '') body.entry_price = Number(draft.entry_price);

    setRowBusy((prev) => new Set(prev).add(tradeId));
    try {
      await updateExtractedTrade(tradeId, body);
      setDrafts((prev) => ({ ...prev, [tradeId]: { ...draft, termsError: null, fieldsError: null } }));
      setFeedback('Trade saved.');
      if (selectedBatchId != null) await refreshBatch(selectedBatchId);
    } catch (e) {
      setFeedback(errorMessage(e));
    } finally {
      setRowBusy((prev) => { const next = new Set(prev); next.delete(tradeId); return next; });
    }
  }, [drafts, selectedBatch, selectedBatchId, refreshBatch]);

  const onTradePortfolioChange = useCallback((tradeId: number, id: number | null) => {
    setTradePortfolio((prev) => ({ ...prev, [tradeId]: id }));
  }, []);

  const onRejectReasonChange = useCallback((tradeId: number, value: string) => {
    setRejectReason((prev) => ({ ...prev, [tradeId]: value }));
  }, []);

  const onBookTrade = useCallback(async (tradeId: number) => {
    const resolved = tradePortfolio[tradeId] ?? selectedBatch?.default_portfolio_id ?? null;
    setRowBusy((prev) => new Set(prev).add(tradeId));
    try {
      const result = await bookExtractedTrade(tradeId, resolved);
      setFeedback(
        result.ok
          ? `Booked as position #${result.position_id}.`
          : `Book failed: ${result.error ?? 'unknown error'}`,
      );
      if (selectedBatchId != null) await refreshBatch(selectedBatchId);
    } catch (e) {
      setFeedback(errorMessage(e));
    } finally {
      setRowBusy((prev) => { const next = new Set(prev); next.delete(tradeId); return next; });
    }
  }, [tradePortfolio, selectedBatch, selectedBatchId, refreshBatch]);

  const onRejectTrade = useCallback(async (tradeId: number) => {
    setRowBusy((prev) => new Set(prev).add(tradeId));
    try {
      await rejectExtractedTrade(tradeId, rejectReason[tradeId] || undefined);
      setFeedback('Trade rejected.');
      if (selectedBatchId != null) await refreshBatch(selectedBatchId);
    } catch (e) {
      setFeedback(errorMessage(e));
    } finally {
      setRowBusy((prev) => { const next = new Set(prev); next.delete(tradeId); return next; });
    }
  }, [rejectReason, selectedBatchId, refreshBatch]);

  return (
    <Confirmations
      batches={batches}
      selectedBatch={selectedBatch}
      loading={loading}
      error={error}
      feedback={feedback}
      portfolios={portfolios}
      portfoliosError={portfoliosError}
      selectedFileNames={selectedFiles.map((f) => f.name)}
      uploadPortfolioId={uploadPortfolioId}
      uploading={uploading}
      drafts={drafts}
      tradePortfolio={tradePortfolio}
      rejectReason={rejectReason}
      rowBusy={rowBusy}
      onFilesSelected={onFilesSelected}
      onUploadPortfolioChange={setUploadPortfolioId}
      onUpload={() => void onUpload()}
      onSelectBatch={setSelectedBatchId}
      onRefresh={() => void loadBatches()}
      onDraftChange={onDraftChange}
      onSaveTrade={(id) => void onSaveTrade(id)}
      onTradePortfolioChange={onTradePortfolioChange}
      onRejectReasonChange={onRejectReasonChange}
      onBookTrade={(id) => void onBookTrade(id)}
      onRejectTrade={(id) => void onRejectTrade(id)}
    />
  );
}

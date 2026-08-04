import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  bookExtractedTrade, errorMessage, getConfirmationBatch, listConfirmationBatches,
  listPortfoliosWithIds, rejectExtractedTrade, updateExtractedTrade, uploadConfirmations,
} from '../api/client';
import type { ConfirmationBatch, ExtractedTrade, PageContextReporter } from '../types';
import { Confirmations, tradeDraftDefaults, type TradeDraft } from './Confirmations';

type Props = { onPageContextChange?: PageContextReporter };

// A document is still being worked on server-side; keep polling the selected
// batch while any of its documents are in one of these states.
const NON_TERMINAL_DOC_STATUSES = new Set(['pending', 'parsing']);

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

  // Separate stale-response guards for the two independent async loaders that
  // both write into `batches` (mirrors AuditLive.tsx's loadRequestIdRef /
  // detailRequestIdRef split — a single shared counter would make every
  // paired loadBatches()+refreshBatch() dispatch (see onRefresh) permanently
  // discard the loadBatches() response, since the refreshBatch() dispatch
  // right after it would always bump a shared counter past loadBatches's
  // captured token). Scoping each loader to its own counter still fully
  // guards the concrete race this exists for: a straggling poll tick's
  // refreshBatch() response landing after a post-book/save/reject
  // refreshBatch() response and clobbering the fresher state.
  const loadSeqRef = useRef(0);
  const refreshSeqRef = useRef(0);

  const selectedBatch = useMemo(
    () => batches.find((b) => b.id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

  const loadBatches = useCallback(async () => {
    const token = ++loadSeqRef.current;
    try {
      const list = await listConfirmationBatches();
      if (token !== loadSeqRef.current) return;
      setBatches(list);
      setError(null);
    } catch (e) {
      if (token === loadSeqRef.current) setError(errorMessage(e));
    } finally {
      if (token === loadSeqRef.current) setLoading(false);
    }
  }, []);

  const refreshBatch = useCallback(async (id: number) => {
    const token = ++refreshSeqRef.current;
    try {
      const updated = await getConfirmationBatch(id);
      if (token !== refreshSeqRef.current) return;
      setBatches((prev) => prev.map((b) => (b.id === id ? updated : b)));
    } catch (e) {
      if (token === refreshSeqRef.current) setFeedback(errorMessage(e));
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

  // Poll the selected batch while any of its documents are still parsing —
  // mirrors the Arena runs-list poll (interval is re-derived from the latest
  // batches state, so it naturally stops once every document is terminal).
  useEffect(() => {
    if (!selectedBatch) return undefined;
    const pending = selectedBatch.documents.some((d) => NON_TERMINAL_DOC_STATUSES.has(d.status));
    if (!pending) return undefined;
    const timer = window.setInterval(() => {
      void refreshBatch(selectedBatch.id);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [selectedBatch, refreshBatch]);

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
      onRefresh={() => {
        void loadBatches();
        if (selectedBatchId != null) void refreshBatch(selectedBatchId);
      }}
      onDraftChange={onDraftChange}
      onSaveTrade={(id) => void onSaveTrade(id)}
      onTradePortfolioChange={onTradePortfolioChange}
      onRejectReasonChange={onRejectReasonChange}
      onBookTrade={(id) => void onBookTrade(id)}
      onRejectTrade={(id) => void onRejectTrade(id)}
    />
  );
}

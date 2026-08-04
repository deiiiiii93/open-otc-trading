import { useCallback, useEffect, useMemo, useState } from 'react';
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

  const selectedBatch = useMemo(
    () => batches.find((b) => b.id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

  const loadBatches = useCallback(async () => {
    try {
      const list = await listConfirmationBatches();
      setBatches(list);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshBatch = useCallback(async (id: number) => {
    try {
      const updated = await getConfirmationBatch(id);
      setBatches((prev) => prev.map((b) => (b.id === id ? updated : b)));
    } catch (e) {
      setFeedback(errorMessage(e));
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
    let parsedTerms: Record<string, unknown>;
    try {
      parsedTerms = draft.termsText.trim() === '' ? {} : JSON.parse(draft.termsText);
    } catch (e) {
      setDrafts((prev) => ({
        ...prev,
        [tradeId]: { ...draft, termsError: `Invalid JSON: ${e instanceof Error ? e.message : String(e)}` },
      }));
      return;
    }
    setRowBusy((prev) => new Set(prev).add(tradeId));
    try {
      await updateExtractedTrade(tradeId, {
        underlying: draft.underlying || null,
        quantity: draft.quantity.trim() === '' ? null : Number(draft.quantity),
        entry_price: draft.entry_price.trim() === '' ? null : Number(draft.entry_price),
        currency: draft.currency || null,
        terms: parsedTerms,
      });
      setDrafts((prev) => ({ ...prev, [tradeId]: { ...draft, termsError: null } }));
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

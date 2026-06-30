import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listMemoryFacts, getMemoryStatus, listPortfoliosWithIds,
  approveMemoryFact, setMemoryFactPinned, createMemoryFact, patchMemoryFact, deleteMemoryFact,
  errorMessage,
} from '../api/client';
import type { MemoryFact, MemoryStatus, PageContextReporter } from '../types';
import {
  Memory, type MemoryScope, type MemoryStatusFilter, type MemoryModal, type MemoryDraft,
} from './Memory';

const DEFAULT_FLOOR = 0.7;
const PAGE = 100;

type Props = { onPageContextChange?: PageContextReporter };

export function MemoryLive(_props: Props) {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [activeScope, setActiveScope] = useState<MemoryScope>('all');
  const [statusFilter, setStatusFilter] = useState<MemoryStatusFilter>('current');
  const [search, setSearch] = useState('');
  const [portfolios, setPortfolios] = useState<Array<{ id: number; name: string }>>([]);
  const [portfoliosError, setPortfoliosError] = useState<string | null>(null);
  const [selectedPortfolio, setSelectedPortfolio] = useState<number | null>(null);
  const [rowBusy, setRowBusy] = useState<Set<number>>(new Set());
  const [modal, setModal] = useState<MemoryModal>(null);
  const [modalSaving, setModalSaving] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const reqSeq = useRef(0);
  const nextOffset = useRef(0);

  const confidenceFloor = status?.config.confidence_floor ?? DEFAULT_FLOOR;

  const loadView = useCallback(
    async (reset: boolean) => {
      const token = ++reqSeq.current;
      if (reset) setLoading(true);
      const scope_type = activeScope === 'all' ? undefined : activeScope;
      const statusParam = statusFilter === 'current' ? undefined : statusFilter;
      const scope_id =
        activeScope === 'book' && selectedPortfolio != null ? String(selectedPortfolio) : undefined;
      const offset = reset ? 0 : nextOffset.current;

      const [factsR, statusR] = await Promise.allSettled([
        listMemoryFacts({ scope_type, status: statusParam, scope_id, limit: PAGE, offset }),
        reset ? getMemoryStatus() : Promise.resolve(undefined),
      ]);
      if (token !== reqSeq.current) return;

      if (factsR.status === 'fulfilled') {
        const { items, total: t } = factsR.value;
        if (reset) {
          setFacts(items);
          nextOffset.current = items.length;
        } else {
          setFacts((prev) => [...prev, ...items]);
          nextOffset.current += items.length;
        }
        setTotal(t);
        setError(null);
        setFeedback(null);
      } else if (reset) {
        setError(errorMessage(factsR.reason));
      } else {
        setFeedback(errorMessage(factsR.reason));
      }

      if (reset && statusR.status === 'fulfilled' && statusR.value) {
        setStatus(statusR.value);
      }
      if (reset) setLoading(false);
    },
    [activeScope, statusFilter, selectedPortfolio],
  );

  useEffect(() => { void loadView(true); }, [loadView]);
  useEffect(() => {
    listPortfoliosWithIds()
      .then(setPortfolios)
      .catch((e: unknown) => setPortfoliosError(errorMessage(e)));
  }, []);

  const withRow = useCallback(
    async (id: number, fn: () => Promise<unknown>) => {
      setRowBusy((prev) => new Set(prev).add(id));
      try {
        await fn();
        await loadView(true);
      } catch (e) {
        setFeedback(errorMessage(e));
      } finally {
        setRowBusy((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    },
    [loadView],
  );

  const onApprove = (f: MemoryFact) => void withRow(f.id, () => approveMemoryFact(f.id));
  const onPin = (f: MemoryFact) => void withRow(f.id, () => setMemoryFactPinned(f.id, !f.pinned));
  const onDelete = (f: MemoryFact) => setModal({ kind: 'delete', fact: f });
  const onConfirmDelete = () => {
    if (!modal || modal.kind !== 'delete') return;
    const f = modal.fact;
    void withRow(f.id, () => deleteMemoryFact(f.id)).then(() => setModal(null));
  };

  const onNew = () =>
    setModal({ kind: 'create', draft: { scope_type: 'user', portfolioId: null, content: '', confidence: 1, category: '' } });
  const onEdit = (f: MemoryFact) =>
    setModal({
      kind: 'edit',
      fact: f,
      draft: { scope_type: f.scope_type, portfolioId: null, content: f.content, confidence: f.confidence, category: f.category ?? '' },
    });
  const onModalChange = (draft: MemoryDraft) =>
    setModal((m) => (m && m.kind !== 'delete' ? { ...m, draft } : m));

  const onModalSave = async () => {
    if (!modal || modal.kind === 'delete') return;
    const d = modal.draft;
    setModalSaving(true);
    setModalError(null);
    try {
      if (modal.kind === 'create') {
        await createMemoryFact({
          scope_type: d.scope_type,
          scope_id: d.scope_type === 'book' && d.portfolioId != null ? String(d.portfolioId) : undefined,
          content: d.content,
          confidence: d.confidence,
          category: d.category === '' ? undefined : d.category,
        });
      } else {
        await patchMemoryFact(modal.fact.id, { content: d.content, confidence: d.confidence, category: d.category });
      }
      setModal(null);
      await loadView(true);
    } catch (e) {
      setModalError(errorMessage(e));
    } finally {
      setModalSaving(false);
    }
  };

  return (
    <Memory
      facts={facts}
      total={total}
      status={status}
      loading={loading}
      error={error}
      feedback={feedback}
      activeScope={activeScope}
      statusFilter={statusFilter}
      search={search}
      portfolios={portfolios}
      portfoliosError={portfoliosError}
      selectedPortfolio={selectedPortfolio}
      rowBusy={rowBusy}
      confidenceFloor={confidenceFloor}
      modal={modal}
      modalSaving={modalSaving}
      modalError={modalError}
      onScope={setActiveScope}
      onStatusFilter={setStatusFilter}
      onSearch={setSearch}
      onSelectPortfolio={setSelectedPortfolio}
      onRefresh={() => void loadView(true)}
      onLoadMore={() => void loadView(false)}
      onApprove={onApprove}
      onPin={onPin}
      onEdit={onEdit}
      onDelete={onDelete}
      onNew={onNew}
      onModalChange={onModalChange}
      onModalSave={() => void onModalSave()}
      onModalCancel={() => { setModal(null); setModalError(null); }}
      onConfirmDelete={onConfirmDelete}
    />
  );
}

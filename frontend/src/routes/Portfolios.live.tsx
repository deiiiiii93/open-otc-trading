import { useEffect, useReducer, useRef, useState } from 'react';
import { api } from '../api/client';
import { PortfolioCreateDialog } from '../components/PortfolioCreateDialog';
import { PortfolioDeleteDialog } from '../components/PortfolioDeleteDialog';
import type {
  FilterRule,
  PageContextReporter,
  PortfolioDetail,
  PortfolioKind,
  PortfolioMembership,
  PortfolioSummary,
} from '../types';
import { Portfolios, type SaveState } from './Portfolios';

type PreviewRow = {
  id: number;
  source_trade_id?: string | null;
  underlying: string;
  product_type: string;
  quantity: number;
  entry_price: number;
  status: string;
};

type SaveAction =
  | { type: 'edit' }
  | { type: 'start' }
  | { type: 'success' }
  | { type: 'fail'; message: string }
  | { type: 'reset' };

function saveReducer(_state: SaveState, action: SaveAction): SaveState {
  switch (action.type) {
    case 'edit': return { kind: 'editing' };
    case 'start': return { kind: 'saving' };
    case 'success': return { kind: 'saved', at: Date.now() };
    case 'fail': return { kind: 'error', message: action.message };
    case 'reset': return { kind: 'idle' };
  }
}

type Props = {
  onPageContextChange?: PageContextReporter;
};

export function PortfoliosLive({ onPageContextChange }: Props) {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [selected, setSelected] = useState<PortfolioDetail | null>(null);
  const [allPositions, setAllPositions] = useState<PreviewRow[]>([]);
  const [pendingPreview, setPendingPreview] = useState<PreviewRow[] | null>(null);
  const [saveState, dispatchSave] = useReducer(saveReducer, { kind: 'idle' });
  const [createKind, setCreateKind] = useState<PortfolioKind | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const hasAutoSelected = useRef(false);

  const refreshList = async () => {
    const rows = await api<PortfolioSummary[]>('/api/portfolios');
    setPortfolios(rows);
  };

  const refreshSelected = async (id: number) => {
    const detail = await api<PortfolioDetail>(`/api/portfolios/${id}`);
    setSelected(detail);
  };

  useEffect(() => { refreshList(); }, []);

  useEffect(() => {
    if (!hasAutoSelected.current && portfolios.length > 0) {
      hasAutoSelected.current = true;
      const latest = portfolios.reduce((a, b) => (a.id > b.id ? a : b));
      refreshSelected(latest.id);
    }
  }, [portfolios]);

  useEffect(() => {
    (async () => {
      const rows = await api<PortfolioSummary[]>('/api/portfolios');
      const positionsById = new Map<number, PreviewRow>();
      for (const row of rows) {
        const detail = await api<PortfolioDetail>(`/api/portfolios/${row.id}`);
        if (detail.kind !== 'container') continue;
        for (const position of detail.positions || []) {
          if (!positionsById.has(position.id)) {
            positionsById.set(position.id, position as PreviewRow);
          }
        }
      }
      setAllPositions(Array.from(positionsById.values()));
    })();
  }, []);

  useEffect(() => {
    if (!selected || selected.kind !== 'view') {
      setPendingPreview(null);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const body = await api<PortfolioMembership>(`/api/portfolios/${selected.id}/membership`);
        const idSet = new Set(body.position_ids);
        setPendingPreview(allPositions.filter((position) => idSet.has(position.id)));
      } catch {
        setPendingPreview(null);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [
    selected?.id,
    JSON.stringify(selected?.filter_rule),
    selected?.manual_include_ids?.join(','),
    selected?.manual_exclude_ids?.join(','),
    selected?.source_portfolio_ids?.join(','),
    allPositions,
  ]);

  const withSave = async (fn: () => Promise<void>) => {
    dispatchSave({ type: 'start' });
    try {
      await fn();
      dispatchSave({ type: 'success' });
    } catch (err) {
      dispatchSave({ type: 'fail', message: err instanceof Error ? err.message : 'unknown error' });
    }
  };

  const onSaveRule = (rule: FilterRule | null) =>
    selected
      ? withSave(async () => {
          await api(`/api/portfolios/${selected.id}/rule`, {
            method: 'PUT',
            body: JSON.stringify({ filter_rule: rule }),
          });
          await refreshSelected(selected.id);
        })
      : Promise.resolve();

  const idsAction = (
    pathSuffix: string,
    method: 'POST' | 'DELETE',
    key: 'position_ids' | 'portfolio_ids',
  ) =>
    (ids: number | number[]) =>
      selected
        ? withSave(async () => {
            const payload = Array.isArray(ids) ? ids : [ids];
            await api(`/api/portfolios/${selected.id}/${pathSuffix}`, {
              method,
              body: JSON.stringify({ [key]: payload }),
            });
            await Promise.all([refreshSelected(selected.id), refreshList()]);
          })
        : Promise.resolve();

  const onSetTags = (tags: string[]) =>
    selected
      ? withSave(async () => {
          await api(`/api/portfolios/${selected.id}/tags`, {
            method: 'PUT',
            body: JSON.stringify({ tags }),
          });
          await refreshSelected(selected.id);
        })
      : Promise.resolve();

  const onCreate = async (input: { name: string; kind: PortfolioKind }) => {
    await api('/api/portfolios', { method: 'POST', body: JSON.stringify(input) });
    setCreateKind(null);
    await refreshList();
  };

  const onDelete = async () => {
    if (!selected) return;
    await fetch(`/api/portfolios/${selected.id}`, { method: 'DELETE' });
    setDeleteOpen(false);
    setSelected(null);
    await refreshList();
  };

  return (
    <>
      <Portfolios
        portfolios={portfolios}
        allPortfolios={portfolios}
        allPositions={allPositions}
        selected={selected}
        selectedPortfolioId={selected?.id ?? null}
        pendingMembershipPreview={pendingPreview}
        saveState={saveState}
        onSelectPortfolio={refreshSelected}
        onOpenCreate={(kind) => setCreateKind(kind)}
        onOpenDelete={() => setDeleteOpen(true)}
        onSaveRule={onSaveRule}
        onAddInclude={(ids) => idsAction('includes', 'POST', 'position_ids')(ids)}
        onRemoveInclude={(id) => idsAction('includes', 'DELETE', 'position_ids')(id)}
        onAddExclude={(ids) => idsAction('excludes', 'POST', 'position_ids')(ids)}
        onRemoveExclude={(id) => idsAction('excludes', 'DELETE', 'position_ids')(id)}
        onAddSource={(ids) => idsAction('sources', 'POST', 'portfolio_ids')(ids)}
        onRemoveSource={(id) => idsAction('sources', 'DELETE', 'portfolio_ids')(id)}
        onSetTags={onSetTags}
        onRunPricing={() =>
          selected &&
          fetch(`/api/portfolios/${selected.id}/positions/price`, {
            method: 'POST',
            body: JSON.stringify({}),
            headers: { 'Content-Type': 'application/json' },
          })
        }
        onRunRisk={() => {}}
        activeDialog={
          createKind
            ? { kind: 'create', portfolioKind: createKind }
            : deleteOpen
              ? { kind: 'delete' }
              : null
        }
        onPageContextChange={onPageContextChange}
      />
      <PortfolioCreateDialog
        open={createKind !== null}
        kind={createKind ?? 'view'}
        onCancel={() => setCreateKind(null)}
        onCreate={onCreate}
      />
      <PortfolioDeleteDialog
        open={deleteOpen}
        portfolioName={selected?.name ?? ''}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={onDelete}
      />
    </>
  );
}

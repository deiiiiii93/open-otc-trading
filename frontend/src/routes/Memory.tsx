import type { MemoryFact, MemoryStatus } from '../types';
import { PageScaffold } from '../components/templates/PageScaffold';
import { PageToolbar, PageToolbarSpacer, PageToolbarSearch } from '../components/PageToolbar';
import { Select } from '../components/Select';
import { Tabs, TabsList, TabsTrigger } from '../components/Tabs';
import { Table, type Column } from '../components/Table';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Button } from '../components/Button';
import { Modal } from '../components/Modal';
import { Empty } from '../components/Empty';
import './Memory.css';

export type MemoryScope = 'all' | 'user' | 'book' | 'domain' | 'correction';
export type MemoryStatusFilter = 'current' | 'proposed' | 'approved' | 'active' | 'archived' | 'all';

export interface MemoryDraft {
  scope_type: 'user' | 'book' | 'domain' | 'correction';
  portfolioId: number | null;
  content: string;
  confidence: number;
  category: string; // '' means "no category" (cleared)
}

export type MemoryModal =
  | { kind: 'create'; draft: MemoryDraft }
  | { kind: 'edit'; fact: MemoryFact; draft: MemoryDraft }
  | { kind: 'delete'; fact: MemoryFact }
  | null;

export interface MemoryProps {
  facts: MemoryFact[];
  total: number;
  status: MemoryStatus | null;
  loading: boolean;
  error: string | null;
  feedback: string | null;
  activeScope: MemoryScope;
  statusFilter: MemoryStatusFilter;
  search: string;
  portfolios: Array<{ id: number; name: string }>;
  portfoliosError: string | null;
  selectedPortfolio: number | null;
  rowBusy: Set<number>;
  confidenceFloor: number;
  modal: MemoryModal;
  modalSaving: boolean;
  modalError: string | null;
  onScope: (s: MemoryScope) => void;
  onStatusFilter: (s: MemoryStatusFilter) => void;
  onSearch: (q: string) => void;
  onSelectPortfolio: (id: number | null) => void;
  onRefresh: () => void;
  onLoadMore: () => void;
  onApprove: (f: MemoryFact) => void;
  onPin: (f: MemoryFact) => void;
  onEdit: (f: MemoryFact) => void;
  onDelete: (f: MemoryFact) => void;
  onNew: () => void;
  onModalChange: (draft: MemoryDraft) => void;
  onModalSave: () => void;
  onModalCancel: () => void;
  onConfirmDelete: () => void;
}

const SCOPES: MemoryScope[] = ['user', 'book', 'domain', 'correction', 'all'];
const STATUS_OPTIONS: MemoryStatusFilter[] = ['current', 'proposed', 'approved', 'active', 'archived', 'all'];
const STATUS_LABEL: Record<MemoryStatusFilter, string> = {
  current: 'Current', proposed: 'Proposed', approved: 'Approved',
  active: 'Active', archived: 'Archived', all: 'All',
};

function statusBadge(status: MemoryFact['status']): BadgeVariant {
  if (status === 'proposed') return 'warn';
  if (status === 'archived') return 'ink';
  return 'pos'; // active / approved
}

function sourceBadge(createdBy: string): { variant: BadgeVariant; label: string } {
  const v = (createdBy || '').trim();
  if (v === 'extractor') return { variant: 'info', label: 'extractor' };
  if (v === 'api') return { variant: 'ink', label: 'api' };
  return { variant: 'ink', label: v || 'unknown' };
}

function scopeNonArchived(counts: Record<string, number> | undefined): number {
  if (!counts) return 0;
  return Object.entries(counts)
    .filter(([k]) => k !== 'archived')
    .reduce((a, [, v]) => a + v, 0);
}

export function Memory(props: MemoryProps) {
  const {
    facts, total, status, loading, error, feedback, activeScope, statusFilter, search,
    portfolios, portfoliosError, selectedPortfolio, rowBusy, confidenceFloor,
    modal, modalSaving, modalError,
    onScope, onStatusFilter, onSearch, onSelectPortfolio, onRefresh, onLoadMore,
    onApprove, onPin, onEdit, onDelete, onNew, onModalChange, onModalSave, onModalCancel, onConfirmDelete,
  } = props;

  const chips: string[] = [];
  if (status) {
    chips.push(status.enabled ? 'enabled' : 'disabled');
    for (const s of ['user', 'book', 'domain', 'correction'] as const) {
      chips.push(`${s} ${scopeNonArchived(status.counts[s])}`);
    }
    const proposed = Object.values(status.counts).reduce((a, c) => a + (c.proposed || 0), 0);
    if (proposed > 0) chips.push(`proposed ${proposed}`);
  }

  const term = search.trim().toLowerCase();
  const visible = term
    ? facts.filter(
        (f) => f.content.toLowerCase().includes(term) || (f.category ?? '').toLowerCase().includes(term),
      )
    : facts;

  const portfolioName = (scopeId: string) =>
    portfolios.find((p) => String(p.id) === scopeId)?.name ?? `Book #${scopeId}`;

  const columns: Column<MemoryFact>[] = [];
  if (activeScope === 'all') {
    columns.push({
      key: 'scope',
      header: 'Scope',
      render: (f) => (f.scope_type === 'book' ? portfolioName(f.scope_id) : f.scope_type),
    });
  }
  columns.push(
    { key: 'status', header: 'Status', render: (f) => <Badge variant={statusBadge(f.status)}>{f.status}</Badge> },
    { key: 'content', header: 'Content', width: '2fr', render: (f) => f.content },
    { key: 'confidence', header: 'Conf', numeric: true, render: (f) => f.confidence.toFixed(2) },
    { key: 'category', header: 'Category', render: (f) => f.category ?? '—' },
    {
      key: 'source',
      header: 'Source',
      render: (f) => {
        const b = sourceBadge(f.created_by);
        const detail = [f.extractor_model, f.source_session_id != null ? `session #${f.source_session_id}` : null]
          .filter(Boolean)
          .join(' · ');
        return (
          <div className="wl-memory__source">
            <Badge variant={b.variant}>{b.label}</Badge>
            <span className="wl-memory__source-detail">{detail || '—'}</span>
          </div>
        );
      },
    },
    {
      key: 'actions',
      header: '',
      render: (f) => {
        const busy = rowBusy.has(f.id);
        return (
          <div className="wl-memory__row-actions">
            {f.status === 'proposed' && (
              <Button variant="primary" disabled={busy} onClick={() => onApprove(f)}>Approve</Button>
            )}
            {f.status !== 'archived' && (
              <>
                <Button variant="ghost" disabled={busy} onClick={() => onPin(f)}>{f.pinned ? 'Unpin' : 'Pin'}</Button>
                <Button variant="ghost" disabled={busy} onClick={() => onEdit(f)}>Edit</Button>
                <Button variant="danger" disabled={busy} onClick={() => onDelete(f)}>Delete</Button>
              </>
            )}
          </div>
        );
      },
    },
  );

  const formModal = modal && modal.kind !== 'delete' ? modal : null;
  const draft = formModal?.draft;
  const saveDisabled =
    modalSaving ||
    !draft ||
    !draft.content.trim() ||
    !Number.isFinite(draft.confidence) ||
    draft.confidence < confidenceFloor ||
    draft.confidence > 1 ||
    (draft.scope_type === 'book' && draft.portfolioId == null);

  return (
    <PageScaffold title="Memory" chips={chips} feedback={feedback}>
      {status && !status.enabled && (
        <div className="wl-memory__banner">
          Memory capture is off (OPEN_OTC_MEMORY) — existing facts are still editable here.
        </div>
      )}
      {status && (
        <div className="wl-memory__config">
          floor {status.config.confidence_floor} · budget {status.config.injection_token_budget}/corr{' '}
          {status.config.correction_token_budget} · cap {status.config.max_facts_per_scope}/corr{' '}
          {status.config.max_correction_facts}
        </div>
      )}

      <Tabs value={activeScope} onValueChange={(v: string) => onScope(v as MemoryScope)}>
        <TabsList aria-label="Memory tabs">
          {SCOPES.map((s) => (
            <TabsTrigger key={s} value={s}>
              {s === 'all' ? 'All' : s[0].toUpperCase() + s.slice(1)}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <PageToolbar>
        <Select
          variant="inline"
          value={statusFilter}
          onChange={(v) => onStatusFilter(v as MemoryStatusFilter)}
          options={STATUS_OPTIONS.map((s) => ({ value: s, label: STATUS_LABEL[s] }))}
        />
        {activeScope === 'book' && (
          <Select
            variant="inline"
            value={selectedPortfolio == null ? '' : String(selectedPortfolio)}
            onChange={(v) => onSelectPortfolio(v === '' ? null : Number(v))}
            options={[
              { value: '', label: 'All portfolios' },
              ...portfolios.map((p) => ({ value: String(p.id), label: p.name })),
            ]}
            placeholder={portfolios.length === 0 ? 'No portfolios' : 'All portfolios'}
          />
        )}
        {portfoliosError && <span className="wl-memory__error">{portfoliosError}</span>}
        <PageToolbarSpacer />
        <PageToolbarSearch
          value={search}
          onChange={onSearch}
          placeholder="Search facts…"
          aria-label="Search memory facts"
        />
        <Button variant="default" onClick={onRefresh}>Refresh</Button>
        <Button variant="primary" onClick={onNew}>New</Button>
      </PageToolbar>

      {loading ? (
        <Empty message="Loading memory…" variant="loading" />
      ) : error ? (
        <Empty message={error} variant="error" />
      ) : (
        <>
          {visible.length === 0 ? (
            <Empty
              message={search.trim() ? 'No loaded facts match your search' : 'No facts in this view'}
              variant="empty"
              hint={search.trim() ? 'Load more to search later pages, or clear the search.' : 'Try a different scope or status filter.'}
            />
          ) : (
            <Table columns={columns} rows={visible} rowKey={(f) => f.id} />
          )}
          {(facts.length > 0 || total > 0) && (
            <div className="wl-memory__footer">
              <span className="wl-memory__count">
                Showing {visible.length} of {facts.length} loaded · {total} total
              </span>
              {facts.length < total && (
                <Button variant="default" onClick={onLoadMore}>Load more</Button>
              )}
            </div>
          )}
        </>
      )}

      <Modal
        open={formModal != null}
        onOpenChange={(o) => { if (!o) onModalCancel(); }}
        title={formModal?.kind === 'edit' ? 'Edit fact' : 'New fact'}
        resizable={false}
      >
        {draft && (
          <div className="wl-memory__form">
            {formModal?.kind === 'create' && (
              <label className="wl-memory__field">
                Scope
                <select
                  className="wl-memory__select"
                  value={draft.scope_type}
                  onChange={(e) => onModalChange({ ...draft, scope_type: e.target.value as MemoryDraft['scope_type'] })}
                >
                  <option value="user">user</option>
                  <option value="book">book</option>
                  <option value="domain">domain</option>
                  <option value="correction">correction</option>
                </select>
              </label>
            )}
            {formModal?.kind === 'create' && draft.scope_type === 'book' && (
              <label className="wl-memory__field">
                Portfolio
                <select
                  className="wl-memory__select"
                  value={draft.portfolioId == null ? '' : String(draft.portfolioId)}
                  onChange={(e) => onModalChange({ ...draft, portfolioId: e.target.value === '' ? null : Number(e.target.value) })}
                >
                  <option value="" disabled>Select a portfolio…</option>
                  {portfolios.map((p) => (
                    <option key={p.id} value={String(p.id)}>{p.name}</option>
                  ))}
                </select>
              </label>
            )}
            <label className="wl-memory__field">
              Content
              <textarea
                className="wl-memory__input"
                rows={3}
                value={draft.content}
                onChange={(e) => onModalChange({ ...draft, content: e.target.value })}
              />
            </label>
            <label className="wl-memory__field">
              Confidence
              <input
                className="wl-memory__input"
                type="number"
                min={confidenceFloor}
                max={1}
                step={0.01}
                value={draft.confidence}
                onChange={(e) => onModalChange({ ...draft, confidence: Number(e.target.value) })}
              />
            </label>
            {(!Number.isFinite(draft.confidence) || draft.confidence < confidenceFloor || draft.confidence > 1) && (
              <span className="wl-memory__error">confidence must be between {confidenceFloor} and 1.0</span>
            )}
            <label className="wl-memory__field">
              Category
              <input
                className="wl-memory__input"
                value={draft.category}
                onChange={(e) => onModalChange({ ...draft, category: e.target.value })}
              />
            </label>
            {modalError && <span className="wl-memory__error">{modalError}</span>}
            <div className="wl-memory__form-actions">
              <Button variant="default" onClick={onModalCancel}>Cancel</Button>
              <Button variant="primary" disabled={saveDisabled} onClick={onModalSave}>Save</Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={modal?.kind === 'delete'}
        onOpenChange={(o) => { if (!o) onModalCancel(); }}
        title="Archive fact"
        resizable={false}
      >
        <div className="wl-memory__form">
          <p>Archive this fact? It will no longer be injected and cannot be restored from this page.</p>
          {modalError && <span className="wl-memory__error">{modalError}</span>}
          <div className="wl-memory__form-actions">
            <Button variant="default" onClick={onModalCancel}>Cancel</Button>
            <Button variant="danger" disabled={modalSaving} onClick={onConfirmDelete}>Archive this fact</Button>
          </div>
        </div>
      </Modal>
    </PageScaffold>
  );
}

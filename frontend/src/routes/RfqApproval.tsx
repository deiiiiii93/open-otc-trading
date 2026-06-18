import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';
import { Search } from 'lucide-react';
import type { AuditEvent, Portfolio, RFQ } from '../types';
import { MasterDetailPage } from '../components/templates';
import { Panel } from '../components/Panel';
import { RfqInbox } from '../components/RfqInbox';
import { RfqDetail } from '../components/RfqDetail';
import { RfqAudit } from '../components/RfqAudit';
import { RfqRejectModal } from '../components/RfqRejectModal';
import { Empty } from '../components/Empty';
import { Select } from '../components/Select';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type { PageContext, PageContextReporter } from '../types';
import type { RfqQuoteOverrides } from '../components/RfqQuoteForm';
import './RfqApproval.css';

type Props = {
  rfqs: RFQ[];
  portfolios?: Portfolio[];
  auditEvents?: AuditEvent[];
  onApprove: (id: number) => Promise<void> | void;
  onReject: (id: number, reason: string) => Promise<void> | void;
  onQuote?: (id: number, overrides: RfqQuoteOverrides) => Promise<void> | void;
  onRelease?: (id: number) => Promise<void> | void;
  onAccept?: (id: number) => Promise<void> | void;
  onBook?: (id: number, portfolioId: number) => Promise<void> | void;
  onPageContextChange?: PageContextReporter;
};

const ALL_STATUSES = 'all';

function availableActions(rfq: RFQ | null): string[] {
  if (!rfq) return [];
  const actions: string[] = [];
  if (['draft', 'submitted', 'pricing_failed', 'pending_approval'].includes(rfq.status)) actions.push('quote_rfq');
  if (rfq.status === 'pending_approval') actions.push('approve_rfq', 'reject_rfq');
  if (rfq.status === 'approved') actions.push('release_rfq');
  if (rfq.status === 'released') actions.push('mark_rfq_client_accepted');
  if (rfq.status === 'client_accepted') actions.push('book_rfq_to_position');
  return actions;
}

export function RfqApproval({
  rfqs,
  portfolios = [],
  auditEvents = [],
  onApprove,
  onReject,
  onQuote,
  onRelease,
  onAccept,
  onBook,
  onPageContextChange,
}: Props) {
  const pending = useMemo(() => rfqs.filter((r) => r.status === 'pending_approval').length, [rfqs]);
  const [statusFilter, setStatusFilter] = useState(ALL_STATUSES);
  const [blotterSearch, setBlotterSearch] = useState('');
  const filteredRfqs = useMemo(
    () => (statusFilter === ALL_STATUSES ? rfqs : rfqs.filter((rfq) => rfq.status === statusFilter)),
    [rfqs, statusFilter],
  );
  const searchedRfqs = useMemo(() => {
    const query = blotterSearch.trim().toLowerCase();
    if (!query) return filteredRfqs;
    return filteredRfqs.filter((rfq) => [
      String(rfq.id),
      rfq.client_name,
      rfq.status,
      rfq.channel,
      String(rfq.request_payload?.underlying ?? ''),
      String(rfq.request_payload?.product_type ?? ''),
    ].some((value) => String(value ?? '').toLowerCase().includes(query)));
  }, [filteredRfqs, blotterSearch]);
  const statusOptions = useMemo(() => Array.from(new Set(rfqs.map((rfq) => rfq.status))).sort(), [rfqs]);
  const [selectedId, setSelectedId] = useState<number | null>(searchedRfqs[0]?.id ?? null);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<number | null>(portfolios[0]?.id ?? null);
  const detailRef = useRef<HTMLDivElement | null>(null);
  const [detailPct, setDetailPct] = useState(60);
  const selectedRfq = useMemo(
    () => searchedRfqs.find((r) => r.id === selectedId) ?? null,
    [searchedRfqs, selectedId],
  );
  const rejectingRfq = useMemo(() => rfqs.find((r) => r.id === rejectingId) ?? null, [rfqs, rejectingId]);
  const selectedQuoteVersion = selectedRfq?.quote_versions?.[0] ?? null;
  const selectedAuditEvents = useMemo(
    () => auditEvents.filter((event) => event.subject_type === 'rfq' && event.subject_id === String(selectedId)),
    [auditEvents, selectedId],
  );

  useEffect(() => {
    if (searchedRfqs.length === 0) {
      setSelectedId(null);
      return;
    }
    if (selectedId == null || !searchedRfqs.some((rfq) => rfq.id === selectedId)) {
      setSelectedId(searchedRfqs[0].id);
    }
  }, [searchedRfqs, selectedId]);

  useEffect(() => {
    if (selectedPortfolioId == null && portfolios[0]) {
      setSelectedPortfolioId(portfolios[0].id);
    }
  }, [portfolios, selectedPortfolioId]);

  const pageContext = useMemo(() => {
    const actions = availableActions(selectedRfq);
    const base: PageContext = {
      route: 'rfq',
      title: 'RFQ Blotter',
      path: '/',
      entity_ids: {
        rfq_id: selectedRfq?.id ?? null,
        quote_version_id: selectedQuoteVersion?.id ?? null,
      },
      snapshot: {
        filters: { status: statusFilter },
        pending_count: pending,
        total_count: rfqs.length,
        visible_count: searchedRfqs.length,
        available_actions: actions,
        selected_rfq: selectedRfq
          ? {
              id: selectedRfq.id,
              client_name: selectedRfq.client_name,
              channel: selectedRfq.channel,
              status: selectedRfq.status,
              request_payload: selectedRfq.request_payload,
              quote_payload: selectedRfq.quote_payload,
              latest_quote_version: selectedQuoteVersion,
            }
          : null,
      },
      chips: [`${pending} pending`, `${searchedRfqs.length} shown`, statusFilter],
    };
    if (!rejectingRfq) return base;
    return {
      ...base,
      title: 'Reject RFQ dialog',
      entity_ids: { ...base.entity_ids, rfq_id: rejectingRfq.id },
      snapshot: {
        parent_context: base,
        rejecting_rfq: {
          id: rejectingRfq.id,
          client_name: rejectingRfq.client_name,
          status: rejectingRfq.status,
        },
      },
      chips: ['dialog', `RFQ #${rejectingRfq.id}`, 'reject'],
    };
  }, [filteredRfqs.length, pending, rejectingRfq, rfqs.length, selectedQuoteVersion, selectedRfq, statusFilter]);
  usePageContextReporter(pageContext, onPageContextChange);

  const handleRejectClick = (id: number) => setRejectingId(id);
  const handleRejectConfirm = async (id: number, reason: string) => {
    await onReject(id, reason);
    setRejectingId(null);
  };
  const handleBook = async (id: number) => {
    if (selectedPortfolioId == null) return;
    await onBook?.(id, selectedPortfolioId);
  };

  const handleResizeDetailPointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!detailRef.current) return;
    event.preventDefault();
    const rect = detailRef.current.getBoundingClientRect();
    const handlePointerMove = (moveEvent: PointerEvent) => {
      const next = ((moveEvent.clientX - rect.left) / rect.width) * 100;
      setDetailPct(Math.min(80, Math.max(30, next)));
    };
    const handlePointerUp = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };

  const rail = (
    <Panel title="Blotter" meta={`${searchedRfqs.length}`}>
      <label className="wl-rfq-approval__blotter-search">
        <Search size={14} aria-hidden="true" />
        <input
          type="search"
          value={blotterSearch}
          onChange={(event) => setBlotterSearch(event.currentTarget.value)}
          aria-label="Search blotter"
          placeholder="Search blotter"
        />
      </label>
      <RfqInbox rfqs={searchedRfqs} selectedId={selectedId} onSelect={setSelectedId} />
    </Panel>
  );

  return (
    <MasterDetailPage
      title="RFQ BLOTTER"
      chips={[`${pending} pending`, `${searchedRfqs.length} shown`, `${rfqs.length} total`]}
      actions={(
        <div className="wl-rfq-approval__filters">
          <Select
            variant="inline"
            label="Status"
            value={statusFilter}
            onChange={(v) => setStatusFilter(v)}
            options={[
              { value: ALL_STATUSES, label: 'All statuses' },
              ...statusOptions.map((status) => ({ value: status, label: status })),
            ]}
          />
        </div>
      )}
      rail={rail}
      railLabel="RFQ blotter"
      resizableRail
      minRailWidth={180}
      maxRailWidth={420}
    >
      <div
        ref={detailRef}
        className="wl-rfq-approval__detail"
        style={{ '--rfq-detail-pct': `${detailPct}%` } as CSSProperties}
      >
        <Panel title={selectedRfq ? `RFQ #${selectedRfq.id}` : 'Detail'} meta={selectedRfq?.status ?? ''}>
          {selectedRfq ? (
            <RfqDetail
              rfq={selectedRfq}
              portfolios={portfolios}
              selectedPortfolioId={selectedPortfolioId}
              onPortfolioChange={setSelectedPortfolioId}
              onQuote={onQuote}
              onApprove={onApprove}
              onRelease={onRelease}
              onAccept={onAccept}
              onBook={handleBook}
              onRejectClick={handleRejectClick}
            />
          ) : (
            <Empty message="Select an RFQ from the inbox" symbol="◌" />
          )}
        </Panel>

        <button
          type="button"
          className="wl-rfq-approval__panel-resizer"
          aria-label="Resize detail panel"
          aria-valuemin={30}
          aria-valuemax={80}
          aria-valuenow={Math.round(detailPct)}
          onPointerDown={handleResizeDetailPointerDown}
        />

        <Panel title="Audit" meta="">
          {selectedRfq ? <RfqAudit rfq={selectedRfq} events={selectedAuditEvents} /> : <Empty message="No selection" symbol="◌" />}
        </Panel>
      </div>
      <RfqRejectModal
        open={rejectingId != null}
        rfqId={rejectingId}
        onConfirm={handleRejectConfirm}
        onOpenChange={(open) => { if (!open) setRejectingId(null); }}
      />
    </MasterDetailPage>
  );
}

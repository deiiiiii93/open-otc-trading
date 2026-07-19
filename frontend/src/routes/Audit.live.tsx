import { useCallback, useEffect, useRef, useState } from 'react';
import type { AuditAction, AuditActionDetail, AuditSummary } from '../types';
import { fetchAuditSummary, getAuditAction, listAuditActions } from '../api/client';
import { Audit } from './Audit';

const DEFAULT_PAGE_SIZE = 25;

function auditRefFromLocation(): string | null {
  const value = new URLSearchParams(window.location.search).get('audit_ref');
  return value != null && value !== '' ? value : null;
}

export function AuditLive() {
  const [items, setItems] = useState<AuditAction[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [detail, setDetail] = useState<AuditActionDetail | null>(null);
  const [auditRef, setAuditRef] = useState<string | null>(auditRefFromLocation);
  const loadRequestIdRef = useRef(0);
  const detailRequestIdRef = useRef(0);

  // Filters/page-size changes invalidate the current page — jump back to
  // page 0 rather than showing an out-of-range offset.
  useEffect(() => {
    setPage(0);
  }, [auditRef, search, statusFilter, classFilter, modeFilter, pageSize]);

  // A deep-link target owns the open detail. Back/Forward navigation must
  // replace it immediately, and pending requests must not reopen stale rows.
  useEffect(() => {
    detailRequestIdRef.current += 1;
    setDetail(null);
  }, [auditRef]);

  useEffect(() => {
    const onPopState = () => {
      const nextAuditRef = auditRefFromLocation();
      if (nextAuditRef === auditRef) return;
      loadRequestIdRef.current += 1;
      detailRequestIdRef.current += 1;
      setDetail(null);
      setAuditRef(nextAuditRef);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [auditRef]);

  useEffect(() => () => {
    loadRequestIdRef.current += 1;
    detailRequestIdRef.current += 1;
  }, []);

  const load = useCallback(
    async () => {
      const requestId = ++loadRequestIdRef.current;
      const autoDetailRequestId = auditRef == null
        ? null
        : ++detailRequestIdRef.current;
      setLoading(true);
      setError(null);
      try {
        const [result, sum] = await Promise.all([
          listAuditActions({
            audit_ref: auditRef || undefined,
            tool_name: search || undefined,
            status: statusFilter || undefined,
            tool_class: classFilter || undefined,
            mode: modeFilter || undefined,
            limit: pageSize,
            offset: page * pageSize,
          }),
          fetchAuditSummary(),
        ]);
        if (requestId !== loadRequestIdRef.current) return;
        setItems(result.items);
        setTotal(result.total);
        setSummary(sum);

        if (auditRef != null && autoDetailRequestId != null) {
          // Defend against a server/client regression that returns a broader
          // list: only an exact audit_ref match may satisfy this deep link.
          const match = result.items.find((row) => row.audit_ref === auditRef);
          if (match == null) {
            setDetail(null);
          } else {
            const nextDetail = await getAuditAction(match.id);
            if (
              requestId === loadRequestIdRef.current
              && autoDetailRequestId === detailRequestIdRef.current
              && nextDetail.audit_ref === auditRef
            ) {
              setDetail(nextDetail);
            }
          }
        }
      } catch (err) {
        if (requestId === loadRequestIdRef.current) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (requestId === loadRequestIdRef.current) setLoading(false);
      }
    },
    [auditRef, search, statusFilter, classFilter, modeFilter, page, pageSize],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onRowClick = useCallback(async (row: AuditAction) => {
    const requestId = ++detailRequestIdRef.current;
    try {
      const nextDetail = await getAuditAction(row.id);
      if (requestId === detailRequestIdRef.current) setDetail(nextDetail);
    } catch (err) {
      if (requestId === detailRequestIdRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }, []);

  return (
    <Audit
      items={items}
      total={total}
      page={page}
      pageSize={pageSize}
      summary={summary}
      loading={loading}
      error={error}
      search={search}
      statusFilter={statusFilter}
      classFilter={classFilter}
      modeFilter={modeFilter}
      detail={detail}
      onSearch={setSearch}
      onStatusFilter={setStatusFilter}
      onClassFilter={setClassFilter}
      onModeFilter={setModeFilter}
      onRowClick={onRowClick}
      onCloseDetail={() => {
        detailRequestIdRef.current += 1;
        setDetail(null);
      }}
      onPage={setPage}
      onPageSize={setPageSize}
      onRefresh={() => void load()}
    />
  );
}

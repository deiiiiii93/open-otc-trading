import { useCallback, useEffect, useState } from 'react';
import type { AuditAction, AuditActionDetail, AuditSummary } from '../types';
import { fetchAuditSummary, getAuditAction, listAuditActions } from '../api/client';
import { Audit } from './Audit';

const PAGE = 50;

export function AuditLive() {
  const [items, setItems] = useState<AuditAction[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [detail, setDetail] = useState<AuditActionDetail | null>(null);

  const load = useCallback(
    async (offset = 0) => {
      setLoading(true);
      setError(null);
      try {
        const [page, sum] = await Promise.all([
          listAuditActions({
            tool_name: search || undefined,
            status: statusFilter || undefined,
            tool_class: classFilter || undefined,
            mode: modeFilter || undefined,
            limit: PAGE,
            offset,
          }),
          fetchAuditSummary(),
        ]);
        setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
        setTotal(page.total);
        setSummary(sum);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [search, statusFilter, classFilter, modeFilter],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const onRowClick = useCallback(async (row: AuditAction) => {
    try {
      setDetail(await getAuditAction(row.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  return (
    <Audit
      items={items}
      total={total}
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
      onCloseDetail={() => setDetail(null)}
      onLoadMore={() => void load(items.length)}
      onRefresh={() => void load(0)}
    />
  );
}

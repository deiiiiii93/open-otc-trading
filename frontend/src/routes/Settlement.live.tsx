import { useCallback, useEffect, useState } from 'react';
import {
  createSettlementNotice,
  fetchSettlementSummary,
  getSettlementCashflow,
  listSettlementCashflows,
  settlementAction,
  sweepSettlement,
} from '../api/client';
import { Button } from '../components/Button';
import { Modal } from '../components/Modal';
import { Select } from '../components/Select';
import { Settlement } from './Settlement';
import type {
  SettlementCashflowDetail,
  SettlementCashflowRow,
  SettlementSummary,
} from './Settlement.types';

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  ...['needs_amount', 'pending', 'blocked', 'released', 'settled', 'void'].map(
    (value) => ({ value, label: value.replace('_', ' ') }),
  ),
];

type Action = Parameters<typeof settlementAction>[1];

export function SettlementLive() {
  const [rows, setRows] = useState<SettlementCashflowRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<SettlementSummary | null>(null);
  const [status, setStatus] = useState('');
  const [staleOnly, setStaleOnly] = useState(false);
  const [detail, setDetail] = useState<SettlementCashflowDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, next] = await Promise.all([
        listSettlementCashflows({ status: status || undefined, stale: staleOnly }),
        fetchSettlementSummary(),
      ]);
      setRows(list.items);
      setTotal(list.total);
      setSummary(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [status, staleOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDetail = useCallback(async (row: SettlementCashflowRow) => {
    setError(null);
    try {
      setDetail(await getSettlementCashflow(row.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const sweep = useCallback(
    async (kind: 'generate' | 'refresh') => {
      setLoading(true);
      setError(null);
      try {
        await sweepSettlement(kind);
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [load],
  );

  const act = useCallback(
    async (row: SettlementCashflowRow, action: Action) => {
      setError(null);
      const result = await settlementAction(row.id, action, row.row_version);
      if (!result.ok) {
        // A 409 means someone else moved the row. Never retry blind against a
        // stale version — reload and let the operator look again.
        setError(
          result.conflict
            ? 'This cashflow changed elsewhere — reloaded with current state.'
            : result.message,
        );
        await load();
        return;
      }
      setDetail(null);
      await load();
    },
    [load],
  );

  const issueNotice = useCallback(
    async (row: SettlementCashflowRow) => {
      setError(null);
      try {
        await createSettlementNotice(row.id);
        setDetail(await getSettlementCashflow(row.id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [],
  );

  return (
    <Settlement
      rows={rows}
      total={total}
      summary={summary}
      loading={loading}
      error={error}
      selectedId={detail?.id ?? null}
      onRowClick={openDetail}
      actions={
        <>
          <Button onClick={() => void sweep('generate')}>Generate</Button>
          <Button onClick={() => void sweep('refresh')}>Refresh drift</Button>
        </>
      }
      toolbar={{
        filters: (
          <>
            <Select
              value={status}
              options={STATUS_OPTIONS}
              onChange={setStatus}
              variant="inline"
              label="Status"
            />
            <label className="wl-settlement__stale-toggle">
              <input
                type="checkbox"
                checked={staleOnly}
                onChange={(e) => setStaleOnly(e.target.checked)}
              />
              Stale only
            </label>
          </>
        ),
      }}
      overlays={
        detail && (
          <Modal
            open
            onOpenChange={(open) => !open && setDetail(null)}
            title={`Cashflow #${detail.id} — ${detail.leg_key}`}
          >
            <div className="wl-settlement__drawer">
              <dl className="wl-settlement__facts">
                <dt>Effective</dt>
                <dd>
                  {detail.amount ?? '—'} {detail.currency} ({detail.direction})
                </dd>
                <dt>Derived</dt>
                <dd>
                  {detail.derived_amount ?? '—'} ({detail.derived_basis ?? 'manual'})
                </dd>
                <dt>Value date</dt>
                <dd>{detail.value_date ?? 'to be confirmed'}</dd>
                <dt>Counterparty</dt>
                <dd>{detail.counterparty ?? '—'}</dd>
                <dt>Drift checked</dt>
                <dd>{detail.last_checked_at ?? 'never checked'}</dd>
              </dl>

              {detail.stale && (
                <pre className="wl-settlement__drift">
                  {JSON.stringify(detail.stale_reason, null, 2)}
                </pre>
              )}

              <div>
                <h3>History</h3>
                <ul className="wl-settlement__history">
                  {detail.events.map((e) => (
                    <li key={e.id}>
                      {e.created_at} — {e.action} by {e.actor}
                      {e.reason ? ` (${e.reason})` : ''}
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h3>Notices</h3>
                {detail.notices.length === 0 ? (
                  <p className="wl-settlement__muted">None issued.</p>
                ) : (
                  <ul className="wl-settlement__history">
                    {detail.notices.map((n) => (
                      <li key={n.id}>
                        <a href={`/artifacts/${n.artifact_path}`}>v{n.version}</a>{' '}
                        ({n.status}, {n.rendered_at})
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="wl-settlement__actions">
                <Button onClick={() => void act(detail, 'release')}>Release</Button>
                <Button onClick={() => void act(detail, 'block')}>Block</Button>
                <Button onClick={() => void act(detail, 'settle')}>Mark settled</Button>
                {detail.stale && (
                  <Button onClick={() => void act(detail, 'resync')}>Resync</Button>
                )}
                <Button onClick={() => void issueNotice(detail)}>Notice</Button>
              </div>
            </div>
          </Modal>
        )
      }
    />
  );
}

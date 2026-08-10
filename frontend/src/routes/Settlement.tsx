import React, { useMemo } from 'react';
import { DataTablePage } from '../components/templates/DataTablePage';
import type { Column } from '../components/Table';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Empty } from '../components/Empty';
import type { TableToolbarProps } from '../components/TableToolbar';
import './Settlement.css';

import type {
  SettlementCashflowRow,
  SettlementSummary,
} from './Settlement.types';

export type { SettlementCashflowRow, SettlementSummary };

export interface SettlementProps {
  rows: SettlementCashflowRow[];
  total: number;
  summary: SettlementSummary | null;
  loading?: boolean;
  error?: string | null;
  selectedId?: number | null;
  onRowClick?: (row: SettlementCashflowRow) => void;
  toolbar?: TableToolbarProps;
  actions?: React.ReactNode;
  overlays?: React.ReactNode;
}

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  needs_amount: 'warn',
  pending: 'info',
  blocked: 'neg',
  released: 'pos',
  settled: 'pos',
  void: 'ink',
};

const STATUS_LABEL: Record<string, string> = {
  needs_amount: 'needs amount',
  pending: 'pending',
  blocked: 'blocked',
  released: 'released',
  settled: 'settled',
  void: 'void',
};

function money(amount: number | null): string {
  if (amount === null || amount === undefined) return '—';
  return amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function Settlement({
  rows,
  total,
  summary,
  loading,
  error,
  selectedId,
  onRowClick,
  toolbar,
  actions,
  overlays,
}: SettlementProps) {
  // Only `fr` and fixed lengths align across rows: the Table primitive renders
  // each row as its own CSS grid, so max-content/auto tracks resolve per row
  // and break column alignment.
  const columns = useMemo<Column<SettlementCashflowRow>[]>(
    () => [
      {
        key: 'position',
        header: 'Position',
        width: 'minmax(0, 1.6fr)',
        render: (r) => (
          <span className="wl-settlement__position">
            #{r.position_id} {r.underlying ?? '—'}
            <span className="wl-settlement__muted"> {r.product_type ?? ''}</span>
          </span>
        ),
      },
      {
        key: 'event',
        header: 'Event',
        width: 'minmax(0, 1fr)',
        render: (r) => r.event_type ?? '—',
      },
      {
        key: 'leg',
        header: 'Leg',
        width: 'minmax(0, 1fr)',
        render: (r) => r.leg_key,
      },
      { key: 'direction', header: 'Dir', width: '72px', render: (r) => r.direction },
      {
        key: 'amount',
        header: 'Amount',
        width: '140px',
        numeric: true,
        render: (r) =>
          r.amount === null ? (
            <span className="wl-settlement__muted">needs amount</span>
          ) : (
            money(r.amount)
          ),
      },
      { key: 'currency', header: 'Ccy', width: '64px', render: (r) => r.currency },
      {
        key: 'value_date',
        header: 'Value date',
        width: '124px',
        render: (r) => r.value_date ?? '—',
      },
      {
        key: 'counterparty',
        header: 'Counterparty',
        width: 'minmax(0, 1.4fr)',
        render: (r) => r.counterparty ?? '—',
      },
      {
        key: 'status',
        header: 'Status',
        width: '132px',
        render: (r) => (
          <Badge variant={STATUS_VARIANT[r.status] ?? 'ink'}>
            {STATUS_LABEL[r.status] ?? r.status}
          </Badge>
        ),
      },
      {
        key: 'flags',
        header: 'Flags',
        width: '92px',
        render: (r) => (r.stale ? <Badge variant="warn">stale</Badge> : null),
      },
    ],
    [],
  );

  const chips = useMemo(() => {
    const parts = [`${total} cashflows`];
    if (!summary) return parts;
    for (const key of ['needs_amount', 'pending', 'released', 'settled']) {
      const count = summary.by_status[key];
      if (count) parts.push(`${count} ${STATUS_LABEL[key] ?? key}`);
    }
    if (summary.stale_count) parts.push(`${summary.stale_count} stale`);
    return parts;
  }, [summary, total]);

  return (
    <DataTablePage<SettlementCashflowRow>
      title="Settlement"
      chips={chips}
      actions={actions}
      feedback={error ?? (loading ? 'Loading…' : null)}
      toolbar={toolbar}
      table={{
        columns,
        rows,
        rowKey: (r) => r.id,
        selectedKey: selectedId ?? null,
        onRowClick,
      }}
      empty={
        <Empty
          message="No settlement cashflows"
          hint="Run Generate to pick up lifecycle events that have not been swept yet."
        />
      }
      overlays={overlays}
    />
  );
}

import { useMemo } from 'react';
import type { AuditAction, AuditActionDetail, AuditSummary } from '../types';
import { DataTablePage } from '../components/templates/DataTablePage';
import type { Column } from '../components/Table';
import { Badge, type BadgeVariant } from '../components/Badge';
import { Select } from '../components/Select';
import { Modal } from '../components/Modal';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import './Audit.css';

export interface AuditProps {
  items: AuditAction[];
  total: number;
  page: number;
  pageSize: number;
  summary: AuditSummary | null;
  loading: boolean;
  error: string | null;
  search: string;
  statusFilter: string;
  classFilter: string;
  modeFilter: string;
  detail: AuditActionDetail | null;
  onSearch: (value: string) => void;
  onStatusFilter: (value: string) => void;
  onClassFilter: (value: string) => void;
  onModeFilter: (value: string) => void;
  onRowClick: (row: AuditAction) => void;
  onCloseDetail: () => void;
  onPage: (page: number) => void;
  onPageSize: (pageSize: number) => void;
  onRefresh: () => void;
}

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  ok: 'pos',
  approved: 'pos',
  denied: 'neg',
  error: 'neg',
  refused: 'neg',
  attempted: 'warn',
  interrupted: 'warn',
  proposed: 'warn',
  rejected: 'ink',
};

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  ...[
    'ok', 'error', 'denied', 'refused', 'attempted', 'interrupted',
    'proposed', 'approved', 'rejected',
  ].map((value) => ({ value, label: value })),
];

const CLASS_OPTIONS = [
  { value: '', label: 'All classes' },
  ...['domain_write', 'async_dispatch', 'fs_write', 'artifact_write'].map(
    (value) => ({ value, label: value }),
  ),
];

const MODE_OPTIONS = [
  { value: '', label: 'All modes' },
  ...['interactive', 'auto', 'yolo'].map((value) => ({ value, label: value })),
];

function statusBadge(status: string) {
  return <Badge variant={STATUS_VARIANT[status] ?? 'ink'}>{status}</Badge>;
}

function formatTime(iso: string): string {
  const stamped = /Z|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  return new Date(stamped).toLocaleString();
}

function argsSummary(row: AuditAction): string {
  const args = row.args_json ?? {};
  const keys = Object.keys(args);
  if (!keys.length) return '—';
  const first = keys
    .slice(0, 3)
    .map((key) => `${key}=${JSON.stringify(args[key])}`)
    .join(', ');
  return keys.length > 3 ? `${first}, +${keys.length - 3}` : first;
}

export function Audit(props: AuditProps) {
  const {
    items, total, page, pageSize, summary, loading, error, search,
    statusFilter, classFilter, modeFilter, detail,
  } = props;

  const columns = useMemo<Column<AuditAction>[]>(
    () => [
      {
        key: 'occurred_at',
        header: 'Time',
        width: '11rem',
        render: (row) => (
          <span className="audit__time">{formatTime(row.occurred_at)}</span>
        ),
      },
      {
        key: 'tool_name',
        header: 'Tool',
        width: 'minmax(0, 1.2fr)',
        render: (row) => (
          <span className="audit__tool">
            {row.tool_name}
            {row.persona ? <span className="audit__persona"> · {row.persona}</span> : null}
          </span>
        ),
      },
      {
        key: 'tool_class',
        header: 'Class',
        width: '8.5rem',
        render: (row) => <Badge variant="info">{row.tool_class}</Badge>,
      },
      {
        key: 'status',
        header: 'Status',
        width: '7.5rem',
        render: (row) => statusBadge(row.status),
      },
      {
        key: 'mode',
        header: 'Mode',
        width: '6.5rem',
        render: (row) =>
          row.mode ? (
            <Badge variant={row.mode === 'yolo' ? 'neg' : 'ink'} solid={row.mode === 'yolo'}>
              {row.mode}
            </Badge>
          ) : (
            '—'
          ),
      },
      { key: 'actor', header: 'Actor', width: '7rem' },
      {
        key: 'args',
        header: 'Args',
        width: 'minmax(0, 2fr)',
        render: (row) => <span className="audit__args">{argsSummary(row)}</span>,
      },
    ],
    [],
  );

  const chips = summary
    ? [
        `${total} records`,
        `denied ${(summary.by_status.denied ?? 0) + (summary.by_status.refused ?? 0)}`,
        `yolo ${summary.by_mode.yolo ?? 0}`,
      ]
    : [`${total} records`];

  const filters = (
    <>
      <Select
        value={statusFilter}
        onChange={props.onStatusFilter}
        options={STATUS_OPTIONS}
        placeholder="Status"
        variant="inline"
      />
      <Select
        value={classFilter}
        onChange={props.onClassFilter}
        options={CLASS_OPTIONS}
        placeholder="Class"
        variant="inline"
      />
      <Select
        value={modeFilter}
        onChange={props.onModeFilter}
        options={MODE_OPTIONS}
        placeholder="Mode"
        variant="inline"
      />
    </>
  );

  return (
    <DataTablePage<AuditAction>
      title="Audit"
      chips={chips}
      feedback={error}
      actions={
        <Button onClick={props.onRefresh} disabled={loading}>
          Refresh
        </Button>
      }
      toolbar={{
        search: {
          value: search,
          onChange: props.onSearch,
          placeholder: 'Filter by tool name…',
        },
        filters,
        pager: {
          page,
          pageSize,
          total,
          onPage: props.onPage,
          onPageSize: props.onPageSize,
        },
      }}
      table={{
        columns,
        rows: items,
        rowKey: (row) => row.id,
        selectedKey: detail?.id ?? null,
        onRowClick: props.onRowClick,
      }}
      empty={
        <Empty
          message="No audit records"
          hint="Dangerous agent actions (bookings, writes, deletes, dispatches) will appear here."
        />
      }
      overlays={
        <Modal
          open={detail != null}
          onOpenChange={(open) => {
            if (!open) props.onCloseDetail();
          }}
          title={detail ? `${detail.tool_name} — ${detail.status}` : 'Audit record'}
          layoutKey="audit-detail"
          defaultWidth={640}
          defaultHeight={520}
        >
          {detail && (
            <div className="audit__detail">
              <dl className="audit__meta">
                <dt>Kind</dt>
                <dd>{detail.kind}</dd>
                <dt>Actor</dt>
                <dd>{detail.actor}</dd>
                <dt>Mode</dt>
                <dd>{detail.mode ?? '—'}</dd>
                <dt>Model</dt>
                <dd>{detail.model ?? '—'}</dd>
                <dt>Persona</dt>
                <dd>{detail.persona ?? '—'}</dd>
                <dt>Thread</dt>
                <dd>{detail.thread_id != null ? `#${detail.thread_id}` : '—'}</dd>
                <dt>Workflow</dt>
                <dd>{detail.desk_workflow_slug ?? detail.workflow_id ?? '—'}</dd>
                <dt>Deny reason</dt>
                <dd>{detail.deny_reason ?? '—'}</dd>
                <dt>Redacted</dt>
                <dd>{detail.redacted ? 'yes' : 'no'}</dd>
                <dt>Completed</dt>
                <dd>{detail.completed_at ? formatTime(detail.completed_at) : '—'}</dd>
              </dl>
              <h4 className="audit__section-title">Args</h4>
              <pre className="audit__json">{JSON.stringify(detail.args_json, null, 2)}</pre>
              {detail.result_preview && (
                <>
                  <h4 className="audit__section-title">Result</h4>
                  <pre className="audit__json">{detail.result_preview}</pre>
                </>
              )}
              {detail.error && (
                <>
                  <h4 className="audit__section-title">Error</h4>
                  <pre className="audit__json audit__json--error">{detail.error}</pre>
                </>
              )}
              {detail.related.length > 0 && (
                <>
                  <h4 className="audit__section-title">Action chain</h4>
                  <ul className="audit__related">
                    {detail.related.map((entry) => (
                      <li key={entry.id} className="audit__related-item">
                        <span className="audit__related-kind">{entry.kind}</span>
                        {statusBadge(entry.status)}
                        <span className="audit__time">{formatTime(entry.occurred_at)}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </Modal>
      }
    />
  );
}

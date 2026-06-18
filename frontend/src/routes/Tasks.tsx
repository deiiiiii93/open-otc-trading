import { useMemo, useState } from 'react';
import { DataTablePage } from '../components/templates';
import { Empty } from '../components/Empty';
import { TaskErrorDialog } from '../components/TaskErrorDialog';
import { RiskReportDialog } from '../components/RiskReportDialog';
import { type Column } from '../components/Table';
import { formatCount } from '../components/numberFormat';
import { usePageContextReporter } from '../hooks/usePageContextReporter';
import type { PageContext, PageContextReporter, TaskRun } from '../types';
import './Tasks.css';

type Props = {
  tasks: TaskRun[];
  loading: boolean;
  error: string | null;
  onPageContextChange?: PageContextReporter;
  onOpenGreeksLandscape?: () => void;
};

const ACTIVE_STATUSES = new Set(['queued', 'running']);
const ERROR_STATUSES = new Set(['failed', 'completed_with_errors']);

export function Tasks({ tasks, loading, error, onPageContextChange, onOpenGreeksLandscape }: Props) {
  const [errorTask, setErrorTask] = useState<TaskRun | null>(null);
  const [riskReportRunId, setRiskReportRunId] = useState<number | null>(null);
  const runningCount = tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length;
  const failedCount = tasks.filter((task) => task.status === 'failed').length;
  const warningCount = tasks.filter((task) => task.status === 'completed_with_errors').length;
  const chips = [
    taskCountLabel(tasks.length),
    runningCount ? `${formatCount(runningCount)} active` : 'No active tasks',
    ...(failedCount ? [`${formatCount(failedCount)} failed`] : []),
    ...(warningCount ? [`${formatCount(warningCount)} with errors`] : []),
  ];
  const columns = useMemo<Column<TaskRun>[]>(() => [
    {
      key: 'task',
      header: 'Task',
      width: 'minmax(240px, 1.5fr)',
      render: (task) => <TaskCell task={task} />,
    },
    {
      key: 'status',
      header: 'STATUS',
      width: '190px',
      render: (task) => <StatusCell task={task} onViewErrors={setErrorTask} />,
    },
    {
      key: 'progress',
      header: 'Progress',
      width: 'minmax(180px, 1fr)',
      render: (task) => <ProgressCell task={task} />,
    },
    {
      key: 'links',
      header: 'Links',
      width: 'minmax(180px, 1fr)',
      render: (task) => <LinkCell task={task} onOpenRiskReport={setRiskReportRunId} onOpenGreeksLandscape={onOpenGreeksLandscape} />,
    },
    {
      key: 'started',
      header: 'STARTED',
      width: '170px',
      render: (task) => (
        <span className="wl-tasks__secondary">
          {formatDateTime(task.started_at ?? task.created_at)}
        </span>
      ),
    },
    {
      key: 'finished',
      header: 'Finished',
      width: '170px',
      render: (task) => (
        <span className="wl-tasks__secondary">
          {formatDateTime(task.finished_at)}
        </span>
      ),
    },
  ], [onOpenGreeksLandscape]);
  const pageContext = useMemo((): PageContext => ({
    route: 'tasks',
    title: 'Tasks',
    path: '/',
    entity_ids: {},
    snapshot: {
      loading,
      error,
      tasks: tasks.slice(0, 20).map((task) => ({
        id: task.id,
        kind: task.kind,
        status: task.status,
        portfolio_id: task.portfolio_id,
        risk_run_id: task.risk_run_id,
        report_job_id: task.report_job_id,
        progress_current: task.progress_current,
        progress_total: task.progress_total,
        message: task.message,
      })),
    },
    chips,
  }), [chips, error, loading, tasks]);
  usePageContextReporter(pageContext, onPageContextChange);

  const showEmpty = !loading && tasks.length === 0;

  return (
    <DataTablePage<TaskRun>
      title="TASKS"
      chips={chips}
      feedback={error ? <div className="wl-tasks__error">{error}</div> : undefined}
      table={showEmpty ? undefined : { columns, rows: tasks, rowKey: (task) => task.id, className: 'wl-tasks__table' }}
      body={showEmpty ? <Empty message="No async tasks yet." symbol="◌" /> : undefined}
      overlays={
        <>
          <TaskErrorDialog
            task={errorTask}
            open={errorTask !== null}
            onClose={() => setErrorTask(null)}
          />
          <RiskReportDialog
            riskRunId={riskReportRunId}
            open={riskReportRunId !== null}
            onClose={() => setRiskReportRunId(null)}
          />
        </>
      }
    />
  );
}

function TaskCell({ task }: { task: TaskRun }) {
  return (
    <div className="wl-tasks__task">
      <div className="wl-tasks__primary">#{task.id} {labelKind(task.kind)}</div>
      <div className="wl-tasks__secondary">{task.message ?? 'No message'}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`wl-tasks__status wl-tasks__status--${status}`}>
      {formatStatus(status)}
    </span>
  );
}

function StatusCell({
  task,
  onViewErrors,
}: {
  task: TaskRun;
  onViewErrors: (task: TaskRun) => void;
}) {
  return (
    <span className="wl-tasks__status-cell">
      <StatusBadge status={task.status} />
      {ERROR_STATUSES.has(task.status) && (
        <button
          type="button"
          className="wl-tasks__error-button"
          onClick={() => onViewErrors(task)}
        >
          View errors
        </button>
      )}
    </span>
  );
}

function ProgressCell({ task }: { task: TaskRun }) {
  const progressTotal = Math.max(0, task.progress_total || 0);
  const progressCurrent = Math.max(0, task.progress_current || 0);
  const percent = progressTotal > 0
    ? Math.min(100, Math.round((progressCurrent / progressTotal) * 100))
    : ACTIVE_STATUSES.has(task.status) ? 8 : 100;
  return (
    <div className="wl-tasks__progress-cell">
      <div className="wl-tasks__progress-line">
        <span>{progressCurrent} / {progressTotal || '-'}</span>
        <span>{percent}%</span>
      </div>
      <div className="wl-tasks__progress" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function LinkCell({
  task,
  onOpenRiskReport,
  onOpenGreeksLandscape,
}: {
  task: TaskRun;
  onOpenRiskReport: (riskRunId: number) => void;
  onOpenGreeksLandscape?: () => void;
}) {
  return (
    <div className="wl-tasks__links">
      {task.portfolio_id != null && <span>Portfolio #{task.portfolio_id}</span>}
      {task.risk_run_id != null && (
        <button
          type="button"
          className="wl-tasks__link-button"
          onClick={() => {
            const riskRunId = task.risk_run_id;
            if (riskRunId != null) onOpenRiskReport(riskRunId);
          }}
        >
          Risk #{task.risk_run_id}
        </button>
      )}
      {task.report_job_id != null && <span>Report #{task.report_job_id}</span>}
      {task.greeks_landscape_run_id != null && (
        <button type="button" className="wl-tasks__link-button" onClick={onOpenGreeksLandscape}>
          Landscape #{task.greeks_landscape_run_id}
        </button>
      )}
    </div>
  );
}

function taskCountLabel(count: number): string {
  return `${formatCount(count)} ${count === 1 ? 'task' : 'tasks'}`;
}

function labelKind(kind: string): string {
  if (kind === 'position_pricing') return 'Position pricing';
  if (kind === 'risk_run') return 'Risk run';
  if (kind === 'report_job') return 'Report job';
  if (kind === 'batch_pricing') return 'Batch pricing';
  if (kind === 'greeks_landscape') return 'Greeks landscape';
  return kind.replaceAll('_', ' ');
}

function formatStatus(status: string): string {
  return status
    .replaceAll('_', ' ')
    .replace(/^\w/, (letter) => letter.toUpperCase());
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Pending';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleString();
}

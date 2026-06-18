import { Button } from './Button';
import {
  personaDisplayLabel,
  proposalToolName,
  type AgentActionProposal,
  type TaskRun,
} from '../types';
import './ActionProposal.css';

type Props = {
  proposal: AgentActionProposal;
  executing?: boolean;
  task?: TaskRun;
  onConfirm: (p: AgentActionProposal) => void;
  onDismiss: (p: AgentActionProposal) => void;
  onViewPayload?: (p: AgentActionProposal) => void;
  disabled?: boolean;
};

export function ActionProposal({
  proposal,
  executing = false,
  task,
  onConfirm,
  onDismiss,
  onViewPayload,
  disabled,
}: Props) {
  const status = proposal.status ?? 'pending';
  const isPending = status === 'pending';
  const toolName = proposalToolName(proposal);
  const progress = actionProgressState({ executing, task, toolName });
  const statusLabel = status === 'confirmed'
    ? 'Confirmed'
    : status === 'dismissed'
      ? 'Dismissed'
      : status === 'failed'
        ? 'Failed'
        : 'Pending Confirmation';

  return (
    <div className={`wl-actprop wl-actprop--${status} wl-actprop--${proposal.risk_level ?? 'unknown'}`}>
      <header className="wl-actprop__head">
        <span className="wl-actprop__tag">{isPending ? '⚠ ' : ''}{statusLabel}</span>
        {proposal.persona && (
          <span className="wl-actprop__persona">{personaDisplayLabel(proposal.persona)}</span>
        )}
        <code className="wl-actprop__tool">{toolName}</code>
        {isPending && <span className="wl-actprop__kbd">⌘↵ to confirm</span>}
      </header>
      <div className="wl-actprop__label">{proposal.label}</div>
      <div className="wl-actprop__summary">{proposal.summary}</div>
      {progress && <ActionProgress progress={progress} />}
      <details className="wl-actprop__args">
        <summary>Arguments</summary>
        <pre>{JSON.stringify(proposal.payload ?? {}, null, 2)}</pre>
      </details>
      <div className="wl-actprop__actions">
        <Button variant="primary" onClick={() => onConfirm(proposal)} disabled={!isPending || disabled}>
          Confirm Action
        </Button>
        <Button onClick={() => onDismiss(proposal)} disabled={!isPending || disabled}>
          Dismiss
        </Button>
        {onViewPayload && <Button variant="ghost" onClick={() => onViewPayload(proposal)}>View payload</Button>}
      </div>
    </div>
  );
}

type ActionProgressState = {
  label: string;
  detail?: string;
  current?: number;
  total?: number;
  tone: 'active' | 'done' | 'error';
};

function actionProgressState({
  executing,
  task,
  toolName,
}: {
  executing: boolean;
  task?: TaskRun;
  toolName: string;
}): ActionProgressState | null {
  if (task) {
    const label = `${taskKindLabel(task.kind)} ${taskStatusLabel(task.status)}`;
    const total = Math.max(0, task.progress_total || 0);
    const current = Math.max(0, task.progress_current || 0);
    return {
      label,
      detail: task.message ?? undefined,
      current: total > 0 ? Math.min(current, total) : undefined,
      total: total > 0 ? total : undefined,
      tone: task.status === 'failed'
        ? 'error'
        : task.status === 'queued' || task.status === 'running'
          ? 'active'
          : 'done',
    };
  }
  if (!executing) return null;
  return {
    label: `Running ${toolName}`,
    detail: 'Waiting for the approved tool to report status.',
    tone: 'active',
  };
}

function ActionProgress({ progress }: { progress: ActionProgressState }) {
  const percent = progress.total
    ? Math.round((Math.max(0, progress.current ?? 0) / progress.total) * 100)
    : null;
  return (
    <div className={`wl-actprop__progress wl-actprop__progress--${progress.tone}`} role="status" aria-live="polite">
      <div className="wl-actprop__progress-row">
        <span className="wl-actprop__progress-label">{progress.label}</span>
        {progress.total && (
          <span className="wl-actprop__progress-count">
            {progress.current}/{progress.total}
          </span>
        )}
      </div>
      <div
        className={`wl-actprop__progress-track${percent == null ? ' is-indeterminate' : ''}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={progress.total ?? undefined}
        aria-valuenow={progress.total ? progress.current : undefined}
      >
        <span
          className="wl-actprop__progress-fill"
          style={percent == null ? undefined : { width: `${percent}%` }}
        />
      </div>
      {progress.detail && <div className="wl-actprop__progress-detail">{progress.detail}</div>}
    </div>
  );
}

function taskKindLabel(kind: string): string {
  if (kind === 'batch_pricing') return 'Batch pricing run';
  if (kind === 'risk_run') return 'Risk run';
  if (kind === 'report_job') return 'Report generation';
  if (kind === 'position_pricing') return 'Pricing run';
  if (kind === 'async_agent') return 'Background task';
  return 'Background task';
}

function taskStatusLabel(status: string): string {
  if (status === 'completed_with_errors') return 'completed with issues';
  return status.replaceAll('_', ' ');
}

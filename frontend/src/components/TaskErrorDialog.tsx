import { Modal } from './Modal';
import type { TaskErrorPosition, TaskRun } from '../types';
import './TaskErrorDialog.css';

type Props = {
  task: TaskRun | null;
  open: boolean;
  onClose: () => void;
};

function positionLabel(position: TaskErrorPosition): string {
  const id = position.position_id != null ? `#${position.position_id}` : 'Position';
  const descriptor = [position.underlying, position.product_type].filter(Boolean).join(' ');
  return descriptor ? `${id} · ${descriptor}` : id;
}

function positionReason(position: TaskErrorPosition): string {
  const reasons: string[] = [];
  if (!position.pricing_ok) reasons.push(position.pricing_error || 'Pricing failed');
  if (!position.greeks_ok) reasons.push(position.greeks_error || 'Greeks failed');
  return reasons.join('; ') || 'Unknown error';
}

export function TaskErrorDialog({ task, open, onClose }: Props) {
  const title = task ? `TASK #${task.id} ERRORS` : 'TASK ERRORS';
  const positions = task?.result_payload?.errors?.positions ?? [];
  const isFailed = task?.status === 'failed';

  return (
    <Modal
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title={title}
      layoutKey="task-error"
    >
      <div className="wl-task-error">
        {isFailed ? (
          <pre className="wl-task-error__trace">
            {task?.error || task?.message || 'No error detail available.'}
          </pre>
        ) : positions.length > 0 ? (
          <ul className="wl-task-error__list">
            {positions.map((position, index) => (
              <li className="wl-task-error__item" key={position.position_id ?? index}>
                <span className="wl-task-error__label">{positionLabel(position)}</span>
                <span className="wl-task-error__reason">{positionReason(position)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="wl-task-error__empty">
            {task?.message || 'No error detail available.'} Detailed per-position
            breakdown is unavailable for this task.
          </p>
        )}
      </div>
    </Modal>
  );
}

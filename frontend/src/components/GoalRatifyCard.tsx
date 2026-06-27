import { Button } from './Button';
import type { GoalContract, GoalRunState } from '../lib/goalApi';
import './GoalRatifyCard.css';

type Props = {
  contract: GoalContract;
  state: GoalRunState;
  onRatify: () => void;
  onCancel: () => void;
  busy?: boolean;
};

const STATUS_LABEL: Record<GoalRunState['status'], string> = {
  awaiting_ratification: 'Awaiting your acceptance',
  running: 'Running — grading against your criteria',
  stuck_needs_human: 'Needs you — could not satisfy the criteria',
  satisfied: 'Satisfied — all criteria met',
  cancelled: 'Cancelled',
};

const REASON_LABEL: Record<string, string> = {
  max_iterations_reached: 'reached the revision limit',
  failed: 'the run failed',
  grader_error: 'the grader could not run',
  context_ceiling: 'hit the context ceiling',
};

/** Goal-mode acceptance card (spec §G): shows the framer's draft criteria so the user
 * ratifies ONCE before the autonomous run begins, then reflects the run's status and —
 * when stuck — the reason and the criteria that are still failing. */
export function GoalRatifyCard({ contract, state, onRatify, onCancel, busy }: Props) {
  const awaiting = state.status === 'awaiting_ratification';
  const stuck = state.status === 'stuck_needs_human';

  return (
    <div className={`wl-goalcard wl-goalcard--${state.status}`}>
      <header className="wl-goalcard__head">
        <span className="wl-goalcard__tag">Goal</span>
        <span className="wl-goalcard__status">{STATUS_LABEL[state.status]}</span>
      </header>

      <div className="wl-goalcard__summary">{contract.summary}</div>
      <div className="wl-goalcard__policy">
        {contract.domain_write_policy === 'allowed_by_mode'
          ? 'May change desk state (writes follow the thread mode).'
          : 'Read-only — no desk state will change.'}
      </div>

      <ol className="wl-goalcard__criteria">
        {(contract.criteria ?? []).map((c) => (
          <li key={c.id} className="wl-goalcard__criterion">
            <span className="wl-goalcard__criterion-id">{c.id}</span>
            <span className="wl-goalcard__criterion-text">{c.text}</span>
            <code className="wl-goalcard__criterion-check">{c.check?.type}</code>
          </li>
        ))}
      </ol>

      {stuck && (
        <div className="wl-goalcard__escalation" role="status">
          <div className="wl-goalcard__escalation-reason">
            Stopped because {REASON_LABEL[state.terminal_reason ?? ''] ?? state.terminal_reason}.
          </div>
          {state.failing_criteria && state.failing_criteria.length > 0 && (
            <ul className="wl-goalcard__failing">
              {state.failing_criteria.map((f) => (
                <li key={f.id}>
                  <span className="wl-goalcard__criterion-id">{f.id}</span> {f.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="wl-goalcard__actions">
        {awaiting ? (
          <>
            <Button variant="primary" onClick={onRatify} disabled={busy}>
              Accept &amp; start
            </Button>
            <Button onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          </>
        ) : (
          state.status !== 'satisfied' &&
          state.status !== 'cancelled' && (
            <Button onClick={onCancel} disabled={busy}>
              Cancel goal
            </Button>
          )
        )}
      </div>
    </div>
  );
}

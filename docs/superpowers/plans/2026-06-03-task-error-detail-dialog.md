# Task Error-Detail Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "View errors" button on errored/failed task rows that opens a dialog showing the error detail, including per-position failures for `completed_with_errors` risk runs.

**Architecture:** Populate the existing-but-unused `task_runs.result_payload` JSON column with a structured error summary at risk-run finish time, expose it on `TaskRunOut`, and render it in a reusable Radix `Modal` opened from the Tasks status cell. No DB migration (column already exists). `failed` tasks keep using `task.error`; `completed_with_errors` tasks use `result_payload.errors.positions[]`.

**Tech Stack:** Backend — FastAPI + SQLAlchemy + Pydantic, pytest (`pythonpath=backend`, tests in `tests/`). Frontend — React + TypeScript, Vitest + Testing Library, `@radix-ui/react-dialog` via `components/Modal.tsx`.

Spec: `docs/superpowers/specs/2026-06-03-task-error-detail-dialog-design.md`

---

## File Structure

- `backend/app/services/risk_engine.py` — add `_risk_error_payload(metrics)`; pass it to `mark_task_finished` at the completion call.
- `backend/app/services/task_runner.py` — add optional `result_payload` param to `mark_task_finished`.
- `backend/app/schemas.py` — add `result_payload` to `TaskRunOut`.
- `tests/test_risk_engine.py` — new backend test (append).
- `frontend/src/types.ts` — add `result_payload` + helper types to `TaskRun`.
- `frontend/src/components/TaskErrorDialog.tsx` (new) — the dialog.
- `frontend/src/components/TaskErrorDialog.css` (new) — dialog styles.
- `frontend/src/components/TaskErrorDialog.test.tsx` (new) — dialog tests.
- `frontend/src/routes/Tasks.tsx` — status-cell button + dialog state; remove inline error line.
- `frontend/src/routes/Tasks.css` — status-cell layout + button style.
- `frontend/src/routes/Tasks.test.tsx` — update existing assertion + add button/dialog tests.

---

## Task 1: Backend — populate and expose `result_payload`

**Files:**
- Modify: `backend/app/services/task_runner.py:154-176` (`mark_task_finished`)
- Modify: `backend/app/services/risk_engine.py:566-573` (add helper after `_risk_completion_message`) and `backend/app/services/risk_engine.py:504-509` (finish call)
- Modify: `backend/app/schemas.py:1072-1087` (`TaskRunOut`)
- Test: `tests/test_risk_engine.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_risk_engine.py`:

```python
def test_risk_run_completed_with_errors_records_result_payload(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, TaskRun
    from app.schemas import TaskRunOut
    from app.services import risk_engine
    from app.services.risk_engine import execute_risk_run_task, queue_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "underlying": position.underlying,
                    "product_type": position.product_type,
                    "pricing_ok": False,
                    "pricing_error": "Pricing profile extraction failed: missing vol",
                    "greeks_ok": True,
                    "greeks_error": None,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        risk_engine, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        session.add(pos)
        session.flush()
        run, task = queue_portfolio_risk(
            session, portfolio_id=portfolio.id, method="summary"
        )
        session.commit()
        run_id, task_id, pos_id = run.id, task.id, pos.id

    execute_risk_run_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        assert task.status == "completed_with_errors"
        payload = task.result_payload or {}
        positions = payload.get("errors", {}).get("positions", [])
        assert len(positions) == 1
        assert positions[0]["position_id"] == pos_id
        assert (
            positions[0]["pricing_error"]
            == "Pricing profile extraction failed: missing vol"
        )
        # API contract exposes result_payload
        out = TaskRunOut.model_validate(task)
        assert out.result_payload["errors"]["failed_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/fuxinyao/open-otc-trading && pytest tests/test_risk_engine.py::test_risk_run_completed_with_errors_records_result_payload -v`
Expected: FAIL — `task.result_payload` is `None`, so `positions` is `[]` and the length assertion fails (or `TaskRunOut` has no `result_payload` attribute).

- [ ] **Step 3a: Add `result_payload` param to `mark_task_finished`**

In `backend/app/services/task_runner.py`, change the signature and body of `mark_task_finished`. Replace:

```python
def mark_task_finished(
    session: Session,
    task_id: int,
    *,
    status: str,
    message: str | None = None,
    error: str | None = None,
) -> TaskRun:
    if status not in TERMINAL_TASK_STATUSES:
        raise ValueError(f"Task status is not terminal: {status}")
    task = _require_task(session, task_id)
    task.status = status
    task.finished_at = datetime.utcnow()
    if task.started_at is None:
        task.started_at = task.finished_at
    if task.progress_total and task.progress_current < task.progress_total:
        task.progress_current = task.progress_total
    if message is not None:
        task.message = message
    task.error = error
    _sync_linked_status(session, task, status)
    session.flush()
    return task
```

with:

```python
def mark_task_finished(
    session: Session,
    task_id: int,
    *,
    status: str,
    message: str | None = None,
    error: str | None = None,
    result_payload: dict | None = None,
) -> TaskRun:
    if status not in TERMINAL_TASK_STATUSES:
        raise ValueError(f"Task status is not terminal: {status}")
    task = _require_task(session, task_id)
    task.status = status
    task.finished_at = datetime.utcnow()
    if task.started_at is None:
        task.started_at = task.finished_at
    if task.progress_total and task.progress_current < task.progress_total:
        task.progress_current = task.progress_total
    if message is not None:
        task.message = message
    task.error = error
    if result_payload is not None:
        task.result_payload = result_payload
    _sync_linked_status(session, task, status)
    session.flush()
    return task
```

- [ ] **Step 3b: Add `_risk_error_payload` helper and wire it into the finish call**

In `backend/app/services/risk_engine.py`, add this function immediately after `_risk_completion_message` (after line 573):

```python
def _risk_error_payload(metrics: dict[str, Any]) -> dict[str, Any] | None:
    """Structured per-position failure summary for completed_with_errors tasks.

    Returns ``None`` when no position failed, so successful runs leave
    ``task.result_payload`` empty.
    """
    failing = [
        {
            "position_id": row.get("position_id"),
            "underlying": row.get("underlying"),
            "product_type": row.get("product_type"),
            "pricing_ok": bool(row.get("pricing_ok")),
            "pricing_error": row.get("pricing_error"),
            "greeks_ok": bool(row.get("greeks_ok")),
            "greeks_error": row.get("greeks_error"),
        }
        for row in (metrics.get("positions") or [])
        if not row.get("pricing_ok") or not row.get("greeks_ok")
    ]
    if not failing:
        return None
    return {
        "errors": {
            "kind": "risk_run",
            "failed_count": len(failing),
            "positions": failing,
        }
    }
```

Then in `_execute_risk_run_task`, update the success-path `mark_task_finished` call (lines 504-509). Replace:

```python
        mark_task_finished(
            session,
            task_id,
            status=status,
            message=_risk_completion_message(metrics, status),
        )
```

with:

```python
        mark_task_finished(
            session,
            task_id,
            status=status,
            message=_risk_completion_message(metrics, status),
            result_payload=_risk_error_payload(metrics),
        )
```

(Leave the `except` block's `mark_task_finished` call unchanged — `failed` tasks carry the exception in `task.error`.)

- [ ] **Step 3c: Expose `result_payload` on `TaskRunOut`**

In `backend/app/schemas.py`, in `class TaskRunOut`, add the field after `error` (line 1082). Replace:

```python
    message: str | None = None
    error: str | None = None
    created_at: datetime
```

with:

```python
    message: str | None = None
    error: str | None = None
    result_payload: dict[str, Any] | None = None
    created_at: datetime
```

(`dict` and `Any` are already imported in this module — they're used by other schemas.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/fuxinyao/open-otc-trading && pytest tests/test_risk_engine.py::test_risk_run_completed_with_errors_records_result_payload -v`
Expected: PASS

Then run the whole file to confirm no regressions:
Run: `cd /Users/fuxinyao/open-otc-trading && pytest tests/test_risk_engine.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add backend/app/services/task_runner.py backend/app/services/risk_engine.py backend/app/schemas.py tests/test_risk_engine.py
git commit -m "feat(tasks): record per-position error summary in result_payload"
```

---

## Task 2: Frontend — `TaskErrorDialog` component

**Files:**
- Modify: `frontend/src/types.ts:616-630` (`TaskRun`)
- Create: `frontend/src/components/TaskErrorDialog.tsx`
- Create: `frontend/src/components/TaskErrorDialog.css`
- Test: `frontend/src/components/TaskErrorDialog.test.tsx`

- [ ] **Step 1: Add types**

In `frontend/src/types.ts`, replace the `TaskRun` type (lines 616-630) with:

```ts
export type TaskErrorPosition = {
  position_id: number | null;
  underlying?: string | null;
  product_type?: string | null;
  pricing_ok: boolean;
  pricing_error?: string | null;
  greeks_ok: boolean;
  greeks_error?: string | null;
};

export type TaskResultPayload = {
  errors?: {
    kind?: string;
    failed_count?: number;
    positions?: TaskErrorPosition[];
  };
  [key: string]: unknown;
};

export type TaskRun = {
  id: number;
  kind: 'risk_run' | 'report_job' | string;
  status: 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | string;
  portfolio_id?: number | null;
  risk_run_id?: number | null;
  report_job_id?: number | null;
  progress_current: number;
  progress_total: number;
  message?: string | null;
  error?: string | null;
  result_payload?: TaskResultPayload | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};
```

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/TaskErrorDialog.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { test, expect } from 'vitest';
import { TaskErrorDialog } from './TaskErrorDialog';
import type { TaskRun } from '../types';

const baseTask: TaskRun = {
  id: 7,
  kind: 'risk_run',
  status: 'completed_with_errors',
  portfolio_id: 1,
  risk_run_id: 2,
  report_job_id: null,
  progress_current: 2,
  progress_total: 2,
  message: 'Completed with 1 position issue',
  error: null,
  result_payload: {
    errors: {
      kind: 'risk_run',
      failed_count: 1,
      positions: [
        {
          position_id: 99,
          underlying: '510050.SH',
          product_type: 'snowball',
          pricing_ok: false,
          pricing_error: 'Pricing profile extraction failed: missing vol',
          greeks_ok: true,
          greeks_error: null,
        },
      ],
    },
  },
  created_at: '2026-06-03T01:00:00Z',
};

test('renders nothing when closed', () => {
  render(<TaskErrorDialog task={baseTask} open={false} onClose={() => {}} />);
  expect(screen.queryByText(/510050\.SH/)).not.toBeInTheDocument();
});

test('lists failing positions with reasons for completed_with_errors', () => {
  render(<TaskErrorDialog task={baseTask} open onClose={() => {}} />);
  expect(screen.getByText(/510050\.SH snowball/)).toBeInTheDocument();
  expect(screen.getByText(/missing vol/)).toBeInTheDocument();
});

test('shows exception text for a failed task', () => {
  const failed: TaskRun = {
    ...baseTask,
    status: 'failed',
    error: 'ValueError: boom',
    result_payload: null,
  };
  render(<TaskErrorDialog task={failed} open onClose={() => {}} />);
  expect(screen.getByText(/ValueError: boom/)).toBeInTheDocument();
});

test('falls back to message when payload is absent', () => {
  const noPayload: TaskRun = { ...baseTask, result_payload: null };
  render(<TaskErrorDialog task={noPayload} open onClose={() => {}} />);
  expect(screen.getByText(/Completed with 1 position issue/)).toBeInTheDocument();
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/components/TaskErrorDialog.test.tsx`
Expected: FAIL — `TaskErrorDialog` does not exist (import error).

- [ ] **Step 4: Implement the component**

Create `frontend/src/components/TaskErrorDialog.tsx`:

```tsx
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
```

Create `frontend/src/components/TaskErrorDialog.css`:

```css
.wl-task-error {
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}

.wl-task-error__trace {
  margin: 0;
  padding: var(--gap-2);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--paper-3);
  color: var(--neg);
  font-family: var(--font-numeric);
  font-size: var(--type-num-s-size);
}

.wl-task-error__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}

.wl-task-error__item {
  display: flex;
  flex-direction: column;
  gap: var(--gap-1);
}

.wl-task-error__label {
  font-family: var(--font-ui);
  font-weight: 600;
  color: var(--ink);
}

.wl-task-error__reason {
  font-family: var(--font-ui);
  font-size: var(--type-small-size);
  color: var(--neg);
  overflow-wrap: anywhere;
}

.wl-task-error__empty {
  margin: 0;
  color: var(--ink-2);
  font-family: var(--font-ui);
  font-size: var(--type-small-size);
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/components/TaskErrorDialog.test.tsx`
Expected: PASS (all 4 tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add frontend/src/types.ts frontend/src/components/TaskErrorDialog.tsx frontend/src/components/TaskErrorDialog.css frontend/src/components/TaskErrorDialog.test.tsx
git commit -m "feat(tasks): add TaskErrorDialog for per-task error detail"
```

---

## Task 3: Frontend — wire the button into the Tasks page

**Files:**
- Modify: `frontend/src/routes/Tasks.tsx` (status column render, `TaskCell`, new state + dialog mount)
- Modify: `frontend/src/routes/Tasks.css:57-61` (status-cell layout) and append button style
- Test: `frontend/src/routes/Tasks.test.tsx`

- [ ] **Step 1: Update the existing test and add button/dialog tests**

In `frontend/src/routes/Tasks.test.tsx`, change the imports line (line 2) to add `userEvent`:

```tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Tasks } from './Tasks';
import type { TaskRun } from '../types';
```

Add a fourth task to the `tasks` array (after the failed task #3, before the closing `];`):

```tsx
  {
    id: 4,
    kind: 'risk_run',
    status: 'completed_with_errors',
    portfolio_id: 12,
    risk_run_id: 22,
    report_job_id: null,
    progress_current: 2,
    progress_total: 2,
    message: 'Completed with 1 position issue',
    error: null,
    result_payload: {
      errors: {
        kind: 'risk_run',
        failed_count: 1,
        positions: [
          {
            position_id: 99,
            underlying: '510050.SH',
            product_type: 'snowball',
            pricing_ok: false,
            pricing_error: 'Pricing profile extraction failed: missing vol',
            greeks_ok: true,
            greeks_error: null,
          },
        ],
      },
    },
    created_at: '2026-05-11T08:04:00Z',
    started_at: '2026-05-11T08:04:01Z',
    finished_at: '2026-05-11T08:04:30Z',
  },
```

In the first test (`lists running, completed, and failed task rows...`), REMOVE this line (the error text is no longer rendered inline):

```tsx
    expect(screen.getByText('interrupted')).toBeInTheDocument();
```

Add these new tests inside the `describe('Tasks', ...)` block:

```tsx
  it('shows a View errors button only for failed and completed_with_errors rows', () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);
    const buttons = screen.getAllByRole('button', { name: /view errors/i });
    expect(buttons).toHaveLength(2);
  });

  it('opens a dialog with the exception text for a failed task', async () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);
    const buttons = screen.getAllByRole('button', { name: /view errors/i });
    await userEvent.click(buttons[0]); // task #3 (failed) is the first error row
    expect(await screen.findByText('interrupted')).toBeInTheDocument();
  });

  it('opens a dialog listing failing positions for a completed_with_errors task', async () => {
    render(<Tasks tasks={tasks} loading={false} error={null} />);
    const buttons = screen.getAllByRole('button', { name: /view errors/i });
    await userEvent.click(buttons[1]); // task #4 (completed_with_errors)
    expect(await screen.findByText(/510050\.SH snowball/)).toBeInTheDocument();
    expect(screen.getByText(/missing vol/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/routes/Tasks.test.tsx`
Expected: FAIL — no `View errors` button exists yet (`getAllByRole` finds 0, and the first test may still pass after the line removal).

- [ ] **Step 3: Implement the button, state, and dialog in `Tasks.tsx`**

In `frontend/src/routes/Tasks.tsx`:

a) Update imports — change line 1 and add the dialog import after the `Empty` import:

```tsx
import { useMemo, useState } from 'react';
```

and after `import { Empty } from '../components/Empty';` add:

```tsx
import { TaskErrorDialog } from '../components/TaskErrorDialog';
```

b) Add the error-status set next to `ACTIVE_STATUSES` (after line 17):

```tsx
const ERROR_STATUSES = new Set(['failed', 'completed_with_errors']);
```

c) Inside the `Tasks` component, add dialog state right after the function opening (before `runningCount`):

```tsx
  const [errorTask, setErrorTask] = useState<TaskRun | null>(null);
```

d) Change the `status` column `render` to use a new `StatusCell` that includes the button. Replace:

```tsx
      render: (task) => (
        <span className="wl-tasks__status-cell">
          <StatusBadge status={task.status} />
        </span>
      ),
```

with:

```tsx
      render: (task) => <StatusCell task={task} onViewErrors={setErrorTask} />,
```

(The `columns` `useMemo` keeps its `[]` deps — `setErrorTask` from `useState` is referentially stable and is exempt from exhaustive-deps.)

e) Mount the dialog. Change the component's returned fragment so the closing `</>` is preceded by the dialog. Replace:

```tsx
      ) : (
        <Table
          className="wl-tasks__table"
          columns={columns}
          rows={tasks}
          rowKey={(task) => task.id}
        />
      )}
    </>
  );
}
```

with:

```tsx
      ) : (
        <Table
          className="wl-tasks__table"
          columns={columns}
          rows={tasks}
          rowKey={(task) => task.id}
        />
      )}
      <TaskErrorDialog
        task={errorTask}
        open={errorTask !== null}
        onClose={() => setErrorTask(null)}
      />
    </>
  );
}
```

f) Remove the inline error line from `TaskCell`. Replace:

```tsx
function TaskCell({ task }: { task: TaskRun }) {
  return (
    <div className="wl-tasks__task">
      <div className="wl-tasks__primary">#{task.id} {labelKind(task.kind)}</div>
      <div className="wl-tasks__secondary">{task.message ?? 'No message'}</div>
      {task.error && <div className="wl-tasks__error-text">{task.error}</div>}
    </div>
  );
}
```

with:

```tsx
function TaskCell({ task }: { task: TaskRun }) {
  return (
    <div className="wl-tasks__task">
      <div className="wl-tasks__primary">#{task.id} {labelKind(task.kind)}</div>
      <div className="wl-tasks__secondary">{task.message ?? 'No message'}</div>
    </div>
  );
}
```

g) Add the `StatusCell` component immediately after `StatusBadge` (after its closing `}` near line 137):

```tsx
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
```

- [ ] **Step 4: Update `Tasks.css` for the status-cell layout and button**

In `frontend/src/routes/Tasks.css`, replace the `.wl-tasks__status-cell` rule (lines 57-61):

```css
.wl-tasks__status-cell {
  min-width: 0;
  width: 100%;
  overflow: hidden;
}
```

with:

```css
.wl-tasks__status-cell {
  min-width: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--gap-1);
}

.wl-tasks__error-button {
  appearance: none;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: var(--neg);
  font-family: var(--font-ui);
  font-size: var(--type-small-size);
  text-decoration: underline;
}

.wl-tasks__error-button:hover {
  opacity: 0.8;
}
```

(The now-unused `.wl-tasks__error-text` rule at lines 76-83 can be left in place or deleted; deleting is cleaner since its only consumer was removed.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/routes/Tasks.test.tsx`
Expected: PASS (original test + 3 new tests).

Then typecheck the touched frontend files:
Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/fuxinyao/open-otc-trading
git add frontend/src/routes/Tasks.tsx frontend/src/routes/Tasks.css frontend/src/routes/Tasks.test.tsx
git commit -m "feat(tasks): add View errors button opening the error dialog"
```

---

## Final verification

- [ ] **Run the full relevant suites**

```bash
cd /Users/fuxinyao/open-otc-trading && pytest tests/test_risk_engine.py -q
cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/routes/Tasks.test.tsx src/components/TaskErrorDialog.test.tsx
```

Expected: all green.

- [ ] **Manual smoke (optional)** — start the app, open the Tasks page, click "View errors" on a `completed_with_errors` row, confirm the dialog lists the failing position and reason; click it on a `failed` row, confirm the exception text shows.

---

## Notes / decisions carried from the spec

- No Alembic migration — `task_runs.result_payload` already exists (`models.py:1373`, `database.py:288`).
- Report-job tasks that finish `completed_with_errors` are NOT populated by this change; their dialog falls back to `task.message` + the "unavailable" note (covered by the `falls back to message` dialog test).
- The faint inline `task.error` line is removed from the Task cell; its content now lives in the dialog (the failed-task dialog test asserts `interrupted` appears only after clicking).

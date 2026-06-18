# Task error-detail dialog — design

Date: 2026-06-03
Status: Approved (design), pending implementation plan

## Problem

The Tasks page lists async tasks with a status badge. Two statuses indicate
something went wrong:

- `failed` — the task raised; the full exception string is stored in
  `task.error` and already returned by `/api/tasks`.
- `completed_with_errors` — the run finished but one or more positions could not
  be priced or had a Greeks failure (e.g. risk runs #38 / #37 in the screenshot,
  "Completed with 1 position issue"). Here `task.error` is **empty**. The real
  detail lives in the linked `RiskRun.metrics["positions"]` rows where
  `pricing_ok` or `greeks_ok` is false — and that is **not** returned by
  `/api/tasks`.

Today the only error affordance is a faint inline `task.error` line in the Task
cell, which shows nothing useful for the `completed_with_errors` case. Users
have no way to see *which* positions failed and *why*.

## Goal

Add a "View errors" button on error/failed task rows that opens a dialog showing
the error detail. For `completed_with_errors`, the dialog must show the failing
positions and their reasons — which means surfacing that detail through the API.

## Approach (chosen)

Populate the already-existing-but-unused `task_runs.result_payload` JSON column
with a structured error summary at risk-run finish time, expose it on
`TaskRunOut`, and render it client-side. This keeps the dialog self-contained
(no second fetch of the risk run) and follows the `result_payload`-as-outcome
convention already used by `ReportJob` and `position_pricer`.

No Alembic migration is required: the `result_payload` column already exists on
`task_runs` (added via `database.py` lightweight migration; model field at
`models.py:1373`). It is currently never populated for risk-run tasks.

## Data shape

A risk metrics position row (`quantark.py:_risk_row`) carries:
`position_id`, `source_trade_id`, `underlying`, `product_type`, `pricing_ok`,
`pricing_error`, `greeks_ok`, `greeks_error` (no human label field).

The error summary stored in `result_payload` (only when there are issues):

```json
{
  "errors": {
    "kind": "risk_run",
    "failed_count": 1,
    "positions": [
      {
        "position_id": 1234,
        "underlying": "510050.SH",
        "product_type": "snowball",
        "pricing_ok": false,
        "pricing_error": "Pricing profile extraction failed: ...",
        "greeks_ok": false,
        "greeks_error": null
      }
    ]
  }
}
```

A position is "failing" iff `not pricing_ok or not greeks_ok`. The display label
is composed client-side as `#{position_id} · {underlying} {product_type}`.

## Backend changes

1. **`services/risk_engine.py`** — add a helper `_risk_error_payload(metrics)`
   that returns the `{"errors": {...}}` dict above from the failing rows, or
   `None` when there are none. At the `completed_with_errors` finish call, pass
   this payload through to `mark_task_finished`.
2. **`services/task_runner.py`** — add an optional
   `result_payload: dict | None = None` parameter to `mark_task_finished`; when
   provided, assign `task.result_payload = result_payload`. (Leave the `failed`
   path as-is — the exception already lives in `task.error`.)
3. **`schemas.py`** — add `result_payload: dict[str, Any] | None = None` to
   `TaskRunOut`.

## Frontend changes

4. **`types.ts`** — add `result_payload?: Record<string, unknown> | null` to the
   `TaskRun` type (loosely typed; the dialog narrows it).
5. **`components/TaskErrorDialog.tsx`** (new) — reuses the existing `Modal`.
   - `failed` → render `task.error` in a monospace block (fallback message if
     somehow empty).
   - `completed_with_errors` → render `result_payload.errors.positions[]` as a
     list: composed label + the non-null reason(s) (`pricing_error`,
     `greeks_error`). Fallback to `task.message` if the payload is absent
     (e.g. a task finished before this feature shipped).
6. **`routes/Tasks.tsx`** — in the **Status cell** (placement A, under the
   badge), render a small "View errors" button only when status is `failed` or
   `completed_with_errors`. Track the selected task in `Tasks` state; the dialog
   opens for that task. Remove the now-redundant faint inline `task.error` line
   from the Task cell (its content moves into the dialog).

## Error / edge handling

- Button is absent for `completed`, `queued`, `running` rows.
- `completed_with_errors` task with no `result_payload` (older tasks): dialog
  shows `task.message` plus a note that detailed breakdown is unavailable.
- `result_payload` present but malformed/empty `positions`: dialog falls back to
  the message; no crash (defensive narrowing).

## Testing

- **`Tasks.test.tsx`**: button renders only for `failed` / `completed_with_errors`
  rows; clicking opens the dialog; failed dialog shows `task.error`;
  completed-with-errors dialog lists the failing position label + reason.
- **Backend (risk_engine test)**: a risk run whose metrics contain a position
  with `pricing_ok=false` finishes with status `completed_with_errors` **and**
  `task.result_payload["errors"]["positions"]` contains that position with its
  `pricing_error`. (Use a non-default error string so the assertion is not
  vacuous.)

## Out of scope (YAGNI)

- No retry/re-run action from the dialog.
- No populating `result_payload` for successful runs.
- No new API endpoint (reuse `/api/tasks`).

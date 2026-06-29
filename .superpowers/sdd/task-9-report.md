# Task 9 Report: ExtractionRunStore

## Status
COMPLETE — all 6 tests pass.

## TDD Red/Green

### RED
```
python -m pytest tests/test_memory_runs.py -v
```
Output (truncated):
```
ERROR tests/test_memory_runs.py
ModuleNotFoundError: No module named 'app.services.deep_agent.memory.runs'
1 error during collection
```

### GREEN
```
python -m pytest tests/test_memory_runs.py -v
```
Output:
```
collected 6 items
tests/test_memory_runs.py ......   [100%]
6 passed in 0.34s
```

## Files Changed
- `tests/test_memory_runs.py` — new test file (6 tests covering all state-machine transitions)
- `backend/app/services/deep_agent/memory/runs.py` — new implementation file

## State Machine Self-Review

| Transition | Expected | Verified |
|---|---|---|
| absent → `enqueue_run` | insert pending, return True | test_enqueue_inserts_pending ✓ |
| `pending` → `enqueue_run` | stays pending, return True | (covered implicitly by test_eligible_runs) |
| `succeeded` → `enqueue_run` | no-op, return False | test_succeeded_is_noop ✓ |
| `failed` (attempts < max) → `enqueue_run` | reset pending, return True | test_failed_under_max_reenqueues ✓ |
| `failed` (attempts == max) → `enqueue_run` | terminal, return False | test_failed_at_max_is_terminal ✓ |
| `mark_succeeded` | status=succeeded, cursor advanced | test_succeeded_is_noop ✓ |
| `mark_failed` | status=failed, attempts += 1 | test_failed_at_max_is_terminal ✓ |
| `eligible_runs` | returns pending only (not succeeded) | test_eligible_runs ✓ |

`run_key` scheme: `session_run_key(5)=="session:5"`, `correction_run_key(5,9)=="corr:5:9"` — verified by `test_run_keys`.

## Concerns
None. The implementation follows the brief verbatim. The `eligible_runs` query correctly uses a compound filter: `status IN (pending, failed)` AND `(status==pending OR attempts<max)`, which avoids returning terminal-failed rows.

---

## Coverage Hardening (2026-06-30)

Reviewer-identified gaps addressed in `tests/test_memory_runs.py`. No implementation changes required — all bugs the new tests could reveal were absent.

### Changes

1. **`test_enqueue_inserts_pending`** — added second `enqueue_run` call on already-pending spec; asserts it returns `True` (idempotent re-enqueue path).

2. **`test_failed_at_max_is_terminal`** — reworked to drive all transitions through `enqueue_run` instead of directly writing `row.status`. Now asserts `True` on iter-1 and iter-2 resets, `False` on the terminal iter.

3. **`test_eligible_runs`** — expanded from 2-run succeeded-exclusion check to a 4-run fixture:
   - spec 1: pending → present
   - spec 2: failed-below-max (attempts=1) → present
   - spec 3: succeeded → absent
   - spec 4: terminal-failed (attempts=3, confirmed by `enqueue_run` returning `False`) → absent
   Result assertion: `eligible_keys == {"session:1", "session:2"}`. This test would fail if the second `eligible_runs` filter were removed or simplified to `status.in_(("pending",))`.

4. **`test_mark_succeeded_null_cursor_guard`** (new) — verifies that a second `mark_succeeded(None)` call does not overwrite a previously set `last_extracted_message_id`.

### Result
```
python -m pytest tests/test_memory_runs.py -v
7 passed in 0.38s
```

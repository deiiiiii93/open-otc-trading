# Task 12d Report: Per-chat serialization + backpressure

## Summary

Implemented per-chat asyncio serialization and a bounded-queue backpressure
mechanism for the Dispatcher message path.  Card-action events continue to
bypass the lane entirely.

---

## Commands run

```
# TDD — fail first
cd /Users/fuxinyao/open-otc-trading/.claude/worktrees/im-message-gateway
python -m pytest tests/gateway/test_dispatch_backpressure.py -q

# After implementation — all 4 new tests pass
python -m pytest tests/gateway/test_dispatch_backpressure.py -v

# Full suite — no regressions
python -m pytest tests/gateway/ -q
```

## Test output

```
162 passed, 3 warnings in 6.57s
```

Pre-implementation: 158 passing.  Post-implementation: 162 passing.
All 4 new backpressure tests pass; 0 regressions.

---

## Files changed / created

| File | Change |
|---|---|
| `backend/app/services/gateway/dispatch.py` | Added `_Lane` dataclass, `_LaneKey` type alias, `_lanes` dict + `_monotonic` to `__init__`, new `_run_in_lane()` method; `handle()` now routes message path through `_run_in_lane` |
| `tests/gateway/test_dispatch_backpressure.py` | New test file — 4 tests covering serial ordering, overflow drop, age-cap drop, card-action bypass |
| `.superpowers/sdd/task-12d-report.md` | This file |

---

## Design decisions

### Depth / drop-counting semantics

`_Lane.depth` counts **all** turns currently associated with this lane: the
one running (holding the lock) plus all waiting to acquire it.

- A lane is created lazily on first admission; depth starts at 0 before
  admission.
- **Drop-newest check (pre-admission):** `if lane.depth > max_queued` → drop.
  With `max_queued=1`:
  - Turn 1 admitted: depth becomes 1.
  - Turn 2 arrives (current depth=1, 1 > 1 = False) → admitted; depth=2.
  - Turn 3 arrives (current depth=2, 2 > 1 = True) → dropped.
  This means at most 1 running + 1 waiting can be admitted simultaneously.
- **Increment then lock:** depth is incremented BEFORE acquiring the lock so
  that the overflow guard is correct for concurrent arrivals.
- **Age-cap check (post-lock):** after acquiring the lock, `monotonic() -
  enqueued_at > max_age_s` → drop with "too old" notice.
- **Finally block:** always decrements depth and removes the lane entry when
  depth returns to 0 (prevents unbounded growth of `_lanes`).

### Injectable monotonic clock

`Dispatcher.__init__` accepts a keyword-only `monotonic: Callable[[], float]`
parameter (default `time.monotonic`).  Tests inject a controllable function to
make age-cap behaviour deterministic without sleeping.

### Card-action bypass

`handle()` calls `_run_in_lane()` only for `kind == "message"`.  Card-actions
call `_handle_card_action_async()` directly and are never queued behind turns.

---

## Concerns / carry-forwards

- The drop-overflow check is not atomic with the increment: two concurrent
  coroutines could both see `depth == max_queued` and both be admitted.
  For the asyncio single-threaded event loop this is fine (no preemption
  between the check and the increment), but would need a mutex in a
  threaded executor.
- `_lanes` grows entries lazily and is cleaned up eagerly (depth → 0 removes
  the entry).  A very short burst followed by a lull leaves no residual memory.
- If `_handle_message_async` raises an unhandled exception, the `finally` block
  in `_run_in_lane` still decrements depth and cleans up the lane, so the lane
  is never permanently stuck.

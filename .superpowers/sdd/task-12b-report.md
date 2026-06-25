# Task 12b Report — Dispatcher message path + refusals

## Status
DONE

## Commit
(see git log after commit)

## Files changed
- `backend/app/services/gateway/dispatch.py` — extended with `_handle_message()` / `_handle_message_async()` async implementation; added `asyncio`, `identity_svc`, `OutboundMessage` imports.
- `tests/gateway/test_dispatch_message.py` — NEW; 13 tests covering all required paths.
- `tests/gateway/test_dispatch_dedup.py` — updated `_make_dispatcher` helper to use `FakeConnector()` instead of `None` (needed because 12b now routes `kind=="message"` into the message path which calls `connector.send_message`).

## Test summary
```
python -m pytest tests/gateway/ -q
150 passed, 3 warnings in 6.04s
```

13 new tests in `test_dispatch_message.py`, all green. 0 regressions.

## Refusal copy
| Path | Text sent |
|---|---|
| Group chat | "The agent is only available in direct messages." |
| Unbound (no code) | "You're not linked yet. Send your linking code to enroll." |
| Enrollment success | "Enrolled — you can now message the desk agent." |
| Enrollment fail (invalid/expired code) | "Invalid or expired linking code." |
| Non-text / blank text | "I can only read text messages." |
| Over-length text | "Message too long." |

## Idempotency key suffixes
Each refusal path appends a distinct suffix to `inbound.provider_event_id`:
- `:refuse-group`
- `:refuse-unbound`
- `:enroll-ok`
- `:enroll-fail`
- `:help-text`
- `:refuse-toolong`

## Implementation notes

### Async boundary
`handle()` stays synchronous (preserves 12a interface); `_handle_message()` wraps `asyncio.run()` around the async coroutine `_handle_message_async()`. This keeps the dedup tests unchanged except for the `connector=None` → `FakeConnector()` stub swap (connector is now required for the message path).

### Execution order (as specified)
1. Group refuse → terminal
2. Resolve identity via `identity.active_binding`
3. Enroll if unbound + code-shaped text → `identity.redeem_code` → success (commit + confirm, terminal) or failure (refusal, terminal)
4. Unbound refuse → terminal
5. Text validation (None → help; blank → help; too-long → refusal) → terminal
6. Turn → `bridge.thread_for` + commit; `bridge.submit_turn` + `renderer.render_turn` → terminal

### Terminal-state invariant
Every branch calls `_finish_inbound(session, inbound)` + `session.commit()` before returning. Verified by `test_*_dedup_row_done` tests.

### `kind != "message"` seam
The `else` branch in `handle()` (for `card_action`) still calls `_finish_inbound + commit` directly — 12c stub preserved.

## Concerns
None. All paths exercise dedup finish. Code shaped as a valid linking code in a group chat is refused at step 1 (group refuse) before identity resolution — correctly DM-only.

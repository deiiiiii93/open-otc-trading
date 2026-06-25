# Task 5: Identity & Enrollment Service — Report

## Status
COMPLETE — all tests passing (GREEN).

## TDD Cycle

### RED
- Created `backend/tests/gateway/conftest.py` with `db_session` fixture (in-memory SQLite).
- Created `backend/tests/gateway/test_identity.py` with 9 tests covering:
  - Brief-required: bind→transfer supersedes, expired code rejected, invalid persona rejected.
  - Extra: revoke idempotency, reused-code rejection, `is_code_shaped`, `KNOWN_PERSONAS` membership, `active_binding` returns None when absent, code structure shape.
- Initial run: 1 ImportError (module not yet created) — confirmed RED.

### GREEN
- Created `backend/app/services/gateway/identity.py`.
- All 9 tests pass; full gateway suite 32/32 green.

## Files
- Created: `backend/app/services/gateway/identity.py`
- Created: `backend/tests/gateway/test_identity.py`
- Created: `backend/tests/gateway/conftest.py`

## Transaction Order Verification

The partial unique index `uq_gateway_binding_active` on `(provider, external_account_id, workspace_id) WHERE status='active'` allows at most one active row per identity.

In `redeem_code`, the transfer path is:
1. SELECT + validate `GatewayLinkingCode` (unexpired, unredeemed).
2. **REVOKE** old active binding → `status='revoked'`, `session.flush()`.
3. **INSERT** new active binding with `supersedes_binding_id = old.id`, `session.flush()`.
4. Mark code `redeemed_by_binding_id = new.id`, flush.
5. Write audit event.

The flush after step 2 ensures the unique constraint is satisfied before the INSERT in step 3. `test_redeem_binds_then_transfer_supersedes` exercises this path directly and would fail with `IntegrityError` if the order were wrong.

## Audit Events
- `gateway.bound` — first binding for an identity (no prior active row).
- `gateway.transferred` — identity already bound but switching to a different persona.
- `gateway.rebound` — identity already bound, re-linking same persona.

## KNOWN_PERSONAS
Sourced from `app.services.deep_agent.persona_domains.PERSONA_WORKFLOW_DOMAINS.keys()` → `{"trader", "risk_manager", "high_board"}`.

## Concerns
None. SQLite note acknowledged: `FOR UPDATE` is a no-op on SQLite; the surrounding transaction + conditional update on the code row is sufficient.

---

## Fix Pass (post-review)

### Important: atomic conditional code-claim (Postgres-safe)
Replaced the plain `code_row.redeemed_by_binding_id = new_binding.id` assignment
with a guarded atomic UPDATE that only succeeds while the code is still
unredeemed:

```python
result = session.execute(
    update(GatewayLinkingCode)
    .where(GatewayLinkingCode.code == code,
           GatewayLinkingCode.redeemed_by_binding_id.is_(None))
    .values(redeemed_by_binding_id=new_binding.id)
)
if result.rowcount == 0:
    savepoint.rollback()
    return None
```

Reordered the transaction so revoke + insert + claim run inside a single
`session.begin_nested()` SAVEPOINT. On `rowcount == 0` (lost race / already
redeemed) the savepoint is rolled back, undoing BOTH the old-binding revoke and
the new-binding insert — **no partial side effects**. The step-1 read is now
only a cheap pre-filter; the guarded UPDATE is the concurrency authority. The
revoke-before-insert ordering (partial-unique-index safety) is preserved inside
the savepoint. Audit is written only after a successful claim. SQLite tests
still pass (single-writer); logic is now Postgres-safe.

### Minor: test coverage
1. Added `test_audit_events_persisted_for_bind_transfer_rebound` — asserts
   persisted `AuditEvent.event_type` is `gateway.bound` (first bind),
   `gateway.rebound` (re-link same persona), `gateway.transferred` (persona
   switch), each with `actor == "desk_user"`. Closes the zero-coverage rebound
   audit path.
2. Renamed `test_reused_code_rejected` -> `test_already_redeemed_code_rejected`
   (accurate name) and strengthened it to assert the losing identity is NOT
   bound (rollback leaves no partial side effects).

### Test command + result
```
cd backend && pytest tests/gateway/test_identity.py -v
```
Result: **10/10 passing** in test_identity.py; full gateway suite **33/33 passing**.

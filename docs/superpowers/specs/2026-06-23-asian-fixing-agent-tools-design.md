# Asian Fixing Agent Tools + Skill — Design

**Date:** 2026-06-23
**Status:** Approved (design), pre-implementation
**Branch:** `feat/asian-fixing-tools` (worktree off `main` @ `1c42a6b`)
**Related:** completes the Asian-option effort — task #13 (`docs/.../specs/2026-06-23-asian-termsheet-pricing-wiring-design.md`) shipped the fixing *services* endpoint-only; this exposes them to agents.

## 1. Problem

The Asian fixing lifecycle has two persisted-write services that today are reachable **only over HTTP**, with no agent-facing tool and no routing skill:

- `generate_asian_fixing_schedule` (`positions.py:655`) — plants informational `fixing` lifecycle events, one per SSE-business-day averaging date.
- `capture_due_asian_fixings` (`positions.py:762`) — snapshots the immutable close print into `product_kwargs.observation_records` for each due (past) observation.

The deck agent can read an Asian schedule (`get_asian_schedule`) but cannot *set up the fixing calendar* or *lock in a due fixing*. This was a deliberate deferral when task #13 shipped (avoiding the skills-catalog test coupling); the user has now opted in to paying that cost.

## 2. Goal

Expose both fixing operations as agent tools and add one dedicated routing skill, with **zero change to the reviewed correctness** of the underlying services (row locks, close-only capture, idempotent regeneration, malformed-record handling all stay exactly as merged).

Non-goals: no new math, no new endpoint, no UI, no change to how pricing consumes `observation_records`.

## 3. Architecture

Two thin `@tool` wrappers in `backend/app/tools/positions.py`, each delegating to the existing service. The services already self-scope their own transaction, so the wrappers carry no session/commit logic of their own beyond invoking the service correctly.

### 3.1 Tools

| Tool name | Delegates to | Gate | Input | Output |
|---|---|---|---|---|
| `generate_asian_fixing_schedule` | `positions_svc.generate_asian_fixing_schedule` | `ToolGroup.DOMAIN_WRITE` | `position_id?`, `source_trade_id?`, `portfolio_id?` | `{position_id, events_created}` |
| `capture_asian_fixings` | `positions_svc.capture_due_asian_fixings` | `ToolGroup.DOMAIN_WRITE` | `position_id`, `portfolio_id?`, `as_of?` | `{position_id, captured}` |

Both are persisted-write actions (lifecycle-event inserts / `product_kwargs` mutation), so both carry `@capability_gated(group=ToolGroup.DOMAIN_WRITE)` above `@tool(...)`, matching `settle_position_tool` / `mark_knockout_tool`. `actor="agent"` for the generate tool (matches sibling write tools; the endpoint uses `desk_user`).

The tools resolve a position the same way the agent already addresses positions elsewhere: `position_id` preferred, with optional `source_trade_id` + `portfolio_id` guards forwarded to the service (generate already accepts all three; capture takes `position_id` + optional `portfolio_id`).

### 3.2 Service tweak (backward-compatible)

`capture_due_asian_fixings` is currently *injected-only*: `session` is a required positional and the body calls only `session.flush()` (commit deferred to `Depends(get_db)`). To let a stateless tool call it without owning a transaction:

- Change the first parameter's annotation `session: Session` → `session: Session | None` (still positional — **no existing caller changes**: endpoint, booking eager-capture, and tests all pass a real session positionally).
- Wrap the body in the module's `_session_scope(session)` (same helper `generate_asian_fixing_schedule` uses).
- When `session is None` (the self-scoped path, i.e. the tool), call `sess.commit()` after the flush so the snapshot persists. When a session was injected, behavior is unchanged (no commit — caller owns it). `_session_scope` documents "write paths commit explicitly", so this matches the module convention.

The `with_for_update()` row lock and all capture logic stay byte-identical; only the transaction boundary becomes self-managing when uninjected.

### 3.3 Registration

- `backend/app/tools/__init__.py`: import both new tool symbols from `.positions` and add them to the exported `__all__` list under the persisted-action / HITL-gated section (next to `settle_position_tool`, `mark_knockout_tool`).
- `backend/app/services/agents.py`: add `"generate_asian_fixing_schedule"` and `"capture_asian_fixings"` to the `DEEP_AGENT_TOOL_NAMES` frozenset (strict allowlist — a tool absent here is silently dropped from the deep agent).

### 3.4 Skill

New `backend/app/skills/workflows/positions/asian-fixings/SKILL.md`:

- Frontmatter: `name: asian-fixings`, `domain: positions`, `workflow_type: write`, `write_actions: true`, `confirmation_required: true`, `required_context: [portfolio_id, position_id]`, `allowed_envelopes` mirroring sibling position write skills (`desk_workflow`), plus `success_criteria`.
- `routing:` frontmatter block (list of `{request, persona}`) so the orchestrator routes fixing requests here — e.g. request "Set up or update the Asian fixing calendar, or lock in a due fixing", persona `trader`.
- Body (≤ ~500 tokens) procedure:
  1. `get_asian_schedule(position_id=…)` to read the averaging schedule and current captured state.
  2. `generate_asian_fixing_schedule(position_id=…, portfolio_id=…)` to (re)plant the informational fixing-event calendar. Idempotent — re-running cancels prior active `fixing` events first.
  3. `capture_asian_fixings(position_id=…, portfolio_id=…)` to snapshot the close for every observation whose date has passed and is not yet captured. Idempotent; never overwrites an existing fixing.
  - Note: capture requires a `close` MarketQuote on the underlying for the observation date; report the captured count and any still-uncaptured past dates so the user knows pricing falls back to a coarse average until they are captured.

## 4. Test Coupling (the accepted cost)

Adding a workflow skill trips exact-count / exact-set assertions. Confirmed updates:

- `tests/test_skills_catalog_v2.py` — `len(catalog) == 23` → `24`; `len(catalog) == 25` → `26`; add `"asian-fixings"` to any exhaustive membership list it asserts.
- `tests/test_routing_table.py` — extend `OLD_TABLE_ROWS` with the new routing triple so the `len(rows)` / `len(lines)` assertions hold.
- Subset checks in `tests/test_workflow_skills_phase3.py` and `tests/test_remaining_workflow_skills_phase3.py` use `<=`, so additions are safe (verify, don't pre-edit).
- `tests/test_reference_docs.py` is an exact set of *reference* docs only — untouched unless a reference doc is added (it is not).

## 5. New Tests — `tests/test_asian_fixing_tools.py`

- **generate tool**: creates one `fixing` event per averaging date for an Asian position; returns `{events_created: N}`; rejects a non-Asian position (`ValueError`); honors `portfolio_id` guard.
- **capture tool**: with a `close` MarketQuote on a past observation date, captures it into `observation_records.observed_price` and returns `{captured: 1}`; idempotent (second call returns `{captured: 0}`, price unchanged); a past date with no quote stays uncaptured; portfolio mismatch raises `LookupError`.
- **self-scoped commit**: capture called with `session=None` (the tool path) persists across a fresh session (proves the new commit boundary).
- **registration**: both tool names are in `DEEP_AGENT_TOOL_NAMES` and importable from `app.tools`.
- **skill well-formedness**: `asian-fixings/SKILL.md` frontmatter parses, `name` matches dir, body within size budget.

## 6. Risks

- **Catalog count drift** — counts elsewhere may also assert; run the full suite, fix every count the new skill trips (don't guess; let red tests enumerate).
- **Commit boundary** — the self-scoped commit must fire *only* when `session is None`; an unconditional commit would break the injected callers' transaction ownership. Pinned by the self-scoped-commit test plus the unchanged endpoint/booking tests.
- **Gate semantics** — both tools must be HITL-gated like sibling writes; a missing gate would let the agent mutate positions without confirmation.

## 7. Process

Worktree off `main`; TDD per task; zenmux-codex-review-loop (GPT-5.5 xhigh, ≤3 loops) as the independent reviewer gate at the spec, plan, and post-implementation stages; fast-forward `main` when green.

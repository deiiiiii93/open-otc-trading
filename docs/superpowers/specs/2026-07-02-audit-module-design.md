# Audit Module — dangerous-action audit trail + Audit page

**Date:** 2026-07-02
**Status:** Draft — pending user review
**Scope:** Backend capture middleware + `agent_action_audits` table (migration 0042) + read-only `/api/audit` router + frontend **Audit** page.

> **Note on autonomy:** the user was away during the clarifying-question phase, so the
> decisions marked *(default — overridable)* below were taken by best judgment against
> the stated requirement ("records of dangerous actions taken by the LLMs … operations
> that save, edit, delete data in DB or any persistent artifacts. Even actions from
> YOLO mode need recorded"). Any of them can be flipped at review with localized impact.

---

## 1. Problem

The desk has a full span-level transcript viewer (`/tracing`), but no **action-shaped,
write-only, always-on** record of what agents actually *did* to persistent state. Today:

- `audit_events` (`models.py::AuditEvent`, `services/audit.py::record_audit`) records
  coarse turn-level events (chat messages, HITL confirm/dismiss, booking events) — it
  never sees individual tool executions, and has no read API or UI.
- The capability gate emits structured denial signals into the in-memory
  `__runtime_signals__` sink, which drive envelope escalation but are **never persisted**.
- Under YOLO/headless mode (`interrupt_on_config` returns `{}`), a write tool executes
  with **no per-action record anywhere** except optional tracing spans (which can be
  disabled and live outside the business DB).

For a financial desk this is the compliance gap: bookings, portfolio edits, deletes,
artifact writes, and async dispatches must leave a durable, queryable trail regardless
of execution mode.

## 2. What counts as auditable *(default — overridable)*

Classification reuses the authoritative taxonomy already proven in
`fanout_readonly.py` — **not** a new heuristic list:

| Class | Rule | Examples |
|---|---|---|
| Domain write | `__capability_group__ == ToolGroup.DOMAIN_WRITE` | `book_position`, RFQ/portfolio edits, memory writes |
| Async dispatch | `__capability_group__ == ToolGroup.ASYNC_DISPATCH` | `start_async_agent` |
| FS/shell write | tool name ∈ `{write_file, edit_file, execute}` (deepagents built-ins, ungated) | report scaffolding |
| Artifact write | `run_python` with `writes_artifacts=True` (argument-aware) | report artifacts |

**Excluded:** `PAGE_ACTION` (ephemeral UI navigation / reply-option proposals — nothing
persists), all read groups. Note `FanoutReadOnlyMiddleware` *does* block `PAGE_ACTION`
in fan-outs; the shared classifier therefore returns the *class*, and each consumer
picks its own group set (see §5.1).

## 3. Approaches considered

**A. Dedicated `AuditTrailMiddleware` + typed business-DB table (chosen).**
A `wrap_tool_call` middleware inserted just inside `ToolErrorBoundaryMiddleware` in
both the orchestrator stack (`orchestrator.py::_agent_middleware`) and every persona
stack (`personas.py::all_personas`) — the exact registration pattern
`FanoutReadOnlyMiddleware` uses, which is proven to fire inside persona subagents.
Sees success, error, and denial for every tool call in every mode. Records to a new
typed `agent_action_audits` table in the business DB (Alembic-managed, FK-able,
filterable).

**B. Persist from the `capability_gated` decorator + runtime-signal sink.**
The decorator fires wherever a gated tool is invoked (even outside a graph), but it
cannot see the ungated FS built-ins or `run_python` args, and sink-flush-at-turn-end
loses records if the turn crashes — the worst failure mode for an audit log. Rejected
as the primary channel; the middleware inherits its taxonomy instead.

**C. Derive the Audit page from existing `trace_runs` spans.**
Zero new capture code, but tracing is optional (off in tests, configurable off in
prod), span-shaped rather than action-shaped, has no denial/HITL semantics, and lives
in a separate non-Alembic SQLite. An audit trail must not be silently disableable by
turning off tracing. Rejected.

## 4. Data model — `AgentActionAudit` (migration `0042_agent_action_audits`)

New model in `backend/app/models.py`, shaped on the `DomainEvent` precedent
(`models.py:452`) plus action semantics. Table `agent_action_audits`:

| Column | Type | Notes |
|---|---|---|
| `id` | int PK autoincrement | |
| `kind` | str, indexed | `execution` \| `hitl_decision` |
| `status` | str, indexed | `attempted` → `ok` \| `error` \| `denied` \| `pending_approval`; for `hitl_decision` rows: `approved` \| `rejected` |
| `deny_reason` | str, nullable | `capability` \| `cost_preview` \| `tool_scope` \| `fanout_readonly` |
| `tool_name` | str, indexed | |
| `tool_class` | str, indexed | `domain_write` \| `async_dispatch` \| `fs_write` \| `artifact_write` |
| `tool_call_id` | str, indexed | correlation key across HITL propose → decide → execute |
| `mode` | str | `interactive` \| `auto` \| `yolo` (resolved execution mode) |
| `envelope` | str, nullable | envelope at call time |
| `actor` | str | turn actor (e.g. `desk_user`, gateway binding user); `hitl_decision` rows use the human decider |
| `model` | str, nullable | resolved model selection for the turn |
| `persona` | str, nullable | subagent persona if resolvable from config metadata |
| `thread_id` | int FK `agent_threads`, nullable, indexed | |
| `workflow_id` / `session_id` / `task_id` | nullable FKs | same pattern as `DomainEvent` |
| `message_id` | int, nullable | assistant turn message when known |
| `desk_workflow_slug` | str, nullable | for desk-workflow / fan-out attribution |
| `args_json` | JSON | tool args, size-capped (truncate serialized > 8 KB with marker) |
| `result_preview` | Text, nullable | truncated str of result content |
| `error` | Text, nullable | repr of exception / error ToolMessage content |
| `occurred_at` | DateTime, indexed | phase-1 insert time |
| `completed_at` | DateTime, nullable | phase-2 outcome time |

Indexes: `(occurred_at)`, `(tool_name, occurred_at)`, `(thread_id, occurred_at)`,
`(status)`, `(tool_call_id)`. Migration follows the `0039` new-table pattern
(idempotent `_has_table` guard, migration-local table, `down_revision =
"0041_morning_breach_assemble_prompt"`).

Append-only by policy: no update/delete endpoints; only the capture path mutates rows
(phase-2 outcome update).

## 5. Capture design

### 5.1 Shared classifier — `deep_agent/write_actions.py` (new)

Extract the classification logic currently embedded in `FanoutReadOnlyMiddleware`
(`_WRITE_GROUPS` / `_FS_WRITE_TOOLS` / `run_python` arg check) into a small shared
module: `classify_write_action(name, args, write_names_by_group) -> WriteClass | None`.
`FanoutReadOnlyMiddleware` is refactored to consume it (its group set includes
`PAGE_ACTION`; the audit consumer's does not). One taxonomy, two policies — the same
reason `__capability_group__` exists at all.

### 5.2 `AuditTrailMiddleware` — `deep_agent/audit_trail.py` (new)

`wrap_tool_call` / `awrap_tool_call`:

1. Classify via §5.1; **non-writes pass through untouched** (zero overhead beyond one
   set lookup).
2. **Phase 1 — attempt record.** Open a short-lived `SessionLocal` (never the tool's
   own session), insert `status='attempted'` with args + full context, **commit
   immediately**. This guarantees an irreversible side effect (a booking) cannot run
   without a durable record already existing, even if the process dies mid-tool.
3. Call `handler(request)`.
4. **Phase 2 — outcome update** (lookup by row id from phase 1):
   - normal return → `ok`; a returned `ToolMessage.status == "error"` → `error`,
     except when the content matches the fan-out deny template, which is recorded as
     `denied` + `deny_reason='fanout_readonly'`; store `result_preview`,
     `completed_at`;
   - `CapabilityDeniedError` / `CostPreviewRequiredError` / `ToolScopeDeniedError` →
     `denied` + `deny_reason`, **re-raise** (escalation must keep working);
   - `GraphBubbleUp` (HITL interrupt in flight) → `pending_approval`, re-raise. On
     approve-resume the tool re-enters this middleware; the phase-1 step first looks
     up an existing `pending_approval` row with the same `tool_call_id` and **updates
     it back to `attempted`** instead of inserting a duplicate (upsert keyed on
     `tool_call_id`). *Exact interrupt ordering relative to `wrap_tool_call` must be
     verified during implementation; the tool_call_id upsert makes both orderings
     produce one final row.*
   - any other exception → `error` + `error` text, re-raise (the outer
     `ToolErrorBoundaryMiddleware` still converts it to an error ToolMessage — audit
     sees the raw exception before conversion).

**Failure policy *(default — overridable)*:** best-effort with loud `ERROR` logging —
an audit-write failure must not take down an agent turn (SQLite transient lock, etc.).
The fail-closed alternative (refuse the tool call if unauditable) is a deliberate
one-line change point, isolated in one `try/except`.

**Registration:**
- `orchestrator.py::_agent_middleware` — insert at index 1 (just inside the error
  boundary).
- `personas.py::all_personas` — insert at index 1 per spec (error boundary stays 0,
  `FanoutReadOnlyMiddleware` shifts to 2).
- Verify during implementation whether `async_agents/runner.py` assembles a separate
  middleware stack; if so, insert there too. (It reuses `graph_run_config`, so context
  enrichment in §5.3 covers it either way.)

No feature flag: audit capture is **always on** (unlike memory/tracing). That is the
point of the module.

### 5.3 Context enrichment — `__audit_context__` in `configurable`

Turn-level identity currently lives only at `stream_and_persist` level; the middleware
reads config via `langgraph.config.get_config()` (the `FanoutReadOnlyMiddleware`
pattern — `configurable` is forwarded by reference into persona subagents, per the
`RUNTIME_SIGNAL_SINK_KEY` design). Add one dict to `configurable` at every
`graph_run_config` call site that runs agent turns (`stream_and_persist`, workflow
stream, resume paths, async runner):

```python
configurable["__audit_context__"] = {
    "actor": ..., "mode": ..., "model": ..., "thread_id": ...,
    "workflow_id": ..., "session_id": ..., "task_id": ..., "message_id": ...,
    "desk_workflow_slug": ..., "envelope": ...,
}
```

All fields nullable; the middleware stamps whatever is present. This mirrors (and can
share plumbing with) the existing `trace_meta` threading.

### 5.4 HITL decision rows

In `AgentService.resume_pending_action` — at the three resume paths where
`record_audit("agent.action.confirmed"/"dismissed")` already fires — also insert an
`AgentActionAudit` row with `kind='hitl_decision'`, `status='approved'|'rejected'`,
`actor` = the human decider, and `tool_call_id` taken from the action's
`source_meta.audit`. Rejected proposals therefore appear in the audit trail even
though the tool never executed; approved ones correlate with their subsequent
`execution` row by `tool_call_id`.

The existing `audit_events` writes stay untouched (non-goal to migrate them).

## 6. API — `backend/app/routers/audit.py`

`build_audit_router()` → `APIRouter(prefix="/api/audit", tags=["audit"])`, registered
in `main.py::create_app` alongside the other routers. **Read-only**, same doctrine as
`tracing.py` (no mutating endpoint may ever be added).

- `GET /api/audit/actions` — filters: `status`, `kind`, `tool_name`, `tool_class`,
  `mode`, `thread_id`, `since`/`until` (ISO-8601), `limit` (default 50, cap 200) +
  `offset`; newest-first. Returns `{items, total}`.
- `GET /api/audit/actions/{id}` — full record (untruncated stored fields).
- `GET /api/audit/summary` — counts by status/class/mode over an optional window
  (feeds the page header chips).

Pydantic response models + `_out()` serializer per the `memory.py` router pattern.

## 7. Frontend — Audit page

Registration (the standard 4 edits): `types.ts` `Route` union + `'audit'`;
`lib/routing.ts` `ROUTE_PATHS.audit = '/audit'`; `main.tsx` navItems `{route:'audit',
label:'Audit'}` + route-switch render line.

Files: `frontend/src/routes/Audit.tsx` (pure, props-only), `Audit.live.tsx` (fetch +
state container), `Audit.css` (token-only, `wl-` prefixed — per `frontend/CLAUDE.md`).
API client fns (`listAuditActions`, `getAuditAction`, `fetchAuditSummary`) in
`api/client.ts`; response types in `types.ts`.

Layout: `PageScaffold` with header chips from `/summary` (e.g. `writes 24h`, `denied`,
`yolo`); `PageToolbar` with search (tool name) + `Select` filters for status /
tool-class / mode; shared `Table` beneath.

Columns: time, tool (+ persona), class badge, status badge (`ok` normal, `denied` +
`error` in danger tone, `pending_approval` warning, `rejected` muted — token colors
only), mode badge (YOLO visually distinct), actor, thread link, args summary
(first-line truncation). **Table alignment gotcha:** fixed widths for time/badges,
`minmax(0, fr)` for tool/args — the shared `Table` renders each row as an independent
grid (the Memory-page lesson).

Row click → detail `Modal`: full args JSON (pre block), result preview, error, all
identifiers, HITL-decision sibling rows for the same `tool_call_id`, and a deep link
to the thread in `/tracing` for the full transcript.

Pagination: `limit/offset` "load more"; no live polling in v1 (manual refresh button)
*(default — overridable)*.

## 8. Testing

- **Classifier unit tests** (`tests/deep_agent/`): group mapping, FS built-ins,
  `run_python` arg-awareness, PAGE_ACTION excluded for audit but retained for fan-out.
- **Middleware tests**: fake handler; phase-1 row exists before handler runs
  (assert via side-effect ordering); outcome transitions for ok / error ToolMessage /
  raised exception / each denial type; `tool_call_id` upsert (no duplicate rows across
  interrupt → resume); audit-write failure does not break the tool call.
  *Conftest trap:* `_bypass_capability_gate` masks the gate outside `_GATE_TEST_FILES`
  — tests that need real `CapabilityDeniedError` must be registered there or construct
  the gate directly.
- **Fan-out regression**: `FanoutReadOnlyMiddleware` behavior unchanged after the
  classifier extraction (existing tests keep passing).
- **Router tests**: filter combinations, pagination caps, read-only surface (405/404
  on mutation attempts).
- **Migration**: upgrade → table + indexes exist; idempotent re-run; downgrade drops.
- **Frontend**: vitest render tests for `Audit.tsx` (rows, badges, detail modal),
  mocked-fetch test for `Audit.live.tsx`; `npx tsc --noEmit`.

## 9. Non-goals (v1)

- No retention/expiry policy or deletion UI (append-only).
- No `PAGE_ACTION` auditing.
- No backfill of historical actions (trail starts at deploy).
- No replacement/migration of the existing `audit_events` turn-level rows.
- No streaming/live updates on the page.
- No capture inside QuantArk or non-agent (human REST) write paths — this module
  audits **LLM-initiated** actions; human UI actions are already covered by
  `record_audit` call sites in `main.py`.

## 10. Decisions taken while the user was away (all overridable)

1. **Scope** = DOMAIN_WRITE + ASYNC_DISPATCH + FS built-ins + `run_python(writes_artifacts=True)`; PAGE_ACTION excluded.
2. **Denials and rejections are recorded**, not just executions (capability/scope/cost-preview denials, fan-out blocks, HITL rejections).
3. **New typed table** rather than extending generic `audit_events`.
4. **Two-phase write** (attempt-then-outcome) so irreversible actions can never execute without a durable record.
5. **Best-effort failure policy** with ERROR logging (fail-closed noted as the one-line alternative).
6. **Always-on** — no env flag to disable capture.

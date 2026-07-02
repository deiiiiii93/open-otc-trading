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
| `kind` | str, indexed | `execution` \| `hitl_proposal` \| `hitl_decision` |
| `status` | str, indexed | `execution`: `attempted` → `ok` \| `error` \| `denied` \| `interrupted`; `hitl_proposal`: `proposed`; `hitl_decision`: `approved` \| `rejected` |
| `deny_reason` | str, nullable | `capability` \| `cost_preview` \| `tool_scope` \| `fanout_readonly` |
| `tool_name` | str, indexed | |
| `tool_class` | str, indexed | `domain_write` \| `async_dispatch` \| `fs_write` \| `artifact_write` |
| `tool_call_id` | str, indexed | model-assigned call id; correlation is always **scoped** `(thread_id, tool_call_id)` — never global |
| `audit_ref` | str (UUID), nullable, indexed | server-generated at HITL projection time, carried in `source_meta.audit` through resume; links proposal → decision → execution rows exactly |
| `mode` | str | `interactive` \| `auto` \| `yolo` (resolved execution mode) |
| `envelope` | str, nullable | envelope at call time |
| `actor` | str | turn actor (e.g. `desk_user`, gateway binding user); `hitl_decision` rows use the human decider |
| `model` | str, nullable | resolved model selection for the turn |
| `persona` | str, nullable | subagent persona if resolvable from config metadata |
| `thread_id` | int FK `agent_threads`, nullable, indexed | |
| `workflow_id` / `session_id` / `task_id` | nullable FKs | same pattern as `DomainEvent` |
| `message_id` | int, nullable | assistant turn message when known |
| `desk_workflow_slug` | str, nullable | for desk-workflow / fan-out attribution |
| `args_json` | JSON | tool args after **redaction** (§5.1b), then size-capped (truncate serialized > 8 KB with marker) |
| `redacted` | bool, default false | true when the redaction layer altered `args_json` |
| `result_preview` | Text, nullable | truncated str of result content, redaction-passed |
| `error` | Text, nullable | repr of exception / error ToolMessage content |
| `occurred_at` | DateTime, indexed | phase-1 insert time |
| `completed_at` | DateTime, nullable | phase-2 outcome time |

Indexes: `(occurred_at)`, `(tool_name, occurred_at)`, `(thread_id, occurred_at)`,
`(status)`, `(tool_call_id)`. Migration follows the `0039` new-table pattern
(idempotent `_has_table` guard, migration-local table, `down_revision =
"0041_morning_breach_assemble_prompt"`).

**Append-only correlation, not cross-row mutation.** Rows are immutable once terminal;
the *only* in-place update is the phase-1 → phase-2 outcome transition on an
`execution` row, performed by the same middleware frame that inserted it, addressed by
its in-memory primary key (never a lookup). HITL chains are represented as *separate*
rows (`hitl_proposal` → `hitl_decision` → `execution`) grouped by `audit_ref` (or by
scoped `(thread_id, tool_call_id)` when `audit_ref` is absent). A checkpointer replay
that re-executes the same tool call therefore appends a new `execution` row rather
than corrupting an old one — duplicates are visible and honest, which is the
audit-grade behavior. No update/delete API endpoints exist.

## 5. Capture design

### 5.1 Shared classifier — `deep_agent/write_actions.py` (new)

Extract the classification logic currently embedded in `FanoutReadOnlyMiddleware`
(`_WRITE_GROUPS` / `_FS_WRITE_TOOLS` / `run_python` arg check) into a small shared
module: `classify_write_action(name, args, write_names_by_group) -> WriteClass | None`.
`FanoutReadOnlyMiddleware` is refactored to consume it (its group set includes
`PAGE_ACTION`; the audit consumer's does not). One taxonomy, two policies — the same
reason `__capability_group__` exists at all.

### 5.1b Redaction before persistence

The audit trail must not become a durable secret/PII sink: `execute` / `write_file` /
`edit_file` / `run_python` arguments can carry credentials, file contents, code
bodies, or customer data, and v1 is append-only with no deletion UI. Before phase-1
persistence (and before `result_preview`/`error` storage) a redaction pass runs:

- **Recursive key-pattern redaction** on `args_json`: values under keys matching
  `token|password|secret|api[_-]?key|credential|authorization` (case-insensitive)
  are replaced with `"[REDACTED]"`.
- **Content-body elision** for FS/artifact tools: `write_file`/`edit_file` `content`
  and `run_python`/`execute` code/command bodies are stored as
  `{sha256, byte_len, head: first 256 chars (redaction-passed)}` instead of the full
  payload — the audit answers *what was written where and how big*, not the full
  content (the artifact itself remains the source of truth).
- Any alteration sets `redacted=true` on the row so the UI can show the marker.
- Required tests (§8): secret-bearing `execute`/`run_python`/`write_file` args are
  not persisted verbatim and not rendered by the API.

No access gating in v1: the desk is single-operator with a constant identity and the
app has no auth layer anywhere; per-role visibility of raw fields becomes relevant
only if multi-user lands (documented follow-up, not scope).

### 5.2 `AuditTrailMiddleware` — `deep_agent/audit_trail.py` (new)

`wrap_tool_call` / `awrap_tool_call`:

1. Classify via §5.1; **non-writes pass through untouched** (zero overhead beyond one
   set lookup).
2. **Phase 1 — attempt record (fail-closed).** Open a short-lived `SessionLocal`
   (never the tool's own session), insert `status='attempted'` with args + full
   context, **commit immediately**. If the insert/commit fails, the dangerous action
   is **refused**: the middleware returns an error `ToolMessage`
   ("audit trail unavailable; write action blocked") without calling the handler.
   The agent turn survives (reads still work, the model sees the refusal and can
   report it), but no classified write may ever execute unaudited — that guarantee is
   the point of the module and is not softened by an availability trade-off.
3. Call `handler(request)`.
4. **Phase 2 — outcome update.** Addressed by the primary key returned from phase 1
   (held in the local frame — never a lookup):
   - normal return → `ok`; a returned `ToolMessage.status == "error"` → `error`,
     except when the content matches the fan-out deny template, which is recorded as
     `denied` + `deny_reason='fanout_readonly'`; store `result_preview`,
     `completed_at`;
   - `CapabilityDeniedError` / `CostPreviewRequiredError` / `ToolScopeDeniedError` →
     `denied` + `deny_reason`, **re-raise** (escalation must keep working);
   - `GraphBubbleUp` (interrupt in flight mid-call) → `interrupted`, re-raise. This
     is **belt-and-braces only**: HITL proposals are durably captured at projection
     time (§5.4), so nothing depends on whether LangGraph raises the interrupt before
     or after `wrap_tool_call`. If an approve-resume re-executes the tool, the
     middleware simply appends a fresh `execution` row — correlated to the proposal
     by `audit_ref` / scoped `tool_call_id`, never by mutating prior rows;
   - any other exception → `error` + `error` text, re-raise (the outer
     `ToolErrorBoundaryMiddleware` still converts it to an error ToolMessage — audit
     sees the raw exception before conversion).
   A phase-2 failure leaves the row at `attempted` and logs `ERROR`: the durable
   record exists, only the outcome is unknown — degraded but never silent.

**Failure policy:** **fail-closed on phase 1** (above), log-and-continue on phase 2.

**Lock handling under fail-closed** (SQLite is single-writer; an unmitigated
transient lock would turn ordinary contention into a desk-wide write outage):
- The audit session sets an explicit `busy_timeout` and the phase-1 commit retries
  with bounded backoff (3 attempts, ~100/300/900 ms) before declaring failure —
  a refusal requires sustained, not momentary, contention. (Note the domain write
  that follows targets the *same* SQLite file, so an audit-blocking lock would very
  likely block the business write too; the retry window just keeps audit from being
  the *more* fragile of the two.)
- Fail-closed refusals are operator-visible, not just logged: the refusal
  `ToolMessage` is distinct ("audit trail unavailable"), and `/api/audit/summary`
  reports a `fail_closed_refusals` counter (derived from ERROR-log-backed rows or a
  lightweight counter table — implementation's choice, but it must survive restart).
- Required test (§8): hold a write lock on the DB while a classified write is
  attempted; assert retry-then-refusal, the business tool never executed, and the
  turn survived.

### 5.2a Registration — mandatory integration points

Every factory/runner that can execute agent tool calls MUST carry the middleware; this
is a requirement, not a follow-up:

- `orchestrator.py::_agent_middleware` — insert at index 1 (just inside the error
  boundary).
- `personas.py::all_personas` — insert at index 1 per spec (error boundary stays 0,
  `FanoutReadOnlyMiddleware` shifts to 2).
- `async_agents/runner.py` (and its resume path) — background async agents can run
  write tools and bubble up HITL; whatever middleware stack they assemble gets the
  audit middleware at the same position. Covered by a required integration test (§8).
- The desk-workflow executor path, if its stack differs from the orchestrator's.

Implementation includes a coverage assertion test: enumerate the middleware stacks
built by each factory and assert `AuditTrailMiddleware` is present in all of them, so
a future factory that forgets the middleware fails CI rather than silently
under-auditing.

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

### 5.4 HITL proposal + decision rows (projection-time capture)

HITL proposals are captured where they become durable today — the projection path
(`pending_actions_from_interrupts` → persistence into `AgentMessage.meta`, both
finalize paths) — **not** in the middleware, whose view of interrupts depends on
unverified ordering:

- **Proposal.** When a pending action for a classified write tool is projected and
  persisted, mint a server-generated `audit_ref` (UUID) and insert an
  `AgentActionAudit` row `kind='hitl_proposal'`, `status='proposed'`, with the tool
  name/args and full turn context. **`audit_ref` minting is mandatory and lives
  inside the projection helper itself** (`pending_actions_from_interrupts`), not in
  callers: the async projection path calls the helper with `persona=None` and no
  source metadata today, and `_source_meta_for_action` returns `{}` when
  `source_meta` is absent — so any caller-side minting would silently skip exactly
  the async proposals that made coverage mandatory. The helper stamps
  `source_meta.audit = {audit_ref, tool_call_id, tool_name, interrupt_id, task_id?}`
  unconditionally. Abandoned proposals (never approved or rejected) remain visible in
  the trail.
- **Atomicity.** The `hitl_proposal` row insert and the `AgentMessage.meta`
  (`pending_actions`) persistence commit **in the same DB transaction** — the
  projection-persistence code paths already hold a session writing the message row;
  the audit insert joins it. No card may exist without its proposal row, and no
  proposal row without its card; failure of either rolls back both (the turn then
  surfaces the persistence error as it does today).
- **Decision.** In `AgentService.resume_pending_action` — at the three resume paths
  where `record_audit("agent.action.confirmed"/"dismissed")` already fires — insert
  `kind='hitl_decision'`, `status='approved'|'rejected'`, `actor` = the human decider,
  carrying the `audit_ref` read back from `source_meta.audit`.
- **Execution.** The approved tool run is captured by the middleware as a normal
  `execution` row (§5.2). Every resume path (orchestrator, workflow-routed, async
  bubble-up) reads `audit_ref` from the action's `source_meta.audit` — mandatory as
  of this spec — and threads it into `__audit_context__`, so the execution row
  carries the same ref. Scoped `(thread_id, tool_call_id)` grouping is **display-only
  best effort for legacy rows** (actions projected before this feature ships); it is
  not a correctness mechanism for new rows.

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
`error` in danger tone, `proposed`/`interrupted` warning, `rejected` muted — token
colors only), mode badge (YOLO visually distinct), actor, thread link, args summary
(first-line truncation). **Table alignment gotcha:** fixed widths for time/badges,
`minmax(0, fr)` for tool/args — the shared `Table` renders each row as an independent
grid (the Memory-page lesson).

Row click → detail `Modal`: full args JSON (pre block), result preview, error, all
identifiers, the correlated HITL proposal/decision/execution chain (grouped by
`audit_ref`, falling back to scoped `(thread_id, tool_call_id)`), and a deep link
to the thread in `/tracing` for the full transcript.

Pagination: `limit/offset` "load more"; no live polling in v1 (manual refresh button)
*(default — overridable)*.

## 8. Testing

- **Classifier unit tests** (`tests/deep_agent/`): group mapping, FS built-ins,
  `run_python` arg-awareness, PAGE_ACTION excluded for audit but retained for fan-out.
- **Middleware tests**: fake handler; phase-1 row exists before handler runs
  (assert via side-effect ordering); outcome transitions for ok / error ToolMessage /
  raised exception / each denial type; **fail-closed**: a phase-1 insert failure
  blocks the handler (write never executes) and returns an error ToolMessage while
  leaving the turn alive; phase-2 failure leaves `attempted` + logs, does not raise.
- **HITL chain tests**: proposal row inserted at projection with `audit_ref` in
  `source_meta.audit` — **including the async projection path with `persona=None`
  and no source metadata**; decision row on confirm/dismiss carries the same
  `audit_ref`; approve → execution row correlates; abandoned proposal stays
  `proposed`; proposal row + message-meta persistence are atomic (inject a failure
  on either side, assert no card-without-proposal or proposal-without-card state).
- **Contention test**: hold a SQLite write lock while a classified write is
  attempted; assert bounded retry then fail-closed refusal, business tool not
  executed, turn alive, refusal counted in `/api/audit/summary`.
- **Registration coverage assertion**: every middleware-stack factory (orchestrator,
  personas, async runner/resume, workflow executor) contains `AuditTrailMiddleware`
  — a new factory missing it fails CI.
- **Async integration test**: a background async-agent task attempts a classified
  write and produces attempt/outcome (and, on the HITL path,
  proposal/decision/execution) rows sharing one correlation key.
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
5. **Fail-closed on phase 1** — a classified write is refused if its attempt record cannot be committed; phase-2 outcome failures degrade to `attempted` + ERROR log. *(Revised from best-effort after adversarial review.)*
6. **Always-on** — no env flag to disable capture.
7. **Append-only correlation** — HITL chains are separate rows linked by a server-generated `audit_ref`, minted **unconditionally inside the projection helper** (async paths lack source metadata today); scoped `(thread_id, tool_call_id)` is display-only for legacy rows; no cross-row upserts. Proposals are captured at projection time, atomically with the pending-action card. Async runner/workflow executor stacks are mandatory integration points with a CI coverage assertion. *(Revised after adversarial review iterations 1–2.)*
8. **Fail-closed is contention-hardened** — busy-timeout + bounded retry before refusal, restart-surviving refusal counter in `/api/audit/summary`, lock-contention test required. *(Added after review iteration 2.)*
9. **Redaction before persistence** — key-pattern redaction + content-body elision (sha256/len/head) for FS/artifact tools, `redacted` marker, no raw secret persistence; no access gating in v1 (no auth layer exists in this single-operator app). *(Added after review iteration 3.)*

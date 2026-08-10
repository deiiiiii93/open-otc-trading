# Settlement module — design

**Date:** 2026-08-10
**Status:** approved (brainstorming complete, pending implementation plan)

---

## 1. Problem

The desk records position lifecycle events (`PositionLifecycleEvent`, `models.py:1360`) but has
nowhere to track the **cash those events imply**. Today exactly one event type computes money:
`_enrich_snowball_ko_settlement` (`services/domains/positions.py:481`) writes `settlement_amount`,
`principal_amount` and `coupon_amount` into the event's `event_data` JSON blob when a
`SnowballOption` settles. Every other cash-generating event — `coupon_paid`, `autocall`,
`maturity`, `knock_out`, `close` — fires carrying no amount at all, and no event's cash is
tracked, approved, or documented anywhere.

There is no record of what the desk owes, no approval step before payment, and no document to
send a counterparty.

## 2. What this module is (and is not)

A **governance layer over the cash that lifecycle events imply.** It answers three questions:

1. What cash does this book owe or expect?
2. Has a human (or an authorised agent) cleared it?
3. What did we tell the counterparty?

**It does not compute payoffs and it does not move money.**

Excluding payoff computation is deliberate. `CLAUDE.md` establishes that pricing/risk math is
delegated to QuantArk and that *numbers never come from an LLM*; a settlement calculator would
become a second, unvalidated pricing surface sitting next to the pinned `quantark==0.3.0` engine,
with none of the harvested-truth discipline the golden fixtures enforce. Instead, when an event
carries no amount the module creates the cashflow in a `needs_amount` state — which honestly says
*"this event generated cash and nobody has told us how much"*, preserving the `empty` vs
`unavailable` distinction the reporting module already fought for.

**Package:** `backend/app/services/settlement/`, modelled on `services/limits/` (governed writes,
incident-style transition history, agent tools, skill) — **not** on `services/reporting/`, whose
LLM-narrator pipeline this feature deliberately avoids.

## 3. Data model — migration `0055`

Three tables. The migration uses migration-local Core table definitions, never ORM models
(established rule: ORM drift broke `0018` via `0019`).

### 3.1 `settlement_cashflows`

One row per (lifecycle event × leg).

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `lifecycle_event_id` | FK → `position_lifecycle_events.id`, indexed, NOT NULL | Every cashflow originates from an event. No manual creation. |
| `leg_key` | str(40) NOT NULL | Discriminates multiple legs from one event: `principal`, `coupon`, `premium`. |
| | | **UNIQUE (`lifecycle_event_id`, `leg_key`)** — this constraint is what makes generation idempotent. |
| `position_id` | FK → `positions.id`, indexed | Denormalized for querying without a join through the event. |
| `currency` | str(8) NOT NULL | Copied from `Position.currency` at generation. |
| `counterparty` | str(255) NULL | Best-effort resolution at generation; editable. See §3.4. |
| `direction` | str(8) NOT NULL | `pay` \| `receive`. |
| `derived_amount` | float NULL | **Snapshot of what the deriver produced.** Never rewritten by generation or by drift detection. The *only* sanctioned path that re-baselines it is an explicit `resync` (§5). |
| `derived_value_date` | date NULL | Same. |
| `derived_basis` | str(80) NULL | How the amount was obtained, e.g. `ko_principal_plus_coupon`, `event_data.settlement_amount`, `none`. |
| `amount` | float NULL | Effective value. Starts equal to `derived_amount`. Only an edit changes it. |
| `value_date` | date NULL | Effective value. Starts equal to `derived_value_date`. |
| `status` | str(20) NOT NULL, indexed | `needs_amount` \| `pending` \| `blocked` \| `released` \| `settled` \| `void`. |
| `stale` | bool NOT NULL default false | Set by the drift sweep. Advisory only — never blocks an action. |
| `stale_reason` | JSON NULL | `{kind, detail, old, new}`. |
| `last_checked_at` | datetime NULL | When drift was last evaluated for this row. |
| `block_reason` | text NULL | |
| `notes` | text NULL | |
| `row_version` | int NOT NULL default 1 | Optimistic concurrency, mirroring `LimitIncident.expected_row_version`. |
| `created_at`, `updated_at` | datetime | |

**Why `derived_*` is kept alongside `amount`.** This is the load-bearing modelling choice. It
separates *"the source event changed"* from *"a human overrode the number"*. Without it, drift
cannot be detected on any row a human has already edited — the recomputed value would be compared
against the human's number and every edited row would read as drifted forever.

### 3.2 `settlement_cashflow_events`

Append-only transition log. Mirrors `LimitIncidentEvent`.

| Column | Notes |
|---|---|
| `id`, `cashflow_id` (FK, indexed) | |
| `action` | `generated` \| `edited` \| `released` \| `unreleased` \| `blocked` \| `unblocked` \| `settled` \| `voided` \| `resynced` \| `flagged_stale` \| `stale_cleared` |
| `from_status`, `to_status` | Nullable (an edit may not change status). |
| `actor` | str(120) |
| `reason` | text NULL |
| `payload` | JSON — field-level before/after for edits |
| `created_at` | |

This is how the desk answers "who released this, when, and on what basis".

### 3.3 `settlement_notices`

| Column | Notes |
|---|---|
| `id`, `cashflow_id` (FK, indexed) | |
| `version` | int; **UNIQUE (`cashflow_id`, `version`)** |
| `artifact_path` | basename, resolved against `settings.artifact_dir` |
| `content_sha256` | of the rendered bytes |
| `payload_snapshot` | JSON — the exact values used at render time |
| `status` | `generated` \| `superseded` |
| `rendered_at`, `rendered_by` | |

The snapshot makes a notice provable evidence of the numbers **as at render time**, the same way a
report embeds its spec plus `spec_sha256` so editing a template never rewrites what an old report
claims to have been generated from.

### 3.4 Counterparty resolution

Positions carry no counterparty. `counterparty` exists only on `ExtractedTrade` (`models.py:1457`,
a confirmation draft) and as `RFQ.client_name`. Resolution order at generation time, best-effort:

1. `ExtractedTrade.counterparty` where `ExtractedTrade.booked_position_id == position.id`
2. `RFQ.client_name` via `Position.rfq_id`
3. `NULL`

The value is denormalized onto the cashflow and is editable. A notice cannot be generated for a
cashflow with a `NULL` counterparty — the request fails with a clear error rather than producing a
document addressed to nobody.

**Not in scope:** a `Counterparty` entity, standing settlement instructions, netting.

## 4. Generation — one pure deriver, two callers

```
services/settlement/derive.py
    derive_cashflows(position, event) -> list[CashflowDraft]     # pure, no DB, total
         ├── called inline by create_lifecycle_event()            (same transaction, best-effort)
         └── called by generate.py :: generate_missing()          (backfill sweep, INSERT-only)
```

### 4.1 The deriver is pure and total

`derive_cashflows` takes a `Position` and a `PositionLifecycleEvent`, touches no session, and
**never raises**. When it cannot determine an amount it returns a draft with `amount=None` and
`basis="none"`, which persists as `needs_amount`.

Purity here is what makes it testable in isolation (table-driven, one case per event type) and
what makes the inline hook safe to be lenient.

### 4.2 The inline hook is best-effort

`create_lifecycle_event` (`services/domains/positions.py:579`) gains a call to generation inside
its existing transaction, wrapped so that **any failure logs and continues**. The lifecycle event
must never be held hostage to cashflow derivation — lifecycle is the source of truth for position
status, and `_project_status_from_lifecycle` replays it.

The backfill sweep is the safety net that makes this leniency acceptable: a gap left by a failed
hook is picked up on the next sweep.

### 4.3 The backfill sweep is INSERT-only and idempotent

`generate_missing(portfolio_id=None, since=None, session=None)` walks lifecycle events, runs the
deriver, and inserts only rows absent under the `(lifecycle_event_id, leg_key)` unique constraint.
**It never updates an existing row.** Running it twice produces the same set — a pinned test.

This path matters immediately: the live DB already holds lifecycle events with no cashflows.

An event that legitimately produces no cash yields zero drafts. Because the deriver is
deterministic, re-attempting it on every sweep returns the same nothing, so no "already
considered" marker is needed.

### 4.4 OPEN — the event→cashflow mapping

**Deliberately deferred to the domain expert, not guessed.** `derive.py` will ship with the
signature, the hazards documented, and a marked TODO for the rule body.

Hazards the rule must resolve:

- **Double counting.** A snowball fires `knock_out` (status → closed) and then `settle` (carrying
  the enriched amount). If both derive a cashflow the money is booked twice.
- **`autocall` and `maturity`** both target status `closed` and may or may not be followed by a
  separate `settle`.
- **`open`** arguably implies a premium cashflow of `entry_price × quantity`.
- **`coupon_observation` vs `coupon_paid`** — only the latter is cash.
- Non-cash types (`reopen`, `knock_in`, `coupon_lock`, `memory_coupon`, `fixing`, `custom`) must
  return no legs.

## 5. Drift — flag, never overwrite

`services/settlement/drift.py :: refresh_drift(...)` re-runs the deriver against the **current**
state of the source event and compares to `derived_amount` / `derived_value_date`:

| Condition | Result |
|---|---|
| source event has `cancelled_at` set | `stale`, `stale_reason.kind = source_event_cancelled` |
| recomputed values ≠ `derived_*` | `stale`, reason carries the old/new delta |
| otherwise | `stale` cleared |

It **never** mutates `amount`, `value_date`, `status`, or anything at all on a `released` or
`settled` row. Resolution is an explicit act:

- **`resync`** — adopt the newly derived values into both `derived_*` and (if unedited) `amount`.
  Illegal on `released` / `settled`.
- **`void`** — terminate the cashflow.

`last_checked_at` is surfaced in the API and UI so neither can imply a freshness it has not
verified.

Invoked by `POST /api/settlement/cashflows/refresh` and as the tail of `generate_missing`.

## 6. State machine

```
needs_amount ──edit──▶ pending ──release──▶ released ──settle──▶ settled (terminal)
                          ▲                    │
                          └────unrelease───────┘

{needs_amount, pending, released} ──block──▶ blocked ──unblock──▶ pending

any non-settled ──void──▶ void (terminal)
```

Rules:

- `edit` is legal only in `needs_amount`, `pending`, `blocked`. Editing a `needs_amount` row to a
  non-null amount transitions it to `pending`. Clearing `amount` back to null is **rejected** —
  a cashflow may not regress into `needs_amount` once a number has been supplied; use `void`.
- `block` is reachable **from `released`** on purpose: pulling a payment back must never be harder
  than releasing it.
- `settled` and `void` are terminal.
- Every transition writes a `settlement_cashflow_events` row.
- Every mutation requires `expected_row_version`; a mismatch returns
  `{ok: false, error: "conflict", hint: ...}` → HTTP 409, matching the limits-incident convention.

## 7. Agent gating (HITL)

| Tool | Risk level |
|---|---|
| `settle_settlement_cashflow` | **`irreversible`** — gated in every mode including AUTO/headless |
| `release_settlement_cashflow` / `unrelease_settlement_cashflow` | `write` |
| `update_settlement_cashflow` | `write` |
| `block_settlement_cashflow` / `unblock_settlement_cashflow` | `write` |
| `void_settlement_cashflow` | `write` |
| `resync_settlement_cashflow` | `write` |
| `generate_settlement_cashflows` | `write` |
| `generate_settlement_notice` | `write` |
| all reads | ungated |

The agent surface is **exactly** the user surface — every REST transition in §9.1 has a
corresponding tool, so there is no action a person can take that an agent cannot.

**Recorded concern (raised, and the decision reaffirmed).** `release` is the action whose
downstream consequence is money leaving the building, and `"write"` means an agent in AUTO/headless
mode executes it with **no approval card at all** — `interrupt_on_config(yolo_mode=True)` strips
every `"write"`-level tool from the interrupt map. This is the exact mechanism by which a booking
tool classified `"write"` booked position 27 unattended.

The decision is coherent under the reading that release is *internal and recallable*, and this
design supports that reading concretely: `unrelease` exists, `block` is reachable from `released`,
every release writes both an `AuditEvent` and a transition row, and only the unrecallable step
(`settle`) is hard-gated. Revisit if release ever becomes a direct payment trigger.

`settle_settlement_cashflow` takes a bare `cashflow_id`, so it **requires a `_SUMMARY_BUILDERS`
entry** (`hitl.py:354`) opening its own short-lived read-only session to state position, product,
amount, currency, counterparty and value date. Without one the approval card shows a human a bare
integer, which is gate theatre.

## 8. Notices — deterministic Markdown

`services/settlement/notice.py` renders one hardcoded template to Markdown, writes it into
`settings.artifact_dir` (served at `/artifacts/<basename>`), and records `content_sha256` plus a
`payload_snapshot`.

- **No narrator, no LLM prose, no grounding guard.** Every number comes from one row, so the
  narrator pipeline and its grounding guard are unnecessary — a simplification, not a limitation.
- Rendering is deterministic: same row → same bytes (timestamps come from the snapshot, not
  `now()`).
- Regenerating mints a new `version` and marks the previous `superseded`; the old artifact stays on
  disk.
- **The file is actually written.** A record that declares an artifact but writes no file is a
  dangling pointer — the failure documented for arena fixtures, where a declared-but-unwritten
  `artifact_paths` entry sent the agent hunting a file that never existed.

Content: notice id/version, generation timestamp, counterparty, portfolio, position and product
identification, the source lifecycle event and its date, leg, direction, amount, currency, value
date, and the settlement status at render time.

## 9. Surfaces

### 9.1 REST — `backend/app/routers/settlement.py` → `/api/settlement`

| Method | Path | Purpose |
|---|---|---|
| GET | `/cashflows` | Filter by portfolio / position / status / value-date range / counterparty / stale; paged |
| GET | `/cashflows/{id}` | Detail incl. transition history and notices |
| POST | `/cashflows/generate` | Backfill sweep |
| POST | `/cashflows/refresh` | Drift sweep |
| PATCH | `/cashflows/{id}` | Edit `amount` / `value_date` / `counterparty` / `notes`; requires `expected_row_version` |
| POST | `/cashflows/{id}/release` \| `/unrelease` \| `/block` \| `/unblock` \| `/settle` \| `/void` \| `/resync` | Transitions |
| POST | `/cashflows/{id}/notice` | Generate a notice |
| GET | `/cashflows/{id}/notices` | List notices |
| GET | `/summary` | Counts by status, totals by currency, stale count — feeds the page chips |

The write service owns a **committing** `_session_scope`. It must **not** copy
`services/domains/risk.py`'s, which is read-only (flushes, never commits) — the report template
store did exactly that and its `PUT` answered 200 while nothing persisted, caught only by an HTTP
test because every unit test injected its own session.

### 9.2 Agent tools — `backend/app/tools/settlement.py`

Reads: `get_settlement_cashflows`, `get_settlement_cashflow`, `get_settlement_summary`.
Writes: as listed in §7.

Full registration checklist (a tool missing any of these is silently unavailable):

1. `QUANT_AGENT_TOOLS` — `tools/__init__.py`
2. `DEEP_AGENT_TOOL_NAMES` — `services/agents.py` (allowlist `select_deep_agent_tools()` filters
   by; registered-but-not-allowlisted is exactly what made the model never call
   `assemble_breach_report`)
3. `hitl.py` — all three structures: `INTERRUPT_TOOL_NAMES` (:23), `_RISK_LEVEL_BY_TOOL` (:71),
   `_LABEL_BY_TOOL` (:141), plus `_SUMMARY_BUILDERS` (:354) for `settle_settlement_cashflow`
4. `__capability_group__ = ToolGroup.DOMAIN_WRITE` on every write tool — feeds the audit-trail
   taxonomy and `FanoutReadOnlyMiddleware`
5. Exact-set test pins: `test_hitl.py`, `test_capability_assignments.py`

### 9.3 Skill

`skills/workflows/settlement/manage-settlement-cashflows/SKILL.md`, **with a `routing:` block**.

A skill without a `routing:` block never enters the orchestrator's Known-skills table and is
unroutable — measured on arena run #101 at 4/36 trials versus 75–97% for routed skills.

`"settlement"` is added to `PERSONA_WORKFLOW_DOMAINS["trader"]` (`persona_domains.py`) — settlement
follows booking, and `trader` is the persona that books. **Tuple order is load-bearing**: it is
preserved into the persona's skill source list and controls catalog listing order in subagent
prompts. `skill_lint.py` cross-checks that a skill's routing persona actually has the domain.

Adding a workflow `SKILL.md` breaks exact-set assertions in roughly six catalog test files;
enumerate them with `grep -rln "book-position" tests/`.

### 9.4 Frontend

`frontend/src/routes/Settlement.{tsx,live.tsx,css}`, nav entry in `main.tsx`, route in
`lib/routing.ts`. Read `frontend/CLAUDE.md` first — token-only styling is non-negotiable.

Built on the shared `DataTablePage` / `TableToolbar` / `Table` primitives (the Audit page
precedent), giving the standard rows-range label, rows-per-page select and prev/next pager.

- **Columns:** position, event type + date, leg, direction, amount, currency, value date,
  counterparty, status, stale badge.
- **Row actions:** Edit, Release, Block, Settle, Notice — enabled per the §6 state machine.
- **Detail drawer:** derived vs effective amount side by side, drift delta when stale, transition
  history, notices with download links.
- Note the shared `Table` primitive renders each row as an independent CSS grid, so only `fr` and
  fixed lengths align across rows — `max-content`/`auto` tracks resolve per-row and break column
  alignment. Status, amount and the action buttons take fixed widths; the rest use `minmax(0, fr)`.

## 10. Testing

| File | Covers |
|---|---|
| `test_settlement_derive.py` | Pure deriver, table-driven per event type; the double-count guard; non-cash types yield no legs |
| `test_settlement_store.py` | State-machine legality matrix (every transition × every source state); `expected_row_version` conflict → 409 |
| `test_settlement_generate.py` | **Sweep idempotency: run twice, identical rows**; hook failure does not break lifecycle-event creation; sweep fills a gap a failed hook left |
| `test_settlement_drift.py` | Cancelled event → stale; amended amount → stale with delta; a `released` row is flagged but never mutated |
| `test_settlement_notice.py` | Deterministic bytes; **the artifact file exists on disk**; regeneration supersedes and increments version; NULL counterparty is refused |
| `test_settlement_api.py` | HTTP-level, asserting writes actually **persist** (the read-only-`_session_scope` trap) |
| `test_settlement_tools.py` | Tool registration in all required structures; summary builder output |
| updates to `test_hitl.py`, `test_capability_assignments.py`, catalog tests | Exact-set pins |

Backend: `.venv/bin/python -m pytest`. Frontend: `cd frontend && npm test`, `npx tsc --noEmit`.
Note the frontend vitest suite is flaky under load — compare failing-file sets against a
same-machine `main` run before attributing failures to this branch.

## 11. Documentation obligations

- `CHANGELOG.md` under `[Unreleased]` (the `pre-push` hook blocks without it)
- `README.md` — new nav page is user-facing
- `CLAUDE.md` — new subsystem section, with the gotchas discovered during implementation

## 12. Explicitly not in scope

Netting across cashflows · a counterparty entity or standing settlement instructions · payment-file
or instruction export · PDF/DOCX rendering · per-desk configurable notice templates · FX conversion
of settlement amounts into a reporting currency · payoff calculation for any product family.

# Design — long thread fixes

**Date:** 2026-05-25
**Status:** Design reviewed; backend implementation landed behind migrations/tests. Frontend workflow timeline remains a separate ticket.
**Scope:** Three architectural fixes derived from thread #23 ("deepseek long") analysis.
Issues 1 (relative `/references/` paths) and 2 (single-line offload JSON) are
prerequisites, not redesigned here. Before implementation planning, verify their
current filenames in this checkout; do not rely on draft filenames from older notes.

## Problems being solved

| #    | Symptom                                                      | Root cause                                                   |
| ---- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 3    | LLM dumps entire `get_positions` JSON to `/large_tool_results/` and post-processes 64 positions in `run_python` instead of querying the DB | `get_positions` returns the full `product_kwargs` blob (including daily KO/KI schedules) inline; no SQL-grade filter tools exist over those terms |
| 4    | LLM writes Python in chat and asks for confirmation instead of invoking `run_python`; sandbox can't ingest large prior tool results | `python-analysis-policy` says "preview first"; `run_python` is unconditionally HITL-gated; Pyodide sandbox has no path to read `/large_tool_results/<id>` |
| 5    | Each user turn re-fetches data, persona has no memory of prior turn; the orchestrator restates the same `get_positions` brief every time | Subagents are ephemeral (`task()` invocation); `_EXCLUDED_STATE_KEYS` drops `messages`; orchestrator brief is treated as self-contained; no "session" abstraction across user turns |

---

## Architectural theme

Move state out of message history and into persistent layers (DB tables, indexed
artifact ledger, structured files). Message history becomes ephemeral and compactable;
all financial truth (positions, valuations, risk runs, reports, findings) is durable
and never compacted. The workspace decomposes into workflows; each workflow owns a
task graph plus a per-workflow orchestrator session and per-persona worker sessions —
all sharing a workspace-level artifact/evidence/event ledger.

## Framework stance — keep ReAct + DeepAgents

This design remains a **ReAct agent framework** design. The executable reasoning loop
is still LangChain's agent loop: model decides, calls tools, observes tool results,
and iterates until a final answer or interrupt. DeepAgents is the preferred harness
because it already wraps LangChain `create_agent` / LangGraph with the pieces this
desk needs: subagents, filesystem backends, skills, summarisation/offload, checkpointer
support, and HITL middleware.

**Default decision:** keep `create_deep_agent(...)` as the workflow orchestrator
runtime and keep DeepAgents `subagents=` for personas. The app owns the desk control
plane around it — workflows, typed tasks, context packs, artifact/evidence ledgers,
snapshot freshness, leases, and UI routing — but it should not hand-roll a replacement
agent loop unless implementation proves a concrete DeepAgents blocker.

**Evidence required to move away from DeepAgents:** name the exact API seam that fails
(for example: task-scoped HITL resume cannot be made to target the paused graph even
with task-scoped `thread_id`, configurable metadata, middleware, or `CompiledSubAgent`),
show the failing test / trace, and document why the DeepAgents alternatives
(`SubAgent`, `CompiledSubAgent`, `AsyncSubAgent`, middleware, config, or backend
extension) cannot satisfy the requirement. Without that evidence, the implementation
path is "extend DeepAgents with app-owned metadata and middleware", not "replace it."

Local repo evidence as of this spec revision: `backend/app/services/agents.py` builds
and streams a `build_orchestrator(...)` graph; `backend/app/services/deep_agent/orchestrator.py`
calls `create_deep_agent(..., subagents=all_personas(...), interrupt_on=..., checkpointer=...)`;
and the installed stack is `deepagents==0.6.2`, `langchain==1.3.1`, `langgraph==1.2.0`.

```
┌─────────────────────────────────────────────────────────────┐
│  Persistent layer (never compacted)                         │
│  ─ Structured DB tables (Issue 3): canonical position data  │
│  ─ session_artifacts ledger (Issue 5)                       │
│  ─ /session/findings/, /trading_desk/, /artifacts/ files    │
│  ─ /large_tool_results/<id> (raw tool result blobs)         │
├─────────────────────────────────────────────────────────────┤
│  Ephemeral layer (compactable on threshold)                 │
│  ─ AI reasoning text between tool calls                     │
│  ─ Old read-tool eviction stubs                             │
│  ─ Pre-compaction summaries (themselves compactable)        │
└─────────────────────────────────────────────────────────────┘
```

## Cross-cutting structure

```
Workspace (thread)
├── workspace state ─────────────► workflows table
│                                   session_artifacts (ledger)
│                                   artifact_evidence_refs
│                                   domain_events
│                                   context_packs
│                                   structured DB tables (positions, runs, …)
│
├── Workspace router  (thin, deterministic-first; LLM later)
│   └── classifies user turn → picks/creates workflow_id
│
├── Workflow #wf_1 "Snowball book risk review"  status=active
│   ├── orchestrator session  (control plane: task graph, HITL, synthesis)
│   ├── task graph
│   │     ├── task_a  fetch_position_summaries   completed   artifact_id=...
│   │     ├── task_b  compute_barrier_proximity  completed   artifact_id=...
│   │     └── task_c  synthesise_review          ready
│   └── persona sessions (workers)
│         ├── risk_manager × episode_1   status=closed
│         └── trader × episode_1         status=closed
│
└── Workflow #wf_2 "RFQ-42 approval"  status=awaiting_approval
    ├── orchestrator session
    ├── task graph (…)
    └── persona sessions (…)

User turn N
   → workspace router classifies (new_workflow | continue_workflow |
                                   status_query | cross_workflow_query)
   → routes to workflow_id
   → workflow orchestrator owns the turn (may delegate to a persona session)
```

**Session keying** (LangGraph checkpointer):

- Workspace router:        `thread:{tid}:router`
- Workflow orchestrator:   `thread:{tid}:workflow:{wfid}:orchestrator` (legacy backfill may reuse `str(thread_id)`)
- Persona session row:     `thread:{tid}:workflow:{wfid}:persona:{persona}:episode:{eid}` (logical namespace)
- Worker/task DeepAgent run: `<session.checkpointer_key>:task:{task_id}` (the LangGraph `thread_id`)

User turns are sent to the workflow orchestrator's `checkpointer_key` directly, so
the orchestrator owns the full visible conversation until `/compact` or `/clear`
starts a new orchestrator session. DeepAgents subagent calls inherit the parent run
config, but subagents only receive the brief/context the orchestrator gives them and
return their work to the orchestrator. Task-scoped physical keys are reserved for
worker/task executions that need independent replay or HITL resume; they are not the
normal wrapper around a user turn.

**Why this shape:** the router is thin (state ≈ `workflows` table + current user input) so it
never grows into a second long-lived agent brain. The per-workflow orchestrator stays
focused — RFQ approval, portfolio review, and risk memos don't contaminate each other.
Persona sessions are scoped narrowly enough that resume + freshness rules are tractable.

**Findings exchange:** workers emit structured artifact rows (typed JSON) into the ledger.
A human-readable `/session/findings/<...>.md` is a *rendering* of the row, not the primary
exchange. Other workers query the ledger; the markdown is for the user.

**Truth contract:** LLM-produced artifacts default to `kind='claim'` — durable but
provisional. A claim becomes **load-bearing for downstream automation** only when
bound to one of three evidence kinds:

1. **`deterministic_run`** — a `risk_run_id` / `valuation_run_id` produced by the
   numeric engine.
2. **`snapshot`** — a tuple of canonical snapshot ids referenced by a deterministic
   query (the query result is reproducible from snapshot inputs).
3. **`human_approval`** — a recorded approval id from the HITL flow.

`agent_attestation` (the binding `(persona, context_pack_id)`) is also recorded but
is **provenance, not truth.** It tells future readers "which worker said this, with
what inputs" so claims can be reproduced or disputed. It does NOT promote a claim to
load-bearing status. The orchestrator enforces this when planning dependent tasks.

---

# Section A — Issue 3: Structured position tables (all 10 product types)

## A.1 Tiered schema

**Tier 1 — universal option terms (one row per option Position):**

```sql
option_core_terms (
    position_id  INT PK FK→positions.id ON DELETE CASCADE,
    strike       FLOAT,                  -- NULL for touch products
    expiry_date  DATE NOT NULL,
    option_type  VARCHAR(8),             -- 'call'|'put'|NULL (touch/double-barrier)
    side         VARCHAR(8) NOT NULL,    -- 'long'|'short'
    currency     VARCHAR(8) NOT NULL,
    notional     FLOAT
)
```

**Tier 2 — per-family extension tables:**

```sql
single_barrier_terms (
    position_id  INT PK FK,
    barrier      FLOAT NOT NULL,
    barrier_type VARCHAR(4) NOT NULL,    -- 'UI'|'UO'|'DI'|'DO'
    rebate       FLOAT
)

double_barrier_terms (
    position_id    INT PK FK,
    upper_barrier  FLOAT NOT NULL,
    lower_barrier  FLOAT NOT NULL,
    barrier_kind   VARCHAR(4) NOT NULL,  -- 'KI'|'KO'|'OT'
    rebate         FLOAT
)

sharkfin_terms (
    position_id        INT PK FK,
    participation_rate FLOAT NOT NULL,
    coupon             FLOAT
)

asian_terms (
    position_id      INT PK FK,
    averaging_method VARCHAR(16) NOT NULL,  -- 'arithmetic'|'geometric'
    averaging_kind   VARCHAR(8)  NOT NULL,  -- 'price'|'strike'
    n_observations   INT NOT NULL
)
asian_averaging_dates (
    position_id      INT FK,
    observation_date DATE NOT NULL,
    sequence         INT  NOT NULL,
    PRIMARY KEY (position_id, observation_date)
)

snowball_terms (
    position_id    INT PK FK,
    initial_price  FLOAT NOT NULL,
    ki_barrier     FLOAT NOT NULL,
    coupon         FLOAT NOT NULL,
    start_date     DATE NOT NULL,
    knocked_in     BOOL NOT NULL DEFAULT FALSE,
    ki_observation VARCHAR(20) NOT NULL,
    payoff_kind    VARCHAR(40) NOT NULL,
    legacy_kwargs  JSON
)
snowball_ko_schedule (
    id                INT PK,
    position_id       INT FK,
    observation_date  DATE NOT NULL,
    ko_level          FLOAT NOT NULL,
    sequence          INT  NOT NULL,
    UNIQUE(position_id, observation_date)
)
```

**Tier 3 — generic barrier-state cache:**

```sql
position_barrier_state (
    position_id           INT PK FK,
    nearest_barrier_kind  VARCHAR(8),    -- 'KO'|'KI'|'OT'|'UB'|'LB'
    nearest_barrier_level FLOAT,
    nearest_barrier_date  DATE,
    days_to_nearest       INT,
    last_computed_at      TIMESTAMP NOT NULL
)
```

**`positions.kwargs_migrated_at TIMESTAMP NULL`** — when set, the structured tables are
authoritative; when null, fall back to `product_kwargs`.

## A.2 Product → table mapping

| product_type          | core | single_barrier | double_barrier | sharkfin | asian | snowball | barrier_state |
| --------------------- | :--: | :------------: | :------------: | :------: | :---: | :------: | :-----------: |
| EuropeanVanillaOption |  ✓   |                |                |          |       |          |               |
| AmericanOption        |  ✓   |                |                |          |       |          |               |
| AsianOption           |  ✓   |                |                |          |   ✓   |          |               |
| BarrierOption         |  ✓   |       ✓        |                |          |       |          |       ✓       |
| DoubleBarrierOption   |  ✓   |                |       ✓        |          |       |          |       ✓       |
| OneTouchOption        |  ✓   |       ✓        |                |          |       |          |       ✓       |
| DoubleOneTouchOption  |  ✓   |                |       ✓        |          |       |          |       ✓       |
| SingleSharkfinOption  |  ✓   |       ✓        |                |    ✓     |       |          |       ✓       |
| DoubleSharkfinOption  |  ✓   |                |       ✓        |    ✓     |       |          |       ✓       |
| SnowballOption        |  ✓   |                |                |          |       |    ✓     |       ✓       |

## A.3 Tool surface

**Universal:**

| Tool                                                         | Behaviour                                                    |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `get_position_summaries(portfolio_id, fields?, limit?=200)`  | Joins core + relevant Tier-2 to promote terms into slim rows. No `product_kwargs`. |
| `get_positions(portfolio_id, fields?, …)`                    | Existing; refactored. Default omits `product_kwargs`; `fields="all"` includes it. |
| `query_positions_near_barrier(portfolio_id, spot, within_pct, kind?)` | Pure read of `position_barrier_state`. Works for all barrier-bearing products. |
| `query_positions(filter, select, order_by?, limit?=200)`     | Structured-filter escape hatch. Allowlist covers Tier-1/2/3 columns. |

**Product-family specific:**

| Tool                                                         | Family                              |
| ------------------------------------------------------------ | ----------------------------------- |
| `get_option_core_terms(position_ids)`                        | All options                         |
| `get_barrier_terms(position_ids)`                            | Merged single + double barrier rows |
| `get_sharkfin_terms(position_ids)`                           | Sharkfins                           |
| `get_asian_schedule(position_id)`                            | Asian averaging dates               |
| `get_snowball_terms(position_ids)`                           | Snowball-specific                   |
| `get_snowball_ko_schedule(position_id, from_date?, limit?=20)` | Snowball KO schedule                |

The generic `query_positions` uses a structured filter (NOT raw SQL):

```python
query_positions(
    portfolio_id=6,
    filter=[
        {"col": "underlying",                          "op": "=", "value": "000852.SH"},
        {"col": "product_type",                        "op": "=", "value": "SnowballOption"},
        {"col": "snowball.knocked_in",                 "op": "=", "value": False},
        {"col": "barrier_state.nearest_barrier_level", "op": "<", "value": 8808.91 * 1.05},
    ],
    select=[
        "id",
        "underlying",
        "snowball.ki_barrier",
        "barrier_state.nearest_barrier_level",
        "barrier_state.nearest_barrier_date",
    ],
    order_by=("barrier_state.nearest_barrier_date", "asc"),
    limit=200,
)
```

Backend validates columns against the allowlist (`positions.*`, `option_core_terms.*`,
`single_barrier_terms.*`, `double_barrier_terms.*`, `sharkfin_terms.*`, `asian_terms.*`,
`asian_averaging_dates.*`, `snowball_terms.*`, `snowball_ko_schedule.*`,
`position_barrier_state.*`), compiles to SQLAlchemy, enforces `limit ≤ 1000`.

## A.4 Service layer

- `services/domains/option_core.py`, `barrier.py`, `sharkfin.py`, `asian.py`, `snowball.py` — per-family read modules.
- `services/domains/positions_query.py` — structured-filter compiler.
- `services/domains/barrier_state.py` — refreshes `position_barrier_state`. Runs on import, daily job, on-demand.
- `presenters/positions.py:shape_position_summary` — new slim presenter; `shape_position` keeps `product_kwargs` for `fields="all"`.

## A.5 Migration

| Phase     | What                                                         |             Reversible             |
| --------- | ------------------------------------------------------------ | :--------------------------------: |
| 0         | Alembic migration creates all tables + `positions.kwargs_migrated_at`. No backfill. |                 ✓                  |
| 1         | Per-family backfill scripts: Snowball → SingleBarrier → DoubleBarrier → Sharkfin → Asian → Vanilla/American. Each parses `product_kwargs`, writes to relevant tables, sets `kwargs_migrated_at`. Idempotent. |                 ✓                  |
| 2         | New tools land. Existing tools route: structured tables if migrated, else `product_kwargs`. |                 ✓                  |
| 3         | Import path writes both structured tables AND `product_kwargs` (parallel write). |                 ✓                  |
| 4 (later) | Per-family: drop parallel `product_kwargs` write, make column nullable-by-default. | Per-family undo via re-derivation. |

## A.6 Coverage & out of scope

**Scope:** the 10 product families enumerated in `quantark.py:71-81` (European/American
Vanilla, Asian, Barrier, DoubleBarrier, OneTouch, DoubleOneTouch, SingleSharkfin,
DoubleSharkfin, Snowball). These have explicit schema in A.1-A.2.

**Out of scope for v1 of Section A** — additional product types active via the
`try_solve` registry but **not** covered by the new tables in this design:

- **Phoenix** (`PhoenixOption`) — `try_solve_registry.py:332`. Will get its own
  `phoenix_terms` + `phoenix_coupon_schedule` tables in a follow-on Section A.v2 ticket.
- **Cash-or-nothing Digital** (`CashOrNothingDigitalOption`) —
  `try_solve_registry.py:378`. Modelled later; until then remains `product_kwargs`-backed.
- **Airbag / Airbag Spread** (`AirbagOption`) — `try_solve_registry.py:435`. Same
  treatment.
- **Futures** (`Futures`) — `try_solve_registry.py:485`. Not an option; would need its
  own root table (`futures_terms`) rather than `option_core_terms`.
- **Range Accrual, KO-Reset Snowball, and other variants** referenced in
  `services/try_solve.py` / `services/rfq.py` — same treatment.

For all of these, **`positions.kwargs_migrated_at` stays NULL** and the new tools fall
back to `product_kwargs` exactly as today. The structured-table tools simply do not
match on these rows; the legacy `get_positions(fields="all")` path remains the
authoritative read.

**Variant-specific extensions** (e.g. step-down Snowball, limited-downside Snowball)
are stored in `snowball_terms.legacy_kwargs` JSON until those variants warrant their
own columns.

---

# Section B — Issue 4: Sandbox `@file:` handle + HITL reclassification

## B.1 Inline `@file:` markers in `payload`

`sandbox_tool.py:_build_wrapper` gains a resolver step BEFORE base64 encoding:

```python
def _resolve_file_markers(value, backend, depth=0):
    if depth > 8:
        raise ValueError("payload nesting too deep for @file: resolver")
    if isinstance(value, str) and value.startswith("@file:"):
        path = value[len("@file:"):]
        result = backend.read(path)
        if result.error:
            raise FileMarkerError(f"@file: read failed for {path}: {result.error}")
        text = file_data_to_string(result.file_data)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if isinstance(value, dict):
        return {k: _resolve_file_markers(v, backend, d=depth+1) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_file_markers(v, backend, d=depth+1) for v in value]
    return value
```

Rules:

- Marker is the entire string value. `"@file:/large_tool_results/abc"` ✓; `"prefix @file:..."` is a literal.
- Resolution recurses through dicts/lists, capped at depth 8.
- JSON files inject parsed objects; non-JSON inject as strings.
- Resolution runs through the same backend the personas use — FilesystemPermissions enforced.
- Per-call budget: ≤20 MB total resolved bytes.

## B.2 HITL reclassification

```python
class RunPythonInput(BaseModel):
    code: str
    payload: dict = {}
    timeout_s: int = 30
    description: str | None = None
    writes_artifacts: bool = Field(
        default=False,
        description=(
            "Set True when your script will write files to /sandbox_out/ "
            "for downstream persistence. Triggers a one-click approval card. "
            "Set False (default) for pure analysis — runs without HITL."
        ),
    )
```

- `hitl.py:interrupt_on_config` becomes argument-aware for `run_python`: HITL fires only when call args have `writes_artifacts=True`.
- `_RISK_LEVEL_BY_TOOL["run_python"]` resolves to `"write"` only when `writes_artifacts=True`; otherwise `"read"` (no HITL even under standard policy).
- Honesty check: post-sandbox, if `writes_artifacts=False` but `/sandbox_out/` artifacts were produced, the wrapper drops them with a logged warning.
- If LangChain's `InterruptOnConfig` lacks a predicate hook, we add a thin middleware in `services/deep_agent/run_python_hitl.py` that maps the call's args to a pass-through decision before the interrupt fires.

## B.3 HITL UI: description-only

Frontend change (separate ticket):

- HITL card renders: tool label + `description` + Approve/Reject buttons.
- "Show details" disclosure expands to code/payload for users who want to audit.
- Apply same treatment to all HITL-gated tool cards for consistency.

## B.4 Policy update

Rewrite `skills/meta/python-analysis-policy.md` to:

- Drop "preview the plan before invoking."
- Instruct direct invocation with `description=`.
- Document `writes_artifacts` flag.
- Document `@file:` payload markers.

## B.5 Edge cases

- `@file:` recursion bomb → depth cap, no re-scan of resolved JSON.
- Path scoping → backend.read enforces FilesystemPermissions; deny rule still catches `/etc/passwd`.
- `writes_artifacts=True` with no artifacts produced → allowed, no error.
- `writes_artifacts=False` with artifacts produced → artifacts dropped, warning logged.

---

# Section C — Issue 5: Workspace → Workflow → Session, with artifact/evidence/event ledger

This section is intentionally heavier than the deepseek-long symptom would suggest.
Treating the symptom (forgotten context across turns) with a single "session persistence"
patch would leave the system blind in ways that matter for a financial desk: stale
assumption control, replayability, and the difference between an LLM *claim* and
load-bearing *truth*. The primitives below are designed to fix the symptom and earn
those properties at the same time.

## C.1 Vocabulary

| Term                 | Definition                                                   |
| -------------------- | ------------------------------------------------------------ |
| **Workspace**        | The top-level container (today's `agent_threads` row). Owns workflows, the artifact ledger, the event log. Long-lived; not workflow-scoped. |
| **Workspace router** | Thin classifier (deterministic first; LLM later) that maps each incoming user turn to one of `new_workflow`/`continue_workflow`/`status_query`/`cross_workflow_query`. Its state is the `workflows` table + the current user input. **Not** another long-lived agent brain. |
| **Workflow (case)**  | A unit of intent ("Snowball book review", "RFQ-42 approval", "Audit Q1 reports"). Owns a task graph and its own orchestrator session. Multiple workflows can coexist in one workspace. |
| **Agent task**       | Typed node in a workflow's DAG. Has input/output contracts, dependency links, a worker assignment, and a status lifecycle. Replaces free-form `dispatch(persona, brief)`. |
| **Agent session**    | A conversational instance scoped to `(workflow_id, persona, episode_id)`. An *episode* is one sitting; sessions can be resumed only inside the same workflow and within a freshness window. |
| **Artifact**         | A typed row in `session_artifacts`. Examples: `kind='deterministic_query'` (a structured DB read), `kind='claim'` (an LLM finding), `kind='render'` (a markdown file derived from a structured row), `kind='sandbox_output'` (a `run_python` result), `kind='persisted_run'` (an attestation of a price_positions/run_risk run). |
| **Evidence ref**     | A row in `artifact_evidence_refs` binding an artifact to a source. **Load-bearing** kinds (graduate a claim to truth for automation): `deterministic_run` (risk_run_id/valuation_run_id), `snapshot` (canonical_snapshot_ids tuple), `human_approval` (approval_id). **Provenance-only** kinds (recorded for replay/audit but never promote a claim): `agent_attestation` ((persona, context_pack_id)), `context_pack` (context_pack_id). |
| **Domain event**     | An append-only entry in `domain_events`: `session_opened`, `task_started`, `task_blocked`, `hitl_requested`, `hitl_approved`, `artifact_created`, `claim_disputed`, `workflow_closed`, `snapshot_captured`, … |
| **Context pack**     | An immutable record of the inputs handed to a worker for one task invocation: the typed task brief, canonical snapshot ids in force, cited artifact refs, a short recent-session summary, scoped tool list. Hashed; `context_pack_id` is referenced by every artifact the worker emits. Enables replay and stale-assumption detection. |
| **Claim vs truth**   | LLM-produced artifacts default to `kind='claim'` — durable but provisional. Becomes load-bearing for *automation* only when bound to one of the three load-bearing evidence kinds (`deterministic_run`, `snapshot`, `human_approval`). `agent_attestation` is recorded for provenance/replay but does NOT promote a claim. Workers can read claims for orientation but must re-derive evidence before they themselves emit downstream load-bearing artifacts. |

## C.2 Data model (first migration creates ALL of these)

```sql
workflows (
    id            INT PK,
    thread_id     INT FK→agent_threads.id ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL,
    intent        VARCHAR(40) NOT NULL,        -- 'rfq'|'review'|'pricing'|'risk'|'reporting'|'ad_hoc'|…
    status        VARCHAR(16) NOT NULL,        -- 'active'|'awaiting_approval'|'paused'|'closed'|'abandoned'
    opened_by     VARCHAR(40) NOT NULL,        -- 'user'|'router'|'orchestrator'
    opened_at     TIMESTAMP NOT NULL,
    closed_at     TIMESTAMP NULL,
    canonical_snapshot_ids JSON NOT NULL,       -- e.g. {"market":42,"position_state":"2026-05-21T00:00Z"}
    summary       TEXT NULL                    -- written at close
)
INDEX (thread_id, status)

agent_sessions (
    id                INT PK,
    workflow_id       INT FK→workflows.id ON DELETE CASCADE,
    persona           VARCHAR(40) NOT NULL,    -- 'orchestrator'|'trader'|'risk_manager'|'high_board'|'router'
    episode_id        INT NOT NULL,            -- 1-indexed within (workflow_id, persona)
    status            VARCHAR(16) NOT NULL,    -- 'active'|'closed'|'archived'
    checkpointer_key  VARCHAR(160) NOT NULL UNIQUE,
    opened_at         TIMESTAMP NOT NULL,
    closed_at         TIMESTAMP NULL,
    closed_reason     VARCHAR(40) NULL,        -- 'return_to_orchestrator'|'freshness_expired'|'snapshot_changed'|'user_abandoned'
    last_summary      TEXT NULL,
    UNIQUE (workflow_id, persona, episode_id)
)
INDEX (workflow_id, persona, status)

agent_tasks (
    id              INT PK,
    workflow_id     INT FK→workflows.id ON DELETE CASCADE,
    task_type       VARCHAR(80) NOT NULL,     -- 'fetch_position_summaries' | 'compute_barrier_proximity' | 'propose_run_risk' | 'synthesise_review' | 'converse_with_user' | …
    inputs          JSON NOT NULL,            -- typed input contract (validated)
    depends_on      JSON NOT NULL,            -- list of agent_task.id
    assigned_persona VARCHAR(40) NOT NULL,
    assigned_session_id INT FK→agent_sessions.id NULL,  -- set when work begins
    status          VARCHAR(16) NOT NULL,     -- 'planned'|'ready'|'in_progress'|'awaiting_hitl'|'completed'|'failed'|'abandoned'
    context_pack_id INT FK→context_packs.id NULL,
    output_artifact_id INT FK→session_artifacts.id NULL,
    error           TEXT NULL,
    opened_at       TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL,
    closed_at       TIMESTAMP NULL
)
INDEX (workflow_id, status), INDEX (assigned_session_id)

session_artifacts (
    id              INT PK,
    workflow_id     INT FK→workflows.id ON DELETE CASCADE,
    session_id      INT FK→agent_sessions.id NULL,
    task_id         INT FK→agent_tasks.id NULL,
    kind            VARCHAR(40) NOT NULL,
    -- kinds: 'deterministic_query'|'claim'|'finding'|'render'|'sandbox_output'|'persisted_run'|'report'|'tool_result'|'plan'
    schema_version  INT NOT NULL DEFAULT 1,
    -- (kind, schema_version) → Pydantic model in ARTIFACT_PAYLOAD_REGISTRY.
    -- Same versioning discipline as domain_events.schema_version (see C.8).
    title           VARCHAR(200) NOT NULL,
    payload         JSON NOT NULL,            -- structured body, validated against (kind, schema_version)
    rendered_path   VARCHAR(400) NULL,        -- path to a human-readable rendering, when applicable
    tool_call_id    VARCHAR(80) NULL,
    tool_name       VARCHAR(80) NULL,
    context_pack_id INT FK→context_packs.id NULL,
    created_at      TIMESTAMP NOT NULL,
    pinned          BOOL NOT NULL DEFAULT FALSE,
    superseded_by   INT FK→session_artifacts.id NULL  -- when a later artifact restates / corrects this one
)
INDEX (workflow_id, kind, created_at)

artifact_evidence_refs (
    id              INT PK,
    artifact_id     INT FK→session_artifacts.id ON DELETE CASCADE,
    evidence_kind   VARCHAR(40) NOT NULL,
    -- 'deterministic_run' (risk_run_id/valuation_run_id) |
    -- 'snapshot' (market_snapshot_id) |
    -- 'table_state_at' ({table, snapshot_at}) |
    -- 'human_approval' (approval_id) |
    -- 'agent_attestation' (persona + context_pack_id) |
    -- 'context_pack' (context_pack_id)
    evidence_payload JSON NOT NULL,
    bound_at        TIMESTAMP NOT NULL
)
INDEX (artifact_id), INDEX (evidence_kind)

-- Split into two tables: a globally content-addressed payload table and a
-- per-invocation binding table. Identical assemblies (same task_type, persona,
-- brief, snapshot, artifacts, tools, prompt, model) share ONE payload row but
-- get distinct context_packs rows so workflow/task provenance is never lost.
context_pack_payloads (
    id              INT PK,
    content_hash    VARCHAR(80) NOT NULL UNIQUE,
    -- Stable hash over the canonicalised stable_payload below.
    stable_payload  JSON NOT NULL,
    -- stable_payload schema (canonicalised before hashing):
    -- {
    --   "task_type":                  "compute_barrier_proximity",
    --   "assigned_persona":           "risk_manager",
    --   "task_brief":                 {...},   -- typed task input, with deterministic key order
    --   "canonical_snapshot_ids":     {...},   -- pinned snapshot tuple (see C.4.1)
    --   "cited_artifact_ids":         [...],   -- sorted asc
    --   "tools_scope":                [...],   -- sorted asc
    --   "tool_signature_hash":        "sha256:...",  -- hash over {tool_name: schema_version, ...} for the scoped tools
    --   "recent_session_summary_hash":"sha256:...",  -- hash of the summary text, not the text
    --   "prompt_revision_hash":       "sha256:...",  -- hash of (persona identity prompt + composed policy fragments) at assembly time
    --   "model_id":                   "deepseek-v4-flash"
    -- }
    created_at      TIMESTAMP NOT NULL
)

context_packs (
    id              INT PK,
    workflow_id     INT FK→workflows.id ON DELETE CASCADE,
    task_id         INT FK→agent_tasks.id NULL,        -- NULL only for orchestrator/router packs that pre-date a task
    payload_id      INT FK→context_pack_payloads.id NOT NULL,
    metadata        JSON NOT NULL,
    -- metadata schema (informational only, never hashed):
    -- {
    --   "recent_session_summary": "...",     -- the actual summary text (corresponds to recent_session_summary_hash)
    --   "assembled_at": "2026-05-22T08:15Z",
    --   "assembler_version": "1.0"
    -- }
    created_at      TIMESTAMP NOT NULL
)
INDEX (workflow_id, created_at), INDEX (payload_id), INDEX (task_id)

domain_events (
    id              INT PK,
    workflow_id     INT FK→workflows.id ON DELETE CASCADE,
    session_id      INT FK→agent_sessions.id NULL,
    task_id         INT FK→agent_tasks.id NULL,
    artifact_id     INT FK→session_artifacts.id NULL,
    kind            VARCHAR(40) NOT NULL,
    -- 'workspace_routed'|'workflow_opened'|'workflow_closed'|
    -- 'session_opened'|'session_closed'|'session_resumed'|
    -- 'task_planned'|'task_started'|'task_blocked'|'task_resumed'|
    -- 'task_completed'|'task_failed'|
    -- 'hitl_requested'|'hitl_approved'|'hitl_rejected'|
    -- 'artifact_created'|'artifact_superseded'|'artifact_gc_evicted'|
    -- 'claim_disputed'|
    -- 'snapshot_captured'|'snapshot_invalidated'|'position_version_bumped'
    schema_version  INT NOT NULL DEFAULT 1,    -- (kind, schema_version) → Pydantic model in EVENT_PAYLOAD_REGISTRY
    payload         JSON NOT NULL,             -- validated against the registry on insert
    actor           VARCHAR(40) NOT NULL,      -- 'user'|'router'|'orchestrator'|'persona:trader'|'persona:risk_manager'|'system'
    occurred_at     TIMESTAMP NOT NULL
)
INDEX (workflow_id, occurred_at), INDEX (kind, occurred_at)
```

**Changes to existing tables:**

- `agent_threads`: + `active_workflow_id INT FK NULL` (workspace router pins last-touched workflow for the "continue" case).
- `agent_messages`: + `session_id INT FK NULL`, + `workflow_id INT FK NULL`. Columns are added **NULLABLE** so existing inserts (`AgentMessage(thread_id, role, character, content, meta)`) in `backend/app/main.py` and `backend/app/services/agents.py` keep working unchanged through C-mig-1. They're populated by C-mig-2's backfill + a dual-write hook on the legacy path, then tightened to `NOT NULL` in a dedicated migration **after** the feature flag has flipped to default-on and no legacy writers remain (C-mig-4, listed in C.12).

**Workspace meta workflow.** Every workspace owns one mandatory workflow row with
`intent='workspace_meta'`, `status='active'`. It hosts the router session and any
workspace-level messages (status replies, cross-workflow synthesis without a domain
session). This keeps `workflow_id`/`session_id` populatable even for "no domain
workflow yet" messages without weakening the schema or relying on the columns staying
nullable forever.

The router classifications described in C.3 always persist their reply against the
meta workflow's router session. Domain workflows are opened/continued separately.

**Partial uniqueness on `agent_sessions`:**

- `UNIQUE (workflow_id, persona) WHERE status = 'active'` — at most one active session per (workflow, persona). Enforces the lifecycle invariant; the engine raises before two episodes can race.
- Existing `UNIQUE (workflow_id, persona, episode_id)` ensures historical episodes have monotonic ids.

**Migration of existing threads:** for each thread, create one workspace meta workflow,
one router session inside it, one domain workflow `intent='ad_hoc'` for current
conversation continuity, one orchestrator session within the domain workflow, one
default context_pack, and assign all existing messages to that domain workflow's
orchestrator session. Existing LangGraph checkpointer keys (currently `str(thread_id)`)
are reused as the orchestrator session's `checkpointer_key` to preserve in-flight state.

## C.3 Workspace router

```
                 User turn N (text)
                          │
                          ▼
              ┌──────────────────────┐
              │  workspace router    │
              │  state:              │
              │   - workflows table  │
              │   - active_workflow  │
              │   - user text        │
              └─────────┬────────────┘
                        │
              classifies into one of:
                        │
   ┌────────────┬───────┴────────┬────────────────────┐
   ▼            ▼                ▼                    ▼
new_workflow  continue_workflow  status_query   cross_workflow_query
   │            │                │                    │
opens new     routes to          replies      synthesises across
workflow,     workflow's         from         multiple workflows'
sets it as    orchestrator       workflows    ledgers without
active                           table        opening a session
```

**Phase 1 (deterministic):**

For all rules below, "active workflows" means `status='active' AND intent != 'workspace_meta'`. The meta workflow is always active by construction; it's the router's *home*, not a candidate for `continue_workflow`.

- If exactly one **domain** workflow is `status='active'` and the user message lacks explicit workflow markers → `continue_workflow` to it.
- If the user explicitly names a workflow (`"continue the snowball review"`, `"workflow #42"`) → resolve and route. The meta workflow is never named by users; it's a system row.
- If the message matches a small grammar of status patterns (`"what's in flight"`, `"any pending approvals"`) → `status_query`. Status replies persist into the meta workflow's router session.
- If the message matches a cross-workflow pattern (`"compare workflows #41 and #42"`) → `cross_workflow_query`. Same persistence target (meta workflow's router session) unless escalation to a new workflow is needed.
- Otherwise → ask one clarifying question OR (in YOLO mode) open a `new_workflow` with `intent='ad_hoc'` (NOT `'workspace_meta'`) and a router-generated title.

Counting rule for "active domain workflows" is canonical and used identically by both the router and any introspection tools (e.g. `list_in_flight_workflows`). Tests must include a fixture where a workspace has only the meta workflow → router treats it as "zero active workflows" and asks/opens accordingly.

**Phase 2 (later):** swap the rule-driven classifier for a small LLM classifier with the
same output schema, sharing the deterministic fast-paths. Same `context_packs`
discipline applies — the router LLM consumes a tiny pack, not the full history.

## C.4 Workflow lifecycle

```
opened (by router or user) → active → [awaiting_approval ↔ active] → closed | abandoned
```

- `opened`: router or orchestrator creates the row. `canonical_snapshot_ids` is captured at this moment — it's the "as-of" basis for the workflow's reasoning.
- `active`: orchestrator is planning/executing tasks.
- `awaiting_approval`: a HITL gate is pending in some task; router won't auto-route new user turns into this workflow unless the user is approving/rejecting.
- `closed`: orchestrator emits a final summary; status flipped; sessions archived.
- `abandoned`: user-driven or timeout. Sessions archived; artifacts retained.

A workflow's `canonical_snapshot_ids` may be refreshed by an explicit "refresh workflow"
event (emitted by orchestrator when stale). Existing context_packs are NOT mutated;
new tasks get new packs against the refreshed snapshot. Old artifacts remain in the
ledger with their original snapshot reference — they're not deleted, just superseded.

### C.4.1 Canonical snapshot contract (v1)

A `canonical_snapshot_ids` value is the immutable "as-of" basis of a workflow.
**Every snapshot carries a `scope_kind` discriminant** that tells the freshness
checker which equality rule to apply. This keeps `canonical_snapshot_ids NOT NULL`
honest across non-portfolio workflows (ad-hoc, status, reporting, RFQ).

#### Scope kinds and minimal contents

```jsonc
// scope_kind = "workspace_meta"  (router/status workflows)
{
  "scope_kind":  "workspace_meta",
  "captured_at": "2026-05-22T08:00:00Z"
}

// scope_kind = "ad_hoc"          (router-opened, no domain entity yet)
{
  "scope_kind":  "ad_hoc",
  "captured_at": "2026-05-22T08:00:00Z"
}

// scope_kind = "portfolio_pricing"  (review, risk, pricing — current Snowball case)
{
  "scope_kind":            "portfolio_pricing",
  "portfolio_id":          6,
  "position_set_hash":     "sha256:<hash over (portfolio_id, position_id, version)>",
  "market_snapshot_id":    42,
  "pricing_profile_id":    7,
  "accounting_date":       "2026-05-22",
  "captured_at":           "2026-05-22T08:00:00Z"
}

// scope_kind = "rfq"             (quoting/approval flows)
{
  "scope_kind":         "rfq",
  "rfq_id":             42,
  "rfq_state_hash":     "sha256:<over RFQ row + terms + side + tenor>",
  "market_snapshot_id": 42,
  "captured_at":        "2026-05-22T08:00:00Z"
}

// scope_kind = "reporting"       (report generation / display)
{
  "scope_kind":         "reporting",
  "report_id":          12,
  "report_state_hash":  "sha256:<over report definition + parameter set>",
  "captured_at":        "2026-05-22T08:00:00Z"
}
```

#### Field rules (when present)

- **`scope_kind`** — required on every snapshot. Discriminant used by the freshness
  checker.
- **`position_set_hash`** — canonical hash over the set of position rows the workflow
  reasons about. Computed by sorting `(portfolio_id, position_id, position.version)`
  ascending and hashing the byte-stable join. `position.version` is a new monotonic
  column on `positions` (see C.12 step C-mig-1 and C.4.1.1).
- **`market_snapshot_id`** — FK to the existing `market_snapshots` rows. Pins
  spot / rates / vol surfaces used by the workflow.
- **`pricing_profile_id`** — FK to the existing pricing parameter profile.
- **`rfq_state_hash`** — hash over the RFQ row, its draft terms, side, and tenor.
  Bumps when the RFQ moves between states or its draft mutates.
- **`report_state_hash`** — hash over the report definition + parameter set the
  workflow is operating on.
- **`accounting_date`** — the business-date anchor. Not the pricing valuation date.
- **`captured_at`** — wall-clock at capture; informational, never used in equality.

#### Freshness equality (per scope_kind)

| scope_kind          | Equality rule                                                |
| ------------------- | ------------------------------------------------------------ |
| `workspace_meta`    | **Always equal** to itself — the router workflow doesn't go stale. Wall-clock freshness window does NOT apply. |
| `ad_hoc`            | **Always equal** to itself. Wall-clock window still applies via C.6's per-task-type freshness; ad-hoc workflows simply don't track domain-level invalidation. |
| `portfolio_pricing` | All of `(portfolio_id, position_set_hash, market_snapshot_id, pricing_profile_id, accounting_date)` equal. |
| `rfq`               | `(rfq_id, rfq_state_hash, market_snapshot_id)` equal.        |
| `reporting`         | `(report_id, report_state_hash)` equal.                      |

Comparing snapshots of different `scope_kind` always returns false. If a workflow
needs to migrate scope (e.g., an `ad_hoc` workflow that becomes a `portfolio_pricing`
one after the user pins a portfolio), the orchestrator emits a
`snapshot_captured` event with the new value; old artifacts cite the prior snapshot
unchanged.

#### Capture process

- On `workflow_opened`: orchestrator picks the `scope_kind` from the workflow's
  intent (e.g. `intent='rfq'` → `scope_kind='rfq'`) and the page context's entity
  ids. It computes the snapshot fields required for that kind, writes the JSON to
  `workflows.canonical_snapshot_ids`, and emits a `snapshot_captured` event.
- On refresh: a new `snapshot_captured` event is emitted with the new value and the
  prior value; downstream freshness checks catch stale sessions.

A scope-to-fields mapping lives in `services/deep_agent/snapshot.py:SCOPE_REGISTRY`
so adding a new `scope_kind` is a single-file change.

#### C.4.1.1 `position.version` bump policy

`position_set_hash` is meaningless without a stable bump policy on
`position.version`. The column bumps on changes to **economic** fields and stays
flat for **administrative/display** fields.

**Bumps (any one of these on an UPDATE triggers `version = version + 1`):**

| Field                                                        | Why it bumps                                                 |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `underlying`                                                 | Changes the price basis entirely.                            |
| `product_type`                                               | Changes the payoff.                                          |
| `product_kwargs` (any subkey)                                | Strike, barrier, KO schedule, KI level, coupon, observation dates, payoff_kind — all economic. |
| `engine_kwargs` (any subkey)                                 | Pricing approach (MC paths, vol model) affects every Greek + valuation. |
| `engine_name`                                                | Switches the pricing engine; same as above amplified.        |
| `quantity`                                                   | Linear exposure scale; affects every aggregate.              |
| `entry_price`                                                | PnL baseline; affects MTM calcs.                             |
| `status`                                                     | open ↔ closed / knocked-out ↔ active changes whether the row contributes. |
| `trade_effective_date`                                       | Shifts the lifecycle window; some products' barriers/coupons key off it. |
| **Structured-table mirrors** (Section A) — any change to `option_core_terms`, `single_barrier_terms`, `double_barrier_terms`, `sharkfin_terms`, `asian_terms`, `asian_averaging_dates`, `snowball_terms`, `snowball_ko_schedule` for the position | These are the canonical projections of `product_kwargs`; their writes are the new path and must keep parity. |

**Does NOT bump (administrative or display-only):**

| Field                                                        | Why it doesn't bump                                          |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `portfolio_id`                                               | Membership change; doesn't alter the position's economics. The portfolio's view membership belongs to portfolios, not positions. |
| `source_trade_id`                                            | Identifier; renaming doesn't change economics.               |
| `kwargs_migrated_at`                                         | Migration-only flag; flipping it changes which table reads from but not the result. |
| `position_barrier_state.*` (the cache)                       | Derived from the canonical tables; cache refresh is not a position mutation. |
| Display labels / comments / annotations columns (if/when added) | UI metadata.                                                 |

**Implementation:**

- SQLAlchemy ORM event hook (`before_update`) compares dirty fields against a bump-set
  whitelist. If any whitelisted field changed, increment `version`. Single source of
  truth so every write path inherits the policy.
- Bulk writes (import_otc_positions, import_position_market_inputs) iterate the same
  hook semantics — they MUST NOT bypass it.
- A `position_version_bumped` domain event is emitted alongside the position UPDATE,
  carrying the diff for audit. Downstream `snapshot_invalidated` events derive from
  workflows whose `position_set_hash` includes the bumped row.

## C.5 Task graph & typed task invocation

Tasks replace `dispatch(persona, brief)`. A persona never sees a free-form brief; it
sees a typed task with a `context_pack_id`.

```python
class TaskSpec(BaseModel):
    task_type: str             # registered task type
    inputs: dict[str, Any]     # validated against TaskRegistry[task_type].InputModel
    depends_on: list[int] = []
    assigned_persona: str      # orchestrator picks; tasks may carry a persona hint
```

**Registered task types** (initial set; extensible):

| Task type                      | Persona      | Inputs                                   | Output artifact kind                                         |
| ------------------------------ | ------------ | ---------------------------------------- | ------------------------------------------------------------ |
| `fetch_position_summaries`     | trader       | portfolio_id, fields                     | `deterministic_query`                                        |
| `compute_barrier_proximity`    | risk_manager | portfolio_id, spot, within_pct           | `deterministic_query`                                        |
| `interpret_snowball_terms`     | trader       | position_ids                             | `claim` (provenance: agent_attestation + context_pack; remains non-load-bearing until cited downstream by a deterministic task) |
| `propose_run_risk`             | risk_manager | portfolio_id, profile_id, valuation_date | `plan` → on approval, `persisted_run`                        |
| `synthesise_workflow_response` | orchestrator | citation_artifact_ids                    | `finding` (rendered to markdown)                             |
| `converse_with_user`           | (any)        | user_message                             | `claim` (chat reply)                                         |
| `run_analytic_script`          | trader/risk  | code, payload, writes_artifacts          | `sandbox_output`                                             |
| `…`                            |              |                                          |                                                              |

**Orchestrator scheduling (per workflow):**

1. On a user turn, the workspace router selects a workflow and sends the turn to that
   workflow's active orchestrator session. The orchestrator may answer directly or
   plan worker tasks. New worker tasks are inserted with `status='planned'`.
2. Tasks with all deps in `completed` flip to `'ready'`.
3. Orchestrator picks a `'ready'` task, asks the context assembler to build/find a context_pack, opens or resumes a persona session, hands the pack to the worker, status → `'in_progress'`.
4. Worker emits artifacts; on completion the task's `output_artifact_id` is set, status → `'completed'`. If the work requires HITL → `'awaiting_hitl'`; on approval/rejection → resume or `'failed'`.
5. Synthesis tasks (`synthesise_workflow_response`) close out the user-visible round: they cite artifact ids and produce a user-facing claim.

A task may be `'failed'` (and re-planned) or `'abandoned'` (workflow drops it). Errors and
state transitions all emit `domain_events`.

### C.5.1 DeepAgent TaskExecutor contract

The current desk runtime is a `create_deep_agent(..., subagents=all_personas(...))`
graph built in `backend/app/services/deep_agent/orchestrator.py`. The new design
keeps that as the default execution substrate. The workflow orchestrator itself is a
long-lived DeepAgents session and owns all user interaction. `TaskExecutor` is not a
replacement agent framework and not a wrapper for ordinary user turns; it is the
app-owned adapter that binds a ready **worker** `agent_task` to a task-scoped
DeepAgent ReAct run, records ledger rows, and updates workflow state.

**Execution model:**

```
                  agent_task.status = 'ready'
                            │
                            ▼
            ┌────────────────────────────────────┐
            │  TaskExecutor.invoke_deep_agent()  │
            └────────────────┬───────────────────┘
                             │
   1. acquire_or_open_session(task.workflow_id, task.assigned_persona)
   2. context_pack = assemble_context_pack(task, workflow, prior_summary)
   3. agent_task.context_pack_id ← context_pack.id
   4. agent_task.assigned_session_id ← session.id
   5. agent_task.status ← 'in_progress'
   6. domain_events: task_started
   7. build a task-scoped DeepAgent prompt from:
        - workflow objective
        - typed TaskSpec
        - context_pack stable payload + metadata
        - required output artifact schema
        - "delegate with `task` only to task.assigned_persona" when persona != orchestrator
                             │
                             ▼
        ┌────────────────────────────────────────────┐
        │  create_deep_agent ReAct graph             │
        │    thread_id=<session.key>:task:<task_id>  │
        │    configurable: workflow_id, session_id,  │
        │      task_id, context_pack_id, envelope    │
        │    subagents=all_personas(...)             │
        └──────────────────┬─────────────────────────┘
                           │
   The worker ReAct loop can answer directly for scoped worker tasks or use
   DeepAgents `task` to call the assigned persona when that is the registered
   execution mode. Tool calls, subagent stream
   events, interrupts, and oversized tool-result writes all carry the task config.
                           │
                           ▼
   On terminal state:
     - artifacts are written through LedgerWriter / CAS backend
     - agent_task.output_artifact_id ← final artifact id
     - agent_task.status ← 'completed' | 'failed' | 'awaiting_hitl'
     - session.status, last_summary updated
     - domain_events: task_completed | task_failed | hitl_requested
```

**DeepAgent graph factory** (`backend/app/services/deep_agent/orchestrator.py`,
evolving the existing `build_orchestrator(...)`):

- Evolves the existing `build_orchestrator(...)`; it still returns a
  `create_deep_agent(...)` graph.
- Personas remain DeepAgents `SubAgent` specs from `all_personas(...)`. Their tools
  are filtered by task scope via middleware/config and by the existing server-side
  capability gate; prompts stop accepting free-form briefs and instead require typed
  artifact output.
- The graph keeps DeepAgents middleware for filesystem access, skills, summarisation,
  offload, permissions, HITL, and subagent streaming. App-specific middleware adds
  workflow/session/task/context identifiers to tool calls and ledger writes.
- A direct LangChain `create_agent(...)`, custom LangGraph, or `CompiledSubAgent` is
  allowed only as a DeepAgents subagent implementation detail. It is not the default
  desk runtime.

**Checkpoint keying — session as namespace, task as resume scope.**

For the workflow orchestrator, the session's `checkpointer_key` is the literal
LangGraph `thread_id` for user turns. For worker/persona task execution, the session's
`checkpointer_key` is a **namespace**, and each executable task writes graph state
under:

```text
<session.checkpointer_key>:task:<task_id>
```

This preserves the ReAct execution model while making resume deterministic:

- **Orchestrator session reuse** sends the next user turn to the same orchestrator
  checkpoint, preserving conversation context until the user compacts or clears the
  session.
- **Worker session reuse** (when the freshness check in C.6 passes for a new task) reopens
  the session row, but the new task starts a fresh DeepAgent graph under a fresh
  task-scoped `thread_id`. The graph's prompt contains the context pack. No prior
  task's messages leak in through checkpoint history.
- **DeepAgents subagent calls** inherit the parent run config, so nested persona
  work still belongs to the same task-scoped checkpoint and carries the same
  `(workflow_id, session_id, task_id, context_pack_id)` config metadata.
- **Task resume** is the only path that reads back an existing checkpoint. It applies
  exclusively when the **same task** was paused at HITL and the DeepAgent run for that
  task needs to continue from where it stopped.

Two distinct domain events emit accordingly:

- `session_resumed` — session row flipped `closed → active`; new task starting fresh.
- `task_resumed` — same task continuing after `hitl_approved` / `hitl_rejected`.

**HITL resume mechanics:**

- When a DeepAgent run hits a HITL gate, the executor catches the interrupt, sets the
  task to `'awaiting_hitl'`, emits `hitl_requested`, and pauses. The user's approval
  flows through the existing endpoints (`/messages/{id}/actions/{aid}/confirm`,
  `.../dismiss`). On approve, the executor resumes the same DeepAgent graph via
  LangGraph's `Command(resume=...)` against the **task-scoped** key
  `<session.checkpointer_key>:task:<task_id>`.

**HITL cutover from legacy `thread_id` resume.**

Today `backend/app/main.py:_resume_action` resumes by invoking the active agent with
`graph_run_config(..., thread_id=thread_id)` against the global desk graph. After
Section C cuts over, that path would resume the wrong checkpoint for flagged
workflow-routing turns — the workspace thread key instead of the task-scoped key.

The migration step **C-impl-5.5 (HITL resolver rewire)** is a mandatory gate before
C-mig-3 cut-over. It updates `main.py:_resume_action`, `AgentService.invoke_resume`,
and `deep_agent/hitl.py` so flagged pending actions carry direct task identity and
resume through the task-scoped DeepAgent key. The resolver never infers task identity
from `tool_call_id`.

**Pending-action / source_meta contract (extended at HITL-emit time):**

When the executor emits a HITL gate, it writes the resume-resolution data directly
into the pending action's `source_meta`. The fields are primary identifiers; the
tool_call_id is kept as secondary audit metadata.

```json
{
  "task_id":           42,
  "session_id":        17,
  "context_pack_id":   91,
  "checkpointer_key":  "thread:23:workflow:5:persona:risk_manager:episode:1:task:42",
  "workflow_id":       5,
  "envelope_final":    "desk_workflow",
  "agent_runtime":     "deepagents",
  "audit": {
    "tool_call_id":  "call_01_vs8d...",
    "tool_name":     "price_positions",
    "persona":       "risk_manager",
    "emitted_at":    "2026-05-22T08:15:00Z"
  }
}
```

Primary fields (`task_id`, `session_id`, `context_pack_id`, `checkpointer_key`,
`workflow_id`, `agent_runtime`) are non-null and authoritative for resume. The
`audit` sub-object is informational only — useful for the UI card and for grep, but
never on the critical path of resume resolution.

**Resolver flow:**

1. Read `source_meta`. If `task_id` is present (flagged threads), go to step 2;
   otherwise dispatch to the legacy `thread_id`-based path.

2. Load `agent_tasks[task_id]`. Assert `status='awaiting_hitl'`; if not, return
   conflict (the user double-clicked / a watchdog already cleared the lease).

3. Rebuild the workflow DeepAgent graph through the same factory used for the original
   run (`build_orchestrator(...)` evolved with task-aware config), preserving the
   originating model selection and YOLO/HITL policy.

4. Build the resume config from `source_meta` directly:

   ```python
   config = graph_run_config(
       settings,
       thread_id=source_meta["checkpointer_key"],
       configurable_extra={
           "workflow_id":      source_meta["workflow_id"],
           "session_id":       source_meta["session_id"],
           "task_id":          source_meta["task_id"],
           "context_pack_id":  source_meta["context_pack_id"],
           "envelope":         _resume_envelope(source_meta),
           "confirmed_cost_preview": True,
       },
   )
   ```

5. Invoke `agent.invoke(cmd, config=config)`. The DeepAgent run continues from the
   suspended checkpoint, sees the same context pack, and finishes the task.

6. On terminal status, the executor's normal lease-release path runs
   (`agent_sessions.current_task_id = NULL`, `task.status = 'completed'`,
   `domain_event: hitl_approved | hitl_rejected` then `task_completed`).

Storing `checkpointer_key` redundantly in `source_meta` is deliberate: it lets the
resolver work even if `agent_tasks` rows are partially loaded, and it makes
audit/replay self-contained — every HITL action carries the exact resume target
inline.

Until C-mig-3, threads without `feature.workflow_routing=on` keep the legacy
`thread_id`-based resume. The resolver dispatches on the thread's flag — flagged
threads take the task-scoped path; unflagged ones take the legacy path.

**Concurrency invariants:**

- At most one **active session** per `(workflow_id, persona)` (partial unique
  index on `status='active'`).
- At most one **task in flight** per session at any moment (enforced by
  `agent_sessions.current_task_id` lease — see C.5.2 below).
- Multiple workflows in a workspace may have tasks in-flight concurrently; their
  sessions don't collide because sessions are workflow-scoped and tasks have distinct
  DeepAgent `thread_id`s.

**Known DeepAgents constraints and mitigations:**

- DeepAgents `task` returns a final subagent result to the parent; the parent should
  not rely on seeing every intermediate subagent message. Mitigation: ledger/tool
  middleware captures artifacts and tool-result evidence at write time, and SSE uses
  DeepAgents/LangGraph stream events for user-visible progress.
- DeepAgents subagents receive selected parent state minus excluded keys. Mitigation:
  the task prompt and context-pack middleware make the immutable context pack the only
  sanctioned task state; server-side scoped tools reject out-of-pack work.
- If a concrete proof shows that nested `task` cannot support task-scoped HITL resume
  in this app, first try `CompiledSubAgent` or `AsyncSubAgent` inside DeepAgents. Only
  then consider a standalone custom LangGraph worker, and document that evidence in
  this spec before implementation.

### C.5.2 Session task-lease (concurrency guard)

The partial unique index prevents two **active session rows** for the same persona-in-workflow, but it does not prevent two TaskExecutor invocations from racing to use the same active session for two different tasks. Add an explicit lease:

```sql
-- Added to agent_sessions in C-mig-1.b:
agent_sessions
    + current_task_id  INT FK→agent_tasks.id NULL
    + lease_acquired_at TIMESTAMP NULL
```

**Lease protocol:**

```sql
-- Atomic claim by the TaskExecutor before invoking the DeepAgent run:
UPDATE agent_sessions
SET    current_task_id = :task_id,
       lease_acquired_at = now()
WHERE  id = :session_id
  AND  current_task_id IS NULL;
-- If 0 rows updated, the lease is held by another task → back off & retry
-- (the orchestrator scheduler treats the task as still 'ready' for the next tick).

-- Released by the TaskExecutor when the task transitions to a terminal state
-- ('completed' | 'failed' | 'abandoned') OR when it pauses at HITL:
UPDATE agent_sessions
SET    current_task_id = NULL,
       lease_acquired_at = NULL
WHERE  id = :session_id
  AND  current_task_id = :task_id;
```

A HITL pause **releases** the lease so the orchestrator can dispatch a different
task to the same persona while approval is pending — but the session row stays
`'active'` to keep affinity. On HITL resume, the same task re-acquires the lease;
if another task has acquired it in the meantime, the resume blocks and the
orchestrator schedules a serialisation point (one task at a time on the session).

**Stale leases:** a lease older than a configurable bound (default 10 minutes) is
considered stale; an executor watchdog clears it and marks the task `'failed'` with
`error='executor_lease_stale'`. Prevents wedged sessions from blocking forever.

**Interaction with orchestrator session:**

- The workflow orchestrator remains a long-lived DeepAgents ReAct coordinator and
  the only component that talks to the user. When planning is needed, the orchestrator
  emits a `kind='plan'` artifact containing proposed `TaskSpec`s. The scheduler
  validates those specs and inserts `agent_tasks`.
- Personas are DeepAgents subagents, not separate home-grown runtimes. A persona
  session row records affinity, freshness, and audit for the DeepAgents subagent
  invocation. The checkpoint boundary remains task-scoped.
- This unifies the model without replacing the framework: everything business-critical
  is a typed task and artifact, while every LLM decision still runs through the
  LangChain/DeepAgents ReAct loop.

## C.6 Session lifecycle & resume policy

Sessions are scoped `(workflow_id, persona, episode_id)`. **A session row IS an
episode.** Each row has one `episode_id`, immutable for its lifetime.

**Acquisition order** (strict; the executor walks in this order and uses the first
match):

1. **Active idle session** — a row with `status='active'`, `persona=X`,
   `current_task_id IS NULL`. This is the HITL-pause case: the prior task on this
   session is parked at an approval gate; while it waits, other tasks may run on
   the same session. The executor claims the lease via the atomic
   `UPDATE … WHERE current_task_id IS NULL` from C.5.2 — if the claim fails
   (HITL just resumed in another thread), fall through to (2). A **`session_resumed`**
   event is emitted (semantically: a new task continued on a still-active session).
2. **Closed reusable session** — most-recent `status='closed'` row for
   `(workflow_id, persona=X)` that passes the freshness check below. On match:
   - `status` flips `'closed'` → `'active'`
   - `episode_id` is unchanged
   - `closed_at` / `closed_reason` are cleared
   - `current_task_id` is set via the atomic lease claim
   - The session's `checkpointer_key` namespace is reused, but the new task
     starts a fresh DeepAgent run under the task-scoped key
     `<session.checkpointer_key>:task:<task_id>` (see C.5.1).
   - A **`session_resumed`** event is emitted.
3. **New episode** — insert a new session row with
   `episode_id = (max prior episode_id for this (workflow, persona)) + 1`, a fresh
   `checkpointer_key` namespace, `status='active'` from the start, lease claimed.
   The old row (if any) is moved to `status='archived'` with `closed_reason` set to
   whichever freshness predicate failed. A **`session_opened`** event is emitted.

For worker/persona sessions, all three paths produce the same external behaviour for
the new task: it runs against a fresh task-scoped DeepAgent graph initialised from the
context pack. (1) and (2) only preserve **affinity** — you get the same `session_id`
again, the artifact ledger groups by it, the freshness window applies to it — they do
**not** preserve message history across worker tasks. The workflow orchestrator is the
exception: user turns continue on the same orchestrator checkpoint, so conversation
history is preserved until explicit compaction or clear creates a new orchestrator
episode. Task-resume after HITL also preserves message history for the same paused
task checkpoint.

The partial unique index `UNIQUE (workflow_id, persona) WHERE status='active'`
guarantees at most one active session per `(workflow, persona)` — so (1) and (3)
cannot coexist, and (2) flips a closed row to active atomically. The lease guard
(C.5.2) layered on top guarantees at most one **task** running on the active session
at a time.

**Freshness check passes iff:**

- The workflow's `canonical_snapshot_ids` are equal under the per-scope rule
  (C.4.1) to the value at session close. `workspace_meta` and `ad_hoc` scopes are
  always equal to themselves; pricing/rfq/reporting scopes use their respective
  field-tuple equality.
- Wall-clock since close is ≤ a freshness bound (default 24h; tunable per task type
  via the TaskRegistry).
- No `claim_disputed` event has fired against an artifact the session cited.

When freshness fails the session row is moved to `'archived'` (its messages and artifacts
remain queryable in the ledger; the LangGraph checkpoint is preserved but no longer
reused for new turns).

**Close:**

- `return_to_orchestrator(summary)` on the persona side closes the session normally, writes `last_summary`, sets `closed_reason='return_to_orchestrator'`. The orchestrator session for the workflow becomes active for the next task.
- Workflow `closed`/`abandoned` cascades to archive all its sessions.

**Within one user turn** (per workflow), task→worker→artifact→task chains are allowed up
to a per-workflow cap (default 5). The cap is on the *task graph depth* added in this
turn, not on session handovers; loops are detected by the orchestrator's planner.

## C.7 Context assembler

```python
def assemble_context_pack(
    task_spec: TaskSpec,
    workflow: Workflow,
    recent_summary: str | None,
) -> ContextPack:
    artifact_ids = sorted(orchestrator.curate_relevant_artifacts(task_spec, workflow))
    tools = sorted(TOOL_SCOPES_BY_TASK_TYPE[task_spec.task_type])
    summary_text = recent_summary or ""
    summary_hash = "sha256:" + sha256(summary_text.encode("utf-8")).hexdigest()

    # Hash the *contract* the worker will be subjected to, not just the brief.
    prompt_revision_hash = persona_prompt_revision_hash(task_spec.assigned_persona)
    tool_signature_hash = tool_signature_hash_for(tools)  # {tool: schema_hash}

    # STABLE part — what makes two packs interchangeable. Must include task_type,
    # persona, prompt rev, and tool sig so workers under different contracts never
    # collide. Workflow identity is intentionally NOT in here — different workflows
    # with truly identical contracts can share storage.
    stable_payload = {
        "task_type":                   task_spec.task_type,
        "assigned_persona":            task_spec.assigned_persona,
        "task_brief":                  canonical_jsonify(task_spec.inputs),
        "canonical_snapshot_ids":      canonical_jsonify(workflow.canonical_snapshot_ids),
        "cited_artifact_ids":          artifact_ids,
        "tools_scope":                 tools,
        "tool_signature_hash":         tool_signature_hash,
        "recent_session_summary_hash": summary_hash,
        "prompt_revision_hash":        prompt_revision_hash,
        "model_id":                    current_model_id(),
    }
    content_hash = "sha256:" + sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    # METADATA — informational; not hashed.
    metadata = {
        "recent_session_summary": summary_text,
        "assembled_at": now().isoformat(),
        "assembler_version": "1.0",
    }

    # 1) Dedup the payload row globally on content_hash.
    payload_row = upsert_context_pack_payload(
        content_hash=content_hash,
        stable_payload=stable_payload,
    )
    # 2) Always create a fresh context_packs row bound to (workflow_id, task_id).
    return insert_context_pack(
        workflow_id=workflow.id,
        task_id=task_spec.id,
        payload_id=payload_row.id,
        metadata=metadata,
    )
```

`canonical_jsonify` recursively sorts dict keys and normalises numeric types so the
hash is byte-stable across runs and platforms.

`upsert_context_pack_payload` is a true content-addressed insert — if the
`content_hash` already exists, returns the existing row (storage dedup, even across
workflows). `insert_context_pack` is *never* a dedup: each task invocation gets its
own `context_packs` row pointing at the (possibly shared) payload. Artifacts emitted
during that invocation cite `context_packs.id` (not `payload_id`), so workflow/task
provenance is exact even when two workflows happened to assemble identical inputs.

`persona_prompt_revision_hash(persona)` is the SHA-256 of the persona identity prompt
file plus its composed policy fragments at assembly time. Bumps when the prompt
changes; ensures repeated invocations after a prompt edit don't silently reuse a pack
built against the old contract. `tool_signature_hash_for(tools)` is the SHA-256 over
a canonical `{tool_name: tool_schema_hash, …}` mapping for the scoped tools.

The pack is the *only* state the worker receives at task start — the persona session
prompt is composed from it, not from arbitrary workspace-level state. When the worker
emits artifacts, every artifact row carries `context_pack_id`, binding outputs to the
exact inputs they were produced from. This is what lets stale-assumption checks and
replay work.

Implementation lives in `services/deep_agent/context_assembler.py`. Curation
(`curate_relevant_artifacts`) starts as a deterministic rule set (cite the artifact ids
listed in the task's `depends_on`'s outputs, plus pinned artifacts in the workflow);
later iterations can use a small ranking model.

## C.8 Artifact / evidence / event ledger

**Artifacts (`session_artifacts`)** carry the canonical structured body in `payload`.
A `rendered_path` like `/session/findings/wf42/ep3-risk_manager.md` is a *view*,
generated by an artifact-renderer when the orchestrator wants a human-readable file.
The renderer is deterministic from `payload` — never the other way around.

**Evidence refs (`artifact_evidence_refs`)** are how a claim graduates to load-bearing.
Examples:

- A `compute_barrier_proximity` task emits an artifact with
  `kind='deterministic_query'` whose evidence ref is
  `{evidence_kind: 'snapshot', evidence_payload: {market_snapshot_id: 42, position_state_at: '...'}}`.
  That row is automation-safe out of the box.
- An `interpret_snowball_terms` task emits `kind='claim'` whose evidence ref is
  `{evidence_kind: 'agent_attestation', evidence_payload: {persona: 'trader', context_pack_id: 17}}`.
  That row is durable but **not** load-bearing for compute — downstream tasks needing
  truth must re-derive via deterministic tools.
- A `propose_run_risk` task that gets HITL-approved emits two artifacts: a `plan`
  pre-approval and a `persisted_run` post-approval; the `persisted_run`'s evidence ref
  is `{evidence_kind: 'human_approval', evidence_payload: {approval_id: 19}}` plus a
  `deterministic_run` ref to the `risk_run_id`.

**Domain events (`domain_events`)** are the audit log. Replay = "given workflow X, walk
`domain_events` ordered by `occurred_at`, reconstruct state".

#### Event payload versioning

Because replay is part of the architectural promise, payload shape must be stable
under code evolution. Discipline:

- Add a `schema_version INT NOT NULL` column on `domain_events` in C-mig-1
  (alongside the other fields). Default to `1` at insert time.
- Each `(kind, schema_version)` pair has a fixed Pydantic-validated payload model
  registered in `services/deep_agent/event_registry.py:EVENT_PAYLOAD_REGISTRY`.
  Writers MUST validate against the registry before insert; readers MUST decode
  through it.
- **Compatibility policy:**
  - **Forward-compat (default for additions):** new optional fields may be added
    to an existing `(kind, version)` payload provided readers tolerate extras (use
    `model_config = ConfigDict(extra='allow')`). Bumps the *minor* of
    `event_registry.PAYLOAD_LIBRARY_VERSION` but the row's `schema_version` stays
    the same.
  - **Breaking change:** removing a field, changing its type, or changing its
    semantics requires either a new `schema_version` for the same `kind` OR a new
    `kind`. Old events keep working; new code writes to the new version. A
    `migrations/` script may be written if old events need to be re-projected,
    but in-place mutation of `payload` is forbidden.
- The registry pins a maximum supported `(kind, schema_version)` per release. Readers
  encountering a higher version refuse to decode and log; this is the "stop the
  world" signal that the deploy is older than the data.

The same versioning discipline applies to `session_artifacts.payload` JSON schemas
keyed by `(artifact.kind, artifact.schema_version)`. The column is declared in C.2
alongside the event-stream version. Writers MUST validate against
`ARTIFACT_PAYLOAD_REGISTRY` before insert; readers MUST decode through it. Forward-
compat / breaking-change rules mirror the event registry exactly: a new optional
field bumps the registry's library version but not the row's `schema_version`;
removing or retyping a field requires a new `(kind, schema_version)` pair or a new
`kind`. Both registries live in `services/deep_agent/payload_registry.py`, which owns
the event and artifact payload model maps plus their compatibility policy constants.

## C.9 Worker scoped authority

Each persona/worker has scoped authority:

- The `tools_scope` whitelist on the context_pack is enforced server-side. A worker
  asking for a tool outside scope gets a permission error.
- Workers **cannot** dispatch sibling workers. To request additional work, a worker
  emits a `kind='plan'` artifact (a proposed sub-task). The orchestrator decides
  whether to schedule it.
- Workers **cannot** mark another worker's claim as evidence-bound. Evidence binding is
  done only by deterministic tools (auto-bound on tool result) or by the orchestrator
  on HITL approval.

## C.9.1 Large-blob storage: out of the checkpoint

Today, `orchestrator.py:_build_backend()` routes `/large_tool_results/` to the default
`StateBackend`, which means every offloaded tool result lives inside the LangGraph
checkpoint state in `agent_checkpoints.sqlite`. That's exactly the bloat root cause
visible in thread #23: a 64-position `get_positions` payload was being rewritten into
the checkpoint blob on every step. Section C cannot honour its "never compacted /
always reachable" promise while large blobs remain checkpoint-embedded.

The fix is a dedicated external content-addressed store:

```
                  ┌─────────────────────────────────────────────────────┐
                  │  CompositeBackend (updated)                         │
                  │   routes:                                           │
                  │    /skills/      → FilesystemBackend (read-only)    │
                  │    /references/  → FilesystemBackend (read-only)    │
                  │    /artifacts/   → FilesystemBackend (read/write)   │
                  │    /large_tool_results/                              │
                  │                  → ContentAddressedFilesystemBackend │
                  │    default       → StateBackend                     │
                  └─────────────────────────────────────────────────────┘
```

**`ContentAddressedFilesystemBackend`** (new, `services/deep_agent/cas_backend.py`):

- **Context propagation.** The backend's `write(path, content)` is reached from
  DeepAgents eviction middleware that today carries no durable desk identity by
  default. Two channels supply the workflow/session/task context:

  1. **Primary — `RunnableConfig.configurable`.** The TaskExecutor / DeepAgent
     invocation adapter sets these keys on the graph's RunnableConfig before invoke:

     ```python
     config["configurable"] = {
         "thread_id": session.checkpointer_key + f":task:{task.id}",
         "workflow_id":  workflow.id,
         "session_id":   session.id,
         "task_id":      task.id,
         "context_pack_id": context_pack.id,
         "envelope":     envelope_value,
     }
     ```

     A thin shim wraps the eviction middleware so it passes the active
     `RunnableConfig` to `backend.write(...)` via a new optional `config=` kwarg the
     CAS backend understands. Other backends ignore the kwarg (Liskov-safe).

  2. **Fallback — ambient `contextvars`.** For write paths that originate outside a
     RunnableConfig (e.g. legacy `backend/app/main.py` request handling or the
     `AgentMessage` insert path in `backend/app/services/agents.py` pre-cutover), a `ContextVar` named
     `_DESK_EXECUTION_CONTEXT` is set by the request handler at the entry point.
     CAS reads it as a fallback when `config=` is absent.

  3. **Legacy/unflagged catch-all.** If neither channel yields workflow/session/task,
     the CAS write attributes the artifact to the **meta workflow's router session**
     for the current thread, with `kind='tool_result'` and an
     `args_summary={"origin":"legacy_unflagged","tool_call_id":...}` marker. This
     keeps `session_artifacts.workflow_id` populated (consistent with C.2's nullable
     `agent_messages.workflow_id` story) and surfaces "unrouted" artifacts in a
     queryable bucket so we can audit the migration.

- Rooted at `data/artifact_blobs/`.

- `write(path, content, config=None)`:

  1. Resolve `(workflow_id, session_id, task_id, tool_name, tool_call_id, context_pack_id)` per the channels above. Raise if a strict-mode flag is on AND the resolution returns the legacy catch-all unintentionally.
  2. Compute `blob_hash = sha256(content)`.
  3. Write content to `data/artifact_blobs/<blob_hash[:2]>/<blob_hash>.json` (idempotent — skip if exists).
  4. Insert a `session_artifacts` row with `kind='tool_result'`, `payload={"blob_hash": blob_hash, "size": …, "tool_call_id": …, "tool_name": …}`, `rendered_path=path`, plus the resolved IDs. Insert a corresponding `domain_event` of kind `artifact_created`.

- `read(path, config=None)`:

  1. Resolve `path → blob_hash` via the artifact ledger.
  2. Stream the file from `data/artifact_blobs/<blob_hash[:2]>/<blob_hash>.json`.

- `ls(prefix)`: queries the artifact ledger, not the filesystem; gives the agent a
  view consistent with the rest of the ledger.

**Consequences:**

- `agent_checkpoints.sqlite` stops storing tool blobs entirely. Checkpoint size grows
  only with reasoning/messages — which the compaction middleware then trims.

- Blobs are deduplicated by hash: two `get_positions(portfolio_id=6)` calls producing
  identical JSON share one file on disk.

- The ledger row exists at write time, so artifacts are first-class citizens
  immediately — they appear in workflow-state queries, can be cited by context_packs,
  and have `artifact_evidence_refs` attached as soon as the tool runs.

- The Issue 2 prerequisite keeps applying — JSON pretty-printing happens before the
  CAS write, so the on-disk blob remains line-addressable. The implementation plan
  must verify the live pretty-print hook filename before wiring CAS around it.

- **Retention is tiered and audit-safe.** A blob's GC eligibility is determined by
  the *artifact-class* it backs — not by age alone. The categories are:

  | Class                     | Defined by                                                   | Retention default                        |
  | ------------------------- | ------------------------------------------------------------ | ---------------------------------------- |
  | **Load-bearing**          | The artifact has at least one `load-bearing` evidence ref (`deterministic_run`, `snapshot`, `human_approval`), OR the artifact is `kind='tool_result' \| 'persisted_run' \| 'report'`, OR the artifact is explicitly `pinned=TRUE`. | **Indefinite.** Never GC'd.              |
  | **Provisional**           | Artifact is `kind='claim' \| 'finding' \| 'plan'` with only `agent_attestation` / `context_pack` evidence refs, AND no downstream artifact has cited it (no inbound FK from another row's `cited_artifact_ids` or `superseded_by`). | TTL applies (default 180 days; tunable). |
  | **Superseded / unpinned** | Artifact has `superseded_by` set AND the superseding artifact is itself retained AND `pinned=FALSE`. | TTL applies (default 90 days; tunable).  |
  | **Orphan**                | Blob with no live `session_artifacts` row referencing it (e.g. left over from a failed write that didn't reach the ledger insert). | Aggressive (default 24h).                |

  The GC sweeper runs on a schedule, walks blobs, and applies the tiering rules. It
  emits a `domain_event` of kind `artifact_gc'd` for each removal, with the prior
  blob_hash, size, and tier. The ledger row itself is **not** deleted — it stays as
  a tombstone with `payload.blob_state = 'gc_evicted'` so audit walks can see the
  removal happened and when.

  **"Evidence unavailable" is no longer a routine outcome.** Load-bearing artifacts
  cannot lose their blobs without an explicit operator action (a separate
  `force_gc` admin command, audited via its own event). For regulator audit
  purposes, the load-bearing tier is effectively WORM.

This is what makes the "/large_tool_results files: Never compactable" row in C.10
actually true — they're never compacted because they were never in the checkpoint
to begin with.

## C.10 Compaction (scoped, narrowed)

Compaction owns only the checkpoint copy of conversation history. It can shorten
reasoning and replace already-captured tool payloads with references; it can never
rewrite a deterministic result, database result, persisted run, report, or ledger row.

### C.10.1 Capture before compaction

`GroundTruthArtifactMiddleware` derives the authoritative tool set from the
server-owned `ToolGroup` capability classification. Read/detail/poll,
deterministic-Python, domain read/write, and async-dispatch results are ground truth.
Artifact-access tools do not create recursive artifacts; `read_artifact` instead
reattaches the original artifact's server reference so its disclosed body can later
compact back to that same id/hash.

Every ground-truth `ToolMessage` is captured synchronously at the tool-call boundary,
before compaction can see it:

1. Preserve the exact UTF-8 result bytes in the content-addressed store.
2. Record an immutable `kind='tool_result'` ledger row with `content_hash`, input
   hash, `tool_call_id`, tool name, byte size, `generated_at`, `observed_at`, and
   extracted `data_as_of` when present.
3. Attach a compact `<artifact_ref>` containing those exact fields and a
   deterministic structural summary to the model-visible result.

Capture is idempotent for `(workflow_id, tool_call_id, content_hash)`. If capture
fails, the raw `ToolMessage` remains in checkpoint state and is explicitly
non-compactable. Loss of the compact reference therefore costs context, never truth.

| Layer | Compactable? |
| --- | --- |
| Domain DB, artifact/evidence/event ledgers, context packs, CAS blobs | **Never** |
| Uncaptured result from a server-classified ground-truth tool | **Never** |
| Captured ground-truth result in checkpoint history | **Reference projection only** |
| Artifact id/hash/tool/timestamp manifest | **Never rewritten by an LLM** |
| Older reasoning, orchestration prose, and non-ground-truth messages | Compactable |
| Most recent N (=6) messages | **Never** |

Before an LLM summarises a compactable window, each captured ground-truth result is
projected to its exact artifact-reference capsule; raw prices, Greeks, rows, and
schedules are not sent to the summariser. The narrative prompt is forbidden from
restating those values. After summarisation, the server appends a deterministic
artifact manifest rendered from message metadata. The narrative is orientation only;
the manifest and canonical artifact are the evidence.

### C.10.2 Progressive disclosure, not RAG

Canonical artifacts are recovered through deterministic, workflow-scoped tools:

- `list_artifacts` lists compact descriptors and exact ids/hashes/timestamps.
- `inspect_artifact` returns metadata plus a JSON-pointer or Markdown-heading map.
- `read_artifact` reads an exact bounded line slice or an explicit JSON pointer /
  Markdown section.

There is no embedding index, vector similarity, semantic chunk selection, or
model-generated retrieval ranking in this path. Context packs carry artifact
descriptors; an agent discloses more only by choosing a concrete artifact id and
selector. Cross-workflow artifact reads fail closed.

### C.10.3 Time-sensitive action evidence

Artifact time and data time are distinct and retained together: `generated_at` is
when the immutable artifact was captured, `observed_at` is when the agent observed it,
and `data_as_of` / domain timestamps describe the underlying market or valuation.

Hedge execution is stricter. Risk aggregation emits `valuation_as_of`,
`risk_generated_at`, `expires_at`, the configured freshness window, and a
`position_set_hash`. A proposal refuses stale or historical risk. `book_hedge` requires
the source artifact id plus artifact-generation, valuation, risk-generation, and
expiry timestamps in the HITL payload. At execution it rechecks the latest usable risk
run, TTL, workflow ownership, portfolio fingerprint, source payload, timestamps, spot,
strategy, and solver legs. Any mismatch returns `stale_hedge_proposal` without a DB
write; the only recovery is fresh risk and a new proposal.

User-triggered `/compact` is a future UI affordance and emits a `compaction_run`
domain event.

## C.11 Prompt updates

- **Router prompt** (`router.md`, new): tiny — input is the context pack
  `{active_workflows, last_selected_workflow_id, latest_user_message}`. Output is one of
  four classifications + (optionally) a new workflow title.
- **Workflow orchestrator** (`orchestrator.md`, rewritten):
  - Own the visible conversation. Resolve follow-up user replies from the current
    orchestrator session context; do not route a user turn through `TaskExecutor`.
  - Replace free-form worker briefs with task-graph planning vocabulary. The
    orchestrator emits `TaskSpec`s when it needs worker execution.
  - "When a task needs data the ledger already has, cite the existing artifact id rather
    than scheduling a refetch."
  - "When a persona's claim needs to be load-bearing for the next task, schedule a
    deterministic-query task or a HITL approval first."
- **Persona prompts** (`trader.md` / `risk_manager.md` / `high_board.md`, rewritten):
  - "Your context pack is your only state. Don't ask for tools or data outside scope."
  - "You produce typed artifacts. The orchestrator decides what becomes truth."
  - "When the task is complete, emit your final artifact and return; do not narrate."
  - Remove the older "conversational persona" framing — workers fulfil tasks, the
    orchestrator handles conversation.

## C.12 Migration & build order

Within Section C alone:

| Step           | What                                                         | Touches                                                      |
| -------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| C-mig-1        | Alembic migration creates the **8** new tables (`workflows`, `agent_sessions`, `agent_tasks`, `session_artifacts`, `artifact_evidence_refs`, `context_pack_payloads`, `context_packs`, `domain_events`) + adds **NULLABLE** `workflow_id`/`session_id` to `agent_messages` + `active_workflow_id` to `agent_threads` + `version` (monotonic counter) on `positions` + `current_task_id`/`lease_acquired_at` on `agent_sessions` + `schema_version` on **both** `domain_events` and `session_artifacts` (default 1). Partial unique index `agent_sessions(workflow_id, persona) WHERE status='active'`. No backfill yet; existing inserts continue to work unchanged. | DB                                                           |
| C-mig-2        | Backfill: each existing thread → 1 workspace meta workflow + 1 router session, 1 domain `ad_hoc` workflow + 1 orchestrator session + 1 default context_pack, all existing messages assigned to the domain workflow's orchestrator session. LangGraph checkpointer keys preserved by reusing `str(thread_id)` as the orchestrator session's `checkpointer_key`. Backfill is idempotent and can run online. | DB + `backend/app/services/agents.py` runtime glue           |
| C-mig-2.5      | **Dual-write hook on the legacy AgentMessage insert path** (`backend/app/main.py` stream endpoint + `AgentService._persist_from_collector` / `_persist_agent_result`): if the thread's `active_workflow_id` is set, populate `workflow_id`/`session_id` automatically. Without this, post-backfill rows from legacy code paths would land with NULLs. | `backend/app/main.py` + `backend/app/services/agents.py`     |
| C-impl-0       | Implement `ContentAddressedFilesystemBackend` and rebind `/large_tool_results/` to it. Tool blobs migrate out of the checkpoint. Existing checkpoint blobs are GC'd lazily — read fallback handles old entries during transition. | services/deep_agent/cas_backend.py + orchestrator.py         |
| C-impl-1       | Registries: `TaskRegistry`, `TOOL_SCOPES_BY_TASK_TYPE`, artifact `kind` taxonomy with validators, evidence kind validators, per-task-type freshness window. | services/deep_agent/registries/                              |
| C-impl-2       | `services/deep_agent/context_assembler.py` (assembler with stable hashing) + `ledger.py` (artifact/evidence/event writes). | services                                                     |
| C-impl-3       | Snapshot capture (`services/deep_agent/snapshot.py`): computes `canonical_snapshot_ids` for a workflow scope. Emitted on `workflow_opened` and `snapshot_captured`. | services                                                     |
| C-impl-4       | Workspace router (deterministic phase). Routes turns; persists router replies into the workspace meta workflow's router session. | `services/deep_agent/workspace_router.py` + `backend/app/main.py` + `services/agents.py` |
| C-impl-5       | `TaskExecutor` + DeepAgent invocation adapter: runs ready worker tasks through task-scoped DeepAgent ReAct graphs, keeps DeepAgents `subagents=` for personas, sets workflow/session/task/config metadata, streams events, handles HITL pause/resume. It does **not** wrap ordinary user turns. | `services/deep_agent/executor.py` + `services/deep_agent/orchestrator.py` + `services/agents.py` |
| **C-impl-5.5** | **HITL resolver rewire.** Extend pending-action `source_meta` so task-scoped worker actions carry `task_id`, `session_id`, `context_pack_id`, `checkpointer_key`, `workflow_id`, and `agent_runtime="deepagents"` directly, while orchestrator-session actions carry `session_id`, `checkpointer_key`, `workflow_id`, and `agent_runtime="deepagents_orchestrator"`. Update `backend/app/main.py:_resume_action`, `AgentService.invoke_resume`, and `deep_agent/hitl.py` to read those fields directly, rebuild the DeepAgent graph, and resume against the exact checkpoint key. Dispatch on `feature.workflow_routing` so unflagged threads still take the legacy `thread_id` path. **Mandatory gate before C-mig-3.** | `backend/app/main.py` + `services/agents.py` + `services/deep_agent/hitl.py` + `services/deep_agent/executor.py` |
| C-impl-6       | Per-workflow DeepAgent orchestrator session: user turns route to the selected workflow's Orchestrator checkpoint key; planner emits `kind='plan'` artifacts the scheduler reads back into `agent_tasks`, and planning still runs inside the long-lived DeepAgents ReAct loop. | `services/deep_agent/orchestrator.py` + `services/agents.py` + prompts              |
| C-impl-7       | Persona rewrites: prompts + tool scoping + artifact emission while keeping personas as DeepAgents `SubAgent` specs. | `personas.py` + prompts/                                     |
| C-impl-8       | Compaction middleware (narrowed scope per C.10).             | services/deep_agent/compaction.py                            |
| C-impl-9       | Frontend workflow/session timeline + artifact ledger browser. | frontend (separate ticket)                                   |
| **C-mig-3**    | **Cut-over:** flip `feature.workflow_routing` default to ON for new threads. Existing threads migrate on next user turn through the backfill code path. Legacy AgentMessage write path remains live but goes through C-mig-2.5's dual-write hook. | config                                                       |
| **C-mig-4**    | **Tighten constraints:** after a confidence window with no NULLs observed in `agent_messages.workflow_id` / `session_id` for new rows, an Alembic migration adds `NOT NULL`. Historical pre-backfill rows are excluded by a WHERE clause if necessary, or the tighten step is preceded by a one-shot backfill of any stragglers. | DB                                                           |

Behaviour migration is feature-flagged from C-mig-2.5 through C-mig-3: a thread with
`feature.workflow_routing=on` uses the new path; others keep the legacy single-session
flow but write into the new schema via the dual-write hook so the data stays
backfilled.

---

# Cross-section dependencies

Build order if shipping in one campaign:

1. **Section C schema (one migration; all 8 tables) + backfill of existing threads into
   the workspace/workflow/session model.** No behaviour change yet — feature-flagged.
2. **Section C ledger + context_assembler + workspace router (deterministic phase).**
   Per-workflow task-graph scheduling lands behind the flag, but execution still runs
   through `create_deep_agent`. Existing `task` subagent paths keep working for
   unflagged threads.
3. **Section A schema migrations + backfills.** Structured position tables become
   evidence sources. Backfill order: Snowball → SingleBarrier → DoubleBarrier → Sharkfin
   → Asian → Vanilla/American (pain-driven).
4. **Section A new query tools** (`query_positions_near_barrier`, `query_positions`,
   `get_*_terms`, `get_*_schedule`). These register as task types in the new
   TaskRegistry, emitting `kind='deterministic_query'` artifacts auto-bound to
   `snapshot` evidence refs.
5. **Section B `@file:` resolver + HITL reclassification.** Lands alongside Section A
   tools. `run_python` becomes a registered task type (`run_analytic_script`) emitting
   `kind='sandbox_output'` artifacts. The `@file:` resolver reads via the same backend
   used by the assembler so artifact ids in the context pack can be resolved directly.
6. **Cut-over: flag-default to ON for new threads, migration pass for old threads.**
7. **Section C compaction middleware.** Narrowed scope per C.10.
8. **Frontend (separate ticket):** workflow tabs, session timeline, artifact ledger
   browser, evidence-binding indicators, replay viewer.

Each phase is independently mergeable behind feature flags / migration gates. The
Issue 1/2 prerequisite direction is unaffected.

---

# Open questions (resolve in implementation planning)

1. **LangChain `InterruptOnConfig` predicate hook:** does the installed
   LangChain/DeepAgents stack natively support argument-aware interrupts, or do we
   need the middleware shim? Verify in implementation phase.
2. **DeepAgents task-scoped HITL proof:** write a focused test that starts a
   task-scoped `create_deep_agent` run, delegates through `task`, pauses on a persona
   tool, then resumes with `Command(resume=...)` against
   `<session.checkpointer_key>:task:<task_id>`. If it fails, try `CompiledSubAgent`
   or `AsyncSubAgent` before proposing a non-DeepAgents worker.
3. **Compaction model choice:** Haiku 4.5 is the default candidate. Confirm pricing
   and latency before committing.
4. **Frontend workflow/session timeline UX:** same-sprint deliverable or follow-on?
5. **Section A backfill ordering** within product families — confirm by counting
   current row volumes per `product_type`.
6. **Workflow boundary heuristics:** when the user message is ambiguous between
   `new_workflow` and `continue_workflow`, what's the right default behaviour? Ask vs
   default to continue? Likely needs UI affordance (workflow picker) before fully
   automated.
7. **Freshness window per task type:** the 24h default is coarse. Some task types
   (RFQ quoting) should expire within minutes; others (term-interpretation) tolerate
   days. Per-task-type table populated during C-impl-1; specific values per task type
   are not yet defined.
8. **Replay viewer UI delivery:** the versioning discipline for `domain_events`
   and `session_artifacts` payloads is now in the design (C.8). What's still open
   is whether the audit/replay *viewer* (a UI surface that walks events and
   reconstructs a workflow's state) is in scope for the first cut-over or a
   follow-on.
9. **Cross-workflow queries:** the router's `cross_workflow_query` branch needs a
   target spec — does it run a workflow-of-workflows pattern (a meta-workflow) or
   answer inline from the ledger? Likely the latter for `status_query`, the former
   when the answer requires re-derivation.
10. **CAS retention TTL values & cadence:** the tiering policy is now design
   (C.9.1) — load-bearing is indefinite, provisional defaults to 180 days,
   superseded to 90 days, orphan to 24 hours. The exact defaults and GC sweeper
   cadence still need a sign-off (regulator? legal? capacity?).
11. **Follow-on Section A.v2 scope:** Phoenix and Airbag tables are the most likely
    next deliveries (per the try_solve registry footprint); confirm before C ships
    so the structured-tool grammar accounts for their column names.
12. **Stale-lease watchdog:** 10-minute default in C.5.2 is illustrative. Pin a
    real value once typical task durations are measured (run_python, multi-tool
    risk runs).
13. **Strict-mode flag for CAS catch-all:** when does the legacy/unflagged
    fallback get treated as an error vs a benign during-migration condition?
    Likely flips to strict after C-mig-3 cut-over.

---

# What this does NOT change

- **ReAct / LangChain DeepAgents as the agent framework:** `create_deep_agent(...)`
  remains the default runtime for workflow orchestration and persona delegation.
  Section C adds workflow/task/session/ledger semantics around it; it does not replace
  the LangChain agent loop.
- **Issues 1 & 2 prerequisite direction:** relative `/references/` paths and
  pretty-printed large-result offload remain prerequisites. Section B's `@file:`
  resolver pairs with pretty-printed offload because it injects parsed JSON regardless
  of indenting; the implementation plan must verify the live filenames before assuming
  those prerequisite patches are already present.
- **Quant tool implementations (`price_positions`, `run_risk`, etc.):** unchanged
  behaviour. They keep their HITL gates; their results become `kind='persisted_run'`
  artifacts in the ledger with automatic `deterministic_run` evidence refs.
- **Async agent dispatch (`start_async_agent`):** lives outside Section C's per-turn
  routing. An async agent that produces work registers it as a workflow (or a task
  inside an existing workflow) and bubbles HITL approvals back through the same gates;
  revisit in a follow-on design once Section C lands.

# What this DOES change in existing code

- **Persona identity prompts** (`trader.md`, `risk_manager.md`, `high_board.md`):
  rewritten. The "conversational persona that talks to the user" framing in the prior
  draft is removed. Personas are now workers that consume a context pack, emit typed
  artifacts, and return, while still being DeepAgents `SubAgent`s. The user-facing
  conversation lives at the orchestrator (and, thinly, at the router).
- **`task` tool / `task` middleware (DeepAgents):** still used, but no longer as
  unconstrained free-form persona dispatch. The app creates durable `agent_tasks`,
  builds context packs, constrains the orchestrator to the assigned persona, and records
  task-scoped evidence before and after the DeepAgents `task` call.
- **`agent_messages.thread_id` semantics:** today's "thread = conversation" becomes
  "thread = workspace". The user-visible conversation is now spread across workflows;
  the UI must reflect that.

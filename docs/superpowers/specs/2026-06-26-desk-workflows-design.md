# Desk Workflows — design spec

**Date:** 2026-06-26
**Status:** Draft (awaiting user review)
**Author:** brainstormed with the user

## 1. Summary

Surface "workflows" — today only authored as on-disk golden-workflow files consumed
by the arena/regression/demo harnesses — as a **first-class, frontend-managed module**
with two capabilities:

1. **Authoring.** Users define workflows as **reproducible Python scripts** through a
   bespoke **Workflow Builder** in a new Workflows page. The builder is LLM-assisted:
   it interviews the user and drafts the script, powered by a `build-workflow` agent
   skill + a `save_desk_workflow` tool underneath.
2. **Execution.** Users run a predefined workflow inside any Agent Thread via a
   **slash-command autocomplete picker** in the composer. The workflow runs
   **auto-pilot** — every step back-to-back — driving the real desk orchestrator.

The flagship `risk-manager-control-day` is seeded as the first workflow and is
runnable end-to-end ("brought alive").

## 2. Goals / non-goals

### Goals
- A `DeskWorkflow` is a versionable, reproducible **Python script** (the source of
  truth), self-describing via an embedded `meta` literal.
- A management page to list / view / edit / delete workflows and toggle `local|shared`
  scope.
- A bespoke conversational **Workflow Builder** that drafts scripts with the desk agent.
- A slash-command picker in the Agent Thread composer that launches a workflow.
- Auto-pilot execution: steps run sequentially through `stream_and_persist`, tasks
  settled between steps, streamed to the UI with step framing.
- Per-workflow default run mode (`auto|yolo`), overridable at launch.
- `risk-manager-control-day` seeded and runnable.

### Non-goals (YAGNI)
- No fixtures / assertions / replay in `DeskWorkflow` (those stay arena-only; the
  arena's files and code are **untouched**).
- No `parallel()` runtime primitive in v1 — the async checkpointer serializes a
  thread's turns, so genuine intra-thread parallelism is not real. v1 = `step()`,
  `log()`, native Python control flow.
- No scheduling / cron of workflows.
- No real multi-user isolation for `shared` scope (single-user MVP; see §9).

## 3. Naming

The internal `Workflow` ORM model (`deep_agent/workflow_state.py`) is per-thread
session/checkpoint bookkeeping and is **never user-visible**. The new user-facing
entity is **`DeskWorkflow`** (table `desk_workflows`), matching the existing
`desk-workflow` domain vocabulary (`desk-workflow-contract.md`, the `desk-workflow`
tag). The UI label is "Workflows". No rename of the existing ORM `Workflow`.

## 4. Data model — `desk_workflows` (migration 0035)

Latest migration on `main` is `0034_arena_match_score_breakdown`; this adds `0035`.

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `slug` | str, unique, indexed | kebab; the `/slug` trigger and `meta["name"]` |
| `title` | str | display name |
| `persona` | str | `trader\|risk_manager\|sales\|quant` |
| `description` | text | shown in picker + manager |
| `scope` | str | `local\|shared` |
| `default_mode` | str | `auto\|yolo` |
| `script` | text | **Python source of truth**, includes the `meta` literal |
| `source` | str | `seed\|user` (the seeded flagship is `seed`) |
| `created_at` / `updated_at` | datetime | |

**Metadata columns are a denormalized cache** extracted from the script's `meta`
literal on every save (see §5.2). The script is authoritative; exporting the script
exports the whole workflow → reproducibility.

Migration follows the repo rule: use **migration-local Core tables**, not ORM
models/services (`migrations_no_live_orm_services`). The flagship seed row (§8) is
inserted in the same migration via Core `insert()`.

## 5. Script format & runtime

### 5.1 Authored shape
```python
meta = {
    "name": "risk-manager-control-day",   # == slug
    "title": "Risk Manager Control Day",
    "persona": "risk_manager",
    "mode": "yolo",            # default run mode; overridable at launch
    "scope": "shared",
    "description": "Full desk-control loop: stale-check, refresh, hotspot, "
                   "Greeks landscape, stress test, backtest, governance report.",
}

await step("What does the latest risk say for the control portfolio?")
await step("Run a fresh risk calculation for the control portfolio using the Control Profile.")
await step("Now check the updated risk result — what's the hotspot?")
await step("Run a Greeks landscape across spot shifts for the control portfolio.")
await step("Stress-test the control portfolio using the market-crash scenario set with the Control Profile.")
await step("Run a historical backtest of the delta-hedge strategy from 2026-03-24 to 2026-06-24.")
await step("Generate a governance risk report for today's control session.")
```

### 5.2 `meta` extraction & validation (on save)
- The backend parses the script with `ast` and **literal-evals only the `meta`
  assignment** (`ast.literal_eval` on the RHS — no execution). `meta` must be a pure
  dict literal (Claude-Code rule).
- Required `meta` keys: `name`, `title`, `persona`, `mode`, `scope`. `description`
  optional. `name` must equal the request `slug`. Enums validated
  (persona/mode/scope). On success the columns are populated from `meta`.
- Failure → `422` with a precise message (missing key, bad enum, non-literal meta).

### 5.3 Runtime contract (helpers injected into the script's namespace)
- `await step(prompt: str, *, mode: str | None = None) -> StepResult`
  - Persists `prompt` as a normal user `AgentMessage` in the thread.
  - Drives **one** `active_agent_service.stream_and_persist(...)` turn bound to the
    workflow's persona (→ `requested_character`) and the resolved mode
    (`yolo_mode`/`confirmed_cost_preview` derived from `resolve_execution_mode`).
  - **Settles** queued `TaskRun`s before returning (reuses the arena settle util in
    `services/arena/task.py`), so a later step reads fresh results.
  - Emits SSE `workflow.step.start {index,total,title}` before and
    `workflow.step.end {index}` after.
  - Returns `StepResult` with `.text` (final assistant text) and `.ok`.
- `log(msg: str)` — emits an SSE `workflow.log {message}` narrator line.
- Native Python control flow (loops, conditionals on `StepResult.text`) is available.
- **No** `parallel`, no `import`, no filesystem/network in v1.

`step` titles for framing are **index-only in v1** (`Step N/total`); the helper
signature is exactly `step(prompt, *, mode=None)` — no `title` argument. (A named-step
arg is a possible future enhancement, deliberately out of v1 so generated
scripts/tests never pass an unsupported kwarg.)

### 5.4 Restricted execution
- The script runs **in-process** in a restricted namespace:
  - `__builtins__` reduced to a safe allowlist (no `open`, `__import__`, `eval`,
    `exec`, `compile`, `getattr`/`setattr` on dunders).
  - An **AST guard** rejects `Import`/`ImportFrom`, attribute access to dunder names
    (`__...__`), and `with`-statements opening resources, before execution.
  - Injected names: `step`, `log`, `meta`, and a minimal safe builtin set.
- The script body is wrapped in an `async def __workflow__()` and `await`ed, so
  top-level `await step(...)` works.
- Trust posture matches the existing Skills write API ("local-dev tool by design: no
  auth"). True sandboxing for a multi-user `shared` future is a carry-forward (§9).
  (The Pyodide/Deno sandbox used by `run_python` is host-isolated and therefore
  **cannot** service `step()` callbacks into the orchestrator — hence restricted
  in-process exec instead.)

## 6. Backend components

### 6.1 `services/desk_workflows.py`
- `upsert_desk_workflow(session, *, slug, script, source="user") -> DeskWorkflow`
  — validates/extracts `meta`, runs the AST guard, writes columns + script. Single
  persistence path shared by the CRUD endpoints and the `save_desk_workflow` tool.
- `list_desk_workflows`, `get_desk_workflow`, `delete_desk_workflow`.
- `run_desk_workflow(...)` — the async-generator runner: loads the script, builds the
  restricted namespace (binding `step`/`log` to the live thread + agent service +
  settle), execs it, yields SSE events. Halts on first step error.

### 6.2 `routers/workflows.py` (`build_desk_workflows_router`, mounted in `main.py`)
- `GET /api/workflows` → list (slug, title, persona, scope, default_mode,
  description, source).
- `GET /api/workflows/{slug}` → full incl. `script`.
- `POST /api/workflows` → create (body: `{script}`; slug from `meta.name`).
- `PUT /api/workflows/{slug}` → update.
- `DELETE /api/workflows/{slug}` → delete; **blocks `source=seed`** (409) to keep the
  flagship alive.
- `POST /api/workflows/validate` → dry-run `meta` extraction + AST guard (used by the
  builder/editor for inline feedback), returns ok/errors.

### 6.3 Run endpoint (in `main.py`, beside `stream_chat_message`)
- `POST /api/chat/threads/{thread_id}/workflows/{slug}/run` — body `{mode?}` →
  **SSE `StreamingResponse`** wrapping `run_desk_workflow(...)`.
- Calls `ensure_thread_workflow_state` first (same as the normal message endpoint).
- Resolves mode = body override ?? `default_mode`.
- On step error → emits `workflow.step.error {index, message}` and stops; completed
  steps stay persisted. On success → `workflow.complete {steps}`.
- Client disconnect / Stop → generator stops before the next step (no orphan step).

### 6.4 LLM-assisted creation
- New agent skill **`build-workflow`** under
  `app/skills/workflows/workflows/build-workflow/SKILL.md` — teaches the agent the
  runtime contract (§5.1/§5.3), the `meta` rules, and to draft a script then call
  `save_desk_workflow`. (Accepts the known **6-file skill-catalog test coupling**:
  test_skills_catalog{,_v2}, test_{remaining_,}workflow_skills_phase3,
  test_reference_docs, test_routing_table — plus a routing-table line so the
  orchestrator routes "help me build a workflow" to it.)
- New tool **`save_desk_workflow`** (in the desk agent tool registry + allowlists):
  args `{script}`; calls `upsert_desk_workflow`. Surfaces the **drafted script as the
  tool-call args** — a structured channel the bespoke builder UI renders as the live
  preview (no prose parsing). Persistence happens on user approval (HITL approve, or
  the builder's Save action), not silently.

## 7. Frontend components

### 7.1 `routes/Workflows.tsx` (+ `.live.tsx`, `.css`, tests)
- **Manager view:** list of workflows (slug, title, persona, `scope` badge, source),
  with view/edit (script in a code field, validated via `POST /api/workflows/validate`),
  delete, scope toggle.
- **Builder view (bespoke):** a split layout —
  - **Left:** a focused builder conversation (MessageList + Composer) bound to a
    dedicated builder thread routed to `build-workflow`. Not the generic AgentDesk.
  - **Right:** a live **script preview** (rendered from the agent's
    `save_desk_workflow` draft tool-call args) + extracted meta (slug/title/persona/
    mode/scope) + a **Save** button (calls the CRUD API via `upsert`).
- New `/workflows` route in `lib/routing.ts` + nav entry.

### 7.2 `ChatComposer` slash picker
- **Reserved-command precedence.** A small set of composer commands handled by their
  own logic — notably `/goal` (goal-mode spec) — is **reserved** and takes precedence
  over the workflow-slug picker. The picker excludes reserved commands from its list
  and does not intercept them; the composer dispatches a leading token matching a
  reserved command to that command's handler first, and only falls through to the
  workflow picker otherwise. (A shared reserved-command constant is the minimal
  registry; both specs reference it.)
- When the composer text starts with `/` and the first token is **not** a reserved
  command, fetch `GET /api/workflows` and show a filtered dropdown (slug + title +
  persona). Keyboard navigable.
- Selecting opens a small **launch dialog** (run mode prefilled from `default_mode`,
  overridable `auto|yolo`) → new `onLaunchWorkflow(slug, mode)` prop.
- A `/slug` is **never** sent as a normal chat message.
- Workflow slugs may not collide with reserved commands; `upsert_desk_workflow`
  rejects a reserved slug (`422`).

### 7.3 AgentDesk wiring
- `onLaunchWorkflow` → `POST .../workflows/{slug}/run`, consume the SSE with the same
  reader used for normal messages.
- `workflow.step.start` / `.end` render as lightweight dividers
  (`▶ Step 2/7`); `workflow.log` as a muted narrator line; `workflow.step.error` as an
  error banner; `workflow.complete` as a done marker. Each step's underlying agent
  turn renders as a normal persisted message, so thread history reads naturally.

## 8. Seed: `risk-manager-control-day`

Inserted in migration 0035 as `source=seed`, `scope=shared`, `default_mode` per its
`meta` (`yolo`). Its `script` is the 7 prompts (§5.1) as sequential `await step(...)`
calls, transcribed from `definitions/risk-manager-control-day.md`'s step `user:`
fields. The arena's `.md`/`.fixtures.json` and code are **not modified**.

## 9. Error handling & constraints

- Unknown slug → 404; invalid script/meta → 422 with precise message.
- Step error mid-run → halt, emit `workflow.step.error`, keep completed steps.
- Settle is bounded (`TASK_SETTLE_MAX_ATTEMPTS`); a stuck task degrades to proceeding
  with stale data rather than hanging (same as arena).
- Restricted exec is a footgun-reducer, **not** a security boundary; `shared` scope in
  a real multi-user deployment would need true isolation — **carry-forward**.
- `source=seed` workflows cannot be deleted (keeps the flagship alive).
- Sequential only: a thread runs one workflow at a time; launching while a run streams
  is blocked client-side.

## 10. Testing

**Backend**
- `upsert` happy path + `meta`/AST-guard rejections (missing key, bad enum, non-literal
  meta, `import`, dunder access).
- CRUD endpoints incl. `source=seed` delete refusal and `/validate`.
- `run_desk_workflow` runner: step order, settle-between-steps, mode→yolo flag mapping,
  halt-on-error, SSE event shape (mock `stream_and_persist` + `settle`).
- Seed presence: flagship row exists with 7 `step(...)` calls and valid `meta`.
- `save_desk_workflow` tool persists via `upsert`; `build-workflow` skill registry +
  catalog/routing coupling (the 6 files).

**Frontend**
- Manager CRUD render; editor inline validation.
- Builder split view renders the drafted script from tool-call args; Save calls CRUD.
- Composer `/` picker filters + select → `onLaunchWorkflow(slug, mode)`.
- MessageList step-divider / log / error / complete rendering.

## 11. Open items for the plan phase
- Exact `persona → requested_character` mapping values (existing mechanism used by
  arena `run_match` / AgentDesk).
- Safe-builtins allowlist contents.
- The reserved-command set + where the shared constant lives (coordinate with the
  goal-mode spec so `/goal` and any siblings are excluded from the workflow picker).
- Builder thread lifecycle (ephemeral per builder session vs. persisted, tagged like
  arena threads).

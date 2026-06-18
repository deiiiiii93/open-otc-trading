# Async Subagents — General-Purpose Background Agent Dispatch — Design

**Date:** 2026-05-16
**Status:** Approved (brainstorming complete; pending writing-plans)
**Scope:** Add a *general-purpose async agent* dispatch capability to the desk orchestrator. One async agent type with a generic identity, specialized per dispatch via a task-specific prompt (Claude Code `Agent`-style). Three orchestrator-facing tools (`start_async_agent`, `list_async_agents`, `cancel_async_agent`), backed by the existing `task_runner` thread pool. Results auto-post into the parent chat thread; HITL interrupts bubble to the parent thread for approval. Ships as one PR with ~18-22 internal commits, behavior-preserving through Phase 7; Phase 8 activates orchestrator dispatch.

**Predecessors:**
- [Agent Skills Layer v1](2026-05-14-agent-skills-layer-design.md) — establishes personas, HITL middleware, checkpointer.
- [Agent Skills Layer v2](2026-05-15-agent-skills-layer-v2-design.md) — establishes domain/procedure/routing skills tiers. Routing skills stay orchestrator-only (async agents do not see them).

---

## Decision summary

Six forks closed during brainstorming:

1. **Shipping scope** — Framework + first user, not "framework only" or "one-shot MVP." The framework supports general async dispatch; the first user is a *test scenario* (report-narrative-writer), not a code artifact, because of fork #5.
2. **Runtime** — Same-process asyncio background tasks, reusing `task_runner.submit_async_task` and the `TaskRun` row pattern. Not a separate LangGraph server, not remote. Trade-off: no kernel-level isolation, but reuses existing operational surface.
3. **Permission tier** — Read + artifact-write + bubble-up HITL. Async agent gets the same `QUANT_AGENT_TOOLS` as personas; HITL bubble-up gates writes through the parent thread for user approval.
4. **Result delivery** — Auto-post a new `AgentMessage` on the parent thread when the subagent completes. No notification-only / pull mode.
5. **Subagent shape** — One general-purpose async agent (Claude Code `Agent`-style), specialized via the per-dispatch `prompt` argument. No per-subagent registry, no per-subagent system prompt, no per-subagent skill allowlist. Specialization is the caller's prompt-writing burden, taught in the orchestrator prompt.
6. **Dispatch gate** — Prompt-driven (operationalized §5.4 proxies) plus a per-thread concurrency cap (4) and audit logging of which proxy fired. `start_async_agent` is **not** in `INTERRUPT_TOOL_NAMES` — dispatching is free; the subagent's own side-effects are still HITL-gated, which is where safety belongs.

---

## 1. Architecture overview

**Change in one sentence.** Add a general-purpose async agent dispatch capability to the orchestrator: one async agent type with a generic identity, **specialized per dispatch via a task-specific prompt** (Claude Code `Agent`-style). Three orchestrator-facing tools (`start_async_agent`, `list_async_agents`, `cancel_async_agent`), backed by the existing `task_runner` thread pool. Results auto-post into the parent chat thread; HITL interrupts bubble to the parent thread for approval.

### 1.1 Two-layer prompting (the central mechanism)

Specialization happens at **dispatch time, not build time**:

- **System prompt (constant, identity-level).** Loaded once from `prompts/async_agent.md` at `build_async_agent()` time. Contents: role ("desk's background analyst"), tool-use discipline (read skills before acting, write findings to scratch dir), output contract (final assistant message = the structured finding the orchestrator can quote), no domain specialization. ~80-120 lines, modeled on the persona prompts but role-agnostic.

- **Task brief (per-dispatch, content-level).** Composed by the runner into the first `HumanMessage` from three pieces:
  1. The orchestrator's free-text `prompt` argument — its responsibility to make this self-contained. The orchestrator prompt teaches it how (see §1.2).
  2. Optional `inputs` dict (`portfolio_id`, `position_id`, `report_id`, `valuation_date`, …) rendered as a structured "Inputs" block — same shape as personas get from `_orchestrator_user_prompt`'s context brief.
  3. Framework-injected envelope: the async `task_id`, the per-task scratch dir path (`/trading_desk/async/<task_id>/`), accounting-date anchor carried forward from the parent thread, parent_thread_id (for traceability only — async agent does NOT read parent thread state).

### 1.2 Orchestrator prompt update

`prompts/orchestrator.md` gains a new section "**Async dispatch**" (full draft in §6.2). Concrete rules:

- **When to dispatch async.** Three signals (operationalized in §5.4): (a) long investigation likely; (b) analysis can run in parallel; (c) the workflow is mostly read + synthesize, with at most one HITL-gated write.
- **How to brief.** Treat the async agent like Claude Code's `Agent`: "brief like a smart colleague who just walked into the room — no shared conversation history."
- **What to expect back.** Result auto-posts as a separate assistant message; orchestrator announces dispatch and continues the conversation. Uses `list_async_agents` for status, `cancel_async_agent` to retract.

### 1.3 Five layers that change or get added

| Layer | What changes |
|---|---|
| **Orchestrator** | Three new tools alongside the existing `task` tool. Prompt gains the §1.2 dispatch guidance. |
| **Async agent runtime** (new) | `services/async_agents/` module. `build_async_agent()` constructs a flat (non-persona) deep agent: same `QUANT_AGENT_TOOLS`, broad skills allowlist (`/skills/domains/`, `/skills/procedures/`, `/skills/products/`), filesystem permissions including write to `/trading_desk/async/<task_id>/`. Same `interrupt_on_config()` as personas. |
| **Runner / lifecycle** (new) | `task_runner.submit_async_task` is reused. New `TaskRun.kind = "async_agent"`. Runner owns: (a) invocation with checkpointer thread_id `async:{parent_thread_id}:{task_id}`; (b) interrupt capture → bubble-up; (c) completion → auto-post; (d) stale-task cleanup. |
| **Bubble-up HITL** | Subagent `Interrupt` → runner calls `pending_actions_from_interrupts(persona=f"async:{task_id}")` → writes a new `AgentMessage` on the parent thread with `agent_phase="awaiting_confirmation"` and `meta.async_task_id`. Existing approve/reject UI works unchanged. Resume handler routes by `async_task_id` to the subagent's checkpointer thread_id. |
| **Frontend** | Existing async task panel (pricing/risk/report jobs) renders the new kind. Auto-post messages render as normal chat messages with `character="async_agent"`. ~1-2 component additions. |

### 1.4 What stays unchanged / out of scope

**Unchanged.** Personas, the `task` tool, every existing langchain tool, `agent_channels.yaml`, the skills layer v2, streaming/SSE, every existing API endpoint, the checkpointer DB layout.

**Out of scope.**
- Pre-baked specialized subagents (snowball-diagnostics, risk-report-reviewer). Specialization is the `prompt` argument's job.
- Per-dispatch tool filtering. Same tool list as personas; HITL gates writes; bubble-up handles approval.
- Mid-flight steering (`update_async_subagent`). Cancel + restart.
- Push delivery for auto-post. Frontend polls.
- Cross-thread visibility (subagent dispatched in thread A is only listed/cancelable in thread A).
- Multi-process / remote execution.

---

## 2. Components

New module sits alongside `services/deep_agent/`, paralleling its shape so the contract is familiar to anyone who's read the persona/orchestrator code.

### 2.1 New module: `backend/app/services/async_agents/`

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports `build_async_agent`, `start_async_agent_task`, the three tool classes, and `resume_async_agent_interrupt`. |
| `agent.py` | `build_async_agent(model, tools, checkpointer) -> CompiledStateGraph`. Mirrors `build_orchestrator` but **flat** (no `subagents=...`), with the broader skills allowlist and an extra `FilesystemPermission(write, "/trading_desk/async/<task_id>/**")` mount. Uses `compose_persona_prompt(identity_prompt=..., policy_fragment_names=_ASYNC_POLICY)`. |
| `prompts/async_agent.md` | Identity prompt for the role-agnostic background analyst. Full text in §6.1. |
| `policy.py` | Constants: `_ASYNC_POLICY = ("read-before-compute", "cost-preview", "hitl-batch-size-1", "run-python-rfsw")` — same as trader/risk minus `clarification-protocol` (async agents cannot clarify mid-flight; the identity prompt instead instructs them to make best-guess assumptions and surface them in the finding). Also: `MAX_CONCURRENT_PER_THREAD = 4`, `SCRATCH_DIR_TEMPLATE = "/trading_desk/async/{task_id}/"`. |
| `runner.py` | Lifecycle owner. Public: `start_async_agent_task(parent_thread_id, description, prompt, inputs)` returns `task_id`. Private: `_run(task_id)` body — builds the agent, composes the task brief HumanMessage, drives `agent.ainvoke`, captures `Interrupt`s after each step, dispatches to `bubble_up.py` or `autopost.py` on terminal state. Reuses `task_runner.submit_async_task` for thread-pool dispatch. |
| `bubble_up.py` | When subagent state contains an interrupt: project via `pending_actions_from_interrupts(persona=f"async:{task_id}")`, attach `meta.async_task_id`, write a new `AgentMessage` on the parent thread with `agent_phase="awaiting_confirmation"`. |
| `autopost.py` | When subagent terminates normally: read final AI message + scratch-dir artifacts, write a new `AgentMessage` on the parent thread with `character="async_agent"`, `meta.async_task_id`, and asset links. |
| `resume.py` | `resume_async_agent_interrupt(task_id, decision, message)` — builds `Command(resume=...)` via `build_resume_command` and invokes the subagent agent with its own thread_id. Returns to runner loop, which either bubbles again, auto-posts, or stays running. |
| `tools.py` | Three `BaseTool` subclasses: `start_async_agent`, `list_async_agents`, `cancel_async_agent`. Full schemas in §5. Each tool resolves the *current parent thread_id* from the LangGraph `RunnableConfig`. |

### 2.2 Touched files

| File | Change |
|---|---|
| `services/agents.py` | Add three tool names to `DEEP_AGENT_TOOL_NAMES`. Extend `invoke_resume` to detect `async_task_id` source on a pending action and call `resume_async_agent_interrupt` instead of resuming the parent thread. |
| `services/langchain_tools.py` | Register the three new tools in `QUANT_AGENT_TOOLS`. |
| `services/deep_agent/prompts/orchestrator.md` | New "**Async dispatch**" section per §6.2. |
| `services/deep_agent/skills/policy/cost-preview.md` | Append "**When you have no user in your conversation**" subsection (full text in §6.3) so async agents embed cost previews into HITL action descriptions instead of attempting in-chat previews. |
| `services/deep_agent/hitl.py` | No code changes. `INTERRUPT_TOOL_NAMES` does **not** include the three async tools — dispatching/listing/canceling are free operations; only the *subagent's own* tool calls are gated, and that gating already works via the inherited `interrupt_on_config()`. `pending_actions_from_interrupts` already accepts a `persona` kwarg (`f"async:{task_id}"`); the existing `_LABEL_BY_TOOL` lookup falls through to the raw tool name. |
| `services/task_runner.py` | Extend `mark_stale_tasks_failed` to handle `kind="async_agent"`. (No new function needed — the existing one walks all `TaskRun.status.in_(ACTIVE_TASK_STATUSES)`.) |
| `models.py` | Add `kind="async_agent"` to allowed `TaskRun.kind` values. Add `parent_thread_id: int | None` FK on `TaskRun` (nullable; only async_agent rows populate it). Add `description: str | None`. Add `result_payload: JSON | None` for the final finding text + asset refs. Add `cancel_requested: bool` default `False`. No new table. |
| `schemas.py` | Add `AsyncAgentTaskOut`, `AsyncAgentStartIn`. Extend `AgentActionProposal` with optional `async_task_id: int | None` so the frontend can render "approve action from background task". |
| `main.py` | Extend the resume endpoint to inspect `async_task_id` and call `AgentService.resume_async_agent(...)` instead of `invoke_resume`. Add `GET /api/threads/{thread_id}/async_agents` (lists running async tasks for a thread; reused by both UI and the `list_async_agents` tool). |
| Frontend `types.ts` | Extend `AsyncTaskKind` union with `"async_agent"`. |
| Frontend `AsyncTasksPanel` (or equivalent) | Render the new kind: description + status + "view in chat" (deep-links to the latest auto-post message in the thread) + cancel button. |
| Frontend `useAgentChatController` | When polling messages, recognize `character="async_agent"` and `meta.async_task_id`; render with a subtle "async" affordance. |

### 2.3 Module boundary

The `services/async_agents/` module **only** depends on:
- `services.deep_agent` (build helpers, prompt composer, HITL projection)
- `services.task_runner` (thread pool, lifecycle helpers)
- `services.langchain_tools` (`QUANT_AGENT_TOOLS`)
- `database`, `models`, `schemas`

It is **not** imported by `services/deep_agent/` — no cycle. `agents.py` is the only consumer that wires both together.

---

## 3. Data flow

Four flows worth nailing down: happy path, bubble-up HITL, cancellation, and server restart.

### 3.1 Happy path — dispatch → complete → auto-post

```
1. Orchestrator calls start_async_agent(description, prompt, inputs)
   ├─ tool reads parent_thread_id from RunnableConfig
   ├─ runner.start_async_agent_task():
   │    - INSERT TaskRun(kind="async_agent", status=QUEUED,
   │                     parent_thread_id, description,
   │                     payload={prompt, inputs})
   │    - task_runner.submit_async_task(runner._run, task_id)
   └─ returns {task_id, status: "queued"} to orchestrator IMMEDIATELY

2. Orchestrator turn completes. Its final assistant message says
   "I've started <description> — task #N. I'll let you know when it's
   ready." This message is persisted normally; the user sees it via SSE.

3. In the worker thread, runner._run(task_id):
   ├─ mark_task_running(task_id)
   ├─ Builds agent = build_async_agent(model, tools, async_checkpointer)
   ├─ Composes HumanMessage from prompt + inputs + envelope (§1.1)
   ├─ Drives agent.ainvoke({"messages": [HumanMessage(...)]},
   │                       config={"configurable": {"thread_id":
   │                       f"async:{parent_thread_id}:{task_id}"}})
   ├─ After invoke returns, reads state via aget_state:
   │    - if state.tasks has interrupts → bubble_up.handle() (§3.2)
   │    - else → autopost.handle()
   └─ mark_task_finished(task_id, status=COMPLETED, result_payload=...)

4. autopost.handle(task_id, state):
   ├─ final_text = _extract_final_ai_text(state.values)
   ├─ assets = scan /trading_desk/async/<task_id>/ from state["files"],
   │           materialize under artifacts/agent/thread-<parent>/async-<task>/
   ├─ INSERT AgentMessage(thread_id=parent_thread_id,
   │                      role="assistant",
   │                      character="async_agent",
   │                      content=final_text,
   │                      meta={agent_graph: "async_agent",
   │                            agent_phase: "completed",
   │                            async_task_id: <task_id>,
   │                            description: <description>,
   │                            assets: [...]})
   └─ record_audit(event_type="async_agent.completed", ...)

5. Frontend's message-list poll picks up the new AgentMessage; renders
   it as a normal assistant message with a subtle "Background" affordance.
   The async-task panel chip flips to "completed" with a "view in chat"
   deep link.
```

### 3.2 Bubble-up HITL — interrupt → parent thread → approval → resume

This is the central new mechanism. Sequence assumes the async agent attempted a write tool (e.g., `create_report`).

```
1. agent.ainvoke returns with state.tasks[0].interrupts = [Interrupt(...)]
   (LangGraph paused on the write-tool call.)

2. runner detects interrupts; calls bubble_up.handle(task_id, state):
   ├─ pending = pending_actions_from_interrupts(
   │              state.interrupts,
   │              persona=f"async:{task_id}")
   ├─ Each pending.async_task_id = task_id  (extension to schemas)
   ├─ INSERT AgentMessage(thread_id=parent_thread_id,
   │                      role="assistant",
   │                      character="async_agent",
   │                      content="Background task <description> wants
   │                               approval for <label>.",
   │                      meta={agent_graph: "async_agent",
   │                            agent_phase: "awaiting_confirmation",
   │                            async_task_id: task_id,
   │                            pending_actions: [...]})
   ├─ mark_task_finished is NOT called; instead:
   │    update_task_progress(task_id, message="awaiting approval")
   │    task.status stays RUNNING (the row tracks "running" loosely;
   │    a sub-status field "awaiting_approval" could be added later if
   │    the panel needs it — not in v1)
   └─ runner._run RETURNS. The asyncio task ends.

3. Frontend polls messages, sees the awaiting_confirmation message,
   renders the existing approve/reject UI. The pending action carries
   async_task_id, so the frontend POSTs to
   /api/threads/<parent>/resume with {decision, async_task_id}.

4. main.py resume endpoint:
   ├─ If body.async_task_id is set:
   │    → AgentService.resume_async_agent(task_id, decision, message)
   │      which calls async_agents.resume.resume_async_agent_interrupt(
   │         task_id, decision, message
   │      )
   └─ Else (legacy path): existing invoke_resume on parent thread.

5. resume_async_agent_interrupt(task_id, decision, message):
   ├─ Re-builds the async agent (same model, tools, async checkpointer)
   ├─ command = build_resume_command(decision, message=message)
   ├─ task_runner.submit_async_task(_resume_run, task_id, command)
   └─ returns immediately; user sees a "resumed" tick on the message.

6. _resume_run(task_id, command):
   ├─ agent.ainvoke(command, config={configurable:
   │                                  {thread_id: f"async:{parent}:{task}"}})
   ├─ After invoke: same branch as §3.1 step 3 — either another
   │    interrupt (bubble again) or completion (auto-post).
   └─ Loop continues until terminal state.
```

Key invariants:
- The async agent's checkpointer thread_id is **stable** across the whole task lifecycle (`async:{parent}:{task}`). All resumes target it.
- The parent thread receives **N+1 messages** for a task that bubbles N times: N awaiting-confirmation messages + 1 final auto-post.
- A single resume endpoint serves both legacy persona HITL and async-agent HITL, dispatched by `async_task_id` presence.

### 3.3 Cancellation

Two cancel entry points: orchestrator tool (`cancel_async_agent(task_id)`) and frontend UI button. Both call the same handler:

```
cancel_async_agent_task(task_id):
├─ Read TaskRun. If status terminal → return {ok: false, reason}.
├─ If status QUEUED → mark_task_finished(FAILED, "cancelled before start").
├─ If status RUNNING:
│    - The asyncio task itself isn't trivially cancellable mid-tool-call
│      (ThreadPoolExecutor futures can't be interrupted cleanly).
│    - Mark task.cancel_requested = True.
│    - Runner checks this flag between LangGraph steps inside _run's loop;
│      if set, raises CancelledRun, runner marks task_finished(FAILED,
│      "cancelled by user").
│    - Tools that are already in-flight finish normally; the cancellation
│      only stops the NEXT step.
│    - We do NOT attempt to interrupt an in-progress LLM streaming call.
└─ If status awaiting-approval (bubble-up pending):
     - mark_task_finished(FAILED, "cancelled while awaiting approval").
     - The pending AgentMessage stays in the thread for audit history,
       but its pending_actions are marked superseded.
```

Cancellation is best-effort and may take up to "one tool call" of latency. This matches `task_runner.py`'s existing semantics for pricing/risk jobs.

### 3.4 Server restart

```
On FastAPI startup (existing hook in main.py):
├─ task_runner.mark_stale_tasks_failed(session)
│    Already walks all rows with status IN (QUEUED, RUNNING).
│    Now naturally covers async_agent rows too.
├─ For each stale async_agent task, we additionally insert an
│  AgentMessage on the parent thread:
│    role="assistant", character="async_agent",
│    content="Background task <description> was interrupted by server
│             restart. Re-dispatch if still needed.",
│    meta.agent_phase = "error", meta.async_task_id = <id>
└─ Cancellation/error audit records as usual.
```

No checkpoint recovery in v1. A future enhancement could re-invoke the agent from its last checkpoint, but it risks duplicate side effects and isn't worth the complexity for an MVP.

---

## 4. Persistence and lifecycle

### 4.1 `TaskRun` schema extensions

| Column | Type | Notes |
|---|---|---|
| `kind` | str | Accepts new value `"async_agent"` alongside existing kinds. |
| `parent_thread_id` | int FK → `AgentThread.id`, nullable | Only async_agent rows populate it. Indexed for `list_async_agents` and the panel query. |
| `description` | str, nullable | Caller-provided 3-5 word label (`description` arg of `start_async_agent`). Shown in panel and audit records. |
| `result_payload` | JSON, nullable | On completion: `{final_text, assets, finding_summary}`. On bubble-up: `null` (the pending action lives on the AgentMessage, not here). On failure: `null` — `error` column already exists. |
| `cancel_requested` | bool, default `False` | Set by `cancel_async_agent` and the UI button. Runner polls between LangGraph steps. |

Single Alembic migration adds these four columns. No new tables.

### 4.2 Checkpointer thread_id namespace

Async agent state shares `agent_checkpoints.sqlite` with personas/orchestrator. Composite key prevents collisions:

```
parent thread:          str(thread.id)             e.g. "42"
async agent task:       f"async:{thread.id}:{task.id}"   e.g. "async:42:317"
```

Listing async agents for a thread is a TaskRun query (not a checkpointer query) — `WHERE kind='async_agent' AND parent_thread_id=? AND status IN active`. The checkpointer prefix is only consulted by the resume path, which already knows the task_id.

Stale `async:*` checkpoint rows are **not** pruned in v1. They're cheap, useful for debugging, and a future cleanup job can sweep them by joining on `TaskRun.finished_at < now - retention_window`.

### 4.3 Concurrency

| Bound | Source | Default |
|---|---|---|
| Process-wide async task workers | Existing `Settings.async_task_workers` | (whatever the deployment sets — typically 4-8) |
| Per-parent-thread async agents | New `policy.MAX_CONCURRENT_PER_THREAD` | 4 |
| Per-process async agents | Implicit via the thread pool | bounded by workers |

The thread pool is shared with pricing/risk/report jobs (no separate pool). Heavy concurrent pricing + async agents can starve each other; that's acceptable for v1 and easy to reason about. If starvation shows up in practice, the fix is splitting the pool — but we don't pre-build that.

`start_async_agent` rejects with a structured error if the per-thread cap is exceeded:

```
{ok: false, error: "too_many_running",
 message: "This thread already has 4 background agents in flight. Cancel one or wait."}
```

The orchestrator's prompt explains how to recover from this (cancel oldest or wait and retry).

### 4.4 Audit log events

Inserted via existing `record_audit(session, ...)`:

| `event_type` | Actor | Subject | Payload |
|---|---|---|---|
| `async_agent.started` | `desk_user` | `thread.id` | `{task_id, description, model_selection, proxy_fired}` |
| `async_agent.awaiting_approval` | `system` | `thread.id` | `{task_id, tool_name, interrupt_id}` |
| `async_agent.resumed` | `desk_user` | `thread.id` | `{task_id, decision}` |
| `async_agent.completed` | `system` | `thread.id` | `{task_id, asset_count}` |
| `async_agent.failed` | `system` | `thread.id` | `{task_id, error_type, message}` |
| `async_agent.cancelled` | `desk_user` | `thread.id` | `{task_id, reason}` |
| `async_agent.stale_recovered` | `system` | `thread.id` | `{task_id}` (server restart sweep) |

`proxy_fired` values: `"5plus_calls"`, `"written_artifact"`, `"user_signal"`, `"multiple"`. The orchestrator does not directly emit this; the runner derives it heuristically from the prompt and inputs at dispatch time (substring match on keywords + length heuristic). It's an observational tag, not a gate.

Same shape as existing `chat.message` / `thread.created` events; downstream audit consumers don't change.

### 4.5 Filesystem semantics

Per-task isolation is enforced by `build_async_agent`'s permissions:

```python
FilesystemPermission(operations=["read"],         paths=["/"],                       mode="allow"),
FilesystemPermission(operations=["read", "write"], paths=["/trading_desk/async/<task_id>",
                                                          "/trading_desk/async/<task_id>/**"], mode="allow"),
FilesystemPermission(operations=["read", "write"], paths=["/", "/**"],                mode="deny"),
```

(Same read-everything policy as personas, but writes are pinned to the per-task scratch.)

At completion, `autopost.handle` reuses `_agent_file_assets_from_state` logic but with a per-task subdir:

```
state["/trading_desk/async/<task_id>/foo.md"]
   → on disk:  <artifact_dir>/agent/thread-<parent>/async-<task>/foo.md
   → URL:      /api/artifacts/agent/thread-<parent>/async-<task>/foo.md
   → asset id: agent-async-<task>-foo-md
```

This guarantees:
- Two concurrent async agents on the same parent thread can never overwrite each other's files.
- The parent thread's regular `/trading_desk/` assets and the async agent's assets coexist in the same artifact tree, attributed by URL prefix.
- The agent's scratch is preserved for inspection after completion (until artifact GC, if any, runs).

### 4.6 Retention

| Artifact | Retention | Notes |
|---|---|---|
| `TaskRun` rows | Indefinite (current policy) | Audit/history. |
| Auto-posted `AgentMessage` rows | Indefinite | Part of normal chat history. |
| Awaiting-approval `AgentMessage` rows | Indefinite | Even if the task is later cancelled, the message stays — `meta.superseded=True` is added. |
| Checkpoint rows for `async:*` thread_ids | Indefinite in v1 | A future cleanup job can sweep these. |
| Materialized scratch artifacts | Same as existing `artifacts/agent/thread-<id>/` | Whatever current policy is. |

### 4.7 Failure-mode summary

| Failure | Behavior | User sees |
|---|---|---|
| LLM call raises | Runner catches, `mark_task_finished(FAILED, error=...)`, auto-posts an error message with `agent_phase="error"`. | Background-task chip turns red; an apologetic message in chat. |
| Tool call raises | LangGraph surfaces it as a tool error message. The agent typically continues. If the run finishes anyway, normal auto-post. | Result message mentions the tool failure. |
| Subagent loops indefinitely | LangGraph's `recursion_limit` (default 25) trips, the runner sees a `GraphRecursionError`, treats as failure. | Same as LLM-raise path. |
| Subagent never returns (hang) | No explicit timeout in v1. (TaskRun has no `timeout_at` column.) Restart sweeps via §3.4. | After restart: "interrupted by server restart" message. |
| Cancel while LLM streaming | Cancel flag flips. Stream completes; runner sees cancel before next step; marks `FAILED`. | "cancelled" chip + audit row. |

A per-task hard timeout (e.g., 10 min) is a candidate v2 enhancement. Not in v1 because it requires either a watchdog thread or a heartbeat column, and there's no existing pattern to copy from `task_runner.py`.

---

## 5. Tool schemas

Three LangChain tools wired into `QUANT_AGENT_TOOLS`, scoped to the orchestrator's tool list. All three resolve `parent_thread_id` from `RunnableConfig.configurable.thread_id` — caller doesn't pass it.

### 5.1 `start_async_agent`

```python
class StartAsyncAgentInput(BaseModel):
    description: str = Field(
        ...,
        max_length=80,
        description=(
            "A 3-7 word label for the task, shown in the user's task panel. "
            'Examples: "narrative draft for report 42", '
            '"snowball KO trace for trade 7831".'
        ),
    )
    prompt: str = Field(
        ...,
        max_length=8000,
        description=(
            "Self-contained briefing for the background agent. "
            "Treat the agent like a smart colleague who just walked in — "
            "include the user's goal, relevant entity ids, what's already "
            "been done in this conversation, what to investigate/produce, "
            "and length/format expectations. The agent has the same tools "
            "and skills you do, but no access to this thread's history."
        ),
    )
    inputs: dict[str, Any] | None = Field(
        None,
        description=(
            "Optional structured context the agent will receive verbatim: "
            "portfolio_id, position_id, report_id, valuation_date, etc. "
            "Use this for ids you want the agent to dispatch tools with — "
            "don't bury them in prose."
        ),
    )

class StartAsyncAgentOutput(BaseModel):
    ok: bool
    task_id: int | None = None
    status: str | None = None        # "queued" on success
    error: str | None = None         # set on failure
    message: str | None = None       # human-readable rejection
```

**Behavior:**
1. Resolve `parent_thread_id` from config.
2. Check per-thread concurrency cap (§4.3). If exceeded, return `{ok: false, error: "too_many_running", message: ...}`.
3. Insert `TaskRun(kind="async_agent", status=QUEUED, parent_thread_id, description, payload={"prompt": prompt, "inputs": inputs})`.
4. `task_runner.submit_async_task(runner._run, task_id)`.
5. Record `async_agent.started` audit event with `proxy_fired` heuristic.
6. Return `{ok: true, task_id, status: "queued"}`.

**Tool is not in `INTERRUPT_TOOL_NAMES`** — dispatching is free; the subagent's own writes are still gated.

### 5.2 `list_async_agents`

```python
class ListAsyncAgentsInput(BaseModel):
    include_terminal: bool = Field(
        False,
        description=(
            "If True, also return completed/failed/cancelled tasks from "
            "this thread. Default False returns only running/awaiting tasks."
        ),
    )
    limit: int = Field(20, ge=1, le=100)

class ListAsyncAgentsOutput(BaseModel):
    tasks: list[AsyncAgentTaskSummary]

class AsyncAgentTaskSummary(BaseModel):
    task_id: int
    description: str
    status: str                            # queued | running | completed | failed | cancelled
    awaiting_approval: bool                # True iff a bubble-up message is pending
    started_at: datetime | None
    finished_at: datetime | None
    last_message_preview: str | None       # 120-char preview of the most-recent auto-post,
                                           # if completed; null otherwise
```

**Behavior:**
1. Resolve `parent_thread_id` from config.
2. Query `TaskRun` filtered by `kind="async_agent"`, `parent_thread_id`, optionally including terminal statuses. Order by `started_at desc`.
3. For each row, compute `awaiting_approval` by checking whether the **latest** auto-posted/awaiting `AgentMessage` for this `task_id` has `agent_phase="awaiting_confirmation"`.
4. Return shaped list.

**Use cases (taught in the orchestrator prompt):**
- User asks "what are you working on?" → list, summarize.
- Before starting a new agent for the same workflow, check for an existing one to avoid duplicates.

### 5.3 `cancel_async_agent`

```python
class CancelAsyncAgentInput(BaseModel):
    task_id: int = Field(..., description="Task id from start_async_agent or list_async_agents.")
    reason: str | None = Field(
        None,
        max_length=200,
        description=(
            "Short user-facing reason. Recorded in audit and in the "
            "cancelled-task message on the thread."
        ),
    )

class CancelAsyncAgentOutput(BaseModel):
    ok: bool
    task_id: int
    previous_status: str
    new_status: str
    note: str | None = None
```

**Behavior:**

| Previous status | Action | New status | `ok` |
|---|---|---|---|
| `queued` | Mark `cancelled` directly. | `cancelled` | True |
| `running` | Set `cancel_requested=True`. Runner observes between steps. | `running` (eventually flips to `cancelled`) | True |
| `awaiting_approval` | Mark `cancelled`. Existing pending `AgentMessage` gets `meta.superseded=True`. | `cancelled` | True |
| terminal (completed/failed/cancelled) | No-op. | unchanged | False, with `note` |

Records `async_agent.cancelled` audit event.

**Note:** Per §3.3, cancellation is best-effort with up to "one tool call" of latency. The orchestrator prompt teaches the agent to set user expectations (e.g., "I've requested cancellation of task #N; it'll stop after the current step.").

### 5.4 Operational definitions for "dispatch async" (orchestrator prompt rules)

The orchestrator's prompt teaches dispatch via **two proxies the LLM can actually evaluate** plus an **explicit example table** that pins down the boundary.

#### Proxy 1 — Tool-call budget estimate ("slow" → operationalized)

| Estimated inline tool calls | Action |
|---|---|
| 1 | Inline. (Lookups, single reads.) |
| 2–4 | Inline. (Normal persona work.) |
| **5 or more** | **Dispatch async.** Reading multiple large artifacts (HTML reports, JSON pricing dumps), composing 3+ domain recipes, or cross-checking many positions all trip this. |
| Unknown / "depends on what I find" | **Dispatch async.** Open-ended investigation is the canonical async case. |

#### Proxy 2 — Deliverable shape ("analysis-heavy" → operationalized)

| User-visible deliverable | Action |
|---|---|
| A number / status / single fact | Inline. |
| A short structured answer (≤200 tokens) | Inline. |
| **A written artifact** (narrative, summary, audit, comparison, walk-through, markdown report) | **Dispatch async.** |
| **A multi-part finding** ("show me X, then explain why, then list anomalies") | **Dispatch async.** |
| A decision the user must approve immediately | Inline. HITL pacing must match chat. |

#### Proxy 3 — User intent signals ("parallel" → operationalized)

| Phrase the user said | Action |
|---|---|
| "*and also*", "*while you're at it*", "*at the same time*", "*in parallel*" linking ≥2 deliverables | **Dispatch async** for the slower side. |
| "*draft a [report / narrative / summary]*" + something else | Async for the draft, inline for the something else. |
| "*walk me through*", "*show me step by step*", "*let's go through this together*" | **Always inline.** |
| "*come back when you have*", "*let me know when*", "*in the background*" | **Always async.** |
| Plain question with no parallelism cue | Use proxies 1 and 2. |

**Default when proxies disagree:** If any one proxy says "async" and none say "inline," dispatch async. If proxies conflict, default **inline** (safer — keeps the turn synchronous and visible). The prompt states this tie-breaker explicitly.

### 5.5 Resume contract (HITL bubble-up)

Not a tool, but the API contract for the bubble-up path. Existing `POST /api/threads/{thread_id}/resume` (used today for persona HITL) is extended:

```
Request body:
  decision: "approve" | "reject"
  message: str | null
  pending_action_id: str        # composite id (interrupt_id:index), as today
  async_task_id: int | null     # NEW — set iff the pending action came from a bubble-up

Response:
  message_id: int | null        # id of the latest message after the resume
  routed_to: "parent_thread" | "async_agent"
  task_id: int | null           # echoed if routed_to == "async_agent"
```

**Routing:**
- `async_task_id` present → `AgentService.resume_async_agent(task_id, decision, message)`.
- `async_task_id` absent → existing `AgentService.invoke_resume(...)` on the parent thread.

The frontend reads `pending_action.async_task_id` from the message meta (already populated by §3.2) and includes it in the resume body. No new endpoint, no migration of existing clients.

---

## 6. Prompts (full sketches)

### 6.1 `prompts/async_agent.md` — async agent identity (full draft)

```markdown
You are the desk's background analyst. The orchestrator dispatched you with a
self-contained task brief in your first message. The user is NOT in this
conversation — the orchestrator wrote the brief on their behalf.

## Decision lens
- Read your brief carefully. It is your only source of intent.
- Gather data via tools and skills. Synthesize into the deliverable the brief
  names.
- Return a final assistant message that the orchestrator can quote to the user.

## Tools you use
You have the same QUANT_AGENT_TOOLS the personas use.

Read tools (no confirmation needed):
- `price_product`, `solve_rfq`, `get_rfq_catalog`,
  `draft_rfq_from_natural_language`, `validate_rfq_terms`, `get_positions`,
  `get_latest_position_valuations`, `get_latest_risk_run`,
  `fetch_market_snapshot`, `list_portfolios`, `get_portfolio`,
  `calculate_risk`, `recommend_hedge`, `run_report_batch`, `list_reports`,
  `get_report`.

Write / irreversible tools (HITL — your call BUBBLES UP to the user via the
parent chat thread):
- `price_positions`, `run_risk`, `create_report`,
  `create_or_update_rfq_draft`, `quote_rfq`, `submit_rfq_for_approval`,
  `approve_rfq`, `reject_rfq`, `release_rfq`, `mark_rfq_client_accepted`,
  `book_rfq_to_position`, `import_otc_positions`,
  `import_position_market_inputs`, `delete_portfolio`, `set_portfolio_rule`,
  `remove_positions_from_portfolio`, `run_python`.

Use writes sparingly. Each HITL call pauses you until the user approves in
the parent chat thread. Prefer reading stored results over re-running.

## Scratch and artifacts
- Your scratch dir is `/trading_desk/async/<task_id>/` (provided in your brief
  envelope).
- Write working artifacts there freely (markdown notes, intermediate JSON,
  chart HTML).
- The persisted `/artifacts/...` tree is read-only to you. To produce a
  persisted report artifact, call `create_report` (HITL).

## Clarification policy
You CANNOT ask the user a clarifying question — they are not in your
conversation. When the brief is ambiguous:
- Make the most defensible assumption from the brief.
- Surface the assumption explicitly in your final answer (e.g., "I assumed
  `valuation_date=2026-05-15` because the brief didn't specify.").
- If the ambiguity is fatal (e.g., portfolio_id missing with no way to
  derive it), return a finding that explains what's blocking and stop. The
  orchestrator can re-dispatch with a corrected brief.

## Skills
Your skills catalog covers `/skills/domains/`, `/skills/procedures/`, and
`/skills/products/`. Routing skills (`/skills/routing/`) are
orchestrator-only and not available to you.

When the brief names a procedure or domain skill by slug, `read_file` it at
`limit=1000` BEFORE invoking tools, then follow its recipe. For
product-specific work, also read the matching product card from
`/skills/products/`.

## Accounting date
The brief envelope includes an `Accounting anchor` line carried forward
from the parent thread. Use it as the business-date anchor for
relative-date logic. It is NOT the pricing valuation_date.

## Output style
- Lead with the finding. The orchestrator will paste your final message into
  the chat thread, so make it readable as-is.
- Structure:
  1. one-line headline,
  2. the finding body (bullets, table, or short prose),
  3. any assumptions you made,
  4. artifact references if you wrote any (e.g., "Narrative draft written to
     `/trading_desk/async/<task_id>/narrative.md`").
- Stay under ~800 words unless the brief explicitly asks for more.
- Do not narrate process ("First I called X, then Y…"). Show conclusions.
- Cite the tools/skills you used inline where it adds signal.

## HITL bubble-up
If you call a write/irreversible tool, the framework pauses your run. The
user sees a pending action in the parent chat thread, approves or rejects,
and you resume. You don't see the approval directly — your next step just
proceeds normally (approve) or returns an error (reject). If rejected,
gracefully wrap up: write what you have to scratch, return a finding that
explains what was blocked.

## Forbidden
- Asking the user a question. The user isn't here.
- Writing outside `/trading_desk/async/<task_id>/`. Other paths are
  read-only.
- Starting a sub-async-agent. Async dispatch is orchestrator-only.
- Claiming work was completed when it was rejected at HITL.
```

### 6.2 `prompts/orchestrator.md` — new "Async dispatch" section (full draft)

Inserted between the existing "Compound queries" and "Batch-size-1 rule for HITL" sections.

```markdown
## Async dispatch (for slow / parallel / analysis-heavy work)

Three new tools let you spawn a *background analyst* that runs in parallel
with the chat:
- `start_async_agent(description, prompt, inputs?)` — fire-and-forget;
  returns a task_id.
- `list_async_agents(include_terminal?, limit?)` — see what's running.
- `cancel_async_agent(task_id, reason?)` — stop a running task.

The result auto-posts as a separate assistant message in this thread when
the agent finishes. HITL writes the agent attempts will pause IT and post
an approval message HERE — you do not need to coordinate that.

### When to dispatch (vs handle inline)

Apply three proxies. If any one fires AND no inline counter-case fires,
dispatch async. If proxies conflict, default INLINE.

**Proxy 1 — Tool-call budget.** Estimate the inline turn's tool-call count.
- 1–4 tool calls → inline.
- 5+ tool calls, or "depends on what I find" → async.

**Proxy 2 — Deliverable shape.**
- Single fact / status / short structured answer → inline.
- Written artifact (narrative, summary, audit, comparison, walk-through,
  markdown report) → async.
- Multi-part finding ("show me X, then explain why, then list anomalies")
  → async.

**Proxy 3 — User intent signals.**
- "*and also*", "*while you're at it*", "*in parallel*", "*come back when
  you have*", "*let me know when*" → async (the slower side).
- "*walk me through*", "*show me step by step*", "*together*" → INLINE; the
  user wants synchronous visible work.
- Decision the user must approve immediately → INLINE.

### Canonical examples

| User says                                                          | Action               |
|--------------------------------------------------------------------|----------------------|
| "Draft a narrative companion for report job 42."                   | Async                |
| "Diagnose why Snowball #7831 priced low yesterday."                | Async                |
| "Compare risk run #100 to #95 and flag breaches."                  | Async                |
| "Price portfolio 7 and draft a risk note; let me know when done."  | Two async dispatches |
| "Look at all three Snowballs and explain which is closest to KO."  | Async                |
| "What's the latest NAV on the Snowball Book?"                      | Inline               |
| "Book this RFQ once you check the limits."                         | Inline               |
| "Walk me through the steps to approve this report."                | Inline               |
| "Re-run risk on portfolio 7."                                      | Inline               |
| "Quote me on this RFQ draft."                                      | Inline               |

### How to write the brief (the `prompt` argument)

The async agent has the same tools and skills you do, but **no access to
this thread's conversation history**. Brief it like a smart colleague who
just walked in:

1. State the user's goal in one sentence.
2. Pin every id it should use (portfolio_id, position_id, report_id,
   valuation_date) in the `inputs` dict, NOT in prose.
3. List what's already been done in this conversation that's relevant.
4. State the deliverable explicitly: format, length, what file to write,
   what the final message should contain.
5. Name the procedure or domain skill if you know the right one (same
   naming pattern as `task`).

**Bad:** `prompt="Look at the report and write something nice."`

**Good:** `prompt="Draft a markdown narrative companion for report job 42
('Q1 Snowball Book Review'). User wants: executive summary (3 bullets),
top 3 anomalies with one-line explanations, two recommendations. Read the
report HTML at /artifacts/report-42/output.html and the pricing JSON at
/artifacts/report-42/pricing.json. Write to
/trading_desk/async/<task_id>/narrative.md and return a 3-bullet summary of
what you wrote. Use the `report-query-and-display` skill if relevant."`

With `inputs`:
`{"report_id": 42, "portfolio_id": 7, "valuation_date": "2026-05-13"}`.

### After dispatch

- Announce it in your visible reply: "I've started a narrative draft —
  task #N. I'll let you know when it's ready."
- Continue the conversation. Don't block.
- When the user asks for status, use `list_async_agents`. When they retract
  the request, use `cancel_async_agent`.
- The result auto-posts as a separate assistant message in this thread.
  You do not need to read or quote it.

### Concurrency

- Per-thread cap: 4 in-flight async agents.
- If you hit the cap, `start_async_agent` returns
  `{ok: false, error: "too_many_running"}`. Tell the user and offer to
  cancel an older task.

### What async agents CANNOT do

- Start sub-async-agents. Don't ask them to.
- Ask the user clarifying questions — your brief must be self-contained.
- Their write tools still go through HITL bubble-up; the user approves in
  this thread.
```

### 6.3 Cost-preview policy fragment — async addendum

The persona pattern is "preview as a conversation turn → wait for yes → invoke." Async agents cannot do that. Append the following to `skills/policy/cost-preview.md`:

```markdown
### When you have no user in your conversation

If you are an async agent (no user in this conversation), you cannot
preview-then-wait. Instead, embed the cost preview into the HITL action's
`description` argument — the user will see the estimate on the approval
card before approving the actual tool call. Do not omit the estimate; the
bubble-up message is your only channel to surface it.
```

The fragment is composed in via `compose_persona_prompt` for both personas and async agents, so this single edit reaches both audiences. §6.1's identity prompt does not duplicate the policy.

---

## 7. Testing strategy

Three test surfaces: unit, integration, end-to-end. Mirroring the existing test layout in `tests/test_api.py`, `tests/test_hitl.py`, `tests/test_stream_and_persist.py`.

### 7.1 Unit tests — `tests/test_async_agents_unit.py` (new)

| Test | What it pins down |
|---|---|
| `test_build_async_agent_has_broad_skills_allowlist` | Built agent's middleware exposes `/skills/domains/`, `/skills/procedures/`, `/skills/products/`; does NOT expose `/skills/routing/`. |
| `test_build_async_agent_scratch_permissions` | FilesystemPermissions allow write to `/trading_desk/async/<task_id>/**`, deny write elsewhere except read-everywhere. |
| `test_build_async_agent_uses_same_interrupt_config` | Subagent's `interrupt_on` matches `interrupt_on_config(yolo_mode=...)` from `hitl.py` exactly. |
| `test_compose_async_brief_envelope` | `_compose_task_brief(prompt, inputs, envelope)` produces a `HumanMessage` containing the prompt, structured inputs section, task_id, scratch dir path, and accounting anchor. |
| `test_dispatch_proxy_audit_tag` | When `start_async_agent_task` records audit, the payload contains `proxy_fired`. |
| `test_per_thread_concurrency_cap` | Inserting 4 active TaskRuns then calling `start_async_agent_task` returns `too_many_running`. |
| `test_cancellation_flags_running_task` | `cancel_async_agent_task` on a RUNNING row sets `cancel_requested=True`, does not transition status. |
| `test_cancellation_terminal_is_noop` | Cancel on COMPLETED returns `{ok: false, note: "...already terminal"}`. |
| `test_stale_recovery_sweep_marks_async_failed` | `mark_stale_tasks_failed` flips RUNNING async_agent rows to FAILED, inserts the "interrupted by restart" AgentMessage on the parent thread. |
| `test_cost_preview_fragment_includes_async_clause` | Loaded `cost-preview.md` body contains the "no user in your conversation" addendum. |

### 7.2 HITL bubble-up tests — `tests/test_async_agents_hitl.py` (new)

These are the highest-risk path. Tests use a stubbed agent that deterministically emits an Interrupt then continues on resume.

| Test | Sequence |
|---|---|
| `test_interrupt_bubbles_to_parent_thread` | Subagent emits Interrupt(price_positions) → runner writes new AgentMessage on parent thread with `agent_phase="awaiting_confirmation"`, `meta.async_task_id` set, pending_action carries `tool_name="price_positions"` and `persona="async:<task_id>"`. |
| `test_approval_routes_to_subagent_thread_id` | POST `/api/threads/{parent}/resume` with `async_task_id` → `AgentService.resume_async_agent` is invoked, `invoke_resume` is NOT. The resume Command targets thread_id `async:{parent}:{task}`. |
| `test_reject_then_subagent_completes_with_block` | After reject, subagent's resume returns a finding noting the block; auto-post message records the rejected tool in meta. |
| `test_multiple_bubble_ups_in_one_task` | Subagent interrupts twice (two writes). Two awaiting-confirmation messages appear on the parent thread, each tagged with the same `async_task_id`. Approvals route correctly each time. |
| `test_legacy_persona_hitl_still_works_unchanged` | Existing persona-interrupt test path is regression-safe: resume without `async_task_id` calls `invoke_resume` on parent thread. |
| `test_pending_actions_persona_label` | `pending_actions_from_interrupts(persona=f"async:{task_id}")` produces `AgentActionProposal.persona = "async:<task_id>"`. |
| `test_async_task_id_propagates_to_action_proposal` | `AgentActionProposal.async_task_id` is populated end-to-end (DB → schema → client payload). |

### 7.3 Tool-surface tests — `tests/test_async_agents_tools.py` (new)

Tests the three LangChain tools without involving a real LLM (drive via direct `.ainvoke` on the tool with a fake `RunnableConfig`).

| Test | What it checks |
|---|---|
| `test_start_resolves_parent_thread_from_config` | Tool reads `config.configurable.thread_id`, doesn't accept it as an argument. |
| `test_start_input_validation` | `description` > 80 chars, `prompt` > 8000 chars, missing required fields → ValidationError. |
| `test_list_filters_terminal_by_default` | After creating one RUNNING + one COMPLETED, default `list_async_agents()` returns 1; `include_terminal=True` returns 2. |
| `test_list_awaiting_approval_flag` | Insert RUNNING task + awaiting-confirmation AgentMessage. `list_async_agents()` returns `awaiting_approval=True` for that task. |
| `test_cancel_returns_previous_and_new_status` | Cancel on a RUNNING row returns previous=`running`, new=`running` (because cancel is best-effort). Cancel on QUEUED row returns previous=`queued`, new=`cancelled`. |
| `test_cancel_on_other_threads_task_refused` | Tool resolves parent_thread_id from config. If `task_id` belongs to a different parent_thread, refuse with `{ok: false, error: "not_owned"}`. |

### 7.4 Integration: orchestrator dispatches a real (stubbed-model) async agent

`tests/test_async_agents_integration.py` (new). Drives the full path with a stubbed model that returns scripted tool calls.

| Test | Scenario |
|---|---|
| `test_orchestrator_dispatches_then_autoposts` | Orchestrator calls `start_async_agent` with a fixed brief. Runner runs the subagent (stubbed model returns a final AI message immediately). `autopost.handle` writes the result message on parent thread with `character="async_agent"`. Three AgentMessages exist on the parent thread: user, orchestrator-with-dispatch-tool-call, async-result. |
| `test_orchestrator_dispatches_subagent_writes_artifact` | Subagent writes `/trading_desk/async/<task_id>/finding.md` in state. Runner materializes to disk under `artifacts/agent/thread-<parent>/async-<task>/finding.md`. Auto-post message includes an asset link. |
| `test_dispatch_then_cancel_via_tool` | Orchestrator dispatches, then in a later turn calls `cancel_async_agent`. Runner observes `cancel_requested` between steps, marks failed, posts cancellation message. |
| `test_dispatch_with_inputs_dict` | `inputs={"portfolio_id": 7}` appears in the subagent's HumanMessage envelope. |

### 7.5 Frontend tests

| File | Tests |
|---|---|
| `frontend/src/components/AsyncTasksPanel.test.tsx` (or wherever the existing panel test lives) | New chip kind renders for `kind="async_agent"`; cancel button calls correct endpoint; "view in chat" deep-links to the latest auto-post message. |
| `frontend/src/components/FloatingAgentMiniChat.test.tsx` (extend existing) | Renders an assistant message with `character="async_agent"` and a subtle "Background" affordance; renders an `awaiting_confirmation` message with `async_task_id` and a working approve/reject flow that POSTs the `async_task_id`. |

### 7.6 What we explicitly do NOT test

- The async agent's reasoning quality. That's evaluation, not unit testing; out of scope for this spec.
- LLM-driven proxy decisions ("did the orchestrator pick async for the right case?"). Tested by audit-log inspection in production, not in CI.
- Long-running soak. The thread pool's behavior under load is already tested by existing pricing/risk tests.

### 7.7 Test data fixtures

Reuse existing `pytest` fixtures from `tests/conftest.py`:
- `client` (TestClient over FastAPI app)
- `session` (SQLAlchemy session bound to an in-memory or temp-file SQLite)
- Existing AgentThread/AgentMessage factory helpers

New fixture: `stub_async_agent_factory` returning a builder that produces a fake `CompiledStateGraph` whose `ainvoke` is scripted (returns AI messages and/or interrupts on demand). Single helper, used across §7.2, §7.3, §7.4.

---

## 8. Rollout

### 8.1 PR shape

One PR (`feat/async-subagents`), ~18-22 internal commits, **behavior-preserving up through Phase 7**; Phase 8 flips the orchestrator prompt and activates dispatch. No feature flag — the prompt itself is the switch, and the change can be reverted cleanly by reverting Phase 8.

### 8.2 Phase-by-phase commit sequence

| # | Phase | Commits | Behavior change visible? |
|---|---|---|---|
| 1 | **Schema migration** | 1 Alembic migration adding `TaskRun.parent_thread_id`, `description`, `result_payload`, `cancel_requested`; 1 commit updating `models.py` + schema regression test. | No (new columns nullable; no existing reads/writes touch them). |
| 2 | **Async agent runtime** | `services/async_agents/__init__.py`, `agent.py`, `prompts/async_agent.md` (full draft from §6.1), `policy.py`. Plus unit tests `test_build_async_agent_*`. | No (module isolated; nothing imports it yet). |
| 3 | **Runner + lifecycle** | `services/async_agents/runner.py`, `bubble_up.py`, `autopost.py`, `resume.py`. `task_runner.mark_stale_tasks_failed` extension (one-line change). Unit tests for runner state transitions, stale recovery. | No (no callers yet). |
| 4 | **Schemas + endpoint plumbing** | `schemas.py`: `AsyncAgentTaskOut`, `AsyncAgentStartIn`, `AgentActionProposal.async_task_id` field. `main.py`: extend resume endpoint to dispatch by `async_task_id`; add `GET /api/threads/{id}/async_agents`. Tests for endpoint routing. | No (legacy resume path unchanged for missing `async_task_id`; new endpoint not yet called from UI). |
| 5 | **Tools registration** | `services/async_agents/tools.py` (three `BaseTool` subclasses). `langchain_tools.py`: register them in `QUANT_AGENT_TOOLS`. `agents.py`: add to `DEEP_AGENT_TOOL_NAMES`. Tool-surface tests. | **Tools exist on the orchestrator but its prompt doesn't mention them — the orchestrator won't call them.** Pre-activation state. |
| 6 | **HITL bubble-up integration** | Wire `AgentService.resume_async_agent` into `main.py` resume handler. Integration tests for bubble-up (§7.2). Audit-log entries (`async_agent.awaiting_approval`, `async_agent.resumed`). | No user-visible change (still no dispatches occurring). |
| 7 | **Frontend integration** | `frontend/src/types.ts` adds `async_agent` kind. `AsyncTasksPanel` (or equivalent) renders new chip + cancel + view-in-chat. `useAgentChatController` recognizes `character="async_agent"` and `async_task_id` on pending actions. Frontend tests. | UI can render async-agent rows IF any exist. (None yet, since no dispatches.) |
| 8 | **Activation** | Update `prompts/orchestrator.md` with the §6.2 "Async dispatch" section. Update `prompts/cost-preview.md` with the §6.3 "no user in your conversation" addendum. Add integration test (§7.4) where orchestrator dispatches a stubbed async agent. | **Orchestrator now dispatches.** First user-visible behavior change. |

Each phase ends in a green test suite. Phases 1-7 are individually revertable. Phase 8 is the revertable activation switch.

### 8.3 Behavior-preservation invariant

Through Phases 1-7:

| Existing behavior | Status |
|---|---|
| Persona dispatch via `task` tool | Unchanged. |
| HITL on persona writes | Unchanged. |
| Resume endpoint for persona HITL | Unchanged (routes by absence of `async_task_id`). |
| `stream_and_persist` / SSE | Unchanged. |
| Skills layer v2 (domains/procedures/products/routing) | Unchanged. |
| `agent_channels.yaml` / model selection | Unchanged. |
| All existing API endpoints | Unchanged (only the resume body grew a new optional field). |
| All existing tests | Pass. |

After Phase 8:

| New behavior | Verified by |
|---|---|
| Orchestrator dispatches async per §6.2 proxies | Integration test §7.4 + audit-log proxy_fired field. |
| Bubble-up message appears on parent thread when subagent writes | Integration test §7.2. |
| Auto-post message appears on parent thread when subagent completes | Integration test §7.4. |
| Existing single-persona work still goes inline | Regression test (e.g., "what's the NAV" should not dispatch). |

### 8.4 Documentation updates

| File | Update |
|---|---|
| `README.md` | One paragraph in the agent overview section: "Async dispatch. The orchestrator can spawn a general-purpose background agent via `start_async_agent` for slow or analysis-heavy work. Results auto-post in the chat thread; HITL writes bubble up for approval. See [spec](docs/superpowers/specs/2026-05-16-async-subagents-design.md)." |
| `backend/app/services/deep_agent/skills/README.md` | One sentence noting routing skills are orchestrator-only and not visible to async agents. |
| Spec document itself | Committed alongside Phase 1, dated 2026-05-16. |
| `docs/superpowers/plans/` | Plan document written by writing-plans skill (next step). |

### 8.5 Acceptance criteria

Marked complete when **all** of:

- [ ] Alembic migration applied and reversible.
- [ ] All test suites green (unit, HITL, tool, integration, frontend).
- [ ] Manual: dispatch a real async agent (with a real LLM) via the chat, observe auto-post on completion.
- [ ] Manual: dispatch an async agent that calls `create_report`, approve the HITL bubble-up message, observe the subagent resume and auto-post.
- [ ] Manual: dispatch an async agent, cancel via the panel button, observe the cancellation message.
- [ ] Manual: restart the FastAPI worker mid-dispatch, observe stale-recovery message.
- [ ] Audit log shows `async_agent.started` events with `proxy_fired` populated for at least 5 dispatches across varied request shapes.
- [ ] `list_async_agents` returns correct results during the integration soak.
- [ ] Per-thread cap rejection path tested (5th concurrent dispatch returns `too_many_running`).

### 8.6 Migration / deployment notes

- Single Alembic migration. Forward-only; no data backfill needed.
- New thread pool is the **same** as `task_runner._EXECUTOR` — no new infrastructure to provision.
- `agent_checkpoints.sqlite` grows by ~1 row per async dispatch's checkpoint. Existing growth/backup procedures cover it.
- Restart sweep is automatic via the existing startup hook in `main.py`. No deploy choreography needed.
- Frontend bundle grows by the new panel chip and the slight `FloatingAgentMiniChat` extension — well under the bundle-size budget.

### 8.7 Out of scope (defer to v2 specs)

- Per-task hard timeout (e.g., 10 min watchdog).
- Pre-baked specialized subagents (snowball-diagnostics, risk-report-reviewer).
- Sub-async-agent spawning (an async agent dispatching another async agent).
- Mid-flight steering (`update_async_subagent`).
- WebSocket / push delivery for auto-post.
- Cross-thread visibility of async tasks.
- Checkpoint-based resumption after server restart.
- Skill `async_eligible: true` frontmatter tag (sanctioned-dispatch gate).

# Session Tracing & Audit Module — Design

**Date:** 2026-06-11
**Status:** Approved (brainstorm validated with user)

## Problem

The agent is a financial agent: every run that prices, books, or hedges must be
fully auditable. Today tracing relies on LangSmith — a paid, non-open-source,
externally hosted service — toggled implicitly through `LANGSMITH_TRACING` /
`LANGCHAIN_TRACING_V2` env vars (currently `"false"`, i.e. no tracing at all).

We want a **self-hosted session tracing module** modeled on LangSmith's
architecture, with LangSmith still supported behind an explicit env switch, and
a per-AgentThread link that opens the right trace view for the active backend.

## Decisions (validated with user)

1. **Capture scope: full fidelity.** Nested run tree (chain/LLM/tool spans),
   full prompts & completions, tool args & results, token usage, latencies,
   errors, model names.
2. **Switching: mode enum, both allowed.** `OPEN_OTC_TRACING=local|langsmith|both|off`,
   default `local`. `both` exists for side-by-side validation during rollout.
3. **Viewer: LangSmith-style tree view** as a route in the existing frontend.
   (No waterfall timeline in v1.)
4. **Audit grade: append-only convention.** Insert-then-finalize is the only
   write path; no update/delete API; trace tables excluded from cleanup jobs;
   keep forever. (No crypto hash chain in v1.)
5. **Capture mechanism: custom `BaseTracer` callback handler** (Approach A).
   Rejected: extending `StreamCollector` (subagent tool events provably do not
   surface in the parent's `astream_events` — known escalation-fix lesson — so
   it cannot be a complete audit); OpenTelemetry stack (heavy infra, generic
   non-LLM-aware viewer outside the app).

## Architecture

New module `backend/app/services/tracing/`:

```
tracing/
  __init__.py
  config.py    # TracingMode enum + tracing_callbacks() factory
  tracer.py    # LocalTracer(BaseTracer)
  store.py     # TraceStore: separate SQLite file, background writer
```

### config.py — mode resolution and handler factory

- `TracingMode` enum: `LOCAL | LANGSMITH | BOTH | OFF`, resolved from
  `OPEN_OTC_TRACING` (default `local`; unknown values → `local` with a warning).
- `tracing_callbacks(*, thread_id, message_id=None, task_id=None,
  workflow_id=None) -> list[BaseCallbackHandler]` returns the handlers to
  attach to one run:
  - `local` → `[LocalTracer]`
  - `langsmith` → `[LangChainTracer(project_name=LANGSMITH_PROJECT)]`
  - `both` → both handlers
  - `off` → `[]`
- The mode enum is the **single authority**. In `langsmith`/`both` mode the
  `LangChainTracer` is attached explicitly; the legacy global
  `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` vars stay `false` so there is no
  double-tracing ambiguity. `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` keep
  their native meaning.

### Entry-point integration

Both agent execution paths attach the handlers and inject run metadata:

- `AgentService.stream_and_persist` (`backend/app/services/agents.py`) — the
  interactive thread turn driving `agent.astream_events(...)`.
- Async-agent runner (`backend/app/services/async_agents/runner.py`) — background
  task agents; metadata carries `task_id` plus `parent_thread_id` as `thread_id`.

Injection = `config["callbacks"] += tracing_callbacks(...)` and
`config["metadata"] |= {"thread_id": ..., "message_id": ..., "task_id": ...,
"workflow_id": ...}`. The metadata is what lets **both** LangSmith and the
local store filter traces by thread.

Callbacks propagate into subagent graphs, so nested subagent LLM/tool activity
is captured — the property `astream_events` lacks.

### tracer.py — LocalTracer

`LocalTracer(BaseTracer)` (same base class as LangSmith's `LangChainTracer`):

- On run **create**: build a span record (id, trace_id, parent_run_id,
  dotted_order, denormalized thread/task/workflow/message ids from metadata,
  name, run_type, start_time, inputs, extra) and enqueue an INSERT.
- On run **update** (end): enqueue the single FINALIZE (outputs, error,
  end_time, status, token counts extracted from LLM end payloads).
- `dotted_order` copies LangSmith's scheme: concatenated
  `{start_time:%Y%m%dT%H%M%S%fZ}{run_id}` segments joined by `.` along the
  ancestor path — the whole tree renders correctly with one `ORDER BY`.
- Root rows aggregate descendant token counts at finalize time for cheap
  per-trace totals.
- **Every hook body is exception-wrapped**: tracing failures log a warning and
  drop the span; they never propagate into the agent run (same philosophy as
  `ToolErrorBoundaryMiddleware`).

### store.py — TraceStore

- Separate SQLite file `data/agent_traces.sqlite3`, override via
  `OPEN_OTC_TRACE_DB_PATH`. WAL mode. Schema auto-created on first open
  (no alembic — independent lifecycle, backup, and retention from the
  business DB; high-volume writes never contend with business tables).
- Single daemon **writer thread** consuming a `queue.Queue` of
  insert/finalize records, batching commits. Tracer hooks never block the
  event loop on disk I/O.
- If the DB cannot open, local tracing disables itself with one error log.
- Public surface: `enqueue_insert(span)`, `enqueue_finalize(span)`, and
  read methods. **No update/delete API.** Trace tables are excluded from any
  cleanup/maintenance jobs.

## Data model

One table, `trace_runs` (every span is a row — LangSmith's run model):

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | LangChain `run_id` UUID |
| `trace_id` | TEXT, indexed | root run id |
| `parent_run_id` | TEXT, indexed | NULL for roots |
| `dotted_order` | TEXT, indexed | sortable tree path |
| `thread_id` | INTEGER, indexed | from run metadata; audit join key to business DB |
| `task_id` / `workflow_id` / `message_id` | INTEGER nullable | further join keys |
| `name` | TEXT | e.g. `price_position`, `ChatAnthropic` |
| `run_type` | TEXT | `chain` / `llm` / `tool` / `retriever` |
| `start_time` / `end_time` | TEXT (ISO) | `end_time` NULL while running |
| `status` | TEXT | `running` → `success` \| `error` |
| `inputs` / `outputs` | TEXT (JSON) | full-fidelity prompts, completions, tool args/results |
| `error` | TEXT nullable | message + stack trace |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | INTEGER nullable | from LLM end events; aggregated on roots |
| `extra` | TEXT (JSON) | tags, metadata, model params |

Indexes: `(thread_id, start_time)`, `(trace_id)`, `(parent_run_id)`.

**Append-only semantics, stated precisely:** each row is INSERTed at span
start (a crash mid-run still leaves audit evidence of what was attempted) and
finalized **exactly once** at span end. That pair is the only mutation that
exists; nothing else can write, update, or delete.

## API

New router `backend/app/routers/tracing.py`:

- `GET /api/tracing/config` → `{ "mode": "...", "langsmith_url": "..." }` —
  what the frontend needs to render trace links. `langsmith_url` is the
  project page URL derived from `LANGSMITH_PROJECT` (LangSmith deep-link
  filter URLs are not a stable public format; the injected `thread_id`
  metadata makes in-LangSmith filtering one click).
- `GET /api/tracing/threads/{thread_id}/traces` → root runs for the thread
  (name, status, duration, token totals, start time), newest first, paginated.
- `GET /api/tracing/traces/{trace_id}` → full span tree in one `dotted_order`
  query, inputs/outputs **truncated to a 2,000-character preview** per field
  per span (with a `truncated: true` flag when clipped).
- `GET /api/tracing/runs/{run_id}` → one span, full untruncated payload
  (lazy detail fetch keeps huge prompts off the tree response).

## Frontend

### Trace viewer — new route `/tracing`

Sidebar entry; follows existing page structure and `UI_STYLE_GUIDE.md` token
conventions (zero hardcoded colors). Three panes, LangSmith-style:

1. **Left — trace list**: traces for the selected thread (driven by
   `?thread={id}` query param; with no param, most recent traces overall),
   showing status dot, root name, start time, duration, token total.
2. **Middle — span tree**: expandable nested tree with run-type badges
   (chain/llm/tool), status dots, duration and token chips per span.
3. **Right — detail panel**: selected span detail — prompt/completion rendered
   readably, tool args/results as formatted JSON, error with stack trace.
   Fetches the untruncated payload lazily via `GET /api/tracing/runs/{id}`.

### Per-thread trace link

In the AgentDesk thread sidebar, each thread row gets a small "trace" action
alongside the existing rename/delete, behavior driven by
`GET /api/tracing/config`:

- mode `local` or `both` → internal router link to `/tracing?thread={id}`
- mode `langsmith` → external link (new tab) to the LangSmith project URL
- mode `off` → no link rendered

## Error handling summary

- Tracer hooks: exception-wrapped; warn + drop span, never break the run.
- Writer thread: failed batch → warn + drop, keep consuming.
- Trace DB unopenable: local tracing self-disables with one error log.
- API: 404 for unknown ids; empty lists are normal results, not errors.

## Testing

- **Store**: schema bootstrap, insert/finalize round-trip, absence of any
  mutation API, writer-thread batching, unopenable-DB self-disable.
- **Tracer**: synthetic callback sequences asserted against persisted trees,
  including a nested-subagent case; token extraction; dotted_order ordering;
  exception-in-hook isolation.
- **Config**: all four mode values + unknown-value fallback; handler list
  composition per mode.
- **Router**: seeded trace DB; thread filtering, tree assembly, truncation,
  full-payload detail.
- **Frontend (vitest)**: Tracing page renders a canned tree; thread-link
  variants per mode (internal / external / hidden).
- **Integration**: drive a minimal LangGraph agent with a stub LLM through
  `stream_and_persist` and assert spans land in the trace store.

## Out of scope (v1)

- Waterfall/Gantt timeline visualization.
- Tamper-evident hash chain.
- Retention/pruning tooling (keep forever; prune manually if ever needed).
- Live-updating viewer (polling/SSE refresh of running traces).
- Datasets/evals/feedback — LangSmith features beyond tracing & audit.

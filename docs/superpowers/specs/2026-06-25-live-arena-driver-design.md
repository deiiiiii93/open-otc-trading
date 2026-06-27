# Live Arena Driver — Design

**Date:** 2026-06-25
**Status:** Approved (design); pending spec review
**Author:** desk (golden-workflows follow-up)

## Problem

The LLM arena (`backend/app/services/arena/`) scores candidate models on golden
desk workflows, but its production driver is a stub:

```python
# backend/app/services/arena/runner.py:376
def _make_langchain_agent_driver(lc_agent):
    def driver(history, step_index):
        raise NotImplementedError("Real LangChain agent driving is not yet implemented...")
```

So `run_match` only works with an injected fake `agent=` (unit tests). A real
`ARENA_LIVE` run records every match as `status="failed"`. We want the arena to
actually run real LLMs against a golden workflow and produce a faithful,
scoreable `MatchTranscript`.

The historical blocker was `skills_routed`: nothing in the agent emits which
*skill* was followed, so the objective manifest's ~7 skill milestones plus the
`skills_routed_sequence` success assertion had no signal. **Resolved by reading a
real trace** (`data/agent_traces.sqlite3`): the deep-agent loads each skill it
commits to via `read_file` on `/skills/workflows/<domain>/<name>/SKILL.md`. That
`read_file` call *is* the ground-truth routing signal — ordered by `start_time`
it yields both the routed set and the sequence, no inference.

## Decisions

### D1 — Drive the real production orchestrator, not a parallel arena agent
Replace the stub + `build_arena_agent`/`arena_tools` with the production turn
driver `AgentService.stream_and_persist(thread_id, content, model_selection, …)`
(`backend/app/services/agents.py:2306`). The arena then measures the *actual*
desk agent — same orchestrator, personas, skills, tools, middleware — and
tracing is attached for free at the existing chokepoint
`graph_run_config()` (`backend/app/services/deep_agent/runtime_config.py:9`).
This deletes the `build_arena_agent` / `arena_tools` / `_wrap_run_tools` /
`_make_langchain_agent_driver` / `_drive_step` machinery (all arena-only clones).

**Rationale:** faithfulness (no clone drift), free tracing, less code.
**Cost:** the arena now depends on the desk turn driver's contract; mitigated by
keeping `run_match`'s injectable seam for tests (see D7).

### D2 — Harvest the transcript from the trace DB, not a live event stream
After driving a thread to completion, build the `MatchTranscript` by reading the
trace store (`backend/app/services/tracing/store.py`), not by scraping
`astream_events`. A new `transcript_from_trace(thread_id, workflow, model)`
walks each turn's span tree (`TraceStore.get_trace(trace_id)`, ordered by
`dotted_order`) and emits the **same `turn_events` dict** that
`extract_step_from_events` already consumes:

| transcript field | trace source |
|---|---|
| `skills_routed` (ground truth, ordered) | `read_file` spans whose `inputs.file_path` matches `^/skills/workflows/.+/SKILL\.md$` → skill name = penultimate path segment; ordered by `start_time` |
| persona (informational) | `task` spans → `inputs.subagent_type` ∈ {trader, risk_manager, high_board} |
| `tool_calls` | spans with `run_type='tool'`, **excluding** meta tools (`task`, `read_file`, `write_todos`) → `{id, name, args}` from `inputs` |
| `tool_results` | same tool spans → `outputs` (normalised by `extract_step_from_events`) |
| `response_text` | the turn's final assistant-message span `outputs` (last `model`/`ChatAnthropic` span with text content in the root turn) — keeps the harvester session-free; trace is the single source |
| `artifacts` | tool outputs that declare an artifact (e.g. `write_report_artifact`) — preserve existing `_copy_artifacts` behaviour |

**Rationale:** the trace is persisted, ordered, and already captures everything;
`skills_routed` becomes ground truth instead of inference.
**Caveat (documented, not fixed):** a skill the model executes from its injected
*description alone* without `read_file` would be missed. Multi-step golden
workflows reliably read the SKILL.md, so this is an accepted edge case.

### D3 — Arena threads live in the main desk DB, tagged + filterable
The flagship workflow *writes* (queues risk runs, books hedges), and the user
wants arena runs visible in Agent Desk. So arena drives **real `AgentThread`
rows in the main business DB**, tagged `source="arena"` and `arena_run_id=<run>`.
Migration `0033_agent_thread_source` adds both columns
(`source TEXT NOT NULL DEFAULT 'desk'`, `arena_run_id INTEGER NULL`, indexed).
Existing rows default to `'desk'`, so the chat UI is unaffected.

### D4 — Drop `isolated_match_db`; seed fresh fixtures per match into the main DB
Because we no longer reconfigure the global `database.SessionLocal`, the
`isolated_match_db` global-mutation hack (the thing that forced pool=1) is
removed. Each match seeds a **fresh** fixture set (`apply_seed`) directly into
the main DB, producing **unique autoincrement IDs per match** — so model A and
model B never share a portfolio and cannot contaminate each other, even without
cleanup. Seed-`$seed.<ns>.<alias>` refs resolve against the freshly-inserted IDs
exactly as today. Seeded fixture portfolios are **not** auto-cleaned (they
persist as inspectable demo data; cleanup is out of scope — D-OOS).

### D5 — Matches stay sequential (v1)
`execute_arena_run_task` already fans out sequentially. The shared async
checkpointer SQLite (`settings.agent_checkpoint_db_path`) serialises writes, so
v1 keeps one match at a time. Concurrency is a documented future option, no
longer architecturally blocked.

### D6 — Model binding via Zenmux channel
New `arena_model_to_selection(model: ArenaModel) -> dict` in
`backend/app/services/arena/models.py` parses `zenmux_name` (`"openai/gpt-5.5"`)
into `{"channel": "zenmux", "provider": "openai", "model": "gpt-5.5"}` and
validates it through `resolve_agent_model_selection`
(`backend/app/services/deep_agent/model_factory.py:108`). Live runs require the
`zenmux` channel + the candidate models to exist in `config/agent_channels.yaml`
and `ZENMUX_API_KEY` to be set; absence ⇒ the match fails cleanly (recorded
`status="failed"`, per existing `_execute` try/except).

### D7 — Preserve a test seam
`run_match(loaded, model, *, artifact_root, drive=None, harvest=None)` keeps
injectable hooks: `drive` (defaults to the real `stream_and_persist`-based
thread driver) and `harvest` (defaults to `transcript_from_trace`). Unit tests
inject a fake `drive` that posts canned assistant messages and a fake `harvest`
that returns seeded trace rows — no live LLM, no real trace DB. The old `agent=`
/ `chat=` parameters are removed (their machinery is deleted).

### D8 — Run with HITL gates auto-cleared
Arena turns run `stream_and_persist(..., yolo_mode=True, confirmed_cost_preview=True)`
so confirmation / cost-preview / HITL middleware do not pause the run. This also
guarantees exactly **one orchestrator root span per turn**, so root traces map
1:1 to workflow steps in chronological order.

## Architecture

### Control flow (rewritten `run_match`)

```
run_match(loaded, model, *, artifact_root, drive=None, harvest=None):
    drive   = drive   or _default_drive       # stream_and_persist-based
    harvest = harvest or transcript_from_trace
    with main-DB session:
        seed = apply_seed(loaded.fixtures, session)        # fresh IDs (D4)
        thread = AgentThread(source="arena", arena_run_id=..., character=persona_of(loaded))
        session.add(thread); session.commit()
    for step_index, step in enumerate(loaded.workflow.steps):
        drive(thread_id, step.user, model_selection=arena_model_to_selection(model))
        # one turn on the SAME thread → one orchestrator root trace
    transcript = harvest(thread_id, loaded.workflow, model)  # → MatchTranscript
    transcript.artifacts copied under artifact_root via _copy_artifacts (kept)
    return transcript
```

`_default_drive` is an async-internal sync wrapper: it runs
`AgentService.stream_and_persist(...)` to completion via `asyncio.run` (run_match
is called from the sync `execute_arena_run_task` loop), consuming the SSE stream
and discarding tokens (the transcript comes from the trace, not the stream).

### Persona mapping
`GoldenWorkflow.persona` ∈ {trader, risk_manager, sales, quant}; desk
`AgentThread.character` ∈ {trader, risk_manager, high_board}. Map
trader→trader, risk_manager→risk_manager, and sales/quant→trader (default) for
v1. The flagship is `risk_manager`, so this is exercised directly; the fallback
is documented.

### Trace-harvest module
New file `backend/app/services/arena/trace_harvest.py`:

```python
SKILL_PATH_RE = re.compile(r"^/skills/workflows/.+/([a-z0-9-]+)/SKILL\.md$")
META_TOOLS = {"task", "read_file", "write_todos"}

def transcript_from_trace(thread_id, workflow, model, *, store=None) -> MatchTranscript:
    store = store or get_trace_store()
    roots = sorted(store.list_thread_traces(thread_id, limit=1000),
                   key=lambda r: r["start_time"])          # chronological
    steps = []
    for i, (wf_step, root) in enumerate(zip(workflow.steps, roots)):
        spans = store.get_trace(root["trace_id"])           # ordered by dotted_order
        turn_events = _spans_to_turn_events(i, wf_step.user, spans)
        steps.append(extract_step_from_events(turn_events))
    return MatchTranscript(schema_version=1, run_id=None,
                           workflow_id=workflow.id, model_id=model.slug,
                           started_at=..., finished_at=..., steps=steps)
```

`_spans_to_turn_events` parses `inputs`/`outputs` JSON per span and applies the
D2 mapping table.

## Files

| Action | Path | Responsibility |
|---|---|---|
| Create | `backend/alembic/versions/0033_agent_thread_source.py` | Add `source`, `arena_run_id` to `agent_thread` (migration-local Core table) |
| Modify | `backend/app/models.py:124` | `AgentThread.source`, `AgentThread.arena_run_id` |
| Modify | `backend/app/schemas.py` | `AgentThreadOut.source` |
| Create | `backend/app/services/arena/trace_harvest.py` | `transcript_from_trace`, `_spans_to_turn_events` |
| Modify | `backend/app/services/arena/runner.py` | Rewrite `run_match`; add `_default_drive`; delete `isolated_match_db`, `_drive_step`, `build_arena_agent`, `arena_tools`, `_wrap_run_tools`, `_make_langchain_agent_driver`, `_default_status_checker` |
| Modify | `backend/app/services/arena/models.py` | `arena_model_to_selection` |
| Modify | `frontend/src/types.ts:25` | `Thread.source?: string` |
| Modify | `frontend/src/routes/AgentDesk.tsx` | "Show arena threads" toggle (default off) filtering `source==='arena'` |
| Tests | `backend/tests/test_arena_runner.py`, `…trace_harvest.py`, `…arena_models.py`, `…migration_0033.py`, `frontend/src/routes/AgentDesk.test.tsx` | See Testing |

## Data flow

1. `execute_arena_run_task` (unchanged) → `run_match(loaded, model, artifact_root=…)`.
2. `run_match` seeds fixtures (fresh IDs), creates arena thread, drives each
   `step.user` through `stream_and_persist` (yolo) bound to the Zenmux model.
3. Each turn → one orchestrator run → spans persisted to the trace DB keyed by
   `thread_id`/`trace_id`.
4. `transcript_from_trace` reads the trace → `MatchTranscript`.
5. `execute_arena_run_task` (unchanged) runs `judge_match`, `objective_score`,
   `total_score`, `store.record_match`.

## Failure handling

- **Per-match exception** (model unreachable, bad config, harvest error): caught
  by `_execute`'s existing try/except → `store.record_match(status="failed")`,
  run continues. No change needed.
- **Turn-level errors** (tool failure mid-run): surface in the harvested step's
  `errors` list (from error spans / failed tool `status`).
- **Trace not yet flushed:** the `LocalTracer` persists per callback hook with
  `run_inline=True`; `stream_and_persist` is awaited to completion before
  harvest, so spans are written. **Risk to verify in the plan:** confirm the
  tracer has no async write buffer; if it does, add an explicit settle/flush
  before `transcript_from_trace`. (Plan Task: assert trace rows exist for the
  thread immediately after a driven turn.)
- **Root/step count mismatch** (fewer root traces than steps, e.g. a turn
  errored before any span): `zip` truncates; the harvested transcript has fewer
  steps and objective scoring penalises the missing milestones — acceptable and
  visible. Log a warning naming the thread and counts (no silent truncation).
- **Missing Zenmux config / `ZENMUX_API_KEY`:** `arena_model_to_selection` +
  `resolve_agent_model_selection` raise `ValueError` → match fails cleanly.

## Testing

- **`transcript_from_trace`** (unit, fake store): given seeded span dicts with a
  `read_file` SKILL.md load, two tool spans, and a `task` span, assert
  `skills_routed == ["run-risk"]`, tool_calls exclude meta tools, response_text
  from the assistant message. Cover the ordered-sequence case (two reads →
  correct order) and the no-skill-read edge (empty `skills_routed`).
- **`arena_model_to_selection`**: `"openai/gpt-5.5"` → `{channel,provider,model}`;
  unknown provider/model → `ValueError` via `resolve_agent_model_selection`.
- **`run_match`** (unit, injected `drive`+`harvest`): assert it seeds fixtures,
  creates an `AgentThread(source="arena", arena_run_id=…)`, calls `drive` once
  per step with the right `model_selection`, and returns the harvested transcript.
- **Migration 0033**: upgrade then assert `agent_thread.source` default `'desk'`
  and `arena_run_id` nullable; downgrade drops them.
- **`AgentThreadOut`**: serialises `source`.
- **Frontend toggle**: arena threads hidden by default; toggling reveals them.
- **Regression**: `transcript_from_replay`, scoring, judge, store, and the
  existing `execute_arena_run_task` tests stay green (the injectable seam keeps
  the orchestration contract intact).

## Out of scope

- **D-OOS1** Concurrent matches (checkpointer serialisation; keep sequential).
- **D-OOS2** Auto-cleanup of seeded arena fixture portfolios (persist as demo data).
- **D-OOS3** Hyperframes MP4 render (still placeholder).
- **D-OOS4** Backend-side arena-thread filtering on `/api/chat/threads` (toggle
  is client-side; the endpoint already returns `source`).

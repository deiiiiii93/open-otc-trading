# Agent Desk Chat UX Redesign

**Date:** 2026-05-09
**Status:** Design — pending implementation
**Topic:** Three coupled UX improvements to the Agent Desk chat: sticky-scroll, asymmetric bubbles, and real-time activity streaming.

---

## 1. Goals

The Agent Desk chat (`frontend/src/routes/AgentDesk.tsx`) has three concrete UX gaps:

1. **Aggressive auto-scroll.** Every state change yanks the viewport to the bottom (`AgentDesk.tsx:57-60`), so users who scroll up to read history get dragged back down.
2. **No visual distinction between user and agent messages.** Both render as full-width cards differing only by `border-left` color (`ChatMessage.css:8-15`). Right/left alignment is missing.
3. **Streaming isn't actually live.** The SSE endpoint runs the agent synchronously to completion (`main.py:249-256`), then "streams" a precomputed answer word-by-word via `stream_response()` (`agents.py:270-280`). Tool calls have already finished by the time anything reaches the browser; users have no real-time view of what the agent is doing.

The fix is to wire up real LangGraph streaming, restructure the message list for asymmetric bubble layout, and add sticky-scroll with a "new messages" pill.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | **Streaming activity** = tool timeline (compact mode) with collapsible tool blocks (detailed mode) | Compact when working, depth on demand. Closer to Claude Code's tool log without the full payload cost. |
| 2 | **Toggle placement** = global (header), persisted to `localStorage` under `wl.agent.viewMode` | Calmer UI than per-message chevrons; user picks once. |
| 3 | **Backend wiring** = single-invocation refactor: stream live, persist *after* stream ends | Avoids 2× LLM token cost; eliminates the "streamed text differs from refreshed text" mismatch that double-invocation would create. |
| 4 | **Bubble layout** = wide bubbles (user 65%, agent 90%) | Keeps bubble metaphor; gives agent room for tables, charts, and `ActionProposal` cards. |
| 5 | **Auto-scroll** = sticky-at-bottom + "new messages" pill when scrolled up | Modern chat-app default; respects user intent without abandoning auto-scroll. |

## 3. Architecture

```
┌────────────────────────────┐    SSE (typed JSON events)    ┌──────────────────────────────┐
│ Frontend (React 19)        │ ◀───────────────────────────  │ Backend (FastAPI + LangGraph)│
│                            │                               │                              │
│ • AgentDesk                │  POST /api/chat/threads/{id}/ │ • StreamCollector            │
│   ├ MessageList (sticky)   │       messages/stream         │   buffers tool calls + text  │
│   ├ ChatBubble (bubbles)   │ ─────────────────────────────▶│ • single astream_events run  │
│   ├ ToolTimeline (B+C)     │                               │ • persist AgentMessage       │
│   └ NewMessagesPill        │                               │   AFTER stream completes     │
│                            │                               │ • HITL: stream ends; persist │
│ View toggle (compact/      │                               │   message with               │
│  detailed) → localStorage  │                               │   pending_actions; client    │
│                            │                               │   refreshes                  │
└────────────────────────────┘                               └──────────────────────────────┘
```

**Invariants preserved:**

- `AgentThread` / `AgentMessage` schema is unchanged. Only the *content* of `meta.process_events` becomes richer (structured tool entries instead of plain strings).
- HITL action confirm/dismiss endpoints (`/actions/{id}/{confirm|dismiss}`) and frontend handlers stay as-is. Only the initial agent turn streams; resumes still flow through the existing synchronous `_resume_action` path.
- `ActionProposal.tsx`, `AssetsPane.tsx`, `ChatComposer.tsx` are untouched.

## 4. Frontend components

### 4.1 `ChatBubble` (rename + restructure of `ChatMessage`)

```
ChatBubble (article, role-aware alignment via parent's flex-direction: column)
  └ ChatBubble__shell  (background, padding, max-width: 65% user / 90% agent)
      ├ ChatBubble__head      (character label — assistant only)
      ├ ToolTimeline          (assistant only, only if process_events exist)
      ├ ChatBubble__body      (markdown + cursor)
      └ pending_actions block (existing ActionProposal — unchanged)
```

CSS direction:

- `.wl-chat-bubble--user { align-self: flex-end; max-width: 65%; }`
- `.wl-chat-bubble--assistant { align-self: flex-start; max-width: 90%; }`

The parent `MessageList` is a `flex-direction: column` container, so per-bubble `align-self` does the right/left work. No per-message wrapper.

Existing `ChatMessage.test.tsx` cases keep passing (they assert content rendering, not layout). New test asserts the role-driven CSS class is applied.

### 4.2 `ToolTimeline`

```tsx
<ToolTimeline events={meta.process_events} mode={viewMode} />
```

`meta.process_events` becomes a structured array (today: `string[]`):

```ts
type ToolEvent = {
  id: string;            // run_id from LangGraph; pairs start/end
  name: string;
  status: 'running' | 'done' | 'error';
  args?: Record<string, unknown>;     // present iff mode === 'detailed'
  output?: unknown;                    // present iff mode === 'detailed', truncated
  duration_ms?: number;
  error?: string;
};
```

Renders as `<ol>` of `<li>`. Each `<li>` has a status icon (running spinner / ✓ / ✕), tool name, timing. In `detailed` mode each `<li>` is wrapped in a `<details>` element so users can fold individual tool calls.

The "running" entry uses the existing `wl-cursor-blink` keyframe (`ChatMessage.css:199-202`).

### 4.3 `MessageList` + `useStickyScroll` hook

Extracted from inlined logic in `AgentDesk.tsx`:

```tsx
<MessageList items={[...messages, draftMessage]} streaming={streaming} />
```

Internally:

- a ref + `useStickyScroll(ref)` hook returning `{ isPinned, scrollToBottom }`
- `useStickyScroll` uses `useSyncExternalStore` to subscribe a single `scroll` listener without re-rendering the parent
- `<NewMessagesPill onClick={scrollToBottom}>` rendered when `!isPinned && hasNewSinceLastPin`
- Auto-scroll fires only when `isPinned`
- Pill shows once when *new* content arrives off-screen and not on every streaming token (debounce on message-id boundary, not on streaming-content updates)

Threshold for "near bottom": `scrollHeight - scrollTop - clientHeight < 120` px.

### 4.4 View-mode toggle

Small two-state toggle in `PageHeader` action area: `compact` / `detailed`. Backed by `useViewMode()`:

```ts
function useViewMode(): [Mode, (m: Mode) => void] {
  // Hydrates from localStorage key 'wl.agent.viewMode' (default 'compact')
  // Writes through on setMode
}
```

Hook owns localStorage I/O and SSR-safety (no `window` reference at module scope).

### 4.5 `AgentDesk.live.tsx` SSE parser rewrite

Replace the current parser at `routes/AgentDesk.live.tsx:71-114`:

```ts
function parseSseEvent(eventType: string, dataPayload: string) {
  if (eventType === 'token')      return { kind: 'token',      ...JSON.parse(dataPayload) };
  if (eventType === 'tool_start') return { kind: 'tool_start', ...JSON.parse(dataPayload) };
  if (eventType === 'tool_end')   return { kind: 'tool_end',   ...JSON.parse(dataPayload) };
  if (eventType === 'error')      return { kind: 'error',      ...JSON.parse(dataPayload) };
  if (eventType === 'done')       return { kind: 'done',       ...JSON.parse(dataPayload) };
  if (eventType === 'heartbeat')  return { kind: 'heartbeat' };
  return null; // unknown event types are ignored
}
```

Streaming state in the route component:

```ts
const [draft, setDraft] = useState<{ content: string; events: ToolEvent[] } | null>(null);
```

Reducers per event kind:

- `token` → append `text` to `draft.content`
- `tool_start` → push `ToolEvent` with `status: 'running'`
- `tool_end` → patch the matching event by `id` (set `status`, `duration_ms`, `output`, `error`)
- `error` → surface via existing `setError`
- `done` → clear `draft` and call `refresh()`
- `heartbeat` → ignored

## 5. SSE wire protocol

**Endpoint:** `POST /api/chat/threads/{id}/messages/stream` (URL unchanged).
**Content-Type:** `text/event-stream`.

Every event uses an explicit `event:` line and a JSON payload on `data:`.

### 5.1 Event types

```
event: tool_start
data: {"id":"run-abc-1","name":"price_product","args":{"underlying":"000300.SH","notional":1000000}}

event: tool_end
data: {"id":"run-abc-1","output":{"price":102345.67,"greeks":{"delta":0.42}},"duration_ms":120}

event: tool_end
data: {"id":"run-abc-2","duration_ms":85,"error":"market data missing for SPX"}

event: token
data: {"text":"Here's the Greeks summary"}

event: heartbeat
data: {}

event: error
data: {"message":"LLM provider returned 503","retryable":true}

event: done
data: {"message_id":1234}
```

### 5.2 Schemas

**`tool_start`**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | LangGraph `run_id`; pairs with `tool_end.id` |
| `name` | string | yes | Tool name (e.g., `price_product`) |
| `args` | object | no | Tool input dict; truncated to 1000 chars of stringified JSON. If cut, replaced with `{"_truncated": true, "preview": "...first 1000 chars..."}`. Omitted when empty. |

**`tool_end`**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Matches `tool_start.id` |
| `duration_ms` | integer | yes | Computed from `on_tool_start` timestamp |
| `output` | any | no | Tool return value, same truncation rule. Omitted when `error` is present. |
| `error` | string | no | Tool error message; when present, `output` omitted |

**`token`**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `text` | string | yes | Non-empty chunk; multiple `token` events concatenate to form final assistant text |

**`error`**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `message` | string | yes | Human-readable error |
| `retryable` | boolean | yes | Drives "Retry" button visibility on the frontend |

After an `error` event, the server still emits `done`.

**`heartbeat`**: empty `{}` payload, emitted every 15s in the absence of other events. Frontend ignores.

**`done`**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `message_id` | integer | nullable | Persisted `AgentMessage.id`; `null` if persistence failed |

Always the final event.

### 5.3 HITL interrupts

When the orchestrator interrupts for a `pending_action`, the stream ends with `done`. The persisted message (already written by `StreamCollector`) carries `meta.pending_actions` — frontend's `refresh()` after `done` reveals the action card. **No new SSE event type for interrupts.**

### 5.4 Truncation policy

We truncate `args` and `output` at 1000 chars of stringified JSON because:

1. SSE is line-buffered and large `data:` lines hurt latency on slow connections.
2. Full tool payloads already live in LangGraph's checkpointer (`agent_checkpoints.sqlite`); a `/threads/{id}/runs/{run_id}/tool/{call_id}` endpoint can expose un-truncated data later if needed (out of scope).

## 6. Backend: `StreamCollector` + persistence

### 6.1 New endpoint shape (`backend/app/main.py`)

```python
@app.post("/api/chat/threads/{thread_id}/messages/stream")
async def stream_chat_message(
    thread_id: int,
    payload: AgentMessageCreate,
    session: Session = Depends(get_db),
):
    thread = session.get(AgentThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    user_msg = AgentMessage(
        thread_id=thread.id, role="user", content=payload.content,
        meta={"page_context": payload.page_context.model_dump(mode="json") if payload.page_context else None},
    )
    session.add(user_msg)
    session.commit()

    return StreamingResponse(
        agent_service.stream_and_persist(
            thread_id=thread.id,
            content=payload.content,
            requested_character=payload.character,
            page_context=payload.page_context,
        ),
        media_type="text/event-stream",
    )
```

### 6.2 `StreamCollector` (`backend/app/services/deep_agent/stream_collector.py`, new)

```python
@dataclass
class StreamCollector:
    text_chunks: list[str] = field(default_factory=list)
    tool_events: dict[str, dict] = field(default_factory=dict)  # keyed by run_id
    interrupts: list = field(default_factory=list)
    personas_invoked: list[str] = field(default_factory=list)
    error: str | None = None

    def on_tool_start(self, run_id: str, name: str, args: Any, started_at: float) -> None: ...
    def on_tool_end(self, run_id: str, output: Any, ended_at: float, error: str | None = None) -> None: ...
    def on_token(self, text: str) -> None: ...
    def note_persona(self, name: str) -> None: ...

    @property
    def final_text(self) -> str: ...
    @property
    def process_events(self) -> list[dict]: ...
```

`on_tool_start` records `started_at` internally; `on_tool_end` accepts an optional `error` string and computes `duration_ms` from the matching start timestamp before dropping it from the serialized form. Truncation happens via a `_truncate(value, limit=1000)` helper:

```python
def _truncate(value: Any, limit: int = 1000) -> Any:
    """Stringify and truncate; preserves objects under the limit, replaces
    over-limit objects with a {_truncated, preview, size} envelope."""
    s = json.dumps(value, default=str, ensure_ascii=False)
    if len(s) <= limit:
        return value
    return {"_truncated": True, "preview": s[:limit], "size": len(s)}
```

The same helper applies to live SSE *and* persisted `meta.process_events`, so a user re-opening a thread later sees the same shape they saw streaming.

### 6.3 `stream_and_persist` (new method on `AgentService`)

```python
async def stream_and_persist(self, *, thread_id, content, requested_character, page_context):
    if self.deep_agent is None:
        yield _sse("error", {"message": _DISABLED_RESPONSE, "retryable": False})
        yield _sse("done", {"message_id": None})
        return

    context = self._context_snapshot(thread_id, page_context)
    assets  = self._context_assets(page_context)
    prompt  = _orchestrator_user_prompt(content, requested_character, context)
    config  = {"configurable": {"thread_id": str(thread_id)}}
    collector = StreamCollector()

    try:
        try:
            async for sse_line in self._drive_stream(prompt, config, collector):
                yield sse_line
        except Exception as exc:
            logger.exception("Live stream failed for thread %s", thread_id)
            collector.error = str(exc)[:500]
            yield _sse("error", {"message": collector.error, "retryable": False})
    finally:
        # Read post-stream state for interrupts AND personas, then persist —
        # even on disconnect, so the assistant message exists in the thread.
        try:
            state = self.deep_agent.get_state(config)
            if state and state.tasks:
                for task in state.tasks:
                    collector.interrupts.extend(getattr(task, "interrupts", []) or [])
            self._extract_personas_from_state(state, collector)
        except Exception:
            logger.exception("get_state failed for thread %s", thread_id)

        try:
            message_id = await asyncio.to_thread(
                self._persist_from_collector, thread_id, collector, assets, page_context
            )
        except Exception:
            logger.exception("Persist failed for thread %s", thread_id)
            message_id = None
        yield _sse("done", {"message_id": message_id})
```

`_extract_personas_from_state` walks `state.values["messages"]` looking for `task(subagent_type=...)` tool calls, mirroring today's `_personas_invoked()` helper at `agents.py:100-111`. Personas are derived post-stream rather than during streaming because LangGraph doesn't surface "persona invoked" as a dedicated event kind.

### 6.4 `_drive_stream` (heartbeats)

`_drive_stream` races `astream_events` against a 15-second timeout to emit `heartbeat` events:

```python
async def _drive_stream(self, prompt, config, collector):
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    async def producer():
        try:
            async for ev in self.deep_agent.astream_events(
                {"messages": [HumanMessage(content=prompt)]}, config=config, version="v2"
            ):
                await queue.put(ev)
        finally:
            await queue.put(DONE)

    task = asyncio.create_task(producer())
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield _sse("heartbeat", {})
                continue
            if ev is DONE:
                return
            sse_line = self._handle_event(ev, collector)
            if sse_line:
                yield sse_line
    finally:
        task.cancel()
```

### 6.5 `_handle_event`

```python
def _handle_event(self, ev, collector) -> str | None:
    kind = ev.get("event")
    run_id = ev.get("run_id")
    name = ev.get("name", "")
    data = ev.get("data") or {}

    if kind == "on_tool_start":
        args = data.get("input") or {}
        collector.on_tool_start(run_id, name, args, time.monotonic())
        return _sse("tool_start", {"id": run_id, "name": name, "args": _truncate(args)})

    if kind == "on_tool_end":
        output = data.get("output")
        # LangGraph surfaces tool errors via on_tool_end where output is a
        # ToolMessage with status="error", or as a raw exception in data["error"].
        error_text = _extract_tool_error(data, output)
        collector.on_tool_end(run_id, None if error_text else output, time.monotonic(), error=error_text)
        ev_data = collector.tool_events.get(run_id, {})
        return _sse("tool_end", {
            "id": run_id,
            "duration_ms": ev_data.get("duration_ms"),
            "output": None if error_text else (_truncate(output) if output is not None else None),
            "error": error_text,
        })

    if kind == "on_chat_model_stream":
        chunk = data.get("chunk")
        text = getattr(chunk, "content", None) if chunk is not None else None
        if isinstance(text, str) and text:
            collector.on_token(text)
            return _sse("token", {"text": text})

    return None
```

### 6.6 `_persist_from_collector`

Opens a fresh `SessionLocal()`, mirrors the meta shape produced today by `_persist_agent_result`. Synchronous — invoked from the async generator via `asyncio.to_thread`.

```python
def _persist_from_collector(self, thread_id, collector, assets, page_context) -> int | None:
    with SessionLocal() as session:
        thread = session.get(AgentThread, thread_id)
        if thread is None:
            return None
        last_persona = collector.personas_invoked[-1] if collector.personas_invoked else None

        if collector.interrupts:
            pending = pending_actions_from_interrupts(collector.interrupts, persona=last_persona)
            assistant_msg = AgentMessage(
                thread_id=thread_id, role="assistant", character=last_persona,
                content=collector.final_text or "Awaiting confirmation for the next step.",
                meta={
                    "agent_graph": "deepagents",
                    "agent_phase": "awaiting_confirmation",
                    "pending_actions": [a.model_dump(mode="json") for a in pending],
                    "interrupt_ids": [intr.id for intr in collector.interrupts],
                    "personas_invoked": collector.personas_invoked,
                    "process_events": collector.process_events,
                    "assets": [asset.model_dump(mode="json") for asset in assets],
                    "context_used": page_context.model_dump(mode="json") if page_context else None,
                    "agent_enabled": True,
                },
            )
        else:
            assistant_msg = AgentMessage(
                thread_id=thread_id, role="assistant", character=last_persona,
                content=collector.final_text or "(no response)",
                meta={
                    "agent_graph": "deepagents",
                    "agent_phase": "error" if collector.error else "completed",
                    "pending_actions": [],
                    "personas_invoked": collector.personas_invoked,
                    "process_events": collector.process_events,
                    "assets": [asset.model_dump(mode="json") for asset in assets],
                    "context_used": page_context.model_dump(mode="json") if page_context else None,
                    "error": collector.error,
                    "agent_enabled": True,
                },
            )
        session.add(assistant_msg)
        thread.character = last_persona or thread.character
        session.commit()
        record_audit(session, event_type="chat.message", actor="desk_user",
                     subject_type="thread", subject_id=thread_id,
                     payload={"personas_invoked": collector.personas_invoked, "streamed": True})
        session.commit()
        return assistant_msg.id
```

### 6.7 Death of the old methods

After this refactor:

- `agent_service.respond()` — only caller was the streaming endpoint. **Delete.**
- `agent_service.stream_response()` (fake word-by-word) — **Delete.**
- `agent_service.stream_response_live()` — superseded by `stream_and_persist`. **Delete.**

We don't keep these as fallbacks. The new `stream_and_persist` is the only path.

### 6.8 Session scope

The streaming generator runs in a single asyncio task. We *don't* hold the request-scoped session for the whole stream — that would keep DB connections open for tens of seconds during long agent turns.

1. Endpoint's session: only used to write the user message, then released by FastAPI's dependency cleanup.
2. Generator opens a fresh `SessionLocal()` at the very end, in `_persist_from_collector`, run via `asyncio.to_thread`.

## 7. HITL behavior

1. Tokens + tool events stream as usual.
2. Orchestrator hits an interrupt; `astream_events` finishes its iteration.
3. Generator calls `self.deep_agent.get_state(config)` → finds non-empty `state.tasks[*].interrupts` → populates `collector.interrupts`.
4. `_persist_from_collector` writes an `AgentMessage` with `agent_phase="awaiting_confirmation"` and `pending_actions` (same shape as today).
5. Generator emits `event: done\ndata: {"message_id":...}`.
6. Frontend's stream reducer processes `done` → calls `refresh()` → fetches the persisted message → `ChatBubble` renders the `ActionProposal` card from `meta.pending_actions`.

The action confirm/dismiss path stays synchronous, going through the existing `_resume_action` → `agent_service.deep_agent.invoke()` → `_persist_agent_result` flow. Resume turns do *not* stream in v1; that can be added later by routing resumes through `stream_and_persist`.

## 8. Error handling

| Bucket | Behavior |
|--------|----------|
| Agent disabled (no LLM configured) | Generator emits `error` + `done` immediately. Frontend shows existing "Agent unavailable" notice. No assistant message is persisted. |
| LLM provider failure mid-stream | `try` around `_drive_stream` catches; `collector.error` set; `event: error` emitted; assistant message *still* persisted with `agent_phase="error"`, partial `final_text`, and partial `process_events`. |
| Tool-level failure | LangGraph emits `on_tool_end` with `error` in payload. `_handle_event` records it in collector and emits `tool_end` SSE with `error` set. Agent decides whether to recover. |
| Stream disconnect (client closed tab) | Persistence runs in `finally` block; best-effort. The trailing `done` event may not reach the client; that's fine. |

The streaming generator's outer shape (full code in §6.3) wraps the `_drive_stream` loop in `try/except` to catch LLM/provider errors, then wraps the whole thing in `try/finally` so persistence runs even if the client disconnects mid-stream. The trailing `done` event may not reach a disconnected client; that's fine — the assistant message is still in the database and shows up on next thread refresh.

`_extract_tool_error(data, output)` is a small helper (§6.5) that returns a string when LangGraph signals a tool error (either `data.get("error")` or a `ToolMessage` with `status == "error"`) and `None` otherwise.

**Frontend error UX:** existing `error` state in `AgentDeskLive` is set on the SSE `error` event. `retryable: true` adds a "Retry" button; `retryable: false` doesn't.

## 9. Testing

### 9.1 Frontend (`vitest` + `@testing-library/react` + `jsdom`, all already in deps)

| File | Asserts |
|------|---------|
| `ChatBubble.test.tsx` | user role → flex-end class; assistant → flex-start class; markdown still renders; cursor only when `isStreaming` |
| `ToolTimeline.test.tsx` | `compact` mode hides args; `detailed` mode renders `<details>` with truncated args; running state shows spinner; events keyed by `id` keep concurrent calls separate |
| `useStickyScroll.test.ts` | scrolled to bottom → `isPinned=true`; scrolled up → `isPinned=false`; stays pinned-off when content grows; `scrollToBottom()` re-pins |
| `useViewMode.test.ts` | reads/writes localStorage under `wl.agent.viewMode`; defaults to `compact` |
| `AgentDesk.live.test.tsx` (extend existing) | mock `fetch` to yield SSE chunks across `tool_start` / `token` / `tool_end` / `done`; assert draft message renders progressively, then `refresh()` is called once on `done`; assert `heartbeat` events are ignored |

### 9.2 Backend (pytest, scripted-model fixtures from commit `9b3aa50`)

| File | Asserts |
|------|---------|
| `test_stream_collector.py` | tool start/end pair into one event with `duration_ms`; truncation at 1000 chars; concurrent tool calls keyed by `run_id` stay distinct |
| `test_stream_and_persist.py` (new) | with a scripted fake `deep_agent` that emits a known sequence of LangGraph events: (1) emitted SSE lines match the wire format, (2) heartbeat fires after 15s of silence (patched via `asyncio.wait_for` mock), (3) persisted `AgentMessage.meta.process_events` mirrors what we streamed |
| `test_stream_and_persist_hitl.py` | scripted agent that interrupts → persisted message has `agent_phase="awaiting_confirmation"` + `pending_actions` populated; existing resume flow through `_resume_action` still works (smoke test) |
| `test_stream_disconnect.py` | client disconnects mid-stream (cancel response task) → `AgentMessage` still persisted with partial content and appropriate `agent_phase` |

We patch `asyncio.wait_for`'s timeout for heartbeat tests rather than waiting 15 real seconds — keeps CI fast.

## 10. Scope summary

### New files (6)

- `frontend/src/components/ChatBubble.tsx` (rename from `ChatMessage.tsx`)
- `frontend/src/components/ChatBubble.css` (rename from `ChatMessage.css`)
- `frontend/src/components/ToolTimeline.tsx` (+ `.css`)
- `frontend/src/components/MessageList.tsx` (+ `.css`)
- `frontend/src/components/NewMessagesPill.tsx` (+ `.css`)
- `frontend/src/hooks/useStickyScroll.ts`
- `frontend/src/hooks/useViewMode.ts`
- `backend/app/services/deep_agent/stream_collector.py`

### Modified

- `frontend/src/routes/AgentDesk.tsx` — use `MessageList`, render view-mode toggle in header
- `frontend/src/routes/AgentDesk.live.tsx` — new SSE parser; structured `draft` state
- `frontend/src/types.ts` — add `ToolEvent` type; `ChatMessage.meta.process_events` becomes `ToolEvent[] | string[]` during the migration window
- `backend/app/services/agents.py` — delete `respond` / `stream_response` / `stream_response_live`; add `stream_and_persist` + `_drive_stream` + `_handle_event` + `_persist_from_collector`
- `backend/app/main.py` — async endpoint; persist user message synchronously, return streaming response

### Deleted methods

- `AgentService.respond()`
- `AgentService.stream_response()`
- `AgentService.stream_response_live()`

### Wire format

The old `event: status` + space-joined `data:` format dies with the old methods. Frontend `refresh()` after stream ensures any in-flight legacy clients hydrate from the persisted message regardless.

## 11. Out of scope (future work)

- **Streaming on resume turns.** When the user clicks Confirm/Dismiss on an `ActionProposal`, the resume turn is currently synchronous. Could be routed through `stream_and_persist` later.
- **Un-truncated tool output endpoint.** A `/threads/{id}/runs/{run_id}/tool/{call_id}` endpoint for inspecting the full output. LangGraph checkpointer already retains it; just needs a thin reader.
- **Real-time progress within tools.** A `tool_progress` event for tools that emit incremental output. Not needed for current tools.
- **Per-message detail override.** Decided against in question 2; could be added by introducing a chevron on each assistant bubble that overrides the global mode.

## 12. References

- Existing fake stream: `backend/app/services/agents.py:270-280`
- Existing real stream (unwired): `backend/app/services/agents.py:282-327`
- HITL interrupt handling: `backend/app/services/deep_agent/hitl.py`, `backend/app/main.py:258-313`
- Auto-scroll source: `frontend/src/routes/AgentDesk.tsx:57-60`
- Current bubble CSS: `frontend/src/components/ChatMessage.css:1-15`
- LangGraph streaming docs: `astream_events` v2, event kinds `on_tool_start` / `on_tool_end` / `on_chat_model_stream`

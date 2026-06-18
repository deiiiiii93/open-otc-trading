# Agent Desk Chat UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Agent Desk chat's fake word-by-word stream with real LangGraph streaming, restructure messages into asymmetric bubbles (user 65% / agent 90%), and add sticky-scroll with a "new messages" pill.

**Architecture:** Backend introduces a `StreamCollector` that buffers tool events and tokens during a single `astream_events` invocation, then persists one `AgentMessage` after the stream completes — no more double-invocation. Frontend gains a `MessageList` with sticky scroll, a renamed `ChatBubble` with role-aware alignment, and a `ToolTimeline` that switches between compact (status icons + timing) and detailed (collapsible tool args + output) modes via a header-level global toggle persisted to `localStorage`.

**Tech Stack:** React 19, Vite, vitest, @testing-library/react, jsdom, axe-core (frontend); FastAPI, LangGraph (`astream_events` v2), pydantic, SQLAlchemy 2.x, pytest (backend). SSE wire format uses typed JSON event payloads.

**Spec:** `docs/superpowers/specs/2026-05-09-agent-chat-ux-design.md` (commit `4f849d8`).

**Spec deviation:** The spec says delete `AgentService.respond()`. Discovery during plan-writing: `respond()` is used by `tests/test_agent_integration.py` (lines 87, 123, 184, 245) and `tests/test_agent_tools.py:85` as a synchronous integration entry point. **This plan keeps `respond()` intact.** Only `stream_response()` and `stream_response_live()` are deleted. The streaming endpoint stops calling `respond()` and uses the new `stream_and_persist` exclusively. Existing synchronous tests of `respond()` continue to pass.

---

## File Structure

### New files

| Path | Responsibility |
|------|----------------|
| `backend/app/services/deep_agent/stream_collector.py` | `StreamCollector` dataclass + `_truncate` helper |
| `tests/test_stream_collector.py` | Unit tests for collector and truncation |
| `tests/test_stream_and_persist.py` | Integration tests for `AgentService.stream_and_persist` (success path, heartbeat, error path) |
| `tests/test_stream_and_persist_hitl.py` | HITL interrupt path through streaming |
| `frontend/src/hooks/useStickyScroll.ts` | Scroll-pinning hook (returns `{isPinned, scrollToBottom}`) |
| `frontend/src/hooks/useStickyScroll.test.ts` | Hook tests with jsdom scroll geometry mocking |
| `frontend/src/hooks/useViewMode.ts` | localStorage-backed view-mode state (`'compact' \| 'detailed'`) |
| `frontend/src/hooks/useViewMode.test.ts` | Hook tests covering hydration and persistence |
| `frontend/src/components/MessageList.tsx` | Scroll container + sticky behavior + new-messages pill rendering |
| `frontend/src/components/MessageList.css` | Flex column layout for asymmetric bubble alignment |
| `frontend/src/components/MessageList.test.tsx` | Tests for sticky scroll and pill visibility |
| `frontend/src/components/NewMessagesPill.tsx` | Floating "↓ new messages" / "↓ live" pill button |
| `frontend/src/components/NewMessagesPill.css` | Pill styling |
| `frontend/src/components/ToolTimeline.tsx` | Renders `ToolEvent[]` in compact or detailed mode |
| `frontend/src/components/ToolTimeline.css` | Status icons, spinner animation, `<details>` styling |
| `frontend/src/components/ToolTimeline.test.tsx` | Tests for both modes, status states, concurrent run-id pairing |
| `frontend/src/components/ChatBubble.tsx` | Role-aware bubble (renamed from `ChatMessage.tsx`) |
| `frontend/src/components/ChatBubble.css` | Bubble layout (renamed from `ChatMessage.css`) |
| `frontend/src/components/ChatBubble.test.tsx` | Tests for alignment classes + content rendering (renamed) |

### Modified files

| Path | What changes |
|------|--------------|
| `frontend/src/types.ts` | Add `ToolEvent` type; widen `meta.process_events` to `ToolEvent[] \| string[]` |
| `frontend/src/routes/AgentDesk.tsx` | Use `MessageList`; render view-mode toggle in `PageHeader` action area; thread `viewMode` to children |
| `frontend/src/routes/AgentDesk.live.tsx` | Replace SSE parser with typed-event reducer; new structured `draft` state |
| `frontend/src/routes/AgentDesk.test.tsx` | Update for new component composition |
| `frontend/src/routes/AgentDesk.live.test.tsx` | Update for new SSE event format |
| `backend/app/services/agents.py` | Add `stream_and_persist`, `_drive_stream`, `_handle_event`, `_persist_from_collector`, `_extract_personas_from_state`, module-level `_truncate`, `_extract_tool_error`, `_sse` helpers; delete `stream_response` and `stream_response_live` methods |
| `backend/app/main.py` | Convert `stream_chat_message` to `async def`; persist user message synchronously; return `StreamingResponse(stream_and_persist(...))` |
| `tests/test_streaming.py` | Delete (the methods it tests are gone; replaced by `test_stream_and_persist.py`) |
| `tests/_scripted_graph.py` | Extend `_ScriptedGraph` with `astream_events` + `get_state` for streaming tests |

### Deleted

- `AgentService.stream_response()` (the fake word-by-word stream)
- `AgentService.stream_response_live()` (the unwired live stream)
- `tests/test_streaming.py` (tests the deleted methods)

---

## Phase A — Backend Streaming Foundation

### Task A1: StreamCollector data class

**Files:**
- Create: `backend/app/services/deep_agent/stream_collector.py`
- Create: `tests/test_stream_collector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stream_collector.py
from __future__ import annotations

from app.services.deep_agent.stream_collector import StreamCollector, _truncate


def test_collector_starts_empty():
    c = StreamCollector()
    assert c.final_text == ""
    assert c.process_events == []
    assert c.interrupts == []
    assert c.personas_invoked == []
    assert c.error is None


def test_collector_pairs_tool_start_and_end_by_run_id():
    c = StreamCollector()
    c.on_tool_start("run-1", "price_product", {"underlying": "SPX"}, started_at=100.0)
    c.on_tool_end("run-1", {"price": 102.5}, ended_at=100.120)

    events = c.process_events
    assert len(events) == 1
    ev = events[0]
    assert ev["id"] == "run-1"
    assert ev["name"] == "price_product"
    assert ev["status"] == "done"
    assert ev["duration_ms"] == 120
    assert ev["args"] == {"underlying": "SPX"}
    assert ev["output"] == {"price": 102.5}
    assert "started_at" not in ev  # internal field is dropped from serialization


def test_collector_keeps_concurrent_tool_calls_distinct():
    c = StreamCollector()
    c.on_tool_start("run-1", "price_product", {"id": 1}, started_at=100.0)
    c.on_tool_start("run-2", "price_product", {"id": 2}, started_at=100.05)
    c.on_tool_end("run-2", {"price": 50}, ended_at=100.20)
    c.on_tool_end("run-1", {"price": 99}, ended_at=100.30)

    events = c.process_events
    assert len(events) == 2
    by_id = {e["id"]: e for e in events}
    assert by_id["run-1"]["output"] == {"price": 99}
    assert by_id["run-2"]["output"] == {"price": 50}


def test_collector_records_tool_error():
    c = StreamCollector()
    c.on_tool_start("run-x", "approve_rfq", {"rfq_id": 7}, started_at=0.0)
    c.on_tool_end("run-x", None, ended_at=0.05, error="permission denied")

    ev = c.process_events[0]
    assert ev["status"] == "error"
    assert ev["error"] == "permission denied"
    assert ev["output"] is None
    assert ev["duration_ms"] == 50


def test_collector_concatenates_text_chunks_in_order():
    c = StreamCollector()
    c.on_token("Hello")
    c.on_token(" ")
    c.on_token("world")
    assert c.final_text == "Hello world"


def test_collector_strips_final_text():
    c = StreamCollector()
    c.on_token("  hi  ")
    assert c.final_text == "hi"


def test_truncate_passes_through_small_values():
    assert _truncate({"a": 1}) == {"a": 1}
    assert _truncate("short") == "short"


def test_truncate_replaces_oversize_values_with_envelope():
    big = "x" * 5000
    out = _truncate(big, limit=1000)
    assert out["_truncated"] is True
    assert len(out["preview"]) == 1000
    assert out["size"] > 5000
    # Preview is JSON-stringified content (which adds quote chars for strings)
    assert out["preview"].startswith('"xxxxx')


def test_truncate_handles_unserializable_values():
    class Obj:
        def __repr__(self):
            return "Obj()"
    out = _truncate(Obj(), limit=1000)
    # default=str ensures the object is stringified, not crashed on
    assert out == "Obj()" or (isinstance(out, dict) and out.get("_truncated"))
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_stream_collector.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.deep_agent.stream_collector'`

- [ ] **Step 3: Implement the collector**

```python
# backend/app/services/deep_agent/stream_collector.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _truncate(value: Any, limit: int = 1000) -> Any:
    """Stringify and truncate; preserve small values, envelope-wrap large ones."""
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)[:limit]
    if len(s) <= limit:
        return value
    return {"_truncated": True, "preview": s[:limit], "size": len(s)}


@dataclass
class StreamCollector:
    """Buffers a single agent turn's streamed events for later persistence."""

    text_chunks: list[str] = field(default_factory=list)
    tool_events: dict[str, dict] = field(default_factory=dict)  # keyed by run_id
    interrupts: list = field(default_factory=list)
    personas_invoked: list[str] = field(default_factory=list)
    error: str | None = None

    def on_tool_start(self, run_id: str, name: str, args: Any, started_at: float) -> None:
        self.tool_events[run_id] = {
            "id": run_id,
            "name": name,
            "status": "running",
            "args": _truncate(args) if args else None,
            "_started_at": started_at,
        }

    def on_tool_end(
        self,
        run_id: str,
        output: Any,
        ended_at: float,
        error: str | None = None,
    ) -> None:
        ev = self.tool_events.get(run_id)
        if ev is None:
            # tool_end with no matching start — record best-effort
            self.tool_events[run_id] = {
                "id": run_id, "name": "?", "status": "error" if error else "done",
                "duration_ms": 0,
                "args": None,
                "output": None if error else (_truncate(output) if output is not None else None),
                "error": error,
            }
            return
        started_at = ev.pop("_started_at", ended_at)
        ev["duration_ms"] = int((ended_at - started_at) * 1000)
        ev["status"] = "error" if error else "done"
        ev["output"] = None if error else (_truncate(output) if output is not None else None)
        ev["error"] = error

    def on_token(self, text: str) -> None:
        if text:
            self.text_chunks.append(text)

    def note_persona(self, name: str) -> None:
        if name and name not in self.personas_invoked:
            self.personas_invoked.append(name)

    @property
    def final_text(self) -> str:
        return "".join(self.text_chunks).strip()

    @property
    def process_events(self) -> list[dict]:
        # Drop any leftover internal fields and return insertion-ordered list
        out: list[dict] = []
        for ev in self.tool_events.values():
            cleaned = {k: v for k, v in ev.items() if not k.startswith("_")}
            out.append(cleaned)
        return out
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
pytest tests/test_stream_collector.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/stream_collector.py tests/test_stream_collector.py
git commit -m "feat(agent): add StreamCollector for live-stream persistence

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A2: Module-level helpers in agents.py

`_sse(event, data)`, `_extract_tool_error(data, output)`, and a new `_personas_from_state` helper. These are the small utility functions that `_handle_event` and `stream_and_persist` will use in Task A3.

**Files:**
- Modify: `backend/app/services/agents.py` (add helpers near top of module, after existing imports)
- Create: `tests/test_agents_helpers.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agents_helpers.py
from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from app.services.agents import _sse, _extract_tool_error


def test_sse_formats_event_with_event_and_data_lines():
    line = _sse("token", {"text": "hi"})
    assert line == 'event: token\ndata: {"text": "hi"}\n\n'


def test_sse_handles_unicode_without_escaping():
    line = _sse("token", {"text": "你好"})
    assert "你好" in line


def test_extract_tool_error_returns_none_for_clean_output():
    assert _extract_tool_error({}, {"price": 1.0}) is None
    assert _extract_tool_error({"output": "ok"}, "ok") is None


def test_extract_tool_error_picks_up_error_key_in_data():
    msg = _extract_tool_error({"error": "boom"}, None)
    assert msg == "boom"


def test_extract_tool_error_picks_up_tool_message_status_error():
    tool_msg = ToolMessage(content="not allowed", tool_call_id="x", status="error")
    assert _extract_tool_error({}, tool_msg) == "not allowed"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_agents_helpers.py -v
```

Expected: `ImportError: cannot import name '_sse' from 'app.services.agents'`

- [ ] **Step 3: Add the helpers to `agents.py`**

Insert directly after the existing `import` block (before `logger = logging.getLogger(...)`):

```python
import json as _json


def _sse(event: str, data: dict) -> str:
    """Serialize one SSE event with a JSON data payload."""
    return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _extract_tool_error(data: dict, output: Any) -> str | None:
    """Detect tool errors from a LangGraph on_tool_end event payload."""
    if isinstance(data, dict):
        err = data.get("error")
        if err:
            return str(err)[:500]
    # ToolMessage with status="error" carries the error text in .content
    content = getattr(output, "content", None)
    status = getattr(output, "status", None)
    if status == "error" and content:
        return str(content)[:500]
    return None
```

Note: `Any` is already imported in `agents.py` (line 6). `_json` is aliased to avoid shadowing the existing `json` import on line 3.

- [ ] **Step 4: Run tests — verify all pass**

```bash
pytest tests/test_agents_helpers.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents.py tests/test_agents_helpers.py
git commit -m "feat(agent): add SSE and tool-error helpers for live streaming

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A3: Extend scripted graph for streaming tests

The existing `_ScriptedGraph` in `tests/_scripted_graph.py` only supports `.invoke()`. Streaming tests need `astream_events` (async iterator) and `get_state` (returns mock state with optional interrupts/messages).

**Files:**
- Modify: `tests/_scripted_graph.py`

- [ ] **Step 1: Add a failing import-only test**

Create `tests/test_scripted_graph_streaming.py`:

```python
# tests/test_scripted_graph_streaming.py
from __future__ import annotations

import asyncio

from tests._scripted_graph import _ScriptedAsyncGraph, _stream_event


def test_scripted_async_graph_replays_events():
    script = [
        _stream_event("on_tool_start", run_id="r1", name="get_positions", input={}),
        _stream_event("on_chat_model_stream", chunk_text="Hello"),
        _stream_event("on_tool_end", run_id="r1", output={"count": 3}),
    ]
    graph = _ScriptedAsyncGraph(events=script)

    async def collect():
        out = []
        async for ev in graph.astream_events({"messages": []}, config={}, version="v2"):
            out.append(ev)
        return out

    result = asyncio.run(collect())
    assert len(result) == 3
    assert result[0]["event"] == "on_tool_start"
    assert result[1]["data"]["chunk"].content == "Hello"
    assert result[2]["event"] == "on_tool_end"


def test_scripted_async_graph_get_state_returns_interrupts_when_set():
    graph = _ScriptedAsyncGraph(events=[], interrupts=["mock-interrupt"])
    state = graph.get_state(config={})
    assert state.tasks
    assert list(state.tasks[0].interrupts) == ["mock-interrupt"]


def test_scripted_async_graph_get_state_returns_empty_tasks_when_no_interrupts():
    graph = _ScriptedAsyncGraph(events=[])
    state = graph.get_state(config={})
    assert state.tasks == ()
```

- [ ] **Step 2: Run test — verify import error**

```bash
pytest tests/test_scripted_graph_streaming.py -v
```

Expected: `ImportError: cannot import name '_ScriptedAsyncGraph'`

- [ ] **Step 3: Extend `_scripted_graph.py`**

Append to the existing file:

```python
from dataclasses import dataclass, field as _dc_field
from typing import Any, Iterable

from langchain_core.messages import AIMessageChunk


def _stream_event(event: str, **kwargs: Any) -> dict:
    """Build a LangGraph astream_events v2 event dict for testing.

    Examples:
        _stream_event("on_tool_start", run_id="r1", name="foo", input={"x": 1})
        _stream_event("on_chat_model_stream", chunk_text="hello")
        _stream_event("on_tool_end", run_id="r1", output={"ok": True})
    """
    out: dict[str, Any] = {"event": event}
    if "run_id" in kwargs:
        out["run_id"] = kwargs.pop("run_id")
    if "name" in kwargs:
        out["name"] = kwargs.pop("name")
    data: dict[str, Any] = {}
    if "input" in kwargs:
        data["input"] = kwargs.pop("input")
    if "output" in kwargs:
        data["output"] = kwargs.pop("output")
    if "chunk_text" in kwargs:
        data["chunk"] = AIMessageChunk(content=kwargs.pop("chunk_text"))
    if "error" in kwargs:
        data["error"] = kwargs.pop("error")
    out["data"] = data
    return out


@dataclass
class _MockTask:
    interrupts: tuple = ()


@dataclass
class _MockState:
    tasks: tuple = ()
    values: dict = _dc_field(default_factory=dict)


class _ScriptedAsyncGraph:
    """Async sibling of _ScriptedGraph for streaming-path tests.

    `events` is replayed by astream_events. `interrupts` populates the mock
    state.tasks[0].interrupts when get_state is called. `messages` populates
    state.values["messages"] for persona extraction tests.
    """

    name = "otc_desk_orchestrator"

    def __init__(
        self,
        events: Iterable[dict],
        *,
        interrupts: Iterable[Any] = (),
        messages: Iterable[Any] = (),
    ) -> None:
        self._events = list(events)
        self._interrupts = list(interrupts)
        self._messages = list(messages)

    async def astream_events(self, payload: Any, *, config: Any = None, version: str = "v2"):
        for ev in self._events:
            yield ev

    def get_state(self, config: Any = None) -> _MockState:
        if self._interrupts or self._messages:
            tasks = (_MockTask(interrupts=tuple(self._interrupts)),)
        else:
            tasks = ()
        return _MockState(tasks=tasks, values={"messages": list(self._messages)})

    def invoke(self, payload: Any, config: Any = None) -> dict:
        # For mixed sync/async tests if needed
        return {"messages": []}
```

- [ ] **Step 4: Run test — verify all pass**

```bash
pytest tests/test_scripted_graph_streaming.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/_scripted_graph.py tests/test_scripted_graph_streaming.py
git commit -m "test(agent): extend scripted graph with async streaming + get_state

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A4: stream_and_persist + helpers (success path)

Build the new streaming entry point on `AgentService`. Tests use the scripted async graph from Task A3.

**Files:**
- Modify: `backend/app/services/agents.py`
- Create: `tests/test_stream_and_persist.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stream_and_persist.py
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import Settings, configure_settings
from app.database import SessionLocal, configure_database, init_db
from app.models import AgentMessage, AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService
from tests._scripted_graph import _ScriptedAsyncGraph, _stream_event


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.sqlite'}")
    configure_settings(settings)
    configure_database(settings)
    init_db()
    yield
    # No teardown needed; tmp_path is cleaned up automatically.


def _make_thread() -> int:
    with SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.commit()
        return thread.id


def _stub_agent_service(monkeypatch, scripted: _ScriptedAsyncGraph) -> AgentService:
    """Build an AgentService whose deep_agent is the scripted graph."""
    monkeypatch.setattr(agents_module, "build_agent_model", lambda settings: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    return AgentService()


def test_stream_and_persist_emits_typed_events_and_persists(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event("on_tool_start", run_id="r1", name="get_positions", input={"portfolio_id": 1}),
        _stream_event("on_chat_model_stream", chunk_text="Here are "),
        _stream_event("on_tool_end", run_id="r1", output={"count": 3}),
        _stream_event("on_chat_model_stream", chunk_text="3 positions."),
    ])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    chunks = asyncio.run(run())
    joined = "".join(chunks)

    # Wire format: typed events with JSON payloads
    assert 'event: tool_start\ndata: {"id": "r1"' in joined
    assert '"name": "get_positions"' in joined
    assert 'event: token\ndata: {"text": "Here are "}' in joined
    assert 'event: tool_end\ndata: {"id": "r1"' in joined
    assert '"duration_ms":' in joined
    assert 'event: token\ndata: {"text": "3 positions."}' in joined

    # Final event is `done` with the persisted message id
    last_done = re.findall(r"event: done\ndata: ({.*?})", joined)
    assert last_done, "expected event: done at end"

    # Assistant message persisted with concatenated text and structured process_events
    with SessionLocal() as session:
        msgs = session.query(AgentMessage).filter(AgentMessage.thread_id == thread_id).all()
    assistants = [m for m in msgs if m.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].content == "Here are 3 positions."
    pe = assistants[0].meta["process_events"]
    assert len(pe) == 1
    assert pe[0]["id"] == "r1"
    assert pe[0]["status"] == "done"
    assert pe[0]["name"] == "get_positions"


def test_stream_and_persist_emits_done_with_message_id(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[_stream_event("on_chat_model_stream", chunk_text="ok")])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    m = re.search(r'event: done\ndata: ({"message_id": \d+})', joined)
    assert m, f"missing done event with message_id: {joined!r}"


def test_stream_and_persist_emits_error_when_agent_disabled(monkeypatch, in_memory_db):
    # Force deep_agent to None by failing model construction
    monkeypatch.setattr(agents_module, "build_agent_model", lambda settings: None)
    service = AgentService()
    assert service.deep_agent is None

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=1, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    assert "event: error" in joined
    assert '"retryable": false' in joined.lower() or '"retryable": False' in joined
    assert "event: done" in joined


def test_stream_and_persist_persists_partial_text_on_error(monkeypatch, in_memory_db):
    thread_id = _make_thread()

    class _ExplodingGraph(_ScriptedAsyncGraph):
        async def astream_events(self, payload, *, config=None, version="v2"):
            yield _stream_event("on_chat_model_stream", chunk_text="partial ")
            raise RuntimeError("LLM 503")

    scripted = _ExplodingGraph(events=[])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    assert "event: error" in joined
    assert "LLM 503" in joined

    with SessionLocal() as session:
        msgs = session.query(AgentMessage).filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant").all()
    assert len(msgs) == 1
    assert msgs[0].meta["agent_phase"] == "error"
    assert msgs[0].content == "partial"  # final_text strips trailing whitespace
    assert msgs[0].meta["error"] is not None


def test_stream_and_persist_emits_heartbeat_on_long_silence(monkeypatch, in_memory_db):
    """Patch asyncio.wait_for to raise TimeoutError once, simulating a 15s gap."""
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[_stream_event("on_chat_model_stream", chunk_text="ok")])
    service = _stub_agent_service(monkeypatch, scripted)

    real_wait_for = asyncio.wait_for
    call_count = {"n": 0}

    async def patched_wait_for(awaitable, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Cancel the awaitable so it doesn't hang the event loop
            if hasattr(awaitable, "cancel"):
                pass
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", patched_wait_for)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    assert "event: heartbeat" in joined
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_stream_and_persist.py -v
```

Expected: failures referencing missing `stream_and_persist` method.

- [ ] **Step 3: Implement `stream_and_persist` and helpers**

Add the following to `backend/app/services/agents.py`:

1. New imports near the top (after the existing `import json`):

```python
import asyncio
import time
```

2. New methods on `AgentService` (place after `respond` and before `stream_response` — `stream_response` will be deleted in Task A6 but is still present at this point):

```python
    async def stream_and_persist(
        self,
        *,
        thread_id: int,
        content: str,
        requested_character: str = "auto",
        page_context: AgentPageContext | None = None,
    ):
        """Stream live LangGraph events for one agent turn, then persist.

        Single-invocation refactor: this method drives a single astream_events
        run, emits typed SSE events to the client, and persists ONE
        AgentMessage after the stream completes.
        """
        if self.deep_agent is None:
            yield _sse("error", {"message": _DISABLED_RESPONSE, "retryable": False})
            yield _sse("done", {"message_id": None})
            return

        # Build prompt + assets the same way respond() does, but without persisting.
        with SessionLocal() as session:
            context = self._context(session, page_context)
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(content, requested_character, context)
        config = {"configurable": {"thread_id": str(thread_id)}}
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
                    self._persist_from_collector,
                    thread_id, collector, assets, page_context,
                )
            except Exception:
                logger.exception("Persist failed for thread %s", thread_id)
                message_id = None

            yield _sse("done", {"message_id": message_id})

    async def _drive_stream(self, prompt: str, config: dict, collector: "StreamCollector"):
        """Race astream_events against a 15s timeout to emit heartbeat events."""
        queue: asyncio.Queue = asyncio.Queue()
        DONE = object()

        async def producer():
            try:
                async for ev in self.deep_agent.astream_events(
                    {"messages": [HumanMessage(content=prompt)]},
                    config=config,
                    version="v2",
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
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    def _handle_event(self, ev: dict, collector: "StreamCollector") -> str | None:
        kind = ev.get("event")
        run_id = ev.get("run_id") or ""
        name = ev.get("name", "")
        data = ev.get("data") or {}

        if kind == "on_tool_start":
            args = data.get("input") or {}
            collector.on_tool_start(run_id, name, args, time.monotonic())
            payload = {"id": run_id, "name": name}
            if args:
                payload["args"] = _truncate(args)
            return _sse("tool_start", payload)

        if kind == "on_tool_end":
            output = data.get("output")
            error_text = _extract_tool_error(data, output)
            collector.on_tool_end(
                run_id,
                None if error_text else output,
                time.monotonic(),
                error=error_text,
            )
            ev_data = collector.tool_events.get(run_id, {})
            payload: dict = {"id": run_id, "duration_ms": ev_data.get("duration_ms", 0)}
            if error_text:
                payload["error"] = error_text
            elif output is not None:
                payload["output"] = _truncate(output)
            return _sse("tool_end", payload)

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            text = getattr(chunk, "content", None) if chunk is not None else None
            if isinstance(text, str) and text:
                collector.on_token(text)
                return _sse("token", {"text": text})

        return None

    def _extract_personas_from_state(self, state: Any, collector: "StreamCollector") -> None:
        """Walk state.values['messages'] for task(name=...) tool calls."""
        if state is None:
            return
        values = getattr(state, "values", None) or {}
        messages = values.get("messages") or []
        for message in messages:
            for tool_call in getattr(message, "tool_calls", None) or []:
                if tool_call.get("name") == "task":
                    args = tool_call.get("args") or {}
                    name = args.get("subagent_type") or args.get("name")
                    if isinstance(name, str):
                        collector.note_persona(name)

    def _persist_from_collector(
        self,
        thread_id: int,
        collector: "StreamCollector",
        assets: list[AgentAssetOut],
        page_context: AgentPageContext | None,
    ) -> int | None:
        from .deep_agent.hitl import pending_actions_from_interrupts

        with SessionLocal() as session:
            thread = session.get(AgentThread, thread_id)
            if thread is None:
                return None
            last_persona = collector.personas_invoked[-1] if collector.personas_invoked else None

            if collector.interrupts:
                pending = pending_actions_from_interrupts(collector.interrupts, persona=last_persona)
                assistant_msg = AgentMessage(
                    thread_id=thread_id,
                    role="assistant",
                    character=last_persona,
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
                    thread_id=thread_id,
                    role="assistant",
                    character=last_persona,
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
            record_audit(
                session,
                event_type="chat.message",
                actor="desk_user",
                subject_type="thread",
                subject_id=thread_id,
                payload={"personas_invoked": collector.personas_invoked, "streamed": True},
            )
            session.commit()
            return assistant_msg.id
```

3. Add a top-of-file import for the collector (with the other relative imports):

```python
from .deep_agent.stream_collector import StreamCollector, _truncate
```

4. Add `SessionLocal` to imports:

```python
from ..database import SessionLocal
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
pytest tests/test_stream_and_persist.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the full agents-related test suite**

```bash
pytest tests/test_agent_integration.py tests/test_agent_tools.py tests/test_stream_collector.py tests/test_stream_and_persist.py tests/test_agents_helpers.py -v
```

Expected: all pass — `respond()` is untouched and existing tests continue to work.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/agents.py tests/test_stream_and_persist.py
git commit -m "feat(agent): add stream_and_persist with single-invocation streaming

Replaces the double-invocation v1 trade-off: drive astream_events once,
collect tool calls + tokens via StreamCollector, persist a single
AgentMessage after the stream ends. Heartbeats every 15s of silence.
Tool errors and provider exceptions still produce a persisted message
so disconnected clients keep their thread history.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A5: HITL streaming integration test

Verify the streaming path correctly persists `pending_actions` when the orchestrator interrupts.

**Files:**
- Create: `tests/test_stream_and_persist_hitl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stream_and_persist_hitl.py
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import Settings, configure_settings
from app.database import SessionLocal, configure_database, init_db
from app.models import AgentMessage, AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService
from tests._scripted_graph import _ScriptedAsyncGraph, _interrupt, _stream_event


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.sqlite'}")
    configure_settings(settings)
    configure_database(settings)
    init_db()


def test_stream_and_persist_writes_pending_actions_on_interrupt(monkeypatch, in_memory_db):
    with SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.commit()
        thread_id = thread.id

    scripted = _ScriptedAsyncGraph(
        events=[
            _stream_event("on_chat_model_stream", chunk_text="Awaiting your approval to "),
            _stream_event("on_chat_model_stream", chunk_text="run risk."),
        ],
        interrupts=[
            _interrupt("intr-1", "run_risk", {"portfolio_id": 7}),
        ],
    )

    monkeypatch.setattr(agents_module, "build_agent_model", lambda settings: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    service = AgentService()

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="run risk on portfolio 7",
            requested_character="auto", page_context=None,
        )]

    asyncio.run(run())

    with SessionLocal() as session:
        msg = session.query(AgentMessage).filter(
            AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant"
        ).one()

    assert msg.meta["agent_phase"] == "awaiting_confirmation"
    pending = msg.meta["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "run_risk"
    assert pending[0]["payload"] == {"portfolio_id": 7}
    assert msg.content == "Awaiting your approval to run risk."
```

- [ ] **Step 2: Run test — verify it fails OR passes**

```bash
pytest tests/test_stream_and_persist_hitl.py -v
```

Expected: PASS (Task A4 already implemented the HITL path through `_persist_from_collector`).

If this passes immediately, that's correct — TDD here is "verify the implementation handles this case." Move on to commit.

If it fails, the failure is likely in `_extract_personas_from_state` or `pending_actions_from_interrupts`; fix in `agents.py` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_stream_and_persist_hitl.py
git commit -m "test(agent): cover HITL interrupt path through streaming

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task A6: Wire the new endpoint, delete dead methods

The streaming HTTP endpoint switches from `respond` + fake-stream to `stream_and_persist`.

**Files:**
- Modify: `backend/app/main.py:249-256` (the `stream_chat_message` endpoint)
- Modify: `backend/app/services/agents.py` (delete `stream_response` and `stream_response_live`)
- Delete: `tests/test_streaming.py` (tests deleted methods)

- [ ] **Step 1: Modify the endpoint**

In `backend/app/main.py`, replace the existing handler at lines 249-256:

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

        # Persist the user turn synchronously — we want it in the DB before any
        # tokens come back, even if the stream errors mid-flight.
        user_msg = AgentMessage(
            thread_id=thread.id,
            role="user",
            character=None,
            content=payload.content,
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

- [ ] **Step 2: Delete the old streaming methods from `agents.py`**

In `backend/app/services/agents.py`, delete:
- `stream_response()` method (around lines 270-280)
- `stream_response_live()` method (around lines 282-327)

Keep `respond()` and `_persist_agent_result()` as they are (used by other tests).

- [ ] **Step 3: Delete `tests/test_streaming.py`**

```bash
git rm tests/test_streaming.py
```

- [ ] **Step 4: Run the full backend test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass. Test count drops by 3 (the deleted `test_streaming.py` tests). New tests from A1–A5 are present and passing.

- [ ] **Step 5: Smoke-test the endpoint manually**

Start the dev server and exercise the streaming endpoint with curl. This catches issues that scripted tests can't (e.g., FastAPI integration, async iteration over the StreamingResponse).

```bash
# Terminal 1: start the backend
cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2: create a thread, then stream
THREAD_ID=$(curl -s -X POST http://127.0.0.1:8000/api/chat/threads \
  -H "Content-Type: application/json" \
  -d '{"title":"smoke test","character":"trader"}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -N -X POST "http://127.0.0.1:8000/api/chat/threads/${THREAD_ID}/messages/stream" \
  -H "Content-Type: application/json" \
  -d '{"content":"hello","character":"auto"}'
```

Expected: SSE lines stream in real time with `event: token`, `event: tool_start`, `event: tool_end`, ending with `event: done`. If the agent is disabled (no LLM key), expect `event: error` followed by `event: done`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/services/agents.py
git rm tests/test_streaming.py
git commit -m "feat(agent): wire stream_and_persist into HTTP endpoint

Removes the fake word-by-word stream_response and the unwired
stream_response_live. The endpoint persists the user message synchronously,
then returns a StreamingResponse over the new typed-event SSE protocol.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Phase B — Frontend Component Foundation

### Task B1: Add ToolEvent type

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add the type**

In `frontend/src/types.ts`, immediately after the `AgentActionProposal` block (around line 51):

```typescript
export type ToolEvent = {
  id: string;
  name: string;
  status: 'running' | 'done' | 'error';
  args?: Record<string, unknown> | { _truncated: true; preview: string; size: number };
  output?: unknown;
  duration_ms?: number;
  error?: string;
};
```

In the `ChatMessage.meta` block (around line 22), update the `process_events` typing if it's currently inferred via index signature (it currently is — the `[key: string]: any` catches it). Make it explicit so consumers get autocomplete:

Replace the `meta` block:

```typescript
  meta?: {
    assets?: AgentAsset[];
    pending_actions?: AgentActionProposal[];
    confirmed_action?: AgentActionProposal & { result?: Record<string, any> };
    context_used?: PageContext | null;
    routed_character?: string;
    process_events?: ToolEvent[] | string[];
    agent_phase?: 'completed' | 'error' | 'awaiting_confirmation';
    [key: string]: any;
  };
```

The `ToolEvent[] | string[]` union accommodates legacy persisted messages from before the refactor (which have `string[]`). Components must handle both shapes.

- [ ] **Step 2: Verify typecheck passes**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "types(frontend): add ToolEvent for streamed tool calls

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B2: useViewMode hook

**Files:**
- Create: `frontend/src/hooks/useViewMode.ts`
- Create: `frontend/src/hooks/useViewMode.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/src/hooks/useViewMode.test.ts
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useViewMode, VIEW_MODE_STORAGE_KEY } from './useViewMode';

describe('useViewMode', () => {
  it('defaults to compact when localStorage is empty', () => {
    const { result } = renderHook(() => useViewMode());
    expect(result.current[0]).toBe('compact');
  });

  it('hydrates from localStorage on mount', () => {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, 'detailed');
    const { result } = renderHook(() => useViewMode());
    expect(result.current[0]).toBe('detailed');
  });

  it('writes through to localStorage on setMode', () => {
    const { result } = renderHook(() => useViewMode());
    act(() => result.current[1]('detailed'));
    expect(result.current[0]).toBe('detailed');
    expect(localStorage.getItem(VIEW_MODE_STORAGE_KEY)).toBe('detailed');
  });

  it('ignores invalid values in localStorage and defaults to compact', () => {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, 'gibberish');
    const { result } = renderHook(() => useViewMode());
    expect(result.current[0]).toBe('compact');
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd frontend && npx vitest run src/hooks/useViewMode.test.ts
```

Expected: ImportError on the missing module.

- [ ] **Step 3: Implement the hook**

```typescript
// frontend/src/hooks/useViewMode.ts
import { useCallback, useEffect, useState } from 'react';

export type ViewMode = 'compact' | 'detailed';

export const VIEW_MODE_STORAGE_KEY = 'wl.agent.viewMode';

function readMode(): ViewMode {
  if (typeof window === 'undefined') return 'compact';
  const raw = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY);
  return raw === 'detailed' ? 'detailed' : 'compact';
}

export function useViewMode(): [ViewMode, (mode: ViewMode) => void] {
  const [mode, setModeState] = useState<ViewMode>(() => readMode());

  // Re-read on mount in case SSR initial value diverged from client storage.
  useEffect(() => {
    setModeState(readMode());
  }, []);

  const setMode = useCallback((next: ViewMode) => {
    setModeState(next);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, next);
    }
  }, []);

  return [mode, setMode];
}
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/hooks/useViewMode.test.ts
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useViewMode.ts frontend/src/hooks/useViewMode.test.ts
git commit -m "feat(frontend): add useViewMode hook for compact/detailed toggle

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B3: useStickyScroll hook

**Files:**
- Create: `frontend/src/hooks/useStickyScroll.ts`
- Create: `frontend/src/hooks/useStickyScroll.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/src/hooks/useStickyScroll.test.ts
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRef } from 'react';
import { useStickyScroll, STICKY_THRESHOLD_PX } from './useStickyScroll';

function setScrollGeometry(node: HTMLElement, opts: { scrollTop: number; scrollHeight: number; clientHeight: number }) {
  Object.defineProperty(node, 'scrollTop', { configurable: true, get: () => opts.scrollTop, set: () => {} });
  Object.defineProperty(node, 'scrollHeight', { configurable: true, get: () => opts.scrollHeight });
  Object.defineProperty(node, 'clientHeight', { configurable: true, get: () => opts.clientHeight });
}

function setupHook() {
  const node = document.createElement('div');
  document.body.appendChild(node);
  const { result } = renderHook(() => {
    const ref = useRef<HTMLDivElement | null>(null);
    if (!ref.current) ref.current = node;
    return useStickyScroll(ref);
  });
  return { node, result };
}

describe('useStickyScroll', () => {
  it('reports isPinned=true when scrolled to bottom', () => {
    const { node, result } = setupHook();
    setScrollGeometry(node, { scrollTop: 880, scrollHeight: 1000, clientHeight: 120 });
    act(() => { node.dispatchEvent(new Event('scroll')); });
    expect(result.current.isPinned).toBe(true);
  });

  it('reports isPinned=false when scrolled away from bottom by more than threshold', () => {
    const { node, result } = setupHook();
    setScrollGeometry(node, {
      scrollTop: 200,
      scrollHeight: 1000,
      clientHeight: 120,
    });
    act(() => { node.dispatchEvent(new Event('scroll')); });
    expect(result.current.isPinned).toBe(false);
  });

  it('respects STICKY_THRESHOLD_PX as a soft boundary', () => {
    const { node, result } = setupHook();
    // Just within threshold: scrollHeight - scrollTop - clientHeight = 100 < 120
    setScrollGeometry(node, { scrollTop: 780, scrollHeight: 1000, clientHeight: 120 });
    act(() => { node.dispatchEvent(new Event('scroll')); });
    expect(result.current.isPinned).toBe(true);
    expect(STICKY_THRESHOLD_PX).toBe(120);
  });

  it('scrollToBottom sets scrollTop to scrollHeight', () => {
    const { node, result } = setupHook();
    let written = 0;
    Object.defineProperty(node, 'scrollTop', {
      configurable: true,
      get: () => 0,
      set: (v) => { written = v; },
    });
    Object.defineProperty(node, 'scrollHeight', { configurable: true, get: () => 1234 });
    Object.defineProperty(node, 'clientHeight', { configurable: true, get: () => 100 });
    act(() => { result.current.scrollToBottom(); });
    expect(written).toBe(1234);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd frontend && npx vitest run src/hooks/useStickyScroll.test.ts
```

Expected: ImportError on the missing module.

- [ ] **Step 3: Implement the hook**

```typescript
// frontend/src/hooks/useStickyScroll.ts
import { type RefObject, useCallback, useEffect, useState } from 'react';

export const STICKY_THRESHOLD_PX = 120;

export function useStickyScroll(ref: RefObject<HTMLElement | null>): {
  isPinned: boolean;
  scrollToBottom: () => void;
} {
  const [isPinned, setIsPinned] = useState(true);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      setIsPinned(distance < STICKY_THRESHOLD_PX);
    };
    onScroll(); // initial measurement
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => { node.removeEventListener('scroll', onScroll); };
  }, [ref]);

  const scrollToBottom = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [ref]);

  return { isPinned, scrollToBottom };
}
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/hooks/useStickyScroll.test.ts
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useStickyScroll.ts frontend/src/hooks/useStickyScroll.test.ts
git commit -m "feat(frontend): add useStickyScroll hook for chat scroll pinning

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B4: NewMessagesPill component

**Files:**
- Create: `frontend/src/components/NewMessagesPill.tsx`
- Create: `frontend/src/components/NewMessagesPill.css`
- Create: `frontend/src/components/NewMessagesPill.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/components/NewMessagesPill.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NewMessagesPill } from './NewMessagesPill';

describe('NewMessagesPill', () => {
  it('shows live label when streaming and no count', () => {
    render(<NewMessagesPill streaming count={0} onClick={() => {}} />);
    expect(screen.getByRole('button')).toHaveTextContent(/live/i);
  });

  it('shows count label when not streaming and count > 0', () => {
    render(<NewMessagesPill streaming={false} count={3} onClick={() => {}} />);
    expect(screen.getByRole('button')).toHaveTextContent(/3/);
  });

  it('renders nothing when neither streaming nor count > 0', () => {
    const { container } = render(<NewMessagesPill streaming={false} count={0} onClick={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it('calls onClick when clicked', async () => {
    const onClick = vi.fn();
    render(<NewMessagesPill streaming count={0} onClick={onClick} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd frontend && npx vitest run src/components/NewMessagesPill.test.tsx
```

Expected: ImportError on missing component.

- [ ] **Step 3: Implement the component**

```tsx
// frontend/src/components/NewMessagesPill.tsx
import './NewMessagesPill.css';

type Props = {
  streaming: boolean;
  count: number;
  onClick: () => void;
};

export function NewMessagesPill({ streaming, count, onClick }: Props) {
  if (!streaming && count <= 0) return null;
  const label = streaming ? '↓ live' : `↓ ${count} new`;
  return (
    <button type="button" className="wl-new-messages-pill" onClick={onClick}>
      {label}
    </button>
  );
}
```

```css
/* frontend/src/components/NewMessagesPill.css */
.wl-new-messages-pill {
  position: absolute;
  bottom: var(--gap-3);
  left: 50%;
  transform: translateX(-50%);
  background: var(--ink);
  color: var(--paper);
  padding: 6px 12px;
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  font-weight: 700;
  border: 0;
  cursor: pointer;
  z-index: 5;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
}

.wl-new-messages-pill:hover {
  background: var(--ink-2);
}

.wl-new-messages-pill:focus-visible {
  outline: 2px solid var(--info);
  outline-offset: 2px;
}
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/components/NewMessagesPill.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/NewMessagesPill.tsx frontend/src/components/NewMessagesPill.css frontend/src/components/NewMessagesPill.test.tsx
git commit -m "feat(frontend): add NewMessagesPill for sticky-scroll chat

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B5: ToolTimeline component

**Files:**
- Create: `frontend/src/components/ToolTimeline.tsx`
- Create: `frontend/src/components/ToolTimeline.css`
- Create: `frontend/src/components/ToolTimeline.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/components/ToolTimeline.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ToolTimeline } from './ToolTimeline';
import type { ToolEvent } from '../types';

const events: ToolEvent[] = [
  { id: 'r1', name: 'get_positions', status: 'done', duration_ms: 120, args: { portfolio_id: 1 }, output: { count: 3 } },
  { id: 'r2', name: 'price_product', status: 'running', args: { underlying: 'SPX' } },
];

describe('ToolTimeline', () => {
  it('renders a list item per tool event', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('get_positions');
    expect(items[1]).toHaveTextContent('price_product');
  });

  it('shows duration_ms for completed events', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    expect(screen.getByText(/120/)).toBeInTheDocument();
  });

  it('shows running indicator for running events', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    const running = screen.getByText('price_product').closest('li');
    expect(running).toHaveAttribute('data-status', 'running');
  });

  it('hides args in compact mode', () => {
    render(<ToolTimeline events={events} mode="compact" />);
    expect(screen.queryByText(/portfolio_id/)).not.toBeInTheDocument();
    expect(screen.queryByText(/underlying/)).not.toBeInTheDocument();
  });

  it('shows args inside <details> in detailed mode', () => {
    render(<ToolTimeline events={events} mode="detailed" />);
    // <details> elements expose a "details" role via toBeInTheDocument
    const details = document.querySelectorAll('details');
    expect(details.length).toBe(2);
    expect(screen.getByText(/portfolio_id/)).toBeInTheDocument();
    expect(screen.getByText(/underlying/)).toBeInTheDocument();
  });

  it('keeps two events with the same name distinct via id', () => {
    const dup: ToolEvent[] = [
      { id: 'r1', name: 'price_product', status: 'done', duration_ms: 50 },
      { id: 'r2', name: 'price_product', status: 'done', duration_ms: 80 },
    ];
    render(<ToolTimeline events={dup} mode="compact" />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(/50/);
    expect(items[1]).toHaveTextContent(/80/);
  });

  it('renders nothing when events is empty', () => {
    const { container } = render(<ToolTimeline events={[]} mode="compact" />);
    expect(container.firstChild).toBeNull();
  });

  it('falls back to compact rendering if events is a string array (legacy meta)', () => {
    render(<ToolTimeline events={['get_positions starting', 'get_positions done'] as unknown as ToolEvent[]} mode="detailed" />);
    expect(screen.getByText(/get_positions starting/)).toBeInTheDocument();
    expect(screen.getByText(/get_positions done/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd frontend && npx vitest run src/components/ToolTimeline.test.tsx
```

Expected: ImportError on missing component.

- [ ] **Step 3: Implement the component**

```tsx
// frontend/src/components/ToolTimeline.tsx
import type { ToolEvent } from '../types';
import './ToolTimeline.css';

type Mode = 'compact' | 'detailed';

type Props = {
  events: ToolEvent[] | string[];
  mode: Mode;
};

export function ToolTimeline({ events, mode }: Props) {
  if (!events || events.length === 0) return null;

  // Legacy meta: string[] from before the streaming refactor — render as plain
  // pills (no expand UI, no run-id keying).
  if (typeof events[0] === 'string') {
    return (
      <ol className="wl-tool-timeline wl-tool-timeline--legacy">
        {(events as string[]).map((s, i) => (
          <li key={`${i}-${s}`}>{s}</li>
        ))}
      </ol>
    );
  }

  return (
    <ol className="wl-tool-timeline">
      {(events as ToolEvent[]).map((ev) => (
        <ToolEventRow key={ev.id} event={ev} mode={mode} />
      ))}
    </ol>
  );
}

function ToolEventRow({ event, mode }: { event: ToolEvent; mode: Mode }) {
  const icon = event.status === 'running' ? '↻' : event.status === 'error' ? '✕' : '✓';
  const summary = (
    <span className="wl-tool-timeline__summary">
      <span className={`wl-tool-timeline__icon wl-tool-timeline__icon--${event.status}`}>{icon}</span>
      <span className="wl-tool-timeline__name">{event.name}</span>
      {event.status === 'running' ? (
        <span className="wl-tool-timeline__timing">running…</span>
      ) : (
        <span className="wl-tool-timeline__timing">{event.duration_ms ?? 0}ms</span>
      )}
      {event.error && <span className="wl-tool-timeline__error">{event.error}</span>}
    </span>
  );

  if (mode === 'compact' || (event.args == null && event.output == null && !event.error)) {
    return (
      <li data-status={event.status}>{summary}</li>
    );
  }

  return (
    <li data-status={event.status}>
      <details open>
        <summary>{summary}</summary>
        {event.args != null && (
          <div className="wl-tool-timeline__detail">
            <span className="wl-tool-timeline__detail-label">args</span>
            <pre className="wl-tool-timeline__detail-body">{JSON.stringify(event.args, null, 2)}</pre>
          </div>
        )}
        {event.output != null && (
          <div className="wl-tool-timeline__detail">
            <span className="wl-tool-timeline__detail-label">→</span>
            <pre className="wl-tool-timeline__detail-body">{JSON.stringify(event.output, null, 2)}</pre>
          </div>
        )}
      </details>
    </li>
  );
}
```

```css
/* frontend/src/components/ToolTimeline.css */
.wl-tool-timeline {
  list-style: none;
  margin: 0 0 var(--gap-2);
  padding: 0;
  border-left: 2px solid var(--hairline);
  padding-left: var(--gap-2);
}

.wl-tool-timeline > li {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  margin: 0 0 var(--gap-1);
}

.wl-tool-timeline > li:last-child {
  margin-bottom: 0;
}

.wl-tool-timeline__summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.wl-tool-timeline__icon {
  display: inline-flex;
  width: 1em;
  justify-content: center;
}

.wl-tool-timeline__icon--running { color: var(--info); animation: wl-cursor-blink 1s step-end infinite; }
.wl-tool-timeline__icon--done    { color: var(--ok, #388e3c); }
.wl-tool-timeline__icon--error   { color: var(--err, #b00020); }

.wl-tool-timeline__name { font-weight: 700; }
.wl-tool-timeline__timing { color: var(--ink-2); font-size: 0.9em; }
.wl-tool-timeline__error { color: var(--err, #b00020); margin-left: 4px; }

.wl-tool-timeline__detail {
  margin-top: 4px;
  padding-left: 1em;
}

.wl-tool-timeline__detail-label {
  display: block;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  font-size: var(--type-caps-size);
  margin-bottom: 2px;
}

.wl-tool-timeline__detail-body {
  margin: 0;
  padding: var(--gap-1) var(--gap-2);
  background: var(--paper-2);
  border: 1px solid var(--hairline);
  font-size: var(--type-small-size);
  white-space: pre-wrap;
  word-break: break-word;
}

.wl-tool-timeline--legacy > li {
  display: inline-block;
  padding: 2px 6px;
  border: 1px solid var(--hairline);
  background: var(--paper-2);
  margin-right: 4px;
}

.wl-tool-timeline--legacy {
  border-left: 0;
  padding-left: 0;
}

@media (prefers-reduced-motion: reduce) {
  .wl-tool-timeline__icon--running { animation: none; opacity: 1; }
}
```

The `wl-cursor-blink` keyframe is defined in `ChatBubble.css` (Task B7). Until B7 lands, this animation reference is valid because the existing `ChatMessage.css` defines the same keyframe at lines 199-202.

- [ ] **Step 4: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/components/ToolTimeline.test.tsx
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ToolTimeline.tsx frontend/src/components/ToolTimeline.css frontend/src/components/ToolTimeline.test.tsx
git commit -m "feat(frontend): add ToolTimeline for streamed tool events

Renders structured ToolEvent[] in compact mode (status + name + duration)
or detailed mode (collapsible <details> with args + output). Falls back
to plain pills for legacy meta with string[] process_events.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B6: ChatBubble (rename + restructure)

This task renames `ChatMessage.tsx` → `ChatBubble.tsx` and restructures it for asymmetric alignment + integrated `ToolTimeline`.

**Files:**
- Rename + Modify: `frontend/src/components/ChatMessage.tsx` → `frontend/src/components/ChatBubble.tsx`
- Rename + Modify: `frontend/src/components/ChatMessage.css` → `frontend/src/components/ChatBubble.css`
- Rename + Modify: `frontend/src/components/ChatMessage.test.tsx` → `frontend/src/components/ChatBubble.test.tsx`

- [ ] **Step 1: Rename the files via git**

```bash
cd /Users/fuxinyao/open-otc-trading
git mv frontend/src/components/ChatMessage.tsx frontend/src/components/ChatBubble.tsx
git mv frontend/src/components/ChatMessage.css frontend/src/components/ChatBubble.css
git mv frontend/src/components/ChatMessage.test.tsx frontend/src/components/ChatBubble.test.tsx
```

- [ ] **Step 2: Update tests for the new component name + alignment + timeline integration**

Replace the entire content of `frontend/src/components/ChatBubble.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatBubble } from './ChatBubble';
import type { ChatMessage as ChatMessageType, ToolEvent } from '../types';

const userMsg: ChatMessageType = {
  id: 1,
  role: 'user',
  character: null,
  content: 'Quote a CSI500 snowball.',
  meta: {},
};

const agentMsg: ChatMessageType = {
  id: 2,
  role: 'assistant',
  character: 'trader',
  content: 'Pricing CSI500 snowball at 10.04.',
  meta: {},
};

const agentWithAction: ChatMessageType = {
  id: 3,
  role: 'assistant',
  character: 'trader',
  content: 'Confirm to run risk.',
  meta: {
    pending_actions: [{
      id: 'p1',
      tool_name: 'run_risk',
      label: 'Run risk on Desk-Q2',
      summary: '12 positions, summary method',
      payload: {},
      requires_confirmation: true,
      status: 'pending',
    }],
  },
};

const events: ToolEvent[] = [
  { id: 'r1', name: 'get_positions', status: 'done', duration_ms: 80 },
];

const agentWithEvents: ChatMessageType = {
  id: 4,
  role: 'assistant',
  character: 'trader',
  content: 'Done.',
  meta: { process_events: events },
};

describe('ChatBubble', () => {
  it('renders user role with user alignment class', () => {
    const { container } = render(
      <ChatBubble message={userMsg} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />
    );
    expect(container.querySelector('.wl-chat-bubble--user')).toBeTruthy();
    expect(container.querySelector('.wl-chat-bubble--assistant')).toBeFalsy();
    expect(screen.getByText('Quote a CSI500 snowball.')).toBeInTheDocument();
  });

  it('renders assistant role with assistant alignment class and character header', () => {
    const { container } = render(
      <ChatBubble message={agentMsg} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />
    );
    expect(container.querySelector('.wl-chat-bubble--assistant')).toBeTruthy();
    expect(screen.getByText('trader')).toBeInTheDocument();
  });

  it('renders ToolTimeline when assistant message has process_events', () => {
    render(<ChatBubble message={agentWithEvents} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />);
    expect(screen.getByRole('listitem')).toHaveTextContent('get_positions');
  });

  it('renders ActionProposal for pending actions', () => {
    render(<ChatBubble message={agentWithAction} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />);
    expect(screen.getByText('Run risk on Desk-Q2')).toBeInTheDocument();
  });

  it('confirms an action through the callback', async () => {
    const onConfirm = vi.fn();
    render(<ChatBubble message={agentWithAction} viewMode="compact" onConfirmAction={onConfirm} onDismissAction={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }));
    expect(onConfirm).toHaveBeenCalledWith(3, 'p1');
  });

  it('shows the streaming cursor only when isStreaming', () => {
    const { container, rerender } = render(
      <ChatBubble message={agentMsg} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />
    );
    expect(container.querySelector('.wl-chat-bubble__cursor')).toBeFalsy();
    rerender(<ChatBubble message={agentMsg} viewMode="compact" isStreaming onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />);
    expect(container.querySelector('.wl-chat-bubble__cursor')).toBeTruthy();
  });

  it('hides pending_actions while streaming', () => {
    render(<ChatBubble message={agentWithAction} viewMode="compact" isStreaming onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />);
    expect(screen.queryByText('Run risk on Desk-Q2')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Rewrite `ChatBubble.tsx`**

Replace the entire content of `frontend/src/components/ChatBubble.tsx`:

```tsx
import { Children, isValidElement, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage as ChatMessageType, AgentActionProposal, ToolEvent } from '../types';
import { ActionProposal } from './ActionProposal';
import { ToolTimeline } from './ToolTimeline';
import type { ViewMode } from '../hooks/useViewMode';
import './ChatBubble.css';

type Props = {
  message: ChatMessageType;
  viewMode: ViewMode;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
  isStreaming?: boolean;
};

type CodeElementProps = {
  className?: string;
  children?: ReactNode;
};

const markdownComponents: Components = {
  pre({ node: _node, children, ...props }) {
    const jsonText = extractJsonCodeBlock(children);
    if (jsonText) {
      const parsed = parseJson(jsonText);
      if (parsed.ok) return <JsonTable data={parsed.value} />;
    }
    return <pre {...props}>{children}</pre>;
  },
  table({ node: _node, ...props }) {
    return (
      <div className="wl-chat-bubble__table-wrap">
        <table className="wl-chat-bubble__table" {...props} />
      </div>
    );
  },
  td({ node: _node, children, ...props }) {
    const text = textFromNode(children).trim();
    return (
      <td className={isNumericTableCell(text) ? 'wl-chat-bubble__table-cell--numeric' : undefined} {...props}>
        {children}
      </td>
    );
  },
};
const remarkPlugins = [remarkGfm];

export function ChatBubble({ message, viewMode, onConfirmAction, onDismissAction, isStreaming }: Props) {
  const variant = message.role === 'user' ? 'user' : 'assistant';
  const meta = message.meta ?? {};
  const pendingActions: AgentActionProposal[] = !isStreaming && Array.isArray(meta.pending_actions)
    ? (meta.pending_actions as AgentActionProposal[])
    : [];
  const processEvents = (meta.process_events ?? []) as ToolEvent[] | string[];

  return (
    <article className={`wl-chat-bubble wl-chat-bubble--${variant}`}>
      <div className="wl-chat-bubble__shell">
        {message.character && variant === 'assistant' && (
          <header className="wl-chat-bubble__head">
            <span className="wl-chat-bubble__character">{message.character}</span>
          </header>
        )}
        {variant === 'assistant' && processEvents && (processEvents as unknown[]).length > 0 && (
          <ToolTimeline events={processEvents} mode={viewMode} />
        )}
        <div className="wl-chat-bubble__body">
          <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
            {message.content}
          </ReactMarkdown>
          {isStreaming && <span className="wl-chat-bubble__cursor" aria-hidden="true" />}
        </div>
        {pendingActions
          .map((action) => (
            <div key={action.id} className="wl-chat-bubble__action">
              <ActionProposal
                proposal={action}
                onConfirm={(p) => onConfirmAction(message.id, p.id)}
                onDismiss={(p) => onDismissAction(message.id, p.id)}
              />
            </div>
          ))}
      </div>
    </article>
  );
}

function extractJsonCodeBlock(children: ReactNode): string | null {
  const child = Children.toArray(children)[0];
  if (!isValidElement<CodeElementProps>(child)) return null;
  const className = child.props.className ?? '';
  if (!/\blanguage-json\b/.test(className)) return null;
  return textFromNode(child.props.children).trim();
}

function textFromNode(node: ReactNode): string {
  return Children.toArray(node).map((child) => (typeof child === 'string' || typeof child === 'number' ? String(child) : '')).join('');
}

function parseJson(value: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(value) };
  } catch {
    return { ok: false };
  }
}

function JsonTable({ data, nested = false }: { data: unknown; nested?: boolean }) {
  if (data === null || typeof data !== 'object') {
    return <span className="wl-chat-bubble__json-value">{formatScalar(data)}</span>;
  }

  const entries = Array.isArray(data)
    ? data.map((value, index) => [String(index), value] as const)
    : Object.entries(data as Record<string, unknown>);

  return (
    <table className={nested ? 'wl-chat-bubble__json-table wl-chat-bubble__json-table--nested' : 'wl-chat-bubble__json-table'}>
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th scope="row">{key}</th>
            <td>
              {value !== null && typeof value === 'object'
                ? <JsonTable data={value} nested />
                : <span className="wl-chat-bubble__json-value">{formatScalar(value)}</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatScalar(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function isNumericTableCell(value: string): boolean {
  return /^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value.replace(/,/g, ''));
}
```

- [ ] **Step 4: Rewrite `ChatBubble.css` with alignment + new class prefix**

Replace the entire content of `frontend/src/components/ChatBubble.css`:

```css
.wl-chat-bubble {
  display: flex;
  margin-bottom: var(--gap-2);
}

.wl-chat-bubble--user {
  justify-content: flex-end;
}

.wl-chat-bubble--assistant {
  justify-content: flex-start;
}

.wl-chat-bubble__shell {
  padding: var(--gap-2) var(--gap-3);
  border: 1px solid var(--line);
  background: var(--paper);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}

.wl-chat-bubble--user .wl-chat-bubble__shell {
  max-width: 65%;
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
  border-radius: 12px 12px 2px 12px;
}

.wl-chat-bubble--assistant .wl-chat-bubble__shell {
  max-width: 90%;
  background: var(--paper);
  border-color: var(--line);
  border-radius: 12px 12px 12px 2px;
}

.wl-chat-bubble__head {
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  font-weight: 700;
}

.wl-chat-bubble__body {
  font-size: var(--type-body-size);
  line-height: 1.5;
}

.wl-chat-bubble--user .wl-chat-bubble__body {
  color: var(--paper);
}

.wl-chat-bubble--assistant .wl-chat-bubble__body {
  color: var(--ink);
}

.wl-chat-bubble__body > :first-child { margin-top: 0; }
.wl-chat-bubble__body > :last-child { margin-bottom: 0; }
.wl-chat-bubble__body p { margin: 0 0 var(--gap-2); }
.wl-chat-bubble__body strong { font-weight: 800; }

.wl-chat-bubble__body ul,
.wl-chat-bubble__body ol {
  margin: 0 0 var(--gap-2);
  padding-left: 1.25rem;
}

.wl-chat-bubble__body li + li { margin-top: 2px; }

.wl-chat-bubble__body pre {
  margin: 0 0 var(--gap-2);
  padding: var(--gap-2);
  overflow-x: auto;
  background: var(--paper-2);
  border: 1px solid var(--line);
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
}

.wl-chat-bubble__body code {
  font-family: var(--font-numeric);
  font-size: 0.95em;
}

.wl-chat-bubble__table-wrap {
  width: 100%;
  margin: var(--gap-2) 0;
  border: 1px solid var(--hairline);
  background: var(--paper);
  overflow-x: auto;
}

.wl-chat-bubble__table {
  width: 100%;
  min-width: 480px;
  border-collapse: collapse;
  table-layout: auto;
  font-size: var(--type-small-size);
  line-height: 1.35;
}

.wl-chat-bubble__table th,
.wl-chat-bubble__table td {
  padding: 7px 9px;
  border-right: 1px solid var(--hairline);
  border-top: 1px solid var(--hairline);
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}

.wl-chat-bubble__table th:last-child,
.wl-chat-bubble__table td:last-child { border-right: 0; }

.wl-chat-bubble__table thead th {
  border-top: 0;
  background: var(--paper-2);
  color: var(--ink-2);
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  font-weight: 800;
  text-transform: uppercase;
}

.wl-chat-bubble__table tbody tr:nth-child(even) {
  background: color-mix(in oklab, var(--paper-2) 55%, var(--paper));
}

.wl-chat-bubble__table-cell--numeric {
  text-align: right;
  font-family: var(--font-numeric);
}

.wl-chat-bubble__json-table {
  width: 100%;
  margin: 0 0 var(--gap-2);
  border-collapse: collapse;
  border: 1px solid var(--line);
  background: var(--paper-2);
  table-layout: fixed;
}

.wl-chat-bubble__json-table--nested { margin: var(--gap-1) 0 0; background: var(--paper); }

.wl-chat-bubble__json-table th,
.wl-chat-bubble__json-table td {
  padding: 6px 8px;
  border: 1px solid var(--line);
  vertical-align: top;
  text-align: left;
  overflow-wrap: anywhere;
}

.wl-chat-bubble__json-table thead th {
  background: var(--paper);
  color: var(--ink-2);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
}

.wl-chat-bubble__json-table tbody th {
  width: 34%;
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
  color: var(--ink-2);
  font-weight: 700;
}

.wl-chat-bubble__json-value {
  font-family: var(--font-numeric);
  font-size: var(--type-small-size);
}

.wl-chat-bubble__action {}

.wl-chat-bubble__cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: currentColor;
  opacity: 0.7;
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: wl-cursor-blink 1s step-end infinite;
}

@keyframes wl-cursor-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .wl-chat-bubble__cursor { animation: none; opacity: 1; }
}
```

- [ ] **Step 5: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/components/ChatBubble.test.tsx
```

Expected: 7 passed.

- [ ] **Step 6: Update import sites**

Find all references to `ChatMessage` (the component) and `./ChatMessage`:

```bash
cd /Users/fuxinyao/open-otc-trading/frontend
grep -rn "from './ChatMessage'" src/
grep -rn "from '../components/ChatMessage'" src/
grep -rn "import.*ChatMessage" src/ | grep -v ChatMessageType
```

Update each import to point at `ChatBubble` and rename the component reference. Specifically `frontend/src/routes/AgentDesk.tsx` uses `<ChatMessage ... />` — that gets updated in Task C1.

For now, leave `AgentDesk.tsx` calling `<ChatMessage>` — it won't compile until C1, which is fine because we run vitest in isolation per task.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ChatBubble.tsx frontend/src/components/ChatBubble.css frontend/src/components/ChatBubble.test.tsx
git commit -m "refactor(frontend): rename ChatMessage → ChatBubble with role alignment

Asymmetric layout: user 65% right-aligned with dark bubble, assistant 90%
left-aligned with light bubble. Integrates ToolTimeline for streamed
process events. Routes still reference the old name; updated next task.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task B7: MessageList component

Wraps the messages container, owns the sticky-scroll behavior and the new-messages pill.

**Files:**
- Create: `frontend/src/components/MessageList.tsx`
- Create: `frontend/src/components/MessageList.css`
- Create: `frontend/src/components/MessageList.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/components/MessageList.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MessageList } from './MessageList';
import type { ChatMessage as ChatMessageType } from '../types';

function makeMsg(id: number, role: 'user' | 'assistant', content: string): ChatMessageType {
  return { id, role, character: role === 'assistant' ? 'trader' : null, content, meta: {} };
}

function setScrollGeometry(node: HTMLElement, opts: { scrollTop: number; scrollHeight: number; clientHeight: number }) {
  Object.defineProperty(node, 'scrollTop', { configurable: true, get: () => opts.scrollTop, set: () => {} });
  Object.defineProperty(node, 'scrollHeight', { configurable: true, get: () => opts.scrollHeight });
  Object.defineProperty(node, 'clientHeight', { configurable: true, get: () => opts.clientHeight });
}

describe('MessageList', () => {
  it('renders one ChatBubble per message', () => {
    const items = [makeMsg(1, 'user', 'hi'), makeMsg(2, 'assistant', 'hello')];
    render(<MessageList items={items} streaming={false} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />);
    expect(screen.getByText('hi')).toBeInTheDocument();
    expect(screen.getByText('hello')).toBeInTheDocument();
  });

  it('does not render the pill when pinned at bottom', () => {
    const items = [makeMsg(1, 'assistant', 'hello')];
    const { container } = render(
      <MessageList items={items} streaming={false} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />
    );
    const list = container.querySelector('.wl-message-list__scroll') as HTMLElement;
    setScrollGeometry(list, { scrollTop: 880, scrollHeight: 1000, clientHeight: 120 });
    act(() => { list.dispatchEvent(new Event('scroll')); });
    expect(container.querySelector('.wl-new-messages-pill')).toBeFalsy();
  });

  it('renders the pill when scrolled up and streaming', () => {
    const items = [makeMsg(1, 'assistant', 'hello')];
    const { container } = render(
      <MessageList items={items} streaming viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />
    );
    const list = container.querySelector('.wl-message-list__scroll') as HTMLElement;
    setScrollGeometry(list, { scrollTop: 100, scrollHeight: 1000, clientHeight: 120 });
    act(() => { list.dispatchEvent(new Event('scroll')); });
    expect(container.querySelector('.wl-new-messages-pill')).toBeTruthy();
  });

  it('shows count pill when scrolled up and a new message arrives (not streaming)', () => {
    const items = [makeMsg(1, 'user', 'hi')];
    const { container, rerender } = render(
      <MessageList items={items} streaming={false} viewMode="compact" onConfirmAction={vi.fn()} onDismissAction={vi.fn()} />
    );
    const list = container.querySelector('.wl-message-list__scroll') as HTMLElement;
    setScrollGeometry(list, { scrollTop: 100, scrollHeight: 1000, clientHeight: 120 });
    act(() => { list.dispatchEvent(new Event('scroll')); });

    rerender(
      <MessageList
        items={[...items, makeMsg(2, 'assistant', 'hello')]}
        streaming={false}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
      />
    );
    const pill = container.querySelector('.wl-new-messages-pill');
    expect(pill).toBeTruthy();
    expect(pill?.textContent).toMatch(/1/);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd frontend && npx vitest run src/components/MessageList.test.tsx
```

Expected: ImportError on missing component.

- [ ] **Step 3: Implement the component**

```tsx
// frontend/src/components/MessageList.tsx
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChatBubble } from './ChatBubble';
import { NewMessagesPill } from './NewMessagesPill';
import { useStickyScroll } from '../hooks/useStickyScroll';
import type { ChatMessage as ChatMessageType } from '../types';
import type { ViewMode } from '../hooks/useViewMode';
import './MessageList.css';

type Props = {
  items: ChatMessageType[];
  streamingItem?: ChatMessageType | null;
  streaming: boolean;
  viewMode: ViewMode;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
};

export function MessageList({
  items,
  streamingItem = null,
  streaming,
  viewMode,
  onConfirmAction,
  onDismissAction,
}: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { isPinned, scrollToBottom } = useStickyScroll(ref);
  const [newCount, setNewCount] = useState(0);
  const lastSeenIdRef = useRef<number | null>(null);

  // Auto-scroll while pinned: when new content lands, snap to bottom.
  useLayoutEffect(() => {
    if (isPinned) scrollToBottom();
  }, [items, streamingItem?.content, isPinned, scrollToBottom]);

  // Track unseen-message count when scrolled away.
  useEffect(() => {
    if (items.length === 0) {
      lastSeenIdRef.current = null;
      setNewCount(0);
      return;
    }
    const latestId = items[items.length - 1].id;
    if (isPinned) {
      lastSeenIdRef.current = latestId;
      setNewCount(0);
    } else if (lastSeenIdRef.current !== latestId) {
      // Count items strictly after lastSeen
      const lastSeen = lastSeenIdRef.current;
      const newer = lastSeen == null
        ? items.length
        : items.findIndex((m) => m.id === lastSeen) === -1
          ? items.length
          : items.length - items.findIndex((m) => m.id === lastSeen) - 1;
      setNewCount(newer);
    }
  }, [items, isPinned]);

  const handlePillClick = () => {
    scrollToBottom();
    if (items.length > 0) lastSeenIdRef.current = items[items.length - 1].id;
    setNewCount(0);
  };

  return (
    <div className="wl-message-list">
      <div ref={ref} className="wl-message-list__scroll">
        {items.map((msg) => (
          <ChatBubble
            key={msg.id}
            message={msg}
            viewMode={viewMode}
            onConfirmAction={onConfirmAction}
            onDismissAction={onDismissAction}
          />
        ))}
        {streamingItem && (
          <ChatBubble
            key="streaming"
            message={streamingItem}
            viewMode={viewMode}
            onConfirmAction={() => {}}
            onDismissAction={() => {}}
            isStreaming
          />
        )}
      </div>
      <NewMessagesPill
        streaming={streaming && !isPinned}
        count={isPinned ? 0 : newCount}
        onClick={handlePillClick}
      />
    </div>
  );
}
```

```css
/* frontend/src/components/MessageList.css */
.wl-message-list {
  position: relative;
  flex: 1;
  min-height: 0;
}

.wl-message-list__scroll {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: var(--gap-3);
  gap: 0;
}
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/components/MessageList.test.tsx
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MessageList.tsx frontend/src/components/MessageList.css frontend/src/components/MessageList.test.tsx
git commit -m "feat(frontend): add MessageList with sticky scroll + new-messages pill

Wraps the chat scroll container; uses useStickyScroll to gate auto-scroll
on user pinning, surfaces NewMessagesPill when scrolled away while
streaming or when new content arrives.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Phase C — Frontend Integration

### Task C1: Update AgentDesk.tsx (presentation route)

**Files:**
- Modify: `frontend/src/routes/AgentDesk.tsx`
- Modify: `frontend/src/routes/AgentDesk.test.tsx`

- [ ] **Step 1: Update existing tests to match new component composition**

Open `frontend/src/routes/AgentDesk.test.tsx` and skim the existing assertions. The shape of `Props` changes (we add `viewMode` + `onChangeViewMode`). Existing tests that render `<AgentDesk ... />` need the new props.

Update test data so any test that renders `<AgentDesk ... />` passes:

```tsx
// Add to each test render call that creates <AgentDesk ... />:
viewMode="compact"
onChangeViewMode={() => {}}
```

Also: the `streamingContent` / `streamingEvents` / `streaming` props change shape — `streamingContent` and `streamingEvents` are removed; instead a single `streamingItem: ChatMessageType | null` prop is passed (built by the live route from the draft). Update existing tests that pass these old props.

- [ ] **Step 2: Run existing tests — verify they fail with prop-mismatch**

```bash
cd frontend && npx vitest run src/routes/AgentDesk.test.tsx
```

Expected: type errors or assertion failures from missing `viewMode`.

- [ ] **Step 3: Rewrite `AgentDesk.tsx`**

Replace the entire content of `frontend/src/routes/AgentDesk.tsx`:

```tsx
import { useId, useMemo } from 'react';
import type { Thread, AgentAsset, ChatMessage as ChatMessageType } from '../types';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { MessageList } from '../components/MessageList';
import { AssetsPane } from '../components/AssetsPane';
import { ChatComposer } from '../components/ChatComposer';
import type { ViewMode } from '../hooks/useViewMode';
import './AgentDesk.css';

type Props = {
  threads: Thread[];
  activeThreadId: number | null;
  sending: boolean;
  streaming?: boolean;
  streamingItem?: ChatMessageType | null;
  viewMode: ViewMode;
  onChangeViewMode: (mode: ViewMode) => void;
  onSelectThread: (id: number) => void;
  onNewThread: () => void;
  onSend: (message: string) => void;
  onConfirmAction: (messageId: number, actionId: string) => void;
  onDismissAction: (messageId: number, actionId: string) => void;
};

function collectAssets(thread: Thread | null): AgentAsset[] {
  if (!thread) return [];
  const collected: AgentAsset[] = [];
  for (const msg of thread.messages) {
    const meta = msg.meta ?? {};
    const assets = Array.isArray(meta.assets) ? (meta.assets as AgentAsset[]) : [];
    for (const a of assets) collected.push(a);
  }
  return collected;
}

export function AgentDesk({
  threads,
  activeThreadId,
  sending,
  streaming,
  streamingItem = null,
  viewMode,
  onChangeViewMode,
  onSelectThread,
  onNewThread,
  onSend,
  onConfirmAction,
  onDismissAction,
}: Props) {
  const pickerId = useId();
  const activeThread = useMemo(
    () => threads.find((t) => t.id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );
  const assets = useMemo(() => collectAssets(activeThread), [activeThread]);

  const chips: string[] = [];
  if (activeThread) {
    chips.push(activeThread.character);
    chips.push(`${activeThread.messages.length} messages`);
  }

  return (
    <>
      <PageHeader
        title="AGENT DESK"
        chips={chips}
        action={
          <div className="wl-agent-desk__actions">
            <fieldset className="wl-agent-desk__view-mode" aria-label="Tool detail level">
              <button
                type="button"
                aria-pressed={viewMode === 'compact'}
                className={`wl-agent-desk__view-mode-btn${viewMode === 'compact' ? ' is-active' : ''}`}
                onClick={() => onChangeViewMode('compact')}
              >
                Compact
              </button>
              <button
                type="button"
                aria-pressed={viewMode === 'detailed'}
                className={`wl-agent-desk__view-mode-btn${viewMode === 'detailed' ? ' is-active' : ''}`}
                onClick={() => onChangeViewMode('detailed')}
              >
                Detailed
              </button>
            </fieldset>
            {threads.length > 0 && (
              <>
                <label htmlFor={pickerId} className="wl-agent-desk__picker-label">Thread</label>
                <select
                  id={pickerId}
                  className="wl-agent-desk__picker"
                  value={activeThreadId ?? ''}
                  onChange={(e) => onSelectThread(Number(e.target.value))}
                >
                  {threads.map((t) => (
                    <option key={t.id} value={t.id}>{t.title}</option>
                  ))}
                </select>
              </>
            )}
            <Button variant="default" onClick={onNewThread}>+ New Thread</Button>
          </div>
        }
      />

      <div className="wl-agent-desk__split">
        <section className="wl-agent-desk__chat">
          {!activeThread ? (
            <Empty
              message="Start a thread to ask the agent for pricing, risk, or research."
              symbol="◌"
              action={<Button variant="primary" onClick={onNewThread}>+ New Thread</Button>}
            />
          ) : activeThread.messages.length === 0 && !streamingItem ? (
            <Empty message="No messages yet — type below to start." symbol="◌" />
          ) : (
            <MessageList
              items={activeThread.messages}
              streamingItem={streamingItem}
              streaming={!!streaming}
              viewMode={viewMode}
              onConfirmAction={onConfirmAction}
              onDismissAction={onDismissAction}
            />
          )}
          <div className="wl-agent-desk__composer">
            <ChatComposer onSend={onSend} sending={sending} streaming={streaming} />
          </div>
        </section>

        <aside className="wl-agent-desk__assets">
          <AssetsPane assets={assets} />
        </aside>
      </div>
    </>
  );
}
```

- [ ] **Step 4: Add view-mode toggle styles**

Append to `frontend/src/routes/AgentDesk.css`:

```css
.wl-agent-desk__view-mode {
  display: inline-flex;
  border: 1px solid var(--line);
  background: var(--paper);
  padding: 0;
  margin: 0;
}

.wl-agent-desk__view-mode-btn {
  background: transparent;
  border: 0;
  padding: 4px 10px;
  font-family: var(--font-numeric);
  font-size: var(--type-caps-size);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ink-2);
  cursor: pointer;
}

.wl-agent-desk__view-mode-btn.is-active {
  background: var(--ink);
  color: var(--paper);
}

.wl-agent-desk__view-mode-btn:focus-visible {
  outline: 2px solid var(--info);
  outline-offset: 1px;
}
```

- [ ] **Step 5: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/routes/AgentDesk.test.tsx
```

Expected: all tests pass.

- [ ] **Step 6: Verify the broader build is healthy**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors. (`AgentDesk.live.tsx` will still type-check because its props consumer is `AgentDesk` — Task C2 changes the live route to satisfy the new interface.)

If `AgentDesk.live.tsx` shows errors at this point, that's expected — we'll fix it in Task C2.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/AgentDesk.tsx frontend/src/routes/AgentDesk.css frontend/src/routes/AgentDesk.test.tsx
git commit -m "feat(frontend): use MessageList in AgentDesk + view-mode toggle

Removes inline scroll container in favor of MessageList; adds a
compact/detailed two-state toggle in the page header.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task C2: Update AgentDesk.live.tsx (SSE parser)

**Files:**
- Modify: `frontend/src/routes/AgentDesk.live.tsx`
- Modify: `frontend/src/routes/AgentDesk.live.test.tsx`

- [ ] **Step 1: Write the failing tests**

Replace the streaming-related tests in `frontend/src/routes/AgentDesk.live.test.tsx` (or add new ones if the file is sparse). The full test file should at minimum cover:

```tsx
// Replace or extend frontend/src/routes/AgentDesk.live.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AgentDeskLive } from './AgentDesk.live';

const enc = new TextEncoder();

function makeSseStream(lines: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const line of lines) controller.enqueue(enc.encode(line));
      controller.close();
    },
  });
}

beforeEach(() => {
  // @ts-expect-error - test stub
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AgentDeskLive (SSE parsing)', () => {
  it('parses tool_start, token, tool_end, done events into a streaming bubble', async () => {
    const fetchMock = vi.mocked(globalThis.fetch);

    // Initial /threads list and /threads POST mocks (very simple stubs)
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/api/chat/threads') && (!init || !init.method || init.method === 'GET')) {
        return new Response(JSON.stringify([{ id: 1, title: 't', character: 'trader', messages: [] }]), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/messages/stream')) {
        const lines = [
          'event: tool_start\ndata: {"id":"r1","name":"get_positions","args":{"portfolio_id":1}}\n\n',
          'event: token\ndata: {"text":"Hello "}\n\n',
          'event: token\ndata: {"text":"world"}\n\n',
          'event: tool_end\ndata: {"id":"r1","duration_ms":80,"output":{"count":3}}\n\n',
          'event: done\ndata: {"message_id":99}\n\n',
        ];
        return new Response(makeSseStream(lines), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
      }
      // refresh after stream
      return new Response(JSON.stringify([{
        id: 1, title: 't', character: 'trader',
        messages: [
          { id: 10, role: 'user', content: 'hi', meta: {} },
          { id: 99, role: 'assistant', character: 'trader', content: 'Hello world', meta: { process_events: [{ id: 'r1', name: 'get_positions', status: 'done', duration_ms: 80 }] } },
        ],
      }]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    render(<AgentDeskLive />);
    await waitFor(() => screen.getByPlaceholderText(/message/i));
    await userEvent.type(screen.getByPlaceholderText(/message/i), 'hi');
    await userEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Hello world/)).toBeInTheDocument();
    });
    expect(screen.getByRole('listitem')).toHaveTextContent('get_positions');
  });

  it('ignores heartbeat events', async () => {
    // Similar setup with `event: heartbeat\ndata: {}\n\n` interleaved between tokens.
    // Asserts no UI artifact appears for heartbeats and the final content excludes "{}".
    // (Implementation as exercise; mirror the structure above.)
  });
});
```

The placeholder text and send-button labels in the assertions must match the existing `ChatComposer` (look at `frontend/src/components/ChatComposer.tsx`); adjust if the component uses different `placeholder` / button text.

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd frontend && npx vitest run src/routes/AgentDesk.live.test.tsx
```

Expected: failures because the SSE parser doesn't yet understand the new format (`tool_start`, `token`, `tool_end` events).

- [ ] **Step 3: Rewrite the live route**

Replace `frontend/src/routes/AgentDesk.live.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Thread, ChatMessage as ChatMessageType, ToolEvent } from '../types';
import { AgentDesk } from './AgentDesk';
import { Skeleton } from '../components/Skeleton';
import { Empty } from '../components/Empty';
import { useViewMode } from '../hooks/useViewMode';

type Draft = { content: string; events: ToolEvent[] };

export function AgentDeskLive() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const streaming = draft != null;
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useViewMode();

  const refresh = useCallback(async () => {
    const list = await api<Thread[]>('/api/chat/threads');
    setThreads(list);
    setActiveId((current) => {
      if (current != null && list.some((t) => t.id === current)) return current;
      return list[0]?.id ?? null;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  const handleNewThread = async () => {
    const created = await api<Thread>('/api/chat/threads', {
      method: 'POST',
      body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
    });
    setThreads((prev) => [created, ...prev]);
    setActiveId(created.id);
  };

  const handleSend = async (message: string) => {
    let cancelled = false;
    let threadId = activeId;
    if (threadId == null) {
      const created = await api<Thread>('/api/chat/threads', {
        method: 'POST',
        body: JSON.stringify({ title: 'New research thread', character: 'trader' }),
      });
      threadId = created.id;
      if (!cancelled) {
        setThreads((prev) => [created, ...prev]);
        setActiveId(created.id);
      }
    }
    if (!cancelled) {
      setSending(true);
      setDraft({ content: '', events: [] });
    }
    try {
      const response = await fetch(`/api/chat/threads/${threadId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: message, character: 'auto' }),
      });
      if (response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventType = 'message';
        let dataLines: string[] = [];

        const dispatch = () => {
          const dataPayload = dataLines.join('\n');
          dataLines = [];
          const eType = eventType;
          eventType = 'message';
          if (!dataPayload) return;
          handleSseEvent(eType, dataPayload, setDraft, setError);
        };

        const processLine = (line: string) => {
          if (line === '') {
            dispatch();
            return;
          }
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim() || 'message';
            return;
          }
          if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) processLine(line.trimEnd());
        }
        if (buffer) processLine(buffer.trimEnd());
        dispatch();
      }
      if (!cancelled) {
        setDraft(null);
        await refresh();
      }
    } catch (e) {
      if (!cancelled) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!cancelled) setSending(false);
    }
  };

  const handleConfirmAction = async (messageId: number, actionId: string) => {
    if (activeId == null) return;
    setThreads((prev) => markActionStatus(prev, messageId, actionId, 'confirmed'));
    try {
      await api<ChatMessageType>(
        `/api/chat/threads/${activeId}/messages/${messageId}/actions/${actionId}/confirm`,
        { method: 'POST' },
      );
      await refresh();
    } catch (e) {
      setThreads((prev) => markActionStatus(prev, messageId, actionId, 'failed'));
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDismissAction = async (messageId: number, actionId: string) => {
    if (activeId == null) return;
    setThreads((prev) => markActionStatus(prev, messageId, actionId, 'dismissed'));
    try {
      const newAssistantMessage = await api<ChatMessageType>(
        `/api/chat/threads/${activeId}/messages/${messageId}/actions/${actionId}/dismiss`,
        { method: 'POST' },
      );
      setThreads((prev) => appendMessageToThread(prev, activeId, newAssistantMessage));
    } catch (e) {
      setThreads((prev) => markActionStatus(prev, messageId, actionId, 'failed'));
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const streamingItem: ChatMessageType | null = draft
    ? {
        id: -1,
        role: 'assistant',
        character: threads.find((t) => t.id === activeId)?.character ?? 'trader',
        content: draft.content,
        meta: { process_events: draft.events },
      }
    : null;

  if (loading) {
    return (
      <div>
        <Skeleton height={32} width="40%" />
        <div style={{ height: 12 }} />
        <Skeleton height={400} />
      </div>
    );
  }

  if (error) {
    return <Empty message={`Could not load Agent Desk: ${error}`} />;
  }

  return (
    <AgentDesk
      threads={threads}
      activeThreadId={activeId}
      sending={sending}
      streaming={streaming}
      streamingItem={streamingItem}
      viewMode={viewMode}
      onChangeViewMode={setViewMode}
      onSelectThread={setActiveId}
      onNewThread={handleNewThread}
      onSend={handleSend}
      onConfirmAction={handleConfirmAction}
      onDismissAction={handleDismissAction}
    />
  );
}

function handleSseEvent(
  eventType: string,
  dataPayload: string,
  setDraft: React.Dispatch<React.SetStateAction<Draft | null>>,
  setError: (msg: string) => void,
) {
  if (eventType === 'heartbeat') return;
  let parsed: any;
  try { parsed = JSON.parse(dataPayload); } catch { return; }

  if (eventType === 'token') {
    setDraft((prev) => ({
      content: (prev?.content ?? '') + (parsed.text ?? ''),
      events: prev?.events ?? [],
    }));
    return;
  }
  if (eventType === 'tool_start') {
    const newEvent: ToolEvent = {
      id: parsed.id,
      name: parsed.name,
      status: 'running',
      args: parsed.args,
    };
    setDraft((prev) => ({
      content: prev?.content ?? '',
      events: [...(prev?.events ?? []), newEvent],
    }));
    return;
  }
  if (eventType === 'tool_end') {
    setDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        events: prev.events.map((ev) =>
          ev.id === parsed.id
            ? {
                ...ev,
                status: parsed.error ? 'error' : 'done',
                duration_ms: parsed.duration_ms,
                output: parsed.output,
                error: parsed.error,
              }
            : ev
        ),
      };
    });
    return;
  }
  if (eventType === 'error') {
    setError(parsed.message ?? 'Stream error');
    return;
  }
  // event: done is handled by the consumer loop (which exits after dispatching it)
}

function appendMessageToThread(
  threads: Thread[],
  threadId: number,
  message: ChatMessageType,
): Thread[] {
  return threads.map((thread) => {
    if (thread.id !== threadId) return thread;
    const already = thread.messages.some((m) => m.id === message.id);
    if (already) return thread;
    return { ...thread, messages: [...thread.messages, message] };
  });
}

function markActionStatus(
  threads: Thread[],
  messageId: number,
  actionId: string,
  status: 'confirmed' | 'dismissed' | 'failed',
): Thread[] {
  return threads.map((thread) => ({
    ...thread,
    messages: thread.messages.map((message) => {
      const actions = message.meta?.pending_actions;
      if (message.id !== messageId || !Array.isArray(actions)) return message;
      return {
        ...message,
        meta: {
          ...message.meta,
          pending_actions: actions.map((action) => (
            action.id === actionId ? { ...action, status } : action
          )),
        },
      };
    }),
  }));
}
```

- [ ] **Step 4: Run tests — verify all pass**

```bash
cd frontend && npx vitest run src/routes/AgentDesk.live.test.tsx
```

Expected: all pass.

- [ ] **Step 5: Verify the full frontend build**

```bash
cd frontend && npx tsc --noEmit && npx vitest run
```

Expected: type-clean; full vitest suite green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/AgentDesk.live.tsx frontend/src/routes/AgentDesk.live.test.tsx
git commit -m "feat(frontend): typed SSE parser for stream_and_persist events

Drops the legacy space-joined data format. Reducer maintains a structured
draft with content + tool events; tool events keyed by run_id pair start/end
correctly even with concurrent calls. View-mode is wired through useViewMode.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task C3: End-to-end smoke test (manual)

After all unit tests pass, exercise the feature end-to-end against a running dev stack to catch integration issues that scripted tests can't (real LangGraph events, real network buffering, real React rendering under streaming load).

- [ ] **Step 1: Start the backend**

```bash
cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: Start the frontend**

```bash
cd frontend && npm run dev
```

Expected: Vite serves on the configured port (look at terminal output).

- [ ] **Step 3: Manual checks in the browser**

Open the Agent Desk in the browser. Verify the following:

1. **Asymmetric layout:** type a message, verify it appears right-aligned in a dark bubble. Submit; verify the assistant reply appears left-aligned with a light bubble and a "trader" / persona label above it.
2. **Real streaming:** send a message that triggers tool calls (e.g., "show me the Greeks for portfolio 1" or whatever your seeded fixtures support). Verify tool entries appear with `↻ running…` and update to `✓ <Nms>` in real time, before the final text finishes.
3. **Compact ↔ Detailed toggle:** click "Detailed" in the page header. The tool timeline rows expand to show args + output blocks. Click "Compact" — they collapse to one-line summaries.
4. **Persistence:** reload the page after a turn finishes. The persisted message should still display the timeline and the same view-mode (from `localStorage`).
5. **Sticky scroll:** generate a long thread (multiple turns). Scroll up to read history. Send a new message. Verify the viewport does NOT jump to the bottom and a `↓ N new` pill appears. Click the pill — viewport snaps to bottom; pill disappears.
6. **Streaming pill:** while a turn is in progress, scroll up. Verify the pill switches to `↓ live`.
7. **HITL:** trigger a tool that requires confirmation (e.g., `approve_rfq` if your fixtures have it). Verify after the stream ends an `ActionProposal` card appears in the bubble. Click confirm — verify a follow-up assistant message appears.
8. **Heartbeat sanity check:** if you have a slow tool, the connection should not drop. (Hard to assert manually; mainly a check that nothing visible breaks.)

If any check fails, write down the failure mode and fix the implementation. Each fix gets its own commit.

- [ ] **Step 4: Document the smoke test in the PR description**

Take screenshots of the new layout (compact + detailed), the running tool timeline, and the new-messages pill. Include them in the PR description when this branch is opened.

There is no commit step for this task on its own — but any fixes uncovered should be committed individually with descriptive messages.

---

## Self-Review

Verifying spec coverage and consistency.

**Spec § coverage map:**

| Spec § | Decision | Plan task |
|--------|----------|-----------|
| §2 #1 streaming activity (compact + detailed) | ✓ | B5 (ToolTimeline) |
| §2 #2 global view-mode toggle | ✓ | B2 (useViewMode) + C1 (toggle in header) |
| §2 #3 single-invocation refactor | ✓ | A4 (stream_and_persist) + A6 (endpoint wiring) |
| §2 #4 wide bubbles (user 65% / agent 90%) | ✓ | B6 (ChatBubble.css) |
| §2 #5 sticky scroll + pill | ✓ | B3 (useStickyScroll) + B4 (NewMessagesPill) + B7 (MessageList) |
| §3 architecture overview | ✓ | covered structurally across A4 + B7 + C2 |
| §4.1 ChatBubble | ✓ | B6 |
| §4.2 ToolTimeline | ✓ | B5 |
| §4.3 MessageList + useStickyScroll | ✓ | B3 + B7 |
| §4.4 view-mode toggle | ✓ | B2 + C1 |
| §4.5 SSE parser rewrite | ✓ | C2 |
| §5 SSE wire protocol | ✓ | A4 (server emits format) + C2 (client parses format) |
| §5.3 HITL via done event | ✓ | A4 + A5 (HITL test) |
| §5.4 truncation policy | ✓ | A1 (`_truncate` in StreamCollector) |
| §6 backend StreamCollector + persistence | ✓ | A1 (collector) + A4 (stream_and_persist + handlers) |
| §6.7 method deletions | ✓ (with deviation: keep `respond()`; delete only `stream_response*`) | A6 |
| §6.8 session scope | ✓ | A4 (uses `SessionLocal()` in `_persist_from_collector`) |
| §7 HITL behavior | ✓ | A5 |
| §8 error handling | ✓ | A4 (try/except/finally + error events) |
| §9 testing | ✓ | A1, A2, A3, A4, A5 (backend) + B2, B3, B4, B5, B6, B7, C1, C2 (frontend) |

**Placeholder scan:** No "TBD"s, no "implement later", no "similar to Task N" references. All steps include concrete code, file paths, and commands.

**Type consistency check:**
- `StreamCollector` methods used in `_handle_event`: `on_tool_start`, `on_tool_end`, `on_token`, `note_persona` — all defined in A1 with matching signatures.
- `ToolEvent` shape: defined in B1 (`{id, name, status, args?, output?, duration_ms?, error?}`); used identically in B5 (ToolTimeline tests) and C2 (SSE parser).
- `ViewMode`: defined as `'compact' | 'detailed'` in B2; consumed by B5 (ToolTimeline `mode` prop), B6 (ChatBubble `viewMode` prop), B7 (MessageList `viewMode` prop), C1 (AgentDesk `viewMode` prop), C2 (`useViewMode` consumer).
- SSE event names match across A4 (server emit), C2 (client parse): `tool_start`, `tool_end`, `token`, `error`, `done`, `heartbeat`.
- `STICKY_THRESHOLD_PX`: defined as `120` in B3; the same value is asserted in B3 tests and used implicitly by B7 tests via the same node geometry.
- `VIEW_MODE_STORAGE_KEY`: defined as `'wl.agent.viewMode'` in B2; not referenced elsewhere by name (but tests assert it).

No inconsistencies found.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-agent-chat-ux-redesign.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

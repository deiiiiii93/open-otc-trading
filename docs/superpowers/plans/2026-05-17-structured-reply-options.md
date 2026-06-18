# Structured Reply Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend `propose_reply_options` BaseTool so the LLM can declare pickable reply buttons as structured `meta.reply_options`, while keeping the existing frontend regex parser as a fallback for messages without structured data.

**Architecture:** New `BaseTool` registered into `QUANT_AGENT_TOOLS` and `DEEP_AGENT_TOOL_NAMES`. Orchestrator stream loop captures the tool's input args on `on_tool_end` (after Pydantic validation has succeeded) into `StreamCollector.reply_options`. Persistence writes `meta["reply_options"]` on the assistant message. Frontend `ChatBubble` prefers structured options when present and bypasses the heuristic; otherwise the existing heuristic runs unchanged.

**Tech Stack:** Python 3 / LangChain `BaseTool` + Pydantic for the tool; FastAPI/SQLAlchemy for persistence; React + Vitest + Testing Library for the frontend; pytest for backend tests.

**Spec:** `docs/superpowers/specs/2026-05-17-structured-reply-options-design.md`

---

## File Structure

**New files:**
- `backend/app/services/reply_options/__init__.py` — package marker, re-exports the tool.
- `backend/app/services/reply_options/tool.py` — `ProposeReplyOptionsTool`, `ReplyOptionSpec`, `ProposeReplyOptionsInput`, `_normalize_reply_option`.
- `tests/test_reply_options_tool.py` — unit tests for the tool class and the normalizer.

**Modified files:**
- `backend/app/services/langchain_tools.py` — import and append the tool to `QUANT_AGENT_TOOLS`.
- `backend/app/services/deep_agent/stream_collector.py` — add `reply_options` field to `StreamCollector`.
- `backend/app/services/agents.py` — add `"propose_reply_options"` to `DEEP_AGENT_TOOL_NAMES`; capture on `on_tool_end` in `_handle_event`; persist `meta["reply_options"]` in both branches of `_persist_from_collector`.
- `backend/app/services/deep_agent/prompts/orchestrator.md` — add the reply-options rule.
- `tests/test_scripted_graph_streaming.py` — three new scenarios (happy path, last-call-wins, invalid call does not corrupt).
- `frontend/src/types.ts` — extend `ChatMessage.meta` with `reply_options`.
- `frontend/src/components/replyOptions.ts` — add optional `value?: string` to `ReplyOption`.
- `frontend/src/components/ChatBubble.tsx` — structured-first selection; click sends `value ?? label`.
- `frontend/src/components/ChatBubble.test.tsx` — four new test cases.

**Unchanged:** `MessageList.tsx`, `FloatingAgentMiniChat.tsx`, `AgentDesk.tsx`, CSS, the existing heuristic parser body.

---

## Task 1: Create the `ProposeReplyOptionsTool` with TDD

**Files:**
- Create: `backend/app/services/reply_options/__init__.py`
- Create: `backend/app/services/reply_options/tool.py`
- Test: `tests/test_reply_options_tool.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_reply_options_tool.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.reply_options.tool import (
    ProposeReplyOptionsInput,
    ProposeReplyOptionsTool,
    ReplyOptionSpec,
    _normalize_reply_option,
)


def test_tool_metadata():
    tool = ProposeReplyOptionsTool()
    assert tool.name == "propose_reply_options"
    assert "pickable reply buttons" in tool.description
    assert tool.args_schema is ProposeReplyOptionsInput


def test_run_happy_path_returns_count_ack():
    tool = ProposeReplyOptionsTool()
    options = [
        {"label": "Yes"},
        {"label": "No", "description": "Stop here"},
    ]
    out = tool._run(options=options)
    assert out == {"ok": True, "count": 2}


def test_arun_mirrors_run():
    import asyncio
    tool = ProposeReplyOptionsTool()
    out = asyncio.run(tool._arun(options=[{"label": "A"}, {"label": "B"}]))
    assert out == {"ok": True, "count": 2}


def test_input_schema_rejects_fewer_than_two_options():
    with pytest.raises(ValidationError):
        ProposeReplyOptionsInput(options=[{"label": "Only one"}])


def test_input_schema_rejects_more_than_five_options():
    with pytest.raises(ValidationError):
        ProposeReplyOptionsInput(
            options=[{"label": f"opt{i}"} for i in range(6)]
        )


def test_input_schema_rejects_oversized_label():
    with pytest.raises(ValidationError):
        ReplyOptionSpec(label="x" * 57)


def test_input_schema_rejects_oversized_description():
    with pytest.raises(ValidationError):
        ReplyOptionSpec(label="ok", description="x" * 241)


def test_input_schema_rejects_oversized_value():
    with pytest.raises(ValidationError):
        ReplyOptionSpec(label="ok", value="x" * 401)


def test_input_schema_accepts_full_shape():
    spec = ReplyOptionSpec(label="Yes", description="Run it", value="Yes, run it now")
    assert spec.label == "Yes"
    assert spec.description == "Run it"
    assert spec.value == "Yes, run it now"


def test_normalize_drops_non_dict():
    assert _normalize_reply_option("nope") is None
    assert _normalize_reply_option(None) is None
    assert _normalize_reply_option(42) is None


def test_normalize_drops_missing_or_empty_label():
    assert _normalize_reply_option({}) is None
    assert _normalize_reply_option({"label": ""}) is None
    assert _normalize_reply_option({"label": "   "}) is None


def test_normalize_drops_oversized_label():
    assert _normalize_reply_option({"label": "x" * 57}) is None


def test_normalize_trims_and_keeps_optional_fields():
    out = _normalize_reply_option(
        {"label": "  Yes  ", "description": "  Run it  ", "value": "  Yes, run it now  "}
    )
    assert out == {"label": "Yes", "description": "Run it", "value": "Yes, run it now"}


def test_normalize_omits_absent_optional_fields():
    out = _normalize_reply_option({"label": "Yes"})
    assert out == {"label": "Yes"}


def test_normalize_truncates_oversized_description():
    out = _normalize_reply_option({"label": "Yes", "description": "x" * 300})
    assert out is not None
    assert len(out["description"]) == 240


def test_normalize_truncates_oversized_value():
    out = _normalize_reply_option({"label": "Yes", "value": "x" * 500})
    assert out is not None
    assert len(out["value"]) == 400
```

- [ ] **Step 2: Run tests to verify they fail (import errors)**

Run: `pytest tests/test_reply_options_tool.py -v`
Expected: ERROR / ModuleNotFoundError for `app.services.reply_options.tool`.

- [ ] **Step 3: Create the package marker**

Create `backend/app/services/reply_options/__init__.py`:

```python
from .tool import ProposeReplyOptionsTool

__all__ = ["ProposeReplyOptionsTool"]
```

- [ ] **Step 4: Implement the tool module**

Create `backend/app/services/reply_options/tool.py`:

```python
"""Backend tool: declare pickable reply buttons for the next assistant turn.

The LLM calls ``propose_reply_options`` immediately before its final reply
whenever it is asking the user to choose between 2-5 alternatives. The
orchestrator captures the tool's input arguments after Pydantic validation
and writes them onto the persisted assistant message as
``meta["reply_options"]``. The tool itself is a pure declaration: it does
not mutate state.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

_LABEL_MAX = 56
_DESCRIPTION_MAX = 240
_VALUE_MAX = 400
_MIN_OPTIONS = 2
_MAX_OPTIONS = 5


class ReplyOptionSpec(BaseModel):
    label: str = Field(..., min_length=1, max_length=_LABEL_MAX)
    description: str | None = Field(None, max_length=_DESCRIPTION_MAX)
    value: str | None = Field(None, max_length=_VALUE_MAX)


class ProposeReplyOptionsInput(BaseModel):
    options: list[ReplyOptionSpec] = Field(
        ..., min_length=_MIN_OPTIONS, max_length=_MAX_OPTIONS
    )


class ProposeReplyOptionsTool(BaseTool):
    name: str = "propose_reply_options"
    description: str = (
        "Attach 2-5 pickable reply buttons to your NEXT assistant message. "
        "Call this immediately before writing the final reply, whenever you "
        "are asking the user to choose between alternatives. Each option has "
        "a short label (what the button shows), an optional description "
        "(secondary text under the label), and an optional value (the user "
        "message sent on click; defaults to the label). "
        "After calling this tool, phrase the question in your reply text but "
        "do NOT list the options as markdown bullets - the tool renders them."
    )
    args_schema: type[BaseModel] = ProposeReplyOptionsInput

    def _run(
        self,
        options: list[dict[str, Any]],
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return {"ok": True, "count": len(options)}

    async def _arun(
        self,
        options: list[dict[str, Any]],
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(options, config=config)


def _normalize_reply_option(option: Any) -> dict[str, Any] | None:
    """Defensive normalizer for raw option dicts read out of tool args.

    Pydantic has already validated when the tool is invoked through the
    standard path, but the orchestrator reads from the raw args dict
    (possibly recovered from event payloads), so we re-check shape and
    enforce caps to keep persisted meta safe.
    """
    if not isinstance(option, dict):
        return None
    raw_label = option.get("label")
    if not isinstance(raw_label, str):
        return None
    label = raw_label.strip()
    if not label or len(label) > _LABEL_MAX:
        return None
    out: dict[str, Any] = {"label": label}
    raw_desc = option.get("description")
    if isinstance(raw_desc, str):
        desc = raw_desc.strip()
        if desc:
            out["description"] = desc[:_DESCRIPTION_MAX]
    raw_value = option.get("value")
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if value:
            out["value"] = value[:_VALUE_MAX]
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_reply_options_tool.py -v`
Expected: all 15 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/reply_options/ tests/test_reply_options_tool.py
git commit -m "feat(reply-options): add ProposeReplyOptionsTool and normalizer"
```

---

## Task 2: Register the tool in the agent toolboxes

**Files:**
- Modify: `backend/app/services/langchain_tools.py:1514-1559` (the `QUANT_AGENT_TOOLS` list)
- Modify: `backend/app/services/agents.py:61-101` (the `DEEP_AGENT_TOOL_NAMES` frozenset)

- [ ] **Step 1: Write the failing registration test**

Append to `tests/test_reply_options_tool.py`:

```python
def test_tool_registered_in_quant_agent_tools():
    from app.services.langchain_tools import QUANT_AGENT_TOOLS

    names = {getattr(t, "name", None) for t in QUANT_AGENT_TOOLS}
    assert "propose_reply_options" in names


def test_tool_listed_in_deep_agent_tool_names():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert "propose_reply_options" in DEEP_AGENT_TOOL_NAMES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reply_options_tool.py::test_tool_registered_in_quant_agent_tools tests/test_reply_options_tool.py::test_tool_listed_in_deep_agent_tool_names -v`
Expected: FAIL — `propose_reply_options` not in either collection.

- [ ] **Step 3: Register in `QUANT_AGENT_TOOLS`**

In `backend/app/services/langchain_tools.py`, add the import near the other reply-option / async-agent imports (search for `StartAsyncAgentTool` and add a line above it):

```python
from .reply_options.tool import ProposeReplyOptionsTool
```

Then in the `QUANT_AGENT_TOOLS = [` list (currently around line 1514), add `ProposeReplyOptionsTool()` in the "Async-subagent dispatch" neighborhood (both are free-to-call UI-affecting tools). Insert just after `CancelAsyncAgentTool()`:

```python
    StartAsyncAgentTool(),
    ListAsyncAgentsTool(),
    CancelAsyncAgentTool(),
    # Pickable reply buttons (declarative, no state change):
    ProposeReplyOptionsTool(),
```

- [ ] **Step 4: Register in `DEEP_AGENT_TOOL_NAMES`**

In `backend/app/services/agents.py`, locate `DEEP_AGENT_TOOL_NAMES` (around line 61) and add `"propose_reply_options"` next to the other async-agent dispatch entries:

```python
        # Async-subagent dispatch (not HITL-gated; the subagent's own writes
        # bubble up to the parent thread).
        "start_async_agent",
        "list_async_agents",
        "cancel_async_agent",
        # Pickable reply buttons (declarative, no state change):
        "propose_reply_options",
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_reply_options_tool.py -v`
Expected: all 17 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/langchain_tools.py backend/app/services/agents.py tests/test_reply_options_tool.py
git commit -m "feat(reply-options): register tool in QUANT_AGENT_TOOLS and DEEP_AGENT_TOOL_NAMES"
```

---

## Task 3: Add `reply_options` field to `StreamCollector`

**Files:**
- Modify: `backend/app/services/deep_agent/stream_collector.py`
- Test: `tests/test_reply_options_collector.py` (new)

- [ ] **Step 1: Write the failing collector test**

Create `tests/test_reply_options_collector.py`:

```python
from __future__ import annotations

from app.services.deep_agent.stream_collector import StreamCollector


def test_reply_options_defaults_to_none():
    collector = StreamCollector()
    assert collector.reply_options is None


def test_reply_options_is_mutable_per_instance():
    a = StreamCollector()
    b = StreamCollector()
    a.reply_options = [{"label": "Yes"}, {"label": "No"}]
    assert b.reply_options is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reply_options_collector.py -v`
Expected: FAIL — `AttributeError: 'StreamCollector' object has no attribute 'reply_options'`.

- [ ] **Step 3: Add the field**

In `backend/app/services/deep_agent/stream_collector.py`, add a new field to the `StreamCollector` dataclass (after `error: str | None = None`):

```python
@dataclass
class StreamCollector:
    """Buffers a single agent turn's streamed events for later persistence."""

    text_chunks: list[str] = field(default_factory=list)
    tool_events: dict[str, dict] = field(default_factory=dict)  # keyed by run_id
    interrupts: list = field(default_factory=list)
    personas_invoked: list[str] = field(default_factory=list)
    error: str | None = None
    reply_options: list[dict] | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reply_options_collector.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/stream_collector.py tests/test_reply_options_collector.py
git commit -m "feat(reply-options): add reply_options field to StreamCollector"
```

---

## Task 4: Capture tool args into the collector on `on_tool_end`

**Files:**
- Modify: `backend/app/services/agents.py:892-907` (the `on_tool_end` branch of `_handle_event`)
- Test: `tests/test_reply_options_capture.py` (new)

This task wires the orchestrator's stream handler to inspect every `on_tool_end` event and, when the tool is `propose_reply_options` and did not error, normalize the args that were stashed at `on_tool_start` into `collector.reply_options`. "Last call wins" is implicit since we always overwrite.

- [ ] **Step 1: Write the failing capture test**

Create `tests/test_reply_options_capture.py`:

```python
from __future__ import annotations

from app.services.agents import _capture_reply_options_from_tool_end
from app.services.deep_agent.stream_collector import StreamCollector


def _seed_args(collector: StreamCollector, run_id: str, options: list[dict]) -> None:
    collector.on_tool_start(
        run_id, "propose_reply_options", {"options": options}, started_at=0.0
    )


def test_capture_sets_options_on_success():
    collector = StreamCollector()
    _seed_args(
        collector,
        "r1",
        [{"label": "Yes"}, {"label": "No", "description": "Stop"}],
    )
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options == [
        {"label": "Yes"},
        {"label": "No", "description": "Stop"},
    ]


def test_capture_ignores_other_tool_names():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "Yes"}, {"label": "No"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="get_positions", error_text=None
    )
    assert collector.reply_options is None


def test_capture_skips_on_tool_error_and_preserves_prior():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "Yes"}, {"label": "No"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    prior = collector.reply_options
    _seed_args(collector, "r2", [{"label": ""}, {"label": ""}])
    _capture_reply_options_from_tool_end(
        collector,
        run_id="r2",
        name="propose_reply_options",
        error_text="validation failed",
    )
    assert collector.reply_options == prior


def test_capture_skips_when_fewer_than_two_normalize():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "Yes"}, {"label": ""}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is None


def test_capture_last_call_wins():
    collector = StreamCollector()
    _seed_args(collector, "r1", [{"label": "A"}, {"label": "B"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    _seed_args(collector, "r2", [{"label": "C"}, {"label": "D"}, {"label": "E"}])
    _capture_reply_options_from_tool_end(
        collector, run_id="r2", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options == [
        {"label": "C"},
        {"label": "D"},
        {"label": "E"},
    ]


def test_capture_clamps_to_max_five():
    collector = StreamCollector()
    _seed_args(
        collector,
        "r1",
        [{"label": f"opt{i}"} for i in range(7)],
    )
    _capture_reply_options_from_tool_end(
        collector, run_id="r1", name="propose_reply_options", error_text=None
    )
    assert collector.reply_options is not None
    assert len(collector.reply_options) == 5


def test_capture_handles_missing_args_entry():
    collector = StreamCollector()
    _capture_reply_options_from_tool_end(
        collector,
        run_id="never_started",
        name="propose_reply_options",
        error_text=None,
    )
    assert collector.reply_options is None
```

- [ ] **Step 2: Run tests to verify they fail (import error)**

Run: `pytest tests/test_reply_options_capture.py -v`
Expected: ImportError — `_capture_reply_options_from_tool_end` does not exist in `agents.py`.

- [ ] **Step 3: Add the capture helper to `agents.py`**

In `backend/app/services/agents.py`, add this helper near the top of the module (after `_extract_tool_error`, around line 56). Import the normalizer at the top of the file alongside other service imports:

Add import (group with other `.reply_options` / async-agent imports — add after the `from .langchain_tools import QUANT_AGENT_TOOLS` line, around line 36):

```python
from .reply_options.tool import _normalize_reply_option
```

Add helper (after `_extract_tool_error`):

```python
_REPLY_OPTIONS_MAX = 5
_REPLY_OPTIONS_MIN = 2


def _capture_reply_options_from_tool_end(
    collector: "StreamCollector",
    *,
    run_id: str,
    name: str,
    error_text: str | None,
) -> None:
    """If a ``propose_reply_options`` tool just ended cleanly, write its
    normalized args into ``collector.reply_options``. Last call wins.
    Validation errors leave any prior valid options in place.
    """
    if name != "propose_reply_options" or error_text:
        return
    ev = collector.tool_events.get(run_id) or {}
    args = ev.get("args")
    if not isinstance(args, dict):
        return
    raw_options = args.get("options")
    if not isinstance(raw_options, list):
        return
    normalized: list[dict] = []
    for opt in raw_options:
        norm = _normalize_reply_option(opt)
        if norm is not None:
            normalized.append(norm)
        if len(normalized) >= _REPLY_OPTIONS_MAX:
            break
    if len(normalized) < _REPLY_OPTIONS_MIN:
        return
    collector.reply_options = normalized
```

- [ ] **Step 4: Run capture tests to verify they pass**

Run: `pytest tests/test_reply_options_capture.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Wire the helper into `_handle_event`**

In `backend/app/services/agents.py`, locate `_handle_event`'s `on_tool_end` branch (currently around lines 892-907). After the `collector.on_tool_end(...)` call and before the `payload = {...}` line, add a call to the helper:

```python
        if kind == "on_tool_end":
            output = data.get("output")
            error_text = _extract_tool_error(data, output)
            collector.on_tool_end(
                run_id,
                None if error_text else output,
                time.monotonic(),
                error=error_text,
            )
            _capture_reply_options_from_tool_end(
                collector, run_id=run_id, name=name, error_text=error_text
            )
            ev_data = collector.tool_events.get(run_id, {})
            payload = {"id": run_id, "duration_ms": ev_data.get("duration_ms", 0)}
            if error_text:
                payload["error"] = error_text
            elif output is not None:
                payload["output"] = _truncate(output)
            return _sse("tool_end", payload)
```

- [ ] **Step 6: Re-run capture tests to confirm nothing regressed**

Run: `pytest tests/test_reply_options_capture.py tests/test_reply_options_tool.py tests/test_reply_options_collector.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/agents.py tests/test_reply_options_capture.py
git commit -m "feat(reply-options): capture tool args into StreamCollector on tool_end"
```

---

## Task 5: Persist `meta["reply_options"]` in both `_persist_from_collector` branches

**Files:**
- Modify: `backend/app/services/agents.py:944-1010` (the `_persist_from_collector` method)
- Test: `tests/test_reply_options_persistence.py` (new)

- [ ] **Step 1: Write the failing persistence test**

Create `tests/test_reply_options_persistence.py`. Uses the same `in_memory_db` fixture pattern and `agents_module` monkeypatching as `tests/test_stream_and_persist_hitl.py`.

```python
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import database
from app.config import Settings, configure_settings
from app.database import configure_database, init_db
from app.models import AgentMessage, AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService
from app.services.deep_agent.stream_collector import StreamCollector

from _scripted_graph import _interrupt


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.sqlite'}")
    configure_settings(settings)
    configure_database(settings)
    init_db()
    try:
        yield
    finally:
        configure_settings(None)


@pytest.fixture
def service(monkeypatch, in_memory_db) -> AgentService:
    monkeypatch.setattr(agents_module, "build_agent_model", lambda registry: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: MagicMock())
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    return AgentService()


def _make_thread() -> int:
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.commit()
        return thread.id


def _persisted_meta(thread_id: int) -> dict:
    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .order_by(AgentMessage.id.desc())
            .first()
        )
        assert msg is not None
        return dict(msg.meta or {})


def test_persist_writes_reply_options_when_collector_has_them(service: AgentService):
    thread_id = _make_thread()
    collector = StreamCollector()
    collector.text_chunks = ["Pick one"]
    collector.reply_options = [{"label": "Yes"}, {"label": "No", "description": "Stop"}]
    service._persist_from_collector(
        thread_id, collector, assets=[], page_context=None,
        model_selection=None, accounting_date=date.today(),
    )
    meta = _persisted_meta(thread_id)
    assert meta["reply_options"] == [
        {"label": "Yes"},
        {"label": "No", "description": "Stop"},
    ]


def test_persist_omits_reply_options_key_when_none(service: AgentService):
    thread_id = _make_thread()
    collector = StreamCollector()
    collector.text_chunks = ["No choices today."]
    service._persist_from_collector(
        thread_id, collector, assets=[], page_context=None,
        model_selection=None, accounting_date=date.today(),
    )
    meta = _persisted_meta(thread_id)
    assert "reply_options" not in meta


def test_persist_writes_reply_options_on_interrupt_branch(service: AgentService):
    thread_id = _make_thread()
    collector = StreamCollector()
    collector.text_chunks = ["Confirm to run."]
    collector.reply_options = [{"label": "Yes"}, {"label": "No"}]
    collector.interrupts = [_interrupt("intr-1", "run_risk", {"portfolio_id": 7})]
    service._persist_from_collector(
        thread_id, collector, assets=[], page_context=None,
        model_selection=None, accounting_date=date.today(),
    )
    meta = _persisted_meta(thread_id)
    assert meta["reply_options"] == [{"label": "Yes"}, {"label": "No"}]
    assert meta["agent_phase"] == "awaiting_confirmation"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reply_options_persistence.py -v`
Expected: `test_persist_writes_reply_options_when_collector_has_them` FAILS — key missing. Other two may fail similarly. (If the interrupt-branch test errors because `pending_actions_from_interrupts` rejects the mock, simplify the mock to whatever the real `pending_actions_from_interrupts` accepts — read `backend/app/services/deep_agent/hitl.py` to align.)

- [ ] **Step 3: Add the key in the interrupt branch**

In `backend/app/services/agents.py`, locate `_persist_from_collector`'s interrupt branch (currently around lines 955-984). Inside the `meta={...}` dict literal, append a conditionally-included `reply_options` entry just before the closing brace:

```python
                    meta={
                        "agent_graph": "deepagents",
                        "agent_phase": "awaiting_confirmation",
                        "pending_actions": [a.model_dump(mode="json") for a in pending],
                        "interrupt_ids": [intr.id for intr in collector.interrupts],
                        "personas_invoked": collector.personas_invoked,
                        "process_events": collector.process_events,
                        "assets": [asset.model_dump(mode="json") for asset in assets],
                        "context_used": (
                            page_context.model_dump(mode="json")
                            if page_context
                            else None
                        ),
                        "context_usage": _context_usage_meta(context_usage),
                        "accounting_date": effective_accounting_date.isoformat(),
                        "agent_enabled": True,
                        "model_selection": resolved,
                        "yolo_mode": yolo_mode,
                        **(
                            {"reply_options": collector.reply_options}
                            if collector.reply_options
                            else {}
                        ),
                    },
```

- [ ] **Step 4: Add the key in the completed branch**

In the same method, the `else:` branch (currently around lines 986-1010) gets the identical conditional spread just before its closing brace:

```python
                    meta={
                        "agent_graph": "deepagents",
                        "agent_phase": "error" if collector.error else "completed",
                        "pending_actions": [],
                        "personas_invoked": collector.personas_invoked,
                        "process_events": collector.process_events,
                        "assets": [asset.model_dump(mode="json") for asset in assets],
                        "context_used": (
                            page_context.model_dump(mode="json")
                            if page_context
                            else None
                        ),
                        "context_usage": _context_usage_meta(context_usage),
                        "accounting_date": effective_accounting_date.isoformat(),
                        "error": collector.error,
                        "agent_enabled": True,
                        "model_selection": resolved,
                        "yolo_mode": yolo_mode,
                        **(
                            {"reply_options": collector.reply_options}
                            if collector.reply_options
                            else {}
                        ),
                    },
```

- [ ] **Step 5: Run persistence tests to verify they pass**

Run: `pytest tests/test_reply_options_persistence.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Run the full reply-options test set to confirm no regressions**

Run: `pytest tests/test_reply_options_tool.py tests/test_reply_options_collector.py tests/test_reply_options_capture.py tests/test_reply_options_persistence.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/agents.py tests/test_reply_options_persistence.py
git commit -m "feat(reply-options): persist meta.reply_options on assistant message"
```

---

## Task 6: Streaming integration test for the tool-call → meta path

**Files:**
- Modify: `tests/test_scripted_graph_streaming.py`

This wires the whole backend together end-to-end via the existing `_ScriptedAsyncGraph` harness — the same one already used by neighboring streaming tests.

- [ ] **Step 1: Add the integration tests**

Append to `tests/test_scripted_graph_streaming.py`. The harness pattern (`in_memory_db` fixture + monkeypatching `build_orchestrator` on the `agents_module`) is the same one used by `tests/test_stream_and_persist_hitl.py`. `_stream_event` already accepts an `error` kwarg (verified in `tests/_scripted_graph.py:125-126`).

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import database
from app.config import Settings, configure_settings
from app.database import configure_database, init_db
from app.models import AgentMessage, AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.sqlite'}")
    configure_settings(settings)
    configure_database(settings)
    init_db()
    try:
        yield
    finally:
        configure_settings(None)


def _make_thread() -> int:
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.commit()
        return thread.id


def _latest_assistant_meta(thread_id: int) -> dict:
    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .order_by(AgentMessage.id.desc())
            .first()
        )
        assert msg is not None
        return dict(msg.meta or {})


def _run_stream(service: AgentService, thread_id: int) -> None:
    async def run():
        return [
            c
            async for c in service.stream_and_persist(
                thread_id=thread_id,
                content="hi",
                requested_character="auto",
                page_context=None,
            )
        ]

    asyncio.run(run())


def test_propose_reply_options_args_land_in_meta(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event(
            "on_tool_start", run_id="r1", name="propose_reply_options",
            input={"options": [
                {"label": "Yes"},
                {"label": "No", "description": "Stop"},
            ]},
        ),
        _stream_event("on_tool_end", run_id="r1", output={"ok": True, "count": 2}),
        _stream_event("on_chat_model_stream", chunk_text="Pick one above."),
    ])
    monkeypatch.setattr(agents_module, "build_agent_model", lambda registry: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    service = AgentService()
    _run_stream(service, thread_id)

    meta = _latest_assistant_meta(thread_id)
    assert meta["reply_options"] == [
        {"label": "Yes"},
        {"label": "No", "description": "Stop"},
    ]


def test_propose_reply_options_last_call_wins(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event(
            "on_tool_start", run_id="r1", name="propose_reply_options",
            input={"options": [{"label": "A"}, {"label": "B"}]},
        ),
        _stream_event("on_tool_end", run_id="r1", output={"ok": True, "count": 2}),
        _stream_event(
            "on_tool_start", run_id="r2", name="propose_reply_options",
            input={"options": [{"label": "C"}, {"label": "D"}, {"label": "E"}]},
        ),
        _stream_event("on_tool_end", run_id="r2", output={"ok": True, "count": 3}),
        _stream_event("on_chat_model_stream", chunk_text="."),
    ])
    monkeypatch.setattr(agents_module, "build_agent_model", lambda registry: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    service = AgentService()
    _run_stream(service, thread_id)

    labels = [o["label"] for o in _latest_assistant_meta(thread_id)["reply_options"]]
    assert labels == ["C", "D", "E"]


def test_propose_reply_options_failed_call_preserves_prior(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event(
            "on_tool_start", run_id="r1", name="propose_reply_options",
            input={"options": [{"label": "Yes"}, {"label": "No"}]},
        ),
        _stream_event("on_tool_end", run_id="r1", output={"ok": True, "count": 2}),
        _stream_event(
            "on_tool_start", run_id="r2", name="propose_reply_options",
            input={"options": [{"label": ""}]},  # would have failed Pydantic
        ),
        _stream_event("on_tool_end", run_id="r2", output=None, error="validation failed"),
        _stream_event("on_chat_model_stream", chunk_text="."),
    ])
    monkeypatch.setattr(agents_module, "build_agent_model", lambda registry: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    service = AgentService()
    _run_stream(service, thread_id)

    labels = [o["label"] for o in _latest_assistant_meta(thread_id)["reply_options"]]
    assert labels == ["Yes", "No"]
```

- [ ] **Step 2: Run the new tests**

Run: `pytest tests/test_scripted_graph_streaming.py -v`
Expected: all tests (existing + 3 new) PASS.

- [ ] **Step 3: Run the full reply-options-related backend suite**

Run: `pytest tests/test_reply_options_tool.py tests/test_reply_options_collector.py tests/test_reply_options_capture.py tests/test_reply_options_persistence.py tests/test_scripted_graph_streaming.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_scripted_graph_streaming.py
git commit -m "test(reply-options): scripted-graph integration for tool->meta wiring"
```

---

## Task 7: Add the reply-options rule to the orchestrator prompt

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`

- [ ] **Step 1: Write a regression test for the prompt content**

Append to `tests/test_reply_options_tool.py`:

```python
def test_orchestrator_prompt_mentions_propose_reply_options():
    from app.services.deep_agent.orchestrator import _orchestrator_prompt

    body = _orchestrator_prompt()
    assert "propose_reply_options" in body
    assert "2-5" in body or "2–5" in body  # ASCII hyphen or en-dash
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_reply_options_tool.py::test_orchestrator_prompt_mentions_propose_reply_options -v`
Expected: FAIL.

- [ ] **Step 3: Add the rule section to `orchestrator.md`**

In `backend/app/services/deep_agent/prompts/orchestrator.md`, insert a new section **immediately before** the existing `## Batch-size-1 rule for HITL` heading (currently around line 272). The new section:

```markdown
## Pickable reply options

When your reply asks the user to choose between 2-5 alternatives, you MUST
call `propose_reply_options(options=[...])` **immediately before** writing
the reply. Each option needs:
- `label` (required, <=56 chars) — the button text.
- `description` (optional, <=240 chars) — secondary text under the label.
- `value` (optional, <=400 chars) — what gets sent on click; defaults to
  the label. Set `value` when the label alone would be ambiguous as a user
  message (e.g., label "Yes" but the desired user reply is "Yes, re-run
  risk first, then write the report").

Phrase the question naturally in your reply text; do **not** repeat the
options as a markdown bullet list — the UI renders the buttons for you.

Do not call this tool for confirmation prompts that already have a
structured ActionProposal (the HITL flow). That flow has its own
Confirm/Dismiss buttons.

```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_reply_options_tool.py::test_orchestrator_prompt_mentions_propose_reply_options -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md tests/test_reply_options_tool.py
git commit -m "feat(reply-options): teach orchestrator to call propose_reply_options"
```

---

## Task 8: Extend frontend types with `reply_options`

**Files:**
- Modify: `frontend/src/types.ts:24-43` (`ChatMessage.meta` shape)
- Modify: `frontend/src/components/replyOptions.ts:1-4` (`ReplyOption` type)

- [ ] **Step 1: Add the meta field type to `types.ts`**

In `frontend/src/types.ts`, add a new exported type and reference it in `ChatMessage.meta`:

```ts
export type ReplyOptionMeta = {
  label: string;
  description?: string;
  value?: string;
};

export type ChatMessage = {
  id: number;
  role: string;
  character?: string | null;
  content: string;
  meta?: {
    assets?: AgentAsset[];
    pending_actions?: AgentActionProposal[];
    confirmed_action?: AgentActionProposal & { result?: Record<string, any> };
    context_used?: PageContext | null;
    context_usage?: AgentContextUsage | null;
    routed_character?: string;
    process_events?: ToolEvent[] | string[];
    agent_phase?: 'completed' | 'error' | 'awaiting_confirmation';
    model_selection?: AgentModelSelection;
    model_selection_fallback?: boolean;
    yolo_mode?: boolean;
    reply_options?: ReplyOptionMeta[];
    [key: string]: any;
  };
};
```

- [ ] **Step 2: Add the optional `value` field to `ReplyOption`**

In `frontend/src/components/replyOptions.ts`, update the type at the top of the file:

```ts
export type ReplyOption = {
  label: string;
  description?: string;
  value?: string;
};
```

The heuristic parser does not set `value` — leaving it undefined is correct; the structured path will set it explicitly.

- [ ] **Step 3: Verify the TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/replyOptions.ts
git commit -m "feat(reply-options): frontend types for structured reply options"
```

---

## Task 9: Structured-first selection in `ChatBubble`

**Files:**
- Modify: `frontend/src/components/ChatBubble.tsx:1-95` (imports + selection logic + click handler)
- Test: `frontend/src/components/ChatBubble.test.tsx` (new test cases)

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/ChatBubble.test.tsx` inside the existing `describe('ChatBubble', () => { ... })` block (before its closing brace):

```tsx
  it('renders structured reply_options from meta and leaves content untouched', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 20,
          role: 'assistant',
          character: 'trader',
          content: 'Do you want to reprice the book before the risk run?',
          meta: {
            reply_options: [
              { label: 'Yes' },
              { label: 'No', description: 'Use stored prices' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    expect(screen.getByText(/Do you want to reprice the book/i)).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /suggested replies/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /No.*Use stored prices/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(20, 'No');
  });

  it('uses meta.reply_options[].value when present', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 21,
          role: 'assistant',
          character: 'trader',
          content: 'Pick a path.',
          meta: {
            reply_options: [
              {
                label: 'Fresh risk',
                description: 'Re-run risk first, then the report',
                value: 'Yes, re-run risk first, then write the report',
              },
              { label: 'Use stored' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /Fresh risk/i }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(
      21,
      'Yes, re-run risk first, then write the report',
    );
  });

  it('structured options win over heuristic and content is verbatim', () => {
    render(
      <ChatBubble
        message={{
          id: 22,
          role: 'assistant',
          character: 'trader',
          content: [
            'Do you want to proceed?',
            '',
            '- **Yes** -> ignored bullet',
            '- **No** -> ignored bullet',
          ].join('\n'),
          meta: {
            reply_options: [
              { label: 'A' },
              { label: 'B' },
            ],
          },
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={vi.fn()}
        replyOptionsEnabled
      />,
    );

    // Buttons reflect structured options, not heuristic ones.
    expect(screen.getByRole('button', { name: /^A$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^B$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Yes$/ })).not.toBeInTheDocument();
    // Content is shown verbatim (heuristic NOT stripping).
    expect(screen.getByText(/ignored bullet/i)).toBeInTheDocument();
  });

  it('falls back to heuristic when meta.reply_options is missing', async () => {
    const onSelectReplyOption = vi.fn();
    render(
      <ChatBubble
        message={{
          id: 23,
          role: 'assistant',
          character: 'trader',
          content: [
            'Do you want to proceed?',
            '',
            '- **Yes** -> Go ahead',
            '- **No** -> Stop',
          ].join('\n'),
          meta: {},
        }}
        viewMode="compact"
        onConfirmAction={vi.fn()}
        onDismissAction={vi.fn()}
        onSelectReplyOption={onSelectReplyOption}
        replyOptionsEnabled
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: /^Yes/ }));
    expect(onSelectReplyOption).toHaveBeenCalledWith(23, 'Yes');
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/ChatBubble.test.tsx`
Expected: the 4 new tests FAIL — structured `reply_options` ignored by current code.

- [ ] **Step 3: Update `ChatBubble.tsx` imports and selection logic**

In `frontend/src/components/ChatBubble.tsx`, add the type import at the top (modify the existing `types` import to include `ReplyOptionMeta`):

```tsx
import {
  personaDisplayLabel,
  type AgentChannel,
  type AgentActionProposal,
  type AgentAsset,
  type ChatMessage as ChatMessageType,
  type ReplyOptionMeta,
  type ToolEvent,
} from '../types';
```

Replace the existing block at lines ~57-68 (the `replyOptionsExtraction` / `showReplyOptions` / `visibleContent` definitions) with structured-first logic:

```tsx
  const structuredOptions: ReplyOptionMeta[] = Array.isArray(meta.reply_options)
    ? (meta.reply_options as ReplyOptionMeta[]).filter(
        (o) => o && typeof o.label === 'string' && o.label.trim().length > 0,
      )
    : [];
  const heuristicExtraction = structuredOptions.length === 0
    && variant === 'assistant'
    && !isStreaming
    && pendingActions.length === 0
    ? extractReplyOptions(message.content)
    : null;
  const showReplyOptions = !!(
    replyOptionsEnabled
    && onSelectReplyOption
    && (
      structuredOptions.length > 0
      || (heuristicExtraction && heuristicExtraction.options.length > 0)
    )
  );
  const visibleContent = showReplyOptions && structuredOptions.length === 0 && heuristicExtraction
    ? heuristicExtraction.contentWithoutOptions
    : message.content;
  const optionsToRender: ReplyOption[] = structuredOptions.length > 0
    ? structuredOptions
    : (heuristicExtraction?.options ?? []);
  const linkedContent = linkifyAssetPaths(visibleContent, assets);
  const markdownComponents = markdownComponentsForAssets(assets);
```

- [ ] **Step 4: Update the `ReplyOptionButtons` invocation and click handler**

Still in `ChatBubble.tsx`, replace the existing `showReplyOptions && replyOptionsExtraction && (...)` block (lines ~89-94) with:

```tsx
        {showReplyOptions && optionsToRender.length > 0 && (
          <ReplyOptionButtons
            options={optionsToRender}
            onSelect={(option) => onSelectReplyOption!(message.id, option.value ?? option.label)}
          />
        )}
```

`ReplyOptionButtons` itself does not need changing — it already iterates `ReplyOption[]` and renders `option.label` / `option.description`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/ChatBubble.test.tsx`
Expected: all `ChatBubble` tests (existing + 4 new) PASS.

- [ ] **Step 6: Confirm heuristic tests still pass**

Run: `cd frontend && npx vitest run src/components/replyOptions.test.ts src/components/MessageList.test.tsx`
Expected: all PASS — the fallback path is intact.

- [ ] **Step 7: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ChatBubble.tsx frontend/src/components/ChatBubble.test.tsx
git commit -m "feat(reply-options): frontend prefers structured meta.reply_options"
```

---

## Task 10: Full-suite smoke run

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full backend suite**

Run: `pytest tests/ -x`
Expected: all PASS.

- [ ] **Step 2: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: all PASS.

- [ ] **Step 3: Manual smoke (optional but recommended)**

Start the dev server and verify in a browser that:
1. When the LLM calls `propose_reply_options` mid-turn, the assistant message renders pickable buttons.
2. Clicking a button (with `value` unset) sends the label as a user message.
3. Clicking a button (with `value` set) sends the value.
4. An older assistant message with bulleted options but **no** `meta.reply_options` still renders buttons via the heuristic.

If any UI smoke check fails, file a follow-up — do not patch in this plan; the unit + integration tests are the ship gate.

- [ ] **Step 4: Final commit (only if Step 3 prompted any changes)**

```bash
git status
# If clean, no commit needed.
```

---

## Notes for the executor

- The `_normalize_reply_option` helper is the **single source of truth** for "is this option dict safe to persist". Don't duplicate length-cap logic anywhere else.
- The capture helper lives in `agents.py` (not in `stream_collector.py`) because it depends on `_normalize_reply_option` from the `reply_options` package, and we want `stream_collector.py` to stay dependency-free (it's imported in many places).
- The frontend's structured path **does not strip** the content. We rely on the prompt rule "do not repeat the options as a markdown bullet list" to keep the rendered text clean. If LLMs frequently bulk-duplicate, revisit in a follow-up — do not add stripping logic preemptively.
- `MessageList.tsx`'s `latestCompletedAssistantId` gate continues to apply to both structured and heuristic paths. No changes there.
- Each task is one logical commit. Do not batch tasks — the review checkpoints depend on per-task atomicity.

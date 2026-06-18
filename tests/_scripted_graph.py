"""Shared scripted-model fixtures for HITL integration tests."""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Iterable

from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.types import Interrupt


class _ScriptedGraph:
    """Replays a list of result dicts, one per .invoke() call.

    Each script entry may be:
    - a plain dict  (returned directly as the invoke result)
    - a callable    (called with (payload, config) -> result dict)

    Fails loudly when invoked more times than scripted.
    """

    name = "otc_desk_orchestrator"

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self._cursor = 0
        self._last_result: dict | None = None
        self.last_config: Any = None

    def invoke(self, payload: Any, config: Any = None) -> dict:
        self.last_config = config
        if self._cursor >= len(self._script):
            raise AssertionError(
                f"_ScriptedGraph.invoke() called {self._cursor + 1} time(s) but only "
                f"{len(self._script)} scripted result(s) were provided."
            )
        entry = self._script[self._cursor]
        self._cursor += 1
        if callable(entry):
            result = entry(payload, config)
        else:
            result = entry
        self._last_result = result
        return result

    async def astream_events(
        self,
        payload: Any,
        *,
        config: Any = None,
        version: str = "v2",
        control: Any = None,
    ):
        result = self.invoke(payload, config)
        events = result.get("__events__") if isinstance(result, dict) else None
        if events is not None:
            for ev in events:
                yield ev
            return
        text = _final_ai_text(result)
        if text:
            yield _stream_event("on_chat_model_stream", chunk_text=text)

    def get_state(self, config: Any = None) -> "_MockState":
        result = self._last_result or {}
        interrupts = tuple(result.get("__interrupt__") or [])
        messages = list(result.get("messages") or [])
        tasks = (_MockTask(interrupts=interrupts),) if interrupts else ()
        values = {"messages": messages}
        if "todos" in result:
            values["todos"] = result["todos"]
        if "files" in result:
            values["files"] = result["files"]
        return _MockState(tasks=tasks, values=values)


def _final_ai_text(result: Any) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            content = getattr(message, "content", "")
            return content.strip() if isinstance(content, str) else str(content).strip()
    return ""


def _interrupt(
    interrupt_id: str,
    action_name: str,
    args: dict,
    description: str | None = None,
) -> Interrupt:
    return Interrupt(
        value={
            "action_requests": [
                {
                    "name": action_name,
                    "args": args,
                    "description": description or f"Run {action_name}",
                }
            ],
            "review_configs": [
                {"action_name": action_name, "allowed_decisions": ["approve", "reject"]}
            ],
        },
        id=interrupt_id,
    )


def _ai(content: str, *, tool_calls: list | None = None) -> AIMessage:
    return AIMessage(content=content, tool_calls=tool_calls or [])


def _task_call(persona: str) -> dict:
    """Mimic a task(name=...) tool call attached to an AIMessage."""
    return {
        "name": "task",
        "args": {"name": persona, "subagent_type": persona},
        "id": f"tc-{persona}",
        "type": "tool_call",
    }


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
        self.last_stream_config: Any = None
        self.last_stream_version: str | None = None
        self.last_stream_control: Any = None
        self.last_state_config: Any = None

    async def astream_events(
        self,
        payload: Any,
        *,
        config: Any = None,
        version: str = "v2",
        control: Any = None,
    ):
        self.last_stream_config = config
        self.last_stream_version = version
        self.last_stream_control = control
        for ev in self._events:
            yield ev

    def get_state(self, config: Any = None) -> _MockState:
        self.last_state_config = config
        if self._interrupts or self._messages:
            tasks = (_MockTask(interrupts=tuple(self._interrupts)),)
        else:
            tasks = ()
        return _MockState(tasks=tasks, values={"messages": list(self._messages)})

    def invoke(self, payload: Any, config: Any = None) -> dict:
        # For mixed sync/async tests if needed
        return {"messages": []}

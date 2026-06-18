from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import database
from app.config import Settings, configure_settings
from app.database import configure_database, init_db
from app.models import AgentMessage, AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService

from _scripted_graph import _ScriptedAsyncGraph, _stream_event


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


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        feature_workflow_routing=False,
    )
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
        _stream_event(
            "on_tool_end", run_id="r1", name="propose_reply_options",
            output={"ok": True, "count": 2},
        ),
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
        _stream_event(
            "on_tool_end", run_id="r1", name="propose_reply_options",
            output={"ok": True, "count": 2},
        ),
        _stream_event(
            "on_tool_start", run_id="r2", name="propose_reply_options",
            input={"options": [{"label": "C"}, {"label": "D"}, {"label": "E"}]},
        ),
        _stream_event(
            "on_tool_end", run_id="r2", name="propose_reply_options",
            output={"ok": True, "count": 3},
        ),
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
        _stream_event(
            "on_tool_end", run_id="r1", name="propose_reply_options",
            output={"ok": True, "count": 2},
        ),
        _stream_event(
            "on_tool_start", run_id="r2", name="propose_reply_options",
            input={"options": [{"label": ""}]},  # would have failed Pydantic
        ),
        _stream_event(
            "on_tool_end", run_id="r2", name="propose_reply_options",
            output=None, error="validation failed",
        ),
        _stream_event("on_chat_model_stream", chunk_text="."),
    ])
    monkeypatch.setattr(agents_module, "build_agent_model", lambda registry: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    service = AgentService()
    _run_stream(service, thread_id)

    labels = [o["label"] for o in _latest_assistant_meta(thread_id)["reply_options"]]
    assert labels == ["Yes", "No"]

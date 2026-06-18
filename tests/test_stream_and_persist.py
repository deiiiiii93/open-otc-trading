from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import re
from pathlib import Path
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
import pytest

from app.config import Settings, configure_settings
from app import database
from app.database import configure_database, init_db
from app.models import AgentMessage, AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService
from _scripted_graph import _ScriptedAsyncGraph, _stream_event


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        agent_recursion_limit=77,
        feature_workflow_routing=False,
    )
    configure_settings(settings)
    configure_database(settings)
    init_db()
    try:
        yield
    finally:
        # Restore the settings override so it does not leak into other tests
        # in the same pytest session.
        configure_settings(None)


def _make_thread() -> int:
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.commit()
        return thread.id


def _stub_agent_service(
    monkeypatch,
    scripted: _ScriptedAsyncGraph,
    *,
    settings: Settings | None = None,
) -> AgentService:
    """Build an AgentService whose deep_agent is the scripted graph."""
    monkeypatch.setattr(
        agents_module, "build_agent_model", lambda *args, **kwargs: MagicMock()
    )
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    return AgentService(settings=settings)


class _NullAsyncCheckpointer:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
    assert scripted.last_stream_version == "v3"
    assert scripted.last_stream_control is not None
    assert scripted.last_stream_config["recursion_limit"] == 77
    assert scripted.last_state_config["recursion_limit"] == 77

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
    with database.SessionLocal() as session:
        msgs = session.query(AgentMessage).filter(AgentMessage.thread_id == thread_id).all()
    assistants = [m for m in msgs if m.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].content == "Here are 3 positions."
    pe = assistants[0].meta["process_events"]
    assert len(pe) == 1
    assert pe[0]["id"] == "r1"
    assert pe[0]["status"] == "done"
    assert pe[0]["name"] == "get_positions"


def test_stream_and_persist_backfills_legacy_thread_for_assistant_scope(
    monkeypatch, in_memory_db
):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(
        events=[_stream_event("on_chat_model_stream", chunk_text="scoped")]
    )
    service = _stub_agent_service(monkeypatch, scripted)

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

    with database.SessionLocal() as session:
        thread = session.get(AgentThread, thread_id)
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )
    assert thread.active_workflow_id is not None
    assert msg.workflow_id == thread.active_workflow_id
    assert msg.session_id is not None


def test_stream_and_persist_can_use_v2_rollback(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(
        events=[_stream_event("on_chat_model_stream", chunk_text="ok")]
    )
    service = _stub_agent_service(
        monkeypatch,
        scripted,
        settings=replace(database.settings, agent_stream_version="v2"),
    )

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

    joined = "".join(asyncio.run(run()))
    assert "event: done" in joined
    assert scripted.last_stream_version == "v2"
    assert scripted.last_stream_control is None


def test_stream_and_persist_forces_v2_for_deepseek_reasoning(
    monkeypatch, in_memory_db
):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(
        events=[_stream_event("on_chat_model_stream", chunk_text="ok")]
    )
    service = _stub_agent_service(
        monkeypatch,
        scripted,
        settings=replace(database.settings, agent_stream_version="v3"),
    )
    monkeypatch.setattr(
        agents_module,
        "build_async_checkpointer",
        lambda settings: _NullAsyncCheckpointer(),
    )

    async def run():
        return [
            c
            async for c in service.stream_and_persist(
                thread_id=thread_id,
                content="hi",
                requested_character="auto",
                page_context=None,
                model_selection={
                    "channel": "deepseek",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                },
            )
        ]

    joined = "".join(asyncio.run(run()))
    assert "event: done" in joined
    assert scripted.last_stream_version == "v2"
    assert scripted.last_stream_control is None


def test_stream_and_persist_parses_v3_message_and_tool_events(
    monkeypatch, in_memory_db
):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(
        events=[
            {
                "type": "event",
                "method": "tools",
                "params": {
                    "data": {
                        "event": "tool-started",
                        "tool_call_id": "v3-tool",
                        "tool_name": "get_positions",
                        "input": {"portfolio_id": 1},
                    }
                },
            },
            {
                "type": "event",
                "method": "messages",
                "params": {
                    "data": (
                        {
                            "event": "content-block-delta",
                            "delta": {"type": "text-delta", "text": "v3 "},
                        },
                        {},
                    )
                },
            },
            {
                "type": "event",
                "method": "tools",
                "params": {
                    "data": {
                        "event": "tool-finished",
                        "tool_call_id": "v3-tool",
                        "output": {"count": 2},
                    }
                },
            },
            {
                "type": "event",
                "method": "messages",
                "params": {
                    "data": (
                        {
                            "event": "content-block-delta",
                            "delta": {"type": "text-delta", "text": "done"},
                        },
                        {},
                    )
                },
            },
        ]
    )
    service = _stub_agent_service(monkeypatch, scripted)

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

    joined = "".join(asyncio.run(run()))
    assert 'event: tool_start\ndata: {"id": "v3-tool"' in joined
    assert 'event: token\ndata: {"text": "v3 "}' in joined
    assert 'event: tool_end\ndata: {"id": "v3-tool"' in joined

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )
    assert msg.content == "v3 done"
    assert msg.meta["process_events"][0]["name"] == "get_positions"
    assert msg.meta["process_events"][0]["status"] == "done"


def test_stream_and_persist_materializes_run_python_trading_desk_artifacts(
    monkeypatch,
    in_memory_db,
    tmp_path: Path,
):
    thread_id = _make_thread()
    csv_path = "/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv"
    csv_content = "position_id,ko_pct_from_spot\n66,0.48\n"
    scripted = _ScriptedAsyncGraph(
        events=[
            {
                "type": "event",
                "method": "tools",
                "params": {
                    "data": {
                        "event": "tool-started",
                        "tool_call_id": "run-python",
                        "tool_name": "run_python",
                        "input": {"writes_artifacts": True},
                    }
                },
            },
            {
                "type": "event",
                "method": "tools",
                "params": {
                    "data": {
                        "event": "tool-finished",
                        "tool_call_id": "run-python",
                        "tool_name": "run_python",
                        "output": {
                            "ok": True,
                            "result": {"file_path": csv_path, "rows_written": 1},
                            "artifacts": [
                                {
                                    "path": csv_path,
                                    "size_bytes": len(csv_content.encode("utf-8")),
                                    "content": csv_content,
                                    "kind": "text",
                                }
                            ],
                        },
                    }
                },
            },
            {
                "type": "event",
                "method": "messages",
                "params": {
                    "data": (
                        {
                            "event": "content-block-delta",
                            "delta": {
                                "type": "text-delta",
                                "text": f"CSV written successfully: {csv_path}",
                            },
                        },
                        {},
                    )
                },
            },
        ]
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        artifact_dir=tmp_path / "artifacts",
        feature_workflow_routing=False,
    )
    service = _stub_agent_service(monkeypatch, scripted, settings=settings)

    async def run():
        return [
            c
            async for c in service.stream_and_persist(
                thread_id=thread_id,
                content="export csv",
                requested_character="auto",
                page_context=None,
            )
        ]

    joined = "".join(asyncio.run(run()))
    assert "event: done" in joined

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )

    assets = msg.meta["assets"]
    csv_assets = [asset for asset in assets if asset["path"] == csv_path]
    assert len(csv_assets) == 1
    asset = csv_assets[0]
    assert asset["mime_type"] == "text/csv"
    artifact_path = Path(asset["metadata"]["artifact_path"])
    assert artifact_path.read_text(encoding="utf-8") == csv_content


def test_stream_and_persist_materializes_generic_binary_tool_artifacts(
    monkeypatch,
    in_memory_db,
    tmp_path: Path,
):
    thread_id = _make_thread()
    docx_path = "/trading_desk/reports/board_report.docx"
    docx_bytes = b"PK\x03\x04docx"
    scripted = _ScriptedAsyncGraph(
        events=[
            {
                "type": "event",
                "method": "tools",
                "params": {
                    "data": {
                        "event": "tool-started",
                        "tool_call_id": "report",
                        "tool_name": "write_report_artifact",
                        "input": {"format": "docx"},
                    }
                },
            },
            {
                "type": "event",
                "method": "tools",
                "params": {
                    "data": {
                        "event": "tool-finished",
                        "tool_call_id": "report",
                        "tool_name": "write_report_artifact",
                        "output": {
                            "file_path": docx_path,
                            "format": "docx",
                            "size_bytes": len(docx_bytes),
                            "artifacts": [
                                {
                                    "path": docx_path,
                                    "size_bytes": len(docx_bytes),
                                    "content_b64": base64.b64encode(docx_bytes).decode("ascii"),
                                    "kind": "binary",
                                }
                            ],
                        },
                    }
                },
            },
            {
                "type": "event",
                "method": "messages",
                "params": {
                    "data": (
                        {
                            "event": "content-block-delta",
                            "delta": {
                                "type": "text-delta",
                                "text": f"DOCX written: {docx_path}",
                            },
                        },
                        {},
                    )
                },
            },
        ]
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        artifact_dir=tmp_path / "artifacts",
        feature_workflow_routing=False,
    )
    service = _stub_agent_service(monkeypatch, scripted, settings=settings)

    async def run():
        return [
            c
            async for c in service.stream_and_persist(
                thread_id=thread_id,
                content="write docx report",
                requested_character="auto",
                page_context=None,
            )
        ]

    joined = "".join(asyncio.run(run()))
    assert "event: done" in joined

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )

    docx_assets = [asset for asset in msg.meta["assets"] if asset["path"] == docx_path]
    assert len(docx_assets) == 1
    asset = docx_assets[0]
    assert asset["mime_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    artifact_path = Path(asset["metadata"]["artifact_path"])
    assert artifact_path.read_bytes() == docx_bytes


def test_stream_and_persist_records_start_async_agent_task_id(
    monkeypatch, in_memory_db
):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event(
            "on_tool_start",
            run_id="async-1",
            name="start_async_agent",
            input={
                "description": "risk narrative",
                "prompt": "Write the risk narrative.",
                "inputs": {"portfolio_id": 5},
            },
        ),
        _stream_event(
            "on_tool_end",
            run_id="async-1",
            output={"ok": True, "task_id": 12, "status": "queued"},
        ),
        _stream_event("on_chat_model_stream", chunk_text="Started async task #12."),
    ])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id,
            content="yes, dispatch an async agent",
            requested_character="auto",
            page_context=None,
        )]

    joined = "".join(asyncio.run(run()))
    assert '"name": "start_async_agent"' in joined
    assert '"task_id": 12' in joined
    assert 'event: token\ndata: {"text": "Started async task #12."}' in joined

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )
    assert msg.content == "Started async task #12."
    pe = msg.meta["process_events"]
    assert len(pe) == 1
    assert pe[0]["name"] == "start_async_agent"
    assert pe[0]["status"] == "done"
    assert pe[0]["output"]["task_id"] == 12


def test_stream_and_persist_yolo_mode_uses_auto_approval_policy(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[_stream_event("on_chat_model_stream", chunk_text="ok")])
    captured: dict = {}

    monkeypatch.setattr(agents_module, "build_agent_model", lambda *args, **kwargs: MagicMock())

    def fake_build_orchestrator(**kwargs):
        captured.update(kwargs)
        return scripted

    monkeypatch.setattr(agents_module, "build_orchestrator", fake_build_orchestrator)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    monkeypatch.setattr(agents_module, "build_async_checkpointer", lambda settings: _NullAsyncCheckpointer())
    service = AgentService()

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id,
            content="run risk",
            requested_character="auto",
            page_context=None,
            yolo_mode=True,
        )]

    asyncio.run(run())

    interrupt_on = captured["interrupt_on"]
    assert "run_batch_pricing" not in interrupt_on
    assert interrupt_on["approve_rfq"]["allowed_decisions"] == ["approve", "reject"]

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .one()
        )
    assert msg.meta["yolo_mode"] is True


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
        async def astream_events(
            self, payload, *, config=None, version="v2", control=None
        ):
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

    with database.SessionLocal() as session:
        msgs = session.query(AgentMessage).filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant").all()
    assert len(msgs) == 1
    assert msgs[0].meta["agent_phase"] == "error"
    assert msgs[0].content == "partial"  # final_text strips trailing whitespace
    assert msgs[0].meta["error"] is not None


def test_stream_and_persist_recovers_completed_tools_on_transport_disconnect(
    monkeypatch, in_memory_db
):
    thread_id = _make_thread()

    class _DisconnectingGraph(_ScriptedAsyncGraph):
        async def astream_events(
            self, payload, *, config=None, version="v2", control=None
        ):
            yield _stream_event(
                "on_tool_start",
                run_id="r1",
                name="run_batch_pricing",
                input={"portfolio_id": 5},
            )
            yield _stream_event(
                "on_tool_end",
                run_id="r1",
                output={"risk_run_id": 11, "status": "completed"},
            )
            raise RuntimeError(
                "peer closed connection without sending complete message body "
                "(incomplete chunked read)"
            )

    scripted = _DisconnectingGraph(events=[])
    service = _stub_agent_service(monkeypatch, scripted)

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

    joined = "".join(asyncio.run(run()))
    assert "event: error" in joined

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )

    assert msg.meta["agent_phase"] == "completed_with_transport_error"
    assert "model stream disconnected before final synthesis" in msg.content
    assert "run_batch_pricing" in msg.content
    assert msg.meta["process_events"][0]["status"] == "done"
    assert msg.meta["process_events"][0]["output"] == {
        "risk_run_id": 11,
        "status": "completed",
    }


def test_stream_and_persist_uses_error_text_when_no_tokens_streamed(monkeypatch, in_memory_db):
    thread_id = _make_thread()

    class _ExplodingGraph(_ScriptedAsyncGraph):
        async def astream_events(
            self, payload, *, config=None, version="v2", control=None
        ):
            raise RuntimeError("LLM 402")
            yield  # pragma: no cover

    scripted = _ExplodingGraph(events=[])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    assert "event: error" in joined

    with database.SessionLocal() as session:
        msgs = session.query(AgentMessage).filter(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == "assistant",
        ).all()
    assert len(msgs) == 1
    assert msgs[0].meta["agent_phase"] == "error"
    assert msgs[0].content == "LLM 402"


def test_stream_and_persist_marks_graph_drain_as_drained(monkeypatch, in_memory_db):
    from langgraph.errors import GraphDrained

    thread_id = _make_thread()

    class _DrainedGraph(_ScriptedAsyncGraph):
        async def astream_events(
            self, payload, *, config=None, version="v2", control=None
        ):
            self.last_stream_version = version
            self.last_stream_control = control
            raise GraphDrained("client disconnect")
            yield  # pragma: no cover

    scripted = _DrainedGraph(events=[])
    service = _stub_agent_service(monkeypatch, scripted)

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

    joined = "".join(asyncio.run(run()))
    assert "event: error" not in joined

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(
                AgentMessage.thread_id == thread_id,
                AgentMessage.role == "assistant",
            )
            .one()
        )
    assert msg.meta["agent_phase"] == "drained"
    assert msg.meta["drained"] is True
    assert msg.meta["drain_reason"] == "client disconnect"


def test_stream_and_persist_falls_back_to_final_state_message_when_no_tokens(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(
        events=[],
        messages=[AIMessage(content="hello from final state")],
    )
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    assert "event: done" in joined

    with database.SessionLocal() as session:
        msg = session.query(AgentMessage).filter(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == "assistant",
        ).one()
    assert msg.meta["agent_phase"] == "completed"
    assert msg.content == "hello from final state"


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
            # Close the unawaited coroutine to suppress RuntimeWarning
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", patched_wait_for)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi", requested_character="auto", page_context=None
        )]

    joined = "".join(asyncio.run(run()))
    assert "event: heartbeat" in joined


def test_stream_and_persist_persists_on_client_disconnect(monkeypatch, in_memory_db):
    """Simulate FastAPI cancelling the generator mid-stream (client disconnect).

    Persistence MUST still happen; no RuntimeError should propagate.
    """
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event("on_chat_model_stream", chunk_text="partial "),
        _stream_event("on_chat_model_stream", chunk_text="answer"),
    ])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        gen = service.stream_and_persist(
            thread_id=thread_id, content="hi",
            requested_character="auto", page_context=None,
        )
        # Pull the first event, then close (simulates client disconnect).
        first = await gen.__anext__()
        await gen.aclose()
        return first

    first = asyncio.run(run())
    # First event should be a token (we got at least partial text).
    assert "event: token" in first

    # Persistence ran in the cancellation `finally` block.
    from app import database
    with database.SessionLocal() as session:
        msgs = session.query(AgentMessage).filter(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == "assistant",
        ).all()
    assert len(msgs) == 1


def test_tool_error_flows_to_wire_and_persistence(monkeypatch, in_memory_db):
    thread_id = _make_thread()
    scripted = _ScriptedAsyncGraph(events=[
        _stream_event("on_tool_start", run_id="r1", name="run_batch_pricing", input={}),
        _stream_event("on_tool_end", run_id="r1", error="portfolio not found"),
    ])
    service = _stub_agent_service(monkeypatch, scripted)

    async def run():
        return [c async for c in service.stream_and_persist(
            thread_id=thread_id, content="hi",
            requested_character="auto", page_context=None,
        )]

    joined = "".join(asyncio.run(run()))
    assert '"error": "portfolio not found"' in joined

    from app import database
    with database.SessionLocal() as session:
        msg = session.query(AgentMessage).filter(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == "assistant",
        ).one()
    assert msg.meta["process_events"][0]["status"] == "error"
    assert msg.meta["process_events"][0]["error"] == "portfolio not found"
    assert msg.meta["agent_phase"] == "completed_with_tool_errors"

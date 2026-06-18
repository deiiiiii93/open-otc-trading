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
from _scripted_graph import _ScriptedAsyncGraph, _interrupt, _stream_event


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


def test_stream_and_persist_writes_pending_actions_on_interrupt(monkeypatch, in_memory_db):
    with database.SessionLocal() as session:
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
            _interrupt("intr-1", "run_batch_pricing", {"portfolio_id": 7}),
        ],
    )

    monkeypatch.setattr(agents_module, "build_agent_model", lambda settings: MagicMock())
    monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: scripted)
    monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
    service = AgentService()

    async def run():
        return [
            c
            async for c in service.stream_and_persist(
                thread_id=thread_id,
                content="run risk on portfolio 7",
                requested_character="auto",
                page_context=None,
            )
        ]

    asyncio.run(run())

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .one()
        )

    assert msg.meta["agent_phase"] == "awaiting_confirmation"
    pending = msg.meta["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "run_batch_pricing"
    assert pending[0]["payload"] == {"portfolio_id": 7}
    assert msg.content == "Awaiting your approval to run risk."

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
    collector.interrupts = [_interrupt("intr-1", "run_batch_pricing", {"portfolio_id": 7})]
    service._persist_from_collector(
        thread_id, collector, assets=[], page_context=None,
        model_selection=None, accounting_date=date.today(),
    )
    meta = _persisted_meta(thread_id)
    assert meta["reply_options"] == [{"label": "Yes"}, {"label": "No"}]
    assert meta["agent_phase"] == "awaiting_confirmation"

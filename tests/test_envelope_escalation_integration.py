"""End-to-end envelope escalation through the REAL orchestrator + persona subagent.

This file is intentionally exempt from conftest's ``_bypass_capability_gate``
autouse fixture (see ``_GATE_TEST_FILES``) so the real capability gate runs.
That matters: domain tools execute inside a persona subagent invoked
imperatively by the ``task`` tool, so a denial there never surfaces as a parent
``astream_events`` event. Escalation must therefore be driven by the configurable
signal sink — which is the channel this test exercises.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from app import database
from app.config import Settings, configure_settings
from app.database import configure_database, init_db
from app.models import AgentThread
from app.services import agents as agents_module
from app.services.agents import AgentService, select_deep_agent_tools
from app.services.deep_agent.orchestrator import build_orchestrator as _real_build_orchestrator
from app.services.deep_agent.hitl import interrupt_on_config


@pytest.fixture
def in_memory_db(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        # Production async checkpointer (AsyncSqliteSaver) — NOT InMemorySaver —
        # so this test faithfully reproduces how `configurable` is forwarded into
        # subagent checkpoint namespaces. The sink-by-reference channel must hold
        # there, which is exactly what a review flagged as the risky assumption.
        agent_checkpoint_db_path=":memory:",
        feature_workflow_routing=False,
    )
    configure_settings(settings)
    configure_database(settings)
    init_db()
    try:
        yield
    finally:
        configure_settings(None)


class _SeqModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _make_thread() -> int:
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.commit()
        return thread.id


def test_real_subagent_domain_read_denial_escalates_pet_page_to_diagnostic(
    monkeypatch, in_memory_db
):
    """A DOMAIN_READ tool denied inside the trader subagent under pet_page must
    auto-widen to pet_diagnostic via the configurable signal sink."""
    thread_id = _make_thread()
    responses = [
        # orchestrator -> dispatch to trader
        AIMessage(content="", tool_calls=[{
            "name": "task",
            "args": {"subagent_type": "trader", "description": "KO proximity portfolio 4"},
            "id": "t1", "type": "tool_call",
        }]),
        # trader -> gated DOMAIN_READ tool (denied under pet_page by the REAL gate)
        AIMessage(content="", tool_calls=[{
            "name": "query_snowball_ko_from_spot",
            "args": {"portfolio_id": 4},
            "id": "q1", "type": "tool_call",
        }]),
        # Widened retry under pet_diagnostic — the first-pass denial is
        # control-flow now, so blocker prose should not be consumed first.
        AIMessage(content="Here is the KO proximity table."),
    ] + [AIMessage(content=f"x{i}") for i in range(8)]
    model = _SeqModel(responses=responses)

    def _fake_build_orchestrator(**kwargs):
        # Build the REAL orchestrator with the scripted model and whatever
        # checkpointer the caller supplies — at stream time that is the real
        # AsyncSqliteSaver from build_async_checkpointer (left un-patched).
        return _real_build_orchestrator(
            model=model,
            tools=select_deep_agent_tools(),
            checkpointer=kwargs.get("checkpointer"),
            interrupt_on=kwargs.get("interrupt_on") or interrupt_on_config(),
        )

    monkeypatch.setattr(agents_module, "build_agent_model", lambda *a, **k: model)
    monkeypatch.setattr(agents_module, "build_orchestrator", _fake_build_orchestrator)
    service = AgentService()

    async def run():
        return [
            c
            async for c in service.stream_and_persist(
                thread_id=thread_id,
                content="KO proximity for portfolio 4",
                requested_character="auto",
                page_context=None,
                envelope="pet_page",
            )
        ]

    blob = "".join(asyncio.run(run()))

    assert "envelope_transitioned" in blob, blob[-800:]
    assert "pet_diagnostic" in blob

    # The persisted final message must reflect ONLY the post-escalation answer.
    # The first-pass refusal prose ("blocked…") the model produced under the
    # narrow envelope is a dead end and must not leak into the user-visible
    # content after a successful widen+retry.
    from app.models import AgentMessage

    with database.SessionLocal() as session:
        msg = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .order_by(AgentMessage.id.desc())
            .first()
        )
    assert msg is not None
    assert "KO proximity table" in msg.content, msg.content
    assert "blocked" not in msg.content.lower(), msg.content

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as app_main_module
from app import database
from app.config import Settings
from app.main import create_app
from app.models import (
    AuditEvent,
    Portfolio,
    Position,
    PositionBarrierState,
    PositionValuationResult,
    PositionValuationRun,
    RFQ,
    RFQQuoteVersion,
    RiskRun,
    SnowballTerm,
    TaskRun,
)
from app.services.deep_agent.channel_registry import (
    ChannelDescriptor,
    ChannelRegistry,
    ModelDescriptor,
)
from test_position_import_pricing import (
    vanilla_row,
    write_market_workbook,
    write_trade_workbook,
)

from _scripted_graph import _ScriptedGraph, _ai, _interrupt, _task_call


def _test_registry() -> ChannelRegistry:
    zenmux = ChannelDescriptor(
        name="zenmux",
        label="Zenmux",
        type="zenmux",
        api_key=None,
        base_url="https://zenmux.test/api/v1",
        anthropic_base_url="https://zenmux.test/api/anthropic",
        healthy=False,
        models=(
            ModelDescriptor(
                id="anthropic/claude-sonnet-4-6",
                provider="anthropic",
                label="Claude Sonnet 4.6",
                tags=("tool-use", "reasoning"),
            ),
            ModelDescriptor(id="openai/gpt-5.4", provider="openai", label="GPT-5.4"),
        ),
    )
    deepseek = ChannelDescriptor(
        name="deepseek",
        label="DeepSeek",
        type="openai_compatible",
        api_key=None,
        base_url="https://api.deepseek.test",
        anthropic_base_url=None,
        healthy=False,
        models=(
            ModelDescriptor(
                id="deepseek-v4-flash",
                provider="deepseek",
                label="DeepSeek V4 Flash",
                tags=("fast",),
            ),
        ),
    )
    return ChannelRegistry(
        channels=(zenmux, deepseek),
        default=("zenmux", "anthropic", "anthropic/claude-sonnet-4-6"),
    )


def _install_test_agent_service(settings: Settings) -> None:
    service = app_main_module.agent_service
    service.settings = settings
    service.registry = _test_registry()
    service.default_model_selection = service.registry.default_selection()
    service.model = None
    service.deep_agent = None
    service.checkpointer = None
    service._owned_deep_agent = None


def make_client(tmp_path: Path) -> TestClient:
    channels_file = tmp_path / "agent_channels.yaml"
    channels_file.write_text(
        """
default:
  channel: zenmux
  model: anthropic/claude-sonnet-4-6

channels:
  - name: zenmux
    label: Zenmux
    type: zenmux
    api_key_env: TEST_ZENMUX_KEY
    base_url: https://zenmux.test/api/v1
    anthropic_base_url: https://zenmux.test/api/anthropic
    models:
      - id: anthropic/claude-sonnet-4-6
        provider: anthropic
        label: Claude Sonnet 4.6
        tags: [tool-use, reasoning]
      - id: openai/gpt-5.4
        provider: openai
        label: GPT-5.4
  - name: deepseek
    label: DeepSeek
    type: openai_compatible
    api_key_env: TEST_DEEPSEEK_KEY
    base_url: https://api.deepseek.test
    models:
      - id: deepseek-v4-flash
        provider: deepseek
        label: DeepSeek V4 Flash
        tags: [fast]
""",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_channels_file=channels_file,
        agent_checkpoint_db_path=str(tmp_path / "agent_checkpoints.sqlite"),
        feature_workflow_routing=False,
    )
    _install_test_agent_service(settings)
    return TestClient(
        create_app(settings, agent_service_override=app_main_module.agent_service)
    )


def wait_task(client: TestClient, task_id: int, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] in {"completed", "completed_with_errors", "failed"}:
            return task
        time.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not finish within {timeout} seconds")


def test_health_and_chat_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """With no LLM key configured, the agent returns the disabled-stub response."""
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", None)
    client = make_client(tmp_path)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    created = client.post(
        "/api/chat/threads",
        json={"title": "Morning desk", "character": "trader"},
    )
    assert created.status_code == 200
    thread_id = created.json()["id"]

    streamed = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={
            "content": "Summarize risk limits",
            "character": "auto",
            "accounting_date": "2026-05-11",
        },
    )
    assert streamed.status_code == 200
    assert "event: done" in streamed.text

    listed = client.get("/api/chat/threads")
    assert listed.status_code == 200
    messages = listed.json()[0]["messages"]
    assert messages
    user_message = next(m for m in messages if m["role"] == "user")
    assert user_message["meta"]["accounting_date"] == "2026-05-11"
    assistant_message = next(m for m in messages if m["role"] == "assistant")
    assert "Agent unavailable" in assistant_message["content"]
    assert assistant_message["meta"]["agent_graph"] == "disabled"
    assert assistant_message["meta"]["agent_phase"] == "completed"


def test_thread_management_rename_export_fork_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", None)
    client = make_client(tmp_path)

    created = client.post(
        "/api/chat/threads",
        json={"title": "Morning desk", "character": "trader"},
    )
    assert created.status_code == 200
    thread_id = created.json()["id"]

    streamed = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "Summarize risk limits", "character": "auto"},
    )
    assert streamed.status_code == 200

    renamed = client.patch(
        f"/api/chat/threads/{thread_id}",
        json={"title": "Renamed desk"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed desk"

    exported = client.get(f"/api/chat/threads/{thread_id}/export")
    assert exported.status_code == 200
    assert (
        exported.headers["content-disposition"]
        == f'attachment; filename="agent-thread-{thread_id}.json"'
    )
    exported_json = exported.json()
    assert exported_json["title"] == "Renamed desk"
    assert exported_json["exported_at"]
    assert len(exported_json["messages"]) == 2

    forked = client.post(f"/api/chat/threads/{thread_id}/fork", json={})
    assert forked.status_code == 200
    forked_json = forked.json()
    assert forked_json["title"] == "Fork of Renamed desk"
    assert forked_json["character"] == "trader"
    assert [m["content"] for m in forked_json["messages"]] == [
        m["content"] for m in exported_json["messages"]
    ]

    checkpoint_path = tmp_path / "agent_checkpoints.sqlite"
    with sqlite3.connect(checkpoint_path) as conn:
        conn.execute("create table checkpoints (thread_id text)")
        conn.execute("create table writes (thread_id text)")
        conn.execute("insert into checkpoints (thread_id) values (?)", (str(thread_id),))
        conn.execute("insert into writes (thread_id) values (?)", (str(thread_id),))
        conn.execute("insert into checkpoints (thread_id) values (?)", (str(forked_json["id"]),))
        conn.execute("insert into writes (thread_id) values (?)", (str(forked_json["id"]),))

    deleted = client.delete(f"/api/chat/threads/{thread_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "deleted_id": thread_id}

    with sqlite3.connect(checkpoint_path) as conn:
        assert conn.execute(
            "select count(*) from checkpoints where thread_id = ?",
            (str(thread_id),),
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from writes where thread_id = ?",
            (str(thread_id),),
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from checkpoints where thread_id = ?",
            (str(forked_json["id"]),),
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from writes where thread_id = ?",
            (str(forked_json["id"]),),
        ).fetchone()[0] == 1

    listed = client.get("/api/chat/threads")
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()] == [forked_json["id"]]


def test_delete_thread_removes_workflow_sessions_and_checkpoint_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.models import (
        AgentSession,
        AgentTask,
        AgentThread,
        ArtifactEvidenceRef,
        ContextPack,
        ContextPackPayload,
        DomainEvent,
        SessionArtifact,
        Workflow,
    )

    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", None)
    client = make_client(tmp_path)
    checkpoint_path = tmp_path / "agent_checkpoints.sqlite"

    with database.SessionLocal() as session:
        thread = AgentThread(title="delete runtime", character="trader")
        session.add(thread)
        session.flush()
        workflow = Workflow(
            thread_id=thread.id,
            title="delete runtime",
            intent="ad_hoc",
            status="active",
            opened_by="system",
            canonical_snapshot_ids={"scope_kind": "ad_hoc"},
        )
        session.add(workflow)
        session.flush()
        agent_session = AgentSession(
            workflow_id=workflow.id,
            persona="orchestrator",
            episode_id=1,
            status="active",
            checkpointer_key="workflow:delete:orchestrator",
        )
        session.add(agent_session)
        payload = ContextPackPayload(
            content_hash="sha256:delete-thread-runtime",
            stable_payload={"ok": True},
        )
        session.add(payload)
        session.flush()
        context_pack = ContextPack(
            workflow_id=workflow.id,
            payload_id=payload.id,
            metadata_={},
        )
        session.add(context_pack)
        session.flush()
        task = AgentTask(
            workflow_id=workflow.id,
            task_type="analysis",
            inputs={},
            depends_on=[],
            assigned_persona="trader",
            assigned_session_id=agent_session.id,
            context_pack_id=context_pack.id,
            status="planned",
        )
        session.add(task)
        session.flush()
        artifact = SessionArtifact(
            workflow_id=workflow.id,
            session_id=agent_session.id,
            task_id=task.id,
            kind="tool_result",
            title="Tool result",
            payload={"ok": True},
            context_pack_id=context_pack.id,
        )
        session.add(artifact)
        session.flush()
        session.add(
            ArtifactEvidenceRef(
                artifact_id=artifact.id,
                evidence_kind="tool",
                evidence_payload={"ok": True},
            )
        )
        session.add(
            DomainEvent(
                workflow_id=workflow.id,
                session_id=agent_session.id,
                task_id=task.id,
                artifact_id=artifact.id,
                kind="artifact_created",
                payload={"ok": True},
                actor="system",
            )
        )
        agent_session.current_task_id = task.id
        thread.active_workflow_id = workflow.id
        session.commit()
        thread_id = thread.id
        workflow_id = workflow.id
        session_id = agent_session.id
        task_id = task.id
        artifact_id = artifact.id

    checkpoint_keys = {
        str(thread_id),
        f"thread:{thread_id}:router",
        "workflow:delete:orchestrator",
    }
    with sqlite3.connect(checkpoint_path) as conn:
        conn.execute("create table checkpoints (thread_id text)")
        conn.execute("create table writes (thread_id text)")
        for key in checkpoint_keys:
            conn.execute("insert into checkpoints (thread_id) values (?)", (key,))
            conn.execute("insert into writes (thread_id) values (?)", (key,))

    deleted = client.delete(f"/api/chat/threads/{thread_id}")
    assert deleted.status_code == 200

    with database.SessionLocal() as session:
        assert session.get(AgentThread, thread_id) is None
        assert session.get(Workflow, workflow_id) is None
        assert session.get(AgentSession, session_id) is None
        assert session.get(AgentTask, task_id) is None
        assert session.get(SessionArtifact, artifact_id) is None
        assert (
            session.query(DomainEvent)
            .filter(DomainEvent.workflow_id == workflow_id)
            .count()
            == 0
        )

    with sqlite3.connect(checkpoint_path) as conn:
        for key in checkpoint_keys:
            assert conn.execute(
                "select count(*) from checkpoints where thread_id = ?",
                (key,),
            ).fetchone()[0] == 0
            assert conn.execute(
                "select count(*) from writes where thread_id = ?",
                (key,),
            ).fetchone()[0] == 0


def test_confirm_returns_503_when_agent_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With no LLM configured, /confirm returns 503 (agent disabled guard)."""
    from app.models import AgentMessage, AgentThread

    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", None)
    client = make_client(tmp_path)
    with database.SessionLocal() as session:
        thread = AgentThread(title="T", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="confirm?",
            meta={
                "model_selection": {
                    "channel": "zenmux",
                    "provider": "anthropic",
                    "model": "anthropic/claude-sonnet-4-6",
                },
                "pending_actions": [
                    {
                        "id": "act-disabled",
                        "tool_name": "calculate_risk",
                        "label": "Run risk",
                        "summary": "risk",
                        "payload": {},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id
    resp = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-disabled/confirm"
    )
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


def test_dismiss_returns_503_when_agent_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With no LLM configured, /dismiss returns 503 (agent disabled guard)."""
    from app.models import AgentMessage, AgentThread

    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", None)
    client = make_client(tmp_path)
    with database.SessionLocal() as session:
        thread = AgentThread(title="T", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="confirm?",
            meta={
                "model_selection": {
                    "channel": "zenmux",
                    "provider": "anthropic",
                    "model": "anthropic/claude-sonnet-4-6",
                },
                "pending_actions": [
                    {
                        "id": "act-disabled",
                        "tool_name": "calculate_risk",
                        "label": "Run risk",
                        "summary": "risk",
                        "payload": {},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id
    resp = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-disabled/dismiss"
    )
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


def test_chat_pricing_prompt_references_position_without_page_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Agent proposes run_batch_pricing when user names a position in chat.

    The scripted graph emits an interrupt for run_batch_pricing; the test
    verifies the HTTP layer surfaces that interrupt as a pending action with
    tool_name == 'run_batch_pricing' and the expected args.
    """
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    client = make_client(tmp_path)

    # Seed DB after create_app has called configure_database
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Agent Reference Book", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying="000852.SH",
            product_type="EuropeanVanillaOption",
            product_kwargs={
                "strike": 100.0,
                "option_type": "CALL",
                "contract_multiplier": 1.0,
            },
            engine_name="BlackScholesEngine",
            engine_kwargs={},
            quantity=1.0,
            entry_price=0.0,
            source_trade_id="SSGK48",
            mapping_status="supported",
        )
        session.add(position)
        session.commit()
        portfolio_id = portfolio.id
        position_id = position.id

    # Wire a scripted graph that pauses on run_batch_pricing with the right args
    graph = _ScriptedGraph(
        [
            {
                "messages": [
                    _ai("Calling trader.", tool_calls=[_task_call("trader")]),
                    _ai("I will price position SSGK48."),
                ],
                "__interrupt__": [
                    _interrupt(
                        "intr-price-1",
                        "run_batch_pricing",
                        {
                            "portfolio_id": portfolio_id,
                            "position_ids": [position_id],
                            "pricing_parameter_profile_id": 5,
                        },
                        description="Batch-price position SSGK48 with pricing profile 5",
                    ),
                ],
            },
        ]
    )
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", graph)

    thread_id = client.post(
        "/api/chat/threads", json={"title": "Desk chat", "character": "trader"}
    ).json()["id"]
    streamed = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "reprice SSGK48 with pricing profile 5", "character": "auto"},
    )

    assert streamed.status_code == 200
    assert "event: done" in streamed.text

    thread = next(
        item
        for item in client.get("/api/chat/threads").json()
        if item["id"] == thread_id
    )
    assistant_message = thread["messages"][-1]
    assert assistant_message["role"] == "assistant"
    action = assistant_message["meta"]["pending_actions"][0]
    assert action["tool_name"] == "run_batch_pricing"
    assert action["payload"]["portfolio_id"] == portfolio_id
    assert action["payload"]["position_ids"] == [position_id]
    assert action["payload"]["pricing_parameter_profile_id"] == 5
    assert assistant_message["meta"]["agent_phase"] == "awaiting_confirmation"


def test_floating_agent_context_persists_and_confirms_pricing_and_risk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """End-to-end HITL flow: page_context preserved → run_batch_pricing pause →
    confirm → completed; then run_batch_pricing pause → confirm → completed.

    Uses a scripted graph so no real LLM is needed. Assertions focus on the
    HITL mechanics (phase transitions, action status updates, duplicate-confirm
    guard) rather than the actual computed values.
    """
    client = make_client(tmp_path)

    trade_path = tmp_path / "trades.xlsx"
    market_path = tmp_path / "market.xlsx"
    write_trade_workbook(trade_path, [vanilla_row()])
    write_market_workbook(market_path, ["T-VANILLA"], spot=101.0)

    portfolio = client.post(
        "/api/portfolios", json={"name": "Agent Book", "base_currency": "CNY"}
    ).json()
    with trade_path.open("rb") as handle:
        client.post(
            f"/api/portfolios/{portfolio['id']}/positions/import",
            files={
                "file": (
                    "trades.xlsx",
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"sheet_name": "Positions"},
        )
    portfolio = next(
        book
        for book in client.get("/api/portfolios").json()
        if book["name"] == "Agent Book"
    )
    position = portfolio["positions"][0]

    page_context = {
        "route": "portfolio",
        "title": "Position Management",
        "path": "/",
        "entity_ids": {
            "portfolio_id": portfolio["id"],
            "selected_position_id": position["id"],
        },
        "snapshot": {
            "portfolio": {"id": portfolio["id"], "name": portfolio["name"]},
            "selected_position": position,
            "pricing_overrides": {
                "spot": "",
                "rate": "0.02",
                "dividend_yield": "0.03",
                "volatility": "",
            },
        },
        "chips": [
            f"Book #{portfolio['id']}",
            f"Position #{position['id']}",
            "T-VANILLA",
        ],
    }

    # Script: pause on run_batch_pricing, then complete; pause on run_batch_pricing, then complete
    graph = _ScriptedGraph(
        [
            # Turn 1: user asks to price — pause on run_batch_pricing
            {
                "messages": [
                    _ai("Calling trader.", tool_calls=[_task_call("trader")]),
                    _ai("I will batch-price this position with profile 7."),
                ],
                "__interrupt__": [
                    _interrupt(
                        "intr-price-1",
                        "run_batch_pricing",
                        {
                            "portfolio_id": portfolio["id"],
                            "position_ids": [position["id"]],
                            "pricing_parameter_profile_id": 7,
                        },
                        description="Batch-price T-VANILLA with pricing profile 7",
                    ),
                ],
            },
            # Turn 1 resume: complete after confirm
            {
                "messages": [
                    _ai("Pricing complete. T-VANILLA priced successfully."),
                ],
            },
            # Turn 2: user asks for risk — pause on run_batch_pricing
            {
                "messages": [
                    _ai(
                        "Calling risk_manager.", tool_calls=[_task_call("risk_manager")]
                    ),
                    _ai("About to run risk analysis."),
                ],
                "__interrupt__": [
                    _interrupt(
                        "intr-risk-1",
                        "run_batch_pricing",
                        {"portfolio_id": portfolio["id"], "method": "summary"},
                        description="Run risk for Agent Book",
                    ),
                ],
            },
            # Turn 2 resume: complete after confirm
            {
                "messages": [
                    _ai("Risk analysis complete. VaR metrics computed."),
                ],
            },
        ]
    )
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", graph)

    thread_id = client.post(
        "/api/chat/threads",
        json={"title": "Floating page agent", "character": "trader"},
    ).json()["id"]

    # --- Turn 1: run_batch_pricing pause ---
    streamed = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={
            "content": "reprice this with pricing profile 7",
            "character": "auto",
            "page_context": page_context,
        },
    )
    assert streamed.status_code == 200

    thread_messages = next(
        item
        for item in client.get("/api/chat/threads").json()
        if item["id"] == thread_id
    )["messages"]
    user_message = next(m for m in thread_messages if m["role"] == "user")
    assistant_message = next(m for m in thread_messages if m["role"] == "assistant")

    # page_context should be stored on user message
    assert user_message["meta"]["page_context"]["route"] == "portfolio"
    # json asset from page_context should appear
    assert any(asset["kind"] == "json" for asset in assistant_message["meta"]["assets"])

    action = assistant_message["meta"]["pending_actions"][0]
    assert action["tool_name"] == "run_batch_pricing"
    assert action["payload"]["portfolio_id"] == portfolio["id"]
    assert action["payload"]["position_ids"] == [position["id"]]
    assert action["payload"]["pricing_parameter_profile_id"] == 7
    assert assistant_message["meta"]["agent_phase"] == "awaiting_confirmation"

    # --- Confirm run_batch_pricing ---
    confirmed = client.post(
        f"/api/chat/threads/{thread_id}/messages/{assistant_message['id']}/actions/{action['id']}/confirm"
    )
    assert confirmed.status_code == 200
    confirmed_message = confirmed.json()
    assert confirmed_message["meta"]["agent_phase"] == "completed"
    assert "pricing complete" in confirmed_message["content"].lower()

    # Pending action on original message should now be "confirmed"
    refreshed = next(
        item
        for item in client.get("/api/chat/threads").json()
        if item["id"] == thread_id
    )["messages"]
    refreshed_source = next(m for m in refreshed if m["id"] == assistant_message["id"])
    assert refreshed_source["meta"]["pending_actions"][0]["status"] == "confirmed"

    # Duplicate confirm must return 409
    duplicate_confirm = client.post(
        f"/api/chat/threads/{thread_id}/messages/{assistant_message['id']}/actions/{action['id']}/confirm"
    )
    assert duplicate_confirm.status_code == 409

    # --- Turn 2: run_batch_pricing pause ---
    risk_context = {**page_context, "route": "risk", "title": "Risk Dashboard"}
    client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={
            "content": "run risk analysis for this book",
            "character": "auto",
            "page_context": risk_context,
        },
    )
    thread_messages2 = next(
        item
        for item in client.get("/api/chat/threads").json()
        if item["id"] == thread_id
    )["messages"]
    risk_proposal = thread_messages2[-1]
    risk_action = risk_proposal["meta"]["pending_actions"][0]
    assert risk_action["tool_name"] == "run_batch_pricing"
    assert risk_proposal["meta"]["agent_phase"] == "awaiting_confirmation"

    # --- Confirm run_batch_pricing ---
    risk_confirmed = client.post(
        f"/api/chat/threads/{thread_id}/messages/{risk_proposal['id']}/actions/{risk_action['id']}/confirm"
    )
    assert risk_confirmed.status_code == 200
    assert risk_confirmed.json()["meta"]["agent_phase"] == "completed"


def test_portfolio_runs_sanitize_stale_implausible_valuations(tmp_path: Path):
    client = make_client(tmp_path)
    session = database.SessionLocal()
    portfolio = Portfolio(name="Stale Pricing Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="000852.SH",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "contract_multiplier": 10_000.0,
        },
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=-1.0,
        entry_price=0.0,
        source_trade_id="OTC-GFQJZX15-20260129-OPTION-01",
        mapping_status="supported",
    )
    session.add(position)
    session.flush()
    run = PositionValuationRun(
        portfolio_id=portfolio.id,
        summary={
            "positions": 1,
            "priced": 1,
            "failed": 0,
            "market_value": -1.573839927106165e253,
            "pnl": 0.0,
        },
        status="completed",
    )
    session.add(run)
    session.flush()
    session.add(
        PositionValuationResult(
            valuation_run_id=run.id,
            position_id=position.id,
            source_trade_id=position.source_trade_id,
            ok=True,
            price=1.573839927106165e253,
            market_value=-1.573839927106165e253,
            pnl=-1.573839927106165e253,
            market_inputs={
                "spot": 100.0,
                "rate": 0.02,
                "dividend_yield": 0.0,
                "volatility": 0.2,
            },
            result_payload={"quantark_price": 1.573839927106165e253},
        )
    )
    portfolio_id = portfolio.id
    session.commit()
    session.close()

    response = client.get(f"/api/portfolios/{portfolio_id}/runs")

    assert response.status_code == 200
    run_payload = response.json()[0]
    result = run_payload["results"][0]
    assert run_payload["summary"]["priced"] == 0
    assert run_payload["summary"]["failed"] == 1
    assert run_payload["summary"]["market_value"] == 0.0
    assert result["ok"] is False
    assert result["price"] is None
    assert result["market_value"] is None
    assert result["pnl"] is None
    assert "implausible" in result["error"]
    assert result["result_payload"]["raw_market_value"] == -1.573839927106165e253


def test_delete_portfolio_removes_generated_run_history(tmp_path: Path):
    client = make_client(tmp_path)
    session = database.SessionLocal()
    portfolio = Portfolio(name="Delete With Runs", base_currency="USD")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="CSI500",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        mapping_status="supported",
    )
    session.add(position)
    session.flush()
    run = PositionValuationRun(
        portfolio_id=portfolio.id,
        summary={"positions": 1},
        status="completed",
        resolved_position_ids=[position.id],
    )
    session.add(run)
    session.flush()
    session.add(
        PositionValuationResult(
            valuation_run_id=run.id,
            position_id=position.id,
            ok=True,
            price=1.0,
            market_value=1.0,
            pnl=1.0,
        )
    )
    session.add(
        RiskRun(
            portfolio_id=portfolio.id,
            method="summary",
            status="completed",
            metrics={"totals": {}},
            resolved_position_ids=[position.id],
        )
    )
    portfolio_id = portfolio.id
    session.commit()
    session.close()

    response = client.delete(f"/api/portfolios/{portfolio_id}")

    assert response.status_code == 204
    session = database.SessionLocal()
    try:
        assert session.get(Portfolio, portfolio_id) is None
        assert (
            session.query(PositionValuationRun)
            .filter_by(portfolio_id=portfolio_id)
            .count()
            == 0
        )
        assert session.query(RiskRun).filter_by(portfolio_id=portfolio_id).count() == 0
    finally:
        session.close()


def test_floating_agent_report_assets_and_artifact_path_safety(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Agent proposes create_report, user confirms → completed; artifact
    path-traversal is blocked by the /api/artifacts guard.

    The scripted graph emits a create_report interrupt on the first invoke
    and a completion message on resume. The test verifies the full confirm
    lifecycle and that the path-traversal endpoint returns 403 or 404.
    """
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "Report Agent Book", "base_currency": "USD"}
    ).json()
    thread_id = client.post(
        "/api/chat/threads",
        json={"title": "Floating page agent", "character": "trader"},
    ).json()["id"]
    page_context = {
        "route": "reports",
        "title": "Report Batch Manager",
        "path": "/",
        "entity_ids": {"portfolio_id": portfolio["id"]},
        "snapshot": {"portfolio_count": 1},
        "chips": [f"Book #{portfolio['id']}", "Reports"],
    }

    graph = _ScriptedGraph(
        [
            # First invoke: pause on create_report
            {
                "messages": [
                    _ai("Calling trader.", tool_calls=[_task_call("trader")]),
                    _ai("I will generate a report for this book."),
                ],
                "__interrupt__": [
                    _interrupt(
                        "intr-report-1",
                        "create_report",
                        {"portfolio_id": portfolio["id"], "report_type": "portfolio"},
                        description="Generate portfolio report artifact",
                    ),
                ],
            },
            # Resume after confirm: complete
            {
                "messages": [
                    _ai("Report generated successfully."),
                ],
            },
        ]
    )
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", graph)

    client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={
            "content": "generate a report artifact for this book",
            "character": "auto",
            "page_context": page_context,
        },
    )
    thread_messages = client.get("/api/chat/threads").json()[0]["messages"]
    proposal = thread_messages[-1]
    action = proposal["meta"]["pending_actions"][0]
    assert action["tool_name"] == "create_report"
    assert proposal["meta"]["agent_phase"] == "awaiting_confirmation"

    confirmed = client.post(
        f"/api/chat/threads/{thread_id}/messages/{proposal['id']}/actions/{action['id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["meta"]["agent_phase"] == "completed"
    assert "report generated" in confirmed.json()["content"].lower()

    # Artifact path-traversal guard — must return 403 or 404 regardless of agent state
    traversal = client.get("/api/artifacts/%2E%2E/secret.txt")
    assert traversal.status_code in {403, 404}


def test_floating_agent_prompt_receives_full_page_context_and_usage_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "Full Context Book", "base_currency": "USD"}
    ).json()
    thread_id = client.post(
        "/api/chat/threads",
        json={"title": "Context audit", "character": "trader"},
    ).json()["id"]
    captured: dict[str, str] = {}

    def capture_prompt(payload, _config):
        captured["prompt"] = payload["messages"][0].content
        return {"messages": [_ai("Context received.")]}

    graph = _ScriptedGraph([capture_prompt])
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", graph)

    full_positions = [
        {"position_id": idx, "marker": f"risk-row-{idx}"}
        for idx in range(20)
    ]
    page_context = {
        "route": "risk",
        "title": "Risk Dashboard",
        "path": "/risk",
        "entity_ids": {"portfolio_id": portfolio["id"]},
        "snapshot": {
            "risk": {
                "totals": {"delta": 12.5},
                "positions": full_positions,
            },
            "deep_snapshot_marker": "full-context-marker",
        },
        "chips": ["Risk", f"Book #{portfolio['id']}"],
    }
    context_usage = {
        "bytes": 123456,
        "estimated_tokens": 30864,
        "chip_count": 2,
        "snapshot_key_count": 2,
        "entity_id_count": 1,
        "warning_level": "large",
        "computed_at": "2026-05-13T12:00:00Z",
    }

    streamed = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={
            "content": "use the full context",
            "character": "auto",
            "page_context": page_context,
            "context_usage": context_usage,
        },
    )
    assert streamed.status_code == 200

    prompt = captured["prompt"]
    # Prose brief replaces the JSON dump; verify the new contract:
    # 1) No raw JSON dump in the prompt (no IT-flavored noise).
    assert "Lightweight context JSON" not in prompt
    assert "Current page context JSON:" not in prompt
    assert "deep_snapshot_marker" not in prompt, (
        "raw snapshot fields must NOT leak into the prose brief"
    )
    # 2) The brief frames the conversation around the page identity.
    assert "=== Conversation context ===" in prompt
    assert "=== User says ===" in prompt
    assert "Risk Dashboard" in prompt
    # 3) DB-hydrated risk totals are surfaced in the brief (server truth).
    assert "delta 12.5" in prompt
    # 4) Internal references list the portfolio_id so tools can be called.
    assert f"portfolio_id={portfolio['id']}" in prompt
    # 5) The full snapshot is STILL preserved in the message meta for audit
    # (asserted below via assistant_message["meta"]["context_used"]).

    messages = next(
        item
        for item in client.get("/api/chat/threads").json()
        if item["id"] == thread_id
    )["messages"]
    user_message = next(m for m in messages if m["role"] == "user")
    assistant_message = next(m for m in messages if m["role"] == "assistant")
    assert user_message["meta"]["context_usage"]["warning_level"] == "large"
    assert assistant_message["meta"]["context_used"]["snapshot"]["risk"]["positions"][19]["marker"] == "risk-row-19"
    assert assistant_message["meta"]["context_usage"]["estimated_tokens"] == 30864


def test_brief_says_no_portfolio_when_page_context_lacks_portfolio_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """When the page has no portfolio_id (e.g. Pricing Parameters), the brief
    must NOT invent one by falling back to query.first(). Otherwise the agent
    answers about the wrong portfolio and the clarify-first rule never triggers.
    """
    client = make_client(tmp_path)
    # Seed two portfolios so query.first() would have something to (wrongly) return.
    client.post(
        "/api/portfolios", json={"name": "Decoy A", "base_currency": "CNY"}
    ).raise_for_status()
    client.post(
        "/api/portfolios", json={"name": "Decoy B", "base_currency": "USD"}
    ).raise_for_status()
    thread_id = client.post(
        "/api/chat/threads", json={"title": "no-portfolio", "character": "trader"}
    ).json()["id"]

    captured: dict[str, str] = {}

    def capture_prompt(payload, _config):
        captured["prompt"] = payload["messages"][0].content
        return {"messages": [_ai("ack")]}

    graph = _ScriptedGraph([capture_prompt])
    monkeypatch.setattr(app_main_module.agent_service, "deep_agent", graph)

    # Pricing Parameters page: pricing_profile_id is set, portfolio_id is NOT.
    page_context = {
        "route": "pricing-parameters",
        "title": "Pricing Parameters",
        "path": "/",
        "entity_ids": {"pricing_profile_id": 1},
        "snapshot": {},
        "chips": ["global", "2026-04-30", "2911 rows"],
    }
    client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={
            "content": "How many underlyings do we have?",
            "character": "auto",
            "page_context": page_context,
        },
    )

    prompt = captured["prompt"]
    # The brief must not present any portfolio as "in view" — neither decoy.
    assert "Decoy A" not in prompt
    assert "Decoy B" not in prompt
    assert "Portfolio in view" not in prompt
    # It must explicitly tell the agent to ask first.
    assert "No portfolio is in view" in prompt
    assert "Ask the user" in prompt


def test_client_rfq_form_and_approval(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post(
        "/api/client/rfq/form",
        json={
            "client_name": "Client A",
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 1,
            },
            "engine_spec": {"engine_name": "BlackScholesEngine"},
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.05,
                "dividend_yield": 0.02,
                "asset_name": "CSI500",
            },
            "unknown": {
                "field_path": "strike",
                "lower_bound": 50,
                "upper_bound": 150,
                "initial_guess": 100,
            },
            "target": {"label": "price", "value": 10},
        },
    )
    assert response.status_code == 200
    rfq = response.json()
    assert rfq["status"] == "pending_approval"
    assert "client_response" in rfq["quote_payload"]

    approved = client.post(
        f"/api/internal/rfq/{rfq['id']}/approve",
        json={"approver": "trader", "comment": "looks good"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_response"]
    assert (
        "pending internal trader approval" not in approved.json()["approved_response"]
    )


def test_rfq_catalog_and_nl_draft_missing_fields(tmp_path: Path):
    client = make_client(tmp_path)

    catalog = client.get("/api/rfq/catalog")
    assert catalog.status_code == 200
    catalog_json = catalog.json()
    product_names = {item["name"] for item in catalog_json["product_types"]}
    assert "EuropeanVanillaOption" in product_names
    assert "SingleSharkfinOption" in product_names
    assert "DoubleSharkfinOption" in product_names
    template_keys = {item["key"] for item in catalog_json["templates"]}
    assert {"vanilla", "phoenix", "single_sharkfin", "double_sharkfin"} <= template_keys

    drafted = client.post(
        "/api/rfq/draft/from-nl",
        json={"client_name": "Client NL", "message": "Quote a CSI500 phoenix"},
    )
    assert drafted.status_code == 200
    body = drafted.json()
    assert body["draft"]["product_type"] == "PhoenixOption"
    assert "target" in body["missing_fields"]
    assert body["assumptions"]


def test_rfq_catalog_templates_carry_unknown_field_specs(tmp_path: Path):
    client = make_client(tmp_path)

    catalog = client.get("/api/rfq/catalog").json()
    assert catalog["templates"], "catalog should have templates"
    for template in catalog["templates"]:
        specs = template["unknown_field_specs"]
        assert [spec["field_path"] for spec in specs] == template["unknown_fields"]
        for spec in specs:
            assert spec["label"]
            assert spec["lower_bound"] < spec["upper_bound"]
            assert spec["lower_bound"] <= spec["initial_guess"] <= spec["upper_bound"]

    snowball = next(t for t in catalog["templates"] if t["key"] == "snowball")
    assert snowball["unknown_field_specs"] == [
        {
            "field_path": "barrier_config.ko_rate",
            "label": "KO Rate",
            "lower_bound": -1.0,
            "upper_bound": 2.0,
            "initial_guess": 0.15,
        }
    ]


def test_failed_rfq_pricing_cannot_be_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.services import rfq as rfq_service

    client = make_client(tmp_path)

    def fail_solve(_draft):
        return SimpleNamespace(
            ok=False,
            data={"status": "pricing_failed"},
            error="QuantArk engine unavailable",
        )

    monkeypatch.setattr(rfq_service, "quantark_solve_rfq", fail_solve)
    created = client.post(
        "/api/internal/rfq/draft",
        json={
            "client_name": "Client Fail",
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 1,
            },
            "engine_spec": {"engine_name": "BlackScholesEngine"},
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.05,
                "dividend_yield": 0.02,
                "asset_name": "CSI500",
            },
            "unknown": {
                "field_path": "strike",
                "lower_bound": 50,
                "upper_bound": 150,
                "initial_guess": 100,
            },
            "target": {"label": "price", "value": 10},
        },
    )
    assert created.status_code == 200
    rfq_id = created.json()["id"]

    quoted = client.post(f"/api/internal/rfq/{rfq_id}/quote", json={})
    assert quoted.status_code == 200
    quoted_json = quoted.json()
    assert quoted_json["status"] == "pricing_failed"
    assert quoted_json["quote_versions"][0]["status"] == "pricing_failed"
    assert quoted_json["quote_versions"][0]["error"] == "QuantArk engine unavailable"

    approved = client.post(
        f"/api/internal/rfq/{rfq_id}/approve",
        json={"approver": "trader", "comment": "should fail"},
    )
    assert approved.status_code == 400
    assert "pending approval" in approved.json()["detail"].lower()


def test_rfq_release_accept_and_booking_creates_position(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "RFQ Booking Book", "base_currency": "CNY"}
    ).json()

    response = client.post(
        "/api/client/rfq/form",
        json={
            "client_name": "Client Book",
            "underlying": "CSI500",
            "quantity": 3,
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 1,
            },
            "engine_spec": {"engine_name": "BlackScholesEngine"},
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.05,
                "dividend_yield": 0.02,
                "asset_name": "CSI500",
            },
            "unknown": {
                "field_path": "strike",
                "lower_bound": 50,
                "upper_bound": 150,
                "initial_guess": 100,
            },
            "target": {"label": "price", "value": 10},
        },
    )
    assert response.status_code == 200
    rfq = response.json()
    quote_version_id = rfq["quote_versions"][0]["id"]
    solved_strike = rfq["quote_versions"][0]["quote_payload"]["solved_value"]
    executable_terms = rfq["quote_versions"][0]["request_payload"]["executable_terms"]
    assert executable_terms["product_kwargs"]["strike"] == pytest.approx(solved_strike)

    approved = client.post(
        f"/api/internal/rfq/{rfq['id']}/approve",
        json={"approver": "trader", "comment": "approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    released = client.post(
        f"/api/internal/rfq/{rfq['id']}/release",
        json={"actor": "trader"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"

    accepted = client.post(
        f"/api/internal/rfq/{rfq['id']}/client-accept",
        json={"actor": "client", "comment": "accepted"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "client_accepted"

    booked = client.post(
        f"/api/internal/rfq/{rfq['id']}/book",
        json={"portfolio_id": portfolio["id"], "actor": "trader"},
    )
    assert booked.status_code == 200
    position = booked.json()
    assert position["portfolio_id"] == portfolio["id"]
    assert position["product_id"] is not None
    assert position["product"]["terms"]["strike"] == pytest.approx(solved_strike)
    assert position["rfq_id"] == rfq["id"]
    assert position["rfq_quote_version_id"] == quote_version_id
    assert position["source_trade_id"] == f"RFQ-{rfq['id']}-V1"
    assert position["mapping_status"] == "supported"
    assert position["mapping_error"] is None
    assert position["product_kwargs"]["strike"] == pytest.approx(solved_strike)
    assert position["source_payload"]["executable_terms"]["product_kwargs"]["strike"] == pytest.approx(solved_strike)

    priced = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={
            "position_ids": [position["id"]],
            "valuation_date": "2026-05-12T00:00:00",
            "spot": 100,
            "rate": 0.05,
            "dividend_yield": 0.02,
            "volatility": 0.2,
        },
    )
    assert priced.status_code == 200
    assert priced.json()["summary"]["priced"] == 1

    refreshed = client.get(f"/api/client/rfq/{rfq['id']}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "booked"


def test_repair_legacy_rfq_booked_position_applies_executable_terms(tmp_path: Path):
    from app.services import rfq as rfq_service

    make_client(tmp_path)
    terms = {
        "client_name": "Legacy RFQ",
        "underlying": "CSI500",
        "side": "buy",
        "quantity": 10,
        "quote_mode": "solve",
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {
            "strike": 100,
            "option_type": "CALL",
            "maturity": 1,
            "contract_multiplier": 1,
        },
        "engine_spec": {"engine_name": "BlackScholesEngine"},
        "market": {
            "spot": 100,
            "volatility": 0.2,
            "rate": 0.05,
            "dividend_yield": 0.02,
            "asset_name": "CSI500",
        },
        "unknown": {
            "field_path": "strike",
            "lower_bound": 50,
            "upper_bound": 150,
            "initial_guess": 100,
        },
        "target": {"label": "price", "value": 5},
    }
    quote_payload = {
        "status": "success",
        "field_path": "strike",
        "solved_value": 111.25,
        "achieved_price": 5,
        "quantark_ok": True,
    }
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Legacy RFQ Book", base_currency="CNY")
        rfq = RFQ(
            client_name="Legacy RFQ",
            channel="form",
            status="booked",
            request_payload=terms,
            quote_payload=quote_payload,
        )
        session.add_all([portfolio, rfq])
        session.flush()
        quote_version = RFQQuoteVersion(
            rfq_id=rfq.id,
            version=1,
            quote_mode="solve",
            status="released",
            request_payload={"terms": terms, "legacy": True},
            quote_payload=quote_payload,
            created_by="legacy",
        )
        session.add(quote_version)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying="CSI500",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100, "option_type": "CALL", "maturity": 1},
            engine_name="BlackScholesEngine",
            engine_kwargs={},
            quantity=10,
            entry_price=5,
            source_trade_id="RFQ-LEGACY-V1",
            mapping_status="manual",
            rfq_id=rfq.id,
            rfq_quote_version_id=quote_version.id,
        )
        session.add(position)
        session.commit()
        position_id = position.id

    with database.SessionLocal() as session:
        assert rfq_service.repair_legacy_rfq_booked_positions(session) == 1
        session.commit()

    with database.SessionLocal() as session:
        repaired = session.get(Position, position_id)
        assert repaired.mapping_status == "supported"
        assert repaired.mapping_error is None
        assert repaired.product_kwargs["strike"] == pytest.approx(111.25)
        assert repaired.source_payload["executable_terms"]["product_kwargs"]["strike"] == pytest.approx(111.25)
        repaired_version = session.get(RFQQuoteVersion, repaired.rfq_quote_version_id)
        assert repaired_version.request_payload["executable_terms"]["product_kwargs"]["strike"] == pytest.approx(111.25)


def test_add_position_creates_product_and_keeps_legacy_fields(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Book", "base_currency": "USD"},
    ).json()

    response = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "product": {
                "asset_class": "equity",
                "product_family": "spot",
                "quantark_class": "SpotInstrument",
                "underlying": "AAPL",
                "currency": "USD",
                "terms": {
                    "deltaone_type": "STOCK",
                    "instrument_code": "AAPL",
                    "initial_price": 180.0,
                },
            },
            "quantity": 10,
            "entry_price": 180.0,
            "engine_name": "DeltaOneEngine",
        },
    )

    assert response.status_code == 200
    body = response.json()
    row = body["positions"][0]
    assert row["product_id"] is not None
    assert row["underlying"] == "AAPL"
    assert row["product_type"] == "SpotInstrument"
    assert row["product_kwargs"]["deltaone_type"] == "STOCK"

    with database.SessionLocal() as session:
        events = session.query(AuditEvent).order_by(AuditEvent.id).all()
        assert [event.event_type for event in events if event.subject_type == "position"] == [
            "position.created"
        ]


def test_patch_position_legacy_payload_updates_product_and_clears_stale_terms(
    tmp_path: Path,
):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Patch Book", "base_currency": "USD"},
    ).json()
    created = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "underlying": "000300.SH",
            "product_type": "SnowballOption",
            "product_kwargs": {
                "initial_price": 100.0,
                "strike": 100.0,
                "maturity": 1.0,
                "barrier_config": {
                    "ki_barrier": 75.0,
                    "ko_barrier": 103.0,
                    "ko_rate": 0.08,
                    "ko_observation_schedule": {
                        "records": [
                            {
                                "observation_date": "2026-06-30",
                                "barrier": 103.0,
                                "return_rate": 0.08,
                                "is_rate_annualized": True,
                            }
                        ]
                    },
                    "ki_observation_schedule": {
                        "records": [
                            {
                                "observation_date": "2026-06-30",
                                "barrier": 75.0,
                            }
                        ]
                    },
                },
            },
            "quantity": 1.0,
            "engine_name": "SnowballQuadEngine",
        },
    ).json()["positions"][0]
    with database.SessionLocal() as session:
        assert session.get(SnowballTerm, created["id"]) is not None
        assert session.get(PositionBarrierState, created["id"]) is not None

    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={
            "underlying": "AAPL",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100.0,
                "option_type": "CALL",
                "maturity": 1.0,
            },
            "quantity": 1.0,
            "engine_name": "BlackScholesEngine",
        },
    )

    assert patched.status_code == 200
    row = patched.json()["positions"][0]
    assert row["product_type"] == "EuropeanVanillaOption"
    assert row["product"]["quantark_class"] == "EuropeanVanillaOption"
    with database.SessionLocal() as session:
        position = session.get(Position, created["id"])
        assert position is not None
        assert position.product is not None
        assert position.product.quantark_class == "EuropeanVanillaOption"
        assert session.get(SnowballTerm, created["id"]) is None
        assert session.get(PositionBarrierState, created["id"]) is None


def test_portfolio_risk_and_report(tmp_path: Path):
    client = make_client(tmp_path)

    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Demo Book", "base_currency": "USD"},
    ).json()

    updated = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 100,
            },
            "engine_name": "BlackScholesEngine",
            "quantity": 5,
            "entry_price": 8.0,
        },
    )
    assert updated.status_code == 200
    assert len(updated.json()["positions"]) == 1

    missing_latest = client.get(f"/api/portfolios/{portfolio['id']}/risk-runs/latest")
    assert missing_latest.status_code == 200
    assert missing_latest.json() is None

    risk = client.post(
        "/api/batch-pricing/runs",
        json={"portfolio_id": portfolio["id"]},
    )
    assert risk.status_code == 200
    queued_risk = risk.json()
    risk_run_id = queued_risk["id"]
    assert queued_risk["task_id"]
    assert queued_risk["status"] in {"queued", "running"}
    task = wait_task(client, queued_risk["task_id"])
    assert task["risk_run_id"] == risk_run_id
    assert task["status"] in {"completed", "completed_with_errors"}
    assert task["kind"] == "batch_pricing"
    assert task["result_payload"]["risk_run_id"] == risk_run_id
    # The SAME task also produced a valuation run for the Positions page.
    valuation_run_id = task["result_payload"]["valuation_run_id"]
    runs = client.get(f"/api/portfolios/{portfolio['id']}/runs")
    assert runs.status_code == 200
    assert any(run["id"] == valuation_run_id for run in runs.json())
    completed_risk = client.get(f"/api/risk/runs/{risk_run_id}")
    assert completed_risk.status_code == 200
    completed_json = completed_risk.json()
    assert completed_json["resolved_position_ids"] == [
        updated.json()["positions"][0]["id"]
    ]
    assert "one_day_var_proxy" in completed_json["metrics"]["totals"]
    totals = completed_json["metrics"]["totals"]
    for greek in ("delta", "gamma", "delta_cash", "gamma_cash", "vega", "theta", "rho"):
        assert greek in totals, f"missing {greek} in totals"
        assert isinstance(totals[greek], (int, float))

    latest = client.get(f"/api/portfolios/{portfolio['id']}/risk-runs/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == risk_run_id
    assert latest.json()["resolved_position_ids"] == [
        updated.json()["positions"][0]["id"]
    ]
    assert (
        latest.json()["metrics"]["totals"]["one_day_var_proxy"]
        == totals["one_day_var_proxy"]
    )
    assert latest.json()["scenario_cells"] is None

    scenario = client.post(
        "/api/risk/scenarios",
        json={"portfolio_id": portfolio["id"], "risk_run_id": risk_run_id},
    )
    assert scenario.status_code == 200
    latest_with_scenarios = client.get(
        f"/api/portfolios/{portfolio['id']}/risk-runs/latest"
    )
    assert latest_with_scenarios.status_code == 200
    assert latest_with_scenarios.json()["scenario_cells"] == scenario.json()["cells"]

    report = client.post(
        "/api/reports/jobs",
        json={
            "report_type": "portfolio",
            "portfolio_id": portfolio["id"],
            "pricing_parameter_profile_id": None,
            "title": "Demo Report",
        },
    )
    assert report.status_code == 200
    queued_report = report.json()
    assert queued_report["task_id"]
    report_task = wait_task(client, queued_report["task_id"])
    assert report_task["report_job_id"] == queued_report["id"]
    assert report_task["status"] in {"completed", "completed_with_errors"}
    completed_report = client.get(f"/api/reports/jobs/{queued_report['id']}")
    assert completed_report.status_code == 200
    assert completed_report.json()["request_payload"]["pricing_parameter_profile_id"] is None
    assert completed_report.json()["result_payload"]["pricing_parameter_profile"] is None
    paths = completed_report.json()["artifact_paths"]
    assert Path(paths["html"]).name.startswith("Demo_Report_")
    assert Path(paths["html"]).name.endswith(".html")
    assert Path(paths["excel"]).name.startswith("Demo_Report_")
    assert Path(paths["excel"]).name.endswith(".xlsx")
    assert Path(paths["html"]).exists()
    assert Path(paths["excel"]).exists()


def test_startup_recovery_marks_stale_tasks_failed(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'recover.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Recover", base_currency="USD")
        session.add(portfolio)
        session.flush()
        run = RiskRun(
            portfolio_id=portfolio.id,
            method="summary",
            status="running",
            metrics={},
        )
        session.add(run)
        session.flush()
        task = TaskRun(
            kind="risk_run",
            status="running",
            portfolio_id=portfolio.id,
            risk_run_id=run.id,
            progress_current=1,
            progress_total=10,
            message="Pricing",
        )
        session.add(task)
        session.commit()
        task_id = task.id
        run_id = run.id

    client = TestClient(create_app(settings))
    recovered_task = client.get(f"/api/tasks/{task_id}")
    assert recovered_task.status_code == 200
    assert recovered_task.json()["status"] == "failed"
    assert "interrupted" in recovered_task.json()["error"].lower()
    recovered_run = client.get(f"/api/risk/runs/{run_id}")
    assert recovered_run.status_code == 200
    assert recovered_run.json()["status"] == "failed"


def test_position_and_market_upload_then_batch_price(tmp_path: Path):
    client = make_client(tmp_path)
    trade_path = tmp_path / "trades.xlsx"
    write_trade_workbook(trade_path, [vanilla_row()])

    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Uploaded Book", "base_currency": "CNY"},
    ).json()

    with trade_path.open("rb") as handle:
        imported = client.post(
            f"/api/portfolios/{portfolio['id']}/positions/import",
            files={
                "file": (
                    "trades.xlsx",
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"sheet_name": "Positions"},
        )
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 1

    listed = client.get("/api/portfolios")
    assert listed.status_code == 200
    uploaded = next(book for book in listed.json() if book["id"] == portfolio["id"])
    assert uploaded["positions"][0]["source_trade_id"] == "T-VANILLA"

    # The standalone market-inputs import/list endpoints were folded
    # (instrument-unification T8); spot/r/q/vol now come from overrides here.
    priced = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={
            "valuation_date": "2026-04-30T00:00:00",
            "spot": 101.0,
            "r": 0.02,
            "q": 0.03,
            "vol": 0.25,
        },
    )
    assert priced.status_code == 200
    assert priced.json()["summary"]["priced"] == 1
    assert priced.json()["resolved_position_ids"] == [uploaded["positions"][0]["id"]]
    assert priced.json()["results"][0]["market_inputs"]["spot"] == 101.0

    queued_pricing = client.post(
        "/api/batch-pricing/runs",
        json={"portfolio_id": portfolio["id"]},
    )
    assert queued_pricing.status_code == 200
    queued_run = queued_pricing.json()
    assert queued_run["task_id"] is not None
    completed_task = wait_task(client, queued_run["task_id"])
    assert completed_task["kind"] == "batch_pricing"
    assert completed_task["status"] in {"completed", "completed_with_errors"}
    assert completed_task["portfolio_id"] == portfolio["id"]
    # Old endpoints are gone.
    assert client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price-task", json={}
    ).status_code in {404, 405}
    assert client.post(
        "/api/risk/runs", json={"portfolio_id": portfolio["id"]}
    ).status_code in {404, 405}
    runs = client.get(f"/api/portfolios/{portfolio['id']}/runs")
    assert runs.status_code == 200
    assert any(run["summary"].get("priced", 0) >= 1 for run in runs.json())

    position_id = uploaded["positions"][0]["id"]
    single_priced = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={
            "position_ids": [position_id],
            "valuation_date": "2026-04-30T00:00:00",
            "spot": 102.0,
            "rate": 0.02,
            "dividend_yield": 0.03,
            "volatility": 0.24,
            "engine_name": "BlackScholesEngine",
            "engine_kwargs": {
                "params_type": "engine_params",
                "params_kwargs": {"bump_size": 0.0002},
            },
        },
    )
    assert single_priced.status_code == 200
    single_payload = single_priced.json()
    assert single_payload["summary"]["priced"] == 1
    assert single_payload["overrides"]["engine_name"] == "BlackScholesEngine"
    assert (
        single_payload["overrides"]["engine_kwargs"]["params_kwargs"]["bump_size"]
        == 0.0002
    )
    assert single_payload["results"][0]["market_inputs"]["spot"] == 102.0

    relisted = client.get("/api/portfolios").json()
    persisted = next(book for book in relisted if book["id"] == portfolio["id"])[
        "positions"
    ][0]
    assert persisted["engine_kwargs"] == {}

    no_position = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={"engine_name": "BlackScholesEngine"},
    )
    assert no_position.status_code == 400

    multiple_positions = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={
            "position_ids": [position_id, position_id],
            "engine_name": "BlackScholesEngine",
        },
    )
    assert multiple_positions.status_code == 400


def test_pricing_parameter_profile_import_list_detail_and_pricing(tmp_path: Path):
    client = make_client(tmp_path)
    trade_path = tmp_path / "trades.xlsx"
    pricing_path = tmp_path / "pricing-parameters.xlsx"
    write_trade_workbook(trade_path, [vanilla_row()])
    write_market_workbook(pricing_path, ["T-VANILLA"], spot=102.0)

    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Pricing Profile Book", "base_currency": "CNY"},
    ).json()

    with trade_path.open("rb") as handle:
        imported = client.post(
            f"/api/portfolios/{portfolio['id']}/positions/import",
            files={
                "file": (
                    "trades.xlsx",
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"sheet_name": "Positions"},
        )
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 1

    with pricing_path.open("rb") as handle:
        profile_response = client.post(
            "/api/pricing-parameter-profiles/import",
            files={
                "file": (
                    "pricing-parameters.xlsx",
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"valuation_date": "2026-04-30", "name": "2026-04-30 Close"},
        )
    assert profile_response.status_code == 200
    profile = profile_response.json()
    assert profile["name"] == "2026-04-30 Close"
    assert profile["summary"]["row_count"] == 1
    assert profile["rows"][0]["source_trade_id"] == "T-VANILLA"
    # Spot left the row (split-write T8): the row carries r/q/vol + instrument_id;
    # the spot observation was recorded to the quote store.
    assert "spot" not in profile["rows"][0]
    assert profile["rows"][0]["instrument_id"] is not None
    assert profile["summary"]["quotes_emitted"] == 1

    listed = client.get("/api/pricing-parameter-profiles")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [profile["id"]]
    assert listed.json()[0]["rows"][0]["source_trade_id"] == "T-VANILLA"

    detail = client.get(f"/api/pricing-parameter-profiles/{profile['id']}")
    assert detail.status_code == 200
    assert "spot" not in detail.json()["rows"][0]

    priced = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={
            "valuation_date": "2026-04-30T00:00:00",
            "pricing_parameter_profile_id": profile["id"],
            "rate": 0.021,
        },
    )
    assert priced.status_code == 200
    payload = priced.json()
    assert payload["pricing_parameter_profile_id"] == profile["id"]
    assert payload["overrides"]["pricing_parameter_profile_id"] == profile["id"]
    assert payload["overrides"]["rate"] == 0.021
    result_market_inputs = payload["results"][0]["market_inputs"]
    assert result_market_inputs["spot"] == 102.0  # from the quote store (split-write)
    assert result_market_inputs["market_input_source"] == "pricing_parameter_profile"
    assert result_market_inputs["pricing_parameter_profile_id"] == profile["id"]
    assert result_market_inputs["pricing_parameter_row_id"] == profile["rows"][0]["id"]
    assert result_market_inputs["field_sources"]["spot"] == "market_quote"


def test_position_writes_reject_view_portfolios(tmp_path: Path):
    client = make_client(tmp_path)
    trade_path = tmp_path / "trades.xlsx"
    write_trade_workbook(trade_path, [vanilla_row()])

    view = client.post(
        "/api/portfolios",
        json={"name": "Snowball View", "kind": "view"},
    ).json()

    added = client.post(
        f"/api/portfolios/{view['id']}/positions",
        json={"underlying": "CSI500"},
    )
    assert added.status_code == 400
    assert "container portfolios" in added.json()["detail"]

    with trade_path.open("rb") as handle:
        imported = client.post(
            f"/api/portfolios/{view['id']}/positions/import",
            files={
                "file": (
                    "trades.xlsx",
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"sheet_name": "Positions"},
        )
    assert imported.status_code == 400
    assert "container portfolios" in imported.json()["detail"]


def test_manual_and_akshare_fallback_snapshots(tmp_path: Path):
    client = make_client(tmp_path)

    manual = client.post(
        "/api/market-data/snapshots/manual",
        json={
            "name": "Manual CSI500",
            "source": "manual",
            "symbol": "000905",
            "asset_class": "index",
            "data": {"spot": 5200, "volatility": 0.22, "rate": 0.02},
        },
    )
    assert manual.status_code == 200
    assert manual.json()["data"]["spot"] == 5200

    fallback = client.post(
        "/api/market-data/snapshots/akshare",
        json={
            "symbol": "000300",
            "asset_class": "index",
            "start_date": "1900-01-01",
            "end_date": "1900-01-05",
        },
    )
    assert fallback.status_code == 200
    assert fallback.json()["source"] == "akshare"
    assert "fallback" in fallback.json()["source_metadata"]


def test_akshare_market_data_profile_list_detail_and_spot_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = make_client(tmp_path)

    def fake_snapshot(payload):
        from app.schemas import MarketDataSnapshot

        return MarketDataSnapshot(
            name=payload.name or "000300 fake",
            source="akshare",
            symbol=payload.symbol,
            asset_class=payload.asset_class,
            data={
                "rows": [
                    {
                        "date": "2026-04-30",
                        "open": 100.0,
                        "high": 103.0,
                        "low": 99.0,
                        "close": 102.5,
                        "volume": 1000,
                    }
                ],
                "latest": {"date": "2026-04-30", "close": 102.5},
                "spot": 102.5,
            },
            source_metadata={"source_name": "fake", "fallback": False},
        )

    monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

    created = client.post(
        "/api/market-data/profiles/akshare",
        json={
            "symbol": "000300",
            "asset_class": "index",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "name": "CSI300 April",
            "adjust": "qfq",
        },
    )
    assert created.status_code == 200
    profile = created.json()
    assert profile["name"] == "CSI300 April"
    assert profile["data"]["spot"] == 102.5
    assert profile["source_metadata"]["fallback"] is False

    listed = client.get("/api/market-data/profiles")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == profile["id"]

    detail = client.get(f"/api/market-data/profiles/{profile['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["rows"][0]["close"] == 102.5


def test_akshare_bulk_market_data_profiles_fetch_all_position_underlyings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    client = make_client(tmp_path)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="All Underlyings", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        session.add_all(
            [
                Position(
                    portfolio_id=portfolio.id,
                    underlying="000300.SH",
                    product_type="EuropeanVanillaOption",
                    product_kwargs={"strike": 100.0, "option_type": "CALL"},
                    engine_name="BlackScholesEngine",
                    engine_kwargs={},
                    quantity=1.0,
                    entry_price=0.0,
                    source_trade_id="T-CSI300",
                    mapping_status="supported",
                ),
                Position(
                    portfolio_id=portfolio.id,
                    underlying="000852.SH",
                    product_type="EuropeanVanillaOption",
                    product_kwargs={"strike": 100.0, "option_type": "CALL"},
                    engine_name="BlackScholesEngine",
                    engine_kwargs={},
                    quantity=1.0,
                    entry_price=0.0,
                    source_trade_id="T-CSI1000",
                    mapping_status="supported",
                ),
                Position(
                    portfolio_id=portfolio.id,
                    underlying="000300.SH",
                    product_type="EuropeanVanillaOption",
                    product_kwargs={"strike": 100.0, "option_type": "CALL"},
                    engine_name="BlackScholesEngine",
                    engine_kwargs={},
                    quantity=1.0,
                    entry_price=0.0,
                    source_trade_id="T-DUP",
                    mapping_status="supported",
                ),
                Position(
                    portfolio_id=portfolio.id,
                    underlying="512800.SH",
                    product_type="EuropeanVanillaOption",
                    product_kwargs={"strike": 100.0, "option_type": "CALL"},
                    engine_name="BlackScholesEngine",
                    engine_kwargs={},
                    quantity=1.0,
                    entry_price=0.0,
                    source_trade_id="T-ETF",
                    mapping_status="supported",
                ),
                Position(
                    portfolio_id=portfolio.id,
                    underlying="AU9999.SGE",
                    product_type="EuropeanVanillaOption",
                    product_kwargs={"strike": 100.0, "option_type": "CALL"},
                    engine_name="BlackScholesEngine",
                    engine_kwargs={},
                    quantity=1.0,
                    entry_price=0.0,
                    source_trade_id="T-SGE",
                    mapping_status="supported",
                ),
            ]
        )
        session.commit()

    seen_payloads = []

    def fake_snapshot(payload):
        from app.schemas import MarketDataSnapshot

        seen_payloads.append(payload)
        spot = 100.0 + len(seen_payloads)
        return MarketDataSnapshot(
            name=payload.name or f"{payload.symbol} fake",
            source="akshare",
            symbol=payload.symbol,
            asset_class=payload.asset_class,
            data={
                "rows": [{"date": "2026-04-30", "close": spot}],
                "latest": {"date": "2026-04-30", "close": spot},
                "spot": spot,
            },
            source_metadata={"source_name": "fake", "fallback": False},
        )

    monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

    created = client.post(
        "/api/market-data/profiles/akshare/bulk",
        json={
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "name": "EOM April",
            "adjust": "qfq",
        },
    )

    assert created.status_code == 200
    profiles = created.json()
    assert {profile["symbol"] for profile in profiles} == {
        "000300.SH",
        "000852.SH",
        "512800.SH",
        "AU9999.SGE",
    }
    assert [payload.symbol for payload in seen_payloads] == [
        "000300",
        "000852",
        "512800",
        "AU9999",
    ]
    assert {payload.asset_class for payload in seen_payloads} == {
        "index",
        "etf",
        "sge_spot",
    }
    assert all(profile["source_metadata"]["bulk_fetch"] is True for profile in profiles)
    assert all(
        profile["source_metadata"]["akshare_symbol"]
        in {"000300", "000852", "512800", "AU9999"}
        for profile in profiles
    )


# ---------------------------------------------------------------------------
# Task 4: market-data endpoints emit quotes
# ---------------------------------------------------------------------------


def test_akshare_profile_emits_market_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = make_client(tmp_path)

    def fake_snapshot(payload):
        from app.schemas import MarketDataSnapshot

        return MarketDataSnapshot(
            name=payload.name or "000905 fake",
            source="akshare",
            symbol=payload.symbol,
            asset_class=payload.asset_class,
            data={
                "rows": [{"date": "2026-06-01", "close": 6412.55}],
                "latest": {"date": "2026-06-01", "close": 6412.55},
                "spot": 6412.55,
            },
            source_metadata={"source_name": "fake", "fallback": False},
        )

    monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

    resp = client.post(
        "/api/market-data/profiles/akshare",
        json={
            "symbol": "000905.SH",
            "asset_class": "index",
            "start_date": "2026-06-01",
            "end_date": "2026-06-04",
        },
    )
    assert resp.status_code == 200
    profile_id = resp.json()["id"]

    from app import database
    from app.models import Instrument, MarketQuote

    with database.SessionLocal() as session:
        quote = session.query(MarketQuote).one()
        inst = session.get(Instrument, quote.instrument_id)
        assert inst.symbol == "000905.SH"
        assert quote.source == "akshare"
        assert quote.market_data_profile_id == profile_id
        assert quote.price == pytest.approx(6412.55)


def test_akshare_bulk_includes_draft_instrument_and_emits_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A DRAFT instrument with akshare mapping is included in bulk and gets a quote."""
    client = make_client(tmp_path)

    from app import database
    from app.models import Instrument, MarketQuote

    # Seed a draft Instrument with akshare mapping (no position needed)
    with database.SessionLocal() as session:
        inst = Instrument(
            symbol="000905.SH",
            kind="index",
            akshare_symbol="000905",
            akshare_asset_class="index",
            status="draft",
        )
        session.add(inst)
        session.commit()

    def fake_snapshot(payload):
        from app.schemas import MarketDataSnapshot

        return MarketDataSnapshot(
            name=payload.name or f"{payload.symbol} fake",
            source="akshare",
            symbol=payload.symbol,
            asset_class=payload.asset_class,
            data={
                "rows": [{"date": "2026-06-04", "close": 5555.0}],
                "latest": {"date": "2026-06-04", "close": 5555.0},
                "spot": 5555.0,
            },
            source_metadata={"source_name": "fake", "fallback": False},
        )

    monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

    resp = client.post(
        "/api/market-data/profiles/akshare/bulk",
        json={"start_date": "2026-06-04", "end_date": "2026-06-04"},
    )
    assert resp.status_code == 200
    profiles = resp.json()
    assert any(p["symbol"] == "000905.SH" for p in profiles), profiles

    with database.SessionLocal() as session:
        quotes = session.query(MarketQuote).all()
        assert len(quotes) == 1
        assert quotes[0].price == pytest.approx(5555.0)
        assert quotes[0].source == "akshare"


def test_fetch_spot_endpoint_emits_market_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = make_client(tmp_path)

    from app import database
    from app.models import Instrument, MarketQuote

    # Pre-register the underlying (ensure_underlying also creates it on-the-fly
    # but we want a stable id to assert against)
    with database.SessionLocal() as session:
        inst = Instrument(
            symbol="000300.SH",
            kind="index",
            akshare_symbol="000300",
            akshare_asset_class="index",
            status="active",
        )
        session.add(inst)
        session.commit()
        inst_id = inst.id

    def fake_snapshot(payload):
        from app.schemas import MarketDataSnapshot

        return MarketDataSnapshot(
            name=f"{payload.symbol} spot fake",
            source="akshare",
            symbol=payload.symbol,
            asset_class=payload.asset_class,
            data={
                "latest": {"date": "2026-06-04", "close": 3912.0},
                "spot": 3912.0,
            },
            source_metadata={"source_name": "fake", "fallback": False},
        )

    monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

    # Endpoint moved to /api/instruments/{id}/fetch-spot (legacy route retired)
    resp = client.post(f"/api/instruments/{inst_id}/fetch-spot")
    assert resp.status_code == 200

    with database.SessionLocal() as session:
        quote = session.query(MarketQuote).one()
        assert quote.price == pytest.approx(3912.0)
        assert quote.source == "akshare"
        inst = session.get(Instrument, quote.instrument_id)
        assert inst.symbol == "000300.SH"


def test_get_agent_models_returns_catalog(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.get("/api/agent/models")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "active" in data
    assert "channels" in data
    assert isinstance(data["channels"], list)
    if data["channels"]:
        first = data["channels"][0]
        assert {"name", "label", "type", "healthy", "models"} <= set(first.keys())


def test_post_reload_channels_returns_summary(tmp_path):
    client = make_client(tmp_path)
    response = client.post("/api/agent/channels/reload")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "active" in data
    assert isinstance(data["healthy_channels"], list)
    assert isinstance(data["errors"], list)


def test_stream_message_persists_model_selection_on_user_msg(tmp_path):
    client = make_client(tmp_path)
    # Create a thread
    thread = client.post(
        "/api/chat/threads",
        json={"title": "t", "character": "trader"},
    ).json()
    tid = thread["id"]

    body = {
        "content": "hi",
        "character": "auto",
        "model": {
            "channel": "zenmux",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-6",
        },
        "yolo_mode": True,
    }
    response = client.post(f"/api/chat/threads/{tid}/messages/stream", json=body)
    assert response.status_code == 200
    response.read()  # drain SSE stream

    # Re-fetch thread, find the user message, check its meta
    threads = client.get("/api/chat/threads").json()
    msgs = [t for t in threads if t["id"] == tid][0]["messages"]
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert user_msgs, "user message not persisted"
    assert user_msgs[-1]["meta"]["model_selection"] == {
        "channel": "zenmux",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-6",
    }
    assert user_msgs[-1]["meta"]["yolo_mode"] is True


def test_hitl_resume_uses_originating_message_model(monkeypatch, tmp_path):
    """Confirming a pending action invokes the agent built for the originating
    message's model selection, not the default cached one."""
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    client = make_client(tmp_path)
    captured: list[tuple[dict, bool]] = []

    def fake_sync_agent(self, model_selection, *, yolo_mode=False):
        captured.append((model_selection, yolo_mode))

        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage

                return {"messages": [AIMessage(content="ok")]}

        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    originating = {"channel": "zenmux", "provider": "openai", "model": "openai/gpt-5.4"}
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": originating,
                "yolo_mode": True,
                "pending_actions": [
                    {
                        "id": "act-1",
                        "tool_name": "calculate_risk",
                        "label": "Run risk",
                        "summary": "ok",
                        "payload": {},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-1/confirm",
    )
    assert response.status_code == 200
    assert response.json()["meta"]["yolo_mode"] is True
    assert captured == [
        (originating, True)
    ], f"resume should use originating selection and YOLO mode, got {captured}"


def test_hitl_resume_does_not_copy_stale_reply_options(monkeypatch, tmp_path):
    """Completed HITL resumes must not inherit old choice buttons from graph state."""
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    client = make_client(tmp_path)

    def fake_sync_agent(self, model_selection, *, yolo_mode=False):
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage

                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[{
                                "id": "old-reply-options",
                                "name": "propose_reply_options",
                                "args": {
                                    "options": [
                                        {
                                            "label": "Quote it first",
                                            "description": "Price before booking.",
                                        },
                                        {
                                            "label": "Book as-is",
                                            "description": "Book at stated terms.",
                                        },
                                    ],
                                },
                            }],
                        ),
                        AIMessage(content="Position 113 booked."),
                    ],
                }

        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": {
                    "channel": "zenmux",
                    "provider": "openai",
                    "model": "openai/gpt-5.4",
                },
                "pending_actions": [
                    {
                        "id": "act-book",
                        "tool_name": "book_position",
                        "label": "Book position",
                        "summary": "book",
                        "payload": {},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-book/confirm",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Position 113 booked."
    assert "reply_options" not in body["meta"]


def test_hitl_resume_allows_agent_tool_database_write(monkeypatch, tmp_path):
    """A confirmed action may resume into a tool that writes to the app DB."""
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    client = make_client(tmp_path)
    created_run_ids: list[int] = []

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Resume Pricing Book", base_currency="CNY")
        thread = AgentThread(title="t", character="trader")
        session.add_all([portfolio, thread])
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": {
                    "channel": "zenmux",
                    "provider": "openai",
                    "model": "openai/gpt-5.4",
                },
                "pending_actions": [
                    {
                        "id": "act-write",
                        "tool_name": "run_batch_pricing",
                        "label": "Price positions",
                        "summary": "price",
                        "payload": {"portfolio_id": portfolio.id},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        portfolio_id = portfolio.id
        thread_id = thread.id
        msg_id = msg.id

    def fake_sync_agent(self, model_selection, *, yolo_mode=False):
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage

                with database.SessionLocal() as tool_session:
                    run = PositionValuationRun(
                        portfolio_id=portfolio_id,
                        valuation_date=datetime.utcnow(),
                        overrides={},
                        summary={"positions": 0},
                        status="completed",
                        resolved_position_ids=[],
                    )
                    tool_session.add(run)
                    tool_session.commit()
                    created_run_ids.append(run.id)
                return {"messages": [AIMessage(content="pricing complete")]}

        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-write/confirm",
    )

    assert response.status_code == 200
    assert response.json()["content"] == "pricing complete"
    assert len(created_run_ids) == 1
    with database.SessionLocal() as session:
        source = session.get(AgentMessage, msg_id)
        assert source.meta["pending_actions"][0]["status"] == "confirmed"
        run = session.get(PositionValuationRun, created_run_ids[0])
        assert run is not None
        assert run.portfolio_id == portfolio_id


def test_hitl_resume_attaches_background_task_watch_to_confirmed_action(
    monkeypatch,
    tmp_path,
):
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    client = make_client(tmp_path)
    created_task_ids: list[int] = []

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Resume Risk Book", base_currency="CNY")
        thread = AgentThread(title="t", character="risk_manager")
        session.add_all([portfolio, thread])
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="risk_manager",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": {
                    "channel": "zenmux",
                    "provider": "openai",
                    "model": "openai/gpt-5.4",
                },
                "pending_actions": [
                    {
                        "id": "act-risk",
                        "tool_name": "run_batch_pricing",
                        "label": "Run risk analysis",
                        "summary": "risk",
                        "payload": {"portfolio_id": portfolio.id},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        portfolio_id = portfolio.id
        thread_id = thread.id
        msg_id = msg.id

    def fake_sync_agent(self, model_selection, *, yolo_mode=False):
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage, ToolMessage

                with database.SessionLocal() as tool_session:
                    task = TaskRun(
                        kind="risk_run",
                        status="running",
                        portfolio_id=portfolio_id,
                        progress_current=3,
                        progress_total=10,
                        message="Running risk scenarios",
                    )
                    tool_session.add(task)
                    tool_session.commit()
                    created_task_ids.append(task.id)
                return {
                    "messages": [
                        ToolMessage(
                            content=json.dumps({"task_id": created_task_ids[-1]}),
                            name="run_batch_pricing",
                            tool_call_id="call-risk",
                        ),
                        AIMessage(content="risk queued"),
                    ]
                }

        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-risk/confirm",
    )

    assert response.status_code == 200
    with database.SessionLocal() as session:
        source = session.get(AgentMessage, msg_id)
        action = source.meta["pending_actions"][0]
        assert action["status"] == "confirmed"
        assert action["task_id"] == created_task_ids[0]
        assert action["task_kind"] == "risk_run"
        assert action["task_status"] == "running"
        assert action["task_progress_current"] == 3
        assert action["task_progress_total"] == 10
        assert action["task_message"] == "Running risk scenarios"


def test_hitl_resume_falls_back_when_originating_model_missing(monkeypatch, tmp_path):
    """If the originating selection no longer resolves (admin removed the model),
    resume falls back to default and the new message records the fallback flag."""
    from app import database
    from app.models import AgentMessage, AgentThread
    from app.services.agents import AgentService

    client = make_client(tmp_path)

    def fake_sync_agent(self, model_selection, *, yolo_mode=False):
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage

                return {"messages": [AIMessage(content="ok")]}

        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)

    bogus = {"channel": "ghost-channel", "provider": "ghost", "model": "ghost-model"}
    with database.SessionLocal() as session:
        thread = AgentThread(title="t", character="trader")
        session.add(thread)
        session.flush()
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": bogus,
                "pending_actions": [
                    {
                        "id": "act-2",
                        "tool_name": "calculate_risk",
                        "label": "Run risk",
                        "summary": "ok",
                        "payload": {},
                        "status": "pending",
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        thread_id = thread.id
        msg_id = msg.id

    response = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-2/confirm",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"].get("model_selection_fallback") is True


def test_create_view_portfolio_and_get(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    resp = client.post(
        "/api/portfolios",
        json={
            "name": "Snowballs",
            "kind": "view",
            "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
            "tags": ["Desk"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "view"
    assert body["tags"] == ["desk"]
    pid = body["id"]

    resp = client.get(f"/api/portfolios/{pid}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Snowballs"


def test_list_portfolios_filters(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    client.post(
        "/api/portfolios", json={"name": "A", "kind": "container", "tags": ["alpha"]}
    )
    client.post("/api/portfolios", json={"name": "B", "kind": "view", "tags": ["beta"]})

    by_kind = client.get("/api/portfolios?kind=view").json()
    assert {p["name"] for p in by_kind} == {"B"}
    by_tag = client.get("/api/portfolios?tag=alpha").json()
    assert {p["name"] for p in by_tag} == {"A"}


def test_patch_and_delete_portfolio(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    pid = client.post(
        "/api/portfolios", json={"name": "X", "kind": "container"}
    ).json()["id"]
    resp = client.patch(
        f"/api/portfolios/{pid}", json={"description": "demo", "tags": ["risk"]}
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "demo"
    assert resp.json()["tags"] == ["risk"]
    resp = client.delete(f"/api/portfolios/{pid}")
    assert resp.status_code == 204
    assert client.get(f"/api/portfolios/{pid}").status_code == 404


def test_create_duplicate_name_returns_409(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))
    assert (
        client.post(
            "/api/portfolios", json={"name": "Dup", "kind": "container"}
        ).status_code
        == 200
    )
    resp = client.post("/api/portfolios", json={"name": "Dup", "kind": "container"})
    assert resp.status_code == 409


def test_put_filter_rule(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    pid = client.post("/api/portfolios", json={"name": "V", "kind": "view"}).json()[
        "id"
    ]
    resp = client.put(
        f"/api/portfolios/{pid}/rule",
        json={
            "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"}
        },
    )
    assert resp.status_code == 200
    assert resp.json()["filter_rule"]["op"] == "eq"


def test_put_filter_rule_validation_400(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    pid = client.post("/api/portfolios", json={"name": "V", "kind": "view"}).json()[
        "id"
    ]
    resp = client.put(
        f"/api/portfolios/{pid}/rule", json={"filter_rule": {"op": "weird"}}
    )
    assert resp.status_code == 400


def test_includes_excludes_endpoints(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    cid = client.post(
        "/api/portfolios", json={"name": "C", "kind": "container"}
    ).json()["id"]
    pos_resp = client.post(
        f"/api/portfolios/{cid}/positions",
        json={
            "underlying": "AAPL",
            "product_type": "EuropeanVanillaOption",
            "quantity": 1.0,
        },
    )
    assert pos_resp.status_code == 200
    pos_id = pos_resp.json()["positions"][0]["id"]

    vid = client.post("/api/portfolios", json={"name": "V", "kind": "view"}).json()[
        "id"
    ]
    resp = client.post(
        f"/api/portfolios/{vid}/includes", json={"position_ids": [pos_id]}
    )
    assert resp.status_code == 200
    assert resp.json()["manual_include_ids"] == [pos_id]
    resp = client.request(
        "DELETE", f"/api/portfolios/{vid}/includes", json={"position_ids": [pos_id]}
    )
    assert resp.status_code == 200
    assert resp.json()["manual_include_ids"] == []


def test_sources_endpoints_and_cycle_400(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    a = client.post("/api/portfolios", json={"name": "A", "kind": "view"}).json()["id"]
    b = client.post("/api/portfolios", json={"name": "B", "kind": "view"}).json()["id"]

    resp = client.post(f"/api/portfolios/{a}/sources", json={"portfolio_ids": [b]})
    assert resp.status_code == 200
    assert resp.json()["source_portfolio_ids"] == [b]

    resp = client.post(f"/api/portfolios/{b}/sources", json={"portfolio_ids": [a]})
    assert resp.status_code == 400


def test_tags_endpoint(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    pid = client.post(
        "/api/portfolios", json={"name": "P", "kind": "container"}
    ).json()["id"]
    resp = client.put(
        f"/api/portfolios/{pid}/tags", json={"tags": ["Alpha", "alpha", "BETA"]}
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["alpha", "beta"]


def test_membership_and_preview(tmp_path, monkeypatch):
    from app.main import create_app
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    client = TestClient(create_app(settings))

    cid = client.post(
        "/api/portfolios", json={"name": "C", "kind": "container"}
    ).json()["id"]
    pos_resp = client.post(
        f"/api/portfolios/{cid}/positions",
        json={"underlying": "AAPL", "product_type": "Snowball", "quantity": 1.0},
    )
    pos_id = pos_resp.json()["positions"][0]["id"]
    vid = client.post(
        "/api/portfolios",
        json={
            "name": "V",
            "kind": "view",
            "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
        },
    ).json()["id"]

    resp = client.get(f"/api/portfolios/{vid}/membership")
    assert resp.status_code == 200
    assert resp.json()["position_ids"] == [pos_id]

    detail = client.get(f"/api/portfolios/{vid}")
    assert detail.status_code == 200
    assert [position["id"] for position in detail.json()["positions"]] == [pos_id]

    resp = client.post(
        "/api/portfolios/preview",
        json={
            "kind": "view",
            "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["position_ids"] == [pos_id]


def test_patch_position_currency_explicit_and_normalized(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Ccy Book", "base_currency": "CNY"},
    ).json()
    body = {
        "underlying": "000852.SH",
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        "quantity": 1.0,
        "engine_name": "BlackScholesEngine",
    }
    created = client.post(
        f"/api/portfolios/{portfolio['id']}/positions", json=body
    ).json()["positions"][0]

    # Explicit currency (lowercase) -> normalized + persisted.
    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={**body, "currency": "usd"},
    )
    assert patched.status_code == 200
    row = next(p for p in patched.json()["positions"] if p["id"] == created["id"])
    assert row["currency"] == "USD"

    # Omitting currency leaves the stored value unchanged.
    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json=body,
    )
    assert patched.status_code == 200
    row = next(p for p in patched.json()["positions"] if p["id"] == created["id"])
    assert row["currency"] == "USD"

    # Invalid code -> 422.
    rejected = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={**body, "currency": "DOLLARS"},
    )
    assert rejected.status_code == 422


def test_patch_position_product_replacement_rederives_currency(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Ccy Book 2", "base_currency": "CNY"},
    ).json()
    base = {
        "underlying": "000852.SH",
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        "quantity": 1.0,
        "engine_name": "BlackScholesEngine",
    }
    created = client.post(
        f"/api/portfolios/{portfolio['id']}/positions", json=base
    ).json()["positions"][0]

    # Replacing the product WITHOUT an explicit currency re-derives it from the
    # new product (set_position_currency provenance).
    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={
            **base,
            "product": {
                "asset_class": "equity",
                "product_family": "option",
                "quantark_class": "EuropeanVanillaOption",
                "underlying": "000852.SH",
                "currency": "HKD",
                "terms": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            },
        },
    )
    assert patched.status_code == 200
    row = next(p for p in patched.json()["positions"] if p["id"] == created["id"])
    assert row["currency"] == "HKD"


def test_post_position_without_currency_defaults_to_cny(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Ccy Default API Book", "base_currency": "CNY"},
    ).json()
    created = client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "underlying": "000852.SH",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            "quantity": 1.0,
            "engine_name": "BlackScholesEngine",
        },
    ).json()["positions"][0]
    assert created["currency"] == "CNY"
    assert created["product"]["currency"] == "CNY"


def _post_vanilla_client_rfq(client, client_name: str) -> dict:
    response = client.post(
        "/api/client/rfq/form",
        json={
            "client_name": client_name,
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 1,
            },
            "engine_spec": {"engine_name": "BlackScholesEngine"},
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.05,
                "dividend_yield": 0.02,
                "asset_name": "CSI500",
            },
            "unknown": {
                "field_path": "strike",
                "lower_bound": 50,
                "upper_bound": 150,
                "initial_guess": 100,
            },
            "target": {"label": "price", "value": 10},
        },
    )
    assert response.status_code == 200
    return response.json()


def test_client_rfqs_list_orders_filters_and_limits(tmp_path: Path):
    client = make_client(tmp_path)

    first = _post_vanilla_client_rfq(client, "Client A")
    second = _post_vanilla_client_rfq(client, "Client B")
    third = _post_vanilla_client_rfq(client, "Client A")

    listed = client.get("/api/client/rfqs")
    assert listed.status_code == 200
    assert [rfq["id"] for rfq in listed.json()] == [third["id"], second["id"], first["id"]]
    assert "quote_versions" in listed.json()[0]

    filtered = client.get("/api/client/rfqs", params={"client_name": "Client A"})
    assert [rfq["id"] for rfq in filtered.json()] == [third["id"], first["id"]]

    limited = client.get("/api/client/rfqs", params={"limit": 1})
    assert [rfq["id"] for rfq in limited.json()] == [third["id"]]


def test_rfq_catalog_template_seeds_build_quantark_products():
    """Every catalog template's seed kwargs must build a QuantArk product +
    engine as-is (after build_product synthesis for snowball families). The
    client form submits these seeds directly, so an unbuildable template is a
    guaranteed pricing_failed RFQ — the range_accrual template shipped broken
    this way (kwargs the strict builder rejects)."""
    from app.schemas import PricingEnvironmentSnapshot, RFQRequestDraft
    from app.services import rfq as rfq_service
    from app.services.quantark import validate_quantark_build

    market = PricingEnvironmentSnapshot(
        spot=100.0,
        volatility=0.2,
        rate=0.02,
        dividend_yield=0.0,
        asset_name="CSI500",
    )
    failures: dict[str, str] = {}
    for template in rfq_service.COMMON_TEMPLATES:
        draft = RFQRequestDraft(
            product_type=template["product_type"],
            product_kwargs=template["product_kwargs"],
            engine_spec=template["engine_spec"],
            market=market,
        )
        kwargs, missing = rfq_service._executable_product_kwargs(
            draft, quote_mode="price"
        )
        if missing:
            failures[template["key"]] = f"missing contract terms: {missing}"
            continue
        result = validate_quantark_build(
            template["product_type"],
            kwargs,
            market,
            template["engine_spec"]["engine_name"],
            {},
        )
        if not result.ok:
            failures[template["key"]] = str(result.error)
            continue
        # Every advertised solve path must also be constructor-reachable: the
        # solver writes the solved value into the termsheet via this path, so
        # a bogus path (e.g. the old flat `forward_price`) only explodes at
        # solve time. Market keys (volatility, ...) route to market kwargs.
        market_keys = {"spot", "volatility", "rate", "dividend_yield"}
        for field_path in template["unknown_fields"]:
            if field_path in market_keys:
                continue
            probe: dict = {"product_kwargs": json.loads(json.dumps(kwargs))}
            guess = rfq_service._unknown_field_spec(field_path)["initial_guess"]
            rfq_service._set_quantark_unknown_path(probe, field_path, guess)
            solved = validate_quantark_build(
                template["product_type"],
                probe["product_kwargs"],
                market,
                template["engine_spec"]["engine_name"],
                {},
            )
            if not solved.ok:
                failures[f"{template['key']}:{field_path}"] = str(solved.error)
    assert not failures, f"unbuildable templates: {failures}"


def test_client_rfq_form_solves_range_accrual_template(tmp_path: Path):
    client = make_client(tmp_path)

    catalog = client.get("/api/rfq/catalog").json()
    template = next(t for t in catalog["templates"] if t["key"] == "range_accrual")
    spec = template["unknown_field_specs"][0]
    assert spec["field_path"] == "range_config.accrual_rate"

    response = client.post(
        "/api/client/rfq/form",
        json={
            "client_name": "Range Client",
            "underlying": "CSI500",
            "product_type": template["product_type"],
            "product_kwargs": template["product_kwargs"],
            "engine_spec": template["engine_spec"],
            "market": {
                "spot": 100,
                "volatility": 0.2,
                "rate": 0.02,
                "dividend_yield": 0.0,
                "asset_name": "CSI500",
            },
            "unknown": {
                "field_path": spec["field_path"],
                "lower_bound": spec["lower_bound"],
                "upper_bound": spec["upper_bound"],
                "initial_guess": spec["initial_guess"],
            },
            "target": {"label": "price", "value": 5.0},
        },
    )
    assert response.status_code == 200
    rfq = response.json()
    assert rfq["status"] == "pending_approval"
    assert rfq["quote_payload"].get("solved_value") is not None
    assert rfq["quote_payload"].get("quantark_error") is None


def test_nl_draft_range_accrual_maps_coupon_to_accrual_rate():
    """Clients say "coupon" for a range accrual's accrual rate; the NL draft
    must solve the canonical range_config path, not a flat key the strict
    QuantArk builder rejects."""
    from app.services import rfq as rfq_service

    drafted = rfq_service.draft_from_natural_language(
        "Quote a 1y CSI500 range accrual solving coupon for target premium 5",
        "NL Client",
    )
    assert drafted.draft.unknown.field_path == "range_config.accrual_rate"

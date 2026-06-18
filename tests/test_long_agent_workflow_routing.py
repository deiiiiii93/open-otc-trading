from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app import database
from app.config import Settings
from app.models import (
    AgentMessage,
    AgentSession,
    AgentTask,
    AgentThread,
    Workflow,
)
from app.services import agents as agents_module

from _scripted_graph import _ScriptedGraph, _ai, _interrupt, _stream_event


@pytest.fixture
def routed_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
        agent_recursion_limit=77,
        feature_workflow_routing=True,
    )
    database.configure_database(settings)
    database.init_db()

    def install(script: list):
        graph = _ScriptedGraph(script)
        monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: graph)
        monkeypatch.setattr(
            agents_module,
            "build_agent_model",
            lambda *args, **kwargs: object(),
        )
        monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
        return agents_module.AgentService(settings=settings), graph

    return install


@pytest.fixture
def legacy_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
        agent_recursion_limit=77,
        feature_workflow_routing=False,
    )
    database.configure_database(settings)
    database.init_db()

    def install(script: list):
        graph = _ScriptedGraph(script)
        monkeypatch.setattr(agents_module, "build_orchestrator", lambda **kwargs: graph)
        monkeypatch.setattr(
            agents_module,
            "build_agent_model",
            lambda *args, **kwargs: object(),
        )
        monkeypatch.setattr(agents_module, "build_checkpointer", lambda settings: None)
        return agents_module.AgentService(settings=settings), graph

    return install


def test_feature_flagged_sync_respond_runs_orchestrator_session(
    routed_service,
):
    observed = {}

    def answer(payload, config):
        observed["payload"] = payload
        return {
            "messages": [
                _ai("Routed answer from the workflow executor."),
            ],
        }

    service, graph = routed_service([answer])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="routed", character="trader")
        message = service.respond(session, thread, content="hello routed workflow")
        session.commit()
        task_count = session.query(AgentTask).count()
        user = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread.id, AgentMessage.role == "user")
            .one()
        )
        orchestrator_session = session.get(AgentSession, message.session_id)

    assert message.content == "Routed answer from the workflow executor."
    assert message.meta["workflow_routing"] is True
    assert message.meta["router_decision"] == "continue_workflow"
    assert task_count == 0
    assert message.workflow_id == thread.active_workflow_id
    assert message.session_id == orchestrator_session.id
    assert user.workflow_id == thread.active_workflow_id
    assert user.session_id == orchestrator_session.id
    assert orchestrator_session.persona == "orchestrator"
    prompt = observed["payload"]["messages"][0].content
    assert "=== User says ===\nhello routed workflow" in prompt

    configurable = graph.last_config["configurable"]
    assert configurable["thread_id"] == orchestrator_session.checkpointer_key
    assert configurable["workflow_id"] == thread.active_workflow_id
    assert configurable["session_id"] == orchestrator_session.id
    assert "task_id" not in configurable
    assert "context_pack_id" not in configurable
    assert configurable["envelope"] == "desk_workflow"


def test_feature_flagged_followup_reuses_same_orchestrator_session(
    routed_service,
):
    observed = []

    def first(payload, config):
        observed.append((payload, config))
        return {"messages": [_ai("Which portfolio should I scan?")]}

    def second(payload, config):
        observed.append((payload, config))
        return {"messages": [_ai("Scanning the Snowballs portfolio.")]}

    service, _graph = routed_service([first, second])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="followup", character="trader")
        first_message = service.respond(
            session,
            thread,
            content="list all positions that KO % From Spot under 5%",
        )
        second_message = service.respond(session, thread, content="Snowballs")
        session.commit()
        task_count = session.query(AgentTask).count()

    assert first_message.session_id == second_message.session_id
    assert first_message.workflow_id == second_message.workflow_id
    assert task_count == 0
    assert observed[0][1]["configurable"]["thread_id"] == observed[1][1]["configurable"]["thread_id"]
    assert "=== User says ===\nSnowballs" in observed[1][0]["messages"][0].content


def test_feature_flagged_sync_respond_suppresses_internal_runtime_language(
    routed_service,
):
    service, _graph = routed_service(
        [
            {
                "messages": [
                    _ai(
                        "The context pack is missing for task 18 in workflow 4, "
                        "so I cannot continue."
                    ),
                ],
            }
        ]
    )

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="routed leak", character="trader")
        message = service.respond(session, thread, content="list positions near KO")
        session.commit()

    assert "context pack" not in message.content.lower()
    assert "task 18" not in message.content.lower()
    assert "workflow 4" not in message.content.lower()
    assert "not suitable to show" in message.content


def test_feature_flagged_sync_respond_uses_last_non_empty_ai_before_empty_final(
    routed_service,
):
    reply_tool_call = {
        "name": "propose_reply_options",
        "args": {
            "options": [
                {"label": "Snowballs", "value": "Scan Snowballs."},
                {"label": "All books", "value": "Scan all books."},
            ]
        },
        "id": "toolu_options",
        "type": "tool_call",
    }
    service, _graph = routed_service(
        [
            {
                "messages": [
                    _ai(
                        "Which portfolio should I scan?",
                        tool_calls=[reply_tool_call],
                    ),
                    ToolMessage(
                        content='{"ok": true, "count": 2}',
                        name="propose_reply_options",
                        tool_call_id="toolu_options",
                    ),
                    AIMessage(content=[]),
                ],
            }
        ]
    )

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="empty final", character="trader")
        message = service.respond(session, thread, content="positions near KO")
        session.commit()

    assert message.content == "Which portfolio should I scan?"
    assert message.meta["reply_options"] == [
        {"label": "Snowballs", "value": "Scan Snowballs."},
        {"label": "All books", "value": "Scan all books."},
    ]


def test_feature_flagged_sync_respond_emits_orchestrator_hitl_source_meta(
    routed_service,
):
    service, graph = routed_service(
        [
            {
                "__interrupt__": [
                    _interrupt(
                        "intr-1",
                        "run_batch_pricing",
                        {"portfolio_id": 7, "method": "summary"},
                        "Run risk for portfolio 7",
                    )
                ],
            }
        ]
    )

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="routed hitl", character="trader")
        message = service.respond(session, thread, content="risk please")
        session.commit()
        task_count = session.query(AgentTask).count()
        orchestrator_session = session.get(AgentSession, message.session_id)

    assert message.meta["agent_phase"] == "awaiting_confirmation"
    assert task_count == 0
    actions = message.meta["pending_actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["id"] == "intr-1:0"
    assert action["tool_name"] == "run_batch_pricing"
    source_meta = action["source_meta"]
    assert "task_id" not in source_meta
    assert "context_pack_id" not in source_meta
    assert source_meta["session_id"] == orchestrator_session.id
    assert source_meta["workflow_id"] == thread.active_workflow_id
    assert source_meta["checkpointer_key"] == graph.last_config["configurable"]["thread_id"]
    assert source_meta["agent_runtime"] == "deepagents_orchestrator"
    assert source_meta["envelope_final"] == "desk_workflow"
    assert source_meta["audit"]["tool_call_id"] == "intr-1"
    assert source_meta["audit"]["tool_name"] == "run_batch_pricing"
    assert source_meta["audit"]["persona"] == "orchestrator"
    assert source_meta["audit"]["emitted_at"]


def test_feature_flagged_status_query_routes_to_workspace_meta_without_agent(
    routed_service,
):
    service, graph = routed_service([])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="router status", character="trader")
        message = service.respond(session, thread, content="what's in flight?")
        session.commit()
        task_count = session.query(AgentTask).count()
        meta_workflow = (
            session.query(Workflow)
            .filter(Workflow.thread_id == thread.id, Workflow.intent == "workspace_meta")
            .one()
        )
        router_session = (
            session.query(AgentSession)
            .filter(
                AgentSession.workflow_id == meta_workflow.id,
                AgentSession.persona == "router",
            )
            .one()
        )

    assert graph.last_config is None
    assert task_count == 0
    assert message.workflow_id == meta_workflow.id
    assert message.session_id == router_session.id
    assert message.meta["workflow_routing"] is True
    assert message.meta["router_decision"] == "status_query"
    assert "Active workflows" in message.content


def test_workspace_router_meta_only_state_counts_as_zero_active_workflows(
    routed_service,
):
    service, _graph = routed_service([])

    with database.SessionLocal() as session:
        from app.services.deep_agent.workspace_router import route_workspace_turn

        thread = AgentThread(title="meta only", character="trader")
        session.add(thread)
        session.flush()
        meta = Workflow(
            thread_id=thread.id,
            title="meta only / workspace",
            intent="workspace_meta",
            status="active",
            opened_by="system",
            canonical_snapshot_ids={"scope_kind": "workspace_meta"},
        )
        session.add(meta)
        session.flush()
        router_session = AgentSession(
            workflow_id=meta.id,
            persona="router",
            episode_id=1,
            status="active",
            checkpointer_key=f"thread:{thread.id}:router",
        )
        session.add(router_session)
        session.flush()

        decision = route_workspace_turn(
            session,
            thread=thread,
            user_message="look at this portfolio",
            yolo_mode=False,
        )
        domain_count = (
            session.query(Workflow)
            .filter(Workflow.thread_id == thread.id, Workflow.intent != "workspace_meta")
            .count()
        )

    assert decision.kind == "clarify"
    assert decision.workflow_id == meta.id
    assert decision.session_id == router_session.id
    assert decision.response_content == "Which workflow should I start for this?"
    assert domain_count == 0


def test_feature_flagged_explicit_workflow_reference_routes_orchestrator_to_that_workflow(
    routed_service,
):
    service, graph = routed_service(
        [
            {
                "messages": [
                    _ai("Routed to the named workflow."),
                ],
            }
        ]
    )

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="router explicit", character="trader")
        target = Workflow(
            thread_id=thread.id,
            title="Snowball review",
            intent="ad_hoc",
            status="active",
            opened_by="desk_user",
            canonical_snapshot_ids={"scope_kind": "ad_hoc"},
        )
        session.add(target)
        session.flush()

        message = service.respond(
            session,
            thread,
            content=f"continue workflow #{target.id}: summarize it",
        )
        session.commit()
        task_count = session.query(AgentTask).count()
        orchestrator_session = session.get(AgentSession, message.session_id)

    assert message.content == "Routed to the named workflow."
    assert message.workflow_id == target.id
    assert message.session_id == orchestrator_session.id
    assert task_count == 0
    assert thread.active_workflow_id == target.id
    assert graph.last_config["configurable"]["workflow_id"] == target.id
    assert graph.last_config["configurable"]["thread_id"] == orchestrator_session.checkpointer_key


def test_feature_flagged_stream_and_persist_runs_orchestrator_session(
    routed_service,
):
    todos = [
        {"content": "Scan positions near KO", "status": "in_progress"},
        {"content": "Return qualifying positions", "status": "pending"},
    ]
    service, graph = routed_service(
        [
            {
                "__events__": [
                    _stream_event(
                        "on_tool_start",
                        run_id="todo-1",
                        name="write_todos",
                        input={"todos": todos},
                    ),
                    _stream_event("on_tool_end", run_id="todo-1", output={"ok": True}),
                    _stream_event(
                        "on_tool_start",
                        run_id="positions-1",
                        name="get_positions",
                        input={"portfolio_id": 7},
                    ),
                    _stream_event("on_chat_model_stream", chunk_text="Streamed routed answer."),
                    _stream_event(
                        "on_tool_end",
                        run_id="positions-1",
                        output={"count": 3},
                    ),
                ],
                "messages": [
                    _ai("Streamed routed answer."),
                ],
                "todos": [
                    {"content": "Scan positions near KO", "status": "completed"},
                    {"content": "Return qualifying positions", "status": "completed"},
                ],
            }
        ]
    )
    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="stream routed", character="trader")
        thread_id = thread.id
        session.commit()

    async def run():
        return [
            chunk
            async for chunk in service.stream_and_persist(
                thread_id=thread_id,
                content="hello stream workflow",
                requested_character="auto",
                page_context=None,
            )
        ]

    chunks = asyncio.run(run())
    joined = "".join(chunks)

    with database.SessionLocal() as session:
        task_count = session.query(AgentTask).count()
        assistant = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .one()
        )
        orchestrator_session = session.get(AgentSession, assistant.session_id)

    assert 'event: tool_start\ndata: {"id": "todo-1", "name": "write_todos"' in joined
    assert 'event: todo_update\ndata: {"todos": [{"content": "Scan positions near KO", "status": "in_progress"}' in joined
    assert 'event: tool_start\ndata: {"id": "positions-1", "name": "get_positions"' in joined
    assert 'event: tool_end\ndata: {"id": "positions-1"' in joined
    assert 'event: token\ndata: {"text": "Streamed routed answer."}' in joined
    assert "event: done" in joined
    assert assistant.content == "Streamed routed answer."
    assert assistant.meta["workflow_routing"] is True
    assert assistant.meta["router_decision"] == "continue_workflow"
    assert [ev["name"] for ev in assistant.meta["process_events"]] == [
        "write_todos",
        "get_positions",
    ]
    assert assistant.meta["todos"] == [
        {"content": "Scan positions near KO", "status": "completed"},
        {"content": "Return qualifying positions", "status": "completed"},
    ]
    assert task_count == 0
    assert graph.last_config["configurable"]["thread_id"] == orchestrator_session.checkpointer_key


def test_feature_flagged_stream_persists_clean_final_state_when_stream_leaks_task_id(
    routed_service,
):
    service, _graph = routed_service(
        [
            {
                "__events__": [
                    _stream_event(
                        "on_tool_start",
                        run_id="risk-1",
                        name="run_batch_pricing",
                        input={"portfolio_id": 1},
                    ),
                    _stream_event(
                        "on_chat_model_stream",
                        chunk_text=(
                            "Batch pricing run is queued "
                            "(risk_run_id=10, task_id=12)."
                        ),
                    ),
                    _stream_event(
                        "on_tool_end",
                        run_id="risk-1",
                        output={"risk_run_id": 10, "status": "completed"},
                    ),
                ],
                "messages": [
                    _ai("Risk analysis completed cleanly for the Default portfolio."),
                ],
                "todos": [
                    {"content": "Run risk analysis", "status": "completed"},
                ],
            }
        ]
    )
    with database.SessionLocal() as session:
        thread = service.create_thread(
            session,
            title="stream routed leak",
            character="trader",
        )
        thread_id = thread.id
        session.commit()

    async def run():
        return [
            chunk
            async for chunk in service.stream_and_persist(
                thread_id=thread_id,
                content="run risk",
                requested_character="auto",
                page_context=None,
            )
        ]

    joined = "".join(asyncio.run(run()))

    with database.SessionLocal() as session:
        assistant = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .one()
        )

    assert "event: done" in joined
    assert assistant.content == "Risk analysis completed cleanly for the Default portfolio."
    assert "not suitable to show" not in assistant.content
    assert [ev["name"] for ev in assistant.meta["process_events"]] == [
        "run_batch_pricing",
    ]
    assert assistant.meta["todos"] == [
        {"content": "Run risk analysis", "status": "completed"},
    ]


def test_feature_flagged_stream_keeps_fallback_when_stream_and_state_leak_internals(
    routed_service,
):
    service, _graph = routed_service(
        [
            {
                "__events__": [
                    _stream_event(
                        "on_chat_model_stream",
                        chunk_text="Batch pricing queued with task_id=12.",
                    ),
                ],
                "messages": [
                    _ai("The context pack is missing for task 12."),
                ],
            }
        ]
    )
    with database.SessionLocal() as session:
        thread = service.create_thread(
            session,
            title="stream routed double leak",
            character="trader",
        )
        thread_id = thread.id
        session.commit()

    async def run():
        return [
            chunk
            async for chunk in service.stream_and_persist(
                thread_id=thread_id,
                content="run risk",
                requested_character="auto",
                page_context=None,
            )
        ]

    joined = "".join(asyncio.run(run()))

    with database.SessionLocal() as session:
        assistant = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "assistant")
            .one()
        )

    assert "event: done" in joined
    assert "not suitable to show" in assistant.content
    assert "task_id=12" not in assistant.content
    assert "context pack" not in assistant.content.lower()


def test_feature_flagged_stream_threads_confirmed_cost_preview(
    routed_service,
):
    service, graph = routed_service(
        [
            {
                "messages": [
                    _ai("Confirmed long run."),
                ],
            }
        ]
    )
    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="stream confirmed", character="trader")
        thread_id = thread.id
        session.commit()

    async def run():
        return [
            chunk
            async for chunk in service.stream_and_persist(
                thread_id=thread_id,
                content="run risk now",
                requested_character="auto",
                page_context=None,
                yolo_mode=True,
                confirmed_cost_preview=True,
            )
        ]

    joined = "".join(asyncio.run(run()))

    assert "event: done" in joined
    configurable = graph.last_config["configurable"]
    assert configurable["confirmed_cost_preview"] is True


def test_workflow_routing_flag_off_keeps_legacy_sync_respond(legacy_service):
    service, graph = legacy_service(
        [
            {
                "messages": [
                    _ai("Legacy path answer."),
                ],
            }
        ]
    )

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="legacy", character="trader")
        message = service.respond(session, thread, content="hello legacy workflow")
        session.commit()
        task_count = session.query(AgentTask).count()

    assert message.content == "Legacy path answer."
    assert message.meta.get("workflow_routing") is None
    assert task_count == 0
    assert graph.last_config["configurable"]["thread_id"] == str(thread.id)

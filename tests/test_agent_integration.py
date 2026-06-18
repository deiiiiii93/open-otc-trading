"""Task 16: Scripted-model integration tests for the DeepAgent HITL flow.

Uses a _ScriptedGraph fixture that replays a deterministic sequence of result
dicts — no real LLM required. Covers four scenarios:

1. Happy path through trader persona (completes without HITL)
2. Single HITL pause (run_batch_pricing) then resume → complete
3. Multi-pause turn (run_batch_pricing then approve_rfq) → 3-message lifecycle
4. Dismiss path (approve_rfq rejected) → completed with acknowledgement
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app import database
from app.config import Settings
from app.services import agents as agents_module

from _scripted_graph import _ScriptedGraph, _ai, _interrupt, _task_call


# ---------------------------------------------------------------------------
# Shared fixture — bootstraps DB + stubs build_orchestrator
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_with_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Bootstrap an AgentService whose deep_agent is a _ScriptedGraph.

    Yields a callable:  install(script) → (AgentService, _ScriptedGraph)

    Call install() ONCE per test with the desired script before any
    database.SessionLocal() calls.
    """
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
            lambda s: object(),  # truthy non-None → deep_agent branch taken
        )
        monkeypatch.setattr(
            agents_module,
            "build_checkpointer",
            lambda s: None,
        )

        service = agents_module.AgentService(settings=settings)
        return service, graph

    yield install


# ---------------------------------------------------------------------------
# Test 1 — happy path: orchestrator dispatches to trader, completes at once
# ---------------------------------------------------------------------------

def test_orchestrator_dispatches_to_trader_and_completes(agent_with_script):
    service, _graph = agent_with_script([
        {
            "messages": [
                _ai(
                    "I called trader.",
                    tool_calls=[_task_call("trader")],
                ),
                _ai("Quote: 12.34. Trader confirmed inputs."),
            ],
        },
    ])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="trader")
        message = service.respond(session, thread, content="quote AAPL")
        session.commit()

    assert message.meta["agent_phase"] == "completed"
    assert message.meta["pending_actions"] == []
    assert "trader" in message.meta.get("personas_invoked", [])
    assert "12.34" in message.content
    assert _graph.last_config["recursion_limit"] == 77


def test_trading_desk_html_file_is_persisted_as_downloadable_asset(agent_with_script):
    service, _graph = agent_with_script([
        {
            "messages": [
                _ai("Calling trader.", tool_calls=[_task_call("trader")]),
                _ai("Chart ready: /trading_desk/charts/candle_000852_SH.html"),
            ],
            "files": {
                "/trading_desk/charts/candle_000852_SH.html": {
                    "content": "<!doctype html><html><body>CSI 500 chart</body></html>",
                    "encoding": "utf-8",
                },
            },
        },
    ])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="trader")
        thread_id = thread.id
        message = service.respond(session, thread, content="build chart")
        session.commit()

    html_assets = [asset for asset in message.meta["assets"] if asset["kind"] == "html"]
    assert len(html_assets) == 1
    asset = html_assets[0]
    assert asset["path"] == "/trading_desk/charts/candle_000852_SH.html"
    assert asset["url"] == f"/api/artifacts/agent/thread-{thread_id}/trading_desk/charts/candle_000852_SH.html"
    artifact_path = Path(asset["metadata"]["artifact_path"])
    assert artifact_path.exists()
    assert "CSI 500 chart" in artifact_path.read_text(encoding="utf-8")


def test_run_python_trading_desk_csv_artifact_is_persisted(agent_with_script):
    csv_path = "/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv"
    csv_content = (
        "position_id,trade_id,underlying,ko_pct_from_spot\n"
        "66,OTC-JNTZ-20260430-OPTION-01,000300.SH,0.48\n"
    )
    service, _graph = agent_with_script([
        {
            "messages": [
                _ai("Calling trader.", tool_calls=[_task_call("trader")]),
                ToolMessage(
                    content=json.dumps(
                        {
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
                        }
                    ),
                    name="run_python",
                    tool_call_id="toolu_csv",
                ),
                _ai(f"CSV written successfully: {csv_path}"),
            ],
        },
    ])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="trader")
        thread_id = thread.id
        message = service.respond(session, thread, content="export csv")
        session.commit()

    csv_assets = [asset for asset in message.meta["assets"] if asset["path"] == csv_path]
    assert len(csv_assets) == 1
    asset = csv_assets[0]
    assert asset["kind"] == "file"
    assert asset["mime_type"] == "text/csv"
    assert asset["url"] == (
        f"/api/artifacts/agent/thread-{thread_id}"
        "/trading_desk/exports/snowballs_ko_proximity_2026-05-26.csv"
    )
    artifact_path = Path(asset["metadata"]["artifact_path"])
    assert artifact_path.exists()
    assert artifact_path.read_text(encoding="utf-8") == csv_content


# ---------------------------------------------------------------------------
# Test 2 — single HITL pause (run_batch_pricing) then approve → completed
# ---------------------------------------------------------------------------

def test_run_batch_pricing_pauses_for_hitl_then_completes_on_resume(agent_with_script):
    service, _graph = agent_with_script([
        # First invoke: orchestrator dispatched to risk_manager → hit run_batch_pricing
        {
            "messages": [
                _ai("Calling risk_manager.", tool_calls=[_task_call("risk_manager")]),
                _ai("About to run_batch_pricing."),
            ],
            "__interrupt__": [
                _interrupt("intr-1", "run_batch_pricing", {"portfolio_id": 7, "method": "summary"}),
            ],
        },
        # Second invoke (resume after approve): risk_manager finalised
        {
            "messages": [
                _ai("Risk run #N filed. VaR within limits."),
            ],
        },
    ])

    # First turn: should pause
    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="risk_manager")
        first = service.respond(session, thread, content="run risk on portfolio 7")
        session.commit()

    assert first.meta["agent_phase"] == "awaiting_confirmation"
    pending = first.meta["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "run_batch_pricing"
    assert pending[0]["id"].startswith("intr-1:")

    # Resume turn: approve and collect the next result via direct service call
    cmd_payload = Command(resume={"decisions": [{"type": "approve"}]})
    with database.SessionLocal() as session:
        thread = session.merge(thread)
        result = service.deep_agent.invoke(
            cmd_payload,
            config={"configurable": {"thread_id": str(thread.id)}},
        )
        final = service._persist_agent_result(session, thread, result, assets=[], page_context=None)
        session.commit()

    assert final.meta["agent_phase"] == "completed"
    assert "VaR within limits" in final.content


# ---------------------------------------------------------------------------
# Test 3 — multi-pause: run_batch_pricing then approve_rfq, three assistant messages
# ---------------------------------------------------------------------------

def test_multi_pause_run_batch_pricing_then_approve_rfq(agent_with_script):
    service, _graph = agent_with_script([
        # Step 1: first invoke → pause on run_batch_pricing
        {
            "messages": [
                _ai("Calling risk_manager.", tool_calls=[_task_call("risk_manager")]),
                _ai("About to run_batch_pricing."),
            ],
            "__interrupt__": [
                _interrupt("intr-1", "run_batch_pricing", {"portfolio_id": 3, "method": "summary"}),
            ],
        },
        # Step 2: resume after run_batch_pricing approved → pause on approve_rfq
        {
            "messages": [
                _ai("Risk done. Now calling high_board.", tool_calls=[_task_call("high_board")]),
                _ai("About to approve_rfq."),
            ],
            "__interrupt__": [
                _interrupt("intr-2", "approve_rfq", {"rfq_id": 99}),
            ],
        },
        # Step 3: resume after approve_rfq approved → final
        {
            "messages": [
                _ai("RFQ-99 approved. All done."),
            ],
        },
    ])

    # Turn 1: pause on run_batch_pricing
    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="risk_manager")
        msg1 = service.respond(session, thread, content="run risk then approve rfq 99")
        session.commit()

    assert msg1.meta["agent_phase"] == "awaiting_confirmation"
    assert msg1.meta["pending_actions"][0]["tool_name"] == "run_batch_pricing"

    # Turn 2: approve run_batch_pricing → pause on approve_rfq
    cmd1 = Command(resume={"decisions": [{"type": "approve"}]})
    with database.SessionLocal() as session:
        thread = session.merge(thread)
        result2 = service.deep_agent.invoke(
            cmd1, config={"configurable": {"thread_id": str(thread.id)}}
        )
        msg2 = service._persist_agent_result(session, thread, result2, assets=[], page_context=None)
        session.commit()

    assert msg2.meta["agent_phase"] == "awaiting_confirmation"
    assert msg2.meta["pending_actions"][0]["tool_name"] == "approve_rfq"

    # Turn 3: approve approve_rfq → completed
    cmd2 = Command(resume={"decisions": [{"type": "approve"}]})
    with database.SessionLocal() as session:
        thread = session.merge(thread)
        result3 = service.deep_agent.invoke(
            cmd2, config={"configurable": {"thread_id": str(thread.id)}}
        )
        msg3 = service._persist_agent_result(session, thread, result3, assets=[], page_context=None)
        session.commit()

    assert msg3.meta["agent_phase"] == "completed"
    assert "RFQ-99 approved" in msg3.content

    # Sequence of phases must be: awaiting → awaiting → completed
    phases = [m.meta["agent_phase"] for m in (msg1, msg2, msg3)]
    assert phases == ["awaiting_confirmation", "awaiting_confirmation", "completed"]


# ---------------------------------------------------------------------------
# Test 4 — dismiss path: approve_rfq rejected → completed with acknowledgement
# ---------------------------------------------------------------------------

def test_dismiss_pending_approve_rfq_completes_with_acknowledgement(agent_with_script):
    service, _graph = agent_with_script([
        # First invoke: orchestrator dispatched to high_board → hit approve_rfq
        {
            "messages": [
                _ai("Calling high_board.", tool_calls=[_task_call("high_board")]),
                _ai("About to approve_rfq."),
            ],
            "__interrupt__": [_interrupt("intr-1", "approve_rfq", {"rfq_id": 42})],
        },
        # Second invoke (resume after reject/dismiss)
        {
            "messages": [
                _ai("Acknowledged: user dismissed the RFQ approval."),
            ],
        },
    ])

    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="high_board")
        first = service.respond(session, thread, content="approve RFQ-42")
        session.commit()

    assert first.meta["agent_phase"] == "awaiting_confirmation"
    assert first.meta["pending_actions"][0]["tool_name"] == "approve_rfq"

    cmd = Command(resume={"decisions": [{"type": "reject", "message": "User dismissed."}]})
    with database.SessionLocal() as session:
        thread = session.merge(thread)
        result = service.deep_agent.invoke(
            cmd, config={"configurable": {"thread_id": str(thread.id)}}
        )
        final = service._persist_agent_result(session, thread, result, assets=[], page_context=None)
        session.commit()

    assert final.meta["agent_phase"] == "completed"
    assert "dismissed" in final.content.lower()

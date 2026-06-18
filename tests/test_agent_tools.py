from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app import database
from app.config import Settings
from app.services.agents import (
    DEEP_AGENT_TOOL_NAMES,
    AgentService,
    select_deep_agent_tools,
)

# The gate-bypass autouse fixture lives in tests/conftest.py — it auto-resolves
# every tool invocation to desk_workflow except in the gate's own tests.

# ---------------------------------------------------------------------------
# Shared test-DB fixture helpers (mirrors test_position_import_pricing.py)
# ---------------------------------------------------------------------------


def _configure_test_db(tmp_path: Path) -> None:
    """Point the global database module at a fresh SQLite file and create tables."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()


class ToolCapableFakeChatModel(FakeMessagesListChatModel):
    def bind_tools(
        self, tools: list[Any], *, tool_choice: str | None = None, **kwargs: Any
    ):
        object.__setattr__(
            self,
            "bound_tool_names",
            [getattr(tool, "name", str(tool)) for tool in tools],
        )
        return self


def test_select_deep_agent_tools_includes_every_required_tool():
    selected = select_deep_agent_tools()
    names = {tool.name for tool in selected}
    assert names == DEEP_AGENT_TOOL_NAMES
    assert {
        "run_greeks_landscape",
        "get_greeks_landscape_run",
        "get_latest_greeks_landscape_run",
        "run_batch_pricing",
        "list_pricing_parameter_profiles",
        "close_position",
        "settle_position",
        "mark_knockout",
        "cancel_lifecycle_event",
        "query_snowball_ko_from_spot",
        "approve_rfq",
    }.issubset(names)
    assert "price_positions" not in names
    assert "run_risk" not in names


def test_scoped_deep_agent_tool_denies_calls_outside_task_scope():
    from app.services.deep_agent.capability_gate import ToolScopeDeniedError

    tool = next(t for t in select_deep_agent_tools() if t.name == "get_positions")

    try:
        tool.invoke(
            {},
            config={
                "configurable": {
                    "envelope": "desk_workflow",
                    "tools_scope": ["run_batch_pricing"],
                }
            },
        )
    except ToolScopeDeniedError as exc:
        assert exc.tool_name == "get_positions"
        assert exc.tools_scope == ("run_batch_pricing",)
    else:
        raise AssertionError("get_positions should be denied outside tools_scope")


def test_render_context_brief_pins_selected_pricing_profile():
    from app.services.agents import render_context_brief

    brief = render_context_brief(
        {
            "current_page_context": {
                "title": "Risk",
                "entity_ids": {"portfolio_id": 7, "pricing_profile_id": 11},
                "snapshot": {
                    "selected_pricing_profile": {
                        "id": 11,
                        "name": "2026-04-30 Close",
                        "valuation_date": "2026-04-30T00:00:00",
                    }
                },
            },
            "portfolio_summary": {"name": "Desk", "position_count": 3},
        }
    )

    assert 'Selected pricing parameter profile: id=11 "2026-04-30 Close"' in brief
    assert "pass pricing_parameter_profile_id=11" in brief


def test_render_context_brief_requires_pricing_profile_clarification_for_writes():
    from app.services.agents import render_context_brief

    brief = render_context_brief(
        {
            "current_page_context": {
                "title": "Risk",
                "entity_ids": {"portfolio_id": 7, "pricing_profile_id": None},
                "snapshot": {},
            },
            "portfolio_summary": {"name": "Desk", "position_count": 3},
        }
    )

    assert "No pricing parameter profile is selected" in brief
    assert "Before proposing run_batch_pricing or create_report" in brief


def test_render_context_brief_includes_recent_thread_messages_and_assets():
    from app.services.agents import render_context_brief

    brief = render_context_brief(
        {
            "current_page_context": {"title": "Agent Desk", "snapshot": {}},
            "recent_thread_messages": [
                {
                    "role": "user",
                    "content": "Create a DOCX report from the near-KO analysis.",
                    "assets": [],
                },
                {
                    "role": "assistant",
                    "character": "high_board",
                    "content": "Near-KO positions are 93 and 106.",
                    "assets": [{"path": "/trading_desk/reports/old.md"}],
                },
            ],
        }
    )

    assert "Recent thread context:" in brief
    assert "Create a DOCX report" in brief
    assert "assistant/high_board" in brief
    assert "/trading_desk/reports/old.md" in brief


def test_agent_service_respond_through_orchestrator(monkeypatch, tmp_path: Path):
    """Stubs out build_orchestrator to return a graph emitting a deterministic
    final AIMessage. Asserts respond() persists the assistant message with
    meta agent_graph='deepagents' and agent_phase='completed'."""
    import os
    from app.services import agents as agents_module

    # Bootstrap test DB
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )
    database.configure_database(settings)
    database.init_db()

    class _StubGraph:
        name = "stub"

        def invoke(self, payload, config=None):
            return {"messages": [AIMessage(content="Stub final reply")]}

    def fake_build_orchestrator(**kwargs):
        return _StubGraph()

    def fake_build_agent_model(s):
        # Return a non-None sentinel so the disabled branch is NOT taken
        return object()

    def fake_build_checkpointer(s):
        return None

    monkeypatch.setattr(agents_module, "build_orchestrator", fake_build_orchestrator)
    monkeypatch.setattr(agents_module, "build_agent_model", fake_build_agent_model)
    monkeypatch.setattr(agents_module, "build_checkpointer", fake_build_checkpointer)

    service = agents_module.AgentService(settings=settings)
    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="trader")
        message = service.respond(session, thread, content="hello")
        session.commit()

    assert message.content == "Stub final reply"
    assert message.meta["agent_graph"] == "deepagents"
    assert message.meta["agent_phase"] == "completed"
    assert message.meta["pending_actions"] == []


# ---------------------------------------------------------------------------
# Tests for Task 7: run_batch_pricing_tool
# ---------------------------------------------------------------------------


def test_run_batch_pricing_tool_returns_expected_keys_and_writes_audit(tmp_path: Path):
    from app.models import AuditEvent, Portfolio, Position
    from app.tools.risk import run_batch_pricing_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Test Portfolio", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            source_trade_id="T001",
            product_type="EuropeanVanillaOption",
            underlying="000300.SH",
            quantity=100,
            product_kwargs={"strike": 4000.0, "notional": 1_000_000},
        )
        session.add(position)
        session.commit()
        portfolio_id = portfolio.id

    result = run_batch_pricing_tool.invoke({"portfolio_id": portfolio_id, "method": "summary"})

    assert result["portfolio_id"] == portfolio_id
    assert result["method"] == "summary"
    assert result["status"] == "queued"
    assert "risk_run_id" in result
    assert "task_id" in result

    with database.SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "batch_pricing.queued")
            .order_by(AuditEvent.id.desc())
            .first()
        )
    assert audit is not None
    assert audit.subject_type == "portfolio"
    assert audit.subject_id == str(portfolio_id)
    assert audit.payload.get("source") == "agent_confirmed"
    assert audit.payload.get("method") == "summary"


def test_run_batch_pricing_tool_binds_pricing_parameter_profile(
    monkeypatch, tmp_path: Path
):
    from datetime import datetime

    from app.models import AuditEvent, Portfolio, PricingParameterProfile, RiskRun
    from app.services import task_runner
    from app.tools.risk import run_batch_pricing_tool

    _configure_test_db(tmp_path)
    monkeypatch.setattr(
        task_runner, "submit_async_task", lambda *args, **kwargs: None
    )
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Profile Bound Risk", base_currency="CNY")
        profile = PricingParameterProfile(
            name="2026-04-30 Close",
            valuation_date=datetime(2026, 4, 30),
            source_type="xlsx",
            summary={"row_count": 1},
        )
        session.add_all([portfolio, profile])
        session.commit()
        portfolio_id = portfolio.id
        profile_id = profile.id

    result = run_batch_pricing_tool.invoke(
        {
            "portfolio_id": portfolio_id,
            "method": "summary",
            "pricing_parameter_profile_id": profile_id,
        }
    )

    assert result["pricing_parameter_profile_id"] == profile_id
    with database.SessionLocal() as session:
        run = session.get(RiskRun, result["risk_run_id"])
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "batch_pricing.queued")
            .order_by(AuditEvent.id.desc())
            .first()
        )
    assert run is not None
    assert run.pricing_parameter_profile_id == profile_id
    assert audit is not None
    assert audit.payload.get("pricing_parameter_profile_id") == profile_id


def test_run_batch_pricing_tool_raises_for_missing_portfolio(tmp_path: Path):
    import pytest
    from app.tools.risk import run_batch_pricing_tool

    _configure_test_db(tmp_path)

    with pytest.raises(Exception):
        run_batch_pricing_tool.invoke({"portfolio_id": 9999})


def test_read_only_latest_number_tools_use_completed_database_rows(tmp_path: Path):
    from datetime import datetime

    from app.models import (
        Portfolio,
        Position,
        PositionValuationResult,
        PositionValuationRun,
        RiskRun,
    )
    from app.tools.positions import get_latest_position_valuations_tool
    from app.tools.risk import get_latest_risk_run_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Stored Numbers", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            source_trade_id="T-STORED",
            product_type="EuropeanVanillaOption",
            underlying="000300.SH",
            quantity=100,
            product_kwargs={"strike": 4000.0},
        )
        session.add(position)
        session.flush()
        valuation_run = PositionValuationRun(
            portfolio_id=portfolio.id,
            valuation_date=datetime(2026, 5, 11),
            status="completed",
            summary={"market_value": 123.0},
        )
        session.add(valuation_run)
        session.flush()
        session.add(
            PositionValuationResult(
                valuation_run_id=valuation_run.id,
                position_id=position.id,
                source_trade_id="T-STORED",
                ok=True,
                price=1.23,
                market_value=123.0,
                pnl=23.0,
            )
        )
        risk_run = RiskRun(
            portfolio_id=portfolio.id,
            method="summary",
            status="completed",
            metrics={"totals": {"delta": 12.0}, "positions": []},
        )
        session.add(risk_run)
        session.commit()
        portfolio_id = portfolio.id

    valuations = get_latest_position_valuations_tool.invoke(
        {"portfolio_id": portfolio_id}
    )
    risk = get_latest_risk_run_tool.invoke({"portfolio_id": portfolio_id})

    assert valuations["found"] is True
    assert valuations["results"][0]["market_value"] == 123.0
    assert risk["found"] is True
    assert risk["metrics"]["totals"]["delta"] == 12.0


# ---------------------------------------------------------------------------
# Tests for Task 7: create_report_tool
# ---------------------------------------------------------------------------


def test_create_report_tool_returns_expected_keys_and_writes_audit(tmp_path: Path):
    from app.models import AuditEvent, Portfolio
    from app.tools.reporting import create_report_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Report Portfolio", base_currency="CNY")
        session.add(portfolio)
        session.commit()
        portfolio_id = portfolio.id

    result = create_report_tool.invoke(
        {
            "portfolio_id": portfolio_id,
            "report_type": "portfolio",
            "title": "Test Report",
        }
    )

    assert "report_job_id" in result
    assert "task_id" in result
    assert result["status"] == "queued"

    with database.SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "report.queued")
            .order_by(AuditEvent.id.desc())
            .first()
        )
    assert audit is not None
    assert audit.subject_type == "report"
    assert audit.payload.get("source") == "agent_confirmed"


def test_create_report_tool_binds_pricing_parameter_profile(
    monkeypatch, tmp_path: Path
):
    from datetime import datetime

    from app.models import Portfolio, PricingParameterProfile, ReportJob
    from app.services import task_runner
    from app.tools.reporting import create_report_tool

    _configure_test_db(tmp_path)
    monkeypatch.setattr(
        task_runner, "submit_async_task", lambda *args, **kwargs: None
    )
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Profile Bound Report", base_currency="CNY")
        profile = PricingParameterProfile(
            name="2026-04-30 Close",
            valuation_date=datetime(2026, 4, 30),
            source_type="xlsx",
            summary={"row_count": 1},
        )
        session.add_all([portfolio, profile])
        session.commit()
        portfolio_id = portfolio.id
        profile_id = profile.id

    result = create_report_tool.invoke(
        {
            "portfolio_id": portfolio_id,
            "report_type": "risk",
            "title": "Profile Bound Risk Report",
            "pricing_parameter_profile_id": profile_id,
        }
    )

    with database.SessionLocal() as session:
        job = session.get(ReportJob, result["report_job_id"])
    assert job is not None
    assert result["pricing_parameter_profile_id"] == profile_id
    assert job.request_payload["pricing_parameter_profile_id"] == profile_id


def test_run_batch_pricing_tool_completed_run_writes_both_outputs(
    monkeypatch, tmp_path: Path
):
    """Agent-triggered run_batch_pricing, once executed, persists BOTH a
    completed RiskRun (with metrics) and a PositionValuationRun in one pass."""
    from app.models import Portfolio, Position, PositionValuationRun, RiskRun, TaskRun
    from app.services.domains import risk as risk_domain
    from app.tools.risk import run_batch_pricing_tool

    _configure_test_db(tmp_path)
    submitted: dict[str, Any] = {}

    def capture_submit(func, *args, **kwargs):
        submitted["func"] = func
        submitted["args"] = args

    monkeypatch.setattr(risk_domain, "submit_async_task", capture_submit)

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Dual Output", base_currency="USD")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            source_trade_id="T-AGENT-1",
            product_type="EuropeanVanillaOption",
            product_kwargs={
                "strike": 100.0,
                "option_type": "CALL",
                "maturity": 1.0,
                "contract_multiplier": 1.0,
            },
            engine_name="BlackScholesEngine",
            quantity=5.0,
            entry_price=8.0,
        )
        session.add(position)
        session.commit()
        portfolio_id = portfolio.id
        position_id = position.id

    result = run_batch_pricing_tool.invoke(
        {"portfolio_id": portfolio_id, "method": "summary"}
    )
    assert result["status"] == "queued"

    # Execute the captured batch-pricing task synchronously.
    task_id, risk_run_id, session_factory = submitted["args"]
    submitted["func"](task_id, risk_run_id, session_factory)

    with database.SessionLocal() as session:
        risk_run = session.get(RiskRun, risk_run_id)
        assert risk_run.status == "completed"
        assert risk_run.metrics["positions"][0]["position_id"] == position_id

        task = session.get(TaskRun, task_id)
        assert task.status == "completed"
        assert task.result_payload["risk_run_id"] == risk_run_id
        valuation_run = session.get(
            PositionValuationRun, task.result_payload["valuation_run_id"]
        )
        assert valuation_run.status == "completed"
        assert valuation_run.portfolio_id == portfolio_id
        assert valuation_run.summary["priced"] == 1


# ---------------------------------------------------------------------------
# Tests for Task 7: approve_rfq_tool
# ---------------------------------------------------------------------------


def test_approve_rfq_tool_sets_status_and_writes_audit(tmp_path: Path):
    from app.models import AuditEvent, RFQ, RfqStatus
    from app.tools.rfq import approve_rfq_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        rfq = RFQ(
            client_name="Test Client",
            status=RfqStatus.PENDING_APPROVAL.value,
            request_payload={
                "product_type": "SnowballNote",
                "underlying": "000300.SH",
                "quantity": 100,
                "side": "buy",
            },
            quote_payload={
                "field_label": "coupon",
                "solved_value": 0.08,
                "achieved_price": 0.95,
            },
        )
        session.add(rfq)
        session.commit()
        rfq_id = rfq.id

    result = approve_rfq_tool.invoke(
        {"rfq_id": rfq_id, "approver": "agent_confirmed", "comment": "Looks good"}
    )

    assert result["rfq_id"] == rfq_id
    assert result["status"] == RfqStatus.APPROVED.value
    assert result["approved_response"] is not None

    with database.SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "rfq.approved")
            .order_by(AuditEvent.id.desc())
            .first()
        )
    assert audit is not None
    assert audit.subject_type == "rfq"
    assert audit.subject_id == str(rfq_id)
    assert audit.actor == "agent_confirmed"
    assert audit.payload.get("source") == "agent_confirmed"


def test_approve_rfq_tool_raises_for_missing_rfq(tmp_path: Path):
    import pytest
    from app.tools.rfq import approve_rfq_tool

    _configure_test_db(tmp_path)

    with pytest.raises(Exception):
        approve_rfq_tool.invoke({"rfq_id": 9999})


# ---------------------------------------------------------------------------
# Tests for Task 7: reject_rfq_tool
# ---------------------------------------------------------------------------


def test_reject_rfq_tool_sets_status_and_writes_audit(tmp_path: Path):
    from app.models import AuditEvent, RFQ, RfqStatus
    from app.tools.rfq import reject_rfq_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        rfq = RFQ(
            client_name="Test Client 2",
            status=RfqStatus.PENDING_APPROVAL.value,
            request_payload={
                "product_type": "EuropeanVanillaOption",
                "underlying": "000001.SZ",
                "quantity": 50,
                "side": "sell",
            },
            quote_payload={
                "field_label": "vol",
                "solved_value": 0.20,
                "achieved_price": 0.50,
            },
        )
        session.add(rfq)
        session.commit()
        rfq_id = rfq.id

    result = reject_rfq_tool.invoke(
        {"rfq_id": rfq_id, "approver": "agent_confirmed", "comment": "Too risky"}
    )

    assert result["rfq_id"] == rfq_id
    assert result["status"] == RfqStatus.REJECTED.value
    assert result["approved_response"] is None

    with database.SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter(AuditEvent.event_type == "rfq.rejected")
            .order_by(AuditEvent.id.desc())
            .first()
        )
    assert audit is not None
    assert audit.subject_type == "rfq"
    assert audit.subject_id == str(rfq_id)
    assert audit.actor == "agent_confirmed"
    assert audit.payload.get("source") == "agent_confirmed"


def test_reject_rfq_tool_raises_for_missing_rfq(tmp_path: Path):
    import pytest
    from app.tools.rfq import reject_rfq_tool

    _configure_test_db(tmp_path)

    with pytest.raises(Exception):
        reject_rfq_tool.invoke({"rfq_id": 9999})


# ---------------------------------------------------------------------------
# Tests for Task 15: list/get/create portfolio tools
# ---------------------------------------------------------------------------


def test_create_portfolio_tool_view_with_rule(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.tools.portfolios import (
        create_portfolio_tool,
        get_portfolio_tool,
        list_portfolios_tool,
    )

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    out = create_portfolio_tool.invoke(
        {
            "name": "Snowballs",
            "kind": "view",
            "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
            "tags": ["desk"],
        }
    )
    assert out["ok"] is True
    pid = out["data"]["id"]

    detail = get_portfolio_tool.invoke({"portfolio_id": pid})
    assert detail["ok"] is True
    assert detail["data"]["kind"] == "view"

    listed = list_portfolios_tool.invoke({"kind": "view"})
    assert listed["ok"] is True
    assert any(p["id"] == pid for p in listed["data"])


def test_get_positions_resolves_view_through_filter_rule(tmp_path, monkeypatch):
    """get_positions(portfolio_id=<view>) must resolve through the view's
    filter_rule, not through Position.portfolio_id == view_id (which would
    always be empty because positions physically live in containers).

    Regression for: user said "Snowballs view has 0 underlyings" even though
    the source container held SnowballOption positions.
    """
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position
    from app.tools.portfolios import create_portfolio_tool
    from app.tools.positions import get_positions_tool

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    # 1) Source container with two positions: one snowball, one vanilla
    container = create_portfolio_tool.invoke(
        {"name": "Holdings", "kind": "container"}
    )["data"]
    with database.SessionLocal() as session:
        session.add_all(
            [
                Position(
                    portfolio_id=container["id"],
                    source_trade_id="T-SB-1",
                    underlying="000852.SH",
                    product_type="SnowballOption",
                    quantity=1.0,
                    entry_price=100.0,
                    status="open",
                ),
                Position(
                    portfolio_id=container["id"],
                    source_trade_id="T-EV-1",
                    underlying="000300.SH",
                    product_type="EuropeanVanillaOption",
                    quantity=1.0,
                    entry_price=100.0,
                    status="open",
                ),
            ]
        )
        session.commit()

    # 2) View filtered to SnowballOption, with NO source_portfolio_ids
    #    (matches the actual configuration the user hit in production).
    view = create_portfolio_tool.invoke(
        {
            "name": "Snowballs",
            "kind": "view",
            "filter_rule": {
                "op": "eq",
                "field": "product_type",
                "value": "SnowballOption",
            },
        }
    )["data"]

    # 3) The tool must return the snowball position from the source container.
    out = get_positions_tool.invoke({"portfolio_id": view["id"]})
    assert out["total_count"] == 1, out
    assert out["positions"][0]["source_trade_id"] == "T-SB-1"
    assert out["positions"][0]["product_type"] == "SnowballOption"
    # And it must NOT match the vanilla position.
    assert all(p["product_type"] == "SnowballOption" for p in out["positions"])


def test_create_portfolio_tool_invalid_rule_returns_errors(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.tools.portfolios import create_portfolio_tool

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    out = create_portfolio_tool.invoke(
        {
            "name": "Bad",
            "kind": "view",
            "filter_rule": {"op": "weird"},
        }
    )
    assert out["ok"] is False
    assert out["errors"]


# ---------------------------------------------------------------------------
# Tests for Task 16: update/delete/set-rule portfolio tools
# ---------------------------------------------------------------------------


def test_update_and_delete_portfolio_tool(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.tools.portfolios import (
        create_portfolio_tool,
        delete_portfolio_tool,
        update_portfolio_tool,
    )

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    pid = create_portfolio_tool.invoke({"name": "P", "kind": "container"})["data"]["id"]
    out = update_portfolio_tool.invoke({"portfolio_id": pid, "description": "d"})
    assert out["ok"] is True
    assert out["data"]["description"] == "d"
    out = delete_portfolio_tool.invoke({"portfolio_id": pid})
    assert out["ok"] is True


def test_set_portfolio_rule_tool_validates(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.tools.portfolios import (
        create_portfolio_tool,
        set_portfolio_rule_tool,
    )

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    pid = create_portfolio_tool.invoke({"name": "V", "kind": "view"})["data"]["id"]
    out = set_portfolio_rule_tool.invoke(
        {
            "portfolio_id": pid,
            "filter_rule": {"op": "eq", "field": "product_type", "value": "Snowball"},
        }
    )
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# Tests for Task 17: add/remove positions + add/remove sources tools
# ---------------------------------------------------------------------------


def test_add_remove_positions_via_tool(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, PortfolioKind
    from app.tools.portfolios import (
        add_positions_to_portfolio_tool,
        create_portfolio_tool,
        remove_positions_from_portfolio_tool,
    )

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    cid = create_portfolio_tool.invoke({"name": "C", "kind": "container"})["data"]["id"]
    with database.SessionLocal() as session:
        p = Position(
            portfolio_id=cid, underlying="AAPL", product_type="X", quantity=1.0
        )
        session.add(p)
        session.commit()
        pos_id = p.id

    vid = create_portfolio_tool.invoke({"name": "V", "kind": "view"})["data"]["id"]

    out = add_positions_to_portfolio_tool.invoke(
        {"portfolio_id": vid, "position_ids": [pos_id]}
    )
    assert out["ok"] is True
    assert out["data"]["manual_include_ids"] == [pos_id]

    out = remove_positions_from_portfolio_tool.invoke(
        {"portfolio_id": vid, "position_ids": [pos_id]}
    )
    assert out["ok"] is True
    assert out["data"]["manual_include_ids"] == []


def test_add_remove_sources_via_tool(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.tools.portfolios import (
        add_portfolio_sources_tool,
        create_portfolio_tool,
        remove_portfolio_sources_tool,
    )

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    a = create_portfolio_tool.invoke({"name": "A", "kind": "view"})["data"]["id"]
    b = create_portfolio_tool.invoke({"name": "B", "kind": "view"})["data"]["id"]

    out = add_portfolio_sources_tool.invoke(
        {"portfolio_id": a, "source_portfolio_ids": [b]}
    )
    assert out["ok"] is True
    out = add_portfolio_sources_tool.invoke(
        {"portfolio_id": b, "source_portfolio_ids": [a]}
    )
    assert out["ok"] is False
    assert "cycle_path" in out
    out = remove_portfolio_sources_tool.invoke(
        {"portfolio_id": a, "source_portfolio_ids": [b]}
    )
    assert out["ok"] is True


# ---------------------------------------------------------------------------
# run_python sandbox tool
# ---------------------------------------------------------------------------


def test_run_python_wrapper_base64_encodes_user_code():
    """The wrapper MUST encode the user's code as base64 before sending it
    through Deno. Deno's argv handling reinterprets backslash escapes — so
    interpolating user code verbatim would silently corrupt any script that
    contains \\n, \\t, etc. (i.e. every realistic CSV/HTML emitter).
    """
    import base64

    from app.services.sandbox_tool import _build_wrapper

    user_code = "MAGIC_CANARY_XYZ = 'value_42'"
    wrapper = _build_wrapper(user_code, {"k": 1})

    # The literal user code must NOT appear in the wrapper source — only its
    # base64 form does. Otherwise the escape-mangling bug returns.
    assert "MAGIC_CANARY_XYZ" not in wrapper
    expected_b64 = base64.b64encode(user_code.encode("utf-8")).decode("ascii")
    assert expected_b64 in wrapper

    # Payload is similarly base64-encoded
    payload_b64 = base64.b64encode(b'{"k": 1}').decode("ascii")
    assert payload_b64 in wrapper


def test_run_python_wrapper_resolves_file_markers_before_payload_encoding():
    import base64
    import json

    from deepagents.backends.protocol import FileData, ReadResult

    from app.services.sandbox_tool import _build_wrapper

    class _FakeBackend:
        def read(self, path: str):
            assert path == "/large_tool_results/positions.json"
            return ReadResult(
                file_data=FileData(
                    content=json.dumps({"positions": [{"id": 1, "qty": 42}]}),
                    encoding="utf-8",
                )
            )

    backend = _FakeBackend()
    payload = {
        "rows": "@file:/large_tool_results/positions.json",
        "literal": "prefix @file:/large_tool_results/positions.json",
    }

    wrapper = _build_wrapper("result = data", payload, backend=backend)

    expected_payload = {
        "rows": {"positions": [{"id": 1, "qty": 42}]},
        "literal": "prefix @file:/large_tool_results/positions.json",
    }
    expected_b64 = base64.b64encode(
        json.dumps(expected_payload, ensure_ascii=False, default=str).encode("utf-8")
    ).decode("ascii")
    assert expected_b64 in wrapper


def test_run_python_tool_resolves_file_markers_before_sandbox(monkeypatch):
    import base64
    import json
    import re

    from deepagents.backends.protocol import FileData, ReadResult

    from app.services import sandbox_tool

    class _FakeBackend:
        def read(self, path: str, offset: int = 0, limit: int = 2000):
            assert path == "/large_tool_results/positions.json"
            return ReadResult(
                file_data=FileData(
                    content=json.dumps({"positions": [{"id": 1, "qty": 42}]}),
                    encoding="utf-8",
                )
            )

    class _FakeRun:
        status = "success"
        stdout = ""
        execution_time = 0.01
        result = {"value": "ok", "artifacts": []}

    captured: dict[str, str] = {}

    class _FakeSandbox:
        async def execute(self, wrapper: str, *, timeout_seconds: float):
            captured["wrapper"] = wrapper
            return _FakeRun()

    monkeypatch.setattr(sandbox_tool, "_state_backend_for_file_markers", _FakeBackend)
    monkeypatch.setattr(sandbox_tool, "_get_sandbox", lambda: _FakeSandbox())

    res = sandbox_tool.run_python_tool.invoke(
        {
            "code": "result = data",
            "payload": {"rows": "@file:/large_tool_results/positions.json"},
        }
    )

    assert res["ok"] is True
    match = re.search(
        r"_sandbox_json\.loads\(_sandbox_b64\.b64decode\('([^']+)'\)",
        captured["wrapper"],
    )
    assert match, captured["wrapper"]
    injected = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
    assert injected["rows"] == {"positions": [{"id": 1, "qty": 42}]}


def test_run_python_validation_error_is_actionable():
    from app.services import sandbox_tool

    result = sandbox_tool.run_python_tool.invoke({})

    assert result == (
        'run_python requires a JSON object with a "code" field containing '
        "the Python script to execute; retry with "
        '{"code": "result = ...", "payload": {...}}.'
    )


def test_run_python_large_file_marker_wrapper_executes_from_temp_file(monkeypatch):
    import base64
    import json
    import re

    from deepagents.backends.protocol import FileData, ReadResult

    from app.services import sandbox_tool

    class _FakeBackend:
        def read(self, path: str, offset: int = 0, limit: int = 2000):
            assert path == "/large_tool_results/positions.json"
            return ReadResult(
                file_data=FileData(
                    content=json.dumps({"marker": True, "padding": "x" * 1000}),
                    encoding="utf-8",
                )
            )

    class _FakeSandbox:
        sessions_dir = "/tmp/open-otc-test-sandbox"
        permissions = []

        async def execute(self, wrapper: str, *, timeout_seconds: float):
            raise AssertionError("large wrappers must not be sent through argv")

    captured: dict[str, object] = {}

    async def _fake_execute_wrapper_file(sandbox, wrapper, *, timeout_seconds: float):
        captured["sandbox"] = sandbox
        captured["wrapper"] = wrapper
        captured["timeout_seconds"] = timeout_seconds
        return sandbox_tool._SandboxExecutionResult(
            status="success",
            execution_time=0.01,
            stdout="",
            stderr="",
            result={"value": {"ok": True}, "artifacts": []},
        )

    monkeypatch.setattr(sandbox_tool, "_MAX_DENO_ARG_WRAPPER_BYTES", 1)
    monkeypatch.setattr(sandbox_tool, "_state_backend_for_file_markers", _FakeBackend)
    monkeypatch.setattr(sandbox_tool, "_get_sandbox", lambda: _FakeSandbox())
    monkeypatch.setattr(
        sandbox_tool,
        "_execute_wrapper_file",
        _fake_execute_wrapper_file,
    )

    res = sandbox_tool.run_python_tool.invoke(
        {
            "code": "result = {'ok': data['rows']['marker']}",
            "payload": {"rows": "@file:/large_tool_results/positions.json"},
        }
    )

    assert res["ok"] is True
    assert res["result"] == {"ok": True}
    assert isinstance(captured["sandbox"], _FakeSandbox)
    assert "marker" not in str(captured["wrapper"])
    assert captured["timeout_seconds"] == 30.0
    match = re.search(
        r"_sandbox_json\.loads\(_sandbox_b64\.b64decode\('([^']+)'\)",
        str(captured["wrapper"]),
    )
    assert match, captured["wrapper"]
    injected = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
    assert injected["rows"]["marker"] is True
    assert len(injected["rows"]["padding"]) == 1000


def test_run_python_drops_unapproved_artifacts_when_writes_artifacts_false(monkeypatch):
    import base64

    from app.services import sandbox_tool

    class _FakeRun:
        status = "success"
        stdout = ""
        execution_time = 0.01
        result = {
            "value": {"total": 42},
            "artifacts": [
                {
                    "path": "summary.md",
                    "size_bytes": 7,
                    "content_b64": base64.b64encode(b"summary").decode("ascii"),
                }
            ],
        }

    class _FakeSandbox:
        async def execute(self, wrapper: str, *, timeout_seconds: float):
            return _FakeRun()

    monkeypatch.setattr(sandbox_tool, "_get_sandbox", lambda: _FakeSandbox())

    res = sandbox_tool.run_python_tool.invoke(
        {
            "code": "result = {'total': 42}",
            "payload": {},
            "writes_artifacts": False,
        }
    )

    assert res["ok"] is True
    assert res["result"] == {"total": 42}
    assert res["artifacts"] == []
    assert "writes_artifacts=False" in res["artifact_warning"]


def test_run_python_keeps_artifacts_when_writes_artifacts_true(monkeypatch):
    import base64

    from app.services import sandbox_tool

    class _FakeRun:
        status = "success"
        stdout = ""
        execution_time = 0.01
        result = {
            "value": "done",
            "artifacts": [
                {
                    "path": "summary.md",
                    "size_bytes": 7,
                    "content_b64": base64.b64encode(b"summary").decode("ascii"),
                }
            ],
        }

    class _FakeSandbox:
        async def execute(self, wrapper: str, *, timeout_seconds: float):
            return _FakeRun()

    monkeypatch.setattr(sandbox_tool, "_get_sandbox", lambda: _FakeSandbox())

    res = sandbox_tool.run_python_tool.invoke(
        {
            "code": "result = 'done'",
            "payload": {},
            "writes_artifacts": True,
        }
    )

    assert res["ok"] is True
    assert res["artifacts"] == [
        {
            "path": "summary.md",
            "size_bytes": 7,
            "content": "summary",
            "kind": "text",
        }
    ]


def test_run_python_keeps_virtual_trading_desk_artifact_paths(monkeypatch):
    import base64

    from app.services import sandbox_tool

    class _FakeRun:
        status = "success"
        stdout = ""
        execution_time = 0.01
        result = {
            "value": {
                "file_path": "/trading_desk/exports/snowballs.csv",
                "rows_written": 1,
            },
            "artifacts": [
                {
                    "path": "/trading_desk/exports/snowballs.csv",
                    "size_bytes": 7,
                    "content_b64": base64.b64encode(b"a,b\n1,2").decode("ascii"),
                }
            ],
        }

    class _FakeSandbox:
        async def execute(self, wrapper: str, *, timeout_seconds: float):
            return _FakeRun()

    monkeypatch.setattr(sandbox_tool, "_get_sandbox", lambda: _FakeSandbox())

    res = sandbox_tool.run_python_tool.invoke(
        {
            "code": "result = {'rows_written': 1}",
            "payload": {},
            "writes_artifacts": True,
        }
    )

    assert res["ok"] is True
    assert res["artifacts"] == [
        {
            "path": "/trading_desk/exports/snowballs.csv",
            "size_bytes": 7,
            "content": "a,b\n1,2",
            "kind": "text",
        }
    ]


def test_run_python_artifact_write_requires_captured_artifact(monkeypatch):
    from app.services import sandbox_tool

    class _FakeRun:
        status = "success"
        stdout = ""
        execution_time = 0.01
        result = {
            "value": {
                "file_path": "/trading_desk/exports/missing.csv",
                "rows_written": 1,
            },
            "artifacts": [],
        }

    class _FakeSandbox:
        async def execute(self, wrapper: str, *, timeout_seconds: float):
            return _FakeRun()

    monkeypatch.setattr(sandbox_tool, "_get_sandbox", lambda: _FakeSandbox())

    res = sandbox_tool.run_python_tool.invoke(
        {
            "code": "result = {'rows_written': 1}",
            "payload": {},
            "writes_artifacts": True,
        }
    )

    assert res["ok"] is False
    assert "No artifacts were captured" in res["error"]
    assert res["artifacts"] == []


def _deno_available() -> bool:
    import shutil

    return shutil.which("deno") is not None


def test_run_python_executes_and_returns_artifacts(tmp_path, monkeypatch):
    """End-to-end live sandbox run. Skipped when Deno is not installed.

    Covers the happy path: payload injection, result extraction, and
    artifact-from-/sandbox_out/ extraction with text content decode.
    """
    import pytest

    if not _deno_available():
        pytest.skip("Deno binary not installed; run_python sandbox skipped")

    from app import config as _config
    from app.config import Settings
    from app.services.sandbox_tool import run_python_tool
    import app.services.sandbox_tool as sandbox_tool

    settings = Settings(artifact_dir=tmp_path / "artifacts")
    monkeypatch.setattr(_config, "get_settings", lambda: settings)
    monkeypatch.setattr(sandbox_tool, "_SANDBOX", None)

    script = (
        "import os\n"
        "total = sum(p['qty'] for p in data['positions'])\n"
        "with open(os.path.join(ARTIFACT_DIR, 'summary.md'), 'w') as f:\n"
        "    f.write('total qty: ' + str(total))\n"
        "result = {'total': total}\n"
    )
    res = run_python_tool.invoke(
        {
            "code": script,
            "payload": {"positions": [{"qty": 10}, {"qty": 25}, {"qty": 7}]},
            "timeout_s": 90,
            "description": "sum quantities and emit summary artifact",
            "writes_artifacts": True,
        }
    )

    assert res["ok"] is True, f"sandbox failed: {res.get('error')}"
    assert res["result"] == {"total": 42}
    assert len(res["artifacts"]) == 1
    artifact = res["artifacts"][0]
    assert artifact["path"] == "summary.md"
    assert artifact["kind"] == "text"
    assert artifact["content"] == "total qty: 42"


def test_run_python_reports_script_error_cleanly(tmp_path, monkeypatch):
    """A script error must come back as ok=False, not crash the agent loop."""
    import pytest

    if not _deno_available():
        pytest.skip("Deno binary not installed; run_python sandbox skipped")

    from app import config as _config
    from app.config import Settings
    from app.services.sandbox_tool import run_python_tool
    import app.services.sandbox_tool as sandbox_tool

    settings = Settings(artifact_dir=tmp_path / "artifacts")
    monkeypatch.setattr(_config, "get_settings", lambda: settings)
    monkeypatch.setattr(sandbox_tool, "_SANDBOX", None)

    res = run_python_tool.invoke(
        {
            "code": "raise ValueError('boom')",
            "payload": {},
            "timeout_s": 60,
        }
    )
    assert res["ok"] is False
    assert "error" in res
    assert "ValueError" in res["error"] or "boom" in res["error"]

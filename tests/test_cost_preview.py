"""Tests for the cost-preview escape hatch on the capability gate."""
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import tool

from app.services.deep_agent.capability_gate import (
    CostPreviewRequiredError,
    LONG_RUNNING_SECONDS,
    capability_gated,
)
from app.services.deep_agent.envelopes import Envelope, ToolGroup


_DESK = {"configurable": {"envelope": Envelope.DESK_WORKFLOW.value}}
_DESK_CONFIRMED = {
    "configurable": {
        "envelope": Envelope.DESK_WORKFLOW.value,
        "confirmed_cost_preview": True,
    },
}


def _high_estimator(_tool_input: dict) -> float:
    return LONG_RUNNING_SECONDS + 15.0  # always over the threshold


def _low_estimator(_tool_input: dict) -> float:
    return 2.0  # always under


def _raising_estimator(_tool_input: dict) -> float:
    raise RuntimeError("estimator unexpectedly failed")


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_high_estimator)
@tool("slow_thing")
def slow_thing_tool(portfolio_id: int) -> dict:
    """Always over the long-running threshold."""
    return {"ran": True, "portfolio_id": portfolio_id}


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_low_estimator)
@tool("quick_thing")
def quick_thing_tool(portfolio_id: int) -> dict:
    """Estimator returns 2s, under the threshold."""
    return {"ran": True}


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_raising_estimator)
@tool("estimator_explodes")
def estimator_explodes_tool(portfolio_id: int) -> dict:
    """Estimator raises; tool should still run."""
    return {"ran": True}


def test_cost_preview_blocks_unconfirmed_high_estimate():
    with pytest.raises(CostPreviewRequiredError) as exc:
        slow_thing_tool.invoke({"portfolio_id": 1}, config=_DESK)  # type: ignore[arg-type]
    assert exc.value.estimated_seconds == LONG_RUNNING_SECONDS + 15.0
    assert exc.value.tool_name == "slow_thing"


def test_cost_preview_lets_through_when_confirmed():
    result = slow_thing_tool.invoke(
        {"portfolio_id": 1}, config=_DESK_CONFIRMED  # type: ignore[arg-type]
    )
    assert result == {"ran": True, "portfolio_id": 1}


def test_cost_preview_fires_at_exactly_threshold():
    """Policy is ≥30s; estimator returning exactly 30.0s must trip the
    gate. Without the inclusive boundary check, ~60 risk positions at
    0.5s each would slip through. Regression for iter-4 P2."""

    def boundary_estimator(_tool_input: Any) -> float:
        return LONG_RUNNING_SECONDS  # exactly 30.0

    @capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=boundary_estimator)
    @tool("boundary_thing")
    def boundary_thing_tool(portfolio_id: int) -> dict:
        """Estimator returns the threshold itself."""
        return {"ran": True}

    with pytest.raises(CostPreviewRequiredError) as exc:
        boundary_thing_tool.invoke({"portfolio_id": 1}, config=_DESK)  # type: ignore[arg-type]
    assert exc.value.estimated_seconds == LONG_RUNNING_SECONDS


def test_low_estimate_never_triggers_preview():
    result = quick_thing_tool.invoke({"portfolio_id": 1}, config=_DESK)  # type: ignore[arg-type]
    assert result == {"ran": True}


def test_estimator_exception_falls_through_to_normal_invoke():
    """An estimator that raises should not block the tool — under-trigger is OK."""
    result = estimator_explodes_tool.invoke({"portfolio_id": 1}, config=_DESK)  # type: ignore[arg-type]
    assert result == {"ran": True}


def test_estimator_unwraps_langgraph_toolnode_wrapper():
    """LangGraph's ToolNode passes {'name','args','id','type':'tool_call'} — the
    estimator must see the args, not the wrapper, or the gate is silently
    bypassed in production. Regression for codex review iter 1."""
    captured: list[Any] = []

    def capturing_estimator(tool_input: Any) -> float:
        captured.append(tool_input)
        # Return high so the gate fires when args have portfolio_id.
        return LONG_RUNNING_SECONDS + 5.0 if (
            isinstance(tool_input, dict) and tool_input.get("portfolio_id")
        ) else 0.0

    @capability_gated(
        group=ToolGroup.DOMAIN_WRITE, cost_estimator=capturing_estimator
    )
    @tool("toolnode_shape_check")
    def toolnode_shape_check_tool(portfolio_id: int) -> dict:
        """Regression test target."""
        return {"ran": True}

    # Direct-args path (what unit tests use): bare dict, should fire the gate.
    with pytest.raises(CostPreviewRequiredError):
        toolnode_shape_check_tool.invoke({"portfolio_id": 7}, config=_DESK)  # type: ignore[arg-type]
    assert captured[-1] == {"portfolio_id": 7}

    # ToolNode-wrapped path: the wrapper dict from LangGraph. The gate must
    # still fire because we unwrap to args before estimating.
    captured.clear()
    with pytest.raises(CostPreviewRequiredError):
        toolnode_shape_check_tool.invoke(
            {
                "name": "toolnode_shape_check",
                "args": {"portfolio_id": 7},
                "id": "call_1",
                "type": "tool_call",
            },
            config=_DESK,  # type: ignore[arg-type]
        )
    # The estimator must have seen the unwrapped args, not the wrapper.
    assert captured[-1] == {"portfolio_id": 7}


def test_capability_denied_takes_precedence_over_cost_preview():
    """If the envelope denies the group, the cost estimator is never consulted."""
    from app.services.deep_agent.capability_gate import CapabilityDeniedError

    with pytest.raises(CapabilityDeniedError):
        slow_thing_tool.invoke(
            {"portfolio_id": 1},
            config={"configurable": {"envelope": Envelope.PET_PAGE.value}},  # type: ignore[arg-type]
        )


def test_run_batch_pricing_tool_estimator_fires_on_large_portfolio(monkeypatch, tmp_path):
    """End-to-end-ish: run_batch_pricing_tool with a >60-position portfolio trips the preview."""
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position
    from app.tools.risk import run_batch_pricing_tool

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 't.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Big Portfolio", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        # estimate_run_seconds = positions * 0.5s, so >60 positions => >30s.
        for i in range(80):
            session.add(
                Position(
                    portfolio_id=portfolio.id,
                    source_trade_id=f"T-{i:04d}",
                    product_type="EuropeanVanillaOption",
                    underlying="000300.SH",
                    quantity=100,
                    product_kwargs={"strike": 4000.0},
                )
            )
        session.commit()
        portfolio_id = portfolio.id

    with pytest.raises(CostPreviewRequiredError) as exc:
        run_batch_pricing_tool.invoke(
            {"portfolio_id": portfolio_id, "method": "summary"}, config=_DESK  # type: ignore[arg-type]
        )
    # 80 * 0.5 = 40s estimate
    assert exc.value.estimated_seconds == pytest.approx(40.0)
    assert exc.value.tool_name == "run_batch_pricing"


def test_run_batch_pricing_tool_estimator_uses_scoped_position_ids(monkeypatch, tmp_path):
    """A small scoped position list should not inherit full portfolio cost."""
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position
    from app.tools.risk import run_batch_pricing_tool

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 't.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Big Portfolio", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        scoped_ids: list[int] = []
        for i in range(80):
            position = Position(
                portfolio_id=portfolio.id,
                source_trade_id=f"T-{i:04d}",
                product_type="EuropeanVanillaOption",
                underlying="000300.SH",
                quantity=100,
                product_kwargs={"strike": 4000.0},
            )
            session.add(position)
            session.flush()
            if len(scoped_ids) < 2:
                scoped_ids.append(position.id)
        session.commit()
        portfolio_id = portfolio.id

    monkeypatch.setattr(
        "app.services.domains.risk.run",
        lambda **kwargs: {"status": "queued", "risk_run_id": 1, "task_id": 2},
    )

    result = run_batch_pricing_tool.invoke(
        {
            "portfolio_id": portfolio_id,
            "method": "summary",
            "position_ids": scoped_ids,
        },
        config=_DESK,  # type: ignore[arg-type]
    )

    assert result["status"] == "queued"

"""@tool wrappers for the backtest domain. Thin LLM adapters."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services import backtest_runner
from app.models import BacktestRun


class RunBacktestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    start_date: str
    end_date: str
    pricing_parameter_profile_id: int | None = None
    position_ids: list[int] | None = None
    engine: str = "quad"
    vol_source: str = "realized"
    vol_window: int = 20
    config: dict = Field(default_factory=dict)


class GetBacktestRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int


class ListBacktestRunsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("run_backtest", args_schema=RunBacktestInput)
def run_backtest_tool(
    portfolio_id: int,
    start_date: str,
    end_date: str,
    pricing_parameter_profile_id: int | None = None,
    position_ids: list[int] | None = None,
    engine: str = "quad",
    vol_source: str = "realized",
    vol_window: int = 20,
    config: dict | None = None,
) -> dict[str, Any]:
    """Queue an async, persisted backtest run for a portfolio over a date range.
    Returns the queued run id and task id; read results later with get_backtest_run."""
    spec = {
        "start": start_date,
        "end": end_date,
        "engine": engine,
        "vol_source": vol_source,
        "vol_window": vol_window,
    }
    database.init_db()
    with database.SessionLocal() as session:
        run, task = backtest_runner.queue_backtest(
            session,
            portfolio_id=portfolio_id,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            spec=spec,
            config=config or {},
            position_ids=position_ids,
        )
        return {"run_id": run.id, "task_id": task.id, "status": run.status}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_backtest_run", args_schema=GetBacktestRunInput)
def get_backtest_run_tool(run_id: int) -> dict[str, Any]:
    """Fetch a backtest run's status, portfolio-level results, per-underlying breakdown,
    and artifact paths."""
    database.init_db()
    with database.SessionLocal() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            return {"error": f"Backtest run not found: {run_id}"}
        results = run.results or {}
        return {
            "run_id": run.id,
            "status": run.status,
            "portfolio": results.get("portfolio"),
            "by_underlying": results.get("by_underlying"),
            "artifacts": run.artifacts,
        }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_backtest_runs", args_schema=ListBacktestRunsInput)
def list_backtest_runs_tool(portfolio_id: int) -> dict[str, Any]:
    """List the most recent backtest runs for a portfolio (newest first, limit 20)."""
    database.init_db()
    with database.SessionLocal() as session:
        from sqlalchemy import select, desc

        stmt = (
            select(BacktestRun)
            .where(BacktestRun.portfolio_id == portfolio_id)
            .order_by(desc(BacktestRun.id))
            .limit(20)
        )
        runs = list(session.scalars(stmt))
        rows = []
        for run in runs:
            spec = run.spec or {}
            results = run.results or {}
            portfolio_summary = results.get("portfolio") or {}
            rows.append(
                {
                    "id": run.id,
                    "status": run.status,
                    "window": f"{spec.get('start', '')}..{spec.get('end', '')}",
                    "total_pnl": portfolio_summary.get("total_pnl"),
                }
            )
        return {"runs": rows}

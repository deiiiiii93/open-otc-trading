"""@tool wrappers for persisted Greeks Landscape runs."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import database
from app.models import GreekLandscapeRun, TaskRun
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.greeks_landscape import (
    execute_greeks_landscape_task,
    queue_greeks_landscape,
)
from app.services.task_runner import submit_async_task


class RunGreeksLandscapeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    engine_config_id: int | None = None
    position_ids: list[int] | None = None
    spot_min_pct: float = -30.0
    spot_max_pct: float = 30.0
    spot_nodes: int = Field(61, ge=3, le=501)


class GetGreeksLandscapeRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: int


class GetLatestGreeksLandscapeRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int


def _shape_run(
    session: Session,
    run: GreekLandscapeRun,
    *,
    include_task_id: bool = False,
) -> dict[str, Any]:
    payload = {
        "found": True,
        "run_id": run.id,
        "portfolio_id": run.portfolio_id,
        "pricing_parameter_profile_id": run.pricing_parameter_profile_id,
        "engine_config_id": run.engine_config_id,
        "status": run.status,
        "config": run.config or {},
        "results": run.results or {},
        "excluded_positions": run.excluded_positions or [],
        "resolved_position_ids": run.resolved_position_ids,
        "created_at": run.created_at.isoformat(),
    }
    if include_task_id:
        task = (
            session.query(TaskRun)
            .filter(TaskRun.greeks_landscape_run_id == run.id)
            .order_by(TaskRun.id.desc())
            .first()
        )
        payload["task_id"] = task.id if task is not None else None
    return payload


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("run_greeks_landscape", args_schema=RunGreeksLandscapeInput)
def run_greeks_landscape_tool(
    portfolio_id: int,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
    spot_min_pct: float = -30.0,
    spot_max_pct: float = 30.0,
    spot_nodes: int = 61,
) -> dict[str, Any]:
    """Queue a persisted Delta/Gamma landscape across a spot-shift grid."""
    database.init_db()
    with database.SessionLocal() as session:
        run, task = queue_greeks_landscape(
            session,
            portfolio_id=portfolio_id,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            engine_config_id=engine_config_id,
            position_ids=position_ids,
            spot_min_pct=spot_min_pct,
            spot_max_pct=spot_max_pct,
            spot_nodes=spot_nodes,
        )
        session.commit()
        payload = _shape_run(session, run, include_task_id=True)
        submit_async_task(execute_greeks_landscape_task, task.id, run.id)
        return payload


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_greeks_landscape_run", args_schema=GetGreeksLandscapeRunInput)
def get_greeks_landscape_run_tool(run_id: int) -> dict[str, Any]:
    """Read one persisted Greeks Landscape run, including curves and exclusions."""
    database.init_db()
    with database.SessionLocal() as session:
        run = session.get(GreekLandscapeRun, run_id)
        if run is None:
            return {"found": False, "run_id": run_id, "message": "Greeks Landscape run not found."}
        return _shape_run(session, run)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_latest_greeks_landscape_run", args_schema=GetLatestGreeksLandscapeRunInput)
def get_latest_greeks_landscape_run_tool(portfolio_id: int) -> dict[str, Any]:
    """Read the newest persisted Greeks Landscape run for a portfolio."""
    database.init_db()
    with database.SessionLocal() as session:
        run = (
            session.query(GreekLandscapeRun)
            .filter(GreekLandscapeRun.portfolio_id == portfolio_id)
            .order_by(GreekLandscapeRun.created_at.desc(), GreekLandscapeRun.id.desc())
            .first()
        )
        if run is None:
            return {
                "found": False,
                "portfolio_id": portfolio_id,
                "message": "No persisted Greeks Landscape run exists for this portfolio.",
            }
        return _shape_run(session, run)

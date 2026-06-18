"""@tool wrappers for the scenario-test domain. Thin LLM adapters."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import scenario_catalog
from app.services import scenario_test_runner
from app.models import ScenarioTestRun


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunScenarioTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int
    pricing_parameter_profile_id: int | None = None
    position_ids: list[int] | None = None
    predefined: list[str] = Field(default_factory=list)
    custom: list[dict] = Field(default_factory=list)
    scenario_set: str | None = None
    config: dict = Field(default_factory=dict)


class GetScenarioTestRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: int


class SaveScenarioSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    custom: list[dict]


class GenerateScenarioSetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    combine_mode: str = "cross_product"
    axes: list[dict] = Field(default_factory=list)


def _estimate_run_seconds(tool_input: Any) -> float:
    if not isinstance(tool_input, dict):
        return 0.0
    n_scen = len(tool_input.get("predefined", []) or []) + len(tool_input.get("custom", []) or [])
    return max(1, n_scen) * 2.0


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_scenario_library", args_schema=_Empty)
def list_scenario_library_tool() -> dict[str, Any]:
    """List predefined stress scenarios and saved scenario sets available for a scenario test."""
    return {"predefined": scenario_catalog.list_predefined(),
            "saved_sets": scenario_catalog.list_sets(),
            "sets": scenario_catalog.list_sets_full()}


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_estimate_run_seconds)
@tool("run_scenario_test", args_schema=RunScenarioTestInput)
def run_scenario_test_tool(
    portfolio_id: int,
    pricing_parameter_profile_id: int | None = None,
    position_ids: list[int] | None = None,
    predefined: list[str] | None = None,
    custom: list[dict] | None = None,
    scenario_set: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Queue an async, persisted scenario (stress) test for a portfolio against a
    pricing parameter profile. Scenarios come from predefined names, custom specs,
    or a saved set. Returns the queued run id; read it later with get_scenario_test_run."""
    database.init_db()
    with database.SessionLocal() as session:
        run, task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=portfolio_id,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            scenario_request={"predefined": predefined or [], "custom": custom or [],
                              "scenario_set": scenario_set},
            config=config or {},
            position_ids=position_ids,
        )
        return {"run_id": run.id, "task_id": task.id, "status": run.status}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_scenario_test_run", args_schema=GetScenarioTestRunInput)
def get_scenario_test_run_tool(run_id: int) -> dict[str, Any]:
    """Fetch a scenario test run's status, results, excluded positions, and artifacts."""
    database.init_db()
    with database.SessionLocal() as session:
        run = session.get(ScenarioTestRun, run_id)
        if run is None:
            return {"error": f"Scenario test run not found: {run_id}"}
        return {"id": run.id, "status": run.status, "results": run.results,
                "excluded_positions": run.excluded_positions, "artifacts": run.artifacts}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("save_scenario_set", args_schema=SaveScenarioSetInput)
def save_scenario_set_tool(name: str, custom: list[dict]) -> dict[str, Any]:
    """Save a reusable named set of custom scenarios."""
    scenarios = [scenario_catalog.build_custom(s) for s in custom]
    path = scenario_catalog.save_set(name, scenarios)
    return {"name": name, "path": path}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_scenario_set", args_schema=GenerateScenarioSetInput)
def generate_scenario_set_tool(
    name: str, combine_mode: str = "cross_product", axes: list[dict] | None = None
) -> dict[str, Any]:
    """Generate and save a named Scenario Set as the cross product of parameter
    axes, each defined by (start, stop, step) over spot/vol/rate/dividend. Each
    generated scenario shocks every axis together (one grid cell). Returns the
    saved set name and scenario count; run it later via run_scenario_test with
    scenario_set=name."""
    spec = {"name": name, "combine_mode": combine_mode, "axes": axes or []}
    specs = scenario_catalog.generate_grid(spec)
    scenarios = [scenario_catalog.build_custom(s) for s in specs]
    path = scenario_catalog.save_set(name, scenarios, grid_spec=spec)
    return {"name": name, "num_scenarios": len(scenarios), "path": path}

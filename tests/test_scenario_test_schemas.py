from datetime import datetime
from types import SimpleNamespace

from app.schemas import (
    ScenarioTestRunRequest, ScenarioTestRunOut, ScenarioSpec, ScenarioStressSpec,
)


def test_run_request_defaults():
    req = ScenarioTestRunRequest(portfolio_id=1, predefined=["market_crash"])
    assert req.config.greeks_method == "numerical"
    assert req.config.export_formats == ["json"]
    assert req.custom == []


def test_run_request_with_custom():
    req = ScenarioTestRunRequest(
        portfolio_id=1,
        custom=[ScenarioSpec(name="C1", stresses=[ScenarioStressSpec(param="spot", value=-0.1)])],
    )
    assert req.custom[0].stresses[0].param == "spot"


def test_run_out_from_attributes():
    row = SimpleNamespace(
        id=5, portfolio_id=1, pricing_parameter_profile_id=None, status="completed",
        scenario_spec={"predefined": ["market_crash"]}, config={}, results={"baseline_value": 1.0},
        excluded_positions=[], artifacts={}, resolved_position_ids=[1, 2],
        created_at=datetime(2026, 6, 8),
    )
    out = ScenarioTestRunOut.model_validate(row)
    assert out.id == 5 and out.status == "completed"
    assert out.results == {"baseline_value": 1.0}


def test_scenario_grid_request_defaults():
    from app.schemas import ScenarioGridRequest
    req = ScenarioGridRequest(name="g", axes=[{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}])
    assert req.combine_mode == "cross_product"
    assert req.axes[0].stress_type == "PERCENTAGE"
    assert req.axes[0].level == "portfolio"


def test_scenario_set_summary_out_shape():
    from app.schemas import ScenarioSetSummaryOut
    out = ScenarioSetSummaryOut(name="g", num_scenarios=6, combine_mode="cross_product",
                                axes_summary="spot × vol", has_grid=True,
                                axes=[{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}])
    assert out.has_grid is True
    assert out.axes[0].param == "spot"

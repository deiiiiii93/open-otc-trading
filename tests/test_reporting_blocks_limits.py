import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block


def _evaluation(scope_label, status, observed=800.0, utilization=1.34,
                headroom=-200.0, boundary="hard_upper", reason=None):
    return {
        "scope_label": scope_label, "status": status,
        "observed_value": observed, "utilization": utilization,
        "headroom": headroom, "governing_boundary": boundary,
        "reason": reason, "reason_code": None,
        "coverage_ratio": 1.0,
    }


def _incident(incident_id=1, status="open", severity="hard"):
    return {
        "incident_id": incident_id, "scope_label": "Desk Control Book / net delta",
        "severity": severity, "status": status,
        "first_seen_at": "2026-08-05T09:00:00", "last_seen_at": "2026-08-06T09:00:00",
        "owner": "desk_user", "row_version": 3,
    }


def test_all_four_blocks_are_registered_with_declared_shapes():
    assert get_block("limits.utilization").shape.value == "scalars_with_prior"
    assert get_block("limits.breaches").shape.value == "items"
    assert get_block("limits.incidents").shape.value == "rows"
    assert get_block("scenario.latest_grid").shape.value == "rows"


def test_utilization_reports_rows_and_the_worst_offender(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "breach", utilization=1.34),
         _evaluation("vega", "ok", utilization=0.42, headroom=500.0)],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.utilization", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert len(result.data["rows"]) == 2
    assert result.data["worst_utilization"] == pytest.approx(1.34)
    assert result.provenance["monitoring_run_id"] == 2


def test_breaches_are_items_when_present(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "breach"), _evaluation("vega", "ok")],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert [item["scope_label"] for item in result.data["items"]] == ["net delta"]
    assert result.data["indeterminate_count"] == 0


def test_no_breaches_is_EMPTY_not_unavailable(monkeypatch):
    """The check ran and found nothing. That is a real, affirmative result."""
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "ok", utilization=0.3)],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "empty"
    assert "no limit" in result.reason.lower()


def test_no_monitoring_run_is_UNAVAILABLE_not_empty(monkeypatch):
    """The check did not run. That must never read as 'the book is fine'."""
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (None, {}))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "unavailable"
    assert "monitoring" in result.reason.lower()


def test_indeterminate_evaluations_are_counted_not_treated_as_passes(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_evaluations", lambda ctx: (
        [_evaluation("net delta", "unknown", reason="missing:spot"),
         _evaluation("vega", "incomplete_scope", reason="2 of 5 priced")],
        {"monitoring_run_id": 2},
    ))
    result = resolve_block("limits.breaches", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["items"] == []
    assert result.data["indeterminate_count"] == 2
    assert len(result.data["indeterminate"]) == 2


def test_incidents_are_rows_with_row_version_for_optimistic_locking(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_incidents",
                        lambda ctx: ([_incident()], {"portfolio_id": 2}))
    result = resolve_block("limits.incidents", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["rows"][0]["row_version"] == 3
    assert result.data["open_count"] == 1


def test_no_incidents_is_empty(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_incidents", lambda ctx: ([], {}))
    result = resolve_block("limits.incidents", BlockContext(portfolio_id=2))
    assert result.status == "empty"


def test_scenario_grid_is_unavailable_while_no_stress_runs_exist(monkeypatch):
    from app.services.reporting.blocks import limits as limit_blocks

    monkeypatch.setattr(limit_blocks, "_load_latest_scenario_run",
                        lambda ctx: (None, {}))
    result = resolve_block("scenario.latest_grid", BlockContext(portfolio_id=2))
    assert result.status == "unavailable"
    assert "scenario" in result.reason.lower()


class _FakeScenarioRun:
    def __init__(self, results):
        self.id = 7
        self.results = results


def _shaped_results(scenarios):
    """A payload shaped like services/domains/scenario_test.py::shape_results.

    Harvested from the real producer's return keys rather than invented: the
    grid lives under "scenarios", and reading a key it does not publish is
    what made this block claim a populated run was empty.
    """
    return {
        "baseline_value": 57334.67,
        "baseline_greeks": {"delta": 12.0},
        "scenarios": scenarios,
        "worst_scenario": "spot -20%",
        "best_scenario": "spot +10%",
        "risk_summary": {"max_loss": -8100.0},
        "var_cvar": {"var": -5000.0, "confidence": 0.95},
        "num_scenarios": len(scenarios),
        "execution_time": 0.42,
    }


def _scenario(name="spot -20%", pnl=-8100.0):
    return {
        "name": name,
        "portfolio_value": 49234.67,
        "pnl": pnl,
        "pnl_pct": -0.141,
        "greeks": {"delta": 9.5, "gamma": -0.2},
        "underlying_results": {"AAPL": {"pnl": pnl}},
        "position_results": [{"position_id": 8, "pnl": pnl}],
        "execution_time": 0.21,
    }


def test_scenario_grid_reads_the_key_the_runner_actually_writes(monkeypatch):
    """The stress grid is persisted under "scenarios", not "rows"/"cells"."""
    from app.services.reporting.blocks import limits as limit_blocks

    run = _FakeScenarioRun(_shaped_results([_scenario(), _scenario("spot +10%", 2400.0)]))
    monkeypatch.setattr(limit_blocks, "_load_latest_scenario_run",
                        lambda ctx: (run, {"scenario_test_run_id": 7}))
    result = resolve_block("scenario.latest_grid", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert len(result.data["rows"]) == 2
    assert result.data["rows"][0]["name"] == "spot -20%"
    assert result.data["rows"][0]["pnl"] == -8100.0
    assert result.data["worst_scenario"] == "spot -20%"
    assert result.provenance["scenario_test_run_id"] == 7


def test_scenario_grid_rows_are_flat_scalars_the_table_can_render(monkeypatch):
    """autoColumns() stringifies non-numbers, so a nested dict renders as
    "[object Object]". Rows must carry scalars only."""
    from app.services.reporting.blocks import limits as limit_blocks

    run = _FakeScenarioRun(_shaped_results([_scenario()]))
    monkeypatch.setattr(limit_blocks, "_load_latest_scenario_run",
                        lambda ctx: (run, {}))
    result = resolve_block("scenario.latest_grid", BlockContext(portfolio_id=2))
    row = result.data["rows"][0]
    assert all(
        value is None or isinstance(value, (str, int, float))
        for value in row.values()
    ), row
    assert "position_results" not in row


def test_an_unreadable_scenario_payload_is_unavailable_not_empty(monkeypatch):
    """A payload with no "scenarios" key means WE COULD NOT READ THE RUN.

    Returning `empty` there would report "we stress-tested and found nothing"
    for a run whose contents we simply failed to parse — the reassuring branch
    by default, which is exactly what the tri-state exists to prevent. The
    runner really does persist this shape mid-flight
    (scenario_test_runner.py sets results={"source_metadata": ...}).
    """
    from app.services.reporting.blocks import limits as limit_blocks

    run = _FakeScenarioRun({"source_metadata": {"as_of": "2026-08-06"}})
    monkeypatch.setattr(limit_blocks, "_load_latest_scenario_run",
                        lambda ctx: (run, {}))
    result = resolve_block("scenario.latest_grid", BlockContext(portfolio_id=2))
    assert result.status == "unavailable"
    assert "could not" in result.reason.lower()


def test_a_run_that_stressed_nothing_is_genuinely_empty(monkeypatch):
    """"scenarios": [] IS a ran-and-found-nothing result, so `empty` is right."""
    from app.services.reporting.blocks import limits as limit_blocks

    run = _FakeScenarioRun(_shaped_results([]))
    monkeypatch.setattr(limit_blocks, "_load_latest_scenario_run",
                        lambda ctx: (run, {}))
    result = resolve_block("scenario.latest_grid", BlockContext(portfolio_id=2))
    assert result.status == "empty"

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

import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block


def _metrics() -> dict:
    return {
        "valuation_as_of": "2026-06-24T00:00:00",
        "position_set_hash": "sha256:f69b",
        "currencies": ["CNY"],
        "totals": {
            "market_value": 9246.35, "delta_cash": 57334.67, "gamma_cash": 1842.88,
            "vega": 276.43, "theta": -27.20, "rho": 240.44, "rho_q": -286.67,
            "pnl": 9246.35, "gross_notional": 100000.0, "one_day_var_proxy": 1200.0,
        },
        "positions": [
            {"position_id": 23, "underlying": "AAPL", "product_type": "EuropeanVanillaOption",
             "quantity": 1000.0, "market_value": 9246.35, "delta_cash": 57334.67,
             "gamma_cash": 1842.88, "vega": 276.43, "theta": -27.20, "rho": 240.44,
             "rho_q": -286.67, "delta": 573.35, "gamma": 18.43, "quote_age_days": 0,
             "pricing_ok": True, "greeks_ok": True, "currency": "CNY"},
            {"position_id": 24, "underlying": "TSLA", "product_type": "BarrierOption",
             "quantity": 200.0, "market_value": 1000.0, "delta_cash": 2000.0,
             "gamma_cash": 100.0, "vega": 50.0, "theta": -5.0, "rho": 10.0,
             "rho_q": -12.0, "delta": 20.0, "gamma": 1.0, "quote_age_days": 3,
             "pricing_ok": True, "greeks_ok": True, "currency": "CNY"},
        ],
        "coverage": {"coverage_count": 2, "total_count": 3},
        "source_metadata": {
            "metric_contract": {"contract_id": "limits-risk_run-metrics/v1"},
            "market_evidence_manifest": {
                "evidence_complete": False,
                "missing_evidence": ["position 25: spot"],
                "positions": [],
            },
        },
    }


@pytest.fixture
def patched_metrics(monkeypatch):
    """Point the risk blocks at an in-memory metrics payload, no DB."""
    from app.services.reporting.blocks import risk as risk_blocks

    def _fake(ctx):
        return _metrics(), {"risk_run_id": 36, "valuation_as_of": "2026-06-24T00:00:00"}

    monkeypatch.setattr(risk_blocks, "_load_metrics", _fake)
    return _fake


def test_all_five_risk_blocks_are_registered_with_declared_shapes():
    assert get_block("risk.totals").shape.value == "scalars"
    assert get_block("risk.exposure_by_underlying").shape.value == "series"
    assert get_block("risk.greeks_by_bucket").shape.value == "position_greeks"
    assert get_block("risk.exposure_diff").shape.value == "scalars_with_prior"
    assert get_block("coverage.evidence").shape.value == "scalars"


def test_risk_totals_returns_metrics_and_currency(patched_metrics):
    result = resolve_block("risk.totals", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["metrics"]["delta_cash"] == pytest.approx(57334.67)
    assert result.data["currency"] == "CNY"
    assert result.provenance["risk_run_id"] == 36


def test_exposure_by_underlying_aggregates_and_sorts_by_absolute_delta(patched_metrics):
    result = resolve_block("risk.exposure_by_underlying", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["x_key"] == "underlying"
    assert result.data["chart_type"] == "bar"
    assert [row["underlying"] for row in result.data["series"]] == ["AAPL", "TSLA"]
    assert result.data["series"][0]["delta_cash"] == pytest.approx(57334.67)


def test_greeks_by_bucket_passes_position_rows_through(patched_metrics):
    result = resolve_block("risk.greeks_by_bucket", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert len(result.data["positions"]) == 2
    assert result.data["positions"][0]["position_id"] == 23


def test_coverage_evidence_surfaces_incompleteness(patched_metrics):
    result = resolve_block("coverage.evidence", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["priced"] == 2
    assert result.data["total"] == 3
    assert result.data["ratio"] == pytest.approx(2 / 3)
    assert result.data["evidence_complete"] is False
    assert result.data["missing_evidence"] == ["position 25: spot"]
    assert result.data["max_quote_age_days"] == 3


def test_blocks_are_unavailable_when_no_risk_run_exists(monkeypatch):
    from app.services.reporting.blocks import risk as risk_blocks

    monkeypatch.setattr(risk_blocks, "_load_metrics", lambda ctx: (None, {}))
    result = resolve_block("risk.totals", BlockContext(portfolio_id=999))
    assert result.status == "unavailable"
    assert "risk run" in result.reason


def test_exposure_diff_is_unavailable_without_a_comparison_run(monkeypatch):
    from app.services.reporting.blocks import risk as risk_blocks

    monkeypatch.setattr(risk_blocks, "_load_run_pair_metrics",
                        lambda ctx: (None, None, {}))
    result = resolve_block("risk.exposure_diff",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "unavailable"


def test_exposure_diff_reports_changes_when_two_runs_exist(monkeypatch):
    from app.services.reporting.blocks import risk as risk_blocks

    before = _metrics()
    after = _metrics()
    after["totals"] = dict(after["totals"], delta_cash=60000.0)
    monkeypatch.setattr(
        risk_blocks, "_load_run_pair_metrics",
        lambda ctx: (before, after, {"risk_run_id": 36, "compare_to_run_id": 35}),
    )
    result = resolve_block("risk.exposure_diff",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "ok"
    assert result.data["metrics"]["delta_cash"]["change"] == pytest.approx(
        60000.0 - 57334.67
    )

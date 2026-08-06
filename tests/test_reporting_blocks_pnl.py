import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.contracts import BlockContext
from app.services.reporting.registry import get_block, resolve_block

CONTRACT = {"contract_id": "limits-risk_run-metrics/v1"}


def _metrics(*, valuation_as_of, positions, spot_by_id=None):
    spot_by_id = spot_by_id or {}
    return {
        "valuation_as_of": valuation_as_of,
        "position_set_hash": "sha256:aaa",
        "totals": {"market_value": sum(p["market_value"] for p in positions)},
        "positions": positions,
        "coverage": {"coverage_count": len(positions), "total_count": len(positions)},
        "source_metadata": {
            "metric_contract": CONTRACT,
            "market_evidence_manifest": {
                "positions": [
                    {"position_id": p["position_id"],
                     "resolved_market": {
                         "spot": spot_by_id.get(p["position_id"], 100.0),
                         "volatility": 0.30, "rate": 0.04, "dividend_yield": 0.005}}
                    for p in positions
                ]
            },
        },
    }


def _pos(position_id, market_value, *, underlying="AAPL", delta=0.0,
         price=10.0, quantity=100.0):
    return {"position_id": position_id, "underlying": underlying,
            "market_value": market_value, "price": price, "quantity": quantity,
            "delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
            "rho": 0.0, "rho_q": 0.0, "pricing_ok": True, "greeks_ok": True}


@pytest.fixture
def two_runs(monkeypatch):
    before = _metrics(valuation_as_of="2026-08-04T00:00:00",
                      positions=[_pos(1, 10_000.0, delta=500.0),
                                 _pos(2, 5_000.0, underlying="TSLA")],
                      spot_by_id={1: 100.0, 2: 100.0})
    after = _metrics(valuation_as_of="2026-08-04T00:00:00",
                     positions=[_pos(1, 12_000.0, delta=500.0),
                                _pos(2, 4_500.0, underlying="TSLA")],
                     spot_by_id={1: 104.0, 2: 100.0})

    from app.services.reporting.blocks import pnl as pnl_blocks

    monkeypatch.setattr(
        pnl_blocks, "_load_run_pair_metrics",
        lambda ctx: (before, after, {"risk_run_id": 36, "compare_to_run_id": 35}),
    )
    return before, after


def test_all_four_pnl_blocks_are_registered_with_declared_shapes():
    assert get_block("pnl.daily").shape.value == "scalars_with_prior"
    assert get_block("pnl.explain").shape.value == "waterfall"
    assert get_block("pnl.by_position").shape.value == "rows"
    assert get_block("pnl.inception").shape.value == "scalars"


def test_pnl_daily_reports_the_market_value_move(two_runs):
    result = resolve_block("pnl.daily",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "ok"
    mv = result.data["metrics"]["market_value"]
    assert mv["before"] == pytest.approx(15_000.0)
    assert mv["after"] == pytest.approx(16_500.0)
    assert mv["change"] == pytest.approx(1_500.0)


def test_pnl_daily_is_unavailable_without_a_comparison(monkeypatch):
    from app.services.reporting.blocks import pnl as pnl_blocks

    monkeypatch.setattr(pnl_blocks, "_load_run_pair_metrics",
                        lambda ctx: (None, None, {}))
    result = resolve_block("pnl.daily",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "unavailable"
    assert "prior" in result.reason


def test_pnl_explain_attributes_the_spot_move_to_delta(two_runs):
    result = resolve_block("pnl.explain",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    assert result.status == "ok"
    # Position 1: delta 500 x spot +4 = 2000. Position 2 did not move on spot,
    # so its -500 is unexplained residual.
    assert result.data["buckets"]["delta"] == pytest.approx(2000.0)
    assert result.data["actual"] == pytest.approx(1500.0)
    assert result.data["residual"] == pytest.approx(-500.0)
    assert result.data["convention"] == "start_of_period_greeks"


def test_pnl_explain_flags_a_residual_over_the_threshold(two_runs):
    result = resolve_block("pnl.explain",
                           BlockContext(portfolio_id=2, compare_to_run_id=35))
    # |-500| / |1500| = 0.33 > 0.10
    assert result.data["residual_exceeds_threshold"] is True


def test_pnl_by_position_sorts_by_absolute_move_and_truncates(two_runs):
    result = resolve_block(
        "pnl.by_position",
        BlockContext(portfolio_id=2, compare_to_run_id=35, params={"top_n": 1}),
    )
    assert result.status == "ok"
    assert len(result.data["rows"]) == 1
    assert result.data["rows"][0]["position_id"] == 1
    assert result.data["rows"][0]["change"] == pytest.approx(2000.0)
    assert result.data["truncated_to"] == 1


def test_pnl_inception_excludes_positions_without_a_basis(monkeypatch):
    from app.services.reporting.blocks import pnl as pnl_blocks

    metrics = _metrics(valuation_as_of="2026-08-04T00:00:00",
                       positions=[_pos(1, 1500.0, price=15.0, quantity=100.0),
                                  _pos(2, 1800.0, price=9.0, quantity=200.0)])
    monkeypatch.setattr(pnl_blocks, "_load_metrics",
                        lambda ctx: (metrics, {"risk_run_id": 36}))
    monkeypatch.setattr(pnl_blocks, "_entry_prices",
                        lambda ctx, metrics: {1: 10.0, 2: 0.0})

    result = resolve_block("pnl.inception", BlockContext(portfolio_id=2))
    assert result.status == "ok"
    assert result.data["total"] == pytest.approx(500.0)
    assert result.data["covered_count"] == 1
    assert result.data["basis_missing_count"] == 1
    assert result.data["basis_missing"] == [2]

import pytest

from app.services.pnl.explain import (
    RESIDUAL_WARN_RATIO,
    UnsupportedMetricContract,
    explain_diff,
)
from app.services.pnl.snapshot_diff import diff_metrics

CONTRACT = {"contract_id": "limits-risk_run-metrics/v1", "version": 1}


def _run(*, valuation_as_of, spot, vol, rate=0.04, div=0.005,
         market_value=0.0, greeks=None, pricing_ok=True, greeks_ok=True):
    g = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
         "rho": 0.0, "rho_q": 0.0}
    g.update(greeks or {})
    return {
        "valuation_as_of": valuation_as_of,
        "position_set_hash": "sha256:aaa",
        "totals": {"market_value": market_value},
        "positions": [{
            "position_id": 1, "underlying": "AAPL",
            "market_value": market_value,
            "pricing_ok": pricing_ok, "greeks_ok": greeks_ok, **g,
        }],
        "coverage": {"coverage_count": 1, "total_count": 1},
        "source_metadata": {
            "metric_contract": CONTRACT,
            "market_evidence_manifest": {"positions": [{
                "position_id": 1,
                "resolved_market": {"spot": spot, "volatility": vol,
                                    "rate": rate, "dividend_yield": div},
            }]},
        },
    }


def _explain(before, after):
    return explain_diff(before, after, diff_metrics(before, after))


def test_pure_spot_bump_is_fully_explained_by_delta():
    # delta 500 units, spot +4 => 2000 of MV move, nothing else changes.
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=10_000.0, greeks={"delta": 500.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30,
                 market_value=12_000.0, greeks={"delta": 500.0})

    result = _explain(before, after)

    assert result["buckets"]["delta"] == pytest.approx(2000.0)
    assert result["buckets"]["vega"] == pytest.approx(0.0)
    assert result["buckets"]["theta"] == pytest.approx(0.0)
    assert result["actual"] == pytest.approx(2000.0)
    assert result["residual"] == pytest.approx(0.0)
    assert result["residual_ratio"] == pytest.approx(0.0)


def test_gamma_contributes_half_gamma_times_move_squared():
    # gamma 10, spot +4 => 0.5 * 10 * 16 = 80 on top of delta.
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=0.0, greeks={"delta": 500.0, "gamma": 10.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30,
                 market_value=2080.0, greeks={"delta": 500.0, "gamma": 10.0})

    result = _explain(before, after)

    assert result["buckets"]["gamma"] == pytest.approx(80.0)
    assert result["residual"] == pytest.approx(0.0)


def test_vega_is_per_vol_point_not_per_unit_of_sigma():
    # vega 276.43 per 1 vol-point; sigma 0.30 -> 0.32 is +2 vol points => 552.86
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=0.0, greeks={"vega": 276.43})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.32,
                 market_value=552.86, greeks={"vega": 276.43})

    result = _explain(before, after)

    assert result["buckets"]["vega"] == pytest.approx(552.86, rel=1e-6)
    assert result["residual"] == pytest.approx(0.0, abs=1e-6)


def test_theta_uses_elapsed_valuation_days():
    # theta -27.2 per day, 3 days elapsed => -81.6
    before = _run(valuation_as_of="2026-08-01T00:00:00", spot=100.0, vol=0.30,
                  market_value=0.0, greeks={"theta": -27.2})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 market_value=-81.6, greeks={"theta": -27.2})

    result = _explain(before, after)

    assert result["buckets"]["theta"] == pytest.approx(-81.6)
    assert result["residual"] == pytest.approx(0.0, abs=1e-9)


def test_rho_is_per_one_percent_of_rate():
    # rho 240 per 1%; rate 0.04 -> 0.05 is +1% => 240
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  rate=0.04, market_value=0.0, greeks={"rho": 240.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 rate=0.05, market_value=240.0, greeks={"rho": 240.0})

    result = _explain(before, after)

    assert result["buckets"]["rho"] == pytest.approx(240.0)
    assert result["residual"] == pytest.approx(0.0, abs=1e-9)


def test_unexplained_move_lands_in_residual_not_in_a_bucket():
    # No market move at all, but MV jumped 5000 => entirely residual.
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=10_000.0, greeks={"delta": 500.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 market_value=15_000.0, greeks={"delta": 500.0})

    result = _explain(before, after)

    assert result["explained"] == pytest.approx(0.0)
    assert result["residual"] == pytest.approx(5000.0)
    assert result["residual_ratio"] == pytest.approx(1.0)
    assert result["residual_ratio"] > RESIDUAL_WARN_RATIO


def test_position_failing_pricing_in_either_run_is_excluded_and_counted():
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=10_000.0, greeks={"delta": 500.0})
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30,
                 market_value=12_000.0, greeks={"delta": 500.0}, greeks_ok=False)

    result = _explain(before, after)

    assert result["excluded"] == [
        {"position_id": 1, "reason": "greeks_ok is false in the after run"}
    ]
    assert result["buckets"]["delta"] == pytest.approx(0.0)
    assert result["by_position"] == {}


def test_unknown_metric_contract_raises_rather_than_guessing_units():
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30)
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=104.0, vol=0.30)
    after["source_metadata"]["metric_contract"] = {
        "contract_id": "something-else/v9", "version": 9
    }

    with pytest.raises(UnsupportedMetricContract):
        _explain(before, after)


def test_residual_ratio_is_none_when_nothing_moved():
    before = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                  market_value=1000.0)
    after = _run(valuation_as_of="2026-08-04T00:00:00", spot=100.0, vol=0.30,
                 market_value=1000.0)

    result = _explain(before, after)

    assert result["actual"] == pytest.approx(0.0)
    assert result["residual_ratio"] is None

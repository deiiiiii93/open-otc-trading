from app.services.pnl.snapshot_diff import diff_metrics


def _metrics(
    *,
    valuation_as_of: str,
    positions: list[dict],
    totals: dict | None = None,
    position_set_hash: str = "sha256:aaa",
    coverage: dict | None = None,
) -> dict:
    """Build a minimal risk_runs.metrics payload for tests."""
    return {
        "valuation_as_of": valuation_as_of,
        "position_set_hash": position_set_hash,
        "totals": totals or {"market_value": sum(p["market_value"] for p in positions)},
        "positions": positions,
        "coverage": coverage or {
            "coverage_count": len(positions),
            "total_count": len(positions),
        },
        "source_metadata": {
            "market_evidence_manifest": {
                "positions": [
                    {
                        "position_id": p["position_id"],
                        "resolved_market": p.get(
                            "resolved_market",
                            {"spot": 100.0, "volatility": 0.30, "rate": 0.04,
                             "dividend_yield": 0.005},
                        ),
                    }
                    for p in positions
                ]
            }
        },
    }


def _pos(position_id: int, market_value: float, **extra) -> dict:
    row = {
        "position_id": position_id,
        "underlying": "AAPL",
        "market_value": market_value,
        "delta": 0.0,
        "gamma": 0.0,
        "vega": 0.0,
        "theta": 0.0,
        "rho": 0.0,
        "rho_q": 0.0,
        "pricing_ok": True,
        "greeks_ok": True,
    }
    row.update(extra)
    return row


def test_totals_change_is_after_minus_before():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 1000.0)])
    after = _metrics(valuation_as_of="2026-08-05T00:00:00", positions=[_pos(1, 1250.0)])

    result = diff_metrics(before, after)

    mv = result["totals"]["market_value"]
    assert mv["before"] == 1000.0
    assert mv["after"] == 1250.0
    assert mv["change"] == 250.0
    assert mv["pct_change"] == 25.0


def test_added_and_removed_positions_land_in_membership_not_totals_noise():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00",
                      positions=[_pos(1, 1000.0), _pos(2, 500.0)])
    after = _metrics(valuation_as_of="2026-08-05T00:00:00",
                     positions=[_pos(1, 1000.0), _pos(3, 700.0)])

    result = diff_metrics(before, after)

    assert result["membership"]["added"] == [3]
    assert result["membership"]["removed"] == [2]
    assert result["membership"]["held"] == [1]
    # A held position with no move contributes no per-position change.
    assert result["positions"][1]["change"] == 0.0
    # Added/removed positions are NOT given a fabricated before/after.
    assert 2 not in result["positions"]
    assert 3 not in result["positions"]


def test_elapsed_days_comes_from_valuation_date_not_wall_clock():
    # Same valuation date, so zero elapsed time regardless of when computed.
    before = _metrics(valuation_as_of="2026-06-24T00:00:00", positions=[_pos(1, 10.0)])
    after = _metrics(valuation_as_of="2026-06-24T00:00:00", positions=[_pos(1, 12.0)])

    result = diff_metrics(before, after)

    assert result["as_of"]["elapsed_days"] == 0.0


def test_elapsed_days_spans_multiple_days():
    before = _metrics(valuation_as_of="2026-08-01T00:00:00", positions=[_pos(1, 10.0)])
    after = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 10.0)])

    result = diff_metrics(before, after)

    assert result["as_of"]["elapsed_days"] == 3.0


def test_composition_change_is_flagged_but_diff_still_produced():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00",
                      positions=[_pos(1, 1000.0)], position_set_hash="sha256:aaa")
    after = _metrics(valuation_as_of="2026-08-05T00:00:00",
                     positions=[_pos(1, 1100.0)], position_set_hash="sha256:bbb")

    result = diff_metrics(before, after)

    assert result["composition_changed"] is True
    assert result["totals"]["market_value"]["change"] == 100.0


def test_market_moves_are_captured_per_position():
    before = _metrics(
        valuation_as_of="2026-08-04T00:00:00",
        positions=[_pos(1, 1000.0, resolved_market={
            "spot": 100.0, "volatility": 0.30, "rate": 0.04, "dividend_yield": 0.005})],
    )
    after = _metrics(
        valuation_as_of="2026-08-05T00:00:00",
        positions=[_pos(1, 1000.0, resolved_market={
            "spot": 104.0, "volatility": 0.32, "rate": 0.04, "dividend_yield": 0.005})],
    )

    result = diff_metrics(before, after)

    assert result["market"][1]["spot"]["before"] == 100.0
    assert result["market"][1]["spot"]["after"] == 104.0
    assert result["market"][1]["spot"]["change"] == 4.0
    assert round(result["market"][1]["volatility"]["change"], 10) == 0.02


def test_coverage_regression_is_visible():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 10.0)],
                      coverage={"coverage_count": 5, "total_count": 5})
    after = _metrics(valuation_as_of="2026-08-05T00:00:00", positions=[_pos(1, 10.0)],
                     coverage={"coverage_count": 4, "total_count": 5})

    result = diff_metrics(before, after)

    assert result["coverage"]["before"] == {"priced": 5, "total": 5}
    assert result["coverage"]["after"] == {"priced": 4, "total": 5}
    assert result["coverage"]["regressed"] is True


def test_pct_change_is_none_when_before_is_zero():
    before = _metrics(valuation_as_of="2026-08-04T00:00:00", positions=[_pos(1, 0.0)])
    after = _metrics(valuation_as_of="2026-08-05T00:00:00", positions=[_pos(1, 50.0)])

    result = diff_metrics(before, after)

    assert result["totals"]["market_value"]["pct_change"] is None

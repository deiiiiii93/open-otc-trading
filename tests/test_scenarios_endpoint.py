from __future__ import annotations

from pathlib import Path

from test_api import make_client


def test_scenarios_endpoint_returns_grid(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios", json={"name": "Scenario Book", "base_currency": "CNY"}
    ).json()
    client.post(
        f"/api/portfolios/{portfolio['id']}/positions",
        json={
            "underlying": "CSI500",
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100,
                "option_type": "CALL",
                "maturity": 1,
                "contract_multiplier": 100,
            },
            "engine_name": "BlackScholesEngine",
            "quantity": 5,
            "entry_price": 8.0,
        },
    )

    res = client.post(
        "/api/risk/scenarios",
        json={"portfolio_id": portfolio["id"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["portfolio_id"] == portfolio["id"]
    # default 3 vol rows × 6 spot cols
    assert len(data["cells"]) == 3
    assert len(data["cells"][0]) == 6
    # base case (0 spot, 0 vol) should have pnl close to zero
    base_cell = next(
        c
        for row in data["cells"]
        for c in row
        if c["spot_shift_pct"] == 0.0 and c["vol_shift_abs"] == 0.0
    )
    assert abs(base_cell["pnl"]) < 1.0  # within rounding noise of base

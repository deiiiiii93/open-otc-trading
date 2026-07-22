from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings


@pytest.fixture()
def api_client(tmp_path) -> TestClient:
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/api.db",
        artifact_dir=tmp_path / "artifacts",
    )
    return TestClient(create_app(settings))


def _seed_underlying(symbol: str) -> None:
    from app.database import SessionLocal
    from app.services.underlyings import ensure_underlying

    with SessionLocal() as s:
        ensure_underlying(s, symbol, source="manual", status="active")
        s.commit()


def test_put_curve_roundtrip(api_client: TestClient) -> None:
    _seed_underlying("000300.SH")
    body = {
        "rate_curve": [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}],
        "volatility_curve": [{"tenor": "6M", "value": 0.22}],
    }
    resp = api_client.put("/api/underlying-pricing-defaults/000300.SH", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["rate_curve"] == [
        {"tenor": "3M", "value": 0.02},
        {"tenor": "1Y", "value": 0.05},
    ]
    assert data["volatility_curve"] == [{"tenor": "6M", "value": 0.22}]
    assert data["dividend_yield_curve"] is None
    # Round-trips through the list endpoint too.
    listed = api_client.get("/api/underlying-pricing-defaults").json()
    row = next(r for r in listed if r["underlying"] == "000300.SH")
    assert row["rate_curve"][0]["tenor"] == "3M"


def test_put_curve_rejects_unknown_label(api_client: TestClient) -> None:
    _seed_underlying("000300.SH")
    resp = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate_curve": [{"tenor": "4M", "value": 0.02}]},
    )
    assert resp.status_code == 400


def test_put_curve_rejects_nonpositive_vol(api_client: TestClient) -> None:
    _seed_underlying("000300.SH")
    resp = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"volatility_curve": [{"tenor": "6M", "value": 0.0}]},
    )
    assert resp.status_code == 400


def _seed_open_option_for_api(symbol: str, maturity: str, trade_id: str) -> None:
    from app.database import SessionLocal
    from app.models import Portfolio, Position
    from app.services.underlyings import ensure_underlying

    with SessionLocal() as s:
        portfolio = s.query(Portfolio).first() or Portfolio(name="Test")
        if portfolio.id is None:
            s.add(portfolio)
            s.flush()
        # Product-less (see _seed_open_option in test_pricing_curve_generation.py).
        s.add(Position(
            portfolio_id=portfolio.id, product_id=None, underlying=symbol,
            product_type="VanillaCall",
            product_kwargs={"underlying": symbol, "maturity_date": maturity},
            quantity=1.0, source_trade_id=trade_id, status="open",
            position_kind="otc", engine_name="quantark.vanilla", engine_kwargs={},
            source_payload={}, mapping_status="supported",
        ))
        inst = ensure_underlying(s, symbol, source="manual", status="active")
        inst.rate = 0.02
        inst.dividend_yield = 0.01
        inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
        s.commit()


def test_generate_from_curves_happy_path(api_client: TestClient) -> None:
    _seed_open_option_for_api("000300.SH", "2026-07-02", "T-API")
    resp = api_client.post(
        "/api/pricing-parameter-profiles/from-curves",
        json={"name": "Curve Run", "valuation_date": "2026-01-01T00:00:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "curve"
    assert body["rows"][0]["symbol"] == "000300.SH"
    assert body["rows"][0]["volatility"] == pytest.approx(0.22)


def test_generate_from_curves_no_positions_400(api_client: TestClient) -> None:
    resp = api_client.post("/api/pricing-parameter-profiles/from-curves", json={})
    assert resp.status_code == 400
    assert "no open positions" in str(resp.json()["detail"])

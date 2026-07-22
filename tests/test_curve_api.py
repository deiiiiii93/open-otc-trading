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

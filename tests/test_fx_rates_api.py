from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_create_list_delete_fx_rate():
    from app import database
    from app.schemas import FxRateCreate
    from app.services import fx as fx_svc

    with database.SessionLocal() as session:
        created = fx_svc.create_fx_rate(session, FxRateCreate(
            base_currency="USD", quote_currency="CNY", rate=7.2,
            as_of_date=datetime(2026, 6, 2), source="manual",
        ))
        session.commit()
        rid = created.id
        rows = fx_svc.list_fx_rates(session)
        assert len(rows) == 1 and rows[0].rate == 7.2
        fx_svc.delete_fx_rate(session, rid)
        session.commit()
        assert fx_svc.list_fx_rates(session) == []


from fastapi.testclient import TestClient


def _client():
    from app.main import create_app
    return TestClient(create_app())


def test_fx_rate_endpoints():
    client = _client()
    resp = client.post("/api/market-data/fx-rates", json={
        "base_currency": "USD", "quote_currency": "CNY", "rate": 7.1,
        "as_of_date": "2026-06-02T00:00:00", "source": "manual",
    })
    assert resp.status_code == 200
    rid = resp.json()["id"]

    listing = client.get("/api/market-data/fx-rates").json()
    assert any(r["id"] == rid and r["rate"] == 7.1 for r in listing)

    assert client.delete(f"/api/market-data/fx-rates/{rid}").status_code == 200
    assert all(r["id"] != rid for r in client.get("/api/market-data/fx-rates").json())


def test_fx_rate_akshare_endpoint(monkeypatch):
    # Mock the akshare fetch so no network is hit.
    monkeypatch.setattr("app.services.fx.fetch_akshare_fx_rate", lambda b, q: 7.33)
    client = _client()
    resp = client.post("/api/market-data/fx-rates/akshare", json={
        "base_currency": "USD", "quote_currency": "CNY",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["rate"] == 7.33 and body["source"] == "akshare"


def test_fx_rate_profile_fetch_creates_market_profile_and_fx_rate(monkeypatch):
    from app.schemas import MarketDataSnapshot

    def fake_snapshot(payload):
        return MarketDataSnapshot(
            name="USD/CNY AKShare spot",
            source="akshare",
            symbol="USD/CNY",
            asset_class="fx_rate",
            data={
                "rows": [
                    {
                        "date": "2026-06-02",
                        "open": 7.25,
                        "high": 7.25,
                        "low": 7.25,
                        "close": 7.25,
                        "volume": None,
                    }
                ],
                "latest": {"date": "2026-06-02", "close": 7.25},
                "spot": 7.25,
            },
            source_metadata={
                "source_name": "AKShare fx_spot_quote",
                "fallback": False,
                "base_currency": "USD",
                "quote_currency": "CNY",
            },
        )

    monkeypatch.setattr("app.main.fetch_akshare_snapshot", fake_snapshot)
    client = _client()

    resp = client.post("/api/market-data/profiles/akshare", json={
        "symbol": "USD/CNY",
        "asset_class": "fx_rate",
        "start_date": "2026-06-02",
        "end_date": "2026-06-02",
        "adjust": "spot",
    })

    assert resp.status_code == 200
    profile = resp.json()
    assert profile["symbol"] == "USD/CNY"
    assert profile["asset_class"] == "fx_rate"
    assert profile["data"]["spot"] == 7.25

    rates = client.get("/api/market-data/fx-rates").json()
    assert any(
        row["base_currency"] == "USD"
        and row["quote_currency"] == "CNY"
        and row["rate"] == 7.25
        and row["source"] == "akshare"
        for row in rates
    )

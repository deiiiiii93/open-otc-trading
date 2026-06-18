from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app import database
from app.config import Settings
from app.models import MarketDataProfile
from app.schemas import AkshareSnapshotRequest, MarketDataSnapshot
from app.services.domains import market_data as md_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_fetch_snapshot_builds_request_and_returns_model(monkeypatch):
    captured: dict[str, AkshareSnapshotRequest] = {}

    def fake_fetch(req: AkshareSnapshotRequest) -> MarketDataSnapshot:
        captured["req"] = req
        return MarketDataSnapshot(
            name="x",
            source="akshare",
            symbol=req.symbol,
            asset_class=req.asset_class,
            valuation_date=datetime.now(timezone.utc),
            data={"rows": [], "latest": None, "spot": None},
            source_metadata={"fallback": False},
        )

    monkeypatch.setattr(
        "app.services.domains.market_data._fetch_akshare_snapshot", fake_fetch
    )

    snapshot = md_svc.fetch_snapshot(
        symbol="000300",
        asset_class="index",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 10),
        use_proxy=False,
    )

    assert isinstance(snapshot, MarketDataSnapshot)
    req = captured["req"]
    assert req.symbol == "000300"
    assert req.asset_class == "index"
    assert req.start_date == "2025-01-01"
    assert req.end_date == "2025-01-10"
    assert req.use_proxy is False
    assert req.adjust == "qfq"


def test_fetch_snapshot_accepts_string_dates(monkeypatch):
    captured: dict[str, AkshareSnapshotRequest] = {}

    def fake_fetch(req: AkshareSnapshotRequest) -> MarketDataSnapshot:
        captured["req"] = req
        return MarketDataSnapshot(symbol=req.symbol, asset_class=req.asset_class)

    monkeypatch.setattr(
        "app.services.domains.market_data._fetch_akshare_snapshot", fake_fetch
    )

    md_svc.fetch_snapshot(
        symbol="000300",
        start_date="2025-01-01",
        end_date="2025-01-10",
    )
    assert captured["req"].start_date == "2025-01-01"
    assert captured["req"].end_date == "2025-01-10"


def _insert_profile(name: str, symbol: str = "000300") -> int:
    with database.SessionLocal() as session:
        profile = MarketDataProfile(
            name=name,
            source="akshare",
            symbol=symbol,
            asset_class="index",
            start_date="2025-01-01",
            end_date="2025-01-10",
            adjust="qfq",
        )
        session.add(profile)
        session.commit()
        return profile.id


def test_list_profiles_returns_orm_rows():
    _insert_profile("alpha")
    _insert_profile("beta", symbol="000905")
    rows = md_svc.list_profiles()
    assert len(rows) == 2
    assert all(isinstance(p, MarketDataProfile) for p in rows)
    assert [p.name for p in rows] == ["alpha", "beta"]


def test_get_profile_returns_row_or_none():
    pid = _insert_profile("alpha")
    fetched = md_svc.get_profile(profile_id=pid)
    assert fetched is not None
    assert fetched.name == "alpha"
    assert md_svc.get_profile(profile_id=9999) is None

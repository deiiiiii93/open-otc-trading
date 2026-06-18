from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.schemas import MarketDataSnapshot
from app.tools.market_data import fetch_market_snapshot_tool, list_market_data_profiles_tool


def test_fetch_market_snapshot_tool():
    snapshot = MarketDataSnapshot(
        name="Test",
        source="akshare",
        symbol="000852.SH",
        asset_class="index",
        valuation_date=datetime(2026, 5, 19, tzinfo=timezone.utc),
        data={"rows": [], "latest": None, "spot": None},
        source_metadata={"fallback": False},
    )
    with patch(
        "app.services.domains.market_data._fetch_akshare_snapshot",
        return_value=snapshot,
    ):
        result = fetch_market_snapshot_tool.invoke(
            {
                "symbol": "000852.SH",
                "asset_class": "index",
                "start_date": "2026-05-19",
                "end_date": "2026-05-19",
            }
        )
    assert result["symbol"] == "000852.SH"
    assert result["asset_class"] == "index"
    assert result["source_metadata"]["fallback"] is False


def test_fetch_market_snapshot_tool_propagates_error():
    with patch(
        "app.services.domains.market_data._fetch_akshare_snapshot",
        side_effect=RuntimeError("AKShare timeout"),
    ):
        with pytest.raises(RuntimeError, match="AKShare timeout"):
            fetch_market_snapshot_tool.invoke(
                {
                    "symbol": "000852.SH",
                    "start_date": "2026-05-19",
                    "end_date": "2026-05-19",
                }
            )


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert_profile(name: str, symbol: str = "000300") -> int:
    from app import database
    from app.models import MarketDataProfile

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


def test_list_market_data_profiles_tool():
    _insert_profile("alpha")
    _insert_profile("beta", symbol="000905")
    result = list_market_data_profiles_tool.invoke({})
    assert result["ok"] is True
    assert result["total_count"] == 2
    assert [p["name"] for p in result["data"]] == ["alpha", "beta"]
    assert all("symbol" in p for p in result["data"])

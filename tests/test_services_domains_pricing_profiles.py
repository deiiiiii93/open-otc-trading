from __future__ import annotations

from datetime import datetime

import pytest

from app import database
from app.config import Settings
from app.models import PricingParameterProfile, PricingParameterRow
from app.services.domains import pricing_profiles as pricing_profiles_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert_profile(
    name: str,
    *,
    valuation_date: datetime,
    source_type: str = "default_underlying",
) -> int:
    with database.SessionLocal() as session:
        profile = PricingParameterProfile(
            name=name,
            valuation_date=valuation_date,
            source_type=source_type,
            status="completed",
            summary={"row_count": 1},
        )
        session.add(profile)
        session.flush()
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=f"{name}-trade",
                symbol="000852.SH",
                volatility=0.2,
            )
        )
        session.commit()
        return profile.id


def test_list_profiles_returns_newest_first_with_rows_loaded():
    _insert_profile("Default 2026-05-26", valuation_date=datetime(2026, 5, 26))
    newest_id = _insert_profile(
        "Default 2026-05-27", valuation_date=datetime(2026, 5, 27)
    )

    rows = pricing_profiles_svc.list_profiles()

    assert [row.name for row in rows] == ["Default 2026-05-27", "Default 2026-05-26"]
    assert rows[0].id == newest_id
    assert len(rows[0].rows) == 1


def test_list_profiles_filters_by_name_date_and_source_type():
    _insert_profile("Default 2026-05-27", valuation_date=datetime(2026, 5, 27))
    _insert_profile(
        "Imported Params", valuation_date=datetime(2026, 5, 26), source_type="xlsx"
    )

    assert [p.name for p in pricing_profiles_svc.list_profiles(query="Default 2026")]
    assert [p.name for p in pricing_profiles_svc.list_profiles(query="2026-05-27")] == [
        "Default 2026-05-27"
    ]
    assert [p.name for p in pricing_profiles_svc.list_profiles(query="xlsx")] == [
        "Imported Params"
    ]


def test_get_profile_returns_row_or_none():
    profile_id = _insert_profile(
        "Default 2026-05-27", valuation_date=datetime(2026, 5, 27)
    )

    fetched = pricing_profiles_svc.get_profile(profile_id=profile_id)

    assert fetched is not None
    assert fetched.name == "Default 2026-05-27"
    assert pricing_profiles_svc.get_profile(profile_id=9999) is None

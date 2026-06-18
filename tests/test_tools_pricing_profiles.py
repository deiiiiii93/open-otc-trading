"""Tool-layer tests: thin adapters, ok-envelopes, error translation."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import database
from app.config import Settings
from app.models import PricingParameterProfile, PricingParameterRow
from app.tools.pricing_profiles import (
    create_pricing_parameter_profile_tool,
    delete_pricing_parameter_profile_tool,
    delete_pricing_parameter_rows_tool,
    get_pricing_parameter_profile_tool,
    list_pricing_parameter_profiles_tool,
    update_pricing_parameter_profile_tool,
    upsert_pricing_parameter_rows_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert_profile(name: str, *, summary: dict | None = None) -> int:
    with database.SessionLocal() as session:
        profile = PricingParameterProfile(
            name=name,
            valuation_date=datetime(2026, 5, 27),
            source_type="default_underlying",
            status="completed",
            summary=summary if summary is not None else {"row_count": 1},
        )
        session.add(profile)
        session.flush()
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="trade-1",
                symbol="000852.SH",
                volatility=0.2,
            )
        )
        session.commit()
        return profile.id


def test_list_pricing_parameter_profiles_tool():
    profile_id = _insert_profile(
        "Default 2026-05-27",
        summary={
            "row_count": 1,
            "underlyings": [{"underlying": "000852.SH", "spot": 100.0}],
            "skipped_positions": [{"position_id": 9}],
        },
    )
    _insert_profile("Imported Params")

    result = list_pricing_parameter_profiles_tool.invoke(
        {"query": "Default 2026-05-27"}
    )

    assert result["ok"] is True
    assert result["total_count"] == 1
    profile = result["data"][0]
    assert profile["id"] == profile_id
    assert profile["name"] == "Default 2026-05-27"
    assert profile["row_count"] == 1
    assert profile["summary"]["underlying_count"] == 1
    assert profile["summary"]["skipped_position_count"] == 1
    assert "underlyings" not in profile["summary"]
    assert "skipped_positions" not in profile["summary"]


def _create(rows=None, **kwargs):
    return create_pricing_parameter_profile_tool.invoke(
        {
            "rows": rows
            or [{"symbol": "000905.SH", "rate": 0.037, "dividend_yield": 0.013,
                 "volatility": 0.31}],
            **kwargs,
        }
    )


def test_create_then_get_roundtrip():
    created = _create(valuation_date="2026-06-05T00:00:00")
    assert created["ok"] is True
    assert created["data"]["source_type"] == "agent"
    assert created["data"]["rows"][0]["volatility"] == 0.31

    fetched = get_pricing_parameter_profile_tool.invoke(
        {"profile_id": created["data"]["id"]}
    )
    assert fetched["ok"] is True
    assert fetched["data"]["rows"][0]["symbol"] == "000905.SH"
    assert fetched["data"]["rows"][0]["id"] is not None


def test_structured_refusals_surface_as_ok_false():
    assert get_pricing_parameter_profile_tool.invoke({"profile_id": 999}) == {
        "ok": False,
        "error": "profile_not_found",
        "detail": {"profile_id": 999},
    }
    empty = _create(rows=[{"symbol": "000905.SH"}])
    assert empty == {"ok": False, "error": "empty_row", "detail": {"row_indexes": [0]}}
    bad_date = _create(valuation_date="yesterday-ish")
    assert bad_date["ok"] is False
    assert bad_date["error"] == "invalid_valuation_date"


def test_update_upsert_delete_rows_and_profile():
    profile_id = _create()["data"]["id"]

    renamed = update_pricing_parameter_profile_tool.invoke(
        {"profile_id": profile_id, "name": "Vol bump"}
    )
    assert renamed["ok"] is True and renamed["data"]["name"] == "Vol bump"

    upserted = upsert_pricing_parameter_rows_tool.invoke(
        {"profile_id": profile_id,
         "rows": [{"symbol": "000905.SH", "volatility": 0.4},
                  {"symbol": "000852.SH", "rate": 0.02, "dividend_yield": 0.0,
                   "volatility": 0.3}]}
    )
    assert upserted["ok"] is True
    assert upserted["updated"] == 1 and upserted["inserted"] == 1

    row_ids = [row["id"] for row in upserted["data"]["rows"]]
    deleted_rows = delete_pricing_parameter_rows_tool.invoke(
        {"profile_id": profile_id, "row_ids": row_ids[:1]}
    )
    assert deleted_rows["ok"] is True and deleted_rows["deleted"] == 1

    deleted = delete_pricing_parameter_profile_tool.invoke({"profile_id": profile_id})
    assert deleted["ok"] is True
    assert deleted["data"]["deleted_profile_id"] == profile_id

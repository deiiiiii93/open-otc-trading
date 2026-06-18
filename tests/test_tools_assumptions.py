"""Tool-layer tests for the assumption pipeline tools."""
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import Instrument, Portfolio, Position
from app.tools.assumptions import (
    build_assumption_set_tool,
    get_assumption_set_tool,
    get_instrument_pricing_defaults_tool,
    list_assumption_sets_tool,
    set_instrument_pricing_defaults_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _seed_open_position(symbol: str) -> None:
    with database.SessionLocal() as session:
        portfolio = Portfolio(name=f"Book-{symbol}", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol=symbol)
        session.add(instrument)
        session.flush()
        session.add(
            Position(
                portfolio_id=portfolio.id,
                underlying=symbol,
                underlying_id=instrument.id,
                product_type="vanilla_option",
                quantity=1.0,
                status="open",
            )
        )
        session.commit()


def test_defaults_build_list_get_pipeline_roundtrip():
    _seed_open_position("PIPE.SH")

    unfilled = build_assumption_set_tool.invoke({})
    assert unfilled == {
        "ok": False,
        "error": "unfilled_underlyings",
        "detail": {"underlyings": ["PIPE.SH"]},
    }

    set_result = set_instrument_pricing_defaults_tool.invoke(
        {"symbol": "PIPE.SH", "rate": 0.03, "dividend_yield": 0.01,
         "volatility": 0.24}
    )
    assert set_result["ok"] is True
    assert set_result["data"]["volatility"] == 0.24

    built = build_assumption_set_tool.invoke({"name": "After defaults"})
    assert built["ok"] is True
    assert built["data"]["row_count"] == 1
    assert built["data"]["rows"][0]["symbol"] == "PIPE.SH"
    assert built["data"]["rows"][0]["field_sources"]["volatility"] == "instrument_default"

    listed = list_assumption_sets_tool.invoke({})
    assert listed["ok"] is True and listed["total_count"] == 1

    fetched = get_assumption_set_tool.invoke({"set_id": built["data"]["id"]})
    assert fetched["ok"] is True
    assert fetched["data"]["rows"][0]["rate"] == 0.03

    defaults = get_instrument_pricing_defaults_tool.invoke({"symbols": ["PIPE.SH"]})
    assert defaults["ok"] is True
    assert defaults["data"][0]["rate"] == 0.03


def test_structured_refusals():
    assert get_assumption_set_tool.invoke({"set_id": 999}) == {
        "ok": False, "error": "set_not_found", "detail": {"set_id": 999},
    }
    conflict = set_instrument_pricing_defaults_tool.invoke(
        {"symbol": "X.SH", "rate": 0.02, "clear": ["rate"]}
    )
    assert conflict["ok"] is False
    assert conflict["error"] == "field_set_and_cleared"
    nothing = build_assumption_set_tool.invoke({})
    assert nothing == {"ok": False, "error": "no_open_positions"}

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_convert_currency_tool_uses_db_rates():
    from datetime import datetime
    from app import database
    from app.models import FxRate
    from app.tools.risk import convert_currency_tool

    with database.SessionLocal() as session:
        session.add(FxRate(base_currency="CNY", quote_currency="USD", rate=0.14,
                           as_of_date=datetime(2026, 6, 1), source="manual"))
        session.commit()

    result = convert_currency_tool.invoke({
        "by_currency": {"CNY": {"market_value": 100.0, "position_count": 1}},
        "target_currency": "USD",
        "valuation_date": "2026-06-02",
    })
    assert result["totals"]["market_value"] == pytest.approx(14.0)
    assert result["missing"] == []
    assert result["as_of"] == "2026-06-02"


def test_convert_currency_in_tool_list():
    from app.tools import QUANT_AGENT_TOOLS
    names = {getattr(t, "name", None) for t in QUANT_AGENT_TOOLS}
    assert "convert_currency" in names


def test_convert_currency_tool_rejects_invalid_target():
    from app.tools.risk import convert_currency_tool

    result = convert_currency_tool.invoke({
        "by_currency": {"CNY": {"market_value": 100.0, "position_count": 1}},
        "target_currency": "XYZ",
        "valuation_date": "2026-06-02",
    })
    assert "error" in result
    assert result["totals"] == {}
    assert result["missing"] == []

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.schemas import PortfolioPositionSpec, PricingEnvironmentSnapshot
from app.tools.risk import (
    calculate_risk_tool,
    get_latest_risk_run_tool,
    recommend_hedge_tool,
    run_batch_pricing_tool,
)


def test_calculate_risk_tool(monkeypatch):
    fake_result = {"totals": {"delta": 1.0}}
    monkeypatch.setattr(
        "app.services.domains.risk.calculate_portfolio_risk",
        lambda portfolio, market: fake_result,
    )
    result = calculate_risk_tool.invoke(
        {
            "positions": [{"underlying": "000300.SH", "quantity": 1.0}],
            "market": {},
        }
    )
    assert result is fake_result


def test_run_batch_pricing_tool(monkeypatch):
    captured: dict = {}
    fake_return = {
        "portfolio_id": 1,
        "method": "summary",
        "position_ids": [10, 12],
        "risk_run_id": 42,
        "task_id": 99,
        "status": "queued",
        "message": "queued",
    }

    def fake_run(**kwargs):
        captured.update(kwargs)
        return fake_return

    monkeypatch.setattr(
        "app.services.domains.risk.run",
        fake_run,
    )
    result = run_batch_pricing_tool.invoke(
        {
            "portfolio_id": 1,
            "method": "summary",
            "position_ids": [10, 12],
            "pricing_parameter_profile_id": 7,
        }
    )
    assert result["risk_run_id"] == 42
    assert result["status"] == "queued"
    assert captured["position_ids"] == [10, 12]
    assert captured["pricing_profile_id"] == 7


def test_run_batch_pricing_tool_accepts_pricing_profile_id_alias(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "app.services.domains.risk.run",
        lambda **kwargs: captured.update(kwargs) or {"status": "queued"},
    )
    run_batch_pricing_tool.invoke({"portfolio_id": 1, "pricing_profile_id": 9})
    assert captured["pricing_profile_id"] == 9


@pytest.mark.parametrize(
    "override_field, value",
    [
        ("spot", 101.0),
        ("rate", 0.02),
        ("dividend_yield", 0.1),
        ("volatility", 0.25),
        ("valuation_date", "2026-06-01"),
    ],
)
def test_run_batch_pricing_tool_rejects_market_overrides(
    monkeypatch, override_field, value
):
    """Batch pricing is profile-driven: market overrides and valuation_date
    must fail loudly, never be silently dropped from an approved action."""
    monkeypatch.setattr(
        "app.services.domains.risk.run",
        lambda **kwargs: pytest.fail("tool body must not run on invalid input"),
    )
    with pytest.raises(Exception) as excinfo:
        run_batch_pricing_tool.invoke({"portfolio_id": 1, override_field: value})
    assert override_field in str(excinfo.value)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_get_latest_risk_run_tool_not_found():
    result = get_latest_risk_run_tool.invoke({"portfolio_id": 1})
    assert result["found"] is False
    assert "No completed stored risk run" in result["message"]


def test_get_latest_risk_run_tool_found():
    from app import database
    from app.models import Portfolio, RiskRun

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.commit()
        run = RiskRun(
            portfolio_id=portfolio.id,
            status="completed",
            metrics={"delta": 2.5},
        )
        session.add(run)
        session.commit()

    result = get_latest_risk_run_tool.invoke({"portfolio_id": portfolio.id})
    assert result["found"] is True
    assert result["risk_run_id"] == run.id
    assert result["metrics"] == {"delta": 2.5}


def test_recommend_hedge_tool(monkeypatch):
    fake_result = {"hedge": "buy_put"}
    monkeypatch.setattr(
        "app.services.domains.risk._recommend_hedge",
        lambda risk: fake_result,
    )
    result = recommend_hedge_tool.invoke({"risk": {"delta": 100}})
    assert result["hedge"] == "buy_put"

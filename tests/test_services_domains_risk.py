from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import database
from app.config import Settings
from app.models import RiskRun
from app.schemas import PortfolioPositionSpec, PricingEnvironmentSnapshot
from app.services.domains import portfolios as portfolios_svc
from app.services.domains import risk as risk_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_estimate_run_seconds_zero_for_empty():
    p = portfolios_svc.create(name="P", kind="container")
    assert risk_svc.estimate_run_seconds(portfolio_id=p.id) == 0.0


from app.models import Position


def _insert_positions(portfolio_id: int, count: int) -> list[int]:
    with database.SessionLocal() as session:
        ids: list[int] = []
        for i in range(count):
            position = Position(
                portfolio_id=portfolio_id,
                source_trade_id=f"T{i}",
                underlying="000300.SH",
                product_type="EuropeanVanillaOption",
                product_kwargs={},
                quantity=1.0,
                status="open",
                trade_effective_date=datetime(2025, 1, 1),
            )
            session.add(position)
            session.flush()
            ids.append(position.id)
        session.commit()
        return ids


def test_estimate_run_seconds_proportional():
    p = portfolios_svc.create(name="P", kind="container")
    _insert_positions(p.id, 10)
    est = risk_svc.estimate_run_seconds(portfolio_id=p.id)
    assert est == pytest.approx(5.0, rel=0.01)


def test_estimate_run_seconds_uses_scoped_position_ids():
    p = portfolios_svc.create(name="P", kind="container")
    position_ids = _insert_positions(p.id, 10)
    est = risk_svc.estimate_run_seconds(
        portfolio_id=p.id,
        position_ids=position_ids[:3],
    )
    assert est == pytest.approx(1.5, rel=0.01)


def test_calculate_risk_calls_quantark(monkeypatch):
    fake_result = {"totals": {"delta": 1.0}}
    monkeypatch.setattr(
        "app.services.domains.risk.calculate_portfolio_risk",
        lambda portfolio, market: fake_result,
    )
    positions = [PortfolioPositionSpec(underlying="000300.SH", quantity=1.0)]
    market = PricingEnvironmentSnapshot()
    result = risk_svc.calculate_risk(positions=positions, market=market)
    assert result is fake_result


def test_get_latest_run_returns_none_when_empty():
    p = portfolios_svc.create(name="P", kind="container")
    assert risk_svc.get_latest_run(portfolio_id=p.id) is None


def test_get_latest_run_returns_most_recent():
    p = portfolios_svc.create(name="P", kind="container")
    with database.SessionLocal() as session:
        session.add(RiskRun(portfolio_id=p.id, status="completed", metrics={"delta": 1}))
        session.add(RiskRun(portfolio_id=p.id, status="completed", metrics={"delta": 2}))
        session.commit()
    run = risk_svc.get_latest_run(portfolio_id=p.id)
    assert run is not None
    assert run.metrics == {"delta": 2}


def test_recommend_hedge_calls_quantark(monkeypatch):
    fake_result = {"hedge": "buy_put"}
    monkeypatch.setattr(
        "app.services.domains.risk._recommend_hedge",
        lambda risk: fake_result,
    )
    result = risk_svc.recommend_hedge(risk={"delta": 100})
    assert result is fake_result

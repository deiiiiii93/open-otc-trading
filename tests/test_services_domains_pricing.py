from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app import database
from app.config import Settings
from app.models import Portfolio, Position
from app.schemas import PricingEnvironmentSnapshot
from app.services.domains import portfolios as portfolios_svc
from app.services.domains import pricing as pricing_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_estimate_price_seconds_empty_portfolio():
    p = portfolios_svc.create(name="P", kind="container")
    assert pricing_svc.estimate_price_seconds(portfolio_id=p.id) == 0.0


def _insert_positions(portfolio_id: int, count: int) -> None:
    with database.SessionLocal() as session:
        for i in range(count):
            session.add(
                Position(
                    portfolio_id=portfolio_id,
                    source_trade_id=f"T{i}",
                    underlying="000300.SH",
                    product_type="EuropeanVanillaOption",
                    product_kwargs={},
                    quantity=1.0,
                    status="open",
                    trade_effective_date=date(2025, 1, 1),
                )
            )
        session.commit()


def test_estimate_price_seconds_proportional_to_count():
    p = portfolios_svc.create(name="P", kind="container")
    _insert_positions(p.id, 10)
    est = pricing_svc.estimate_price_seconds(portfolio_id=p.id)
    assert est == pytest.approx(3.0, rel=0.01)


def test_estimate_price_seconds_with_explicit_position_ids():
    est = pricing_svc.estimate_price_seconds(portfolio_id=1, position_ids=[1, 2, 3])
    assert est == pytest.approx(0.9, rel=0.01)


def test_price_product_calls_quantark(monkeypatch):
    fake_result = MagicMock(ok=True, data={"price": 1.23}, error=None)
    monkeypatch.setattr(
        "app.services.domains.pricing._quantark_price_product",
        lambda **kwargs: fake_result,
    )
    market = PricingEnvironmentSnapshot()
    result = pricing_svc.price_product(
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100},
        market=market,
        engine_name="BlackScholesEngine",
    )
    assert result is fake_result


def test_price_positions_calls_pricer(monkeypatch):
    fake_run = MagicMock(
        id=42,
        portfolio_id=1,
        pricing_parameter_profile_id=None,
        status="completed",
        summary={"total": 1.0},
    )
    captured: dict[str, Any] = {}

    def fake_price(sess, **kwargs):
        captured["kwargs"] = kwargs
        return fake_run

    monkeypatch.setattr(
        "app.services.domains.pricing.price_portfolio_positions", fake_price
    )

    result = pricing_svc.price_positions(
        portfolio_id=1,
        position_ids=[10, 20],
        pricing_profile_id=5,
        valuation_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        market_overrides={"spot": 5300.0},
    )

    assert result is fake_run
    assert captured["kwargs"]["portfolio_id"] == 1
    assert captured["kwargs"]["position_ids"] == [10, 20]
    assert captured["kwargs"]["pricing_parameter_profile_id"] == 5
    assert captured["kwargs"]["overrides"].spot == 5300.0

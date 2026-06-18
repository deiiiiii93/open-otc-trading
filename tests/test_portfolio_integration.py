from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import Portfolio, Position, PortfolioKind
from app.services import portfolio_service
from app.services.position_pricer import price_portfolio_positions


@pytest.fixture
def session(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as s:
        yield s


def test_create_view_then_price_resolves_correctly(session):
    container = portfolio_service.create_portfolio(
        session, name="Book", base_currency="USD", kind="container",
    )
    session.flush()
    pos_a = Position(
        portfolio_id=container.id, underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        engine_name="BlackScholesEngine", quantity=1.0,
    )
    pos_b = Position(
        portfolio_id=container.id, underlying="TSLA",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 200.0, "option_type": "PUT", "maturity": 1.0},
        engine_name="BlackScholesEngine", quantity=2.0,
    )
    session.add_all([pos_a, pos_b])
    session.flush()

    view = portfolio_service.create_portfolio(
        session, name="View", base_currency="USD", kind="view",
        filter_rule={"op": "in", "field": "underlying", "value": ["AAPL"]},
    )
    session.commit()

    resolved_ids = portfolio_service.preview_membership(session, view.id)
    assert resolved_ids == [pos_a.id]

    run = price_portfolio_positions(session, portfolio_id=view.id)
    session.commit()
    assert sorted(run.resolved_position_ids) == [pos_a.id]

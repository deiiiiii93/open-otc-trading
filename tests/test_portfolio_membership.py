from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import (
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKind,
    Portfolio,
    Position,
)
from app.services.portfolio_membership import resolve_positions


@pytest.fixture
def session(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as s:
        yield s


def _make(session, *, name, kind=PortfolioKind.CONTAINER.value, **kwargs):
    p = Portfolio(name=name, base_currency="USD", kind=kind, **kwargs)
    session.add(p)
    session.flush()
    return p


def _pos(session, portfolio, **kwargs):
    p = Position(portfolio_id=portfolio.id, quantity=1.0, **kwargs)
    session.add(p)
    session.flush()
    return p


def test_container_returns_owned_positions(session):
    c = _make(session, name="C")
    a = _pos(session, c, underlying="AAPL", product_type="Snowball")
    b = _pos(session, c, underlying="TSLA", product_type="Phoenix")
    out = resolve_positions(c, session)
    assert {p.id for p in out} == {a.id, b.id}


def test_view_with_rule_only_filters_across_containers(session):
    c1 = _make(session, name="C1")
    c2 = _make(session, name="C2")
    snow1 = _pos(session, c1, underlying="AAPL", product_type="Snowball")
    snow2 = _pos(session, c2, underlying="TSLA", product_type="Snowball")
    _pos(session, c1, underlying="QQQ", product_type="Phoenix")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"})
    out = resolve_positions(v, session)
    assert {p.id for p in out} == {snow1.id, snow2.id}


def test_view_rule_ignores_stale_positions_owned_by_views(session):
    c = _make(session, name="C")
    v = _make(
        session,
        name="V",
        kind=PortfolioKind.VIEW.value,
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    real = _pos(session, c, underlying="AAPL", product_type="Snowball")
    stale = _pos(session, v, underlying="TSLA", product_type="Snowball")

    out = resolve_positions(v, session)

    assert {p.id for p in out} == {real.id}
    assert stale.id not in {p.id for p in out}


def test_view_manual_include_ignores_stale_positions_owned_by_views(session):
    c = _make(session, name="C")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value)
    real = _pos(session, c, underlying="AAPL", product_type="Phoenix")
    stale = _pos(session, v, underlying="TSLA", product_type="Phoenix")
    v.manual_include_ids = [real.id, stale.id]
    session.flush()

    out = resolve_positions(v, session)

    assert {p.id for p in out} == {real.id}


def test_view_manual_includes_and_excludes(session):
    c = _make(session, name="C")
    a = _pos(session, c, underlying="AAPL", product_type="Snowball")
    b = _pos(session, c, underlying="TSLA", product_type="Snowball")
    extra = _pos(session, c, underlying="QQQ", product_type="Phoenix")
    v = _make(
        session, name="V", kind=PortfolioKind.VIEW.value,
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
        manual_include_ids=[extra.id],
        manual_exclude_ids=[a.id],
    )
    out = resolve_positions(v, session)
    assert {p.id for p in out} == {b.id, extra.id}


def test_view_aggregates_from_source_portfolios(session):
    c1 = _make(session, name="C1")
    c2 = _make(session, name="C2")
    a = _pos(session, c1, underlying="AAPL", product_type="Snowball")
    b = _pos(session, c2, underlying="TSLA", product_type="Phoenix")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              source_portfolio_ids=[c1.id, c2.id])
    out = resolve_positions(v, session)
    assert {p.id for p in out} == {a.id, b.id}


def test_view_dedups_overlap_between_rule_sources_and_manual(session):
    c = _make(session, name="C")
    a = _pos(session, c, underlying="AAPL", product_type="Snowball")
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
              source_portfolio_ids=[c.id],
              manual_include_ids=[a.id])
    out = resolve_positions(v, session)
    assert [p.id for p in out] == [a.id]


def test_view_skips_dangling_source(session):
    v = _make(session, name="V", kind=PortfolioKind.VIEW.value,
              source_portfolio_ids=[9999])
    assert resolve_positions(v, session) == []


def test_cycle_detection(session):
    a = _make(session, name="A", kind=PortfolioKind.VIEW.value)
    b = _make(session, name="B", kind=PortfolioKind.VIEW.value, source_portfolio_ids=[a.id])
    a.source_portfolio_ids = [b.id]
    session.flush()
    with pytest.raises(PortfolioCycleError):
        resolve_positions(a, session)


def test_depth_exceeded(session):
    chain = [_make(session, name=f"V{i}", kind=PortfolioKind.VIEW.value) for i in range(5)]
    for i in range(len(chain) - 1):
        chain[i].source_portfolio_ids = [chain[i + 1].id]
    session.flush()
    with pytest.raises(PortfolioDepthError):
        resolve_positions(chain[0], session)

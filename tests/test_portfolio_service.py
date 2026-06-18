from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import PortfolioKind, Portfolio, Position
from app.services import portfolio_service


@pytest.fixture
def session(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as s:
        yield s


def _seed_container(session, name="Book", *, kind=PortfolioKind.CONTAINER.value, **kwargs):
    p = Portfolio(name=name, base_currency="USD", kind=kind, **kwargs)
    session.add(p)
    session.flush()
    return p


def test_list_portfolios_empty(session):
    assert portfolio_service.list_portfolios(session) == []


def test_list_portfolios_filters_by_kind(session):
    c = _seed_container(session, name="C", tags=["a"])
    v = _seed_container(session, name="V", kind=PortfolioKind.VIEW.value)
    assert {p.id for p in portfolio_service.list_portfolios(session, kind="container")} == {c.id}
    assert {p.id for p in portfolio_service.list_portfolios(session, kind="view")} == {v.id}


def test_list_portfolios_filters_by_tags_AND(session):
    a = _seed_container(session, name="A", tags=["alpha", "beta"])
    _seed_container(session, name="B", tags=["alpha"])
    out = portfolio_service.list_portfolios(session, tags=["alpha", "beta"])
    assert {p.id for p in out} == {a.id}


def test_get_portfolio_raises_for_unknown(session):
    with pytest.raises(LookupError):
        portfolio_service.get_portfolio(session, 9999)


def test_preview_membership_for_view(session):
    c = _seed_container(session, name="C")
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="Snowball", quantity=1.0)
    session.add(a)
    session.flush()
    v = _seed_container(
        session, name="V",
        kind=PortfolioKind.VIEW.value,
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    assert portfolio_service.preview_membership(session, v.id) == [a.id]


def test_preview_membership_dry_run(session):
    c = _seed_container(session, name="C")
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="Snowball", quantity=1.0)
    session.add(a)
    session.flush()
    ids = portfolio_service.preview_membership_dry_run(
        session,
        kind="view",
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    assert ids == [a.id]


from app.models import PortfolioNameConflict


def test_create_container(session):
    p = portfolio_service.create_portfolio(
        session, name="Book A", base_currency="USD", kind="container", tags=["Desk"],
    )
    session.commit()
    assert p.kind == "container"
    assert p.tags == ["desk"]


def test_create_view_with_rule(session):
    p = portfolio_service.create_portfolio(
        session, name="Snow", base_currency="USD", kind="view",
        filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
    )
    session.commit()
    assert p.kind == "view"
    assert p.filter_rule["op"] == "eq"


def test_create_container_rejects_filter_rule(session):
    with pytest.raises(Exception):
        portfolio_service.create_portfolio(
            session, name="X", base_currency="USD", kind="container",
            filter_rule={"op": "eq", "field": "product_type", "value": "Snowball"},
        )


def test_create_view_rejects_invalid_rule(session):
    with pytest.raises(Exception):
        portfolio_service.create_portfolio(
            session, name="X", base_currency="USD", kind="view",
            filter_rule={"op": "weird"},
        )


def test_create_duplicate_name_raises_name_conflict(session):
    portfolio_service.create_portfolio(session, name="Dup", base_currency="USD", kind="container")
    session.commit()
    with pytest.raises(PortfolioNameConflict):
        portfolio_service.create_portfolio(session, name="Dup", base_currency="USD", kind="container")
        session.commit()


def test_update_portfolio_changes_name_and_tags(session):
    p = portfolio_service.create_portfolio(session, name="Old", base_currency="USD", kind="container")
    session.commit()
    portfolio_service.update_portfolio(session, p.id, name="New", tags=["Hedging"])
    session.commit()
    refetched = session.get(type(p), p.id)
    assert refetched.name == "New"
    assert refetched.tags == ["hedging"]


def test_delete_container_cascades_positions(session):
    from app.models import Position
    p = portfolio_service.create_portfolio(session, name="Z", base_currency="USD", kind="container")
    session.flush()
    pos = Position(portfolio_id=p.id, underlying="AAPL", product_type="Snowball", quantity=1.0)
    session.add(pos)
    session.commit()
    portfolio_service.delete_portfolio(session, p.id)
    session.commit()
    assert session.query(Position).filter_by(portfolio_id=p.id).count() == 0


def test_set_filter_rule_views_only(session):
    c = portfolio_service.create_portfolio(session, name="C", base_currency="USD", kind="container")
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.set_filter_rule(session, c.id, {"op": "eq", "field": "product_type", "value": "Snowball"})


def test_set_filter_rule_persists_and_validates(session):
    v = portfolio_service.create_portfolio(session, name="V", base_currency="USD", kind="view")
    session.commit()
    portfolio_service.set_filter_rule(session, v.id, {"op": "eq", "field": "product_type", "value": "Snowball"})
    session.commit()
    refetched = session.get(type(v), v.id)
    assert refetched.filter_rule["op"] == "eq"
    with pytest.raises(Exception):
        portfolio_service.set_filter_rule(session, v.id, {"op": "weird"})


def test_manual_includes_add_remove(session):
    from app.models import Position
    c = portfolio_service.create_portfolio(session, name="C", base_currency="USD", kind="container")
    session.flush()
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="X", quantity=1.0)
    b = Position(portfolio_id=c.id, underlying="TSLA", product_type="X", quantity=1.0)
    session.add_all([a, b])
    session.commit()
    v = portfolio_service.create_portfolio(session, name="V", base_currency="USD", kind="view")
    session.commit()
    portfolio_service.add_manual_includes(session, v.id, [a.id, b.id])
    session.commit()
    assert sorted(session.get(type(v), v.id).manual_include_ids) == sorted([a.id, b.id])
    portfolio_service.remove_manual_includes(session, v.id, [a.id])
    session.commit()
    assert session.get(type(v), v.id).manual_include_ids == [b.id]


def test_includes_excludes_overlap_rejected(session):
    from app.models import Position
    c = portfolio_service.create_portfolio(session, name="C", base_currency="USD", kind="container")
    session.flush()
    a = Position(portfolio_id=c.id, underlying="AAPL", product_type="X", quantity=1.0)
    session.add(a)
    session.commit()
    v = portfolio_service.create_portfolio(session, name="V", base_currency="USD", kind="view",
                                            manual_include_ids=[a.id])
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.add_manual_excludes(session, v.id, [a.id])


def test_add_sources_cycle_rejected(session):
    a = portfolio_service.create_portfolio(session, name="A", base_currency="USD", kind="view")
    b = portfolio_service.create_portfolio(session, name="B", base_currency="USD", kind="view",
                                            source_portfolio_ids=[])
    session.commit()
    portfolio_service.add_portfolio_sources(session, a.id, [b.id])
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.add_portfolio_sources(session, b.id, [a.id])


def test_set_tags_normalizes(session):
    p = portfolio_service.create_portfolio(session, name="P", base_currency="USD", kind="container")
    session.commit()
    portfolio_service.set_portfolio_tags(session, p.id, ["Alpha", "  beta  ", "Alpha"])
    session.commit()
    assert session.get(type(p), p.id).tags == ["alpha", "beta"]


def test_set_tags_rejects_long(session):
    p = portfolio_service.create_portfolio(session, name="P", base_currency="USD", kind="container")
    session.commit()
    with pytest.raises(Exception):
        portfolio_service.set_portfolio_tags(session, p.id, ["x" * 41])

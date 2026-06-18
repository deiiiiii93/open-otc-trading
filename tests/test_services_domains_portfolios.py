from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app import database
from app.config import Settings
from app.models import Portfolio
from app.services.domains import portfolios as portfolios_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert(name: str, kind: str = "container") -> int:
    with database.SessionLocal() as session:
        p = Portfolio(name=name, kind=kind)
        session.add(p)
        session.commit()
        return p.id


def test_list_all_returns_orm_objects():
    _insert("A")
    _insert("B")
    result = portfolios_svc.list_all()
    assert len(result) == 2
    assert all(isinstance(p, Portfolio) for p in result)
    assert {p.name for p in result} == {"A", "B"}


def test_list_all_kind_filter():
    _insert("C", "container")
    _insert("V", "view")
    result = portfolios_svc.list_all(kind="view")
    assert {p.name for p in result} == {"V"}


def test_create_and_get():
    p = portfolios_svc.create(name="X", kind="container")
    fetched = portfolios_svc.get(portfolio_id=p.id)
    assert fetched is not None and fetched.name == "X"


def test_resolve_by_name_or_id():
    p = portfolios_svc.create(name="Snow", kind="container")
    assert portfolios_svc.resolve(identifier=p.id).id == p.id
    assert portfolios_svc.resolve(identifier="Snow").id == p.id
    assert portfolios_svc.resolve(identifier=str(p.id)).id == p.id
    assert portfolios_svc.resolve(identifier="nope") is None


def test_update_fields():
    p = portfolios_svc.create(name="U", kind="container")
    portfolios_svc.update(portfolio_id=p.id, fields={"description": "edited"})
    assert portfolios_svc.get(portfolio_id=p.id).description == "edited"


def test_delete_returns_true_then_false():
    p = portfolios_svc.create(name="D", kind="container")
    assert portfolios_svc.delete(portfolio_id=p.id) is True
    assert portfolios_svc.delete(portfolio_id=p.id) is False


def test_set_rule_round_trip():
    p = portfolios_svc.create(name="R", kind="view")
    rule = {"op": "eq", "field": "product_type", "value": "Snowball"}
    portfolios_svc.set_rule(portfolio_id=p.id, filter_rule=rule)
    assert portfolios_svc.get(portfolio_id=p.id).filter_rule == rule
    portfolios_svc.set_rule(portfolio_id=p.id, filter_rule=None)
    assert portfolios_svc.get(portfolio_id=p.id).filter_rule is None


def test_add_remove_member_excludes():
    from app.models import Position

    container = portfolios_svc.create(name="C", kind="container")
    with database.SessionLocal() as session:
        for symbol in ("AAPL", "MSFT"):
            session.add(
                Position(
                    portfolio_id=container.id,
                    underlying=symbol,
                    product_type="X",
                    quantity=1.0,
                )
            )
        session.commit()
        ids = sorted(pid for (pid,) in session.query(Position.id).all())
    p = portfolios_svc.create(name="X", kind="view")
    portfolios_svc.add_member_excludes(portfolio_id=p.id, position_ids=ids)
    assert sorted(portfolios_svc.get(portfolio_id=p.id).manual_exclude_ids) == ids
    portfolios_svc.remove_member_excludes(portfolio_id=p.id, position_ids=[ids[0]])
    assert portfolios_svc.get(portfolio_id=p.id).manual_exclude_ids == [ids[1]]


def test_set_tags_round_trip():
    p = portfolios_svc.create(name="Y", kind="container")
    portfolios_svc.set_tags(portfolio_id=p.id, tags=["A", "B"])
    assert sorted(portfolios_svc.get(portfolio_id=p.id).tags) == ["a", "b"]

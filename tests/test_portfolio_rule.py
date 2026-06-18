from __future__ import annotations

from sqlalchemy import select

from app import database
from app.models import Position, Portfolio
from app.services.portfolio_rule import (
    MAX_RULE_DEPTH,
    compile_rule_to_sqla,
    validate_rule,
)


def test_validate_rule_accepts_simple_eq():
    assert validate_rule({"op": "eq", "field": "product_type", "value": "Snowball"}) == []


def test_validate_rule_rejects_unknown_op():
    errors = validate_rule({"op": "matches", "field": "underlying", "value": "AAPL"})
    assert any("matches" in e for e in errors)


def test_validate_rule_rejects_unknown_field():
    errors = validate_rule({"op": "eq", "field": "color", "value": "blue"})
    assert any("color" in e for e in errors)


def test_validate_rule_rejects_eq_with_list_value():
    errors = validate_rule({"op": "eq", "field": "underlying", "value": ["AAPL", "TSLA"]})
    assert any("scalar" in e.lower() for e in errors)


def test_validate_rule_rejects_in_with_scalar_value():
    errors = validate_rule({"op": "in", "field": "underlying", "value": "AAPL"})
    assert any("list" in e.lower() for e in errors)


def test_validate_rule_rejects_empty_and():
    errors = validate_rule({"op": "and", "children": []})
    assert any("empty" in e.lower() for e in errors)


def test_validate_rule_rejects_between_with_reversed_bounds():
    errors = validate_rule({"op": "between", "field": "quantity", "value": [10, 1]})
    assert any("between" in e.lower() for e in errors)


def test_validate_rule_rejects_too_deep():
    rule = {"op": "eq", "field": "underlying", "value": "AAPL"}
    for _ in range(MAX_RULE_DEPTH + 1):
        rule = {"op": "and", "children": [rule]}
    errors = validate_rule(rule)
    assert any("depth" in e.lower() for e in errors)


def test_compile_eq(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        a = Position(portfolio_id=portfolio.id, underlying="AAPL", product_type="Snowball", quantity=10)
        b = Position(portfolio_id=portfolio.id, underlying="TSLA", product_type="Phoenix", quantity=20)
        session.add_all([a, b])
        session.flush()

        clause = compile_rule_to_sqla({"op": "eq", "field": "product_type", "value": "Snowball"})
        rows = session.execute(select(Position).where(clause)).scalars().all()
        assert {r.id for r in rows} == {a.id}


def test_compile_in_and_not(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        rows = [
            Position(portfolio_id=portfolio.id, underlying="AAPL", product_type="Snowball", quantity=10),
            Position(portfolio_id=portfolio.id, underlying="TSLA", product_type="Snowball", quantity=20),
            Position(portfolio_id=portfolio.id, underlying="QQQ",  product_type="Phoenix",  quantity=30),
        ]
        session.add_all(rows)
        session.flush()

        clause = compile_rule_to_sqla({
            "op": "and",
            "children": [
                {"op": "in",  "field": "underlying",   "value": ["AAPL", "TSLA", "QQQ"]},
                {"op": "not", "child": {"op": "eq", "field": "product_type", "value": "Phoenix"}},
            ],
        })
        out = session.execute(select(Position).where(clause)).scalars().all()
        assert {r.underlying for r in out} == {"AAPL", "TSLA"}

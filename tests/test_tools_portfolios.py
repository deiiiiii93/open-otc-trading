from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import Position
from app.services.domains import portfolios as portfolios_svc
from app.tools.portfolios import (
    add_portfolio_sources_tool,
    add_positions_to_portfolio_tool,
    create_portfolio_tool,
    delete_portfolio_tool,
    get_portfolio_tool,
    list_portfolios_tool,
    remove_portfolio_sources_tool,
    remove_positions_from_portfolio_tool,
    set_portfolio_rule_tool,
    update_portfolio_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _make_position(portfolio_id: int, symbol: str = "AAPL") -> int:
    with database.SessionLocal() as session:
        pos = Position(
            portfolio_id=portfolio_id,
            underlying=symbol,
            product_type="X",
            quantity=1.0,
        )
        session.add(pos)
        session.commit()
        return pos.id


def test_list_portfolios_tool_returns_legacy_shape():
    portfolios_svc.create(name="A", kind="container")
    portfolios_svc.create(name="B", kind="view")
    result = list_portfolios_tool.invoke({})
    assert result["ok"] is True
    rows = result["data"]
    assert {p["name"] for p in rows} == {"A", "B"}
    assert all("filter_rule" in p for p in rows)


def test_get_portfolio_tool_found():
    p = portfolios_svc.create(name="G", kind="container")
    result = get_portfolio_tool.invoke({"portfolio_id": p.id})
    assert result["ok"] is True
    assert result["data"]["name"] == "G"
    assert "resolved_position_ids" in result["data"]


def test_get_portfolio_tool_not_found():
    result = get_portfolio_tool.invoke({"portfolio_id": 999})
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_create_portfolio_tool_returns_envelope():
    result = create_portfolio_tool.invoke({"name": "T", "kind": "container"})
    assert result["ok"] is True
    assert result["data"]["name"] == "T"


def test_update_portfolio_tool_found():
    p = portfolios_svc.create(name="U", kind="container")
    result = update_portfolio_tool.invoke(
        {"portfolio_id": p.id, "description": "edited"}
    )
    assert result["ok"] is True
    assert result["data"]["description"] == "edited"


def test_update_portfolio_tool_not_found():
    result = update_portfolio_tool.invoke({"portfolio_id": 999, "description": "x"})
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_delete_portfolio_tool_succeeds():
    p = portfolios_svc.create(name="D", kind="container")
    result = delete_portfolio_tool.invoke({"portfolio_id": p.id})
    assert result["ok"] is True
    assert result["data"]["deleted"] is True


def test_delete_portfolio_tool_not_found():
    result = delete_portfolio_tool.invoke({"portfolio_id": 999})
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_set_portfolio_rule_tool_set_then_clear():
    p = portfolios_svc.create(name="R", kind="view")
    rule = {"op": "eq", "field": "product_type", "value": "Snowball"}
    result = set_portfolio_rule_tool.invoke({"portfolio_id": p.id, "filter_rule": rule})
    assert result["ok"] is True
    assert result["data"]["filter_rule"] == rule
    cleared = set_portfolio_rule_tool.invoke({"portfolio_id": p.id, "filter_rule": None})
    assert cleared["ok"] is True
    assert cleared["data"]["filter_rule"] is None


def test_add_positions_to_portfolio_tool_view():
    container = portfolios_svc.create(name="C", kind="container")
    pos_id = _make_position(container.id)
    view = portfolios_svc.create(name="V", kind="view")
    result = add_positions_to_portfolio_tool.invoke(
        {"portfolio_id": view.id, "position_ids": [pos_id]}
    )
    assert result["ok"] is True
    assert result["data"]["manual_include_ids"] == [pos_id]
    assert result["kind_resolved_as"] == "view"


def test_remove_positions_from_portfolio_tool_view():
    container = portfolios_svc.create(name="C", kind="container")
    pos_id = _make_position(container.id)
    view = portfolios_svc.create(name="V", kind="view", manual_include_ids=[pos_id])
    result = remove_positions_from_portfolio_tool.invoke(
        {"portfolio_id": view.id, "position_ids": [pos_id]}
    )
    assert result["ok"] is True
    assert result["data"]["manual_include_ids"] == []
    assert result["kind_resolved_as"] == "view"


def test_add_portfolio_sources_tool_happy_path():
    a = portfolios_svc.create(name="A", kind="view")
    b = portfolios_svc.create(name="B", kind="view")
    result = add_portfolio_sources_tool.invoke(
        {"portfolio_id": a.id, "source_portfolio_ids": [b.id]}
    )
    assert result["ok"] is True
    assert result["data"]["source_portfolio_ids"] == [b.id]


def test_add_portfolio_sources_tool_cycle_error():
    a = portfolios_svc.create(name="A", kind="view")
    b = portfolios_svc.create(name="B", kind="view")
    add_portfolio_sources_tool.invoke(
        {"portfolio_id": a.id, "source_portfolio_ids": [b.id]}
    )
    result = add_portfolio_sources_tool.invoke(
        {"portfolio_id": b.id, "source_portfolio_ids": [a.id]}
    )
    assert result["ok"] is False
    assert "cycle_path" in result or "error" in result


def test_remove_portfolio_sources_tool():
    a = portfolios_svc.create(name="A", kind="view")
    b = portfolios_svc.create(name="B", kind="view")
    add_portfolio_sources_tool.invoke(
        {"portfolio_id": a.id, "source_portfolio_ids": [b.id]}
    )
    result = remove_portfolio_sources_tool.invoke(
        {"portfolio_id": a.id, "source_portfolio_ids": [b.id]}
    )
    assert result["ok"] is True
    assert result["data"]["source_portfolio_ids"] == []


def test_create_portfolio_tool_defaults_to_cny():
    """Desk convention (a26aca8): agent-layer creation defaults to CNY, not USD.
    REST keeps its own schema; this pins the tool layer only."""
    result = create_portfolio_tool.invoke({"name": "CcyDefault"})
    assert result["ok"] is True
    assert result["data"]["base_currency"] == "CNY"


def test_create_portfolio_tool_view_rule_round_trip():
    result = create_portfolio_tool.invoke({
        "name": "OpenSnowballs",
        "kind": "view",
        "filter_rule": {"op": "and", "children": [
            {"op": "eq", "field": "underlying", "value": "000905.SH"},
            {"op": "eq", "field": "status", "value": "open"},
        ]},
    })
    assert result["ok"] is True
    assert result["data"]["kind"] == "view"
    assert result["data"]["filter_rule"]["op"] == "and"


def test_create_portfolio_tool_rejects_bad_rule_op_without_persisting():
    result = create_portfolio_tool.invoke({
        "name": "BadRule",
        "kind": "view",
        "filter_rule": {"op": "bogus", "field": "underlying", "value": "X"},
    })
    assert result["ok"] is False
    assert any("Unsupported op" in e for e in result["errors"])
    listing = list_portfolios_tool.invoke({})
    assert all(p["name"] != "BadRule" for p in listing["data"])

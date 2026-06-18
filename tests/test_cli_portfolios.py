from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from app import database
from app.cli import main as cli_main
from app.config import Settings


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _run(*argv: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(list(argv))
    assert rc == 0, buf.getvalue()
    return json.loads(buf.getvalue())


def test_create_then_list():
    _run("portfolios", "create", "--name", "Book", "--kind", "container")
    out = _run("portfolios", "list", "--json")
    # `out` is the full payload {"portfolios": [...]}; extract the list
    portfolios = out["portfolios"] if isinstance(out, dict) and "portfolios" in out else out
    assert any(p["name"] == "Book" for p in portfolios)


def test_create_view_with_rule_text():
    _run("portfolios", "create-view", "--name", "Snow",
         "--rule-text", 'product_type = Snowball')
    out = _run("portfolios", "show", "--portfolio", "Snow")
    assert out["filter_rule"]["op"] == "eq"


def test_list_kind_filter():
    _run("portfolios", "create", "--name", "C", "--kind", "container")
    _run("portfolios", "create", "--name", "V", "--kind", "view")
    out = _run("portfolios", "list", "--kind", "view", "--json")
    portfolios = out["portfolios"] if isinstance(out, dict) and "portfolios" in out else out
    assert {p["name"] for p in portfolios} == {"V"}


def test_update_then_delete():
    _run("portfolios", "create", "--name", "X", "--kind", "container")
    out = _run("portfolios", "update", "--portfolio", "X", "--description", "demo", "--tag", "alpha")
    assert out["description"] == "demo"
    assert out["tags"] == ["alpha"]
    out = _run("portfolios", "delete", "--portfolio", "X", "--confirm")
    assert out["deleted"] is True


def test_set_rule_via_text():
    _run("portfolios", "create", "--name", "V", "--kind", "view")
    out = _run("portfolios", "set-rule", "--portfolio", "V",
               "--rule-text", 'product_type = Snowball AND status = open')
    assert out["filter_rule"]["op"] == "and"


def test_resolve_view():
    _run("portfolios", "create", "--name", "C", "--kind", "container")
    # No positions yet — resolution should be empty
    out = _run("portfolios", "resolve", "--portfolio", "C")
    assert out["position_ids"] == []


def test_includes_excludes_via_cli():
    cid = _run("portfolios", "create", "--name", "C", "--kind", "container")["id"]
    # Use service helper through HTTP-style; inline insertion via SQL fixture
    from app import database
    from app.models import Position, Portfolio
    with database.SessionLocal() as session:
        p = Position(portfolio_id=cid, underlying="AAPL", product_type="X", quantity=1.0)
        session.add(p)
        session.commit()
        pos_id = p.id
    _run("portfolios", "create", "--name", "V", "--kind", "view")
    out = _run("portfolios", "includes", "add", "--portfolio", "V", "--position-id", str(pos_id))
    assert out["manual_include_ids"] == [pos_id]
    out = _run("portfolios", "includes", "remove", "--portfolio", "V", "--position-id", str(pos_id))
    assert out["manual_include_ids"] == []


def test_sources_via_cli_and_cycle():
    a = _run("portfolios", "create", "--name", "A", "--kind", "view")["id"]
    b = _run("portfolios", "create", "--name", "B", "--kind", "view")["id"]
    out = _run("portfolios", "sources", "add", "--portfolio", "A", "--source", str(b))
    assert out["source_portfolio_ids"] == [b]
    with pytest.raises(Exception):
        _run("portfolios", "sources", "add", "--portfolio", "B", "--source", str(a))


def test_tags_via_cli():
    _run("portfolios", "create", "--name", "P", "--kind", "container")
    out = _run("portfolios", "tags", "set", "--portfolio", "P", "--tag", "Alpha", "--tag", "BETA")
    assert out["tags"] == ["alpha", "beta"]

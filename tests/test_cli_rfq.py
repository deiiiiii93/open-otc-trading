from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from app.cli.rfq import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _draft_json() -> str:
    return json.dumps(
        {
            "client_name": "Demo",
            "underlying": "AAPL",
            "side": "buy",
            "quantity": 1.0,
            "product_type": "EuropeanVanillaOption",
            "product_kwargs": {
                "strike": 100.0,
                "maturity": 1.0,
                "option_type": "CALL",
            },
            "engine_spec": {"engine_name": "BlackScholesEngine"},
        }
    )


def _make_rfq_stub(**overrides):
    base = {
        "id": 1,
        "status": "draft",
        "client_name": "Demo",
        "channel": "desk",
        "request_payload": {},
        "quote_payload": {},
        "approved_response": None,
        "quote_versions": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_catalog_cmd(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.rfq.get_rfq_catalog",
        lambda: {"product_types": [], "engine_options": []},
    )
    result = runner.invoke(app, ["catalog"])
    assert result.exit_code == 0
    assert "product_types" in result.output


def test_draft_cmd_create(monkeypatch):
    rfq = _make_rfq_stub(id=10, status="draft")
    monkeypatch.setattr(
        "app.services.domains.rfq.create_rfq_draft",
        lambda session, payload, *, channel, actor: rfq,
    )
    result = runner.invoke(app, ["draft", "--draft", _draft_json()])
    assert result.exit_code == 0
    assert '"rfq_id": 10' in result.output
    assert '"status": "draft"' in result.output


def test_draft_cmd_update(monkeypatch):
    rfq = _make_rfq_stub(id=4, status="draft")
    captured: dict = {}

    def fake_update(session, rfq_id, payload, *, actor):
        captured["rfq_id"] = rfq_id
        captured["actor"] = actor
        return rfq

    monkeypatch.setattr("app.services.domains.rfq.update_rfq_draft", fake_update)
    result = runner.invoke(
        app,
        ["draft", "--draft", _draft_json(), "--rfq-id", "4", "--actor", "alice"],
    )
    assert result.exit_code == 0
    assert captured == {"rfq_id": 4, "actor": "alice"}


def test_draft_cmd_bad_json():
    result = runner.invoke(app, ["draft", "--draft", "{not json"])
    assert result.exit_code != 0
    assert "not valid JSON" in result.output


def test_quote_cmd(monkeypatch):
    rfq = _make_rfq_stub(id=22, status="pending_approval")
    monkeypatch.setattr(
        "app.services.domains.rfq.quote_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = runner.invoke(app, ["quote", "--rfq-id", "22"])
    assert result.exit_code == 0
    assert "pending_approval" in result.output


def test_quote_cmd_invalid_mode():
    result = runner.invoke(app, ["quote", "--rfq-id", "1", "--quote-mode", "bogus"])
    assert result.exit_code != 0
    assert "solve" in result.output or "price" in result.output


def test_approve_cmd(monkeypatch):
    rfq = _make_rfq_stub(id=33, status="approved", approved_response="OK")
    monkeypatch.setattr(
        "app.services.domains.rfq.approve_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = runner.invoke(app, ["approve", "--rfq-id", "33", "--approver", "alice"])
    assert result.exit_code == 0
    assert "approved" in result.output


def test_reject_cmd(monkeypatch):
    rfq = _make_rfq_stub(id=44, status="rejected")
    monkeypatch.setattr(
        "app.services.domains.rfq.reject_rfq",
        lambda session, rfq_id, payload: rfq,
    )
    result = runner.invoke(app, ["reject", "--rfq-id", "44", "--approver", "alice"])
    assert result.exit_code == 0
    assert "rejected" in result.output


def test_help_shows_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("catalog", "draft", "quote", "approve", "reject"):
        assert cmd in result.output

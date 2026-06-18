from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from app.cli.reporting import app


runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _job_stub(**overrides):
    base = dict(
        id=1,
        report_type="portfolio",
        status="completed",
        request_payload={"portfolio_id": 7, "title": "T"},
        result_payload={},
        artifact_paths={"html": "/tmp/foo.html"},
        created_at=datetime(2025, 1, 1, 0, 0, 0),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_list_cmd_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.reporting.list_reports",
        lambda **kwargs: [],
    )
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert '"reports": []' in result.output
    assert '"total": 0' in result.output


def test_list_cmd_passes_filters(monkeypatch):
    captured: dict = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return [_job_stub(id=3)]

    monkeypatch.setattr("app.services.domains.reporting.list_reports", fake_list)
    result = runner.invoke(
        app,
        ["list", "--portfolio-id", "7", "--report-type", "risk", "--limit", "10"],
    )
    assert result.exit_code == 0
    assert captured == {
        "portfolio_id": 7,
        "report_type": "risk",
        "status": None,
        "limit": 10,
    }


def test_show_cmd_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.reporting.get_report",
        lambda *, report_id: None,
    )
    result = runner.invoke(app, ["show", "--report-id", "9999"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_show_cmd_found(monkeypatch):
    job = _job_stub(
        id=8, result_payload={"risk": {"totals": {"delta": 1.0}}, "portfolio": {}}
    )
    monkeypatch.setattr(
        "app.services.domains.reporting.get_report",
        lambda *, report_id: job,
    )
    result = runner.invoke(app, ["show", "--report-id", "8"])
    assert result.exit_code == 0
    assert '"report_id": 8' in result.output
    assert '"delta": 1.0' in result.output


def test_create_cmd(monkeypatch):
    fake_payload = {
        "report_job_id": 99,
        "task_id": 100,
        "pricing_parameter_profile_id": None,
        "status": "queued",
        "message": "Report queued.",
    }
    monkeypatch.setattr(
        "app.services.domains.reporting.create_report",
        lambda **kwargs: fake_payload,
    )
    result = runner.invoke(
        app, ["create", "--portfolio-id", "7", "--title", "Q4"]
    )
    assert result.exit_code == 0
    assert '"report_job_id": 99' in result.output
    assert "queued" in result.output


def test_create_cmd_invalid_type():
    result = runner.invoke(
        app, ["create", "--portfolio-id", "1", "--report-type", "bogus"]
    )
    assert result.exit_code != 0
    assert "portfolio" in result.output or "risk" in result.output or "rfq" in result.output


def test_batch_run_cmd():
    result = runner.invoke(app, ["batch-run", "--title", "Snap"])
    assert result.exit_code == 0
    assert '"title": "Snap"' in result.output
    assert '"status": "ready"' in result.output


def test_help_shows_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("list", "show", "create", "batch-run"):
        assert cmd in result.output

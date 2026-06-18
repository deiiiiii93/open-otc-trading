from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli.risk import app
from app.services.domains import portfolios as portfolios_svc


runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_estimate_cmd():
    p = portfolios_svc.create(name="P", kind="container")
    result = runner.invoke(app, ["estimate", "--portfolio", str(p.id)])
    assert result.exit_code == 0
    assert "0.0s" in result.output


def test_estimate_cmd_not_found():
    result = runner.invoke(app, ["estimate", "--portfolio", "9999"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_run_cmd():
    p = portfolios_svc.create(name="P", kind="container")
    fake_return = {
        "portfolio_id": p.id,
        "method": "summary",
        "risk_run_id": 42,
        "task_id": 99,
        "status": "queued",
        "message": "queued",
    }
    with patch("app.services.domains.risk.run", return_value=fake_return):
        result = runner.invoke(
            app,
            [
                "run",
                "--portfolio",
                str(p.id),
                "--method",
                "summary",
                "--pricing-profile-id",
                "7",
            ],
        )
    assert result.exit_code == 0
    assert "42" in result.output
    assert "queued" in result.output


def test_latest_cmd_not_found():
    p = portfolios_svc.create(name="P", kind="container")
    result = runner.invoke(app, ["latest", "--portfolio", str(p.id)])
    assert result.exit_code == 0
    assert "No completed stored risk run" in result.output


def test_latest_cmd_found():
    from app import database
    from app.models import RiskRun

    p = portfolios_svc.create(name="P", kind="container")
    with database.SessionLocal() as session:
        run = RiskRun(
            portfolio_id=p.id,
            status="completed",
            metrics={"delta": 2.5},
        )
        session.add(run)
        session.commit()

    result = runner.invoke(app, ["latest", "--portfolio", str(p.id)])
    assert result.exit_code == 0
    assert "completed" in result.output
    assert "delta" in result.output


def test_help_shows_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "estimate" in result.output
    assert "run" in result.output
    assert "latest" in result.output

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli.pricing import app
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
    fake_run = MagicMock(
        id=42,
        pricing_parameter_profile_id=7,
        status="completed",
        summary={"total": 1.0},
    )
    with patch(
        "app.services.domains.pricing.price_portfolio_positions",
        return_value=fake_run,
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--portfolio", str(p.id),
                "--pricing-profile-id", "7",
                "--valuation-date", "2025-01-15T00:00:00",
            ],
        )
    assert result.exit_code == 0
    assert "42" in result.output
    assert "completed" in result.output


def test_run_cmd_no_json():
    p = portfolios_svc.create(name="P", kind="container")
    fake_run = MagicMock(
        id=99,
        pricing_parameter_profile_id=None,
        status="completed",
        summary={},
    )
    with patch(
        "app.services.domains.pricing.price_portfolio_positions",
        return_value=fake_run,
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--portfolio", str(p.id),
                "--no-json",
            ],
        )
    assert result.exit_code == 0


def test_help_shows_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "estimate" in result.output
    assert "run" in result.output

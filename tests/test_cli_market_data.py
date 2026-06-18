from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.cli.market_data import app
from typer.testing import CliRunner


runner = CliRunner()


def test_fetch_command():
    snapshot = {
        "name": "Test",
        "source": "akshare",
        "symbol": "000852.SH",
        "asset_class": "index",
        "valuation_date": "2026-05-19T00:00:00",
        "data": {"rows": [], "latest": None, "spot": None},
        "source_metadata": {"fallback": False},
    }
    with patch(
        "app.cli.market_data.md_svc.fetch_snapshot",
        return_value=type("obj", (object,), {"model_dump": lambda self, mode: snapshot})(),
    ):
        result = runner.invoke(
            app,
            [
                "fetch",
                "--symbol", "000852.SH",
                "--start", "2026-05-19",
                "--end", "2026-05-19",
            ],
        )
    assert result.exit_code == 0
    assert "000852.SH" in result.output


def test_fetch_command_no_json():
    snapshot = {
        "name": "Test",
        "source": "akshare",
        "symbol": "000852.SH",
        "asset_class": "index",
        "valuation_date": "2026-05-19T00:00:00",
        "data": {},
        "source_metadata": {},
    }
    with patch(
        "app.cli.market_data.md_svc.fetch_snapshot",
        return_value=type("obj", (object,), {"model_dump": lambda self, mode: snapshot})(),
    ):
        result = runner.invoke(
            app,
            [
                "fetch",
                "--symbol", "000852.SH",
                "--start", "2026-05-19",
                "--end", "2026-05-19",
                "--no-json",
            ],
        )
    assert result.exit_code == 0


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert_profile(name: str, symbol: str = "000300") -> int:
    from app import database
    from app.models import MarketDataProfile

    with database.SessionLocal() as session:
        profile = MarketDataProfile(
            name=name,
            source="akshare",
            symbol=symbol,
            asset_class="index",
            start_date="2025-01-01",
            end_date="2025-01-10",
            adjust="qfq",
        )
        session.add(profile)
        session.commit()
        return profile.id


def test_profiles_command():
    _insert_profile("alpha")
    _insert_profile("beta", symbol="000905")
    result = runner.invoke(app, ["profiles"])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_profiles_command_no_json():
    _insert_profile("alpha")
    result = runner.invoke(app, ["profiles", "--no-json"])
    assert result.exit_code == 0


def test_help_shows_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "fetch" in result.output
    assert "profiles" in result.output

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.config import Settings


_ROOT = Path(__file__).resolve().parents[1]


def test_fresh_sqlite_upgrade_reaches_head(tmp_path: Path, monkeypatch) -> None:
    """The documented empty-database Alembic path must reach the live head."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}"

    from app import config as config_module

    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: Settings(database_url=database_url),
    )

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(_ROOT / "backend" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    assert revision == ScriptDirectory.from_config(config).get_current_head()

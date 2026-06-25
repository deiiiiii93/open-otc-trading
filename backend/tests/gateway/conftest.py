"""Shared fixtures for gateway tests that need a real DB session."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import database
from app.config import Settings


@pytest.fixture
def db_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )


@pytest.fixture
def db_session(db_settings: Settings):
    """Configure an in-memory SQLite DB and yield a session."""
    database.configure_database(db_settings)
    database.init_db()
    with database.SessionLocal() as session:
        yield session

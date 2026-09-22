"""Migration 0064 + the ORM model agree on the (event_id, kind) key (spec §Data model).

Drives the migration module directly against temp SQLite — never `head`.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

_TABLE = "limit_incident_reviews"
_INDEXES = {"ix_limit_incident_reviews_incident_id", "ix_limit_incident_reviews_created_at"}
_COLUMNS = {
    "id", "incident_id", "event_id", "kind", "status", "unscored_reason",
    "rationale_grade", "rationale_confidence", "authority_only_p",
    "thread_state", "thread_state_p", "claims_json", "answers_json",
    "model", "latency_ms", "attempted_at", "created_at",
}


def _run(method: str, engine: sa.Engine) -> None:
    module = importlib.import_module("backend.alembic.versions.0064_limit_incident_reviews")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _engine(tmp_path: Path, name: str) -> sa.Engine:
    return sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")


def _insert(conn, event_id: int, kind: str) -> None:
    conn.execute(sa.text(
        "INSERT INTO limit_incident_reviews (incident_id, event_id, kind, status, "
        "claims_json, answers_json, created_at) VALUES (1, :e, :k, 'unscored', '[]', '{}', "
        "'2026-09-22 00:00:00')"), {"e": event_id, "k": kind})


def _assert_key_behaviour(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        _insert(conn, 10, "waiver")
        _insert(conn, 10, "thread")   # same event, other kind: a different review
        _insert(conn, 11, "waiver")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert(conn, 10, "waiver")


def test_upgrade_creates_table_indexes_and_the_unique_key(tmp_path):
    engine = _engine(tmp_path, "empty.sqlite3")
    _run("upgrade", engine)
    insp = inspect(engine)
    assert _TABLE in insp.get_table_names()
    assert {c["name"] for c in insp.get_columns(_TABLE)} == _COLUMNS
    assert _INDEXES <= {i["name"] for i in insp.get_indexes(_TABLE)}
    _assert_key_behaviour(engine)


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    """0001 materialises today's ORM, so the table already exists on a fresh chain."""
    from app.models import LimitIncidentReview

    engine = _engine(tmp_path, "fresh.sqlite3")
    LimitIncidentReview.__table__.create(bind=engine)
    before = {i["name"] for i in inspect(engine).get_indexes(_TABLE)}
    _run("upgrade", engine)
    assert {i["name"] for i in inspect(engine).get_indexes(_TABLE)} == before
    assert _INDEXES <= before


def test_the_orm_table_carries_the_same_key(tmp_path):
    from app.models import LimitIncidentReview

    engine = _engine(tmp_path, "orm.sqlite3")
    LimitIncidentReview.__table__.create(bind=engine)
    assert {c["name"] for c in inspect(engine).get_columns(_TABLE)} == _COLUMNS
    _assert_key_behaviour(engine)


def test_downgrade_drops_the_table(tmp_path):
    engine = _engine(tmp_path, "down.sqlite3")
    _run("upgrade", engine)
    _run("downgrade", engine)
    assert _TABLE not in inspect(engine).get_table_names()

from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.services.confirmations.llm import TradeDraft
from app.services.confirmations.service import _draft_to_row


def _run(method, engine):
    module = importlib.import_module("backend.alembic.versions.0063_extracted_trade_family_check")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _pre_0063(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre63.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE confirmation_documents (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("""
            CREATE TABLE extracted_trades (
                id INTEGER PRIMARY KEY,
                document_id INTEGER REFERENCES confirmation_documents (id),
                family VARCHAR(80), status VARCHAR(20), validation_status VARCHAR(20)
            )"""))
        conn.execute(sa.text("CREATE INDEX ix_extracted_trades_status ON extracted_trades (status)"))
        conn.execute(sa.text("INSERT INTO confirmation_documents (id) VALUES (1)"))
        conn.execute(sa.text(
            "INSERT INTO extracted_trades VALUES (1, 1, 'SnowballOption', 'extracted', 'valid')"))
    return engine


def test_upgrade_adds_a_nullable_json_column(tmp_path):
    engine = _pre_0063(tmp_path)
    _run("upgrade", engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("extracted_trades")}
    assert cols["family_check"]["nullable"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT family_check FROM extracted_trades")).scalar() is None


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    from app.models import ExtractedTrade

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    ExtractedTrade.__table__.create(bind=engine)
    _run("upgrade", engine)
    assert "family_check" in {c["name"] for c in inspect(engine).get_columns("extracted_trades")}


def test_downgrade_keeps_rows_fk_and_index(tmp_path):
    engine = _pre_0063(tmp_path)
    _run("upgrade", engine)
    _run("downgrade", engine)
    insp = inspect(engine)
    assert "family_check" not in {c["name"] for c in insp.get_columns("extracted_trades")}
    assert [fk["referred_table"] for fk in insp.get_foreign_keys("extracted_trades")] == [
        "confirmation_documents"]
    assert "ix_extracted_trades_status" in {i["name"] for i in insp.get_indexes("extracted_trades")}
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM extracted_trades")).scalar() == 1


def test_the_draft_carries_the_check_onto_the_row():
    check = {"status": "agree", "reason": None, "jev_family": "SnowballOption",
             "confidence": 0.9, "top": [["SnowballOption", 0.9]], "model": "typesafe/jev-1.13"}
    row = _draft_to_row(1, 1, TradeDraft(family="SnowballOption", terms={}, family_check=check))
    assert row.family_check == check
    assert _draft_to_row(1, 2, TradeDraft(family="SnowballOption", terms={})).family_check is None

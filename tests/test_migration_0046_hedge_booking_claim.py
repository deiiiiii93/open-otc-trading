from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0046_hedge_booking_claim"
    )


def _run(module, method: str, engine: sa.Engine) -> None:
    with engine.connect() as connection:
        original = module.op
        module.op = Operations(MigrationContext.configure(connection))
        try:
            getattr(module, method)()
            connection.commit()
        finally:
            module.op = original


def test_hedge_booking_claim_migration_round_trip(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'claim-mig.sqlite3'}")
    module = _migration()

    _run(module, "upgrade", engine)
    inspector = sa.inspect(engine)
    assert "hedge_booking_claims" in inspector.get_table_names()
    assert {
        "id",
        "portfolio_id",
        "risk_run_id",
        "underlying",
        "source_artifact_id",
        "workflow_id",
        "strategy",
        "actor",
        "claimed_at",
    } <= {column["name"] for column in inspector.get_columns("hedge_booking_claims")}
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("hedge_booking_claims")
    }
    assert {
        "uq_hedge_booking_claim_source_artifact",
        "uq_hedge_booking_claim_run_underlying",
    } <= unique_names

    _run(module, "upgrade", engine)
    _run(module, "downgrade", engine)
    assert "hedge_booking_claims" not in sa.inspect(engine).get_table_names()

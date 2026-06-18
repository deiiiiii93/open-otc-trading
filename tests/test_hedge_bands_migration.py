# tests/test_hedge_bands_migration.py
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load(name):
    path = Path("backend/alembic/versions") / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hedge_bands_upgrade_creates_table(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE underlyings (id INTEGER PRIMARY KEY)"
        ))
        mod = _load("0022_hedge_bands.py")
        mod.op = Operations(MigrationContext.configure(conn))
        mod.upgrade()
        insp = sa.inspect(conn)
        assert "hedge_bands" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("hedge_bands")}
        assert {"delta_cash_band", "gamma_cash_band", "vega_band", "underlying_id"} <= cols


def test_hedge_bands_upgrade_is_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE underlyings (id INTEGER PRIMARY KEY)"
        ))
        mod = _load("0022_hedge_bands.py")
        mod.op = Operations(MigrationContext.configure(conn))
        mod.upgrade()
        mod.upgrade()  # second call must not raise
        insp = sa.inspect(conn)
        assert "hedge_bands" in insp.get_table_names()


def test_hedge_bands_downgrade_removes_table(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE underlyings (id INTEGER PRIMARY KEY)"
        ))
        mod = _load("0022_hedge_bands.py")
        mod.op = Operations(MigrationContext.configure(conn))
        mod.upgrade()
        mod.downgrade()
        insp = sa.inspect(conn)
        assert "hedge_bands" not in insp.get_table_names()

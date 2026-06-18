# tests/test_hedge_bands_default_unique_migration.py
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load(name):
    path = Path("backend/alembic/versions") / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bind(conn, mod):
    mod.op = Operations(MigrationContext.configure(conn))
    return mod


def _make_table(conn):
    conn.execute(sa.text("CREATE TABLE underlyings (id INTEGER PRIMARY KEY)"))
    bands = _bind(conn, _load("0022_hedge_bands.py"))
    bands.upgrade()


def test_default_unique_blocks_second_null(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        _make_table(conn)
        # Before 0023, two NULL-underlying defaults rows slip through.
        conn.execute(sa.text("INSERT INTO hedge_bands "
                             "(underlying_id, delta_cash_band, gamma_cash_band, vega_band, currency) "
                             "VALUES (NULL, 1, 2, 3, 'CNY')"))
        mod = _bind(conn, _load("0023_hedge_bands_default_unique.py"))
        mod.upgrade()
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("INSERT INTO hedge_bands "
                                 "(underlying_id, delta_cash_band, gamma_cash_band, vega_band, currency) "
                                 "VALUES (NULL, 4, 5, 6, 'CNY')"))


def test_default_unique_upgrade_is_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        _make_table(conn)
        mod = _bind(conn, _load("0023_hedge_bands_default_unique.py"))
        mod.upgrade()
        mod.upgrade()  # second call must not raise
        names = [r[0] for r in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'"))]
        assert "uq_hedge_bands_default" in names


def test_default_unique_downgrade_removes_index(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        _make_table(conn)
        mod = _bind(conn, _load("0023_hedge_bands_default_unique.py"))
        mod.upgrade()
        mod.downgrade()
        names = [r[0] for r in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'"))]
        assert "uq_hedge_bands_default" not in names
        # downgrade restores the multi-NULL-rows behavior
        for vals in ("(NULL, 1, 2, 3, 'CNY')", "(NULL, 4, 5, 6, 'CNY')"):
            conn.execute(sa.text("INSERT INTO hedge_bands "
                                 "(underlying_id, delta_cash_band, gamma_cash_band, vega_band, currency) "
                                 f"VALUES {vals}"))

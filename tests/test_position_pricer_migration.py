from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPO_ROOT / "backend/alembic/versions/0009_strip_default_quad_grid_points.py"
)


def _load_migration():
    """Load the migration module by file path (leading-digit module names
    can't be imported by dotted notation)."""
    spec = importlib.util.spec_from_file_location("_mig0009", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def positions_engine(tmp_path: Path):
    db_path = tmp_path / "mig.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            """
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY,
                portfolio_id INTEGER NOT NULL,
                underlying VARCHAR(80) NOT NULL,
                product_type VARCHAR(120) NOT NULL,
                product_kwargs JSON NOT NULL,
                engine_name VARCHAR(120) NOT NULL,
                engine_kwargs JSON NOT NULL,
                quantity FLOAT NOT NULL,
                entry_price FLOAT NOT NULL,
                status VARCHAR(40) NOT NULL,
                source_trade_id VARCHAR(160),
                source_row INTEGER,
                mapping_status VARCHAR(40) NOT NULL,
                mapping_error TEXT,
                source_payload JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                trade_effective_date DATETIME
            )
            """
        ))
    yield engine


def _insert(engine, *, id, engine_kwargs):
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO positions(id, portfolio_id, underlying, product_type, "
            "product_kwargs, engine_name, engine_kwargs, quantity, entry_price, "
            "status, mapping_status, created_at, updated_at) VALUES "
            "(:id, 1, 'X', 'SnowballOption', '{}', 'E', :ek, 1.0, 0.0, 'open', 'supported', "
            "'2026-01-01', '2026-01-01')"
        ), {"id": id, "ek": json.dumps(engine_kwargs)})


def _engine_kwargs(engine, *, id):
    with engine.begin() as conn:
        ek = conn.execute(
            sa.text("SELECT engine_kwargs FROM positions WHERE id=:id"), {"id": id}
        ).scalar()
    return json.loads(ek)


def _run(positions_engine, fn_name: str):
    """Drive upgrade/downgrade in isolation by stubbing alembic.op for the
    duration of the call.

    The migration uses op.get_bind() to retrieve the current connection.
    We attach a tiny proxy to alembic.op so the migration can run without
    a full alembic environment.
    """
    mig = _load_migration()
    from alembic import op as _op
    with positions_engine.begin() as conn:
        original_get_bind = getattr(_op, "get_bind", None)
        _op.get_bind = lambda: conn  # type: ignore[assignment]
        try:
            getattr(mig, fn_name)()
        finally:
            if original_get_bind is None:
                # Best effort restore: leave attribute set; tests don't share state.
                pass
            else:
                _op.get_bind = original_get_bind  # type: ignore[assignment]


def test_migration_strips_default_grid_points_only(positions_engine):
    _insert(positions_engine, id=1,
            engine_kwargs={"params_type": "quad_params",
                           "params_kwargs": {"grid_points": 1001}})
    _insert(positions_engine, id=2,
            engine_kwargs={"params_type": "quad_params",
                           "params_kwargs": {"grid_points": 501}})
    _insert(positions_engine, id=3,
            engine_kwargs={"params_type": "mc_params",
                           "params_kwargs": {"num_paths": 100000}})

    _run(positions_engine, "upgrade")

    assert _engine_kwargs(positions_engine, id=1) == {"params_type": "quad_params"}
    assert _engine_kwargs(positions_engine, id=2) == {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 501},
    }
    assert _engine_kwargs(positions_engine, id=3) == {
        "params_type": "mc_params",
        "params_kwargs": {"num_paths": 100000},
    }


def test_migration_downgrade_restores_grid_points_1001(positions_engine):
    _insert(positions_engine, id=1,
            engine_kwargs={"params_type": "quad_params"})

    _run(positions_engine, "downgrade")

    assert _engine_kwargs(positions_engine, id=1) == {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 1001},
    }

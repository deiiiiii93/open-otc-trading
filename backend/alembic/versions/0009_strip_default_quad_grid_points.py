"""strip default quad grid_points

Revision ID: 0009_strip_default_quad_grid_points
Revises: 0008_task_runs
Create Date: 2026-05-12
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text


revision = "0009_strip_default_quad_grid_points"
down_revision = "0008_task_runs"
branch_labels = None
depends_on = None


def _load(value):
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, engine_kwargs FROM positions")).fetchall()
    for pid, raw in rows:
        kw = _load(raw)
        if not isinstance(kw, dict):
            continue
        if kw.get("params_type") != "quad_params":
            continue
        pk = kw.get("params_kwargs") or {}
        if not isinstance(pk, dict) or pk.get("grid_points") != 1001:
            continue
        pk.pop("grid_points", None)
        if pk:
            kw["params_kwargs"] = pk
        else:
            kw.pop("params_kwargs", None)
        bind.execute(
            text("UPDATE positions SET engine_kwargs = :ek WHERE id = :id"),
            {"ek": json.dumps(kw), "id": pid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, engine_kwargs FROM positions")).fetchall()
    for pid, raw in rows:
        kw = _load(raw)
        if not isinstance(kw, dict):
            continue
        if kw.get("params_type") != "quad_params":
            continue
        pk = kw.get("params_kwargs") or {}
        if "grid_points" in pk:
            continue
        pk["grid_points"] = 1001
        kw["params_kwargs"] = pk
        bind.execute(
            text("UPDATE positions SET engine_kwargs = :ek WHERE id = :id"),
            {"ek": json.dumps(kw), "id": pid},
        )

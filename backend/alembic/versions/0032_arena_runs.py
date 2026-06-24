"""arena_run and arena_match tables

Revision ID: 0032_arena_runs
Revises: 0031_asian_averaging_weight
Create Date: 2026-06-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0032_arena_runs"
down_revision = "0031_asian_averaging_weight"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("arena_run"):
        op.create_table(
            "arena_run",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("workflow_ids", sa.JSON(), nullable=False),
            sa.Column("model_ids", sa.JSON(), nullable=False),
            sa.Column("weights", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
        )

    if not _has_table("arena_match"):
        op.create_table(
            "arena_match",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("arena_run.id"),
                nullable=False,
            ),
            sa.Column("workflow_id", sa.String(), nullable=False),
            sa.Column("model_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("objective_score", sa.Float(), nullable=True),
            sa.Column("judged_score", sa.Float(), nullable=True),
            sa.Column("total_score", sa.Float(), nullable=True),
            sa.Column("judge_missing", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("transcript_path", sa.String(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("run_id", "workflow_id", "model_id", name="uq_arena_match_run_workflow_model"),
        )

    match_indexes = _indexes("arena_match")
    if "ix_arena_match_run_id" not in match_indexes:
        op.create_index("ix_arena_match_run_id", "arena_match", ["run_id"])
    if "ix_arena_match_model_id" not in match_indexes:
        op.create_index("ix_arena_match_model_id", "arena_match", ["model_id"])


def downgrade() -> None:
    if _has_table("arena_match"):
        if "ix_arena_match_model_id" in _indexes("arena_match"):
            op.drop_index("ix_arena_match_model_id", table_name="arena_match")
        if "ix_arena_match_run_id" in _indexes("arena_match"):
            op.drop_index("ix_arena_match_run_id", table_name="arena_match")
        op.drop_table("arena_match")
    if _has_table("arena_run"):
        op.drop_table("arena_run")

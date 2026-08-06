"""seed the four shipped report templates

Revision ID: 0054_seed_report_templates
Revises: 0053_report_templates

Reads the YAML seed files rather than embedding them, and upserts by slug so a
re-run picks up seed edits instead of inserting duplicates. Uses a
migration-local Core table, never the ORM model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
import yaml
from alembic import op

revision = "0054_seed_report_templates"
down_revision = "0053_report_templates"
branch_labels = None
depends_on = None

_SEED_SLUGS = (
    "trader-daily",
    "risk-manager-daily",
    "high-board-daily",
    "portfolio-snapshot",
)

# Migration-local Core table. Deliberately not app.models.ReportTemplate: an
# ORM model tracks head, while a migration must describe the schema as it is at
# THIS revision.
report_templates = sa.table(
    "report_templates",
    sa.column("id", sa.Integer),
    sa.column("slug", sa.String),
    sa.column("title", sa.String),
    sa.column("persona", sa.String),
    sa.column("description", sa.Text),
    sa.column("spec", sa.Text),
    sa.column("source", sa.String),
    sa.column("version", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def _seeds_dir() -> Path:
    # backend/alembic/versions/ -> backend/app/services/reporting/seeds/
    return (
        Path(__file__).resolve().parents[2]
        / "app" / "services" / "reporting" / "seeds"
    )


def upgrade() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    bind = op.get_bind()
    seeds_dir = _seeds_dir()

    for slug in _SEED_SLUGS:
        path = seeds_dir / f"{slug}.yaml"
        spec_text = path.read_text(encoding="utf-8")
        meta = (yaml.safe_load(spec_text) or {}).get("meta") or {}

        existing = bind.execute(
            sa.select(report_templates.c.id).where(report_templates.c.slug == slug)
        ).first()

        values = {
            "title": meta.get("title") or slug,
            "persona": meta.get("persona") or "risk_manager",
            "description": (meta.get("description") or "").strip(),
            "spec": spec_text,
            "source": "seed",
            "updated_at": now,
        }

        if existing is None:
            bind.execute(
                report_templates.insert().values(
                    slug=slug, version=1, created_at=now, **values
                )
            )
        else:
            bind.execute(
                report_templates.update()
                .where(report_templates.c.slug == slug)
                .values(**values)
            )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        report_templates.delete().where(
            sa.and_(
                report_templates.c.slug.in_(_SEED_SLUGS),
                report_templates.c.source == "seed",
            )
        )
    )

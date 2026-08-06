"""Report template store: validate-then-commit over the report_templates table.

Every write runs the full parse + reference check before touching the row, so a
rejected save leaves the live template byte-unchanged (HTTP 422) rather than
persisting a template that would render as a gap. This mirrors
``channel_registry_writer._mutate``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import ReportTemplate

from .template_spec import (
    TemplateSpec,
    TemplateSpecError,
    parse_spec,
    validate_spec,
)


class TemplateProtectedError(Exception):
    """Raised when a seeded template is deleted."""


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _checked_spec(slug: str, spec_yaml: str) -> TemplateSpec:
    """Parse, validate, and confirm the spec's slug matches the target slug."""
    spec = parse_spec(spec_yaml)
    validate_spec(spec)
    if spec.meta.slug != slug:
        raise TemplateSpecError(
            [
                f"meta.slug {spec.meta.slug!r} does not match the target slug "
                f"{slug!r}; a template's spec owns its slug"
            ]
        )
    return spec


def validate_only(spec_yaml: str) -> list[str]:
    """Return validation errors for a spec without saving. Never raises."""
    try:
        spec = parse_spec(spec_yaml)
        validate_spec(spec)
    except TemplateSpecError as exc:
        return list(exc.errors)
    return []


def list_templates(
    *, persona: str | None = None, session: Session | None = None
) -> list[ReportTemplate]:
    with _session_scope(session) as sess:
        query = sess.query(ReportTemplate)
        if persona is not None:
            query = query.filter(ReportTemplate.persona == persona)
        return list(query.order_by(ReportTemplate.slug).all())


def get_template(
    *, slug: str, session: Session | None = None
) -> ReportTemplate | None:
    with _session_scope(session) as sess:
        return (
            sess.query(ReportTemplate)
            .filter(ReportTemplate.slug == slug)
            .one_or_none()
        )


def save_template(
    *,
    slug: str,
    spec_yaml: str,
    source: str = "user",
    session: Session | None = None,
) -> ReportTemplate:
    """Create or update a template. Validates fully BEFORE any mutation."""
    spec = _checked_spec(slug, spec_yaml)

    with _session_scope(session) as sess:
        row = (
            sess.query(ReportTemplate)
            .filter(ReportTemplate.slug == slug)
            .one_or_none()
        )
        if row is None:
            row = ReportTemplate(slug=slug, source=source, version=1)
            sess.add(row)
        else:
            row.version = (row.version or 0) + 1

        row.title = spec.meta.title
        row.persona = spec.meta.persona
        row.description = spec.meta.description
        row.spec = spec_yaml
        sess.flush()
        return row


def delete_template(*, slug: str, session: Session | None = None) -> bool:
    with _session_scope(session) as sess:
        row = (
            sess.query(ReportTemplate)
            .filter(ReportTemplate.slug == slug)
            .one_or_none()
        )
        if row is None:
            return False
        if row.source == "seed":
            raise TemplateProtectedError(
                f"template {slug!r} is seeded and cannot be deleted; "
                "edit it or create a new template instead"
            )
        sess.delete(row)
        sess.flush()
        return True


__all__ = [
    "TemplateProtectedError",
    "validate_only",
    "list_templates",
    "get_template",
    "save_template",
    "delete_template",
]

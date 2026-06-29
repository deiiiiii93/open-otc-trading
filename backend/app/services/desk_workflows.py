"""CRUD for DeskWorkflow rows (Python-script workflows)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import DeskWorkflow
from .desk_workflows_script import WorkflowScriptError, validate_script


def upsert_desk_workflow(
    session: Session, *, slug: str, script: str, source: str = "user"
) -> DeskWorkflow:
    meta = validate_script(script, slug=slug)
    wf = session.query(DeskWorkflow).filter_by(slug=slug).one_or_none()
    if wf is None:
        wf = DeskWorkflow(slug=slug, source=source)
        session.add(wf)
    wf.title = meta["title"]
    wf.persona = meta["persona"]
    wf.description = meta.get("description", "")
    wf.scope = meta["scope"]
    wf.default_mode = meta["mode"]
    wf.script = script
    session.flush()
    return wf


def list_desk_workflows(session: Session) -> list[DeskWorkflow]:
    return session.query(DeskWorkflow).order_by(DeskWorkflow.slug).all()


def get_desk_workflow(session: Session, slug: str) -> DeskWorkflow | None:
    return session.query(DeskWorkflow).filter_by(slug=slug).one_or_none()


def delete_desk_workflow(session: Session, slug: str) -> None:
    wf = session.query(DeskWorkflow).filter_by(slug=slug).one_or_none()
    if wf is None:
        return
    if wf.source == "seed":
        raise WorkflowScriptError(f"workflow {slug!r} is seeded and cannot be deleted")
    session.delete(wf)
    session.flush()

"""CRUD API for DeskWorkflow rows (Python-script workflows)."""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import DeskWorkflowOut, DeskWorkflowSave, DeskWorkflowSummaryOut
from ..services.desk_workflows import (
    delete_desk_workflow,
    get_desk_workflow,
    list_desk_workflows,
    upsert_desk_workflow,
)
from ..services.desk_workflows_script import (
    WorkflowScriptError,
    extract_slug,
    validate_script,
)


def build_desk_workflows_router(get_db: Callable | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/workflows", tags=["workflows"])

    def _get_db():
        if get_db is not None:
            yield from get_db()
        else:
            from app import database

            db = database.SessionLocal()
            try:
                yield db
            finally:
                db.close()

    @router.get("", response_model=list[DeskWorkflowSummaryOut])
    def list_workflows(session=Depends(_get_db)):
        return list_desk_workflows(session)

    @router.post("/validate")
    def validate(payload: DeskWorkflowSave):
        try:
            validate_script(payload.script, slug=extract_slug(payload.script))
        except WorkflowScriptError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "error": None}

    @router.get("/{slug}", response_model=DeskWorkflowOut)
    def get_workflow(slug: str, session=Depends(_get_db)):
        wf = get_desk_workflow(session, slug)
        if wf is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return wf

    def _upsert(slug: str, payload: DeskWorkflowSave, session):
        try:
            wf = upsert_desk_workflow(session, slug=slug, script=payload.script)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
        session.refresh(wf)
        return wf

    @router.post("", response_model=DeskWorkflowOut)
    def create_workflow(payload: DeskWorkflowSave, session=Depends(_get_db)):
        try:
            slug = extract_slug(payload.script)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if get_desk_workflow(session, slug) is not None:
            raise HTTPException(status_code=409, detail=f"workflow {slug!r} already exists")
        return _upsert(slug, payload, session)

    @router.put("/{slug}", response_model=DeskWorkflowOut)
    def update_workflow(slug: str, payload: DeskWorkflowSave, session=Depends(_get_db)):
        return _upsert(slug, payload, session)

    @router.delete("/{slug}")
    def delete_workflow(slug: str, session=Depends(_get_db)):
        try:
            delete_desk_workflow(session, slug)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        return {"ok": True}

    return router

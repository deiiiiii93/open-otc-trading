"""Report template and generation REST surface.

Mirrors the Skills router's lint-before-save UX: `validate` returns errors as a
200 body so the editor can show them live, while `PUT` returns 422 and persists
nothing when a spec does not validate.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ReportGenerateIn,
    ReportTemplateOut,
    ReportTemplateValidateOut,
    ReportTemplateWriteIn,
)
from app.services.reporting import blocks as _blocks  # noqa: F401 - registers producers
from app.services.reporting import templates as templates_svc
from app.services.reporting.generate import TemplateNotFound, generate_report
from app.services.reporting.registry import list_blocks
from app.services.reporting.template_spec import TemplateSpecError
from app.services.reporting.templates import TemplateProtectedError


def build_reports_router() -> APIRouter:
    router = APIRouter(prefix="/api/reports", tags=["reports"])

    def _out(row, *, include_spec: bool = False) -> ReportTemplateOut:
        return ReportTemplateOut(
            slug=row.slug, title=row.title, persona=row.persona,
            description=row.description or "", source=row.source,
            version=row.version, spec=row.spec if include_spec else None,
        )

    @router.get("/blocks")
    def list_report_blocks() -> dict:
        catalog = [spec.catalog_entry() for spec in list_blocks()]
        return {"blocks": catalog, "total": len(catalog)}

    @router.get("/templates", response_model=list[ReportTemplateOut])
    def list_templates(persona: str | None = Query(default=None)):
        return [_out(row) for row in templates_svc.list_templates(persona=persona)]

    # Declared BEFORE /templates/{slug}: FastAPI matches in declaration order,
    # so the reverse would resolve "validate" as a slug and 404.
    @router.post("/templates/validate", response_model=ReportTemplateValidateOut)
    def validate_template(payload: ReportTemplateWriteIn):
        errors = templates_svc.validate_only(payload.spec_yaml)
        return ReportTemplateValidateOut(ok=not errors, errors=errors)

    @router.get("/templates/{slug}", response_model=ReportTemplateOut)
    def get_template(slug: str):
        row = templates_svc.get_template(slug=slug)
        if row is None:
            raise HTTPException(status_code=404, detail=f"template not found: {slug}")
        return _out(row, include_spec=True)

    @router.put("/templates/{slug}", response_model=ReportTemplateOut)
    def put_template(slug: str, payload: ReportTemplateWriteIn):
        try:
            row = templates_svc.save_template(slug=slug, spec_yaml=payload.spec_yaml)
        except TemplateSpecError as exc:
            raise HTTPException(
                status_code=422, detail={"errors": list(exc.errors)}
            ) from exc
        return _out(row, include_spec=True)

    @router.delete("/templates/{slug}", status_code=204)
    def delete_template(slug: str):
        try:
            deleted = templates_svc.delete_template(slug=slug)
        except TemplateProtectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"template not found: {slug}")
        return None

    @router.post("/generate")
    def generate(payload: ReportGenerateIn):
        from app.services.reporting.narrator import default_narrator

        try:
            job = generate_report(
                template_slug=payload.template_slug,
                portfolio_id=payload.portfolio_id,
                compare_to=payload.compare_to,
                narrate=default_narrator(),
            )
        except TemplateNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "id": job.id,
            "report_type": job.report_type,
            "status": job.status,
            "template_slug": job.template_slug,
            "compare_to_run_id": job.compare_to_run_id,
            "request_payload": job.request_payload,
            "result_payload": job.result_payload,
            "artifact_paths": job.artifact_paths,
            "created_at": job.created_at,
        }

    return router


__all__ = ["build_reports_router"]

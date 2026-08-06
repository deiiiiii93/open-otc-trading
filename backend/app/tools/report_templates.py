"""@tool wrappers for the report template module.

Thin adapters over ``services/reporting``: parse args, call the service, shape
JSON. The production narrator is bound here, at the edge, rather than inside the
pipeline, so the pipeline stays testable without a model.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.reporting import blocks as _blocks  # noqa: F401 - registers producers
from app.services.reporting import templates as templates_svc
from app.services.reporting.contracts import BlockContext
from app.services.reporting.generate import TemplateNotFound, generate_report
from app.services.reporting.registry import list_blocks, resolve_block
from app.services.reporting.template_spec import TemplateSpecError


# ----- args schemas -----------------------------------------------------------


class ListTemplatesInput(BaseModel):
    persona: str | None = Field(
        default=None, description="Filter to one persona: trader, risk_manager, high_board."
    )


class GetTemplateInput(BaseModel):
    slug: str = Field(description="Template slug from list_report_templates.")


class ResolveBlockInput(BaseModel):
    key: str = Field(description="Block key from list_report_blocks.")
    portfolio_id: int = Field(description="Portfolio to resolve the block against.")
    compare_to: int | None = Field(
        default=None, description="Risk run id to compare against, for diff blocks."
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Optional block params, e.g. {'top_n': 5}."
    )


class SaveTemplateInput(BaseModel):
    slug: str = Field(description="Template slug; must match meta.slug in the spec.")
    spec_yaml: str = Field(
        description=(
            "Full template spec as YAML. Must declare meta (slug, title, persona) "
            "and sections. Every blocks[].key must exist in list_report_blocks, and "
            "each render must accept that block's shape."
        )
    )


class GenerateReportInput(BaseModel):
    template_slug: str = Field(description="Template slug to generate from.")
    portfolio_id: int = Field(description="Portfolio to report on.")
    compare_to: int | None = Field(
        default=None,
        description="Risk run id to compare against. Defaults to the prior run.",
    )


# ----- tools ------------------------------------------------------------------


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_report_blocks")
def list_report_blocks_tool() -> dict[str, Any]:
    """List every data block a report template may declare.

    Returns each block's key, title, output shape, required params, and domain.
    Use this before authoring or editing a template: a template may only name
    keys that appear here.
    """
    catalog = [spec.catalog_entry() for spec in list_blocks()]
    return {"blocks": catalog, "total": len(catalog)}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_report_templates", args_schema=ListTemplatesInput)
def list_report_templates_tool(persona: str | None = None) -> dict[str, Any]:
    """List report templates, optionally filtered to one persona."""
    rows = templates_svc.list_templates(persona=persona)
    return {
        "templates": [
            {
                "slug": row.slug, "title": row.title, "persona": row.persona,
                "description": row.description, "source": row.source,
                "version": row.version,
            }
            for row in rows
        ],
        "total": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_report_template", args_schema=GetTemplateInput)
def get_report_template_tool(slug: str) -> dict[str, Any]:
    """Return one template's full YAML spec. Raises ValueError if not found."""
    row = templates_svc.get_template(slug=slug)
    if row is None:
        raise ValueError(f"Report template not found: slug={slug!r}")
    return {
        "slug": row.slug, "title": row.title, "persona": row.persona,
        "description": row.description, "source": row.source,
        "version": row.version, "spec": row.spec,
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("resolve_report_block", args_schema=ResolveBlockInput)
def resolve_report_block_tool(
    key: str,
    portfolio_id: int,
    compare_to: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one report block to its deterministic data.

    Use this to pull additional evidence while drafting narrative. The numbers
    come from persisted risk evidence, never from the model.
    """
    result = resolve_block(
        key,
        BlockContext(
            portfolio_id=portfolio_id,
            compare_to_run_id=compare_to,
            params=params or {},
        ),
    )
    return result.model_dump()


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("save_report_template", args_schema=SaveTemplateInput)
def save_report_template_tool(slug: str, spec_yaml: str) -> dict[str, Any]:
    """Create or update a report template from a YAML spec.

    Validation failures are returned as data, not raised, so the model can fix
    the spec and retry. Nothing persists unless the spec fully validates.
    """
    try:
        row = templates_svc.save_template(slug=slug, spec_yaml=spec_yaml)
    except TemplateSpecError as exc:
        return {"ok": False, "slug": slug, "errors": list(exc.errors)}
    return {
        "ok": True, "slug": row.slug, "title": row.title,
        "persona": row.persona, "version": row.version, "errors": [],
    }


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_report", args_schema=GenerateReportInput)
def generate_report_tool(
    template_slug: str, portfolio_id: int, compare_to: int | None = None
) -> dict[str, Any]:
    """Generate a report from a template and persist it.

    Every number comes from deterministic producers; the agent contributes only
    the narrative prose for sections that declare one.
    """
    from app.services.reporting.narrator import default_narrator

    try:
        job = generate_report(
            template_slug=template_slug,
            portfolio_id=portfolio_id,
            compare_to=compare_to,
            narrate=default_narrator(),
        )
    except TemplateNotFound as exc:
        raise ValueError(str(exc)) from exc

    document = job.result_payload or {}
    flagged = sum(
        len(section.get("grounding", {}).get("flags") or [])
        for section in document.get("sections") or []
    )
    return {
        "report_id": job.id,
        "template_slug": template_slug,
        "status": job.status,
        "section_count": len(document.get("sections") or []),
        "grounding_flags": flagged,
        "message": f"Report #{job.id} generated. Open it on the Reports page.",
    }


__all__ = [
    "list_report_blocks_tool",
    "list_report_templates_tool",
    "get_report_template_tool",
    "resolve_report_block_tool",
    "save_report_template_tool",
    "generate_report_tool",
]

"""Agent tool: persist a DeskWorkflow drafted by the build-workflow skill."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.desk_workflows import upsert_desk_workflow
from app.services.desk_workflows_script import WorkflowScriptError, extract_meta


class SaveDeskWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script: str = Field(
        description="Full Python workflow script including a `meta = {...}` literal."
    )


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("save_desk_workflow", args_schema=SaveDeskWorkflowInput)
def save_desk_workflow_tool(script: str) -> dict[str, Any]:
    """Validate and persist a desk workflow script. Returns the slug on success."""
    database.init_db()
    try:
        slug = extract_meta(script)["name"]
    except WorkflowScriptError as exc:
        return {"ok": False, "error": str(exc)}
    with database.SessionLocal() as session:
        try:
            wf = upsert_desk_workflow(session, slug=slug, script=script)
        except WorkflowScriptError as exc:
            return {"ok": False, "error": str(exc)}
        session.commit()
        return {"ok": True, "slug": wf.slug, "title": wf.title}

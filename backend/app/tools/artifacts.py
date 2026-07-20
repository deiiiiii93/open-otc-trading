"""Progressive, exact artifact disclosure tools (no semantic retrieval)."""
from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app import database
from app.services.deep_agent import artifact_access
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup


class ListArtifactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = None
    tool_name: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ArtifactIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: int = Field(gt=0)


class ReadArtifactInput(ArtifactIdInput):
    json_pointer: str | None = None
    section: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=500)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_artifacts", args_schema=ListArtifactsInput)
def list_artifacts_tool(
    kind: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
    config: RunnableConfig = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """First disclosure step: list compact artifact cards in the current workflow.
    Choose an artifact id, then inspect it; this performs no semantic retrieval."""
    workflow_id = artifact_access.workflow_id_from_config(config)
    with database.SessionLocal() as session:
        rows = artifact_access.list_workflow_artifacts(
            session,
            workflow_id=workflow_id,
            kind=kind,
            tool_name=tool_name,
            limit=limit,
        )
    return {"workflow_scope": "current", "artifacts": rows}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("inspect_artifact", args_schema=ArtifactIdInput)
def inspect_artifact_tool(
    artifact_id: int,
    config: RunnableConfig = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Second disclosure step: inspect provenance, timestamps, and the deterministic
    section map without returning the body. Use a returned selector with read_artifact."""
    workflow_id = artifact_access.workflow_id_from_config(config)
    with database.SessionLocal() as session:
        return artifact_access.inspect_artifact(
            session, workflow_id=workflow_id, artifact_id=artifact_id
        )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("read_artifact", args_schema=ReadArtifactInput)
def read_artifact_tool(
    artifact_id: int,
    json_pointer: str | None = None,
    section: str | None = None,
    offset: int = 0,
    limit: int = 200,
    config: RunnableConfig = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Final disclosure step: read exact content by artifact id and explicit JSON or
    Markdown selector (or a bounded line slice); never semantic top-k retrieval."""
    workflow_id = artifact_access.workflow_id_from_config(config)
    with database.SessionLocal() as session:
        return artifact_access.read_artifact(
            session,
            workflow_id=workflow_id,
            artifact_id=artifact_id,
            json_pointer=json_pointer,
            section=section,
            offset=offset,
            limit=limit,
        )

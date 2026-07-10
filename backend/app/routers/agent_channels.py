"""Flag-gated CRUD over the agent channel/model registry (config/agent_channels.yaml)."""
from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, HTTPException

from ..config import Settings, get_settings
from ..schemas import (
    AgentRegistryOut,
    ChannelWriteIn,
    DefaultWriteIn,
    ModelWriteIn,
)
from ..services.deep_agent import channel_registry as cr
from ..services.deep_agent import channel_registry_writer as writer
from ..services.deep_agent.model_factory import agent_registry_config


class SupportsModelRebuild(Protocol):
    def rebuild_default_model(self) -> None: ...


def build_agent_channels_router(
    agent_service: SupportsModelRebuild,
    *,
    settings: Settings | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent-channels"])

    def _require_write() -> None:
        active = settings or get_settings()
        if not active.feature_model_write_api:
            raise HTTPException(
                status_code=403,
                detail="model write API disabled (set OPEN_OTC_FEATURE_MODEL_WRITE_API=true)",
            )

    def _apply(fn, *args) -> dict:
        _require_write()
        try:
            new_registry = fn(*args)
        except writer.RegistryValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except writer.RegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        agent_service.rebuild_default_model()
        return agent_registry_config(new_registry)

    @router.get("/registry", response_model=AgentRegistryOut)
    def get_registry() -> dict:
        return agent_registry_config(cr.get_registry())

    @router.post("/channels", response_model=AgentRegistryOut)
    def create_channel(payload: ChannelWriteIn) -> dict:
        return _apply(writer.add_channel, payload.model_dump())

    @router.put("/channels/{name}", response_model=AgentRegistryOut)
    def update_channel(name: str, payload: ChannelWriteIn) -> dict:
        return _apply(writer.update_channel, name, payload.model_dump())

    @router.delete("/channels/{name}", response_model=AgentRegistryOut)
    def delete_channel(name: str) -> dict:
        return _apply(writer.delete_channel, name)

    @router.post("/channels/{name}/models", response_model=AgentRegistryOut)
    def add_model(name: str, payload: ModelWriteIn) -> dict:
        return _apply(writer.add_model, name, payload.model_dump())

    @router.put("/channels/{name}/models/{model_id:path}", response_model=AgentRegistryOut)
    def update_model(name: str, model_id: str, payload: ModelWriteIn) -> dict:
        return _apply(writer.update_model, name, model_id, payload.model_dump())

    @router.delete("/channels/{name}/models/{model_id:path}", response_model=AgentRegistryOut)
    def delete_model(name: str, model_id: str) -> dict:
        return _apply(writer.delete_model, name, model_id)

    @router.put("/registry/default", response_model=AgentRegistryOut)
    def set_default(payload: DefaultWriteIn) -> dict:
        return _apply(writer.set_default, payload.channel, payload.model)

    @router.post("/channels/validate")
    def validate(payload: dict) -> dict:
        _require_write()
        kind = payload.get("kind")
        body = payload.get("payload") or {}
        try:
            writer.validate_draft(kind, body)
        except writer.RegistryValidationError as exc:
            return {"ok": False, "errors": [str(exc)]}
        except writer.RegistryConflictError as exc:
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "errors": []}

    return router

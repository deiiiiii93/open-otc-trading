"""REST API over MemoryStore (spec §Gateway API). All mutations via MemoryStore."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory.config import get_memory_config
from app.services.deep_agent.memory.runtime import get_memory_store
from app.services.deep_agent.memory.store import (
    MemoryConflictError, MemoryNotFound, MemoryValidationError, _to_fact,
)

_VALID_SCOPES = {"user", "book", "domain", "correction"}
_VALID_STATUS = {"active", "proposed", "approved", "archived", "all"}
_STATUS_ORDER = {"proposed": 0, "approved": 1, "active": 2, "archived": 3}
_CANON = {"user": "desk", "correction": "desk", "domain": "global"}


class FactOut(BaseModel):
    id: int
    scope_type: str
    scope_id: str
    content: str
    confidence: float
    status: str
    category: str | None
    source_error: bool
    created_at: Any
    updated_at: Any


class FactCreate(BaseModel):
    scope_type: str
    scope_id: str | None = None
    content: str
    confidence: float = 1.0
    category: str | None = None


class FactPatch(BaseModel):
    content: str | None = None
    confidence: float | None = None
    category: str | None = None


def _out(fact) -> dict:
    return FactOut(id=fact.id, scope_type=fact.scope_type, scope_id=fact.scope_id,
                   content=fact.content, confidence=fact.confidence, status=fact.status,
                   category=fact.category, source_error=fact.source_error,
                   created_at=fact.created_at, updated_at=fact.updated_at).model_dump()


def build_memory_router() -> APIRouter:
    router = APIRouter(prefix="/api/memory", tags=["memory"])
    # NOTE: resolve the store INSIDE each handler via get_memory_store(), never
    # bind it once at build time — otherwise reset_memory_runtime() / config
    # changes leave the router pointing at a stale store + stale config.

    @router.get("/facts")
    def list_facts(scope_type: str | None = None, scope_id: str | None = None,
                   status: str | None = None, limit: int = Query(50, le=200), offset: int = 0):
        if scope_type is not None and scope_type not in _VALID_SCOPES:
            raise HTTPException(400, "invalid scope_type")
        if status is not None and status not in _VALID_STATUS:
            raise HTTPException(400, "invalid status")
        with database.SessionLocal() as session:
            q = session.query(MemoryEntry)
            if scope_type:
                q = q.filter(MemoryEntry.scope_type == scope_type)
            if scope_id:
                q = q.filter(MemoryEntry.scope_id == scope_id)
            if status is None:
                q = q.filter(MemoryEntry.status != "archived")
            elif status != "all":
                q = q.filter(MemoryEntry.status == status)
            rows = q.all()
            rows.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 9),
                                     -r.confidence, -r.updated_at.timestamp()))
            return {"items": [_out(_to_fact(r)) for r in rows[offset:offset + limit]],
                    "total": len(rows)}

    @router.post("/facts", status_code=201)
    def create_fact(body: FactCreate):
        if body.scope_type not in _VALID_SCOPES:
            raise HTTPException(400, "invalid scope_type")
        if body.scope_type == "book":
            if not body.scope_id:
                raise HTTPException(400, "scope_id required for book")
            scope_id = body.scope_id
        else:
            scope_id = _CANON[body.scope_type]
        with database.SessionLocal() as session:
            try:
                fact = get_memory_store().create(
                    session, scope_type=body.scope_type, scope_id=scope_id,
                    content=body.content, confidence=body.confidence,
                    category=body.category, created_by="api")
                session.commit()
            except MemoryValidationError as exc:
                raise HTTPException(400, str(exc)) from exc
            except MemoryConflictError as exc:
                raise HTTPException(409, str(exc)) from exc
            return _out(fact)

    @router.patch("/facts/{fact_id}")
    def patch_fact(fact_id: int, body: FactPatch):
        with database.SessionLocal() as session:
            try:
                fact = get_memory_store().update(
                    session, fact_id, content=body.content,
                    confidence=body.confidence, category=body.category)
                session.commit()
            except MemoryNotFound as exc:
                raise HTTPException(404, "not found") from exc
            except MemoryValidationError as exc:
                raise HTTPException(400, str(exc)) from exc
            except MemoryConflictError as exc:
                raise HTTPException(409, str(exc)) from exc
            return _out(fact)

    @router.post("/facts/{fact_id}/approve")
    def approve_fact(fact_id: int):
        with database.SessionLocal() as session:
            try:
                fact = get_memory_store().set_status(session, fact_id, "approved")
                session.commit()
            except MemoryNotFound as exc:
                raise HTTPException(404, "not found") from exc
            except MemoryConflictError as exc:
                raise HTTPException(409, str(exc)) from exc
            return _out(fact)

    @router.delete("/facts/{fact_id}", status_code=204)
    def delete_fact(fact_id: int):
        with database.SessionLocal() as session:
            try:
                get_memory_store().archive(session, fact_id)
                session.commit()
            except MemoryNotFound as exc:
                raise HTTPException(404, "not found") from exc
        return None

    @router.get("/status")
    def memory_status():
        config = get_memory_config()
        counts: dict[str, dict[str, int]] = {}
        with database.SessionLocal() as session:
            for row in session.query(MemoryEntry).all():
                counts.setdefault(row.scope_type, {})
                counts[row.scope_type][row.status] = counts[row.scope_type].get(row.status, 0) + 1
        return {"enabled": config.enabled,
                "config": {"confidence_floor": config.confidence_floor,
                           "max_facts_per_scope": config.max_facts_per_scope,
                           "max_correction_facts": config.max_correction_facts,
                           "injection_token_budget": config.injection_token_budget,
                           "correction_token_budget": config.correction_token_budget},
                "counts": counts}

    return router

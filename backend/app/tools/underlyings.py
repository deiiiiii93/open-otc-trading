"""@tool wrapper: create-or-tag an instrument as a valid underlying."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import database
from app.models import Instrument
from app.services.audit import record_audit
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.underlyings import ensure_underlying, normalize_underlying_symbol


class RegisterUnderlyingInput(BaseModel):
    symbol: str = Field(min_length=1)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("register_underlying", args_schema=RegisterUnderlyingInput)
def register_underlying_tool(symbol: str) -> dict[str, Any]:
    """Create-or-tag an instrument as a valid underlying (adds the
    "underlying" tag; creates the instrument via the existing symbol-
    inference path if it doesn't exist yet, activating it). Call this when
    book_position/book_hedge return error=underlying_not_registered, then
    retry the booking call. HITL — requires confirmation except in yolo mode.
    """
    cleaned = normalize_underlying_symbol(symbol)
    database.init_db()
    with database.SessionLocal() as session:
        existing = session.query(Instrument).filter(Instrument.symbol == cleaned).one_or_none()
        if existing is None:
            instrument = ensure_underlying(session, cleaned, source="agent", status="active", activate=True)
            action = "created_new"
        else:
            instrument = existing
            action = "already_registered"
            if instrument.status != "active":
                instrument.status = "active"
                action = "tagged_existing"
        tags = list(instrument.tags or [])
        if "underlying" not in tags:
            tags.append("underlying")
            instrument.tags = tags
            if action == "already_registered":
                action = "tagged_existing"
        session.flush()
        # This tool is risk_level="irreversible" and can auto-run headlessly
        # under yolo mode with no human in the loop — a durable, searchable
        # audit trail is the only record of who/what changed the registry.
        record_audit(
            session,
            event_type="instrument.underlying_registered",
            actor="agent",
            subject_type="instrument",
            subject_id=instrument.id,
            payload={"symbol": instrument.symbol, "action": action, "tags": instrument.tags},
        )
        session.commit()
        return {
            "ok": True,
            "data": {
                "symbol": instrument.symbol,
                "instrument_id": instrument.id,
                "action": action,
                "kind": instrument.kind,
                "currency": instrument.currency,
                "status": instrument.status,
                "tags": instrument.tags,
            },
        }


__all__ = ["register_underlying_tool"]

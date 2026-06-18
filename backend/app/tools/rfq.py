"""@tool wrappers for the RFQ domain.

Each wrapper is a thin LLM adapter: parse args, call services/domains/rfq,
shape JSON. The wire shapes preserve the legacy langchain_tools.py payloads
(via ``_shaping.shape_rfq`` and ``shape_booked_position``) so existing agent
tests continue to exercise this layer unchanged.

Session ownership: every tool that mutates state opens its own
``database.SessionLocal`` and commits before returning. This matches how the
legacy ``langchain_tools.py`` definitions framed transactions — one tool
invocation, one commit — and keeps the LLM-facing contract synchronous.
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel

from app import database
from app.schemas import (
    RFQApprovalDecision,
    RFQBookRequest,
    RFQClientAcceptRequest,
    RFQDraftCreate,
    RFQDraftUpdate,
    RFQQuoteRequest,
    RFQReleaseRequest,
)
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import rfq as rfq_svc

from ._shaping import shape_booked_position, shape_rfq
from ._product_inputs import ToolProductSpec, ToolRFQDraft


# ----- args schemas -----------------------------------------------------------


class ApproveRfqInput(BaseModel):
    rfq_id: int
    approver: str = "agent_confirmed"
    comment: str | None = None


class RejectRfqInput(BaseModel):
    rfq_id: int
    approver: str = "agent_confirmed"
    comment: str | None = None


class ValidateRfqTermsInput(BaseModel):
    terms: ToolRFQDraft
    quote_mode: Literal["solve", "price"] = "solve"


class CreateOrUpdateRfqDraftInput(BaseModel):
    rfq_id: int | None = None
    draft: ToolRFQDraft
    channel: str = "agent"
    actor: str = "agent_confirmed"


class QuoteRfqInput(BaseModel):
    rfq_id: int
    quote_mode: Literal["solve", "price"] | None = None
    created_by: str = "desk_user"
    valid_until: Any | None = None
    market: Any | None = None
    engine_spec: Any | None = None
    product: ToolProductSpec | None = None
    unknown: Any | None = None
    target: Any | None = None


class SubmitRfqInput(BaseModel):
    rfq_id: int
    actor: str = "agent_confirmed"


class ReleaseRfqInput(RFQReleaseRequest):
    rfq_id: int


class MarkRfqClientAcceptedInput(RFQClientAcceptRequest):
    rfq_id: int


class BookRfqInput(RFQBookRequest):
    rfq_id: int


# ----- read-only / pure tools -------------------------------------------------


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("solve_rfq", args_schema=ToolRFQDraft)
def solve_rfq_tool(**kwargs: Any) -> dict[str, Any]:
    """Solve an RFQ unknown term through QuantArk's RFQ registry."""
    result = rfq_svc.solve_rfq(ToolRFQDraft.model_validate(kwargs).to_rfq_request_draft())
    return {"ok": result.ok, "quote": result.data, "error": result.error}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_rfq_catalog")
def get_rfq_catalog_tool() -> dict[str, Any]:
    """Return registered RFQ products, engines, unknown fields, and desk templates."""
    return rfq_svc.get_rfq_catalog()


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("validate_rfq_terms", args_schema=ValidateRfqTermsInput)
def validate_rfq_terms_tool(
    terms: ToolRFQDraft, quote_mode: Literal["solve", "price"] = "solve"
) -> dict[str, Any]:
    """Validate RFQ terms before pricing or submission."""
    return rfq_svc.validate_rfq_terms(terms.to_rfq_request_draft(), quote_mode)


# ----- write / lifecycle tools ------------------------------------------------


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("create_or_update_rfq_draft", args_schema=CreateOrUpdateRfqDraftInput)
def create_or_update_rfq_draft_tool(
    draft: ToolRFQDraft,
    rfq_id: int | None = None,
    channel: str = "agent",
    actor: str = "agent_confirmed",
) -> dict[str, Any]:
    """Create or update a persisted RFQ draft after HITL confirmation."""
    legacy_draft = draft.to_rfq_request_draft()
    database.init_db()
    with database.SessionLocal() as session:
        if rfq_id is None:
            rfq = rfq_svc.create_rfq_draft(
                session,
                RFQDraftCreate(**legacy_draft.model_dump(), channel=channel),
                channel=channel,
                actor=actor,
            )
        else:
            rfq = rfq_svc.update_rfq_draft(
                session,
                rfq_id,
                RFQDraftUpdate(**legacy_draft.model_dump()),
                actor=actor,
            )
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("quote_rfq", args_schema=QuoteRfqInput)
def quote_rfq_tool(**kwargs: Any) -> dict[str, Any]:
    """Create an immutable quote version for an RFQ through QuantArk."""
    request = QuoteRfqInput.model_validate(kwargs)
    request_payload = request.model_dump(exclude={"rfq_id", "product"})
    if request.product is not None:
        request_payload["product_kwargs"] = dict(request.product.terms or {})
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.quote_rfq(
            session,
            request.rfq_id,
            RFQQuoteRequest.model_validate(request_payload),
        )
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("submit_rfq_for_approval", args_schema=SubmitRfqInput)
def submit_rfq_for_approval_tool(
    rfq_id: int, actor: str = "agent_confirmed"
) -> dict[str, Any]:
    """Submit a drafted or quoted RFQ into the approval workflow."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.submit_rfq_for_approval(session, rfq_id, actor=actor)
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("approve_rfq", args_schema=ApproveRfqInput)
def approve_rfq_tool(
    rfq_id: int,
    approver: str = "agent_confirmed",
    comment: str | None = None,
) -> dict[str, Any]:
    """Approve a pending RFQ, set its status to approved, and audit the decision."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.approve_rfq(
            session,
            rfq_id,
            RFQApprovalDecision(
                approver=approver,
                comment=comment or "approved from floating agent",
            ),
        )
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("reject_rfq", args_schema=RejectRfqInput)
def reject_rfq_tool(
    rfq_id: int,
    approver: str = "agent_confirmed",
    comment: str | None = None,
) -> dict[str, Any]:
    """Reject a pending RFQ, set its status to rejected, and audit the decision."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.reject_rfq(
            session,
            rfq_id,
            RFQApprovalDecision(
                approver=approver,
                comment=comment or "rejected from floating agent",
            ),
        )
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("release_rfq", args_schema=ReleaseRfqInput)
def release_rfq_tool(
    rfq_id: int,
    actor: str = "trader",
    response_override: str | None = None,
) -> dict[str, Any]:
    """Release an approved RFQ to the client after HITL confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.release_rfq(
            session,
            rfq_id,
            RFQReleaseRequest(actor=actor, response_override=response_override),
        )
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("mark_rfq_client_accepted", args_schema=MarkRfqClientAcceptedInput)
def mark_rfq_client_accepted_tool(
    rfq_id: int, actor: str = "client", comment: str | None = None
) -> dict[str, Any]:
    """Mark a released RFQ as accepted by the client after confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.mark_client_accepted(
            session,
            rfq_id,
            RFQClientAcceptRequest(actor=actor, comment=comment),
        )
        result = shape_rfq(rfq)
        session.commit()
        return result


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_rfq_to_position", args_schema=BookRfqInput)
def book_rfq_to_position_tool(**kwargs: Any) -> dict[str, Any]:
    """Book an accepted RFQ into a selected portfolio as a traceable position."""
    request = BookRfqInput.model_validate(kwargs)
    database.init_db()
    with database.SessionLocal() as session:
        position = rfq_svc.book_rfq_to_position(
            session,
            request.rfq_id,
            RFQBookRequest.model_validate(request.model_dump(exclude={"rfq_id"})),
        )
        result = shape_booked_position(position)
        session.commit()
        return result


__all__ = [
    "solve_rfq_tool",
    "get_rfq_catalog_tool",
    "validate_rfq_terms_tool",
    "create_or_update_rfq_draft_tool",
    "quote_rfq_tool",
    "submit_rfq_for_approval_tool",
    "approve_rfq_tool",
    "reject_rfq_tool",
    "release_rfq_tool",
    "mark_rfq_client_accepted_tool",
    "book_rfq_to_position_tool",
]

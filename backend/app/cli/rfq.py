"""RFQ CLI commands (Typer).

Five most common operations: ``catalog``, ``draft``, ``quote``, ``approve``,
``reject``. The less-common lifecycle actions (``release``,
``mark-client-accepted``, ``book-to-position``) are accessible via the
matching @tool wrappers; deferred from the CLI per the Phase 1 plan since
they're trader-driven and rarely scripted.

Each command opens its own ``database.SessionLocal``, calls the
``services.domains.rfq`` facade, shapes the result via ``tools._shaping``,
and emits JSON (default) or human-friendly text via ``cli._format.emit``.
"""
from __future__ import annotations

import json
from typing import Any

import typer

from app import database
from app.schemas import RFQApprovalDecision, RFQQuoteRequest, RFQRequestDraft
from app.services.domains import rfq as rfq_svc
from app.tools._shaping import shape_rfq

from ._format import emit

app = typer.Typer(no_args_is_help=True)


def _load_draft(draft_json: str) -> RFQRequestDraft:
    """Parse ``--draft '{...}'`` JSON into a validated RFQRequestDraft."""
    try:
        payload = json.loads(draft_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--draft is not valid JSON: {exc}") from exc
    try:
        return RFQRequestDraft.model_validate(payload)
    except Exception as exc:
        raise typer.BadParameter(f"--draft failed validation: {exc}") from exc


@app.command("catalog")
def catalog_cmd(
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show registered RFQ products, engines, unknown fields, and templates."""
    payload = rfq_svc.get_rfq_catalog()
    emit(payload, as_json=json_output)


@app.command("draft")
def draft_cmd(
    draft: str = typer.Option(..., "--draft", help="RFQRequestDraft JSON"),
    rfq_id: int = typer.Option(None, "--rfq-id", help="Existing RFQ id to update"),
    channel: str = typer.Option("desk", "--channel"),
    actor: str = typer.Option("desk_user", "--actor"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Create or update a persisted RFQ draft from JSON terms."""
    request = _load_draft(draft)
    database.init_db()
    with database.SessionLocal() as session:
        if rfq_id is None:
            from app.schemas import RFQDraftCreate

            rfq = rfq_svc.create_rfq_draft(
                session,
                RFQDraftCreate(**request.model_dump(), channel=channel),
                channel=channel,
                actor=actor,
            )
        else:
            from app.schemas import RFQDraftUpdate

            rfq = rfq_svc.update_rfq_draft(
                session,
                rfq_id,
                RFQDraftUpdate(**request.model_dump()),
                actor=actor,
            )
        payload: dict[str, Any] = shape_rfq(rfq)
        session.commit()
    emit(payload, as_json=json_output)


@app.command("quote")
def quote_cmd(
    rfq_id: int = typer.Option(..., "--rfq-id"),
    quote_mode: str = typer.Option("solve", "--quote-mode"),
    created_by: str = typer.Option("desk_user", "--created-by"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Create an immutable quote version for an existing RFQ."""
    if quote_mode not in ("solve", "price"):
        raise typer.BadParameter("--quote-mode must be 'solve' or 'price'")
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.quote_rfq(
            session,
            rfq_id,
            RFQQuoteRequest(quote_mode=quote_mode, created_by=created_by),  # type: ignore[arg-type]
        )
        payload = shape_rfq(rfq)
        session.commit()
    emit(payload, as_json=json_output)


@app.command("approve")
def approve_cmd(
    rfq_id: int = typer.Option(..., "--rfq-id"),
    approver: str = typer.Option("desk_user", "--approver"),
    comment: str = typer.Option(None, "--comment"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Approve a pending RFQ."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.approve_rfq(
            session,
            rfq_id,
            RFQApprovalDecision(
                approver=approver,
                comment=comment or "approved from CLI",
            ),
        )
        payload = shape_rfq(rfq)
        session.commit()
    emit(payload, as_json=json_output)


@app.command("reject")
def reject_cmd(
    rfq_id: int = typer.Option(..., "--rfq-id"),
    approver: str = typer.Option("desk_user", "--approver"),
    comment: str = typer.Option(None, "--comment"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Reject a pending RFQ."""
    database.init_db()
    with database.SessionLocal() as session:
        rfq = rfq_svc.reject_rfq(
            session,
            rfq_id,
            RFQApprovalDecision(
                approver=approver,
                comment=comment or "rejected from CLI",
            ),
        )
        payload = shape_rfq(rfq)
        session.commit()
    emit(payload, as_json=json_output)

"""Fail-closed approval-card builder for the IM message gateway (Task 10).

Payload shape
-------------
``build_approval_card`` receives an ``AgentActionProposal`` (from
``app.schemas``) whose fields map directly from a LangGraph interrupt:

  pending_action.tool_name  — the tool name string (e.g. "book_position")
  pending_action.payload    — the raw args dict passed to the tool

REQUIRED_FIELDS uses the *actual* tool parameter names (not logical aliases),
sourced from the concrete tool input schemas:

  book_position:           product, quantity, portfolio_id
  book_hedge:              portfolio_id, underlying, risk/artifact timestamps,
                           strategy, spot, legs
  quote_rfq:               rfq_id, quote_mode, created_by
  submit_rfq_for_approval: rfq_id, actor
  approve_rfq:             rfq_id, approver
  reject_rfq:              rfq_id, approver
  release_rfq:             rfq_id, actor
  __cost_preview__:        estimated_cost, scope

Fail-closed contract
--------------------
- Unknown tool name  → non-approvable card (actions=[])
- Any required field missing from payload → non-approvable card (actions=[])
- Approvable → Approve + Reject CardActions; tokens minted via mint_card_action
- Oversized field values are TRUNCATED in display but approval is kept enabled
- IRREVERSIBLE tools show a warning line in the card
- A web deep-link is always included in non-approvable cards when
  gateway_web_base_url is configured.
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from app.services.gateway.actions import mint_card_action
from app.services.gateway.config import GatewayConfig
from app.services.gateway.types import CardAction, CardSection, MessageRef, OutboundCard

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import GatewayBinding
    from app.schemas import AgentActionProposal


# ---------------------------------------------------------------------------
# Authoritative per-tool required-field map
# (keys = actual tool parameter names from the tool input schemas)
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict[str, list[str]] = {
    "book_position": ["product", "quantity", "portfolio_id"],
    "book_hedge": [
        "portfolio_id", "underlying", "risk_run_id", "source_artifact_id",
        "artifact_generated_at", "valuation_as_of", "risk_generated_at",
        "expires_at", "strategy", "spot", "legs",
    ],
    "quote_rfq": ["rfq_id", "quote_mode", "created_by"],
    "submit_rfq_for_approval": ["rfq_id", "actor"],
    "approve_rfq": ["rfq_id", "approver"],
    "reject_rfq": ["rfq_id", "approver"],
    "release_rfq": ["rfq_id", "actor"],
    "__cost_preview__": ["estimated_cost", "scope"],
}

IRREVERSIBLE: set[str] = {
    "book_position",
    "book_hedge",
    "approve_rfq",
    "reject_rfq",
    "release_rfq",
}

# Maximum characters for a single displayed field value before truncation.
_MAX_FIELD_CHARS = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_missing(payload: dict[str, Any], field: str) -> bool:
    """Return whether *field* is missing for fail-closed purposes.

    A required field counts as missing when it is absent, ``None``, or an
    empty/whitespace-only string. ``0``, ``False``, and empty containers are
    NOT treated as missing (a legitimate zero value must not silently void
    approval), with one exception: an empty dict has no decision-relevant
    content, so it is treated as missing too.
    """
    if field not in payload:
        return True
    value = payload[field]
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, dict) and len(value) == 0:
        return True
    return False


def _truncate_value(raw: Any) -> str:
    """Render a field value, truncating if it exceeds _MAX_FIELD_CHARS."""
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, ensure_ascii=False)
    else:
        text = str(raw)
    if len(text) > _MAX_FIELD_CHARS:
        return text[:_MAX_FIELD_CHARS] + "…"
    return text


def _build_field_rows(payload: dict[str, Any], fields: list[str]) -> str:
    """Build a multi-line string of field: value pairs for the card body."""
    lines: list[str] = []
    for field in fields:
        val = payload.get(field)
        lines.append(f"{field}: {_truncate_value(val)}")
    return "\n".join(lines)


def _non_approvable_card(
    *,
    title: str,
    reason: str,
    web_link: str | None,
) -> OutboundCard:
    """Return a non-approvable card (no actions) with a clear reason."""
    body_parts = [reason]
    if web_link:
        body_parts.append(f"View on web desk: {web_link}")
    return OutboundCard(
        title=title,
        body="\n".join(body_parts),
        sections=[],
        actions=[],
        resolved=False,
        footer=None,
    )


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_approval_card(
    session: "Session",
    *,
    binding: "GatewayBinding",
    thread_id: int,
    message_id: int,
    pending_action: "AgentActionProposal",
    out_ref: MessageRef,
    settings,
) -> OutboundCard:
    """Build an approval card for *pending_action*.

    Fail-closed:
    - Unknown tool name → non-approvable (actions=[]) with web deep-link.
    - Missing required field → non-approvable (actions=[]) with web deep-link.
    - All required fields present → Approve + Reject actions with minted tokens.
    - Oversized fields are truncated in display; approval stays enabled.
    - IRREVERSIBLE tools include a warning section.
    """
    tool_name = pending_action.tool_name
    payload = dict(pending_action.payload or {})

    config = GatewayConfig.from_settings(settings)
    web_link = config.web_thread_link(str(thread_id))

    card_title = f"Action required: {tool_name}"

    # ------------------------------------------------------------------
    # Guard 1: unknown tool
    # ------------------------------------------------------------------
    if tool_name not in REQUIRED_FIELDS:
        return _non_approvable_card(
            title=card_title,
            reason=(
                f"Tool '{tool_name}' is not registered in the approval gateway. "
                "This action cannot be approved via chat."
            ),
            web_link=web_link,
        )

    required = REQUIRED_FIELDS[tool_name]

    # ------------------------------------------------------------------
    # Guard 2: missing required fields
    # ------------------------------------------------------------------
    missing = [f for f in required if _is_missing(payload, f)]
    if missing:
        return _non_approvable_card(
            title=card_title,
            reason=(
                f"Cannot build approval card for '{tool_name}': "
                f"required field(s) missing from payload: {', '.join(missing)}. "
                "Please approve via the web desk."
            ),
            web_link=web_link,
        )

    # ------------------------------------------------------------------
    # Build field rows (with truncation for oversized values)
    # ------------------------------------------------------------------
    field_rows = _build_field_rows(payload, required)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    sections: list[CardSection] = [
        CardSection(title="Parameters", body=field_rows),
    ]

    # Irreversible warning
    if tool_name in IRREVERSIBLE:
        sections.append(
            CardSection(
                title="Warning",
                body="This action is IRREVERSIBLE and cannot be undone. Please review carefully before approving.",
            )
        )

    # Web deep-link for full details (always appended when URL is configured)
    footer: str | None = None
    if web_link:
        footer = f"Full details: {web_link}"

    # ------------------------------------------------------------------
    # Mint tokens and build actions
    # ------------------------------------------------------------------
    action_id = pending_action.id

    approve_token = mint_card_action(
        session,
        binding=binding,
        thread_id=thread_id,
        message_id=message_id,
        action_id=action_id,
        decision="confirm",
        out_ref=out_ref,
        settings=settings,
    )
    reject_token = mint_card_action(
        session,
        binding=binding,
        thread_id=thread_id,
        message_id=message_id,
        action_id=action_id,
        decision="dismiss",
        out_ref=out_ref,
        settings=settings,
    )

    actions: list[CardAction] = [
        CardAction(label="Approve", style="primary", token=approve_token),
        CardAction(label="Reject", style="danger", token=reject_token),
    ]

    return OutboundCard(
        title=card_title,
        body=pending_action.summary or "",
        sections=sections,
        actions=actions,
        resolved=False,
        footer=footer,
    )

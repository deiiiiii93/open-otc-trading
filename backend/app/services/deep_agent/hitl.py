"""HITL projection helpers for the desk deep agent.

Verified against langchain.agents.middleware.human_in_the_loop:
- DecisionType = Literal["approve", "edit", "reject"]
- HITLRequest:  {"action_requests": list[ActionRequest], "review_configs": [...]}
- ActionRequest: {"name": str, "args": dict, "description": str?}
- HITLResponse: {"decisions": list[Decision]}  # positional
- Decision:    {"type": "approve"} | {"type": "reject", "message": str?} | {"type": "edit", ...}

v1 exposes only approve/reject at the API edge.

Subsequent tasks add `pending_actions_from_interrupts(...)` and
`build_resume_command(...)` to this same module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from langchain.agents.middleware import InterruptOnConfig


INTERRUPT_TOOL_NAMES: tuple[str, ...] = (
    "run_batch_pricing",
    "run_greeks_landscape",
    "create_report",
    "create_or_update_rfq_draft",
    "quote_rfq",
    "submit_rfq_for_approval",
    "approve_rfq",
    "reject_rfq",
    "release_rfq",
    "mark_rfq_client_accepted",
    "book_rfq_to_position",
    "book_position",
    "book_hedge",
    "register_underlying",
    "import_otc_positions",
    "close_position",
    "settle_position",
    "mark_knockout",
    "cancel_lifecycle_event",
    "delete_portfolio",
    "set_portfolio_rule",
    "set_hedge_bands",
    "remove_positions_from_portfolio",
    "create_portfolio",
    "update_portfolio",
    "add_positions_to_portfolio",
    "add_portfolio_sources",
    "remove_portfolio_sources",
    "create_pricing_parameter_profile",
    "generate_pricing_parameters_from_curves",
    "update_pricing_parameter_profile",
    "upsert_pricing_parameter_rows",
    "delete_pricing_parameter_rows",
    "delete_pricing_parameter_profile",
    "set_instrument_pricing_defaults",
    "build_assumption_set",
    "run_python",
)


_RISK_LEVEL_BY_TOOL: dict[str, str] = {
    "run_batch_pricing": "write",
    "run_greeks_landscape": "write",
    "create_report": "write",
    "create_or_update_rfq_draft": "write",
    "quote_rfq": "write",
    "submit_rfq_for_approval": "write",
    "approve_rfq": "irreversible",
    "reject_rfq": "irreversible",
    "release_rfq": "irreversible",
    "mark_rfq_client_accepted": "irreversible",
    "book_rfq_to_position": "irreversible",
    "book_position": "irreversible",
    "book_hedge": "irreversible",
    # "irreversible", NOT "write": "write"-risk tools bypass confirmation
    # under BOTH auto and yolo mode (interrupt_on_config's yolo_mode flag),
    # which would let auto mode silently persist an unvetted underlying —
    # contradicting the requirement that only yolo auto-adds.
    "register_underlying": "irreversible",
    "import_otc_positions": "write",
    "close_position": "write",
    "settle_position": "write",
    "mark_knockout": "write",
    "cancel_lifecycle_event": "irreversible",
    "delete_portfolio": "irreversible",
    "set_portfolio_rule": "write",
    "set_hedge_bands": "write",
    "remove_positions_from_portfolio": "irreversible",
    # Portfolio maintenance writes are reversible (delete exists) — "write"
    # level, so YOLO mode auto-approves them.
    "create_portfolio": "write",
    "update_portfolio": "write",
    "add_positions_to_portfolio": "write",
    "add_portfolio_sources": "write",
    "remove_portfolio_sources": "write",
    # Pricing parameter writes are reversible (delete/upsert exist) — "write"
    # level. Profile delete is the exception: rows are gone for good.
    "create_pricing_parameter_profile": "write",
    "generate_pricing_parameters_from_curves": "write",
    "update_pricing_parameter_profile": "write",
    "upsert_pricing_parameter_rows": "write",
    "delete_pricing_parameter_rows": "write",
    "delete_pricing_parameter_profile": "irreversible",
    "set_instrument_pricing_defaults": "write",
    "build_assumption_set": "write",
    # Argument-aware: pure analysis is read-like; writes_artifacts=True is
    # handled by RunPythonArtifactHITLMiddleware.
    "run_python": "read",
}


_LABEL_BY_TOOL: dict[str, str] = {
    "run_batch_pricing": "Run batch pricing (valuations + risk)",
    "run_greeks_landscape": "Run Greeks Landscape",
    "create_report": "Create report artifacts",
    "create_or_update_rfq_draft": "Save RFQ draft",
    "quote_rfq": "Quote RFQ",
    "submit_rfq_for_approval": "Submit RFQ",
    "approve_rfq": "Approve RFQ",
    "reject_rfq": "Reject RFQ",
    "release_rfq": "Release RFQ",
    "mark_rfq_client_accepted": "Mark RFQ accepted",
    "book_rfq_to_position": "Book RFQ",
    "book_position": "Book position",
    "register_underlying": "Register/tag underlying",
    "book_hedge": "Book hedge",
    "import_otc_positions": "Import OTC positions",
    "close_position": "Close position",
    "settle_position": "Settle position",
    "mark_knockout": "Mark position KO",
    "cancel_lifecycle_event": "Cancel lifecycle event",
    "delete_portfolio": "Delete portfolio",
    "set_portfolio_rule": "Replace portfolio filter rule",
    "set_hedge_bands": "Set hedge bands",
    "remove_positions_from_portfolio": "Remove positions from portfolio",
    "create_portfolio": "Create portfolio",
    "update_portfolio": "Update portfolio",
    "add_positions_to_portfolio": "Add positions to portfolio",
    "add_portfolio_sources": "Add view sources",
    "remove_portfolio_sources": "Remove view sources",
    "create_pricing_parameter_profile": "Create pricing profile",
    "generate_pricing_parameters_from_curves": "Generate pricing params from curves",
    "update_pricing_parameter_profile": "Update pricing profile",
    "upsert_pricing_parameter_rows": "Upsert pricing profile rows",
    "delete_pricing_parameter_rows": "Delete pricing profile rows",
    "delete_pricing_parameter_profile": "Delete pricing profile",
    "set_instrument_pricing_defaults": "Set instrument pricing defaults",
    "build_assumption_set": "Build assumption set",
    "run_python": "Run Python script",
}

_ACTION_CARD_PERSONAS = {"trader", "risk_manager", "high_board"}


def interrupt_on_config(
    *, yolo_mode: bool = False, headless: bool = False
) -> dict[str, bool | InterruptOnConfig]:
    """Return the interrupt_on mapping passed to create_deep_agent.

    Three execution modes map onto this gate:

    - interactive (defaults): every state-mutating tool is gated.
    - auto (``yolo_mode=True``): ordinary *write* confirmations are bypassed, but
      *irreversible* operations stay gated. Unknown tools remain gated by default.
    - yolo / ``headless=True``: ALL HITL is omitted — including irreversible
      operations — so a headless run (e.g. an arena match in its isolated,
      auto-cleaned DB) can complete bookings/approvals with no human in the loop.
      ``headless`` dominates ``yolo_mode``.
    """
    if headless:
        return {}
    names = tuple(name for name in INTERRUPT_TOOL_NAMES if name != "run_python")
    if yolo_mode:
        names = tuple(
            name
            for name in INTERRUPT_TOOL_NAMES
            if name != "run_python" and _RISK_LEVEL_BY_TOOL.get(name) != "write"
        )
    config: InterruptOnConfig = {"allowed_decisions": ["approve", "reject"]}
    return {name: config for name in names}


def run_python_requires_hitl(args: dict[str, Any] | None) -> bool:
    """Return whether a run_python call should pause for user approval."""
    return bool((args or {}).get("writes_artifacts") is True)


from langgraph.types import Command, Interrupt  # noqa: E402

from app.schemas import AgentActionProposal  # noqa: E402


def _compact_value(value: Any) -> str:
    """Render an arg value for a one-line summary without dumping raw JSON.

    Nested dicts/lists (e.g. a product spec carrying terms + synthesized
    schedules) collapse to a short placeholder so the card summary stays
    human-readable.
    """
    if isinstance(value, dict):
        for key in ("display_name", "product_family", "quantark_class", "name"):
            label = value.get(key)
            if isinstance(label, str) and label:
                return label
        return "…"
    if isinstance(value, (list, tuple)):
        return f"[{len(value)} items]"
    return str(value)


def _summarize_book_position(args: dict[str, Any]) -> str:
    product = args.get("product")
    product = product if isinstance(product, dict) else {}
    family = product.get("product_family") or product.get("quantark_class") or "product"
    underlying = product.get("underlying")
    qty = args.get("quantity")
    portfolio_id = args.get("portfolio_id")

    parts = [f"Book {qty}" if qty is not None else "Book", str(family)]
    if underlying:
        parts.append(f"on {underlying}")
    if portfolio_id is not None:
        parts.append(f"into portfolio {portfolio_id}")
    text = " ".join(parts)

    extras = []
    entry = args.get("entry_price")
    if entry not in (None, 0, 0.0):
        extras.append(f"entry {entry}")
    engine = args.get("engine_name")
    if engine:
        extras.append(f"engine {engine}")
    if extras:
        text += " (" + ", ".join(extras) + ")"
    return text


def _summarize_register_underlying(args: dict[str, Any]) -> str:
    """Preflight-aware: LangGraph's interrupt fires before the tool body
    runs, so without this the card could only show the raw symbol. Opens its
    own short-lived read-only session (self-contained in this module, no
    signature change needed on pending_actions_from_interrupts/_summary_for
    or their 5 call sites in agents.py)."""
    symbol = args.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return "Register underlying"
    symbol = symbol.strip()

    from app import database
    from app.models import Instrument
    from app.services.underlyings import akshare_asset_class, infer_currency, infer_market

    try:
        database.init_db()
        with database.SessionLocal() as session:
            row = session.query(Instrument).filter(Instrument.symbol == symbol).one_or_none()
            if row is None:
                return (
                    f"Register NEW underlying {symbol} — inferred kind="
                    f"{akshare_asset_class(symbol)}, currency={infer_currency(symbol)}, "
                    f"market={infer_market(symbol) or 'n/a'}"
                )
            if "underlying" not in (row.tags or []):
                return (
                    f"Add 'underlying' tag to existing instrument {symbol} "
                    f"(kind={row.kind}, status={row.status})"
                )
            return f"Register underlying {symbol} (already valid)"
    except Exception:
        # Card rendering must never 500 the turn over a preview lookup.
        return f"Register underlying {symbol}"


_SUMMARY_BUILDERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "book_position": _summarize_book_position,
    "register_underlying": _summarize_register_underlying,
}


def _summary_for(action_request: dict[str, Any]) -> str:
    description = action_request.get("description")
    if isinstance(description, str) and description:
        return description
    name = action_request["name"]
    args = action_request.get("args") or {}
    builder = _SUMMARY_BUILDERS.get(name)
    if builder is not None:
        return builder(args)
    if not args:
        return f"Run {name}"
    arg_summary = ", ".join(f"{k}={_compact_value(v)}" for k, v in list(args.items())[:4])
    return f"Run {name} ({arg_summary})"


def pending_actions_from_interrupts(
    interrupts: list[Interrupt],
    *,
    persona: str | None = None,
    source_meta: dict[str, Any] | None = None,
) -> list[AgentActionProposal]:
    """Project LangGraph interrupts into AgentActionProposal records.

    Composite id: f"{interrupt_id}:{i}" where i is the position in
    action_requests. The position is significant because the resume payload
    feeds decisions back as a positional list.
    """
    proposals: list[AgentActionProposal] = []
    for intr in interrupts:
        value = intr.value or {}
        action_requests = value.get("action_requests") or []
        for index, action_request in enumerate(action_requests):
            tool_name = str(action_request["name"])
            risk_level = _RISK_LEVEL_BY_TOOL.get(tool_name)
            label = _LABEL_BY_TOOL[tool_name] if tool_name in _LABEL_BY_TOOL else tool_name
            proposal_persona = persona if persona in _ACTION_CARD_PERSONAS else None
            action_source_meta = _source_meta_for_action(
                source_meta=source_meta,
                interrupt_id=intr.id,
                action_request=action_request,
                persona=persona,
                tool_name=tool_name,
            )
            proposals.append(
                AgentActionProposal(
                    id=f"{intr.id}:{index}",
                    tool_name=tool_name,
                    label=label,
                    summary=_summary_for(action_request),
                    payload=dict(action_request.get("args") or {}),
                    requires_confirmation=True,
                    status="pending",
                    persona=proposal_persona,  # type: ignore[arg-type]
                    risk_level=risk_level,  # type: ignore[arg-type]
                    source_meta=action_source_meta,
                )
            )
    return proposals


def _source_meta_for_action(
    *,
    source_meta: dict[str, Any] | None,
    interrupt_id: str,
    action_request: dict[str, Any],
    persona: str | None,
    tool_name: str,
) -> dict[str, Any]:
    """Always returns an audit block — audit_ref minting is unconditional
    (audit spec §5.4): async projections pass source_meta=None and previously
    got {} here, which broke proposal/decision/execution correlation."""
    from uuid import uuid4

    emitted_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    tool_call_id = (
        action_request.get("id")
        or action_request.get("tool_call_id")
        or interrupt_id
    )
    base = dict(source_meta or {})
    audit = dict(base.get("audit") or {})
    audit.setdefault("audit_ref", str(uuid4()))
    audit.update(
        {
            "tool_call_id": str(tool_call_id),
            "tool_name": tool_name,
            "persona": persona,
            "emitted_at": emitted_at,
            "interrupt_id": interrupt_id,
        }
    )
    base["audit"] = audit
    return base


def build_resume_command(decision: str, *, message: str | None = None) -> Command:
    """Build Command(resume=...) for a single-action HITL batch.

    v1 design constraint: at most one HITL action per assistant turn (see
    spec §5.3). The resume payload's `decisions` list therefore has one
    element. If a future change relaxes the batch-size-1 rule, this
    function gains an `index` and `total` argument.
    """
    if decision == "approve":
        return Command(resume={"decisions": [{"type": "approve"}]})
    if decision == "reject":
        body: dict[str, Any] = {"type": "reject"}
        if message:
            body["message"] = message
        return Command(resume={"decisions": [body]})
    raise ValueError(f"unknown HITL decision: {decision}")

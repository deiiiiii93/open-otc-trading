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
    "save_report_template",
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
    "record_lifecycle_event",
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
    "run_limit_monitoring",
    "acknowledge_limit_incident",
    "comment_limit_incident",
    "waive_limit_incident",
    "resolve_limit_incident",
    "book_extracted_trade",
    "generate_settlement_cashflows",
    "update_settlement_cashflow",
    "release_settlement_cashflow",
    "unrelease_settlement_cashflow",
    "block_settlement_cashflow",
    "unblock_settlement_cashflow",
    "void_settlement_cashflow",
    "resync_settlement_cashflow",
    "settle_settlement_cashflow",
    "generate_settlement_notice",
    "run_python",
)


_RISK_LEVEL_BY_TOOL: dict[str, str] = {
    "run_batch_pricing": "write",
    "run_greeks_landscape": "write",
    "create_report": "write",
    # "write" = interactive only; AUTO mode strips it from the interrupt map.
    # Correct here: a template edit is reversible and audited, and past reports
    # embed their own spec, so an unattended edit cannot rewrite history.
    # Contrast the booking tools, which are "irreversible" precisely because
    # AUTO must still stop them.
    "save_report_template": "write",
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
    # "write" matches its three hardcoded siblings: a lifecycle event is
    # recallable via cancel_lifecycle_event, unlike a booking. Note "write"
    # means AUTO/headless executes it unattended.
    "record_lifecycle_event": "write",
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
    "run_limit_monitoring": "write",
    "acknowledge_limit_incident": "write",
    "comment_limit_incident": "write",
    "waive_limit_incident": "write",
    "resolve_limit_incident": "write",
    # "irreversible", NOT "write": this books a real position through the very
    # same book_position gate as its siblings above, and positions have no
    # delete (close/settle are lifecycle events layered on top). As "write" it
    # was stripped from the interrupt map under auto mode, so AUTO booked a
    # trade straight off a parsed PDF with no human in the loop -- exactly the
    # failure the register_underlying comment below guards against, one order
    # of magnitude larger.
    "book_extracted_trade": "irreversible",
    # Settlement. "write" = interactive only; AUTO strips these from the
    # interrupt map. Deliberate desk decision: release is recallable
    # (unrelease exists, and block is reachable from released), and every
    # transition is audited.
    "generate_settlement_cashflows": "write",
    "update_settlement_cashflow": "write",
    "release_settlement_cashflow": "write",
    "unrelease_settlement_cashflow": "write",
    "block_settlement_cashflow": "write",
    "unblock_settlement_cashflow": "write",
    "void_settlement_cashflow": "write",
    "resync_settlement_cashflow": "write",
    "generate_settlement_notice": "write",
    # "irreversible", NOT "write": marking a cashflow settled asserts money
    # actually moved and cannot be recalled. AUTO mode must still stop here.
    "settle_settlement_cashflow": "irreversible",
    # Argument-aware: pure analysis is read-like; writes_artifacts=True is
    # handled by RunPythonArtifactHITLMiddleware.
    "run_python": "read",
}


_LABEL_BY_TOOL: dict[str, str] = {
    "run_batch_pricing": "Run batch pricing (valuations + risk)",
    "run_greeks_landscape": "Run Greeks Landscape",
    "create_report": "Create report artifacts",
    "save_report_template": "Save report template",
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
    "record_lifecycle_event": "Record lifecycle event",
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
    "run_limit_monitoring": "Run limit monitoring",
    "acknowledge_limit_incident": "Acknowledge limit incident",
    "comment_limit_incident": "Comment on limit incident",
    "waive_limit_incident": "Waive limit incident",
    "resolve_limit_incident": "Resolve limit incident",
    "book_extracted_trade": "Book extracted trade",
    "generate_settlement_cashflows": "Generate settlement cashflows",
    "update_settlement_cashflow": "Edit settlement cashflow",
    "release_settlement_cashflow": "Release cashflow for payment",
    "unrelease_settlement_cashflow": "Recall released cashflow",
    "block_settlement_cashflow": "Block settlement cashflow",
    "unblock_settlement_cashflow": "Unblock settlement cashflow",
    "void_settlement_cashflow": "Void settlement cashflow",
    "resync_settlement_cashflow": "Resync cashflow to its source event",
    "settle_settlement_cashflow": "Mark cashflow SETTLED",
    "generate_settlement_notice": "Generate settlement notice",
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


def _summarize_book_extracted_trade(args: dict[str, Any]) -> str:
    """Preflight-aware, same rationale as _summarize_register_underlying: the
    interrupt fires before the tool body runs, so the raw args are just
    ``{portfolio_id, trade_id}`` -- meaningless to a human asked to approve an
    irreversible booking. Reads the staged trade so the card states what is
    actually being booked."""
    trade_id = args.get("trade_id")
    if not isinstance(trade_id, int):
        return "Book extracted trade"

    from app import database
    from app.models import ExtractedTrade, Portfolio

    try:
        database.init_db()
        with database.SessionLocal() as session:
            trade = session.get(ExtractedTrade, trade_id)
            if trade is None:
                return f"Book extracted trade #{trade_id} (not found)"
            head = f"Book {_compact_value(trade.quantity)} {trade.family or 'trade'}"
            if trade.underlying:
                head += f" on {trade.underlying}"
            extras: list[str] = []
            if trade.entry_price is not None:
                extras.append(f"entry {trade.entry_price} {trade.currency or ''}".strip())
            if trade.counterparty:
                extras.append(f"cpty {trade.counterparty}")
            if trade.external_trade_id:
                extras.append(f"ref {trade.external_trade_id}")
            portfolio_id = args.get("portfolio_id")
            if isinstance(portfolio_id, int):
                portfolio = session.get(Portfolio, portfolio_id)
                extras.append(
                    f"into {portfolio.name} (id={portfolio_id})" if portfolio
                    else f"into portfolio id={portfolio_id}"
                )
            if trade.validation_status and trade.validation_status != "valid":
                extras.append(f"VALIDATION {trade.validation_status}")
            return head + (" — " + ", ".join(extras) if extras else "")
    except Exception:
        # Card rendering must never 500 the turn over a preview lookup.
        return f"Book extracted trade #{trade_id}"


def _summarize_settle_settlement_cashflow(args: dict[str, Any]) -> str:
    """Same rationale as _summarize_book_extracted_trade: the interrupt fires
    before the tool body runs, so the raw args are just ``{cashflow_id,
    expected_row_version}`` -- meaningless to a human asked to confirm that
    money actually moved. Reads the row so the card states the amount."""
    cashflow_id = args.get("cashflow_id")
    if not isinstance(cashflow_id, int):
        return "Mark settlement cashflow SETTLED"

    from app import database
    from app.models import Position, SettlementCashflow

    try:
        database.init_db()
        with database.SessionLocal() as session:
            row = session.get(SettlementCashflow, cashflow_id)
            if row is None:
                return f"Mark settlement cashflow #{cashflow_id} SETTLED (not found)"
            amount = "unknown amount" if row.amount is None else f"{row.amount:,.2f}"
            head = f"Mark SETTLED: {amount} {row.currency} ({row.direction})"
            extras: list[str] = []
            if row.counterparty:
                extras.append(f"cpty {row.counterparty}")
            if row.value_date:
                extras.append(f"value {row.value_date.isoformat()}")
            position = session.get(Position, row.position_id)
            if position is not None:
                extras.append(
                    f"position #{position.id} {position.product_type} "
                    f"on {position.underlying}"
                )
            extras.append(f"currently {row.status}")
            if row.stale:
                extras.append("STALE vs source event")
            return head + (" — " + ", ".join(extras) if extras else "")
    except Exception:
        # Card rendering must never 500 the turn over a preview lookup.
        return f"Mark settlement cashflow #{cashflow_id} SETTLED"


def _lifecycle_subject(args: dict[str, Any]) -> tuple[str, list[str]]:
    """Describe the position a lifecycle tool's args point at.

    Shared by all four lifecycle cards. Same rationale as
    _summarize_book_extracted_trade: the interrupt fires before the tool body,
    so every one of these tools can only offer ``position_id`` /
    ``source_trade_id`` — and `position_id=27` tells a human nothing about the
    trade they are being asked to close, settle or knock out.

    Returns ``(subject, extras)``. Never raises: a card that throws would break
    the gate it exists to serve, so an unreadable position degrades to its id.
    """
    position_id = args.get("position_id")
    if not isinstance(position_id, int):
        trade_id = args.get("source_trade_id")
        if trade_id:
            return f"trade {_compact_value(trade_id)}", []
        return "position", []

    from app import database
    from app.models import Position

    try:
        database.init_db()
        with database.SessionLocal() as session:
            position = session.get(Position, position_id)
            if position is None:
                return f"position #{position_id}", ["not found"]
            subject = _compact_value(position.quantity)
            if position.product_type:
                subject += f" {position.product_type}"
            if position.underlying:
                subject += f" / {position.underlying}"
            extras = [f"position #{position_id}"]
            if position.status:
                extras.append(f"now {position.status}")
            return subject, extras
    except Exception:  # noqa: BLE001 - a card must never break the gate
        return f"position #{position_id}", []


def _lifecycle_card(head: str, extras: list[str]) -> str:
    return head + (f" ({', '.join(extras)})" if extras else "")


def _money_extra(source: dict[str, Any]) -> str | None:
    """The first cash figure this event carries, if any."""
    for key in ("settlement_amount", "payoff", "coupon_amount"):
        if source.get(key) is not None:
            return f"{key.replace('_', ' ')} {_compact_value(source[key])}"
    return None


def _summarize_close_position(args: dict[str, Any]) -> str:
    subject, extras = _lifecycle_subject(args)
    if args.get("reason"):
        extras.append(f"reason: {_compact_value(args['reason'])}")
    if args.get("closed_at"):
        extras.append(f"as of {_compact_value(args['closed_at'])}")
    return _lifecycle_card(f"Close {subject}", extras)


def _summarize_settle_position(args: dict[str, Any]) -> str:
    subject, extras = _lifecycle_subject(args)
    money = _money_extra(args)
    if money:
        extras.append(money)
    if args.get("currency"):
        extras.append(str(args["currency"]))
    if args.get("settlement_date"):
        extras.append(f"value {_compact_value(args['settlement_date'])}")
    if args.get("reason"):
        extras.append(f"reason: {_compact_value(args['reason'])}")
    return _lifecycle_card(f"Settle {subject}", extras)


def _summarize_mark_knockout(args: dict[str, Any]) -> str:
    subject, extras = _lifecycle_subject(args)
    money = _money_extra(args)
    if money:
        extras.append(money)
    for key, label in (("ko_level", "KO level"), ("observed_spot", "observed")):
        if args.get(key) is not None:
            extras.append(f"{label} {_compact_value(args[key])}")
    if args.get("observation_date"):
        extras.append(f"on {_compact_value(args['observation_date'])}")
    return _lifecycle_card(f"Mark knocked out: {subject}", extras)


def _summarize_record_lifecycle_event(args: dict[str, Any]) -> str:
    """The general form: the event type is model-supplied, so it leads the card."""
    event_type = str(args.get("event_type") or "").strip() or "lifecycle event"
    data = args.get("event_data")
    data = data if isinstance(data, dict) else {}
    subject, extras = _lifecycle_subject(args)

    money = _money_extra(data)
    if money:
        extras.append(money)
    if data.get("early") is True:
        extras.append("EARLY exercise")
    if data.get("reason"):
        extras.append(f"reason: {_compact_value(data['reason'])}")
    return _lifecycle_card(f"Record {event_type} on {subject}", extras)


_SUMMARY_BUILDERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "book_position": _summarize_book_position,
    "register_underlying": _summarize_register_underlying,
    "book_extracted_trade": _summarize_book_extracted_trade,
    "settle_settlement_cashflow": _summarize_settle_settlement_cashflow,
    # All four lifecycle cards share _lifecycle_subject: without a builder they
    # each showed a bare `position_id=27`, which is a gate in name only.
    "record_lifecycle_event": _summarize_record_lifecycle_event,
    "close_position": _summarize_close_position,
    "settle_position": _summarize_settle_position,
    "mark_knockout": _summarize_mark_knockout,
}


def _summary_for(action_request: dict[str, Any]) -> str:
    name = action_request["name"]
    args = action_request.get("args") or {}
    # A registered builder is a deliberate, tool-specific override and MUST win
    # over the description. HumanInTheLoopMiddleware stamps every action request
    # with generic boilerplate ("Tool execution requires approval\n\nTool: ...\n
    # Args: {...}"), so checking `description` first made this whole table
    # unreachable in the live path — every real approval card showed raw ids
    # (verified against historical pending_actions for book_position). Only a
    # live smoke caught it: the unit tests called the builders directly, which
    # proves they work, not that anything calls them.
    builder = _SUMMARY_BUILDERS.get(name)
    if builder is not None:
        return builder(args)
    description = action_request.get("description")
    if isinstance(description, str) and description:
        return description
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

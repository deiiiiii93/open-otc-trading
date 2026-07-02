from __future__ import annotations

import ast
import asyncio
import base64
from copy import deepcopy
import inspect
import json as _json
import logging
import re
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphDrained
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import database as _database
from ..config import Settings, get_settings
from ..models import (
    AgentMessage,
    AgentSession,
    AgentTask,
    AgentThread,
    Portfolio,
    Position,
    TaskRun,
    Workflow,
)
from ..schemas import AgentAssetOut, AgentContextUsage, AgentPageContext
from .audit import record_audit
from .audit_trail import (
    AUDIT_CONTEXT_KEY,
    record_hitl_decision,
    record_hitl_proposals,
)
from .deep_agent.channel_registry import ChannelRegistry, get_registry
from .deep_agent.checkpointer import build_async_checkpointer, build_checkpointer
from .deep_agent.hitl import (
    build_resume_command,
    interrupt_on_config,
    pending_actions_from_interrupts,
)
from .deep_agent.model_factory import (
    build_agent_model,
    default_agent_model_selection,
    resolve_agent_model_selection,
)
from .deep_agent.capability_gate import (
    CapabilityDeniedError,
    CostPreviewRequiredError,
    RUNTIME_SIGNAL_SINK_KEY,
    tool_scope_gated,
)
from .deep_agent.envelopes import Envelope, ToolGroup
from .deep_agent.escalation import resolve_escalation
from .deep_agent.executor import TaskExecutionResult, TaskExecutor
from .deep_agent.ledger import LedgerWriter
from .deep_agent.orchestrator import build_orchestrator
from .deep_agent.goal_mode import GoalRunService, goal_grader_for_turn
from .deep_agent.run_control import new_run_control, request_drain
from .deep_agent.runtime_config import graph_run_config
from .deep_agent.scheduler import schedule_tasks_from_plan
from .deep_agent.session_lifecycle import acquire_session_lease, release_session_lease
from .deep_agent.task_registry import task_registration
from .deep_agent.stream_collector import StreamCollector, _truncate
from .deep_agent.workflow_state import ensure_thread_workflow_state
from .deep_agent.workspace_router import (
    WorkspaceRouteDecision,
    route_workspace_turn,
)
from ..tools import QUANT_AGENT_TOOLS
from .reply_options.tool import _normalize_reply_option
from .term_form.tool import _MAX_FIELDS as _TERM_FORM_MAX_FIELDS
from .term_form.tool import _normalize_term_field


def _sse(event: str, data: dict) -> str:
    """Serialize one SSE event with a JSON data payload."""
    return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _subagent_sse_line(event: dict, collector) -> str:
    """Record + serialize a dynamic-subagents fan-out lifecycle event to the web SSE."""
    collector.on_subagent(event)
    return _sse("subagent", event)


def _done_payload(message_id: int | None, thread_id: int | None) -> dict:
    """Build a ``done`` SSE payload, enriched with ``thread_id`` and the
    persisted message's ``pending_actions``.

    The web UI re-fetches the message and ignores these extra fields, but IM
    connectors (the gateway StreamRenderer) consume the SSE stream directly and
    need ``thread_id`` + ``pending_actions`` in the terminal event to render
    HITL approval cards.
    """
    payload: dict[str, Any] = {"message_id": message_id}
    if message_id is None or thread_id is None:
        return payload
    payload["thread_id"] = thread_id
    try:
        with _database.SessionLocal() as session:
            msg = session.get(AgentMessage, message_id)
            meta = (msg.meta or {}) if msg else {}
        pending = meta.get("pending_actions")
        if pending:
            payload["pending_actions"] = pending
        reply_options = meta.get("reply_options")
        if reply_options:
            payload["reply_options"] = reply_options
    except Exception:
        logger.debug("failed to enrich done payload", exc_info=True)
    return payload


def _extract_tool_error(data: dict, output: Any) -> str | None:
    """Detect tool errors from a LangGraph on_tool_end event payload."""
    if isinstance(data, dict):
        err = data.get("error")
        if err:
            return str(err)[:500]
    # ToolMessage with status="error" carries the error text in .content
    content = getattr(output, "content", None)
    status = getattr(output, "status", None)
    if status == "error" and content:
        return str(content)[:500]
    return None


# Must match _MIN_OPTIONS / _MAX_OPTIONS in services/reply_options/tool.py
# (kept local to avoid an import dependency for two ints).
_REPLY_OPTIONS_MAX = 5
_REPLY_OPTIONS_MIN = 2
_INTERNAL_ROUTING_FALLBACK = (
    "I couldn't complete that desk request because the assistant response was "
    "not suitable to show. "
    "Please restate the request in business terms, including the portfolio or "
    "position scope, and I'll continue."
)
_INTERNAL_RESPONSE_TERMS = (
    "agenttask",
    "assigned persona",
    "context pack",
    "context_pack",
    "context_pack_id",
    "scoped tools",
    "task-scoped",
    "task type:",
    "task_id",
    "workflow_id",
)
_INTERNAL_TASK_REF_RE = re.compile(r"\btask\s*#?\d+\b", re.IGNORECASE)
_INTERNAL_WORKFLOW_REF_RE = re.compile(r"\bworkflow\s*#?\d+\b", re.IGNORECASE)
_TODO_STATUSES = {"pending", "in_progress", "completed"}


def _contains_internal_routing_language(content: str) -> bool:
    lowered = content.lower()
    return (
        any(term in lowered for term in _INTERNAL_RESPONSE_TERMS)
        or bool(_INTERNAL_TASK_REF_RE.search(content))
        or bool(_INTERNAL_WORKFLOW_REF_RE.search(content))
    )


def _select_public_stream_response(
    *,
    stream_text: str | None,
    state_final_text: str | None = None,
    fallback_text: str = "(no response)",
) -> str:
    primary = (stream_text or "").strip()
    state_final = (state_final_text or "").strip()
    fallback = (fallback_text or "").strip()

    if primary:
        if not _contains_internal_routing_language(primary):
            return primary
        if state_final and not _contains_internal_routing_language(state_final):
            return state_final
        return _INTERNAL_ROUTING_FALLBACK

    if state_final:
        if not _contains_internal_routing_language(state_final):
            return state_final
        return _INTERNAL_ROUTING_FALLBACK

    if fallback and _contains_internal_routing_language(fallback):
        return _INTERNAL_ROUTING_FALLBACK
    return fallback or "(no response)"


def _capture_reply_options_from_tool_end(
    collector: StreamCollector,
    *,
    run_id: str,
    name: str,
    error_text: str | None,
) -> None:
    """If a ``propose_reply_options`` tool just ended cleanly, write its
    normalized args into ``collector.reply_options``. Last call wins.
    Validation errors leave any prior valid options in place.

    Reads ``collector.reply_options_args[run_id]``, which is populated at
    ``on_tool_start`` BEFORE ``_truncate`` is applied. Reading from the
    truncated ``tool_events[run_id]["args"]`` would silently drop options
    near the upper cap envelope (5 options near max length sizes exceed
    the 1000-byte truncation threshold).
    """
    if name != "propose_reply_options" or error_text:
        return
    raw_options = collector.reply_options_args.get(run_id)
    if not isinstance(raw_options, list):
        return
    normalized: list[dict] = []
    for opt in raw_options:
        norm = _normalize_reply_option(opt)
        if norm is not None:
            normalized.append(norm)
        if len(normalized) >= _REPLY_OPTIONS_MAX:
            break
    if len(normalized) < _REPLY_OPTIONS_MIN:
        return
    collector.reply_options = normalized


def _capture_term_form_from_tool_end(
    collector: StreamCollector,
    *,
    run_id: str,
    name: str,
    error_text: str | None,
) -> None:
    """If a ``propose_term_form`` tool just ended cleanly, write its normalized
    payload into ``collector.term_form``. Last call wins; validation errors
    leave any prior payload in place. Mirrors the reply-options capture: reads
    ``collector.term_form_args[run_id]`` (populated at on_tool_start before
    truncation)."""
    if name != "propose_term_form" or error_text:
        return
    raw = collector.term_form_args.get(run_id)
    if not isinstance(raw, dict):
        return
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list):
        return
    fields: list[dict] = []
    for field in raw_fields:
        norm = _normalize_term_field(field)
        if norm is not None:
            fields.append(norm)
        if len(fields) >= _TERM_FORM_MAX_FIELDS:
            break
    if not fields:
        return
    title = raw.get("title")
    payload: dict = {
        "title": title.strip()[:120] if isinstance(title, str) and title.strip() else "Complete booking",
        "fields": fields,
    }
    subtitle = raw.get("subtitle")
    if isinstance(subtitle, str) and subtitle.strip():
        payload["subtitle"] = subtitle.strip()[:200]
    submit_label = raw.get("submit_label")
    payload["submit_label"] = (
        submit_label.strip()[:40]
        if isinstance(submit_label, str) and submit_label.strip()
        else "Review & book"
    )
    collector.term_form = payload


def _collector_completion_phase(collector: StreamCollector) -> str:
    if collector.drained:
        return "drained"
    if _collector_transport_recovery_text(collector):
        return "completed_with_transport_error"
    if collector.error:
        return "error"
    if collector.has_tool_errors:
        return "completed_with_tool_errors"
    return "completed"


def _collector_tool_error_text(collector: StreamCollector) -> str | None:
    for event in collector.process_events:
        if event.get("status") != "error":
            continue
        name = str(event.get("name") or "tool")
        error = str(event.get("error") or "").strip()
        return f"{name} failed: {error}" if error else f"{name} failed"
    return None


_TRANSPORT_DISCONNECT_MARKERS = (
    "incomplete chunked read",
    "peer closed connection without sending complete message body",
    "remoteprotocolerror",
)


def _is_transport_disconnect_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(marker in lowered for marker in _TRANSPORT_DISCONNECT_MARKERS)


def _completed_tool_names(collector: StreamCollector) -> list[str]:
    names: list[str] = []
    for event in collector.process_events:
        if event.get("status") != "done":
            continue
        name = str(event.get("name") or "").strip()
        if not name or name in {"write_todos", "propose_reply_options", "propose_term_form"}:
            continue
        names.append(name)
    return names


def _collector_transport_recovery_text(collector: StreamCollector) -> str | None:
    if not _is_transport_disconnect_error(collector.error):
        return None
    names = _completed_tool_names(collector)
    if not names:
        return None
    latest = ", ".join(names[-3:])
    return (
        "The model stream disconnected before final synthesis, but completed "
        f"tool results were captured. Latest completed tools: {latest}. "
        "Use the preserved process events on this message for the tool outputs."
    )


def _normalize_todos(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    todos: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        status = item.get("status")
        if not isinstance(content, str) or not isinstance(status, str):
            continue
        content = content.strip()
        status = status.strip()
        if not content or status not in _TODO_STATUSES:
            continue
        todos.append({"content": content, "status": status})
    return todos


def _todos_from_tool_args(args: Any) -> list[dict[str, str]] | None:
    if not isinstance(args, dict):
        return None
    return _normalize_todos(args.get("todos"))


logger = logging.getLogger("agent.deep")


DEEP_AGENT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "price_product",
        "solve_rfq",
        "get_rfq_catalog",
        "build_product",
        "validate_rfq_terms",
        "create_or_update_rfq_draft",
        "quote_rfq",
        "submit_rfq_for_approval",
        "get_positions",
        "query_snowball_ko_from_spot",
        "calculate_risk",
        "recommend_hedge",
        # Hedging strategy workflow (hedge-portfolio skill): guard + solve
        # reads, then the HITL-gated tagged booking writes. Without these the
        # skill is unexecutable — the interrupt card surfaces (it matches the
        # tool-CALL name) but execution fails with "not a valid tool".
        "get_hedgeable_underlyings",
        "propose_hedge",
        "get_hedge_bands",
        "book_hedge",
        "set_hedge_bands",
        "register_underlying",
        "run_report_batch",
        "write_report_artifact",
        "fetch_market_snapshot",
        "list_pricing_parameter_profiles",
        "get_pricing_parameter_profile",
        "list_assumption_sets",
        "get_assumption_set",
        "get_instrument_pricing_defaults",
        # Pricing parameter writes (persisted / HITL-gated): profile CRUD +
        # assumption pipeline. Bound so HITL cards always execute.
        "create_pricing_parameter_profile",
        "update_pricing_parameter_profile",
        "upsert_pricing_parameter_rows",
        "delete_pricing_parameter_rows",
        "delete_pricing_parameter_profile",
        "set_instrument_pricing_defaults",
        "build_assumption_set",
        "get_latest_position_valuations",
        "get_latest_risk_run",
        # Dynamic-subagents pilot: the deterministic coverage-reconciliation tool the
        # morning-risk-breach workflow's finalize step calls. Registered in
        # QUANT_AGENT_TOOLS but was missing here, so select_deep_agent_tools() dropped
        # it — the model could never call it and fell back to write_report_artifact.
        "assemble_breach_report",
        "import_otc_positions",
        "close_position",
        "settle_position",
        "mark_knockout",
        "cancel_lifecycle_event",
        "generate_asian_fixing_schedule",
        "capture_asian_fixings",
        "run_batch_pricing",
        # Scenario test tools: list/run/read/save/generate
        "list_scenario_library",
        "run_scenario_test",
        "get_scenario_test_run",
        "save_scenario_set",
        "generate_scenario_set",
        # Backtest tools: run + read
        "run_backtest",
        "get_backtest_run",
        "list_backtest_runs",
        # Greeks Landscape tools: run + read
        "run_greeks_landscape",
        "get_greeks_landscape_run",
        "get_latest_greeks_landscape_run",
        "create_report",
        "approve_rfq",
        "reject_rfq",
        "release_rfq",
        "mark_rfq_client_accepted",
        "book_rfq_to_position",
        "book_position",
        "list_portfolios",
        "get_portfolio",
        # Portfolio maintenance writes (all HITL-carded; delete/remove are
        # "irreversible" so even YOLO mode keeps their cards, the rest are
        # "write"). Bound so cards in INTERRUPT_TOOL_NAMES always execute.
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
        "create_portfolio",
        "update_portfolio",
        "add_positions_to_portfolio",
        "add_portfolio_sources",
        "remove_portfolio_sources",
        "run_python",
        # v2 (agent-skills-layer-v2): read-only report query tools, used by
        # the high_board report-query-and-display workflow.
        "list_reports",
        "get_report",
        # Async-subagent dispatch (not HITL-gated; the subagent's own writes
        # bubble up to the parent thread).
        "start_async_agent",
        "list_async_agents",
        "cancel_async_agent",
        # UI-control tool: personas can surface the same pickable-choice UX
        # as the orchestrator when their delegated reply asks the user to pick.
        "propose_reply_options",
        # Desk-workflow authoring: the build-workflow skill persists drafts.
        "save_desk_workflow",
    }
)


def select_deep_agent_tools(tools: Iterable[Any] = QUANT_AGENT_TOOLS) -> list[Any]:
    by_name = {tool.name: tool for tool in tools}
    missing = sorted(DEEP_AGENT_TOOL_NAMES - set(by_name))
    if missing:
        raise RuntimeError(f"Missing required DeepAgent tools: {', '.join(missing)}")
    return [tool_scope_gated(by_name[name]) for name in sorted(DEEP_AGENT_TOOL_NAMES)]


# Async agents must run with the same gated allowlist the orchestrator uses
# (HITL bubble-up requires interrupt-eligible tools only) and must not be
# able to recursively spawn more async agents.
_ASYNC_AGENT_EXCLUDE = frozenset(
    {"start_async_agent", "list_async_agents", "cancel_async_agent"}
)


def select_async_agent_tools(
    tools: Iterable[Any] = QUANT_AGENT_TOOLS,
) -> list[Any]:
    return [
        tool
        for tool in select_deep_agent_tools(tools)
        if tool.name not in _ASYNC_AGENT_EXCLUDE
    ]


_DISABLED_RESPONSE = (
    "Agent unavailable — no healthy LLM channel is configured. "
    "Check config/agent_channels.yaml and ensure the corresponding "
    "API key environment variable is set."
)
_PLACEHOLDER_THREAD_TITLES = {"New research thread", "Untitled thread"}
_THREAD_TITLE_MAX_CHARS = 80
_THREAD_TITLE_SOURCE_MAX_CHARS = 2000
_THREAD_TITLE_PROMPT = (
    "Write a concise title summarizing the user's intent in 5 to 8 words. "
    "Return only the title. Do not use quotes, bullets, labels, or punctuation."
)


def _fallback_thread_title(content: str) -> str:
    return " ".join(content.split())[:_THREAD_TITLE_MAX_CHARS].strip()


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return str(content)


def _clean_thread_title_summary(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first_line.lower().startswith("title:"):
        first_line = first_line.split(":", 1)[1].strip()
    cleaned = first_line.strip(" \t\r\n\"'`*_")
    cleaned = re.sub(r"[.!?。！？]+$", "", cleaned)
    words = cleaned.split()
    if len(words) > 8:
        cleaned = " ".join(words[:8])
    return cleaned[:_THREAD_TITLE_MAX_CHARS].strip()


def _orchestrator_user_prompt(
    content: str,
    character_hint: str,
    context: dict[str, Any],
    *,
    yolo_mode: bool = False,
) -> str:
    brief = render_context_brief(context)
    hint = (
        f"(User suggested persona: {character_hint.replace('_', ' ')}.)\n\n"
        if character_hint and character_hint != "auto"
        else ""
    )
    execution_mode = (
        "YOLO mode is ON. This app is using LangChain's built-in "
        "auto-approval policy for ordinary write tools, so those tools may "
        "run without pausing for confirmation. Irreversible tools still "
        "require explicit user confirmation."
        if yolo_mode
        else "YOLO mode is OFF. Follow the normal confirmation policy for "
        "write and irreversible tools."
    )
    return (
        f"{hint}"
        f"=== Conversation context ===\n"
        f"{brief}\n\n"
        f"=== Execution mode ===\n"
        f"{execution_mode}\n\n"
        f"=== User says ===\n"
        f"{content}"
    )


def render_context_brief(context: dict[str, Any]) -> str:
    """Render the four-key context dict as a natural-language briefing.

    Replaces the prior JSON-dump prompt format. Goals:
    - Frame the conversation as desk advisory, not web debugging
    - Lead with DB-truth (portfolio_summary) over UI snapshot
    - Expose entity_ids only as compact "internal references" for tools
    - Stay under ~150 tokens for the typical case
    """
    page = context.get("current_page_context") or {}
    portfolio = context.get("portfolio_summary") or {}
    accounting = context.get("accounting_context") or {}
    recent_messages = context.get("recent_thread_messages") or []

    title = (page.get("title") or "").strip() or "(no active page)"
    chips = page.get("chips") or []
    is_dialog = any(isinstance(chip, str) and chip.lower() == "dialog" for chip in chips)
    surface = "dialog" if is_dialog else "page"

    lines: list[str] = [f"You are advising a desk user currently viewing the **{title}** {surface}."]

    if portfolio:
        portfolio_line = _portfolio_brief_line(portfolio)
        if portfolio_line:
            lines.append("")
            lines.append(f"Portfolio in view: {portfolio_line}")

        price_line = _latest_price_brief_line(portfolio.get("latest_price_summary"))
        if price_line:
            lines.append(f"Latest pricing run: {price_line}")

        risk_line = _risk_brief_line(portfolio.get("risk_totals"))
        if risk_line:
            lines.append(f"Latest risk totals: {risk_line}")

        selected = portfolio.get("selected_position")
        if isinstance(selected, dict) and selected:
            lines.append(f"Selected position: {_selected_position_brief(selected)}")
    else:
        lines.append("")
        lines.append(
            "No portfolio is in view from this page. Ask the user which portfolio, "
            "position, or underlying they mean before invoking domain tools."
        )

    if recent_messages:
        lines.append("")
        lines.append("Recent thread context:")
        for msg in recent_messages:
            if not isinstance(msg, dict):
                continue
            label = str(msg.get("role") or "message")
            character = msg.get("character")
            if character:
                label = f"{label}/{character}"
            content = str(msg.get("content") or "").replace("\n", " ").strip()
            if len(content) > 500:
                content = content[:497] + "..."
            lines.append(f"- {label}: {content}")
            assets = msg.get("assets")
            if isinstance(assets, list) and assets:
                asset_names = [
                    str(asset.get("path") or asset.get("title"))
                    for asset in assets
                    if isinstance(asset, dict) and (asset.get("path") or asset.get("title"))
                ][:3]
                if asset_names:
                    lines.append(f"  assets: {', '.join(asset_names)}")

    profile_line = _pricing_profile_brief_line(page)
    if profile_line:
        lines.append("")
        lines.append(profile_line)
    elif portfolio:
        lines.append("")
        lines.append(
            "No pricing parameter profile is selected. Before proposing "
            "run_batch_pricing or create_report for portfolio/risk "
            "calculations, ask which pricing parameter profile to use unless "
            "the user explicitly says to run without one."
        )

    refs = _internal_references_line(page.get("entity_ids"))
    if refs:
        lines.append("")
        lines.append(f"Internal references (for tool calls): {refs}")

    date = accounting.get("accounting_date")
    if date:
        lines.append("")
        lines.append(
            f"Accounting anchor: {date}. Use this as the business-date anchor for "
            f"relative-date questions; it is NOT the pricing valuation_date."
        )

    return "\n".join(lines)


def _portfolio_brief_line(portfolio: dict[str, Any]) -> str:
    parts: list[str] = []
    name = portfolio.get("name")
    if name:
        parts.append(f'"{name}"')
    count = portfolio.get("position_count")
    if isinstance(count, int):
        parts.append(f"{count} position{'s' if count != 1 else ''}")
    base = portfolio.get("base_currency")
    if base:
        parts.append(f"base {base}")
    return " · ".join(parts)


def _latest_price_brief_line(summary: Any) -> str:
    if not isinstance(summary, dict) or not summary:
        return ""
    parts: list[str] = []
    nav = summary.get("nav") or summary.get("market_value")
    if nav is not None:
        parts.append(f"NAV {nav}")
    if summary.get("pnl") is not None:
        parts.append(f"PnL {summary['pnl']}")
    if summary.get("delta") is not None:
        parts.append(f"Δ {summary['delta']}")
    if summary.get("vega") is not None:
        parts.append(f"Vega {summary['vega']}")
    if summary.get("valuation_date"):
        parts.append(f"as of {summary['valuation_date']}")
    return ", ".join(parts) if parts else ""


def _risk_brief_line(totals: Any) -> str:
    if not isinstance(totals, dict) or not totals:
        return ""
    return ", ".join(f"{key} {value}" for key, value in totals.items() if value is not None)


def _selected_position_brief(selected: dict[str, Any]) -> str:
    parts: list[str] = []
    trade_id = selected.get("source_trade_id") or selected.get("id")
    if trade_id is not None:
        parts.append(str(trade_id))
    if selected.get("underlying"):
        parts.append(str(selected["underlying"]))
    if selected.get("product_type"):
        parts.append(str(selected["product_type"]))
    return " / ".join(parts)


def _internal_references_line(entity_ids: Any) -> str:
    if not isinstance(entity_ids, dict):
        return ""
    pairs = [
        f"{key}={value}"
        for key, value in entity_ids.items()
        if value is not None and value != ""
    ]
    return ", ".join(pairs)


def _pricing_profile_brief_line(page: dict[str, Any]) -> str:
    entity_ids = page.get("entity_ids")
    profile_id = None
    if isinstance(entity_ids, dict):
        profile_id = entity_ids.get("pricing_parameter_profile_id")
        if profile_id in (None, ""):
            profile_id = entity_ids.get("pricing_profile_id")

    profile = _pricing_profile_snapshot(page.get("snapshot"))
    if profile_id in (None, "") and isinstance(profile, dict):
        profile_id = profile.get("id")
    if profile_id in (None, ""):
        return ""

    parts = [f"Selected pricing parameter profile: id={profile_id}"]
    if isinstance(profile, dict):
        name = profile.get("name")
        valuation_date = profile.get("valuation_date")
        if name:
            parts.append(f'"{name}"')
        if valuation_date:
            parts.append(f"valuation_date={valuation_date}")
    return (
        " ".join(parts)
        + ". When proposing persisted pricing, repricing, risk, or "
        f"portfolio/risk report jobs, pass pricing_parameter_profile_id={profile_id}."
    )


def _pricing_profile_snapshot(snapshot: Any) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    for key in ("selected_pricing_profile", "pricing_profile", "selected_profile"):
        value = snapshot.get(key)
        if isinstance(value, dict):
            return value
    parent = snapshot.get("parent_context")
    if isinstance(parent, dict):
        return _pricing_profile_snapshot(parent.get("snapshot"))
    return None


def _extract_final_ai_text(result: Any) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            text = _message_content_to_text(getattr(message, "content", ""))
            if text:
                return text
    return ""


def _reply_options_from_result(result: Any) -> list[dict] | None:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    captured: list[dict] | None = None
    for message in messages:
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") != "propose_reply_options":
                continue
            args = tool_call.get("args") or {}
            raw_options = args.get("options")
            if not isinstance(raw_options, list):
                continue
            normalized: list[dict] = []
            for opt in raw_options:
                norm = _normalize_reply_option(opt)
                if norm is not None:
                    normalized.append(norm)
                if len(normalized) >= _REPLY_OPTIONS_MAX:
                    break
            if len(normalized) >= _REPLY_OPTIONS_MIN:
                captured = normalized
    return captured


def _term_form_from_result(result: Any) -> dict | None:
    """Extract a normalized term_form payload from a non-streaming agent result
    by scanning AIMessage tool_calls for the last propose_term_form call.
    Mirrors _reply_options_from_result."""
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return None
    payload: dict | None = None
    for message in messages:
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") != "propose_term_form":
                continue
            args = tool_call.get("args") or {}
            raw_fields = args.get("fields")
            if not isinstance(raw_fields, list):
                continue
            fields: list[dict] = []
            for field in raw_fields:
                norm = _normalize_term_field(field)
                if norm is not None:
                    fields.append(norm)
                if len(fields) >= _TERM_FORM_MAX_FIELDS:
                    break
            if not fields:
                continue
            title = args.get("title")
            payload = {
                "title": title.strip()[:120] if isinstance(title, str) and title.strip() else "Complete booking",
                "fields": fields,
                "submit_label": (args.get("submit_label") or "Review & book"),
            }
            if isinstance(args.get("subtitle"), str) and args["subtitle"].strip():
                payload["subtitle"] = args["subtitle"].strip()[:200]
    return payload


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p.strip() for p in parts if p.strip()).strip()
    return str(content).strip() if content is not None else ""


def _tool_result_payload(output: Any) -> dict[str, Any] | None:
    if isinstance(output, dict):
        return output
    content = getattr(output, "content", None)
    if content is not None and content is not output:
        return _tool_result_payload(content)
    if not isinstance(output, str):
        return None
    text = output.strip()
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except _json.JSONDecodeError:
        pass
    match = re.search(
        r"content=(?P<literal>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        content_text = ast.literal_eval(match.group("literal"))
    except (SyntaxError, ValueError):
        return None
    return _tool_result_payload(content_text)


def _task_id_from_result(result: Any) -> int | None:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        payload = _tool_result_payload(message)
        if not isinstance(payload, dict):
            continue
        raw_task_id = payload.get("task_id")
        if raw_task_id is None:
            continue
        try:
            return int(raw_task_id)
        except (TypeError, ValueError):
            continue
    return None


def _task_watch_from_result(
    session: Session,
    result: Any,
) -> dict[str, Any] | None:
    task_id = _task_id_from_result(result)
    if task_id is None:
        return None
    task = session.get(TaskRun, task_id)
    if task is None:
        return {"task_id": task_id}
    return {
        "task_id": task.id,
        "task_kind": task.kind,
        "task_status": task.status,
        "task_progress_current": task.progress_current,
        "task_progress_total": task.progress_total,
        "task_message": task.message,
    }


def _mark_pending_action_resolved(
    pending_actions: list[dict[str, Any]],
    *,
    action_id: str,
    status: str,
    task_watch: dict[str, Any] | None = None,
) -> None:
    for entry in pending_actions:
        if entry.get("id") != action_id:
            continue
        entry["status"] = status
        entry["resolved_at"] = datetime.utcnow().isoformat()
        if status == "confirmed" and task_watch is not None:
            entry.update(task_watch)


def _personas_invoked(result: Any) -> list[str]:
    """Scan messages for task(name=...) tool calls to record which personas ran."""
    invoked: list[str] = []
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in messages:
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") == "task":
                args = tool_call.get("args") or {}
                name = args.get("subagent_type") or args.get("name")
                if isinstance(name, str) and name not in invoked:
                    invoked.append(name)
    return invoked


def _agent_file_assets_from_state(
    files: Any,
    *,
    artifact_dir: Any,
    thread_id: int,
) -> list[AgentAssetOut]:
    if not isinstance(files, dict):
        return []
    assets: list[AgentAssetOut] = []
    for virtual_path, file_data in sorted(files.items()):
        if not isinstance(virtual_path, str) or not virtual_path.startswith(
            "/trading_desk/"
        ):
            continue
        relative_path = _safe_virtual_artifact_path(virtual_path)
        if relative_path is None:
            continue
        content_bytes = _file_data_bytes(file_data)
        if content_bytes is None:
            continue
        target = artifact_dir / "agent" / f"thread-{thread_id}" / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_bytes)
        asset_kind = _asset_kind_for_path(virtual_path)
        asset_id = "agent-file-" + virtual_path.strip("/").replace("/", "-")
        assets.append(
            AgentAssetOut(
                id=asset_id,
                kind=asset_kind,
                title=PurePosixPath(virtual_path).name or virtual_path,
                mime_type=_mime_type_for_asset(asset_kind, virtual_path),
                url=f"/api/artifacts/agent/thread-{thread_id}/{relative_path.as_posix()}",
                path=virtual_path,
                metadata={
                    "virtual_path": virtual_path,
                    "artifact_path": str(target),
                    "size": len(content_bytes),
                },
            )
        )
    return assets


def _file_data_bytes(file_data: Any) -> bytes | None:
    if isinstance(file_data, bytes):
        return file_data
    if isinstance(file_data, str):
        return file_data.encode("utf-8")
    if isinstance(file_data, dict):
        content = file_data.get("content")
        if isinstance(content, str):
            return content.encode("utf-8")
        if isinstance(content, bytes):
            return content
        if isinstance(content, list):
            return "\n".join(str(item) for item in content).encode("utf-8")
        content_b64 = file_data.get("content_b64")
        if isinstance(content_b64, str):
            try:
                return base64.b64decode(content_b64)
            except Exception:
                return None
    return None


def _tool_artifact_files_from_result(result: Any) -> dict[str, Any]:
    files: dict[str, Any] = {}
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in messages:
        files.update(_tool_artifact_files_from_output(message))
    return files


def _tool_artifact_files_from_output(output: Any) -> dict[str, Any]:
    payload = _tool_output_payload(output)
    if not isinstance(payload, dict):
        return {}
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return {}
    result = payload.get("result") if "result" in payload else payload
    files: dict[str, Any] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        virtual_path = _tool_artifact_virtual_path(artifact, result)
        if virtual_path is None:
            continue
        file_data = _tool_artifact_file_data(artifact)
        if file_data is None:
            continue
        files[virtual_path] = file_data
    return files


def _tool_output_payload(output: Any) -> dict[str, Any] | None:
    if isinstance(output, dict):
        return output
    content = getattr(output, "content", None)
    if content is not None:
        return _tool_output_payload(content)
    if not isinstance(output, str):
        return None
    text = output.strip()
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except _json.JSONDecodeError:
        pass
    match = re.search(
        r"content=(?P<literal>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        content_text = ast.literal_eval(match.group("literal"))
    except (SyntaxError, ValueError):
        return None
    return _tool_output_payload(content_text)


def _tool_artifact_virtual_path(
    artifact: dict[str, Any],
    result: Any,
) -> str | None:
    path = artifact.get("path")
    if isinstance(path, str) and path.startswith("/trading_desk/"):
        return path
    result_paths = _tool_result_paths(result)
    if not result_paths:
        return None
    if not isinstance(path, str) or not path:
        return result_paths[0] if len(result_paths) == 1 else None
    name = PurePosixPath(path).name
    matches = [
        candidate for candidate in result_paths if PurePosixPath(candidate).name == name
    ]
    if len(matches) == 1:
        return matches[0]
    return result_paths[0] if len(result_paths) == 1 else None


def _tool_result_paths(result: Any) -> list[str]:
    paths: list[str] = []

    def add(value: Any) -> None:
        if (
            isinstance(value, str)
            and value.startswith("/trading_desk/")
            and value not in paths
        ):
            paths.append(value)

    if isinstance(result, dict):
        add(result.get("file_path"))
        add(result.get("path"))
        files = result.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict):
                    add(item.get("file_path"))
                    add(item.get("path"))
                else:
                    add(item)
    return paths


def _tool_artifact_file_data(artifact: dict[str, Any]) -> dict[str, str] | None:
    content = artifact.get("content")
    if isinstance(content, str):
        return {"content": content, "encoding": "utf-8"}
    content_b64 = artifact.get("content_b64")
    if isinstance(content_b64, str):
        return {"content_b64": content_b64, "encoding": "base64"}
    return None


def _safe_virtual_artifact_path(virtual_path: str) -> PurePosixPath | None:
    path = PurePosixPath(virtual_path)
    if path.is_absolute():
        path = PurePosixPath(*path.parts[1:])
    if ".." in path.parts or not path.parts or path.parts[0] != "trading_desk":
        return None
    return path


def _asset_kind_for_path(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    return "file"


def _mime_type_for_asset(kind: str, path: str | None = None) -> str | None:
    suffix = PurePosixPath(path or "").suffix.lower()
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".txt":
        return "text/plain"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return {
        "html": "text/html",
        "markdown": "text/markdown",
        "json": "application/json",
    }.get(kind)


def _merge_assets(*groups: list[AgentAssetOut]) -> list[AgentAssetOut]:
    merged: list[AgentAssetOut] = []
    seen: set[str] = set()
    for group in groups:
        for asset in group:
            if asset.id in seen:
                continue
            seen.add(asset.id)
            merged.append(asset)
    return merged


class ResumeValidationError(ValueError):
    """Raised by resume_pending_action when the request is structurally invalid.

    Maps to HTTP 400/404 in the web layer.
    """

    def __init__(self, message: str, status_hint: int = 400) -> None:
        super().__init__(message)
        self.status_hint = status_hint


class ResumeConflictError(RuntimeError):
    """Raised when a pending action has already been resolved (HTTP 409)."""


class ResumeAgentError(RuntimeError):
    """Raised when the underlying agent invocation fails (HTTP 502/503)."""

    def __init__(self, message: str, status_hint: int = 502) -> None:
        super().__init__(message)
        self.status_hint = status_hint


class WorkflowResumeConflict(RuntimeError):
    """Raised when a task-scoped HITL action can no longer be resumed."""


@dataclass(frozen=True)
class _WorkflowStreamTurn:
    route: WorkspaceRouteDecision
    prompt: str | None = None
    config: dict[str, Any] | None = None
    assets: tuple[AgentAssetOut, ...] = ()
    agent_session_id: int | None = None
    checkpointer_key: str | None = None
    envelope_final: str | None = None
    router_message_id: int | None = None
    router_response_text: str | None = None


# Execution modes. Interactive surfaces HITL prompts; AUTO auto-clears them but
# the model may still ask via propose_reply_options; YOLO is fully headless (no
# HITL prompts AND no deferral — the card tool is withheld).
_VALID_MODES = {"interactive", "auto", "yolo"}


def resolve_execution_mode(
    mode: str | None, yolo_mode: bool
) -> tuple[str, bool, bool]:
    """Resolve a turn's execution mode into ``(mode, clear_hitl, allow_reply_options)``.

    ``mode`` is the canonical signal. When it is absent (legacy callers), it is
    derived from the deprecated ``yolo_mode`` boolean: ``True`` → ``auto`` (the
    old YOLO behavior — clears HITL but still allows reply cards), ``False`` →
    ``interactive``. Legacy callers are never headless.

    Returns the normalized mode plus the two capability flags the interior uses:
    ``clear_hitl`` (the value threaded as ``yolo_mode`` internally) and
    ``allow_reply_options``.
    """
    if mode is None:
        mode = "auto" if yolo_mode else "interactive"
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown execution mode {mode!r}; expected one of {sorted(_VALID_MODES)}")
    clear_hitl = mode in {"auto", "yolo"}
    allow_reply_options = mode != "yolo"
    return mode, clear_hitl, allow_reply_options


class AgentService:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: ChannelRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or get_registry()
        self.tools = select_deep_agent_tools()
        self.default_model_selection = default_agent_model_selection(self.registry)
        self.model = build_agent_model(self.registry)
        if self.model is None:
            self.deep_agent = None
            self.checkpointer = None
        else:
            self.checkpointer = build_checkpointer(self.settings)
            self.deep_agent = build_orchestrator(
                model=self.model,
                tools=self.tools,
                checkpointer=self.checkpointer,
                interrupt_on=interrupt_on_config(),
                enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
            )
        self._owned_deep_agent = self.deep_agent
        # Goal mode (spec §G): set by create_app once the DB-backed GoalRunService is
        # built. The stream path consults it to attach the grader on a goal turn.
        self.goal_service: GoalRunService | None = None

    def rebuild_default_model(self) -> None:
        """Refresh the cached default model after a registry reload."""
        self.registry = get_registry()
        self.default_model_selection = default_agent_model_selection(self.registry)
        self.model = build_agent_model(self.registry)
        if self.model is None:
            self.deep_agent = None
            self.checkpointer = None
        else:
            self.checkpointer = build_checkpointer(self.settings)
            self.deep_agent = build_orchestrator(
                model=self.model,
                tools=self.tools,
                checkpointer=self.checkpointer,
                interrupt_on=interrupt_on_config(),
                enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
            )
        self._owned_deep_agent = self.deep_agent

    def rebuild_orchestrator(self) -> bool:
        """Rebuild the deep-agent graph from current on-disk skills/prompts.

        Narrower than `rebuild_default_model`: keeps the existing model and
        checkpointer and does NOT re-read the channel registry or dotenv —
        a skill edit must not fail on unrelated channel problems. In-flight
        streams keep the old graph alive via Python references; requests
        after this call use the new graph.

        Returns False when the agent is disabled (no model configured).
        """
        if self.model is None:
            return False
        self.deep_agent = build_orchestrator(
            model=self.model,
            tools=self.tools,
            checkpointer=self.checkpointer,
            interrupt_on=interrupt_on_config(),
            enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
        )
        self._owned_deep_agent = self.deep_agent
        return True

    def normalize_model_selection(
        self,
        model_selection: dict[str, str] | None = None,
    ) -> dict[str, str]:
        return resolve_agent_model_selection(self.registry, model_selection)

    def is_enabled(self, model_selection: dict[str, str] | None = None) -> bool:
        resolved = self.normalize_model_selection(model_selection)
        if self._is_default_model_selection(resolved):
            return self.deep_agent is not None
        return build_agent_model(self.registry, resolved) is not None

    def _record_envelope_transition(
        self, thread_id: int, audit_payload: dict
    ) -> None:
        """Persist an envelope.transitioned audit event off the event loop."""
        with _database.SessionLocal() as session:
            record_audit(
                session,
                event_type=audit_payload["event_type"],
                actor="runtime",
                subject_type="thread",
                subject_id=thread_id,
                payload=audit_payload,
            )
            session.commit()

    def _resolve_envelope(
        self,
        envelope: Envelope | str | None,
        page_context: AgentPageContext | None,
    ) -> Envelope:
        """Pick the envelope for this turn.

        Caller-supplied envelope wins; otherwise default by UI origin —
        presence of ``page_context`` implies a pet (page-pinned) request and
        starts at ``pet_page``. No page context implies the desk agent.
        """
        if isinstance(envelope, Envelope):
            return envelope
        if isinstance(envelope, str):
            try:
                return Envelope(envelope)
            except ValueError:
                pass
        return Envelope.PET_PAGE if page_context is not None else Envelope.DESK_WORKFLOW

    def _resolve_signal_escalation(
        self, denial: dict[str, Any]
    ) -> tuple[Envelope, dict] | None:
        """Reconstruct a CapabilityDeniedError from a captured signal and reuse
        the pure escalation policy. Returns ``(new_envelope, audit_payload)`` or
        ``None`` when the denial has no defined transition."""
        try:
            error = CapabilityDeniedError(
                envelope=Envelope(denial["envelope"]),
                group=ToolGroup(denial["group"]),
                tool_name=denial.get("tool_name", "?"),
            )
        except (KeyError, ValueError):
            return None
        return resolve_escalation(error)

    @staticmethod
    def _ensure_runtime_denial_signal(config: dict, denial: CapabilityDeniedError) -> None:
        """Ensure propagated capability-denial control flow is visible to retry logic."""
        configurable = config.get("configurable") or {}
        sink = configurable.get(RUNTIME_SIGNAL_SINK_KEY)
        if not isinstance(sink, list):
            return
        signal = {
            "kind": "capability_denied",
            "envelope": denial.envelope.value,
            "group": denial.group.value,
            "tool_name": denial.tool_name,
        }
        if signal not in sink:
            sink.append(signal)

    async def _apply_runtime_signals(
        self,
        *,
        agent: Any,
        prompt: str,
        base_config: dict,
        collector: StreamCollector,
        thread_id: int,
        stream_version: str | None = None,
    ):
        """Act on runtime control-flow signals captured during the stream.

        The capability gate surfaces denials as data through TWO channels:

        The capability gate appends denials/cost-previews to a mutable list in
        ``configurable[RUNTIME_SIGNAL_SINK_KEY]`` before raising. That sink is the
        only channel out of a persona subagent: deepagents invokes subagents
        imperatively inside the ``task`` tool, so their inner tool events never
        reach the parent stream, but ``configurable`` is forwarded by reference.

        After the stream ends:

        - ``capability_denied`` -> widen the envelope once and re-drive the turn
          under the new envelope. One-shot: a denial during the retry is not
          escalated again (it surfaces as a refusal/cost-preview like any turn).
        - ``cost_preview_required`` (first pass OR widened retry) -> emit a
          structured event so the UI can render a confirm button.

        Yields SSE lines.
        """
        sink = (base_config.get("configurable") or {}).get(RUNTIME_SIGNAL_SINK_KEY) or []
        denial = next(
            (s for s in sink if isinstance(s, dict) and s.get("kind") == "capability_denied"),
            None,
        )
        if denial is not None and not collector.envelope_transitioned:
            resolution = self._resolve_signal_escalation(denial)
            if resolution is None:
                logger.warning(
                    "Capability denied, no escalation: %s under %s",
                    denial.get("tool_name"),
                    denial.get("envelope"),
                )
                collector.error = (
                    f"The tool '{denial.get('tool_name')}' is not available at the "
                    "current access level and cannot be widened further."
                )
                yield _sse("error", {"message": collector.error, "retryable": False})
            else:
                new_envelope, audit_payload = resolution
                await asyncio.to_thread(
                    self._record_envelope_transition, thread_id, audit_payload
                )
                yield _sse(
                    "envelope_transitioned",
                    {
                        "previous_envelope": audit_payload["previous_envelope"],
                        "new_envelope": audit_payload["new_envelope"],
                        "reason": audit_payload["reason"],
                        "denied_tool": audit_payload["denied_tool"],
                    },
                )
                collector.envelope_final = new_envelope.value
                collector.envelope_transitioned = True
                # Drop the first-pass refusal prose/UI proposals so the persisted
                # final message reflects only the post-escalation answer. (The
                # tokens already streamed live; the client resets on the
                # envelope_transitioned event. Tool-denial events are kept.)
                collector.reset_user_facing_output_for_retry()
                # Derive the retry config from the base config so the checkpointer
                # key (and confirmed_cost_preview, recursion_limit, …) are
                # preserved; only the envelope widens.
                retry_config = dict(base_config)
                retry_config["configurable"] = {
                    **(base_config.get("configurable") or {}),
                    "envelope": new_envelope.value,
                }
                try:
                    async for sse_line in self._drive_stream(
                        agent,
                        prompt,
                        retry_config,
                        collector,
                        stream_version=stream_version,
                    ):
                        yield sse_line
                except CapabilityDeniedError as retry_denial:
                    collector.error = (
                        f"The tool '{retry_denial.tool_name}' is not available after "
                        f"widening to '{new_envelope.value}'."
                    )
                    yield _sse(
                        "error",
                        {"message": collector.error, "retryable": False},
                    )

        sink_cost = next(
            (s for s in sink if isinstance(s, dict) and s.get("kind") == "cost_preview_required"),
            None,
        )
        if sink_cost is not None and collector.cost_preview is None:
            # Stash on the collector so the persisted message carries it (the UI
            # confirm-button path reads collector.cost_preview at finalize).
            collector.cost_preview = {
                "tool_name": sink_cost.get("tool_name"),
                "estimated_seconds": sink_cost.get("estimated_seconds"),
            }
        cost = collector.cost_preview
        if cost is not None and not collector.error:
            estimated = float(cost.get("estimated_seconds") or 0.0)
            yield _sse(
                "cost_preview_required",
                {"tool_name": cost.get("tool_name"), "estimated_seconds": estimated},
            )
            collector.error = (
                f"Confirmation needed: {cost.get('tool_name')} is estimated at "
                f"~{estimated:.1f}s. Resubmit with confirmed_cost_preview=true to "
                "proceed."
            )

    def _is_default_model_selection(self, model_selection: dict[str, str]) -> bool:
        return model_selection == self.default_model_selection

    def _stream_version_for_selection(self, model_selection: dict[str, str]) -> str:
        version = self.settings.agent_stream_version
        if version != "v3":
            return version
        if (
            model_selection.get("channel") == "deepseek"
            or model_selection.get("provider") == "deepseek"
            or model_selection.get("model", "").startswith("deepseek-")
        ):
            # LangGraph v3 content-block streaming currently drops DeepSeek
            # reasoning_content from checkpointed assistant tool-call messages.
            # DeepSeek requires that field to be replayed after tool calls.
            return "v2"
        return version

    def create_thread(
        self, session: Session, title: str, character: str, source: str = "desk"
    ) -> AgentThread:
        thread = AgentThread(title=title, character=character, source=source)
        session.add(thread)
        session.flush()
        ensure_thread_workflow_state(session, thread.id)
        record_audit(
            session,
            event_type="thread.created",
            actor="system",
            subject_type="thread",
            subject_id=thread.id,
            payload={"character": character, "source": source},
        )
        return thread

    def auto_name_thread_from_first_message(
        self,
        session: Session,
        thread: AgentThread,
        content: str,
    ) -> bool:
        current_title = (thread.title or "").strip()
        if current_title not in _PLACEHOLDER_THREAD_TITLES:
            return False
        existing_messages = (
            session.query(func.count(AgentMessage.id))
            .filter(AgentMessage.thread_id == thread.id)
            .scalar()
            or 0
        )
        if existing_messages != 0:
            return False
        new_title = self.summarize_thread_title(content)
        if not new_title:
            return False

        thread.title = new_title
        self._sync_default_workflow_titles_for_auto_name(
            session,
            thread_id=thread.id,
            old_title=current_title,
            new_title=new_title,
        )
        record_audit(
            session,
            event_type="thread.auto_renamed",
            actor="system",
            subject_type="thread",
            subject_id=thread.id,
            payload={
                "old_title": current_title,
                "new_title": new_title,
                "source": "first_user_message",
            },
        )
        return True

    @staticmethod
    def _sync_default_workflow_titles_for_auto_name(
        session: Session,
        *,
        thread_id: int,
        old_title: str,
        new_title: str,
    ) -> None:
        workflows = (
            session.query(Workflow)
            .filter(
                Workflow.thread_id == thread_id,
                Workflow.intent.in_(("ad_hoc", "workspace_meta")),
            )
            .all()
        )
        for workflow in workflows:
            if workflow.intent == "ad_hoc" and workflow.title == old_title:
                workflow.title = new_title
            elif (
                workflow.intent == "workspace_meta"
                and workflow.title == f"{old_title} / workspace"
            ):
                workflow.title = f"{new_title} / workspace"

    def summarize_thread_title(self, content: str) -> str:
        fallback = _fallback_thread_title(content)
        selection = self._thread_title_model_selection()
        if selection is None:
            return fallback
        try:
            model = build_agent_model(self.registry, selection)
            if model is None:
                return fallback
            response = model.invoke(
                [
                    SystemMessage(content=_THREAD_TITLE_PROMPT),
                    HumanMessage(
                        content=(
                            "User message:\n"
                            f"{content.strip()[:_THREAD_TITLE_SOURCE_MAX_CHARS]}"
                        )
                    ),
                ]
            )
        except Exception:
            logger.exception("Thread title summarization failed; using fallback title")
            return fallback
        return _clean_thread_title_summary(_message_text(response)) or fallback

    def _thread_title_model_selection(self) -> dict[str, str] | None:
        for channel in self.registry.channels:
            if not channel.healthy:
                continue
            for model in channel.models:
                if "fast" in model.tags:
                    return {
                        "channel": channel.name,
                        "provider": model.provider,
                        "model": model.id,
                    }
        return self.registry.default_selection() if any(
            channel.healthy for channel in self.registry.channels
        ) else None

    def respond(
        self,
        session: Session,
        thread: AgentThread,
        content: str,
        requested_character: str = "auto",
        page_context: AgentPageContext | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        accounting_date: date | str | None = None,
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
    ) -> AgentMessage:
        resolved = self.normalize_model_selection(model_selection)
        effective_accounting_date = _effective_accounting_date(accounting_date)
        ensure_thread_workflow_state(session, thread.id)
        self.auto_name_thread_from_first_message(session, thread, content)
        user_msg = AgentMessage(
            thread_id=thread.id,
            role="user",
            character=None,
            content=content,
            meta={
                "page_context": (
                    page_context.model_dump(mode="json") if page_context else None
                ),
                "context_usage": _context_usage_meta(context_usage),
                "accounting_date": effective_accounting_date.isoformat(),
                "model_selection": resolved,
                "yolo_mode": yolo_mode,
            },
        )
        session.add(user_msg)

        if self.settings.feature_workflow_routing:
            route = route_workspace_turn(
                session,
                thread=thread,
                user_message=content,
                yolo_mode=yolo_mode,
            )
            self._assign_message_route(user_msg, route)
            if route.response_content is not None:
                return self._persist_router_response(
                    session,
                    thread,
                    route,
                    page_context=page_context,
                    context_usage=context_usage,
                    accounting_date=effective_accounting_date,
                    model_selection=resolved,
                    yolo_mode=yolo_mode,
                )

        agent = self._sync_agent_for_selection(resolved, yolo_mode=yolo_mode)
        if agent is None:
            return self._persist_disabled_response(
                session, thread, model_selection=resolved, yolo_mode=yolo_mode
            )
        if self.settings.feature_workflow_routing:
            return self._respond_workflow_routed(
                session,
                thread,
                content,
                agent,
                route=route,
                requested_character=requested_character,
                page_context=page_context,
                context_usage=context_usage,
                accounting_date=effective_accounting_date,
                model_selection=resolved,
                yolo_mode=yolo_mode,
                envelope=None,
            )

        context = self._context(
            session,
            page_context,
            effective_accounting_date,
            thread_id=thread.id,
        )
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(
            content,
            requested_character,
            context,
            yolo_mode=yolo_mode,
        )
        resolved_envelope = self._resolve_envelope(None, page_context)

        try:
            result = agent.invoke(
                {"messages": [HumanMessage(content=prompt)]},
                config=graph_run_config(
                    self.settings,
                    thread_id=thread.id,
                    configurable_extra={"envelope": resolved_envelope.value},
                ),
            )
        except Exception as exc:
            logger.exception("DeepAgent invoke failed for thread %s", thread.id)
            record_audit(
                session,
                event_type="agent.error",
                actor="system",
                subject_type="thread",
                subject_id=thread.id,
                payload={"error_type": type(exc).__name__, "message": str(exc)[:500]},
            )
            raise

        return self._persist_agent_result(
            session,
            thread,
            result,
            assets,
            page_context,
            effective_accounting_date,
            context_usage=context_usage,
            model_selection=resolved,
            yolo_mode=yolo_mode,
        )

    @staticmethod
    def _assign_message_route(
        message: AgentMessage,
        route: WorkspaceRouteDecision,
    ) -> None:
        message.workflow_id = route.workflow_id
        message.session_id = route.session_id
        message.meta = {
            **(message.meta or {}),
            "router_decision": route.kind,
            "routed_workflow_id": route.target_workflow_id,
        }

    def _persist_router_response(
        self,
        session: Session,
        thread: AgentThread,
        route: WorkspaceRouteDecision,
        *,
        page_context: AgentPageContext | None,
        context_usage: AgentContextUsage | dict[str, Any] | None,
        accounting_date: date | str,
        model_selection: dict[str, str],
        yolo_mode: bool,
        actor: str = "desk_user",
    ) -> AgentMessage:
        message = AgentMessage(
            thread_id=thread.id,
            workflow_id=route.workflow_id,
            session_id=route.session_id,
            role="assistant",
            character="router",
            content=route.response_content or "",
            meta={
                "agent_graph": "workspace_router",
                "agent_phase": "completed",
                "workflow_routing": True,
                "router_decision": route.kind,
                "workflow_id": route.workflow_id,
                "session_id": route.session_id,
                "pending_actions": [],
                "context_used": (
                    page_context.model_dump(mode="json") if page_context else None
                ),
                "context_usage": _context_usage_meta(context_usage),
                "accounting_date": _effective_accounting_date(accounting_date).isoformat(),
                "agent_enabled": True,
                "model_selection": model_selection,
                "yolo_mode": yolo_mode,
            },
        )
        session.add(message)
        session.flush()
        record_audit(
            session,
            event_type="chat.message",
            actor=actor,
            subject_type="thread",
            subject_id=thread.id,
            payload={
                "workflow_routing": True,
                "router_decision": route.kind,
                "workflow_id": route.workflow_id,
            },
        )
        return message

    def _respond_workflow_routed(
        self,
        session: Session,
        thread: AgentThread,
        content: str,
        agent: Any,
        *,
        route: WorkspaceRouteDecision,
        requested_character: str,
        page_context: AgentPageContext | None,
        context_usage: AgentContextUsage | dict[str, Any] | None,
        accounting_date: date | str,
        model_selection: dict[str, str],
        yolo_mode: bool,
        envelope: Envelope | str | None,
    ) -> AgentMessage:
        agent_session = session.get(AgentSession, route.session_id)
        if agent_session is None:
            raise ValueError(f"AgentSession {route.session_id} not found")

        context = self._context(
            session,
            page_context,
            accounting_date,
            thread_id=thread.id,
        )
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(
            content,
            requested_character,
            context,
            yolo_mode=yolo_mode,
        )
        resolved_envelope = self._resolve_envelope(envelope, page_context)
        memory_extra = {}
        from .deep_agent.memory.config import get_memory_config
        if get_memory_config().enabled:
            from .deep_agent.memory.runtime import latest_user_message_id, memory_configurable
            memory_extra = memory_configurable(
                session_id=route.session_id,
                thread_id=thread.id,
                persona=getattr(agent_session, "persona", None),
                message_id=latest_user_message_id(session, thread.id),
            )
        config = graph_run_config(
            self.settings,
            thread_id=agent_session.checkpointer_key,
            configurable_extra={
                "workflow_id": route.workflow_id,
                "session_id": route.session_id,
                "envelope": resolved_envelope.value,
                "router_decision": route.kind,
                "agent_runtime": "deepagents_orchestrator",
                **memory_extra,
            },
        )

        try:
            result = agent.invoke({"messages": [HumanMessage(content=prompt)]}, config=config)
        except Exception as exc:
            logger.exception("Workflow orchestrator invoke failed for thread %s", thread.id)
            record_audit(
                session,
                event_type="agent.error",
                actor="system",
                subject_type="thread",
                subject_id=thread.id,
                payload={"error_type": type(exc).__name__, "message": str(exc)[:500]},
            )
            raise

        return self._persist_workflow_orchestrator_result(
            session,
            thread,
            result,
            assets,
            route=route,
            agent_session=agent_session,
            page_context=page_context,
            accounting_date=accounting_date,
            context_usage=context_usage,
            model_selection=model_selection,
            yolo_mode=yolo_mode,
            envelope_final=resolved_envelope.value,
        )

    def _persist_workflow_orchestrator_result(
        self,
        session: Session,
        thread: AgentThread,
        result: Any,
        assets: list[AgentAssetOut],
        *,
        route: WorkspaceRouteDecision,
        agent_session: AgentSession,
        page_context: AgentPageContext | None,
        accounting_date: date | str | None,
        context_usage: AgentContextUsage | dict[str, Any] | None,
        model_selection: dict[str, str],
        yolo_mode: bool,
        envelope_final: str,
        include_interactive_affordances: bool = True,
        actor: str = "desk_user",
    ) -> AgentMessage:
        resolved = self.normalize_model_selection(model_selection)
        effective_accounting_date = _effective_accounting_date(accounting_date)
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        personas = _personas_invoked(result)
        last_persona = personas[-1] if personas else "orchestrator"
        reply_options = (
            _reply_options_from_result(result)
            if include_interactive_affordances
            else None
        )
        term_form = (
            _term_form_from_result(result)
            if include_interactive_affordances
            else None
        )
        assets = _merge_assets(
            assets,
            _agent_file_assets_from_state(
                result.get("files") if isinstance(result, dict) else None,
                artifact_dir=self.settings.artifact_dir,
                thread_id=thread.id,
            ),
            _agent_file_assets_from_state(
                _tool_artifact_files_from_result(result),
                artifact_dir=self.settings.artifact_dir,
                thread_id=thread.id,
            ),
        )
        source_meta = {
            "workflow_id": route.workflow_id,
            "session_id": route.session_id,
            "checkpointer_key": agent_session.checkpointer_key,
            "envelope_final": envelope_final,
            "agent_runtime": "deepagents_orchestrator",
        }

        if interrupts:
            pending = pending_actions_from_interrupts(
                list(interrupts),
                persona=last_persona,
                source_meta=source_meta,
            )
            raw_content = (
                _extract_final_ai_text(result)
                or "Awaiting confirmation for the next step."
            )
            content = self._public_response_text(raw_content)
            agent_phase = "awaiting_confirmation"
            interrupt_ids = [getattr(intr, "id", "") for intr in list(interrupts)]
        else:
            pending = []
            raw_content = _extract_final_ai_text(result) or "(no response)"
            content = self._public_response_text(raw_content)
            agent_phase = "completed"
            interrupt_ids = []

        assistant_msg = AgentMessage(
            thread_id=thread.id,
            workflow_id=route.workflow_id,
            session_id=route.session_id,
            role="assistant",
            character=last_persona,
            content=content,
            meta={
                "agent_graph": "deepagents",
                "agent_phase": agent_phase,
                "workflow_routing": True,
                "router_decision": route.kind,
                "workflow_id": route.workflow_id,
                "session_id": route.session_id,
                "pending_actions": [a.model_dump(mode="json") for a in pending],
                "interrupt_ids": interrupt_ids,
                "personas_invoked": personas,
                "assets": [asset.model_dump(mode="json") for asset in assets],
                "context_used": (
                    page_context.model_dump(mode="json") if page_context else None
                ),
                "context_usage": _context_usage_meta(context_usage),
                "accounting_date": effective_accounting_date.isoformat(),
                "agent_enabled": True,
                "model_selection": resolved,
                "yolo_mode": yolo_mode,
                "envelope_final": envelope_final,
                **({"reply_options": reply_options} if reply_options else {}),
                **({"term_form": term_form} if term_form else {}),
            },
        )
        session.add(assistant_msg)
        thread.character = last_persona or thread.character
        session.flush()
        if pending:
            # Audit spec §5.4: proposal rows commit atomically with the card.
            record_hitl_proposals(
                session,
                assistant_msg.meta["pending_actions"],
                tools=self.tools,
                context={
                    "thread_id": thread.id,
                    "actor": actor,
                    "workflow_id": route.workflow_id,
                    "session_id": route.session_id,
                    "message_id": assistant_msg.id,
                },
            )
        record_audit(
            session,
            event_type="chat.message",
            actor=actor,
            subject_type="thread",
            subject_id=thread.id,
            payload={
                "workflow_routing": True,
                "router_decision": route.kind,
                "workflow_id": route.workflow_id,
                "session_id": route.session_id,
                "personas_invoked": personas,
            },
        )
        return assistant_msg

    def _prepare_workflow_routed_stream_turn(
        self,
        *,
        thread_id: int,
        content: str,
        page_context: AgentPageContext | None,
        context_usage: AgentContextUsage | dict[str, Any] | None,
        accounting_date: date | str,
        model_selection: dict[str, str],
        yolo_mode: bool,
        envelope: Envelope | str | None,
        requested_character: str,
        confirmed_cost_preview: bool = False,
        desk_workflow_slug: str | None = None,
        desk_workflow_source: str | None = None,
        desk_workflow_launch_args: dict | None = None,
        actor: str = "desk_user",
        mode: str | None = None,
    ) -> _WorkflowStreamTurn:
        with _database.SessionLocal() as session:
            thread = session.get(AgentThread, thread_id)
            if thread is None:
                raise ValueError(f"AgentThread {thread_id} not found")
            ensure_thread_workflow_state(session, thread_id)
            route = route_workspace_turn(
                session,
                thread=thread,
                user_message=content,
                yolo_mode=yolo_mode,
            )
            latest_user = (
                session.query(AgentMessage)
                .filter(
                    AgentMessage.thread_id == thread_id,
                    AgentMessage.role == "user",
                )
                .order_by(AgentMessage.id.desc())
                .first()
            )
            if latest_user is not None:
                self._assign_message_route(latest_user, route)
            if route.response_content is not None:
                message = self._persist_router_response(
                    session,
                    thread,
                    route,
                    page_context=page_context,
                    context_usage=context_usage,
                    accounting_date=accounting_date,
                    model_selection=model_selection,
                    yolo_mode=yolo_mode,
                )
                session.commit()
                return _WorkflowStreamTurn(
                    route=route,
                    router_message_id=message.id,
                    router_response_text=message.content,
                )

            agent_session = session.get(AgentSession, route.session_id)
            if agent_session is None:
                raise ValueError(f"AgentSession {route.session_id} not found")

            context = self._context(
                session,
                page_context,
                accounting_date,
                thread_id=thread_id,
            )
            assets = tuple(self._context_assets(page_context))
            prompt = _orchestrator_user_prompt(
                content,
                requested_character,
                context,
                yolo_mode=yolo_mode,
            )
            resolved_envelope = self._resolve_envelope(envelope, page_context)
            configurable_extra: dict[str, Any] = {
                "workflow_id": route.workflow_id,
                "session_id": route.session_id,
                "envelope": resolved_envelope.value,
                "router_decision": route.kind,
                "agent_runtime": "deepagents_orchestrator",
                # Mutable sink the capability gate writes denials into, even from
                # inside a persona subagent (configurable is forwarded by deepagents).
                RUNTIME_SIGNAL_SINK_KEY: [],
                # Turn identity for the dangerous-action audit trail (spec §5.3);
                # read by AuditTrailMiddleware inside every stack.
                AUDIT_CONTEXT_KEY: {
                    "actor": actor,
                    "mode": mode or ("auto" if yolo_mode else "interactive"),
                    "envelope": resolved_envelope.value,
                    "model": model_selection.get("model"),
                    "thread_id": thread_id,
                    "workflow_id": route.workflow_id,
                    "session_id": route.session_id,
                    "desk_workflow_slug": desk_workflow_slug,
                },
            }
            if confirmed_cost_preview:
                configurable_extra["confirmed_cost_preview"] = True
            from .deep_agent.dynamic_subagents import fanout_attribution_extra
            configurable_extra.update(
                fanout_attribution_extra(
                    slug=desk_workflow_slug, source=desk_workflow_source,
                    launch_args=desk_workflow_launch_args,
                )
            )
            config = graph_run_config(
                self.settings,
                thread_id=agent_session.checkpointer_key,
                configurable_extra=configurable_extra,
                trace_meta={"thread_id": thread_id, "workflow_id": route.workflow_id},
            )
            session.commit()
            return _WorkflowStreamTurn(
                route=route,
                prompt=prompt,
                config=config,
                assets=assets,
                agent_session_id=agent_session.id,
                checkpointer_key=agent_session.checkpointer_key,
                envelope_final=resolved_envelope.value,
            )

    async def _finalize_workflow_stream_turn(
        self,
        *,
        agent: Any,
        config: dict,
        thread_id: int,
        collector: StreamCollector,
        prepared: _WorkflowStreamTurn,
        page_context: AgentPageContext | None,
        model_selection: dict[str, str] | None = None,
        accounting_date: date | str | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        yolo_mode: bool = False,
        actor: str = "desk_user",
    ) -> int | None:
        assets = list(prepared.assets)
        assets = _merge_assets(
            assets,
            _agent_file_assets_from_state(
                collector.artifact_files,
                artifact_dir=self.settings.artifact_dir,
                thread_id=thread_id,
            ),
        )
        state_final_text = ""
        try:
            state = await self._read_stream_state(agent, config)
            values = getattr(state, "values", None) or {}
            if state and state.tasks:
                for task in state.tasks:
                    collector.interrupts.extend(getattr(task, "interrupts", []) or [])
            self._extract_personas_from_state(state, collector)
            state_todos = _normalize_todos(values.get("todos"))
            collector.set_todos(state_todos)
            state_final_text = _extract_final_ai_text(values)
            if not collector.final_text and collector.error is None:
                if state_final_text:
                    collector.on_token(state_final_text)
            assets = _merge_assets(
                assets,
                _agent_file_assets_from_state(
                    values.get("files"),
                    artifact_dir=self.settings.artifact_dir,
                    thread_id=thread_id,
                ),
            )
        except Exception:
            logger.exception("get_state failed for workflow-routed thread %s", thread_id)

        try:
            return await asyncio.to_thread(
                self._persist_workflow_stream_collector,
                thread_id,
                collector,
                assets,
                prepared.route,
                prepared.agent_session_id,
                prepared.checkpointer_key,
                page_context,
                model_selection,
                accounting_date,
                context_usage,
                yolo_mode,
                state_final_text,
                prepared.envelope_final,
                actor,
            )
        except Exception:
            logger.exception("Persist failed for workflow-routed thread %s", thread_id)
            return None

    def _persist_workflow_stream_collector(
        self,
        thread_id: int,
        collector: StreamCollector,
        assets: list[AgentAssetOut],
        route: WorkspaceRouteDecision,
        agent_session_id: int | None,
        checkpointer_key: str | None,
        page_context: AgentPageContext | None,
        model_selection: dict[str, str] | None = None,
        accounting_date: date | str | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        yolo_mode: bool = False,
        state_final_text: str | None = None,
        envelope_final: str | None = None,
        actor: str = "desk_user",
    ) -> int | None:
        resolved = self.normalize_model_selection(model_selection)
        effective_accounting_date = _effective_accounting_date(accounting_date)
        with _database.SessionLocal() as session:
            thread = session.get(AgentThread, thread_id)
            if thread is None:
                return None
            ensure_thread_workflow_state(session, thread_id)
            agent_session = (
                session.get(AgentSession, agent_session_id)
                if agent_session_id is not None
                else None
            )
            source_meta = {
                "workflow_id": route.workflow_id,
                "session_id": route.session_id,
                "checkpointer_key": checkpointer_key
                or (agent_session.checkpointer_key if agent_session else None),
                "envelope_final": envelope_final,
                "agent_runtime": "deepagents_orchestrator",
            }
            last_persona = (
                collector.personas_invoked[-1]
                if collector.personas_invoked
                else "orchestrator"
            )

            if collector.interrupts:
                pending = pending_actions_from_interrupts(
                    collector.interrupts,
                    persona=last_persona,
                    source_meta=source_meta,
                )
                content = _select_public_stream_response(
                    stream_text=collector.final_text,
                    state_final_text=state_final_text,
                    fallback_text="Awaiting confirmation for the next step.",
                )
                agent_phase = "awaiting_confirmation"
                interrupt_ids = [getattr(intr, "id", "") for intr in collector.interrupts]
            else:
                pending = []
                agent_phase = _collector_completion_phase(collector)
                tool_error_text = _collector_tool_error_text(collector)
                transport_recovery_text = _collector_transport_recovery_text(
                    collector
                )
                recovered_text = "\n\n".join(
                    part
                    for part in (collector.final_text, transport_recovery_text)
                    if part
                )
                state_recovered_text = "\n\n".join(
                    part
                    for part in (state_final_text, transport_recovery_text)
                    if part
                )
                content = (
                    recovered_text
                    or collector.error
                    or tool_error_text
                    or (
                        "Run paused before completion."
                        if collector.drained
                        else "(no response)"
                    )
                )
                content = (
                    content
                    if collector.error
                    else _select_public_stream_response(
                        stream_text=content,
                        state_final_text=state_recovered_text,
                    )
                )
                interrupt_ids = []

            assistant_msg = AgentMessage(
                thread_id=thread_id,
                workflow_id=route.workflow_id,
                session_id=route.session_id,
                role="assistant",
                character=last_persona,
                content=content,
                meta={
                    "agent_graph": "deepagents",
                    "agent_phase": agent_phase,
                    "workflow_routing": True,
                    "router_decision": route.kind,
                    "workflow_id": route.workflow_id,
                    "session_id": route.session_id,
                    "pending_actions": [a.model_dump(mode="json") for a in pending],
                    "interrupt_ids": interrupt_ids,
                    "personas_invoked": collector.personas_invoked,
                    "process_events": collector.process_events,
                    "todos": collector.todos,
                    "assets": [asset.model_dump(mode="json") for asset in assets],
                    "context_used": (
                        page_context.model_dump(mode="json") if page_context else None
                    ),
                    "context_usage": _context_usage_meta(context_usage),
                    "accounting_date": effective_accounting_date.isoformat(),
                    "agent_enabled": True,
                    "model_selection": resolved,
                    "yolo_mode": yolo_mode,
                    "envelope_final": envelope_final,
                    "cost_preview": collector.cost_preview,
                    "error": collector.error,
                    "drained": collector.drained,
                    "drain_reason": collector.drain_reason,
                    **(
                        {"reply_options": collector.reply_options}
                        if collector.reply_options
                        else {}
                    ),
                    **(
                        {"term_form": collector.term_form}
                        if collector.term_form
                        else {}
                    ),
                },
            )
            session.add(assistant_msg)
            thread.character = last_persona or thread.character
            session.flush()
            if pending:
                # Audit spec §5.4: proposal rows commit atomically with the card.
                record_hitl_proposals(
                    session,
                    assistant_msg.meta["pending_actions"],
                    tools=self.tools,
                    context={
                        "thread_id": thread_id,
                        "actor": actor,
                        "workflow_id": route.workflow_id,
                        "session_id": route.session_id,
                        "message_id": assistant_msg.id,
                    },
                )
            record_audit(
                session,
                event_type="chat.message",
                actor=actor,
                subject_type="thread",
                subject_id=thread_id,
                payload={
                    "workflow_routing": True,
                    "router_decision": route.kind,
                    "workflow_id": route.workflow_id,
                    "session_id": route.session_id,
                    "personas_invoked": collector.personas_invoked,
                    "streamed": True,
                    "yolo_mode": yolo_mode,
                },
            )
            session.commit()
            return assistant_msg.id

    @staticmethod
    def _raw_artifact_response_text(payload: dict[str, Any]) -> str:
        for key in ("content", "message", "markdown", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "(no response)"

    @staticmethod
    def _artifact_response_text(payload: dict[str, Any]) -> str:
        return AgentService._public_response_text(
            AgentService._raw_artifact_response_text(payload)
        )

    @staticmethod
    def _public_response_text(content: str) -> str:
        if _contains_internal_routing_language(content):
            return _INTERNAL_ROUTING_FALLBACK
        return content

    async def stream_and_persist(
        self,
        *,
        thread_id: int,
        content: str,
        requested_character: str = "auto",
        page_context: AgentPageContext | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        accounting_date: date | str | None = None,
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
        mode: str | None = None,
        envelope: str | None = None,
        confirmed_cost_preview: bool = False,
        actor: str = "desk_user",
        desk_workflow_slug: str | None = None,
        desk_workflow_source: str | None = None,
        desk_workflow_launch_args: dict | None = None,
    ):
        """Stream live LangGraph events for one agent turn, then persist.

        Single-invocation refactor: this method drives a single astream_events
        run, emits typed SSE events to the client, and persists ONE
        AgentMessage after the stream completes.

        ``mode`` ("interactive" | "auto" | "yolo") is the canonical execution
        signal; the deprecated ``yolo_mode`` boolean is accepted for back-compat
        and maps to auto/interactive. YOLO drives the orchestrator headless.
        """
        mode, yolo_mode, allow_reply_options = resolve_execution_mode(mode, yolo_mode)
        # "yolo" is the headless mode: omit ALL HITL (incl. irreversible bookings)
        # so a headless run completes with no human in the loop. "auto" keeps
        # irreversible operations gated (yolo_mode bool only clears writes).
        headless = mode == "yolo"
        resolved = self.normalize_model_selection(model_selection)
        effective_accounting_date = _effective_accounting_date(accounting_date)
        if not self.is_enabled(resolved):
            message_id = await asyncio.to_thread(
                self._persist_disabled_response_by_thread,
                thread_id,
                resolved,
                yolo_mode,
            )
            yield _sse("error", {"message": _DISABLED_RESPONSE, "retryable": False})
            yield _sse("done", {"message_id": message_id})
            return

        if self.settings.feature_workflow_routing:
            resolved_envelope = self._resolve_envelope(envelope, page_context)
            collector = StreamCollector()
            collector.envelope_initial = resolved_envelope.value
            collector.envelope_final = resolved_envelope.value
            prepared: _WorkflowStreamTurn | None = None
            persisted = False
            # Goal mode (spec §G): if this thread has a running goal run, attach its
            # ledger grader to the orchestrator and carry the rubric in the invoke state.
            goal_grader, goal_state_fragment = goal_grader_for_turn(
                self.goal_service,
                model=self.model,
                tools=self.tools,
                thread_id=str(thread_id),
            )
            async with self._streaming_agent(
                resolved,
                yolo_mode=yolo_mode,
                headless=headless,
                allow_reply_options=allow_reply_options,
                goal_grader=goal_grader,
            ) as agent:
                try:
                    if agent is None:
                        raise RuntimeError("Agent is disabled (no LLM configured)")
                    prepared = await asyncio.to_thread(
                        self._prepare_workflow_routed_stream_turn,
                        thread_id=thread_id,
                        content=content,
                        page_context=page_context,
                        context_usage=context_usage,
                        accounting_date=effective_accounting_date,
                        model_selection=resolved,
                        yolo_mode=yolo_mode,
                        envelope=resolved_envelope,
                        requested_character=requested_character,
                        confirmed_cost_preview=confirmed_cost_preview,
                        desk_workflow_slug=desk_workflow_slug,
                        desk_workflow_source=desk_workflow_source,
                        desk_workflow_launch_args=desk_workflow_launch_args,
                        actor=actor,
                        mode=mode,
                    )
                    if prepared.router_message_id is not None:
                        if prepared.router_response_text:
                            yield _sse("token", {"text": prepared.router_response_text})
                        yield _sse("done", {"message_id": prepared.router_message_id})
                        persisted = True
                        return
                    assert prepared.prompt is not None
                    assert prepared.config is not None
                    collector.envelope_final = prepared.envelope_final
                    try:
                        async for sse_line in self._drive_stream(
                            agent,
                            prepared.prompt,
                            prepared.config,
                            collector,
                            stream_version=self._stream_version_for_selection(resolved),
                            extra_state=goal_state_fragment,
                        ):
                            yield sse_line
                        # Runtime signals (capability denial, cost preview) are
                        # surfaced as data by the tool-error boundary; act on
                        # them after the stream (one-shot envelope widening).
                        async for sse_line in self._apply_runtime_signals(
                            agent=agent,
                            prompt=prepared.prompt,
                            base_config=prepared.config,
                            collector=collector,
                            thread_id=thread_id,
                            stream_version=self._stream_version_for_selection(resolved),
                        ):
                            yield sse_line
                    except CapabilityDeniedError as denial:
                        self._ensure_runtime_denial_signal(prepared.config, denial)
                        async for sse_line in self._apply_runtime_signals(
                            agent=agent,
                            prompt=prepared.prompt,
                            base_config=prepared.config,
                            collector=collector,
                            thread_id=thread_id,
                            stream_version=self._stream_version_for_selection(resolved),
                        ):
                            yield sse_line
                    except CostPreviewRequiredError as cost_denial:
                        logger.info(
                            "Cost preview required for workflow-routed %s "
                            "(~%.1fs) on thread %s",
                            cost_denial.tool_name,
                            cost_denial.estimated_seconds,
                            thread_id,
                        )
                        yield _sse(
                            "cost_preview_required",
                            {
                                "tool_name": cost_denial.tool_name,
                                "estimated_seconds": cost_denial.estimated_seconds,
                            },
                        )
                        collector.error = (
                            f"Confirmation needed: {cost_denial.tool_name} is "
                            f"estimated at ~{cost_denial.estimated_seconds:.1f}s. "
                            f"Resubmit with confirmed_cost_preview=true to proceed."
                        )
                        collector.cost_preview = {
                            "tool_name": cost_denial.tool_name,
                            "estimated_seconds": cost_denial.estimated_seconds,
                        }
                    except Exception as exc:
                        logger.exception(
                            "Workflow-routed live stream failed for thread %s",
                            thread_id,
                        )
                        collector.error = str(exc)[:500]
                        yield _sse(
                            "error",
                            {"message": collector.error, "retryable": False},
                        )

                    message_id = await self._finalize_workflow_stream_turn(
                        agent=agent,
                        config=prepared.config,
                        thread_id=thread_id,
                        collector=collector,
                        prepared=prepared,
                        page_context=page_context,
                        model_selection=resolved,
                        accounting_date=effective_accounting_date,
                        context_usage=context_usage,
                        yolo_mode=yolo_mode,
                        actor=actor,
                    )
                    persisted = True
                    yield _sse("done", _done_payload(message_id, thread_id))
                except Exception as exc:
                    logger.exception(
                        "Workflow-routed stream failed for thread %s", thread_id
                    )
                    yield _sse(
                        "error",
                        {"message": str(exc)[:500], "retryable": False},
                    )
                    yield _sse("done", {"message_id": None})
                finally:
                    if (
                        not persisted
                        and prepared is not None
                        and prepared.router_message_id is None
                        and prepared.config is not None
                    ):
                        try:
                            await self._finalize_workflow_stream_turn(
                                agent=agent,
                                config=prepared.config,
                                thread_id=thread_id,
                                collector=collector,
                                prepared=prepared,
                                page_context=page_context,
                                model_selection=resolved,
                                accounting_date=effective_accounting_date,
                                context_usage=context_usage,
                                yolo_mode=yolo_mode,
                                actor=actor,
                            )
                        except Exception:
                            logger.exception(
                                "Persist failed during workflow cancellation for thread %s",
                                thread_id,
                            )
            return

        with _database.SessionLocal() as session:
            context = self._context(
                session,
                page_context,
                effective_accounting_date,
                thread_id=thread_id,
            )
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(
            content,
            requested_character,
            context,
            yolo_mode=yolo_mode,
        )
        resolved_envelope = self._resolve_envelope(envelope, page_context)
        configurable_extra: dict[str, Any] = {
            "envelope": resolved_envelope.value,
            # Mutable sink the capability gate writes denials into (see routing path).
            RUNTIME_SIGNAL_SINK_KEY: [],
            # Turn identity for the dangerous-action audit trail (spec §5.3).
            AUDIT_CONTEXT_KEY: {
                "actor": actor,
                "mode": mode,
                "envelope": resolved_envelope.value,
                "model": resolved.get("model") if isinstance(resolved, dict) else None,
                "thread_id": thread_id,
            },
        }
        if confirmed_cost_preview:
            configurable_extra["confirmed_cost_preview"] = True
        from .deep_agent.dynamic_subagents import fanout_attribution_extra
        configurable_extra.update(
            fanout_attribution_extra(
                slug=desk_workflow_slug, source=desk_workflow_source,
                launch_args=desk_workflow_launch_args,
            )
        )
        config = graph_run_config(
            self.settings,
            thread_id=thread_id,
            configurable_extra=configurable_extra,
            trace_meta={"thread_id": thread_id},
        )
        collector = StreamCollector()
        collector.envelope_initial = resolved_envelope.value
        collector.envelope_final = resolved_envelope.value
        persisted = False  # set True once _finalize_turn returns

        async with self._streaming_agent(
            resolved, yolo_mode=yolo_mode, headless=headless,
            allow_reply_options=allow_reply_options,
        ) as agent:
            try:
                try:
                    async for sse_line in self._drive_stream(
                        agent,
                        prompt,
                        config,
                        collector,
                        stream_version=self._stream_version_for_selection(resolved),
                    ):
                        yield sse_line
                    # Runtime signals (capability denial, cost preview) are
                    # surfaced as data by the tool-error boundary; act on them
                    # after the stream (one-shot envelope widening).
                    async for sse_line in self._apply_runtime_signals(
                        agent=agent,
                        prompt=prompt,
                        base_config=config,
                        collector=collector,
                        thread_id=thread_id,
                        stream_version=self._stream_version_for_selection(resolved),
                    ):
                        yield sse_line
                except CapabilityDeniedError as denial:
                    self._ensure_runtime_denial_signal(config, denial)
                    async for sse_line in self._apply_runtime_signals(
                        agent=agent,
                        prompt=prompt,
                        base_config=config,
                        collector=collector,
                        thread_id=thread_id,
                        stream_version=self._stream_version_for_selection(resolved),
                    ):
                        yield sse_line
                except CostPreviewRequiredError as cost_denial:
                    # The model picked a long-running tool without prior
                    # confirmation. Surface a structured event so the UI can
                    # render a confirm button; that resubmits the same prompt
                    # with confirmed_cost_preview=True, which threads through
                    # to configurable.confirmed_cost_preview and bypasses the
                    # estimator check on the retry.
                    logger.info(
                        "Cost preview required for %s (~%.1fs) on thread %s",
                        cost_denial.tool_name,
                        cost_denial.estimated_seconds,
                        thread_id,
                    )
                    yield _sse(
                        "cost_preview_required",
                        {
                            "tool_name": cost_denial.tool_name,
                            "estimated_seconds": cost_denial.estimated_seconds,
                        },
                    )
                    # Treat this as a soft terminal state: the user must
                    # confirm before we re-invoke. Record on the collector so
                    # the persisted message reflects "awaiting confirmation".
                    collector.error = (
                        f"Confirmation needed: {cost_denial.tool_name} is "
                        f"estimated at ~{cost_denial.estimated_seconds:.1f}s. "
                        f"Resubmit with confirmed_cost_preview=true to proceed."
                    )
                    # Structured meta so the UI can render a confirm button
                    # without parsing the error text.
                    collector.cost_preview = {
                        "tool_name": cost_denial.tool_name,
                        "estimated_seconds": cost_denial.estimated_seconds,
                    }
                except Exception as exc:
                    logger.exception("Live stream failed for thread %s", thread_id)
                    collector.error = str(exc)[:500]
                    yield _sse(
                        "error", {"message": collector.error, "retryable": False}
                    )

                # Normal (non-cancelled) path: collect interrupts, persist, emit done.
                message_id = await self._finalize_turn(
                    agent,
                    config,
                    thread_id,
                    collector,
                    assets,
                    page_context,
                    resolved,
                    effective_accounting_date,
                    context_usage,
                    yolo_mode,
                    actor,
                )
                persisted = True
                yield _sse("done", _done_payload(message_id, thread_id))
            finally:
                # Cancelled path (GeneratorExit from client disconnect): still
                # persist for disconnect resilience, but DO NOT yield — yielding
                # during generator cleanup raises RuntimeError.
                if not persisted:
                    try:
                        await self._finalize_turn(
                            agent,
                            config,
                            thread_id,
                            collector,
                            assets,
                            page_context,
                            resolved,
                            effective_accounting_date,
                            context_usage,
                            yolo_mode,
                            actor,
                        )
                    except Exception:
                        logger.exception(
                            "Persist failed during cancellation for thread %s",
                            thread_id,
                        )

    @asynccontextmanager
    async def _streaming_agent(
        self,
        model_selection: dict[str, str],
        *,
        yolo_mode: bool = False,
        headless: bool = False,
        allow_reply_options: bool = True,
        goal_grader: Any = None,
    ):
        """Yield an agent whose checkpointer supports async LangGraph methods.

        A non-None ``goal_grader`` (a RubricMiddleware for an active goal run) forces a
        fresh orchestrator — the prebuilt ``deep_agent`` lacks the grader — and is
        appended to its middleware stack (spec §G)."""
        if (
            goal_grader is not None
            or not allow_reply_options
            or yolo_mode
            or not self._is_default_model_selection(model_selection)
        ):
            model = build_agent_model(self.registry, model_selection)
            if model is None:
                yield None
                return
            async with build_async_checkpointer(self.settings) as checkpointer:
                yield build_orchestrator(
                    model=model,
                    tools=self.tools,
                    checkpointer=checkpointer,
                    interrupt_on=interrupt_on_config(yolo_mode=yolo_mode, headless=headless),
                    enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
                    yolo_mode=yolo_mode,
                    allow_reply_options=allow_reply_options,
                    goal_grader=goal_grader,
                )
            return

        if self._needs_async_sqlite_streaming_agent():
            assert (
                self.model is not None
            )  # narrowed by _needs_async_sqlite_streaming_agent
            async with build_async_checkpointer(self.settings) as checkpointer:
                yield build_orchestrator(
                    model=self.model,
                    tools=self.tools,
                    checkpointer=checkpointer,
                    interrupt_on=interrupt_on_config(),
                    enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
                )
        else:
            yield self.deep_agent

    def _sync_agent_for_selection(
        self,
        model_selection: dict[str, str],
        *,
        yolo_mode: bool = False,
        allow_reply_options: bool = True,
    ) -> Any:
        if (
            allow_reply_options
            and not yolo_mode
            and self._is_default_model_selection(model_selection)
        ):
            return self.deep_agent
        model = build_agent_model(self.registry, model_selection)
        if model is None:
            return None
        return build_orchestrator(
            model=model,
            tools=self.tools,
            checkpointer=build_checkpointer(self.settings),
            interrupt_on=interrupt_on_config(yolo_mode=yolo_mode),
            enable_code_interpreter=self.settings.agent_code_interpreter_enabled,
            yolo_mode=yolo_mode,
            allow_reply_options=allow_reply_options,
        )

    def resume_async_agent(
        self,
        task_id: int,
        decision: str,
        message: str | None,
        audit_ref: str | None = None,
    ) -> None:
        """Resume an async-agent run after a HITL decision on the parent thread."""
        from .async_agents.resume import resume_async_agent_interrupt

        resume_async_agent_interrupt(
            task_id=task_id, decision=decision, message=message,
            audit_ref=audit_ref,
        )

    def _classify_action_tool(self, action: dict[str, Any]) -> str:
        """Write class for a pending action's tool (audit spec §5.4) — a
        run_python(writes_artifacts=True) approval must land as artifact_write."""
        from .deep_agent.write_actions import classify_write_action, write_names_by_class

        return classify_write_action(
            str(action.get("tool_name") or ""),
            action.get("payload") or {},
            write_names_by_class(self.tools),
            include_page_action=False,
        ) or "domain_write"

    def resume_pending_action(
        self,
        *,
        thread_id: int,
        message_id: int,
        action_id: str,
        decision: str,
        actor: str,
        session: Session,
    ) -> AgentMessage:
        """Resolve a pending HITL action and return the resulting AgentMessage.

        Raises:
            ResumeValidationError  — invalid request (maps to HTTP 400/404)
            ResumeConflictError    — action already resolved (HTTP 409)
            ResumeAgentError       — agent invocation failed (HTTP 502/503)
            WorkflowResumeConflict — task-scoped conflict (HTTP 409)
        """
        source_message = (
            session.query(AgentMessage)
            .filter(AgentMessage.id == message_id)
            .one_or_none()
        )
        if source_message is None or source_message.role != "assistant":
            raise ResumeValidationError(
                "Only assistant action proposals can be resumed", status_hint=400
            )

        source_meta = deepcopy(source_message.meta or {})
        pending_actions = source_meta.get("pending_actions") or []
        action = next((a for a in pending_actions if a.get("id") == action_id), None)
        if action is None:
            raise ResumeValidationError("Pending action not found", status_hint=404)
        if action.get("status") != "pending":
            raise ResumeConflictError(f"Action already {action.get('status')}")

        # Audit spec §5.4: record the human decision BEFORE any resume/graph
        # invocation, in its own committed transaction — several resume
        # branches roll back on failure, and a failed approved-write must not
        # erase the approval that triggered it.
        try:
            with _database.SessionLocal() as decision_session:
                record_hitl_decision(
                    decision_session,
                    action=action,
                    decision="approved" if decision == "confirm" else "rejected",
                    actor=actor,
                    tool_class=self._classify_action_tool(action),
                    context={"thread_id": thread_id, "message_id": message_id},
                )
                decision_session.commit()
        except SQLAlchemyError:
            logger.exception(
                "audit decision row could not be persisted for action %s", action_id
            )

        # Async-agent bubble-up: the pending action's async_task_id field
        # tells us this came from a background subagent; route the resume to
        # the subagent's checkpointer thread_id instead of the parent thread.
        async_task_id = action.get("async_task_id")
        if isinstance(async_task_id, int):
            from .async_agents import TaskNotResumableError

            try:
                self.resume_async_agent(
                    async_task_id,
                    "approve" if decision == "confirm" else "reject",
                    "User dismissed the action." if decision == "dismiss" else None,
                    audit_ref=(
                        (action.get("source_meta") or {}).get("audit") or {}
                    ).get("audit_ref"),
                )
            except TaskNotResumableError as exc:
                raise ResumeConflictError(str(exc)) from exc
            new_status = "confirmed" if decision == "confirm" else "dismissed"
            _mark_pending_action_resolved(
                pending_actions,
                action_id=action_id,
                status=new_status,
            )
            source_message.meta = {**source_meta, "pending_actions": pending_actions}
            flag_modified(source_message, "meta")
            record_audit(
                session,
                event_type=(
                    "agent.action.confirmed"
                    if decision == "confirm"
                    else "agent.action.dismissed"
                ),
                actor=actor,
                subject_type="thread",
                subject_id=thread_id,
                payload={
                    "action_id": action_id,
                    "tool_name": action.get("tool_name"),
                    "async_task_id": async_task_id,
                },
            )
            return source_message

        action_source_meta = action.get("source_meta") or {}
        if (
            self.settings.feature_workflow_routing
            and isinstance(action_source_meta, dict)
            and action_source_meta.get("agent_runtime") == "deepagents"
            and action_source_meta.get("task_id") is not None
        ):
            cmd = build_resume_command(
                decision=("approve" if decision == "confirm" else "reject"),
                message=(
                    "User dismissed the action." if decision == "dismiss" else None
                ),
            )
            originating = source_meta.get("model_selection")
            yolo_mode = bool(source_meta.get("yolo_mode", False))
            fallback_used = False
            try:
                resolved = self.normalize_model_selection(originating)
            except ValueError:
                resolved = self.default_model_selection
                fallback_used = True
                logger.warning(
                    "Workflow HITL resume falling back to default; originating selection unresolvable: %r",
                    originating,
                )
            resume_decision = "approve" if decision == "confirm" else "reject"
            try:
                execution = self.invoke_workflow_resume(
                    session,
                    cmd,
                    source_meta=action_source_meta,
                    model_selection=resolved,
                    yolo_mode=yolo_mode,
                    decision=resume_decision,
                    actor=actor,
                )
            except WorkflowResumeConflict:
                raise
            except Exception as exc:
                session.rollback()
                logger.exception(
                    "Workflow resume failed for thread %s action %s",
                    thread_id,
                    action_id,
                )
                raise ResumeAgentError(f"Agent resume failed: {exc}", status_hint=502) from exc

            source_message = (
                session.query(AgentMessage).filter(AgentMessage.id == message_id).one()
            )
            source_meta = deepcopy(source_message.meta or {})
            pending_actions = source_meta.get("pending_actions") or []
            new_status = "confirmed" if decision == "confirm" else "dismissed"
            _mark_pending_action_resolved(
                pending_actions,
                action_id=action_id,
                status=new_status,
            )
            source_message.meta = {**source_meta, "pending_actions": pending_actions}
            flag_modified(source_message, "meta")

            task = session.get(AgentTask, execution.task_id)
            persona = task.assigned_persona if task is not None else "orchestrator"
            if execution.artifact is None:
                response_content = "Awaiting confirmation for the next step."
                agent_phase = "awaiting_confirmation"
                artifact_id = None
                pending = pending_actions_from_interrupts(
                    list(execution.interrupts or []),
                    persona=persona,
                    source_meta=self._task_source_meta(
                        execution,
                        envelope_final=execution.envelope,
                    ),
                )
            else:
                response_content = self._artifact_response_text(
                    execution.artifact.payload
                )
                agent_phase = "completed"
                artifact_id = execution.artifact.id
                pending = []

            new_msg = AgentMessage(
                thread_id=thread_id,
                workflow_id=execution.workflow_id,
                session_id=execution.session_id,
                role="assistant",
                character=persona,
                content=response_content,
                meta={
                    "agent_graph": "deepagents",
                    "agent_phase": agent_phase,
                    "workflow_routing": True,
                    "workflow_id": execution.workflow_id,
                    "task_id": execution.task_id,
                    "session_id": execution.session_id,
                    "context_pack_id": execution.context_pack_id,
                    "artifact_id": artifact_id,
                    "pending_actions": [
                        a.model_dump(mode="json") for a in pending
                    ],
                    "interrupt_ids": [
                        getattr(intr, "id", "")
                        for intr in list(execution.interrupts or [])
                    ],
                    "agent_enabled": True,
                    "model_selection": resolved,
                    "model_selection_fallback": fallback_used,
                    "yolo_mode": yolo_mode,
                    "envelope_final": execution.envelope,
                },
            )
            session.add(new_msg)
            thread = session.query(AgentThread).filter(AgentThread.id == thread_id).one()
            thread.character = persona or thread.character
            session.flush()
            if pending:
                # Audit spec §5.4: re-projected proposals get rows too, in the
                # same transaction as the card.
                record_hitl_proposals(
                    session,
                    new_msg.meta["pending_actions"],
                    tools=self.tools,
                    context={
                        "thread_id": thread_id,
                        "actor": actor,
                        "workflow_id": execution.workflow_id,
                        "session_id": execution.session_id,
                        "task_id": execution.task_id,
                        "message_id": new_msg.id,
                    },
                )

            record_audit(
                session,
                event_type=(
                    "agent.action.confirmed"
                    if decision == "confirm"
                    else "agent.action.dismissed"
                ),
                actor=actor,
                subject_type="thread",
                subject_id=thread_id,
                payload={
                    "action_id": action_id,
                    "tool_name": action.get("tool_name") or action.get("type"),
                    "workflow_routing": True,
                    "task_id": execution.task_id,
                    "session_id": execution.session_id,
                    "context_pack_id": execution.context_pack_id,
                    "model_selection": resolved,
                    "model_selection_fallback": fallback_used,
                    "yolo_mode": yolo_mode,
                },
            )
            return new_msg

        if (
            self.settings.feature_workflow_routing
            and isinstance(action_source_meta, dict)
            and action_source_meta.get("agent_runtime") == "deepagents_orchestrator"
            and action_source_meta.get("checkpointer_key")
            and action_source_meta.get("workflow_id") is not None
            and action_source_meta.get("session_id") is not None
        ):
            cmd = build_resume_command(
                decision=("approve" if decision == "confirm" else "reject"),
                message=(
                    "User dismissed the action." if decision == "dismiss" else None
                ),
            )
            originating = source_meta.get("model_selection")
            yolo_mode = bool(source_meta.get("yolo_mode", False))
            fallback_used = False
            try:
                resolved = self.normalize_model_selection(originating)
            except ValueError:
                resolved = self.default_model_selection
                fallback_used = True
                logger.warning(
                    "Orchestrator HITL resume falling back to default; originating selection unresolvable: %r",
                    originating,
                )

            agent = self._sync_agent_for_selection(
                resolved,
                yolo_mode=yolo_mode,
            )
            if agent is None:
                raise ResumeAgentError(
                    "Agent is disabled (no LLM configured)", status_hint=503
                )

            prior_envelope = action_source_meta.get("envelope_final")
            resume_envelope = (
                "desk_async" if prior_envelope == "desk_async" else "desk_workflow"
            )
            resume_extras = {
                "envelope": resume_envelope,
                "confirmed_cost_preview": True,
                "workflow_id": action_source_meta["workflow_id"],
                "session_id": action_source_meta["session_id"],
                "agent_runtime": "deepagents_orchestrator",
                # Audit spec §5.4: the approved execution row must carry the
                # proposal's audit_ref so the chain correlates.
                AUDIT_CONTEXT_KEY: {
                    "actor": actor,
                    "thread_id": thread_id,
                    "workflow_id": action_source_meta.get("workflow_id"),
                    "session_id": action_source_meta.get("session_id"),
                    "message_id": message_id,
                    "audit_ref": (action_source_meta.get("audit") or {}).get(
                        "audit_ref"
                    ),
                },
            }
            checkpointer_key = str(action_source_meta["checkpointer_key"])

            session.rollback()
            try:
                result = agent.invoke(
                    cmd,
                    config=graph_run_config(
                        self.settings,
                        thread_id=checkpointer_key,
                        configurable_extra=resume_extras,
                        trace_meta={
                            "thread_id": thread_id,
                            "workflow_id": action_source_meta["workflow_id"],
                        },
                    ),
                )
            except Exception as exc:
                session.rollback()
                logger.exception(
                    "Orchestrator resume failed for thread %s action %s",
                    thread_id,
                    action_id,
                )
                raise ResumeAgentError(
                    f"Agent resume failed: {exc}", status_hint=502
                ) from exc
            task_watch = _task_watch_from_result(session, result)

            source_message = (
                session.query(AgentMessage).filter(AgentMessage.id == message_id).one()
            )
            source_meta = deepcopy(source_message.meta or {})
            pending_actions = source_meta.get("pending_actions") or []
            new_status = "confirmed" if decision == "confirm" else "dismissed"
            _mark_pending_action_resolved(
                pending_actions,
                action_id=action_id,
                status=new_status,
                task_watch=task_watch,
            )
            source_message.meta = {**source_meta, "pending_actions": pending_actions}
            flag_modified(source_message, "meta")

            workflow_id = int(action_source_meta["workflow_id"])
            session_id = int(action_source_meta["session_id"])
            agent_session = session.get(AgentSession, session_id)
            if agent_session is None:
                raise ResumeConflictError(f"AgentSession {session_id} not found")
            thread = session.query(AgentThread).filter(AgentThread.id == thread_id).one()
            route = WorkspaceRouteDecision(
                kind=source_meta.get("router_decision") or "continue_workflow",
                workflow_id=workflow_id,
                session_id=session_id,
            )
            new_msg = self._persist_workflow_orchestrator_result(
                session,
                thread,
                result,
                assets=[],
                route=route,
                agent_session=agent_session,
                page_context=None,
                accounting_date=None,
                context_usage=None,
                model_selection=resolved,
                yolo_mode=yolo_mode,
                envelope_final=resume_envelope,
                include_interactive_affordances=False,
            )
            if fallback_used and isinstance(new_msg.meta, dict):
                new_msg.meta = {**new_msg.meta, "model_selection_fallback": True}
                flag_modified(new_msg, "meta")

            record_audit(
                session,
                event_type=(
                    "agent.action.confirmed"
                    if decision == "confirm"
                    else "agent.action.dismissed"
                ),
                actor=actor,
                subject_type="thread",
                subject_id=thread_id,
                payload={
                    "action_id": action_id,
                    "tool_name": action.get("tool_name") or action.get("type"),
                    "workflow_routing": True,
                    "workflow_id": workflow_id,
                    "session_id": session_id,
                    "model_selection": resolved,
                    "model_selection_fallback": fallback_used,
                    "yolo_mode": yolo_mode,
                    "agent_runtime": "deepagents_orchestrator",
                },
            )
            return new_msg

        cmd = build_resume_command(
            decision=("approve" if decision == "confirm" else "reject"),
            message=("User dismissed the action." if decision == "dismiss" else None),
        )

        # Resume against the originating message's model selection — not the default.
        # Falls back with audit if the selection is no longer resolvable in the
        # current registry (e.g., admin removed the model since the message was sent).
        originating = source_meta.get("model_selection")
        yolo_mode = bool(source_meta.get("yolo_mode", False))
        fallback_used = False
        try:
            resolved = self.normalize_model_selection(originating)
        except ValueError:
            resolved = self.default_model_selection
            fallback_used = True
            logger.warning(
                "HITL resume falling back to default; originating selection unresolvable: %r",
                originating,
            )

        agent = self._sync_agent_for_selection(
            resolved,
            yolo_mode=yolo_mode,
        )
        if agent is None:
            raise ResumeAgentError(
                "Agent is disabled (no LLM configured)", status_hint=503
            )

        # The resumed graph may execute tools that open their own app DB
        # sessions and write rows, e.g. run_batch_pricing -> queued run rows.
        # Release this request session's read transaction first so SQLite does
        # not block the tool write during the long-running agent invoke.
        session.rollback()

        # HITL paused BEFORE the capability gate ran on this tool, so
        # source_meta.envelope_final reflects the pre-gate envelope (often
        # pet_page for floating-pet writes). Always widen to at least
        # desk_workflow so the approved write actually runs. Preserve
        # desk_async if the original turn was already async so HITL
        # callbacks don't downgrade async dispatch authority.
        prior_envelope = source_meta.get("envelope_final")
        if prior_envelope == "desk_async":
            resume_envelope = "desk_async"
        else:
            resume_envelope = "desk_workflow"

        # HITL approval is itself the cost-preview confirmation step (the
        # user explicitly said yes to this action). Pre-confirm so the gate
        # doesn't ask again with a structured cost_preview_required event
        # the /confirm endpoint can't surface. Without this, large
        # run_batch_pricing approvals would 502.
        resume_extras = {
            "envelope": resume_envelope,
            "confirmed_cost_preview": True,
            # Audit spec §5.4: the approved execution row must carry the
            # proposal's audit_ref so the chain correlates.
            AUDIT_CONTEXT_KEY: {
                "actor": actor,
                "thread_id": thread_id,
                "message_id": message_id,
                "audit_ref": (
                    (action.get("source_meta") or {}).get("audit") or {}
                ).get("audit_ref"),
            },
        }
        try:
            result = agent.invoke(
                cmd,
                config=graph_run_config(
                    self.settings,
                    thread_id=thread_id,
                    configurable_extra=resume_extras,
                    trace_meta={"thread_id": thread_id},
                ),
            )
        except Exception as exc:
            session.rollback()
            logger.exception(
                "Resume failed for thread %s action %s", thread_id, action_id
            )
            raise ResumeAgentError(
                f"Agent resume failed: {exc}", status_hint=502
            ) from exc
        task_watch = _task_watch_from_result(session, result)

        source_message = (
            session.query(AgentMessage).filter(AgentMessage.id == message_id).one()
        )
        new_status = "confirmed" if decision == "confirm" else "dismissed"
        _mark_pending_action_resolved(
            pending_actions,
            action_id=action_id,
            status=new_status,
            task_watch=task_watch,
        )
        source_message.meta = {**source_meta, "pending_actions": pending_actions}
        flag_modified(source_message, "meta")

        record_audit(
            session,
            event_type=(
                "agent.action.confirmed"
                if decision == "confirm"
                else "agent.action.dismissed"
            ),
            actor=actor,
            subject_type="thread",
            subject_id=thread_id,
            payload={
                "action_id": action_id,
                "tool_name": action.get("tool_name") or action.get("type"),
                "model_selection": resolved,
                "model_selection_fallback": fallback_used,
                "yolo_mode": yolo_mode,
            },
        )

        thread = session.query(AgentThread).filter(AgentThread.id == thread_id).one()
        new_msg = self._persist_agent_result(
            session,
            thread,
            result,
            assets=[],
            page_context=None,
            model_selection=resolved,
            yolo_mode=yolo_mode,
            include_interactive_affordances=False,
            actor=actor,
        )
        if fallback_used and isinstance(new_msg.meta, dict):
            new_msg.meta = {**new_msg.meta, "model_selection_fallback": True}
            flag_modified(new_msg, "meta")
        return new_msg

    def invoke_resume(
        self,
        command: Any,
        *,
        thread_id: int,
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
        envelope: str | None = None,
    ) -> Any:
        resolved = self.normalize_model_selection(model_selection)
        agent = self._sync_agent_for_selection(resolved, yolo_mode=yolo_mode)
        if agent is None:
            raise RuntimeError("Agent is disabled (no LLM configured)")
        # A HITL resume runs after the user approved an action; default to
        # desk_workflow if no envelope was passed so the gate doesn't block
        # the very write the user just approved.
        env = envelope or "desk_workflow"
        return agent.invoke(
            command,
            config=graph_run_config(
                self.settings,
                thread_id=thread_id,
                configurable_extra={"envelope": env},
            ),
        )

    def invoke_workflow_resume(
        self,
        session: Session,
        command: Any,
        *,
        source_meta: dict[str, Any],
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
        decision: str,
        actor: str = "desk_user",
    ) -> TaskExecutionResult:
        task_id = int(source_meta["task_id"])
        workflow_id = int(source_meta["workflow_id"])
        source_session_id = int(source_meta["session_id"])
        context_pack_id = int(source_meta["context_pack_id"])
        checkpointer_key = str(source_meta["checkpointer_key"])
        task = session.get(AgentTask, task_id)
        if task is None:
            raise WorkflowResumeConflict(f"AgentTask {task_id} not found")
        if task.status != "awaiting_hitl":
            raise WorkflowResumeConflict(
                f"AgentTask {task_id} is {task.status}, not awaiting_hitl"
            )
        if task.workflow_id != workflow_id:
            raise WorkflowResumeConflict(
                f"AgentTask {task_id} belongs to workflow {task.workflow_id}, not {workflow_id}"
            )
        if task.assigned_session_id != source_session_id:
            raise WorkflowResumeConflict(
                f"AgentTask {task_id} session changed from {source_session_id}"
            )
        if task.context_pack_id != context_pack_id:
            raise WorkflowResumeConflict(
                f"AgentTask {task_id} context pack changed from {context_pack_id}"
            )

        resolved = self.normalize_model_selection(model_selection)
        agent = self._sync_agent_for_selection(resolved, yolo_mode=yolo_mode)
        if agent is None:
            raise RuntimeError("Agent is disabled (no LLM configured)")

        agent_session = acquire_session_lease(session, task=task)
        if agent_session.id != source_session_id:
            release_session_lease(
                session,
                session_id=agent_session.id,
                task_id=task.id,
            )
            raise WorkflowResumeConflict(
                f"AgentTask {task_id} resumed session {agent_session.id}, not {source_session_id}"
            )

        task.status = "in_progress"
        writer = LedgerWriter(session)
        hitl_event = "hitl_approved" if decision == "approve" else "hitl_rejected"
        writer.emit_event(
            workflow_id=workflow_id,
            session_id=source_session_id,
            task_id=task.id,
            kind=hitl_event,
            payload={
                "task_id": task.id,
                "session_id": source_session_id,
                "context_pack_id": context_pack_id,
                "decision": decision,
            },
            actor=actor,
        )
        writer.emit_event(
            workflow_id=workflow_id,
            session_id=source_session_id,
            task_id=task.id,
            kind="task_resumed",
            payload={
                "task_id": task.id,
                "session_id": source_session_id,
                "context_pack_id": context_pack_id,
                "source": "hitl",
            },
        )
        session.flush()

        envelope = self._resume_envelope_from_source_meta(source_meta)
        registration = task_registration(task.task_type)
        config = graph_run_config(
            self.settings,
            thread_id=checkpointer_key,
            configurable_extra={
                "workflow_id": workflow_id,
                "session_id": source_session_id,
                "task_id": task.id,
                "context_pack_id": context_pack_id,
                "envelope": envelope,
                "confirmed_cost_preview": True,
                "tools_scope": sorted(registration.tools_scope),
            },
        )
        try:
            result = agent.invoke(command, config=config)
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)[:1000]
            task.closed_at = datetime.utcnow()
            release_session_lease(
                session,
                session_id=source_session_id,
                task_id=task.id,
                close_reason="task_failed",
                last_summary=task.error,
            )
            writer.emit_event(
                workflow_id=workflow_id,
                session_id=source_session_id,
                task_id=task.id,
                kind="task_failed",
                payload={
                    "task_id": task.id,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                },
            )
            raise

        if TaskExecutor._has_interrupt(result):
            task.status = "awaiting_hitl"
            release_session_lease(
                session,
                session_id=source_session_id,
                task_id=task.id,
            )
            writer.emit_event(
                workflow_id=workflow_id,
                session_id=source_session_id,
                task_id=task.id,
                kind="hitl_requested",
                payload={
                    "task_id": task.id,
                    "session_id": source_session_id,
                    "context_pack_id": context_pack_id,
                },
            )
            return TaskExecutionResult(
                task_id=task.id,
                workflow_id=workflow_id,
                session_id=source_session_id,
                context_pack_id=context_pack_id,
                checkpointer_key=checkpointer_key,
                envelope=envelope,
                raw_result=result,
                artifact=None,
                interrupts=list(result.get("__interrupt__") or []),
            )

        artifact_payload = TaskExecutor._artifact_payload(result)
        artifact_kind = TaskExecutor._artifact_kind_for(
            task=task,
            default_kind=registration.output_artifact_kind,
            payload=artifact_payload,
        )
        artifact = writer.write_artifact(
            workflow_id=workflow_id,
            session_id=source_session_id,
            task_id=task.id,
            context_pack_id=context_pack_id,
            kind=artifact_kind,
            title=f"{task.task_type} output",
            payload=artifact_payload,
        )
        if artifact.kind == "plan" and isinstance(artifact.payload.get("tasks"), list):
            schedule_tasks_from_plan(
                session,
                planner_task_id=task.id,
                plan_artifact_id=artifact.id,
            )
        task.status = "completed"
        task.closed_at = datetime.utcnow()
        release_session_lease(
            session,
            session_id=source_session_id,
            task_id=task.id,
            close_reason="return_to_orchestrator",
            last_summary=f"completed task {task.id}",
        )
        writer.emit_event(
            workflow_id=workflow_id,
            session_id=source_session_id,
            task_id=task.id,
            artifact_id=artifact.id,
            kind="task_completed",
            payload={
                "task_id": task.id,
                "artifact_id": artifact.id,
                "context_pack_id": context_pack_id,
            },
        )
        return TaskExecutionResult(
            task_id=task.id,
            workflow_id=workflow_id,
            session_id=source_session_id,
            context_pack_id=context_pack_id,
            checkpointer_key=checkpointer_key,
            envelope=envelope,
            raw_result=result,
            artifact=artifact,
            interrupts=[],
        )

    @staticmethod
    def _resume_envelope_from_source_meta(source_meta: dict[str, Any]) -> str:
        return (
            "desk_async"
            if source_meta.get("envelope_final") == "desk_async"
            else "desk_workflow"
        )

    @staticmethod
    def _task_source_meta(
        execution: TaskExecutionResult,
        *,
        envelope_final: str,
    ) -> dict[str, Any]:
        return {
            "task_id": execution.task_id,
            "session_id": execution.session_id,
            "context_pack_id": execution.context_pack_id,
            "checkpointer_key": execution.checkpointer_key,
            "workflow_id": execution.workflow_id,
            "envelope_final": envelope_final,
            "agent_runtime": "deepagents",
        }

    def _needs_async_sqlite_streaming_agent(self) -> bool:
        if self.deep_agent is not self._owned_deep_agent:
            return False
        if self.model is None or self.checkpointer is None:
            return False
        checkpointer_type = type(self.checkpointer)
        return (
            checkpointer_type.__name__ == "SqliteSaver"
            and checkpointer_type.__module__.startswith("langgraph.checkpoint.sqlite")
        )

    async def _read_stream_state(self, agent: Any, config: dict) -> Any:
        aget_state = getattr(agent, "aget_state", None)
        if callable(aget_state):
            return await aget_state(config)  # type: ignore[no-any-return]
        get_state = getattr(agent, "get_state")
        return get_state(config)

    async def _finalize_turn(
        self,
        agent: Any,
        config: dict,
        thread_id: int,
        collector: StreamCollector,
        assets: list[AgentAssetOut],
        page_context: AgentPageContext | None,
        model_selection: dict[str, str] | None = None,
        accounting_date: date | str | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        yolo_mode: bool = False,
        actor: str = "desk_user",
    ) -> int | None:
        """Read interrupts/personas from state, then persist a single AgentMessage."""
        assets = _merge_assets(
            assets,
            _agent_file_assets_from_state(
                collector.artifact_files,
                artifact_dir=self.settings.artifact_dir,
                thread_id=thread_id,
            ),
        )
        state_final_text = ""
        try:
            state = (
                await self._read_stream_state(agent, config)
                if agent is not None
                else None
            )
            values = getattr(state, "values", None) or {}
            if state and state.tasks:
                for task in state.tasks:
                    collector.interrupts.extend(getattr(task, "interrupts", []) or [])
            self._extract_personas_from_state(state, collector)
            collector.set_todos(_normalize_todos(values.get("todos")))
            state_final_text = _extract_final_ai_text(values)
            if not collector.final_text and collector.error is None:
                if state_final_text:
                    collector.on_token(state_final_text)
            assets = _merge_assets(
                assets,
                _agent_file_assets_from_state(
                    values.get("files"),
                    artifact_dir=self.settings.artifact_dir,
                    thread_id=thread_id,
                ),
            )
        except Exception:
            logger.exception("get_state failed for thread %s", thread_id)

        try:
            return await asyncio.to_thread(
                self._persist_from_collector,
                thread_id,
                collector,
                assets,
                page_context,
                model_selection,
                accounting_date,
                context_usage,
                yolo_mode,
                state_final_text,
                actor,
            )
        except Exception:
            logger.exception("Persist failed for thread %s", thread_id)
            return None

    async def _drive_stream(
        self,
        agent: Any,
        prompt: str,
        config: dict,
        collector: StreamCollector,
        *,
        stream_version: str | None = None,
        extra_state: dict | None = None,
    ):
        """Race astream_events against a 15s timeout to emit heartbeat events.

        ``extra_state`` is merged into the invocation state — goal mode passes the
        ``{"rubric": ...}`` fragment here so the attached grader has its criteria."""
        queue: asyncio.Queue = asyncio.Queue()
        DONE = object()
        control = new_run_control()
        payload: dict[str, Any] = {"messages": [HumanMessage(content=prompt)]}
        if extra_state:
            payload.update(extra_state)
        version = stream_version or self.settings.agent_stream_version

        async def producer():
            try:
                if version == "v3":
                    try:
                        stream = agent.astream_events(
                            payload,
                            config=config,
                            version="v3",
                            control=control,
                        )
                    except TypeError as exc:
                        if "control" not in str(exc):
                            raise
                        stream = agent.astream_events(
                            payload,
                            config=config,
                            version="v3",
                        )
                    run = await stream if inspect.isawaitable(stream) else stream
                    if hasattr(run, "__aenter__"):
                        async with run:
                            async for ev in run:
                                await queue.put(ev)
                    else:
                        async for ev in run:
                            await queue.put(ev)
                else:
                    async for ev in agent.astream_events(
                        payload,
                        config=config,
                        version="v2",
                    ):
                        await queue.put(ev)
            except GraphDrained as exc:
                collector.drained = True
                collector.drain_reason = getattr(exc, "reason", None) or str(exc)
            finally:
                await queue.put(DONE)

        task = asyncio.create_task(producer())
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse("heartbeat", {})
                    continue
                if ev is DONE:
                    # Producer finished: re-raise any exception it captured so
                    # stream_and_persist can emit `event: error` and persist
                    # whatever text was streamed before the failure.
                    if task.done():
                        try:
                            exc = task.exception()
                        except asyncio.CancelledError:
                            return
                        if exc is not None:
                            raise exc
                    return
                sse_line = self._handle_event(ev, collector)
                if sse_line:
                    yield sse_line
        finally:
            if not task.done():
                request_drain(control, "stream_closed")
                try:
                    await asyncio.wait_for(task, timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()
                except Exception:
                    pass
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _handle_event(self, ev: dict, collector: StreamCollector) -> str | None:
        if ev.get("method"):
            return self._handle_v3_event(ev, collector)
        return self._handle_v2_event(ev, collector)

    def _handle_v2_event(self, ev: dict, collector: StreamCollector) -> str | None:
        kind = ev.get("event")
        run_id = ev.get("run_id") or ""
        name = ev.get("name", "")
        data = ev.get("data") or {}

        if kind == "on_custom_event" and isinstance(data, dict) and data.get("type") == "subagent":
            # Dynamic-subagents fan-out lifecycle events on the v2 stream (e.g. a
            # DeepSeek-forced-v2 run), mirroring the v3 custom branch.
            return _subagent_sse_line(data, collector)

        if kind == "on_tool_start":
            args = data.get("input") or {}
            if name == "propose_reply_options" and isinstance(args, dict):
                raw_options = args.get("options")
                if isinstance(raw_options, list):
                    collector.reply_options_args[run_id] = raw_options
            if name == "propose_term_form" and isinstance(args, dict):
                collector.term_form_args[run_id] = args
            todos = _todos_from_tool_args(args) if name == "write_todos" else None
            collector.set_todos(todos)
            collector.on_tool_start(run_id, name, args, time.monotonic())
            payload: dict = {"id": run_id, "name": name}
            if args:
                payload["args"] = _truncate(args)
            sse = _sse("tool_start", payload)
            if todos is not None:
                sse += _sse("todo_update", {"todos": todos})
            return sse

        if kind == "on_tool_end":
            output = data.get("output")
            error_text = _extract_tool_error(data, output)
            collector.on_tool_end(
                run_id,
                None if error_text else output,
                time.monotonic(),
                error=error_text,
            )
            if not error_text:
                collector.add_artifact_files(_tool_artifact_files_from_output(output))
            _capture_reply_options_from_tool_end(
                collector, run_id=run_id, name=name, error_text=error_text
            )
            _capture_term_form_from_tool_end(
                collector, run_id=run_id, name=name, error_text=error_text
            )
            ev_data = collector.tool_events.get(run_id, {})
            payload = {"id": run_id, "duration_ms": ev_data.get("duration_ms", 0)}
            if error_text:
                payload["error"] = error_text
            elif output is not None:
                payload["output"] = _truncate(output)
            return _sse("tool_end", payload)

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            text = getattr(chunk, "content", None) if chunk is not None else None
            if isinstance(text, str) and text:
                collector.on_token(text)
                return _sse("token", {"text": text})

        return None

    def _handle_v3_event(self, ev: dict, collector: StreamCollector) -> str | None:
        method = ev.get("method")
        params = ev.get("params") or {}
        data = params.get("data")

        if method == "custom":
            # Dynamic-subagents fan-out lifecycle events ride the LangGraph custom
            # stream. The payload is the event dict (start/complete/error).
            payload = data if isinstance(data, dict) else params
            if isinstance(payload, dict) and payload.get("type") == "subagent":
                return _subagent_sse_line(payload, collector)
            return None

        if method == "messages":
            payload = data[0] if isinstance(data, (tuple, list)) and data else data
            if isinstance(payload, dict):
                event = payload.get("event")
                if event == "content-block-delta":
                    delta = payload.get("delta") or {}
                    if delta.get("type") == "text-delta":
                        text = delta.get("text")
                        if isinstance(text, str) and text:
                            collector.on_token(text)
                            return _sse("token", {"text": text})
                return None
            if getattr(payload, "type", None) == "ai":
                text = _message_content_to_text(getattr(payload, "content", ""))
                if text:
                    collector.on_token(text)
                    return _sse("token", {"text": text})
            return None

        if method != "tools" or not isinstance(data, dict):
            return None

        event = data.get("event")
        run_id = str(data.get("tool_call_id") or "")
        name = str(data.get("tool_name") or "")

        if event == "tool-started":
            args = data.get("input") or {}
            if name == "propose_reply_options" and isinstance(args, dict):
                raw_options = args.get("options")
                if isinstance(raw_options, list):
                    collector.reply_options_args[run_id] = raw_options
            if name == "propose_term_form" and isinstance(args, dict):
                collector.term_form_args[run_id] = args
            todos = _todos_from_tool_args(args) if name == "write_todos" else None
            collector.set_todos(todos)
            collector.on_tool_start(run_id, name, args, time.monotonic())
            payload: dict = {"id": run_id, "name": name}
            if args:
                payload["args"] = _truncate(args)
            sse = _sse("tool_start", payload)
            if todos is not None:
                sse += _sse("todo_update", {"todos": todos})
            return sse

        if event in {"tool-finished", "tool-error"}:
            output = data.get("output")
            error_text = None
            if event == "tool-error":
                error_text = str(data.get("message") or "tool error")[:500]
            else:
                error_text = _extract_tool_error(data, output)
            if not name:
                name = str(collector.tool_events.get(run_id, {}).get("name") or "")
            collector.on_tool_end(
                run_id,
                None if error_text else output,
                time.monotonic(),
                error=error_text,
            )
            if not error_text:
                collector.add_artifact_files(_tool_artifact_files_from_output(output))
            _capture_reply_options_from_tool_end(
                collector, run_id=run_id, name=name, error_text=error_text
            )
            _capture_term_form_from_tool_end(
                collector, run_id=run_id, name=name, error_text=error_text
            )
            ev_data = collector.tool_events.get(run_id, {})
            payload = {"id": run_id, "duration_ms": ev_data.get("duration_ms", 0)}
            if error_text:
                payload["error"] = error_text
            elif output is not None:
                payload["output"] = _truncate(output)
            return _sse("tool_end", payload)

        return None

    def _extract_personas_from_state(
        self, state: Any, collector: StreamCollector
    ) -> None:
        """Walk state.values['messages'] for task(name=...) tool calls."""
        if state is None:
            return
        values = getattr(state, "values", None) or {}
        messages = values.get("messages") or []
        for message in messages:
            for tool_call in getattr(message, "tool_calls", None) or []:
                if tool_call.get("name") == "task":
                    args = tool_call.get("args") or {}
                    name = args.get("subagent_type") or args.get("name")
                    if isinstance(name, str):
                        collector.note_persona(name)

    def _persist_from_collector(
        self,
        thread_id: int,
        collector: StreamCollector,
        assets: list[AgentAssetOut],
        page_context: AgentPageContext | None,
        model_selection: dict[str, str] | None = None,
        accounting_date: date | str | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        yolo_mode: bool = False,
        state_final_text: str | None = None,
        actor: str = "desk_user",
    ) -> int | None:
        resolved = self.normalize_model_selection(model_selection)
        effective_accounting_date = _effective_accounting_date(accounting_date)
        with _database.SessionLocal() as session:
            thread = session.get(AgentThread, thread_id)
            if thread is None:
                return None
            ensure_thread_workflow_state(session, thread_id)
            last_persona = (
                collector.personas_invoked[-1] if collector.personas_invoked else None
            )

            if collector.interrupts:
                pending = pending_actions_from_interrupts(
                    collector.interrupts, persona=last_persona
                )
                assistant_msg = AgentMessage(
                    thread_id=thread_id,
                    role="assistant",
                    character=last_persona,
                    content=_select_public_stream_response(
                        stream_text=collector.final_text,
                        state_final_text=state_final_text,
                        fallback_text="Awaiting confirmation for the next step.",
                    ),
                    meta={
                        "agent_graph": "deepagents",
                        "agent_phase": "awaiting_confirmation",
                        "pending_actions": [a.model_dump(mode="json") for a in pending],
                        "interrupt_ids": [intr.id for intr in collector.interrupts],
                        "personas_invoked": collector.personas_invoked,
                        "process_events": collector.process_events,
                        "todos": collector.todos,
                        "assets": [asset.model_dump(mode="json") for asset in assets],
                        "context_used": (
                            page_context.model_dump(mode="json")
                            if page_context
                            else None
                        ),
                        "context_usage": _context_usage_meta(context_usage),
                        "accounting_date": effective_accounting_date.isoformat(),
                        "agent_enabled": True,
                        "model_selection": resolved,
                        "yolo_mode": yolo_mode,
                        "envelope_initial": collector.envelope_initial,
                        "envelope_final": collector.envelope_final,
                        "envelope_transitioned": collector.envelope_transitioned,
                        "cost_preview": collector.cost_preview,
                        **(
                            {"reply_options": collector.reply_options}
                            if collector.reply_options
                            else {}
                        ),
                        **(
                            {"term_form": collector.term_form}
                            if collector.term_form
                            else {}
                        ),
                    },
                )
            else:
                agent_phase = _collector_completion_phase(collector)
                tool_error_text = _collector_tool_error_text(collector)
                transport_recovery_text = _collector_transport_recovery_text(
                    collector
                )
                recovered_text = "\n\n".join(
                    part
                    for part in (collector.final_text, transport_recovery_text)
                    if part
                )
                state_recovered_text = "\n\n".join(
                    part
                    for part in (state_final_text, transport_recovery_text)
                    if part
                )
                content = (
                    recovered_text
                    or collector.error
                    or tool_error_text
                    or (
                        "Run paused before completion."
                        if collector.drained
                        else "(no response)"
                    )
                )
                assistant_msg = AgentMessage(
                    thread_id=thread_id,
                    role="assistant",
                    character=last_persona,
                    content=(
                        content
                        if collector.error
                        else _select_public_stream_response(
                            stream_text=content,
                            state_final_text=state_recovered_text,
                        )
                    ),
                    meta={
                        "agent_graph": "deepagents",
                        "agent_phase": agent_phase,
                        "pending_actions": [],
                        "personas_invoked": collector.personas_invoked,
                        "process_events": collector.process_events,
                        "todos": collector.todos,
                        "assets": [asset.model_dump(mode="json") for asset in assets],
                        "context_used": (
                            page_context.model_dump(mode="json")
                            if page_context
                            else None
                        ),
                        "context_usage": _context_usage_meta(context_usage),
                        "accounting_date": effective_accounting_date.isoformat(),
                        "error": collector.error,
                        "agent_enabled": True,
                        "model_selection": resolved,
                        "yolo_mode": yolo_mode,
                        "envelope_initial": collector.envelope_initial,
                        "envelope_final": collector.envelope_final,
                        "envelope_transitioned": collector.envelope_transitioned,
                        "cost_preview": collector.cost_preview,
                        "drained": collector.drained,
                        "drain_reason": collector.drain_reason,
                        **(
                            {"reply_options": collector.reply_options}
                            if collector.reply_options
                            else {}
                        ),
                        **(
                            {"term_form": collector.term_form}
                            if collector.term_form
                            else {}
                        ),
                    },
                )
            session.add(assistant_msg)
            thread.character = last_persona or thread.character
            session.flush()
            if collector.interrupts:
                # Audit spec §5.4: proposal rows commit atomically with the card.
                record_hitl_proposals(
                    session,
                    assistant_msg.meta["pending_actions"],
                    tools=self.tools,
                    context={
                        "thread_id": thread_id,
                        "actor": actor,
                        "message_id": assistant_msg.id,
                    },
                )
            session.commit()
            record_audit(
                session,
                event_type="chat.message",
                actor=actor,
                subject_type="thread",
                subject_id=thread_id,
                payload={
                    "personas_invoked": collector.personas_invoked,
                    "streamed": True,
                    "yolo_mode": yolo_mode,
                },
            )
            session.commit()
            return assistant_msg.id

    def _persist_agent_result(
        self,
        session: Session,
        thread: AgentThread,
        result: Any,
        assets: list[AgentAssetOut],
        page_context: AgentPageContext | None,
        accounting_date: date | str | None = None,
        context_usage: AgentContextUsage | dict[str, Any] | None = None,
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
        include_interactive_affordances: bool = True,
        actor: str = "desk_user",
    ) -> AgentMessage:
        resolved = self.normalize_model_selection(model_selection)
        effective_accounting_date = _effective_accounting_date(accounting_date)
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        personas = _personas_invoked(result)
        last_persona = personas[-1] if personas else None
        reply_options = (
            _reply_options_from_result(result)
            if include_interactive_affordances
            else None
        )
        term_form = (
            _term_form_from_result(result)
            if include_interactive_affordances
            else None
        )
        assets = _merge_assets(
            assets,
            _agent_file_assets_from_state(
                result.get("files") if isinstance(result, dict) else None,
                artifact_dir=self.settings.artifact_dir,
                thread_id=thread.id,
            ),
            _agent_file_assets_from_state(
                _tool_artifact_files_from_result(result),
                artifact_dir=self.settings.artifact_dir,
                thread_id=thread.id,
            ),
        )

        if interrupts:
            pending = pending_actions_from_interrupts(
                list(interrupts), persona=last_persona
            )
            interim_text = (
                _extract_final_ai_text(result)
                or "Awaiting confirmation for the next step."
            )
            assistant_msg = AgentMessage(
                thread_id=thread.id,
                role="assistant",
                character=last_persona,
                content=interim_text,
                meta={
                    "agent_graph": "deepagents",
                    "agent_phase": "awaiting_confirmation",
                    "pending_actions": [a.model_dump(mode="json") for a in pending],
                    "interrupt_ids": [intr.id for intr in interrupts],
                    "personas_invoked": personas,
                    "assets": [asset.model_dump(mode="json") for asset in assets],
                    "context_used": (
                        page_context.model_dump(mode="json") if page_context else None
                    ),
                    "context_usage": _context_usage_meta(context_usage),
                    "accounting_date": effective_accounting_date.isoformat(),
                    "agent_enabled": True,
                    "model_selection": resolved,
                    "yolo_mode": yolo_mode,
                    **({"reply_options": reply_options} if reply_options else {}),
                    **({"term_form": term_form} if term_form else {}),
                },
            )
            session.add(assistant_msg)
            thread.character = last_persona or thread.character
            session.flush()
            # Audit spec §5.4: proposal rows commit atomically with the card.
            record_hitl_proposals(
                session,
                assistant_msg.meta["pending_actions"],
                tools=self.tools,
                context={
                    "thread_id": thread.id,
                    "message_id": assistant_msg.id,
                },
            )
            return assistant_msg

        final_text = _extract_final_ai_text(result) or "(no response)"
        assistant_msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character=last_persona,
            content=final_text,
            meta={
                "agent_graph": "deepagents",
                "agent_phase": "completed",
                "pending_actions": [],
                "personas_invoked": personas,
                "assets": [asset.model_dump(mode="json") for asset in assets],
                "context_used": (
                    page_context.model_dump(mode="json") if page_context else None
                ),
                "context_usage": _context_usage_meta(context_usage),
                "accounting_date": effective_accounting_date.isoformat(),
                "agent_enabled": True,
                "model_selection": resolved,
                "yolo_mode": yolo_mode,
                **({"reply_options": reply_options} if reply_options else {}),
                **({"term_form": term_form} if term_form else {}),
            },
        )
        session.add(assistant_msg)
        thread.character = last_persona or thread.character
        session.flush()
        record_audit(
            session,
            event_type="chat.message",
            actor=actor,
            subject_type="thread",
            subject_id=thread.id,
            payload={"personas_invoked": personas},
        )
        return assistant_msg

    def _persist_disabled_response(
        self,
        session: Session,
        thread: AgentThread,
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
    ) -> AgentMessage:
        resolved = self.normalize_model_selection(model_selection)
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character=None,
            content=_DISABLED_RESPONSE,
            meta={
                "agent_graph": "disabled",
                "agent_phase": "completed",
                "agent_enabled": False,
                "pending_actions": [],
                "model_selection": resolved,
                "yolo_mode": yolo_mode,
            },
        )
        session.add(msg)
        session.flush()
        return msg

    def _persist_disabled_response_by_thread(
        self,
        thread_id: int,
        model_selection: dict[str, str] | None = None,
        yolo_mode: bool = False,
    ) -> int | None:
        with _database.SessionLocal() as session:
            thread = session.get(AgentThread, thread_id)
            if thread is None:
                return None
            ensure_thread_workflow_state(session, thread_id)
            msg = self._persist_disabled_response(
                session,
                thread,
                model_selection=model_selection,
                yolo_mode=yolo_mode,
            )
            session.commit()
            return msg.id

    def _context(
        self,
        session: Session,
        page_context: AgentPageContext | None,
        accounting_date: date | str | None = None,
        thread_id: int | None = None,
    ) -> dict[str, Any]:
        portfolio_id = _entity_int(page_context, "portfolio_id")
        portfolio = (
            session.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
            if portfolio_id
            else None
        )
        page_summary = _page_summary(page_context)
        accounting = _accounting_context(accounting_date)
        current_page_context = (
            page_context.model_dump(mode="json") if page_context else None
        )
        recent_messages = _recent_thread_messages(session, thread_id)
        if not portfolio:
            return {
                "accounting_context": accounting,
                "current_page_context": current_page_context,
                "recent_thread_messages": recent_messages,
                "page_summary": page_summary,
            }
        return {
            "accounting_context": accounting,
            "current_page_context": current_page_context,
            "recent_thread_messages": recent_messages,
            "portfolio_summary": _lightweight_portfolio_summary(
                session, portfolio, page_context
            ),
            "page_summary": page_summary,
        }

    def _context_assets(
        self, page_context: AgentPageContext | None
    ) -> list[AgentAssetOut]:
        if not page_context:
            return []
        assets = [
            AgentAssetOut(
                id="current-page-context",
                kind="json",
                title="Current page context",
                data=page_context.model_dump(mode="json"),
            )
        ]
        latest_run = (
            page_context.snapshot.get("latest_price_run")
            if isinstance(page_context.snapshot, dict)
            else None
        )
        if isinstance(latest_run, dict) and isinstance(latest_run.get("results"), list):
            assets.append(
                AgentAssetOut(
                    id="latest-pricing-results",
                    kind="table",
                    title="Latest pricing results",
                    data={
                        "columns": [
                            "source_trade_id",
                            "ok",
                            "price",
                            "market_value",
                            "pnl",
                            "error",
                        ],
                        "rows": latest_run["results"][:12],
                    },
                )
            )
        risk = (
            page_context.snapshot.get("risk")
            if isinstance(page_context.snapshot, dict)
            else None
        )
        if isinstance(risk, dict) and isinstance(risk.get("positions"), list):
            assets.append(
                AgentAssetOut(
                    id="risk-positions",
                    kind="table",
                    title="Risk positions",
                    data={
                        "columns": [
                            "position_id",
                            "underlying",
                            "product_type",
                            "quantity",
                            "market_value",
                            "pnl",
                        ],
                        "rows": risk["positions"][:12],
                    },
                )
            )
        return assets


def _recent_thread_messages(
    session: Session,
    thread_id: int | None,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if thread_id is None:
        return []
    rows = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread_id)
        .order_by(AgentMessage.id.desc())
        .limit(limit)
        .all()
    )
    messages: list[dict[str, Any]] = []
    for row in reversed(rows):
        meta = row.meta or {}
        raw_assets = meta.get("assets") if isinstance(meta, dict) else None
        assets = raw_assets if isinstance(raw_assets, list) else []
        messages.append(
            {
                "id": row.id,
                "role": row.role,
                "character": row.character,
                "content": row.content[:1500],
                "created_at": row.created_at.isoformat()
                if row.created_at is not None
                else None,
                "assets": [
                    {
                        "title": asset.get("title"),
                        "kind": asset.get("kind"),
                        "path": asset.get("path"),
                        "url": asset.get("url"),
                    }
                    for asset in assets[:5]
                    if isinstance(asset, dict)
                ],
            }
        )
    return messages


def search_memories(session, scopes=None):
    """Load injectable long-term-memory facts for the given scopes.

    Rewritten as the memory loader (the legacy namespace-keyword helper was
    orphaned). Defaults to the always-on read scopes. See deep_agent.memory.
    """
    from .deep_agent.memory.config import get_memory_config
    from .deep_agent.memory.scope import active_read_scopes
    from .deep_agent.memory.store import MemoryStore

    if scopes is None:
        scopes = active_read_scopes(None)
    return MemoryStore(get_memory_config()).load_injectable(session, scopes)


def _page_summary(page_context: AgentPageContext | None) -> str:
    if not page_context:
        return ""
    chips = ", ".join(page_context.chips[:5])
    if chips:
        return f" Current page context loaded: {chips}. "
    return f" Current page context loaded: {page_context.title}. "


def _entity_int(page_context: AgentPageContext | None, key: str) -> int | None:
    if not page_context:
        return None
    value = page_context.entity_ids.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _effective_accounting_date(value: date | str | None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            pass
    return datetime.utcnow().date()


def _context_usage_meta(
    context_usage: AgentContextUsage | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if context_usage is None:
        return None
    if isinstance(context_usage, AgentContextUsage):
        return context_usage.model_dump(mode="json")
    return context_usage


def _accounting_context(value: date | str | None) -> dict[str, Any]:
    effective = _effective_accounting_date(value)
    return {
        "accounting_date": effective.isoformat(),
        "relative_date_anchor": effective.isoformat(),
        "date_semantics": (
            "Use accounting_date, not wall-clock today, for relative business-date questions. "
            "For position inventory questions such as 'new added positions in the last N days', "
            "use trade_effective_date filters. Do not confuse accounting_date with pricing valuation_date."
        ),
    }


def _lightweight_portfolio_summary(
    session: Session,
    portfolio: Portfolio,
    page_context: AgentPageContext | None,
) -> dict[str, Any]:
    position_count = (
        session.query(func.count(Position.id))
        .filter(Position.portfolio_id == portfolio.id)
        .scalar()
        or 0
    )
    summary: dict[str, Any] = {
        "portfolio_id": portfolio.id,
        "name": portfolio.name,
        "base_currency": portfolio.base_currency,
        "position_count": int(position_count),
    }
    snapshot = (
        page_context.snapshot
        if page_context and isinstance(page_context.snapshot, dict)
        else {}
    )
    risk = snapshot.get("risk")
    if isinstance(risk, dict) and isinstance(risk.get("totals"), dict):
        summary["risk_totals"] = risk["totals"]
    latest_run = snapshot.get("latest_price_run")
    if isinstance(latest_run, dict) and isinstance(latest_run.get("summary"), dict):
        summary["latest_price_summary"] = latest_run["summary"]
    selected = snapshot.get("selected_position")
    if isinstance(selected, dict):
        summary["selected_position"] = {
            key: selected.get(key)
            for key in ("id", "source_trade_id", "underlying", "product_type")
            if selected.get(key) is not None
        }
    return summary

"""The state the System One tool guard sends to Jev (spec 2026-09-21 §1).

A BOUNDED projection, on purpose: the caps below are the documented
summarisation, and `ask()` itself never cuts anything. Order is fixed:
redact args -> render to text -> cap -> hand to ask() (which sanitizes,
serializes and size-checks).
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolCall, ToolMessage

from ..system_one import cap
from .audit_redaction import redact_args

USER_REQUEST_CHARS = 4000
DELEGATED_TASK_CHARS = 2000
EARLIER_CALLS = 8
RESULT_HEAD_CHARS = 300
# Not in the spec: 8 uncapped arg payloads (redact_args allows 8 KB each) would
# blow the 60 000-char budget and make every long turn state_too_large.
ARGS_HEAD_CHARS = 300


def load_user_request(context: dict[str, Any]) -> tuple[str | None, str | None]:
    """The USER's words for this turn, read from the DB (D11).

    Inside a persona the first human message is the orchestrator's `task()`
    paraphrase; the predicate is about what the user named, so it is read from
    `agent_messages`. Turn-scoped when the audit context carries
    `user_message_id` (stamped by stream_and_persist); otherwise the thread's
    latest user message. Returns (text, "message_id" | "latest") or (None, None).
    """
    thread_id = context.get("thread_id")
    if not isinstance(thread_id, int):
        return None, None
    from app import database
    from app.models import AgentMessage

    with database.SessionLocal() as session:
        user_message_id = context.get("user_message_id")
        if isinstance(user_message_id, int):
            row = session.get(AgentMessage, user_message_id)
            if row is None or row.role != "user" or row.thread_id != thread_id:
                return None, None
            return row.content or "", "message_id"
        row = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "user")
            .order_by(AgentMessage.id.desc())
            .first()
        )
        if row is None:
            return None, None
        return row.content or "", "latest"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _render_args(tool_name: str, args: dict[str, Any] | None) -> str:
    payload, _redacted = redact_args(tool_name, args)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _earlier_calls(messages: Sequence[AnyMessage]) -> list[str]:
    last_human = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
    )
    window = list(messages[last_human + 1:])
    if window and isinstance(window[-1], AIMessage):
        window = window[:-1]  # the pending AIMessage is not "earlier"
    results = {m.tool_call_id: m for m in window if isinstance(m, ToolMessage)}
    rendered: list[str] = []
    for message in window:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            args_text = cap(_render_args(call["name"], call.get("args")), ARGS_HEAD_CHARS)
            result = results.get(call.get("id") or "")
            head = (
                cap(_text_of(result.content), RESULT_HEAD_CHARS)
                if result is not None else "(no result)"
            )
            rendered.append(f"{call['name']}({args_text}) -> {head}")
    return rendered[-EARLIER_CALLS:]


def build_guard_state(
    messages: Sequence[AnyMessage],
    tool_call: ToolCall,
    *,
    user_request: str,
    is_subagent: bool,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "mode": "auto (no human will review this call)",
        "user_request": cap(user_request, USER_REQUEST_CHARS),
    }
    if is_subagent:
        first = next((m for m in messages if isinstance(m, HumanMessage)), None)
        if first is not None:
            state["delegated_task"] = cap(_text_of(first.content), DELEGATED_TASK_CHARS)
    state["earlier_in_this_turn"] = _earlier_calls(messages)
    payload, _redacted = redact_args(tool_call["name"], tool_call.get("args"))
    state["pending_tool_call"] = {"name": tool_call["name"], "args": payload}
    return state

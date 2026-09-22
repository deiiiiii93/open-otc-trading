"""Rebuild the guard's TurnWindow from durable records (spec 2026-09-22 D5-D7).

The live guard builds its window from the stack's `state["messages"]`; the
retrospective sweep has only what was written down — the thread's user
messages, the trace DB's spans and the audit trail. Both hand the window to
`tool_guard_state.assemble_guard_state`, so rendering, caps and order cannot
drift apart.

`trace` fidelity reproduces what the live guard saw:
- the call's OWN span is found by `extra.tool_call_id` (audit `occurred_at`
  precedes it by ~1 ms, so time is never the key);
- its agent scope is the nearest enclosing `task` span by `dotted_order` prefix
  (none = the orchestrator). `delegated_task` is that span's description —
  structural, not a heuristic (audit `persona` is NULL on ~99% of rows);
- the window is the scope's tool spans that started before the LLM span which
  emitted the pending call: the live guard never sees a sibling persona's calls
  or the other calls in its own pending AIMessage.
`audit_only` (no trace DB, or no own span) sees less: the thread's audited
writes in the turn, `result_preview` as the head, no delegated_task. The two
are never pooled (D7).

Every timestamp is parsed to aware UTC before any comparison (D6): audit rows
store naive `YYYY-MM-DD HH:MM:SS`, trace spans ISO with `T` and `+00:00`, and a
string comparison between the two silently returns nothing.
"""
from __future__ import annotations

import ast
import io
import json
import logging
import sqlite3
import tokenize
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...models import AgentActionAudit, AgentMessage
from .tool_guard_state import EarlierCall, TurnWindow, _text_of

logger = logging.getLogger(__name__)

TRACE = "trace"
AUDIT_ONLY = "audit_only"
#: The orchestrator graph's `name=` (orchestrator.py); its spans carry it as
#: `lc_agent_name`. Personas carry their own name ("trader", "risk_manager", ...).
ORCHESTRATOR_AGENT = "otc_desk_orchestrator"
_TASK = "task"
_TOOL_MESSAGE_MARK = "ToolMessage(content="

# Both are scoped by thread_id and served by ix_trace_runs_thread_start: the
# trace DB is tens of GB and an unscoped query would scan all of it.
TOOL_SPANS_SQL = (
    "SELECT id, dotted_order, name, start_time, inputs, outputs, error, extra "
    "FROM trace_runs WHERE thread_id = ? AND start_time >= ? AND run_type = 'tool'"
)
LLM_SPANS_SQL = (
    "SELECT id, dotted_order, start_time "
    "FROM trace_runs WHERE thread_id = ? AND start_time >= ? AND run_type = 'llm'"
)


def parse_utc(value: datetime | str) -> datetime:
    """Aware UTC from either storage format, or from a naive/aware datetime."""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class UserTurn:
    """The user's words for a call, and the turn's bounds (aware UTC)."""

    message_id: int
    text: str
    started: datetime
    ended: datetime | None       # the next user message; None = still the latest turn


def user_turn(session: Session, thread_id: int, occurred_at: datetime | str) -> UserTurn | None:
    """The thread's latest user message at or before the call (D6).

    Turns on one thread are sequential, so the next user message closes the
    window. Stronger than the live fallback ("the thread's latest"), which the
    sweep never needs.
    """
    at = parse_utc(occurred_at)
    stamped = sorted(
        ((parse_utc(created_at), message_id) for message_id, created_at in (
            session.query(AgentMessage.id, AgentMessage.created_at)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "user")
            .all()
        )),
    )
    before = [(ts, message_id) for ts, message_id in stamped if ts <= at]
    if not before:
        return None
    started, message_id = before[-1]
    ended = min((ts for ts, _ in stamped if ts > at), default=None)
    message = session.get(AgentMessage, message_id)
    return UserTurn(message_id, (message.content or "") if message else "", started, ended)


@dataclass(frozen=True)
class Span:
    id: str
    dotted_order: str
    run_type: str                  # "tool" | "llm"
    start: datetime                # aware UTC
    name: str = ""
    tool_call_id: str | None = None
    agent: str | None = None       # extra.metadata.lc_agent_name
    args: dict[str, Any] | None = None
    result: str | None = None


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _content_from_repr(text: str) -> str | None:
    """The first `ToolMessage(content=<literal>)` in a repr, parsed back exactly."""
    at = text.find(_TOOL_MESSAGE_MARK)
    if at < 0:
        return None
    rest = text[at + len(_TOOL_MESSAGE_MARK):]
    try:
        token = next(tokenize.generate_tokens(io.StringIO(rest).readline))
    except (tokenize.TokenError, StopIteration, SyntaxError):
        return None
    if token.type != tokenize.STRING:
        return None
    try:
        value = ast.literal_eval(token.string)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def span_result(outputs: Any, error: str | None) -> str | None:
    """The text the agent saw for a tool span, as closely as the trace can say.

    `{"output": <dumpd ToolMessage>}` -> its content (exact). A `task` span's
    output is a `Command`, stored by dumpd as a repr: its first ToolMessage
    content is parsed back (exact when it parses; the raw repr otherwise). An
    errored span has no output: the live guard saw the error boundary's
    "Error: ..." message, approximated here by the error's first line.
    """
    output = outputs.get("output") if isinstance(outputs, dict) else None
    if isinstance(output, dict):
        ident = output.get("id") or []
        if ident and ident[-1] == "ToolMessage":
            return _text_of((output.get("kwargs") or {}).get("content", ""))
        if isinstance(output.get("repr"), str):
            return _content_from_repr(output["repr"]) or output["repr"]
        return json.dumps(output, ensure_ascii=False, default=str)
    if isinstance(output, str):
        return output
    if error and error.strip():
        return f"Error: {error.strip().splitlines()[0]}"
    if output is not None:
        return json.dumps(output, ensure_ascii=False, default=str)
    return None


def read_spans(trace_path: str | Path | None, thread_id: int, since: datetime) -> list[Span] | None:
    """The thread's tool and llm spans from `since` on, sorted by start; None when
    no trace DB is usable. Opened read-only: the sweep never writes, creates or
    migrates a trace DB."""
    if trace_path is None:
        return None
    path = Path(trace_path)
    if not path.is_file():
        return None
    # Coarse and format-agnostic: both formats begin YYYY-MM-DD, so a date-prefix
    # bound is a safe SQL pre-filter; the exact bound is applied after parsing.
    floor = parse_utc(since)
    day = (floor - timedelta(days=1)).date().isoformat()
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            tool_rows = conn.execute(TOOL_SPANS_SQL, (thread_id, day)).fetchall()
            llm_rows = conn.execute(LLM_SPANS_SQL, (thread_id, day)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning("guard sweep: trace DB %s unreadable; audit_only", path, exc_info=True)
        return None
    spans: list[Span] = []
    for span_id, dotted, name, start, inputs, outputs, error, extra in tool_rows:
        try:
            started = parse_utc(start)
        except (TypeError, ValueError):
            continue
        meta = _loads(extra)
        meta = meta if isinstance(meta, dict) else {}
        args = _loads(inputs)
        spans.append(Span(
            id=span_id, dotted_order=dotted, run_type="tool", start=started, name=name,
            tool_call_id=meta.get("tool_call_id"),
            agent=(meta.get("metadata") or {}).get("lc_agent_name"),
            args=args if isinstance(args, dict) else {},
            result=span_result(_loads(outputs), error),
        ))
    for span_id, dotted, start in llm_rows:
        try:
            spans.append(Span(id=span_id, dotted_order=dotted, run_type="llm",
                              start=parse_utc(start)))
        except (TypeError, ValueError):
            continue
    spans = [s for s in spans if s.start >= floor]
    spans.sort(key=lambda s: (s.start, s.dotted_order))
    return spans


@dataclass(frozen=True)
class RecordWindow:
    window: TurnWindow
    fidelity: str                  # TRACE | AUDIT_ONLY
    agent: str | None = None       # the stack that made the call, when the trace says


def _task_key(task: Span) -> str:
    """A `task` call's identity. A HITL resume starts a NEW trace root and re-enters
    the same `task` call under it, so its dotted_order changes and its
    tool_call_id does not; dotted_order is only the fallback."""
    return task.tool_call_id or task.dotted_order


def _scope(span: Span, tasks: Sequence[Span]) -> str:
    """The key of the nearest enclosing `task` span; "" = the orchestrator."""
    best: Span | None = None
    for task in tasks:
        if (span.dotted_order.startswith(task.dotted_order + ".")
                and (best is None or len(task.dotted_order) > len(best.dotted_order))):
            best = task
    return _task_key(best) if best is not None else ""


def _persona(agent: str | None) -> str | None:
    return "orchestrator" if agent == ORCHESTRATOR_AGENT else agent


def window_from_trace(spans: Sequence[Span], tool_call_id: str, turn: UserTurn) -> RecordWindow | None:
    """The live guard's window rebuilt from spans; None if the call's own span is missing."""
    in_turn = [s for s in spans
               if s.start >= turn.started and (turn.ended is None or s.start < turn.ended)]
    tools = [s for s in in_turn if s.run_type == "tool"]
    own = next((s for s in tools if s.tool_call_id == tool_call_id), None)
    if own is None:
        return None
    tasks = [s for s in tools if s.name == _TASK]
    scope = _scope(own, tasks)
    emitted_at = max(
        (s.start for s in in_turn
         if s.run_type == "llm" and s.start <= own.start and _scope(s, tasks) == scope),
        default=own.start,
    )
    earlier = tuple(
        EarlierCall(s.name, s.args, s.result)
        for s in tools
        if s is not own and s.start < emitted_at and _scope(s, tasks) == scope
    )
    delegated: str | None = None
    if scope:
        task = next((t for t in tasks if _task_key(t) == scope), None)
        description = (task.args or {}).get("description") if task is not None else None
        delegated = description if isinstance(description, str) else None
    return RecordWindow(TurnWindow(turn.text, delegated, earlier), TRACE, _persona(own.agent))


def _audit_head(row: AgentActionAudit) -> str | None:
    if row.status == "ok":
        return row.result_preview
    if row.error:
        return row.error
    if row.status == "denied":
        return f"denied ({row.deny_reason})" if row.deny_reason else "denied"
    return None


def window_from_audit(session: Session, audit_row: AgentActionAudit, turn: UserTurn) -> TurnWindow:
    """What the audit trail alone can say (D7): the thread's audited writes in
    the turn before this call — classified writes only, no reads."""
    at = parse_utc(audit_row.occurred_at)
    rows = (
        session.query(AgentActionAudit)
        .filter(AgentActionAudit.thread_id == audit_row.thread_id,
                AgentActionAudit.kind == "execution",
                AgentActionAudit.id != audit_row.id)
        .all()
    )
    earlier = sorted(
        (r for r in rows if turn.started <= parse_utc(r.occurred_at) < at),
        key=lambda r: (parse_utc(r.occurred_at), r.id),
    )
    return TurnWindow(turn.text, None, tuple(
        EarlierCall(r.tool_name, dict(r.args_json or {}), _audit_head(r)) for r in earlier))


def window_from_records(session: Session, audit_row: AgentActionAudit, turn: UserTurn, *,
                        trace_path: str | Path | None) -> RecordWindow:
    spans = read_spans(trace_path, audit_row.thread_id, turn.started)
    if spans:
        found = window_from_trace(spans, audit_row.tool_call_id or "", turn)
        if found is not None:
            return found
    return RecordWindow(window_from_audit(session, audit_row, turn), AUDIT_ONLY)

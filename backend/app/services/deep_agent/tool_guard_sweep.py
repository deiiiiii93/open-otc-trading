"""Retrospective sweep: the guard's predicates over EXECUTED calls (spec 2026-09-22).

Advisory only (D4): a sweep verdict changes no state and raises no interrupt,
and the live guard can never meet one (D3). The middleware commits its verdict
BEFORE a call runs, while a sweep row exists only for a call that already has a
terminal `execution` audit row — `due_rows` never returns anything else and
`score_audit_row` refuses anything else.

One scorer, two drivers (D1): `SweepDaemon` (hourly, desk threads only, behind
OPEN_OTC_SYSTEM_ONE + OPEN_OTC_GUARD_SWEEP) and `scripts/guard_sweep.py` (the
evidence CLI; it may read arena history when a person runs it).
"""
from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import exists, or_
from sqlalchemy.orm import Query, Session

from ...config import Settings, get_settings
from ...models import AgentActionAudit, AgentThread, AgentToolGuardVerdict
from ..system_one import Noul, SystemOneUnavailable, ask
from .tool_guard import _unscored, scored_fields
from .tool_guard_policy import policy_for, swept_tools
from .tool_guard_records import user_turn, window_from_records
from .tool_guard_state import assemble_guard_state
from .tool_guard_store import StoredVerdict, args_fingerprint, commit_verdict, find_verdict

logger = logging.getLogger(__name__)

SWEEP = "sweep"
DESK = "desk"        # every thread whose source is neither arena nor smoke (D8)
ARENA = "arena"
KINDS = frozenset({DESK, ARENA})
_NOT_DESK = ("arena", "smoke")
TERMINAL_STATUSES = ("ok", "error", "denied")
#: The endpoint is down: nothing is written, the call stays due, the pass ends (F4).
OUTAGE_REASONS = frozenset({"no_key", "timeout", "http_error"})
USER_REQUEST_SOURCE = "occurred_at"

# Daemon cadence (D11). Module constants, as the limit review's are; only the
# switch is a Settings field.
sweep_interval_s = 3600
sweep_lookback_days = 7
sweep_batch = 20


def eligible_rows(session: Session, *, kinds: Collection[str],
                  tools: Collection[str] | None = None,
                  since: datetime | None = None) -> Query:
    """Terminal, keyable executions of a swept tool on threads of `kinds`, oldest
    first — scored or not. The CLI's `select` reads this; `due_rows` narrows it."""
    wanted = frozenset(kinds)
    if not wanted or not wanted <= KINDS:
        raise ValueError(f"kinds must be a non-empty subset of {sorted(KINDS)}, got {sorted(wanted)}")
    names = swept_tools() if tools is None else frozenset(tools)
    unknown = names - swept_tools()
    if unknown:
        raise ValueError(f"{sorted(unknown)} are in neither policy")
    by_source = []
    if DESK in wanted:
        by_source.append(AgentThread.source.notin_(_NOT_DESK))
    if ARENA in wanted:
        by_source.append(AgentThread.source == "arena")
    q = (
        session.query(AgentActionAudit)
        .join(AgentThread, AgentThread.id == AgentActionAudit.thread_id)
        .filter(
            AgentActionAudit.kind == "execution",
            AgentActionAudit.status.in_(TERMINAL_STATUSES),
            AgentActionAudit.tool_call_id.isnot(None),
            AgentActionAudit.tool_call_id != "",
            AgentActionAudit.tool_name.in_(sorted(names)),
            or_(*by_source),
        )
    )
    if since is not None:
        # ORM column vs an ORM-typed bound: one storage format, so SQL compares
        # correctly here. The D6 format trap is only ever against the trace DB.
        q = q.filter(AgentActionAudit.occurred_at >= since)
    return q.order_by(AgentActionAudit.occurred_at.asc(), AgentActionAudit.id.asc())


def due_rows(session: Session, *, kinds: Collection[str], since: datetime | None = None,
             limit: int | None = None,
             tools: Collection[str] | None = None) -> list[AgentActionAudit]:
    """Eligible rows with no verdict of ANY source for the call key (D13). Oldest
    first, so a backlog drains in order and a crashed CLI resumes where it
    stopped — the verdict table is the checkpoint (D3)."""
    v = AgentToolGuardVerdict
    scored = exists().where(v.thread_id == AgentActionAudit.thread_id,
                            v.tool_call_id == AgentActionAudit.tool_call_id)
    q = eligible_rows(session, kinds=kinds, tools=tools, since=since).filter(~scored)
    if limit is not None:
        q = q.limit(limit)
    return q.all()


@dataclass(frozen=True)
class SweepResult:
    audit_id: int
    stored: StoredVerdict | None       # None iff `outage` is set
    outage: str | None = None          # nothing written; the call stays due (F4)
    fidelity: str | None = None        # trace | audit_only; None without a user turn
    already_scored: bool = False       # an existing row (any source) won; no Jev call


def _require_sweepable(row: AgentActionAudit) -> None:
    """D3's invariant, enforced where the row is written, not only where it is chosen."""
    if row.kind != "execution" or row.status not in TERMINAL_STATUSES:
        raise ValueError(
            f"audit row {row.id} is not a terminal execution ({row.kind}/{row.status})")
    if row.thread_id is None or not row.tool_call_id:
        raise ValueError(f"audit row {row.id} has no call key")
    if policy_for(row.tool_name) is None:
        raise ValueError(f"{row.tool_name!r} is in neither GUARD_POLICY nor SWEEP_POLICY")


def score_audit_row(session: Session, audit_row: AgentActionAudit, *,
                    settings: Settings | None = None, post: Any = None,
                    trace_path: str | Path | None = None) -> SweepResult:
    """Score one executed call and commit an advisory `source="sweep"` row.

    Only SystemOneUnavailable is mapped: an outage writes nothing (F4), a row
    fact stamps `unscored`. Anything else RAISES — a bug must not stamp rows
    permanently unscored; the daemon logs and ends its pass, the CLI exits
    non-zero. Does not read the master switch: the daemon gates on it, the CLI
    is a manual act (D11).
    """
    _require_sweepable(audit_row)
    cfg = settings or get_settings()
    existing = find_verdict(audit_row.thread_id, audit_row.tool_call_id)
    if existing is not None:
        return SweepResult(audit_row.id, existing, already_scored=True)
    predicates = policy_for(audit_row.tool_name)
    args = dict(audit_row.args_json or {})
    fp = args_fingerprint(audit_row.tool_name, args)
    base: dict[str, Any] = {
        "thread_id": audit_row.thread_id, "tool_call_id": audit_row.tool_call_id,
        "persona": audit_row.persona, "exec_mode": audit_row.mode,
        "guard_mode": cfg.tool_guard_mode, "tool_name": audit_row.tool_name,
        "args_json": fp.payload, "redacted": bool(audit_row.redacted),
        "args_hash": fp.sha256, "action": "recorded", "source": SWEEP,
        "audit_id": audit_row.id,
    }
    turn = user_turn(session, audit_row.thread_id, audit_row.occurred_at)
    if turn is None:
        stored = commit_verdict({**base, **_unscored("no_user_request", model=cfg.system_one_model),
                                 "state_fidelity": None})
        return SweepResult(audit_row.id, stored)
    path = Path(cfg.trace_db_path) if trace_path is None else Path(trace_path)
    rec = window_from_records(session, audit_row, turn, trace_path=path)
    if base["persona"] is None:
        base["persona"] = rec.agent
    state = assemble_guard_state(rec.window, {"name": audit_row.tool_name, "args": args})
    try:
        result = ask(state, {p.key: Noul(p.instructions) for p in predicates},
                     post=post, settings=cfg)
    except SystemOneUnavailable as exc:
        if exc.reason in OUTAGE_REASONS:
            return SweepResult(audit_row.id, None, exc.reason, rec.fidelity)
        fields = _unscored(exc.reason, model=cfg.system_one_model, latency_ms=exc.latency_ms,
                           error=exc.detail, source=USER_REQUEST_SOURCE)
    else:
        fields = scored_fields(predicates, result, user_request_source=USER_REQUEST_SOURCE)
    stored = commit_verdict({**base, **fields, "state_fidelity": rec.fidelity})
    return SweepResult(audit_row.id, stored, None, rec.fidelity)

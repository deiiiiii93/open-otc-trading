"""Verdict persistence for the System One tool guard (spec 2026-09-21 §1, D10).

The interrupt set must be a pure function of COMMITTED state. A verdict is
committed under UNIQUE (thread_id, tool_call_id) before any interrupt; a
re-entered node reads it back instead of re-asking a non-deterministic model.
Lookups and commits raise GuardStoreUnavailable (=> the pass's verdict is
persist_failed); the two best-effort writers only log.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from ... import database
from ...models import AgentToolGuardVerdict
from .audit_redaction import redact_args

logger = logging.getLogger(__name__)

# Same bounded backoff as the audit trail's fail-closed phase 1.
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.3, 0.9)


class GuardStoreUnavailable(RuntimeError):
    """The verdict store could not be read or written."""


@dataclass(frozen=True)
class StoredVerdict:
    id: int
    tool_name: str
    args_hash: str
    verdict: str
    unscored_reason: str | None
    predicates: list[dict]
    max_probability: float | None
    source: str = "live"     # live | sweep (spec 2026-09-22 D3)


@dataclass(frozen=True)
class ArgsFingerprint:
    payload: dict       # redacted, JSON-safe (default=str round trip)
    redacted: bool
    sha256: str         # of the canonical JSON of `payload`


def args_fingerprint(tool_name: str, args: dict[str, Any] | None) -> ArgsFingerprint:
    """Identity of a call's arguments: part of the verdict's reuse key (D10)."""
    payload, redacted = redact_args(tool_name, args)
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    )
    return ArgsFingerprint(
        payload=json.loads(canonical),
        redacted=redacted,
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _snapshot(row: AgentToolGuardVerdict) -> StoredVerdict:
    return StoredVerdict(
        id=row.id,
        tool_name=row.tool_name,
        args_hash=row.args_hash,
        verdict=row.verdict,
        unscored_reason=row.unscored_reason,
        predicates=list(row.predicates_json or []),
        max_probability=row.max_probability,
        source=row.source or "live",
    )


def _by_key(session, thread_id: int, tool_call_id: str):
    return (
        session.query(AgentToolGuardVerdict)
        .filter(
            AgentToolGuardVerdict.thread_id == thread_id,
            AgentToolGuardVerdict.tool_call_id == tool_call_id,
        )
        .one_or_none()
    )


def find_verdict(thread_id: int, tool_call_id: str) -> StoredVerdict | None:
    try:
        with database.SessionLocal() as session:
            row = _by_key(session, thread_id, tool_call_id)
            return _snapshot(row) if row is not None else None
    except SQLAlchemyError as exc:
        raise GuardStoreUnavailable(f"verdict lookup failed: {exc}") from exc


def commit_verdict(fields: dict[str, Any]) -> StoredVerdict:
    """Insert-or-select under the UNIQUE key. If a concurrent pass won the
    insert, the STORED row wins — never the one just computed."""
    last_exc: Exception | None = None
    for delay in (*_RETRY_DELAYS, None):
        try:
            with database.SessionLocal() as session:
                row = AgentToolGuardVerdict(**fields)
                session.add(row)
                try:
                    session.commit()
                    return _snapshot(row)
                except IntegrityError:
                    session.rollback()
                    existing = _by_key(session, fields["thread_id"], fields["tool_call_id"])
                    if existing is None:
                        raise
                    return _snapshot(existing)
        except OperationalError as exc:
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
        except SQLAlchemyError as exc:
            raise GuardStoreUnavailable(f"verdict commit failed: {exc}") from exc
    raise GuardStoreUnavailable(
        f"verdict commit failed after {len(_RETRY_DELAYS) + 1} tries"
    ) from last_exc


def record_structural(fields: dict[str, Any]) -> None:
    """Best-effort row for a verdict that needs no store (empty tool_call_id):
    it is identical on every pass, so a lost row changes nothing."""
    try:
        with database.SessionLocal() as session:
            session.add(AgentToolGuardVerdict(**fields))
            session.commit()
    except SQLAlchemyError:
        logger.warning("tool guard: structural verdict row not recorded", exc_info=True)


def mark_interrupted(row_ids: Iterable[int | None]) -> None:
    """Best-effort: the verdict itself is already committed."""
    ids = [row_id for row_id in row_ids if row_id is not None]
    if not ids:
        return
    try:
        with database.SessionLocal() as session:
            session.execute(
                update(AgentToolGuardVerdict)
                .where(AgentToolGuardVerdict.id.in_(ids))
                .values(action="interrupted")
            )
            session.commit()
    except SQLAlchemyError:
        logger.warning("tool guard: could not mark verdicts interrupted", exc_info=True)

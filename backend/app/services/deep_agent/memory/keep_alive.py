"""Memory keep-alive score (spec 2026-09-21 §2).

System One's second opinion on whether a fact is still worth keeping, shown
beside the extractor's self-reported `confidence` for proposed and live facts.
DISPLAY-ONLY (D3): nothing here changes eviction order, injection order or
status — which is also why every write below pins `updated_at` to itself (the
column has onupdate=utcnow and `load_injectable` orders by it).

Runs only on the memory-writer daemon, never on a turn: after a job's
`apply_diff` has COMMITTED, and on each sweep tick to backfill.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, update

from app.config import Settings
from app.models import MemoryEntry

from ...system_one import Score, SystemOneUnavailable, ask, cap, is_enabled
from .config import MemoryConfig
from .safety import is_memorable
from .store import Fact, MemoryStore

logger = logging.getLogger(__name__)

#: Levels as SITUATIONS, not degrees (Jev's guidance), 0 = drop, 3 = keep.
KEEP_ALIVE_LEVELS: tuple[str, ...] = (
    "Contradicted or replaced by another listed fact, or plainly no longer true",
    "A one-off detail of a single conversation that goes stale quickly (a run id, "
    "today's number, a temporary state)",
    "True for now but likely to change within weeks (a current project, a temporary limit)",
    "A standing preference, rule or fact about how this desk works, true until "
    "someone changes it",
)
KEEP_ALIVE_QUESTION = Score(
    instructions=(
        "Which situation best describes the memory fact in `fact` today, judged "
        "against the other facts listed in `other_facts_in_scope` and the fact's "
        "age in days?"
    ),
    criteria=KEEP_ALIVE_LEVELS,
)
_QUESTION_KEY = "keep_alive"

#: The service is unreachable for EVERY row: the first one ends the batch (one
#: failed request per sweep tick, not ten). Other reasons are about THAT row.
OUTAGE_REASONS = frozenset({"no_key", "timeout", "http_error"})

SessionFactory = Callable[[], Any]


def keep_alive_live(config: MemoryConfig, settings: Settings | None = None) -> bool:
    """Live iff master, OPEN_OTC_MEMORY and OPEN_OTC_MEMORY_KEEP_ALIVE are all on.
    A desk that turned memory off has said its memory is not processed."""
    return config.enabled and config.keep_alive_enabled and is_enabled(settings)


def age_days(created_at: datetime, now: datetime) -> int:
    """Whole UTC days the fact has existed (created_at is naive UTC)."""
    return max(0, int((now - created_at).total_seconds() // 86400))


def build_state(
    row: MemoryEntry, siblings: list[Fact], config: MemoryConfig, now: datetime
) -> dict[str, Any]:
    return {
        "fact": row.content,
        "scope": f"{row.scope_type}:{row.scope_id}",
        "category": row.category,
        "source": row.created_by,
        "status": row.status,
        "pinned": bool(row.pinned),
        "age_days": age_days(row.created_at, now),
        # Siblings are what make level 0 ("replaced by another fact") answerable.
        "other_facts_in_scope": [cap(f.content, config.keep_alive_sibling_chars) for f in siblings],
    }


def _stamp(session, row_id: int, **values: Any) -> None:
    session.execute(
        update(MemoryEntry)
        .where(MemoryEntry.id == row_id)
        .values(**values, updated_at=MemoryEntry.updated_at)  # D3: never reorder
        .execution_options(synchronize_session=False)
    )


def _due(session, config: MemoryConfig, now: datetime) -> list[MemoryEntry]:
    stale_before = now - timedelta(days=config.keep_alive_refresh_days)
    return (
        session.query(MemoryEntry)
        .filter(
            MemoryEntry.status != "archived",
            or_(MemoryEntry.keep_alive_score.is_(None),
                MemoryEntry.keep_alive_scored_at < stale_before),
        )
        # Rotation: never-attempted first, then least-recently attempted, so a
        # permanently failing row cannot starve the rest.
        .order_by(
            MemoryEntry.keep_alive_attempted_at.is_not(None),
            MemoryEntry.keep_alive_attempted_at.asc(),
            MemoryEntry.updated_at.asc(),
            MemoryEntry.id.asc(),
        )
        .limit(config.keep_alive_batch)
        .all()
    )


def _score_one(session_factory: SessionFactory, store: MemoryStore, config: MemoryConfig,
               row_id: int, *, post: Any, settings: Settings | None, now: datetime) -> str:
    """Score one fact in its own small transaction. Returns "scored", "skipped",
    "denylist", "internal_error" or an UNAVAILABLE reason. Never raises."""
    with session_factory() as session:
        try:
            row = session.get(MemoryEntry, row_id)
            if row is None or row.status == "archived":
                return "skipped"
            # The CURRENT denylist, re-run now: backfill reaches rows admitted
            # under older rules or imported, so "it passed once" proves nothing.
            if not is_memorable(row.content, config.denylist)[0]:
                _stamp(session, row_id, keep_alive_attempted_at=now,
                       keep_alive_unscored_reason="denylist")
                session.commit()
                return "denylist"
            siblings = [
                fact
                for fact in store.load_existing(session, row.scope_type, row.scope_id,
                                                limit=config.keep_alive_sibling_limit)
                if fact.id != row.id and is_memorable(fact.content, config.denylist)[0]
            ]
            state = build_state(row, siblings, config, now)
            try:
                result = ask(state, {_QUESTION_KEY: KEEP_ALIVE_QUESTION},
                             post=post, settings=settings)
            except SystemOneUnavailable as exc:
                store.counters["keep_alive_failed"] += 1
                _stamp(session, row_id, keep_alive_attempted_at=now,
                       keep_alive_unscored_reason=exc.reason)
                session.commit()
                return exc.reason
            answer = result.answers[_QUESTION_KEY]
            _stamp(session, row_id,
                   keep_alive_score=answer.normalized,
                   keep_alive_confidence=answer.confidence,
                   keep_alive_scored_at=now,
                   keep_alive_attempted_at=now,
                   keep_alive_unscored_reason=None)
            session.commit()
            return "scored"
        except Exception:  # noqa: BLE001 — best-effort, isolated
            session.rollback()
            store.counters["keep_alive_failed"] += 1
            logger.warning("keep-alive: scoring memory fact %s failed", row_id, exc_info=True)
            try:
                _stamp(session, row_id, keep_alive_attempted_at=now,
                       keep_alive_unscored_reason="internal_error")
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
            return "internal_error"


def score_pending(
    session_factory: SessionFactory,
    store: MemoryStore,
    config: MemoryConfig,
    *,
    post: Any = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Score up to `keep_alive_batch` due facts; return how many were scored.

    Due = non-archived and never scored, or scored more than
    `keep_alive_refresh_days` ago (the score judges staleness, so it goes stale).
    Inert unless keep_alive_live(). Never raises.
    """
    try:
        if not keep_alive_live(config, settings):
            return 0
        when = now or datetime.utcnow()
        with session_factory() as session:
            due = [row.id for row in _due(session, config, when)]
    except Exception:  # noqa: BLE001
        store.counters["keep_alive_failed"] += 1
        logger.warning("keep-alive: due-fact query failed", exc_info=True)
        return 0
    scored = 0
    for row_id in due:
        outcome = _score_one(session_factory, store, config, row_id,
                             post=post, settings=settings, now=when)
        if outcome == "scored":
            scored += 1
        elif outcome in OUTAGE_REASONS:
            logger.info("keep-alive: System One unreachable (%s); ending this batch", outcome)
            break
    return scored

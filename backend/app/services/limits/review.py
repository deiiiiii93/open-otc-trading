"""Limit incident text review — Jev site 4 (spec 2026-09-21-limit-incident-review).

Three reads of the text on a limit incident: a waiver rationale's completeness
grade, the claims it makes (three of which are checked against the database),
and the state a comment thread leaves the incident in. DISPLAY-ONLY (D5): a
review changes no status, blocks no waiver, and is invisible to the agent (D13).

Everything is built from the immutable `limit_incident_events` row (D6) and
stored once per (event_id, kind) in `limit_incident_reviews` (D3). Jev is the
only source of probabilities; every number in `state` is computed here.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import database
from ...config import Settings, get_settings
from ...models import (
    AgentThread,
    LimitEvaluation,
    LimitIncident,
    LimitIncidentEvent,
    LimitIncidentReview,
    RiskLimit,
    RiskLimitVersion,
)
from ..system_one import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    SystemOneUnavailable,
    ask,
    cap,
    is_enabled,
)
from ..system_one.client import Answer
from ..task_runner import submit_async_task
from ..thread_access import thread_is_arena
from .review_checks import ClaimCheck, run_check

logger = logging.getLogger(__name__)

KIND_WAIVER = "waiver"
KIND_THREAD = "thread"
KINDS: tuple[str, ...] = (KIND_WAIVER, KIND_THREAD)
EVENT_TYPE_FOR_KIND: dict[str, str] = {KIND_WAIVER: "waived", KIND_THREAD: "commented"}

#: Module constants (the parent's MemoryConfig precedent); only the switch is a Setting.
review_rationale_chars = 4000
review_comment_chars = 600
review_thread_comments = 20
review_chip_min_p = 0.70     # provisional: between the parent's HIGH and low bands
review_choice_min_p = 0.50   # the parent's confirmations site
review_batch = 10

#: The service is unreachable for EVERY row: the first ends the batch (keep-alive rule).
OUTAGE_REASONS = frozenset({"no_key", "timeout", "http_error"})

# --- questions (developer-authored constants; never sanitized, never rewritten) ----

RATIONALE_LEVELS: tuple[str, ...] = (
    "No reason is given: the text is filler, restates that the limit is breached, or "
    "only says the waiver was requested or approved",
    "A cause is asserted with nothing checkable — \"market move\", \"temporary\", "
    "\"known issue\" — and no position, trade, event or date is named",
    "A specific cause is named (a position, trade, market event or data problem), but "
    "nothing says what will bring the exposure back inside the limit",
    "A specific cause and a specific remediation are named, but with no owner or date "
    "— or the remediation lands after the waiver expires (compare any date in the text "
    "with `waiver.duration_days`)",
    "A specific cause, a specific remediation, who will do it, and a date on or before "
    "the waiver's expiry",
)
RATIONALE_QUESTION = Score(
    instructions=(
        "Which situation best describes the waiver rationale in `waiver.rationale`, "
        "read against the breach in `breach` and the limit in `limit`? The waiver "
        "lasts `waiver.duration_days` days from the day it was written."
    ),
    criteria=RATIONALE_LEVELS,
)

_CLAIM = "The rationale in `waiver.rationale` claims that "
CLAIM_QUESTIONS: dict[str, Noul] = {
    "position_rolling_off": Noul(_CLAIM + "the exposure will fall on its own because a "
                                 "position expires, matures, knocks out or settles"),
    "data_error": Noul(_CLAIM + "the breach comes from a wrong, stale or missing market "
                       "input or a failed risk run, not from real exposure"),
    "limit_under_review": Noul(_CLAIM + "the limit itself is mis-sized or is being changed"),
    "hedge_in_progress": Noul(_CLAIM + "a hedge or unwind trade has been ordered or is "
                              "being executed"),
    "client_flow_expected": Noul(_CLAIM + "an expected client trade or unwind will reduce "
                                 "the exposure"),
    "market_reversion": Noul(_CLAIM + "the exposure will return inside the limit because "
                             "the market will move back"),
}
AUTHORITY_ONLY_QUESTION = Noul(
    "The only justification given in `waiver.rationale` is that a trader, a manager or "
    "another person asked for or approved the waiver"
)
WAIVER_QUESTIONS: dict[str, Question] = {
    "rationale_grade": RATIONALE_QUESTION,
    **CLAIM_QUESTIONS,
    "authority_only": AUTHORITY_ONLY_QUESTION,
}

THREAD_STATE_OPTIONS: dict[str, str] = {
    "disputes_number": "The desk says the breach figure is wrong. The figure is contested; "
                       "no error has been confirmed",
    "remediating": "The desk accepts the breach and describes action taken or under way",
    "requests_limit_change": "The desk argues the limit should change rather than the exposure",
    "requests_more_time": "The desk asks for a waiver or more time without describing remediation",
    "root_cause_only": "The comments explain why the breach happened and say nothing about "
                       "what happens next",
    "no_position": "The comments are administrative (assignment, FYI); none takes a position",
}
THREAD_QUESTIONS: dict[str, Question] = {
    "thread_state": Choice(
        instructions=(
            "Which option best describes where the comment thread in `comments` (oldest "
            "first) leaves the incident described in `incident` and `limit`?"
        ),
        criteria=THREAD_STATE_OPTIONS,
    ),
}

#: Honesty marker per question (parent's EVIDENCE_LEVELS). Every one starts untested;
#: a key moves to "tested-posthoc" only by an edit that cites a probe run.
QUESTION_EVIDENCE: dict[str, str] = {
    key: "untested" for key in (*WAIVER_QUESTIONS, *THREAD_QUESTIONS)
}

#: Short labels the UI and the probe share, indexed by rationale level.
RATIONALE_LEVEL_LABELS: tuple[str, ...] = (
    "no reason", "cause not checkable", "cause, no remediation",
    "remediation, no owner/date", "complete",
)


def is_live(settings: Settings | None = None) -> bool:
    """Live iff the master switch AND OPEN_OTC_LIMIT_REVIEW (D15)."""
    cfg = settings or get_settings()
    return bool(cfg.limit_review_enabled) and is_enabled(cfg)


# --- arithmetic (code computes, Jev reads) -----------------------------------------

def floor_days(later: datetime, earlier: datetime) -> int:
    return max(0, int((later - earlier).total_seconds() // 86400))


def ceil_days(later: datetime, earlier: datetime) -> int:
    return max(0, math.ceil((later - earlier).total_seconds() / 86400))


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 text -> naive UTC datetime (the event payload stores isoformat())."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


# --- as-of lookups (D6) --------------------------------------------------------------

def evaluation_as_of(
    session: Session, incident: LimitIncident, at: datetime
) -> LimitEvaluation | None:
    """The evaluation on the incident's latest evaluation-stamped event at or before `at`.

    NOT `incident.last_evaluation_id`: that keeps moving after the waiver, and a late
    retry would grade the rationale against a breach its author never saw.
    """
    event = session.scalar(
        select(LimitIncidentEvent)
        .where(
            LimitIncidentEvent.incident_id == incident.id,
            LimitIncidentEvent.evaluation_id.is_not(None),
            LimitIncidentEvent.created_at <= at,
        )
        .order_by(LimitIncidentEvent.created_at.desc(), LimitIncidentEvent.id.desc())
    )
    if event is None:
        return None
    return session.get(LimitEvaluation, event.evaluation_id)


_STATUS_AFTER: dict[str, str] = {
    "opened": "open", "acknowledged": "acknowledged", "assigned": "assigned",
    "waived": "waived", "waiver_expired": "open", "recovered": "recovered",
    "resolved": "resolved", "reopened": "open",
}


def status_as_of(events: Sequence[LimitIncidentEvent]) -> str:
    """Replay the timeline: the incident's status right after the last event given.
    Mirrors incidents.py, including that assigning a waived incident keeps it waived."""
    status = "open"
    for event in events:
        if event.event_type == "assigned" and status == "waived":
            continue
        status = _STATUS_AFTER.get(event.event_type, status)
    return status


def _ordered_events(incident: LimitIncident) -> list[LimitIncidentEvent]:
    return sorted(incident.events, key=lambda e: (e.created_at, e.id))


def _events_up_to(incident: LimitIncident, event: LimitIncidentEvent) -> list[LimitIncidentEvent]:
    return [
        e for e in _ordered_events(incident)
        if (e.created_at, e.id) <= (event.created_at, event.id)
    ]


def latest_event_id(incident: LimitIncident, kind: str) -> int | None:
    wanted = EVENT_TYPE_FOR_KIND[kind]
    ids = [e.id for e in incident.events if e.event_type == wanted and e.id is not None]
    return max(ids) if ids else None


# --- state builders ------------------------------------------------------------------

class ReviewStateTooLarge(ValueError):
    """The text is over its cap; it is rejected, never clipped (spec §Waiver request)."""


def _limit_block(
    session: Session, incident: LimitIncident, evaluation: LimitEvaluation | None
) -> dict[str, Any]:
    limit = session.get(RiskLimit, incident.risk_limit_id)
    version: RiskLimitVersion | None = None
    if evaluation is not None:
        version = session.get(RiskLimitVersion, evaluation.limit_version_id)
    if version is None and limit is not None and limit.active_version_id is not None:
        version = session.get(RiskLimitVersion, limit.active_version_id)
    return {
        "name": limit.name if limit is not None else None,
        "metric_kind": version.metric_kind if version is not None else None,
        "unit": version.unit if version is not None else None,
        "scope_type": incident.scope_type,
        "scope_label": incident.scope_label,
    }


def build_waiver_state(
    session: Session, incident: LimitIncident, event: LimitIncidentEvent
) -> dict[str, Any]:
    payload = dict(event.payload or {})
    rationale = str(payload.get("rationale") or "")
    if len(rationale) > review_rationale_chars:
        raise ReviewStateTooLarge(
            f"rationale {len(rationale)} > {review_rationale_chars} chars"
        )
    expires = parse_iso(payload.get("waiver_expires_at"))
    evaluation = evaluation_as_of(session, incident, event.created_at)
    return {
        "limit": _limit_block(session, incident, evaluation),
        "breach": {
            "severity": evaluation.status if evaluation is not None else None,
            "utilization": evaluation.utilization if evaluation is not None else None,
            "days_open": floor_days(event.created_at, incident.first_seen_at),
        },
        "waiver": {
            "rationale": rationale,
            "duration_days": (
                ceil_days(expires, event.created_at) if expires is not None else None
            ),
        },
    }


def build_thread_state(
    session: Session, incident: LimitIncident, event: LimitIncidentEvent
) -> dict[str, Any]:
    up_to = _events_up_to(incident, event)
    comments = [e for e in up_to if e.event_type == "commented"][-review_thread_comments:]
    evaluation = evaluation_as_of(session, incident, event.created_at)
    limit = session.get(RiskLimit, incident.risk_limit_id)
    return {
        "limit": {
            "name": limit.name if limit is not None else None,
            "scope_label": incident.scope_label,
        },
        "incident": {
            "severity": evaluation.status if evaluation is not None else None,
            "status": status_as_of(up_to),
            "days_open": floor_days(event.created_at, incident.first_seen_at),
        },
        "comments": [
            {
                "at": e.created_at.isoformat(),
                "actor": e.actor,
                "text": cap(str((e.payload or {}).get("comment") or ""), review_comment_chars),
            }
            for e in comments
        ],
    }

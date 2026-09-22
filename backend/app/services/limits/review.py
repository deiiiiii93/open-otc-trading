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


# --- answers <-> json ------------------------------------------------------------------

def answers_to_json(answers: Mapping[str, Answer]) -> dict[str, Any]:
    """Every raw answer, so a threshold change is a re-render, never a re-score."""
    out: dict[str, Any] = {}
    for key, answer in answers.items():
        if isinstance(answer, NoulAnswer):
            out[key] = {"p": round(answer.probability, 4)}
        elif isinstance(answer, ScoreAnswer):
            out[key] = {"score": answer.score, "confidence": answer.confidence,
                        "probabilities": dict(answer.probabilities)}
        elif isinstance(answer, ChoiceAnswer):
            out[key] = {"choice": answer.choice, "confidence": answer.confidence,
                        "probabilities": dict(answer.probabilities)}
    return out


def claims_from_answers(
    answers_json: Mapping[str, Any], checks: Mapping[str, ClaimCheck]
) -> list[dict[str, Any]]:
    """All six claims in question order; check fields are null below the chip threshold."""
    out = []
    for claim in CLAIM_QUESTIONS:
        p = float((answers_json.get(claim) or {}).get("p", 0.0))
        check = checks.get(claim)
        out.append({
            "claim": claim, "p": p,
            "check": check.check if check is not None else None,
            "detail": check.detail if check is not None else None,
            "checked_at": check.checked_at.isoformat() if check is not None else None,
        })
    return out


def _claims_to_check(answers_json: Mapping[str, Any]) -> list[str]:
    return [claim for claim in CLAIM_QUESTIONS
            if float((answers_json.get(claim) or {}).get("p", 0.0)) >= review_chip_min_p]


# --- store: insert-or-select on (event_id, kind) --------------------------------------

def _existing(session: Session, event_id: int, kind: str) -> LimitIncidentReview | None:
    return session.scalar(select(LimitIncidentReview).where(
        LimitIncidentReview.event_id == event_id, LimitIncidentReview.kind == kind))


def _write(session: Session, *, incident_id: int, event_id: int, kind: str,
           values: dict[str, Any]) -> LimitIncidentReview:
    """Insert in a savepoint; on the unique key, load the winner and update it only
    if it is still `unscored`. A `scored` row is never overwritten."""
    row = LimitIncidentReview(incident_id=incident_id, event_id=event_id, kind=kind, **values)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        # The savepoint rollback usually expunges the pending row itself; make sure it
        # is gone so the next flush does not replay the INSERT against the key.
        if row in session:
            session.expunge(row)
    winner = _existing(session, event_id, kind)
    if winner is None:  # pragma: no cover — the key fired, so the row exists
        raise RuntimeError(f"limit review ({event_id}, {kind}) vanished after an insert race")
    if winner.status == "unscored":
        for key, value in values.items():
            setattr(winner, key, value)
        session.flush()
    return winner


def _unscored_values(reason: str, *, now: datetime, model: str | None = None,
                     latency_ms: int | None = None,
                     answers: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "unscored", "unscored_reason": reason, "attempted_at": now,
        "rationale_grade": None, "rationale_confidence": None, "authority_only_p": None,
        "thread_state": None, "thread_state_p": None,
        "claims_json": [], "answers_json": answers or {},
        "model": model, "latency_ms": latency_ms, "created_at": now,
    }


# --- scoring one event ---------------------------------------------------------------

def _scored_values(kind: str, session: Session, incident: LimitIncident,
                   event: LimitIncidentEvent, answers: Mapping[str, Answer],
                   model: str, latency_ms: int, now: datetime) -> dict[str, Any]:
    answers_json = answers_to_json(answers)
    base = {"attempted_at": now, "model": model, "latency_ms": latency_ms,
            "answers_json": answers_json, "created_at": now}
    if kind == KIND_WAIVER:
        grade = answers["rationale_grade"]
        checks = {claim: run_check(session, claim, incident, event, now=now)
                  for claim in _claims_to_check(answers_json)}
        return {
            **base, "status": "scored", "unscored_reason": None,
            "rationale_grade": grade.normalized, "rationale_confidence": grade.confidence,
            "authority_only_p": answers["authority_only"].probability,
            "thread_state": None, "thread_state_p": None,
            "claims_json": claims_from_answers(answers_json, checks),
        }
    choice = answers["thread_state"]
    p = float(choice.probabilities.get(choice.choice, 0.0))
    if p < review_choice_min_p:
        return _unscored_values("low_confidence", now=now, model=model, latency_ms=latency_ms,
                                answers=answers_json)
    return {
        **base, "status": "scored", "unscored_reason": None,
        "rationale_grade": None, "rationale_confidence": None, "authority_only_p": None,
        "thread_state": choice.choice, "thread_state_p": p, "claims_json": [],
    }


def score_event(event_id: int, kind: str, *,
                session_factory: Callable[[], Session] | None = None,
                post: Any = None, settings: Settings | None = None,
                now: datetime | None = None) -> str:
    """Score one event in its own session. Returns "scored", "skipped", "exists" or the
    unscored reason written. Never raises; never touches a caller's Session."""
    if kind not in KINDS:
        return "skipped"
    cfg = settings or get_settings()
    if not is_live(cfg):
        return "skipped"
    factory = session_factory or database.SessionLocal
    when = now or datetime.utcnow()
    with factory() as session:
        try:
            event = session.get(LimitIncidentEvent, event_id)
            if event is None or event.event_type != EVENT_TYPE_FOR_KIND[kind]:
                return "skipped"
            if thread_is_arena(session, event.thread_id) is not False:   # None ⇒ skip (D14)
                return "skipped"
            existing = _existing(session, event_id, kind)
            if existing is not None and existing.status == "scored":
                return "exists"
            incident = session.get(LimitIncident, event.incident_id)
            if incident is None:
                return "skipped"
            try:
                builder = build_waiver_state if kind == KIND_WAIVER else build_thread_state
                state = builder(session, incident, event)
            except ReviewStateTooLarge:
                _write(session, incident_id=incident.id, event_id=event_id, kind=kind,
                       values=_unscored_values("state_too_large", now=when))
                session.commit()
                return "state_too_large"
            questions = WAIVER_QUESTIONS if kind == KIND_WAIVER else THREAD_QUESTIONS
            try:
                result = ask(state, questions, post=post, settings=cfg)
            except SystemOneUnavailable as exc:
                _write(session, incident_id=incident.id, event_id=event_id, kind=kind,
                       values=_unscored_values(exc.reason, now=when, model=cfg.system_one_model,
                                               latency_ms=exc.latency_ms))
                session.commit()
                return exc.reason
            values = _scored_values(kind, session, incident, event, result.answers,
                                    result.model, result.latency_ms, when)
            _write(session, incident_id=incident.id, event_id=event_id, kind=kind, values=values)
            session.commit()
            return "scored" if values["status"] == "scored" else str(values["unscored_reason"])
        except Exception:  # noqa: BLE001 — a review can never fail anything else
            session.rollback()
            logger.warning("limit review: scoring event %s (%s) failed",
                           event_id, kind, exc_info=True)
            try:
                event = session.get(LimitIncidentEvent, event_id)
                if event is not None:
                    _write(session, incident_id=event.incident_id, event_id=event_id, kind=kind,
                           values=_unscored_values("internal_error", now=when))
                    session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
            return "internal_error"


# --- fast path (D9: only the fast path; the sweep is the guarantee) --------------------

_submit = submit_async_task


def enqueue(event_id: int, kind: str, *, settings: Settings | None = None,
            submit: Callable[..., Any] | None = None) -> bool:
    """Hand `score_event` to the async pool after the caller has COMMITTED. False when
    not live, the event is missing, or its thread is (or may be) an arena thread.
    Never raises: forgetting or failing to enqueue only delays a review (D9)."""
    try:
        cfg = settings or get_settings()
        if kind not in KINDS or not is_live(cfg):
            return False
        with database.SessionLocal() as session:
            event = session.get(LimitIncidentEvent, event_id)
            if event is None or thread_is_arena(session, event.thread_id) is not False:
                return False
        (submit or _submit)(score_event, event_id, kind)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("limit review: enqueue of event %s (%s) failed",
                       event_id, kind, exc_info=True)
        return False


def enqueue_latest(incident: LimitIncident, kind: str, *, settings: Settings | None = None,
                   submit: Callable[..., Any] | None = None) -> bool:
    try:
        event_id = latest_event_id(incident, kind)
    except Exception:  # noqa: BLE001
        return False
    if event_id is None:
        return False
    return enqueue(event_id, kind, settings=settings, submit=submit)


# --- sweep (D9 / D10 / D12) -------------------------------------------------------------

def due_events(session: Session, limit: int) -> list[tuple[int, str]]:
    """Scoreable events with no row or an `unscored` row: every `waived` event, and
    each incident's LATEST `commented` event (D10). Arena-thread events are excluded
    here; a thread that cannot be resolved stays due and is skipped per attempt."""
    review = LimitIncidentReview
    not_arena = or_(
        LimitIncidentEvent.thread_id.is_(None),
        AgentThread.id.is_(None),
        AgentThread.source != "arena",
    )
    waived = (
        select(LimitIncidentEvent.id, literal(KIND_WAIVER).label("kind"), review.attempted_at)
        .outerjoin(AgentThread, AgentThread.id == LimitIncidentEvent.thread_id)
        .outerjoin(review, and_(review.event_id == LimitIncidentEvent.id,
                                review.kind == KIND_WAIVER))
        .where(LimitIncidentEvent.event_type == "waived", not_arena,
               or_(review.id.is_(None), review.status == "unscored"))
    )
    latest_comment = (
        select(func.max(LimitIncidentEvent.id).label("event_id"))
        .where(LimitIncidentEvent.event_type == "commented")
        .group_by(LimitIncidentEvent.incident_id)
        .subquery()
    )
    commented = (
        select(LimitIncidentEvent.id, literal(KIND_THREAD).label("kind"), review.attempted_at)
        .join(latest_comment, latest_comment.c.event_id == LimitIncidentEvent.id)
        .outerjoin(AgentThread, AgentThread.id == LimitIncidentEvent.thread_id)
        .outerjoin(review, and_(review.event_id == LimitIncidentEvent.id,
                                review.kind == KIND_THREAD))
        .where(not_arena, or_(review.id.is_(None), review.status == "unscored"))
    )
    rows = list(session.execute(waived)) + list(session.execute(commented))
    rows.sort(key=lambda r: (r[2] is not None, r[2] or datetime.min, r[0]))
    return [(int(r[0]), str(r[1])) for r in rows[:limit]]


def recompute_checks(session: Session, *, now: datetime) -> int:
    """Re-run the checkers on every scored waiver review whose incident is still
    `waived` and whose event is the incident's current waiver. Jev answers untouched."""
    rows = list(session.scalars(
        select(LimitIncidentReview)
        .join(LimitIncident, LimitIncident.id == LimitIncidentReview.incident_id)
        .where(LimitIncidentReview.kind == KIND_WAIVER,
               LimitIncidentReview.status == "scored",
               LimitIncident.status == "waived")
        .order_by(LimitIncidentReview.id)
    ))
    count = 0
    for row in rows:
        incident = session.get(LimitIncident, row.incident_id)
        event = session.get(LimitIncidentEvent, row.event_id)
        if (incident is None or event is None
                or latest_event_id(incident, KIND_WAIVER) != row.event_id):
            continue
        answers_json = dict(row.answers_json or {})
        checks = {claim: run_check(session, claim, incident, event, now=now)
                  for claim in _claims_to_check(answers_json)}
        row.claims_json = claims_from_answers(answers_json, checks)
        count += 1
    session.commit()
    return count


def sweep(session_factory: Callable[[], Session] | None = None, *, post: Any = None,
          settings: Settings | None = None, now: datetime | None = None) -> dict[str, int]:
    """Score up to `review_batch` due events, then recompute checks. Never raises."""
    counters = {"scored": 0, "unscored": 0, "skipped": 0, "rechecked": 0}
    try:
        cfg = settings or get_settings()
        if not is_live(cfg):
            return counters
        factory = session_factory or database.SessionLocal
        when = now or datetime.utcnow()
        with factory() as session:
            due = due_events(session, review_batch)
        for event_id, kind in due:
            outcome = score_event(event_id, kind, session_factory=factory, post=post,
                                  settings=cfg, now=when)
            if outcome == "scored":
                counters["scored"] += 1
            elif outcome in ("skipped", "exists"):
                counters["skipped"] += 1
            else:
                counters["unscored"] += 1
                if outcome in OUTAGE_REASONS:
                    logger.info("limit review: System One unreachable (%s); ending this batch",
                                outcome)
                    break
        with factory() as session:
            counters["rechecked"] = recompute_checks(session, now=when)
    except Exception:  # noqa: BLE001 — a monitoring run must never fail because of this
        logger.warning("limit review: sweep failed", exc_info=True)
    return counters


# --- read model --------------------------------------------------------------------------

def latest_reviews(
    session: Session, incident_ids: Iterable[int]
) -> dict[int, dict[str, LimitIncidentReview | None]]:
    """The row with the highest event_id per kind (the latest scoreable event, not the
    latest write — a late retry of an old waiver must not outrank the current one)."""
    ids = [int(i) for i in incident_ids]
    out: dict[int, dict[str, LimitIncidentReview | None]] = {
        i: {KIND_WAIVER: None, KIND_THREAD: None} for i in ids}
    if not ids:
        return out
    rows = session.scalars(
        select(LimitIncidentReview)
        .where(LimitIncidentReview.incident_id.in_(ids))
        .order_by(LimitIncidentReview.event_id.asc(), LimitIncidentReview.id.asc())
    )
    for row in rows:
        out[row.incident_id][row.kind] = row
    return out

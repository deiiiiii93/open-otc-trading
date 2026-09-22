"""Deterministic checks of the claims a waiver rationale makes (spec §Checkers, D2).

Read-only. `detail` is one sentence built from database facts, never from the
rationale. A check can say a claim is `supported` or that this database holds
`no_evidence` for it — never that it is false (D11): the review may be happening
by email. A checker that raises degrades that one claim to `unverified`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    LimitEvaluation,
    LimitIncident,
    LimitIncidentEvent,
    LimitSourceReference,
    OptionCoreTerm,
    Position,
    RiskLimitVersion,
)
from .scopes import scope_matches, scope_value

logger = logging.getLogger(__name__)

SUPPORTED = "supported"
NO_EVIDENCE = "no_evidence"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ClaimCheck:
    check: str
    detail: str
    checked_at: datetime

    def as_json(self) -> dict[str, Any]:
        return {"check": self.check, "detail": self.detail,
                "checked_at": self.checked_at.isoformat()}


CheckFn = Callable[[Session, LimitIncident, LimitIncidentEvent], ClaimCheck]


def _now() -> datetime:
    return datetime.utcnow()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(tzinfo=None)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def check_position_rolling_off(
    session: Session, incident: LimitIncident, event: LimitIncidentEvent
) -> ClaimCheck:
    """Open positions in the incident's scope whose materialised expiry
    (`option_core_terms.expiry_date`, written by position_terms) falls inside
    [event.created_at, waiver_expires_at]. Non-option products carry no term row
    and are therefore never counted."""
    expires = _parse_iso((event.payload or {}).get("waiver_expires_at"))
    if expires is None:
        return ClaimCheck(UNVERIFIED, "the waived event carries no waiver_expires_at", _now())
    start, end = event.created_at.date(), expires.date()
    value = scope_value(incident.scope_key)
    rows = session.execute(
        select(Position.id, Position.underlying, Position.product_type)
        .join(OptionCoreTerm, OptionCoreTerm.position_id == Position.id)
        .where(
            Position.portfolio_id == incident.portfolio_id,
            Position.status == "open",
            OptionCoreTerm.expiry_date.is_not(None),
            OptionCoreTerm.expiry_date >= start,
            OptionCoreTerm.expiry_date <= end,
        )
    ).all()
    hits = [
        row for row in rows
        if scope_matches(incident.scope_type, value, position_id=row.id,
                         underlying=row.underlying, family=str(row.product_type))
    ]
    if hits:
        return ClaimCheck(
            SUPPORTED,
            f"{_plural(len(hits), 'position')} in scope "
            f"{'expires' if len(hits) == 1 else 'expire'} on or before {end.isoformat()}",
            _now(),
        )
    return ClaimCheck(
        NO_EVIDENCE,
        f"no open position in scope expires between {start.isoformat()} and {end.isoformat()}",
        _now(),
    )


def check_data_error(
    session: Session, incident: LimitIncident, event: LimitIncidentEvent
) -> ClaimCheck:
    """The evaluations this incident's events reference up to the scored event,
    plus first_evaluation_id. Evidence keys read from evaluator.py / monitoring.py:
    `reason_code` (evaluator._preflight_reason), `coverage_ratio`, and in `evidence`
    the `source_reference_id` monitoring stamps, plus `is_fresh`,
    `missing_market_evidence` and `missing_fx` from sources.py."""
    ids = set(session.scalars(
        select(LimitIncidentEvent.evaluation_id).where(
            LimitIncidentEvent.incident_id == incident.id,
            LimitIncidentEvent.evaluation_id.is_not(None),
            LimitIncidentEvent.created_at <= event.created_at,
        )
    ))
    if incident.first_evaluation_id is not None:
        ids.add(incident.first_evaluation_id)
    evaluations = list(session.scalars(
        select(LimitEvaluation).where(LimitEvaluation.id.in_(ids)).order_by(LimitEvaluation.id)
    )) if ids else []
    problems: list[str] = []
    for ev in evaluations:
        evidence = dict(ev.evidence or {})
        if ev.reason_code:
            problems.append(f"evaluation #{ev.id} reason {ev.reason_code}")
        elif ev.coverage_ratio is not None and ev.coverage_ratio < 1.0:
            problems.append(f"evaluation #{ev.id} coverage {ev.coverage_ratio:.2f}")
        elif evidence.get("is_fresh") is False:
            problems.append(f"evaluation #{ev.id} used stale source evidence")
        elif evidence.get("missing_market_evidence") or evidence.get("missing_fx"):
            problems.append(f"evaluation #{ev.id} is missing market inputs")
        else:
            ref_id = evidence.get("source_reference_id")
            ref = (
                session.get(LimitSourceReference, int(ref_id))
                if isinstance(ref_id, int) and not isinstance(ref_id, bool)
                else None
            )
            if ref is not None and ref.is_fresh is False:
                problems.append(f"evaluation #{ev.id} used source reference #{ref.id}, "
                                "outside the freshness policy")
            elif ref is not None and (ref.completeness_diagnostics or {}).get("missing_market_evidence"):
                problems.append(f"evaluation #{ev.id} used source reference #{ref.id} "
                                "with missing market evidence")
    if problems:
        return ClaimCheck(SUPPORTED, "; ".join(problems[:3]), _now())
    return ClaimCheck(
        NO_EVIDENCE,
        f"{_plural(len(evaluations), 'evaluation')} behind this incident "
        f"{'carries' if len(evaluations) == 1 else 'carry'} no reason code, coverage gap or stale source",
        _now(),
    )


def check_limit_under_review(
    session: Session, incident: LimitIncident, event: LimitIncidentEvent
) -> ClaimCheck:
    newer = list(session.scalars(
        select(RiskLimitVersion)
        .where(
            RiskLimitVersion.risk_limit_id == incident.risk_limit_id,
            RiskLimitVersion.created_at > incident.first_seen_at,
        )
        .order_by(RiskLimitVersion.version)
    ))
    if newer:
        latest = newer[-1]
        return ClaimCheck(
            SUPPORTED,
            f"limit version {latest.version} ({latest.state}) was created on "
            f"{latest.created_at.date().isoformat()}, after the incident opened",
            _now(),
        )
    return ClaimCheck(
        NO_EVIDENCE, "no limit version has been created since the incident opened", _now()
    )


CHECKERS: dict[str, CheckFn] = {
    "position_rolling_off": check_position_rolling_off,
    "data_error": check_data_error,
    "limit_under_review": check_limit_under_review,
}
CLAIMS_WITH_CHECKERS: frozenset[str] = frozenset(CHECKERS)


def run_check(
    session: Session, claim: str, incident: LimitIncident, event: LimitIncidentEvent,
    *, now: datetime,
) -> ClaimCheck:
    """Never raises; a failing checker yields `unverified` and one log line."""
    checker = CHECKERS.get(claim)
    if checker is None:
        return ClaimCheck(UNVERIFIED, "no checker for this claim", now)
    try:
        result = checker(session, incident, event)
    except Exception:  # noqa: BLE001 — the review is still written
        logger.warning("limit review: %s check failed for incident %s event %s",
                       claim, incident.id, event.id, exc_info=True)
        return ClaimCheck(UNVERIFIED, "check failed", now)
    return replace(result, checked_at=now)

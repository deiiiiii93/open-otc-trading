"""Scratch-desk seeding for the limit-review smoke.

The rows a monitoring run would have written (run, evaluation) are inserted
directly, as the service's own tests do; everything downstream goes through the
real services: `incidents.reconcile_monitoring_incidents` opens the episode,
`position_terms.upsert_position_term_rows` writes the expiry the checker reads,
`definitions.add_version` drafts the new limit version, and `incidents.waive` /
`incidents.comment` write the events the review is built from.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.models import (
    LimitEvaluation,
    LimitIncident,
    LimitMonitoringRun,
    Portfolio,
    Position,
    RiskLimit,
    RiskLimitVersion,
)
from app.services.domains import position_terms
from app.services.limits import definitions, incidents
from app.services.limits.contracts import LimitActionContext, LimitVersionSpec
from app.services.limits.scopes import scope_key_for

#: Thread-less, as a REST call is: `thread_is_arena(None)` is False, so it is scored.
DESK = LimitActionContext(actor="lw", persona="limit_manager", mode="interactive")
MONITOR = LimitActionContext(actor="system", persona="limit_monitor", mode="auto")
HARD = {"delta": 500.0, "vega": 120000.0}


def _scope_config(scope_type: str, value: str, portfolio_id: int) -> dict[str, Any]:
    if scope_type == "underlying":
        return {"symbols": [value]}
    if scope_type == "portfolio":
        return {"portfolio_ids": [portfolio_id]}
    raise ValueError(scope_type)


def open_incident(session, *, tag: str, spec: dict[str, Any], now: datetime,
                  coverage_ratio: float = 1.0) -> LimitIncident:
    """One portfolio, one limit, one breach evaluation -> one open incident."""
    hard = HARD[spec["metric_kind"]]
    first_seen = now - timedelta(days=spec["days_open"], hours=2)
    portfolio = Portfolio(name=f"Jev review {tag}", base_currency="CNY")
    limit = RiskLimit(key=f"jev-review-{tag}", name=spec["limit_name"], description="",
                      category="greek", owner="market-risk", tags=[])
    session.add_all([portfolio, limit])
    session.flush()
    version = RiskLimitVersion(
        risk_limit_id=limit.id, version=1, state="active", metric_kind=spec["metric_kind"],
        source_kind="risk_run", methodology={}, scope_type=spec["scope_type"],
        scope_config=_scope_config(spec["scope_type"], spec["scope_value"], portfolio.id),
        aggregation="net", transform="absolute", comparator="upper",
        warning_upper=hard * 0.8, hard_upper=hard, unit=spec["unit"],
        freshness_policy={"max_age_seconds": 3600},
        effective_from=first_seen - timedelta(days=30), created_at=first_seen - timedelta(days=30),
    )
    session.add(version)
    session.flush()
    limit.active_version_id = version.id
    run = LimitMonitoringRun(
        trigger="scheduled", mode="auto", portfolio_id=portfolio.id, valuation_as_of=first_seen,
        source_policy="reuse_only", status="completed", summary={"breach": 1},
        definition_snapshot={}, definition_snapshot_hash="0" * 64,
        started_at=first_seen - timedelta(seconds=30), finished_at=first_seen,
    )
    session.add(run)
    session.flush()
    observed = spec["utilization"] * hard
    evaluation = LimitEvaluation(
        monitoring_run_id=run.id, limit_version_id=version.id, scope_type=spec["scope_type"],
        scope_key=scope_key_for(spec["scope_type"], spec["scope_value"]),
        scope_label=spec["scope_value"], observed_value=observed, adverse_value=observed,
        warning_upper=hard * 0.8, hard_upper=hard, utilization=spec["utilization"],
        headroom=hard - observed, governing_boundary="upper", status="breach",
        coverage_count=3, coverage_ratio=coverage_ratio, evidence={"is_fresh": True},
        evaluated_at=first_seen,
    )
    session.add(evaluation)
    session.flush()
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=MONITOR,
        occurred_at=first_seen)
    session.commit()
    return result.incidents[0]


def book_option(session, portfolio_id: int, *, underlying: str, expiry: date) -> Position:
    position = Position(portfolio_id=portfolio_id, underlying=underlying,
                        product_type="EuropeanVanillaOption",
                        product_kwargs={"strike": 3000.0, "option_type": "call",
                                        "expiry_date": expiry.isoformat()},
                        quantity=10.0, entry_price=0.0, currency="CNY", status="open")
    session.add(position)
    session.flush()
    position_terms.upsert_position_term_rows(session, position)   # the real expiry writer
    session.commit()
    return position


def draft_new_version(session, incident: LimitIncident, spec: dict[str, Any]) -> RiskLimitVersion:
    limit = session.get(RiskLimit, incident.risk_limit_id)
    hard = HARD[spec["metric_kind"]] * 1.5
    version = definitions.add_version(
        session, limit_id=limit.id, expected_row_version=limit.row_version, context=DESK,
        spec=LimitVersionSpec(
            metric_kind=spec["metric_kind"], source_kind="risk_run", methodology={},
            scope_type=spec["scope_type"],
            scope_config=_scope_config(spec["scope_type"], spec["scope_value"], incident.portfolio_id),
            aggregation="net", transform="absolute", comparator="upper",
            warning_upper=hard * 0.8, hard_upper=hard, unit=spec["unit"],
            freshness_policy={"max_age_seconds": 3600}, rationale="resize for the grown book"))
    session.commit()
    return version


def waive(session, incident_id: int, rationale: str, *, duration_days: int,
          context: LimitActionContext = DESK) -> int:
    """Waive (or re-waive) through the service; returns the new `waived` event id."""
    incident = session.get(LimitIncident, incident_id)
    session.refresh(incident)
    now = datetime.utcnow()
    incidents.waive(session, incident_id=incident_id, rationale=rationale,
                    expires_at=now + timedelta(days=duration_days),
                    expected_row_version=incident.row_version, context=context, occurred_at=now)
    session.commit()
    session.refresh(incident)
    return max(e.id for e in incident.events if e.event_type == "waived")


def comment(session, incident_id: int, actor: str, text: str, *,
            thread_id: int | None = None) -> int:
    incident = session.get(LimitIncident, incident_id)
    session.refresh(incident)
    context = LimitActionContext(actor=actor, persona="limit_manager", mode="interactive",
                                 thread_id=thread_id)
    incidents.comment(session, incident_id=incident_id, comment=text,
                      expected_row_version=incident.row_version, context=context,
                      occurred_at=datetime.utcnow())
    session.commit()
    session.refresh(incident)
    return max(e.id for e in incident.events if e.event_type == "commented")

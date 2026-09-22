"""Checkers say supported / no_evidence / unverified — never contradicted (D11)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.models import LimitSourceReference, Position
from app.services.domains import position_terms
from app.services.limits import definitions, incidents
from app.services.limits.contracts import LimitVersionSpec
from app.services.limits.review_checks import CHECKERS, ClaimCheck, run_check
from test_limit_incidents import CONTEXT, NOW, _evaluation, _fixture


def _incident(session, *, evaluation_kwargs=None):
    limit, version, run = _fixture(session)
    # The fixture stamps wall-clock created_at; the incident lives in 2026-07, so backdate
    # version 1 or it would read as "created after the incident opened".
    version.created_at = NOW - timedelta(days=1)
    evaluation = _evaluation(session, version, run, status="breach", at=NOW, utilization=1.2)
    for key, value in (evaluation_kwargs or {}).items():
        setattr(evaluation, key, value)
    session.flush()
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=CONTEXT, occurred_at=NOW)
    session.commit()
    incident = result.incidents[0]
    incidents.waive(session, incident_id=incident.id, rationale="r",
                    expires_at=NOW + timedelta(days=10), expected_row_version=incident.row_version,
                    context=CONTEXT, occurred_at=NOW + timedelta(hours=1))
    session.commit()
    session.refresh(incident)
    waived = [e for e in incident.events if e.event_type == "waived"][-1]
    return incident, waived, limit, version, run, evaluation


def _book_option(session, portfolio_id, *, position_id=None, underlying="000300", expiry: date):
    position = Position(id=position_id, portfolio_id=portfolio_id, underlying=underlying,
                        product_type="EuropeanVanillaOption",
                        product_kwargs={"strike": 100.0, "option_type": "call",
                                        "expiry_date": expiry.isoformat()},
                        quantity=1.0, entry_price=0.0, currency="USD", status="open")
    session.add(position)
    session.flush()
    position_terms.upsert_position_term_rows(session, position)   # the real expiry writer
    session.commit()
    return position


def test_position_rolling_off_supported_when_a_scoped_position_expires_in_window(session):
    incident, waived, *_ = _incident(session)          # scope position:7
    _book_option(session, incident.portfolio_id, position_id=7, expiry=NOW.date() + timedelta(days=5))
    result = CHECKERS["position_rolling_off"](session, incident, waived)
    assert result.check == "supported"
    assert result.detail == (
        f"1 position in scope expires on or before {(NOW + timedelta(days=10)).date().isoformat()}")


def test_position_rolling_off_ignores_positions_outside_scope_or_window(session):
    incident, waived, *_ = _incident(session)
    _book_option(session, incident.portfolio_id, position_id=8, expiry=NOW.date() + timedelta(days=5))   # not position 7
    _book_option(session, incident.portfolio_id, position_id=7, expiry=NOW.date() + timedelta(days=40))  # after expiry
    result = CHECKERS["position_rolling_off"](session, incident, waived)
    assert result.check == "no_evidence"
    assert "no open position in scope expires between" in result.detail


def test_data_error_supported_on_a_coverage_gap(session):
    incident, waived, *_ = _incident(session, evaluation_kwargs={"coverage_ratio": 0.8})
    result = CHECKERS["data_error"](session, incident, waived)
    assert result.check == "supported" and "coverage 0.80" in result.detail


def test_data_error_supported_on_a_stale_source_reference(session):
    incident, waived, _limit, _version, run, evaluation = _incident(session)
    ref = LimitSourceReference(monitoring_run_id=run.id, source_kind="risk_run",
                               source_status="completed", is_fresh=False,
                               completeness_diagnostics={})
    session.add(ref)
    session.flush()
    evaluation.evidence = {**(evaluation.evidence or {}), "source_reference_id": ref.id}
    session.commit()
    result = CHECKERS["data_error"](session, incident, waived)
    assert result.check == "supported" and "outside the freshness policy" in result.detail


def test_data_error_no_evidence_on_a_clean_evaluation(session):
    incident, waived, *_ = _incident(session)
    result = CHECKERS["data_error"](session, incident, waived)
    assert result.check == "no_evidence"
    assert result.detail == (
        "1 evaluation behind this incident carries no reason code, coverage gap or stale source")


def test_limit_under_review_supported_by_a_version_created_after_the_incident_opened(session):
    incident, waived, limit, _version, run, _evaluation = _incident(session)
    assert CHECKERS["limit_under_review"](session, incident, waived).check == "no_evidence"
    definitions.add_version(
        session, limit_id=limit.id, expected_row_version=limit.row_version, context=CONTEXT,
        spec=LimitVersionSpec(
            metric_kind="delta", source_kind="risk_run", methodology={},
            scope_type="portfolio", scope_config={"portfolio_ids": [run.portfolio_id]},
            aggregation="net", transform="absolute", comparator="upper",
            warning_upper=120.0, hard_upper=150.0, unit="underlying_units",
            freshness_policy={"max_age_seconds": 60}, rationale="resize for the new book"))
    session.commit()
    result = CHECKERS["limit_under_review"](session, incident, waived)
    assert result.check == "supported"
    assert result.detail.startswith("limit version 2 (draft) was created on ")


def test_run_check_degrades_a_raising_checker_and_an_unknown_claim_to_unverified(session, monkeypatch):
    incident, waived, *_ = _incident(session)
    now = datetime(2026, 9, 22, 10, 0)

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setitem(CHECKERS, "position_rolling_off", boom)
    assert run_check(session, "position_rolling_off", incident, waived, now=now) == ClaimCheck(
        "unverified", "check failed", now)
    assert run_check(session, "hedge_in_progress", incident, waived, now=now) == ClaimCheck(
        "unverified", "no checker for this claim", now)
    assert run_check(session, "limit_under_review", incident, waived, now=now).checked_at == now


def test_as_json_shape():
    now = datetime(2026, 9, 22, 10, 0)
    assert ClaimCheck("supported", "d", now).as_json() == {
        "check": "supported", "detail": "d", "checked_at": "2026-09-22T10:00:00"}

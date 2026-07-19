from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.models import (
    LimitEvaluation,
    LimitIncident,
    LimitIncidentEvent,
    LimitMonitoringRun,
    MarketSnapshot,
    Portfolio,
    RiskLimit,
    RiskLimitVersion,
    TaskRun,
)
from app.routers.limits import build_limits_router


NOW = datetime(2026, 7, 18, 9, 0)


@pytest.fixture
def limits_api(session):
    dispatched: list[tuple[int, int]] = []
    app = FastAPI()

    def get_db():
        with database.SessionLocal() as db:
            yield db

    app.include_router(
        build_limits_router(
            get_db=get_db,
            dispatch_limit_monitoring_fn=lambda task_id, run_id: dispatched.append(
                (task_id, run_id)
            ),
        )
    )
    with TestClient(app) as client:
        yield client, dispatched


def _version_payload(portfolio_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metric_kind": "delta",
        "source_kind": "risk_run",
        "methodology": {},
        "scope_type": "portfolio",
        "scope_config": {"portfolio_ids": [portfolio_id]},
        "aggregation": "net",
        "transform": "absolute",
        "comparator": "upper",
        "warning_upper": 80.0,
        "hard_upper": 100.0,
        "unit": "underlying_units",
        "freshness_policy": {"max_age_seconds": 60},
        "rationale": "Desk delta envelope",
    }
    payload.update(overrides)
    return payload


def _create_limit(
    client: TestClient,
    portfolio_id: int,
    *,
    key: str = "desk-delta",
    name: str = "Desk delta",
) -> dict[str, Any]:
    response = client.post(
        "/api/limits",
        json={
            "key": key,
            "name": name,
            "description": "Portfolio delta limit",
            "category": "greek",
            "owner": "market-risk",
            "tags": ["intraday"],
            "initial_version": _version_payload(portfolio_id),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_monitoring_episode(
    session,
    *,
    portfolio: Portfolio,
    limit: RiskLimit,
    version: RiskLimitVersion,
    status: str = "breach",
    scope_type: str = "portfolio",
    scope_key: str = "portfolio",
) -> tuple[LimitMonitoringRun, LimitEvaluation, LimitIncident]:
    run = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        market_snapshot_id=None,
        valuation_as_of=NOW,
        source_policy="reuse_only",
        max_source_age_seconds=60,
        status="completed",
        summary={"breach": 1},
        definition_snapshot={
            "inputs": {"effective_market_evidence_id": "evidence-20260718"}
        },
        definition_snapshot_hash="a" * 64,
        started_at=NOW - timedelta(seconds=5),
        finished_at=NOW,
    )
    session.add(run)
    session.flush()
    evaluation = LimitEvaluation(
        monitoring_run_id=run.id,
        limit_version_id=version.id,
        scope_type=scope_type,
        scope_key=scope_key,
        scope_label=portfolio.name,
        observed_value=110.0,
        adverse_value=110.0,
        warning_upper=80.0,
        hard_upper=100.0,
        utilization=1.1,
        headroom=-10.0,
        governing_boundary="upper",
        status=status,
        coverage_count=2,
        coverage_ratio=1.0,
        evidence={"source_reference_id": 17, "is_fresh": True},
        evaluated_at=NOW,
    )
    session.add(evaluation)
    session.flush()
    incident = LimitIncident(
        portfolio_id=portfolio.id,
        risk_limit_id=limit.id,
        scope_type=scope_type,
        scope_key=scope_key,
        scope_label=portfolio.name,
        severity=status,
        status="open",
        first_evaluation_id=evaluation.id,
        last_evaluation_id=evaluation.id,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    session.add(incident)
    session.flush()
    session.add(
        LimitIncidentEvent(
            incident_id=incident.id,
            event_type="opened",
            evaluation_id=evaluation.id,
            actor="monitor",
            persona="limit_manager",
            mode="auto",
            payload={"utilization": 1.1},
            created_at=NOW,
        )
    )
    session.commit()
    return run, evaluation, incident


def test_definition_lifecycle_is_typed_versioned_and_never_hard_deleted(
    limits_api, session
) -> None:
    client, _dispatched = limits_api
    portfolio = Portfolio(name="Definitions desk", base_currency="USD")
    session.add(portfolio)
    session.commit()

    created = _create_limit(client, portfolio.id)
    assert created["row_version"] == 1
    assert created["active_version_id"] is None
    assert created["versions"][0]["state"] == "draft"

    forbidden_attribution = client.post(
        "/api/limits",
        json={
            "key": "spoofed-limit",
            "name": "Spoofed",
            "description": "",
            "category": "greek",
            "owner": "market-risk",
            "tags": [],
            "actor": "mallory",
            "initial_version": _version_payload(portfolio.id),
        },
    )
    assert forbidden_attribution.status_code == 422

    caller_owned_activation_time = client.post(
        "/api/limits",
        json={
            "key": "caller-clock",
            "name": "Caller clock",
            "description": "",
            "category": "greek",
            "owner": "market-risk",
            "tags": [],
            "initial_version": _version_payload(
                portfolio.id, effective_from=NOW.isoformat()
            ),
        },
    )
    assert caller_owned_activation_time.status_code == 422

    patched = client.patch(
        f"/api/limits/{created['id']}",
        json={
            "expected_row_version": created["row_version"],
            "name": "Desk delta updated",
            "tags": ["intraday", "board"],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["row_version"] == 2

    stale = client.patch(
        f"/api/limits/{created['id']}",
        json={"expected_row_version": 1, "owner": "stale-owner"},
    )
    assert stale.status_code == 409

    version_added = client.post(
        f"/api/limits/{created['id']}/versions",
        json={
            "expected_row_version": 2,
            "version": _version_payload(
                portfolio.id, warning_upper=70.0, hard_upper=90.0
            ),
        },
    )
    assert version_added.status_code == 201, version_added.text
    assert version_added.json()["row_version"] == 3
    assert [row["version"] for row in version_added.json()["versions"]] == [1, 2]

    second = _create_limit(client, portfolio.id, key="other-delta", name="Other delta")
    foreign_activate = client.post(
        f"/api/limits/{created['id']}/versions/"
        f"{second['versions'][0]['id']}/activate",
        json={"expected_row_version": 3},
    )
    assert foreign_activate.status_code == 404

    caller_owned_activation_time = client.post(
        f"/api/limits/{created['id']}/versions/"
        f"{version_added.json()['versions'][1]['id']}/activate",
        json={
            "expected_row_version": 3,
            "activated_at": NOW.isoformat(),
        },
    )
    assert caller_owned_activation_time.status_code == 422

    activated = client.post(
        f"/api/limits/{created['id']}/versions/"
        f"{version_added.json()['versions'][1]['id']}/activate",
        json={"expected_row_version": 3},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["row_version"] == 4
    assert activated.json()["active_version"]["state"] == "active"
    assert activated.json()["active_version"]["effective_from"] is not None

    listed = client.get(
        "/api/limits",
        params={
            "category": "greek",
            "owner": "market-risk",
            "state": "active",
            "scope_type": "portfolio",
            "tag": "board",
            "limit": 1,
            "offset": 0,
        },
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    versions = client.get(f"/api/limits/{created['id']}/versions")
    assert versions.status_code == 200
    assert [row["version"] for row in versions.json()] == [1, 2]

    deactivated = client.post(
        f"/api/limits/{created['id']}/deactivate",
        json={"expected_row_version": 4},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["row_version"] == 5
    assert deactivated.json()["active_version_id"] is None

    retired = client.post(
        f"/api/limits/{created['id']}/retire",
        json={"expected_row_version": 5},
    )
    assert retired.status_code == 200
    assert retired.json()["row_version"] == 6
    assert {row["state"] for row in retired.json()["versions"]} == {
        "retired",
        "superseded",
    }

    missing = client.get("/api/limits/999999")
    assert missing.status_code == 404
    assert all(
        "delete" not in methods
        for path, methods in client.app.openapi()["paths"].items()
        if path.startswith("/api/limits")
    )


def test_monitoring_queue_dispatch_and_reads_are_portfolio_scoped(
    limits_api, session
) -> None:
    client, dispatched = limits_api
    first = Portfolio(name="Monitoring desk A", base_currency="USD")
    second = Portfolio(name="Monitoring desk B", base_currency="USD")
    snapshot = MarketSnapshot(
        name="Morning close",
        source="manual",
        symbol="SPX",
        asset_class="index",
        valuation_date=NOW,
        data={"spot": 6000.0},
        source_metadata={},
    )
    session.add_all([first, second, snapshot])
    session.commit()

    limit = _create_limit(client, first.id)
    activated = client.post(
        f"/api/limits/{limit['id']}/versions/{limit['versions'][0]['id']}/activate",
        json={"expected_row_version": 1},
    )
    assert activated.status_code == 200

    missing_evidence = client.post(
        "/api/limit-monitoring/runs",
        json={
            "portfolio_id": first.id,
            "valuation_as_of": NOW.isoformat(),
            "source_policy": "reuse_only",
        },
    )
    assert missing_evidence.status_code == 422

    spoofed_context = client.post(
        "/api/limit-monitoring/runs",
        json={
            "portfolio_id": first.id,
            "market_snapshot_id": snapshot.id,
            "valuation_as_of": NOW.isoformat(),
            "source_policy": "reuse_only",
            "mode": "yolo",
        },
    )
    assert spoofed_context.status_code == 422

    queue_payload = {
        "portfolio_id": first.id,
        "market_snapshot_id": snapshot.id,
        "effective_market_evidence_id": "manual:spx:20260718",
        "valuation_as_of": NOW.isoformat(),
        "source_policy": "reuse_only",
        "max_source_age_seconds": 300,
    }
    queued = client.post("/api/limit-monitoring/runs", json=queue_payload)
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["task_id"] > 0
    assert body["trigger"] == "manual"
    assert body["mode"] == "interactive"
    assert dispatched == [(body["task_id"], body["id"])]

    overlapping = client.post(
        "/api/limit-monitoring/runs",
        json=queue_payload,
    )
    assert overlapping.status_code == 409, overlapping.text
    assert "already has active limit monitoring run" in overlapping.json()["detail"]
    assert dispatched == [(body["task_id"], body["id"])]

    own_list = client.get(
        "/api/limit-monitoring/runs",
        params={"portfolio_id": first.id, "limit": 10, "offset": 0},
    )
    assert own_list.status_code == 200
    assert own_list.json()["total"] == 1
    assert own_list.json()["items"][0]["task_id"] == body["task_id"]

    own_detail = client.get(
        f"/api/limit-monitoring/runs/{body['id']}",
        params={"portfolio_id": first.id},
    )
    assert own_detail.status_code == 200
    assert own_detail.json()["effective_market_evidence_id"] == ("manual:spx:20260718")

    hidden_detail = client.get(
        f"/api/limit-monitoring/runs/{body['id']}",
        params={"portfolio_id": second.id},
    )
    assert hidden_detail.status_code == 404
    hidden_evaluations = client.get(
        f"/api/limit-monitoring/runs/{body['id']}/evaluations",
        params={"portfolio_id": second.id},
    )
    assert hidden_evaluations.status_code == 404

    own_evaluations = client.get(
        f"/api/limit-monitoring/runs/{body['id']}/evaluations",
        params={"portfolio_id": first.id, "limit": 10, "offset": 0},
    )
    assert own_evaluations.status_code == 200
    assert own_evaluations.json() == {"items": [], "total": 0}

    missing_portfolio = client.get(
        "/api/limit-monitoring/runs",
        params={"portfolio_id": 999999},
    )
    assert missing_portfolio.status_code == 404


def test_dispatch_failure_marks_queue_terminal_and_allows_retry(session) -> None:
    attempts: list[tuple[int, int]] = []
    app = FastAPI()

    def get_db():
        with database.SessionLocal() as db:
            yield db

    def dispatch(task_id: int, run_id: int) -> None:
        attempts.append((task_id, run_id))
        if len(attempts) == 1:
            raise RuntimeError("executor offline")

    app.include_router(
        build_limits_router(
            get_db=get_db,
            dispatch_limit_monitoring_fn=dispatch,
        )
    )
    portfolio = Portfolio(name="Dispatch recovery desk", base_currency="USD")
    snapshot = MarketSnapshot(
        name="Dispatch recovery evidence",
        source="manual",
        symbol="SPX",
        asset_class="index",
        valuation_date=NOW,
        data={"spot": 6000.0},
        source_metadata={},
    )
    session.add_all([portfolio, snapshot])
    session.commit()
    payload = {
        "portfolio_id": portfolio.id,
        "market_snapshot_id": snapshot.id,
        "effective_market_evidence_id": "manual:spx:dispatch-recovery",
        "valuation_as_of": NOW.isoformat(),
        "source_policy": "reuse_only",
        "max_source_age_seconds": 300,
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        failed = client.post("/api/limit-monitoring/runs", json=payload)
        assert failed.status_code == 500
        assert len(attempts) == 1

        failed_task_id, failed_run_id = attempts[0]
        with database.SessionLocal() as verification:
            failed_run = verification.get(LimitMonitoringRun, failed_run_id)
            failed_task = verification.get(TaskRun, failed_task_id)
            assert failed_run is not None
            assert failed_run.status == "failed"
            assert failed_run.finished_at is not None
            assert failed_task is not None
            assert failed_task.status == "failed"
            assert failed_task.message == "Limit monitoring dispatch failed"
            assert failed_task.error == "executor offline"
            assert failed_task.finished_at is not None

        retried = client.post("/api/limit-monitoring/runs", json=payload)
        assert retried.status_code == 202, retried.text
        assert len(attempts) == 2
        assert attempts[1] == (retried.json()["task_id"], retried.json()["id"])


def test_evaluation_detail_is_typed_and_portfolio_scoped(
    limits_api, session
) -> None:
    client, _dispatched = limits_api
    first = Portfolio(name="Evaluation desk A", base_currency="USD")
    second = Portfolio(name="Evaluation desk B", base_currency="USD")
    session.add_all([first, second])
    session.flush()
    limit = RiskLimit(
        key="evaluation-delta-a",
        name="Evaluation delta A",
        description="",
        category="greek",
        owner="market-risk",
        tags=["intraday"],
    )
    version = RiskLimitVersion(
        risk_limit=limit,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="portfolio",
        scope_config={"portfolio_ids": [first.id]},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=80.0,
        hard_upper=100.0,
        unit="underlying_units",
        freshness_policy={"max_age_seconds": 60},
        effective_from=NOW - timedelta(days=1),
    )
    session.add_all([limit, version])
    session.flush()
    limit.active_version_id = version.id
    run, evaluation, _incident = _seed_monitoring_episode(
        session,
        portfolio=first,
        limit=limit,
        version=version,
    )

    detail = client.get(
        f"/api/limit-evaluations/{evaluation.id}",
        params={"portfolio_id": first.id},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json() == {
        "id": evaluation.id,
        "monitoring_run_id": run.id,
        "limit_version_id": version.id,
        "scope_type": "portfolio",
        "scope_key": "portfolio",
        "scope_label": first.name,
        "observed_value": 110.0,
        "adverse_value": 110.0,
        "warning_lower": None,
        "warning_upper": 80.0,
        "hard_lower": None,
        "hard_upper": 100.0,
        "utilization": 1.1,
        "headroom": -10.0,
        "governing_boundary": "upper",
        "status": "breach",
        "reason_code": None,
        "reason": None,
        "coverage_count": 2,
        "coverage_ratio": 1.0,
        "evidence": {"source_reference_id": 17, "is_fresh": True},
        "evaluated_at": NOW.isoformat(),
    }

    assert client.get(f"/api/limit-evaluations/{evaluation.id}").status_code == 422

    hidden = client.get(
        f"/api/limit-evaluations/{evaluation.id}",
        params={"portfolio_id": second.id},
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "limit evaluation not found"

    missing = client.get(
        "/api/limit-evaluations/999999",
        params={"portfolio_id": first.id},
    )
    assert missing.status_code == 404


def test_incident_ledger_actions_dashboard_and_summary_are_scoped(
    limits_api, session
) -> None:
    client, _dispatched = limits_api
    first = Portfolio(name="Incident desk A", base_currency="USD")
    second = Portfolio(name="Incident desk B", base_currency="USD")
    limit = RiskLimit(
        key="incident-delta-a",
        name="Incident delta A",
        description="",
        category="greek",
        owner="market-risk",
        tags=["intraday"],
    )
    version = RiskLimitVersion(
        risk_limit=limit,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="portfolio",
        scope_config={},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=80.0,
        hard_upper=100.0,
        unit="underlying_units",
        freshness_policy={"max_age_seconds": 60},
        effective_from=NOW - timedelta(days=1),
    )
    session.add_all([first, second, limit, version])
    session.flush()
    limit.active_version_id = version.id
    _run, _evaluation, incident = _seed_monitoring_episode(
        session, portfolio=first, limit=limit, version=version
    )

    listed = client.get(
        "/api/limit-incidents",
        params={
            "portfolio_id": first.id,
            "status": "open",
            "severity": "breach",
            "limit": 20,
            "offset": 0,
        },
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["portfolio_id"] == first.id
    assert listed.json()["items"][0]["risk_limit"]["owner"] == "market-risk"

    hidden_list = client.get("/api/limit-incidents", params={"portfolio_id": second.id})
    assert hidden_list.status_code == 200
    assert hidden_list.json()["items"] == []
    hidden_detail = client.get(
        f"/api/limit-incidents/{incident.id}",
        params={"portfolio_id": second.id},
    )
    assert hidden_detail.status_code == 404
    missing_portfolio = client.get(
        "/api/limit-incidents",
        params={"portfolio_id": 999999},
    )
    assert missing_portfolio.status_code == 404

    acknowledged = client.post(
        f"/api/limit-incidents/{incident.id}/acknowledge",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 1},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "acknowledged"
    assert acknowledged.json()["row_version"] == 2
    assert acknowledged.json()["events"][-1]["actor"] == "desk_user"
    assert acknowledged.json()["events"][-1]["mode"] == "interactive"

    stale = client.post(
        f"/api/limit-incidents/{incident.id}/assign",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 1, "assignee": "alice"},
    )
    assert stale.status_code == 409

    assigned = client.post(
        f"/api/limit-incidents/{incident.id}/assign",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 2, "assignee": "alice"},
    )
    assert assigned.status_code == 200
    commented = client.post(
        f"/api/limit-incidents/{incident.id}/comments",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 3, "comment": "Hedge is in progress"},
    )
    assert commented.status_code == 200
    resolved = client.post(
        f"/api/limit-incidents/{incident.id}/resolve",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 4},
    )
    assert resolved.status_code == 200
    reopened = client.post(
        f"/api/limit-incidents/{incident.id}/reopen",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 5},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"

    forbidden_attribution = client.post(
        f"/api/limit-incidents/{incident.id}/comments",
        params={"portfolio_id": first.id},
        json={
            "expected_row_version": 6,
            "comment": "spoof",
            "actor": "mallory",
        },
    )
    assert forbidden_attribution.status_code == 422

    dashboard = client.get(
        "/api/limit-monitoring/dashboard",
        params={"portfolio_id": first.id, "trend_limit": 10},
    )
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["summary"]["breaches"] == 1
    assert dashboard.json()["summary"]["highest_utilization"] == 1.1
    assert dashboard.json()["current_evaluations"][0]["coverage_ratio"] == 1.0
    assert dashboard.json()["evaluation_groups"][0]["category"] == "greek"
    assert dashboard.json()["trends"][0]["run_id"] > 0

    summary = client.get(
        "/api/limit-monitoring/summary", params={"portfolio_id": first.id}
    )
    assert summary.status_code == 200
    assert summary.json()["breaches"] == 1
    assert summary.json()["latest_incident_event_id"] > 0
    assert "latest_notification_id" not in summary.json()


def test_shared_scope_incidents_remain_isolated_by_portfolio(
    limits_api,
    session,
) -> None:
    client, _dispatched = limits_api
    first = Portfolio(name="Shared scope desk A", base_currency="USD")
    second = Portfolio(name="Shared scope desk B", base_currency="USD")
    limit = RiskLimit(
        key="shared-underlying-delta",
        name="Shared underlying delta",
        description="",
        category="greek",
        owner="market-risk",
        tags=[],
    )
    version = RiskLimitVersion(
        risk_limit=limit,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="underlying",
        scope_config={"symbols": ["SPX"]},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=80.0,
        hard_upper=100.0,
        unit="underlying_units",
        freshness_policy={"max_age_seconds": 60},
        effective_from=NOW - timedelta(days=1),
    )
    session.add_all([first, second, limit, version])
    session.flush()
    limit.active_version_id = version.id
    _first_run, _first_evaluation, first_incident = _seed_monitoring_episode(
        session,
        portfolio=first,
        limit=limit,
        version=version,
        scope_type="underlying",
        scope_key="underlying:SPX",
    )
    _second_run, _second_evaluation, second_incident = _seed_monitoring_episode(
        session,
        portfolio=second,
        limit=limit,
        version=version,
        scope_type="underlying",
        scope_key="underlying:SPX",
    )
    assert first_incident.id != second_incident.id

    first_list = client.get(
        "/api/limit-incidents",
        params={"portfolio_id": first.id},
    )
    second_list = client.get(
        "/api/limit-incidents",
        params={"portfolio_id": second.id},
    )
    assert [row["id"] for row in first_list.json()["items"]] == [first_incident.id]
    assert [row["id"] for row in second_list.json()["items"]] == [second_incident.id]

    cross_portfolio_detail = client.get(
        f"/api/limit-incidents/{first_incident.id}",
        params={"portfolio_id": second.id},
    )
    assert cross_portfolio_detail.status_code == 404
    cross_portfolio_action = client.post(
        f"/api/limit-incidents/{first_incident.id}/acknowledge",
        params={"portfolio_id": second.id},
        json={"expected_row_version": 1},
    )
    assert cross_portfolio_action.status_code == 404

    acknowledged = client.post(
        f"/api/limit-incidents/{first_incident.id}/acknowledge",
        params={"portfolio_id": first.id},
        json={"expected_row_version": 1},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "acknowledged"
    untouched = client.get(
        f"/api/limit-incidents/{second_incident.id}",
        params={"portfolio_id": second.id},
    )
    assert untouched.status_code == 200
    assert untouched.json()["status"] == "open"

    first_dashboard = client.get(
        "/api/limit-monitoring/dashboard",
        params={"portfolio_id": first.id},
    )
    second_dashboard = client.get(
        "/api/limit-monitoring/dashboard",
        params={"portfolio_id": second.id},
    )
    assert [row["id"] for row in first_dashboard.json()["active_incidents"]] == [
        first_incident.id
    ]
    assert [row["id"] for row in second_dashboard.json()["active_incidents"]] == [
        second_incident.id
    ]


def test_dashboard_uses_one_latest_terminal_run_for_current_state(
    limits_api,
    session,
) -> None:
    client, _dispatched = limits_api
    portfolio = Portfolio(name="Terminal dashboard desk", base_currency="USD")
    session.add(portfolio)
    session.flush()

    versions: list[RiskLimitVersion] = []
    for suffix in ("old", "current"):
        limit = RiskLimit(
            key=f"dashboard-{suffix}",
            name=f"Dashboard {suffix}",
            description="",
            category="greek",
            owner="market-risk",
            tags=[],
        )
        session.add(limit)
        session.flush()
        version = RiskLimitVersion(
            risk_limit_id=limit.id,
            version=1,
            state="active",
            metric_kind="delta",
            source_kind="risk_run",
            methodology={},
            scope_type="portfolio",
            scope_config={"portfolio_ids": [portfolio.id]},
            aggregation="net",
            transform="absolute",
            comparator="upper",
            warning_upper=80.0,
            hard_upper=100.0,
            unit="underlying_units",
            freshness_policy={"max_age_seconds": 60},
            effective_from=NOW - timedelta(days=1),
            activated_at=NOW - timedelta(days=1),
        )
        session.add(version)
        session.flush()
        limit.active_version_id = version.id
        versions.append(version)

    old_run = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=NOW - timedelta(minutes=2),
        source_policy="reuse_only",
        status="completed",
        summary={"breach": 1},
        definition_snapshot={},
        definition_snapshot_hash="b" * 64,
        created_at=NOW - timedelta(minutes=2),
    )
    latest_terminal = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=NOW - timedelta(minutes=1),
        source_policy="reuse_only",
        status="completed",
        summary={"warning": 1},
        definition_snapshot={},
        definition_snapshot_hash="c" * 64,
        created_at=NOW - timedelta(minutes=1),
    )
    queued = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=NOW,
        source_policy="reuse_only",
        status="queued",
        summary={},
        definition_snapshot={},
        definition_snapshot_hash="d" * 64,
        created_at=NOW,
    )
    backdated_terminal = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=NOW - timedelta(minutes=10),
        source_policy="reuse_only",
        status="completed",
        summary={"breach": 1},
        definition_snapshot={},
        definition_snapshot_hash="e" * 64,
        created_at=NOW + timedelta(minutes=1),
    )
    session.add_all([old_run, latest_terminal, queued, backdated_terminal])
    session.flush()
    old_evaluation = LimitEvaluation(
        monitoring_run_id=old_run.id,
        limit_version_id=versions[0].id,
        scope_type="portfolio",
        scope_key=f"portfolio:{portfolio.id}",
        scope_label=portfolio.name,
        observed_value=110.0,
        adverse_value=110.0,
        warning_upper=80.0,
        hard_upper=100.0,
        utilization=1.1,
        headroom=-10.0,
        status="breach",
        evidence={},
    )
    current_evaluation = LimitEvaluation(
        monitoring_run_id=latest_terminal.id,
        limit_version_id=versions[1].id,
        scope_type="portfolio",
        scope_key=f"portfolio:{portfolio.id}",
        scope_label=portfolio.name,
        observed_value=85.0,
        adverse_value=85.0,
        warning_upper=80.0,
        hard_upper=100.0,
        utilization=0.85,
        headroom=15.0,
        status="warning",
        evidence={},
    )
    backdated_evaluation = LimitEvaluation(
        monitoring_run_id=backdated_terminal.id,
        limit_version_id=versions[0].id,
        scope_type="portfolio",
        scope_key=f"portfolio:{portfolio.id}",
        scope_label=portfolio.name,
        observed_value=120.0,
        adverse_value=120.0,
        warning_upper=80.0,
        hard_upper=100.0,
        utilization=1.2,
        headroom=-20.0,
        status="breach",
        evidence={},
    )
    session.add_all([old_evaluation, current_evaluation, backdated_evaluation])
    session.commit()

    response = client.get(
        "/api/limit-monitoring/dashboard",
        params={"portfolio_id": portfolio.id},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_run"]["id"] == backdated_terminal.id
    assert body["current_evidence_run"]["id"] == latest_terminal.id
    assert body["summary"]["warnings"] == 1
    assert body["summary"]["breaches"] == 0
    assert [row["id"] for row in body["current_evaluations"]] == [current_evaluation.id]

    session.delete(current_evaluation)
    session.commit()
    zero_evaluations = client.get(
        "/api/limit-monitoring/dashboard",
        params={"portfolio_id": portfolio.id},
    )

    assert zero_evaluations.status_code == 200, zero_evaluations.text
    zero_body = zero_evaluations.json()
    assert zero_body["latest_run"]["id"] == backdated_terminal.id
    assert zero_body["current_evidence_run"]["id"] == latest_terminal.id
    assert zero_body["current_evaluations"] == []


def test_market_snapshot_selector_is_bounded_filtered_and_newest_first(
    limits_api, session
) -> None:
    client, _dispatched = limits_api
    snapshots = [
        MarketSnapshot(
            name="Old manual",
            source="manual",
            symbol="SPX",
            asset_class="index",
            valuation_date=NOW - timedelta(hours=2),
            data={"spot": 5990.0},
            source_metadata={},
        ),
        MarketSnapshot(
            name="New manual",
            source="manual",
            symbol="SPX",
            asset_class="index",
            valuation_date=NOW,
            data={"spot": 6000.0},
            source_metadata={},
        ),
        MarketSnapshot(
            name="Vendor",
            source="vendor",
            symbol="SPX",
            asset_class="index",
            valuation_date=NOW + timedelta(hours=1),
            data={"spot": 6010.0},
            source_metadata={},
        ),
    ]
    session.add_all(snapshots)
    session.commit()

    response = client.get(
        "/api/market-data/snapshots",
        params={
            "source": "manual",
            "as_of": NOW.isoformat(),
            "limit": 1,
            "offset": 0,
        },
    )
    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["New manual"]

    invalid_limit = client.get("/api/market-data/snapshots", params={"limit": 201})
    assert invalid_limit.status_code == 422

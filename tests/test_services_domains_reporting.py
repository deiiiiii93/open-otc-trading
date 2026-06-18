from __future__ import annotations

from datetime import datetime

import pytest

from app.services.domains import reporting as reporting_svc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert(session, **overrides):
    from app.models import ReportJob

    base = dict(
        report_type="portfolio",
        status="completed",
        request_payload={"portfolio_id": 1, "title": "T"},
        result_payload={},
        artifact_paths={},
    )
    base.update(overrides)
    job = ReportJob(**base)
    session.add(job)
    session.commit()
    return job


def _insert_portfolio(session, portfolio_id: int = 42):
    from app.models import Portfolio

    portfolio = Portfolio(
        id=portfolio_id,
        name=f"Portfolio {portfolio_id}",
        base_currency="CNY",
    )
    session.add(portfolio)
    session.commit()
    return portfolio


# ----- list_reports -----------------------------------------------------------


def test_list_reports_empty():
    rows = reporting_svc.list_reports()
    assert rows == []


def test_list_reports_newest_first():
    from app import database

    with database.SessionLocal() as session:
        a = _insert(session, request_payload={"portfolio_id": 1, "title": "old"})
        a.created_at = datetime(2025, 1, 1, 0, 0, 0)
        b = _insert(session, request_payload={"portfolio_id": 1, "title": "new"})
        b.created_at = datetime(2025, 6, 1, 0, 0, 0)
        session.commit()

    rows = reporting_svc.list_reports()
    assert [r.request_payload["title"] for r in rows] == ["new", "old"]


def test_list_reports_filters_by_portfolio_id():
    from app import database

    with database.SessionLocal() as session:
        _insert(session, request_payload={"portfolio_id": 1, "title": "match"})
        _insert(session, request_payload={"portfolio_id": 2, "title": "skip"})

    rows = reporting_svc.list_reports(portfolio_id=1)
    assert [r.request_payload["title"] for r in rows] == ["match"]


def test_list_reports_filters_by_report_type():
    from app import database

    with database.SessionLocal() as session:
        _insert(session, report_type="portfolio")
        _insert(session, report_type="risk")

    rows = reporting_svc.list_reports(report_type="risk")
    assert [r.report_type for r in rows] == ["risk"]


def test_list_reports_filters_by_status():
    from app import database

    with database.SessionLocal() as session:
        _insert(session, status="queued")
        _insert(session, status="completed")

    rows = reporting_svc.list_reports(status="completed")
    assert [r.status for r in rows] == ["completed"]


def test_list_reports_respects_limit():
    from app import database

    with database.SessionLocal() as session:
        for i in range(5):
            _insert(session)

    rows = reporting_svc.list_reports(limit=2)
    assert len(rows) == 2


def test_list_reports_streams_with_portfolio_filter():
    """Portfolio filter applies in Python: older matching rows must surface."""
    from app import database

    with database.SessionLocal() as session:
        # Newest rows belong to the wrong portfolio.
        for i in range(3):
            _insert(session, request_payload={"portfolio_id": 99, "title": f"skip-{i}"})
        # One older matching row, inserted last so it has the highest id
        # (because we order by created_at DESC then id DESC).
        match = _insert(
            session,
            request_payload={"portfolio_id": 1, "title": "found"},
        )
        match.created_at = datetime(2020, 1, 1, 0, 0, 0)
        session.commit()

    rows = reporting_svc.list_reports(portfolio_id=1, limit=10)
    assert [r.request_payload["title"] for r in rows] == ["found"]


# ----- get_report -------------------------------------------------------------


def test_get_report_not_found():
    assert reporting_svc.get_report(report_id=9999) is None


def test_get_report_returns_row():
    from app import database

    with database.SessionLocal() as session:
        job = _insert(session, request_payload={"portfolio_id": 7, "title": "T"})
        job_id = job.id

    row = reporting_svc.get_report(report_id=job_id)
    assert row is not None
    assert row.id == job_id
    assert row.request_payload["portfolio_id"] == 7


# ----- create_report ----------------------------------------------------------


def test_create_report_returns_ids(monkeypatch):
    """Service queues a job + task; submit_async_task is patched to no-op."""
    from app import database

    with database.SessionLocal() as session:
        _insert_portfolio(session, portfolio_id=42)

    submitted: list[tuple] = []
    monkeypatch.setattr(
        "app.services.domains.reporting.submit_async_task",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )
    payload = reporting_svc.create_report(
        portfolio_id=42, report_type="portfolio", title="Q3", pricing_profile_id=3
    )
    assert payload["pricing_parameter_profile_id"] == 3
    assert payload["status"] == "queued"
    assert isinstance(payload["report_job_id"], int)
    assert isinstance(payload["task_id"], int)
    assert len(submitted) == 1


def test_create_report_rejects_unsupported_type():
    with pytest.raises(ValueError, match="unsupported report_type"):
        reporting_svc.create_report(portfolio_id=1, report_type="bogus")  # type: ignore[arg-type]


# ----- run_batch --------------------------------------------------------------


def test_run_batch_shapes_envelope():
    payload = reporting_svc.run_batch(
        title="Desk Report",
        report_type="portfolio",
        risk_summary={"delta": 1.5, "vega": 0.2},
    )
    assert payload["title"] == "Desk Report"
    assert payload["report_type"] == "portfolio"
    assert payload["status"] == "ready"
    assert payload["summary"] == {"delta": 1.5, "vega": 0.2}
    assert "artifact_hint" in payload

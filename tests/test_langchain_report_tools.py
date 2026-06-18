"""Unit tests for list_reports and get_report langchain tools."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import database
from app.config import Settings
from app.models import ReportJob


def _configure_test_db(tmp_path: Path) -> None:
    """Configure an isolated SQLite DB for this test (repo convention from
    tests/test_agent_tools.py)."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()


def _insert_report(
    *,
    report_type: str,
    status: str,
    portfolio_id: int | None,
    title: str,
    artifact_paths: dict | None = None,
) -> int:
    """Insert a ReportJob row and return its id."""
    with database.SessionLocal() as session:
        job = ReportJob(
            report_type=report_type,
            status=status,
            request_payload={"portfolio_id": portfolio_id, "title": title},
            result_payload={},
            artifact_paths=artifact_paths or {},
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def test_list_reports_returns_empty_when_no_rows(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    result = list_reports_tool.invoke({})
    assert result == {"reports": [], "total": 0}


def test_list_reports_returns_newest_first(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    first_id = _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="First"
    )
    second_id = _insert_report(
        report_type="risk", status="completed", portfolio_id=1, title="Second"
    )

    result = list_reports_tool.invoke({})
    assert result["total"] == 2
    assert [r["report_id"] for r in result["reports"]] == [second_id, first_id]
    assert result["reports"][0]["title"] == "Second"
    assert result["reports"][0]["portfolio_id"] == 1


def test_list_reports_filters_by_portfolio_id(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="P1"
    )
    _insert_report(
        report_type="risk", status="completed", portfolio_id=2, title="P2"
    )

    result = list_reports_tool.invoke({"portfolio_id": 1})
    assert result["total"] == 1
    assert result["reports"][0]["portfolio_id"] == 1
    assert result["reports"][0]["title"] == "P1"


def test_list_reports_filters_by_report_type(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="P"
    )
    _insert_report(
        report_type="risk", status="completed", portfolio_id=1, title="R"
    )
    _insert_report(
        report_type="rfq", status="completed", portfolio_id=1, title="Q"
    )

    result = list_reports_tool.invoke({"report_type": "risk"})
    assert result["total"] == 1
    assert result["reports"][0]["report_type"] == "risk"


def test_list_reports_filters_by_status(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    _insert_report(
        report_type="portfolio", status="completed", portfolio_id=1, title="Done"
    )
    _insert_report(
        report_type="portfolio", status="queued", portfolio_id=1, title="Queued"
    )

    result = list_reports_tool.invoke({"status": "completed"})
    assert result["total"] == 1
    assert result["reports"][0]["status"] == "completed"


def test_list_reports_respects_limit(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    for i in range(5):
        _insert_report(
            report_type="portfolio",
            status="completed",
            portfolio_id=1,
            title=f"R{i}",
        )

    result = list_reports_tool.invoke({"limit": 3})
    assert result["total"] == 3


def test_list_reports_surfaces_artifact_paths(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import list_reports_tool

    _insert_report(
        report_type="portfolio",
        status="completed",
        portfolio_id=1,
        title="With artifacts",
        artifact_paths={"html": "/artifacts/report-1.html", "excel": "/artifacts/report-1.xlsx"},
    )

    result = list_reports_tool.invoke({})
    assert result["total"] == 1
    paths = result["reports"][0]["artifact_paths"]
    assert paths == {"html": "/artifacts/report-1.html", "excel": "/artifacts/report-1.xlsx"}


def test_get_report_returns_full_row(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import get_report_tool

    rid = _insert_report(
        report_type="risk",
        status="completed",
        portfolio_id=42,
        title="Q3 Risk Review",
        artifact_paths={"html": "/artifacts/report-7.html", "excel": "/artifacts/report-7.xlsx"},
    )

    result = get_report_tool.invoke({"report_id": rid})
    assert result["report_id"] == rid
    assert result["report_type"] == "risk"
    assert result["status"] == "completed"
    assert result["portfolio_id"] == 42
    assert result["title"] == "Q3 Risk Review"
    assert result["artifact_paths"] == {
        "html": "/artifacts/report-7.html",
        "excel": "/artifacts/report-7.xlsx",
    }
    assert "created_at" in result and result["created_at"] is not None
    assert "summary" in result
    assert "result_payload" in result


def test_get_report_surfaces_result_payload_as_summary(tmp_path: Path):
    """For the agent, `summary` is the most useful slice of result_payload."""
    _configure_test_db(tmp_path)
    from app.tools.reporting import get_report_tool

    with database.SessionLocal() as session:
        job = ReportJob(
            report_type="portfolio",
            status="completed",
            request_payload={"portfolio_id": 1, "title": "Summary test"},
            result_payload={
                "summary": {"totals": {"delta": 100.0, "gamma": 5.0}},
                "rows": [{"position_id": 1, "value": 1.0}],
            },
            artifact_paths={"html": "/artifacts/x.html"},
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        rid = job.id

    result = get_report_tool.invoke({"report_id": rid})
    assert result["summary"] == {"totals": {"delta": 100.0, "gamma": 5.0}}
    assert result["result_payload"]["rows"] == [{"position_id": 1, "value": 1.0}]


def test_get_report_missing_id_raises(tmp_path: Path):
    _configure_test_db(tmp_path)
    from app.tools.reporting import get_report_tool

    with pytest.raises(ValueError) as exc:
        get_report_tool.invoke({"report_id": 99999})
    assert "99999" in str(exc.value)
    assert "not found" in str(exc.value).lower()

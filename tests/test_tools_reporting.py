from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from app.tools.reporting import (
    create_report_tool,
    get_report_tool,
    list_reports_tool,
    run_report_batch_tool,
    write_report_artifact_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _job_stub(**overrides):
    base = dict(
        id=1,
        report_type="portfolio",
        status="completed",
        request_payload={"portfolio_id": 7, "title": "T"},
        result_payload={},
        artifact_paths={"html": "/tmp/foo.html"},
        created_at=datetime(2025, 1, 1, 0, 0, 0),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ----- list_reports_tool ------------------------------------------------------


def test_list_reports_tool_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.reporting.list_reports",
        lambda **kwargs: [],
    )
    result = list_reports_tool.invoke({})
    assert result == {"reports": [], "total": 0}


def test_list_reports_tool_shapes_rows(monkeypatch):
    job = _job_stub(id=42)
    monkeypatch.setattr(
        "app.services.domains.reporting.list_reports",
        lambda **kwargs: [job],
    )
    result = list_reports_tool.invoke({"portfolio_id": 7})
    assert result["total"] == 1
    row = result["reports"][0]
    assert row["report_id"] == 42
    assert row["artifact_paths"] == {"html": "/artifacts/foo.html"}
    assert row["created_at"] == "2025-01-01T00:00:00"


def test_list_reports_tool_passes_filters(monkeypatch):
    captured: dict = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.services.domains.reporting.list_reports", fake_list)
    list_reports_tool.invoke(
        {
            "portfolio_id": 1,
            "report_type": "risk",
            "status": "completed",
            "limit": 5,
        }
    )
    assert captured == {
        "portfolio_id": 1,
        "report_type": "risk",
        "status": "completed",
        "limit": 5,
    }


# ----- get_report_tool --------------------------------------------------------


def test_get_report_tool_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.reporting.get_report",
        lambda *, report_id: None,
    )
    with pytest.raises(ValueError, match="Report job not found"):
        get_report_tool.invoke({"report_id": 9999})


def test_get_report_tool_shapes_full(monkeypatch):
    job = _job_stub(
        id=7,
        report_type="portfolio",
        result_payload={"risk": {"totals": {"delta": 1.5}}, "portfolio": {"id": 1}},
        artifact_paths={"html": "/var/foo.html"},
    )
    monkeypatch.setattr(
        "app.services.domains.reporting.get_report",
        lambda *, report_id: job,
    )
    result = get_report_tool.invoke({"report_id": 7})
    assert result["report_id"] == 7
    assert result["portfolio_id"] == 7
    assert result["summary"]["totals"] == {"delta": 1.5}
    assert result["artifact_paths"] == {"html": "/artifacts/foo.html"}


# ----- write_report_artifact_tool --------------------------------------------


def test_write_report_artifact_tool_creates_markdown():
    result = write_report_artifact_tool.invoke(
        {
            "title": "Near KO Report",
            "format": "markdown",
            "body_markdown": "# Near KO\n\nSummary.",
            "filename_stem": "near ko/report",
        }
    )

    assert result["format"] == "markdown"
    assert result["file_path"].startswith("/trading_desk/reports/near_ko_report_")
    assert result["file_path"].endswith(".md")
    artifact = result["artifacts"][0]
    assert artifact["path"] == result["file_path"]
    assert artifact["content"] == "# Near KO\n\nSummary."
    assert artifact["size_bytes"] == result["size_bytes"]


def test_write_report_artifact_tool_creates_html():
    result = write_report_artifact_tool.invoke(
        {
            "title": "Board Report",
            "format": "html",
            "body_markdown": "# Board\n\n- item",
            "body_html": "<html><body><h1>Board</h1></body></html>",
        }
    )

    assert result["file_path"].endswith(".html")
    assert result["artifacts"][0]["content"].startswith("<html>")


def test_write_report_artifact_tool_creates_valid_docx(tmp_path):
    result = write_report_artifact_tool.invoke(
        {
            "title": "DOCX Report",
            "format": "docx",
            "body_markdown": "# Title\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
        }
    )

    assert result["file_path"].endswith(".docx")
    artifact = result["artifacts"][0]
    assert "content_b64" in artifact
    import base64

    docx_path = tmp_path / "report.docx"
    docx_path.write_bytes(base64.b64decode(artifact["content_b64"]))
    with ZipFile(docx_path) as zf:
        assert "word/document.xml" in zf.namelist()


def test_write_report_artifact_tool_rejects_invalid_format():
    with pytest.raises(Exception):
        write_report_artifact_tool.invoke(
            {"title": "Bad", "format": "pdf", "body_markdown": "x"}
        )


# ----- create_report_tool -----------------------------------------------------


def test_create_report_tool_dispatches(monkeypatch):
    fake_payload = {
        "report_job_id": 11,
        "task_id": 22,
        "pricing_parameter_profile_id": 3,
        "status": "queued",
        "message": "Report queued.",
    }
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return fake_payload

    monkeypatch.setattr("app.services.domains.reporting.create_report", fake_create)
    result = create_report_tool.invoke(
        {
            "portfolio_id": 42,
            "report_type": "portfolio",
            "title": "Q3",
            "pricing_parameter_profile_id": 3,
        }
    )
    assert result is fake_payload
    assert captured == {
        "portfolio_id": 42,
        "report_type": "portfolio",
        "title": "Q3",
        "pricing_profile_id": 3,
    }


def test_create_report_tool_accepts_pricing_profile_id_alias(monkeypatch):
    """The schema alias accepts both pricing_parameter_profile_id and pricing_profile_id."""
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {"report_job_id": 1, "task_id": 1, "status": "queued"}

    monkeypatch.setattr("app.services.domains.reporting.create_report", fake_create)
    create_report_tool.invoke(
        {"portfolio_id": 1, "pricing_profile_id": 9}
    )
    assert captured["pricing_profile_id"] == 9


# ----- run_report_batch_tool --------------------------------------------------


def test_run_report_batch_tool(monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.risk.calculate_risk",
        lambda *, positions, market: {"totals": {"delta": 2.0}},
    )
    result = run_report_batch_tool.invoke(
        {"title": "Desk Risk", "report_type": "portfolio", "portfolio": {}}
    )
    assert result["title"] == "Desk Risk"
    assert result["status"] == "ready"
    assert result["summary"] == {"delta": 2.0}
    assert "artifact_hint" in result

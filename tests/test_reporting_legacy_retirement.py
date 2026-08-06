"""The legacy hardcoded report writer is retired; the tool NAME is retained.

`create_report` is a graded `tool_not_called` prohibition in three golden
workflows. Deleting the tool would make those checks trivially always-pass —
the exact "check at N/N carries zero ability signal while still occupying the
denominator" defect the Run #58 scoring-validity audit found. So the
implementation goes and the name stays, routed through a seeded template.
"""
import pytest


def test_the_hardcoded_writers_are_gone():
    """The duplicate report writer is deleted; one pipeline remains."""
    import app.services.reports as reports

    for name in (
        "_write_html", "_write_xlsx", "_build_report_payload", "_metric_card",
        "_MONEY_DISPLAY", "_SHARED_DISPLAY", "_report_status_from_payload",
    ):
        assert not hasattr(reports, name), f"{name} should have been deleted"


def test_create_report_still_exists_and_routes_through_a_template():
    from app.services.domains import reporting as reporting_svc

    assert hasattr(reporting_svc, "create_report")
    assert reporting_svc.DEFAULT_TEMPLATE_SLUG == "portfolio-snapshot"


def test_create_report_is_still_a_registered_agent_tool():
    """The prohibition is graded against the TOOL, so it must stay reachable."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES
    from app.tools import QUANT_AGENT_TOOLS

    assert "create_report" in {tool.name for tool in QUANT_AGENT_TOOLS}
    assert "create_report" in set(DEEP_AGENT_TOOL_NAMES)


def test_create_report_produces_a_templated_document(
    session, seeded_templates, container_portfolio, monkeypatch
):
    from app.services.domains import reporting as reporting_svc

    # Suppress the real async dispatch. Left live, the worker thread outlives
    # this test and races the NEXT test's init_db() on the rebound global
    # engine ("table instruments already exists"). The async completion path is
    # covered end-to-end by test_api.py::test_portfolio_risk_and_report.
    monkeypatch.setattr(reporting_svc, "submit_async_task", lambda *a, **k: None)

    result = reporting_svc.create_report(
        portfolio_id=container_portfolio.id, session=session
    )
    assert result["template_slug"] == "portfolio-snapshot"
    assert result["report_job_id"] > 0


def test_create_report_rejects_an_unsupported_type(session):
    from app.services.domains import reporting as reporting_svc

    with pytest.raises(ValueError):
        reporting_svc.create_report(portfolio_id=1, report_type="bogus", session=session)


@pytest.fixture
def container_portfolio(session):
    """A real portfolio: task_runs.portfolio_id carries a FK constraint."""
    from app.models import Portfolio, PortfolioKind

    row = Portfolio(name="Legacy Retirement Book", kind=PortfolioKind.CONTAINER.value)
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def seeded_templates(session):
    from app.services.reporting import templates as templates_svc
    from app.services.reporting.seeds import load_seed_specs

    for slug, spec_yaml in load_seed_specs().items():
        templates_svc.save_template(
            slug=slug, spec_yaml=spec_yaml, source="seed", session=session
        )
    session.commit()

import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.template_spec import TemplateSpecError

GOOD = """
meta:
  slug: demo-daily
  title: Demo Daily
  persona: risk_manager
  description: A demo template.
sections:
  - id: headline
    title: Headline
    blocks:
      - { key: risk.totals, render: metric_row }
    narrative: State the position.
"""


def test_report_template_model_and_report_job_columns_exist():
    from app.models import ReportJob, ReportTemplate

    columns = {column.name for column in ReportTemplate.__table__.columns}
    assert columns == {
        "id", "slug", "title", "persona", "description", "spec",
        "source", "version", "created_at", "updated_at",
    }
    job_columns = {column.name for column in ReportJob.__table__.columns}
    assert "template_slug" in job_columns
    assert "compare_to_run_id" in job_columns


def test_save_creates_a_template_and_caches_its_metadata(session):
    from app.services.reporting import templates

    row = templates.save_template(
        slug="demo-daily", spec_yaml=GOOD, session=session
    )
    assert row.slug == "demo-daily"
    assert row.title == "Demo Daily"
    assert row.persona == "risk_manager"
    assert row.description == "A demo template."
    assert row.version == 1
    assert row.source == "user"


def test_saving_again_bumps_the_version(session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=session)
    updated = GOOD.replace("title: Demo Daily", "title: Demo Daily v2")
    row = templates.save_template(
        slug="demo-daily", spec_yaml=updated, session=session
    )
    assert row.version == 2
    assert row.title == "Demo Daily v2"


def test_an_invalid_spec_is_rejected_before_anything_persists(session):
    from app.models import ReportTemplate
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=session)
    session.commit()
    before = session.get(ReportTemplate, 1).spec

    bad = GOOD.replace("risk.totals", "risk.does_not_exist")
    with pytest.raises(TemplateSpecError) as exc:
        templates.save_template(slug="demo-daily", spec_yaml=bad, session=session)
    assert "risk.does_not_exist" in str(exc.value)

    session.rollback()
    assert session.get(ReportTemplate, 1).spec == before
    assert session.get(ReportTemplate, 1).version == 1


def test_slug_mismatch_between_argument_and_spec_is_rejected(session):
    from app.services.reporting import templates

    with pytest.raises(TemplateSpecError) as exc:
        templates.save_template(slug="other-slug", spec_yaml=GOOD, session=session)
    assert "demo-daily" in str(exc.value)


def test_seed_templates_cannot_be_deleted(session):
    from app.services.reporting import templates
    from app.services.reporting.templates import TemplateProtectedError

    templates.save_template(
        slug="demo-daily", spec_yaml=GOOD, source="seed", session=session
    )
    with pytest.raises(TemplateProtectedError):
        templates.delete_template(slug="demo-daily", session=session)


def test_user_templates_can_be_deleted(session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=session)
    assert templates.delete_template(slug="demo-daily", session=session) is True
    assert templates.get_template(slug="demo-daily", session=session) is None


def test_validate_only_returns_errors_without_raising():
    from app.services.reporting import templates

    assert templates.validate_only(GOOD) == []
    errors = templates.validate_only(GOOD.replace("risk.totals", "risk.nope"))
    assert errors and any("risk.nope" in message for message in errors)


def test_list_filters_by_persona(session):
    from app.services.reporting import templates

    templates.save_template(slug="demo-daily", spec_yaml=GOOD, session=session)
    trader = GOOD.replace("slug: demo-daily", "slug: trader-demo").replace(
        "persona: risk_manager", "persona: trader"
    )
    templates.save_template(slug="trader-demo", spec_yaml=trader, session=session)

    rows = templates.list_templates(persona="trader", session=session)
    assert [row.slug for row in rows] == ["trader-demo"]

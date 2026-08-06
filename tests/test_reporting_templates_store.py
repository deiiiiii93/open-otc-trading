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

from app import models
from app.services.arena import runner


def test_persona_maps_high_board_to_high_board():
    assert runner._persona_to_character("high_board") == "high_board"


def test_purge_seeded_reports_removes_reserved_marker_only(session):
    # Non-marker report_type — must SURVIVE (reserved-marker purge never touches it)
    keep = models.ReportJob(report_type="portfolio_governance", status="completed")
    # Reserved arena marker report_type — must be PURGED
    marker = models.ReportJob(report_type=runner.ARENA_REPORT_MARKER, status="completed")
    session.add_all([keep, marker])
    session.commit()
    keep_id, marker_id = keep.id, marker.id

    runner._purge_seeded_reports(session)

    assert session.get(models.ReportJob, marker_id) is None     # reserved marker purged
    assert session.get(models.ReportJob, keep_id) is not None   # production type survives

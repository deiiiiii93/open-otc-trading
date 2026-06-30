from app import models
from app.golden_workflows.fixtures import FixtureBundle, apply_seed


def _bundle():
    # FixtureBundle is a dataclass with fields: seed, replay, seed_map (no schema_version).
    return FixtureBundle(
        seed={
            "reports": [
                {"alias": "q3", "report_type": "arena_high_board_governance",
                 "status": "completed",
                 "request_payload": {"arena_seed": True},
                 "result_payload": {"summary": "prior governance"},
                 "artifact_paths": {"markdown": "reports/q3.md"}},
            ]
        },
        replay={},
        seed_map={},
    )


def test_reports_namespace_inserts_reportjob_and_records_id(session):
    ids = apply_seed(_bundle(), session)
    rid = ids["reports"]["q3"]
    row = session.get(models.ReportJob, rid)
    assert row is not None
    assert row.report_type == "arena_high_board_governance"
    assert row.status == "completed"
    assert row.result_payload == {"summary": "prior governance"}
    # request_payload is an allowlisted seedable column — assert it flows
    # through so a future allowlist regression can't silently drop it.
    assert row.request_payload == {"arena_seed": True}

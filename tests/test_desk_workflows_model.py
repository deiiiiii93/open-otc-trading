from app import database
from app.models import DeskWorkflow


def test_desk_workflow_roundtrip():
    database.init_db()
    with database.SessionLocal() as session:
        wf = DeskWorkflow(
            slug="t-wf",
            title="T WF",
            persona="risk_manager",
            description="desc",
            scope="local",
            default_mode="auto",
            script="meta = {}\n",
            source="user",
        )
        session.add(wf)
        session.commit()
        got = session.query(DeskWorkflow).filter_by(slug="t-wf").one()
        assert got.title == "T WF"
        assert got.scope == "local"
        assert got.created_at is not None and got.updated_at is not None


def test_seed_flagship_present():
    database.init_db()
    with database.SessionLocal() as session:
        wf = session.query(DeskWorkflow).filter_by(slug="risk-manager-control-day").one()
        assert wf.source == "seed"
        assert wf.persona == "risk_manager"
        assert wf.script.count("await step(") == 7

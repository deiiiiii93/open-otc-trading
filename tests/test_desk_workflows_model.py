from app.models import DeskWorkflow


def test_desk_workflow_roundtrip(session):
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


def test_seed_flagship_present(session):
    wf = session.query(DeskWorkflow).filter_by(slug="risk-manager-control-day").one()
    assert wf.source == "seed"
    assert wf.persona == "risk_manager"
    assert wf.script.count("await step(") == 7


def test_desk_workflow_params_property(session):
    script = (
        'meta = {"name":"pw","title":"PW","persona":"trader","mode":"auto",'
        '"scope":"local","params":[{"name":"p","label":"P","type":"portfolio"}]}\n'
        'await step(f"{args.p}")\n'
    )
    wf = DeskWorkflow(
        slug="pw", title="PW", persona="trader", description="",
        scope="local", default_mode="auto", script=script, source="user",
    )
    assert wf.params == [{"name": "p", "label": "P", "type": "portfolio"}]


def test_desk_workflow_params_empty_when_absent(session):
    wf = DeskWorkflow(
        slug="np", title="NP", persona="trader", description="",
        scope="local", default_mode="auto", script="meta = {}\n", source="user",
    )
    assert wf.params == []

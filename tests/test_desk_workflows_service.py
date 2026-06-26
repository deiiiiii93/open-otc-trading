import pytest
from app.services.desk_workflows import (
    delete_desk_workflow,
    get_desk_workflow,
    list_desk_workflows,
    upsert_desk_workflow,
)
from app.services.desk_workflows_script import WorkflowScriptError

SCRIPT = (
    'meta = {"name": "wf-a", "title": "WF A", "persona": "trader", '
    '"mode": "auto", "scope": "local", "description": "d"}\n'
    'await step("one")\n'
)


def test_upsert_creates_then_updates(session):
    wf = upsert_desk_workflow(session, slug="wf-a", script=SCRIPT)
    session.commit()
    assert wf.title == "WF A" and wf.persona == "trader" and wf.description == "d"
    updated = SCRIPT.replace('"title": "WF A"', '"title": "WF A2"')
    wf2 = upsert_desk_workflow(session, slug="wf-a", script=updated)
    session.commit()
    assert wf2.id == wf.id and wf2.title == "WF A2"


def test_upsert_rejects_bad_script(session):
    with pytest.raises(WorkflowScriptError):
        upsert_desk_workflow(session, slug="wf-a", script='await step("x")\n')


def test_delete_blocks_seed(session):
    with pytest.raises(WorkflowScriptError):
        delete_desk_workflow(session, "risk-manager-control-day")


def test_list_and_get(session):
    upsert_desk_workflow(session, slug="wf-a", script=SCRIPT)
    session.commit()
    assert any(w.slug == "wf-a" for w in list_desk_workflows(session))
    assert get_desk_workflow(session, "wf-a") is not None
    assert get_desk_workflow(session, "nope") is None

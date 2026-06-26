import pytest
from app import database
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


def _session():
    database.init_db()
    return database.SessionLocal()


def test_upsert_creates_then_updates():
    with _session() as s:
        wf = upsert_desk_workflow(s, slug="wf-a", script=SCRIPT)
        s.commit()
        assert wf.title == "WF A" and wf.persona == "trader" and wf.description == "d"
        updated = SCRIPT.replace('"title": "WF A"', '"title": "WF A2"')
        wf2 = upsert_desk_workflow(s, slug="wf-a", script=updated)
        s.commit()
        assert wf2.id == wf.id and wf2.title == "WF A2"


def test_upsert_rejects_bad_script():
    with _session() as s:
        with pytest.raises(WorkflowScriptError):
            upsert_desk_workflow(s, slug="wf-a", script='await step("x")\n')


def test_delete_blocks_seed():
    with _session() as s:
        with pytest.raises(WorkflowScriptError):
            delete_desk_workflow(s, "risk-manager-control-day")


def test_list_and_get():
    with _session() as s:
        upsert_desk_workflow(s, slug="wf-a", script=SCRIPT)
        s.commit()
        assert any(w.slug == "wf-a" for w in list_desk_workflows(s))
        assert get_desk_workflow(s, "wf-a") is not None
        assert get_desk_workflow(s, "nope") is None

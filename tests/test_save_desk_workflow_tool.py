from app import database
from app.services.agents import DEEP_AGENT_TOOL_NAMES
from app.tools import all_agent_tools
from app.tools.desk_workflows import save_desk_workflow_tool

SCRIPT = (
    'meta = {"name": "tool-wf", "title": "Tool WF", "persona": "trader", '
    '"mode": "auto", "scope": "local"}\n'
    'await step("one")\n'
)


def test_tool_registered():
    assert "save_desk_workflow" in DEEP_AGENT_TOOL_NAMES
    assert "save_desk_workflow" in {t.name for t in all_agent_tools()}


def test_tool_saves(session):
    # `session` fixture points the global SessionLocal at a tmp DB.
    out = save_desk_workflow_tool.invoke({"script": SCRIPT})
    assert out["ok"] is True and out["slug"] == "tool-wf"
    with database.SessionLocal() as s:
        from app.services.desk_workflows import get_desk_workflow

        assert get_desk_workflow(s, "tool-wf") is not None


def test_tool_rejects_bad_script(session):
    out = save_desk_workflow_tool.invoke({"script": "import os\n"})
    assert out["ok"] is False and out["error"]

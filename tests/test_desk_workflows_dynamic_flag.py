"""Task 4: server-owned `dynamic_subagents` flag + immutable seed workflows."""
import pytest

from app.services.desk_workflows_script import WorkflowScriptError, validate_script

_PILOT = "morning-risk-breach-commentary"


def _script(slug: str, *, dyn: bool) -> str:
    flag = "\n    'dynamic_subagents': True," if dyn else ""
    return (
        f"meta = {{\n 'name': '{slug}', 'title': 'T', 'persona': 'risk_manager',"
        f"\n 'mode': 'yolo', 'scope': 'shared',{flag}\n}}\n\nawait step('x')\n"
    )


def test_user_save_with_dynamic_flag_rejected():
    with pytest.raises(WorkflowScriptError):
        validate_script(_script(_PILOT, dyn=True), slug=_PILOT, source="user")


def test_non_allowlisted_seed_with_flag_rejected():
    with pytest.raises(WorkflowScriptError):
        validate_script(_script("other-wf", dyn=True), slug="other-wf", source="seed")


def test_allowlisted_seed_with_flag_ok():
    meta = validate_script(_script(_PILOT, dyn=True), slug=_PILOT, source="seed")
    assert meta.get("dynamic_subagents") is True


def test_no_flag_still_ok_for_user():
    meta = validate_script(_script("my-wf", dyn=False), slug="my-wf", source="user")
    assert "dynamic_subagents" not in meta


def test_user_cannot_overwrite_seed_workflow(session):
    from app.services import desk_workflows as dw

    dw.upsert_desk_workflow(session, slug="pilot-wf", script=_script("pilot-wf", dyn=False), source="seed")
    with pytest.raises(WorkflowScriptError):
        dw.upsert_desk_workflow(session, slug="pilot-wf", script=_script("pilot-wf", dyn=False), source="user")

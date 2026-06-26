import json

import pytest

from app.models import DeskWorkflow
from app.services.desk_workflow_runner import persona_to_character, run_desk_workflow


def _wf(script: str) -> DeskWorkflow:
    return DeskWorkflow(
        slug="t", title="T", persona="risk_manager", description="",
        scope="local", default_mode="yolo", script=script, source="user",
    )


def _parse(frames: list[str]) -> list[tuple[str, dict]]:
    events = []
    for frame in frames:
        lines = frame.strip().split("\n")
        ev = next(l[6:].strip() for l in lines if l.startswith("event:"))
        data = next((l[5:].strip() for l in lines if l.startswith("data:")), "{}")
        events.append((ev, json.loads(data)))
    return events


@pytest.mark.asyncio
async def test_runner_drives_steps_in_order_and_settles():
    calls = []

    async def drive(thread_id, prompt, mode):
        calls.append(("drive", prompt, mode))
        yield 'event: token\ndata: {"text": "ok"}\n\n'

    def settle():
        calls.append(("settle", None, None))

    script = (
        'meta = {"name":"t","title":"T","persona":"risk_manager","mode":"yolo","scope":"local"}\n'
        'await step("a")\n'
        'log("mid")\n'
        'await step("b")\n'
    )
    frames = [f async for f in run_desk_workflow(
        thread_id=1, workflow=_wf(script), mode="yolo", drive=drive, settle=settle,
    )]
    events = _parse(frames)
    names = [e[0] for e in events]
    assert names[0] == "workflow.start"
    assert names[-1] == "workflow.complete"
    assert names.count("workflow.step.start") == 2
    assert ("workflow.log", {"message": "mid"}) in events
    drive_calls = [c for c in calls if c[0] == "drive"]
    assert [c[1] for c in drive_calls] == ["a", "b"]
    assert calls.count(("settle", None, None)) == 2


@pytest.mark.asyncio
async def test_runner_halts_on_step_error():
    async def drive(thread_id, prompt, mode):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    script = (
        'meta = {"name":"t","title":"T","persona":"risk_manager","mode":"yolo","scope":"local"}\n'
        'await step("a")\n'
        'await step("b")\n'
    )
    frames = [f async for f in run_desk_workflow(
        thread_id=1, workflow=_wf(script), mode="yolo", drive=drive, settle=lambda: None,
    )]
    events = _parse(frames)
    names = [e[0] for e in events]
    assert "workflow.step.error" in names
    assert "workflow.complete" not in names
    assert names.count("workflow.step.start") == 1


def test_persona_to_character():
    assert persona_to_character("risk_manager") == "risk_manager"
    assert persona_to_character("quant") == "high_board"
    assert persona_to_character("sales") == "trader"

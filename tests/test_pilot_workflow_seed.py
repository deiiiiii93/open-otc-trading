"""Task 6: the seeded morning-risk-breach-commentary pilot workflow."""
import pytest
import sqlalchemy as sa

from app.desk_workflow_seed import SEED_WORKFLOWS
from app.services.desk_workflows_script import validate_script

_PILOT = "morning-risk-breach-commentary"


def test_pilot_workflow_present_and_validates_as_seed():
    wf = next(w for w in SEED_WORKFLOWS if w["slug"] == _PILOT)
    meta = validate_script(wf["script"], slug=wf["slug"], source="seed")
    assert meta["persona"] == "risk_manager"
    assert meta["dynamic_subagents"] is True


def test_flagship_still_in_seed_list():
    assert any(w["slug"] == "risk-manager-control-day" for w in SEED_WORKFLOWS)


def test_boot_seed_creates_pilot_row(tmp_path):
    from app import database
    from app.models import Base

    engine = sa.create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    database.seed_desk_workflows(engine)
    with engine.connect() as c:
        row = c.execute(
            sa.text("SELECT source FROM desk_workflows WHERE slug = :s"), {"s": _PILOT}
        ).fetchone()
    assert row is not None and row[0] == "seed"


@pytest.mark.asyncio
async def test_seeded_pilot_emits_three_steps():
    """Prove the stored script is NOT a no-op: the runner lifts top-level `await
    step(...)` and emits three workflow.step.start frames."""
    from types import SimpleNamespace

    from app.services.desk_workflow_runner import run_desk_workflow

    entry = next(w for w in SEED_WORKFLOWS if w["slug"] == _PILOT)
    wf = SimpleNamespace(
        slug=entry["slug"], script=entry["script"], persona=entry["persona"],
        default_mode=entry["default_mode"], source="seed",
    )

    async def fake_drive(thread_id, prompt, mode):
        if False:  # pragma: no cover — async generator that drives no frames
            yield ""

    starts = 0
    async for frame in run_desk_workflow(
        thread_id=1, workflow=wf, mode="yolo",
        drive=fake_drive, settle=lambda: None, args={"portfolio_id": "1"},
    ):
        if "workflow.step.start" in frame:
            starts += 1
    assert starts == 3

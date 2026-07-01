"""Task 3: server-derived fan-out attribution (allowlisted seed workflows only)."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services.deep_agent.dynamic_subagents import (
    FANOUT_ATTRIBUTION_CASE3,
    FANOUT_ATTRIBUTION_KEY,
    FANOUT_WORKFLOW_ID_KEY,
    fanout_attribution_extra,
)


def test_stamps_for_allowlisted_seed_workflow():
    assert fanout_attribution_extra(slug="morning-risk-breach-commentary", source="seed") == {
        FANOUT_ATTRIBUTION_KEY: FANOUT_ATTRIBUTION_CASE3,
        FANOUT_WORKFLOW_ID_KEY: "morning-risk-breach-commentary",
    }


def test_no_stamp_for_user_source_even_if_allowlisted_slug():
    assert fanout_attribution_extra(slug="morning-risk-breach-commentary", source="user") == {}


def test_no_stamp_for_non_allowlisted():
    assert fanout_attribution_extra(slug="whatever", source="seed") == {}


def test_no_stamp_for_plain_chat():
    assert fanout_attribution_extra(slug=None, source=None) == {}


@pytest.mark.asyncio
async def test_drive_factory_forwards_slug_and_source(monkeypatch):
    """The desk-workflow driver threads the workflow's slug+source into the run."""
    import app.main as main

    @contextmanager
    def _fake_session():
        class _S:
            def add(self, *a):
                pass

            def commit(self):
                pass

        yield _S()

    monkeypatch.setattr(main.database, "SessionLocal", _fake_session)

    captured: dict = {}

    class _FakeService:
        async def stream_and_persist(self, **kw):
            captured.update(kw)
            if False:  # pragma: no cover — makes this an async generator
                yield ""

    wf = SimpleNamespace(slug="morning-risk-breach-commentary", source="seed")
    drive = main._desk_workflow_drive_factory(_FakeService(), "auto", desk_workflow=wf)
    async for _ in drive(1, "hi", "yolo"):
        pass

    assert captured["desk_workflow_slug"] == "morning-risk-breach-commentary"
    assert captured["desk_workflow_source"] == "seed"

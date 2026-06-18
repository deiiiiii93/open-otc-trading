from __future__ import annotations

import dataclasses

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings
from app.services.deep_agent.checkpointer import build_async_checkpointer, build_checkpointer


def test_build_checkpointer_with_in_memory_path_returns_sqlite_saver():
    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")

    saver = build_checkpointer(settings)

    assert isinstance(saver, SqliteSaver)
    # It must be usable for at least a list call (no checkpoints yet → empty)
    assert list(saver.list({"configurable": {"thread_id": "nonexistent"}})) == []


def test_build_checkpointer_with_disk_path(tmp_path):
    settings = dataclasses.replace(
        Settings(), agent_checkpoint_db_path=str(tmp_path / "ck.sqlite")
    )

    saver = build_checkpointer(settings)

    assert isinstance(saver, SqliteSaver)


@pytest.mark.asyncio
async def test_build_async_checkpointer_with_disk_path(tmp_path):
    settings = dataclasses.replace(
        Settings(), agent_checkpoint_db_path=str(tmp_path / "ck.sqlite")
    )

    async with build_async_checkpointer(settings) as saver:
        assert isinstance(saver, AsyncSqliteSaver)
        assert [
            item
            async for item in saver.alist({"configurable": {"thread_id": "nonexistent"}})
        ] == []

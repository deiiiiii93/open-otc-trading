"""Persistent SqliteSaver factory for HITL-aware DeepAgent threads.

HITL needs graph state to survive across HTTP requests (the user's confirm
click hits a separate process from the one that produced the pause), so the
checkpointer cannot be InMemorySaver.

We construct from a long-lived sqlite3 connection with check_same_thread=False
because FastAPI's threadpool may invoke the saver from worker threads.

`SqliteSaver.from_conn_string` is a context manager intended for short-lived
use; for an app-lifetime saver shared across requests we construct directly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings


def clear_thread_checkpoints(
    settings: Settings, thread_id: str | int
) -> dict[str, int]:
    """Delete persisted LangGraph checkpoints for one chat thread.

    This intentionally leaves visible chat messages in the application DB alone.
    It prevents stale graph state from surviving thread deletion and being
    replayed if SQLite later reuses the numeric thread id.
    """
    thread_key = str(thread_id)
    conn = sqlite3.connect(settings.agent_checkpoint_db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        deleted: dict[str, int] = {}
        with conn:
            for table in ("writes", "checkpoints"):
                if table not in tables:
                    deleted[table] = 0
                    continue
                cursor = conn.execute(
                    f"delete from {table} where thread_id = ?",  # noqa: S608
                    (thread_key,),
                )
                deleted[table] = cursor.rowcount if cursor.rowcount != -1 else 0
        return deleted
    finally:
        conn.close()


def build_checkpointer(settings: Settings) -> SqliteSaver:
    conn = sqlite3.connect(
        settings.agent_checkpoint_db_path,
        check_same_thread=False,
    )
    return SqliteSaver(conn)


@asynccontextmanager
async def build_async_checkpointer(
    settings: Settings,
) -> AsyncIterator[AsyncSqliteSaver]:
    async with AsyncSqliteSaver.from_conn_string(
        settings.agent_checkpoint_db_path
    ) as saver:
        yield saver

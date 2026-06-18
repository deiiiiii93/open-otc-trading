"""Auto-post completion: read subagent state, write parent-thread message,
materialize scratch artifacts to disk."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from ... import database
from ...models import AgentMessage, AgentThread, TaskRun, TaskStatus
from ..agents import (
    _agent_file_assets_from_state,
    _extract_final_ai_text,
    _tool_artifact_files_from_result,
)
from ..audit import record_audit


def _materialize_assets(
    files: dict[str, Any] | None,
    *,
    artifact_dir: Path,
    thread_id: int,
    task_id: int,
) -> list[dict[str, Any]]:
    """Materialize /trading_desk/async/<task_id>/** files to disk."""
    if not isinstance(files, dict):
        return []
    prefix = f"/trading_desk/async/{task_id}/"
    assets: list[dict[str, Any]] = []
    for virtual_path, file_data in sorted(files.items()):
        if not isinstance(virtual_path, str) or not virtual_path.startswith(prefix):
            continue
        if isinstance(file_data, str):
            content = file_data
        elif isinstance(file_data, dict):
            raw = file_data.get("content")
            content = raw if isinstance(raw, str) else None
        else:
            content = None
        if not isinstance(content, str):
            continue
        relative = virtual_path[len(prefix):]
        # Reject traversal: model/tool state is untrusted, so any '..' in the
        # relative tail could escape async-<id>/ even though prefix matches.
        rel_path = PurePosixPath(relative)
        if rel_path.is_absolute() or ".." in rel_path.parts or not rel_path.parts:
            continue
        target = (
            artifact_dir
            / "agent"
            / f"thread-{thread_id}"
            / f"async-{task_id}"
            / relative
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        url = (
            f"/api/artifacts/agent/thread-{thread_id}/"
            f"async-{task_id}/{relative}"
        )
        assets.append(
            {
                "id": f"agent-async-{task_id}-" + relative.replace("/", "-"),
                "kind": _kind(relative),
                "title": PurePosixPath(relative).name,
                "mime_type": _mime(relative),
                "url": url,
                "path": virtual_path,
                "metadata": {
                    "virtual_path": virtual_path,
                    "artifact_path": str(target),
                },
            }
        )
    return assets


def _kind(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix == ".json":
        return "json"
    return "file"


def _mime(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.lower()
    return {
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".json": "application/json",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(suffix)


def _materialize_tool_output_assets(
    state_values: dict[str, Any],
    *,
    artifact_dir: Path,
    thread_id: int,
) -> list[dict[str, Any]]:
    files = _tool_artifact_files_from_result(
        {"messages": state_values.get("messages") or []}
    )
    return [
        asset.model_dump(mode="json")
        for asset in _agent_file_assets_from_state(
            files,
            artifact_dir=artifact_dir,
            thread_id=thread_id,
        )
    ]


def handle(
    session: Session, *, task_id: int, state_values: dict[str, Any]
) -> AgentMessage:
    """Write a completed AgentMessage on the parent thread + materialize assets."""
    task = session.get(TaskRun, task_id)
    if task is None or task.parent_thread_id is None:
        raise ValueError(f"async_agent task {task_id} not found")

    final_text = _extract_final_ai_text(state_values) or "(no response)"
    assets = _materialize_assets(
        state_values.get("files"),
        artifact_dir=Path(database.settings.artifact_dir),
        thread_id=task.parent_thread_id,
        task_id=task_id,
    )
    assets.extend(
        _materialize_tool_output_assets(
            state_values,
            artifact_dir=Path(database.settings.artifact_dir),
            thread_id=task.parent_thread_id,
        )
    )
    msg = AgentMessage(
        thread_id=task.parent_thread_id,
        role="assistant",
        character="async_agent",
        content=final_text,
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "completed",
            "async_task_id": task_id,
            "description": task.description,
            "assets": assets,
        },
    )
    session.add(msg)
    thread = session.get(AgentThread, task.parent_thread_id)
    if thread is not None:
        thread.updated_at = datetime.utcnow()

    task.status = TaskStatus.COMPLETED.value
    # Clear the lifecycle message (bubble_up may have set it to
    # "awaiting approval"; without clearing, list_async_agents would
    # report a completed task as still awaiting).
    task.message = None
    task.finished_at = datetime.utcnow()
    if task.started_at is None:
        task.started_at = task.finished_at
    # Merge: preserve any dispatch-time durable copy (e.g. model_selection)
    # alongside the completion fields.
    existing = task.result_payload if isinstance(task.result_payload, dict) else {}
    task.result_payload = {
        **existing,
        "final_text": final_text,
        "asset_count": len(assets),
    }

    record_audit(
        session,
        event_type="async_agent.completed",
        actor="system",
        subject_type="thread",
        subject_id=task.parent_thread_id,
        payload={"task_id": task_id, "asset_count": len(assets)},
    )
    session.flush()
    return msg

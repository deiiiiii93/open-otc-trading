"""Workflow-scoped deterministic artifact discovery and exact reads."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from ... import database
from ...models import SessionArtifact

ARTIFACT_DISCLOSURE_TOOL_NAMES = frozenset(
    {"list_artifacts", "inspect_artifact", "read_artifact"}
)


def effective_tools_scope(tool_names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Add workflow-scoped artifact disclosure to a task's domain tool scope."""
    return tuple(sorted(set(tool_names) | ARTIFACT_DISCLOSURE_TOOL_NAMES))


def _timestamp(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _active_config(config: RunnableConfig | None) -> dict[str, Any]:
    if config:
        values = config.get("configurable") or {}
        if isinstance(values, dict):
            return dict(values)
    try:
        from langgraph.config import get_config

        values = get_config().get("configurable") or {}
        return dict(values) if isinstance(values, dict) else {}
    except Exception:
        return {}


def workflow_id_from_config(config: RunnableConfig | None) -> int:
    values = _active_config(config)
    raw = values.get("workflow_id")
    if raw is not None:
        return int(raw)

    # Legacy/non-routed agent graphs carry only the parent thread id. Mirror the
    # CAS backend's attribution rule so evidence captured before HITL can still
    # be resolved after resume under the same meta workflow.
    raw_thread = values.get("parent_thread_id") or values.get("thread_id")
    try:
        thread_id = int(str(raw_thread).split(":", 1)[0])
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact access requires current workflow context") from exc
    from .workflow_state import ensure_thread_workflow_state

    with database.SessionLocal() as session:
        state = ensure_thread_workflow_state(session, thread_id)
        session.commit()
        return state.meta_workflow_id


def _payload_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def artifact_descriptor(artifact: SessionArtifact) -> dict[str, Any]:
    payload = dict(artifact.payload or {})
    content_hash = payload.get("content_hash")
    if not content_hash and payload.get("blob_hash"):
        content_hash = f"sha256:{payload['blob_hash']}"
    if not content_hash:
        content_hash = _payload_hash(payload)
    generated_at = payload.get("generated_at") or _timestamp(artifact.created_at)
    byte_size = payload.get("byte_size") or payload.get("size")
    if byte_size is None and isinstance(payload.get("content"), str):
        byte_size = len(payload["content"].encode("utf-8"))
    return {
        "artifact_id": artifact.id,
        "kind": artifact.kind,
        "title": artifact.title,
        "tool_name": artifact.tool_name,
        "tool_call_id": artifact.tool_call_id,
        "content_hash": str(content_hash),
        "generated_at": generated_at,
        "observed_at": payload.get("observed_at"),
        "data_as_of": payload.get("data_as_of") or payload.get("valuation_as_of"),
        "byte_size": int(byte_size or 0),
        "media_type": payload.get("media_type") or "application/json",
        "summary": (
            dict(payload.get("summary") or {})
            if isinstance(payload.get("summary"), dict)
            else {}
        ),
        "summary_provenance": payload.get("summary_provenance"),
        "locator": artifact.rendered_path or f"artifact:{artifact.id}",
        "pinned": bool(artifact.pinned),
        "superseded_by": artifact.superseded_by,
        "section_map_available": bool(
            payload.get("blob_hash")
            or isinstance(payload.get("content"), str)
            or payload
        ),
    }


def list_workflow_artifacts(
    session: Session,
    *,
    workflow_id: int,
    kind: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = session.query(SessionArtifact).filter(
        SessionArtifact.workflow_id == workflow_id
    )
    if kind:
        query = query.filter(SessionArtifact.kind == kind)
    if tool_name:
        query = query.filter(SessionArtifact.tool_name == tool_name)
    rows = (
        query.order_by(SessionArtifact.created_at.desc(), SessionArtifact.id.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [artifact_descriptor(row) for row in rows]


def get_workflow_artifact(
    session: Session,
    *,
    workflow_id: int,
    artifact_id: int,
) -> SessionArtifact:
    artifact = session.get(SessionArtifact, int(artifact_id))
    if artifact is None or artifact.workflow_id != workflow_id:
        raise ValueError("artifact not found in current workflow")
    return artifact


def _artifact_file_path(rendered_path: str) -> Path:
    root = Path(database.settings.artifact_dir).resolve()
    candidate = Path(rendered_path)
    if not candidate.is_absolute():
        candidate = root / rendered_path.lstrip("/")
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("artifact path escapes configured artifact directory")
    return candidate


def raw_artifact_content(artifact: SessionArtifact) -> str:
    payload = dict(artifact.payload or {})
    blob_hash = str(payload.get("blob_hash") or "")
    if blob_hash:
        if not re.fullmatch(r"[0-9a-f]{64}", blob_hash):
            raise ValueError(f"artifact {artifact.id} blob hash is invalid")
        root = Path(database.settings.artifact_dir) / "artifact_blobs"
        path = root / blob_hash[:2] / f"{blob_hash}.json"
        if not path.exists():
            raise ValueError(f"artifact {artifact.id} blob is unavailable")
        content_bytes = path.read_bytes()
        if hashlib.sha256(content_bytes).hexdigest() != blob_hash:
            raise ValueError(f"artifact {artifact.id} blob failed hash verification")
        return content_bytes.decode("utf-8")
    if isinstance(payload.get("content"), str):
        return payload["content"]
    if artifact.rendered_path and not artifact.rendered_path.startswith(
        "/large_tool_results/"
    ):
        path = _artifact_file_path(artifact.rendered_path)
        if path.exists() and path.suffix.lower() in {
            ".txt",
            ".md",
            ".json",
            ".csv",
            ".html",
        }:
            return path.read_text(encoding="utf-8")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_sections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return [
        {
            "id": str(key),
            "title": str(key),
            "selector": "/" + str(key).replace("~", "~0").replace("/", "~1"),
            "kind": type(item).__name__,
            "count": len(item) if isinstance(item, (list, dict)) else None,
        }
        for key, item in value.items()
    ]


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return value or "section"


def _markdown_sections(content: str) -> list[dict[str, Any]]:
    lines = content.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2)))
    out = []
    used: dict[str, int] = {}
    for position, (start, level, title) in enumerate(headings):
        end = len(lines)
        for later_start, later_level, _later_title in headings[position + 1 :]:
            if later_level <= level:
                end = later_start
                break
        base = _slug(title)
        used[base] = used.get(base, 0) + 1
        section_id = base if used[base] == 1 else f"{base}-{used[base]}"
        out.append(
            {
                "id": section_id,
                "title": title,
                "selector": section_id,
                "kind": "markdown_heading",
                "level": level,
                "start_line": start + 1,
                "end_line": end,
            }
        )
    return out


def section_map(content: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "text", _markdown_sections(content)
    return "json", _json_sections(parsed)


def inspect_artifact(
    session: Session,
    *,
    workflow_id: int,
    artifact_id: int,
) -> dict[str, Any]:
    artifact = get_workflow_artifact(
        session, workflow_id=workflow_id, artifact_id=artifact_id
    )
    content = raw_artifact_content(artifact)
    format_name, sections = section_map(content)
    descriptor = artifact_descriptor(artifact)
    descriptor.update(
        {
            "format": format_name,
            "sections": sections,
            "line_count": len(content.splitlines()),
            "byte_size": len(content.encode("utf-8")),
        }
    )
    return descriptor


def _resolve_json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("json_pointer must be empty or start with '/'")
    current = value
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"JSON pointer component not found: {token}")
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"invalid JSON list index: {token}") from exc
        else:
            raise ValueError(f"JSON pointer cannot descend through {type(current).__name__}")
    return current


def read_artifact(
    session: Session,
    *,
    workflow_id: int,
    artifact_id: int,
    json_pointer: str | None = None,
    section: str | None = None,
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    if json_pointer is not None and section is not None:
        raise ValueError("choose json_pointer or section, not both")
    artifact = get_workflow_artifact(
        session, workflow_id=workflow_id, artifact_id=artifact_id
    )
    raw = raw_artifact_content(artifact)
    descriptor = artifact_descriptor(artifact)
    if json_pointer is not None:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("json_pointer requires a JSON artifact") from exc
        content: Any = _resolve_json_pointer(parsed, json_pointer)
        selector: dict[str, Any] = {"json_pointer": json_pointer}
    elif section is not None:
        _format, sections = section_map(raw)
        match = next((item for item in sections if item["id"] == section), None)
        if match is None or match.get("kind") != "markdown_heading":
            raise ValueError(f"artifact section not found: {section}")
        lines = raw.splitlines()
        content = "\n".join(
            lines[int(match["start_line"]) - 1 : int(match["end_line"])]
        ).rstrip()
        selector = {"section": section}
    else:
        lines = raw.splitlines()
        bounded_limit = max(1, min(int(limit), 500))
        bounded_offset = max(0, int(offset))
        content = "\n".join(lines[bounded_offset : bounded_offset + bounded_limit])
        selector = {"offset": bounded_offset, "limit": bounded_limit}
    return {
        **descriptor,
        "selector": selector,
        "content": content,
    }

from __future__ import annotations

import json

import pytest

from app import database
from app.models import AgentThread, SessionArtifact
from app.services.deep_agent.ledger import LedgerWriter
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state
from app.tools.artifacts import (
    inspect_artifact_tool,
    list_artifacts_tool,
    read_artifact_tool,
)


def _state(session, title: str):
    thread = AgentThread(title=title, character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()
    return thread, state


def _config(thread_id: int, state) -> dict:
    return {
        "configurable": {
            "thread_id": str(thread_id),
            "workflow_id": state.domain_workflow_id,
            "session_id": state.orchestrator_session_id,
            "context_pack_id": state.context_pack_id,
            "envelope": "desk_workflow",
        }
    }


def test_artifact_tools_list_inspect_and_read_exact_json_pointer(session, settings):
    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend

    thread, state = _state(session, "artifact access")
    config = _config(thread.id, state)
    content = json.dumps(
        {
            "status": "ok",
            "valuation_as_of": "2026-07-16T01:35:00Z",
            "underlyings": [
                {"symbol": "000905.SH", "delta": 1250000.0},
                {"symbol": "000300.SH", "delta": -400000.0},
            ],
        },
        separators=(",", ":"),
    )
    backend = ContentAddressedFilesystemBackend(
        root_dir=settings.artifact_dir / "artifact_blobs"
    )
    reference = backend.capture_tool_result(
        tool_call_id="risk-read-1",
        tool_name="get_hedgeable_underlyings",
        content=content,
        tool_args={"portfolio_id": 4},
        config=config,
        classification="domain_read",
    )

    listed = list_artifacts_tool.invoke({"limit": 20}, config=config)
    assert [row["artifact_id"] for row in listed["artifacts"]] == [
        reference["artifact_id"]
    ]
    assert listed["artifacts"][0]["content_hash"] == reference["content_hash"]
    assert listed["artifacts"][0]["data_as_of"] == "2026-07-16T01:35:00Z"

    inspected = inspect_artifact_tool.invoke(
        {"artifact_id": reference["artifact_id"]}, config=config
    )
    selectors = {section["selector"] for section in inspected["sections"]}
    assert "/underlyings" in selectors
    assert inspected["byte_size"] == len(content.encode("utf-8"))

    exact = read_artifact_tool.invoke(
        {
            "artifact_id": reference["artifact_id"],
            "json_pointer": "/underlyings/0",
        },
        config=config,
    )
    assert exact["content"] == {"symbol": "000905.SH", "delta": 1250000.0}
    assert exact["content_hash"] == reference["content_hash"]
    assert exact["generated_at"] == reference["generated_at"]


def test_artifact_tools_build_markdown_heading_map(session):
    thread, state = _state(session, "report sections")
    config = _config(thread.id, state)
    artifact = LedgerWriter(session).write_artifact(
        workflow_id=state.domain_workflow_id,
        session_id=state.orchestrator_session_id,
        kind="report",
        title="Risk report",
        payload={
            "content": "# Overview\nDesk summary\n\n## Delta\nDelta detail\n\n## Vega\nVega detail",
            "generated_at": "2026-07-16T01:40:00Z",
            "data_as_of": "2026-07-16T01:35:00Z",
        },
    )
    session.commit()

    inspected = inspect_artifact_tool.invoke(
        {"artifact_id": artifact.id}, config=config
    )

    assert [section["title"] for section in inspected["sections"]] == [
        "Overview",
        "Delta",
        "Vega",
    ]
    delta = read_artifact_tool.invoke(
        {"artifact_id": artifact.id, "section": "delta"}, config=config
    )
    assert delta["content"] == "## Delta\nDelta detail"


def test_artifact_tools_deny_cross_workflow_reads(session):
    first_thread, first = _state(session, "first")
    _second_thread, second = _state(session, "second")
    artifact = LedgerWriter(session).write_artifact(
        workflow_id=second.domain_workflow_id,
        session_id=second.orchestrator_session_id,
        kind="deterministic_query",
        title="private to second workflow",
        payload={"rows": [1, 2, 3]},
    )
    session.commit()

    with pytest.raises(ValueError, match="current workflow"):
        read_artifact_tool.invoke(
            {"artifact_id": artifact.id}, config=_config(first_thread.id, first)
        )


def test_artifact_read_fails_closed_when_cas_bytes_are_changed(session, settings):
    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend

    thread, state = _state(session, "tamper detection")
    config = _config(thread.id, state)
    backend = ContentAddressedFilesystemBackend(
        root_dir=settings.artifact_dir / "artifact_blobs"
    )
    reference = backend.capture_tool_result(
        tool_call_id="tamper-1",
        tool_name="query_positions",
        content='{"rows":[1]}',
        tool_args={},
        config=config,
        classification="domain_read",
    )
    blob_hash = reference["content_hash"].removeprefix("sha256:")
    blob_path = settings.artifact_dir / "artifact_blobs" / blob_hash[:2] / f"{blob_hash}.json"
    blob_path.write_bytes(b'{"rows":[2]}')

    with pytest.raises(ValueError, match="hash verification"):
        read_artifact_tool.invoke(
            {"artifact_id": reference["artifact_id"]}, config=config
        )


def test_list_artifacts_uses_deterministic_filters_and_order(session):
    thread, state = _state(session, "filters")
    writer = LedgerWriter(session)
    first = writer.write_artifact(
        workflow_id=state.domain_workflow_id,
        kind="finding",
        title="finding",
        payload={"content": "derived"},
    )
    second = writer.write_artifact(
        workflow_id=state.domain_workflow_id,
        kind="deterministic_query",
        title="query",
        payload={"rows": []},
        tool_name="query_positions",
    )
    session.commit()

    result = list_artifacts_tool.invoke(
        {"kind": "deterministic_query", "tool_name": "query_positions"},
        config=_config(thread.id, state),
    )

    assert [row["artifact_id"] for row in result["artifacts"]] == [second.id]
    assert first.id not in {row["artifact_id"] for row in result["artifacts"]}


def test_legacy_thread_config_resolves_the_same_meta_workflow(session):
    from app.services.deep_agent.artifact_access import workflow_id_from_config

    thread, state = _state(session, "legacy workflow fallback")

    resolved = workflow_id_from_config(
        {"configurable": {"thread_id": str(thread.id)}}
    )

    assert resolved == state.meta_workflow_id

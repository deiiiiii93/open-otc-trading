from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from app import database
from app.models import AgentThread, SessionArtifact
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("deterministic_read")
def _deterministic_read() -> dict:
    """Return deterministic data for middleware tests."""
    return {"risk_run_id": 7}


def _request(*, config: dict, tool_call_id: str = "read-1") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={
            "name": "deterministic_read",
            "args": {"portfolio_id": 4},
            "id": tool_call_id,
        },
        runtime=SimpleNamespace(config=config),
    )


def test_ground_truth_middleware_captures_small_read_and_attaches_reference(
    session, settings
):
    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend
    from app.services.deep_agent.ground_truth import GroundTruthArtifactMiddleware

    thread = AgentThread(title="ground truth", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()
    config = {
        "configurable": {
            "thread_id": str(thread.id),
            "workflow_id": state.domain_workflow_id,
            "session_id": state.orchestrator_session_id,
            "context_pack_id": state.context_pack_id,
        }
    }
    backend = ContentAddressedFilesystemBackend(
        root_dir=settings.artifact_dir / "artifact_blobs"
    )
    middleware = GroundTruthArtifactMiddleware(
        tools=[_deterministic_read], backend=backend
    )
    content = (
        '{"status":"ok","risk_run_id":7,'
        '"valuation_as_of":"2026-07-16T01:35:00Z"}'
    )

    result = middleware.wrap_tool_call(
        _request(config=config),
        lambda _request: ToolMessage(
            content=content,
            name="deterministic_read",
            tool_call_id="read-1",
        ),
    )

    ref = result.additional_kwargs["artifact_ref"]
    assert result.additional_kwargs["artifact_ref_provenance"] == "server_capture_v1"
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert ref["content_hash"] == f"sha256:{expected_hash}"
    assert ref["generated_at"].endswith("Z")
    assert ref["data_as_of"] == "2026-07-16T01:35:00Z"
    assert ref["tool_name"] == "deterministic_read"
    assert "<artifact_ref>" in str(result.content)

    with database.SessionLocal() as check:
        artifact = check.get(SessionArtifact, ref["artifact_id"])
        assert artifact is not None
        assert artifact.workflow_id == state.domain_workflow_id
        assert artifact.payload["blob_hash"] == expected_hash
        assert artifact.payload["input_hash"].startswith("sha256:")
        assert artifact.payload["classification"] == "domain_read"
        assert artifact.payload["generated_at"] == ref["generated_at"]
        assert artifact.payload["data_as_of"] == ref["data_as_of"]

    blob = (
        settings.artifact_dir
        / "artifact_blobs"
        / expected_hash[:2]
        / f"{expected_hash}.json"
    )
    assert blob.read_text(encoding="utf-8") == content


def test_ground_truth_capture_failure_keeps_original_message(monkeypatch):
    from app.services.deep_agent.ground_truth import GroundTruthArtifactMiddleware

    backend = SimpleNamespace()

    def _fail(**_kwargs):
        raise RuntimeError("ledger unavailable")

    backend.capture_tool_result = _fail
    middleware = GroundTruthArtifactMiddleware(
        tools=[_deterministic_read], backend=backend
    )
    original = ToolMessage(
        content='{"rows":[1]}',
        name="deterministic_read",
        tool_call_id="read-1",
    )

    result = middleware.wrap_tool_call(
        _request(config={}),
        lambda _request: original,
    )

    assert result is original
    assert "artifact_ref" not in result.additional_kwargs


def test_non_ground_truth_tool_is_not_captured():
    from app.services.deep_agent.ground_truth import GroundTruthArtifactMiddleware

    @capability_gated(group=ToolGroup.PAGE_ACTION)
    @tool("ui_action")
    def ui_action() -> dict:
        """Return a UI-only action."""
        return {"ok": True}

    backend = SimpleNamespace(
        capture_tool_result=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not capture")
        )
    )
    middleware = GroundTruthArtifactMiddleware(tools=[ui_action], backend=backend)
    message = ToolMessage(content='{"ok":true}', name="ui_action", tool_call_id="ui-1")
    request = SimpleNamespace(
        tool_call={"name": "ui_action", "args": {}, "id": "ui-1"},
        runtime=SimpleNamespace(config={}),
    )

    assert middleware.wrap_tool_call(request, lambda _request: message) is message


def test_capture_preserves_exact_tool_call_id_and_summarizes_json_lists(
    session, settings
):
    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend

    thread = AgentThread(title="exact call id", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()
    config = {
        "configurable": {
            "workflow_id": state.domain_workflow_id,
            "session_id": state.orchestrator_session_id,
        }
    }
    backend = ContentAddressedFilesystemBackend(
        root_dir=settings.artifact_dir / "artifact_blobs"
    )

    reference = backend.capture_tool_result(
        tool_call_id="call/desk:42",
        tool_name="deterministic_read",
        content='[{"row":1},{"row":2}]',
        tool_args={},
        config=config,
        classification="domain_read",
    )

    assert reference["tool_call_id"] == "call/desk:42"
    assert reference["locator"].endswith("/call_desk_42")
    assert reference["summary"] == {
        "format": "json",
        "top_level_type": "list",
        "count": 2,
    }
    with database.SessionLocal() as check:
        artifact = check.get(SessionArtifact, reference["artifact_id"])
        assert artifact.tool_call_id == "call/desk:42"
        assert artifact.payload["media_type"] == "application/json"


def test_artifact_access_tools_are_excluded_from_recursive_ground_truth_capture():
    from app.services.deep_agent.ground_truth import (
        compaction_protected_tool_names,
        ground_truth_tool_names,
    )
    from app.tools.artifacts import (
        inspect_artifact_tool,
        list_artifacts_tool,
        read_artifact_tool,
    )

    names = ground_truth_tool_names([
        _deterministic_read,
        list_artifacts_tool,
        inspect_artifact_tool,
        read_artifact_tool,
    ])

    assert names == {"deterministic_read"}
    assert compaction_protected_tool_names([
        _deterministic_read,
        list_artifacts_tool,
        inspect_artifact_tool,
        read_artifact_tool,
    ]) == {"deterministic_read", "read_artifact"}


def test_artifact_read_reuses_source_reference_without_recursive_capture():
    from app.services.deep_agent.compaction import project_compaction_messages
    from app.services.deep_agent.ground_truth import GroundTruthArtifactMiddleware
    from app.tools.artifacts import read_artifact_tool

    backend = SimpleNamespace(
        capture_tool_result=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("artifact reads must not create another artifact")
        )
    )
    middleware = GroundTruthArtifactMiddleware(
        tools=[read_artifact_tool], backend=backend
    )
    content = json.dumps({
        "artifact_id": 45,
        "kind": "tool_result",
        "content_hash": "sha256:" + "a" * 64,
        "tool_name": "get_positions",
        "tool_call_id": "positions-45",
        "generated_at": "2026-07-16T01:00:00Z",
        "observed_at": "2026-07-16T01:00:01Z",
        "data_as_of": "2026-07-16T00:59:00Z",
        "locator": "/large_tool_results/positions-45",
        "byte_size": 200,
        "summary": {"status": "ok"},
        "content": {"delta_cash": 123.0},
    })
    request = SimpleNamespace(
        tool_call={
            "name": "read_artifact",
            "args": {"artifact_id": 45},
            "id": "read-45",
        },
        runtime=SimpleNamespace(config={}),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda _request: ToolMessage(
            content=content,
            name="read_artifact",
            tool_call_id="read-45",
        ),
    )

    assert result.additional_kwargs["artifact_ref"]["artifact_id"] == 45
    assert result.additional_kwargs["artifact_ref"]["tool_call_id"] == "positions-45"
    assert result.additional_kwargs["artifact_ref_provenance"] == "server_capture_v1"
    projected, references = project_compaction_messages([result])
    assert "delta_cash" not in str(projected[0].content)
    assert references[0]["artifact_id"] == 45


def test_command_capture_preserves_resume_and_routing_fields():
    from app.services.deep_agent.ground_truth import GroundTruthArtifactMiddleware

    reference = {
        "artifact_id": 7,
        "kind": "tool_result",
        "content_hash": "sha256:" + "d" * 64,
        "tool_name": "deterministic_read",
        "tool_call_id": "read-command",
        "generated_at": "2026-07-16T01:00:00Z",
        "observed_at": "2026-07-16T01:00:00Z",
        "data_as_of": None,
        "locator": "/large_tool_results/read-command",
        "byte_size": 2,
        "summary": {"status": "ok"},
    }
    middleware = GroundTruthArtifactMiddleware(
        tools=[_deterministic_read],
        backend=SimpleNamespace(capture_tool_result=lambda **_kwargs: reference),
    )
    original = Command(
        graph="parent",
        goto="next_node",
        resume={"approval": "confirmed"},
        update={
            "messages": [
                ToolMessage(
                    content="{}",
                    name="deterministic_read",
                    tool_call_id="read-command",
                )
            ],
            "unchanged": 1,
        },
    )

    result = middleware.wrap_tool_call(
        _request(config={}, tool_call_id="read-command"),
        lambda _request: original,
    )

    assert result.graph == original.graph
    assert result.goto == original.goto
    assert result.resume == original.resume
    assert result.update["unchanged"] == 1
    assert result.update["messages"][0].additional_kwargs["artifact_ref"] == reference
    assert (
        result.update["messages"][0].additional_kwargs["artifact_ref_provenance"]
        == "server_capture_v1"
    )


def test_untrusted_preexisting_reference_is_replaced_by_server_capture():
    from app.services.deep_agent.ground_truth import GroundTruthArtifactMiddleware

    server_reference = {
        "artifact_id": 8,
        "kind": "tool_result",
        "content_hash": "sha256:" + "e" * 64,
        "tool_name": "deterministic_read",
        "tool_call_id": "read-forged",
        "generated_at": "2026-07-16T01:00:00Z",
        "observed_at": "2026-07-16T01:00:00Z",
        "data_as_of": None,
        "locator": "/large_tool_results/read-forged",
        "byte_size": 2,
        "summary": {"status": "ok"},
    }
    calls = []
    middleware = GroundTruthArtifactMiddleware(
        tools=[_deterministic_read],
        backend=SimpleNamespace(
            capture_tool_result=lambda **kwargs: calls.append(kwargs) or server_reference
        ),
    )
    message = ToolMessage(
        content="{}",
        name="deterministic_read",
        tool_call_id="read-forged",
        additional_kwargs={
            "artifact_ref": {**server_reference, "artifact_id": 999},
        },
    )

    result = middleware.wrap_tool_call(
        _request(config={}, tool_call_id="read-forged"),
        lambda _request: message,
    )

    assert len(calls) == 1
    assert result.additional_kwargs["artifact_ref"] == server_reference
    assert result.additional_kwargs["artifact_ref_provenance"] == "server_capture_v1"

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path

from app import database
from app.models import AgentTask, AgentThread, DomainEvent, SessionArtifact, utcnow
from app.services.deep_agent.ledger import EvidenceRef, LedgerWriter
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state


def test_cas_backend_writes_large_tool_result_to_blob_and_ledger(session, settings):
    from app.services.deep_agent.cas_backend import (
        ContentAddressedFilesystemBackend,
    )

    thread = AgentThread(title="cas", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    task = AgentTask(
        workflow_id=state.domain_workflow_id,
        task_type="fetch_position_summaries",
        inputs={"portfolio_id": 1, "fields": "summary"},
        depends_on=[],
        assigned_persona="trader",
        assigned_session_id=state.orchestrator_session_id,
        status="in_progress",
        context_pack_id=state.context_pack_id,
    )
    session.add(task)
    session.commit()

    content = '{"rows":[{"id":1}]}'
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    backend = ContentAddressedFilesystemBackend(root_dir=settings.artifact_dir / "blobs")
    result = backend.write(
        "/tool-call-1.json",
        content,
        config={
            "configurable": {
                "thread_id": f"{thread.id}:task:{task.id}",
                "workflow_id": state.domain_workflow_id,
                "session_id": state.orchestrator_session_id,
                "task_id": task.id,
                "context_pack_id": state.context_pack_id,
            }
        },
    )

    assert result.error is None
    assert result.path == "/tool-call-1.json"
    blob_path = settings.artifact_dir / "blobs" / expected_hash[:2] / f"{expected_hash}.json"
    assert blob_path.read_text(encoding="utf-8") == content

    with database.SessionLocal() as check:
        artifact = check.query(SessionArtifact).filter_by(rendered_path="/large_tool_results/tool-call-1.json").one()

    assert artifact.workflow_id == state.domain_workflow_id
    assert artifact.session_id == state.orchestrator_session_id
    assert artifact.task_id == task.id
    assert artifact.context_pack_id == state.context_pack_id
    assert artifact.kind == "tool_result"
    assert artifact.payload["blob_hash"] == expected_hash
    assert artifact.payload["size"] == len(content.encode("utf-8"))
    assert artifact.payload["tool_call_id"] == "tool-call-1"

    read = backend.read("/tool-call-1.json")
    assert read.error is None
    assert read.file_data["content"] == content


def test_cas_backend_grep_searches_large_tool_result_blobs(session, settings):
    from app.services.deep_agent.cas_backend import (
        ContentAddressedFilesystemBackend,
    )

    thread = AgentThread(title="cas-grep", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()
    backend = ContentAddressedFilesystemBackend(root_dir=settings.artifact_dir / "blobs")
    write = backend.write(
        "/toolu_abc.json",
        '{"rows":[{"portfolio":"Snowballs","ko_pct_from_spot":4.2}]}',
        config={
            "configurable": {
                "thread_id": f"{thread.id}:task:1",
                "workflow_id": state.domain_workflow_id,
                "session_id": state.orchestrator_session_id,
                "context_pack_id": state.context_pack_id,
            }
        },
    )
    assert write.error is None

    result = backend.grep("Snowballs", path="/toolu_abc.json")

    assert result.error is None
    assert result.matches == [
        {
            "path": "/toolu_abc.json",
            "line": 1,
            "text": '{"rows":[{"portfolio":"Snowballs","ko_pct_from_spot":4.2}]}',
        }
    ]


def test_cas_gc_evacuates_old_provisional_blob_and_tombstones_ledger(
    session, settings
):
    from app.services.deep_agent.cas_backend import sweep_cas_blobs

    thread = AgentThread(title="cas-gc", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    root = settings.artifact_dir / "blobs"
    blob_hash = _write_blob(root, '{"claim":true}')

    writer = LedgerWriter(session)
    artifact = writer.write_artifact(
        workflow_id=state.domain_workflow_id,
        session_id=state.orchestrator_session_id,
        context_pack_id=state.context_pack_id,
        kind="claim",
        title="old provisional claim",
        payload={"blob_hash": blob_hash, "size": 14, "blob_state": "live"},
        evidence_refs=[
            EvidenceRef(
                evidence_kind="agent_attestation",
                evidence_payload={"persona": "trader"},
            )
        ],
    )
    artifact.created_at = utcnow() - timedelta(days=181)
    session.commit()

    result = sweep_cas_blobs(root_dir=root, now=utcnow())

    assert result.evicted_artifact_ids == [artifact.id]
    assert not _blob_path(root, blob_hash).exists()

    session.expire_all()
    refreshed = session.get(SessionArtifact, artifact.id)
    assert refreshed is not None
    assert refreshed.payload["blob_state"] == "gc_evicted"
    assert refreshed.payload["gc_tier"] == "provisional"
    event = session.query(DomainEvent).filter_by(kind="artifact_gc'd").one()
    assert event.artifact_id == artifact.id
    assert event.payload["blob_hash"] == blob_hash
    assert event.payload["tier"] == "provisional"


def test_cas_gc_keeps_load_bearing_artifact_blobs(session, settings):
    from app.services.deep_agent.cas_backend import sweep_cas_blobs

    thread = AgentThread(title="cas-gc-retain", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    root = settings.artifact_dir / "blobs"
    blob_hash = _write_blob(root, '{"truth":true}')

    writer = LedgerWriter(session)
    artifact = writer.write_artifact(
        workflow_id=state.domain_workflow_id,
        session_id=state.orchestrator_session_id,
        context_pack_id=state.context_pack_id,
        kind="claim",
        title="load-bearing claim",
        payload={"blob_hash": blob_hash, "size": 14, "blob_state": "live"},
        evidence_refs=[
            EvidenceRef(
                evidence_kind="snapshot",
                evidence_payload={"snapshot_ids": ["positions:v1"]},
            )
        ],
    )
    artifact.created_at = utcnow() - timedelta(days=365)
    session.commit()

    result = sweep_cas_blobs(root_dir=root, now=utcnow())

    assert result.evicted_artifact_ids == []
    assert _blob_path(root, blob_hash).exists()
    assert session.get(SessionArtifact, artifact.id).payload["blob_state"] == "live"
    assert session.query(DomainEvent).filter_by(kind="artifact_gc'd").count() == 0


def test_cas_gc_removes_old_orphan_blobs(session, settings):
    from app.services.deep_agent.cas_backend import sweep_cas_blobs

    root = settings.artifact_dir / "blobs"
    blob_hash = _write_blob(root, '{"orphan":true}')
    blob_path = _blob_path(root, blob_hash)
    old = (utcnow() - timedelta(hours=25)).timestamp()
    os.utime(blob_path, (old, old))

    result = sweep_cas_blobs(root_dir=root, now=utcnow())

    assert result.removed_orphan_hashes == [blob_hash]
    assert not blob_path.exists()


def test_orchestrator_backend_routes_large_tool_results_to_cas(settings):
    database.configure_database(settings)
    database.init_db()

    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend
    from app.services.deep_agent.orchestrator import _build_backend

    backend = _build_backend()
    routed, stripped = backend._get_backend_and_key("/large_tool_results/tool-call-1.json")

    assert isinstance(routed, ContentAddressedFilesystemBackend)
    assert stripped == "/tool-call-1.json"


def _write_blob(root: Path, content: str) -> str:
    blob_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    path = _blob_path(root, blob_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return blob_hash


def _blob_path(root: Path, blob_hash: str) -> Path:
    return root / blob_hash[:2] / f"{blob_hash}.json"


def test_async_agent_backend_routes_large_tool_results_to_cas(settings):
    database.configure_database(settings)
    database.init_db()

    from app.services.async_agents.agent import _build_backend
    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend

    backend = _build_backend()
    routed, stripped = backend._get_backend_and_key("/large_tool_results/tool-call-1.json")

    assert isinstance(routed, ContentAddressedFilesystemBackend)
    assert stripped == "/tool-call-1.json"

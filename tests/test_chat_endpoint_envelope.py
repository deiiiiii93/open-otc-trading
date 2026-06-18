"""End-to-end test: the chat endpoint accepts an ``envelope`` field
and forwards it to ``AgentService.stream_and_persist``.

The full agent path is stubbed so this test stays unit-scope and does not
require live LLM channels."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# tests/ is not a Python package, so `from test_api import make_client` would
# normally fail. The pytest pythonpath only points at backend/. Add this dir
# explicitly so we can reuse the existing client fixture.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def client(tmp_path: Path):
    from test_api import make_client  # noqa: E402

    return make_client(tmp_path)


def _stub_stream_and_persist(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace stream_and_persist with a capture stub. Returns the call-arg sink."""
    seen: dict[str, Any] = {}

    async def fake_stream(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        # Minimum: a single SSE event so the StreamingResponse closes cleanly.
        yield "event: ready\ndata: {}\n\n"
        yield 'event: done\ndata: {"message_id": 0}\n\n'

    from app.services import agents as agents_mod

    monkeypatch.setattr(
        agents_mod.AgentService, "stream_and_persist", fake_stream
    )
    return seen


def test_message_post_forwards_envelope_field(client, monkeypatch):
    seen = _stub_stream_and_persist(monkeypatch)

    r = client.post("/api/chat/threads", json={"title": "t"})
    assert r.status_code == 200, r.text
    thread_id = r.json()["id"]

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "hi", "envelope": "pet_page"},
    )
    assert r.status_code == 200, r.text
    assert seen.get("envelope") == "pet_page"


def test_message_post_defaults_envelope_to_none_when_omitted(client, monkeypatch):
    """No envelope -> service receives None and picks its own default."""
    seen = _stub_stream_and_persist(monkeypatch)

    r = client.post("/api/chat/threads", json={"title": "t"})
    thread_id = r.json()["id"]

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "hi"},
    )
    assert r.status_code == 200, r.text
    assert "envelope" in seen
    assert seen["envelope"] is None


def test_message_post_persists_envelope_in_user_meta(client, monkeypatch):
    """Envelope is stored on the user message's meta for later inspection."""
    _stub_stream_and_persist(monkeypatch)

    r = client.post("/api/chat/threads", json={"title": "t"})
    thread_id = r.json()["id"]

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "hi", "envelope": "desk_workflow"},
    )
    assert r.status_code == 200

    listed = client.get("/api/chat/threads")
    messages = listed.json()[0]["messages"]
    user_messages = [m for m in messages if m["role"] == "user"]
    assert user_messages
    assert user_messages[-1]["meta"]["envelope"] == "desk_workflow"


def test_resolve_envelope_helper_defaults_by_page_context_presence():
    """AgentService._resolve_envelope: present page_context -> pet_page; absent -> desk_workflow."""
    from app.schemas import AgentPageContext
    from app.services.agents import AgentService
    from app.services.deep_agent.envelopes import Envelope

    svc = object.__new__(AgentService)  # bypass __init__; we only call the pure helper
    pc = AgentPageContext(route="positions", title="Positions")

    assert svc._resolve_envelope(None, pc) is Envelope.PET_PAGE  # type: ignore[arg-type]
    assert svc._resolve_envelope(None, None) is Envelope.DESK_WORKFLOW  # type: ignore[arg-type]
    assert svc._resolve_envelope("pet_diagnostic", None) is Envelope.PET_DIAGNOSTIC  # type: ignore[arg-type]
    assert svc._resolve_envelope("bogus", pc) is Envelope.PET_PAGE  # type: ignore[arg-type]


def _stub_capturing_agent(monkeypatch, captured: list[dict]) -> None:
    """Replace AgentService._sync_agent_for_selection with a stub that
    captures each invoke's config so tests can assert on the configurable."""
    from app.services.agents import AgentService

    def fake_sync_agent(self, model_selection, *, yolo_mode=False):
        class _Stub:
            def invoke(self, cmd, config=None):
                from langchain_core.messages import AIMessage

                captured.append(config or {})
                return {"messages": [AIMessage(content="ok")]}

        return _Stub()

    monkeypatch.setattr(AgentService, "_sync_agent_for_selection", fake_sync_agent)


def _seed_pending_action_message(
    *, envelope_final: str | None = None, thread_title: str = "t"
) -> tuple[int, int]:
    from app import database
    from app.models import AgentMessage, AgentThread

    with database.SessionLocal() as session:
        thread = AgentThread(title=thread_title, character="trader")
        session.add(thread)
        session.flush()
        meta: dict = {
            "agent_phase": "awaiting_confirmation",
            "pending_actions": [
                {
                    "id": "act-1",
                    "tool_name": "run_batch_pricing",
                    "label": "Price",
                    "summary": "...",
                    "payload": {},
                    "status": "pending",
                }
            ],
        }
        if envelope_final is not None:
            meta["envelope_final"] = envelope_final
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character="trader",
            content="please confirm",
            meta=meta,
        )
        session.add(msg)
        session.commit()
        return thread.id, msg.id


def _seed_workflow_pending_action_message() -> tuple[int, int, int, dict]:
    from app import database
    from app.models import AgentMessage, AgentTask, AgentThread
    from app.services.deep_agent.workflow_state import ensure_thread_workflow_state

    with database.SessionLocal() as session:
        thread = AgentThread(title="workflow hitl", character="trader")
        session.add(thread)
        session.flush()
        state = ensure_thread_workflow_state(session, thread.id)
        task = AgentTask(
            workflow_id=state.domain_workflow_id,
            task_type="converse_with_user",
            inputs={"user_message": "run risk"},
            depends_on=[],
            assigned_persona="orchestrator",
            assigned_session_id=state.orchestrator_session_id,
            status="awaiting_hitl",
            context_pack_id=state.context_pack_id,
        )
        session.add(task)
        session.flush()
        source_meta = {
            "task_id": task.id,
            "session_id": state.orchestrator_session_id,
            "context_pack_id": state.context_pack_id,
            "checkpointer_key": f"{thread.id}:task:{task.id}",
            "workflow_id": state.domain_workflow_id,
            "envelope_final": "pet_page",
            "agent_runtime": "deepagents",
            "audit": {
                "tool_call_id": "intr-1",
                "tool_name": "run_batch_pricing",
                "persona": "orchestrator",
                "emitted_at": "2026-05-25T00:00:00Z",
            },
        }
        msg = AgentMessage(
            thread_id=thread.id,
            workflow_id=state.domain_workflow_id,
            session_id=state.orchestrator_session_id,
            role="assistant",
            character="orchestrator",
            content="please confirm",
            meta={
                "agent_phase": "awaiting_confirmation",
                "model_selection": {
                    "channel": "zenmux",
                    "provider": "anthropic",
                    "model": "anthropic/claude-sonnet-4-6",
                },
                "yolo_mode": False,
                "pending_actions": [
                    {
                        "id": "intr-1:0",
                        "tool_name": "run_batch_pricing",
                        "label": "Run risk analysis",
                        "summary": "Run risk",
                        "payload": {"portfolio_id": 7},
                        "status": "pending",
                        "source_meta": source_meta,
                    }
                ],
            },
        )
        session.add(msg)
        session.commit()
        return thread.id, msg.id, task.id, source_meta


def test_hitl_resume_widens_pet_page_to_desk_workflow(tmp_path, monkeypatch):
    """Iter-3 regression: HITL pauses BEFORE the gate, so a pet-page write
    leaves envelope_final='pet_page'. Resume must widen to desk_workflow
    so the user's approval actually runs the gated write tool."""
    from test_api import make_client  # noqa: E402

    client = make_client(tmp_path)
    captured: list[dict] = []
    _stub_capturing_agent(monkeypatch, captured)
    thread_id, msg_id = _seed_pending_action_message(envelope_final="pet_page")

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-1/confirm"
    )
    assert r.status_code == 200, r.text
    configurable = captured[0].get("configurable", {})
    assert configurable.get("envelope") == "desk_workflow"


def test_hitl_resume_preserves_desk_async_envelope(tmp_path, monkeypatch):
    """A turn that ended in desk_async should resume in desk_async; we only
    widen pet_page/None up to desk_workflow."""
    from test_api import make_client  # noqa: E402

    client = make_client(tmp_path)
    captured: list[dict] = []
    _stub_capturing_agent(monkeypatch, captured)
    thread_id, msg_id = _seed_pending_action_message(envelope_final="desk_async")

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-1/confirm"
    )
    assert r.status_code == 200, r.text
    configurable = captured[0].get("configurable", {})
    assert configurable.get("envelope") == "desk_async"


def test_hitl_resume_defaults_to_desk_workflow_when_envelope_missing(tmp_path, monkeypatch):
    """Pre-Phase-2 assistant messages have no envelope_final; resume must
    still let the approved write tool run by defaulting to desk_workflow."""
    from test_api import make_client  # noqa: E402

    client = make_client(tmp_path)
    captured: list[dict] = []
    _stub_capturing_agent(monkeypatch, captured)
    thread_id, msg_id = _seed_pending_action_message()

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-1/confirm"
    )
    assert r.status_code == 200, r.text
    configurable = captured[0].get("configurable", {})
    assert configurable.get("envelope") == "desk_workflow"


def test_hitl_resume_pre_confirms_cost_preview(tmp_path, monkeypatch):
    """HITL approval is itself the cost-preview confirmation step. The
    resume must thread confirmed_cost_preview=true so the gate doesn't
    raise CostPreviewRequiredError on the approved write (which the
    /confirm endpoint would surface as a 502)."""
    from test_api import make_client  # noqa: E402

    client = make_client(tmp_path)
    captured: list[dict] = []
    _stub_capturing_agent(monkeypatch, captured)
    thread_id, msg_id = _seed_pending_action_message()

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/act-1/confirm"
    )
    assert r.status_code == 200, r.text
    configurable = captured[0].get("configurable", {})
    assert configurable.get("confirmed_cost_preview") is True


def test_hitl_resume_uses_task_scoped_source_meta_for_workflow_routing(
    tmp_path,
    monkeypatch,
):
    from test_api import make_client  # noqa: E402

    from dataclasses import replace

    from app import database
    import app.main as app_main_module
    from app.models import AgentMessage, AgentTask

    client = make_client(tmp_path)
    app_main_module.agent_service.settings = replace(
        app_main_module.agent_service.settings,
        feature_workflow_routing=True,
    )
    captured: list[dict] = []
    _stub_capturing_agent(monkeypatch, captured)
    thread_id, msg_id, task_id, source_meta = _seed_workflow_pending_action_message()

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/{msg_id}/actions/intr-1:0/confirm"
    )
    assert r.status_code == 200, r.text
    configurable = captured[0].get("configurable", {})
    assert configurable["thread_id"] == source_meta["checkpointer_key"]
    assert configurable["task_id"] == task_id
    assert configurable["session_id"] == source_meta["session_id"]
    assert configurable["context_pack_id"] == source_meta["context_pack_id"]
    assert configurable["workflow_id"] == source_meta["workflow_id"]
    assert configurable["envelope"] == "desk_workflow"
    assert configurable["confirmed_cost_preview"] is True
    assert configurable["tools_scope"] == ["propose_reply_options"]

    with database.SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        original = session.get(AgentMessage, msg_id)
        assistant = (
            session.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id, AgentMessage.id != msg_id)
            .order_by(AgentMessage.id.desc())
            .first()
        )

    assert task.status == "completed"
    assert original.meta["pending_actions"][0]["status"] == "confirmed"
    assert assistant.meta["workflow_routing"] is True
    assert assistant.meta["task_id"] == task_id


def test_record_envelope_transition_writes_audit_event(tmp_path, monkeypatch):
    """The streaming path's _record_envelope_transition writes an AuditEvent row."""
    from app import database
    from app.config import Settings
    from app.models import AuditEvent
    from app.services.agents import AgentService

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()

    svc = object.__new__(AgentService)  # bypass __init__

    audit_payload = {
        "event_type": "envelope.transitioned",
        "previous_envelope": "pet_page",
        "new_envelope": "desk_workflow",
        "reason": "write_action_requested",
        "denied_tool": "create_portfolio",
        "denied_group": "domain_write",
    }
    svc._record_envelope_transition(thread_id=99, audit_payload=audit_payload)

    with database.SessionLocal() as session:
        events = session.query(AuditEvent).all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_type == "envelope.transitioned"
    assert evt.subject_type == "thread"
    assert evt.subject_id == "99"
    assert evt.payload["new_envelope"] == "desk_workflow"
    assert evt.payload["denied_tool"] == "create_portfolio"

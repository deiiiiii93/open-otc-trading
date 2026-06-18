"""Unit tests for the async_agents module."""
from __future__ import annotations

import pytest

from app.models import TaskRun


def test_task_run_has_async_agent_columns():
    """TaskRun gains parent_thread_id, description, result_payload, cancel_requested."""
    columns = {col.name for col in TaskRun.__table__.columns}
    assert "parent_thread_id" in columns
    assert "description" in columns
    assert "result_payload" in columns
    assert "cancel_requested" in columns


def test_task_run_async_columns_have_expected_types():
    """Types: parent_thread_id int FK nullable, description Text nullable,
    result_payload JSON nullable, cancel_requested Boolean default False."""
    cols = {c.name: c for c in TaskRun.__table__.columns}
    assert cols["parent_thread_id"].nullable is True
    assert cols["description"].nullable is True
    assert cols["result_payload"].nullable is True
    assert cols["cancel_requested"].nullable is False
    assert cols["cancel_requested"].default.arg is False


def test_task_run_async_columns_present_in_test_db(session):
    """The test fixture-built DB has the new columns (migration or metadata.create_all)."""
    from sqlalchemy import inspect

    inspector = inspect(session.bind)
    cols = {col["name"] for col in inspector.get_columns("task_runs")}
    assert "parent_thread_id" in cols
    assert "description" in cols
    assert "result_payload" in cols
    assert "cancel_requested" in cols


def test_async_agent_identity_prompt_loads_and_has_required_sections():
    """The identity prompt names each section the runtime depends on."""
    from pathlib import Path
    import app.services.async_agents as pkg

    prompt_path = Path(pkg.__file__).parent / "prompts" / "async_agent.md"
    text = prompt_path.read_text(encoding="utf-8")
    for needle in (
        "background analyst",
        "## Decision lens",
        "## Tools you use",
        "## Scratch and artifacts",
        "## Clarification policy",
        "## Skills",
        "## Output style",
        "## HITL bubble-up",
        "## Forbidden",
    ):
        assert needle in text, f"missing section/phrase: {needle!r}"
    assert "/skills/workflows/" in text
    assert "/skills/references/" in text
    assert "/skills/domains/" not in text
    assert "/skills/procedures/" not in text
    assert "/skills/products/" not in text
    assert "/skills/routing/" not in text


def test_build_async_agent_loads_workflow_skill_catalog(monkeypatch):
    from app.services.async_agents import agent as async_agent
    from app.services.deep_agent.envelope_skills import (
        EnvelopeSkillsMiddleware,
        WORKFLOW_SKILL_SOURCES,
    )

    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)

        class Graph:
            name = kwargs["name"]

        return Graph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    async_agent.build_async_agent(
        model=object(),
        tools=[],
        checkpointer=object(),
        task_id=42,
    )

    assert captured["skills"] == []
    middleware = captured["middleware"]
    envelope_middleware = [
        item for item in middleware if isinstance(item, EnvelopeSkillsMiddleware)
    ]
    assert len(envelope_middleware) == 1
    assert tuple(envelope_middleware[0].sources) == WORKFLOW_SKILL_SOURCES


def test_async_policy_constants():
    """Policy constants are as specified in spec §2.1."""
    from app.services.async_agents import policy

    assert policy.MAX_CONCURRENT_PER_THREAD == 4
    assert policy.SCRATCH_DIR_TEMPLATE == "/trading_desk/async/{task_id}/"
    # Mirrors trader/risk minus clarification-policy
    assert policy.ASYNC_POLICY_FRAGMENTS == (
        "read-before-compute-policy",
        "cost-preview-policy",
        "yolo-hitl-policy",
        "python-analysis-policy",
    )


def test_scratch_dir_for_task():
    """Helper builds the scratch dir for a task id."""
    from app.services.async_agents import policy

    assert policy.scratch_dir_for_task(42) == "/trading_desk/async/42/"


def test_build_async_agent_writes_only_to_task_scratch():
    """FilesystemPermissions allow write to /trading_desk/async/<task_id>/** only."""
    from app.services.async_agents.agent import _filesystem_permissions

    perms = _filesystem_permissions(task_id=42)
    paths_with_write_allow: set[str] = set()
    for perm in perms:
        ops = set(getattr(perm, "operations", []) or [])
        mode = getattr(perm, "mode", None)
        path_list = getattr(perm, "paths", []) or []
        if mode == "allow" and "write" in ops:
            paths_with_write_allow.update(path_list)
    assert "/trading_desk/async/42" in paths_with_write_allow
    assert "/trading_desk/async/42/**" in paths_with_write_allow


def test_build_async_agent_uses_same_interrupt_config():
    """Async agent's interrupt_on mirrors hitl.interrupt_on_config exactly."""
    from app.services.async_agents.agent import _interrupt_on_for_async
    from app.services.deep_agent.hitl import interrupt_on_config

    assert _interrupt_on_for_async(yolo_mode=False) == interrupt_on_config(yolo_mode=False)
    assert _interrupt_on_for_async(yolo_mode=True) == interrupt_on_config(yolo_mode=True)


def test_compose_task_brief_includes_framework_meta():
    """Task brief HumanMessage contains prompt, structured inputs, framework-meta fields."""
    from app.services.async_agents.runner import compose_task_brief

    msg = compose_task_brief(
        task_id=42,
        parent_thread_id=7,
        prompt="Draft a narrative for report 99.",
        inputs={"report_id": 99, "portfolio_id": 3},
        accounting_date="2026-05-16",
    )
    text = msg.content
    assert "Draft a narrative for report 99." in text
    assert "report_id" in text and "99" in text
    assert "portfolio_id" in text and "3" in text
    assert "/trading_desk/async/42/" in text
    assert "Accounting anchor" in text
    assert "2026-05-16" in text
    assert "task_id" in text.lower()
    assert "42" in text
    assert "parent_thread_id" in text.lower()
    assert "7" in text


def test_compose_task_brief_handles_missing_inputs():
    """inputs=None or empty dict still produces a valid brief."""
    from app.services.async_agents.runner import compose_task_brief

    msg = compose_task_brief(
        task_id=1,
        parent_thread_id=2,
        prompt="Hello.",
        inputs=None,
        accounting_date="2026-05-16",
    )
    assert "Hello." in msg.content
    assert "(no structured inputs)" in msg.content or "Inputs: none" in msg.content


def test_start_async_agent_task_creates_taskrun_row(session, agent_thread_factory):
    """start_async_agent_task inserts a QUEUED TaskRun with kind=async_agent."""
    import json as _json

    from app.models import TaskRun, TaskStatus
    from app.services.async_agents import runner

    thread = agent_thread_factory()
    task_id = runner.start_async_agent_task(
        session,
        parent_thread_id=thread.id,
        description="test draft",
        prompt="Test brief.",
        inputs={"x": 1},
        _submit=lambda *a, **k: None,
    )
    session.commit()
    row = session.get(TaskRun, task_id)
    assert row is not None
    assert row.kind == "async_agent"
    assert row.status == TaskStatus.QUEUED.value
    assert row.parent_thread_id == thread.id
    assert row.description == "test draft"
    assert row.cancel_requested is False
    payload = _json.loads(row.message)
    assert payload["prompt"] == "Test brief."
    assert payload["inputs"] == {"x": 1}


def test_start_async_agent_task_concurrency_cap_rejects(session, agent_thread_factory):
    """5th active dispatch on the same parent thread returns too_many_running."""
    from app.models import TaskRun, TaskStatus
    from app.services.async_agents import policy, runner

    thread = agent_thread_factory()
    for _ in range(policy.MAX_CONCURRENT_PER_THREAD):
        session.add(
            TaskRun(
                kind="async_agent",
                status=TaskStatus.RUNNING.value,
                parent_thread_id=thread.id,
                description="prior",
            )
        )
    session.commit()
    with pytest.raises(runner.TooManyRunningError):
        runner.start_async_agent_task(
            session,
            parent_thread_id=thread.id,
            description="overflow",
            prompt="...",
            inputs=None,
            _submit=lambda *a, **k: None,
        )


def test_bubble_up_writes_pending_action_message(session, agent_thread_factory):
    """A subagent Interrupt projects to a parent-thread AgentMessage."""
    from app.models import AgentMessage, TaskRun
    from app.services.async_agents import bubble_up
    from langgraph.types import Interrupt

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="bubble test",
    )
    session.add(row)
    session.flush()

    intr = Interrupt(
        value={
            "action_requests": [
                {
                    "name": "create_report",
                    "args": {"portfolio_id": 1},
                    "description": "Create a report",
                }
            ]
        },
        id="intr-1",
    )
    bubble_up.handle(session, task_id=row.id, interrupts=[intr])
    session.commit()

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.role == "assistant"
    assert msg.character == "async_agent"
    assert msg.meta["agent_phase"] == "awaiting_confirmation"
    assert msg.meta["async_task_id"] == row.id
    pending = msg.meta["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "create_report"
    assert pending[0]["async_task_id"] == row.id
    # persona is None for async proposals (the Literal only allows persona names)
    assert pending[0]["persona"] is None


def test_autopost_writes_completion_message_and_materializes_artifacts(
    session, agent_thread_factory, settings
):
    """autopost.handle writes the final message + materializes scratch files."""
    from langchain_core.messages import AIMessage

    from app.models import AgentMessage, TaskRun
    from app.services.async_agents import autopost

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="autopost test",
    )
    session.add(row)
    session.flush()

    state_values = {
        "messages": [AIMessage(content="Headline.\n\n- finding 1\n- finding 2")],
        "files": {
            f"/trading_desk/async/{row.id}/note.md": "# Note\nhello",
        },
    }
    autopost.handle(session, task_id=row.id, state_values=state_values)
    session.commit()

    msgs = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .order_by(AgentMessage.id)
        .all()
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.role == "assistant"
    assert msg.character == "async_agent"
    assert msg.meta["agent_phase"] == "completed"
    assert msg.meta["async_task_id"] == row.id
    assert "Headline." in msg.content

    materialized = (
        settings.artifact_dir
        / "agent"
        / f"thread-{thread.id}"
        / f"async-{row.id}"
        / "note.md"
    )
    assert materialized.exists()
    assert "hello" in materialized.read_text()

    assets = msg.meta.get("assets", [])
    assert any(
        a.get("url", "").endswith(f"async-{row.id}/note.md") for a in assets
    )


def test_autopost_materializes_generic_binary_tool_artifacts(
    session, agent_thread_factory, settings
):
    import base64
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    from app.models import AgentMessage, TaskRun
    from app.services.async_agents import autopost

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="autopost report",
    )
    session.add(row)
    session.flush()
    docx_path = "/trading_desk/reports/async_report.docx"
    docx_bytes = b"PK\x03\x04docx"

    state_values = {
        "messages": [
            ToolMessage(
                content=json.dumps(
                    {
                        "file_path": docx_path,
                        "format": "docx",
                        "artifacts": [
                            {
                                "path": docx_path,
                                "size_bytes": len(docx_bytes),
                                "content_b64": base64.b64encode(docx_bytes).decode("ascii"),
                                "kind": "binary",
                            }
                        ],
                    }
                ),
                name="write_report_artifact",
                tool_call_id="report-1",
            ),
            AIMessage(content=f"DOCX written: {docx_path}"),
        ],
    }
    autopost.handle(session, task_id=row.id, state_values=state_values)
    session.commit()

    msg = session.query(AgentMessage).filter(AgentMessage.thread_id == thread.id).one()
    assets = msg.meta.get("assets", [])
    docx_assets = [asset for asset in assets if asset.get("path") == docx_path]
    assert len(docx_assets) == 1
    assert docx_assets[0]["mime_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    artifact_path = settings.artifact_dir / "agent" / f"thread-{thread.id}" / "trading_desk" / "reports" / "async_report.docx"
    assert artifact_path.read_bytes() == docx_bytes


def test_autopost_rejects_path_traversal_in_async_artifacts(
    session, agent_thread_factory, settings
):
    """Virtual paths with '..' segments must not escape async-<id>/ on disk."""
    from langchain_core.messages import AIMessage

    from app.models import TaskRun
    from app.services.async_agents import autopost

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="traversal test",
    )
    session.add(row)
    session.flush()

    state_values = {
        "messages": [AIMessage(content="ok")],
        "files": {
            f"/trading_desk/async/{row.id}/../escape.md": "should not be written",
            f"/trading_desk/async/{row.id}/safe.md": "ok",
        },
    }
    msg = autopost.handle(session, task_id=row.id, state_values=state_values)
    session.commit()

    escape_target = (
        settings.artifact_dir / "agent" / f"thread-{thread.id}" / "escape.md"
    )
    safe_target = (
        settings.artifact_dir
        / "agent"
        / f"thread-{thread.id}"
        / f"async-{row.id}"
        / "safe.md"
    )
    assert not escape_target.exists()
    assert safe_target.exists()
    assets = msg.meta.get("assets", [])
    assert all("escape.md" not in (a.get("url") or "") for a in assets)


def test_stale_recovery_marks_async_agent_failed_and_posts_message(
    session, agent_thread_factory
):
    from app.models import AgentMessage, TaskRun, TaskStatus
    from app.services.task_runner import mark_stale_tasks_failed

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="stale test",
    )
    session.add(row)
    session.flush()

    count = mark_stale_tasks_failed(session)
    session.commit()
    assert count >= 1
    refreshed = session.get(TaskRun, row.id)
    assert refreshed.status == TaskStatus.FAILED.value

    posted = (
        session.query(AgentMessage)
        .filter(AgentMessage.thread_id == thread.id)
        .all()
    )
    matched = [
        m
        for m in posted
        if (m.meta or {}).get("async_task_id") == row.id
        and (m.meta or {}).get("agent_phase") == "error"
    ]
    assert len(matched) == 1
    assert "interrupted by server restart" in matched[0].content.lower()


def test_agent_action_proposal_has_async_task_id_field():
    """AgentActionProposal accepts and serializes async_task_id."""
    from app.schemas import AgentActionProposal

    p = AgentActionProposal(
        id="x:0",
        tool_name="create_report",
        label="Create report",
        summary="...",
        payload={},
        requires_confirmation=True,
        status="pending",
        async_task_id=42,
    )
    dumped = p.model_dump(mode="json")
    assert dumped["async_task_id"] == 42


def test_async_agent_schemas_exist():
    from app.schemas import AsyncAgentStartIn, AsyncAgentTaskOut

    out = AsyncAgentTaskOut(
        task_id=1,
        description="d",
        status="running",
        awaiting_approval=False,
        started_at=None,
        finished_at=None,
        last_message_preview=None,
    )
    assert out.task_id == 1

    incoming = AsyncAgentStartIn(
        description="d",
        prompt="p",
        inputs={"x": 1},
    )
    assert incoming.inputs == {"x": 1}


def test_cost_preview_fragment_includes_async_clause():
    from app.services.deep_agent.skills_paths import POLICY_DIR

    text = (POLICY_DIR / "cost-preview-policy.md").read_text(encoding="utf-8")
    assert "When you have no user in your conversation" in text
    assert "embed the cost preview into the hitl action" in text.lower()


def test_cost_preview_offers_dispatch_async_option():
    from app.services.deep_agent.skills_paths import POLICY_DIR

    text = (POLICY_DIR / "cost-preview-policy.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "(yes / dispatch async / no / adjust scope)" in lowered
    assert "≥30s" in text
    assert "start_async_agent" in lowered
    assert "return control to the orchestrator" in lowered


def test_orchestrator_prompt_has_async_dispatch_section():
    from pathlib import Path
    import app.services.deep_agent as deep_pkg

    text = (
        Path(deep_pkg.__file__).parent / "prompts" / "orchestrator.md"
    ).read_text(encoding="utf-8")
    assert "## Async dispatch" in text
    assert "start_async_agent" in text
    assert "list_async_agents" in text
    assert "cancel_async_agent" in text
    assert "Proxy 1" in text and "Tool-call budget" in text
    assert "Proxy 2" in text and "Deliverable shape" in text
    assert "Proxy 3" in text and "User intent signals" in text
    assert "Canonical examples" in text
    assert "Per-thread cap: 4" in text


def test_orchestrator_prompt_has_explicit_async_intent_override():
    from pathlib import Path
    import app.services.deep_agent as deep_pkg

    text = (
        Path(deep_pkg.__file__).parent / "prompts" / "orchestrator.md"
    ).read_text(encoding="utf-8")
    lowered = text.lower()
    assert "explicit async intent override" in lowered
    assert "must call `start_async_agent(...)`" in lowered
    assert "do not satisfy explicit async intent with inline `task(...)`" in lowered
    assert "returned task id" in lowered


def test_orchestrator_prompt_override_precedes_proxy_heuristic():
    """Override section must appear above the proxy heuristic so the model reads it first."""
    from pathlib import Path
    import app.services.deep_agent as deep_pkg

    text = (
        Path(deep_pkg.__file__).parent / "prompts" / "orchestrator.md"
    ).read_text(encoding="utf-8")
    override_idx = text.lower().find("explicit async intent override")
    proxy_idx = text.find("**Proxy 1")
    assert override_idx != -1, "override section missing"
    assert proxy_idx != -1, "Proxy 1 section missing"
    assert override_idx < proxy_idx, (
        f"override (idx={override_idx}) must precede Proxy 1 (idx={proxy_idx})"
    )


def test_resume_async_agent_interrupt_submits_run(
    session, agent_thread_factory, monkeypatch
):
    """resume_async_agent_interrupt schedules _resume_run via _submit."""
    from app.models import TaskRun
    from app.services.async_agents import resume as resume_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="resume test",
    )
    session.add(row)
    session.flush()
    session.commit()

    captured: dict = {}

    def fake_submit(fn, *args):
        captured["fn"] = fn
        captured["args"] = args
        return None

    monkeypatch.setattr(resume_mod, "_submit", fake_submit)

    resume_mod.resume_async_agent_interrupt(
        task_id=row.id,
        decision="approve",
        message=None,
    )
    assert "fn" in captured
    # fn is _resume_run, args are (task_id, decision, message)
    assert captured["args"] == (row.id, "approve", None)


def test_resume_async_agent_interrupt_refuses_cancelled_task(
    session, agent_thread_factory, monkeypatch
):
    """A cancelled task must not be resurrected by a stale HITL approval."""
    import pytest as _pytest

    from app.models import TaskRun, TaskStatus
    from app.services.async_agents import resume as resume_mod

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="cancelled mid-hitl",
        cancel_requested=True,
    )
    session.add(row)
    session.commit()

    submit_called: list = []
    monkeypatch.setattr(
        resume_mod, "_submit", lambda *a, **k: submit_called.append(a)
    )

    with _pytest.raises(resume_mod.TaskNotResumableError):
        resume_mod.resume_async_agent_interrupt(
            task_id=row.id, decision="approve", message=None
        )
    assert submit_called == []


def test_start_async_agent_task_persists_accounting_date(
    session, agent_thread_factory
):
    """accounting_date passed by the start tool lands in TaskRun.message JSON."""
    import json as _json

    from app.models import TaskRun
    from app.services.async_agents import runner

    thread = agent_thread_factory()
    task_id = runner.start_async_agent_task(
        session,
        parent_thread_id=thread.id,
        description="anchor test",
        prompt="brief",
        inputs=None,
        accounting_date="2026-05-14",
        _submit=lambda *a, **k: None,
    )
    row = session.get(TaskRun, task_id)
    payload = _json.loads(row.message)
    assert payload["accounting_date"] == "2026-05-14"


def test_start_async_agent_task_persists_model_selection_durably(
    session, agent_thread_factory
):
    """model_selection lands in result_payload so resume can read it after
    task.message has been overwritten by mark_task_running/bubble_up."""
    from app.models import TaskRun
    from app.services.async_agents import runner

    thread = agent_thread_factory()
    sel = {"channel": "ch", "provider": "deepseek", "model": "ds-v3"}
    task_id = runner.start_async_agent_task(
        session,
        parent_thread_id=thread.id,
        description="model selection durability",
        prompt="brief",
        inputs=None,
        model_selection=sel,
        _submit=lambda *a, **k: None,
    )
    row = session.get(TaskRun, task_id)
    assert isinstance(row.result_payload, dict)
    assert row.result_payload["model_selection"] == sel


def test_select_async_agent_tools_excludes_recursion_and_only_gated_set(
    session,
):
    """Async agents must run with the gated tool allowlist and must not be
    able to recursively spawn more async agents."""
    from app.services.agents import (
        DEEP_AGENT_TOOL_NAMES,
        select_async_agent_tools,
    )

    names = {tool.name for tool in select_async_agent_tools()}
    assert names == set(DEEP_AGENT_TOOL_NAMES) - {
        "start_async_agent",
        "list_async_agents",
        "cancel_async_agent",
    }


def test_task_run_parent_thread_fk_has_set_null_on_delete(session):
    """Deleting an agent_thread must not fail when async TaskRuns reference
    it; the FK is declared with ondelete=SET NULL to preserve task history."""
    from app.models import TaskRun

    col = TaskRun.__table__.c.parent_thread_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"


def test_autopost_clears_awaiting_message_and_sets_finished_at(
    session, agent_thread_factory, settings
):
    """After resume completes a task that bubble_up paused, the row should
    no longer report 'awaiting approval' and should have finished_at set."""
    from langchain_core.messages import AIMessage

    from app.models import TaskRun
    from app.services.async_agents import autopost

    thread = agent_thread_factory()
    row = TaskRun(
        kind="async_agent",
        status="running",
        parent_thread_id=thread.id,
        description="finished_at test",
        message="awaiting approval",
        result_payload={"model_selection": {"channel": "x"}},
    )
    session.add(row)
    session.flush()

    autopost.handle(
        session,
        task_id=row.id,
        state_values={"messages": [AIMessage(content="done")], "files": {}},
    )
    session.commit()
    session.refresh(row)
    assert row.message is None
    assert row.finished_at is not None
    # merge preserved the dispatch-time durable copy
    assert row.result_payload["model_selection"] == {"channel": "x"}
    assert row.result_payload["final_text"] == "done"

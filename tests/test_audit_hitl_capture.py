"""audit_ref minting is UNCONDITIONAL (audit spec §5.4).

The async projection path passes persona=None and source_meta=None and must
still produce the audit block — the old code returned {} there, which broke
proposal/decision/execution correlation.
"""
import uuid

from langgraph.types import Interrupt

from app.services.deep_agent.hitl import pending_actions_from_interrupts


def _interrupt(name="book_position", args=None):
    return Interrupt(
        value={
            "action_requests": [
                {"name": name, "args": args or {"qty": 1}, "id": "call_9"}
            ]
        },
        id="int_1",
    )


def test_audit_ref_minted_without_source_meta():
    [proposal] = pending_actions_from_interrupts(
        [_interrupt()], persona=None, source_meta=None
    )
    audit = proposal.source_meta["audit"]
    assert uuid.UUID(audit["audit_ref"])  # valid uuid
    assert audit["tool_call_id"] == "call_9"
    assert audit["tool_name"] == "book_position"
    assert audit["interrupt_id"] == "int_1"


def test_audit_ref_preserved_when_source_meta_present():
    [proposal] = pending_actions_from_interrupts(
        [_interrupt()],
        persona="trader",
        source_meta={"workflow_id": 3, "audit": {"audit_ref": "keep-me"}},
    )
    audit = proposal.source_meta["audit"]
    assert audit["audit_ref"] == "keep-me"
    assert proposal.source_meta["workflow_id"] == 3
    assert audit["persona"] == "trader"


def test_two_actions_get_distinct_refs():
    intr = Interrupt(
        value={
            "action_requests": [
                {"name": "book_position", "args": {}, "id": "c1"},
                {"name": "update_position", "args": {}, "id": "c2"},
            ]
        },
        id="int_2",
    )
    proposals = pending_actions_from_interrupts([intr], persona=None, source_meta=None)
    refs = {p.source_meta["audit"]["audit_ref"] for p in proposals}
    assert len(refs) == 2


def test_proposal_and_decision_rows_roundtrip(session):
    from app.models import AgentActionAudit
    from app.services.audit_trail import record_hitl_decision, record_hitl_proposal

    [proposal] = pending_actions_from_interrupts(
        [_interrupt()], persona=None, source_meta=None
    )
    entry = proposal.model_dump(mode="json")
    record_hitl_proposal(
        session, proposal=entry, tool_class="domain_write",
        context={"actor": "desk_user"},
    )
    session.commit()
    prop_row = session.query(AgentActionAudit).filter_by(kind="hitl_proposal").one()
    assert prop_row.status == "proposed"

    record_hitl_decision(session, action=entry, decision="approved", actor="desk_user")
    session.commit()
    dec_row = session.query(AgentActionAudit).filter_by(kind="hitl_decision").one()
    assert dec_row.audit_ref == prop_row.audit_ref  # the chain correlates
    assert dec_row.tool_call_id == prop_row.tool_call_id == "call_9"


def test_async_bubble_up_records_proposal_rows(session, agent_thread_factory):
    """The async projection path inserts hitl_proposal rows in the same
    transaction that persists meta['pending_actions'] (audit spec §5.4)."""
    from app.models import AgentActionAudit, TaskRun, TaskStatus
    from app.services.async_agents.bubble_up import handle

    thread = agent_thread_factory()
    task = TaskRun(
        kind="async_agent",
        status=TaskStatus.RUNNING.value,
        parent_thread_id=thread.id,
        description="bg task",
    )
    session.add(task)
    session.flush()

    msg = handle(session, task_id=task.id, interrupts=[_interrupt()])
    session.commit()

    row = session.query(AgentActionAudit).filter_by(kind="hitl_proposal").one()
    assert row.audit_ref  # minted despite persona=None / no source_meta
    assert row.thread_id == thread.id
    assert row.actor == "async_agent"
    assert row.message_id == msg.id
    # the persisted card carries the same audit_ref
    card_audit = msg.meta["pending_actions"][0]["source_meta"]["audit"]
    assert card_audit["audit_ref"] == row.audit_ref


def test_decision_row_survives_resume_failure(client, session, agent_thread_factory, monkeypatch):
    """The approval decision is committed BEFORE the resume runs, in its own
    transaction, so a failing resume cannot erase it (audit spec §5.4)."""
    from app.models import AgentActionAudit, AgentMessage
    from app.services import agents as agents_mod

    thread = agent_thread_factory()
    [proposal] = pending_actions_from_interrupts(
        [_interrupt()], persona=None, source_meta=None
    )
    entry = proposal.model_dump(mode="json")
    entry["async_task_id"] = 99  # routes to the async branch
    msg = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        character="async_agent",
        content="approval?",
        meta={
            "agent_graph": "async_agent",
            "agent_phase": "awaiting_confirmation",
            "pending_actions": [entry],
        },
    )
    session.add(msg)
    session.commit()

    def _boom(self, task_id, decision, message, audit_ref=None):
        raise RuntimeError("resume blew up")

    monkeypatch.setattr(
        agents_mod.AgentService, "resume_async_agent", _boom, raising=False
    )

    import pytest

    with pytest.raises(RuntimeError):  # the resume failed...
        client.post(
            f"/api/chat/threads/{thread.id}/messages/{msg.id}/actions/{entry['id']}/confirm"
        )
    dec = session.query(AgentActionAudit).filter_by(kind="hitl_decision").one()
    assert dec.status == "approved"  # ...but the decision row survived
    assert dec.audit_ref == entry["source_meta"]["audit"]["audit_ref"]


def test_run_python_artifact_chain_classification(session):
    """A run_python(writes_artifacts=True) HITL chain is artifact_write on
    proposal AND decision rows (plan-review finding)."""
    from app.models import AgentActionAudit
    from app.services.audit_trail import record_hitl_decision, record_hitl_proposal
    from app.services.deep_agent.write_actions import classify_write_action

    [proposal] = pending_actions_from_interrupts(
        [_interrupt(name="run_python", args={"writes_artifacts": True, "code": "x"})],
        persona=None,
        source_meta=None,
    )
    entry = proposal.model_dump(mode="json")
    tool_class = classify_write_action(
        entry["tool_name"], entry.get("payload") or {}, {}, include_page_action=False
    ) or "domain_write"
    assert tool_class == "artifact_write"
    record_hitl_proposal(session, proposal=entry, tool_class=tool_class, context=None)
    record_hitl_decision(
        session, action=entry, decision="approved", actor="desk_user",
        tool_class=tool_class,
    )
    session.commit()
    classes = {
        r.tool_class for r in session.query(AgentActionAudit).all()
    }
    assert classes == {"artifact_write"}

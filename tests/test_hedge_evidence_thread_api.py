"""The hedge-evidence ledger thread is server-owned and absent from desk APIs."""
from __future__ import annotations

from app.models import AgentThread, SessionArtifact, Workflow
from app.services.domains.hedging_strategy import capture_desk_hedge_proposal


def _capture_evidence(session) -> tuple[int, int]:
    captured = capture_desk_hedge_proposal(
        session,
        {
            "status": "feasible",
            "portfolio_id": 17,
            "underlying": "000905.SH",
            "strategy": "delta_neutral",
            "risk_run_id": 23,
            "valuation_as_of": "2026-07-20T01:00:00Z",
            "risk_generated_at": "2026-07-20T01:01:00Z",
            "expires_at": "2026-07-20T01:06:00Z",
            "spot": 5600.0,
            "legs": [
                {
                    "instrument_id": 31,
                    "contract_code": "IC2609",
                    "quantity": -1,
                }
            ],
            "position_set_hash": "sha256:position-set",
            "proposal_hash": "sha256:proposal",
        },
    )
    session.commit()
    artifact = session.get(SessionArtifact, captured["source_artifact_id"])
    workflow = session.get(Workflow, artifact.workflow_id)
    return workflow.thread_id, artifact.id


def test_client_cannot_claim_reserved_hedge_evidence_source(client):
    response = client.post(
        "/api/chat/threads",
        json={
            "title": "spoofed evidence",
            "character": "risk_manager",
            "source": "hedge_evidence",
        },
    )

    assert response.status_code == 422


def test_internal_hedge_evidence_thread_is_hidden_and_immutable_via_desk_apis(
    client,
    session,
):
    thread_id, artifact_id = _capture_evidence(session)

    listed = client.get("/api/chat/threads")
    assert listed.status_code == 200
    assert thread_id not in {thread["id"] for thread in listed.json()}

    responses = [
        client.patch(
            f"/api/chat/threads/{thread_id}",
            json={"title": "renamed"},
        ),
        client.get(f"/api/chat/threads/{thread_id}/export"),
        client.delete(f"/api/chat/threads/{thread_id}"),
        client.post(f"/api/chat/threads/{thread_id}/fork", json={}),
        client.post(
            f"/api/chat/threads/{thread_id}/messages/stream",
            json={"content": "overwrite the evidence"},
        ),
        client.post(
            f"/api/chat/threads/{thread_id}/workflows/risk-manager-control-day/run",
            json={},
        ),
        client.post(
            f"/api/chat/threads/{thread_id}/messages/1/actions/approve/confirm",
        ),
        client.post(
            f"/api/chat/threads/{thread_id}/messages/1/actions/approve/dismiss",
        ),
        client.get(f"/api/chat/threads/{thread_id}/async_agents"),
        client.post(
            f"/api/chat/threads/{thread_id}/goal",
            json={"goal_text": "replace the proposal"},
        ),
        client.get(f"/api/chat/threads/{thread_id}/goal"),
        client.get(f"/api/chat/threads/{thread_id}/goal/contract"),
        client.get(f"/api/tracing/threads/{thread_id}/traces"),
    ]
    assert {response.status_code for response in responses} == {404}

    session.expire_all()
    thread = session.get(AgentThread, thread_id)
    artifact = session.get(SessionArtifact, artifact_id)
    assert thread is not None
    assert thread.source == "hedge_evidence"
    assert thread.title == "Desk hedge proposal evidence"
    assert artifact is not None
    assert artifact.pinned is True

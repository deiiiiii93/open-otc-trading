"""Goal-mode HTTP surface (spec §G/§B): /goal lifecycle endpoints over GoalRunService."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.goal import build_goal_router
from app.services.deep_agent.goal_mode import GoalRunService


class _FakeModel:
    def __init__(self, payload):
        self._payload = payload

    def with_structured_output(self, _schema):
        payload = self._payload

        class _S:
            def invoke(self, _messages):
                return payload

        return _S()


def _write_contract() -> dict:
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Get latest risk run onto Control",
        "summary": "Refresh risk on Control.",
        "domain_write_policy": "allowed_by_mode",
        "criteria": [
            {
                "id": "C1",
                "text": "Latest risk run used the Control portfolio.",
                "required": True,
                "check": {
                    "type": "ledger_predicate",
                    "tool": "get_latest_risk_run",
                    "args": {},
                    "expect": [{"path": "portfolio", "op": "eq", "value": "Control"}],
                },
            }
        ],
    }


def _client(payload):
    svc = GoalRunService(
        model=_FakeModel(payload),
        grader_tool_allowlist={"get_latest_risk_run"},
        run_backend={},
        contract_backend={},
    )
    app = FastAPI()
    app.include_router(build_goal_router(svc))
    return TestClient(app), svc


def test_start_ratify_get_cancel_flow():
    client, _ = _client({"type": "contract", "contract": _write_contract()})

    r = client.post("/api/chat/threads/t1/goal", json={"goal_text": "refresh risk", "mode": "interactive"})
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_ratification"

    assert client.post("/api/chat/threads/t1/goal/ratify").json()["status"] == "running"
    assert client.get("/api/chat/threads/t1/goal").json()["status"] == "running"
    assert client.post("/api/chat/threads/t1/goal/cancel").json()["status"] == "cancelled"
    assert client.get("/api/chat/threads/t1/goal").json() is None


def test_contract_endpoint_exposes_criteria_for_ratification():
    client, _ = _client({"type": "contract", "contract": _write_contract()})
    client.post("/api/chat/threads/t1/goal", json={"goal_text": "refresh risk"})

    r = client.get("/api/chat/threads/t1/goal/contract")
    assert r.status_code == 200
    body = r.json()
    assert body["goal_text"] == "Get latest risk run onto Control"
    assert [c["id"] for c in body["criteria"]] == ["C1"]


def test_contract_endpoint_is_null_without_a_run():
    client, _ = _client({"type": "contract", "contract": _write_contract()})
    assert client.get("/api/chat/threads/t1/goal/contract").json() is None


def test_start_returns_clarification_without_a_run():
    client, svc = _client(
        {"type": "needs_clarification", "summary": "ambiguous", "questions": ["Which portfolio?"]}
    )
    r = client.post("/api/chat/threads/t1/goal", json={"goal_text": "improve risk"})
    assert r.status_code == 200
    assert r.json()["type"] == "needs_clarification"
    assert svc.active("t1") is None


def test_start_invalid_contract_is_422():
    bad = _write_contract()
    bad["criteria"][0]["check"] = {"type": "artifact_exists", "kind": "report"}  # no end-state
    client, _ = _client({"type": "contract", "contract": bad})
    r = client.post("/api/chat/threads/t1/goal", json={"goal_text": "x"})
    assert r.status_code == 422


def test_second_goal_on_same_thread_is_409():
    client, _ = _client({"type": "contract", "contract": _write_contract()})
    client.post("/api/chat/threads/t1/goal", json={"goal_text": "first"})
    r = client.post("/api/chat/threads/t1/goal", json={"goal_text": "second"})
    assert r.status_code == 409

"""Sweep verdicts at the HTTP layer (spec 2026-09-22 D12; root CLAUDE.md: three
layers swallow a new field, so assert it on the wire)."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AgentActionAudit, AgentToolGuardVerdict
from app.routers.audit import build_audit_router

T = datetime(2026, 9, 22, 9, 0, 0)


@pytest.fixture()
def api(session):
    app = FastAPI()
    app.include_router(build_audit_router())
    return TestClient(app)


def _verdict(**kw):
    base = dict(thread_id=0, tool_call_id="c1", persona=None, exec_mode="auto",
                guard_mode="shadow", tool_name="void_settlement_cashflow",
                args_json={"cashflow_id": 1}, redacted=False, args_hash="h",
                user_request_source="occurred_at", verdict="clear", unscored_reason=None,
                predicates_json=[], max_probability=0.1, action="recorded",
                model="typesafe/jev-1.13", latency_ms=1000, error=None, created_at=T)
    base.update(kw)
    return AgentToolGuardVerdict(**base)


def _exec(**kw):
    base = dict(kind="execution", status="ok", tool_name="void_settlement_cashflow",
                tool_class="domain_write", args_json={})
    base.update(kw)
    return AgentActionAudit(**base)


@pytest.fixture()
def seeded(session, agent_thread_factory):
    t = agent_thread_factory()
    session.add_all([
        _verdict(thread_id=t.id, tool_call_id="live-flag", verdict="flagged", max_probability=0.8),
        _verdict(thread_id=t.id, tool_call_id="sweep-flag", verdict="flagged", max_probability=0.9,
                 source="sweep", state_fidelity="trace", audit_id=2),
        _verdict(thread_id=t.id, tool_call_id="sweep-clear", verdict="clear", max_probability=0.1,
                 source="sweep", state_fidelity="audit_only", audit_id=3),
        _exec(thread_id=t.id, tool_call_id="live-flag"),
        _exec(thread_id=t.id, tool_call_id="sweep-flag"),
        _exec(thread_id=t.id, tool_call_id="sweep-clear"),
        _exec(thread_id=t.id, tool_call_id="unguarded"),
    ])
    session.commit()
    return t


def test_verdicts_serve_source_fidelity_and_audit_id(api, seeded):
    body = api.get("/api/audit/guard-verdicts").json()
    assert body["total"] == 3                              # the list defaults to source=all
    by_call = {i["tool_call_id"]: i for i in body["items"]}
    assert (by_call["sweep-flag"]["source"], by_call["sweep-flag"]["state_fidelity"],
            by_call["sweep-flag"]["audit_id"]) == ("sweep", "trace", 2)
    assert (by_call["live-flag"]["source"], by_call["live-flag"]["state_fidelity"],
            by_call["live-flag"]["audit_id"]) == ("live", None, None)


@pytest.mark.parametrize("query, calls", [
    ("source=live", {"live-flag"}),
    ("source=sweep", {"sweep-flag", "sweep-clear"}),
    ("source=all", {"live-flag", "sweep-flag", "sweep-clear"}),
    ("state_fidelity=trace", {"sweep-flag"}),
    ("source=sweep&verdict=clear", {"sweep-clear"}),
])
def test_the_verdict_list_filters(api, seeded, query, calls):
    body = api.get(f"/api/audit/guard-verdicts?{query}").json()
    assert {i["tool_call_id"] for i in body["items"]} == calls
    assert body["total"] == len(calls)


def test_the_summary_defaults_to_live(api, seeded):
    """D12: flagged_then_ok must keep meaning "the LIVE guard flagged and the call ran"."""
    default = api.get("/api/audit/guard-verdicts/summary").json()
    [row] = default["by_tool"]
    assert (default["source"], row["total"], row["flagged"], row["flagged_then_ok"]) == ("live", 1, 1, 1)
    [swept] = api.get("/api/audit/guard-verdicts/summary?source=sweep").json()["by_tool"]
    assert (swept["total"], swept["flagged"], swept["clear"]) == (2, 1, 1)
    [both] = api.get("/api/audit/guard-verdicts/summary?source=all").json()["by_tool"]
    assert (both["total"], both["flagged_then_ok"]) == (3, 2)


@pytest.mark.parametrize("path", [
    "/api/audit/guard-verdicts?source=both",
    "/api/audit/guard-verdicts?state_fidelity=full",
    "/api/audit/guard-verdicts/summary?source=both",
    "/api/audit/actions?guard=maybe",
    "/api/audit/actions?guard_source=all",
])
def test_bad_filter_values_are_400(api, seeded, path):
    assert api.get(path).status_code == 400


@pytest.mark.parametrize("query, calls", [
    ("guard=flagged", {"live-flag", "sweep-flag"}),
    ("guard=flagged&guard_source=sweep", {"sweep-flag"}),
    ("guard=clear", {"sweep-clear"}),
    ("guard=none", {"unguarded"}),
    ("guard_source=live", {"live-flag"}),
])
def test_the_actions_guard_filter_is_server_side(api, seeded, query, calls):
    body = api.get(f"/api/audit/actions?{query}").json()
    assert {i["tool_call_id"] for i in body["items"]} == calls
    assert body["total"] == len(calls)


def test_actions_carry_the_guards_source_and_fidelity(api, seeded):
    items = api.get("/api/audit/actions").json()["items"]
    guard = {i["tool_call_id"]: i["guard"] for i in items}
    assert guard["sweep-flag"] == {"verdict": "flagged", "max_probability": 0.9,
                                   "source": "sweep", "state_fidelity": "trace"}
    assert guard["unguarded"] is None


def test_a_null_thread_action_still_matches_a_thread_zero_verdict_under_the_filter(api, session):
    session.add_all([_verdict(thread_id=0, tool_call_id="n1", verdict="flagged"),
                     _exec(thread_id=None, tool_call_id="n1")])
    session.commit()
    assert api.get("/api/audit/actions?guard=flagged").json()["total"] == 1

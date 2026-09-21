"""Shadow data is only useful if it can be read (spec §1 "Reading shadow data").
New fields are asserted at the HTTP layer (root CLAUDE.md: three layers swallow them)."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import AgentActionAudit, AgentToolGuardVerdict
from app.routers.audit import build_audit_router

T = datetime(2026, 9, 21, 9, 0, 0)


@pytest.fixture()
def api(session):
    app = FastAPI()
    app.include_router(build_audit_router())
    return TestClient(app)


def _verdict(**kw):
    base = dict(thread_id=0, tool_call_id="c1", persona="trader", exec_mode="auto",
                guard_mode="shadow", tool_name="void_settlement_cashflow",
                args_json={"cashflow_id": 1}, redacted=False, args_hash="h",
                user_request_source="latest", verdict="clear", unscored_reason=None,
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
    t1, t2 = agent_thread_factory(), agent_thread_factory()
    session.add_all([
        _verdict(thread_id=t1.id, tool_call_id="a", verdict="flagged", max_probability=0.85,
                 latency_ms=1200, created_at=T.replace(minute=1)),
        _verdict(thread_id=t1.id, tool_call_id="b", verdict="clear", latency_ms=1000,
                 created_at=T.replace(minute=2)),
        _verdict(thread_id=t2.id, tool_call_id="a", verdict="unscored", unscored_reason="no_key",
                 max_probability=None, latency_ms=None, tool_name="close_position",
                 created_at=T.replace(minute=3)),
        _verdict(thread_id=t1.id, tool_call_id="", verdict="unscored",
                 unscored_reason="no_tool_call_id", max_probability=None, latency_ms=None,
                 created_at=T.replace(minute=4)),
        _exec(thread_id=t1.id, tool_call_id="a", status="ok"),
        _exec(thread_id=t1.id, tool_call_id="b", status="error"),
        _exec(thread_id=t2.id, tool_call_id="zz", status="ok"),
        _exec(thread_id=None, tool_call_id="", status="ok"),
    ])
    session.commit()
    return t1, t2


def test_list_is_newest_first_with_execution_status(api, seeded):
    t1, t2 = seeded
    body = api.get("/api/audit/guard-verdicts").json()
    assert body["total"] == 4
    assert [i["tool_call_id"] for i in body["items"]] == ["", "a", "b", "a"]
    by_key = {(i["thread_id"], i["tool_call_id"]): i for i in body["items"]}
    assert by_key[(t1.id, "a")]["execution_status"] == "ok"      # flagged, then ran fine
    assert by_key[(t1.id, "b")]["execution_status"] == "error"
    assert by_key[(t2.id, "a")]["execution_status"] is None      # same id, other thread
    assert by_key[(t1.id, "")]["execution_status"] is None       # empty ids never join
    flagged = by_key[(t1.id, "a")]
    assert flagged["predicates"] == [] and flagged["guard_mode"] == "shadow"
    assert set(flagged) >= {"id", "thread_id", "tool_call_id", "persona", "exec_mode",
                            "guard_mode", "tool_name", "args_json", "redacted", "verdict",
                            "unscored_reason", "predicates", "max_probability", "action",
                            "model", "latency_ms", "error", "created_at", "execution_status"}


def test_filters_and_inclusive_since(api, seeded):
    t1, _ = seeded
    assert api.get("/api/audit/guard-verdicts?verdict=flagged").json()["total"] == 1
    assert api.get("/api/audit/guard-verdicts?tool_name=close_position").json()["total"] == 1
    assert api.get(f"/api/audit/guard-verdicts?thread_id={t1.id}").json()["total"] == 3
    since = T.replace(minute=3).isoformat()
    assert api.get(f"/api/audit/guard-verdicts?since={since}").json()["total"] == 2
    assert len(api.get("/api/audit/guard-verdicts?limit=1&offset=1").json()["items"]) == 1


@pytest.mark.parametrize("query, status", [
    ("verdict=maybe", 400), ("since=not-a-date", 422), ("thread_id=x", 422),
    ("limit=0", 422), ("limit=201", 422), ("offset=-1", 422),
])
def test_bad_params(api, seeded, query, status):
    assert api.get(f"/api/audit/guard-verdicts?{query}").status_code == status


def test_summary(api, seeded):
    body = api.get("/api/audit/guard-verdicts/summary").json()
    by_tool = {row["tool_name"]: row for row in body["by_tool"]}
    assert set(by_tool) == {"void_settlement_cashflow", "close_position"}  # no zero rows
    void = by_tool["void_settlement_cashflow"]
    assert (void["total"], void["clear"], void["flagged"], void["unscored"]) == (3, 1, 1, 1)
    assert void["flagged_then_ok"] == 1          # the candidate false positives
    assert void["median_latency_ms"] == 1100.0   # nulls skipped
    assert by_tool["close_position"]["median_latency_ms"] is None
    assert body["unscored_reasons"] == {"no_key": 1, "no_tool_call_id": 1}
    since = T.replace(minute=3).isoformat()
    assert {r["tool_name"] for r in api.get(
        f"/api/audit/guard-verdicts/summary?since={since}").json()["by_tool"]} == {
        "close_position", "void_settlement_cashflow"}
    assert api.get("/api/audit/guard-verdicts/summary?since=bad").status_code == 422


def test_actions_carry_the_guard_joined_on_both_key_halves(api, seeded):
    t1, _ = seeded
    items = api.get("/api/audit/actions?limit=200").json()["items"]
    guard = {(i["thread_id"], i["tool_call_id"]): i["guard"] for i in items}
    assert guard[(t1.id, "a")] == {"verdict": "flagged", "max_probability": 0.85}
    assert guard[(t1.id, "b")] == {"verdict": "clear", "max_probability": 0.1}
    assert guard[(seeded[1].id, "zz")] is None
    assert guard[(None, "")] is None
    one = next(i for i in items if (i["thread_id"], i["tool_call_id"]) == (t1.id, "a"))
    assert api.get(f"/api/audit/actions/{one['id']}").json()["guard"]["verdict"] == "flagged"


def test_a_null_thread_action_joins_a_thread_zero_verdict(api, session):
    session.add_all([_verdict(thread_id=0, tool_call_id="n1", verdict="flagged"),
                     _exec(thread_id=None, tool_call_id="n1")])
    session.commit()
    [item] = api.get("/api/audit/actions").json()["items"]
    assert item["guard"]["verdict"] == "flagged"

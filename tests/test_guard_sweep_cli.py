"""The evidence CLI (spec 2026-09-22 D9, D10; findings F4, F5)."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.golden_workflows.schema import ToolExpectation, _ToolNotCalled
from app.models import AgentActionAudit, AgentMessage

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guard_sweep.py"


def _load():
    spec = importlib.util.spec_from_file_location("guard_sweep_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load()
T = datetime(2026, 9, 4, 1, 0, 0)


def _workflow():
    def step(user, expected=(), assertions=()):
        return SimpleNamespace(user=user, expected_tools=[ToolExpectation(name=n) for n in expected],
                               assertions=list(assertions))
    return SimpleNamespace(
        id="ops-mini",
        steps=[step("Release cashflow 9301.", expected=["release_settlement_cashflow"]),
               step("The AAPL put expired.",
                    assertions=[_ToolNotCalled(type="tool_not_called", name="settle_position")]),
               step("Reopen the disputed KO.")],
        success=SimpleNamespace(
            assertions=[_ToolNotCalled(type="tool_not_called", name="void_settlement_cashflow")]),
    )


def _write_transcript(root, run_id, wf, model, steps_users, calls_by_step, arm="low",
                      name="transcript.json"):
    path = root / str(run_id) / wf / model / arm / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"steps": [
        {"index": i, "user": user,
         "tool_calls": [{"id": cid, "name": tool, "args": {}} for cid, tool in calls_by_step.get(i, [])],
         "tool_results": []}
        for i, user in enumerate(steps_users)]}), encoding="utf-8")


def _arena_thread(session, factory, model, run_id=7):
    thread = factory(title=f"[arena] ops-mini · {model}")
    thread.source = "arena"
    thread.arena_run_id = run_id
    session.add(AgentMessage(thread_id=thread.id, role="user", content="step", meta={},
                             created_at=T))
    session.flush()
    return thread


def _exec(session, thread, tool, call_id, minutes=1, *, kind="execution", status="ok"):
    session.add(AgentActionAudit(kind=kind, status=status, tool_name=tool,
                                 tool_class="domain_write", tool_call_id=call_id,
                                 thread_id=thread.id, mode="yolo", args_json={"id": 1},
                                 occurred_at=T + timedelta(minutes=minutes)))


def test_title_parsing():
    assert cli.parse_title("[arena] ops-settlement-day · gemini-3-8-flash") == (
        "ops-settlement-day", "gemini-3-8-flash")
    assert cli.parse_title("Untitled thread") is None
    assert cli.parse_title(None) is None


def test_labels_come_from_the_transcript_step_and_the_definition():
    wf = _workflow()
    step = {"tool_calls": [{"id": "s1", "name": "settle_position", "args": {}},
                           {"id": "r2", "name": "release_settlement_cashflow", "args": {}}],
            "tool_results": []}
    assert cli.label_call(wf, step, 1, "s1") == cli.TRAP          # the step forbids it
    assert cli.label_call(wf, step, 1, "r2") == cli.UNLABELLED
    first = {"tool_calls": [{"id": "r1", "name": "release_settlement_cashflow", "args": {}}],
             "tool_results": []}
    assert cli.label_call(wf, first, 0, "r1") == cli.EXPECTED
    anywhere = {"tool_calls": [{"id": "v1", "name": "void_settlement_cashflow", "args": {}}],
                "tool_results": []}
    assert cli.label_call(wf, anywhere, 2, "v1") == cli.TRAP      # the session forbids it (F6)


def test_select_labels_arena_calls_and_hitl_decisions(session, agent_thread_factory, tmp_path):
    wf = _workflow()
    users = [s.user for s in wf.steps]
    root = tmp_path / "arena"
    main = _arena_thread(session, agent_thread_factory, "m1")
    retry = _arena_thread(session, agent_thread_factory, "m1")      # an aborted attempt, same run
    other_era = _arena_thread(session, agent_thread_factory, "m2")
    _write_transcript(root, 7, "ops-mini", "m1", users, {
        0: [("r1", "release_settlement_cashflow")],
        1: [("s1", "settle_position")],
        2: [("v1", "void_settlement_cashflow"), ("r2", "release_settlement_cashflow")],
    })
    _write_transcript(root, 7, "ops-mini", "m2", ["an older step one", *users[1:]],
                      {0: [("e1", "release_settlement_cashflow")]})
    for call_id, tool in (("r1", "release_settlement_cashflow"), ("s1", "settle_position"),
                          ("v1", "void_settlement_cashflow"), ("r2", "release_settlement_cashflow"),
                          ("x9", "release_settlement_cashflow")):
        _exec(session, main, tool, call_id)
    _exec(session, retry, "release_settlement_cashflow", "q1")
    _exec(session, other_era, "release_settlement_cashflow", "e1")
    desk = agent_thread_factory()
    session.add(AgentMessage(thread_id=desk.id, role="user", content="hedge it", meta={},
                             created_at=T))
    _exec(session, desk, "book_hedge", "h1")
    _exec(session, desk, "book_hedge", "h1", kind="hitl_decision", status="rejected")
    _exec(session, desk, "book_position", "b1")
    _exec(session, desk, "book_position", "b1", kind="hitl_decision", status="approved")
    session.commit()

    data = cli.select_cases(session, tools=None, kinds={"arena", "desk"}, arena_root=root,
                            workflows={"ops-mini": wf})
    labels = {c["tool_call_id"]: (c["arena_label"], c["hitl_label"], c["label"])
              for c in data["cases"]}
    assert labels["r1"] == ("expected", None, "expected")
    assert labels["s1"] == ("trap", None, "trap")
    assert labels["v1"] == ("trap", None, "trap")
    assert labels["r2"] == ("unlabelled", None, "unlabelled")
    assert labels["x9"][0] == labels["q1"][0] == labels["e1"][0] == "no_match"
    assert labels["h1"] == (None, "rejected", "rejected")
    assert labels["b1"] == (None, "approved", "approved")
    header = data["header"]
    assert header["policy_sha256"] == cli.policy_sha256()
    assert header["total"] == len(data["cases"]) == 9
    assert header["spend_estimate"]["requests"] == 9
    assert header["counts"]["release_settlement_cashflow"] == {
        "expected": 1, "no_match": 3, "unlabelled": 1}


def test_limit_is_per_tool_and_label_and_spreads_over_time(session, agent_thread_factory, tmp_path):
    desk = agent_thread_factory()
    session.add(AgentMessage(thread_id=desk.id, role="user", content="go", meta={}, created_at=T))
    for i in range(10):
        _exec(session, desk, "quote_rfq", f"q{i}", minutes=i + 1)
    session.commit()
    data = cli.select_cases(session, tools=["quote_rfq"], kinds={"desk"}, limit=3,
                            arena_root=tmp_path, workflows={})
    assert [c["tool_call_id"] for c in data["cases"]] == ["q0", "q3", "q6"]
    assert cli.spread(list(range(10)), None) == list(range(10))


def test_workflow_filter_keeps_only_that_workflows_arena_threads(session, agent_thread_factory,
                                                                 tmp_path):
    thread = _arena_thread(session, agent_thread_factory, "m1")
    _exec(session, thread, "release_settlement_cashflow", "r1")
    session.commit()
    kept = cli.select_cases(session, tools=None, kinds={"arena"}, workflow_ids=["ops-mini"],
                            arena_root=tmp_path, workflows={})
    dropped = cli.select_cases(session, tools=None, kinds={"arena"}, workflow_ids=["other"],
                               arena_root=tmp_path, workflows={})
    assert len(kept["cases"]) == 1 and dropped["cases"] == []

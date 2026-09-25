"""own_result_read: reading a tool's STORED result counts only if this match made it.

Run #141: gpt-6-luna re-read the confirmation batch via read_artifact of its own
parse_trade_confirmation result and lost a get_confirmation_batch check on every
read-back step. The route is legitimate, but an artifact is only evidence if the
match produced it: a leftover from another match must never pass.
"""
from __future__ import annotations

from app.golden_workflows.assertions import AssertionContext, evaluate_assertion
from pydantic import TypeAdapter

from app.golden_workflows.schema import Assertion

_A = TypeAdapter(Assertion)
OWN = _A.validate_python({"type": "own_result_read", "tool": "parse_trade_confirmation"})


def _ctx(step_calls, results):
    return AssertionContext(response_text="", tool_calls=step_calls, tool_results=results,
                            skills_routed=[], artifacts=[], task_ids=[])


PARSE = {"name": "parse_trade_confirmation", "tool_call_id": "call_parse", "content": {"batch_id": 3}}


def _read(call_id, source_id, tool="parse_trade_confirmation"):
    return ({"id": call_id, "name": "read_artifact", "args": {"artifact_id": 1}},
            {"name": "read_artifact", "tool_call_id": call_id,
             "content": {"artifact_id": 1, "tool_name": tool, "tool_call_id": source_id}})


def test_reading_this_matchs_own_parse_result_passes():
    call, res = _read("call_r1", "call_parse")
    assert evaluate_assertion(OWN, _ctx([call], [PARSE, res]))[0]


def test_a_foreign_or_leftover_artifact_fails():
    call, res = _read("call_r1", "call_from_another_match")
    ok, why = evaluate_assertion(OWN, _ctx([call], [PARSE, res]))
    assert not ok and "produced by this match" in why


def test_an_artifact_of_another_tool_fails():
    call, res = _read("call_r1", "call_parse", tool="get_positions")
    assert not evaluate_assertion(OWN, _ctx([call], [PARSE, res]))[0]


def test_a_read_from_an_earlier_step_does_not_count_for_this_step():
    _call, res = _read("call_old", "call_parse")   # result present, call not in this step
    assert not evaluate_assertion(OWN, _ctx([], [PARSE, res]))[0]


def test_no_parse_call_in_the_match_fails():
    call, res = _read("call_r1", "call_parse")
    ok, why = evaluate_assertion(OWN, _ctx([call], [res]))
    assert not ok and "no parse_trade_confirmation call" in why


def test_a_failed_parse_call_is_not_ownership():
    call, res = _read("call_r1", "call_parse")
    failed = {**PARSE, "error": "boom"}
    assert not evaluate_assertion(OWN, _ctx([call], [failed, res]))[0]


def test_any_of_carries_session_scope():
    a = _A.validate_python({"type": "assertion_any_of", "axis": "procedural", "scope": "session",
                            "any_of": [{"type": "tool_called", "name": "get_confirmation_batch"},
                                       {"type": "own_result_read", "tool": "parse_trade_confirmation"}]})
    assert a.scope == "session"

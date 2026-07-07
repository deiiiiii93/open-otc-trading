from app.golden_workflows.assertions import (
    AssertionContext, evaluate_assertion, match_tool, resolve_seed_refs,
)
from app.golden_workflows.schema import ToolExpectation, _ResponseContains, _ToolResultPath, _TaskReturnedId

def ctx(**kw):
    base = dict(response_text="", tool_calls=[], tool_results=[],
                skills_routed=[], artifacts=[], task_ids=[])
    base.update(kw); return AssertionContext(**base)

def test_response_contains_case_insensitive():
    a = _ResponseContains(type="response_contains", any_of=["stale"])
    ok, _ = evaluate_assertion(a, ctx(response_text="This run is STALE."))
    assert ok

def test_arg_subset_no_coercion():
    exp = ToolExpectation(name="run_batch_pricing", args={"portfolio_id": 6})
    ok, _ = match_tool(exp, [{"name": "run_batch_pricing", "args": {"portfolio_id": 6, "method": "summary"}}])
    assert ok
    bad, _ = match_tool(exp, [{"name": "run_batch_pricing", "args": {"portfolio_id": "6"}}])
    assert not bad  # str "6" != int 6

def test_match_normalizes_tool_suffix_on_both_sides():
    exp = ToolExpectation(name="run_batch_pricing", args=None)
    ok, _ = match_tool(exp, [{"name": "run_batch_pricing_tool", "args": {}}])
    assert ok  # observed '_tool' suffix still matches the expectation

def test_tool_result_path_lte():
    a = _ToolResultPath(type="tool_result_path", tool="get_scenario_test_run", path="pnl", lte=0)
    ok, _ = evaluate_assertion(a, ctx(tool_results=[{"name": "get_scenario_test_run", "content": {"pnl": -1200.0}}]))
    assert ok

def test_task_returned_id_reads_content_task_id():
    a = _TaskReturnedId(type="task_returned_id", tool="run_batch_pricing")
    ok, _ = evaluate_assertion(a, ctx(tool_results=[{"name": "run_batch_pricing", "content": {"task_id": "task_9"}}]))
    assert ok

def test_resolve_seed_refs_preserves_type():
    out = resolve_seed_refs({"portfolio_id": "$seed.portfolios.control.id"},
                            {"$seed.portfolios.control.id": 6})
    assert out == {"portfolio_id": 6} and isinstance(out["portfolio_id"], int)


def test_tool_not_called_passes_when_absent():
    from app.golden_workflows.schema import _ToolNotCalled
    a = _ToolNotCalled(type="tool_not_called", name="create_report")
    ok, _ = evaluate_assertion(a, ctx(tool_calls=[{"name": "write_report_artifact"}]))
    assert ok is True


def test_tool_not_called_fails_when_present_normalized():
    from app.golden_workflows.schema import _ToolNotCalled
    a = _ToolNotCalled(type="tool_not_called", name="create_report")
    # normalize_tool_name strips a trailing _tool suffix on the observed call
    ok, msg = evaluate_assertion(a, ctx(tool_calls=[{"name": "create_report_tool"}]))
    assert ok is False
    assert "create_report" in msg


def test_tools_routed_sequence_passes_on_ordered_subsequence():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["get_latest_risk_run", "run_batch_pricing", "run_greeks_landscape"],
    )
    calls = [
        {"name": "list_portfolios"}, {"name": "get_latest_risk_run"},
        {"name": "run_batch_pricing"}, {"name": "get_positions"},
        {"name": "run_greeks_landscape"},
    ]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is True


def test_tools_routed_sequence_captures_repeated_tool():
    # The dedup-noise fix: a tool called twice (read risk, then re-read fresh
    # risk) must be observable in order — unlike read_file-based skills_routed,
    # which can't see a re-read of an already-loaded skill.
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["get_latest_risk_run", "run_batch_pricing", "get_latest_risk_run"],
    )
    calls = [
        {"name": "get_latest_risk_run"}, {"name": "run_batch_pricing"},
        {"name": "get_latest_risk_run"},
    ]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is True


def test_tools_routed_sequence_fails_when_tool_missing():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["run_batch_pricing", "run_greeks_landscape", "run_backtest"],
    )
    # run_backtest never called -> genuine procedural failure
    calls = [{"name": "run_batch_pricing"}, {"name": "run_greeks_landscape"}]
    ok, msg = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is False
    assert "run_backtest" in msg


def test_tools_routed_sequence_fails_on_wrong_order():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["run_batch_pricing", "run_greeks_landscape"],
    )
    # greeks before batch pricing -> out of designed order
    calls = [{"name": "run_greeks_landscape"}, {"name": "run_batch_pricing"}]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is False


def test_tools_routed_sequence_normalizes_tool_suffix():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["run_batch_pricing"],
    )
    ok, _ = evaluate_assertion(a, ctx(tool_calls=[{"name": "run_batch_pricing_tool"}]))
    assert ok is True


def test_tool_called_scores_backtest_date_window_explicitly():
    """A `tool_called` assertion carrying the requested start/end dates passes
    only when run_backtest is invoked with those exact dates — so a model that
    substitutes its own window (the Sonnet 5 / Opus 4.8 failure) fails this
    check explicitly rather than only via downstream numbers."""
    from app.golden_workflows.schema import _ToolCalled

    a = _ToolCalled(
        type="tool_called",
        name="run_backtest",
        args={"start_date": "2026-03-24", "end_date": "2026-06-24"},
    )
    correct = ctx(tool_calls=[{
        "name": "run_backtest",
        "args": {"portfolio_id": 2, "start_date": "2026-03-24",
                 "end_date": "2026-06-24", "engine": "quad"},
    }])
    substituted = ctx(tool_calls=[{
        "name": "run_backtest",
        "args": {"portfolio_id": 2, "start_date": "2026-04-01",
                 "end_date": "2026-07-03"},
    }])
    assert evaluate_assertion(a, correct)[0] is True
    assert evaluate_assertion(a, substituted)[0] is False


def test_flagship_backtest_step_asserts_requested_dates():
    """The risk-manager flagship backtest step must carry the date-args check."""
    from app.golden_workflows.registry import get_workflow

    wf = get_workflow("risk-manager-control-day")
    step = wf.steps[6]  # step 7 (0-indexed): the backtest step in the v2 manifest
    assert "2026-03-24" in step.user and "2026-06-24" in step.user
    date_checks = [
        a for a in step.assertions
        if a.type == "tool_called" and a.name == "run_backtest"
        and (a.args or {}).get("start_date") == "2026-03-24"
        and (a.args or {}).get("end_date") == "2026-06-24"
    ]
    assert len(date_checks) == 1


def test_high_board_is_a_valid_persona():
    from app.golden_workflows.schema import parse_workflow
    wf = parse_workflow({
        "id": "x", "schema_version": 1, "persona": "high_board",
        "title": "t", "objective": "o", "fixtures": "x.fixtures.json",
        "steps": [{"user": "u", "expected_skill": "s", "outcome": "o", "replay": "r"}],
        "success": {"assertions": [], "rubric": []},
    })
    assert wf.persona == "high_board"


# ---------------------------------------------------------------------------
# _dig list selectors  (flagship v2)
# ---------------------------------------------------------------------------

from app.golden_workflows.assertions import _dig

_GRID = {"landscape": [
    {"spot_shift": -0.2, "delta": -310000, "gamma": -16000},
    {"spot_shift": 0.1, "delta": -220000, "gamma": -9600},
]}


def test_dig_selects_row_by_numeric_key():
    found, val = _dig(_GRID, "landscape[spot_shift=0.1].gamma")
    assert found and val == -9600


def test_dig_selects_negative_float_key():
    found, val = _dig(_GRID, "landscape[spot_shift=-0.2].delta")
    assert found and val == -310000


def test_dig_missing_row_not_found():
    found, _ = _dig(_GRID, "landscape[spot_shift=0.3].gamma")
    assert not found


def test_dig_selector_on_non_list_not_found():
    found, _ = _dig({"landscape": {"a": 1}}, "landscape[spot_shift=0.1].gamma")
    assert not found


def test_dig_plain_index_still_works():
    found, val = _dig(_GRID, "landscape.1.gamma")
    assert found and val == -9600


# ---------------------------------------------------------------------------
# response_quotes_tool_value  (flagship v2)
# ---------------------------------------------------------------------------

def _quote_assertion(**over):
    from app.golden_workflows.schema import _ResponseQuotesToolValue
    d = dict(type="response_quotes_tool_value", tool="get_latest_risk_run",
             path="hotspot.delta")
    d.update(over)
    return _ResponseQuotesToolValue(**d)


_RISK_RESULT = [{"name": "get_latest_risk_run", "tool_call_id": "t1",
                 "content": {"hotspot": {"delta": -148000.0}}}]


def test_quotes_signed_exact_passes():
    ok, _ = evaluate_assertion(
        _quote_assertion(),
        ctx(response_text="AAPL delta is -148,000 today", tool_results=_RISK_RESULT))
    assert ok


def test_quotes_inverted_sign_fails_signed():
    ok, _ = evaluate_assertion(
        _quote_assertion(),
        ctx(response_text="AAPL delta is 148,000", tool_results=_RISK_RESULT))
    assert not ok


def test_quotes_inverted_sign_passes_magnitude():
    ok, _ = evaluate_assertion(
        _quote_assertion(match="magnitude"),
        ctx(response_text="a loss of 148,000", tool_results=_RISK_RESULT))
    assert ok


def test_quotes_suffix_expansion():
    ok, _ = evaluate_assertion(
        _quote_assertion(),
        ctx(response_text="delta roughly -148k", tool_results=_RISK_RESULT))
    assert ok


def test_quotes_rel_tol_boundary():
    ok, _ = evaluate_assertion(
        _quote_assertion(),
        ctx(response_text="delta -145,100", tool_results=_RISK_RESULT))
    assert ok  # |Δ|=2900 <= 2960 = 0.02*148000
    ok2, _ = evaluate_assertion(
        _quote_assertion(),
        ctx(response_text="delta -144,000", tool_results=_RISK_RESULT))
    assert not ok2  # |Δ|=4000 > 2960


def test_quotes_near_anchor_binds_metric():
    res = [{"name": "get_greeks_landscape_run", "tool_call_id": "t1", "content": {
        "landscape": [{"spot_shift": 0.1, "delta": -220000, "gamma": -9600}]}}]
    a = _quote_assertion(tool="get_greeks_landscape_run",
                         path="landscape[spot_shift=0.1].gamma", near=["gamma"])
    # Swapped answers with the true gamma value pushed beyond the 160-char window
    swapped = "gamma at +10% is -220,000." + (" filler" * 30) + " delta is -9,600"
    ok, _ = evaluate_assertion(a, ctx(response_text=swapped, tool_results=res))
    assert not ok
    good = "gamma at +10% is -9,600 and delta is -220,000"
    ok2, _ = evaluate_assertion(a, ctx(response_text=good, tool_results=res))
    assert ok2


def test_quotes_near_window_cutoff():
    text = "delta " + ("x" * 170) + " -148,000"
    ok, _ = evaluate_assertion(
        _quote_assertion(near=["delta"]),
        ctx(response_text=text, tool_results=_RISK_RESULT))
    assert not ok


def test_quotes_percent_token_division():
    res = [{"name": "t", "tool_call_id": "1", "content": {"v": 0.1}}]
    a = _quote_assertion(tool="t", path="v")
    ok, _ = evaluate_assertion(a, ctx(response_text="shift of 10%", tool_results=res))
    assert ok


def test_quotes_zero_target_abs_tol():
    res = [{"name": "t", "tool_call_id": "1", "content": {"v": 0.0}}]
    a = _quote_assertion(tool="t", path="v")
    ok, _ = evaluate_assertion(a, ctx(response_text="flat at 0.01", tool_results=res))
    assert ok  # |0.01 - 0| <= 0.02 abs


def test_quotes_missing_path_fails():
    ok, msg = evaluate_assertion(
        _quote_assertion(path="hotspot.vega"),
        ctx(response_text="-148,000", tool_results=_RISK_RESULT))
    assert not ok and "vega" in msg


def test_quotes_non_numeric_target_fails():
    res = [{"name": "get_latest_risk_run", "tool_call_id": "t1",
            "content": {"hotspot": {"delta": "big"}}}]
    ok, _ = evaluate_assertion(
        _quote_assertion(), ctx(response_text="big", tool_results=res))
    assert not ok


# ---------------------------------------------------------------------------
# tool_called args_any_of + exclusive_keys  (flagship v2)
# ---------------------------------------------------------------------------

def _scenario_called(**over):
    from app.golden_workflows.schema import _ToolCalled
    d = dict(type="tool_called", name="run_scenario_test",
             args_any_of=[{"predefined": ["market_crash"]},
                          {"scenario_set": "market-crash"}],
             exclusive_keys=["predefined", "custom", "scenario_set"])
    d.update(over)
    return _ToolCalled(**d)


def test_args_any_of_predefined_alternative_matches():
    calls = [{"name": "run_scenario_test",
              "args": {"portfolio_id": 2, "predefined": ["market_crash"]}}]
    ok, _ = evaluate_assertion(_scenario_called(), ctx(tool_calls=calls))
    assert ok


def test_args_any_of_scenario_set_alternative_matches():
    calls = [{"name": "run_scenario_test",
              "args": {"portfolio_id": 2, "scenario_set": "market-crash"}}]
    ok, _ = evaluate_assertion(_scenario_called(), ctx(tool_calls=calls))
    assert ok


def test_args_any_of_extra_predefined_sets_fail():
    calls = [{"name": "run_scenario_test",
              "args": {"predefined": ["market_crash", "vol_spike"]}}]
    ok, _ = evaluate_assertion(_scenario_called(), ctx(tool_calls=calls))
    assert not ok


def test_exclusive_keys_mixed_carrier_fails():
    calls = [{"name": "run_scenario_test",
              "args": {"predefined": ["market_crash"],
                       "custom": [{"name": "extra"}]}}]
    ok, _ = evaluate_assertion(_scenario_called(), ctx(tool_calls=calls))
    assert not ok


def test_exclusive_keys_empty_carrier_counts_as_absent():
    calls = [{"name": "run_scenario_test",
              "args": {"predefined": ["market_crash"], "custom": [],
                       "scenario_set": None}}]
    ok, _ = evaluate_assertion(_scenario_called(), ctx(tool_calls=calls))
    assert ok


def test_exclusive_keys_composes_with_plain_args():
    from app.golden_workflows.schema import _ToolCalled
    a = _ToolCalled(type="tool_called", name="run_scenario_test",
                    args={"predefined": ["market_crash"]},
                    exclusive_keys=["predefined", "custom"])
    bad = [{"name": "run_scenario_test",
            "args": {"predefined": ["market_crash"], "custom": [{"n": 1}]}}]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=bad))
    assert not ok


# ---------------------------------------------------------------------------
# artifact_contains  (flagship v2)
# ---------------------------------------------------------------------------

_ARTS = [{"kind": "text", "content": "Governance report: AAPL hotspot, CVaR -2,100,000"},
         {"kind": "chart", "content": "backtest"}]


def _artifact_contains(any_of, kind="text"):
    from app.golden_workflows.schema import _ArtifactContains
    return _ArtifactContains(type="artifact_contains", kind=kind, any_of=any_of)


def test_artifact_contains_case_insensitive_substring():
    ok, _ = evaluate_assertion(_artifact_contains(["cvar"]), ctx(artifacts=_ARTS))
    assert ok


def test_artifact_contains_kind_filtering():
    ok, _ = evaluate_assertion(_artifact_contains(["backtest"]), ctx(artifacts=_ARTS))
    assert not ok  # "backtest" only appears in the chart artifact


def test_artifact_contains_text_key_fallback():
    arts = [{"kind": "text", "text": "expected shortfall discussed"}]
    ok, _ = evaluate_assertion(_artifact_contains(["expected shortfall"]),
                               ctx(artifacts=arts))
    assert ok


def test_artifact_contains_miss_reports_terms():
    ok, msg = evaluate_assertion(_artifact_contains(["backtest"]), ctx(artifacts=_ARTS))
    assert not ok and "backtest" in msg


def test_all_calls_extra_substituted_call_fails():
    """Exact-use mode: a compliant first call must not mask a later
    over-executing call of the same tool."""
    a = _scenario_called(all_calls=True)
    calls = [
        {"name": "run_scenario_test", "args": {"predefined": ["market_crash"]}},
        {"name": "run_scenario_test", "args": {"predefined": ["vol_spike"]}},
    ]
    ok, msg = evaluate_assertion(a, ctx(tool_calls=calls))
    assert not ok and "run_scenario_test" in msg
    # Without all_calls the first compliant call passes (documenting the delta)
    ok2, _ = evaluate_assertion(_scenario_called(), ctx(tool_calls=calls))
    assert ok2


def test_all_calls_requires_at_least_one_call():
    a = _scenario_called(all_calls=True)
    ok, _ = evaluate_assertion(a, ctx(tool_calls=[]))
    assert not ok


def test_max_calls_blocks_duplicate_compliant_dispatch():
    """Even duplicate COMPLIANT calls of a costly dispatch tool are
    over-execution when max_calls caps the count."""
    calls = [
        {"name": "run_scenario_test", "args": {"predefined": ["market_crash"]}},
        {"name": "run_scenario_test", "args": {"scenario_set": "market-crash"}},
    ]
    capped = _scenario_called(all_calls=True, max_calls=1)
    ok, msg = evaluate_assertion(capped, ctx(tool_calls=calls))
    assert not ok and "over-execution" in msg
    # Without the cap, all-compliant duplicates pass (documenting the delta)
    uncapped = _scenario_called(all_calls=True)
    ok2, _ = evaluate_assertion(uncapped, ctx(tool_calls=calls))
    assert ok2


# --- response_quotes_value (fixture-truth grounding, Spec B) -----------------
from app.golden_workflows.schema import _ResponseQuotesValue

def test_response_quotes_value_signed_hit():
    a = _ResponseQuotesValue(type="response_quotes_value", value=573.3467, near=["delta"])
    ok, _ = evaluate_assertion(a, ctx(response_text="AAPL delta is 573.35"))
    assert ok

def test_response_quotes_value_signed_miss_on_wrong_sign():
    a = _ResponseQuotesValue(type="response_quotes_value", value=573.3467, near=["delta"])
    ok, _ = evaluate_assertion(a, ctx(response_text="AAPL delta is -573.35"))
    assert not ok

def test_response_quotes_value_magnitude_ignores_sign():
    a = _ResponseQuotesValue(type="response_quotes_value", value=-7758.99,
                             match="magnitude", near=["cvar"])
    ok, _ = evaluate_assertion(a, ctx(response_text="CVaR loss of 7,759"))
    assert ok

def test_response_quotes_value_no_tool_needed():
    # point-2 fix: correct-from-context, ZERO tool_results present.
    a = _ResponseQuotesValue(type="response_quotes_value", value=16.403, near=["gamma"])
    ok, _ = evaluate_assertion(a, ctx(response_text="gamma at +10% is 16.40"))
    assert ok


# --- structured-answer accessor (record_answer) ---
from app.golden_workflows.assertions import answer_fields


def test_answer_fields_merges_last_wins():
    c = ctx(tool_calls=[
        {"name": "record_answer", "args": {"answer": {"hotspot": "TSLA", "delta": 1.0}}},
        {"name": "record_answer", "args": {"answer": {"delta": 573.35}}},
    ])
    assert answer_fields(c) == {"hotspot": "TSLA", "delta": 573.35}


def test_answer_fields_flat_kwargs_shape():
    c = ctx(tool_calls=[{"name": "record_answer", "args": {"hotspot": "AAPL", "delta": 573.35}}])
    assert answer_fields(c) == {"hotspot": "AAPL", "delta": 573.35}


def test_answer_fields_suffixed_tool_name():
    # a harvested/live trace may carry the _tool suffix — normalize_tool_name folds it
    c = ctx(tool_calls=[{"name": "record_answer_tool", "args": {"answer": {"cvar": -7758.99}}}])
    assert answer_fields(c) == {"cvar": -7758.99}


def test_answer_fields_empty_when_absent():
    assert answer_fields(ctx(tool_calls=[{"name": "get_latest_risk_run", "args": {}}])) == {}


# --- structured-answer evaluation branches ---
from app.golden_workflows.schema import _AnswerFieldEquals, _AnswerFieldQuotes


def _answer_ctx(fields):
    return ctx(tool_calls=[{"name": "record_answer", "args": {"answer": fields}}])


def test_answer_field_equals_hit_and_miss():
    a = _AnswerFieldEquals(type="answer_field_equals", field="hotspot", equals="AAPL")
    assert evaluate_assertion(a, _answer_ctx({"hotspot": "aapl"}))[0] is True
    ok, detail = evaluate_assertion(a, _answer_ctx({"hotspot": "TSLA"}))
    assert ok is False and "TSLA" in detail


def test_answer_field_equals_no_answer_and_wrong_key():
    a = _AnswerFieldEquals(type="answer_field_equals", field="hotspot", equals="AAPL")
    assert evaluate_assertion(a, ctx())[1] == "no answer recorded for hotspot"
    ok, detail = evaluate_assertion(a, _answer_ctx({"underlying": "AAPL"}))
    assert ok is False and "key hotspot absent" in detail and "underlying" in detail


def test_answer_field_equals_any_of():
    a = _AnswerFieldEquals(type="answer_field_equals", field="hotspot", any_of=["AAPL", "Apple"])
    assert evaluate_assertion(a, _answer_ctx({"hotspot": "apple"}))[0] is True


def test_answer_field_quotes_signed_and_magnitude():
    a = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467)
    assert evaluate_assertion(a, _answer_ctx({"delta": 573.35}))[0] is True
    assert evaluate_assertion(a, _answer_ctx({"delta": -573.35}))[0] is False  # sign matters
    m = _AnswerFieldQuotes(type="answer_field_quotes", field="cvar", value=-7758.99, match="magnitude")
    assert evaluate_assertion(m, _answer_ctx({"cvar": 7758.5}))[0] is True


def test_answer_field_quotes_non_numeric_and_no_answer():
    a = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467)
    ok, detail = evaluate_assertion(a, _answer_ctx({"delta": "the delta"}))
    assert ok is False and "not numeric" in detail
    assert evaluate_assertion(a, ctx())[1] == "no answer recorded for delta"


def test_answer_field_quotes_numeric_string_coerced():
    a = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467)
    assert evaluate_assertion(a, _answer_ctx({"delta": "573.35"}))[0] is True


def test_answer_field_axes():
    from app.services.arena.scoring import _AXIS_BY_TYPE
    assert _AXIS_BY_TYPE["answer_field_equals"] == "adherence"
    assert _AXIS_BY_TYPE["answer_field_quotes"] == "grounding"


def test_response_quotes_value_miss_reports_what_was_quoted():
    # A failed grounding check names the numbers the response actually wrote in
    # the near-anchored region, so the drilldown can show the wrong value quoted.
    a = _ResponseQuotesValue(type="response_quotes_value", value=573.3467, near=["delta"])
    ok, detail = evaluate_assertion(
        a, ctx(response_text="AAPL delta cash 66.6%, gamma cash 86.2%"))
    assert not ok
    assert "response quoted" in detail
    assert "86.2%" in detail  # the actual token the model wrote near 'delta'


def test_response_quotes_value_miss_reports_no_number_near_anchor():
    # When nothing numeric sits near the anchor, say so plainly.
    a = _ResponseQuotesValue(type="response_quotes_value", value=16.403, near=["gamma"])
    ok, detail = evaluate_assertion(a, ctx(response_text="gamma looks elevated today"))
    assert not ok
    assert "no number" in detail

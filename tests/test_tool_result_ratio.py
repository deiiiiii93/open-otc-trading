"""Unit tests for the spot- AND multiplier-invariant `tool_result_ratio` assertion.

The whole point: the live arena fetches REAL market data, so absolute-value grounds
drift. A ratio like premium/(spot*contract_multiplier) is invariant across spot AND
contract-multiplier regimes — verified here against BOTH captured Run #21 shapes
(pro: multiplier 1; flash: multiplier 100).
"""
from app.golden_workflows.assertions import AssertionContext, evaluate_assertion
from app.golden_workflows.schema import _ToolResultRatio


def _ctx(result_content=None, call_args=None):
    return AssertionContext(
        response_text="",
        tool_calls=[{"name": "book_position", "args": call_args or {}}],
        tool_results=[{"name": "quote_rfq", "content": result_content or {}}],
        skills_routed=[], artifacts=[], task_ids=[],
    )


# Captured Run #21 quote_rfq shapes (real numbers).
_PRO = {"quote_payload": {"achieved_price": 33.33101381620867},
        "request_payload": {"market": {"spot": 390.99},
                            "product": {"terms": {"contract_multiplier": 1.0}}}}
_FLASH = {"quote_payload": {"achieved_price": 3333.101381620867},
          "request_payload": {"market": {"spot": 390.99},
                              "product": {"terms": {"contract_multiplier": 100.0}}}}


def _prem_ratio(**over):
    kw = dict(type="tool_result_ratio", tool="quote_rfq",
              numer="quote_payload.achieved_price",
              denom="request_payload.market.spot",
              denom_mult="request_payload.product.terms.contract_multiplier",
              equals=0.08525, rel_tol=0.03)
    kw.update(over)
    return _ToolResultRatio(**kw)


def test_premium_ratio_passes_multiplier_1_pro():
    ok, msg = evaluate_assertion(_prem_ratio(), _ctx(_PRO))
    assert ok, msg


def test_premium_ratio_passes_multiplier_100_flash():
    # SAME assertion; multiplier normalization makes 3333.1/(390.99*100)=0.08525.
    ok, msg = evaluate_assertion(_prem_ratio(), _ctx(_FLASH))
    assert ok, msg


def test_premium_ratio_fails_wrong_target():
    ok, _ = evaluate_assertion(_prem_ratio(equals=0.05), _ctx(_PRO))
    assert not ok


def test_ratio_fails_zero_denominator():
    bad = {"quote_payload": {"achieved_price": 1.0},
           "request_payload": {"market": {"spot": 0.0},
                               "product": {"terms": {"contract_multiplier": 1.0}}}}
    ok, msg = evaluate_assertion(_prem_ratio(), _ctx(bad))
    assert not ok and "denominator is zero" in msg


def test_ratio_fails_missing_or_nonnumeric_path():
    ok, _ = evaluate_assertion(_prem_ratio(numer="quote_payload.nope"), _ctx(_PRO))
    assert not ok


def test_ratio_no_denom_mult_defaults_to_one():
    a = _ToolResultRatio(type="tool_result_ratio", tool="quote_rfq",
                         numer="request_payload.product.terms.barrier",
                         denom="request_payload.product.terms.strike",
                         equals=0.80, rel_tol=0.01)
    content = {"request_payload": {"product": {"terms": {"barrier": 312.792, "strike": 390.99}}}}
    ok, msg = evaluate_assertion(a, _ctx(content))
    assert ok, msg


def test_ratio_source_call_reads_call_args():
    # barrier/strike on the AUTHORITATIVE book_position call args (0.80).
    a = _ToolResultRatio(type="tool_result_ratio", tool="book_position", source="call",
                         numer="product.terms.barrier", denom="product.terms.strike",
                         equals=0.80, rel_tol=0.01)
    call = {"product": {"terms": {"barrier": 312.792, "strike": 390.99}}}
    ok, msg = evaluate_assertion(a, _ctx(call_args=call))
    assert ok, msg
    # A DOWN_OUT/off-0.80 booking must fail.
    bad = {"product": {"terms": {"barrier": 200.0, "strike": 390.99}}}
    ok2, _ = evaluate_assertion(a, _ctx(call_args=bad))
    assert not ok2

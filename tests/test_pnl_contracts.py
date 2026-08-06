import pytest
from pydantic import ValidationError

from app.services.reporting.contracts import (
    BlockContext,
    BlockResult,
    BlockShape,
)


def test_ok_result_needs_no_reason():
    result = BlockResult.ok(data={"delta": 1.0}, provenance={"risk_run_id": 36})
    assert result.status == "ok"
    assert result.reason is None
    assert result.data == {"delta": 1.0}


def test_empty_and_unavailable_are_distinct_states():
    empty = BlockResult.empty("no breaches recorded for this run")
    unavailable = BlockResult.unavailable("no prior risk run to compare against")
    assert empty.status == "empty"
    assert unavailable.status == "unavailable"
    assert empty.status != unavailable.status


def test_non_ok_status_requires_a_reason():
    with pytest.raises(ValidationError):
        BlockResult(status="unavailable", reason=None, data={}, provenance={})
    with pytest.raises(ValidationError):
        BlockResult(status="empty", reason="", data={}, provenance={})


def test_block_shapes_cover_every_renderer_target():
    assert {shape.value for shape in BlockShape} == {
        "scalars",
        "scalars_with_prior",
        "rows",
        "series",
        "waterfall",
        "items",
        "position_greeks",
    }


def test_block_context_defaults_comparison_to_none():
    ctx = BlockContext(portfolio_id=2)
    assert ctx.compare_to_run_id is None
    assert ctx.params == {}

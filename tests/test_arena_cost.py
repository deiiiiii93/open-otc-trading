"""Arena match cost: token metering and list-price USD (services/arena/cost.py)."""
from __future__ import annotations

import json

import pytest

from app.golden_workflows.transcript import MatchTranscript
from app.services.arena import cost
from app.services.arena.trace_harvest import _llm_usage

LUNA = {  # tiered: the whole request doubles once the prompt reaches 272k
    "prompt": [{"usd_per_mtok": 0.1, "min_prompt_ktok": 0}, {"usd_per_mtok": 0.2, "min_prompt_ktok": 272}],
    "completion": [{"usd_per_mtok": 0.5, "min_prompt_ktok": 0}, {"usd_per_mtok": 0.75, "min_prompt_ktok": 272}],
    "input_cache_read": [{"usd_per_mtok": 0.01, "min_prompt_ktok": 0}, {"usd_per_mtok": 0.02, "min_prompt_ktok": 272}],
}
AMBIGUOUS = {  # deepseek-v4.1-flash shape: unconditioned entries that disagree
    "prompt": [{"usd_per_mtok": 0.075, "min_prompt_ktok": 0}, {"usd_per_mtok": 0.15, "min_prompt_ktok": 0}],
    "completion": [{"usd_per_mtok": 0.3, "min_prompt_ktok": 0}, {"usd_per_mtok": 0.6, "min_prompt_ktok": 0}],
}
NO_CACHE_RATE = {
    "prompt": [{"usd_per_mtok": 1.0, "min_prompt_ktok": 0}],
    "completion": [{"usd_per_mtok": 2.0, "min_prompt_ktok": 0}],
}


def _call(model="openai/gpt-6-luna", **tokens):
    return {"model": model, "generation_id": "g1", "input": 0, "cache_read": 0,
            "cache_write": 0, "output": 0, "reasoning": 0, **tokens}


def test_cached_tokens_priced_at_cache_rate_not_prompt_rate():
    # 200k input (below the 272k tier) of which 150k cached, 10k output:
    # 50k×0.1 + 150k×0.01 + 10k×0.5 per 1M
    low, high = cost.price_call(_call(input=200_000, cache_read=150_000, output=10_000), LUNA)
    assert low == high == pytest.approx(0.05 * 0.1 + 0.15 * 0.01 + 0.01 * 0.5, rel=1e-9)


def test_prompt_tier_applies_to_the_whole_request():
    below = cost.price_call(_call(input=271_999, output=1_000_000), LUNA)
    above = cost.price_call(_call(input=272_000, output=1_000_000), LUNA)
    assert below[0] == pytest.approx(0.271999 * 0.1 + 0.5)
    assert above[0] == pytest.approx(0.272 * 0.2 + 0.75)


def test_route_without_cache_rate_bills_cached_as_prompt():
    low, _ = cost.price_call(_call(input=1_000_000, cache_read=1_000_000), NO_CACHE_RATE)
    assert low == pytest.approx(1.0)


def _transcript(calls):
    return MatchTranscript.model_validate({
        "schema_version": 1, "run_id": None, "workflow_id": "w", "model_id": "m",
        "started_at": None, "finished_at": None,
        "steps": [{"index": 0, "user": "u", "messages": [], "tool_calls": [],
                   "tool_results": [], "skills_routed": [], "artifacts": [],
                   "task_ids": [], "response_text": "", "errors": [], "usage": calls}],
    })


@pytest.fixture
def snapshot(monkeypatch):
    snap = {"models": {"openai/gpt-6-luna": LUNA, "deepseek/deepseek-v4.1-flash": AMBIGUOUS},
            "source": "test", "fetched_at": "2026-09-25", "sha256": "abc"}
    monkeypatch.setattr(cost, "pricing_snapshot", lambda: snap)


def test_usage_block_totals_and_exact_usd(snapshot):
    block = cost.usage_block(_transcript([
        _call(input=100_000, cache_read=50_000, output=2_000, reasoning=500),
        _call(input=200_000, output=1_000),
    ]))
    assert block["calls"] == 2 and block["input"] == 300_000 and block["reasoning"] == 500
    assert block["total"] == 303_000 and block["generation_ids"] == 2
    assert block["usd"] == block["usd_low"] == block["usd_high"] > 0
    assert block["pricing"]["sha256"] == "abc"


def test_ambiguous_rates_report_a_range_not_a_number(snapshot):
    block = cost.usage_block(_transcript([
        _call(model="deepseek/deepseek-v4.1-flash", input=1_000_000, output=1_000_000)]))
    assert block["usd"] is None
    assert block["usd_low"] == pytest.approx(0.375) and block["usd_high"] == pytest.approx(0.75)


def test_unpriced_model_is_never_read_as_free(snapshot):
    block = cost.usage_block(_transcript([_call(), _call(model="acme/unknown", input=10)]))
    assert block["unpriced_calls"] == 1 and block["usd"] is None
    assert block["by_model"]["acme/unknown"]["priced"] is False


def test_unmetered_transcript_is_none_not_zero(snapshot):
    assert cost.usage_block(_transcript([])) is None


def test_fold_sums_trials_and_keeps_coverage():
    a = {"calls": 2, "input": 10, "output": 1, "total": 11, "usd": 0.5, "usd_low": 0.5,
         "usd_high": 0.5, "generation_ids": 2, "unpriced_calls": 0, "pricing": {"x": 1}}
    folded = cost.fold_usage([a, None, dict(a)])
    assert folded["calls"] == 4 and folded["usd"] == 1.0
    assert folded["trials_measured"] == 2 and folded["trials_total"] == 3
    assert cost.fold_usage([None]) is None


def test_harvest_reads_usage_metadata_and_generation_id():
    outputs = {"generations": [[{"message": {"kwargs": {
        "usage_metadata": {"input_tokens": 34301, "output_tokens": 182,
                           "input_token_details": {"cache_read": 33161},
                           "output_token_details": {"reasoning": 53}},
        "response_metadata": {"model_name": "openai/gpt-6-luna",
                              "generation_ids": ["e6164", "e6164"]},
    }}}]]}
    assert _llm_usage(json.dumps(outputs)) == {
        "model": "openai/gpt-6-luna", "generation_id": "e6164", "input": 34301,
        "cache_read": 33161, "cache_write": 0, "output": 182, "reasoning": 53}
    assert _llm_usage(json.dumps({"generations": [[{"message": {"kwargs": {}}}]]})) is None


def test_vendored_snapshot_prices_this_boards_contestants():
    models = cost.pricing_snapshot()["models"]
    for m in ("openai/gpt-6-luna", "xiaomi/mimo-v2.6-flash", "deepseek/deepseek-v4.1-flash"):
        assert "prompt" in models[m] and "completion" in models[m]

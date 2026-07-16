"""Unit tests for check_term_completeness - contract-grounded verdicts."""
from __future__ import annotations

from app.services.agents import DEEP_AGENT_TOOL_NAMES
from app.tools import QUANT_AGENT_TOOLS
from app.tools.term_completeness import check_term_completeness


def _invoke(quantark_class: str, terms: dict | None = None) -> dict:
    return check_term_completeness.invoke(
        {"quantark_class": quantark_class, "terms": terms or {}}
    )


def test_tool_is_registered_everywhere() -> None:
    assert "check_term_completeness" in {t.name for t in QUANT_AGENT_TOOLS}
    assert "check_term_completeness" in DEEP_AGENT_TOOL_NAMES


def test_sharkfin_missing_barrier_only() -> None:
    # The live A/B probe scenario: the ONLY missing required term is the
    # barrier - no knock-in, no coupon, no rebate (the phantom terms models
    # confabulate from priors).
    result = _invoke(
        "SingleSharkfinOption",
        {"initial_price": 100, "maturity_years": 1.0, "strike": 100,
         "participation_rate": 1.0},
    )
    assert result["complete"] is False
    assert result["missing_required"] == ["barrier"]
    assert "participation_rate" not in result["defaulted_unset"]


def test_dotted_and_nested_keys_are_equivalent() -> None:
    base = {
        "initial_price": 100, "maturity_years": 1.0, "trade_start_date": "2026-07-02",
        "observation_frequency": "MONTHLY",
        "barrier_config.ko_barrier": 1.0, "barrier_config.ki_barrier": 0.8,
        "barrier_config.ko_rate": 0.12, "barrier_config.lockup_months": 3,
    }
    nested = {
        "initial_price": 100, "maturity_years": 1.0, "trade_start_date": "2026-07-02",
        "observation_frequency": "MONTHLY",
        "barrier_config": {"ko_barrier": 1.0, "ki_barrier": 0.8,
                           "ko_rate": 0.12, "lockup_months": 3},
    }
    assert _invoke("SnowballOption", base) == _invoke("SnowballOption", nested)


def test_snowball_custom_frequency_requires_dates() -> None:
    # Flat term vocabulary — what the synthesize builder actually reads. (Nested-only
    # barrier_config is a malformed synthesize input; the non-barrier legs ko_rate/
    # lockup_months are read flat, so completeness must be checked against flat terms.)
    terms = {
        "initial_price": 100, "maturity_years": 1.0, "trade_start_date": "2026-07-02",
        "ko_barrier": 1.0, "ki_barrier": 0.8, "ko_rate": 0.12, "lockup_months": 3,
        "ki_convention": "DAILY",
    }
    monthly = _invoke("SnowballOption", {**terms, "observation_frequency": "MONTHLY"})
    assert monthly["complete"] is True
    custom = _invoke("SnowballOption", {**terms, "observation_frequency": "CUSTOM"})
    assert custom["missing_required"] == ["ko_observation_dates"]


def test_unknown_class_lists_known() -> None:
    result = _invoke("NopeOption")
    assert "error" in result
    assert "SnowballOption" in result["known_classes"]

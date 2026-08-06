from app.services.reporting.grounding import (
    check_grounding,
    collect_numeric_values,
)


def test_collect_walks_nested_structures():
    data = {
        "metrics": {"delta_cash": 57334.67, "vega": 276.43},
        "rows": [{"change": -12.5}, {"change": 3}],
        "label": "not a number",
        "nested": {"deep": [{"x": 1.5}]},
    }
    values = collect_numeric_values(data)
    assert 57334.67 in values
    assert 276.43 in values
    assert -12.5 in values
    assert 3.0 in values
    assert 1.5 in values


def test_collect_ignores_booleans():
    """True/False are ints in Python; treating them as data numbers is noise."""
    assert collect_numeric_values({"ok": True, "bad": False, "n": 2.0}) == [2.0]


def test_a_narrative_quoting_only_block_numbers_has_no_flags():
    blocks = [{"metrics": {"delta_cash": 57334.67, "vega": 276.43}}]
    narrative = "Delta cash stands at 57,334.67 with vega of 276.43."
    result = check_grounding(narrative, blocks)
    assert result["checked"] is True
    assert result["flags"] == []
    assert result["grounded_count"] == 2


def test_an_invented_number_is_flagged():
    blocks = [{"metrics": {"delta_cash": 57334.67}}]
    narrative = "Delta cash stands at 57,334.67 and vega at 999.99."
    result = check_grounding(narrative, blocks)
    assert [flag["token"] for flag in result["flags"]] == [999.99]


def test_tolerance_allows_rounded_prose():
    """A trader writes 57,335 not 57,334.67. That is grounded, not invented."""
    blocks = [{"metrics": {"delta_cash": 57334.67}}]
    result = check_grounding("Delta cash is about 57,335.", blocks)
    assert result["flags"] == []


def test_tolerance_does_not_swallow_a_materially_different_number():
    blocks = [{"metrics": {"delta_cash": 57334.67}}]
    result = check_grounding("Delta cash is about 61,000.", blocks)
    assert [flag["token"] for flag in result["flags"]] == [61000.0]


def test_percentages_match_either_form():
    """A 0.34 ratio in data may legitimately be written as 34%."""
    blocks = [{"data": {"ratio": 0.34}}]
    assert check_grounding("Utilisation reached 34%.", blocks)["flags"] == []


def test_a_narrative_with_no_numbers_is_trivially_grounded():
    result = check_grounding("The book is quiet and needs no action.", [{}])
    assert result["flags"] == []
    assert result["grounded_count"] == 0


def test_empty_narrative_is_reported_as_unchecked():
    result = check_grounding("", [{"metrics": {"x": 1.0}}])
    assert result["checked"] is False
    assert result["flags"] == []


def test_values_from_any_block_in_the_section_count_as_grounded():
    blocks = [{"metrics": {"a": 10.0}}, {"metrics": {"b": 20.0}}]
    assert check_grounding("We saw 10 and 20.", blocks)["flags"] == []

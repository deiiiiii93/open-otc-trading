from __future__ import annotations

import pytest

from app.services.portfolio_rule_dsl import (
    DslSyntaxError,
    compile_rule_to_text,
    parse_text_to_rule,
)


def test_parse_simple_eq():
    assert parse_text_to_rule('product_type = "Snowball"') == {
        "op": "eq", "field": "product_type", "value": "Snowball",
    }


def test_parse_unquoted_value():
    assert parse_text_to_rule('product_type = Snowball') == {
        "op": "eq", "field": "product_type", "value": "Snowball",
    }


def test_parse_in_list():
    assert parse_text_to_rule('underlying IN (AAPL, TSLA)') == {
        "op": "in", "field": "underlying", "value": ["AAPL", "TSLA"],
    }


def test_parse_and():
    rule = parse_text_to_rule('product_type = Snowball AND status = open')
    assert rule == {
        "op": "and",
        "children": [
            {"op": "eq", "field": "product_type", "value": "Snowball"},
            {"op": "eq", "field": "status", "value": "open"},
        ],
    }


def test_parse_or_and_not():
    rule = parse_text_to_rule('NOT (status = closed) OR quantity < 100')
    assert rule == {
        "op": "or",
        "children": [
            {"op": "not", "child": {"op": "eq", "field": "status", "value": "closed"}},
            {"op": "lt", "field": "quantity", "value": 100.0},
        ],
    }


def test_parse_between():
    rule = parse_text_to_rule('quantity BETWEEN 10 AND 100')
    assert rule == {"op": "between", "field": "quantity", "value": [10.0, 100.0]}


def test_parse_invalid_raises():
    with pytest.raises(DslSyntaxError):
        parse_text_to_rule('product_type ===== Snowball')


def test_compile_then_parse_roundtrip():
    original = {
        "op": "and",
        "children": [
            {"op": "eq", "field": "product_type", "value": "Snowball"},
            {"op": "in", "field": "underlying", "value": ["AAPL", "TSLA"]},
            {"op": "not", "child": {"op": "eq", "field": "status", "value": "closed"}},
        ],
    }
    text = compile_rule_to_text(original)
    assert parse_text_to_rule(text) == original


def test_scientific_notation_roundtrip():
    """Floats that repr as scientific notation must round-trip."""
    for value in (1e-20, 1e20, -2.5e-10, 3.14e15):
        original = {"op": "eq", "field": "quantity", "value": value}
        text = compile_rule_to_text(original)
        parsed = parse_text_to_rule(text)
        assert parsed == original, f"failed roundtrip for {value!r} via {text!r}"


def test_parse_scientific_notation_literal():
    assert parse_text_to_rule("quantity = 1e-3") == {
        "op": "eq", "field": "quantity", "value": 1e-3,
    }
    assert parse_text_to_rule("quantity = 2.5E+10") == {
        "op": "eq", "field": "quantity", "value": 2.5e10,
    }

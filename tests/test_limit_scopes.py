"""The single scope-membership rule both monitoring and sources use (spec §Checkers)."""
from __future__ import annotations

import pytest

from app.services.limits.scopes import SCOPE_TYPES, scope_key_for, scope_matches, scope_value


def test_key_round_trip_matches_the_monitoring_format():
    assert scope_key_for("underlying", "000300") == "underlying:000300"
    assert scope_key_for("position", 7) == "position:7"
    assert scope_value("position:7") == "7"
    assert scope_value("underlying:A:B") == "A:B"      # only the FIRST colon splits
    assert scope_value("portfolio") is None             # legacy bare key


def test_unknown_scope_type_is_rejected():
    with pytest.raises(ValueError):
        scope_key_for("desk", 1)
    with pytest.raises(ValueError):
        scope_matches("desk", 1, position_id=1, underlying="x", family="y")


@pytest.mark.parametrize("scope_type, value, row, expected", [
    ("portfolio", "3", dict(position_id=1, underlying="000300", family="Snowball"), True),
    ("position", "7", dict(position_id=7, underlying="000300", family="Snowball"), True),
    ("position", "7", dict(position_id=8, underlying="000300", family="Snowball"), False),
    ("position", "7", dict(position_id=None, underlying="000300", family="Snowball"), False),
    ("underlying", "000300", dict(position_id=1, underlying="000300", family="x"), True),
    ("underlying", "000300", dict(position_id=1, underlying="510050", family="x"), False),
    ("underlying", "000300", dict(position_id=1, underlying=None, family="x"), False),
    ("product_family", "SnowballOption", dict(position_id=1, underlying="x", family="SnowballOption"), True),
    ("product_family", "SnowballOption", dict(position_id=1, underlying="x", family="PhoenixOption"), False),
])
def test_membership(scope_type, value, row, expected):
    assert scope_matches(scope_type, value, **row) is expected


def test_comparison_is_by_string_so_int_and_str_ids_agree():
    assert scope_matches("position", 7, position_id="7", underlying=None, family=None)
    assert SCOPE_TYPES == ("portfolio", "underlying", "product_family", "position")

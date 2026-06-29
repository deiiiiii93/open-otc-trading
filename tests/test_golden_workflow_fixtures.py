"""Tests for golden_workflows.fixtures: load_fixtures + apply_seed.

TDD order:
  Step 1 – validation tests (no DB):  test_seed_map_*, test_unknown_*, ...
  Step 3b – DB test:                  test_apply_seed_inserts_explicit_ids_and_resolves_fk
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from app.golden_workflows.fixtures import load_fixtures
from app.golden_workflows.schema import DuplicateAliasError, UnknownSeedNamespaceError


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "wf.fixtures.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# seed_map construction
# ---------------------------------------------------------------------------

def test_seed_map_built_with_type_preserved(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {"portfolios": [{"alias": "control", "id": 6, "name": "Book"}]},
        "replay": {},
    })
    b = load_fixtures(p)
    assert b.seed_map["$seed.portfolios.control.id"] == 6


# ---------------------------------------------------------------------------
# Namespace / alias validation
# ---------------------------------------------------------------------------

def test_unknown_namespace_rejected(tmp_path):
    p = _write(tmp_path, {"schema_version": 1, "seed": {"banana": []}, "replay": {}})
    with pytest.raises(UnknownSeedNamespaceError):
        load_fixtures(p)


def test_duplicate_alias_rejected(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "portfolios": [
                {"alias": "a", "id": 1, "name": "x"},
                {"alias": "a", "id": 2, "name": "y"},
            ]
        },
        "replay": {},
    })
    with pytest.raises(DuplicateAliasError):
        load_fixtures(p)


# ---------------------------------------------------------------------------
# Replay tool_call_id integrity
# ---------------------------------------------------------------------------

def test_replay_tool_call_id_integrity(tmp_path):
    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {},
        "replay": {
            "r1": {
                "ai": {
                    "content": "",
                    "tool_calls": [{"id": "c1", "name": "t", "args": {}}],
                },
                "tool_results": [
                    {"tool_call_id": "MISSING", "name": "t", "content": {}}
                ],
                "skills_routed": [],
                "artifacts": [],
                "response_text": "",
            }
        },
    })
    from app.golden_workflows.schema import WorkflowError
    with pytest.raises(WorkflowError):
        load_fixtures(p)


# ---------------------------------------------------------------------------
# apply_seed — temp-DB gate test (Step 3b)
# Note: Position requires `portfolio_id`, `underlying`, `product_type`,
#       `quantity` — the seed row includes the latter two as extra columns.
# ---------------------------------------------------------------------------

def test_apply_seed_inserts_explicit_ids_and_resolves_fk(tmp_path, session):
    from app import models

    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "portfolios": [{"alias": "control", "id": 6, "name": "Book"}],
            "positions": [
                {
                    "alias": "p1",
                    "portfolio": "control",
                    "underlying": "AAPL",
                    "product_type": "vanilla",
                    "quantity": 1.0,
                }
            ],
        },
        "replay": {},
    })
    from app.golden_workflows.fixtures import apply_seed

    ids = apply_seed(load_fixtures(p), session)

    assert ids["portfolios"]["control"] == 6
    assert session.get(models.Portfolio, 6) is not None
    pos = session.get(models.Position, ids["positions"]["p1"])
    assert pos is not None
    assert pos.portfolio_id == 6


def test_apply_seed_inserts_pricing_parameter_rows_under_profile(tmp_path, session):
    """The pricing_parameter_rows namespace FK-resolves to its profile and
    forwards r/q/vol so profile-bound batch pricing can extract parameters."""
    from app import models

    p = _write(tmp_path, {
        "schema_version": 1,
        "seed": {
            "pricing_profiles": [
                {"alias": "prof", "name": "Control Profile",
                 "valuation_date": "2026-06-24"}
            ],
            "pricing_parameter_rows": [
                {"alias": "ppr-aapl", "profile": "prof", "symbol": "AAPL",
                 "rate": 0.04, "dividend_yield": 0.005, "volatility": 0.30}
            ],
        },
        "replay": {},
    })
    from app.golden_workflows.fixtures import apply_seed

    ids = apply_seed(load_fixtures(p), session)

    profile_id = ids["pricing_profiles"]["prof"]
    row = session.get(models.PricingParameterRow, ids["pricing_parameter_rows"]["ppr-aapl"])
    assert row is not None
    assert row.profile_id == profile_id
    assert row.symbol == "AAPL"
    assert (row.rate, row.dividend_yield, row.volatility) == (0.04, 0.005, 0.30)
    # source_trade_id is NOT NULL on the model; the seeder defaults it to "".
    assert row.source_trade_id == ""

"""Engine config variants: rule validation, engine resolution, run plumbing.

Covers the contract from the engine-config feature request:
- tasks read the engine from the position first, else map via the config
- family rules (autocallables/others) use the QUAD/MC/PDE/ANALYTICAL enums
- product-type rules name real QuantArk engines and beat family rules
- backtest has no standalone config: the resolved pricing engine is projected
  onto the backtest engine vocabulary
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import EngineConfigVariant
from app.services.engine_configs import (
    get_engine_config,
    resolve_backtest_engine,
    resolve_pricing_engine,
    validate_rules,
)
from app.services.quantark import ensure_quantark_path


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


def _pos(product_type: str, engine_name: str | None = None, engine_kwargs: dict | None = None):
    return SimpleNamespace(
        id=1,
        product_type=product_type,
        engine_name=engine_name,
        engine_kwargs=engine_kwargs or {},
        product=None,
    )


def _config(rules: list[dict]) -> EngineConfigVariant:
    return EngineConfigVariant(name="test", rules={"rules": rules})


def _family_config(autocallables: str = "QUAD", others: str = "ANALYTICAL") -> EngineConfigVariant:
    return _config([
        {"name": "Autocallables", "match": {"product_family": "autocallables"},
         "pricing": {"engine_type": autocallables}},
        {"name": "Others", "match": {"product_family": "others"},
         "pricing": {"engine_type": others}},
    ])


# ---------------------------------------------------------------------------
# Resolution precedence: override > position engine > config rule
# ---------------------------------------------------------------------------

def test_position_engine_beats_config_rule():
    config = _family_config()
    resolved = resolve_pricing_engine(_pos("EuropeanVanillaOption", "BlackScholesEngine"), config)
    assert resolved.engine_name == "BlackScholesEngine"
    assert resolved.source == "position"


def test_explicit_override_beats_position_engine():
    config = _family_config()
    resolved = resolve_pricing_engine(
        _pos("EuropeanVanillaOption", "BlackScholesEngine"),
        config,
        override_engine_name="EuropeanMCEngine",
        override_engine_kwargs={"n_paths": 1000},
    )
    assert resolved.engine_name == "EuropeanMCEngine"
    assert resolved.engine_kwargs == {"n_paths": 1000}
    assert resolved.source == "override"


@pytest.mark.parametrize("missing", [None, "", "  ", "auto", "AUTO"])
def test_blank_or_auto_position_engine_falls_through_to_config(missing):
    config = _family_config()
    resolved = resolve_pricing_engine(_pos("EuropeanVanillaOption", missing), config)
    assert resolved.source == "engine_config"
    assert resolved.engine_name == "BlackScholesEngine"


def test_product_type_rule_beats_family_rule_regardless_of_order():
    config = _config([
        {"name": "Others", "match": {"product_family": "others"},
         "pricing": {"engine_type": "ANALYTICAL"}},
        {"name": "Vanilla MC", "match": {"product_type": "EuropeanVanillaOption"},
         "pricing": {"engine_name": "EuropeanMCEngine", "engine_kwargs": {"n_paths": 5000}}},
    ])
    resolved = resolve_pricing_engine(_pos("EuropeanVanillaOption"), config)
    assert resolved.engine_name == "EuropeanMCEngine"
    assert resolved.engine_kwargs == {"n_paths": 5000}
    assert resolved.rule_name == "Vanilla MC"


def test_no_config_and_no_position_engine_raises():
    with pytest.raises(ValueError):
        resolve_pricing_engine(_pos("EuropeanVanillaOption"), None)


# ---------------------------------------------------------------------------
# Family enum -> engine name, per product type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("product_type, expected", [
    ("EuropeanVanillaOption", "BlackScholesEngine"),
    ("AmericanOption", "AmericanOptionAnalyticalEngine"),
    ("CashOrNothingDigitalOption", "DigitalOptionAnalyticalEngine"),
    ("BarrierOption", "BarrierAnalyticalEngine"),
    ("OneTouchOption", "OneTouchAnalyticalEngine"),
    ("DoubleOneTouchOption", "OneTouchAnalyticalEngine"),
    ("AsianOption", "AsianOptionAnalyticalEngine"),
    ("RangeAccrualOption", "RangeAccrualAnalyticalEngine"),
    ("SingleSharkfinOption", "SingleSharkfinOptionAnalyticalEngine"),
    ("DoubleSharkfinOption", "DoubleSharkfinOptionAnalyticalEngine"),
])
def test_analytical_family_rule_covers_every_others_product(product_type, expected):
    resolved = resolve_pricing_engine(_pos(product_type), _family_config())
    assert resolved.engine_name == expected
    assert resolved.source == "engine_config"


@pytest.mark.parametrize("product_type, expected", [
    ("SnowballOption", "SnowballMCEngine"),
    ("PhoenixOption", "PhoenixMCEngine"),
    ("EuropeanVanillaOption", "EuropeanMCEngine"),
    ("AmericanOption", "AmericanOptionMCEngine"),
    ("CashOrNothingDigitalOption", "DigitalOptionMCEngine"),
    ("BarrierOption", "BarrierOptionMCEngine"),
    ("AsianOption", "AsianOptionMCEngine"),
    ("RangeAccrualOption", "RangeAccrualMCEngine"),
    ("SingleSharkfinOption", "SingleSharkfinOptionMCEngine"),
    ("DoubleSharkfinOption", "DoubleSharkfinOptionMCEngine"),
])
def test_mc_family_rule_maps_to_registered_mc_engines(product_type, expected):
    resolved = resolve_pricing_engine(_pos(product_type), _family_config("MC", "MC"))
    assert resolved.engine_name == expected


@pytest.mark.parametrize("product_type", [
    "KnockOutResetSnowballOption",  # no KO-reset MC engine in QuantArk
    "OneTouchOption",
    "DoubleOneTouchOption",
])
def test_mc_family_rule_rejects_products_without_mc_engine(product_type):
    with pytest.raises(ValueError):
        resolve_pricing_engine(_pos(product_type), _family_config("MC", "MC"))


@pytest.mark.parametrize("product_type", [
    "EuropeanVanillaOption",
    "AmericanOption",
    "BarrierOption",
    "OneTouchOption",
    "DoubleOneTouchOption",
    "SnowballOption",
    "KnockOutResetSnowballOption",
    "PhoenixOption",
])
def test_pde_family_rule_maps_supported_products_to_pde_engine(product_type):
    resolved = resolve_pricing_engine(_pos(product_type), _family_config("PDE", "PDE"))
    assert resolved.engine_name == "PDEEngine"


def test_pde_family_rule_rejects_unsupported_product():
    with pytest.raises(ValueError):
        resolve_pricing_engine(_pos("AsianOption"), _family_config("PDE", "PDE"))


@pytest.mark.parametrize("product_type, expected", [
    ("SnowballOption", "SnowballQuadEngine"),
    ("PhoenixOption", "PhoenixQuadEngine"),
    ("KnockOutResetSnowballOption", "KOResetSnowballQuadEngine"),
    ("EuropeanVanillaOption", "EuropeanQuadEngine"),
    ("BarrierOption", "BarrierQuadEngine"),
    ("OneTouchOption", "OneTouchQuadEngine"),
])
def test_quad_family_rule_maps_to_registered_quad_engines(product_type, expected):
    resolved = resolve_pricing_engine(_pos(product_type), _family_config("QUAD", "QUAD"))
    assert resolved.engine_name == expected


def test_quad_family_rule_rejects_product_without_quad_engine():
    with pytest.raises(ValueError):
        resolve_pricing_engine(_pos("AsianOption"), _family_config("QUAD", "QUAD"))


# ---------------------------------------------------------------------------
# Backtest projection (requirement: no standalone backtest config)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("product_type, family_engines, expected", [
    ("SnowballOption", ("QUAD", "ANALYTICAL"), "quad"),
    ("KnockOutResetSnowballOption", ("QUAD", "ANALYTICAL"), "quad"),
    ("EuropeanVanillaOption", ("QUAD", "ANALYTICAL"), "analytical"),
    ("EuropeanVanillaOption", ("QUAD", "MC"), "mc"),
    ("SnowballOption", ("PDE", "ANALYTICAL"), "pde"),
    ("AsianOption", ("QUAD", "ANALYTICAL"), "analytical"),
])
def test_backtest_engine_projection(product_type, family_engines, expected):
    config = _family_config(*family_engines)
    resolved = resolve_backtest_engine(_pos(product_type), None, config)
    assert resolved["engine"] == expected


def test_backtest_projection_keeps_position_engine_first():
    resolved = resolve_backtest_engine(
        _pos("SnowballOption", "SnowballQuadEngine"), None, _family_config("MC", "MC")
    )
    assert resolved["engine"] == "quad"
    assert resolved["source"] == "position"


# ---------------------------------------------------------------------------
# Rule validation
# ---------------------------------------------------------------------------

def test_validate_rules_rejects_unknown_quantark_engine():
    with pytest.raises(ValueError):
        validate_rules({"rules": [
            {"name": "bad", "match": {"product_type": "EuropeanVanillaOption"},
             "pricing": {"engine_name": "NotARealEngine"}},
        ]})


def test_validate_rules_rejects_non_dict_engine_kwargs():
    with pytest.raises(ValueError):
        validate_rules({"rules": [
            {"name": "bad", "match": {"product_type": "EuropeanVanillaOption"},
             "pricing": {"engine_name": "BlackScholesEngine", "engine_kwargs": [1, 2]}},
        ]})


def test_validate_rules_rejects_bad_family_enum():
    with pytest.raises(ValueError):
        validate_rules({"rules": [
            {"name": "bad", "match": {"product_family": "others"},
             "pricing": {"engine_type": "TREE"}},
        ]})


def test_validate_rules_accepts_default_rules():
    from app.services.engine_configs import DEFAULT_ENGINE_CONFIG_RULES

    validate_rules(DEFAULT_ENGINE_CONFIG_RULES)


# ---------------------------------------------------------------------------
# Default config availability + lookup
# ---------------------------------------------------------------------------

def test_fresh_database_seeds_a_default_engine_config(session):
    config = get_engine_config(session, None)
    assert config is not None
    assert config.is_default
    validate_rules(config.rules)


def test_get_engine_config_unknown_id_raises(session):
    with pytest.raises(ValueError):
        get_engine_config(session, 999_999)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def test_engine_config_crud_roundtrip(client):
    created = client.post("/api/engine-configs", json={
        "name": "MC Everywhere",
        "rules": {"rules": [
            {"name": "Others", "match": {"product_family": "others"},
             "pricing": {"engine_type": "MC"}},
        ]},
    })
    assert created.status_code == 200, created.text
    config_id = created.json()["id"]

    listed = client.get("/api/engine-configs").json()
    assert any(row["id"] == config_id for row in listed)

    updated = client.put(f"/api/engine-configs/{config_id}", json={
        "name": "MC Everywhere",
        "rules": {"rules": [
            {"name": "Others", "match": {"product_family": "others"},
             "pricing": {"engine_type": "PDE"}},
        ]},
    })
    assert updated.status_code == 200
    assert updated.json()["rules"]["rules"][0]["pricing"]["engine_type"] == "PDE"

    assert client.delete(f"/api/engine-configs/{config_id}").json() == {"ok": True}


def test_create_engine_config_with_invalid_rules_is_a_400(client):
    response = client.post("/api/engine-configs", json={
        "name": "broken",
        "rules": {"rules": [{"name": "x", "match": {}, "pricing": {}}]},
    })
    assert response.status_code == 400


def test_set_default_is_exclusive(client):
    first = client.post("/api/engine-configs", json={"name": "A", "rules": {"rules": []}}).json()
    second = client.post("/api/engine-configs", json={"name": "B", "rules": {"rules": []}}).json()

    assert client.post(f"/api/engine-configs/{first['id']}/default").status_code == 200
    assert client.post(f"/api/engine-configs/{second['id']}/default").status_code == 200

    rows = client.get("/api/engine-configs").json()
    defaults = [row["id"] for row in rows if row["is_default"]]
    assert defaults == [second["id"]]


def test_deleting_the_default_config_is_refused(client):
    rows = client.get("/api/engine-configs").json()
    default_row = next(row for row in rows if row["is_default"])
    assert client.delete(f"/api/engine-configs/{default_row['id']}").status_code == 400


# ---------------------------------------------------------------------------
# Run plumbing: engine_config_id persists on queued runs
# ---------------------------------------------------------------------------

def test_queue_batch_pricing_persists_engine_config_id(session):
    from app.models import Portfolio, Position
    from app.services.batch_pricing import queue_batch_pricing
    from app.services.engine_configs import ensure_default_engine_config

    config = ensure_default_engine_config(session)
    portfolio = Portfolio(name="P", base_currency="USD")
    session.add(portfolio)
    session.flush()
    session.add(Position(
        portfolio_id=portfolio.id, underlying="AAPL", source_trade_id="T-1",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        engine_name=None, quantity=1.0, entry_price=0.0,
    ))
    session.flush()

    run, _task = queue_batch_pricing(
        session, portfolio_id=portfolio.id, engine_config_id=config.id
    )
    assert run.engine_config_id == config.id


def test_queue_functions_reject_unknown_engine_config_id(session):
    """Bad engine_config_id must fail at queue time (clean 4xx via the
    endpoints' ValueError mapping), not mid-pipeline in the worker."""
    from app.models import Portfolio, Position
    from app.services.backtest_runner import queue_backtest
    from app.services.batch_pricing import queue_batch_pricing
    from app.services.scenario_test_runner import queue_scenario_test

    portfolio = Portfolio(name="Q", base_currency="USD")
    session.add(portfolio)
    session.flush()
    session.add(Position(
        portfolio_id=portfolio.id, underlying="AAPL", source_trade_id="T-Q1",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        engine_name=None, quantity=1.0, entry_price=0.0,
    ))
    session.flush()

    with pytest.raises(ValueError, match="Engine config not found"):
        queue_batch_pricing(session, portfolio_id=portfolio.id, engine_config_id=999_999)
    with pytest.raises(ValueError, match="Engine config not found"):
        queue_scenario_test(
            session, portfolio_id=portfolio.id, engine_config_id=999_999,
            scenario_request={"predefined": ["spot_down_5"], "custom": []},
            config={},
        )
    with pytest.raises(ValueError, match="Engine config not found"):
        queue_backtest(
            session, portfolio_id=portfolio.id, engine_config_id=999_999,
            spec={"start": "2024-01-01", "end": "2024-06-01"}, config={},
        )

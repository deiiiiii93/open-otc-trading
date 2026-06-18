from types import SimpleNamespace

from app.models import ScenarioTestRun, TaskKind, TaskRun
from app.schemas import PricingEnvironmentSnapshot
from app.services import scenario_test_bridge
from app.services.quantark import ensure_quantark_path

import pytest


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


def test_scenario_test_kind_exists():
    assert TaskKind.SCENARIO_TEST.value == "scenario_test"


def test_scenario_test_run_roundtrip(session):
    from app.models import Portfolio

    pf = Portfolio(name="P1", base_currency="USD", kind="container")
    session.add(pf)
    session.flush()

    run = ScenarioTestRun(
        portfolio_id=pf.id,
        pricing_parameter_profile_id=None,
        status="queued",
        scenario_spec={"predefined": ["market_crash"]},
        config={"calculate_greeks": True},
        results={},
        excluded_positions=[],
        artifacts={},
        resolved_position_ids=[],
    )
    session.add(run)
    session.flush()

    task = TaskRun(
        kind=TaskKind.SCENARIO_TEST.value,
        status="queued",
        portfolio_id=pf.id,
        scenario_test_run_id=run.id,
    )
    session.add(task)
    session.flush()

    assert task.scenario_test_run.id == run.id
    assert run.task_runs[0].id == task.id


def _pos(pid, underlying, qty, mapping_status="manual"):
    return SimpleNamespace(
        id=pid, underlying=underlying, quantity=qty,
        mapping_status=mapping_status, mapping_error=None, status="open",
        product_type="european_vanilla", product_kwargs={"initial_price": 100.0},
        engine_name="BlackScholesEngine", engine_kwargs={},
    )


def test_excludes_unsupported_positions(monkeypatch):
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_product_for_position",
                        lambda p, m: SimpleNamespace(name="prod"))
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_engine_for_position",
                        lambda p, m: SimpleNamespace(name="engine"))
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_pricing_env",
                        lambda m: SimpleNamespace(name="env"))

    positions = [_pos(1, "AAPL", 10), _pos(2, "AAPL", 5, mapping_status="unsupported")]
    markets = {1: PricingEnvironmentSnapshot(spot=100.0),
               2: PricingEnvironmentSnapshot(spot=100.0)}

    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, markets, portfolio_name="test")

    assert len(portfolio) == 1
    assert "AAPL" in portfolio.pricing_environments
    assert excluded == [{"position_id": 2, "reason": "Position mapping status is unsupported"}]
    # Positions must be keyed by DB id (str), not QuantArk-generated UUID.
    assert str(1) in portfolio.positions


def test_build_failure_isolates_position(monkeypatch):
    """A build failure for one position must not abort the whole run."""
    call_count = {"n": 0}

    def _product(p, m):
        call_count["n"] += 1
        if p.id == 10:
            raise RuntimeError("bad product")
        return SimpleNamespace(name="prod")

    monkeypatch.setattr(scenario_test_bridge.quantark, "build_product_for_position", _product)
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_engine_for_position",
                        lambda p, m: SimpleNamespace(name="engine"))
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_pricing_env",
                        lambda m: SimpleNamespace(name="env"))

    positions = [_pos(10, "TSLA", 5), _pos(20, "TSLA", 3)]
    markets = {10: PricingEnvironmentSnapshot(spot=150.0),
               20: PricingEnvironmentSnapshot(spot=150.0)}

    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, markets, portfolio_name="iso-test")

    # Position 10 failed -> excluded; position 20 still added under DB id key.
    assert len(portfolio) == 1
    assert str(20) in portfolio.positions
    assert any(e["position_id"] == 10 and "build failed" in e["reason"] for e in excluded)


def test_zero_quantity_excluded(monkeypatch):
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_product_for_position",
                        lambda p, m: SimpleNamespace())
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_engine_for_position",
                        lambda p, m: SimpleNamespace())
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_pricing_env",
                        lambda m: SimpleNamespace())
    positions = [_pos(3, "TSLA", 0)]
    markets = {3: PricingEnvironmentSnapshot(spot=200.0)}
    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, markets, portfolio_name="t")
    assert len(portfolio) == 0
    assert excluded[0]["reason"] == "Position quantity is zero"


def test_first_seen_market_seeds_underlying_env(monkeypatch):
    seen = {}
    def _fake_env(market):
        # record which spot seeded the env, return a sentinel keyed by spot
        env = SimpleNamespace(spot=market.spot)
        seen.setdefault(market.spot, env)
        return env
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_pricing_env", _fake_env)
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_product_for_position",
                        lambda p, m: SimpleNamespace())
    monkeypatch.setattr(scenario_test_bridge.quantark, "build_engine_for_position",
                        lambda p, m: SimpleNamespace())
    positions = [_pos(1, "AAPL", 10), _pos(2, "AAPL", 5)]
    markets = {1: PricingEnvironmentSnapshot(spot=100.0),
               2: PricingEnvironmentSnapshot(spot=999.0)}  # different spot, same underlying
    portfolio, excluded = scenario_test_bridge.build_equity_portfolio(
        positions, markets, portfolio_name="t")
    assert excluded == []
    assert len(portfolio) == 2                       # both positions added
    assert portfolio.pricing_environments["AAPL"].spot == 100.0   # first-seen (pos 1) wins

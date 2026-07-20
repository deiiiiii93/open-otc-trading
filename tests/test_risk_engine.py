from __future__ import annotations

from datetime import datetime
import threading
import time

import pytest

from app.models import Portfolio, Position
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import (
    QuantArkResult,
    calculate_portfolio_risk,
    ensure_quantark_path,
)
from app.services.risk_engine import compute_position_greeks, compute_portfolio_greeks


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


def _vanilla_position(qty: float = 100.0) -> Position:
    return Position(
        id=1,
        portfolio_id=1,
        underlying="CSI500",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=qty,
        entry_price=8.0,
    )


def test_compute_position_greeks_returns_full_set_for_vanilla():
    position = _vanilla_position()
    result = compute_position_greeks(position, PricingEnvironmentSnapshot())
    assert result["ok"] is True
    for key in ("delta", "gamma", "vega", "theta", "rho", "rho_q"):
        assert key in result
        assert isinstance(result[key], float)
    # ATM call delta is positive and < 1
    assert 0.0 < result["delta"] < 1.0


def test_compute_position_greeks_trims_phoenix_coupon_barriers_with_past_ko():
    position = Position(
        id=34,
        portfolio_id=1,
        underlying="000852.SH",
        product_type="PhoenixOption",
        product_kwargs={
            "initial_price": 100.0,
            "strike": 100.0,
            "initial_date": "2026-01-01",
            "exercise_date": "2026-12-31",
            "settlement_date": "2027-01-04",
            "contract_multiplier": 1.0,
            "is_reverse": False,
            "barrier_config": {
                "ko_barrier": [105.0, 102.0, 100.0],
                "ko_rate": 0.0,
                "ko_observation_type": "DISCRETE",
                "ko_observation_schedule": {
                    "records": [
                        {
                            "observation_date": "2026-03-31",
                            "barrier": 105.0,
                            "return_rate": 0.0,
                            "is_rate_annualized": False,
                        },
                        {
                            "observation_date": "2026-06-30",
                            "barrier": 102.0,
                            "return_rate": 0.0,
                            "is_rate_annualized": False,
                        },
                        {
                            "observation_date": "2026-12-31",
                            "barrier": 100.0,
                            "return_rate": 0.0,
                            "is_rate_annualized": False,
                        },
                    ],
                    "aggregation_mode": "STOP_FIRST_HIT",
                    "frequency": "CUSTOM",
                },
                "ki_barrier": 70.0,
                "ki_observation_type": "DISCRETE",
                "ki_continuous": False,
                "ki_observation_schedule": {
                    "records": [{"observation_date": "2026-12-31", "barrier": 70.0}],
                    "aggregation_mode": "STOP_FIRST_HIT",
                    "frequency": "CUSTOM",
                },
            },
            "coupon_config": {
                "coupon_barrier": [80.0, 81.0, 82.0],
                "coupon_rate": 0.01,
                "coupon_pay_type": "INSTANT",
                "memory_coupon": False,
            },
            "payoff_config": {
                "include_principal": False,
                "participation_rate": 1.0,
                "protection_type": "NONE",
                "protection_rate": 0.0,
                "rebate_rate": 0.0,
            },
            "accrual_config": {
                "coupon_pay_type": "INSTANT",
                "is_annualized": False,
                "is_annualized_ko": False,
            },
        },
        engine_name="PhoenixQuadEngine",
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 101}},
        quantity=-1.0,
        entry_price=0.0,
    )
    market = PricingEnvironmentSnapshot(
        valuation_date=datetime(2026, 5, 8),
        spot=101.0,
        rate=0.02,
        dividend_yield=0.03,
        volatility=0.25,
        asset_name="000852.SH",
    )

    result = compute_position_greeks(position, market)

    assert result["ok"] is True, result.get("error")
    assert "Coupon barrier schedule length does not match KO observations" not in str(
        result.get("error")
    )


def test_compute_portfolio_greeks_aggregates_signed_quantities():
    long = _vanilla_position(qty=100.0)
    short = _vanilla_position(qty=-50.0)
    short.id = 2
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=[long, short])
    per_position_delta = compute_position_greeks(long, PricingEnvironmentSnapshot())[
        "delta"
    ]
    aggregated = compute_portfolio_greeks(portfolio, PricingEnvironmentSnapshot())
    # long: 100 * delta, short: -50 * delta → net = 50 * delta (positions identical except quantity)
    assert aggregated["delta"] == pytest.approx(50.0 * per_position_delta, rel=1e-6)


def test_calculate_portfolio_risk_exposes_position_trade_id_and_greek_contributions():
    position = _vanilla_position(qty=5.0)
    position.source_trade_id = "T-VANILLA"
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=[position])

    risk = calculate_portfolio_risk(portfolio, PricingEnvironmentSnapshot())

    row = risk["positions"][0]
    assert row["source_trade_id"] == "T-VANILLA"
    assert row["position_id"] == position.id
    for greek in ("delta", "gamma", "vega", "theta", "rho", "rho_q"):
        assert greek in row
        assert isinstance(row[greek], float)
        assert risk["totals"][greek] == pytest.approx(row[greek])
    for greek in ("delta_cash", "gamma_cash"):
        assert greek in row
        assert isinstance(row[greek], float)
        assert risk["totals"][greek] == pytest.approx(row[greek])
    assert row["greeks_ok"] is True


def test_calculate_portfolio_risk_excludes_implausible_model_outputs(monkeypatch):
    position = _vanilla_position(qty=1.0)
    position.product_kwargs["contract_multiplier"] = 100.0
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=[position])

    def huge_price(*args, **kwargs):
        return QuantArkResult(
            ok=True,
            data={"price": 1e281, "engine": "TestEngine", "product_type": "Test"},
        )

    monkeypatch.setattr("app.services.quantark.price_product", huge_price)

    risk = calculate_portfolio_risk(portfolio, PricingEnvironmentSnapshot())

    assert risk["totals"]["market_value"] == 0.0
    assert risk["totals"]["pnl"] == 0.0
    assert risk["positions"][0]["pricing_ok"] is False
    assert "implausible" in risk["positions"][0]["pricing_error"]


def test_calculate_portfolio_risk_prices_positions_in_parallel(monkeypatch):
    calls: list[int] = []
    lock = threading.Lock()

    def fake_price(product_type, product_kwargs, market, engine_name, engine_kwargs):
        with lock:
            calls.append(threading.get_ident())
        time.sleep(0.05)
        return QuantArkResult(
            ok=True,
            data={"price": 10.0, "engine": engine_name, "product_type": product_type},
        )

    def fake_greeks(position, market):
        return {
            "ok": True,
            "delta": 1.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }

    monkeypatch.setattr("app.services.quantark.price_product", fake_price)
    monkeypatch.setattr("app.services.risk_engine.compute_position_greeks", fake_greeks)
    positions = []
    for index in range(4):
        position = _vanilla_position(qty=1.0)
        position.id = index + 1
        positions.append(position)
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=positions)

    risk = calculate_portfolio_risk(
        portfolio, PricingEnvironmentSnapshot(), max_workers=4
    )

    assert [row["position_id"] for row in risk["positions"]] == [1, 2, 3, 4]
    assert len(set(calls)) > 1
    assert risk["totals"]["market_value"] == pytest.approx(40.0)


def test_calculate_portfolio_risk_uses_futures_market_price_and_multiplier(monkeypatch):
    position = Position(
        id=113,
        portfolio_id=1,
        underlying="IF2606.CFFEX",
        product_type="Futures",
        product_kwargs={"underlying": "IF2606.CFFEX", "multiplier": 300.0, "maturity": 0.25},
        engine_name="DeltaOneEngine",
        engine_kwargs={},
        quantity=4.0,
        entry_price=0.0,
    )
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=[position])

    def fake_price(product_type, product_kwargs, market, engine_name, engine_kwargs):
        assert product_type == "Futures"
        assert product_kwargs["market_price"] == 4913.8
        assert engine_kwargs["use_market_price"] is True
        return QuantArkResult(ok=True, data={"price": product_kwargs["market_price"], "engine": engine_name})

    monkeypatch.setattr("app.services.quantark.price_product", fake_price)
    monkeypatch.setattr(
        "app.services.risk_engine.compute_position_greeks",
        lambda position, market: {
            "ok": True,
            "delta": 1.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        },
    )

    risk = calculate_portfolio_risk(
        portfolio,
        PricingEnvironmentSnapshot(spot=4913.8),
    )

    row = risk["positions"][0]
    assert row["price"] == 4913.8
    assert row["market_value"] == pytest.approx(4913.8 * 4.0 * 300.0)
    assert row["delta"] == pytest.approx(4.0)
    assert row["delta_cash"] == pytest.approx(4913.8 * 4.0 * 300.0)
    assert risk["totals"]["market_value"] == pytest.approx(4913.8 * 4.0 * 300.0)
    assert risk["totals"]["delta_cash"] == pytest.approx(4913.8 * 4.0 * 300.0)


def test_calculate_portfolio_risk_uses_exact_greeks_for_quad_engines(monkeypatch):
    position = _vanilla_position(qty=2.0)
    position.product_type = "SnowballOption"
    position.engine_name = "SnowballQuadEngine"
    position.engine_kwargs = {
        "params_type": "quad_params",
        "params_kwargs": {"grid_points": 101},
    }
    portfolio = Portfolio(id=1, name="t", base_currency="CNY", positions=[position])

    def fake_price(product_type, product_kwargs, market, engine_name, engine_kwargs):
        return QuantArkResult(
            ok=True,
            data={"price": 10.0, "engine": engine_name, "product_type": product_type},
        )

    greek_calls = []

    def fake_greeks(position, market):
        greek_calls.append((position.engine_name, position.product_type))
        return {
            "ok": True,
            "delta": 0.25,
            "gamma": 0.01,
            "vega": 0.5,
            "theta": -0.02,
            "rho": 0.03,
            "rho_q": 0.04,
        }

    monkeypatch.setattr("app.services.quantark.price_product", fake_price)
    monkeypatch.setattr("app.services.risk_engine.compute_position_greeks", fake_greeks)

    risk = calculate_portfolio_risk(
        portfolio, PricingEnvironmentSnapshot(), max_workers=1
    )

    row = risk["positions"][0]
    assert greek_calls == [("SnowballQuadEngine", "SnowballOption")]
    assert row["greeks_ok"] is True
    assert row["greeks_error"] is None
    assert row["delta"] == pytest.approx(0.5)
    assert row["gamma"] == pytest.approx(0.02)
    assert row["delta_cash"] == pytest.approx(50.0)
    assert row["gamma_cash"] == pytest.approx(2.0)
    assert risk["totals"]["delta"] == pytest.approx(0.5)
    assert risk["totals"]["gamma"] == pytest.approx(0.02)
    assert risk["totals"]["delta_cash"] == pytest.approx(50.0)
    assert risk["totals"]["gamma_cash"] == pytest.approx(2.0)


def test_risk_run_view_portfolio_records_resolved_ids(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import PortfolioKind, Portfolio, Position
    from app.services.risk_engine import run_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        c = Portfolio(name="C", base_currency="USD", kind=PortfolioKind.CONTAINER.value)
        session.add(c)
        session.flush()
        pos = Position(
            portfolio_id=c.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        session.add(pos)
        session.flush()
        v = Portfolio(
            name="V",
            base_currency="USD",
            kind=PortfolioKind.VIEW.value,
            filter_rule={"op": "eq", "field": "underlying", "value": "AAPL"},
        )
        session.add(v)
        session.flush()
        run = run_portfolio_risk(session, portfolio_id=v.id, method="summary")
        session.commit()
        assert run.resolved_position_ids == [pos.id]


def test_risk_run_can_scope_to_position_ids(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services import risk_engine
    from app.services.risk_engine import run_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    captured: dict[str, list[int]] = {}

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        captured["position_ids"] = [position.id for position in portfolio.positions]
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "pricing_ok": True,
                    "greeks_ok": True,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        risk_engine,
        "calculate_portfolio_risk",
        fake_calculate_portfolio_risk,
    )
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        pos_a = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
        )
        pos_b = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0},
            quantity=2.0,
        )
        session.add_all([pos_a, pos_b])
        session.flush()

        run = run_portfolio_risk(
            session,
            portfolio_id=portfolio.id,
            method="summary",
            position_ids=[pos_b.id],
        )

        assert captured["position_ids"] == [pos_b.id]
        assert run.resolved_position_ids == [pos_b.id]
        from app.services.hedging_greeks import resolved_position_set_hash

        assert run.metrics["position_set_hash"] == resolved_position_set_hash([pos_b])


def test_risk_run_rejects_position_ids_outside_portfolio(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.risk_engine import run_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        other = Portfolio(name="Other", base_currency="USD")
        session.add_all([portfolio, other])
        session.flush()
        outside = Position(
            portfolio_id=other.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0},
            quantity=2.0,
        )
        session.add(outside)
        session.flush()

        with pytest.raises(ValueError, match="not in portfolio"):
            run_portfolio_risk(
                session,
                portfolio_id=portfolio.id,
                method="summary",
                position_ids=[outside.id],
            )


def test_risk_run_uses_exact_trade_profile_row_before_underlying(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import (
        Instrument,
        Portfolio,
        Position,
        PricingParameterProfile,
        PricingParameterRow,
    )
    from app.services.quotes import record_quote
    from app.services.risk_engine import run_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    captured: dict[str, float] = {}

    def fake_price(product_type, product_kwargs, market, engine_name, engine_kwargs):
        captured["spot"] = market.spot
        captured["volatility"] = market.volatility
        return QuantArkResult(
            ok=True,
            data={"price": 10.0, "engine": engine_name, "product_type": product_type},
        )

    def fake_greeks(position, market):
        return {
            "ok": True,
            "delta": 1.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }

    monkeypatch.setattr("app.services.quantark.price_product", fake_price)
    monkeypatch.setattr("app.services.risk_engine.compute_position_greeks", fake_greeks)

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol="AAPL", kind="stock", status="active")
        session.add(instrument)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            source_trade_id="MANUAL-1",
            underlying="AAPL",
            underlying_id=instrument.id,
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        profile = PricingParameterProfile(
            name="Mixed Profile",
            valuation_date=datetime(2026, 4, 30),
            source_type="xlsx",
            status="completed",
            summary={"row_count": 2},
        )
        session.add_all([position, profile])
        session.flush()
        # Spot is observation-only — one recorded quote drives the spot slot;
        # the exact-vs-underlying row precedence now governs r/q/vol (here: vol).
        record_quote(session, instrument_id=instrument.id, price=111.0,
                     as_of=datetime(2026, 4, 30), source="xlsx_import", price_type="mid")
        session.add_all([
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="MANUAL-1",
                symbol="AAPL",
                instrument_id=instrument.id,
                rate=0.01,
                dividend_yield=0.02,
                volatility=0.33,  # exact trade row
            ),
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="UNDERLYING-AAPL",
                symbol="AAPL",
                instrument_id=instrument.id,
                rate=0.01,
                dividend_yield=0.02,
                volatility=0.99,  # underlying row — must NOT be chosen
            ),
        ])
        session.flush()

        run = run_portfolio_risk(
            session,
            portfolio_id=portfolio.id,
            pricing_parameter_profile_id=profile.id,
        )

        row = run.metrics["positions"][0]
        assert captured["spot"] == 111.0
        assert captured["volatility"] == 0.33  # exact trade row wins over underlying
        assert row["pricing_ok"] is True
        assert row["pricing_parameter_match_type"] == "trade_id"
        assert row["pricing_parameter_profile_id"] == profile.id


def test_risk_run_uses_unique_complete_underlying_profile_row(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import (
        Instrument,
        Portfolio,
        Position,
        PricingParameterProfile,
        PricingParameterRow,
    )
    from app.services.quotes import record_quote
    from app.services.risk_engine import run_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    captured: dict[str, float] = {}

    def fake_price(product_type, product_kwargs, market, engine_name, engine_kwargs):
        captured["spot"] = market.spot
        captured["rate"] = market.rate
        captured["dividend_yield"] = market.dividend_yield
        captured["volatility"] = market.volatility
        return QuantArkResult(
            ok=True,
            data={"price": 10.0, "engine": engine_name, "product_type": product_type},
        )

    def fake_greeks(position, market):
        return {
            "ok": True,
            "delta": 1.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "rho": 0.0,
            "rho_q": 0.0,
        }

    monkeypatch.setattr("app.services.quantark.price_product", fake_price)
    monkeypatch.setattr("app.services.risk_engine.compute_position_greeks", fake_greeks)

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol="AAPL", kind="stock", status="active")
        session.add(instrument)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            source_trade_id="MANUAL-1",
            underlying="AAPL",
            underlying_id=instrument.id,
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        profile = PricingParameterProfile(
            name="Underlying Profile",
            valuation_date=datetime(2026, 4, 30),
            source_type="underlying",
            status="completed",
            summary={"row_count": 1},
        )
        session.add_all([position, profile])
        session.flush()
        # Spot is observation-only; r/q/vol come from the unique-complete underlying row.
        record_quote(session, instrument_id=instrument.id, price=222.0,
                     as_of=datetime(2026, 4, 30), source="xlsx_import", price_type="mid")
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="UNDERLYING-AAPL",
                symbol="AAPL",
                instrument_id=instrument.id,
                rate=0.01,
                dividend_yield=0.02,
                volatility=0.33,
            )
        )
        session.flush()

        run = run_portfolio_risk(
            session,
            portfolio_id=portfolio.id,
            pricing_parameter_profile_id=profile.id,
        )

        row = run.metrics["positions"][0]
        assert captured == {
            "spot": 222.0,
            "rate": 0.01,
            "dividend_yield": 0.02,
            "volatility": 0.33,
        }
        assert row["pricing_ok"] is True
        assert row["pricing_parameter_match_type"] == "underlying"
        assert row["pricing_parameter_profile_id"] == profile.id
        assert row["delta_cash"] == pytest.approx(222.0)


@pytest.mark.parametrize(
    ("profile_rows", "match_type", "error_fragment"),
    [
        ([], "missing", "no exact trade row or unique complete underlying row"),
        (
            [
                {
                    "source_trade_id": "UNDERLYING-AAPL",
                    "symbol": "AAPL",
                    "rate": None,  # incomplete on a REAL remaining field (spot left the row)
                    "dividend_yield": 0.02,
                    "volatility": 0.33,
                }
            ],
            "incomplete",
            "missing rate",
        ),
        (
            [
                {
                    "source_trade_id": "UNDERLYING-AAPL-1",
                    "symbol": "AAPL",
                    "rate": 0.01,
                    "dividend_yield": 0.02,
                    "volatility": 0.33,
                },
                {
                    "source_trade_id": "UNDERLYING-AAPL-2",
                    "symbol": "AAPL",
                    "rate": 0.01,
                    "dividend_yield": 0.02,
                    "volatility": 0.99,  # second complete row -> ambiguous (was spot-distinguished)
                },
            ],
            "ambiguous",
            "multiple complete rows",
        ),
    ],
)
def test_risk_run_fails_when_profile_cannot_extract_pricing_params(
    tmp_path,
    monkeypatch,
    profile_rows,
    match_type,
    error_fragment,
):
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, PricingParameterProfile, PricingParameterRow
    from app.services.risk_engine import run_portfolio_risk

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    def fake_price(*args, **kwargs):
        raise AssertionError("Risk should not price unmatched profile rows with defaults")

    monkeypatch.setattr("app.services.quantark.price_product", fake_price)

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            source_trade_id="MANUAL-1",
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        profile = PricingParameterProfile(
            name="Profile",
            valuation_date=datetime(2026, 4, 30),
            source_type="xlsx",
            status="completed",
            summary={"row_count": len(profile_rows)},
        )
        session.add_all([position, profile])
        session.flush()
        for raw_row in profile_rows:
            session.add(PricingParameterRow(profile_id=profile.id, **raw_row))
        session.flush()

        run = run_portfolio_risk(
            session,
            portfolio_id=portfolio.id,
            pricing_parameter_profile_id=profile.id,
        )

        row = run.metrics["positions"][0]
        assert run.status == "completed_with_errors"
        assert row["pricing_ok"] is False
        assert row["greeks_ok"] is False
        assert row["pricing_parameter_match_type"] == match_type
        assert row["pricing_parameter_profile_id"] == profile.id
        assert error_fragment in row["pricing_error"]
        assert run.metrics["totals"]["market_value"] == 0.0


def test_pricing_context_skips_param_failure_for_delta_one_under_profile(
    tmp_path, monkeypatch
):
    """Delta-one (futures/spot) positions consume no rate/dividend/vol params, so a
    profile with no matching row must NOT record a pricing failure for them — while
    an option in the same call still fails loudly (no blanket suppression)."""
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, PricingParameterProfile
    from app.services.risk_engine import _pricing_position_context

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        future = Position(
            portfolio_id=portfolio.id,
            source_trade_id="FUT-1",
            underlying="IC2606.CFE",
            product_type="Futures",
            product_kwargs={"maturity": 0.25, "multiplier": 200.0, "underlying": "CSI500"},
            engine_name="DeltaOneEngine",
            quantity=-2.0,
            entry_price=8362.16,
        )
        option = Position(
            portfolio_id=portfolio.id,
            source_trade_id="OPT-1",
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        # Profile with NO rows -> neither underlying can extract params.
        profile = PricingParameterProfile(
            name="Empty",
            valuation_date=datetime(2026, 4, 30),
            source_type="xlsx",
            status="completed",
            summary={"row_count": 0},
        )
        session.add_all([future, option, profile])
        session.flush()

        markets, failures, diagnostics = _pricing_position_context(
            session,
            [future, option],
            pricing_parameter_profile_id=profile.id,
        )

        # Delta-one future: market built, NO failure, diagnostic flag stamped.
        assert future.id in markets
        assert future.id not in failures
        assert diagnostics[future.id]["pricing_params_required"] is False
        # Option: still fails loudly (params genuinely required and missing).
        assert option.id in failures
        assert (
            "cannot extract pricing parameters"
            in failures[option.id]["pricing_error"]
        )


def test_compute_position_greeks_respects_engine_kwargs_override(monkeypatch):
    from types import SimpleNamespace
    from app.services import risk_engine

    captured: dict = {}

    def fake_build_engine_for_position(position, market):
        captured["engine_kwargs"] = dict(position.engine_kwargs or {})
        return SimpleNamespace()

    def fake_build_product_for_position(position, market):
        return SimpleNamespace()

    def fake_build_pricing_env(market):
        return SimpleNamespace()

    class FakeGreeksCalculator:
        def calculate(self, product, env, engine, method):
            return {"delta": 0.5, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
                    "rho": 0.0, "dividend_rho": 0.0}

    monkeypatch.setattr(risk_engine, "build_engine_for_position", fake_build_engine_for_position)
    monkeypatch.setattr(risk_engine, "build_product_for_position", fake_build_product_for_position)
    monkeypatch.setattr(risk_engine, "build_pricing_env", fake_build_pricing_env)
    monkeypatch.setattr(
        "quantark.asset.equity.riskmeasures.greeks_calculator.GreeksCalculator",
        FakeGreeksCalculator,
        raising=False,
    )

    position = SimpleNamespace(
        product_type="SnowballOption",
        product_kwargs={},
        engine_name="SnowballQuadEngine",
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 1001}},
        quantity=1.0,
        status="open",
        mapping_status="supported",
        mapping_error=None,
        source_payload={},
    )
    market = SimpleNamespace()

    out = risk_engine.compute_position_greeks(
        position,
        market,
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 201}},
    )

    assert out["ok"] is True
    assert captured["engine_kwargs"]["params_kwargs"]["grid_points"] == 201


def test_risk_run_completed_with_errors_records_result_payload(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import Portfolio, Position, TaskRun
    from app.schemas import TaskRunOut
    from app.services import batch_pricing
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "underlying": position.underlying,
                    "product_type": position.product_type,
                    "pricing_ok": False,
                    "pricing_error": "Pricing profile extraction failed: missing vol",
                    "greeks_ok": True,
                    "greeks_error": None,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        batch_pricing, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=1.0,
        )
        session.add(pos)
        session.flush()
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()
        run_id, task_id, pos_id = run.id, task.id, pos.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        assert task.status == "completed_with_errors"
        payload = task.result_payload or {}
        positions = payload.get("errors", {}).get("positions", [])
        assert len(positions) == 1
        assert positions[0]["position_id"] == pos_id
        assert (
            positions[0]["pricing_error"]
            == "Pricing profile extraction failed: missing vol"
        )
        # API contract exposes result_payload
        out = TaskRunOut.model_validate(task)
        assert out.result_payload["errors"]["failed_count"] == 1


def _risk_db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _capture_calculate(monkeypatch):
    from app.services import risk_engine

    captured: dict[str, list[int]] = {}

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        captured["position_ids"] = [position.id for position in portfolio.positions]
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "pricing_ok": True,
                    "greeks_ok": True,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        risk_engine,
        "calculate_portfolio_risk",
        fake_calculate_portfolio_risk,
    )
    return captured


def test_risk_run_excludes_closed_positions(tmp_path, monkeypatch):
    """A closed position must not enter the run at all — no metrics row, not in
    resolved_position_ids, and (crucially) it cannot poison run status: a run
    whose only 'problem' is a closed position completes plainly."""
    from app.services.risk_engine import run_portfolio_risk

    database = _risk_db(tmp_path, monkeypatch)
    captured = _capture_calculate(monkeypatch)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        open_pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="open",
        )
        closed_pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="closed",
        )
        session.add_all([open_pos, closed_pos])
        session.flush()

        run = run_portfolio_risk(session, portfolio_id=portfolio.id, method="summary")

        assert captured["position_ids"] == [open_pos.id]
        assert run.resolved_position_ids == [open_pos.id]
        assert run.status == "completed"


def test_risk_run_silently_drops_closed_explicit_ids(tmp_path, monkeypatch):
    """Explicit position_ids naming a closed position: the closed id is
    silently filtered (user decision 2026-06-05), the rest run; foreign ids
    still raise (covered by test_risk_run_rejects_position_ids_outside_portfolio)."""
    from app.services.risk_engine import run_portfolio_risk

    database = _risk_db(tmp_path, monkeypatch)
    captured = _capture_calculate(monkeypatch)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        open_pos = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="open",
        )
        closed_pos = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0},
            quantity=2.0,
            status="closed",
        )
        session.add_all([open_pos, closed_pos])
        session.flush()

        run = run_portfolio_risk(
            session,
            portfolio_id=portfolio.id,
            method="summary",
            position_ids=[open_pos.id, closed_pos.id],
        )

        assert captured["position_ids"] == [open_pos.id]
        assert run.resolved_position_ids == [open_pos.id]


def test_risk_run_excludes_terminal_source_state(tmp_path, monkeypatch):
    """status='open' but source row says 敲出 (knocked out): economically
    closed, excluded the same way (closed_position_exclusion covers both)."""
    from app.services.risk_engine import run_portfolio_risk

    database = _risk_db(tmp_path, monkeypatch)
    captured = _capture_calculate(monkeypatch)
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="P", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        live = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0},
            quantity=1.0,
            status="open",
        )
        knocked_out = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            product_type="SnowballOption",
            product_kwargs={},
            quantity=1.0,
            status="open",
            source_payload={"row": {"Trade Status": "Knocked Out"}},
        )
        session.add_all([live, knocked_out])
        session.flush()

        run = run_portfolio_risk(session, portfolio_id=portfolio.id, method="summary")

        assert captured["position_ids"] == [live.id]
        assert run.resolved_position_ids == [live.id]

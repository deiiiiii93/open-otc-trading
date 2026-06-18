from __future__ import annotations

import json
import importlib
from datetime import datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from openpyxl import Workbook
import sqlalchemy as sa

from app import database
from app.cli import main as cli_main
from app.config import Settings
from app.models import (
    AssumptionRow,
    AssumptionSet,
    Instrument,
    Portfolio,
    Position,
    PositionBarrierState,
    PositionValuationResult,
    PricingParameterProfile,
    PricingParameterRow,
    SnowballTerm,
)
from app.tools import QUANT_AGENT_TOOLS
from app.services.position_adapter import (
    import_positions_from_xlsx,
    map_trade_row,
    parse_accrual_day_count_list,
    parse_accrual_factor_list,
)
from app.services.position_pricer import (
    MarketOverrides,
    price_portfolio_positions,
)
from app.services.pricing_profiles import (
    import_pricing_parameter_profile_from_xlsx,
    pricing_rows_for_profile,
)
from app.services.quotes import latest_quote, record_quote
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import QuantArkResult, _drop_past_observations, price_product
from app.services.domains import pricing_profiles as pricing_profiles_domain


TRADE_HEADERS = [
    "Structure Type",
    "Option Type",
    "Direction",
    "Underlying Code",
    "Trade ID",
    "Trade Status",
    "Start Date",
    "Final Observation Date",
    "Maturity Date",
    "Settlement Date",
    "Initial Notional",
    "Notional",
    "Notional Unit",
    "Initial Price",
    "Strike Price",
    "Participation Rate",
    "Coupon Rate",
    "Knock-Out Barrier",
    "Knock-Out Coupon",
    "Knock-Out Observation Dates",
    "Knock-Out Day Counts",
    "Knock-In Barrier",
    "Already Knocked In",
    "Custom Structure",
    "Dividend Coupon",
    "Knock-In Min Return Rate",
    "Annualized",
    "Knock-In Annualized",
    "Knock-Out/Coupon Observation Dates",
    "Day-Count Factors",
    "Coupon Barrier",
    "Coupon Barrier Rate",
    "Currency",
]


def configure_test_db(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()
    return database.SessionLocal()


def write_trade_workbook(path: Path, rows: list[dict[str, object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Positions"
    worksheet.append(TRADE_HEADERS)
    for row in rows:
        worksheet.append([row.get(header) for header in TRADE_HEADERS])
    workbook.save(path)


def write_market_workbook(path: Path, trade_ids: list[str], *, spot: float | None = None) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "SheetJS"
    headers = ["Trade ID", "Underlying Code", "Underlying Price", "Volatility", "Risk-Free Rate", "Dividend/Borrow Yield"]
    worksheet.append(headers)
    for trade_id in trade_ids:
        worksheet.append([trade_id, "000852.SH - 中证1000指数", spot, 0.22, 0.02, 0.03])
    workbook.save(path)


def vanilla_row(trade_id: str = "T-VANILLA") -> dict[str, object]:
    return {
        "Structure Type": "European Vanilla",
        "Option Type": "Call",
        "Direction": "Sell",
        "Underlying Code": "000852.SH",
        "Trade ID": trade_id,
        "Trade Status": "Open",
        "Start Date": datetime(2026, 1, 1),
        "Final Observation Date": datetime(2026, 12, 31),
        "Maturity Date": datetime(2026, 12, 31),
        "Settlement Date": datetime(2027, 1, 4),
        "Initial Notional": 1_000_000,
        "Notional": 1_000_000,
        "Notional Unit": "CNY",
        "Initial Price": 100.0,
        "Strike Price": 100.0,
        "Participation Rate": 1.0,
        "Annualized": "No",
    }


def shark_row() -> dict[str, object]:
    row = vanilla_row("T-SHARK")
    row["Structure Type"] = "Single Sharkfin"
    row["Knock-Out Barrier"] = 120.0
    row["Knock-Out Coupon"] = "2%"
    row["Coupon Rate"] = "0.5%"
    row["Knock-Out Observation Dates"] = "2026/06/30,2026/12/31"
    return row


def double_shark_row() -> dict[str, object]:
    row = vanilla_row("T-DOUBLE-SHARK")
    row["Structure Type"] = "Double Sharkfin"
    row["Knock-Out Barrier"] = 120.0
    row["Knock-In Barrier"] = 80.0
    row["Knock-Out Coupon"] = "1.5%"
    row["Coupon Rate"] = "0.25%"
    row["Knock-Out Observation Dates"] = "2026/06/30,2026/12/31"
    return row


def unsupported_row() -> dict[str, object]:
    row = vanilla_row("T-UNSUPPORTED")
    row["Structure Type"] = "Pending Structure"
    return row


def snowball_row(trade_id: str = "T-SNOWBALL") -> dict[str, object]:
    row = vanilla_row(trade_id)
    row.update(
        {
            "Structure Type": "Snowball (No Protection)",
            "Option Type": None,
            "Final Observation Date": datetime(2026, 12, 31),
            "Knock-Out Barrier": "105,100",
            "Knock-Out Coupon": "10%,10%",
            "Knock-Out Observation Dates": "2026/06/30,2026/12/31",
            "Knock-Out Day Counts": "183,365",
            "Knock-In Barrier": 70.0,
            "Already Knocked In": "No",
            "Dividend Coupon": 0.03,
            "Annualized": "Yes",
            "Knock-In Annualized": "No",
        }
    )
    return row


def phoenix_row(trade_id: str = "T-PHOENIX") -> dict[str, object]:
    row = snowball_row()
    row.update(
        {
            "Structure Type": "Phoenix (No Protection)",
            "Trade ID": trade_id,
            "Knock-Out/Coupon Observation Dates": "2026/06/30,2026/12/31",
            "Day-Count Factors": "30/30,30/360",
            "Coupon Barrier": "80,80",
            "Coupon Barrier Rate": "1%,1%",
        }
    )
    return row


def test_adapter_parses_rates_lists_and_maps_products():
    mapping = map_trade_row(snowball_row())

    assert mapping.mapping_status == "supported"
    assert mapping.product_type == "SnowballOption"
    assert mapping.engine_name == "SnowballQuadEngine"
    assert mapping.product_kwargs["barrier_config"]["ko_barrier"] == [105.0, 100.0]
    assert mapping.product_kwargs["barrier_config"]["ko_observation_schedule"]["records"][0]["return_rate"] == 0.10
    assert mapping.product_kwargs["barrier_config"]["ki_observation_type"] == "DISCRETE"
    assert mapping.product_kwargs["_otc_ki_observation_convention"] == "DAILY"
    assert mapping.product_kwargs["barrier_config"]["ki_observation_schedule"]["frequency"] == "DAILY"
    ki_dates = {
        record["observation_date"]
        for record in mapping.product_kwargs["barrier_config"]["ki_observation_schedule"]["records"]
    }
    assert "2026-05-01" not in ki_dates
    assert "2026-05-02" not in ki_dates
    assert "2026-05-03" not in ki_dates
    assert "2026-05-06" in ki_dates
    assert mapping.product_kwargs["barrier_config"]["ki_continuous"] is False
    assert mapping.product_kwargs["accrual_config"]["is_annualized"] is True
    assert mapping.product_kwargs["accrual_config"]["is_annualized_ki"] is False
    assert mapping.product_kwargs["accrual_config"]["accrual_factors"] == [183 / 365, 1.0]

    shark = map_trade_row(shark_row())
    assert shark.mapping_status == "supported"
    assert shark.product_type == "SingleSharkfinOption"
    assert shark.engine_name == "SingleSharkfinOptionAnalyticalEngine"
    assert shark.product_kwargs["barrier"] == 120.0
    assert shark.product_kwargs["knock_out_rebate"] == 2.0
    assert shark.product_kwargs["no_hit_rebate"] == 0.5
    assert shark.product_kwargs["observation_type"] == "DISCRETE"
    assert shark.product_kwargs["observation_schedule"]["records"][0] == {
        "observation_date": "2026-06-30",
        "barrier": 120.0,
        "payoff": 2.0,
    }

    double_shark = map_trade_row(double_shark_row())
    assert double_shark.mapping_status == "supported"
    assert double_shark.product_type == "DoubleSharkfinOption"
    assert double_shark.engine_name == "DoubleSharkfinOptionAnalyticalEngine"
    assert double_shark.product_kwargs["lower_barrier"] == 80.0
    assert double_shark.product_kwargs["upper_barrier"] == 120.0
    assert double_shark.product_kwargs["observation_schedule"]["records"][0] == {
        "observation_date": "2026-06-30",
        "lower_barrier": 80.0,
        "upper_barrier": 120.0,
        "payoff": 1.5,
    }

    unsupported = map_trade_row(unsupported_row())
    assert unsupported.mapping_status == "unsupported"
    assert "Unsupported structure type" in unsupported.mapping_error


def test_adapter_maps_ki_annualization_from_dedicated_field():
    row = snowball_row()
    row["Annualized"] = "No"
    row["Knock-In Annualized"] = "Yes"

    mapping = map_trade_row(row)

    assert mapping.product_kwargs["accrual_config"]["is_annualized"] is False
    assert mapping.product_kwargs["accrual_config"]["is_annualized_ko"] is False
    assert mapping.product_kwargs["accrual_config"]["is_annualized_ki"] is True
    assert mapping.product_kwargs["accrual_config"]["is_annualized_rebate"] is False


def test_adapter_maps_external_accrual_factors_by_autocallable_product():
    snowball_mapping = map_trade_row(snowball_row())
    assert snowball_mapping.product_kwargs["accrual_config"]["accrual_factors"] == [183 / 365, 1.0]

    phoenix_mapping = map_trade_row(phoenix_row())
    assert phoenix_mapping.product_type == "PhoenixOption"
    assert phoenix_mapping.product_kwargs["accrual_config"]["accrual_factors"] == [1.0, 30 / 360]


def test_accrual_factor_parsers_translate_xlsx_formats():
    assert parse_accrual_factor_list("[30/30, 30/360, 0.25]") == [1.0, 30 / 360, 0.25]
    assert parse_accrual_day_count_list("[32, 64]") == [32 / 365, 64 / 365]


def test_autocallable_accrual_factors_trim_with_past_ko_observations():
    row = snowball_row()
    row["Knock-Out Observation Dates"] = "2026/04/29,2026/04/30,2026/05/06,2026/05/07,2026/05/08,2026/05/11"
    row["Knock-Out Day Counts"] = "91,92,98,99,100,103"
    row["Knock-Out Barrier"] = "105,104,103,102,101,100"
    row["Knock-Out Coupon"] = "15%,15%,15%,15%,15%,15%"
    mapping = map_trade_row(row)

    filtered = _drop_past_observations(mapping.product_kwargs, datetime(2026, 5, 8))

    records = filtered["barrier_config"]["ko_observation_schedule"]["records"]
    assert [record["observation_date"] for record in records] == ["2026-05-08", "2026-05-11"]
    assert filtered["barrier_config"]["ko_barrier"] == [101.0, 100.0]
    assert filtered["barrier_config"]["ko_rate"] == [0.15, 0.15]
    assert filtered["accrual_config"]["accrual_factors"] == [100 / 365, 103 / 365]


def test_phoenix_coupon_barriers_trim_with_past_ko_observations():
    row = phoenix_row()
    row["Knock-Out/Coupon Observation Dates"] = "2026/04/29,2026/04/30,2026/05/08,2026/05/11"
    row["Knock-Out Barrier"] = "105,104,101,100"
    row["Knock-Out Coupon"] = "0%,0%,0%,0%"
    row["Day-Count Factors"] = "30/30,30/30,30/30,30/30"
    row["Coupon Barrier"] = "80,81,82,83"
    row["Coupon Barrier Rate"] = "1%,1%,1%,1%"
    mapping = map_trade_row(row)

    filtered = _drop_past_observations(mapping.product_kwargs, datetime(2026, 5, 8))

    records = filtered["barrier_config"]["ko_observation_schedule"]["records"]
    assert [record["observation_date"] for record in records] == ["2026-05-08", "2026-05-11"]
    assert filtered["barrier_config"]["ko_barrier"] == [101.0, 100.0]
    assert filtered["barrier_config"]["ko_rate"] == [0.0, 0.0]
    assert filtered["coupon_config"]["coupon_barrier"] == [82.0, 83.0]
    assert filtered["accrual_config"]["accrual_factors"] == [1.0, 1.0]


def test_adapter_classifies_autocallable_knock_in_terms_from_custom_structure():
    european_ki = snowball_row()
    european_ki["Custom Structure"] = "European Knock-In"
    european_mapping = map_trade_row(european_ki)
    assert european_mapping.product_kwargs["barrier_config"]["ki_observation_type"] == "DISCRETE"
    assert european_mapping.product_kwargs["_otc_ki_observation_convention"] == "EUROPEAN"
    assert european_mapping.product_kwargs["barrier_config"]["ki_observation_schedule"]["records"] == [
        {"observation_date": "2026-12-31", "barrier": 70.0}
    ]
    assert european_mapping.product_kwargs["barrier_config"]["ki_continuous"] is False
    assert european_mapping.product_kwargs["_otc_lifecycle_knocked_in"] is False

    no_ki = snowball_row()
    no_ki["Custom Structure"] = "No Knock-In"
    no_ki_mapping = map_trade_row(no_ki)
    assert no_ki_mapping.product_kwargs["barrier_config"]["ki_observation_type"] == "DISCRETE"
    assert no_ki_mapping.product_kwargs["_otc_ki_observation_convention"] == "NONE"
    assert no_ki_mapping.product_kwargs["barrier_config"]["ki_barrier"] is None
    assert "ki_observation_schedule" not in no_ki_mapping.product_kwargs["barrier_config"]
    assert no_ki_mapping.product_kwargs["barrier_config"]["ki_continuous"] is False
    assert no_ki_mapping.product_kwargs["_otc_lifecycle_knocked_in"] is True

    phoenix = phoenix_row()
    phoenix["Custom Structure"] = "European Knock-In"
    phoenix_mapping = map_trade_row(phoenix)
    assert phoenix_mapping.product_type == "PhoenixOption"
    assert phoenix_mapping.product_kwargs["barrier_config"]["ki_observation_type"] == "DISCRETE"
    assert phoenix_mapping.product_kwargs["_otc_ki_observation_convention"] == "EUROPEAN"
    assert phoenix_mapping.product_kwargs["barrier_config"]["ki_continuous"] is False


def test_autocallable_knock_in_terms_build_quantark_products():
    market = PricingEnvironmentSnapshot(
        valuation_date=datetime(2026, 4, 30),
        spot=101.0,
        volatility=0.25,
        rate=0.02,
        dividend_yield=0.03,
        asset_name="000852.SH",
        currency="CNY",
    )
    engine_kwargs = {"params_type": "quad_params", "params_kwargs": {"grid_points": 101}}

    daily_mapping = map_trade_row(snowball_row("T-SNOWBALL-DAILY"))
    daily_price = price_product(
        daily_mapping.product_type,
        daily_mapping.product_kwargs,
        market,
        daily_mapping.engine_name,
        engine_kwargs,
    )
    assert daily_price.ok, daily_price.error

    european_row = snowball_row("T-SNOWBALL-EUROPEAN")
    european_row["Custom Structure"] = "European Knock-In"
    european_mapping = map_trade_row(european_row)
    european_price = price_product(
        european_mapping.product_type,
        european_mapping.product_kwargs,
        market,
        european_mapping.engine_name,
        engine_kwargs,
    )
    assert european_price.ok, european_price.error
    european_pde_price = price_product(
        european_mapping.product_type,
        european_mapping.product_kwargs,
        market,
        "PDEEngine",
        {
            "params_type": "pde_params",
            "params_kwargs": {
                "grid_size": 80,
                "time_steps": 80,
                "max_grid_size": 80,
                "max_time_steps": 80,
                "auto_grid": True,
                "time_grid_type": "event_aligned",
            },
        },
    )
    assert european_pde_price.ok, european_pde_price.error

    no_ki_row = snowball_row("T-SNOWBALL-NO-KI")
    no_ki_row["Custom Structure"] = "No Knock-In"
    no_ki_mapping = map_trade_row(no_ki_row)
    no_ki_price = price_product(
        no_ki_mapping.product_type,
        no_ki_mapping.product_kwargs,
        market,
        no_ki_mapping.engine_name,
        engine_kwargs,
    )
    assert no_ki_price.ok, no_ki_price.error

    phoenix_mapping = map_trade_row(phoenix_row("T-PHOENIX-DAILY"))
    phoenix_price = price_product(
        phoenix_mapping.product_type,
        phoenix_mapping.product_kwargs,
        market,
        phoenix_mapping.engine_name,
        engine_kwargs,
    )
    assert phoenix_price.ok, phoenix_price.error


def test_sharkfin_terms_build_quantark_products():
    market = PricingEnvironmentSnapshot(
        valuation_date=datetime(2026, 4, 30),
        spot=101.0,
        volatility=0.25,
        rate=0.02,
        dividend_yield=0.03,
        asset_name="000852.SH",
        currency="CNY",
    )

    single_mapping = map_trade_row(shark_row())
    single_price = price_product(
        single_mapping.product_type,
        single_mapping.product_kwargs,
        market,
        single_mapping.engine_name,
    )
    assert single_price.ok, single_price.error

    double_mapping = map_trade_row(double_shark_row())
    double_price = price_product(
        double_mapping.product_type,
        double_mapping.product_kwargs,
        market,
        double_mapping.engine_name,
    )
    assert double_price.ok, double_price.error


def test_import_positions_is_idempotent_and_preserves_source(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row(), shark_row(), snowball_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Import Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    first = import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    second = import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    positions = session.query(Position).filter(Position.portfolio_id == portfolio.id).all()
    assert first.imported_count == 3
    assert second.imported_count == 3
    assert len(positions) == 3
    assert {position.position_kind for position in positions} == {"otc"}
    vanilla = next(position for position in positions if position.source_trade_id == "T-VANILLA")
    assert vanilla.product_id is not None
    assert vanilla.quantity == -1
    assert vanilla.mapping_status == "supported"
    assert vanilla.product_kwargs["contract_multiplier"] == 10_000
    assert vanilla.source_payload["row"]["Trade ID"] == "T-VANILLA"


def test_import_product_change_clears_stale_structured_terms(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [snowball_row("T-REWRITE")])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Rewrite Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()
    position = session.query(Position).filter_by(source_trade_id="T-REWRITE").one()
    assert session.get(SnowballTerm, position.id) is not None
    assert session.get(PositionBarrierState, position.id) is not None

    write_trade_workbook(xlsx_path, [vanilla_row("T-REWRITE")])
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    session.refresh(position)
    assert position.product_type == "EuropeanVanillaOption"
    assert session.get(SnowballTerm, position.id) is None
    assert session.get(PositionBarrierState, position.id) is None


def test_market_workbook_join_and_override_precedence(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row(), unsupported_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Pricing Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    def unused_spot_fetcher(symbol: str, valuation_date: datetime):
        raise AssertionError("spot_fetcher should not be called when --spot override is supplied")

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        overrides=MarketOverrides(spot=101.0, rate=0.02, dividend_yield=0.03, volatility=0.25),
        spot_fetcher=unused_spot_fetcher,
    )
    session.commit()

    results = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).all()
    priced = next(result for result in results if result.ok)
    unsupported = next(result for result in results if not result.ok)
    assert run.summary["priced"] == 1
    assert run.summary["unsupported"] == 1
    assert priced.market_inputs["spot"] == 101.0
    assert priced.market_inputs["rate"] == 0.02
    assert priced.market_inputs["dividend_yield"] == 0.03
    assert priced.market_inputs["volatility"] == 0.25
    assert priced.result_payload["contract_multiplier"] == 10_000
    assert priced.price == priced.result_payload["quantark_price"]
    assert priced.price == priced.result_payload["unit_price"]
    assert "Unsupported structure type" in unsupported.error

    selected_position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    selected_run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        position_ids=[selected_position.id],
        valuation_date=datetime(2026, 4, 30),
        overrides=MarketOverrides(spot=101.0, rate=0.02, dividend_yield=0.03, volatility=0.25),
        engine_name="BlackScholesEngine",
        engine_kwargs={"params_type": "engine_params", "params_kwargs": {"bump_size": 0.0002}},
        spot_fetcher=unused_spot_fetcher,
    )
    session.commit()
    selected_results = session.query(PositionValuationResult).filter_by(valuation_run_id=selected_run.id).all()
    assert len(selected_results) == 1
    assert selected_results[0].source_trade_id == "T-VANILLA"
    assert selected_run.overrides["engine_name"] == "BlackScholesEngine"
    assert selected_run.overrides["engine_kwargs"]["params_kwargs"]["bump_size"] == 0.0002
    session.refresh(selected_position)
    assert selected_position.engine_kwargs == {}


def test_pricer_fails_when_akshare_spot_is_missing(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Missing Spot Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        spot_fetcher=lambda symbol, valuation_date: (None, {"source": "test"}),
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert not result.ok
    # Transitional spot chain (T6): when override/pricing-row/market-input/quote
    # store/akshare all miss the spot slot, the failure is the explicit
    # no-source diagnostic (was the aggregate "Missing market inputs: spot").
    assert "no market quote for" in result.error


def test_delta_one_pricer_requires_only_spot(tmp_path: Path, monkeypatch):
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Delta One Book", base_currency="CNY")
    instrument = Instrument(symbol="IF2606.CFFEX", kind="futures", status="active")
    session.add_all([portfolio, instrument])
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="IF2606.CFFEX",
        underlying_id=instrument.id,
        product_type="Futures",
        product_kwargs={
            "underlying": "IF2606.CFFEX",
            "multiplier": 300.0,
            "maturity": 0.25,
            "market_price": 4913.8,
        },
        engine_name="DeltaOneEngine",
        engine_kwargs={},
        quantity=4.0,
        entry_price=0.0,
        mapping_status="supported",
        source_trade_id="HEDGE:33:1",
    )
    session.add(position)
    session.flush()
    record_quote(
        session,
        instrument_id=instrument.id,
        price=4913.8,
        as_of=datetime(2026, 4, 30),
        source="hedge_load",
        price_type="mid",
    )
    session.commit()

    def fake_price_product(product_type, product_kwargs, market, engine_name, engine_kwargs):
        assert product_type == "Futures"
        assert engine_name == "DeltaOneEngine"
        assert market.spot == 4913.8
        assert market.volatility > 0.0
        assert product_kwargs["market_price"] == 4913.8
        assert engine_kwargs["use_market_price"] is True
        return QuantArkResult(ok=True, data={"price": market.spot, "engine": engine_name})

    monkeypatch.setattr("app.services.position_pricer.price_product", fake_price_product)

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert result.ok, result.error
    assert run.summary["priced"] == 1
    assert result.price == 4913.8
    assert result.market_value == 4913.8 * 4.0 * 300.0
    assert result.pnl == 4913.8 * 4.0 * 300.0
    assert result.result_payload["contract_multiplier"] == 300.0
    assert result.market_inputs["spot"] == 4913.8
    assert "rate" not in result.market_inputs
    assert "dividend_yield" not in result.market_inputs
    assert "volatility" not in result.market_inputs


def test_pricer_rejects_implausible_model_output(tmp_path: Path, monkeypatch):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Implausible Pricing Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    # Spot is observation-only now — seed the quote store (was a market input).
    record_quote(
        session,
        instrument_id=position.underlying_id,
        price=100.0,
        as_of=datetime(2026, 4, 30),
        source="xlsx_import",
        price_type="mid",
    )
    session.commit()

    def huge_price(*args, **kwargs):
        return QuantArkResult(ok=True, data={"price": 1.573839927106165e253, "engine": "TestEngine"})

    monkeypatch.setattr("app.services.position_pricer.price_product", huge_price)

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        overrides=MarketOverrides(rate=0.02, dividend_yield=0.03, volatility=0.22),
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert run.summary["priced"] == 0
    assert run.summary["failed"] == 1
    assert run.summary["market_value"] == 0.0
    assert run.summary["pnl"] == 0.0
    assert not result.ok
    assert result.price is None
    assert result.market_value is None
    assert result.pnl is None
    assert "implausible" in result.error
    assert result.result_payload["quantark_price"] == 1.573839927106165e253
    assert result.result_payload["gross_notional"] == 1_000_000.0


def test_market_rows_are_keyed_by_trade_id(tmp_path: Path):
    from app.services.market_input_workbooks import read_market_rows_with_diagnostics

    market_path = tmp_path / "market.xlsx"
    write_market_workbook(market_path, ["T-VANILLA"])

    market_rows, _duplicates = read_market_rows_with_diagnostics(market_path)

    assert market_rows["T-VANILLA"]["symbol"] == "000852.SH"
    assert market_rows["T-VANILLA"]["volatility"] == 0.22


def test_import_global_pricing_parameter_profile_from_xlsx(tmp_path: Path):
    market_path = tmp_path / "market-20260430.xlsx"
    write_market_workbook(market_path, ["T-VANILLA", "T-SECOND"], spot=101.0)
    session = configure_test_db(tmp_path)

    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="2026-04-30 Close",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    assert profile.name == "2026-04-30 Close"
    assert profile.valuation_date == datetime(2026, 4, 30)
    assert profile.source_type == "xlsx"
    assert profile.summary["row_count"] == 2
    assert profile.summary["duplicate_trade_ids"] == []

    rows = session.query(PricingParameterRow).filter_by(profile_id=profile.id).order_by(PricingParameterRow.source_trade_id).all()
    assert [row.source_trade_id for row in rows] == ["T-SECOND", "T-VANILLA"]
    # Spot left the row (split-write T8): r/q/vol stay on the row, spot -> quote store.
    assert not hasattr(rows[0], "spot")
    assert rows[0].instrument_id is not None
    assert rows[0].rate == 0.02
    assert rows[0].dividend_yield == 0.03
    assert rows[0].volatility == 0.22
    assert rows[0].source_payload["row_number"] == 3
    # The observation was recorded against the row's instrument.
    quote = latest_quote(session, rows[0].instrument_id, as_of=datetime(2026, 4, 30))
    assert quote.price == 101.0
    assert quote.source == "xlsx_import"
    assert quote.price_type == "mid"


def test_import_global_pricing_profile_reports_duplicate_trade_ids(tmp_path: Path):
    market_path = tmp_path / "market.xlsx"
    write_market_workbook(market_path, ["T-VANILLA", "T-VANILLA"], spot=101.0)
    session = configure_test_db(tmp_path)

    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Duplicate Check",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    assert profile.summary["row_count"] == 1
    assert profile.summary["duplicate_trade_ids"] == ["T-VANILLA"]
    rows = session.query(PricingParameterRow).filter_by(profile_id=profile.id).all()
    assert len(rows) == 1
    assert rows[0].source_row == 3


def test_pricing_rows_for_profile_returns_all_rows(tmp_path: Path):
    market_path = tmp_path / "market.xlsx"
    write_market_workbook(market_path, ["T-VANILLA", "T-OTHER"], spot=101.0)
    session = configure_test_db(tmp_path)
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Lookup Profile",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    rows = pricing_rows_for_profile(session, profile_id=profile.id)

    assert isinstance(rows, list)
    assert {row.source_trade_id for row in rows} == {"T-VANILLA", "T-OTHER"}
    assert all(row.profile_id == profile.id for row in rows)
    assert all(row.instrument_id is not None for row in rows)


def test_pricing_profile_precedence_between_profile_and_overrides(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    pricing_path = tmp_path / "pricing.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    write_market_workbook(pricing_path, ["T-VANILLA"], spot=102.0)
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Pricing Profile Precedence", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=pricing_path,
        name="Profile Inputs",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    def unused_spot_fetcher(symbol: str, valuation_date: datetime):
        raise AssertionError("spot_fetcher should not be called when profile supplies parameters")

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=profile.id,
        valuation_date=datetime(2026, 4, 30),
        overrides=MarketOverrides(spot=103.0),
        spot_fetcher=unused_spot_fetcher,
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert run.pricing_parameter_profile_id == profile.id
    assert run.overrides["pricing_parameter_profile_id"] == profile.id
    assert run.overrides["spot"] == 103.0
    assert result.market_inputs["spot"] == 103.0
    assert result.market_inputs["rate"] == 0.02
    assert result.market_inputs["market_input_source"] == "pricing_parameter_profile"
    assert result.market_inputs["pricing_parameter_profile_id"] == profile.id
    assert result.market_inputs["spot_metadata"]["source"] == "override"
    assert result.market_inputs["field_sources"] == {
        "spot": "override",
        "rate": "pricing_parameter_profile",
        "dividend_yield": "pricing_parameter_profile",
        "volatility": "pricing_parameter_profile",
    }


def test_pricing_profile_uses_unique_complete_underlying_row(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Underlying Profile Pricing", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    profile = PricingParameterProfile(
        name="Underlying Inputs",
        valuation_date=datetime(2026, 4, 30),
        source_type="underlying",
        status="completed",
        summary={"row_count": 1},
    )
    session.add(profile)
    session.flush()
    session.add(
        PricingParameterRow(
            profile_id=profile.id,
            source_trade_id="UNDERLYING-000852",
            symbol="000852.SH",
            instrument_id=position.underlying_id,
            rate=0.021,
            dividend_yield=0.031,
            volatility=0.23,
        )
    )
    # Spot is observation-only — recorded against the underlying instrument.
    record_quote(
        session,
        instrument_id=position.underlying_id,
        price=104.0,
        as_of=datetime(2026, 4, 30),
        source="xlsx_import",
        price_type="mid",
    )
    session.commit()

    def unused_spot_fetcher(symbol: str, valuation_date: datetime):
        raise AssertionError("spot_fetcher should not be called when the quote store has spot")

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=profile.id,
        valuation_date=datetime(2026, 4, 30),
        spot_fetcher=unused_spot_fetcher,
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    profile_row = session.query(PricingParameterRow).filter_by(profile_id=profile.id).one()
    assert result.market_inputs["spot"] == 104.0  # quote store
    assert result.market_inputs["rate"] == 0.021
    assert result.market_inputs["dividend_yield"] == 0.031
    assert result.market_inputs["volatility"] == 0.23
    assert result.market_inputs["market_input_source"] == "pricing_parameter_profile"
    assert result.market_inputs["pricing_parameter_profile_id"] == profile.id
    assert result.market_inputs["pricing_parameter_row_id"] == profile_row.id
    assert result.market_inputs["field_sources"] == {
        "spot": "market_quote",
        "rate": "pricing_parameter_profile",
        "dividend_yield": "pricing_parameter_profile",
        "volatility": "pricing_parameter_profile",
    }


def test_incomplete_pricing_profile_uses_position_market_input_fallback(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    pricing_path = tmp_path / "pricing.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    write_market_workbook(pricing_path, ["T-VANILLA"], spot=102.0)
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Pricing Profile Mixed Sources", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    # Split-write records spot=102.0 to the quote store; r/q/vol land on the row.
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=pricing_path,
        name="Spot Only Profile",
        valuation_date=datetime(2026, 4, 30),
    )
    pricing_row = session.query(PricingParameterRow).filter_by(profile_id=profile.id).one()
    # Null the row's r/q/vol so the trade row is INCOMPLETE -> r/q/vol fall through
    # to the instrument-level assumption set (was: position_market_inputs).
    pricing_row.rate = None
    pricing_row.dividend_yield = None
    pricing_row.volatility = None
    assumption_set = AssumptionSet(
        name="Mixed", valuation_date=datetime(2026, 4, 30), status="completed", summary={}
    )
    session.add(assumption_set)
    session.flush()
    session.add(
        AssumptionRow(
            set_id=assumption_set.id,
            instrument_id=position.underlying_id,
            symbol=position.underlying,
            rate=0.02,
            dividend_yield=0.03,
            volatility=0.22,
        )
    )
    session.commit()

    def unused_spot_fetcher(symbol: str, valuation_date: datetime):
        raise AssertionError("spot_fetcher should not be called when the quote store has spot")

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=profile.id,
        valuation_date=datetime(2026, 4, 30),
        spot_fetcher=unused_spot_fetcher,
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert result.market_inputs["spot"] == 102.0  # quote store
    assert result.market_inputs["rate"] == 0.02
    assert result.market_inputs["dividend_yield"] == 0.03
    assert result.market_inputs["volatility"] == 0.22
    assert result.market_inputs["market_input_source"] == "assumption_set"
    assert result.market_inputs["pricing_parameter_profile_id"] is None
    assert result.market_inputs["pricing_parameter_row_id"] is None
    assert result.market_inputs["assumption_set_id"] == assumption_set.id
    assert result.market_inputs["field_sources"] == {
        "spot": "market_quote",
        "rate": "assumption_set",
        "dividend_yield": "assumption_set",
        "volatility": "assumption_set",
    }


def test_pricer_honors_full_overrides_without_persisted_inputs(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Override Only Pricing", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)

    def unused_spot_fetcher(symbol: str, valuation_date: datetime):
        raise AssertionError("spot_fetcher should not be called when spot override is supplied")

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        overrides=MarketOverrides(spot=103.0, rate=0.02, dividend_yield=0.03, volatility=0.22),
        spot_fetcher=unused_spot_fetcher,
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert result.ok
    assert result.market_inputs["asset_name"] == "000852.SH"
    assert result.market_inputs["spot"] == 103.0
    assert result.market_inputs["rate"] == 0.02
    assert result.market_inputs["dividend_yield"] == 0.03
    assert result.market_inputs["volatility"] == 0.22
    assert result.market_inputs["market_input_source"] == "override"
    assert result.market_inputs["pricing_parameter_profile_id"] is None
    assert result.market_inputs["pricing_parameter_row_id"] is None
    assert result.market_inputs["assumption_set_id"] is None
    assert result.market_inputs["assumption_row_id"] is None
    assert result.market_inputs["field_sources"] == {
        "spot": "override",
        "rate": "override",
        "dividend_yield": "override",
        "volatility": "override",
    }


def test_global_pricing_profile_migration_adds_valuation_run_foreign_key(tmp_path: Path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}")
    metadata = sa.MetaData()
    sa.Table(
        "position_valuation_runs",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    metadata.create_all(engine)

    migration = importlib.import_module("backend.alembic.versions.0005_global_pricing_market_profiles")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        original_op = migration.op
        migration.op = Operations(context)
        try:
            migration.upgrade()
        finally:
            migration.op = original_op

    inspector = sa.inspect(engine)
    foreign_keys = inspector.get_foreign_keys("position_valuation_runs")

    assert any(
        foreign_key["referred_table"] == "pricing_parameter_profiles"
        and foreign_key["constrained_columns"] == ["pricing_parameter_profile_id"]
        for foreign_key in foreign_keys
    )


def test_cli_import_and_price_with_overrides(tmp_path: Path, capsys):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    session.close()

    assert cli_main(["positions", "import", "--xlsx", str(xlsx_path), "--portfolio", "CLI Book"]) == 0
    import_output = json.loads(capsys.readouterr().out)
    assert import_output["imported_count"] == 1

    # Spot/r/q/vol come from CLI overrides; the folded import-market step is gone.
    assert (
        cli_main(
            [
                "positions",
                "price",
                "--portfolio",
                "CLI Book",
                "--valuation-date",
                "2026-04-30",
                "--spot",
                "101",
                "--r",
                "0.02",
                "--q",
                "0.03",
                "--vol",
                "0.25",
            ]
        )
        == 0
    )
    price_output = json.loads(capsys.readouterr().out)
    assert price_output["summary"]["priced"] == 1


def test_agent_tool_registry_exposes_run_batch_pricing():
    tool_names = {tool.name for tool in QUANT_AGENT_TOOLS}
    assert "import_otc_positions" in tool_names
    assert "run_batch_pricing" in tool_names
    # The old split agent tools were unified into run_batch_pricing.
    assert "price_positions" not in tool_names
    assert "run_risk" not in tool_names
    # import_position_market_inputs was folded (PositionMarketInput removed).
    assert "import_position_market_inputs" not in tool_names


def test_price_view_portfolio_resolves_across_containers(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.models import (
        AssumptionRow,
        AssumptionSet,
        Instrument,
        Portfolio,
        Position,
        PortfolioKind,
    )
    from app.services.position_pricer import price_portfolio_positions
    from app.services.quotes import record_quote

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as session:
        c = Portfolio(name="C", base_currency="USD", kind=PortfolioKind.CONTAINER.value)
        session.add(c)
        session.flush()
        instrument = Instrument(symbol="AAPL", kind="stock", status="active")
        session.add(instrument)
        session.flush()
        pos = Position(
            portfolio_id=c.id, underlying="AAPL", underlying_id=instrument.id,
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine", quantity=1.0, mapping_status="supported",
        )
        session.add(pos)
        session.flush()
        # Spot via quote store; r/q/vol via instrument-level assumption set —
        # same numbers the folded PositionMarketInput used to carry.
        record_quote(
            session, instrument_id=instrument.id, price=101.0,
            as_of=datetime(2026, 1, 1), source="xlsx_import", price_type="mid",
        )
        assumption_set = AssumptionSet(
            name="View", valuation_date=datetime(2026, 1, 1), status="completed", summary={}
        )
        session.add(assumption_set)
        session.flush()
        session.add(
            AssumptionRow(
                set_id=assumption_set.id, instrument_id=instrument.id, symbol="AAPL",
                rate=0.02, dividend_yield=0.0, volatility=0.2,
            )
        )
        pos.source_trade_id = "T-VIEW"
        v = Portfolio(
            name="V", base_currency="USD", kind=PortfolioKind.VIEW.value,
            filter_rule={"op": "eq", "field": "product_type", "value": "EuropeanVanillaOption"},
        )
        session.add(v)
        session.flush()

        run = price_portfolio_positions(session, portfolio_id=v.id)
        session.commit()
        assert run.resolved_position_ids == [pos.id]
        assert run.summary["priced"] == 1


def test_quad_engine_kwargs_no_longer_bakes_grid_points():
    from app.services.position_adapter import _quad_engine_kwargs

    kwargs = _quad_engine_kwargs()
    assert kwargs == {"params_type": "quad_params"}


def test_import_gates_all_families_and_isolates_invalid_row(tmp_path: Path):
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Gate Test Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    good_vanilla = vanilla_row("T-OK-VANILLA")
    good_phoenix = phoenix_row("T-OK-PHOENIX")
    bad_snowball = snowball_row("T-BAD-SNOWBALL")
    bad_snowball["Knock-Out Observation Dates"] = None  # no KO schedule -> build_product rejects as malformed

    path = tmp_path / "trades.xlsx"
    write_trade_workbook(path, [good_vanilla, good_phoenix, bad_snowball])

    # Must NOT raise — the whole import completes despite one invalid row.
    batch = import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=path)

    positions = {p.source_trade_id: p for p in session.query(Position).all()}
    assert positions["T-OK-VANILLA"].mapping_status == "supported"   # scalar now validated + booked
    assert positions["T-OK-PHOENIX"].mapping_status == "supported"   # phoenix now validated + booked
    assert positions["T-BAD-SNOWBALL"].mapping_status == "error"     # isolated, not a crash
    assert batch.error_count >= 1


def test_import_update_path_is_gated_and_demotes_invalid_reimport(tmp_path: Path):
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Gate Update Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    good = snowball_row("T-UPD")
    write_trade_workbook(tmp_path / "first.xlsx", [good])
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=tmp_path / "first.xlsx")
    first = session.query(Position).filter(Position.source_trade_id == "T-UPD").one()
    assert first.mapping_status == "supported"

    # Re-import the SAME trade with now-malformed (schedule-less) terms. The UPDATE
    # branch routes through the gate, which rejects it -> the existing position is
    # demoted in place (no crash, no duplicate row).
    bad = snowball_row("T-UPD")
    bad["Knock-Out Observation Dates"] = None
    write_trade_workbook(tmp_path / "second.xlsx", [bad])
    batch = import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=tmp_path / "second.xlsx")

    rows = session.query(Position).filter(Position.source_trade_id == "T-UPD").all()
    assert len(rows) == 1                       # demoted in place, NOT duplicated
    assert rows[0].id == first.id
    assert rows[0].position_kind == "otc"
    assert rows[0].mapping_status == "error"    # update path gated + isolated
    assert batch.error_count >= 1


def test_import_currency_defaults_to_cny_without_column_value(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])  # 币种 cell left blank
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Default Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    # Pins the import-channel default: CNY, not the generic spec default (USD).
    assert position.currency == "CNY"
    assert position.product.currency == "CNY"


def test_import_reads_currency_column_per_row(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    usd_row = vanilla_row("T-USD")
    usd_row["Currency"] = "usd"  # normalized to USD
    cny_row = vanilla_row("T-CNY")
    write_trade_workbook(xlsx_path, [usd_row, cny_row])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Column Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    usd = session.query(Position).filter_by(source_trade_id="T-USD").one()
    cny = session.query(Position).filter_by(source_trade_id="T-CNY").one()
    assert usd.currency == "USD"
    assert cny.currency == "CNY"


def test_import_invalid_currency_becomes_error_row(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    bad_row = vanilla_row("T-BAD-CCY")
    bad_row["Currency"] = "DOLLARS"
    write_trade_workbook(xlsx_path, [bad_row, vanilla_row("T-GOOD")])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Error Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    batch = import_positions_from_xlsx(
        session, portfolio_id=portfolio.id, xlsx_path=xlsx_path
    )
    session.commit()

    bad = session.query(Position).filter_by(source_trade_id="T-BAD-CCY").one()
    good = session.query(Position).filter_by(source_trade_id="T-GOOD").one()
    assert bad.mapping_status == "error"
    assert "Invalid currency code" in (bad.mapping_error or "")
    assert good.mapping_status == "supported"
    assert batch.error_count == 1
    assert any(
        "Invalid currency code" in entry.get("error", "")
        for entry in batch.summary["errors"]
    )


def test_reimport_refreshes_currency(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row("T-REFRESH")])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Refresh Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()
    position = session.query(Position).filter_by(source_trade_id="T-REFRESH").one()
    assert position.currency == "CNY"

    updated = vanilla_row("T-REFRESH")
    updated["Currency"] = "USD"
    write_trade_workbook(xlsx_path, [updated])
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    session.refresh(position)
    assert position.currency == "USD"


def test_agent_created_profile_prices_positions_end_to_end(tmp_path: Path):
    """Characterization: the previously-blocked flow — agent-created profile
    consumed by the pricer with per-field attribution. Values are deliberately
    NON-default so attribution cannot pass vacuously."""
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Agent Profile Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    record_quote(
        session,
        instrument_id=position.underlying_id,
        price=100.0,
        as_of=datetime(2026, 4, 30),
        source="xlsx_import",
        price_type="mid",
    )
    session.commit()

    profile = pricing_profiles_domain.create_profile(
        session=session,
        rows=[{
            "symbol": position.underlying,
            "source_trade_id": "",  # underlying-level what-if row
            "rate": 0.037,
            "dividend_yield": 0.013,
            "volatility": 0.31,
        }],
    )

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        pricing_parameter_profile_id=profile.id,
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(
        valuation_run_id=run.id
    ).one()
    assert result.ok, result.error
    assert result.market_inputs["rate"] == 0.037
    assert result.market_inputs["dividend_yield"] == 0.013
    assert result.market_inputs["volatility"] == 0.31
    assert result.market_inputs["market_input_source"] == "pricing_parameter_profile"
    assert result.market_inputs["pricing_parameter_profile_id"] == profile.id
    assert result.market_inputs["field_sources"] == {
        "spot": "market_quote",
        "rate": "pricing_parameter_profile",
        "dividend_yield": "pricing_parameter_profile",
        "volatility": "pricing_parameter_profile",
    }

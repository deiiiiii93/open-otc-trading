"""Trade-keyed split-write import + r/q/vol assumption chain (instrument-unification T8).

The xlsx importer now writes observations (spot) ONLY to the quote store and
keeps r/q/vol on the trade-keyed PricingParameterRow. Spot leaves the row. The
pricer + risk r/q/vol chain falls through to the instrument-level AssumptionSet
when no exact trade row supplies a field. Every asserted number is NON-default so
a silent fallback flips the assertion.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from app import database
from app.config import Settings
from app.models import (
    AssumptionRow,
    AssumptionSet,
    Instrument,
    MarketQuote,
    Portfolio,
    Position,
    PositionValuationResult,
    PricingParameterRow,
)
from app.services.position_adapter import import_positions_from_xlsx
from app.services.position_pricer import MarketOverrides, price_portfolio_positions
from app.services.pricing_profiles import import_pricing_parameter_profile_from_xlsx
from app.services.quotes import latest_quote
from app.services.risk_engine import run_portfolio_risk


MARKET_HEADERS = ["Trade ID", "Underlying Code", "Underlying Price", "Volatility", "Risk-Free Rate", "Dividend/Borrow Yield"]

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


def write_market_rows(path: Path, rows: list[dict[str, object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "SheetJS"
    worksheet.append(MARKET_HEADERS)
    for row in rows:
        worksheet.append([row.get(header) for header in MARKET_HEADERS])
    workbook.save(path)


def market_row(
    trade_id: str,
    *,
    symbol: str = "000852.SH",
    spot: float | None = None,
    vol: float | None = 0.22,
    rate: float | None = 0.02,
    div: float | None = 0.03,
) -> dict[str, object]:
    return {
        "Trade ID": trade_id,
        "Underlying Code": symbol,
        "Underlying Price": spot,
        "Volatility": vol,
        "Risk-Free Rate": rate,
        "Dividend/Borrow Yield": div,
    }


def vanilla_trade(trade_id: str, *, symbol: str = "000852.SH") -> dict[str, object]:
    return {
        "Structure Type": "European Vanilla",
        "Option Type": "Call",
        "Direction": "Sell",
        "Underlying Code": symbol,
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
        "Coupon Rate": 0.05,
        "Currency": "CNY",
    }


def write_trade_workbook(path: Path, rows: list[dict[str, object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Positions"
    worksheet.append(TRADE_HEADERS)
    for row in rows:
        worksheet.append([row.get(header) for header in TRADE_HEADERS])
    workbook.save(path)


# ---------------------------------------------------------------------------
# 1. Split-write happy path
# ---------------------------------------------------------------------------

def test_split_write_records_quotes_keeps_rqv_on_row(tmp_path: Path):
    session = configure_test_db(tmp_path)
    # Two booked positions share one symbol with DIFFERENT spots; one unbooked.
    portfolio = Portfolio(name="Split Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    import_positions_from_xlsx(
        session,
        portfolio_id=portfolio.id,
        xlsx_path=_trade_path(tmp_path, [vanilla_trade("T-A"), vanilla_trade("T-B")]),
    )
    session.commit()

    market_path = tmp_path / "market-20260430.xlsx"
    write_market_rows(
        market_path,
        [
            market_row("T-A", spot=6412.55),
            market_row("T-B", spot=6410.00),
            # Unbooked trade on a DIFFERENT symbol -> dormant, no spot conflict.
            market_row("T-DORMANT", symbol="000300.SH", spot=6411.00),
        ],
    )

    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Split",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    rows = {
        row.source_trade_id: row
        for row in session.query(PricingParameterRow).filter_by(profile_id=profile.id)
    }
    # Every row carries an instrument_id and NO spot attribute.
    for row in rows.values():
        assert row.instrument_id is not None
        assert not hasattr(row, "spot")
        # r/q/vol stay on the row.
        assert row.volatility == 0.22
        assert row.rate == 0.02
        assert row.dividend_yield == 0.03

    booked = session.query(Position).filter_by(source_trade_id="T-A").one()
    assert rows["T-A"].instrument_id == booked.underlying_id

    # Three observations recorded (one per source row), all xlsx_import/mid.
    quotes = session.query(MarketQuote).all()
    assert len(quotes) == 3
    for quote in quotes:
        assert quote.source == "xlsx_import"
        assert quote.price_type == "mid"
        assert quote.as_of == datetime(2026, 4, 30)

    # Summary surfaces the split-write diagnostics.
    summary = profile.summary
    assert summary["rows_applied"] == 2
    assert summary["rows_dormant"] == 1
    assert summary["dormant_trade_ids"] == ["T-DORMANT"]
    assert summary["quotes_emitted"] == 3
    assert summary["spot_conflicts"] == [
        {"symbol": "000852.SH", "count": 2, "resolution": "last row wins"}
    ]

    # Resolver tie-break: last row wins for the conflicting symbol.
    instrument_id = booked.underlying_id
    latest = latest_quote(session, instrument_id, as_of=datetime(2026, 4, 30))
    assert latest.price == 6410.00


# ---------------------------------------------------------------------------
# 2. r/q/vol chain: row -> assumption row -> env fallback
# ---------------------------------------------------------------------------

def test_rqv_chain_pricer_row_then_assumption_then_fallback(tmp_path: Path):
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Chain Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    # T-ROW and T-ASSUMED have DIFFERENT underlyings so the pricing profile (which
    # only carries T-ROW's symbol) cannot underlying-resolve a row for T-ASSUMED.
    import_positions_from_xlsx(
        session,
        portfolio_id=portfolio.id,
        xlsx_path=_trade_path(
            tmp_path,
            [
                vanilla_trade("T-ROW", symbol="000852.SH"),
                vanilla_trade("T-ASSUMED", symbol="000300.SH"),
            ],
        ),
    )
    session.commit()

    pos_row = session.query(Position).filter_by(source_trade_id="T-ROW").one()
    pos_assumed = session.query(Position).filter_by(source_trade_id="T-ASSUMED").one()

    # Profile: an EXACT trade row for T-ROW with a distinctive vol; nothing for T-ASSUMED.
    market_path = tmp_path / "market.xlsx"
    write_market_rows(
        market_path,
        [market_row("T-ROW", symbol="000852.SH", spot=102.0, vol=0.37, rate=0.011, div=0.013)],
    )
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Chain",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    # Instrument-level AssumptionSet supplies r/q/vol for T-ASSUMED's underlying.
    assumption_set = AssumptionSet(
        name="Assumed", valuation_date=datetime(2026, 4, 30), status="completed", summary={}
    )
    session.add(assumption_set)
    session.flush()
    session.add(
        AssumptionRow(
            set_id=assumption_set.id,
            instrument_id=pos_assumed.underlying_id,
            symbol=pos_assumed.underlying,
            rate=0.044,
            dividend_yield=0.055,
            volatility=0.66,
        )
    )
    session.commit()

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=profile.id,
        valuation_date=datetime(2026, 4, 30),
        spot_fetcher=lambda symbol, vdate: (102.0, {"source": "test"}),
    )
    session.commit()

    results = {
        r.position_id: r
        for r in session.query(PositionValuationResult).filter_by(valuation_run_id=run.id)
    }
    row_result = results[pos_row.id]
    assumed_result = results[pos_assumed.id]

    # T-ROW: exact trade row wins for vol.
    assert row_result.market_inputs["volatility"] == 0.37
    assert row_result.market_inputs["rate"] == 0.011
    assert row_result.market_inputs["field_sources"]["volatility"] == "pricing_parameter_profile"

    # T-ASSUMED: no row -> assumption row supplies r/q/vol.
    assert assumed_result.market_inputs["volatility"] == 0.66
    assert assumed_result.market_inputs["rate"] == 0.044
    assert assumed_result.market_inputs["dividend_yield"] == 0.055
    assert assumed_result.market_inputs["field_sources"]["volatility"] == "assumption_set"
    assert assumed_result.market_inputs["market_input_source"] == "assumption_set"


def test_rqv_chain_env_fallback_when_neither_row_nor_assumption(tmp_path: Path):
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Fallback Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    import_positions_from_xlsx(
        session,
        portfolio_id=portfolio.id,
        xlsx_path=_trade_path(tmp_path, [vanilla_trade("T-ENV")]),
    )
    session.commit()

    # No profile, no assumption set. r/q/vol come from explicit overrides
    # (the env-fallback slot) so the pricer can still produce a value.
    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        overrides=MarketOverrides(spot=102.0, rate=0.07, dividend_yield=0.08, volatility=0.29),
        spot_fetcher=lambda symbol, vdate: (None, {"source": "test"}),
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert result.ok
    assert result.market_inputs["volatility"] == 0.29
    assert result.market_inputs["rate"] == 0.07
    assert result.market_inputs["market_input_source"] == "override"


def test_rqv_chain_risk_path_uses_assumption_row(tmp_path: Path):
    """The no-profile risk path consults the assumption set for r/q/vol."""
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Risk Chain Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    import_positions_from_xlsx(
        session,
        portfolio_id=portfolio.id,
        xlsx_path=_trade_path(tmp_path, [vanilla_trade("T-RISK")]),
    )
    session.commit()

    pos = session.query(Position).filter_by(source_trade_id="T-RISK").one()
    # Quote for spot.
    from app.services.quotes import record_quote

    record_quote(
        session,
        instrument_id=pos.underlying_id,
        price=6412.55,
        as_of=datetime(2026, 4, 30),
        source="xlsx_import",
        price_type="mid",
    )
    assumption_set = AssumptionSet(
        name="Risk Assumed", valuation_date=datetime(2026, 4, 30), status="completed", summary={}
    )
    session.add(assumption_set)
    session.flush()
    session.add(
        AssumptionRow(
            set_id=assumption_set.id,
            instrument_id=pos.underlying_id,
            symbol=pos.underlying,
            rate=0.044,
            dividend_yield=0.055,
            volatility=0.66,
        )
    )
    session.commit()

    run = run_portfolio_risk(
        session,
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=None,
    )
    session.commit()

    row = next(r for r in run.metrics["positions"] if r["position_id"] == pos.id)
    assert row["spot"] == 6412.55
    assert row["market_input_source"] == "assumption_set"


# ---------------------------------------------------------------------------
# 3. Dormant application: import-before-booking -> dormant, then booking applies
# ---------------------------------------------------------------------------

def test_dormant_row_applies_after_booking(tmp_path: Path):
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Dormant Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    session.commit()

    # Import the pricing sheet BEFORE the position exists.
    market_path = tmp_path / "market.xlsx"
    write_market_rows(
        market_path,
        [market_row("T-LATE", spot=6412.55, vol=0.41, rate=0.012, div=0.014)],
    )
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Dormant",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()
    assert profile.summary["rows_applied"] == 0
    assert profile.summary["rows_dormant"] == 1
    assert profile.summary["dormant_trade_ids"] == ["T-LATE"]
    # The row falls back to ensure_instrument for the unbooked symbol.
    row = session.query(PricingParameterRow).filter_by(source_trade_id="T-LATE").one()
    assert row.instrument_id is not None
    inst = session.get(Instrument, row.instrument_id)
    assert inst.symbol == "000852.SH"

    # Now book a position with the same trade id.
    import_positions_from_xlsx(
        session,
        portfolio_id=portfolio.id,
        xlsx_path=_trade_path(tmp_path, [vanilla_trade("T-LATE")]),
    )
    session.commit()
    pos = session.query(Position).filter_by(source_trade_id="T-LATE").one()

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        pricing_parameter_profile_id=profile.id,
        valuation_date=datetime(2026, 4, 30),
        spot_fetcher=lambda symbol, vdate: (6412.55, {"source": "test"}),
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert result.position_id == pos.id
    # Trade-id match resolves the row regardless of prior dormancy.
    assert result.market_inputs["volatility"] == 0.41
    assert result.market_inputs["rate"] == 0.012
    assert result.market_inputs["field_sources"]["volatility"] == "pricing_parameter_profile"


def _trade_path(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / f"trades-{rows[0]['Trade ID']}.xlsx"
    write_trade_workbook(path, rows)
    return path

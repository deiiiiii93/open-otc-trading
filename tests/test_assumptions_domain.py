"""Domain facade tests for app.services.domains.assumptions."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import database
from app.config import Settings
from app.models import (
    AssumptionRow,
    AssumptionSet,
    AuditEvent,
    Instrument,
    Portfolio,
    Position,
)
from app.services.domains import assumptions as svc
from app.services.domains._errors import DomainWriteError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert_set(name: str, *, valuation_date: datetime) -> int:
    with database.SessionLocal() as session:
        instrument = Instrument(symbol=f"{name}.SYM")
        session.add(instrument)
        session.flush()
        assumption_set = AssumptionSet(
            name=name, valuation_date=valuation_date, status="completed",
            summary={"row_count": 1},
        )
        session.add(assumption_set)
        session.flush()
        session.add(
            AssumptionRow(
                set_id=assumption_set.id,
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                rate=0.03, dividend_yield=0.01, volatility=0.2,
                source_payload={"manual_input_sources": {"rate": "instrument_default"}},
            )
        )
        session.commit()
        return assumption_set.id


def test_list_sets_newest_first_with_query():
    _insert_set("Old", valuation_date=datetime(2026, 6, 1))
    newest = _insert_set("New", valuation_date=datetime(2026, 6, 5))

    sets = svc.list_sets()
    assert [s.name for s in sets] == ["New", "Old"]
    assert sets[0].id == newest
    assert len(sets[0].rows) == 1

    assert [s.name for s in svc.list_sets(query="2026-06-01")] == ["Old"]


def test_get_set_returns_row_or_none():
    set_id = _insert_set("One", valuation_date=datetime(2026, 6, 5))
    found = svc.get_set(set_id=set_id)
    assert found is not None and found.rows[0].symbol == "One.SYM"
    assert svc.get_set(set_id=99999) is None


def test_get_instrument_defaults_filters_by_symbols():
    with database.SessionLocal() as session:
        session.add(Instrument(symbol="AAA.SH", rate=0.03))
        session.add(Instrument(symbol="BBB.SH", volatility=0.25))
        session.commit()

    rows = svc.get_instrument_defaults()
    assert [r.symbol for r in rows] == ["AAA.SH", "BBB.SH"]

    only_b = svc.get_instrument_defaults(symbols=["BBB.SH", " "])
    assert [r.symbol for r in only_b] == ["BBB.SH"]
    assert only_b[0].volatility == 0.25


def test_set_instrument_defaults_sets_clears_and_creates_draft():
    instrument = svc.set_instrument_defaults(
        symbol="NEW.SH", rate=0.03, volatility=0.22
    )
    assert instrument.rate == 0.03
    assert instrument.volatility == 0.22
    assert instrument.status == "draft"

    cleared = svc.set_instrument_defaults(symbol="NEW.SH", clear=["volatility"])
    assert cleared.volatility is None
    assert cleared.rate == 0.03  # untouched
    with database.SessionLocal() as session:
        assert session.query(AuditEvent).filter_by(
            event_type="instrument.pricing_defaults_updated"
        ).count() == 2


def test_set_instrument_defaults_refusals():
    with pytest.raises(DomainWriteError) as nothing:
        svc.set_instrument_defaults(symbol="NEW.SH")
    assert nothing.value.error == "no_fields"

    with pytest.raises(DomainWriteError) as unknown:
        svc.set_instrument_defaults(symbol="NEW.SH", clear=["spot"])
    assert unknown.value.error == "invalid_clear_field"

    with pytest.raises(DomainWriteError) as conflict:
        svc.set_instrument_defaults(symbol="NEW.SH", rate=0.02, clear=["rate"])
    assert conflict.value.error == "field_set_and_cleared"

    with pytest.raises(DomainWriteError) as blank:
        svc.set_instrument_defaults(symbol="  ", rate=0.02)
    assert blank.value.error == "blank_symbol"


def _seed_open_position(symbol: str, *, with_defaults: bool) -> None:
    with database.SessionLocal() as session:
        portfolio = Portfolio(name=f"Book-{symbol}", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol=symbol)
        if with_defaults:
            instrument.rate = 0.03
            instrument.dividend_yield = 0.01
            instrument.volatility = 0.2
        session.add(instrument)
        session.flush()
        session.add(
            Position(
                portfolio_id=portfolio.id,
                underlying=symbol,
                underlying_id=instrument.id,
                product_type="vanilla_option",
                quantity=1.0,
                status="open",
            )
        )
        session.commit()


def test_build_set_builds_from_instrument_defaults():
    _seed_open_position("FULL.SH", with_defaults=True)

    built = svc.build_set(name="Nightly", valuation_date=datetime(2026, 6, 5))

    assert built.name == "Nightly"
    assert built.summary["row_count"] == 1
    assert built.rows[0].symbol == "FULL.SH"
    assert built.rows[0].volatility == 0.2
    with database.SessionLocal() as session:
        audit = session.query(AuditEvent).filter_by(event_type="assumptions.built").one()
        assert audit.actor == "agent"


def test_build_set_surfaces_unfilled_underlyings():
    _seed_open_position("BARE.SH", with_defaults=False)

    with pytest.raises(DomainWriteError) as unfilled:
        svc.build_set()
    assert unfilled.value.error == "unfilled_underlyings"
    assert unfilled.value.detail == {"underlyings": ["BARE.SH"]}
    with database.SessionLocal() as session:
        assert session.query(AssumptionSet).count() == 0


def test_build_set_surfaces_no_open_positions():
    with pytest.raises(DomainWriteError) as nothing:
        svc.build_set()
    assert nothing.value.error == "no_open_positions"


def test_set_instrument_defaults_refuses_nonsense_values():
    with pytest.raises(DomainWriteError) as zero_vol:
        svc.set_instrument_defaults(symbol="GUARD.SH", volatility=0.0)
    assert zero_vol.value.error == "invalid_value"
    assert zero_vol.value.detail == {"fields": {"volatility": "must_be_positive"}}

    with pytest.raises(DomainWriteError) as nan_rate:
        svc.set_instrument_defaults(symbol="GUARD.SH", rate=float("nan"))
    assert nan_rate.value.error == "invalid_value"
    assert nan_rate.value.detail == {"fields": {"rate": "not_finite"}}

    # Refusal precedes ensure-create: nothing persisted.
    with database.SessionLocal() as session:
        assert session.query(Instrument).filter_by(symbol="GUARD.SH").count() == 0

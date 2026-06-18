# tests/test_pricing_explain.py
"""Read-only pricing-params resolution explain (T15).

Composition fn + endpoint: spot from the quote store, r/q/vol from the
trade-keyed pricing-parameter profile (when a profile id is supplied) else the
instrument-level assumption set, else missing. No AKShare, no record_quote.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


@pytest.fixture()
def scenario():
    """Build a portfolio + position + instrument with a quote, a profile whose
    trade row carries only `rate`, and an assumption set carrying only
    `dividend_yield` — so the four fields resolve to four distinct sources:

    spot -> market_quote, rate -> pricing_parameter_profile,
    dividend_yield -> assumption_set, volatility -> missing.
    """
    from app import database
    from app.models import (
        AssumptionRow,
        AssumptionSet,
        Instrument,
        Portfolio,
        Position,
        PricingParameterProfile,
        PricingParameterRow,
    )
    from app.services.quotes import record_quote

    with database.SessionLocal() as session:
        instrument = Instrument(symbol="000905.SH", kind="index", status="active")
        session.add(instrument)
        session.flush()

        portfolio = Portfolio(name="Book A", kind="container")
        session.add(portfolio)
        session.flush()

        position = Position(
            portfolio_id=portfolio.id,
            underlying="000905.SH",
            underlying_id=instrument.id,
            product_type="EuropeanOption",
            quantity=1.0,
            source_trade_id="T-0142",
        )
        session.add(position)
        session.flush()

        record_quote(
            session,
            instrument_id=instrument.id,
            price=6412.55,
            as_of=datetime(2026, 6, 2),
            source="xlsx_import",
        )

        profile = PricingParameterProfile(
            name="Imported 2026-06-04",
            valuation_date=datetime(2026, 6, 4),
            status="completed",
        )
        session.add(profile)
        session.flush()
        prow = PricingParameterRow(
            profile_id=profile.id,
            source_trade_id="T-0142",
            symbol="000905.SH",
            instrument_id=instrument.id,
            rate=0.023,
            dividend_yield=None,
            volatility=None,
        )
        session.add(prow)

        aset = AssumptionSet(
            name="Defaults",
            valuation_date=datetime(2026, 6, 3),
            status="completed",
            summary={},
        )
        session.add(aset)
        session.flush()
        arow = AssumptionRow(
            set_id=aset.id,
            instrument_id=instrument.id,
            symbol="000905.SH",
            rate=None,
            dividend_yield=0.018,
            volatility=None,
        )
        session.add(arow)
        session.commit()

        yield {
            "portfolio_id": portfolio.id,
            "position_id": position.id,
            "instrument_id": instrument.id,
            "profile_id": profile.id,
            "assumption_set_id": aset.id,
            "assumption_row_id": arow.id,
        }


def _client():
    from app.main import create_app

    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Composition fn (unit)
# ---------------------------------------------------------------------------


def test_compose_four_distinct_sources(scenario):
    from app import database
    from app.models import Position
    from app.services.pricing_explain import resolve_position_pricing_params

    with database.SessionLocal() as session:
        position = session.get(Position, scenario["position_id"])
        out = resolve_position_pricing_params(
            session,
            position=position,
            pricing_parameter_profile_id=scenario["profile_id"],
            as_of=datetime(2026, 6, 4),
        )

    assert out["spot"]["value"] == 6412.55
    assert out["spot"]["source"] == "market_quote"
    assert out["spot"]["quote_source"] == "xlsx_import"
    assert out["spot"]["age_days"] == pytest.approx(2.0, abs=0.01)

    assert out["rate"]["value"] == 0.023
    assert out["rate"]["source"] == "pricing_parameter_profile"
    assert out["rate"]["profile_id"] == scenario["profile_id"]
    assert out["rate"]["source_trade_id"] == "T-0142"

    assert out["dividend_yield"]["value"] == 0.018
    assert out["dividend_yield"]["source"] == "assumption_set"
    assert out["dividend_yield"]["assumption_set_id"] == scenario["assumption_set_id"]
    assert out["dividend_yield"]["assumption_row_id"] == scenario["assumption_row_id"]

    assert out["volatility"]["value"] is None
    assert out["volatility"]["source"] == "missing"


def test_compose_profile_id_respected_vs_omitted(scenario):
    """Without a profile id the trade-row layer is skipped — rate falls
    through to the assumption set (None here) -> missing, NOT 0.023."""
    from app import database
    from app.models import Position
    from app.services.pricing_explain import resolve_position_pricing_params

    with database.SessionLocal() as session:
        position = session.get(Position, scenario["position_id"])
        out = resolve_position_pricing_params(
            session,
            position=position,
            pricing_parameter_profile_id=None,
            as_of=datetime(2026, 6, 4),
        )

    # spot still resolves from the quote store
    assert out["spot"]["value"] == 6412.55
    # rate no longer comes from the profile; assumption row has rate=None
    assert out["rate"]["source"] == "missing"
    assert out["rate"]["value"] is None
    # dividend_yield still resolves from the assumption set
    assert out["dividend_yield"]["value"] == 0.018
    assert out["dividend_yield"]["source"] == "assumption_set"


def test_compose_all_missing():
    from app import database
    from app.models import Portfolio, Position
    from app.services.pricing_explain import resolve_position_pricing_params

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Empty", kind="container")
        session.add(portfolio)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying="UNKNOWN",
            underlying_id=None,
            product_type="EuropeanOption",
            quantity=1.0,
            source_trade_id="T-9999",
        )
        session.add(position)
        session.commit()

        out = resolve_position_pricing_params(
            session,
            position=position,
            pricing_parameter_profile_id=None,
            as_of=datetime(2026, 6, 4),
        )

    for field in ("spot", "rate", "dividend_yield", "volatility"):
        assert out[field]["value"] is None
        assert out[field]["source"] == "missing"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def test_endpoint_four_sources(scenario):
    client = _client()
    resp = client.get(
        f"/api/portfolios/{scenario['portfolio_id']}"
        f"/positions/{scenario['position_id']}/pricing-params",
        params={"pricing_parameter_profile_id": scenario["profile_id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["spot"]["value"] == 6412.55
    assert body["spot"]["source"] == "market_quote"
    assert body["rate"]["source"] == "pricing_parameter_profile"
    assert body["rate"]["value"] == 0.023
    assert body["dividend_yield"]["source"] == "assumption_set"
    assert body["dividend_yield"]["value"] == 0.018
    assert body["volatility"]["source"] == "missing"


def test_endpoint_omitting_profile_skips_trade_layer(scenario):
    client = _client()
    resp = client.get(
        f"/api/portfolios/{scenario['portfolio_id']}"
        f"/positions/{scenario['position_id']}/pricing-params",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["spot"]["value"] == 6412.55
    assert body["rate"]["source"] == "missing"
    assert body["dividend_yield"]["source"] == "assumption_set"


def test_endpoint_404_unknown_portfolio(scenario):
    client = _client()
    resp = client.get(
        f"/api/portfolios/999999/positions/{scenario['position_id']}/pricing-params",
    )
    assert resp.status_code == 404


def test_endpoint_404_unknown_position(scenario):
    client = _client()
    resp = client.get(
        f"/api/portfolios/{scenario['portfolio_id']}/positions/999999/pricing-params",
    )
    assert resp.status_code == 404


def test_endpoint_404_position_in_other_portfolio(scenario):
    from app import database
    from app.models import Portfolio

    with database.SessionLocal() as session:
        other = Portfolio(name="Other", kind="container")
        session.add(other)
        session.commit()
        other_id = other.id

    client = _client()
    resp = client.get(
        f"/api/portfolios/{other_id}/positions/{scenario['position_id']}/pricing-params",
    )
    assert resp.status_code == 404

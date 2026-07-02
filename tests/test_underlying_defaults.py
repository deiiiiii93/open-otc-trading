from __future__ import annotations

from datetime import datetime
from typing import Generator

import pytest
import sqlalchemy.exc as sa_exc
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Portfolio, Position, PricingParameterProfile, PricingParameterRow, Underlying, UnderlyingPricingDefault
from app.schemas import (
    BuildDefaultProfileRequest,
    UnderlyingPricingDefaultOut,
    UnderlyingPricingDefaultUpdate,
)
from app.services.assumptions import build_assumptions_set
from app.services.pricing_profiles import (
    _open_position_underlyings,
    resolve_pricing_parameter_row_for_position,
)
from app.services.underlyings import ensure_underlying

from app.config import Settings


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_position(
    session: Session,
    *,
    underlying: str,
    status: str = "open",
    position_kind: str = "otc",
    source_trade_id: str | None = "TRD-1",
    source_trade_state: str | None = None,
) -> Position:
    portfolio = (
        session.query(Portfolio).first()
        or Portfolio(name="Test")
    )
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    payload = {"trade_state": source_trade_state} if source_trade_state else {}
    position = Position(
        portfolio_id=portfolio.id,
        underlying=underlying,
        product_type="VanillaCall",
        quantity=1.0,
        source_trade_id=source_trade_id,
        status=status,
        position_kind=position_kind,
        engine_name="quantark.vanilla",
        engine_kwargs={},
        source_payload=payload,
        mapping_status="supported",
    )
    session.add(position)
    session.flush()
    return position


def test_open_position_underlyings_returns_distinct_open(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    _make_position(session, underlying="000852.SH", source_trade_id="TRD-3")
    _make_position(
        session, underlying="600519.SH", status="closed", source_trade_id="TRD-4"
    )
    _make_position(
        session,
        underlying="600000.SH",
        source_trade_state="Knocked Out",
        source_trade_id="TRD-5",
    )
    assert _open_position_underlyings(session) == ["000300.SH", "000852.SH"]


def test_open_position_underlyings_empty(session: Session) -> None:
    assert _open_position_underlyings(session) == []


def test_underlying_pricing_default_persists_and_round_trips(session: Session) -> None:
    row = UnderlyingPricingDefault(
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
        notes="baseline",
    )
    session.add(row)
    session.commit()

    fetched = session.query(UnderlyingPricingDefault).filter_by(underlying="000300.SH").one()
    assert fetched.rate == pytest.approx(0.025)
    assert fetched.dividend_yield == pytest.approx(0.02)
    assert fetched.volatility == pytest.approx(0.185)
    assert fetched.notes == "baseline"
    assert isinstance(fetched.created_at, datetime)
    assert isinstance(fetched.updated_at, datetime)


def test_delete_underlying_default_syncs_hedge_tag_for_stock(session: Session) -> None:
    from app.services.underlying_defaults import delete_underlying_default

    stock = UnderlyingPricingDefault(
        underlying="600519.SH", kind="stock", status="active", tags=["hedge"],
    )
    session.add(stock)
    session.commit()

    delete_underlying_default(session, underlying="600519.SH")
    session.commit()

    session.refresh(stock)
    assert stock.status == "inactive"
    assert "hedge" not in stock.tags


def test_underlying_unique(session: Session) -> None:
    session.add(UnderlyingPricingDefault(underlying="000300.SH"))
    session.commit()
    session.add(UnderlyingPricingDefault(underlying="000300.SH"))
    with pytest.raises(sa_exc.IntegrityError):
        session.commit()


def test_is_complete_when_all_fields_present(session: Session) -> None:
    row = UnderlyingPricingDefault(
        underlying="000300.SH", rate=0.025, dividend_yield=0.02, volatility=0.185
    )
    session.add(row)
    session.commit()
    fetched = session.query(UnderlyingPricingDefault).filter_by(underlying="000300.SH").one()
    assert fetched.is_complete is True


def test_is_complete_when_any_field_missing(session: Session) -> None:
    row = UnderlyingPricingDefault(underlying="000300.SH", rate=0.025, dividend_yield=0.02)
    session.add(row)
    session.commit()
    fetched = session.query(UnderlyingPricingDefault).filter_by(underlying="000300.SH").one()
    assert fetched.is_complete is False


def test_underlying_pricing_default_out_includes_is_complete(session: Session) -> None:
    row = UnderlyingPricingDefault(
        underlying="000300.SH", rate=0.025, dividend_yield=0.02, volatility=0.185
    )
    session.add(row)
    session.commit()
    out = UnderlyingPricingDefaultOut.model_validate(row, from_attributes=True)
    assert out.is_complete is True
    assert out.latest_akshare_close is None


def test_underlying_pricing_default_out_marks_incomplete(session: Session) -> None:
    row = UnderlyingPricingDefault(underlying="000300.SH", rate=0.025)
    session.add(row)
    session.commit()
    out = UnderlyingPricingDefaultOut.model_validate(row, from_attributes=True)
    assert out.is_complete is False


def test_update_payload_allows_partial_fields() -> None:
    payload = UnderlyingPricingDefaultUpdate(rate=0.02)
    assert payload.rate == 0.02
    assert payload.dividend_yield is None
    assert payload.volatility is None
    assert payload.notes is None


def test_build_request_defaults() -> None:
    req = BuildDefaultProfileRequest()
    assert req.name is None
    assert req.valuation_date is None
    assert req.adjust == "qfq"


from app.services.underlying_defaults import (
    list_underlying_defaults,
    refresh_underlying_defaults_from_open_positions,
    upsert_underlying_default,
)


def test_upsert_creates_new_row(session: Session) -> None:
    row = upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
        notes="baseline",
    )
    session.commit()
    assert row.is_complete is True
    assert row.notes == "baseline"
    assert row.symbol == "000300.SH"
    assert row.status == "draft"


def test_ensure_underlying_creates_draft_master_row(session: Session) -> None:
    row = ensure_underlying(session, "000852.SH", source="booking")
    session.commit()

    fetched = session.query(Underlying).filter_by(symbol="000852.SH").one()
    assert fetched.id == row.id
    assert fetched.status == "draft"
    assert fetched.source == "booking"
    assert fetched.akshare_symbol == "000852"
    assert fetched.akshare_asset_class == "index"


def test_upsert_preserves_omitted_fields(session: Session) -> None:
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
        notes="baseline",
    )
    session.commit()
    updated = upsert_underlying_default(session, underlying="000300.SH", rate=0.03)
    session.commit()
    assert updated.rate == pytest.approx(0.03)
    assert updated.dividend_yield == pytest.approx(0.02)
    assert updated.volatility == pytest.approx(0.185)
    assert updated.notes == "baseline"


def test_refresh_from_open_positions_auto_discovers(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    _make_position(session, underlying="000852.SH", source_trade_id="TRD-2")
    session.commit()

    refreshed = refresh_underlying_defaults_from_open_positions(session)
    session.commit()
    underlyings = {row.underlying for row in refreshed}
    assert underlyings == {"000300.SH", "000852.SH"}
    for row in refreshed:
        assert row.is_complete is False


def test_refresh_does_not_modify_existing_rows(session: Session) -> None:
    _make_position(session, underlying="000300.SH")
    upsert_underlying_default(session, underlying="000300.SH", rate=0.025)
    session.commit()
    refresh_underlying_defaults_from_open_positions(session)
    session.commit()
    row = (
        session.query(UnderlyingPricingDefault)
        .filter_by(underlying="000300.SH")
        .one()
    )
    assert row.rate == pytest.approx(0.025)


def test_build_inherits_manual_inputs_from_latest_profile_until_human_edit(session: Session) -> None:
    # Re-targeted: now builds an AssumptionSet (no AKShare).
    # Tests that inheritance picks the newest profile and that a manual edit
    # on the instrument overrides the inherited value.
    _make_position(session, underlying="000300.SH")
    older = PricingParameterProfile(
        name="Older profile",
        valuation_date=datetime(2026, 4, 30),
        source_type="xlsx",
        status="completed",
        summary={"row_count": 1},
    )
    newer = PricingParameterProfile(
        name="Newer profile",
        valuation_date=datetime(2026, 5, 15),
        source_type="xlsx",
        status="completed",
        summary={"row_count": 1},
    )
    session.add_all([older, newer])
    session.flush()
    session.add_all(
        [
            PricingParameterRow(
                profile_id=older.id,
                source_trade_id="TRD-1",
                symbol="000300.SH",
                rate=0.01,
                dividend_yield=0.02,
                volatility=0.15,
            ),
            PricingParameterRow(
                profile_id=newer.id,
                source_trade_id="TRD-1",
                symbol="000300.SH",
                rate=0.025,
                dividend_yield=0.012,
                volatility=0.22,
            ),
        ]
    )
    session.commit()

    inherited_set = build_assumptions_set(session)
    session.commit()

    inherited_row = next(r for r in inherited_set.rows if r.symbol == "000300.SH")
    assert inherited_row.rate == pytest.approx(0.025)
    assert inherited_row.dividend_yield == pytest.approx(0.012)
    assert inherited_row.volatility == pytest.approx(0.22)
    assert inherited_row.source_payload["manual_input_sources"] == {
        "rate": "inherited_pricing_parameter_row",
        "dividend_yield": "inherited_pricing_parameter_row",
        "volatility": "inherited_pricing_parameter_row",
    }
    stored_default = (
        session.query(UnderlyingPricingDefault)
        .filter_by(underlying="000300.SH")
        .one()
    )
    assert stored_default.rate is None
    assert stored_default.dividend_yield is None
    assert stored_default.volatility is None

    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.03,
        dividend_yield=0.01,
        volatility=0.18,
    )
    session.commit()
    edited_set = build_assumptions_set(session)
    session.commit()

    edited_row = next(r for r in edited_set.rows if r.symbol == "000300.SH")
    assert edited_row.rate == pytest.approx(0.03)
    assert edited_row.dividend_yield == pytest.approx(0.01)
    assert edited_row.volatility == pytest.approx(0.18)
    assert edited_row.source_payload["manual_input_sources"] == {
        "rate": "instrument_default",
        "dividend_yield": "instrument_default",
        "volatility": "instrument_default",
    }


def test_list_returns_sorted_by_underlying(session: Session) -> None:
    upsert_underlying_default(session, underlying="000852.SH")
    upsert_underlying_default(session, underlying="000300.SH")
    upsert_underlying_default(session, underlying="600519.SH")
    session.commit()
    rows = list_underlying_defaults(session)
    assert [r.underlying for r in rows] == ["000300.SH", "000852.SH", "600519.SH"]


def test_build_fails_when_no_open_positions(session: Session) -> None:
    # Re-targeted: assumptions build raises same error as old build-default.
    with pytest.raises(ValueError, match="no open positions"):
        build_assumptions_set(session)


def test_build_fails_with_unfilled_underlyings(session: Session) -> None:
    # Re-targeted: unfilled check is preserved in build_assumptions_set.
    _make_position(session, underlying="000300.SH")
    session.commit()
    with pytest.raises(ValueError) as excinfo:
        build_assumptions_set(session)
    payload = excinfo.value.args
    assert any("000300.SH" in str(arg) for arg in payload)


# NOTE: test_build_does_not_call_akshare_when_unfilled is DELETED.
# Rationale: build_assumptions_set never imports or calls any AKShare fetcher,
# so there is nothing to guard against. The new test_assumptions.py covers this
# via the monkeypatch guard in test_build_assumptions_set_no_akshare.

# NOTE: test_build_fails_when_akshare_falls_back is DELETED.
# Rationale: the failed_akshare_underlyings failure mode no longer exists.
# build_assumptions_set performs NO AKShare fetch; it will never fail for that reason.


def test_build_happy_path(session: Session) -> None:
    # Re-targeted: AssumptionSet has no spot column; fields are r/q/vol only.
    _make_position(session, underlying="000300.SH")
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    _make_position(session, underlying="000852.SH", source_trade_id="TRD-3")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    upsert_underlying_default(
        session,
        underlying="000852.SH",
        rate=0.025,
        dividend_yield=0.015,
        volatility=0.24,
    )
    session.commit()

    assumption_set = build_assumptions_set(session)
    session.commit()

    assert assumption_set.status == "completed"
    assert len(assumption_set.rows) == 2
    by_symbol = {row.symbol: row for row in assumption_set.rows}
    assert by_symbol["000300.SH"].rate == pytest.approx(0.025)
    assert by_symbol["000300.SH"].dividend_yield == pytest.approx(0.02)
    assert by_symbol["000300.SH"].volatility == pytest.approx(0.185)
    assert by_symbol["000852.SH"].dividend_yield == pytest.approx(0.015)
    instruments = set(assumption_set.summary["instruments"])
    assert instruments == {"000300.SH", "000852.SH"}


def test_build_auto_discovers_missing_entries(session: Session) -> None:
    # Re-targeted: auto-discovery of instrument rows still works.
    _make_position(session, underlying="000300.SH")
    session.commit()
    with pytest.raises(ValueError):
        build_assumptions_set(session)
    assert (
        session.query(UnderlyingPricingDefault)
        .filter_by(underlying="000300.SH")
        .one()
        .is_complete
        is False
    )


def test_build_emits_one_row_per_underlying_regardless_of_trade_id(session: Session) -> None:
    # Re-targeted: one AssumptionRow per underlying, not per position.
    _make_position(session, underlying="000300.SH", source_trade_id=None)
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    assumption_set = build_assumptions_set(session)
    session.commit()
    assert len(assumption_set.rows) == 1
    assert assumption_set.rows[0].symbol == "000300.SH"


def test_build_appends_new_profile_each_invocation(session: Session) -> None:
    # Re-targeted: each build call creates a new AssumptionSet.
    _make_position(session, underlying="000300.SH")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    first = build_assumptions_set(session)
    session.commit()
    second = build_assumptions_set(session)
    session.commit()
    assert first.id != second.id
    from app.models import AssumptionSet
    assert session.query(AssumptionSet).count() == 2


from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def api_client(tmp_path, monkeypatch) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/api.db",
        artifact_dir=tmp_path / "artifacts",
    )
    return TestClient(create_app(settings))


def test_list_underlying_defaults_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/api/underlying-pricing-defaults")
    assert response.status_code == 200
    assert response.json() == []


def test_put_underlying_default_creates(api_client: TestClient) -> None:
    response = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025, "dividend_yield": 0.02, "volatility": 0.185},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_complete"] is True
    assert body["rate"] == 0.025
    # No open positions seeded — flag must be False.
    assert body["has_open_position"] is False


def test_list_defaults_marks_open_position_underlyings(api_client: TestClient) -> None:
    """has_open_position mirrors the build gate's open-position scope."""
    from app.database import SessionLocal

    # One underlying with an open position, one with a closed-only position.
    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH", status="open")
        _make_position(s, underlying="000852.SH", status="closed", source_trade_id="TRD-2")
        s.commit()

    # Materialise defaults rows for both underlyings.
    api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025},
    )
    api_client.put(
        "/api/underlying-pricing-defaults/000852.SH",
        json={"rate": 0.025},
    )

    response = api_client.get("/api/underlying-pricing-defaults")
    assert response.status_code == 200
    flags = {row["underlying"]: row["has_open_position"] for row in response.json()}
    assert flags["000300.SH"] is True
    assert flags["000852.SH"] is False


def test_put_underlying_default_partial(api_client: TestClient) -> None:
    api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025, "dividend_yield": 0.02, "volatility": 0.185},
    )
    response = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.03},
    )
    body = response.json()
    assert body["rate"] == 0.03
    assert body["dividend_yield"] == 0.02


def test_put_underlying_default_with_slash_in_symbol(api_client: TestClient) -> None:
    # FX-pair underlyings (e.g. GBP/CNY) contain a "/". The frontend
    # encodeURIComponent()s it, but uvicorn decodes %2F back to a real slash
    # before routing, so a single-segment {underlying} param 404s. The route
    # must use the :path convertor to capture the full identifier.
    response = api_client.put(
        "/api/underlying-pricing-defaults/GBP/CNY",
        json={"rate": 0.02, "dividend_yield": 0.01, "volatility": 0.1},
    )
    assert response.status_code == 200
    assert response.json()["underlying"] == "GBP/CNY"


def test_refresh_from_positions_endpoint(api_client: TestClient, monkeypatch) -> None:
    # Seed a position using the same SessionLocal the API uses
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()

    response = api_client.post("/api/underlying-pricing-defaults/refresh-from-positions")
    assert response.status_code == 200
    body = response.json()
    assert any(row["underlying"] == "000300.SH" and row["is_complete"] is False for row in body)


def test_refresh_from_positions_endpoint_ignores_listed_only_underlyings(
    api_client: TestClient,
) -> None:
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(
            s,
            underlying="IC2606.CFE",
            position_kind="listed",
            source_trade_id="HEDGE:1:1",
        )
        s.commit()

    response = api_client.post("/api/underlying-pricing-defaults/refresh-from-positions")
    assert response.status_code == 200
    assert all(row["underlying"] != "IC2606.CFE" for row in response.json())


def test_underlyings_endpoint_syncs_and_updates(api_client: TestClient) -> None:
    """Re-targeted from /api/underlyings/* to /api/instruments/* (Task 10)."""
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()

    # sync-from-positions now lives under instruments
    synced = api_client.post("/api/instruments/sync-from-positions")
    assert synced.status_code == 200
    body = synced.json()
    assert body["created"] == 1
    row = next(item for item in body["instruments"] if item["symbol"] == "000300.SH")
    assert row["status"] == "draft"
    assert row["akshare_symbol"] == "000300"
    instrument_id = row["id"]

    # patch by id (PATCH-not-PUT contract)
    updated = api_client.patch(
        f"/api/instruments/{instrument_id}",
        json={
            "status": "active",
            "rate": 0.025,
            "dividend_yield": 0.01,
            "volatility": 0.2,
            "display_name": "CSI 300",
        },
    )
    assert updated.status_code == 200
    edited = updated.json()
    assert edited["symbol"] == "000300.SH"
    assert edited["status"] == "active"
    assert edited["display_name"] == "CSI 300"
    # is_complete is not in InstrumentOut; check rate instead
    assert edited["rate"] == pytest.approx(0.025)


def test_manual_booking_auto_links_draft_underlying(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/portfolios",
        json={"name": "Desk", "kind": "container", "base_currency": "CNY"},
    )
    assert created.status_code == 200
    portfolio_id = created.json()["id"]

    booked = api_client.post(
        f"/api/portfolios/{portfolio_id}/positions",
        json={
            "underlying": "000905.SH",
            "product_type": "EuropeanVanillaOption",
            "quantity": 1,
            "source_trade_id": "T-UNDERLYING",
        },
    )
    assert booked.status_code == 200
    position = booked.json()["positions"][0]
    assert position["underlying"] == "000905.SH"
    assert position["underlying_id"] is not None

    # Re-targeted from /api/underlyings (retired) to /api/instruments
    instruments = api_client.get("/api/instruments")
    row = next(item for item in instruments.json() if item["symbol"] == "000905.SH")
    assert row["status"] == "draft"


def test_refresh_from_positions_endpoint_inherits_latest_profile(api_client: TestClient) -> None:
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        profile = PricingParameterProfile(
            name="Latest imported",
            valuation_date=datetime(2026, 5, 15),
            source_type="xlsx",
            status="completed",
            summary={"row_count": 1},
        )
        s.add(profile)
        s.flush()
        s.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="TRD-1",
                symbol="000300.SH",
                rate=0.025,
                dividend_yield=0.012,
                volatility=0.22,
            )
        )
        s.commit()

    response = api_client.post("/api/underlying-pricing-defaults/refresh-from-positions")
    assert response.status_code == 200
    body = response.json()
    row = next(item for item in body if item["underlying"] == "000300.SH")
    assert row["is_complete"] is True
    assert row["rate"] == pytest.approx(0.025)
    assert row["dividend_yield"] == pytest.approx(0.012)
    assert row["volatility"] == pytest.approx(0.22)

    update = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.03},
    )
    assert update.status_code == 200
    edited = update.json()
    assert edited["rate"] == pytest.approx(0.03)
    assert edited["dividend_yield"] == pytest.approx(0.012)
    assert edited["volatility"] == pytest.approx(0.22)


def test_build_assumptions_endpoint_unfilled(api_client: TestClient) -> None:
    # Re-targeted from test_build_default_endpoint_unfilled.
    # Same unfilled check, now on /api/assumptions/build.
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()
    response = api_client.post("/api/assumptions/build", json={})
    assert response.status_code == 400
    body = response.json()
    assert "unfilled_underlyings" in body["detail"]
    assert body["detail"]["unfilled_underlyings"] == ["000300.SH"]


def test_build_assumptions_endpoint_no_positions(api_client: TestClient) -> None:
    # Re-targeted from test_build_default_endpoint_no_positions.
    response = api_client.post("/api/assumptions/build", json={})
    assert response.status_code == 400
    assert "no open positions" in response.json()["detail"]


def test_build_assumptions_endpoint_happy(api_client: TestClient) -> None:
    # Re-targeted from test_build_default_endpoint_happy.
    # No AKShare monkeypatch needed — assumptions build never fetches spot.
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH")
        s.commit()
    api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate": 0.025, "dividend_yield": 0.02, "volatility": 0.185},
    )
    response = api_client.post("/api/assumptions/build", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["rows"][0]["rate"] == pytest.approx(0.025)
    assert body["rows"][0]["volatility"] == pytest.approx(0.185)
    # Assumptions have no spot field
    assert "spot" not in body["rows"][0]


# NOTE: test_default_underlying_profile_rows_match_equivalent_xlsx is DELETED.
# Rationale: AssumptionRows carry only r/q/vol (no spot), so there is no
# xlsx-equivalent profile to compare against. Equivalence is covered by
# test_build_happy_path (values match instrument defaults).

# NOTE: test_build_default_prices_trade_id_less_position was deleted with the
# build-default→assumptions split (no underlying-keyed pricing rows are built
# anymore). The surviving intent — a position WITHOUT a source_trade_id still
# resolves a unique complete underlying row from an imported profile — is
# pinned directly below against the resolution primitive.


def test_resolve_pricing_row_null_trade_id_falls_back_to_underlying() -> None:
    from types import SimpleNamespace

    row = PricingParameterRow(
        source_trade_id="T-OTHER",
        symbol="000300.SH",
        rate=0.021,
        dividend_yield=0.017,
        volatility=0.243,
    )
    position = SimpleNamespace(source_trade_id=None, underlying="000300.SH", id=7)
    resolution = resolve_pricing_parameter_row_for_position([row], position)
    assert resolution.match_type == "underlying"
    assert resolution.row is row

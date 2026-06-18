"""Tests for the assumptions service and build endpoint (Task 7).

No AKShare fetches are involved anywhere in this module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import (
    AssumptionRow,
    AssumptionSet,
    Portfolio,
    Position,
    PricingParameterProfile,
    PricingParameterRow,
    UnderlyingPricingDefault,
)
from app.services.assumptions import (
    build_assumptions_set,
    latest_assumption_row,
)
from app.services.underlying_defaults import upsert_underlying_default
from app.services.underlyings import ensure_underlying


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


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
    source_trade_id: str | None = "TRD-1",
    position_kind: str = "otc",
) -> Position:
    portfolio = session.query(Portfolio).first() or Portfolio(name="Test")
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    pos = Position(
        portfolio_id=portfolio.id,
        underlying=underlying,
        product_type="VanillaCall",
        quantity=1.0,
        source_trade_id=source_trade_id,
        status=status,
        position_kind=position_kind,
        engine_name="quantark.vanilla",
        engine_kwargs={},
        source_payload={},
        mapping_status="supported",
    )
    session.add(pos)
    session.flush()
    return pos


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


def test_build_assumptions_set_no_akshare(session: Session, monkeypatch) -> None:
    """Assumptions service must NOT touch AKShare even when the module exists.

    Strategy:
    - monkeypatch `app.services.pricing_profiles.fetch_akshare_snapshot` to
      fail the test if called (assumptions must not delegate back there).
    - Confirm the assumptions module itself has no direct import of any akshare
      fetcher.
    - Build with one underlying: rate/dividend_yield from instrument_default,
      volatility inherited from a prior PricingParameterRow.
    """
    import app.services.assumptions as assumptions_mod
    import app.services.pricing_profiles as pp_mod
    import app.services.market_data as market_data_mod

    # Guard: assumptions module must not import fetch_akshare_snapshot
    assert not hasattr(assumptions_mod, "fetch_akshare_snapshot"), (
        "assumptions module must not expose fetch_akshare_snapshot"
    )
    # Guard: pricing_profiles no longer imports/exposes fetch_akshare_snapshot
    # either (it was removed when build_default_pricing_profile was deleted).
    assert not hasattr(pp_mod, "fetch_akshare_snapshot"), (
        "pricing_profiles must not expose fetch_akshare_snapshot after the build fn was deleted"
    )

    def _fail(*_a, **_kw):
        pytest.fail("fetch_akshare_snapshot was called from assumptions build")

    # Guard at source: patch on market_data module so any call from any path fails
    monkeypatch.setattr(market_data_mod, "fetch_akshare_snapshot", _fail)


    # Seed an open position
    _make_position(session, underlying="000300.SH", source_trade_id="T-0142")
    session.flush()

    # Instrument with rate + dividend_yield set; volatility NOT set (comes from
    # a prior PricingParameterProfile row keyed to trade "T-0142").
    inst = ensure_underlying(session, "000300.SH", source="pricing_profile", status="draft")
    inst.rate = 0.023
    inst.dividend_yield = 0.018
    inst.volatility = None
    session.flush()

    # Seed a prior profile row that supplies vol=0.221 for T-0142 / 000300.SH
    prior_profile = PricingParameterProfile(
        name="Prior Profile",
        valuation_date=datetime(2026, 5, 1),
        source_type="xlsx",
        status="completed",
        summary={"row_count": 1},
    )
    session.add(prior_profile)
    session.flush()
    session.add(
        PricingParameterRow(
            profile_id=prior_profile.id,
            source_trade_id="T-0142",
            symbol="000300.SH",
            rate=0.011,       # overridden by instrument_default
            dividend_yield=0.007,  # overridden by instrument_default
            volatility=0.221,
        )
    )
    session.flush()

    assumption_set = build_assumptions_set(
        session,
        name="Test Set",
        valuation_date=datetime(2026, 6, 4, 9, 0),
    )
    session.flush()

    assert assumption_set.status == "completed"
    assert assumption_set.name == "Test Set"
    assert assumption_set.valuation_date == datetime(2026, 6, 4, 9, 0)
    assert len(assumption_set.rows) == 1

    row = assumption_set.rows[0]
    assert row.symbol == "000300.SH"
    assert row.rate == pytest.approx(0.023)         # from instrument_default
    assert row.dividend_yield == pytest.approx(0.018)  # from instrument_default
    assert row.volatility == pytest.approx(0.221)   # inherited

    sp = row.source_payload
    assert sp["manual_input_sources"]["rate"] == "instrument_default"
    assert sp["manual_input_sources"]["dividend_yield"] == "instrument_default"
    assert sp["manual_input_sources"]["volatility"] == "inherited_pricing_parameter_row"
    assert sp["inherited_source_trade_id"] == "T-0142"


def test_build_assumptions_uses_only_open_otc_underlyings(session: Session) -> None:
    _make_position(session, underlying="000905.SH", source_trade_id="OTC-1")
    _make_position(
        session,
        underlying="IC2606.CFE",
        source_trade_id="HEDGE:1:1",
        position_kind="listed",
    )
    otc_inst = ensure_underlying(session, "000905.SH", source="manual", status="active")
    listed_inst = ensure_underlying(session, "IC2606.CFE", source="manual", status="active")
    for inst in (otc_inst, listed_inst):
        inst.rate = 0.025
        inst.dividend_yield = 0.015
        inst.volatility = 0.2
    session.flush()

    assumption_set = build_assumptions_set(session)

    assert [row.symbol for row in assumption_set.rows] == ["000905.SH"]


def test_build_assumptions_unfilled_raises(session: Session) -> None:
    """Missing vol everywhere must raise ValueError with unfilled_underlyings."""
    _make_position(session, underlying="000300.SH", source_trade_id="T-U1")
    # Instrument exists but vol is None and no prior rows supply it
    inst = ensure_underlying(session, "000300.SH", source="pricing_profile", status="draft")
    inst.rate = 0.023
    inst.dividend_yield = 0.018
    inst.volatility = None
    session.flush()

    with pytest.raises(ValueError) as exc_info:
        build_assumptions_set(session)

    payload = exc_info.value.args[0]
    assert isinstance(payload, dict)
    assert "unfilled_underlyings" in payload
    assert "000300.SH" in payload["unfilled_underlyings"]


def test_build_assumptions_no_open_positions_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="no open positions"):
        build_assumptions_set(session)


def test_build_assumptions_auto_discovers_instrument(session: Session) -> None:
    """Build must create Instrument row for unknowns rather than error."""
    _make_position(session, underlying="000852.SH", source_trade_id="T-NEW")
    session.flush()
    # No instrument seeded — build should discover it and then raise unfilled
    with pytest.raises(ValueError) as exc_info:
        build_assumptions_set(session)
    payload = exc_info.value.args[0]
    assert "unfilled_underlyings" in payload
    assert "000852.SH" in payload["unfilled_underlyings"]


def test_latest_assumption_row_resolution(session: Session) -> None:
    """latest_assumption_row picks the right set by valuation_date."""
    # Two sets with different valuation dates
    set_early = AssumptionSet(
        name="Early",
        valuation_date=datetime(2026, 5, 1),
        status="completed",
        summary={},
    )
    set_late = AssumptionSet(
        name="Late",
        valuation_date=datetime(2026, 6, 1),
        status="completed",
        summary={},
    )
    session.add_all([set_early, set_late])
    session.flush()

    inst = ensure_underlying(session, "000300.SH")
    session.flush()

    row_early = AssumptionRow(
        set_id=set_early.id,
        instrument_id=inst.id,
        symbol="000300.SH",
        rate=0.019,
        dividend_yield=0.009,
        volatility=0.181,
        source_payload={},
    )
    row_late = AssumptionRow(
        set_id=set_late.id,
        instrument_id=inst.id,
        symbol="000300.SH",
        rate=0.023,
        dividend_yield=0.018,
        volatility=0.221,
        source_payload={},
    )
    session.add_all([row_early, row_late])
    session.flush()

    # as_of between early and late → picks early set
    found = latest_assumption_row(
        session, inst.id, as_of=datetime(2026, 5, 15)
    )
    assert found is not None
    assert found.rate == pytest.approx(0.019)

    # as_of after both → picks late (most recent ≤ as_of)
    found2 = latest_assumption_row(
        session, inst.id, as_of=datetime(2026, 6, 4)
    )
    assert found2 is not None
    assert found2.rate == pytest.approx(0.023)

    # unknown instrument → None
    found_none = latest_assumption_row(session, 999_999, as_of=datetime(2026, 6, 4))
    assert found_none is None


def test_latest_assumption_row_falls_through_to_older_set(session: Session) -> None:
    """An instrument absent from the newest set resolves from an older set —
    the resolver searches across sets, not just the newest one."""
    older = AssumptionSet(
        name="Older", valuation_date=datetime(2026, 5, 1),
        status="completed", summary={},
    )
    newest = AssumptionSet(
        name="Newest", valuation_date=datetime(2026, 6, 1),
        status="completed", summary={},
    )
    session.add_all([older, newest])
    session.flush()

    covered = ensure_underlying(session, "000300.SH")
    only_in_older = ensure_underlying(session, "LH2609.DCE")
    session.flush()
    session.add_all([
        AssumptionRow(set_id=older.id, instrument_id=only_in_older.id,
                      symbol="LH2609.DCE", rate=0.027, dividend_yield=0.0,
                      volatility=0.314, source_payload={}),
        AssumptionRow(set_id=newest.id, instrument_id=covered.id,
                      symbol="000300.SH", rate=0.023, dividend_yield=0.018,
                      volatility=0.221, source_payload={}),
    ])
    session.flush()

    found = latest_assumption_row(
        session, only_in_older.id, as_of=datetime(2026, 6, 4)
    )
    assert found is not None
    assert found.volatility == pytest.approx(0.314)


def test_build_assumptions_idempotent_appends(session: Session) -> None:
    """Each build call creates a new AssumptionSet (not overwrites)."""
    _make_position(session, underlying="000300.SH", source_trade_id="T-IDEM")
    inst = ensure_underlying(session, "000300.SH", source="pricing_profile", status="draft")
    inst.rate = 0.025
    inst.dividend_yield = 0.019
    inst.volatility = 0.199
    session.flush()

    set1 = build_assumptions_set(session)
    session.flush()
    set2 = build_assumptions_set(session)
    session.flush()

    assert set1.id != set2.id
    assert session.query(AssumptionSet).count() == 2


# ---------------------------------------------------------------------------
# Endpoint tests (TestClient)
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client(tmp_path) -> TestClient:
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/api.db",
        artifact_dir=tmp_path / "artifacts",
    )
    return TestClient(create_app(settings))


def test_build_assumptions_endpoint_200(api_client: TestClient) -> None:
    """POST /api/assumptions/build succeeds with all defaults filled."""
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH", source_trade_id="T-API1")
        inst = ensure_underlying(s, "000300.SH", source="pricing_profile", status="draft")
        inst.rate = 0.027
        inst.dividend_yield = 0.012
        inst.volatility = 0.233
        s.commit()

    response = api_client.post("/api/assumptions/build", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["symbol"] == "000300.SH"
    assert row["rate"] == pytest.approx(0.027)
    assert row["volatility"] == pytest.approx(0.233)
    assert "id" in body
    assert "valuation_date" in body


def test_build_assumptions_endpoint_400_unfilled(api_client: TestClient) -> None:
    """POST /api/assumptions/build returns 400 when inputs are missing."""
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH", source_trade_id="T-MISS")
        inst = ensure_underlying(s, "000300.SH", source="pricing_profile", status="draft")
        inst.rate = 0.025
        inst.dividend_yield = 0.018
        # volatility deliberately omitted
        inst.volatility = None
        s.commit()

    response = api_client.post("/api/assumptions/build", json={})
    assert response.status_code == 400
    body = response.json()
    detail = body["detail"]
    assert detail["detail"] == "instrument assumptions missing inputs"
    assert "000300.SH" in detail["unfilled_underlyings"]


def test_build_assumptions_endpoint_400_no_positions(api_client: TestClient) -> None:
    response = api_client.post("/api/assumptions/build", json={})
    assert response.status_code == 400
    assert "no open positions" in response.json()["detail"]


def test_get_assumption_sets_list(api_client: TestClient) -> None:
    """GET /api/assumptions/sets returns newest first."""
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH", source_trade_id="T-LST")
        inst = ensure_underlying(s, "000300.SH", source="pricing_profile", status="draft")
        inst.rate = 0.027
        inst.dividend_yield = 0.014
        inst.volatility = 0.217
        s.commit()

    api_client.post("/api/assumptions/build", json={"name": "Set A"})
    api_client.post("/api/assumptions/build", json={"name": "Set B"})

    response = api_client.get("/api/assumptions/sets")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    # Newest first (Set B was inserted last)
    assert items[0]["name"] == "Set B"
    assert items[1]["name"] == "Set A"


def test_get_assumption_set_by_id(api_client: TestClient) -> None:
    """GET /api/assumptions/sets/{id} returns the set with rows."""
    from app.database import SessionLocal

    with SessionLocal() as s:
        _make_position(s, underlying="000300.SH", source_trade_id="T-ID1")
        inst = ensure_underlying(s, "000300.SH", source="pricing_profile", status="draft")
        inst.rate = 0.029
        inst.dividend_yield = 0.011
        inst.volatility = 0.241
        s.commit()

    built = api_client.post("/api/assumptions/build", json={})
    assert built.status_code == 200
    set_id = built.json()["id"]

    response = api_client.get(f"/api/assumptions/sets/{set_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == set_id
    assert len(body["rows"]) >= 1


def test_get_assumption_set_by_id_404(api_client: TestClient) -> None:
    response = api_client.get("/api/assumptions/sets/999999")
    assert response.status_code == 404

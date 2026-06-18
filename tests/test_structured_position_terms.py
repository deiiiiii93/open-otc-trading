from __future__ import annotations

from datetime import date, datetime

from app.models import Portfolio, PortfolioKind, Position


def test_snowball_terms_are_mirrored_from_product_kwargs(session):
    from app.models import SnowballKoSchedule, SnowballTerm
    from app.services.domains.position_terms import upsert_position_term_rows

    portfolio = Portfolio(name="Snowball Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="Snowball",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {
                "ki_barrier": 80.0,
                "ki_observation": "daily",
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "ko_level": 103.0},
                    {"observation_date": "2026-07-31", "ko_level": 102.0},
                ],
            },
            "accrual_config": {"coupon_rate": 0.12},
            "start_date": "2026-01-02",
            "payoff_kind": "snowball",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add(position)
    session.flush()

    upsert_position_term_rows(session, position)
    session.flush()

    terms = session.get(SnowballTerm, position.id)
    schedule = (
        session.query(SnowballKoSchedule)
        .filter_by(position_id=position.id)
        .order_by(SnowballKoSchedule.sequence)
        .all()
    )
    assert terms is not None
    assert terms.initial_price == 100.0
    assert terms.ki_barrier == 80.0
    assert terms.coupon == 0.12
    assert terms.ki_observation == "daily"
    assert [row.ko_level for row in schedule] == [103.0, 102.0]


def test_snowball_core_expiry_uses_exercise_date(session):
    from app.models import OptionCoreTerm
    from app.services.domains.position_terms import upsert_position_term_rows

    portfolio = Portfolio(name="Snowball Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="000852.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 8171.12,
            "strike": 8171.12,
            "exercise_date": "2029-03-05",
            "barrier_config": {"ki_barrier": 6536.9},
        },
        engine_name="SnowballQuadEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add(position)
    session.flush()

    upsert_position_term_rows(session, position)
    session.flush()

    core = session.get(OptionCoreTerm, position.id)
    assert core is not None
    assert core.expiry_date == date(2029, 3, 5)


def test_query_positions_near_barrier_uses_structured_terms(session):
    from app.services.domains.position_terms import (
        query_positions_near_barrier,
        refresh_position_barrier_state,
        upsert_position_term_rows,
    )

    portfolio = Portfolio(name="Snowball Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    near = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="Snowball",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {"ki_barrier": 80.0},
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    far = Position(
        portfolio_id=portfolio.id,
        underlying="000905.SH",
        product_type="Snowball",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {"ki_barrier": 60.0},
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add_all([near, far])
    session.flush()
    upsert_position_term_rows(session, near)
    upsert_position_term_rows(session, far)
    refresh_position_barrier_state(session, portfolio_id=portfolio.id, as_of=date(2026, 5, 1))
    session.flush()

    result = query_positions_near_barrier(
        session,
        portfolio_id=portfolio.id,
        spot={"000300.SH": 82.0, "000905.SH": 82.0},
        within_pct=5.0,
    )

    assert [row["position_id"] for row in result] == [near.id]
    assert result[0]["barrier_source"] == "KI"
    assert result[0]["distance_pct"] == 2.5


def test_query_snowball_ko_from_spot_resolves_view_and_market_inputs(session):
    from app.models import Instrument, SnowballTerm
    from app.services.domains.position_terms import query_snowball_ko_from_spot
    from app.services.quotes import record_quote

    container = Portfolio(
        name="Raw Imported Book",
        base_currency="CNY",
        kind=PortfolioKind.CONTAINER.value,
    )
    view = Portfolio(
        name="Snowballs",
        base_currency="CNY",
        kind=PortfolioKind.VIEW.value,
        filter_rule={"op": "eq", "field": "product_type", "value": "SnowballOption"},
    )
    session.add_all([container, view])
    session.flush()
    inst_300 = Instrument(symbol="000300.SH", kind="index", status="active")
    inst_905 = Instrument(symbol="000905.SH", kind="index", status="active")
    session.add_all([inst_300, inst_905])
    session.flush()
    near = Position(
        portfolio_id=container.id,
        source_trade_id="SB-NEAR",
        underlying="000300.SH",
        underlying_id=inst_300.id,
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {
                "ki_barrier": 75.0,
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "ko_level": 104.0}
                ],
            },
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    far = Position(
        portfolio_id=container.id,
        source_trade_id="SB-FAR",
        underlying="000905.SH",
        underlying_id=inst_905.id,
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {
                "ki_barrier": 75.0,
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "ko_level": 108.0}
                ],
            },
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add_all([near, far])
    session.flush()
    # Spot is observation-only now — seed the quote store (was PositionMarketInput).
    record_quote(session, instrument_id=inst_300.id, price=100.0,
                 as_of=datetime(2026, 5, 26), source="xlsx_import", price_type="mid")
    record_quote(session, instrument_id=inst_905.id, price=100.0,
                 as_of=datetime(2026, 5, 26), source="xlsx_import", price_type="mid")
    session.flush()

    result = query_snowball_ko_from_spot(
        session,
        portfolio_id=view.id,
        within_pct=5.0,
        as_of=date(2026, 5, 26),
    )

    assert result["portfolio_id"] == view.id
    assert result["portfolio_kind"] == PortfolioKind.VIEW.value
    assert result["resolved_position_count"] == 2
    assert result["checked_snowball_count"] == 2
    assert result["missing_spot_count"] == 0
    assert result["missing_ko_schedule_count"] == 0
    assert [row["position_id"] for row in result["positions"]] == [near.id]
    row = result["positions"][0]
    assert row["portfolio_id"] == container.id
    assert row["requested_portfolio_id"] == view.id
    assert row["next_ko_level"] == 104.0
    assert row["spot"] == 100.0
    assert row["ko_pct_from_spot"] == 4.0
    assert row["distance_pct"] == 4.0
    assert row["spot_source"].startswith("market_quote:")
    assert session.query(SnowballTerm).count() == 2


def test_position_canonical_term_update_bumps_version(session):
    portfolio = Portfolio(name="Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="EuropeanOption",
        product_kwargs={"strike": 100.0},
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add(position)
    session.commit()
    assert position.version == 1

    position.product_kwargs = {"strike": 101.0}
    session.commit()

    assert position.version == 2


def test_backfill_position_term_rows_migrates_existing_positions(session):
    from app.models import SnowballTerm
    from app.services.domains.position_terms import backfill_position_term_rows

    portfolio = Portfolio(name="Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    session.add(
        Position(
            portfolio_id=portfolio.id,
            underlying="000300.SH",
            product_type="Snowball",
            product_kwargs={
                "initial_price": 100.0,
                "barrier_config": {"ki_barrier": 80.0},
                "coupon": 0.1,
                "start_date": "2026-01-02",
            },
            engine_name="SnowballEngine",
            engine_kwargs={},
            quantity=1,
            entry_price=0,
            status="open",
        )
    )
    session.commit()

    migrated = backfill_position_term_rows(session)
    session.flush()

    assert migrated == 1
    assert session.query(SnowballTerm).count() == 1


def test_backfill_position_term_rows_handles_imported_product_type_names(session):
    from app.models import OptionCoreTerm, SnowballTerm
    from app.services.domains.position_terms import backfill_position_term_rows

    portfolio = Portfolio(name="Imported Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    session.add_all(
        [
            Position(
                portfolio_id=portfolio.id,
                underlying="000300.SH",
                product_type="SnowballOption",
                product_kwargs={
                    "initial_price": 100.0,
                    "barrier_config": {"ki_barrier": 80.0},
                    "coupon": 0.1,
                    "start_date": "2026-01-02",
                },
                engine_name="SnowballEngine",
                engine_kwargs={},
                quantity=1,
                entry_price=0,
                status="open",
            ),
            Position(
                portfolio_id=portfolio.id,
                underlying="AAPL",
                product_type="EuropeanVanillaOption",
                product_kwargs={"strike": 100.0, "expiry_date": "2026-12-31"},
                engine_name="BlackScholesEngine",
                engine_kwargs={},
                quantity=1,
                entry_price=0,
                status="open",
            ),
        ]
    )
    session.commit()

    assert backfill_position_term_rows(session) == 2
    session.flush()

    assert session.query(SnowballTerm).count() == 1
    assert session.query(OptionCoreTerm).count() == 2


def test_refresh_position_barrier_state_persists_nearest_barrier(session):
    from app.models import PositionBarrierState
    from app.services.domains.position_terms import (
        refresh_position_barrier_state,
        upsert_position_term_rows,
    )

    portfolio = Portfolio(name="Barrier Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {
                "ki_barrier": 80.0,
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "ko_level": 103.0},
                    {"observation_date": "2026-07-31", "ko_level": 102.0},
                ],
            },
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add(position)
    session.flush()
    upsert_position_term_rows(session, position)

    refreshed = refresh_position_barrier_state(
        session,
        portfolio_id=portfolio.id,
        as_of=date(2026, 6, 1),
    )

    state = session.get(PositionBarrierState, position.id)
    assert refreshed == 1
    assert state is not None
    assert state.nearest_barrier_kind == "KO"
    assert state.nearest_barrier_level == 103.0
    assert state.nearest_barrier_date.isoformat() == "2026-06-30"
    assert state.days_to_nearest == 29


def test_structured_query_filters_selects_and_orders_allowlisted_columns(session):
    from app.services.domains.position_terms import (
        query_positions,
        refresh_position_barrier_state,
        upsert_position_term_rows,
    )

    portfolio = Portfolio(name="Structured Query Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    near = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {
                "ki_barrier": 80.0,
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "ko_level": 103.0}
                ],
            },
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    far = Position(
        portfolio_id=portfolio.id,
        underlying="000905.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "barrier_config": {"ki_barrier": 60.0},
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add_all([near, far])
    session.flush()
    upsert_position_term_rows(session, near)
    upsert_position_term_rows(session, far)
    refresh_position_barrier_state(session, portfolio_id=portfolio.id, as_of=date(2026, 6, 1))
    session.flush()

    rows = query_positions(
        session,
        portfolio_id=portfolio.id,
        filters=[
            {"col": "product_type", "op": "=", "value": "SnowballOption"},
            {"col": "snowball.ki_barrier", "op": ">", "value": 70},
            {"col": "barrier_state.nearest_barrier_level", "op": "<", "value": 110},
        ],
        select=[
            "id",
            "underlying",
            "snowball.ki_barrier",
            "barrier_state.nearest_barrier_level",
        ],
        order_by=("barrier_state.nearest_barrier_level", "asc"),
    )

    assert rows == [
        {
            "id": near.id,
            "underlying": "000300.SH",
            "snowball.ki_barrier": 80.0,
            "barrier_state.nearest_barrier_level": 103.0,
        }
    ]


def test_family_term_readers_return_structured_rows(session):
    from app.services.domains.position_terms import (
        get_barrier_terms,
        get_option_core_terms,
        get_snowball_ko_schedule,
        get_snowball_terms,
        upsert_position_term_rows,
    )

    portfolio = Portfolio(name="Reader Book", base_currency="CNY")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="000300.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "strike": 100.0,
            "barrier_config": {
                "ki_barrier": 80.0,
                "ko_observation_schedule": [
                    {"observation_date": "2026-06-30", "ko_level": 103.0}
                ],
            },
            "coupon": 0.1,
            "start_date": "2026-01-02",
        },
        engine_name="SnowballEngine",
        engine_kwargs={},
        quantity=1,
        entry_price=0,
        status="open",
    )
    session.add(position)
    session.flush()
    upsert_position_term_rows(session, position)
    session.flush()

    assert get_option_core_terms(session, [position.id])[0]["strike"] == 100.0
    assert get_snowball_terms(session, [position.id])[0]["ki_barrier"] == 80.0
    assert get_snowball_ko_schedule(session, position.id)[0]["ko_level"] == 103.0
    assert get_barrier_terms(session, [position.id]) == []

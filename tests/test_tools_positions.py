from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import database
from app.config import Settings
from app.models import (
    AuditEvent,
    EquityAutocallableObservation,
    Portfolio,
    Position,
    Product,
    PositionValuationResult,
    PositionValuationRun,
)
from app.services.domains.products import ProductSpec, create_or_get_product
from app.tools.positions import (
    ProductBookingInput,
    book_position_tool,
    cancel_lifecycle_event_tool,
    close_position_tool,
    get_product_details_tool,
    get_latest_position_valuations_tool,
    get_positions_tool,
    import_otc_positions_tool,
    mark_knockout_tool,
    query_autocallable_observations_tool,
    query_products_tool,
    settle_position_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _make_portfolio(name: str = "Book", kind: str = "container") -> int:
    with database.SessionLocal() as session:
        p = Portfolio(name=name, kind=kind, base_currency="CNY")
        session.add(p)
        session.commit()
        return p.id


def _tag_underlying(symbol: str) -> None:
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = session.query(Instrument).filter_by(symbol=symbol).one_or_none()
        if row is None:
            row = Instrument(symbol=symbol, kind="index")
            session.add(row)
        row.tags = list({*(row.tags or []), "underlying"})
        session.commit()


def _make_position(portfolio_id: int, **kwargs) -> int:
    defaults = {
        "underlying": "000300.SH",
        "product_type": "EuropeanVanillaOption",
        "quantity": 1.0,
        "status": "open",
    }
    defaults.update(kwargs)
    with database.SessionLocal() as session:
        pos = Position(portfolio_id=portfolio_id, **defaults)
        session.add(pos)
        session.commit()
        return pos.id


def test_get_positions_tool_empty_portfolio():
    pid = _make_portfolio()
    result = get_positions_tool.invoke({"portfolio_id": pid})
    assert result["source"] == "database"
    assert result["total_count"] == 0
    assert result["positions"] == []
    assert result["portfolio_total_count"] == 0


def test_get_positions_tool_returns_position_rows():
    pid = _make_portfolio()
    _make_position(pid, product_type="SnowballOption", source_trade_id="T1")
    _make_position(pid, product_type="EuropeanVanillaOption", source_trade_id="T2")
    result = get_positions_tool.invoke({"portfolio_id": pid})
    assert result["total_count"] == 2
    trade_ids = {p["source_trade_id"] for p in result["positions"]}
    assert trade_ids == {"T1", "T2"}


def test_get_positions_tool_provided_context():
    spec = {
        "product": {
            "underlying": "AAPL",
            "quantark_class": "EuropeanVanillaOption",
            "terms": {"strike": 100.0, "option_type": "CALL"},
        },
        "engine_name": "BlackScholesEngine",
        "engine_kwargs": {},
        "quantity": 1.0,
        "entry_price": 0.0,
        "status": "open",
    }
    result = get_positions_tool.invoke({"positions": [spec]})
    assert result["source"] == "provided_context"
    assert result["total_count"] == 1
    assert result["positions"][0]["underlying"] == "AAPL"


def test_get_positions_tool_omits_product_kwargs_by_default():
    pid = _make_portfolio()
    _make_position(
        pid,
        source_trade_id="T1",
        product_kwargs={"strike": 100.0, "huge_schedule": [{"date": "2026-01-01"}]},
    )
    result = get_positions_tool.invoke({"portfolio_id": pid})

    row = result["positions"][0]
    assert "product_kwargs" not in row
    assert "engine_kwargs" in row


def test_get_positions_tool_schema_has_no_raw_terms_switch():
    pid = _make_portfolio()
    _make_position(
        pid,
        source_trade_id="T1",
        product_kwargs={"strike": 100.0, "huge_schedule": [{"date": "2026-01-01"}]},
    )
    schema = get_positions_tool.args_schema.model_json_schema()
    result = get_positions_tool.invoke({"portfolio_id": pid})

    row = result["positions"][0]
    assert "fields" not in schema["properties"]
    assert "product_kwargs" not in str(schema)
    assert "product_kwargs" not in row


def test_get_positions_tool_filter_product_type():
    pid = _make_portfolio()
    _make_position(pid, product_type="SnowballOption", source_trade_id="T-SB")
    _make_position(pid, product_type="EuropeanVanillaOption", source_trade_id="T-EV")
    result = get_positions_tool.invoke(
        {"portfolio_id": pid, "product_type": "snowball"}
    )
    assert result["total_count"] == 1
    assert result["positions"][0]["source_trade_id"] == "T-SB"


def test_get_position_summaries_promotes_snowball_terms():
    from app.tools.positions import get_position_summaries_tool

    pid = _make_portfolio()
    _make_position(
        pid,
        source_trade_id="SB-1",
        product_type="SnowballOption",
        underlying="000852.SH",
        product_kwargs={
            "initial_price": 100.0,
            "ki_barrier": 80.0,
            "coupon_rate": 0.12,
            "barrier_config": {
                "ki_barrier": 75.0,
                "ko_barrier": [103.0, 102.0],
                "ko_observation_schedule": {
                    "records": [
                        {"observation_date": "2026-06-01", "barrier": 103.0},
                        {"observation_date": "2026-07-01", "barrier": 102.0},
                    ]
                },
            },
            "accrual_config": {"coupon_rate": 0.13},
        },
    )

    result = get_position_summaries_tool.invoke({"portfolio_id": pid})

    assert result["total_count"] == 1
    row = result["positions"][0]
    assert row["source_trade_id"] == "SB-1"
    assert row["underlying"] == "000852.SH"
    assert row["initial_price"] == 100.0
    assert row["ki_barrier"] == 75.0
    assert row["coupon"] == 0.13
    assert row["ko_observation_count"] == 2
    assert row["next_ko_level"] == 103.0
    assert "product_kwargs" not in row


def test_query_positions_near_barrier_tool_returns_structured_matches():
    from app.services.domains.position_terms import upsert_position_term_rows
    from app.tools.positions import query_positions_near_barrier_tool

    pid = _make_portfolio()
    with database.SessionLocal() as session:
        position = Position(
            portfolio_id=pid,
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
        session.add(position)
        session.flush()
        upsert_position_term_rows(session, position)
        session.commit()

    result = query_positions_near_barrier_tool.invoke(
        {
            "portfolio_id": pid,
            "spot": {"000300.SH": 82.0},
            "within_pct": 5.0,
        }
    )

    assert result["returned_count"] == 1
    assert result["positions"][0]["barrier_source"] == "KI"


def test_query_positions_tool_uses_structured_filters_and_selects():
    from app.services.domains.position_terms import (
        refresh_position_barrier_state,
        upsert_position_term_rows,
    )
    from app.tools.positions import query_positions_tool

    pid = _make_portfolio()
    with database.SessionLocal() as session:
        position = Position(
            portfolio_id=pid,
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
        refresh_position_barrier_state(session, portfolio_id=pid)
        session.commit()

    result = query_positions_tool.invoke(
        {
            "portfolio_id": pid,
            "filter": [{"col": "snowball.ki_barrier", "op": "=", "value": 80.0}],
            "select": ["id", "underlying", "snowball.ki_barrier"],
        }
    )

    assert result["returned_count"] == 1
    assert result["positions"][0]["snowball.ki_barrier"] == 80.0


def test_query_product_tools_read_normalized_product_tables_without_product_kwargs():
    with database.SessionLocal() as session:
        product = create_or_get_product(
            session,
            ProductSpec(
                asset_class="equity",
                product_family="autocallable",
                quantark_class="SnowballOption",
                underlying="000300.SH",
                currency="CNY",
                terms={
                    "initial_price": 100.0,
                    "strike": 100.0,
                    "barrier_config": {
                        "ko_barrier": 103.0,
                        "ko_observation_schedule": [
                            {"observation_date": "2026-06-30"}
                        ],
                    },
                },
            ),
            reuse=False,
        )
        session.commit()
        product_id = product.id

    observations_schema = (
        query_autocallable_observations_tool.args_schema.model_json_schema()
    )
    assert "product_kwargs" not in str(observations_schema)

    observations = query_autocallable_observations_tool.invoke(
        {"product_id": product_id, "role": "ko"}
    )
    products = query_products_tool.invoke({"product_family": "autocallable"})
    details = get_product_details_tool.invoke({"product_id": product_id})

    assert observations["source"] == "equity_autocallable_observations"
    assert observations["observations"][0]["barrier_level"] == 103.0
    assert products["returned_count"] == 1
    assert products["products"][0]["id"] == product_id
    assert details["product"]["id"] == product_id
    assert details["family_terms"]["autocallable"]["initial_price"] == 100.0


def test_book_position_tool_creates_product_and_position():
    pid = _make_portfolio()
    _tag_underlying("000300.SH")

    result = book_position_tool.invoke(
        {
            "portfolio_id": pid,
            "quantity": -1.0,
            "entry_price": 0.0,
            "source_trade_id": "SB-BOOK",
            "product": {
                "asset_class": "equity",
                "product_family": "autocallable",
                "quantark_class": "SnowballOption",
                "underlying": "000300.SH",
                "currency": "CNY",
                "terms": {
                    "initial_price": 100.0,
                    "strike": 100.0,
                    "maturity": 1.0,
                    "barrier_config": {
                        "ko_barrier": 103.0,
                        "ko_rate": 0.08,
                        "ko_observation_schedule": {
                            "records": [
                                {
                                    "observation_date": "2026-06-30",
                                    "barrier": 103.0,
                                    "return_rate": 0.08,
                                    "is_rate_annualized": True,
                                }
                            ]
                        },
                    },
                },
            },
        }
    )

    assert result["ok"] is True
    assert result["product"]["product_family"] == "autocallable"
    assert result["position"]["source_trade_id"] == "SB-BOOK"
    assert result["position"]["product_id"] == result["product"]["id"]
    with database.SessionLocal() as session:
        product = session.get(Product, result["product"]["id"])
        position = session.get(Position, result["position"]["id"])
        position_id = position.id if position is not None else None
        observations = (
            session.query(EquityAutocallableObservation)
            .filter_by(product_id=result["product"]["id"], observation_role="ko")
            .all()
        )

    assert product is not None
    assert position is not None
    assert position.product_id == product.id
    assert observations[0].barrier_level == 103.0
    with database.SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter_by(subject_type="position", subject_id=position_id)
            .one()
        )
    assert audit.actor == "agent"
    assert audit.event_type == "position.created"
    assert audit.payload["source"] == "agent_tool"


@pytest.mark.parametrize(
    "underlying, expected_currency",
    [
        ("AAPL", "USD"),
        ("000300.SH", "CNY"),
    ],
)
def test_book_position_tool_defaults_currency_to_underlying(
    underlying, expected_currency
):
    """When the agent omits currency, the position should inherit the underlying's
    currency instead of a hardcoded default."""
    pid = _make_portfolio()
    _tag_underlying(underlying)

    result = book_position_tool.invoke(
        {
            "portfolio_id": pid,
            "quantity": 1.0,
            "source_trade_id": f"CCY-{underlying}",
            "product": {
                "product_family": "option",
                "quantark_class": "EuropeanVanillaOption",
                "underlying": underlying,
                "terms": {
                    "strike": 100.0,
                    "option_type": "CALL",
                    "maturity": 1.0,
                },
            },
        }
    )

    assert result["ok"] is True
    assert result["product"]["currency"] == expected_currency
    with database.SessionLocal() as session:
        position = session.get(Position, result["position"]["id"])
        assert position.currency == expected_currency


def test_book_position_rejects_unregistered_underlying():
    result = book_position_tool.invoke({
        "portfolio_id": _make_portfolio(),
        "product": {
            "product_family": "spot",
            "quantark_class": "Spot",
            "underlying": "UNREGISTERED_SYMBOL.SH",
            "terms": {},
        },
        "quantity": 1,
    })
    assert result["ok"] is False
    assert result["error"] == "underlying_not_registered"
    assert result["detail"]["symbol"] == "UNREGISTERED_SYMBOL.SH"


def test_product_booking_input_derives_family_from_quantark_class():
    """The model routinely puts the quantark class (or 'snowball') in the
    product_family slot. Derive the canonical stored family from quantark_class
    instead of rejecting it — otherwise direct snowball bookings loop forever on
    'Unsupported product family' (the Booking test 4 failure).
    """
    for supplied in ("SnowballOption", "snowball", "option"):
        spec = ProductBookingInput(
            product_family=supplied,
            quantark_class="SnowballOption",
            underlying="000905.SH",
        )
        assert spec.product_family == "autocallable"


def test_book_position_tool_schema_omits_provenance_controls():
    schema = book_position_tool.args_schema.model_json_schema()
    top_level_properties = schema["properties"]
    product_properties = schema["$defs"]["ProductBookingInput"]["properties"]

    assert {
        "actor",
        "source",
        "rfq_id",
        "rfq_quote_version_id",
        "mapping_status",
        "mapping_error",
        "source_payload",
        "engine_kwargs",
        "reuse_product",
    }.isdisjoint(top_level_properties)
    assert "source_payload" not in product_properties


def test_quant_agent_tools_expose_product_aware_position_tools():
    from app.tools import QUANT_AGENT_TOOLS

    tool_names = {tool.name for tool in QUANT_AGENT_TOOLS}

    assert {
        "query_products",
        "get_product_details",
        "query_autocallable_observations",
        "book_position",
    } <= tool_names


def test_family_term_tools_return_structured_rows():
    from app.services.domains.booking import BookingRequest, ProductBookingSpec, book_position
    from app.tools.positions import (
        get_option_core_terms_tool,
        get_snowball_ko_schedule_tool,
        get_snowball_terms_tool,
    )

    pid = _make_portfolio()
    with database.SessionLocal() as session:
        position = book_position(
            session,
            BookingRequest(
                portfolio_id=pid,
                product=ProductBookingSpec(
                    asset_class="equity",
                    product_family="autocallable",
                    quantark_class="SnowballOption",
                    underlying="000300.SH",
                    currency="CNY",
                    terms={
                        "initial_price": 100.0,
                        "strike": 100.0,
                        "maturity": 1.0,
                        "barrier_config": {
                            "ki_barrier": 80.0,
                            "ko_barrier": 103.0,
                            "ko_rate": 0.08,
                            "ko_observation_schedule": {
                                "records": [
                                    {
                                        "observation_date": "2026-06-30",
                                        "barrier": 103.0,
                                        "return_rate": 0.08,
                                        "is_rate_annualized": True,
                                    }
                                ]
                            },
                            "ki_observation_schedule": {
                                "records": [
                                    {
                                        "observation_date": "2026-06-30",
                                        "barrier": 80.0,
                                    }
                                ]
                            },
                        },
                    },
                ),
                engine_name="SnowballQuadEngine",
                quantity=1,
                entry_price=0,
            ),
        )
        position_id = position.id
        session.commit()

    core = get_option_core_terms_tool.invoke({"position_ids": [position_id]})
    snowball = get_snowball_terms_tool.invoke({"position_ids": [position_id]})
    schedule = get_snowball_ko_schedule_tool.invoke({"position_id": position_id})

    assert core["terms"][0]["strike"] == 100.0
    assert snowball["terms"][0]["initial_price"] == 100.0
    assert schedule["schedule"][0]["barrier_level"] == 103.0


def test_get_latest_position_valuations_tool_empty():
    pid = _make_portfolio()
    result = get_latest_position_valuations_tool.invoke({"portfolio_id": pid})
    assert result["found"] is False
    assert result["results"] == []


def test_get_latest_position_valuations_tool_returns_results():
    pid = _make_portfolio()
    posid = _make_position(pid, source_trade_id="T-1")
    with database.SessionLocal() as session:
        run = PositionValuationRun(
            portfolio_id=pid,
            valuation_date=datetime(2026, 5, 11),
            status="completed",
            summary={"market_value": 99.0},
        )
        session.add(run)
        session.flush()
        session.add(
            PositionValuationResult(
                valuation_run_id=run.id,
                position_id=posid,
                source_trade_id="T-1",
                ok=True,
                price=1.0,
                market_value=99.0,
                pnl=9.0,
            )
        )
        session.commit()

    result = get_latest_position_valuations_tool.invoke({"portfolio_id": pid})
    assert result["found"] is True
    assert result["returned_count"] == 1
    assert result["results"][0]["market_value"] == 99.0


def test_mark_knockout_tool_closes_position():
    pid = _make_portfolio()
    posid = _make_position(
        pid,
        product_type="SnowballOption",
        source_trade_id="SB-KO",
        status="knocked_in",
    )

    result = mark_knockout_tool.invoke(
        {
            "position_id": posid,
            "observed_spot": 8799.312,
            "ko_level": 8416.47,
            "observation_date": "2026-05-27",
        }
    )

    assert result["ok"] is True
    assert result["position"]["status"] == "closed"
    event = result["lifecycle_event"]
    assert event["event_type"] == "knock_out"
    assert event["old_status"] == "knocked_in"
    assert event["new_status"] == "closed"
    assert event["event_data"]["observed_spot"] == 8799.312


def test_settle_position_tool_resolves_source_trade_id():
    pid = _make_portfolio()
    _make_position(
        pid,
        product_type="SnowballOption",
        source_trade_id="SB-SETTLE",
    )

    result = settle_position_tool.invoke(
        {
            "source_trade_id": "SB-SETTLE",
            "portfolio_id": pid,
            "settlement_date": "2026-05-28",
            "settlement_amount": 102.5,
            "currency": "CNY",
        }
    )

    assert result["position"]["source_trade_id"] == "SB-SETTLE"
    assert result["position"]["status"] == "closed"
    assert result["lifecycle_event"]["event_type"] == "settle"
    assert result["lifecycle_event"]["event_data"]["settlement_amount"] == 102.5


def test_settle_position_tool_adds_snowball_ko_coupon_when_amount_is_principal():
    pid = _make_portfolio()
    _make_position(
        pid,
        product_type="SnowballOption",
        source_trade_id="SB-KO-SETTLE",
        quantity=-1.0,
        product_kwargs={
            "initial_price": 100.0,
            "contract_multiplier": 10_000.0,
            "barrier_config": {
                "ko_observation_schedule": {
                    "records": [
                        {
                            "observation_date": "2026-05-27",
                            "barrier": 103.0,
                            "return_rate": 0.12,
                            "is_rate_annualized": True,
                        }
                    ]
                }
            },
            "accrual_config": {"accrual_factors": [0.5]},
        },
    )

    result = settle_position_tool.invoke(
        {
            "source_trade_id": "SB-KO-SETTLE",
            "portfolio_id": pid,
            "settlement_date": "2026-05-27",
            "settlement_amount": 1_000_000.0,
            "currency": "CNY",
        }
    )

    data = result["lifecycle_event"]["event_data"]
    assert data["settlement_amount"] == 1_060_000.0
    assert data["principal_amount"] == 1_000_000.0
    assert data["coupon_amount"] == 60_000.0
    assert data["ko_return_rate"] == 0.12
    assert data["ko_accrual_factor"] == 0.5
    assert data["settlement_amount_basis"] == "ko_principal_plus_coupon"


def test_settle_position_tool_uses_prior_knockout_date_for_ko_coupon():
    pid = _make_portfolio()
    posid = _make_position(
        pid,
        product_type="SnowballOption",
        source_trade_id="SB-KO-DATE",
        product_kwargs={
            "initial_price": 100.0,
            "contract_multiplier": 10_000.0,
            "barrier_config": {
                "ko_observation_schedule": {
                    "records": [
                        {
                            "observation_date": "2026-05-27",
                            "barrier": 103.0,
                            "return_rate": 0.12,
                            "is_rate_annualized": True,
                        }
                    ]
                }
            },
            "accrual_config": {"accrual_factors": [0.5]},
        },
    )
    mark_knockout_tool.invoke(
        {
            "position_id": posid,
            "observation_date": "2026-05-27",
            "ko_level": 103.0,
        }
    )

    result = settle_position_tool.invoke(
        {
            "position_id": posid,
            "settlement_amount": 1_000_000.0,
            "currency": "CNY",
        }
    )

    data = result["lifecycle_event"]["event_data"]
    assert data["settlement_date"] == "2026-05-27"
    assert data["settlement_amount"] == 1_060_000.0


def test_close_position_tool_records_reason():
    pid = _make_portfolio()
    posid = _make_position(pid, source_trade_id="VANILLA-1")

    result = close_position_tool.invoke(
        {"position_id": posid, "reason": "manual unwind", "closed_at": "2026-05-27"}
    )

    assert result["position"]["status"] == "closed"
    assert result["lifecycle_event"]["event_type"] == "close"
    assert result["lifecycle_event"]["event_data"]["reason"] == "manual unwind"


def test_cancel_lifecycle_event_tool_recomputes_status():
    pid = _make_portfolio()
    posid = _make_position(
        pid,
        product_type="SnowballOption",
        source_trade_id="SB-CANCEL",
        status="knocked_in",
    )
    knockout = mark_knockout_tool.invoke(
        {
            "position_id": posid,
            "observation_date": "2026-05-27",
        }
    )

    result = cancel_lifecycle_event_tool.invoke(
        {
            "lifecycle_event_id": knockout["lifecycle_event"]["id"],
            "source_trade_id": "SB-CANCEL",
            "portfolio_id": pid,
            "reason": "bad observation",
        }
    )

    assert result["position"]["status"] == "knocked_in"
    assert result["lifecycle_event"]["cancelled_by"] == "agent"
    assert result["lifecycle_event"]["cancellation_reason"] == "bad observation"
    assert result["lifecycle_event"]["cancelled_at"] is not None


def test_import_otc_positions_tool_delegates():
    pid = _make_portfolio()
    fake_batch = SimpleNamespace(
        id=42,
        row_count=10,
        imported_count=10,
        supported_count=8,
        unsupported_count=2,
        error_count=0,
        status="completed",
    )
    with patch(
        "app.services.domains.positions.position_adapter.import_positions_from_xlsx",
        return_value=fake_batch,
    ) as mocked:
        result = import_otc_positions_tool.invoke(
            {"portfolio_id": pid, "xlsx_path": "/tmp/trades.xlsx"}
        )
    assert mocked.call_count == 1
    assert result == {
        "import_batch_id": 42,
        "portfolio_id": pid,
        "row_count": 10,
        "imported_count": 10,
        "supported_count": 8,
        "unsupported_count": 2,
        "error_count": 0,
        "status": "completed",
    }


